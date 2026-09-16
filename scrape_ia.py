#!/usr/bin/env python3
"""Scrape the Iowa Missing Person Information Clearinghouse for the past 7
days, strip personally identifying fields, derive county from the reporting
agency, and emit a de-identified demographic CSV.

See PLAN_IA.md for scope, the privacy rationale, the county-derivation
method, and - important - a caveat on the age field that does not apply to
the Nebraska/South Dakota scrapers: Iowa's site only exposes current age,
not age at the time reported missing.

How this differs from Nebraska/South Dakota:
- Like Nebraska, race requires a per-record detail-page fetch; age, sex,
  agency and last-contact-date are on the list page.
- Unlike either: the site paginates (~50/page, 250+ total active cases), and
  the list can be sorted newest-first via query params - but ONLY against
  the site root ("/"); passing the same query string to the canonical path
  ("/divisions/.../missing-persons") triggers a meta-refresh redirect that
  drops the query entirely. Hitting "/" is not a workaround, it's required.
- County derivation is a hybrid of Nebraska's and South Dakota's approaches:
  county sheriff / county dispatch agencies name their county directly in
  the agency string (mechanical, no external data, like Nebraska) - but
  municipal police departments only give a city, which requires a static,
  human-verified city->county table (like South Dakota's map) since Iowa's
  site exposes no county grouping anywhere. CITY_COUNTY below is that table.

Output columns are controlled by OUTPUT_COLUMNS below. Name, the detail-page
slug, missing date, and the raw agency string are never written out.

Usage:
    python scrape_ia.py                   # scrape live, write the weekly CSV
    python scrape_ia.py --from-cache      # re-emit from the last run's cache
    python scrape_ia.py --dry-run         # scrape + summarize, write nothing
    python scrape_ia.py --check-agencies  # report any agency/city not covered
                                           # by CITY_COUNTY, without scraping
                                           # a full week
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

# Query-string sorting/pagination only works against the site root - see the
# module docstring. Do not "simplify" this to the canonical /divisions/... path.
BASE = "https://missingpersons.iowa.gov"
LIST_URL = f"{BASE}/"
USER_AGENT = (
    "IA-missing-persons-demographics/1.0 "
    "(aggregate public-safety statistics; no personal data retained)"
)

WINDOW_DAYS = 7
DETAIL_DELAY_SEC = 1.0
REQUEST_TIMEOUT = 60
MAX_PAGES = 10  # hard cap: sort is confirmed monotonic newest-first, but
                # don't let a future sort regression turn into an unbounded crawl

# Columns always written to the published CSV - matches the Nebraska/SD schema
# (age_range, race, county, sex) for cross-state consistency. See PLAN_IA.md
# for why age_range here is current age, not age when reported missing.
OUTPUT_COLUMNS = ["age_range", "race", "county", "sex"]
SUPPRESS_COUNTY_BELOW = 5  # with --suppress-small-counties, roll smaller counties into "Other"

PROJECT_DIR = Path(__file__).resolve().parent
CACHE_PATH = PROJECT_DIR / ".cache_records_ia.json"
COUNTY_MAP_PATH = PROJECT_DIR / "agency_county_map_ia.csv"

AGE_BUCKETS = [(0, 9), (10, 19), (20, 29), (30, 39),
               (40, 49), (50, 59), (60, 69), (70, 79)]

STATEWIDE_COUNTY = "Statewide / not county-specific"
UNKNOWN_COUNTY = "Unknown"

# County sheriff offices and county dispatch/law-enforcement centers name
# their county directly in the agency string - no external data needed.
# e.g. "BLACK HAWK COUNTY SO, WATERLOO" -> Black Hawk; "WEBSTER CO LEC, FT
# DODGE" -> Webster; "DUBUQUE CO COMM, DUBUQUE" -> Dubuque.
COUNTY_AGENCY_RE = re.compile(r"^(.*?)\s+COUNTY\s+SO\b", re.I)
CO_AGENCY_RE = re.compile(r"^(.*?)\s+CO\s+(?:LEC|COMM)\b", re.I)

# Agencies with no single home county.
STATEWIDE_AGENCIES = {"IOWA DIV OF CRIM INVESTIGATION"}

# Municipal-PD city -> county. Built 2026-09-15: verified against the raw
# "List of cities in Iowa" Wikipedia table (comprehensive incorporated-places
# list, parsed locally, not via a summarizer) and cross-checked for the
# 30-ish overlapping county-seat cities against this site's own county
# sheriff/CO LEC agency strings (independent, site-authoritative confirmation
# - every overlap agreed). Only covers cities that actually appear as an
# agency's city on the live site (80 distinct agencies site-wide as of
# 2026-09-15), not all ~940 Iowa municipalities - see PLAN_IA.md.
CITY_COUNTY = {
    "ALTOONA": "Polk",
    "AMES": "Story",
    "ANKENY": "Polk",
    "ATLANTIC": "Cass",
    "BETTENDORF": "Scott",
    "BOONE": "Boone",
    "BURLINGTON": "Des Moines",
    "CAMANCHE": "Clinton",
    "CARROLL": "Carroll",
    "CARTER LAKE": "Pottawattamie",
    "CEDAR FALLS": "Black Hawk",
    "CEDAR RAPIDS": "Linn",
    "CENTERVILLE": "Appanoose",
    "CLARINDA": "Page",
    "CLINTON": "Clinton",
    "COUNCIL BLUFFS": "Pottawattamie",
    "DAVENPORT": "Scott",
    "DENISON": "Crawford",
    "DES MOINES": "Polk",
    "EVANSDALE": "Black Hawk",
    "FORT MADISON": "Lee",
    "IOWA CITY": "Johnson",
    "LE CLAIRE": "Scott",
    "LECLAIRE": "Scott",  # spacing variant seen on the live site
    "MARION": "Linn",
    "MARSHALLTOWN": "Marshall",
    "MASON CITY": "Cerro Gordo",
    "MOVILLE": "Woodbury",
    "MOUNT PLEASANT": "Henry",
    "MT PLEASANT": "Henry",  # abbreviation used on the live site
    "MUSCATINE": "Muscatine",
    "NEWTON": "Jasper",
    "OTTUMWA": "Wapello",
    "PERRY": "Dallas",
    "ROCKWELL CITY": "Calhoun",
    "ROCKELL CITY": "Calhoun",  # source typo, seen verbatim on the live site
    "SIOUX CITY": "Woodbury",
    "SPENCER": "Clay",
    "TAMA": "Tama",
    "URBANDALE": "Polk",
    "WATERLOO": "Black Hawk",
    "WAUKEE": "Dallas",
    "WEBSTER CITY": "Hamilton",
    "WEST DES MOINES": "Polk",
    "WINTERSET": "Madison",
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


def parse_last_contact_date(raw: str) -> dt.date | None:
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
    if agency.upper() in STATEWIDE_AGENCIES:
        return STATEWIDE_COUNTY
    m = COUNTY_AGENCY_RE.match(agency) or CO_AGENCY_RE.match(agency)
    if m:
        # Plain county name, no "County" suffix - matches the style of
        # CITY_COUNTY's values below (and South Dakota's map), so e.g. "POLK
        # COUNTY SO" and "DES MOINES PD" (in Polk County) land on the same
        # value instead of "Polk County" vs "Polk".
        return norm(m.group(1)).title()
    # Municipal/other: city is whatever follows the last comma; if there's no
    # comma, derive it by stripping a trailing department-name suffix.
    if "," in agency:
        city = agency.rsplit(",", 1)[1].strip()
    else:
        city = re.sub(r"\s*(POLICE DEPARTMENT|POLICE DEPT\.?|PD|COMMUNICATIONS)\s*$",
                       "", agency, flags=re.I).strip()
    return CITY_COUNTY.get(city.upper(), UNKNOWN_COUNTY)


CARD_RE = re.compile(r'<li class="col-12 col-md-6 col-xl-4">.*?</li>', re.S)
FIELD_PATTERNS = {
    "href": re.compile(r'<a href="([^"]+)"\s+class="card-img-top'),
    "age_now": re.compile(r'<span class="age-now">([^<]*)</span>'),
    "last_contact_date": re.compile(r"Last Contact Date:</strong>\s*([0-9/]+)"),
    "agency": re.compile(r"<strong>Agency:</strong>\s*([^\n<]*)"),
    "gender": re.compile(r"<strong>Gender:\s*</strong>\s*([^\n<]*)"),
}
PAGE_COUNT_RE = re.compile(r'title="Go to last page"[^>]*>.*?<span[^>]*>(\d+)', re.S)


def fetch_page(session: requests.Session, page: int) -> list[dict]:
    params = {"sort_by": "field_mp_last_contact_date_value", "sort_order": "DESC"}
    if page:
        params["page"] = page
    resp = session.get(LIST_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    text = resp.text
    records = []
    for block in CARD_RE.findall(text):
        fields = {}
        for key, pat in FIELD_PATTERNS.items():
            fm = pat.search(block)
            fields[key] = norm(fm.group(1)) if fm else ""
        records.append(fields)
    return records


def fetch_detail(session: requests.Session, href: str) -> dict:
    resp = session.get(BASE + href, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    text = resp.text
    race_m = re.search(r"Race:\s*</th>\s*<td[^>]*>\s*([^<]*)", text)
    if race_m is None:
        # fall back to the plain-text label ordering seen on the rendered page
        race_m = re.search(r"Race:\s*\n?\s*([A-Za-z /]+)", text)
    sex_m = re.search(r"Sex:\s*</th>\s*<td[^>]*>\s*([^<]*)", text)
    if sex_m is None:
        sex_m = re.search(r"Sex:\s*\n?\s*([A-Za-z]+)", text)
    return {
        "race": norm(race_m.group(1)) if race_m else "Unknown",
        "sex": norm(sex_m.group(1)) if sex_m else "Unknown",
    }


def fetch_window(session: requests.Session, cutoff: dt.date) -> list[dict]:
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        batch = fetch_page(session, page)
        if not batch:
            break
        rows.extend(batch)
        dates = [parse_last_contact_date(r["last_contact_date"]) for r in batch]
        dates = [d for d in dates if d is not None]
        if not dates or min(dates) < cutoff:
            break
        time.sleep(0.5)
    else:
        print(f"warning: hit MAX_PAGES={MAX_PAGES} without reaching the cutoff date "
              f"- the window may be incomplete", file=sys.stderr)
    return rows


def write_county_map(records: list[dict]) -> None:
    """Audit trail: agency -> derived county, for exactly the agencies seen
    this run. Regenerated every run (like Nebraska's), unlike South Dakota's
    static file, since most of the derivation here is mechanical - only the
    CITY_COUNTY table is external/hand-verified, and that lives in this
    script, reviewable inline."""
    seen = sorted({r["agency"] for r in records if r["agency"]})
    with COUNTY_MAP_PATH.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["agency", "county"])
        for agency in seen:
            w.writerow([agency, derive_county(agency)])
    print(f"wrote {COUNTY_MAP_PATH.name} ({len(seen)} agencies)")


def check_agencies(session: requests.Session) -> None:
    rows = fetch_page(session, 0)
    all_rows = list(rows)
    for page in range(1, MAX_PAGES):
        batch = fetch_page(session, page)
        if not batch:
            break
        all_rows.extend(batch)
    agencies = sorted({norm(r["agency"]) for r in all_rows if r["agency"]})
    unmatched = [a for a in agencies if derive_county(a) == UNKNOWN_COUNTY]
    if unmatched:
        print(f"{len(unmatched)} agenc{'y' if len(unmatched)==1 else 'ies'} with no "
              f"county match - extend CITY_COUNTY in scrape_ia.py:")
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
            if n < 5 and k not in (UNKNOWN_COUNTY, STATEWIDE_COUNTY)]
    if thin:
        print("\nsmall-cell warning - counties with < 5 records this week:")
        for k in thin:
            print(f"  {k}")
        print("consider a publication lag or suppression before publishing "
              "(see PLAN_IA.md 'Privacy posture').")


def suppress_small_counties(records: list[dict]) -> None:
    from collections import Counter
    counts = Counter(r["county"] for r in records)
    protected = {UNKNOWN_COUNTY, STATEWIDE_COUNTY}
    for r in records:
        if r["county"] not in protected and counts[r["county"]] < SUPPRESS_COUNTY_BELOW:
            r["county"] = "Other (small county)"


def write_csv(records: list[dict], columns: list[str], cutoff: dt.date) -> Path:
    path = PROJECT_DIR / f"missing_persons_ia_week_of_{cutoff.isoformat()}.csv"
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
    path = PROJECT_DIR / f"missing_persons_ia_week_of_{cutoff.isoformat()}_counts.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "value", "count"])
        for field in columns:
            counts = Counter(r[field] for r in records)
            for value, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                w.writerow([field, value, n])
    print(f"wrote {path.name}: counts for {columns}")
    return path


def scrape_from(rows: list[dict], session: requests.Session, cutoff: dt.date) -> list[dict]:
    in_window, skipped = [], 0
    for r in rows:
        d = parse_last_contact_date(r["last_contact_date"])
        if d is None:
            skipped += 1
            continue
        if d >= cutoff:
            in_window.append(r)
    print(f"list rows fetched: {len(rows)}   in past {WINDOW_DAYS} days "
          f"(last contact >= {cutoff.isoformat()}): {len(in_window)}"
          f"   unparseable dates skipped: {skipped}")

    seen, records = set(), []
    for i, r in enumerate(in_window, 1):
        if r["href"] in seen:
            continue
        seen.add(r["href"])
        detail = (fetch_detail(session, r["href"]) if r["href"]
                   else {"race": "Unknown", "sex": "Unknown"})
        sex = detail["sex"] or {"M": "Male", "F": "Female"}.get(r["gender"].upper(), "Unknown")
        records.append({
            "age_range": age_range(r["age_now"]),
            "race": detail["race"] or "Unknown",
            "sex": sex,
            "agency": r["agency"],
            "county": derive_county(r["agency"]),
        })
        if i < len(in_window):
            time.sleep(DETAIL_DELAY_SEC)
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
                     help="scrape the full site-wide agency list, report any not "
                          "covered by CITY_COUNTY, then exit without writing a CSV")
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
        rows = fetch_window(session, cutoff)
        records = scrape_from(rows, session, cutoff)
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
