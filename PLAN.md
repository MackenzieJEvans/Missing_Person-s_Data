# Missing Persons Data

The goal is to create a csv of from https://statepatrol.nebraska.gov/services/missing-persons including age, agency, missing date, sex and race. 

The goal of this is to be able to make the csv into a dashboard that shows trends without names to examine trends and statitstics of missing people in the state.  Similar to dashboards like those on DHHS drug overdose statistics page and NDOT's crash statistics page where numbers and locations of instances are shown, but no personal data is archived, like names of people who were missing, but were found and removed from the website. 

Per follow-up direction, the scope is narrowed to:
Past 7 days only (volume varies week to week; ~28 records were live on 2026-09-08 — treat as illustrative, not a fixed expectation)
No missing date in the output
Age as 10-year ranges, not exact values
County, derived from the reporting agency (the agency string itself is not published). See "Deriving county" below.
Race is included; it comes only from the per-person detail page, so the script must fetch /person/{id} for each past-week row.
Sex: TBC — the goal text says include it, the 2026-09-10 publish instruction listed only age_range and race. Confirm before the CSV ships.
Output CSV is published to the public GitHub repo MackenzieJEvans/Missing_Person-s_Data (decided 2026-09-10).

### How the source actually works (verified 2026-09-08)
Search	Drupal form #missing-persons-search-form, POST to the same URL, requires a per-session form_build_id + form_id
Blank search	Returns all 471 active records on one page — no pagination
Sort order	Missing date descending, so the past week is the top of the list
List fields	data-id, Name, Missing Date, Age Missing, Current Age, Agency Name
Sex / Race	Not in the list. Only via GET /person/{id}
Detail endpoint	https://statepatrol.nebraska.gov/person/{id} — plain GET, no session or token needed, returns a small HTML fragment
Detail fields	Name, Age When Missing, Current Age, Race, Sex, Eyes, Hair, Height, Weight, Missing Date, Remarks, Description, Last Seen, Agency Name, Agency Phone
robots.txt	Stock Drupal. Neither /services/missing-persons nor /person/ is disallowed
Past-week volume	28 records across 8 agencies (18 of them Omaha PD)

### What a weekly count actually measures

Records are removed from the source once the person is found. A past-7-days scrape therefore captures people *still missing at scrape time*, not everyone *reported missing that week* — someone reported and found within the window never appears. The dashboard should label this "unresolved cases by report week", not "people reported missing per week", and the CSV/README should carry the same caveat. Run on a fixed weekly schedule so windows are consistent.

## Approach

Single Python script (scrape.py), stdlib + requests + beautifulsoup4.

Fetch the full list
GET /services/missing-persons, parse form_build_id and form_id out of the form.
POST the same URL with all filters at their defaults (op=Submit, selects = All, name fields blank) using the same requests.Session.
Parse .nsp-missing-list > .u-row. Select direct children only — nested descendants also match a naive "contains Missing Date" filter and inflate the count roughly 3x.
Per row, read data-id and the .u-info pairs keyed by .u-label text (Missing Date:, Age Missing:, Agency Name:).

### Filter to the past week

Keep rows where Missing Date >= today - 7 days. Dates are MM-DD-YYYY. Scan and filter every row rather than stopping at the first row past the cutoff: the full list returns in one response (no pagination to avoid), and early-stop breaks silently if the sort is not perfectly monotonic. Rows with an unparseable or blank date are logged and skipped. The date is used only as a filter and is never written to the output.

### Fetch sex and race per row

For each row that survives the past-week filter, GET https://statepatrol.nebraska.gov/person/{data-id}. Parse Race and Sex from the fragment by their label text; normalize whitespace/casing. If a label is absent, emit the value as "Unknown".
- Space the detail requests (~1 req/sec) and send a descriptive User-Agent.
- The detail fragment contains the person's name. Do not write raw responses to disk, logs, or cache. data-id is held only in memory to build the URL and is never emitted.

### Deriving county

The source's own agency dropdown (reporting_agency_select on the search form, ~249 options) is organized by county: each option is either a sheriff entry "<County> CO SO <SeatTown>" or a municipal/other agency listed immediately under its county's sheriff. So a map is built by scanning the dropdown options in document order, tracking the last "<County> CO SO ..." seen, and assigning every following non-SO option to that county until the next SO entry.
- Build this map at run time from the live form, and also write it to agency_county_map.csv (committed) so the derivation is auditable and diffable.
- The trailing block of the dropdown — Nebraska State Patrol, "SP <city>", Carrier Enforcement, FBI, USAF/ANG, Dept of Vet Affairs — is not under any county cluster. These map to "Statewide / not county-specific".
- A record whose agency string does not match any dropdown option maps to "Unknown"; log it so the map can be extended.
- Match on a normalized agency string (collapse whitespace, case-fold); expect minor spelling drift between the list rows and the dropdown labels.

### Emit the CSV

missing_persons_week_of_YYYY-MM-DD.csv

Column	Values
age_range	0-9, 10-19, 20-29, 30-39, 40-49, 50-59, 60-69, 70-79, 80+ (bucketed from "Age Missing", not current age)
race	Value from the detail page, whitespace-normalized, verbatim otherwise; "Unknown" if absent
county	Derived from the reporting agency (see above); "Statewide / not county-specific" or "Unknown" where it can't be pinned
sex	Value from the detail page ("M"/"F"/...), whitespace-normalized; "Unknown" if absent

Final column set (decided 2026-09-10): age_range, race, county, sex. No small-cell suppression — --suppress-small-counties exists in scrape.py but is not used for the published file.

### Deliberately excluded from the output
name, data-id, missing date, exact age, current age, reporting agency string, eyes, hair, height, weight, description, remarks, last seen, agency phone, photo.

data-id in particular must not ship — it is the /person/{id} key, so publishing it would hand any reader the name back in one request. The agency string is dropped too: it is finer-grained than county and, combined with the 7-day window, points at the same small municipal caseloads.

### Privacy posture (decided 2026-09-10)

Decision: publish a row-level CSV to the public repo with age_range + race + county + sex, no suppression.

The residual risk was raised explicitly and accepted: the source site is live and still carries name + missing date + exact age + agency for the same 7-day window, so a county with a single record that week (this run: Colfax, Buffalo, Sherman — 1 each) is re-identifiable by anyone who cross-references the published row against the live named list. The mitigations in place are the 10-year age band, county instead of the exact agency, and no missing-date column; small-cell suppression and a publication lag were considered and declined.

Levers still available in scrape.py if the posture changes later: `--suppress-small-counties` (rolls counties with < 5 records that week into "Other (small county)"), dropping `--include-county`, or a publication lag.

Repo confirmed public on 2026-09-10 (GitHub API, "visibility": "public") — every committed CSV is world-readable and retained in git history even if later removed.

### Resources 

Remove names from public document. Only provide age, agency, sex, and race. Use agency to find county. 

Source data: https://statepatrol.nebraska.gov/services/missing-persons

Model similar to these data dashboards: https://app.powerbigov.us/view?r=eyJrIjoiNGM2YmI5YTQtOTU3ZS00ZTUxLWE4NjgtN2IxYTkyN2Q1MDA5IiwidCI6IjA0MzIwN2RmLWU2ODktNGJmNi05MDIwLTAxMDM4ZjExZjBiMSJ9 
https://experience.arcgis.com/experience/3f46876d8cfa4f42981a6ba4922d554d/page/Statistical-Data-and-Maps 

Where no names, just demographics of age, sex and location and numbers of missing people per week. Mostly bar charts based on age, sex and location. 
