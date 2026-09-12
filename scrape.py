#!/usr/bin/env python3
"""Scrape the Nebraska State Patrol missing-persons list for the past 7 days,
strip personally identifying fields, derive county from the reporting agency,
and emit a de-identified demographic CSV.

See PLAN.md for scope, the privacy rationale, and the county-derivation method.

Output columns are controlled by OUTPUT_COLUMNS below. Name, data-id, missing
date, exact age and the raw agency string are never written out. data-id and the
person's name are held only transiently in memory to fetch race/sex.

Usage:
    python scrape.py                 # scrape live, write the weekly CSV
    python scrape.py --include-sex   # also emit a `sex` column
    python scrape.py --from-cache    # re-emit from the last run's cached records
    python scrape.py --dry-run       # scrape + summarize, write nothing
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://statepatrol.nebraska.gov"
LIST_URL = f"{BASE}/services/missing-persons"
PERSON_URL = BASE + "/person/{}"
USER_AGENT = (
    "NE-missing-persons-demographics/1.0 "
    "(aggregate public-safety statistics; no personal data retained)"
)

WINDOW_DAYS = 7
DETAIL_DELAY_SEC = 1.0
REQUEST_TIMEOUT = 60

# Columns always written to the published CSV. --include-county appends "county",
# --include-sex appends "sex".
OUTPUT_COLUMNS = ["age_range", "race"]
SUPPRESS_COUNTY_BELOW = 5  # with --suppress-small-counties, roll smaller counties into "Other"

PROJECT_DIR = Path(__file__).resolve().parent
# De-identified intermediate (age band input, race, sex, agency, county only -
# no name, no data-id). Git-ignored; lets --from-cache re-emit a different
# column set without re-hitting the site.
CACHE_PATH = PROJECT_DIR / ".cache_records.json"

AGE_BUCKETS = [(0, 9), (10, 19), (20, 29), (30, 39),
               (40, 49), (50, 59), (60, 69), (70, 79)]

COUNTY_NAME_FIXUPS = {"Mc Pherson": "McPherson"}

# Agencies that are state/federal and do not belong to a single county.
STATEWIDE_PATTERNS = [
    r"\bnebraska state patrol\b",
    r"^sp\s",
    r"\bstate patrol\b",
    r"carrier enfore?cement",
    r"\bfbi\b",
    r"\busaf\b|air force|security forces squadron|offutt afb|\b155th\b",
    r"bia div law enf",
    r"dept of vet affairs|veterans affairs|vet affairs medical",
    r"agate fossil",
    r"nebraska department of insurance|dept of insurance|department of insurance",
]
STATEWIDE_RE = re.compile("|".join(STATEWIDE_PATTERNS), re.I)
STATEWIDE_COUNTY = "Statewide / not county-specific"
UNKNOWN_COUNTY = "Unknown"


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def norm_key(s: str | None) -> str:
    return norm(s).casefold()


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
    for fmt in ("%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def serialize_form(form) -> dict:
    """Reproduce the form's default submission, then blank the name fields."""
    data: dict[str, str] = {}
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name:
            continue
        kind = (el.get("type") or el.name).lower()
        if kind in ("button", "image", "file", "reset"):
            continue
        if kind == "submit":
            data.setdefault("op", el.get("value", "Submit"))
            continue
        if kind in ("checkbox", "radio"):
            if el.has_attr("checked"):
                data[name] = el.get("value", "on")
            continue
        if el.name == "select":
            opt = el.find("option", selected=True) or el.find("option")
            data[name] = opt.get("value", "") if opt else ""
        else:
            data[name] = el.get("value", "")
    data["op"] = data.get("op", "Submit")
    for blank in ("first_name_text", "last_name_text", "description"):
        if blank in data:
            data[blank] = ""
    return data


