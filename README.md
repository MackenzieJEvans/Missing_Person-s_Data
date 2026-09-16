# Missing_Persons_Data

De-identified weekly demographics of active missing-persons cases across
seven states, for trend dashboards (age / race / sex / location) without
publishing anyone's identity.

| State | Source | Scope doc | Scraper |
| --- | --- | --- | --- |
| Nebraska | <https://statepatrol.nebraska.gov/services/missing-persons> | [PLAN.md](PLAN.md) | `scrape.py` |
| South Dakota | <https://missingpersons.sd.gov/> | [PLAN_SD.md](PLAN_SD.md) | `scrape_sd.py` |
| Iowa | <https://missingpersons.iowa.gov/> | [PLAN_IA.md](PLAN_IA.md) | `scrape_ia.py` |
| North Dakota | <https://missingpersons.nd.gov/search/all-missing-persons> | [PLAN_ND.md](PLAN_ND.md) | `scrape_nd.py` |
| Wyoming | <https://wyomingdci.wyo.gov/dci-homepage/missing-persons> | [PLAN_WY.md](PLAN_WY.md) | none - see below |
| Oklahoma | <https://okmissing.osbi.ok.gov/SEARCH_PERSON_PUBLIC.aspx> | [PLAN_OK.md](PLAN_OK.md) | `scrape_ok.py` |
| Missouri | <https://www.mshp.dps.missouri.gov/CJ51/Search> | [PLAN_MO.md](PLAN_MO.md) | `scrape_mo.py` |

All seven are public sites; none disallowed by robots.txt for the paths
scraped (North Dakota's and Wyoming's domains have no robots.txt at all).

**Wyoming has no scraper.** Its "database" is a Looker Studio (Google Data
Studio) embed with no plain-HTTP data path - getting a case's county/race/sex
requires clicking it in a real browser. This week's tiny CSV
(`missing_persons_wy_week_of_*.csv`) was produced by hand; see
[PLAN_WY.md](PLAN_WY.md) for what was tried and why, and for the option of a
browser-automation script if ongoing Wyoming coverage is wanted.

**Age isn't the same measure everywhere.** Nebraska, South Dakota, and
Missouri give age *when reported missing*. Iowa, North Dakota, and Oklahoma
only expose *current* age (Oklahoma's inferred, not labeled - a person
missing since 1963 is listed at age 98). Don't stack all seven states'
`age_range` columns as one measure in a combined dashboard without noting
the split - see PLAN_IA.md's first Decision for the full reasoning.

## Files

| File pattern | What it is |
| --- | --- |
| `scrape*.py` | One scraper per state with a scraper (see table above). stdlib + `requests` (Nebraska also uses `beautifulsoup4`). Each fetches the list, keeps the past 7 days, derives county from the reporting agency, drops every identifying field, writes the weekly CSV + a counts CSV. |
| `missing_persons_<state>_week_of_YYYY-MM-DD.csv` (no state suffix for Nebraska) | One row per person still listed as missing whose report/contact/last-seen date is within 7 days of the scrape. Columns: `age_range`, `race`, `county`, `sex`. |
| the matching `..._counts.csv` for each | The same week's data rolled up: how many people fall in each value of each column (e.g. how many 10-19, how many from Douglas County, how many women). Long format - `field, value, count` - so a chart tool can filter by `field` rather than needing one column per dimension. |
| `agency_county_map.csv` (NE) / `agency_county_map_sd.csv` (SD) | Committed agency-to-county lookups. Nebraska's is generated fresh each run straight from the site's own county-grouped agency dropdown. South Dakota's is a static, hand-built reference (its dropdown has no county grouping) - see PLAN_SD.md. |
| `agency_county_map_ia.csv` / `agency_county_map_nd.csv` / `agency_county_map_ok.csv` / `agency_county_map_mo.csv` | Audit trails for Iowa/North Dakota/Oklahoma/Missouri, regenerated fresh every run (not committed as static references) - each state's derivation is mostly or entirely mechanical (the county is often in the agency's own name or code); the externally-sourced piece for the hybrid states (a city->county table) lives inline in each `scrape_*.py` as `CITY_COUNTY` - see the matching PLAN doc. |
| `PLAN*.md` | Scope, method, and the privacy decisions behind what is and isn't published, per state. |
| `design/` | Dashboard source (design-canvas `.dc.html` files + the published HTML dashboards for Nebraska, South Dakota, and Iowa so far). |

Not published, for any state: name, the site's internal person id/detail-page
link/poster link, missing/contact/last-seen date, exact or raw age, the
specific reporting agency string, and all physical-description fields.

## Regenerating

```
pip install requests beautifulsoup4
python scrape.py --include-county --include-sex   # Nebraska
python scrape_sd.py                               # South Dakota
python scrape_ia.py                               # Iowa
python scrape_nd.py                               # North Dakota
python scrape_ok.py                                # Oklahoma
python scrape_mo.py                                # Missouri
```

(No script for Wyoming - see above.)

`--suppress-small-counties` (all six scripts) rolls any county with fewer
than 5 records that week into "Other (small county)"; `--dry-run` prints the
summary without writing. `scrape_sd.py --check-agencies`,
`scrape_ia.py --check-agencies`, `scrape_nd.py --check-agencies`,
`scrape_ok.py --check-agencies`, and `scrape_mo.py --check-agencies` report
any agency the county map doesn't yet cover, without scraping a full week.

Missouri's site is intermittently flaky about returning a complete result
for one of its two person-type queries (adults/juveniles) - `scrape_mo.py`
retries automatically against the page's own result count, so this
shouldn't need manual attention, but see PLAN_MO.md if a weekly count looks
suspiciously low.

## Reading the numbers

Records leave each source once the person is found, so a weekly file counts
**people still missing at scrape time whose case opened that week**, not
everyone reported missing that week. Someone reported and found within the
window never appears. Label charts accordingly (e.g. "unresolved cases by
report week").

A county (or, for the several states with multi-county tribal/regional
agencies - South Dakota, North Dakota, Oklahoma, Missouri - a named agency)
with only one or two records in a given week, combined with the still-live
named source list, can be traced back to an individual. Weeks are small -
treat single-cell counts as sensitive. See each state's PLAN doc for the
specific privacy-posture decision on record.
