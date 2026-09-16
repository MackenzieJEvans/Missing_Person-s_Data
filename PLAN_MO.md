# Missouri Missing Persons Demographic Scrape

Seventh state, same goal as [PLAN.md](PLAN.md) (Nebraska), [PLAN_SD.md](PLAN_SD.md)
(South Dakota), [PLAN_IA.md](PLAN_IA.md) (Iowa), [PLAN_ND.md](PLAN_ND.md)
(North Dakota), [PLAN_WY.md](PLAN_WY.md) (Wyoming) and [PLAN_OK.md](PLAN_OK.md)
(Oklahoma): a de-identified CSV of age range, race, county and sex for
people reported missing in the past 7 days.

Source: https://www.mshp.dps.missouri.gov/CJ51/Search (Missouri State
Highway Patrol).

## Decisions (confirmed 2026-09-15)

- **Adults and juveniles are two separate result sets with no combined
  view** - `personType=A` and `personType=J`. The URL originally supplied
  covered adults only. This matters a lot: juveniles were 35 of this week's
  49 records (71%) - fetching only adults would have silently dropped most
  of the week. scrape_mo.py always fetches both and merges them.
- **Time window**: past 7 days, matching the other six states. 49 records
  this week (14 adults + 35 juveniles) - the largest weekly count of any
  state covered so far, consistent with Missouri being the most populous.
- **Multi-county agencies named directly, single-site institutions mapped
  to a plain county** - same principle as South Dakota's tribal PDs vs.
  Iowa's Meskwaki Nation Police, applied here to a different kind of
  agency. Three Missouri Highway Patrol troop offices and three Division of
  Youth Services regional offices each cover many counties and are named
  directly (e.g. `MO HP Troop C - St. Louis` appears as-is in the `county`
  field, matched by prefix so a troop/region not yet seen doesn't silently
  fall through to `Unknown`). Five single-site institutions were instead
  each individually verified to one county/independent city: Fort Leonard
  Wood (Army post, wholly in Pulaski County), St. Louis Lambert
  International Airport (unincorporated St. Louis County), North County
  Police Cooperative (multiple municipalities, all in north St. Louis
  County), and two Division of Youth Services facilities that sound
  regional but are single buildings - Sierra-Osage Treatment Center
  (Poplar Bluff, Butler County) and the Waverly Regional Youth Center
  (Waverly, Lafayette County).
- **Publish target**: not yet decided. Nothing pushed to the public repo
  until asked.

## How the source differs from the other six states

**Closest to South Dakota's shape once both personType values are
combined**: every field needed - age, race, sex, agency, missing-since date
- is already in the list-page response for each personType (adults: 621
active cases; juveniles: 415; both in one response each, no pagination).
No detail-page fetch. Age here is directly "Age Missing" (age when reported
missing), like Nebraska/South Dakota/Wyoming - no Iowa/North
Dakota/Oklahoma-style current-age caveat.

**A genuinely flaky server, worth documenting so it isn't "fixed" back into
a bug.** Fetching both personType values isn't just two independent GETs:
- Reusing one `requests.Session` (so its cookies carry over) across the
  Adults request and then the Juveniles request was observed, repeatedly,
  to make the *second* request come back with zero rows - even though its
  query string was identical to a fresh request that worked fine on its
  own. A fresh `Session` per personType avoids that specific failure mode.
- Separately, and independently of the above, a single fresh-session
  request can itself intermittently return a truncated result (this was
  observed for *both* personType values at different points while building
  this script, not consistently one or the other). Nothing about the
  request differed between a truncated and a complete response.
- scrape_mo.py handles this by comparing the number of `<tr>` rows it
  parsed against the page's own `"N results found"` label, and retrying
  (new session, short delay, up to `FETCH_RETRIES` attempts) whenever they
  don't match, rather than trusting whatever came back on the first try.
  This is the only state's scraper that needs this - treat a future report
  of "the weekly count looks low" for Missouri as a first suspect for this
  exact flakiness, not necessarily a real drop in cases.

**County derivation is the same hybrid shape as Iowa's/North Dakota's,**
at a larger scale. `"<County> CO SO <seat>"` (58 of ~198 agencies) and two
sibling patterns seen on this site - `"SO <County> County - <seat>"` and
`"<County> County PD"` (St. Louis County PD, St. Charles County PD) - name
their county directly, mechanical, no external data. Everything else is a
municipal PD or similar giving only a city, needing the `CITY_COUNTY` table
in scrape_mo.py: ~120 entries, built from Wikipedia's "List of cities in
Missouri" (a ~940-row comprehensive table, parsed locally rather than via a
summarizer) and cross-checked wherever a city also happens to be a
county-sheriff's seat already confirmed by the mechanical patterns above
(e.g. Columbia/Boone, Rolla/Phelps - every overlap agreed). Two cities get a
non-obvious value: Kansas City spans four counties and is mapped to its
primary county, Jackson, per Wikipedia's own convention (same "primary
county for a split city" call Iowa made for Sioux Falls); St. Louis is
mapped to `St. Louis City`, not a county at all - it's one of the few
independent cities in the US, administratively separate from St. Louis
County even though they share a name.

**This map needs periodic re-validation.** Run
`python scrape_mo.py --check-agencies` to check every currently-active
agency (adults and juveniles both) against `CITY_COUNTY`/`AGENCY_OVERRIDES`/
`MULTI_COUNTY_PREFIXES` without narrowing to a week first. A new municipal
agency would need a `CITY_COUNTY` entry; a new single-site institution needs
the same individual fact-check the five above got, not a guess from its
name.

## Output

`missing_persons_mo_week_of_YYYY-MM-DD.csv`, columns: `age_range`, `race`,
`county`, `sex` - same column names as the other six states.

### Deliberately excluded from the output

Name, the poster PDF/photo links, missing-since date, exact age, raw agency
string, height/weight/eye/hair (not exposed by this particular list view -
only on the individual poster PDFs, which are out of scope here anyway).
Same rationale as the other six: county (or, for the six multi-county
agencies, the agency's own name) is coarser than the full record, which for
a single-record county/week would otherwise point straight back at the live
named list.

### Privacy posture

Not yet decided - see "Publish target" above. If Missouri data joins the
same public repo, the other states' posture (row-level CSV, no suppression,
residual re-identification risk for single-record counties/agencies
accepted and documented) is the default this would inherit, pending
confirmation. This week's run has twelve single-record counties out of 49
total records.  `--suppress-small-counties` exists in scrape_mo.py if the
posture ends up different.

## Resources

Source data: https://www.mshp.dps.missouri.gov/CJ51/Search (also
https://www.mshp.dps.missouri.gov/CJ51/searchMissing.jsp for the full filter
form, not used directly by the script but useful for exploring the site)
City-to-county source: https://en.wikipedia.org/wiki/List_of_cities_in_Missouri
Institution-location checks: https://en.wikipedia.org/wiki/Fort_Leonard_Wood ,
https://en.wikipedia.org/wiki/St._Louis_Lambert_International_Airport ,
https://www.ksdk.com/article/news/local/what-is-the-north-county-police-cooperative/63-24f7aa42-f672-47ee-b5a5-44858f6a47f6 ,
https://archive.oa.mo.gov/fmdc/Institutional_Information/pdf/Sierra-Osage_Treatment_Center.pdf
