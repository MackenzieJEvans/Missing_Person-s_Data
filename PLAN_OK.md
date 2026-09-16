# Oklahoma Missing Persons Demographic Scrape

Sixth state, same goal as [PLAN.md](PLAN.md) (Nebraska), [PLAN_SD.md](PLAN_SD.md)
(South Dakota), [PLAN_IA.md](PLAN_IA.md) (Iowa), [PLAN_ND.md](PLAN_ND.md)
(North Dakota) and [PLAN_WY.md](PLAN_WY.md) (Wyoming): a de-identified CSV of
age range, race, county and sex for people reported missing in the past 7
days.

Source: https://okmissing.osbi.ok.gov/SEARCH_PERSON_PUBLIC.aspx (Oklahoma
State Bureau of Investigation, classic ASP.NET WebForms).

## Decisions (confirmed 2026-09-15)

- **Age caveat, same shape as Iowa's/North Dakota's**: the AGE column is
  almost certainly current age, not age when last seen - inferred rather
  than labeled. The dataset includes a person missing since 06/28/1963
  listed at age 98, which only makes sense as an age computed at read time
  from a birth date, not an age fixed to 1963. There's no separate
  "age when last seen" field to fall back on. Don't stack Oklahoma's
  `age_range` with Nebraska's/South Dakota's/Wyoming's as the same measure
  in a combined dashboard.
- **Time window**: past 7 days, matching the other five states. 40 records
  this week - Oklahoma's population is larger than any state covered so
  far, and its weekly count reflects that (Nebraska ~28, Iowa ~22, South
  Dakota ~7, North Dakota ~4, Wyoming ~2).
- **Two tribal/special agencies fact-checked individually, not assumed**:
  Chickasaw Nation Tribal Police is named directly in the `county` field -
  its reservation spans 13 counties (Bryan, Carter, Coal, Garvin, Grady,
  Jefferson, Johnston, Love, McClain, Marshall, Murray, Pontotoc, Stephens;
  verified), so no single county is accurate. Seminole Nation of Oklahoma is
  instead mapped to plain `Seminole` county - its jurisdiction is
  essentially all of, and only, Seminole County (verified), the same
  single-county situation as Iowa's Meskwaki Nation Police. Fort Sill
  Military Police (a federal Army post, not tribal) is mapped to `Comanche`
  - the post sits entirely in one county (verified). Oklahoma State Bureau
  of Investigation itself is `Statewide / not county-specific`.
- **Publish target**: not yet decided. Nothing pushed to the public repo
  until asked.

## How the source differs from the other five states

**A real CSV export, not HTML scraping.** The search page has its own
"download CSV" image button (`imgbtn_dl_csv`) that does exactly what a
person clicking it would get: a clean CSV of every currently-active case
(1,075 as of 2026-09-15, no pagination). scrape_ok.py drives the same two
ASP.NET WebForms postbacks a browser would - `GET` the search page for a
fresh `__VIEWSTATE`/`__EVENTVALIDATION`, `POST` the search with every filter
at its default, then `POST` again with the export button's coordinates - and
gets the CSV back directly. No regex over an HTML results table anywhere in
this script, unlike every other state's scraper. One WebForms trap worth
noting: most of this form's "ALL" dropdown sentinel values are `%`, but
`ddl_TEMP_PERSON_AGE` uses `""` and `ddl_TEMP_PERSON_AGE_TO` uses `"N/A"` -
sending `%` for those two specifically trips the site's generic error page.

**County derivation needs no external data at all - a first among these six
states.** Oklahoma's own agency codes (ORI, the standard NCIC "originating
agency identifier") embed the county number directly: `OK0070000` is Bryan
County (county code 007) whether the agency is Bryan County's own sheriff or
a city PD inside Bryan County. The 77 county codes are Oklahoma's counties
in alphabetical order, with one classic government-index quirk: **"Mc"
sorts as "Mac"** - McClain/McCurtain/McIntosh land at codes 44-46, ahead of
Major (47), Marshall (48), Mayes (49), rather than where strict dictionary
order would put them. This was verified, not assumed: all 54 county-sheriff
agencies live on the site at once were checked against this numbering and
every one matched exactly (`COUNTIES_BY_CODE` in scrape_ok.py carries a
comment warning not to "fix" this back to dictionary order).

