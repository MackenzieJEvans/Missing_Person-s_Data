# Missing_Persons_Data

De-identified weekly demographics of active missing-persons cases in Nebraska,
for trend dashboards (age / race / sex / location) without publishing anyone's
identity.

Source: Nebraska State Patrol missing-persons list,
<https://statepatrol.nebraska.gov/services/missing-persons> (public; not
disallowed by robots.txt). Race and sex come from the per-person detail pages.

## Files

| File | What it is |
| --- | --- |
| `scrape.py` | Fetches the list, keeps the past 7 days, derives county from the reporting agency, drops every identifying field, writes the weekly CSV. stdlib + `requests` + `beautifulsoup4`. |
| `missing_persons_week_of_YYYY-MM-DD.csv` | One row per person still listed as missing whose missing date is within 7 days of the scrape. Columns: `age_range` (10-year band from age when missing), `race`, `county`, `sex`. |
| `agency_county_map.csv` | The agency to county lookup, generated from the source's own reporting-agency dropdown (which is ordered county-by-county). Committed so the derivation is auditable. |
| `PLAN.md` | Scope, method, and the privacy decisions behind what is and isn't published. |

Not published: name, the site's internal person id, missing date, exact age, the
specific reporting agency, and all physical-description fields.

## Regenerating

```
pip install requests beautifulsoup4
python scrape.py --include-county --include-sex
```

`--suppress-small-counties` rolls any county with fewer than 5 records that week
into "Other (small county)"; `--dry-run` prints the summary without writing.

## Reading the numbers

Records leave the source once the person is found, so a weekly file counts
**people still missing at scrape time whose case opened that week**, not everyone
reported missing that week. Someone reported and found within the window never
appears. Label charts accordingly (e.g. "unresolved cases by report week").

A county with only one or two records in a given week, combined with the still-live
named source list, can be traced back to an individual. Weeks are small (25
records in the first run), so treat single-county cells as sensitive.