def agency_to_county_map(form) -> dict[str, str]:
    """The reporting-agency dropdown is ordered county-by-county: every option is
    either a '<County> CO SO <seat>' sheriff entry or a municipal/other agency
    listed under the sheriff for its county. Walk the options in order, tracking
    the last county seen."""
    select = form.find(attrs={"name": "reporting_agency_select"})
    if select is None:
        raise SystemExit("reporting_agency_select not found on the search form")
    co_so = re.compile(r"^(.*?)\s+CO\s+SO\b", re.I)
    mapping: dict[str, str] = {}
    current = STATEWIDE_COUNTY
    for opt in select.find_all("option"):
        label = norm(opt.get_text())
        if not label or label.lower() == "all":
            continue
        m = co_so.match(label)
        if m:
            county = m.group(1).strip()
            county = COUNTY_NAME_FIXUPS.get(county, county)
            current = f"{county} County"
        mapping[norm_key(label)] = current
    return mapping


def derive_county(agency: str, mapping: dict[str, str]) -> str:
    if STATEWIDE_RE.search(agency or ""):
        return STATEWIDE_COUNTY
    hit = mapping.get(norm_key(agency))
    if hit:
        return hit
    # tolerate minor spelling drift: substring match against dropdown labels
    key = norm_key(agency)
    for label_key, county in mapping.items():
        if key and (key in label_key or label_key in key):
            return county
    return UNKNOWN_COUNTY


