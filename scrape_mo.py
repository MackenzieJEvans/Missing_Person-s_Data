#!/usr/bin/env python3
"""Scrape the Missouri State Highway Patrol missing-persons search for the
past 7 days, strip personally identifying fields, derive county from the
reporting agency, and emit a de-identified demographic CSV.

See PLAN_MO.md for scope, the privacy rationale, and the county-derivation
method.

How this differs from the other six states:
- The site has two separate result sets with no combined view: adults
  (personType=A) and juveniles (personType=J). The URL the user originally
  supplied only covers adults - this script fetches both and merges them,
  or a systematic chunk of the week's data (juveniles were 35 of this
  week's 49 records, 2026-09-15) goes missing.
- Like South Dakota, every field needed - age, race, sex, agency,
  missing-since date - is already in the one list-page response for each
  personType (all ~600-1,000 active cases per list, no pagination). No
  detail-page fetch.
- "Age Missing" is labeled and given directly (age at the time reported
  missing, not current age) - no Iowa/North Dakota/Oklahoma-style caveat
  needed here.
- County derivation is the same hybrid shape as Iowa's/North Dakota's:
  "<County> CO SO <seat>" and a couple of sibling patterns name their
  county directly (mechanical); everything else is a municipal PD/agency
  giving only a city, needing the CITY_COUNTY table below (built from
  Wikipedia's "List of cities in Missouri", ~940 rows, filtered to the ~120
  cities that actually appear as an agency's city here). A handful of
  agencies are neither - regional Division of Youth Services offices and
  Highway Patrol troop offices each cover many counties and are named
  directly rather than assigned one; a few single-site institutions
  (a university, an Army post, an airport, a youth treatment facility) were
  each individually fact-checked to a single county.

Output columns are controlled by OUTPUT_COLUMNS below. Name, the poster
photo/PDF links, missing-since date, and the raw agency string are never
written out.

Usage:
    python scrape_mo.py                   # scrape live, write the weekly CSV
    python scrape_mo.py --from-cache      # re-emit from the last run's cache
    python scrape_mo.py --dry-run         # scrape + summarize, write nothing
    python scrape_mo.py --check-agencies  # report any agency not covered by
                                           # CITY_COUNTY, without narrowing to
                                           # a week first
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import time
from pathlib import Path

import requests

BASE = "https://www.mshp.dps.missouri.gov"
SEARCH_URL = f"{BASE}/CJ51/Search"
USER_AGENT = (
    "MO-missing-persons-demographics/1.0 "
    "(aggregate public-safety statistics; no personal data retained)"
)

WINDOW_DAYS = 7
REQUEST_TIMEOUT = 60
PERSON_TYPES = ["A", "J"]  # Adults, Juveniles - the site has no combined view

OUTPUT_COLUMNS = ["age_range", "race", "county", "sex"]
SUPPRESS_COUNTY_BELOW = 5  # with --suppress-small-counties, roll smaller counties into "Other"

PROJECT_DIR = Path(__file__).resolve().parent
CACHE_PATH = PROJECT_DIR / ".cache_records_mo.json"
COUNTY_MAP_PATH = PROJECT_DIR / "agency_county_map_mo.csv"

AGE_BUCKETS = [(0, 9), (10, 19), (20, 29), (30, 39),
               (40, 49), (50, 59), (60, 69), (70, 79)]

UNKNOWN_COUNTY = "Unknown"
ST_LOUIS_CITY = "St. Louis City"  # an independent city, not part of any county

# "<County> CO SO <seat>" (58 of ~198 agencies) and two sibling patterns seen
# on the site name their county directly - mechanical, no external data.
COUNTY_AGENCY_PATTERNS = [
    re.compile(r"^(.*?)\s+CO\s+SO\b", re.I),                       # "Boone CO SO Columbia"
    re.compile(r"^SO\s+(.*?)\s+(?:County|CO)\b[\s-]", re.I),       # "SO Phelps County - Rolla"
    re.compile(r"^(.*?)\s+County\s+PD\b", re.I),                   # "St. Charles County PD"
]

# Agencies covering many counties at once - named directly in the county
# field rather than assigned one, same rule as South Dakota's/North Dakota's
# multi-county tribal agencies. Matched by prefix so a not-yet-seen Highway
# Patrol troop or DYS region doesn't silently fall through to Unknown.
MULTI_COUNTY_PREFIXES = ("MO HP TROOP", "DYS-NORTHEAST REGION",
                          "DYS-SOUTHEAST REGION", "DYS-ST. LOUIS REGION")

# Agencies that are neither county-named nor a plain city PD - each
# individually fact-checked to a single county/independent city (2026-09-15).
AGENCY_OVERRIDES = {
    "CIRCUIT ATTORNEY'S OFFICE": ST_LOUIS_CITY,
    "ST. LOUIS METROPOLITAN POLICE": ST_LOUIS_CITY,
    "DEPT OF POLICE NW MO STATE UNIV": "Nodaway",       # Northwest Missouri State Univ., Maryville
    "UNIVERSITY OF MISSOURI-COLUMBIA": "Boone",
    "FORT LEONARD WOOD MP": "Pulaski",                  # verified: wholly in Pulaski County
    "LAMBERT INTL AIRPORT PD": "St. Louis",             # verified: unincorporated St. Louis County
    "NORTH COUNTY POLICE COOPERATIVE": "St. Louis",     # verified: north St. Louis County municipalities
    "DEPT PUBLIC SAFETY-SIKESTON": "Scott",
    "MARYVILLE DEPT. PUBLIC SAFETY": "Nodaway",
    "CHARLESTON DEPT OF PUBLIC SAFETY": "Mississippi",
    "DYS-SIERRA/OSAGE TREATMENT CENTER": "Butler",      # single facility, Poplar Bluff (verified)
    "DYS-WAVERLY REG YOUTH CENTER": "Lafayette",        # single facility, Waverly (verified)
}

# Municipal-PD/city-based agency -> county. Built 2026-09-15 from Wikipedia's
# "List of cities in Missouri" (comprehensive incorporated-places table,
# parsed locally, not via a summarizer), covering the ~120 cities that
# actually appear as an agency's city across the ~198 distinct agencies live
# on the site (adults + juveniles combined). Cross-checked wherever a city
# also appears as a county-sheriff's seat in COUNTY_AGENCY_PATTERNS' own
# output (e.g. Columbia/Boone, Rolla/Phelps) - every overlap agreed. Kansas
# City and St. Louis each span multiple counties; Kansas City uses its
# primary county (Jackson) per Wikipedia, and St. Louis is its own
# independent city, not a county at all.
CITY_COUNTY = {
    "ARNOLD": "Jefferson", "AURORA": "Lawrence", "BELLEFONTAINE NEIGHBORS": "St. Louis",
    "BERKELEY": "St. Louis", "BETHANY": "Harrison", "BLUE SPRINGS": "Jackson",
    "BOLIVAR": "Polk", "BRANSON": "Taney", "BRECKENRIDGE HILLS": "St. Louis",
    "BRIDGETON": "St. Louis", "BROOKFIELD": "Linn", "CABOOL": "Texas",
    "CAMERON": "Clinton", "CAPE GIRARDEAU": "Cape Girardeau", "CHESTERFIELD": "St. Louis",
    "CLINTON": "Henry", "COLUMBIA": "Boone", "CRYSTAL CITY": "Jefferson",
    "DESLOGE": "St. Francois", "DES PERES": "St. Louis", "EL DORADO SPRINGS": "Cedar",
    "ELDON": "Miller", "EXCELSIOR SPRINGS": "Clay", "FAYETTE": "Howard",
    "FERGUSON": "St. Louis", "FESTUS": "Jefferson", "FLORISSANT": "St. Louis",
    "FRONTENAC": "St. Louis", "FULTON": "Callaway", "GLADSTONE": "Clay",
    "GRAIN VALLEY": "Jackson", "GRANDVIEW": "Jackson", "GREENWOOD": "Jackson",
    "HANNIBAL": "Marion", "HARRISONVILLE": "Cass", "HAZELWOOD": "St. Louis",
    "HERCULANEUM": "Jefferson", "HILLSBORO": "Jefferson", "HOLLISTER": "Taney",
    "HOUSTON": "Texas", "INDEPENDENCE": "Jackson", "IRONTON": "Iron",
    "JEFFERSON CITY": "Cole", "JOPLIN": "Jasper", "KANSAS CITY": "Jackson",
    "KENNETT": "Dunklin", "KIRKWOOD": "St. Louis", "LA GRANGE": "Lewis",
    "LATHROP": "Clinton", "LEBANON": "Laclede", "LEE'S SUMMIT": "Jackson",
    "LIBERTY": "Clay", "LOUISIANA": "Pike", "MALDEN": "Dunklin",
    "MARYLAND HEIGHTS": "St. Louis", "MOBERLY": "Randolph", "MOLINE ACRES": "St. Louis",
    "MONETT": "Barry", "MONROE CITY": "Monroe", "NEOSHO": "Newton",
    "NEVADA": "Vernon", "NEW MADRID": "New Madrid", "NORMANDY": "St. Louis",
    "NORTH KANSAS CITY": "Clay", "NORTHWOODS": "St. Louis", "OAK GROVE": "Jackson",
    "O'FALLON": "St. Charles", "OSAGE BEACH": "Camden", "OVERLAND": "St. Louis",
    "PAGEDALE": "St. Louis", "PARK HILLS": "St. Francois", "PARKVILLE": "Platte",
    "PECULIAR": "Cass", "PLATTE CITY": "Platte", "POPLAR BLUFF": "Butler",
    "RAYTOWN": "Jackson", "RICHMOND": "Ray", "RICHMOND HEIGHTS": "St. Louis",
    "RIVERVIEW": "St. Louis", "ROLLA": "Phelps", "SCOTT CITY": "Scott",
    "SEDALIA": "Pettis", "SHREWSBURY": "St. Louis", "SPRINGFIELD": "Greene",
    "ST. ANN": "St. Louis", "ST. CHARLES": "St. Charles", "ST. CLAIR": "Franklin",
    "ST. JAMES": "Phelps", "ST. JOSEPH": "Buchanan", "ST. LOUIS": ST_LOUIS_CITY,
    "ST. PETERS": "St. Charles", "STOVER": "Morgan", "STRAFFORD": "Greene",
    "SUGAR CREEK": "Jackson", "SULLIVAN": "Franklin", "TOWN AND COUNTRY": "St. Louis",
    "TOWN & COUNTRY": "St. Louis", "TRENTON": "Grundy", "TROY": "Lincoln",
    "UNION": "Franklin", "UNIVERSITY CITY": "St. Louis", "VERSAILLES": "Morgan",
    "VIBURNUM": "Iron", "WARRENTON": "Warren", "WASHINGTON": "Franklin",
    "WAYNESVILLE": "Pulaski", "WEBB CITY": "Jasper", "WEBSTER GROVES": "St. Louis",
    "WEST PLAINS": "Howell", "WESTON": "Platte", "WOODSON TERRACE": "St. Louis",
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


def parse_missing_since(raw: str) -> dt.date | None:
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
    agency = norm(agency)
    agency_u = agency.upper()
    if agency_u in AGENCY_OVERRIDES:
        return AGENCY_OVERRIDES[agency_u]
    if agency_u.startswith(MULTI_COUNTY_PREFIXES):
        return agency  # name the agency itself - see MULTI_COUNTY_PREFIXES
    for pat in COUNTY_AGENCY_PATTERNS:
        m = pat.match(agency)
        if m:
            return norm(m.group(1))
    base = re.sub(r"\s*(PD|Police Department|Dept\.?\s*of\s*Public\s*Safety|"
                  r"Dept\.?\s*Public\s*Safety(?:-\w+)?)\s*$", "", agency, flags=re.I).strip()
    # "Dept Public Safety-Sikeston" style has the city glued on after a dash;
    # AGENCY_OVERRIDES handles the ones seen so far, so this is just the plain-PD case.
    return CITY_COUNTY.get(base.upper(), UNKNOWN_COUNTY)


ROW_RE = re.compile(r"<tr>\s*<td>\d+</td>.*?</tr>", re.S)
RESULTS_COUNT_RE = re.compile(r'results-count[^>]*>\s*(\d+)\s*results found', re.S)
FETCH_RETRIES = 4
FETCH_RETRY_DELAY_SEC = 2.0


def fetch_person_type_once(session: requests.Session, person_type: str) -> tuple[list[dict], int | None]:
    r = session.get(SEARCH_URL, params={"page": 1, "county": "", "personType": person_type},
                     timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    text = r.text
    records = []
    for block in ROW_RE.findall(text):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", block, re.S)
        if len(cells) < 9:
            continue
        strip = lambda c: norm(re.sub(r"<[^>]+>", "", c))
        records.append({
            "sex": strip(cells[3]),
            "race": strip(cells[4]),
            "missing_since": strip(cells[5]),
            "age": strip(cells[6]),
            "agency": strip(cells[7]),
        })
    m = RESULTS_COUNT_RE.search(text)
    expected = int(m.group(1)) if m else None
    return records, expected


def fetch_person_type(person_type: str) -> list[dict]:
    """The site is flaky about which of the two personType requests actually
    comes back with real data - observed (2026-09-15) to sometimes return
    an empty/short result for one of Adults or Juveniles, even from a fresh
    session with the identical query string that worked moments before.
    A fresh Session per attempt (reusing one across both personType calls
    was also observed to empty out the second call) plus a retry gated on
    the page's own "N results found" count catches this rather than
    silently shipping an incomplete week."""
    for attempt in range(1, FETCH_RETRIES + 1):
        records, expected = fetch_person_type_once(build_session(), person_type)
        if expected is not None and len(records) == expected:
            return records
        print(f"personType={person_type}: got {len(records)} rows, page said "
              f"{expected} - retrying ({attempt}/{FETCH_RETRIES})", file=sys.stderr)
        time.sleep(FETCH_RETRY_DELAY_SEC)
    raise SystemExit(f"personType={person_type} never returned a consistent result "
                      f"after {FETCH_RETRIES} attempts - site may be down or its "
                      f"markup changed")


def fetch_all() -> list[dict]:
    rows = []
    for pt in PERSON_TYPES:
        rows.extend(fetch_person_type(pt))
    return rows


def write_county_map(records: list[dict]) -> None:
    """Audit trail: agency -> derived county, for exactly the agencies seen
    this run. Regenerated every run, like Nebraska's/Iowa's/North Dakota's/
    Oklahoma's - only CITY_COUNTY itself is externally sourced, and that
    lives in this script, reviewable inline."""
    seen = sorted({r["agency"] for r in records if r["agency"]})
    with COUNTY_MAP_PATH.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["agency", "county"])
        for agency in seen:
            w.writerow([agency, derive_county(agency)])
    print(f"wrote {COUNTY_MAP_PATH.name} ({len(seen)} agencies)")


def check_agencies() -> None:
    rows = fetch_all()
    agencies = sorted({r["agency"] for r in rows if r["agency"]})
    unmatched = [a for a in agencies if derive_county(a) == UNKNOWN_COUNTY]
    if unmatched:
        print(f"{len(unmatched)} agenc{'y' if len(unmatched)==1 else 'ies'} with no "
              f"county match - extend CITY_COUNTY in scrape_mo.py:")
        for a in unmatched:
            print(f"  - {a!r}")
        sys.exit(1)
    print(f"all {len(agencies)} agencies (adults + juveniles) resolve to a county")


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
              "(see PLAN_MO.md 'Privacy posture').")


def suppress_small_counties(records: list[dict]) -> None:
    from collections import Counter
    counts = Counter(r["county"] for r in records)
    for r in records:
        if r["county"] != UNKNOWN_COUNTY and counts[r["county"]] < SUPPRESS_COUNTY_BELOW:
            r["county"] = "Other (small county)"


def write_csv(records: list[dict], columns: list[str], cutoff: dt.date) -> Path:
    path = PROJECT_DIR / f"missing_persons_mo_week_of_{cutoff.isoformat()}.csv"
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
    path = PROJECT_DIR / f"missing_persons_mo_week_of_{cutoff.isoformat()}_counts.csv"
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
        d = parse_missing_since(r["missing_since"])
        if d is None:
            skipped += 1
            continue
        if d >= cutoff:
            in_window.append(r)
    print(f"list rows (adults + juveniles): {len(rows)}   in past {WINDOW_DAYS} days "
          f"(missing since >= {cutoff.isoformat()}): {len(in_window)}"
          f"   unparseable dates skipped: {skipped}")

    records, unmatched = [], set()
    for r in in_window:
        county = derive_county(r["agency"])
        if county == UNKNOWN_COUNTY:
            unmatched.add(r["agency"])
        records.append({
            "age_range": age_range(r["age"]),
            "race": r["race"] or "Unknown",
            "sex": r["sex"] or "Unknown",
            "agency": r["agency"],
            "county": county,
        })
    if unmatched:
        print("agencies with no county match (mapped to Unknown) - "
              "extend CITY_COUNTY in scrape_mo.py:", file=sys.stderr)
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
        check_agencies()
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
        rows = fetch_all()
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
