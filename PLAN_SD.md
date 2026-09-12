# South Dakota Missing Persons Demographic Scrape

Second state, same goal as [PLAN.md](PLAN.md) (Nebraska): a de-identified CSV of
age range, race, county and sex for people reported missing in the past 7
days, suitable for a trend dashboard with no names or exact dates.

Source: https://missingpersons.sd.gov/ (South Dakota Missing Persons
Clearinghouse, an ASP.NET Core site run by the state).

## Decisions (confirmed 2026-09-11)

- **Tribal jurisdictions** (e.g. Pine Ridge Oglala Sioux Tribal PD, Cheyenne
  River Sioux Tribal PD): the `county` field carries the tribal agency's own
  name instead of a guessed single county, since these agencies police
  reservations that span multiple counties (revised 2026-09-11, per request -
  originally these were bucketed as a generic `Tribal jurisdiction / not
  county-specific`). These rows get no special exemption from the small-cell
  warning or `--suppress-small-counties`: an agency name here is just as
  specific as any county name elsewhere in the column.
- **Time window**: past 7 days, matching Nebraska's convention exactly
  ("unresolved cases by report week"). ~7 records/week currently vs.
  Nebraska's ~28/week - expected, given SD's smaller population.
- **Publish target**: the same public repo as Nebraska,
  `MackenzieJEvans/Missing_Person-s_Data` (confirmed 2026-09-11, after the
  tribal-agency-naming revision above). Nebraska's privacy posture - row-level
  CSV, no suppression, residual re-identification risk for single-record
  counties/agencies accepted and documented - carries over unchanged; see
  "Privacy posture" below.

## How the source differs from Nebraska (see scrape.py's PLAN.md for contrast)

**Easier:** every field needed - age, agency, race, sex, missing date - is
present in the initial GET of `/`. Age, sex, race are visible in the page
Nebraska only exposed via a per-record `/person/{id}` fetch. There's no
search-form POST, no session/CSRF replay, and no pagination: all currently
active records (98 as of 2026-09-11) render in one response inside repeated
`<article>` blocks under `#missingPersons`, with a `hfMissingPersonsCount`
hidden input as a self-check on the count. One GET per run, not ~1
request/second for a week's worth of detail pages.

**Harder: county derivation.** Nebraska's own reporting-agency dropdown lists
options grouped by county (each sheriff's office followed immediately by that
county's municipal agencies), so scrape.py builds the agency->county map live,
every run, straight from the site. South Dakota's dropdown
(`SearchedORIAgencyId`, ~224 options) is a flat alphabetical list with no such
grouping - there is nothing on the site itself that says Rapid City is in
Pennington County.

So [agency_county_map_sd.csv](agency_county_map_sd.csv) is a **static,
committed reference**, not something scrape_sd.py re-derives each run. It was
built once (2026-09-11) by:

1. Pulling the full ~224-option agency list from the live dropdown.
2. Classifying each agency:
   - `<County> Co. Sheriff's Office` -> that county directly (unambiguous).
   - Campus police (SDSU, USD, SDSM&T UPD) and one clearly single-site federal
     agency (VA Police - SF) -> the county of that campus/site.
   - Tribal police departments -> the agency's own name (per decision above).
     The three dropdown variants for the Yankton Sioux Tribal PD (`YANKTON
     SIOUX TRIBAL PD`, `Yankton Sioux Tribal Police Dept`, `Yankton Tribal
     Police Dept`) are normalized to one name, `Yankton Sioux Tribal Police
     Dept`, since they read as the same agency entered redundantly (the same
     dropdown has an unrelated exact duplicate, `Huron Police Dept`, under two
     different ids). `Kyle Oglala Sioux Tribal PD` and `Pine Ridge Oglala
     Sioux Tribal PD` are left as separate entries - unconfirmed whether Kyle
     is a distinct station or the same agency under another name.
   - True statewide/institutional agencies with no single home county
     (Highway Patrol, DCI, Bureau of Indian Affairs, State Radio
     Communications, the state penitentiary/women's prison entries) ->
     `Statewide / not county-specific`.
   - Everything else (municipal police departments) -> matched by city name
     against a public city-to-county list (Wikipedia's "List of municipalities
     in South Dakota", cross-checked against the U.S. Census Gazetteer
     convention of using each place's primary county when a city straddles a
     county line, e.g. Sioux Falls -> Minnehaha, not Minnehaha/Lincoln).
3. Verifying zero unmatched agencies against both the full dropdown and the
   live list of currently-active records (31 distinct agencies as of
   2026-09-11, all matched).

**This map needs periodic re-validation.** If South Dakota adds a new
municipal agency to the dropdown, scrape_sd.py will map it to `Unknown` and
print a warning (or run `python scrape_sd.py --check-agencies` to check
without scraping a full week). Extend agency_county_map_sd.csv by hand when
that happens - re-run the classification logic isn't automatic here the way
it is for Nebraska.

**Confidence caveat**: unlike Nebraska's map (mechanically derived from the
site's own grouping, so structurally correct by construction), this map's
municipal-PD rows depend on an external, human-curated source (Wikipedia) and
should be spot-checked before this ships to a public audience - particularly
for the handful of municipalities that straddle county lines, where a
"primary county" call was made.

## Output

`missing_persons_sd_week_of_YYYY-MM-DD.csv`, columns: `age_range`, `race`,
`county`, `sex` - identical schema to the Nebraska CSV for a combined/
comparable dashboard.

Age is bucketed into the same 10-year ranges as Nebraska
(0-9, ..., 70-79, 80+), from "Age Missing" (age when reported missing), not
current age.

### Deliberately excluded from the output

Name, the person's internal record id, missing date, exact age, current age,
eyes, hair, height, weight, remarks/last-seen text, agency phone, photo. Same
rationale as Nebraska: county is coarser than the raw agency string, which for
a single-record county/week would otherwise point straight back at the live
named listing. The one deliberate exception is tribal police departments,
where the `county` field *is* the agency name (see "Decisions" above) - a
narrower exposure than Nebraska's model, accepted because there is no coarser
label available that isn't misleading.

### Privacy posture

Confirmed 2026-09-11: publish a row-level CSV to the public repo, same as
Nebraska - age_range, race, county, sex, no suppression. The residual risk is
the same shape as Nebraska's: the source site is live and still carries name +
missing date + exact age + agency for the same 7-day window, so any county (or
named tribal agency) with a single record that week is re-identifiable by
cross-referencing the published row against the live named list. This week's
run has three such singletons (Bon Homme, Minnehaha, Cheyenne River Sioux
Tribal PD). `--suppress-small-counties` remains available in scrape_sd.py
(rolls county/agency values with < 5 records that week into "Other (small
county)") if the posture is revisited.

## Resources

Source data: https://missingpersons.sd.gov/
Agency dropdown reference: `SearchedORIAgencyId` select on the same page.
City-to-county source: https://en.wikipedia.org/wiki/List_of_municipalities_in_South_Dakota
