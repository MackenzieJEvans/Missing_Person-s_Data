# North Dakota Missing Persons Demographic Scrape

Fourth state, same goal as [PLAN.md](PLAN.md) (Nebraska), [PLAN_SD.md](PLAN_SD.md)
(South Dakota) and [PLAN_IA.md](PLAN_IA.md) (Iowa): a de-identified CSV of age
range, race, county and sex for people reported missing in the past 7 days.

Source: https://missingpersons.nd.gov/search/all-missing-persons (a
server-rendered Blazor/DataTables app run by the ND Attorney General's
office).

## Decisions (confirmed 2026-09-15)

- **Age caveat, same as Iowa's**: the site exposes only "Current Age", never
  age at the time reported missing. For a 7-day window the two nearly
  coincide but are not the same measure - see PLAN_IA.md's first Decision
  for the full reasoning, which applies here unchanged. Don't stack North
  Dakota's `age_range` with Nebraska's/South Dakota's as one measure in a
  combined dashboard without labeling the difference.
- **Time window**: past 7 days, matching the other three states. 4 records
  this week - North Dakota has the smallest population of the four states
  covered so far, and the smallest weekly count (Nebraska ~28, Iowa ~22,
  South Dakota ~7, North Dakota ~4).
- **Tribal/BIA agencies**: all three that appear on the site - BIA Fort
  Totten Agency, BIA OJS Standing Rock Agency, BIA Turtle Mountain Agency -
  are named directly in the `county` field rather than assigned a county.
  Each reservation was checked individually (not assumed from the South
  Dakota precedent): Spirit Lake/Fort Totten spans five counties (Benson,
  Eddy, Ramsey, Wells, Nelson); Standing Rock spans Sioux County, ND and
  Corson County, SD; Turtle Mountain's reservation-proper is mostly Rolette
  County, but its dispersed off-reservation trust lands reach into 22
  counties across North Dakota, Montana and South Dakota. None of the three
  has a single accurate county the way Iowa's Meskwaki Settlement does (see
  PLAN_IA.md) - hence the South Dakota rule applies here, not Iowa's.
- **Publish target**: not yet decided. Nothing pushed to the public repo
  until asked.

## How the source differs from the other three states

**Closest to South Dakota**: one GET to the search page returns every
currently-active case - 57 as of 2026-09-15 - with age, race, sex, last-seen
date and agency already in the list markup. No per-record detail-page fetch,
no session/CSRF form, no pagination (DataTables paginates client-side over
JS; the full table is already in the server-rendered HTML, confirmed by
counting `<tr>` blocks directly against the raw response - 57 rows in the
one GET). robots.txt doesn't exist on this domain (404), so nothing to check
there.

**County derivation is the Iowa-style hybrid**: `"<County> COUNTY
SHERIFF"`/`"<County> COUNTY DISPATCH CENTER"` (Stutsman County's dispatch
center is the one non-sheriff county agency) name their county directly in
the agency string - mechanical, no external data. Municipal police
departments give only a city name with no county attached
(`"BISMARCK POLICE DEPARTMENT"`), needing the small `CITY_COUNTY` table in
scrape_nd.py - only 8 cities appear as an agency's city across the 24
distinct agencies live on the site, verified against Wikipedia's "List of
cities in North Dakota" and cross-checked where possible against this site's
own data (Williston agrees with `WILLIAMS COUNTY SHERIFF`; Grand Forks is a
self-named county). One override needed: `"GRAND FORKS UND POLICE DEPT."` is
the University of North Dakota's campus police, named after the university
rather than its city, so a generic "strip the department-name suffix" rule
would leave "GRAND FORKS UND" instead of "GRAND FORKS" - handled with an
explicit `AGENCY_CITY_OVERRIDES` entry rather than a fuzzier stripping rule.

North Dakota's county names include the same `Mc`-prefix trap Nebraska's map
already had a fixup for (`Mc Pherson` -> `McPherson`), plus one more:
`LaMoure County`. Both `.title()`-breaks are corrected in `COUNTY_NAME_FIXUPS`.

**This map needs periodic re-validation.** Run
`python scrape_nd.py --check-agencies` to check every currently-active
agency against `CITY_COUNTY` without narrowing to a week first; it exits
non-zero and lists anything unmatched. Extend `CITY_COUNTY` (or
`AGENCY_CITY_OVERRIDES`/`TRIBAL_AGENCIES`) by hand when the site adds a new
agency.

**Confidence caveat**: `CITY_COUNTY`'s 8 entries came from a single source
(Wikipedia's North Dakota cities list) rather than the two-source
cross-validation Iowa's larger table got; only Williston and Grand Forks
have independent, site-authoritative confirmation. Worth a second look
before this ships to a wide audience, though the risk surface is small (8
cities, all well-known population centers, not obscure incorporated places).

## Output

`missing_persons_nd_week_of_YYYY-MM-DD.csv`, columns: `age_range`, `race`,
`county`, `sex` - same column names as the other three states, with the
age-measure caveat above.

### Deliberately excluded from the output

Name, the detail-page URL slug, last-seen date, exact/current age, raw
agency string, photo. Same rationale as the other three: county (or, for the
three tribal agencies, the agency's own name) is coarser than the full
record, which for a single-record county/week would otherwise point straight
back at the live named listing.

### Privacy posture

Not yet decided - see "Publish target" above. If North Dakota data joins the
same public repo, the other three states' posture (row-level CSV, no
suppression, residual re-identification risk for single-record
counties/agencies accepted and documented) is the default this would
inherit, pending confirmation. This week's run is a strong example of the
risk: all 4 records are singleton counties (Burleigh, Cass, Stark, Morton).
`--suppress-small-counties` exists in scrape_nd.py if the posture ends up
different.

## Resources

Source data: https://missingpersons.nd.gov/search/all-missing-persons
City-to-county source: https://en.wikipedia.org/wiki/List_of_cities_in_North_Dakota
Reservation-county checks: https://en.wikipedia.org/wiki/Spirit_Lake_Reservation ,
https://en.wikipedia.org/wiki/Turtle_Mountain_Indian_Reservation
