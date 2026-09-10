# Missing Persons Data

The goal is to create a csv of from https://statepatrol.nebraska.gov/services/missing-persons including age, agency, missing date, sex and race. 

The goal of this is to be able to make the csv into a dashboard that shows trends without names to examine trends and statitstics of missing people in the state.  Similar to dashboards like those on DHHS drug overdose statistics page and NDOT's crash statistics page where numbers and locations of instances are shown, but no personal data is archived, like names of people who were missing, but were found and removed from the website. 

Per follow-up direction, the scope is narrowed to:
Past 7 days only (~28 records as of 2026-09-08)
No missing date in the output
Age as 10-year ranges, not exact values
Reporting agency instead of derived county

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

## Approach

Single Python script (scrape.py), stdlib + requests + beautifulsoup4.

Fetch the full list
GET /services/missing-persons, parse form_build_id and form_id out of the form.
POST the same URL with all filters at their defaults (op=Submit, selects = All, name fields blank) using the same requests.Session.
Parse .nsp-missing-list > .u-row. Select direct children only — nested descendants also match a naive "contains Missing Date" filter and inflate the count roughly 3x.
Per row, read data-id and the .u-info pairs keyed by .u-label text (Missing Date:, Age Missing:, Agency Name:).

### Filter to the past week

Keep rows where Missing Date >= today - 7 days. Dates are MM-DD-YYYY. Because the list is sorted descending, the scan can stop at the first row past the cutoff. The date is used only as a filter and is never written to the output.

### Emit the CSV

missing_persons_week_of_YYYY-MM-DD.csv

Column	Values
age_range	0-9, 10-19, 20-29, 30-39, 40-49, 50-59, 60-69, 70-79, 80+
agency	Reporting agency string, verbatim from source

### Deliberately excluded: 
name, data-id, missing date, current age, eyes, hair, height, weight, description, remarks, last seen, agency phone, photo.

data-id in particular must not ship — it is the /person/{id} key, so publishing it would hand any reader the name back in one request.

Small-cell suppression — publish row-level CSV as-is, or roll agencies with fewer than ~5 records into Other, or publish only per-dimension aggregate counts? This is the main remaining privacy lever.

### Resources 

Remove names from public document. Only provide age, agency, sex, and race. Use agency to find county. 

Source data: https://statepatrol.nebraska.gov/services/missing-persons

Model similar to these data dashboards: https://app.powerbigov.us/view?r=eyJrIjoiNGM2YmI5YTQtOTU3ZS00ZTUxLWE4NjgtN2IxYTkyN2Q1MDA5IiwidCI6IjA0MzIwN2RmLWU2ODktNGJmNi05MDIwLTAxMDM4ZjExZjBiMSJ9 
https://experience.arcgis.com/experience/3f46876d8cfa4f42981a6ba4922d554d/page/Statistical-Data-and-Maps 

Where no names, just demographics of age, sex and location and numbers of missing people per week. Mostly bar charts based on age, sex and location. 