The results/export only gives the human-readable agency name, not its ORI
code directly, so scrape_ok.py builds an agency-name -> ORI map from the
search form's own dropdown (148 real agencies, `%`/"ALL" excluded) each run
and joins the export's `AGENCY NAME` column against it. Cross-checked
2026-09-15: every agency name appearing across all 1,075 active records
matched a dropdown entry exactly except one row with a blank agency
(rendered as `&nbsp;` in the HTML, empty in the CSV) - that row is `Unknown`,
same fallback as every other state uses for an unmatched agency.

Six agencies needed special handling beyond the plain `OK<3-digit-county><4
more>` pattern - `ORI_RE` only requires the 3-digit county prefix now, not
that the remaining 4 characters are also digits, which mechanically resolves
two of the six (`22ND DISTRICT COURT`, ORI `OK062015J` -> Pontotoc via the
`062` prefix; `PUBLIC SAFETY COMMUNICATIONS CITY OF TULSA`, ORI `OK072013N`
-> Tulsa via `072`). The other four don't follow the county-number scheme at
all and are handled by explicit name -> county entries in
`AGENCY_OVERRIDES`, documented in the Decisions section above: Oklahoma
State Bureau of Investigation (`OK0BI0000`), Fort Sill Military Police
(`OKUSA0000`), Chickasaw Nation Tribal Police and Seminole Nation of
Oklahoma (both use a distinct `OKDI......` tribal ORI series unrelated to
the county-number scheme - notably, naively reading digits out of
`OKDI06200` would have suggested Pontotoc (code 062) for Seminole Nation,
which is wrong; the real answer, Seminole County, only came from checking
the tribe's actual jurisdiction, not from parsing the ORI).

**This map needs periodic re-validation** the same way as the hybrid-derivation
states, even though most of it needs no external data: run
`python scrape_ok.py --check-agencies` to check every currently-active
agency against `AGENCY_OVERRIDES`/`ORI_RE` without narrowing to a week
first. A new agency with a non-standard ORI (another tribal or federal
agency, say) would need a new `AGENCY_OVERRIDES` entry, fact-checked the
same way as the four above - not guessed from its ORI's digits.

## Output

`missing_persons_ok_week_of_YYYY-MM-DD.csv`, columns: `age_range`, `race`,
`county`, `sex` - same column names as the other five states, with the
age-measure caveat above. Race (`A`/`B`/`I`/`U`/`W`) and sex (`M`/`F`) are
expanded from the source's single-letter NCIC codes to full words
(Asian/Black/American Indian/Unknown/White, Male/Female) for consistency
with Iowa/South Dakota/North Dakota's style.

### Deliberately excluded from the output

Name, the internal record id, date last seen, exact/current age, raw agency
string, height, weight, eye color, hair color, photo. Same rationale as the
other five: county (or, for Chickasaw Nation, the agency's own name) is
coarser than the full record, which for a single-record county/week would
otherwise point straight back at the live named list.

### Privacy posture

Not yet decided - see "Publish target" above. If Oklahoma data joins the
same public repo, the other states' posture (row-level CSV, no suppression,
residual re-identification risk for single-record counties/agencies accepted
and documented) is the default this would inherit, pending confirmation.
This week's run has ten single-record counties out of 40 total records.
`--suppress-small-counties` exists in scrape_ok.py if the posture ends up
different.

## Resources

Source data: https://okmissing.osbi.ok.gov/SEARCH_PERSON_PUBLIC.aspx
County numbering verified against: https://en.wikipedia.org/wiki/List_of_counties_in_Oklahoma
Tribal/federal jurisdiction checks: https://en.wikipedia.org/wiki/Chickasaw_Nation ,
https://en.wikipedia.org/wiki/Seminole_Nation_of_Oklahoma ,
https://en.wikipedia.org/wiki/Fort_Sill
