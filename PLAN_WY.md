# Wyoming Missing Persons Demographic Scrape

Fifth state, same goal as [PLAN.md](PLAN.md) (Nebraska), [PLAN_SD.md](PLAN_SD.md)
(South Dakota), [PLAN_IA.md](PLAN_IA.md) (Iowa) and [PLAN_ND.md](PLAN_ND.md)
(North Dakota): a de-identified CSV of age range, race, county and sex for
people reported missing in the past 7 days.

Source: https://wyomingdci.wyo.gov/dci-homepage/missing-persons (Wyoming
Division of Criminal Investigation).

## This one does not have a scrape script - read this first

Every other state's data is either static server-rendered HTML or a plain
form `GET`/`POST` - reachable with `requests` and no browser. Wyoming's page
is a Google Sites page whose actual "MISSING PERSON DATABASE" is not HTML at
all: it's an **embedded Looker Studio (Google Data Studio) report**
(`lookerstudio.google.com/embed/reporting/87d1bf76-.../page/RdPWD`). Findings
from investigating it directly (2026-09-15):

- `curl`/plain HTTP gets nothing: the report's data isn't in the page source
  in any form (verified - searched the raw HTML for known names in the
  dataset; zero matches). It's fetched at render time by JS running inside a
  cross-origin iframe.
- The report has two parts: a summary table (Days Missing, Name, Last
  Contact, free-text Information, Current Age) showing all rows at once, and
  a **detail panel that only populates once you click a row** - County, Race,
  Sex, and Age When Last Seen (the fields this project actually needs, other
  than age) all live in that detail panel, not the summary table.
- Each field in the detail panel is its own small chart component, and each
  one issues its own `POST` to `lookerstudio.google.com/embed/batchedDataV2`
  when the row-name filter changes - confirmed by inspecting the response
  body of one such call (a single-value dataset already filtered to the
  clicked name). This is Looker Studio's internal, undocumented
  cross-filtering protocol, not a stable API.
- Tried to capture a replayable request by patching `window.fetch` from the
  parent page before clicking a row: it caught nothing, because the fetch
  happens inside the cross-origin iframe, which a parent-page script can't
  instrument. Getting a replayable request would mean reverse-engineering
  Looker Studio's request-signing from inside that iframe's own JS context -
  fragile even if achieved, since it's an internal protocol Google can change
  without notice, not a documented API meant for this kind of use.

**Net effect**: getting county/race/sex/age-when-last-seen for a case
requires actually clicking it in a real browser. There is no
`pip install requests` path here the way there is for the other four states.

## What was done for this week's CSV (2026-09-15)

The summary table is sorted by Days Missing ascending, i.e. newest first, so
the past-7-days window is always at the top and doesn't require scanning
93 rows: as of 2026-09-15, only two rows had a Last Contact date on or after
the 2026-09-08 cutoff (Bruner, Alicia - 9/14/2026; Drake, Ernest - 9/8/2026;
the next row, Hettinger, Logan, was 9/1/2026 - outside the window). Both were
clicked individually in a live browser session and their detail-panel fields
read off directly:

| Name (not published) | Age when last seen | Race | Sex | County |
| --- | --- | --- | --- | --- |
| Bruner, Alicia | 17 | White | F | Fremont |
| Drake, Ernest | 13 | White | M | Natrona |

Unlike every other state so far, **county is a field the source provides
directly** - no agency-to-county derivation, no city lookup table, no
sheriff-name regex. Same for age: Wyoming's detail panel gives "Age When
Last Seen" directly, so there's no current-age-vs-age-when-missing caveat
here the way there is for Iowa and North Dakota.

## Options for future weeks

1. **Repeat this manually** - re-open the report, note which rows fall in
   the new window (still fast, since it's sorted newest-first), click each,
   record County/Race/Sex/Age. Fine at Wyoming's volume (2 this week) but
   doesn't scale if a week is ever unusually large, and it's a person doing
   it, not a script.
2. **A browser-automation script** (Playwright or Selenium) that opens the
   report, reads the sorted summary table to find this week's rows, clicks
   each, and reads the detail panel - technically the closest equivalent to
   `scrape.py` for this source, but it's a materially different kind of
   dependency (a real browser, not just `requests`) from every other script
   in this repo, and it would need to keep working against Looker Studio's
   UI, which isn't a stable contract the way a state agency's own HTML is.
   Not built without confirming this tradeoff is wanted.
3. **Ask Wyoming DCI directly** whether the underlying data (it's ultimately
   NCIC entries re-published for this report) is available as a plain feed -
   out of scope for this project to pursue, but worth knowing as an option.

## Output

`missing_persons_wy_week_of_YYYY-MM-DD.csv`, columns: `age_range`, `race`,
`county`, `sex` - same column names as the other four states. `age_range` is
bucketed from **age when last seen**, matching Nebraska's and South Dakota's
measure (not Iowa's/North Dakota's current-age caveat).

### Deliberately excluded from the output

Name, exact/current age, AKA name, eye/hair/height/weight, the free-text
"Information" field (often contains clothing/last-seen narrative detail),
contact-agency phone number, photo.

### Privacy posture

Not yet decided - see the other states' PLAN docs for the standing default
(row-level CSV, no suppression, residual re-identification risk for
single-record counties accepted and documented) that this would inherit if
published. This week is a small, stark example: 2 records, 2 different
counties, each a singleton.

## Resources

Source page: https://wyomingdci.wyo.gov/dci-homepage/missing-persons
Underlying report: https://lookerstudio.google.com/embed/reporting/87d1bf76-2ea4-4122-9773-631f66ca2153/page/RdPWD
