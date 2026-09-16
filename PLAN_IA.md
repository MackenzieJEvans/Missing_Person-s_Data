# Iowa Missing Persons Demographic Scrape

Third state, same goal as [PLAN.md](PLAN.md) (Nebraska) and
[PLAN_SD.md](PLAN_SD.md) (South Dakota): a de-identified CSV of age range,
race, county and sex for people reported missing in the past 7 days.

Source: https://missingpersons.iowa.gov/ (Iowa Missing Person Information
Clearinghouse, a Drupal 10 site run by the Iowa Department of Public Safety).

## Decisions (confirmed 2026-09-15)

- **Age caveat - read this first.** Iowa's site does not expose age at the
  time a person was reported missing anywhere, on the list or the detail
  page - only "Age Now" (current age, computed from DOB). Nebraska's and
  South Dakota's `age_range` columns are bucketed from age *when reported
  missing*; Iowa's `age_range` is bucketed from age *today*. For a 7-day
  window the two nearly always agree, but they are not the same measure, and
  they drift further apart on any re-run against stale cached data. **A
  combined NE/SD/IA dashboard should not silently stack these as one
  measure** - label Iowa's age column distinctly, or note the difference
  wherever the three states are shown together.
- **Time window**: past 7 days, matching Nebraska and South Dakota exactly.
  22 records this week - between Nebraska's ~28 and South Dakota's ~7, in
  line with Iowa's population being between the two.
- **Tribal agency (Meskwaki Nation Police)**: mapped to **Tama County**, not
  named directly the way South Dakota's tribal PDs are. This is a deliberate
  divergence from South Dakota's rule, not an oversight: South Dakota's
  reservations span multiple counties, so no single county name was accurate
  and naming the agency was the only honest option. The Meskwaki Settlement
  sits entirely within Tama County (verified), so a plain county value is
  both accurate and keeps Iowa's `county` column comparable to Nebraska's and
  South Dakota's non-tribal rows. If this reasoning turns out to be wrong -
  i.e. Meskwaki jurisdiction turns out not to track Tama County cleanly -
  revert to naming the agency directly, per the South Dakota precedent.
- **Publish target**: not yet decided (same posture as South Dakota was
  before its own publish decision). Nothing is pushed to the public repo
  until asked.

## How the source differs from Nebraska and South Dakota

**Pagination, unlike either.** ~250+ active cases across 5 pages of ~51 each,
not one page (South Dakota) or a session-scoped POST (Nebraska). The list
supports `sort_by=field_mp_last_contact_date_value&sort_order=DESC`, confirmed
monotonic across pages by direct check (page 0 runs 09/15 down to 08/11 as of
2026-09-15; page 1 continues from 08/10 down to 2025). scrape_ia.py fetches
pages newest-first and stops once a page's oldest date is before the cutoff,
with a hard `MAX_PAGES` cap in case the site's sort ever regresses.

**A redirect trap**: the site's own exposed-filter form submits to
`/divisions/criminal-investigation/missing-persons`, but a GET to that path
*with a query string* triggers a meta-refresh redirect to the site root that
drops the query string entirely (confirmed - `curl` without `-L` shows the
`<meta http-equiv="refresh">` target losing the path). Sorting and pagination
only work against `/`. scrape_ia.py hits `/` directly; this is load-bearing,
not a simplification.