def fetch_list(session: requests.Session):
    resp = session.get(LIST_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.select_one("#missing-persons-search-form") or soup.find("form")
    if form is None:
        raise SystemExit("search form not found on the list page")
    mapping = agency_to_county_map(form)
    payload = serialize_form(form)

    resp = session.post(LIST_URL, data=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    container = soup.select_one(".nsp-missing-list")
    if container is None:
        raise SystemExit("results container .nsp-missing-list missing - the POST "
                         "did not return a list (form_build_id may have expired)")
    rows = container.find_all("div", class_="u-row", recursive=False)
    records = []
    for row in rows:
        data_id = row.get("data-id") or ""
        if not data_id:
            el = row.select_one("[data-id]")
            data_id = el.get("data-id") if el else ""
        fields = {}
        for info in row.find_all("div", class_="u-info"):
            label = info.select_one(".u-label")
            cell = info.select_one(".u-cell")
            if not label:
                continue
            key = norm(label.get_text()).rstrip(":").lower()
            fields[key] = norm(cell.get_text()) if cell else ""
        records.append({
            "data_id": str(data_id).strip(),
            "missing_date": fields.get("missing date", ""),
            "age_missing": fields.get("age missing", ""),
            "agency": fields.get("agency name", ""),
        })
    return records, mapping


def fetch_detail(session: requests.Session, data_id: str) -> dict:
    resp = session.get(PERSON_URL.format(data_id), timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    out = {}
    for label in soup.select("label.inline-label"):
        key = norm(label.get_text()).rstrip(":").lower()
        span = label.find_next_sibling("span")
        out[key] = norm(span.get_text()) if span else ""
    return {
        "race": out.get("race", "") or "Unknown",
        "sex": out.get("sex", "") or out.get("gender", "") or "Unknown",
    }


def write_agency_county_map(mapping: dict[str, str]) -> None:
    path = PROJECT_DIR / "agency_county_map.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["agency", "county"])
        for agency_key in sorted(mapping):
            w.writerow([agency_key, mapping[agency_key]])
    print(f"wrote {path.name} ({len(mapping)} agencies)")


def summarize(records: list[dict]) -> None:
    if not records:
        print("no records in window - nothing to summarize")
        return
    from collections import Counter
    for dim in ("age_range", "race", "county"):
        c = Counter(r[dim] for r in records)
        print(f"\n{dim}:")
        for k, n in c.most_common():
            print(f"  {k:<28} {n}")
    thin = [k for k, n in Counter(r["county"] for r in records).items() if n < 5]
    if thin:
        print("\nsmall-cell warning - counties with < 5 records this week:")
        for k in thin:
            print(f"  {k}")
        print("consider a publication lag or suppression before publishing "
              "(see PLAN.md 'Privacy posture').")


def suppress_small_counties(records: list[dict]) -> None:
    from collections import Counter
    counts = Counter(r["county"] for r in records)
    for r in records:
        if r["county"] not in (STATEWIDE_COUNTY, UNKNOWN_COUNTY) and \
                counts[r["county"]] < SUPPRESS_COUNTY_BELOW:
            r["county"] = "Other (small county)"


def write_csv(records: list[dict], columns: list[str], cutoff: dt.date) -> Path:
    path = PROJECT_DIR / f"missing_persons_week_of_{cutoff.isoformat()}.csv"
    rows = sorted(({c: r[c] for c in columns} for r in records),
                  key=lambda r: tuple(r[c] for c in columns))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {path.name}: {len(rows)} rows, columns {columns}")
    return path


def write_counts_csv(records: list[dict], columns: list[str], cutoff: dt.date) -> Path:
    """One row per (field, value) with how many people fall in it - e.g. how
    many 10-19s, how many from Douglas County, how many women. Long format so
    a chart tool can filter by `field` instead of needing one column per
    dimension. Counted straight from the same records write_csv() emits, so
    the two files always agree."""
    from collections import Counter
    path = PROJECT_DIR / f"missing_persons_week_of_{cutoff.isoformat()}_counts.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "value", "count"])
        for field in columns:
            counts = Counter(r[field] for r in records)
            for value, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                w.writerow([field, value, n])
    print(f"wrote {path.name}: counts for {columns}")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--include-county", action="store_true",
                    help="append a `county` column derived from the reporting agency")
    ap.add_argument("--include-sex", action="store_true",
                    help="append a `sex` column")
    ap.add_argument("--suppress-small-counties", action="store_true",
                    help=f"roll counties with < {SUPPRESS_COUNTY_BELOW} records this "
                         f"week into 'Other (small county)' (only with --include-county)")
    ap.add_argument("--from-cache", action="store_true",
                    help="re-emit from the previous run's cached de-identified records")
    ap.add_argument("--dry-run", action="store_true",
                    help="scrape and summarize but write no CSV")
    args = ap.parse_args()

    columns = list(OUTPUT_COLUMNS)
    if args.include_county:
        columns.append("county")
    if args.include_sex:
        columns.append("sex")

    if args.from_cache:
        if not CACHE_PATH.exists():
            sys.exit(f"no cache at {CACHE_PATH.name}; run without --from-cache first")
        cached = json.loads(CACHE_PATH.read_text())
        records = cached["records"]
        cutoff = dt.date.fromisoformat(cached["cutoff"])
        print(f"loaded {len(records)} cached records from {CACHE_PATH.name} "
              f"(originally scraped for cutoff {cutoff.isoformat()})")
    else:
        cutoff = dt.date.today() - dt.timedelta(days=WINDOW_DAYS)
        session = build_session()
        rows, mapping = fetch_list(session)
        write_agency_county_map(mapping)
        records = scrape_from(rows, mapping, session, cutoff)
        CACHE_PATH.write_text(json.dumps(
            {"cutoff": cutoff.isoformat(), "records": records}, indent=1))
        print(f"cached {len(records)} de-identified records to {CACHE_PATH.name}")

    summarize(records)
    if args.include_county and args.suppress_small_counties:
        suppress_small_counties(records)
        print(f"\nsuppressed counties with < {SUPPRESS_COUNTY_BELOW} records "
              f"into 'Other (small county)'")
    if args.dry_run:
        print("\n--dry-run: no CSV written")
        return
    write_csv(records, columns, cutoff)
    write_counts_csv(records, columns, cutoff)


def scrape_from(rows, mapping, session, cutoff: dt.date) -> list[dict]:
    """scrape() split so main() can reuse the already-fetched list + map."""
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

    seen, records, unmatched = set(), [], set()
    for i, r in enumerate(in_window, 1):
        if r["data_id"] in seen:
            continue
        seen.add(r["data_id"])
        detail = (fetch_detail(session, r["data_id"]) if r["data_id"]
                  else {"race": "Unknown", "sex": "Unknown"})
        county = derive_county(r["agency"], mapping)
        if county == UNKNOWN_COUNTY:
            unmatched.add(r["agency"])
        records.append({
            "age_range": age_range(r["age_missing"]),
            "race": detail["race"],
            "sex": detail["sex"],
            "agency": r["agency"],
            "county": county,
        })
        if i < len(in_window):
            time.sleep(DETAIL_DELAY_SEC)

    if unmatched:
        print("agencies with no county match (mapped to Unknown):")
        for a in sorted(unmatched):
            print(f"  - {a!r}")
    return records


if __name__ == "__main__":
    main()
