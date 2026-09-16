#!/usr/bin/env python3
"""Scrape the North Dakota Missing Persons Clearinghouse for the past 7 days,
strip personally identifying fields, derive county from the reporting
agency, and emit a de-identified demographic CSV.

See PLAN_ND.md for scope, the privacy rationale, and the county-derivation
method - including the same age-field caveat as Iowa's scraper: North
Dakota's site only exposes current age, not age at the time reported
missing.

How this differs from the other three states:
- Closest to South Dakota: a single GET to the search page returns every
  currently-active case (57 as of 2026-09-15) with every needed field -
  age, race, sex, last-seen date, agency - already in the list markup
  (a server-rendered Blazor/DataTables table). No per-record detail-page
  fetch, no session/CSRF form, no pagination to worry about.
- County derivation is the same hybrid as Iowa's: "<County> COUNTY
  SHERIFF"/"<County> COUNTY DISPATCH CENTER" name their county directly in
  the agency string (mechanical); municipal police departments give only a
  city, needing the small CITY_COUNTY table below; and North Dakota has
  three BIA tribal agencies whose reservations were each individually
  checked against county lines - all three (Fort Totten/Spirit Lake,
  Standing Rock, Turtle Mountain's full trust-land footprint) span multiple
  counties (Turtle Mountain's reservation-proper is mostly Rolette County,
  but its dispersed trust lands reach into 22 counties across three states),
  so all three are named directly in the county field rather than assigned
  a single county - the same call made for South Dakota's tribal agencies,
  and the opposite of the call made for Iowa's Meskwaki Nation Police, whose
  settlement land was confirmed to sit wholly within one county.

Output columns are controlled by OUTPUT_COLUMNS below. Name, the detail-page
slug, last-seen date, and the raw agency string are never written out.

Usage:
    python scrape_nd.py                   # scrape live, write the weekly CSV
    python scrape_nd.py --from-cache      # re-emit from the last run's cache
    python scrape_nd.py --dry-run         # scrape + summarize, write nothing
    python scrape_nd.py --check-agencies  # report any agency/city not
                                           # covered by CITY_COUNTY
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

BASE = "https://missingpersons.nd.gov"
LIST_URL = f"{BASE}/search/all-missing-persons"
USER_AGENT = (
    "ND-missing-persons-demographics/1.0 "
    "(aggregate public-safety statistics; no personal data retained)"
)

WINDOW_DAYS = 7
REQUEST_TIMEOUT = 60

# Columns always written to the published CSV - matches the other three
# states' schema. See PLAN_ND.md for why age_range here is current age, not
# age when reported missing (same caveat as Iowa).
OUTPUT_COLUMNS = ["age_range", "race", "county", "sex"]
SUPPRESS_COUNTY_BELOW = 5  # with --suppress-small-counties, roll smaller counties into "Other"

PROJECT_DIR = Path(__file__).resolve().parent
CACHE_PATH = PROJECT_DIR / ".cache_records_nd.json"
COUNTY_MAP_PATH = PROJECT_DIR / "agency_county_map_nd.csv"

AGE_BUCKETS = [(0, 9), (10, 19), (20, 29), (30, 39),
               (40, 49), (50, 59), (60, 69), (70, 79)]

UNKNOWN_COUNTY = "Unknown"

# County sheriff offices and the one county dispatch center name their
# county directly in the agency string - no external data needed.
COUNTY_AGENCY_RE = re.compile(r"^(.*?)\s+COUNTY\s+(?:SHERIFF|DISPATCH CENTER)\b", re.I)

# ND county names that .title() gets wrong (Mc-prefix and LaMoure). Applied
# after .title() on the regex-captured county name above.
COUNTY_NAME_FIXUPS = {
    "Mckenzie": "McKenzie", "Mclean": "McLean",
    "Mchenry": "McHenry", "Mcintosh": "McIntosh",
    "Lamoure": "LaMoure",
}

# BIA/tribal agencies - each reservation was individually checked against
# North Dakota county lines (see module docstring); all three span multiple
# counties, so the agency's own name is used as the county-field value
# rather than an inaccurate single county (same rule as South Dakota's
# tribal PDs).
TRIBAL_AGENCIES = {
    "BIA FORT TOTTEN AGENCY": "BIA Fort Totten Agency",
    "BIA OJS STANDING ROCK AGENCY": "BIA OJS Standing Rock Agency",
    "BIA TURTLE MOUNTAIN AGENCY": "BIA Turtle Mountain Agency",
}

# Agencies whose city can't be recovered by generically stripping a
# "POLICE DEPARTMENT" suffix (e.g. a campus PD named after its parent
# institution, not its city).
AGENCY_CITY_OVERRIDES = {
    "GRAND FORKS UND POLICE DEPT.": "GRAND FORKS",
}

# Municipal-PD city -> county. Built 2026-09-15 from Wikipedia's "List of
# cities in North Dakota"; only 8 cities appear as an agency's city across
# the 24 distinct agencies live on the site as of 2026-09-15 - small enough
# to review inline. Williston and Grand Forks are self-cross-checked against
# this site's own "WILLIAMS COUNTY SHERIFF" / self-named-county agencies.
CITY_COUNTY = {
    "BISMARCK": "Burleigh",
    "DICKINSON": "Stark",
    "FARGO": "Cass",
    "GRAND FORKS": "Grand Forks",
    "MANDAN": "Morton",
    "MINOT": "Ward",
    "WEST FARGO": "Cass",
    "WILLISTON": "Williams",
}


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


def parse_last_seen(raw: str) -> dt.date | None:
    raw = norm(raw)
    try:
        return dt.datetime.strptime(raw, "%m/%d/%Y").date()
    except ValueError:
        return None


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def derive_county(agency: str) -> str:
    agency_u = norm(agency).upper()
    if agency_u in TRIBAL_AGENCIES:
        return TRIBAL_AGENCIES[agency_u]
    m = COUNTY_AGENCY_RE.match(agency_u)
    if m:
        county = m.group(1).strip().title()
        return COUNTY_NAME_FIXUPS.get(county, county)
    city = AGENCY_CITY_OVERRIDES.get(agency_u)
    if city is None:
        city = re.sub(r"\s*(POLICE DEPARTMENT|POLICE DEPT\.?|PD)\s*$",
                       "", agency_u, flags=re.I).strip()
    return CITY_COUNTY.get(city, UNKNOWN_COUNTY)


RACE_TITLE = {"AMERICAN INDIAN": "American Indian", "WHITE": "White",
              "BLACK": "Black", "ASIAN": "Asian"}
SEX_TITLE = {"MALE": "Male", "FEMALE": "Female"}

ROW_RE = re.compile(r"<tr>\s*<td>.*?</tr>", re.S)
FIELD_PATTERNS = {
    "desc": re.compile(r"CURRENT AGE:\s*(\d+)((?:<br\s*/?>[^<]*)*)</ul>", re.I),
    "last_seen": re.compile(r"</td>\s*<td>\s*([0-9/]+)\s*</td>"),
    "agency": re.compile(r"</td>\s*<td>\s*([0-9/]+)\s*</td>\s*<td>\s*([^<]+)"),
}


def fetch_list(session: requests.Session) -> list[dict]:
    resp = session.get(LIST_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    text = resp.text
    records = []
    for block in ROW_RE.findall(text):
        desc_m = FIELD_PATTERNS["desc"].search(block)
        age, race, sex = "", "Unknown", "Unknown"
        if desc_m:
            age = desc_m.group(1)
            parts = [norm(p) for p in re.split(r"<br\s*/?>", desc_m.group(2)) if norm(p)]
            # parts is [] , [SEX], or [RACE, SEX]
            if len(parts) >= 2:
                race = RACE_TITLE.get(parts[0].upper(), parts[0].title())
            if len(parts) >= 1:
                sex = SEX_TITLE.get(parts[-1].upper(), parts[-1].title())
        agency_m = FIELD_PATTERNS["agency"].search(block)
        records.append({
            "age_now": age,
            "race": race,
            "sex": sex,
            "last_seen": norm(agency_m.group(1)) if agency_m else "",
            "agency": norm(agency_m.group(2)) if agency_m else "",
        })
    return records


def write_county_map(records: list[dict]) -> None:
    """Audit trail: agency -> derived county, for exactly the agencies seen
    this run. Regenerated every run, like Nebraska's and Iowa's - only
    CITY_COUNTY itself is externally sourced, and that lives in this script,
    reviewable inline."""
    seen = sorted({r["agency"] for r in records if r["agency"]})
    with COUNTY_MAP_PATH.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["agency", "county"])
        for agency in seen:
            w.writerow([agency, derive_county(agency)])
    print(f"wrote {COUNTY_MAP_PATH.name} ({len(seen)} agencies)")


def check_agencies(session: requests.Session) -> None:
    rows = fetch_list(session)
    agencies = sorted({r["agency"] for r in rows if r["agency"]})
    unmatched = [a for a in agencies if derive_county(a) == UNKNOWN_COUNTY]
    if unmatched:
        print(f"{len(unmatched)} agenc{'y' if len(unmatched)==1 else 'ies'} with no "
              f"county match - extend CITY_COUNTY in scrape_nd.py:")
        for a in unmatched:
            print(f"  - {a!r}")
        sys.exit(1)
    print(f"all {len(agencies)} agencies site-wide resolve to a county")


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
    thin = [k for k, n in Counter(r["county"] for r in records).items() if n < 5]
    if thin:
        print("\nsmall-cell warning - counties with < 5 records this week:")
        for k in thin:
            print(f"  {k}")
        print("consider a publication lag or suppression before publishing "
              "(see PLAN_ND.md 'Privacy posture').")


def suppress_small_counties(records: list[dict]) -> None:
    from collections import Counter
    counts = Counter(r["county"] for r in records)
    for r in records:
        if r["county"] != UNKNOWN_COUNTY and counts[r["county"]] < SUPPRESS_COUNTY_BELOW:
            r["county"] = "Other (small county)"


def write_csv(records: list[dict], columns: list[str], cutoff: dt.date) -> Path:
    path = PROJECT_DIR / f"missing_persons_nd_week_of_{cutoff.isoformat()}.csv"
    rows = sorted(({c: r[c] for c in columns} for r in records),
                  key=lambda r: tuple(r[c] for c in columns))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {path.name}: {len(rows)} rows, columns {columns}")
    return path


def write_counts_csv(records: list[dict], columns: list[str], cutoff: dt.date) -> Path:
    from collections import Counter
    path = PROJECT_DIR / f"missing_persons_nd_week_of_{cutoff.isoformat()}_counts.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "value", "count"])
        for field in columns:
            counts = Counter(r[field] for r in records)
            for value, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                w.writerow([field, value, n])
    print(f"wrote {path.name}: counts for {columns}")
    return path


def scrape_from(rows: list[dict], cutoff: dt.date) -> list[dict]:
    in_window, skipped = [], 0
    for r in rows:
        d = parse_last_seen(r["last_seen"])
        if d is None:
            skipped += 1
            continue
        if d >= cutoff:
            in_window.append(r)
    print(f"list rows: {len(rows)}   in past {WINDOW_DAYS} days "
          f"(last seen >= {cutoff.isoformat()}): {len(in_window)}"
          f"   unparseable dates skipped: {skipped}")

    records, unmatched = [], set()
    for r in in_window:
        county = derive_county(r["agency"])
        if county == UNKNOWN_COUNTY:
            unmatched.add(r["agency"])
        records.append({
            "age_range": age_range(r["age_now"]),
            "race": r["race"],
            "sex": r["sex"],
            "agency": r["agency"],
            "county": county,
        })
    if unmatched:
        print("agencies with no county match (mapped to Unknown) - "
              "extend CITY_COUNTY in scrape_nd.py:", file=sys.stderr)
        for a in sorted(unmatched):
            print(f"  - {a!r}", file=sys.stderr)
    return records


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
                     help="scrape live, report any agency not covered by CITY_COUNTY, "
                          "then exit without writing a CSV")
    args = ap.parse_args()

    if args.check_agencies:
        check_agencies(build_session())
        return

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
        rows = fetch_list(session)
        records = scrape_from(rows, cutoff)
        write_county_map(records)
        CACHE_PATH.write_text(json.dumps(
            {"cutoff": cutoff.isoformat(), "records": records}, indent=1))
        print(f"cached {len(records)} de-identified records to {CACHE_PATH.name}")

    summarize(records)
    if args.suppress_small_counties:
        suppress_small_counties(records)
        print(f"\nsuppressed counties with < {SUPPRESS_COUNTY_BELOW} records "
              f"into 'Other (small county)'")
    if args.dry_run:
        print("\n--dry-run: no CSV written")
        return
    write_csv(records, OUTPUT_COLUMNS, cutoff)
    write_counts_csv(records, OUTPUT_COLUMNS, cutoff)


if __name__ == "__main__":
    main()
