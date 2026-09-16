#!/usr/bin/env python3
"""Scrape the Oklahoma Missing Person Index for the past 7 days, strip
personally identifying fields, derive county from the reporting agency, and
emit a de-identified demographic CSV.

See PLAN_OK.md for scope, the privacy rationale, and the county-derivation
method - the cleanest of any state so far, and worth understanding: no city
lookup table, no per-agency hand-mapping. Oklahoma's own agency codes (ORI,
the standard NCIC "originating agency identifier") embed the county number
directly - e.g. "OK0070000" is Bryan County (county 007), whether the agency
is Bryan County's own sheriff or a city PD inside Bryan County. The county
numbers are Oklahoma's 77 counties in alphabetical order, with one classic
government-index quirk: "Mc..." sorts as "Mac..." (McClain/McCurtain/
McIntosh land at 44-46, ahead of Major/Marshall/Mayes) - confirmed against
all 54 county-sheriff agencies that were live on the site at once, zero
mismatches.

How this differs from the other five states:
- The site (okmissing.osbi.ok.gov, classic ASP.NET WebForms) has a genuine
  CSV export button on its own search results - `imgbtn_dl_csv`. This
  script drives the same two-step postback a browser would (GET the search
  page for a fresh __VIEWSTATE, POST the search, POST the export button)
  and gets a clean CSV back directly - no HTML-table regex parsing needed,
  unlike every other state.
- Like Iowa and North Dakota, the AGE column is (almost certainly) current
  age, not age when last seen - inferred, not labeled: a person missing
  since 1963 in this dataset is listed at age 98, which only makes sense as
  an age computed from DOB at read time, not their age in 1963. See
  PLAN_OK.md's Decisions for the same cross-state caveat this implies.
- No pagination: all ~1,075 currently-active cases come back in one export.

Output columns are controlled by OUTPUT_COLUMNS below. Name, the internal
record id, date last seen, and the raw agency string are never written out.

Usage:
    python scrape_ok.py                   # scrape live, write the weekly CSV
    python scrape_ok.py --from-cache      # re-emit from the last run's cache
    python scrape_ok.py --dry-run         # scrape + summarize, write nothing
    python scrape_ok.py --check-agencies  # report any agency ORI that
                                           # doesn't resolve to a county
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

import requests

BASE = "https://okmissing.osbi.ok.gov"
SEARCH_URL = f"{BASE}/SEARCH_PERSON_PUBLIC.aspx"
USER_AGENT = (
    "OK-missing-persons-demographics/1.0 "
    "(aggregate public-safety statistics; no personal data retained)"
)

WINDOW_DAYS = 7
REQUEST_TIMEOUT = 60

OUTPUT_COLUMNS = ["age_range", "race", "county", "sex"]
SUPPRESS_COUNTY_BELOW = 5  # with --suppress-small-counties, roll smaller counties into "Other"

PROJECT_DIR = Path(__file__).resolve().parent
CACHE_PATH = PROJECT_DIR / ".cache_records_ok.json"
COUNTY_MAP_PATH = PROJECT_DIR / "agency_county_map_ok.csv"

AGE_BUCKETS = [(0, 9), (10, 19), (20, 29), (30, 39),
               (40, 49), (50, 59), (60, 69), (70, 79)]

UNKNOWN_COUNTY = "Unknown"

RACE_NAMES = {"A": "Asian", "B": "Black", "I": "American Indian",
              "U": "Unknown", "W": "White"}
SEX_NAMES = {"M": "Male", "F": "Female"}

# Oklahoma's 77 counties, in the alphabetical order its own ORI codes number
# them 1-77 - EXCEPT "Mc" sorts as "Mac", a classic government-index
# convention (McClain/McCurtain/McIntosh = 44/45/46, ahead of Major(47)/
# Marshall(48)/Mayes(49)). Verified 2026-09-15 against all 54 county-sheriff
# ORI codes live on the site at once - zero mismatches. Do not "fix" this to
# strict dictionary order; that would silently break the derivation.
COUNTIES_BY_CODE = {i + 1: name for i, name in enumerate([
    "Adair", "Alfalfa", "Atoka", "Beaver", "Beckham", "Blaine", "Bryan",
    "Caddo", "Canadian", "Carter", "Cherokee", "Choctaw", "Cimarron",
    "Cleveland", "Coal", "Comanche", "Cotton", "Craig", "Creek", "Custer",
    "Delaware", "Dewey", "Ellis", "Garfield", "Garvin", "Grady", "Grant",
    "Greer", "Harmon", "Harper", "Haskell", "Hughes", "Jackson", "Jefferson",
    "Johnston", "Kay", "Kingfisher", "Kiowa", "Latimer", "LeFlore",
    "Lincoln", "Logan", "Love", "McClain", "McCurtain", "McIntosh", "Major",
    "Marshall", "Mayes", "Murray", "Muskogee", "Noble", "Nowata",
    "Okfuskee", "Oklahoma", "Okmulgee", "Osage", "Ottawa", "Pawnee",
    "Payne", "Pittsburg", "Pontotoc", "Pottawatomie", "Pushmataha",
    "Roger Mills", "Rogers", "Seminole", "Sequoyah", "Stephens", "Texas",
    "Tillman", "Tulsa", "Wagoner", "Washington", "Washita", "Woods",
    "Woodward",
])}
ORI_RE = re.compile(r"^OK(\d{3})")

# Agencies whose ORI doesn't follow the standard "OK" + 3-digit-county +
# 4-digit-sequence pattern - checked before ORI_RE. Each fact-checked
# individually (2026-09-15), not assumed from precedent:
STATEWIDE_COUNTY = "Statewide / not county-specific"
AGENCY_OVERRIDES = {
    # State bureau, no single home county.
    "OKLAHOMA STATE BUREAU OF INVEST": STATEWIDE_COUNTY,
    # A federal Army post; Fort Sill sits entirely in one county (verified).
    "FORT SILL MILITARY POLICE": "Comanche",
    # Chickasaw Nation's reservation spans 13 counties (Bryan, Carter, Coal,
    # Garvin, Grady, Jefferson, Johnston, Love, McClain, Marshall, Murray,
    # Pontotoc, Stephens - verified) - no single county is accurate, so the
    # agency is named directly, same rule as South Dakota's/North Dakota's
    # multi-county tribal agencies.
    "CHICKASAW NATION TRIBAL POLICE": "Chickasaw Nation Tribal Police",
    # Seminole Nation's jurisdiction is essentially all of, and only,
    # Seminole County (verified) - unlike Chickasaw Nation, a plain county
    # value is accurate here, same call made for Iowa's Meskwaki Nation
    # Police (whose settlement is wholly within one county).
    "SEMINOLE NATION OF OKLAHOMA": "Seminole",
}


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


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


def parse_date_last_seen(raw: str) -> dt.date | None:
    raw = norm(raw)
    try:
        return dt.datetime.strptime(raw, "%m/%d/%Y").date()
    except ValueError:
        return None


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def hidden_field(html: str, field_id: str) -> str:
    m = re.search(rf'id="{field_id}"[^>]*value="([^"]*)"', html)
    return m.group(1) if m else ""


def fetch_ori_map(html: str) -> dict[str, str]:
    """agency name -> ORI code, from the search form's own dropdown."""
    m = re.search(r'<select[^>]*id="MainContent_ddl_TEMP_PERSON_ORI"[^>]*>(.*?)</select>',
                  html, re.S)
    if m is None:
        raise SystemExit("agency (ORI) dropdown not found on the search form")
    mapping = {}
    for value, label in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', m.group(1)):
        name = norm(label)
        if value and value != "%":
            mapping[name] = value
    return mapping


