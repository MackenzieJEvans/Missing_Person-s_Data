#!/usr/bin/env python3
"""Scrape the South Dakota Missing Persons Clearinghouse for the past 7 days,
strip personally identifying fields, derive county from the reporting agency,
and emit a de-identified demographic CSV.

See PLAN_SD.md for scope, the privacy rationale, and how this differs from the
Nebraska scraper (scrape.py) - most importantly, the county map here.

Unlike Nebraska, South Dakota's own site can't be made to hand us a
county-grouped agency list: its dropdown is a flat alphabetical list of ~224
agencies with no county structure. agency_county_map_sd.csv is therefore a
static, committed reference built once from that dropdown plus a public
city-to-county lookup (Wikipedia's list of South Dakota municipalities), not
something the script re-derives from the live page each run. If the site adds
a new agency, --check-agencies (or the "no county match" warning) will flag it
so the map can be extended by hand.

Also unlike Nebraska: every field this script needs (age, agency, race, sex,
missing date) is present in the single list-page GET. There is no per-record
detail-page fetch and no session/CSRF form to replay.

Output columns are controlled by OUTPUT_COLUMNS below. Name, the person's
internal id, missing date, exact age and the raw agency string are never
written out.

Usage:
    python scrape_sd.py                 # scrape live, write the weekly CSV
    python scrape_sd.py --from-cache    # re-emit from the last run's cached records
    python scrape_sd.py --dry-run       # scrape + summarize, write nothing
    python scrape_sd.py --check-agencies  # only report any agency missing from the map
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

import requests

BASE = "https://missingpersons.sd.gov"
LIST_URL = f"{BASE}/"
USER_AGENT = (
    "SD-missing-persons-demographics/1.0 "
    "(aggregate public-safety statistics; no personal data retained)"
)

WINDOW_DAYS = 7
REQUEST_TIMEOUT = 60

# Columns always written to the published CSV - matches the Nebraska schema
# (age_range, race, county, sex) for cross-state consistency.
OUTPUT_COLUMNS = ["age_range", "race", "county", "sex"]
SUPPRESS_COUNTY_BELOW = 5  # with --suppress-small-counties, roll smaller counties into "Other"

PROJECT_DIR = Path(__file__).resolve().parent
COUNTY_MAP_PATH = PROJECT_DIR / "agency_county_map_sd.csv"
# De-identified intermediate (age band input, race, sex, agency, county only -
# no name, no internal id). Git-ignored; lets --from-cache re-emit without
# re-hitting the site.
CACHE_PATH = PROJECT_DIR / ".cache_records_sd.json"

AGE_BUCKETS = [(0, 9), (10, 19), (20, 29), (30, 39),
               (40, 49), (50, 59), (60, 69), (70, 79)]

UNKNOWN_COUNTY = "Unknown"

ARTICLE_RE = re.compile(r"<article\b.*?</article>", re.S | re.I)
FIELD_PATTERNS = {
    "missing_date": re.compile(r"Missing Since\s+(\d{1,2}/\d{1,2}/\d{4})"),
    "age_missing": re.compile(r"Age Missing:\s*<span class=\"fw-bold\">([^<]*)</span>"),
    "agency": re.compile(r"Agency:\s*<span class=\"fw-bold\">([^<]*)</span>"),
    "race": re.compile(r"Race:\s*<span class=\"fw-bold\">([^<]*)</span>"),
    "sex": re.compile(r"Sex:\s*<span class=\"fw-bold\">([^<]*)</span>"),
}
COUNT_RE = re.compile(r'id="hfMissingPersonsCount"\s*/?>|value="(\d+)"\s*id="hfMissingPersonsCount"')
COUNT_INPUT_RE = re.compile(r'<input[^>]*id="hfMissingPersonsCount"[^>]*value="(\d+)"')


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def age_range(raw: str) -> str:
    m = re.search(r"\d+", raw or "")
    if not m:
        return "Unknown"
    age = int(m.group())
    if age < 0:
        return "Unknown"
    if age >= 80:
        return "80+"
    for lo, hi in AGE_BUCKETS:
        if lo <= age <= hi:
            return f"{lo}-{hi}"
    return "Unknown"


def parse_missing_date(raw: str) -> dt.date | None:
    raw = norm(raw)
    try:
        return dt.datetime.strptime(raw, "%m/%d/%Y").date()
    except ValueError:
        return None


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def load_county_map() -> dict[str, str]:
    if not COUNTY_MAP_PATH.exists():
        raise SystemExit(f"{COUNTY_MAP_PATH.name} not found - required, static reference "
                          "(see PLAN_SD.md); it is not derived from the live page")
    mapping = {}
    with COUNTY_MAP_PATH.open(newline="") as fh:
        for row in csv.DictReader(fh):
            mapping[row["agency"].strip().casefold()] = row["county"]
    return mapping


def derive_county(agency: str, mapping: dict[str, str]) -> str:
    return mapping.get(norm(agency).casefold(), UNKNOWN_COUNTY)


def fetch_list(session: requests.Session) -> list[dict]:
    resp = session.get(LIST_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    text = resp.text

    m = COUNT_INPUT_RE.search(text)
    expected = int(m.group(1)) if m else None

    records = []
    for block in ARTICLE_RE.findall(text):
        fields = {}
        for key, pat in FIELD_PATTERNS.items():
            fm = pat.search(block)
            fields[key] = norm(fm.group(1)) if fm else ""
        records.append(fields)

    if expected is not None and len(records) != expected:
        print(f"warning: page reports {expected} records but parsed {len(records)} "
              f"<article> blocks - the markup may have changed", file=sys.stderr)
    return records


def summarize(records: list[dict]) -> None:
    if not records:
        print("no records in window - nothing to summarize")
        return
    from collections import Counter
    for dim in OUTPUT_COLUMNS:
        c = Counter(r[dim] for r in records)
        print(f"\n{dim}:")
        for k, n in c.most_common():
            print(f"  {k:<40} {n}")
    thin = [k for k, n in Counter(r["county"] for r in records).items()
            if n < 5 and k not in (UNKNOWN_COUNTY, "Statewide / not county-specific")]
    if thin:
        print("\nsmall-cell warning - counties with < 5 records this week:")
        for k in thin:
            print(f"  {k}")
        print("consider a publication lag or suppression before publishing "
              "(see PLAN_SD.md 'Privacy posture').")


def suppress_small_counties(records: list[dict]) -> None:
    from collections import Counter
    counts = Counter(r["county"] for r in records)
    protected = {UNKNOWN_COUNTY, "Statewide / not county-specific"}
    for r in records:
        if r["county"] not in protected and counts[r["county"]] < SUPPRESS_COUNTY_BELOW:
            r["county"] = "Other (small county)"


def write_csv(records: list[dict], columns: list[str]) -> Path:
    cutoff = dt.date.today() - dt.timedelta(days=WINDOW_DAYS)
    path = PROJECT_DIR / f"missing_persons_sd_week_of_{cutoff.isoformat()}.csv"
    rows = sorted(({c: r[c] for c in columns} for r in records),
                  key=lambda r: tuple(r[c] for c in columns))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {path.name}: {len(rows)} rows, columns {columns}")
    return path


def write_counts_csv(records: list[dict], columns: list[str]) -> Path:
    """One row per (field, value) with how many people fall in it - e.g. how
    many 10-19s, how many from Pennington. Long format so a chart tool can
    filter by `field` instead of needing one column per dimension. Counted
    straight from the same records write_csv() emits, so the two files
    always agree."""
    from collections import Counter
    cutoff = dt.date.today() - dt.timedelta(days=WINDOW_DAYS)
    path = PROJECT_DIR / f"missing_persons_sd_week_of_{cutoff.isoformat()}_counts.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "value", "count"])
        for field in columns:
            counts = Counter(r[field] for r in records)
            for value, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                w.writerow([field, value, n])
    print(f"wrote {path.name}: counts for {columns}")
    return path


def scrape_from(rows: list[dict], mapping: dict[str, str]) -> list[dict]:
    cutoff = dt.date.today() - dt.timedelta(days=WINDOW_DAYS)
    in_window, skipped = [], 0
    for r in rows:
        d = parse_missing_date(r["missing_date"])
        if d is None:
            skipped += 1
            continue
        if d >= cutoff:
            in_window.append(r)
    print(f"list rows: {len(rows)}   in past {WINDOW_DAYS} days "
          f"(missing date >= {cutoff.isoformat()}): {len(in_window)}"
          f"   unparseable dates skipped: {skipped}")

    records, unmatched = [], set()
    for r in in_window:
        county = derive_county(r["agency"], mapping)
        if county == UNKNOWN_COUNTY:
            unmatched.add(r["agency"])
        records.append({
            "age_range": age_range(r["age_missing"]),
            "race": r["race"] or "Unknown",
            "sex": r["sex"] or "Unknown",
            "agency": r["agency"],
            "county": county,
        })

    if unmatched:
        print("agencies with no county match (mapped to Unknown) - "
              f"extend {COUNTY_MAP_PATH.name}:", file=sys.stderr)
        for a in sorted(unmatched):
            print(f"  - {a!r}", file=sys.stderr)
    return records


def check_agencies(rows: list[dict], mapping: dict[str, str]) -> None:
    seen = {norm(r["agency"]) for r in rows if r["agency"]}
    missing = sorted(a for a in seen if a.casefold() not in mapping)
    if missing:
        print(f"{len(missing)} agency name(s) on the live page are not in "
              f"{COUNTY_MAP_PATH.name}:")
        for a in missing:
            print(f"  - {a!r}")
        sys.exit(1)
    print(f"all {len(seen)} agencies seen on the live page are present in "
          f"{COUNTY_MAP_PATH.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suppress-small-counties", action="store_true",
                     help=f"roll counties with < {SUPPRESS_COUNTY_BELOW} records this "
                          f"week into 'Other (small county)'")
    ap.add_argument("--from-cache", action="store_true",
                     help="re-emit from the previous run's cached de-identified records")
    ap.add_argument("--dry-run", action="store_true",
                     help="scrape and summarize but write no CSV")
    ap.add_argument("--check-agencies", action="store_true",
                     help="scrape live, report any agency not covered by the county map, "
                          "then exit without writing a CSV")
    args = ap.parse_args()

    mapping = load_county_map()

    if args.check_agencies:
        session = build_session()
        rows = fetch_list(session)
        check_agencies(rows, mapping)
        return

    if args.from_cache:
        if not CACHE_PATH.exists():
            sys.exit(f"no cache at {CACHE_PATH.name}; run without --from-cache first")
        records = json.loads(CACHE_PATH.read_text())
        print(f"loaded {len(records)} cached records from {CACHE_PATH.name}")
    else:
        session = build_session()
        rows = fetch_list(session)
        records = scrape_from(rows, mapping)
        CACHE_PATH.write_text(json.dumps(records, indent=1))
        print(f"cached {len(records)} de-identified records to {CACHE_PATH.name}")

    summarize(records)
    if args.suppress_small_counties:
        suppress_small_counties(records)
        print(f"\nsuppressed counties with < {SUPPRESS_COUNTY_BELOW} records "
              f"into 'Other (small county)'")
    if args.dry_run:
        print("\n--dry-run: no CSV written")
        return
    write_csv(records, OUTPUT_COLUMNS)
    write_counts_csv(records, OUTPUT_COLUMNS)


if __name__ == "__main__":
    main()
