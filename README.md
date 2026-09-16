# Missing_Persons_Data

De-identified weekly demographics of active missing-persons cases in
Nebraska, South Dakota, and Iowa, for trend dashboards (age / race / sex /
location) without publishing anyone's identity.

| State | Source | Scope doc |
| --- | --- | --- |
| Nebraska | <https://statepatrol.nebraska.gov/services/missing-persons> | [PLAN.md](PLAN.md) |
| South Dakota | <https://missingpersons.sd.gov/> | [PLAN_SD.md](PLAN_SD.md) |
| Iowa | <https://missingpersons.iowa.gov/> | [PLAN_IA.md](PLAN_IA.md) |

All three are public sites, none disallowed by robots.txt for the paths
scraped. Race comes from a per-person detail-page fetch on all three; Iowa's
`age_range` is bucketed from *current* age rather than age when reported
missing, since that's the only age field the site exposes - see
[PLAN_IA.md](PLAN_IA.md)'s first Decision before treating it as the same
measure as Nebraska's/South Dakota's.

## Files

| File | What it is |
| --- | --- |
| `scrape.py` / `scrape_sd.py` / `scrape_ia.py` | One scraper per state. stdlib + `requests` (Nebraska also uses `beautifulsoup4`). Each fetches the list, keeps the past 7 days, derives county from the reporting agency, drops every identifying field, writes the weekly CSV + a counts CSV. |
| `missing_persons_week_of_YYYY-MM-DD.csv` (NE) / `missing_persons_sd_week_of_YYYY-MM-DD.csv` / `missing_persons_ia_week_of_YYYY-MM-DD.csv` | One row per person still listed as missing whose report/last-contact date is within 7 days of the scrape. Columns: `age_range`, `race`, `county`, `sex`. |
| the matching `..._counts.csv` for each | The same week's data rolled up: how many people fall in each value of each column (e.g. how many 10-19, how many from Douglas County, how many women). Long format - `field, value, count` - so a chart tool can filter by `field` rather than needing one column per dimension. |
| `agency_county_map.csv` (NE) / `agency_county_map_sd.csv` | Agency-to-county lookups. Nebraska's is generated fresh each run straight from the site's own county-grouped agency dropdown. South Dakota's is a static, hand-built reference (its dropdown has no county grouping) - see PLAN_SD.md. |
| `agency_county_map_ia.csv` | Iowa's agency-to-county audit trail, regenerated fresh each run like Nebraska's - Iowa's derivation is mostly mechanical (county name is often in the agency string itself); the one externally-sourced piece, a city->county table for municipal police departments, lives as `CITY_COUNTY` inside `scrape_ia.py` - see PLAN_IA.md. |
| `PLAN.md` / `PLAN_SD.md` / `PLAN_IA.md` | Scope, method, and the privacy decisions behind what is and isn't published, per state. |
| `design/` | Dashboard source (design-canvas `.dc.html` files + the published HTML dashboards for Nebraska and South Dakota). |

Not published, for any state: name, the site's internal person id or detail-page
link, missing/last-contact date, exact or raw age, the specific reporting
agency string, and all physical-description fields.

## Regenerating

```
pip install requests beautifulsoup4
python scrape.py --include-county --include-sex   # Nebraska
python scrape_sd.py                               # South Dakota
python scrape_ia.py                               # Iowa
```

`--suppress-small-counties` (all three) rolls any county with fewer than 5
records that week into "Other (small county)"; `--dry-run` prints the summary
without writing. `scrape_sd.py --check-agencies` and `scrape_ia.py
--check-agencies` report any agency the county map doesn't yet cover, without
scraping a full week.

## Reading the numbers

Records leave each source once the person is found, so a weekly file counts
**people still missing at scrape time whose case opened that week**, not
everyone reported missing that week. Someone reported and found within the
window never appears. Label charts accordingly (e.g. "unresolved cases by
report week").

A county (or, for South Dakota, a named tribal agency) with only one or two
records in a given week, combined with the still-live named source list, can
be traced back to an individual. Weeks are small - treat single-cell counts
as sensitive. See each state's PLAN doc for the specific privacy-posture
decision on record.