def derive_county(agency: str, ori_map: dict[str, str]) -> str:
    agency = norm(agency)
    if agency in AGENCY_OVERRIDES:
        return AGENCY_OVERRIDES[agency]
    ori = ori_map.get(agency)
    if ori is None:
        return UNKNOWN_COUNTY
    m = ORI_RE.match(ori)
    if m is None:
        return UNKNOWN_COUNTY
    return COUNTIES_BY_CODE.get(int(m.group(1)), UNKNOWN_COUNTY)


def search_and_export(session: requests.Session) -> tuple[str, dict[str, str]]:
    """Two ASP.NET WebForms postbacks, like a browser would do: load the
    search form, submit it with every filter at its default/all value, then
    click the CSV-export image button on the results page. Returns the CSV
    text and the agency->ORI map from the form's own dropdown."""
    r = session.get(SEARCH_URL, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    html = r.text
    ori_map = fetch_ori_map(html)

    base_fields = {
        "ctl00$MainContent$ddl_TEMP_PERSON_ORI": "%",
        "ctl00$MainContent$tb_TEMP_PERSON_DLC": "",
        "ctl00$MainContent$tb_TEMP_PERSON_DLC_TO": "",
        "ctl00$MainContent$TEMP_PERSON_NAM": "rad_TEMP_PERSON_NAM_EXACT",
        "ctl00$MainContent$tb_TEMP_PERSON_NAM": "",
        "ctl00$MainContent$ddl_TEMP_PERSON_AGE": "",        # "ALL" sentinel for this field is "", not "%"
        "ctl00$MainContent$ddl_TEMP_PERSON_AGE_TO": "N/A",  # ditto, "N/A" not "%"
        "ctl00$MainContent$ddl_TEMP_PERSON_SEX": "%",
        "ctl00$MainContent$ddl_TEMP_PERSON_RAC": "%",
        "ctl00$MainContent$ddl_TEMP_PERSON_HGT": "%",
        "ctl00$MainContent$ddl_TEMP_PERSON_WGT": "%",
        "ctl00$MainContent$ddl_TEMP_PERSON_EYE": "%",
        "ctl00$MainContent$ddl_TEMP_PERSON_HAI": "%",
    }

    search_payload = {
        "__EVENTTARGET": "ctl00$MainContent$btn_TEMP_PERSON_SEARCH",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": hidden_field(html, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": hidden_field(html, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": hidden_field(html, "__EVENTVALIDATION"),
        **base_fields,
    }
    r2 = session.post(SEARCH_URL, data=search_payload, timeout=REQUEST_TIMEOUT)
    r2.raise_for_status()
    if "Error.aspx" in r2.text:
        raise SystemExit("search postback hit the site's error page - "
                          "form field defaults may have changed")
    results_html = r2.text

    export_payload = {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": hidden_field(results_html, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": hidden_field(results_html, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": hidden_field(results_html, "__EVENTVALIDATION"),
        **base_fields,
        "ctl00$MainContent$imgbtn_dl_csv.x": "15",
        "ctl00$MainContent$imgbtn_dl_csv.y": "15",
    }
    r3 = session.post(SEARCH_URL, data=export_payload, timeout=REQUEST_TIMEOUT)
    r3.raise_for_status()
    if not r3.text.startswith("CURRENT STATUS,"):
        raise SystemExit("CSV export did not return a CSV - the export "
                          "button's field/column names may have changed")
    return r3.text, ori_map


def parse_csv_rows(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []
    for row in reader:
        if norm(row.get("CURRENT STATUS", "")).upper() != "MISSING":
            continue  # defensive - every row was "MISSING" as of 2026-09-15
        rows.append({
            "agency": norm(row.get("AGENCY NAME", "")),
            "date_last_seen": norm(row.get("DATE LAST SEEN", "")),
            "age": norm(row.get("AGE", "")),
            "sex": norm(row.get("SEX", "")).upper(),
            "race": norm(row.get("RACE", "")).upper(),
        })
    return rows


def write_county_map(records: list[dict], ori_map: dict[str, str]) -> None:
    """Audit trail: agency -> derived county, for exactly the agencies seen
    this run. Regenerated every run, like Nebraska's/Iowa's/North Dakota's -
    the whole derivation here is mechanical (no external data at all), so
    there's nothing to hand-maintain between runs."""
    seen = sorted({r["agency"] for r in records if r["agency"]})
    with COUNTY_MAP_PATH.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["agency", "county"])
        for agency in seen:
            w.writerow([agency, derive_county(agency, ori_map)])
    print(f"wrote {COUNTY_MAP_PATH.name} ({len(seen)} agencies)")


def check_agencies(session: requests.Session) -> None:
    csv_text, ori_map = search_and_export(session)
    rows = parse_csv_rows(csv_text)
    agencies = sorted({r["agency"] for r in rows if r["agency"]})
    unmatched = [a for a in agencies if derive_county(a, ori_map) == UNKNOWN_COUNTY]
    if unmatched:
        print(f"{len(unmatched)} agenc{'y' if len(unmatched)==1 else 'ies'} with no "
              f"county match:")
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
    thin = [k for k, n in Counter(r["county"] for r in records).items()
            if n < 5 and k != UNKNOWN_COUNTY]
    if thin:
        print("\nsmall-cell warning - counties with < 5 records this week:")
        for k in thin:
            print(f"  {k}")
        print("consider a publication lag or suppression before publishing "
              "(see PLAN_OK.md 'Privacy posture').")


def suppress_small_counties(records: list[dict]) -> None:
    from collections import Counter
    counts = Counter(r["county"] for r in records)
    for r in records:
        if r["county"] != UNKNOWN_COUNTY and counts[r["county"]] < SUPPRESS_COUNTY_BELOW:
            r["county"] = "Other (small county)"


def write_csv(records: list[dict], columns: list[str], cutoff: dt.date) -> Path:
    path = PROJECT_DIR / f"missing_persons_ok_week_of_{cutoff.isoformat()}.csv"
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
    path = PROJECT_DIR / f"missing_persons_ok_week_of_{cutoff.isoformat()}_counts.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "value", "count"])
        for field in columns:
            counts = Counter(r[field] for r in records)
            for value, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                w.writerow([field, value, n])
    print(f"wrote {path.name}: counts for {columns}")
    return path


def scrape_from(rows: list[dict], ori_map: dict[str, str], cutoff: dt.date) -> list[dict]:
    in_window, skipped = [], 0
    for r in rows:
        d = parse_date_last_seen(r["date_last_seen"])
        if d is None:
            skipped += 1
            continue
        if d >= cutoff:
            in_window.append(r)
    print(f"list rows: {len(rows)}   in past {WINDOW_DAYS} days "
          f"(date last seen >= {cutoff.isoformat()}): {len(in_window)}"
          f"   unparseable dates skipped: {skipped}")

    records, unmatched = [], set()
    for r in in_window:
        county = derive_county(r["agency"], ori_map)
        if county == UNKNOWN_COUNTY:
            unmatched.add(r["agency"])
        records.append({
            "age_range": age_range(r["age"]),
            "race": RACE_NAMES.get(r["race"], "Unknown"),
            "sex": SEX_NAMES.get(r["sex"], "Unknown"),
            "agency": r["agency"],
            "county": county,
        })
    if unmatched:
        print("agencies with no county match (mapped to Unknown):", file=sys.stderr)
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
                     help="scrape live, report any agency ORI that doesn't resolve to "
                          "a county, then exit without writing a CSV")
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
        csv_text, ori_map = search_and_export(session)
        rows = parse_csv_rows(csv_text)
        records = scrape_from(rows, ori_map, cutoff)
        write_county_map(records, ori_map)
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