**Race is detail-page-only, like Nebraska**; unlike South Dakota, which puts
everything in the list markup. Age, sex, agency and last-contact-date are all
on the list page, so only race needs the per-record fetch (sex is re-fetched
from the same page for consistency, though the list's abbreviated `Gender:
F/M` already agrees).

**County derivation is a hybrid of Nebraska's and South Dakota's methods.**
County sheriff offices and county dispatch/law-enforcement centers name their
county directly in the agency string - `BLACK HAWK COUNTY SO, WATERLOO`,
`WEBSTER CO LEC, FT DODGE`, `DUBUQUE CO COMM, DUBUQUE` - so those are parsed
mechanically, no external data, the same way Nebraska's whole map is built.
Municipal police departments give only a city (`WATERLOO PD, WATERLOO`,
`DES MOINES PD, DES MOINES`), which needs an external city->county lookup,
the same problem South Dakota has. That lookup is `CITY_COUNTY` in
scrape_ia.py - a plain dict, not a separate CSV, since only ~44 cities
actually appear as an agency's city across the 80 distinct agencies live on
the site as of 2026-09-15 (small enough to review inline; see "Confidence
caveat" below for how it was built). `agency_county_map_ia.csv` is instead
**regenerated fresh every run**, like Nebraska's map, since it's a mechanical
function of live data + CITY_COUNTY, not something requiring its own git
history for audit - the git history of `CITY_COUNTY` in scrape_ia.py serves
that purpose for the one part that is externally sourced.

One consistency fix made during testing: the county-sheriff regex path
originally emitted `"Polk County"` while the city-lookup path emitted
`"Polk"` for the exact same county - `POLK COUNTY SO, DES MOINES` and
`DES MOINES PD, DES MOINES` need to land on the same value. Both paths now
emit the plain name (`"Polk"`), matching South Dakota's convention.

Two Iowa-specific naming traps this project needed to get right (both are
avoided by construction, since the regex path only ever reads the word(s)
*before* "COUNTY SO"/"CO LEC"/"CO COMM", never the trailing city):
- The city **Wapello** (Louisa County) is not the same place as **Wapello
  County** (seat: Ottumwa).
- The city **Jefferson** (seat of Greene County) is not the same place as
  **Jefferson County** (seat: Fairfield).

**Confidence caveat on `CITY_COUNTY`**: built 2026-09-15 from Wikipedia's
"List of cities in Iowa" comprehensive incorporated-places table, parsed
locally from the raw table (not from a page-summary tool, to avoid
truncation/hallucination risk on a page that large). Independently
cross-checked for every city that is also a county seat (~30 of the ~44
entries) against this site's own county-sheriff/CO-LEC agency strings, which
are site-authoritative and need no external source - every one of those
agreed. The remaining ~14 entries (non-county-seat cities: Ames, Altoona,
Ankeny, Atlantic, Bettendorf, Camanche, Carter Lake, Cedar Falls, Cedar
Rapids, Council Bluffs, Davenport, Le Claire, Marion, Urbandale, Waukee,
Webster City) rest on Wikipedia alone and have not been cross-checked against
a second source. Known source quirks folded into `CITY_COUNTY` as aliases:
`MT PLEASANT` (abbreviation used on the live site for Mount Pleasant),
`LECLAIRE` (no-space spacing variant used on the live site for Le Claire),
and `ROCKELL CITY` (a source typo for Rockwell City, verbatim on the live
site - not fuzzy-matched, added as an explicit alias with a comment).

**This map needs periodic re-validation.** Run
`python scrape_ia.py --check-agencies` to check every currently-active
agency site-wide against `CITY_COUNTY` without scraping a full week; it exits
non-zero and lists anything unmatched. Extend `CITY_COUNTY` in scrape_ia.py
by hand when the site adds a new municipal agency.

## Output

`missing_persons_ia_week_of_YYYY-MM-DD.csv`, columns: `age_range`, `race`,
`county`, `sex` - same column names as Nebraska/South Dakota, but see the age
caveat above before treating `age_range` as the same measure across states.

### Deliberately excluded from the output

Name, the detail-page URL slug, last-contact date, exact/current age, raw
agency string, weight, height, hair color, eye color, photo. Same rationale
as the other two states: county is coarser than the raw agency string, which
for a single-record county/week would otherwise point straight back at the
live named listing.

### Privacy posture

Not yet decided - see "Publish target" above. If Iowa data joins the same
public repo, Nebraska's and South Dakota's posture (row-level CSV, no
suppression, residual re-identification risk for single-record
counties/agencies accepted and documented) is the default this would
inherit, pending confirmation. `--suppress-small-counties` exists in
scrape_ia.py (rolls counties with < 5 records that week into "Other (small
county)") if the posture ends up different.

## Resources

Source data: https://missingpersons.iowa.gov/
City-to-county source: https://en.wikipedia.org/wiki/List_of_cities_in_Iowa
Meskwaki Settlement county confirmation: https://en.wikipedia.org/wiki/Meskwaki_Settlement
