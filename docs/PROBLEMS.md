# Known open problems

This lists every bank/environment issue that is confirmed (live-tested against
the real source) but not yet fixed. Each section is self-contained: hand it to
an AI assistant as-is (the "Prompt for an AI" block at the end of each
section) and it should have enough context to start working without needing
anything else from this repo's history.

All enabled adapters work correctly when run individually and, since
2026-09-16, together: browser-driven banks run in their own process in any
multi-bank run (see "Mitigated (2026-09-16)" below).

## Recently resolved (2026-10-04): PPP-adjusted amounts; WDI's mismatched currency units

**What changed.** `--dyad` now also gives every project with a nominal USD
amount and a single recipient country its value in constant 2025 international
$ (`ppp_intl_constant_2025`), and the country-year files a `ppp_2025_total`.
See the README section "Constant 2025 USD and country-year totals".

**Trap avoided.** The obvious price level, WDI `PA.NUS.PPP` / `PA.NUS.FCRF`,
is wrong for some country-years: the two series are not always in the same
local currency unit.

- Venezuela 2002-2011 comes out at ~0.000000 and Zimbabwe at 0.00001-0.0003.
  Each is one redenomination in one series and not the other.
- Lebanon 2021-22 comes out at 2.7-5.4, from the official peg after the
  currency's collapse.
- 731 further rows had no official rate at all (Uzbekistan before 2013,
  Turkmenistan, Somalia, Palestine, and 2024-25 lags).

The ratio is therefore GDP in current US$ / GDP in current PPP international $
(`NY.GDP.MKTP.CD` / `NY.GDP.MKTP.PP.CD`). That is the same quantity, but the
World Bank has already reconciled the currency units and filled in its
alternative conversion factor where needed. Over the projects' country-years it
ranges from 0.12 (Uzbekistan 2003) to 1.68 (Iceland 2007).

**Still open.** 313 rows from 2002-2025 have no World Bank PPP for their
country-year and stay blank: Venezuela 59, Djibouti 52, South Sudan 50, Yemen
46, Syria 26, Kosovo 25, Cook Islands 23, Eritrea 12, and small territories.
Nothing is imputed. `dyad_manifest.json` → `ppp.no_factor_by_country` lists
them (that list also includes 2026 rows, which are never priced).

## Recently resolved (2026-09-16): the shrinking combined file, frozen and thinning sources, official-file backbone

**What happened.** On 2026-09-16 `all_mdb_projects.csv` held 46,152 rows, down
from 55,157 on 2026-09-09. The code changes in between (EIB `loan_amount`
fallback, default year filter, concessionality columns) removed no rows. The
drop had two causes.

- **Pipeline (−8,693 rows).** The 2026-09-15/16 refresh ran the 11 non-browser
  banks with `--combine`, then `aiib`/`badea`/`ebrd` in separate runs without
  it. `--combine` only ever writes the banks of the current run, so those three
  never reached the combined file. The 2026-09-09 refresh had avoided this with
  a throwaway merge script that was never committed.
  - Fixes: `--combine-only` rebuilds the file from the per-bank CSVs and refuses
    files with an older column layout. A partial `--combine` names the banks it
    left out. Browser banks now run in their own process, so one
    `--all-banks --combine` is complete.
- **Sources (−312 rows).**
  - **CAF** replaced its IATI file (2025-10-10 file: 478 activities;
    2026-09-09 file: 286). 226 in-scope projects approved 2007-2025 were
    removed, 218 of them closed: $30.3bn of $49.9bn. After this, 2009 had no
    CAF project at all.
  - **AfDB's** own IATI files lost 108 activities (5,606 → 5,498). AfDB
    overwrites them in place, so these cannot be recovered from IATI; the
    official MapAfrica list now defines AfDB anyway.
  - The rest was ordinary churn.

**Coverage audit and fixes** (all 14 banks, approval years 2002-2026):

- **World Bank v2 API frozen and undercounting.**
  - `search.worldbank.org/api/v2/projects` stopped at 2024-12-20 (a 2025+
    filter returns 0).
  - It also listed fewer projects in every year than the Bank's own records
    (IDA 2014: 155 vs 251).
  - The adapter now uses **v3**, whose yearly counts match the official
    "World Bank Projects" export and Finances One. `loan_amount` is the
    original principal from Finances One, `total_disbursement` also comes from
    there, and `total_commitment` is the current commitment.
  - Dropped projects without a Finances One loan were never approved and are
    left out (1,140 for IBRD). Pipeline projects keep a blank approval year.
- **CAF.** Five archived IATI files are packaged and merged behind the live
  file: 532 projects, 249 of them archive-only, each noted. Nothing before 2006
  was ever published.
  - Search for more snapshots (2026-09-16): registry dataset pages
    (`caf-actfile-46008`, `-2`, `-2603`), the Wayback CDX API for `caf.com/media/`
    and the blob store (intermittently offline), and file names guessed from
    the blob store's naming pattern. Only the five packaged files turned up.
  - **When CAF republishes again:** download the file it replaces, gzip it
    into `mdbs_scraper/data/caf_iati_archive/` and add it to
    `ARCHIVED_SNAPSHOTS`, newest first.
- **CABEI.**
  - The project list has 167 operations, almost none before 2015.
  - The `prestamos` package lists every public-sector loan approval since
    1962. 1,149 of its 1,294 rows had no match in the project list and are
    added. After the year filter, CABEI has 493 rows: 15-33 a year for
    2002-2026, where it previously had 0 a year before 2009.
  - Private-sector approvals exist only as country-year totals and go to
    `cabei_private_approvals_country_year.csv`.
- **CDB** lists only projects under implementation (100%), 2013-2023. This is
  recorded as `coverage_start_year`/`coverage_end_year`, so the balanced panel
  never claims zeros outside that window. CDB's IATI feed (`caribank`, 53
  activities) is no better.
- **ADB.**
  - IATI had almost nothing before 2006 and only 2,084 of the 7,106 sovereign
    projects in ADB's own file.
  - ADB's sovereign-projects file (as of 2026-01-27) plus its Statement of
    Loans (to 2017) are now the backbone; see "official files" below.
  - IATI operations missing from the sovereign file turned out to be
    non-sovereign, even when IATI labelled them "Sovereign" (e.g. 50146-001,
    Electric Networks of Armenia). They are kept as non-sovereign.
- **IsDB.**
  - The official approvals list (as of 2026-06-30, 1,905 operations, no ids)
    is the backbone for IsDB's own operations, matched by country, year ±1 and
    name. Only 492 IATI activities match; the other 721 IsDB-proper IATI
    activities are listed in `reports/isdb_reconciliation.csv` and left out.
  - Many of those carry a later commitment year than their approval (e.g.
    Ghazi-Barotha, approved in the 1990s, appears in IATI as 2002).
  - ITFC trade finance (1,282 activities), which the list omits, is kept as
    `instrument_category = trade_finance`.
- **Stale ends.**
  - IDB's dataset ends at 2025-10-16; today's official download is the same
    file, so the lag is IDB's publication lag.
  - IsDB IATI ends at 2025-09; BADEA at 2022 (`coverage_end_year`); EBRD's
    workbook is annual (to 2025-12-31).
  - `reports/coverage_by_year.csv` flags `low`, `stale` and `not_covered`
    years on every run.
- **Year definition.** The scope filter now uses `approval_year` for every
  bank. IDB used to be filtered by signing year, which kept 459 operations
  approved before 2002.

**Official files.** Nine files in `online pulled data/` are registered in
`mdbs_scraper/official_sources.json` with publisher, `as_of` date and sha256.

- **backbone:** ADB sovereign projects, AfDB project list, IsDB approvals;
- **supplement:** ADB Statement of Loans;
- **extra-fields:** World Bank export;
- **backstop:** every file except the loan statement. That includes CABEI, IDB
  and EBRD, which are the same datasets the adapters read, and the AIIB list
  print, read through a committed text extract.

Each run writes `reports/backstop_summary.csv`, covering counts per year,
field visibility and amounts. `--strict-backstop` turns a failed check into
exit code 3.

**Other fixes made along the way.**

- **Country names.** About 634 output rows had a real country but no
  `country_code`: EIB "United Kingdom"/"The Netherlands", CDB "The Bahamas",
  IsDB "Iran"/"U.A.E.", and ADB's double-encoded "TÃ¼rkiye" (repaired when the
  file is read). The aliases are added and pinned in a test.
- **Currencies.** `detect_currency` no longer accepts bank acronyms
  ("IDA", "ADF", "OCR") or AIIB's "TBC"/"TBD" as currencies, and `RMB` is now
  `CNY`.
- **Speed.** Generic row mapping normalized every column name for every field
  lookup; IDB's 27k rows took ~100 s and now take ~5 s. The same change briefly
  lost IsDB's ITFC package note (`_source_note` is now read from the raw row);
  a test pins it.
- **Three-decimal amounts.** The first full constant-USD build showed EBRD at
  about $4.6 trillion.
  - Cause: the workbook's cells are plain euro numbers with up to three decimals
    (Trans Adriatic Pipeline: 245863069.515). A three-decimal value was read as
    thousands grouping, inflating EBRD tenfold. EBRD now sets
    `amount_period_is_decimal`, as IDB already did; its 2002-2026 total is
    €199bn.
  - The backstop could not catch this, because it reads the official
    workbook with the same parser.
  - The same hazard hit NDB's "RMB 1.448 billion" (read as 1.448 trillion). A
    "." before an English magnitude word is now always a decimal point.
    Spanish "millones" keeps the thousands reading.
- **Local-currency amounts labelled USD (ADB IATI).** ADB's Indonesia file
  gives Cimory (equity, 55301-001) `default-currency="USD"` and a value of
  59,900,456,000, which is rupiah.
  - Four more non-sovereign ADB operations have the same problem:
    - Arnur Credit, Kazakhstan: 2.43bn, tenge;
    - Shubham, India: 1.85bn, rupees;
    - the poultry project, India: 1.09bn, rupees;
    - GCash, Philippines: 1.75bn, pesos.
  - The country-year build now flags any scraped (non-official) non-sovereign
    "USD" amount above $1bn in a country whose currency trades at 10+ per
    dollar as `excluded-currency-suspect`. The row stays in the project files,
    with a note, but is kept out of the totals.
- **AfDB very large commitments.** MapAfrica gives Medupi (P-ZA-FAA-001) a $10.8bn commitment, far above
  AfDB's reported ~$2.6bn loan. Ethiopia's Basic Services Transformation
  program (P-ET-IZ0-002, $7.7bn, 2015) and Morocco's education program
  (P-MA-IAZ-003, $4.5bn, 2009) look similarly high. The values are kept as the
  official file states them; check them before using AfDB totals for those
  country-years. `dyad_manifest.json` lists the ten largest operations on
  every build.
- **Registry and packaging.** `year_filter.json` (and every other JSON and data
  file) is in package data.
- **User agent.** The contact comes from `MDBS_SCRAPER_CONTACT`, and browser
  helpers send the same agent.
- **Throttling.** The per-host throttle is shared across the whole process.
- **CKAN.** `package_search` is paged.
- **Retries.** `Retry-After` dates are honoured.
- **Manifest.** It records the sha256 of every fetched input and the git
  commit; `--save-raw` archives the bodies.

## Recently resolved (2026-09-20): OECD CRS bulk files located; ADB/AfDB/IsDB have a real 2002-2007 backfill option, CAF/CDB do not

This supersedes the 2026-09-16 "not pursued further" decision (this section
used to record it; see git history for the original text). That decision was
based on the only files findable at the time being OECD sector-specific
narrative PDF publications (e.g. "Aid Activities in Support of Agriculture,
2002-2007"), not project-level microdata. Since then, the genuine bulk files
(`CRS 2002-03 data.zip` through `CRS 2024 data.zip`, 21 files, 739MB total)
were located and downloaded by hand, and placed in `data/external/oecd_crs/`
(gitignored, per README "OECD CRS"). `--crs-profile` ran against them: 87
bank-year rows in `output/reports/crs_profile.csv`.

**ADB, AfDB and IsDB have real 2002-2007 CRS coverage:**

- ADB: 60-85 CRS projects/year, $4.1bn-$6.7bn/year in commitments.
- AfDB: 16-87 CRS projects/year. Its raw row count is far higher (up to
  ~1,800/year) because `--crs-profile` only counts a row toward `crs_projects`
  when it carries a positive `USD_Commitment` -- most raw rows for AfDB
  don't.
- IsDB: 90-151 CRS projects/year, $0.74bn-$2.2bn/year.
- `crs_projects_matching_our_ids` is 0 for every one of these years, exactly
  as expected: the scraper's own IATI-based sources for these three banks
  don't reach back to 2002-2007 either, so there is nothing on our side yet
  to match against. A real backfill would **add** these as new records --
  the same supplement pattern CABEI's `prestamos` package and ADB's
  Statement of Loans already use against their own backbones -- not merge
  into existing ones.

**CAF and CDB's own documented gaps cannot be closed this way.** CRS itself
has zero rows for CAF before 2017 and zero for CDB before 2015 -- both well
short of CAF's 2002-2005 IATI gap or CDB's pre-2013 map-listing gap. CRS was
never going to help these two specific banks' early years; that's now
confirmed rather than assumed.

**Decision (2026-09-20): a real ADB/AfDB/IsDB 2002-2007 backfill is a live
option, not attempted here.** `--crs-profile` only measures; it changes no
output. Building the actual supplement (registering the CRS files in
`official_sources.json` with a `supplement` role, writing a reader for the
CRS row shape) is separate work.

**Still open.**

- AfDB's 108 removed IATI activities cannot be recovered.

## Recently resolved (2026-09-22): ~900 missing approval dates diagnosed; a new `pipeline_stage` field

`output/all_mdb_projects.csv` (61,014 rows, the 2026-09-16 rebuild) had 937
rows with no `approval_year` (and therefore no `commitment_year` either --
the two are always both-blank or both-set). Diagnosed live against the real
sources (World Bank's v3 API + Finances One, NDB's and AIIB's live project
pages, BADEA's decoded Tableau fixture) rather than guessed.

**The great majority, 864/937 (92%), is not a bug at all** -- it's projects
still in the pipeline, which have no approval date yet by definition: adb
`Proposed`/`Pipeline/identification`, aiib `Proposed`, eib `Under
appraisal`, ibrd/ida `Pipeline`, idb `Preparation`, cabei `En Preparación`
(the Spanish equivalent -- easy to miss without checking cabei's status
values directly). The same pattern already documented for AIIB's
`commitment_year` gap further down this file.

Of the remaining 73 already-approved/committed/closed rows missing a date,
**44 were a real, fixable bug across four root causes** (three found by
diagnosis, a fourth caught during the live re-verification below), 26 are a
confirmed genuine source gap, and 4 are an unexamined long tail too small to
justify dedicated investigation right now (adb 2, ebrd 1, idb 1 -- same call
as AIIB's own "4 Approved + 1 On Hold exceptions weren't investigated
further" note above).

**Live re-verified (2026-09-22) by re-scraping ibrd/ida/ndb/aiib and
re-counting:** ibrd 15/15, ida 6/6, aiib 5/5, and ndb 18/18 (17 originally
diagnosed + 1 more caught live, below) all now resolve to a real
`approval_year` -- an exact match to what the diagnosis below predicted, not
just a plausible-looking fix.

**IBRD/IDA (21 of 31 `Closed` rows fixed).** `worldbank.py`'s `_record()`
only fell back to Finances One's `board_approval_date` when
`status.casefold() == "dropped"`. Live-fetched
`search.worldbank.org/api/v3/projects?id=P008921` ("GRAIN STORAGE", status
`Closed`): the v3 API exposes no `boardapprovaldate` at all for this (and
other) old, closed projects. But the *same* Finances One loan-terms fetch
the scraper already makes for `interest_rate`/`last_repayment_date`
**does** carry `board_approval_date: "1979-06-26"` for this exact project
(confirmed live against the real `DS00047`/`DS00001` Finances One SQL
endpoint) -- it just wasn't being used because the project's status is
`Closed`, not `Dropped`. Checking all 31 `Closed` IBRD/IDA rows missing a
date: 21 (15 IBRD + 6 IDA) have a real Finances One board date sitting
unused; the other 10 genuinely have none in either source. Fixed by
generalizing the fallback to fire whenever the API's own board date is
blank and a Finances One loan record has one, for any status, not just
`Dropped` (the existing `Dropped`-specific "approved, then terminated"
override is unchanged). A new `data_quality_notes` entry marks every row
this recovers.

**NDB (18 of 18 rows fixed, two separate bugs).** `ndb.py`'s
`_QUICK_FACTS_LABELS` matched exact-case strings like `"Financing Approval
Date"` via `extract_labelled_sequence()` (`parsers.py`), case-sensitive by
design. Live-fetching all 17 NDB pages missing a date showed the real
labels vary: `"Financing approval date"` / `"Current limit of financing"`
(sentence case) on most pages -- NDB's own markup is inconsistent about
capitalization, not a template-version split, confirmed by seeing both
casings across pages of the same status -- recovers 15/17 once matched
case-insensitively; a third label pair, `"TA Approval Date"`/`"Limit of NDB
Financing"`, used specifically on technical-assistance projects and not in
the label dict at all, recovers the final 2/17. Fixed:
`extract_labelled_sequence()` gained a `case_sensitive: bool = True`
parameter (default unchanged, so CDB -- the only other caller -- is
unaffected); NDB now passes `case_sensitive=False` and the TA label pair
was added. Bonus: this also backfills `loan_amount` on several of the same
rows, which was silently missing for the identical reason.

Re-scraping NDB live to verify the above surfaced one more, separate bug:
one row (the COVID-19 emergency loan to Russia) still came back with no
`approval_year` even though its label *did* match -- its page reads
`"25 March, 2021"`, day-month order with a comma, which `parse_date()`
didn't recognize: only `"%B %d, %Y"` (month-day order, e.g. "March 25,
2021") was in the format list, not the day-month equivalent. Added `"%d
%B, %Y"`/`"%d %b, %Y"` to `cleaning.py`'s format list -- a generic parser
fix, like the AIIB comma fix below, not NDB-specific. This is exactly the
kind of gap a live re-verification catches and a root-cause-only review
doesn't: the label match looked fine and the row simply looked like a
"still missing" residual until the actual page text was checked.

**AIIB (5 of 5 rows fixed).** Live-fetching all 5 AIIB pages missing a date
showed the same pattern on every one: `"FINANCING APPROVAL"` reads e.g.
`"October 15，2020"` -- a **full-width comma** (U+FF0C), not `,`.
`ascii_fold()` already NFKD-normalizes U+FF0C down to a plain ASCII comma,
but leaves no space after it, so `"October 15,2020"` failed every `"%B %d,
%Y"`-style `strptime` format in `parse_date()` (confirmed live:
`parse_date("October 15，2020", day_first=False)` returned `""`, while the
same string with a normal `", "` parsed fine). Fixed in `cleaning.py`'s
`parse_date()`: a comma with no following space is normalized to `", "`
before the format list runs -- a generic fix (any bank hitting the same
"comma glued to the next token" pattern benefits), not AIIB-specific.

**Confirmed genuine gaps, left as-is.** 16 of BADEA's 131 rows (12%, not a
general BADEA gap) are a Tableau "pie"-worksheet bucket whose own
`"MIN(Approval Year)"` field decodes to `"%null%"` -- confirmed by decoding
`tests/fixtures/badea_bootstrap_secondary_info.json` through `badea.py`'s
real `_pie_rows()`; the other 115/131 BADEA rows decode a real year from
the same field without issue. The remaining 10 IBRD/IDA rows have no board
date in the v3 API *or* Finances One -- genuinely undisclosed by the
source.

**New: `pipeline_stage` field.** Nothing in the output previously
distinguished "no date because not yet approved" from "no date because of a
real gap" -- both got the same generic note, making the 864-row pipeline
bucket indistinguishable from a problem without already knowing every
bank's status vocabulary. Added the same treatment this file already gives
`sector`/`sector_category` and `concessional`/`concessional_flag`: a new
`pipeline_stage` schema field (`Decimal` 1/0, blank only when `status`
itself is undisclosed), computed centrally in `BaseScraper.finalize()`
(`base.py`) against an explicit, live-confirmed set of "not yet approved"
status strings across all 14 banks -- never pattern-matched or guessed.
AIIB's `On Hold` and idb's/ndb's `Cancelled` are deliberately excluded from
that set: confirmed live/by majority that those statuses mostly describe
projects that were already approved. `drop if pipeline_stage == 1` removes
every not-yet-approved row from a date/amount analysis, not just the ones
that happen to be missing a date today.

## Recently resolved: concessionality and the AfDB/AfDF, AsDB/AsDF split

The dataset could not say whether an operation was concessional. `loan_type`
only ever holds Sovereign/Non-sovereign (a risk classification, a different
axis), and `interest_rate` is 100% blank for both `afdb` and `adb`. AfDF
commitments sat inside `afdb_projects.csv` under a single `AfDB` identity, and
AsDF inside `adb_projects.csv` — while the World Bank half of the same dataset
drew exactly this line structurally, as separate `ibrd`/`ida` outputs.

All of it turned out to be recoverable from feeds already in the pipeline.
Live-verified 2026-09-15 against the complete published portfolios:

- **`afdb` names the window on 100% of its 5,498 activities**, as a Funding-role
  `<participating-org>` with its own org ref: `XM-DAC-46003` African Development
  Fund (2,801 activities), `XM-DAC-46002` African Development Bank ordinary
  capital (1,289), both together (144), and an AfDB-administered special fund
  only (1,264: TSF 544, MIC Fund 241, SRF 219, NTF 86, …).
- **`adb` never names the window** — 0 of 3,498 activities carry a Funding-role
  participating-org. But `<default-flow-type>` is present on 100%, splitting
  1,742 ODA / 1,750 OOF, and that split validates by country (China 271 OOF vs
  8 ODA, Nepal 123 ODA vs 2 OOF, Bangladesh 148/86). Substantively correct too:
  ADB merged AsDF's lending into ordinary capital in 2017, so no AsDF lending
  entity remains to attribute a loan to. `funding_window` stays blank for adb,
  with the reason written into `data_quality_notes` rather than left to read as
  "unknown".
- **`isdb` names its windows too** — "IsDB - Ordinary Capital Resources"
  (1,538), "IsDB - Islamic Solidarity Fund for Development" (33), "IsDB -
  Endowment Fund" (104) — but they were being *discarded*: they reuse IsDB's own
  `<reporting-org>` ref, which `_iati_cofinancing_field` treated as "the bank
  listing itself" and dropped.
- **`caf` discloses neither** (286 activities, no flow-type, no funding orgs) and
  stays blank.

**The trap, and why the precedence rule is what it is.** AfDB's own
`default-flow-type` cannot be used for concessionality: it tags **799 of its
1,289 ordinary-capital activities as ODA (10)**, which would invert them. ADB's
flow type, by contrast, is reliable. Rather than branch per bank, the rule is
*a disclosed funding window always wins; flow type is consulted only when no
window is disclosed* — which reaches the right answer for both, since AfDB
always discloses a window and ADB never does. Do not "simplify" this by
preferring flow type.

A second trap, caught during implementation: **AfDB's `<reporting-org>` is
`XM-DAC-46002` with the same name as its ordinary-capital window.** Any
"is this just the bank listing itself?" check keyed on matching ref or name
silently deletes the non-concessional half of the split. `_AFDB_GROUP_WINDOW_REFS`
is therefore checked *before* the self-reference tests in `tabular.py`, and
`ConcessionalityTests` pins this.

Result: `concessional` populated for 97% of afdb rows (Yes 3,902 / No 1,266 /
Blended 167) and 100% of adb rows. `financing_instrument`, previously 100% blank
for adb/afdb/caf, is now filled from the same feeds' `<default-finance-type>`
codes. `cofinancing_partners` no longer lists AfDB's own windows as external
cofinanciers (it had "African Development Fund" on 1,586 rows); genuine
cofinanciers — EU, IDA, EIB, BADEA, IsDB — are untouched.

Not covered, and deliberately left blank rather than guessed: `idb` (Fund for
Special Operations) and `cdb` (Special Development Fund) both have real
concessional windows, but neither source was verified here — the IDB dataset
download failed during this work, consistent with the data.iadb.org URL churn
recorded further down this file. AfDB special funds whose terms the source
never states (MIC Fund, SEFA, FAPA, AWF — 163 rows) are blank with a note
naming the fund, unless the financing is disclosed as a grant, which settles
concessionality on its own.

## Recently resolved: adb, afdb, caf, isdb now use their official IATI feeds

`adb`, `afdb`, `caf`, and `isdb` were previously blocked: `adb`'s and `afdb`'s
official CSV/HTML downloads sit behind Cloudflare bot-challenge protection,
`caf`'s public project page sits behind Incapsula bot-challenge protection,
and `isdb`'s approvals data platform only exposes dashboard-aggregate JSON
(not a per-project list). Live testing confirmed the bot-challenges block
*headless-browser automation too*, even from a genuine residential IP (not
just the datacenter/sandbox network this repo may be developed on) — pointing
at browser-fingerprint detection, not IP reputation, and ruling out a
`download_via_browser()`-style fix.

All four institutions separately publish their full project portfolios as
official IATI Standard activity files through the [IATI
Registry](https://iatiregistry.org) (a CKAN-based open-data catalog, the same
kind of source `cabei` already used), hosted without any bot-protection —
confirmed reachable via plain `urllib`, no browser needed. This is a fully
official, purpose-built transparency channel, not a scraping workaround, and
it turned out to have *better* field coverage than the original protected
sources would have offered (IATI's structured `<transaction>`/`<activity-date>`/
`<participating-org>` elements directly expose commitment/disbursement
amounts and dates, and cofinancing partners).

- `adb`: publisher `asdb` on the IATI Registry, 41 packages (one per
  recipient country/region).
- `afdb`: publisher `afdb`, 57 packages.
- `caf`: publisher `caf`, 1 activity-file package.
- `isdb`: publisher `isdb`, 4 activity-file packages (IsDB proper split into
  two files, plus its ITFC trade-finance arm) plus 1 organisation-file
  package (excluded).

Implementation: `mdbs_scraper/adapters/{adb,afdb,caf,isdb}.py` now subclass
`CkanOrganizationScraper` (`mdbs_scraper/adapters/common.py`), which fetches
an IATI Registry `package_search` result (`banks.json`'s new `source_url` for
each), downloads every relevant package's best resource via
`rows_from_ckan_resources()`, and combines the rows. `mdbs_scraper/tabular.py`'s
`xml_rows()` was extended with IATI-aware transaction/date/participating-org
handling (`_iati_transaction_fields`, `_iati_activity_date_fields`,
`_iati_cofinancing_field`) so multiple same-named siblings (e.g. several
disbursement transactions, or planned vs. actual activity dates) are
correctly disambiguated by their IATI type/role codes instead of the generic
XML flattener silently keeping only the first of each — this feeds directly
into the `first_disbursement_date`/`last_disbursement_date`/
`cofinancing_partners` fields. New `FIELD_ALIASES` entries in `common.py` map
the resulting `iati_*` keys onto the canonical schema. See
`tests/fixtures/iati_transactions_sample.xml` and
`tests/test_tabular.py::IatiTransactionFieldsTests` for the exact semantics
(including that a net-negative disbursement, e.g. a refund/reversal
transaction, is preserved rather than rejected — `schema.py`'s `validate()`
deliberately does not reject negative `total_disbursement`).

One secondary gap remained from this switch: `badea` has no IATI Registry
presence at all (checked directly; not found under any plausible publisher
slug or full-text search). See "Recently resolved: BADEA's Tableau Public
embed" below for how it was eventually reached anyway.

## Recently resolved: DAC sector code lookup for adb and isdb

`adb`'s and `isdb`'s IATI `<sector>` elements carry only a bare OECD DAC
numeric code (e.g. `code="31161" vocabulary="1"`, or IsDB's
`code="236" vocabulary="2"`) with no `<narrative>` text, so `sector` was 0%
for both despite otherwise-excellent coverage. `mdbs_scraper/dac_sectors.py`
(new) provides `sector_name_from_dac_code(code, vocabulary)`, backed by two
tables generated directly from IATI's own replicated OECD DAC codelists
(`https://codelists.codeforiati.org/api/json/en/Sector.json` — the 326-entry
5-digit purpose codelist, IATI vocabulary `"1"` or absent — and
`.../SectorCategory.json` — the 44-entry 3-digit category codelist, IATI
vocabulary `"2"`). Any other vocabulary is deliberately left untranslated
(e.g. `"99"`, a reporting-org's own custom list) since its codes aren't DAC
codes and could otherwise coincidentally collide with one. Wired into
`generic_record_from_row()` in `mdbs_scraper/adapters/common.py` (needs
conditional vocabulary-gated translation logic, not a simple `FIELD_ALIASES`
rename); a `data_quality_notes` entry documents when a sector name was
DAC-derived rather than disclosed as text. This also surfaced and fixed a
latent bug: the existing loan-amount → total-commitment fallback note used
plain assignment instead of `join_values()`, so it would have silently
clobbered the new DAC note (or vice versa) whenever both applied to the same
record — both now combine correctly.

Live-verified: `adb` `sector` 0%→100% (20/20, all resolved via the 5-digit
purpose codelist). `isdb` `sector` 0%→100% (20/20, all resolved via the
3-digit category codelist). Tests: `tests/test_dac_sectors.py`.

## Recently resolved: EBRD's missing commitment-year alias, EIB's sector backfill

**EBRD** (`commitment_year` was 0% despite good coverage elsewhere): the
downloaded CSV's actual date column is `"Original Signing Date"`, which
normalizes to `original_signing_date` — not previously in `FIELD_ALIASES`.
Added to `FIELD_ALIASES["commitment_date"]` in `mdbs_scraper/adapters/common.py`.
Live-verified: `commitment_year` 0% → 100%. Test:
`tests/test_field_aliases.py::EbrdCsvAliasTests`.

**EIB** had two separate issues, both resolved:
- **Sector always 0%:** `_enrich_detail()`'s caller in `scrape()` was gated
  on `if not record.country`, so it never ran for records that already had a
  country but no sector. Fixed to `if not record.country or not record.sector`.
  That alone wasn't enough, though — EIB's detail pages don't expose sector
  via `extract_label_values()` at all (it's not a `Label: value` shape); a new
  `EIBScraper._sector_from_text()` helper parses the page's "Location /
  Sector(s)" text block directly (the location value appears first and is
  skipped, then sector-name lines are collected until "Description"/
  "Objectives"/etc.), live-verified against real projects including a
  multi-sector case (`"Agriculture, fisheries, forestry; Water, sewerage"`)
  and a "Credit lines" intermediated-loan edge case. `sector` 0% → 100%. Test:
  `tests/test_registry_and_adapters.py::test_eib_detail_enrichment_backfills_sector_independent_of_country`.
- **Pagination "stops after ~16 records"** — investigated and found **not
  currently reproducible**, contrary to this file's own prior documentation.
  Live testing fetched all 12 requested pages with `--max-projects 300`
  (`rows_seen: 300`, zero failures) and all 28 requested pages with
  `--max-projects 700` (`rows_seen: 700`, zero failures, correctly discovering
  `source_total: 29,701`). A direct plain-`urllib` request to the real page-2
  endpoint (URL captured via a throwaway Playwright `page.on("request", ...)`
  script) also succeeded with no cookies and no special headers. No code
  change was needed. The high duplicate count seen across pages (e.g.
  324 of 700 rows) comes from the source's own pagination instability — it
  sorts by `loanParts.loanPartStatus.statusDate` with no stable secondary
  tiebreaker, so records with tied dates can shift across page boundaries
  between separate requests. This isn't a client-side bug and doesn't corrupt
  data (`BaseScraper.finalize()`'s existing `(bank_id, project_id)` dedup
  already handles it correctly) — just some redundant fetching. If a future
  live run does show pagination genuinely stalling (an empty page before
  `source_total` is reached), re-open this as a new problem with fresh
  evidence rather than assuming the old diagnosis still applies.

## Recently resolved: CDB and NDB now reach detail-page data correctly

Both were "silently broken" — producing project names/URLs but 0% on every
other structured field. Root-caused independently, per bank, as the plan
anticipated they might not share one cause:

- **CDB**: the listing page has its own summary `<table>` (`Project Title,
  Country, Sectors & Themes, Project Total, Approved`), and
  `HTMLPortfolioScraper.scrape()`'s table-parsing path takes priority over
  visiting detail pages once that table yields credible-enough records — so
  CDB's adapter never reached the far richer per-project detail pages at all
  (confirmed live: a detail page discloses `Sector`, `Date of Approval`,
  `Country`, `Approved total`, `Status` as a static "label on one line,
  value on the next" block that neither `extract_label_values()`'s
  `Label: value` regex nor its two-column-table extraction can parse).
  `CDBScraper` (`mdbs_scraper/adapters/cdb.py`) now overrides `scrape()` to
  always walk detail pages instead (filtering out the listing page's
  sort-order links, which match the broad `project_href_pattern` too), and
  overrides the new `_extract_detail_fields()` hook (see below) with CDB's
  label set.
- **NDB**: detail pages *were* being reached (`project_url` was already
  populating), but two separate issues combined: (1) the same "label on one
  line, value on the next" static-text shape as CDB, and (2) `banks.json`'s
  `project_href_pattern` matched both NDB's singular `/project/&lt;slug&gt;/`
  URLs (real projects) and plural `/projects/&lt;slug&gt;/` URLs (informational
  hub pages like "Environmental & Social Sustainability" — confirmed false
  positives), diluting the sample with non-project pages. Tightened the
  pattern to `/project/` only; `NDBScraper` overrides `_extract_detail_fields()`
  with NDB's "Quick Facts" label set (`Country`, `Status`, `Area Of
  Operation` → sector, `Type` → loan_type, `Concept Approval Date`,
  `Proposed Limit of NDB Financing` → loan_amount).

Both fixes share a new, reusable mechanism rather than duplicating logic:
`extract_labelled_sequence()` (`mdbs_scraper/parsers.py`) generalizes the
"known label text → take the next non-empty, non-label line as the value"
scan AIIB's adapter already used privately; `HTMLPortfolioScraper` gained an
overridable `_extract_detail_fields()` hook (`mdbs_scraper/adapters/common.py`,
defaulting to the existing `extract_label_values()`) so any bank needing this
shape doesn't need its own bespoke `_records_from_details()` copy.

A side finding while fixing CDB: `cleaning.py`'s `parse_date()` didn't
recognize month/year-only dates (e.g. "December, 2018", as CDB discloses
approval dates) at all, silently losing the year — no format in its list
matched a day-less month+year. Added `"%B, %Y"`, `"%b, %Y"`, `"%B %Y"`,
`"%b %Y"` (day defaults to the 1st, matching common data-precision
convention); this is a generic parser fix, not CDB-specific, so it may
improve `commitment_year` coverage on other banks disclosing dates this way
too.

Live-verified: `cdb` — `commitment_year` 0%→100%, `sector` 0%→95%
(`loan_amount`/`total_commitment` correctly stay blank; CDB only discloses a
total project cost, not a distinct bank figure — mapped to
`total_project_cost` instead, consistent with "a bank financing amount is
never treated as total project cost"). `ndb` — `sector` 0%→100%,
`commitment_year`/`loan_amount`/`total_commitment` 0%→55% (the remaining 45%
confirmed genuinely undisclosed on a live page, not a parsing gap).

## Recently resolved: IBRD/IDA disbursement/conditionality claim re-verified; total_project_cost added

Fetched a live `search.worldbank.org/api/v2/projects` response and inspected
a project dict's full 37-key set. The existing hardcoded claim ("the Projects
search API does not expose total disbursement or project conditionality")
holds — confirmed no disbursement- or conditionality-shaped key exists; the
note in `worldbank.py`'s `WorldBankScraper._record()` is left as-is, now
re-confirmed rather than just assumed. (`curr_ibrd_commitment`/
`curr_ida_commitment`/`curr_total_commitment` were also checked as candidate
new fields — every sample showed them numerically identical to the existing
`ibrdcommamt`/`idacommamt`/`totalcommamt`, just rescaled to millions, so
they weren't wired in.)

One genuinely new, useful, previously-unread key was found: `lendprojectcost`
— the project's total cost, which can substantially exceed the Bank's own
commitment (e.g. one live sample: $379M IDA commitment against a $5B total
project cost, implying large financing from other sources). This maps
directly onto the existing `total_project_cost`/`total_project_cost_usd`
schema fields, which `_record()` never populated before. Wired in via
`parse_amount()`, the same helper already used for the bank's own commitment
amount. Live-verified against `ibrd`; test extended in
`tests/test_registry_and_adapters.py::test_world_bank_arms_filter_and_preserve_bank_specific_commitments`
and `tests/fixtures/worldbank_projects.json`.

The World Bank's separate Statement of Loans / Statement of Credits and
Grants finances datasets remain a possible, bigger, separate follow-up for
real disbursement history — not investigated (shape and how to join it to
`search.worldbank.org` project IDs is unknown), and out of scope unless
asked for specifically.

## Recently resolved: AIIB full-scale validation

AIIB had only ever been live-tested on 20 of an unverified "~466" figure from
a stale commit message, and `run_metadata["source_total"]` was never
populated for it (unlike `eib.py`), so there was no automated "found N,
scraped M" completeness signal. `aiib.py`'s `_detail_records()` now sets
`self.run_metadata["source_total"]` to the deduplicated project-link count
before applying any `--max-projects` cap, mirroring `eib.py`'s pattern.

An uncapped live run confirms the old "~466" figure was accurate:
`source_total: 466`, `completeness: complete`. More importantly, the earlier
20-project sample turned out to be **not representative** — full-scale
coverage is substantially better than the small sample suggested:
`commitment_year` 81% (not 50%), `loan_amount`/`total_commitment` 99% (not
50%), `sector` 100% (matches). `total_disbursement`/`subnational location`/
`conditionality`/`cofinancing` remain 0% at full scale too, confirming those
are genuinely not disclosed by AIIB's detail pages, not a small-sample
artifact. Test added: `tests/test_registry_and_adapters.py::test_aiib_scraper_records_source_total_from_deduplicated_links`.

## Recently resolved: BADEA's Tableau Public embed

`https://www.badea.org/interactive-map/` renders its project map entirely
through an embedded **Tableau Public** dashboard (workbook `ProjectsMapv2`,
view `overview`) — there is no plain HTML/JSON listing, and BADEA has no
presence on the IATI Registry at all (checked directly: no publisher entry
under any plausible slug, no full-text match), so neither of the two usual
fallbacks applied.

**What actually worked, confirmed live:** Tableau's `get-summary-data` /
`get-underlying-data` commands (the mechanism behind its own "download data"
button, and what the `tableauscraper` PyPI library primarily wraps) return
HTTP 410 Gone for this embed — the workbook author disabled them, a common
Tableau Public setting; don't retry that path. What does work is that the
dashboard's **initial** `bootstrapSession` response (captured before any user
interaction — no clicking, no per-country selection, no session-pacing
concerns) already contains the full per-record breakdown via its linked "pie"
worksheet: 274 rows across 49 country/beneficiary values, each a
(country, sector classification, approval year, committed-loan amount)
tuple. Internal consistency was verified two ways: summing the pie rows'
amounts per country reproduces the map worksheet's own independently-encoded
per-country totals (within their own one-decimal rounding), and a fresh live
capture on a different day reproduced the exact same 274 rows/49 countries.

The response body is Tableau's length-prefixed segment format
(`"<length>;<json><length>;<json>..."`), but the length prefix is a
JS-string (UTF-16 code unit) count, not a UTF-8 byte count, so it silently
drifts out of sync with a byte-sliced parse the moment a country name has an
accented character (e.g. "Sao Tomé", "Côte d'Ivoire"). The fix is to ignore
the stated length entirely and decode each segment as the next complete JSON
value (`json.JSONDecoder().raw_decode`), only using the digits to find where
a segment starts.

Actual per-mark values are indices into per-type dictionaries
(`dataDictionary...dataSegments[*].dataColumns[*].dataValues`, keyed by
`dataType`), decoded via `pool[index] if index >= 0 else
cstring_pool[abs(index) - 1]` (a negative index means the value is really a
formatted string, e.g. `"%null%"`, living in the shared `cstring` pool
instead of the field's own type pool) — reverse-engineered by reading the
`bertrandmartel/tableau-scraping` library's decode logic directly (its HTTP
methods weren't usable here since they rely on the blocked download
commands, but the decode algorithm itself is endpoint-agnostic).

**Confirmed, honest source limitations** (not something a smarter decode
would recover): the dashboard exposes totals bucketed by
(country/beneficiary, sector classification, approval year) — never an
individual project name or ID — and amounts are only available at
one-decimal-place (millions of USD) precision, the same precision the map's
own tooltips show.

Implementation: `mdbs_scraper/adapters/badea.py` is now a bespoke
`BADEAScraper` (Tableau vizql session/decode logic kept local to this one
file, since no other adapter in this codebase uses Tableau) that captures the
`bootstrapSession` response via a dedicated Playwright helper (`render_page()`
in `browser.py` only retains `content-type: json` responses, and this one
isn't), decodes the "pie" worksheet into rows, and also decodes the "map"
worksheet's per-country marker `Latitude (generated)`/`Longitude (generated)`
columns to attach an approximate (country-centroid, not project-site)
coordinate to each record. `mdbs_scraper/banks.json`'s `badea` entry now
reflects this (`source_format: "json"`, `method: "tableau-vizql"`,
`default_currency: "USD"`). Test fixture:
`tests/fixtures/badea_bootstrap_secondary_info.json` (the real captured
`secondaryInfo` segment, trimmed to just that segment — segment 0, the
unrelated "info" block, isn't used by the decoder at all) and
`tests/test_badea.py`.

## Recently resolved: 10 data-quality reports diagnosed against a full `--all-banks` run

Diagnosed live against a real `output/` from a full `--all-banks --parallel 3`
run, rather than guessed. Two were real bugs; four were confirmed
working-as-designed genuine source limitations; the rest are covered in their
own sections below.

**NDB: `Approved`/`Completed`/`Cancelled` projects were missing
`loan_amount`/`commitment_year` entirely** (only `Proposed`, 28/167 records,
had them). `ndb.py`'s `_QUICK_FACTS_LABELS` hardcoded `"Concept Approval
Date"`/`"Proposed Limit of NDB Financing"` — labels that only appear on
`Proposed`-status pages. Once a project moves past Proposed, NDB relabels the
same two facts `"Financing Approval Date"`/`"Current Limit of NDB
Financing"` (confirmed live on one page of each status). Fixed by adding
both label variants, mapped to the same canonical fields. Live re-check:
loan_amount coverage went from 28/167 (17%) to 154/167 (92%). (Optional,
not implemented: Approved/Completed/Cancelled pages also show a "Source of
Fund" / "Amount (USD)" table with real cofinancing data NDB currently leaves
undisclosed in our output.)

**IDB's `record.source_url` was an already-expired link.** IDB's download
endpoint (`data.iadb.org/file/download/...`) redirects to a signed,
short-lived `token-download` URL; `TabularScraper.map_row()` was recording
that *resolved* URL as `record.source_url` instead of the stable requested
one. The JWT's own `exp` claim decoded to ~2 hours after generation
(confirmed twice at different times) — by the time anyone opened that link
later, it could not still work. Fixed: `map_row()` now records
`self.options.source_url or self.bank.source_url` (the stable, always
re-fetchable URL) instead of `source.url`. General fix, not IDB-specific —
any bank whose download redirects through a signed/expiring URL had the same
latent issue. (`project_url` being blank for all 16,696 IDB rows is separately
confirmed *correct*, not a bug: IDB's own CSV export has 28 columns and none
of them is a URL/link field.)

**IsDB's `isdb-activity` CKAN package was silently ingesting IsDB's own
organisation file as a fake project** (`project_name: "Islamic Development
Bank"`, no real fields). Its package name has neither a `-org` suffix nor
`orgfile` in it (unlike the `isdb-org` package that IS correctly excluded),
so `CkanOrganizationScraper._is_relevant_package()`'s name-based check missed
it, even though its one resource is `isdb-organisation.xml`. Fixed with a
fallback check: also exclude a package when every one of its resources'
URLs contains `organisation`/`organization`. Confirmed the fix removes
exactly this one stray row and doesn't affect `adb`/`afdb`/`caf` (their
packages are named cleanly).

**IsDB's `total_disbursement` gap is a genuine, confirmed ITFC-specific
publisher gap, not a timing/"not yet disbursed" issue.** Bucketing by
`commitment_year` alone looked like recency, but cross-referencing all 3 of
IsDB's IATI activity files directly (fetched live, matched by
`iati-identifier`) shows: IsDB's own activities (files `_a`/`_b`, 1,229 of
2,496 records) have 92–100% disbursement coverage in *every* year 2002–2023,
including 20+-year-old ones; its ITFC (Islamic Trade Finance Corporation,
trade-finance arm) activities (1,266 records — just over half the dataset)
have 0–40% coverage in *every* year including 2008-2010, far too old to
still be "in progress." `CkanOrganizationScraper` gained a `_package_note()`
hook (default: none); `IsDBScraper` overrides it to tag every ITFC-sourced
row with a `data_quality_notes` entry explaining this, so it's visible
per-record rather than only discoverable by cross-referencing raw files like
this diagnosis did.

**AfDB/adb/caf/isdb duration can silently reflect a *planned*, not actual,
completion date.** IATI's `<activity-date>` element comes in planned/actual
pairs; `tabular.py` already preferred actual over planned
(`_IATI_END_DATE_CODES = ("4", "3")`), but gave no visible signal when the
fallback fired. Live-sampled one AfDB IATI file (49 activities): 38/49 had
both actual dates (a true realized duration), but 10/49 had only a *planned*
end date (still open/in finalisation) — for those, `duration_years` was
silently computed against an estimate. Fixed: `_iati_activity_date_fields()`
now also emits `iati_start_date_planned`/`iati_end_date_planned` markers when
the planned code was the only one disclosed, and `generic_record_from_row()`
adds a `data_quality_notes` entry when they fire. Benefits all 4 IATI-sourced
banks. Confirmed live on real IsDB records.

**CAF (no data before 2006) and CDB (almost nothing before 2013, not just
"2002-2006") are both genuine, source-side coverage floors, not bugs** — see
their `banks.json` `coverage_notes`, updated with the exact live-confirmed
detail. CDB's own page states outright: *"This map shows a selection of
capital projects approved during the period 2013-2026... to gain access to
the Bank's project archive"* [contact them directly] — their public map
deliberately excludes most pre-2013 history; it isn't a pagination cap
(confirmed live: the listing page itself has exactly 99 project links,
matching our ~94 retained).

**CABEI's `total_disbursement` vs `total_project_cost` "discrepancy" is
`total_project_cost` being 0/150 populated while the other three money
fields are 150/150** — CABEI's source only discloses CABEI's own loan amount,
never a separate multi-financier total project cost, and every record
already carries the auto-note explaining exactly that. Not a bug.

**AIIB's ~89 missing `commitment_year` is 84/89 (94%) `status = "Proposed"`
projects** — a Proposed project hasn't been approved/committed yet by
definition, so there's genuinely no commitment date to disclose (each record
already carries *"Commitment/approval year is missing; record was
retained."*). Not a bug; the 4 Approved + 1 On Hold exceptions weren't
investigated further (too small a residual to justify a dedicated change).

## Mitigated (2026-09-16): browser-driven banks now run in their own process

**Update 2026-09-16.** The per-bank process isolation this section called for
is now built. In any multi-bank run, `cli._run_bank_isolated` runs each
`dynamic: true` bank (`aiib`, `badea`, `ebrd`) in a fresh
`python -m mdbs_scraper --bank X --child-result FILE` process and reads its
records back from a pickle. A crashed child is retried once in another fresh
process.

The children are started with `subprocess.run(..., close_fds=False)` and no
`cwd`, which makes CPython use `posix_spawn` instead of `fork` on macOS. That
detail matters. The first isolated run (`--all-banks`) still lost all three
browser banks: every child died with exit code -11 within 0.1 s, because the
parent had already used the networking frameworks while collecting ADB. The
spawn itself then crashed in the same `nw_settings_child_has_forked()`
atfork handler described below. `posix_spawn` runs no atfork handlers;
`--banks adb badea`, which reproduced the crash every time, now completes.
The history below is kept for context.

**Status before 2026-09-16:** Partially mitigated, not solved. `cli.py`'s `_run_bank()` retries
a `dynamic: true` bank (`aiib`/`badea`/`ebrd`/`eib`) once, in-process, on any
exception — this does recover some transient failures and is a net
improvement, but a full live re-run (`--all-banks --parallel 3`,
2026-08-24) showed it is **not sufficient** for the specific failure this was
written for: `badea`, `ebrd`, *and* `eib` all failed with the identical
`AttributeError: 'PlaywrightContextManager' object has no attribute
'_playwright'` on **both** the first attempt and the retry.

**Root cause, better understood after that run's evidence:** this isn't a
one-off blip per launch attempt — it's the Python `playwright` package's
shared sync-API driver-loop state getting corrupted *once* (by the same
underlying macOS/Network.framework fork-safety race documented elsewhere in
this file) and then failing identically for every subsequent
`sync_playwright()` call **for the rest of that process's lifetime**. Two
pieces of evidence pin this down:
- `eib`'s attempt (16:32:22) came a minute *after* `aiib` — the long-running
  bank most likely to trigger the original corruption — had already finished
  (16:31:04), and it still failed identically on both tries. So it isn't
  simply "don't run two Playwright launches at literally the same instant."
- A burst of `Exception: Connection.init: Connection closed while reading
  from the driver` / `Task exception was never retrieved` errors appeared
  **after** the run's own final "Wrote N records" line — orphaned async
  tasks from driver connections that never resolved, surfacing only at
  process teardown.
- This matches why manually rerunning a failed bank *as a brand-new `python
  main.py --bank X` process* has reliably worked all session (confirmed
  repeatedly) while the in-process retry added here often doesn't: a fresh
  process gets fresh Playwright driver-loop state; retrying inside the same
  already-corrupted process does not.

**Until this is revisited:** if `--parallel` run leaves `aiib`/`badea`/
`ebrd`/`eib` in the failures list, rerun just those banks as their own
process invocation (`python main.py --banks badea ebrd eib`, or one at a
time) rather than expecting the built-in retry to recover them — that's the
approach with a consistent track record this session. A more robust fix
would isolate each Playwright-driven bank's collection in its own OS
subprocess (matching the pattern that's actually been reliable) rather than
retrying in-thread in the same process; that's a bigger change than what's
implemented here and hasn't been built.

**Additional evidence (2026-08-27), pinning down the exact crash
mechanism.** A `--all-banks --combine` run (no `--parallel`, so purely
*sequential* within one process) crashed three times in ~4 minutes on its
first Playwright launches, writing zero output. macOS's own crash reporter
(`~/Library/Logs/DiagnosticReports/Python-*.ips`) captured the actual
segfault each time: `EXC_BAD_ACCESS SIGSEGV`, with the faulting stack
`fork → subprocess_fork_exec → _pthread_atfork_child_handlers →
nw_settings_child_has_forked() → nw_path_release_globals →
NEFlowDirectorDestroy → _os_log_preferences_refresh` -- a real crash inside
Apple's Network.framework, specifically in its child-side atfork handler,
not just the downstream `AttributeError` this section otherwise documents.
This confirms the corruption isn't merely a Playwright/asyncio-level
book-keeping bug recoverable by a Python-level retry: it's a hard segfault
in OS-provided code, which is why the in-process retry (which can only catch
Python exceptions) never had a chance here, and reinforces that per-bank
process isolation is the only reliable mitigation, not a smarter retry.
Workaround applied successfully: `aiib`, `badea`, and `ebrd` each run to
completion without incident when launched as their own separate process
(confirmed live, this session) — it's specifically multiple
`sync_playwright()`-driven launches sharing one process, sequential or not,
that's unsafe on this machine.

## Recently resolved: standardized sector_category / sector_category_code across all 15 banks

Sectors were disclosed in wildly incompatible vocabularies — OECD DAC 5-digit
purpose names (adb/afdb/isdb), Spanish free text (caf), ALL-CAPS bank jargon
(idb), short internal labels (aiib/cdb/ndb/cabei) — making any cross-bank
sector comparison or aggregation impossible. `schema.py` gained two new
fields, `sector_category` and `sector_category_code` (the OECD DAC 3-digit
category, e.g. `"Transport & Storage"` / `"210"`), derived in
`dac_sectors.py`'s new `sector_category_from()` and wired into both
`generic_record_from_row()` (`common.py`) and World Bank's separate
hand-built `ProjectRecord(...)` path (`worldbank.py` — the only other place
in the codebase that constructs a record directly instead of going through
the shared function). Original `sector`/`sector_code`/`sector_vocabulary`
fields are untouched.

Derivation tries 4 tiers before giving up (never guesses): (1) a disclosed
DAC code, if present; (2) an exact match against the DAC's own 326 purpose
names (many publishers' narrative text already reads this way — e.g. AfDB's
sector text resolved 3,655/3,667 records, 99.7%, with zero hand-curation);
(3) an exact match against the DAC's 44 category names directly (some
publishers, e.g. CABEI, disclose category-level text as-is); (4) a
hand-curated `SECTOR_TEXT_TO_CATEGORY` lookup for whatever's left — built
against each bank's real, complete distinct-value list (aiib 16, cabei 30,
caf 12, cdb 11, idb 18, ndb 8, plus ~60 World Bank sub-sector fragments
covering 99.9% of a live 1,500-project IBRD sample). Deliberately-vague
values (`"Other"`, `"OTHER"`, `"Youth"`, `"Infrastructure"`) are left
unmapped on purpose rather than guessed. Live coverage confirmed per bank
(2026-08-24): adb/caf/isdb/ndb 100%, ibrd 100% (of sector-having rows),
afdb/idb/aiib/cdb 97-99%, cabei 99%.

Two bugs were found and fixed along the way, both blocking clean input to
the above:
- **World Bank's `_sector()` was leaking raw Python dicts into the sector
  text** (e.g. `"Social Protection; {'Name': 'Public Administration - Social
  Protection'}; {'name': ..., 'code': 'SG'}"`, 4,612/2,577 "distinct" values
  for ibrd/ida). Root cause: when a sector field's value is a `list`
  (`sector`, `sector_namecode`), the code did `values.extend(value)`,
  pushing the raw dicts in instead of extracting `"Name"`/`"name"` first (it
  only did that extraction when the field was a single `dict`, e.g.
  `sector1`). A second bug in the same function: `sector1: {"Name": "",
  "Percent": 0}` (the API's own empty-sector placeholder) leaked through as
  the literal string `"0"` (2,073 + 828 occurrences) because the dict was
  treated as truthy and its raw `.values()` (including the integer `0`)
  dumped in. Both fixed in `worldbank.py`'s `_sector()`/new `_sector_name()`
  helper.
- **`cleaning.py`'s `normalize_loan_type()` misclassified "Nonsovereign"
  (no space) as `"Sovereign"`** — the opposite of correct. AIIB's own
  `"FINANCING TYPE"` field (100% populated, confirmed live) was also found
  mis-routed to `financing_instrument` instead of `loan_type` in `aiib.py`.
  Fixing the routing without first fixing `normalize_loan_type()` would have
  silently mislabeled all 187 AIIB non-sovereign records as Sovereign: the
  "non sovereign" (with space) substring check never matched AIIB's
  concatenated "Nonsovereign", so it fell through to the plain "sovereign"
  substring check, which does match. Fixed by also checking for
  `"nonsovereign"` explicitly.

## Recently resolved: missing country (10,154 records), unlabeled XDR currency, and IBRD/IDA's misleading $0 rows

Diagnosed against a real, full `--all-banks` run fed into Stata for
cleaning, which surfaced three more issues.

**Missing `country` (10,154 records, 22.7% of the whole dataset) was a real
bug with a clean fix.** 10,143 of those were 100% of `adb`/`afdb`/`caf`/`isdb`
(every IATI-sourced bank). Root cause, confirmed live: IATI discloses the
country as a bare ISO2 code only --
`<recipient-country code="DZ"/>`, no `<narrative>` name at all -- and
`country_code` was already being correctly populated (97-98% of these same
rows) via the generic XML attribute flattening; the code was just never
translated to a name. New module `mdbs_scraper/iati_geography.py`
(`IATI_COUNTRY_CODES`, 257 entries, and `IATI_REGION_CODES`, 26 entries,
both generated programmatically from
`https://codelists.codeforiati.org/api/json/en/Country.json` and
`.../Region.json` -- the same authoritative-source convention as
`dac_sectors.py`) adds `country_name_from_code()`/`region_name_from_code()`,
wired into `generic_record_from_row()` (`common.py`) as a fallback when
`country` text is blank. A second piece of the same gap: AfDB's
`afdb-multinational` IATI package (987 activities in a full run) discloses
`<recipient-region code="298"/>` ("Africa, regional") instead of
`<recipient-country>` for multi-country projects -- there's no
`FIELD_ALIASES` entry for a region code at all previously, so these had *no*
geography whatsoever. New `region_code`/`region_vocabulary` `FIELD_ALIASES`
entries (mirroring `sector_code`/`sector_vocabulary`) plus a region fallback
for `country` when no country code is present either. Live re-check:
`adb`/`caf`/`isdb` now 100% `country` coverage, `afdb` 99% (the residual
0.7% is activities with neither a `recipient-country` nor a
`recipient-region` element at all).

**AfDB and IsDB's `loan_currency` was blank, not "XDR"/their real currency
-- a real bug.** Live-fetched an AfDB activity:
`<iati-activity default-currency="XDR" ...>` with every `<value>` element
omitting its own `currency` attribute (both valid per the IATI standard,
which lets per-value currency be inherited from the activity's declared
default). `tabular.py`'s `_iati_transaction_fields()` only ever read
`value_element.get("currency", "")`, never falling back to the activity's
`default-currency` -- so the currency ended up as an empty string, not
"XDR", for every AfDB transaction (and similarly for IsDB, which turned out
to use "USD" as its own default rather than XDR -- confirmed live per-bank,
not assumed). This was worse than just leaving `loan_amount_usd` blank
(which is correct and unchanged -- XDR/local-currency-to-USD is a real
conversion this project deliberately never invents): a reader couldn't
previously tell these figures were in a different unit at all. Fixed with
one fallback line in `_iati_transaction_fields(activity)` (which already
receives the enclosing activity element). Live re-check: `afdb` now 100%
`loan_currency` coverage, entirely "XDR"; `isdb` 100%, entirely "USD"; `adb`
(already working before this fix, confirmed unaffected) shows its real
per-loan mix (USD/INR/CNY/EUR/GEL/...).

**IBRD's ~8,300 (70.6%) "$0 `loan_amount`" rows, and most of its 33.7%
missing-`commitment_year` rate, traced to the same design flaw in
`worldbank.py`'s `_included()`.** It included a project in `ibrd_projects.csv`
if `ibrdcommamt > 0` *or* `"IBRD"` merely appeared in the project's `source`
list, even at `ibrdcommamt == 0`. Live-fetched 8 examples: this fallback was
catching projects that are actually 100% IDA-financed
(`ibrdcommamt: 0, idacommamt: 280,000,000, source: ['IBRD']`) or
pure-grant-financed (`ibrdcommamt: 0, idacommamt: 0, grantamt: 10,800,000`)
-- `source` names an administering/lead unit, not reliably who actually
financed the project. These zero-content rows also typically had no
`boardapprovaldate` (of the 3,985 missing-year IBRD rows, 2,967 (74.5%) were
these same rows). Per the user's explicit choice (keep the dataset to rows
with a real, positive commitment from that specific arm; the real financing,
when it's IDA's, already appears correctly in `ida_projects.csv`), the
`"source"`-based fallback was removed entirely -- inclusion is now solely
`amount is not None and amount > 0`. Live re-check (uncapped): both symptoms
disappeared in the same sample together, confirming the shared root cause.

**CDB confirmed, again, to have a genuine source-side gap, this time at the
recent end:** live-checked the same day, the listing page has exactly 94
project links (matching what's retained exactly -- no pagination
truncation) and nothing newer than 2023, despite the page's own text
claiming to cover "2013-2026." `banks.json`'s `cdb` `coverage_notes` updated
accordingly.

**NDB (45 records) and AIIB (13 records)'s blank `loan_amount_usd`
confirmed genuine, not a bug:** all of NDB's 45 have a real, populated
non-USD `loan_currency` (RMB/EUR/ZAR/INR/CNY/CHF), and AIIB's 13 are all
EUR -- both banks really do disclose some loans in other currencies, so a
blank USD conversion for those specific rows is correct, not missing data.

## Recently resolved: negative disbursements, EBRD/CDB/BADEA gaps, IATI status codes, confirmed-correct duration, and a universal ISO-3 `country_code`

Diagnosed against a Stata diagnostics pass over a real 44,635-record
`--all-banks` combine, which surfaced pervasive missingness (loan amounts,
year, country code, currencies), gaps in `sector_category`, a 21-year max
project duration, and 23 negative `total_disbursement` values. Every item
below was traced to a specific, verified root cause against the real,
current `output/*.csv` plus live re-checks of the underlying IATI XML,
EBRD's own downloaded workbook, a live CDB detail page, the World Bank's
live site, and independent codelist/ISO datasets -- not guessed.

**Negative disbursements (23 records, all ADB) were real, ADB-disclosed
data, not a parsing bug -- but a balance correction recorded as a
Disbursement-type transaction.** Traced one example
(`XM-DAC-46004-55343-001-GRNT0822`) to its live IATI activity: commitment
$135,000,000, then ~30 monthly disbursement (IATI type 3) transactions,
almost all $0, plus one genuine +$35,000,000 and one **-$135,000,000**
"correction" transaction the same month ADB apparently reconciled the
ledger (`<value currency="USD">-135000000...</value>` is literally in
their published XML). ADB recorded this as a negative Disbursement-type
(3) transaction rather than IATI's dedicated Reimbursement type (7), so
the previous net-sum approach surfaced a confusing negative "money
disbursed" figure. Fixed (per explicit choice: sum positive transactions
only): `_iati_transaction_fields()` (`tabular.py`) now sums only
*positive*-valued disbursement transactions into `iati_disbursement_value`;
negative ones are excluded from the sum but not discarded -- their combined
magnitude surfaces via a new `iati_disbursement_correction` field, and
`generic_record_from_row()` (`common.py`) adds a `data_quality_notes` entry
disclosing the excluded amount whenever this fires. `total_disbursement`
can no longer be negative for any bank. Tests:
`tests/test_tabular.py::IatiTransactionFieldsTests`.

**EBRD: currency, sector, and status.** *Currency* (8,084 records, 100% of
EBRD): live-downloaded EBRD's own project workbook -- row 4 of its "List"
sheet, directly above the column headers, reads **"€ million at reported
rates unless specified"**, explicit. Fixed via `default_currency: "EUR"` in
`banks.json`'s `ebrd` entry, the same mechanism `caf`/`adb`/`isdb` already
use. *Sector* (6,887 of 8,084 missing `sector_category`; the raw `sector`
text itself was 100% populated across only 13 distinct values, never
hand-mapped before this session): added EBRD's 13 values to
`dac_sectors.py`'s `SECTOR_TEXT_TO_CATEGORY`. *Status* (100% missing --
EBRD's downloaded workbook has no status column at all, confirmed live):
per explicit choice, inferred as `"Signed"` for every record. The
workbook's own glossary defines its title, "Net Cumulative Bank
Investment," as "stock of all **commitments made** by the Bank since
inception" -- which only happens at/after signing, even though EBRD's live
site separately confirms a fuller pipeline exists (Exploratory -> Concept
-> Structure -> Final Review -> Board Approved -> Signed -> Implementing ->
Completed) that this specific report's scope wouldn't capture anyway.
Implemented as an `EBRDScraper.map_row()` override (`ebrd.py`) that sets
`record.status = "Signed"` and adds a `data_quality_notes` entry stating
this is inferred from the report's own definition, not individually
disclosed per project. Tests: `tests/test_ebrd.py`,
`tests/test_field_aliases.py::EbrdCsvAliasTests`.

**CDB's "Approved total" was mapped to the wrong canonical field.**
Confirmed live (fetched a real CDB detail page): "Approved total" is the
*only* financial figure CDB discloses per project, but `cdb.py`'s
`_DETAIL_LABELS` mapped it to `total_project_cost` -- so `loan_amount`/
`total_commitment` were 100% blank for all 94 CDB records despite the
figure being disclosed. Remapped `"Approved total"` to `loan_amount`; the
existing loan-amount-to-commitment fallback in `generic_record_from_row()`
then correctly populates `total_commitment` too, and `total_project_cost`
now correctly stays blank (CDB doesn't disclose a separate, broader
project-cost figure). Test: `tests/test_cdb_ndb.py`.

**BADEA's 8 classification values were never hand-mapped** (106 of 131
records missing `sector_category`). Confirmed against the current output:
exactly 8 distinct values (`Social`, `Capacity Development`, `Microfinance,
SMEs and Entrepreneurship Development`, `Agriculture, Rural Development and
food security`, `Utilities`, `Industrialisation`, `Transport` -- already
covered, `Women and Youth Development`). Added to
`SECTOR_TEXT_TO_CATEGORY`.

**`status` was never populated for adb/afdb/caf/isdb (10,153 records, 100%
of each).** IATI's `<activity-status code="X">` is disclosed (confirmed
live, e.g. code `3` = Finalisation on a real AfDB activity) but nothing in
`FIELD_ALIASES["status"]` matched the generically-flattened
`activity_status_code` key. Fixed with a small `IATI_ACTIVITY_STATUS_CODES`
lookup (`dac_sectors.py`, 6 stable codes fetched live from
`codelists.codeforiati.org`: Pipeline/identification, Implementation,
Finalisation, Closed, Cancelled, Suspended), a new
`FIELD_ALIASES["status_code"]` entry, and a `status_name_from_code()`
fallback in `generic_record_from_row()` mirroring the existing
sector/country code-derivation pattern, with a matching `data_quality_notes`
entry. Tests: `tests/test_tabular.py::IatiTransactionFieldsTests::test_generic_record_from_row_maps_iati_fields`.

**Max duration (21.75 years) confirmed correct, not a bug.** Traced the
actual record: `XM-DAC-46004-55248-001-GRNT4200` (ADB), commitment
2022-08-31, completion 2044-05-31 -- already carries the existing
"planned, not actual" note from an earlier session's fix, since 2044 is a
disclosed *planned* completion date, not a parsing artifact. A second,
actual-dated long-duration example (AfDB, 17.6 years, an oil-rig-repair
facility construction project, 2003-2020) was also spot-checked against its
live IATI record: both start and end dates are genuinely type-2/type-4
(actual), confirmed real. No code change; documented here so it isn't
re-investigated later.

**World Bank dropped/pipeline/cancelled projects' missing commitment year
-- investigated, not pursued (per explicit choice).** Live-checked: the
World Bank API's `boardapprovaldate` is genuinely null for most of these
(confirmed on real examples, including `P171272`, status Dropped, which
still has a real `ibrdcommamt` of $100,000,000 -- so this isn't limited to
the already-fixed zero-content rows from an earlier session). The *live
website* (JS-rendered, not the API) does show "Approval Date"/"Concept
Review" for at least some dropped projects, but recovering it would need
rendering each affected project's own page via a real browser -- for the
~1,977 combined ibrd/ida records missing a year, that's roughly 1.5-2.5+
hours of additional sequential scraping plus a new per-project detail-page
capability that doesn't exist today (ibrd/ida are pure JSON-API adapters,
with no detail-page-walking machinery to reuse). Not pursued; these records
keep their existing "commitment/approval year is missing; record was
retained" note. Documented so the tradeoff doesn't need re-deriving if
reconsidered later.

**A universal ISO 3166-1 alpha-3 `country_code`, with 0 missing values for
every single-country project (per explicit choice).** The free-text
`country` field itself is reported inconsistently across the 15 banks
(mixed case, accents, long-form vs. short names, Spanish/French spellings),
and the pre-existing `country_code` field made this worse rather than
better: it held real IATI alpha-2 codes for adb/afdb/caf/isdb (~98% each),
but IDB's own **proprietary, non-ISO** 2-letter scheme elsewhere (confirmed
live: IDB's `"PR"` means Peru, not Puerto Rico), and nothing at all for
every other bank -- not usable as a single clean analysis/join key.

`country_code` is repurposed to hold a uniform ISO-3 code, derived from the
country *name* text rather than trusted from whatever a source happened to
disclose (which also naturally discards IDB's non-standard codes, since its
name text is used instead). New module additions in `iati_geography.py`:
- `IATI_ALPHA2_TO_ALPHA3` (253 of the module's existing 257 alpha-2 codes):
  IATI's own Country codelist has no alpha-3 field, so this is sourced from
  a second, independent authoritative dataset -- the community-maintained
  mirror of the official ISO 3166 tables at
  [lukes/ISO-3166-Countries-with-Regional-Codes](https://github.com/lukes/ISO-3166-Countries-with-Regional-Codes)
  (`all/all.json`). Of the 8 codes with no direct match (all
  withdrawn/historical), 4 have an unambiguous modern successor and are
  hand-resolved (Burma -> Myanmar, East Timor -> Timor-Leste, Zaire -> Congo
  (the Democratic Republic of the), Kosovo -> the widely-used unofficial
  user-assigned code `"XKX"`, which has no official ISO 3166-1 code at
  all); the remaining 4 (Netherlands Antilles, Neutral Zone, Serbia and
  Montenegro, Yugoslavia) each split into multiple successor states with no
  single correct answer and are deliberately left unmapped.
- A generic "(the)"-suffix strip: IATI's ISO "friendly" names carry a
  trailing definite article (`"Bahamas (the)"`, `"Sudan (the)"`, `"Gambia
  (the)"`, `"Philippines (the)"`, ~15 more) that almost no bank source
  repeats -- handled once, mechanically, in `_NAME_TO_ALPHA2`'s construction
  rather than as ~15 near-duplicate alias entries.
- `COUNTRY_NAME_TO_ISO2` (28 curated entries): real single countries under a
  different name/spelling/language, curated live against the exact 78
  distinct `country` strings in the combined output that didn't already
  resolve (2026-08-25) -- WB/IDB long-form names (`"Egypt, Arab Republic
  of"`, `"Congo, Democratic Republic of"`, `"Venezuela, Republica
  Bolivariana de"`, `"Korea, Republic of"`), plain short forms IATI's list
  doesn't have standalone (`"Bolivia"`, `"Vietnam"`, `"Russia"`,
  `"Moldova"`, `"Venezuela"`, `"Tanzania"`), Spanish/French spellings from
  caf/badea (`"República Dominicana"`, `"Belice"`, `"Sao Tomé-et-Principe"`,
  `"Guinea Conakry"`, `"Congo Brazzaville"`), and `"St."`/abbreviation
  variants (`"St. Lucia"`, `"Lao PDR"`).
- `iso3_country_code_from_name()`: normalize (accent-fold + lowercase,
  reusing `dac_sectors.py`'s pattern) -> exact IATI name match (including
  the "(the)"-stripped form) -> alias-table match -> alpha-3 conversion.
  Returns `""` for anything else, including genuinely multi-country,
  regional, or institutional text (`"Regional"`, `"Africa, regional"`,
  `"OECS Countries"`, `"BADEA"`) -- never guessed.

Wired into `generic_record_from_row()` (`common.py`): when `country` was
itself just derived from a disclosed `country_code` (adb/afdb/caf/isdb's
existing code-to-name path), that code has already been proven genuine by
the very fact the translation succeeded, so it's converted to alpha-3
directly (`alpha3_from_alpha2()`); otherwise `country_code` is derived
purely from the `country` name text. A `data_quality_notes` entry is added
whenever a non-blank `country` still can't resolve to a code, explaining
why (never silently blank without explanation, matching this project's
existing convention throughout).

**A real bug caught during verification, not during implementation:** the
first live rescrape after wiring this into `generic_record_from_row()`
showed `ibrd`/`ida` at ~0% `country_code` coverage (3,468/3,469 and
5,221/5,221 unresolved) -- wildly off the ~13% non-codeable rate every
other bank showed. Root cause: `worldbank.py` builds `ProjectRecord`
directly (`WorldBankScraper._record()`) instead of going through
`generic_record_from_row()` at all -- the *only* other place in the
codebase that does this (already true of, and already handled for,
`sector_category` -- see "standardized sector_category" above -- but missed
for this new field). Fixed by deriving `country_code` from `country` (via
the same `iso3_country_code_from_name()`) directly inside `_record()`, with
the same style of `data_quality_notes` entry when it can't resolve. Test:
`tests/test_registry_and_adapters.py::test_world_bank_derives_an_iso3_country_code_from_country_name_text`.

**Live-verified against a fresh, full rescrape of all 13 currently-working
banks** (44,632 records, `output/all_mdb_projects.csv`, 2026-08-25; `eib`
excluded -- see the new "Open" section below): 39,090 records (87.6%)
resolve to a real ISO-3 code; of the 5,503 that don't, every one is a
genuinely non-codeable multi-country/regional/institutional grouping
(`"Regional"` alone accounts for 4,131, mostly `idb`'s 4,124; the rest are
things like `"Africa, regional"` (afdb, 875), `"Multicountry"`, `"OECS
Countries"`, `"Aral Sea"`, `"BADEA"`/`"BDEAC"`) -- zero real countries
slipped through uncoded, confirmed per-bank. `ibrd`/`ida`'s residual
(28/3,483 and 318/5,217) is the World Bank's own regional-program entries
(`"Africa"`, `"Central Africa"`, `"West Africa I"/"II"`, etc.), the same
kind of grouping every other bank shows. The remaining 39 records have no
`country` text at all (a separate, pre-existing gap, not something this
feature could address). Also confirmed: negative `total_disbursement` is
now 0 across the whole combined dataset (was 23, all ADB); the traced
example (`XM-DAC-46004-55343-001-GRNT0822`) now shows `total_disbursement:
35000000` with its correction note intact; `ebrd` is 100%
`loan_currency`/`sector_category`/`status`; `cdb`'s `loan_amount` is 100%
(was 0%); `badea`'s `sector_category` is 83.2% (was 19%); `status` is 100%
for adb/afdb/caf/isdb (was 0% for all four). Tests:
`tests/test_iati_geography.py` (`Iso3CountryCodeFromNameTests`,
`Alpha3FromAlpha2Tests`, `IatiGeographyWiringTests`), plus new/extended
assertions in `tests/test_field_aliases.py` covering CABEI/EBRD/IDB's real
row shapes (including the Argentina/Peru "IDB's own code would have been
wrong" case) and `tests/test_registry_and_adapters.py` for World Bank's
separate record-construction path.

*Superseded 2026-09-16:* that count predates EIB's switch to its own portal. About 634 rows with a real single country and a blank `country_code` had crept back in (EIB "United Kingdom" 359, "The Netherlands" 223, CDB "The Bahamas", ...). The alias table now resolves every single-country name found in the output and in the official files, and `tests/test_iati_geography.py` pins each mapping.

One known, deliberate simplification: a handful of IATI activities disclose
more than one `<recipient-country>` for genuinely multi-country projects
(confirmed live in AfDB's own files); the existing generic XML flattener
resolves only the first one, so `country_code` reflects just that first
country for those rows. Documented as a known limitation, not something
this change re-architects -- multi-value geography is a bigger schema
question than "add ISO-3 codes."

## Recently resolved: EIB switched to the IATI Registry (its official portal is Cloudflare-blocked)

A full `--all-banks` re-run (2026-08-25) failed `eib` with
`SourceLayoutChanged: no recognizable projects were exposed by the official
page`, on both the automatic in-process retry and a fresh `--bank eib`
process by itself -- ruling out the already-documented "`--parallel`/
Playwright driver-loop corruption" issue above as the cause this time.
Live-inspected `render_page()`'s captured HTML directly: it's a
**Cloudflare bot-challenge page** ("Sorry, you have been blocked" /
"Attention Required! | Cloudflare"), not EIB's real project listing at all.
A direct `urllib`/`curl` fetch of EIB's own raw IATI XML file (not just the
JS-heavy portal) also returned 403 -- the block covers the whole `eib.org`
domain. Same failure mode that previously blocked `adb`/`afdb`/`caf` (see
"Recently resolved: adb, afdb, caf, isdb now use their official IATI feeds"
above).

**EIB publishes to the IATI Registry too** -- publisher `eib`, package
`eib-act` (1,395 activities, real project data) plus `eib-org` (an
organisation-file package, excluded by the same `-org`-suffix check that
already excludes `isdb-org`). One earlier assumption from this problem's
initial write-up turned out to be wrong and is corrected here: `eib-act`'s
one resource is itself hosted on `http://www.eib.org/...` -- the very
domain that's blocked -- so plugging EIB into `CkanOrganizationScraper`
unmodified (the adb/afdb/caf/isdb pattern) would still have tried to fetch
a dead URL. The actual fix uses **IATI's own official Bulk Data Service**
(`bulk-data.iatistandard.org`), which mirrors every registry publisher's
files server-side, independent of the original host, refreshed daily --
confirmed live: `https://bulk-data.iatistandard.org/eib/eib-act.xml`
returns HTTP 200 via a plain `urllib` request (no special headers) and
parses into all 1,395 activities. `eib.py` is now a plain `TabularScraper`
subclass (`class EIBScraper(TabularScraper): pass`) pointed straight at
that mirror URL via `banks.json` -- simpler than `CkanOrganizationScraper`
*and* more robust here, since EIB has exactly one real package (unlike
adb's 41 or afdb's 57 country-specific ones), so there's no package-search
machinery this bank actually needs, and a hardcoded mirror URL never
touches `eib.org` at all. Trade-off, documented in `banks.json`'s
`coverage_notes`: the mirror is keyed by publisher+package slug, not by
resolving the registry's live resource URL, so it's immune to `eib.org`
being down but would need updating if EIB ever renames the `eib-act`
package on the Registry.

**One new field-alias fix, generic (not EIB-specific):** EIB's activities
carry `<document-link url="...">` elements that flatten to
`document_link_url`, which wasn't in `FIELD_ALIASES["project_url"]`'s alias
tuple. Added -- `project_url` went from a field EIB never disclosed at all
under the old adapter to 100% coverage, and this could incidentally help
any other/future IATI-sourced bank with `<document-link>` data too.

**`sector_category` needed real curation, unlike adb/afdb/caf/isdb.** EIB's
IATI sector vocabulary is `"99"` (Eurostat NACE Rev. 2 economic-activity
codes), not OECD DAC, so the existing disclosed-code translation tiers
never fire -- but `<sector><narrative>` text is 100% populated (unlike
adb/isdb's bare-code-only disclosure), so this only affects the
standardized `sector_category` field, not raw `sector`. Live-tested EIB's
162 distinct sector-text values against the existing pipeline: only 35
auto-resolved via coincidental DAC-name overlaps. Hand-mapped ~50 of the
most frequent values to `dac_sectors.py`'s `SECTOR_TEXT_TO_CATEGORY`
(grouped under Finance/Water/Transport/Energy/Health/Agriculture/
Construction/Social-Infrastructure DAC categories), leaving the long tail
of one-off NACE codes deliberately unmapped -- the same "diminishing
returns" convention already used for EBRD (13 values)/BADEA (8). One
notable entry: `"Global Loans, Loans for SMEs, Loans for SMEs and
Mid-Caps, Loans for Mid-Caps"` (478 of 1,395 activities, the single largest
value, an intermediated-lending-facility label rather than a NACE economic
sector) is matched via just its first-listed fragment (`"global loans"`),
using `sector_category_from()`'s existing multi-value-text tier -- more
robust than hardcoding the full string, since it survives any future
variant of the trailing SME/Mid-Cap list.

**Live-verified** (`python main.py --bank eib --min-year 2002 --max-year
2026`, 2026-08-25): 1,395 records collected, matching the source's real
activity count exactly. `loan_currency`/`sector`/`project_url` 100%;
`country` 99.9%; `status` 99.7% (the 0.3% gap is `<activity-status
code="0"/>`, 4 activities with a code outside IATI's valid 1-6 range --
already handled safely by the existing `status_name_from_code()` fallback,
degrading to a blank status with no crash and no spurious note, not a new
bug); `sector_category` 88.0% (beat the ~80% modeled target);
`country_code` (ISO-3) 79.6% -- the gap here is the 284 activities that
disclose `<recipient-region>` instead of a specific country (already
correctly left blank, not a bug -- same non-codeable-regional handling
every other bank gets); negative `total_disbursement` 0. Recombined with
the other 13 banks (`--all-banks --combine`) successfully.

## Recently resolved: EIB's and IsDB's `cofinancing_partners` self-references

Found during EIB's IATI-Registry live verification (see above): EIB's
`_iati_cofinancing_field()` filter excluded a `<participating-org role="1">`
(Funding role) entry only when its narrative text exactly matched the
activity's own `<reporting-org>` narrative ("European Investment Bank") --
**938 of 1,395 EIB records (67.2%) showed `cofinancing_partners: "EIB"`**
instead, because EIB sometimes lists itself under role="1" using its own
abbreviation rather than its full reporting-org name. That problem's own
write-up flagged a "likely fix" (also exclude an exact abbreviation match)
but deferred it, pending live-checking against other IATI banks' cofinancing
data first to rule out false positives.

Live-checking IsDB's real IATI files (`isdb_activities_a/b.xml`,
`isdb_itfc_activities.xml`, fetched directly from isdb.org) surfaced the same
class of bug, worse in scale -- **2,462 of IsDB's 2,495 records (98.7%)**
showed a self-reference, via two different mechanisms:
- **1,180 records** showed `"IsDB - Ordinary Capital Resources"` (1,079),
  `"IsDB - Endowment Fund"` (68), or `"IsDB - Islamic Solidarity Fund for
  Development"` (33) -- IsDB's own internal financing windows/funds.
  Confirmed live: every one of these `role="1"` entries carries the exact
  same org `ref` as the activity's own `<reporting-org>` (e.g. both
  `ref="XM-DAC-46025"`), even though the narrative text differs -- so the
  narrative-only filter missed them, but a `ref`-based check catches them
  precisely.
- **1,282 records** (all from the `isdb-itfc-activities` package) showed
  `"International Islamic Trade Finance Corporation"` -- IsDB's own
  trade-finance arm, already treated as part of the same `isdb` bank
  elsewhere in this codebase (`IsDBScraper._package_note()`'s existing
  disclosure-gap tagging). Confirmed live: every one of the 1,287 ITFC-file
  activities has exactly the same two `role="1"` participants -- "Islamic
  Development Bank" (already excluded) and ITFC (not excluded) -- with no
  variation and no org `ref` on the ITFC entry at all, so it can't be caught
  by a `ref`-based check. Per explicit user choice, this is also treated as a
  self-reference.

Also live-checked, confirming the general fix isn't over-broad: AfDB's
`"African Development Fund"` / `"Transition Support Facility"` / etc. (2,311
of AfDB's 5,606 records) are a **different, legitimate** case -- these
participating-orgs carry their own genuinely distinct IATI org `ref` (e.g.
`XM-DAC-46003` for African Development Fund vs. `XM-DAC-46002` for African
Development Bank), so the `ref`-based check correctly leaves them alone.
Same for EIB's real cofinanciers (`"EDF"` 256, `"MS-EC"` 193, `"EU Budget"` 4,
`"AECID"` 4) and CABEI's `"TaiwanICDF"` (2) -- none share the reporting
bank's `ref`, and none equal the bank's own abbreviation.

**Fix, three parts:**
- `_iati_cofinancing_field()` (`tabular.py`) now also excludes a role="1"
  participating-org whose `ref` matches the activity's own `<reporting-org
  ref="...">` (guarded so two blank refs never coincidentally "match").
  Bank-agnostic, benefits every IATI-sourced bank.
- `generic_record_from_row()` (`common.py`) gained a new `bank_abbreviation`
  keyword param; when set, it strips any cofinancing partner
  case-insensitively equal to the abbreviation, via the existing
  `join_values()` helper. `TabularScraper.map_row()` now passes
  `self.bank.abbreviation` -- inherited by both `CkanOrganizationScraper`
  (isdb/adb/afdb/caf) and `EIBScraper`, so this is one shared fix, not a
  per-bank one.
- `IsDBScraper` (`isdb.py`) gained a `map_row()` override that strips
  "International Islamic Trade Finance Corporation" specifically, since it
  has no `ref` signal to key off of and is a one-bank case.

**Live-verified:** `eib` -- `"EIB"` (938) gone, `"EDF"`/`"MS-EC"`/`"EU
Budget"`/`"AECID"` unchanged. `isdb` -- all three `"IsDB - <Fund>"` values
and `"International Islamic Trade Finance Corporation"` gone;
`cofinancing_partners` is now blank for all 2,974 records (IsDB's source
files grew between runs), consistent with adb/caf, which genuinely disclose
none. `afdb`/`cabei` confirmed unaffected. Tests:
`tests/test_tabular.py::IatiTransactionFieldsTests` (ref-based exclusion via
an extended `iati_transactions_sample.xml`, plus a new abbreviation-based
test) and `tests/test_registry_and_adapters.py::test_isdb_excludes_itfc_as_a_cofinancing_self_reference`.

## Recently resolved: post-fix data audit -- CABEI project_url/loan_type, BADEA status, CDB country

Diagnosed by running per-bank/per-field coverage stats over a real
`--all-banks` output (2026-08-27), cross-referencing every anomaly against
this file's existing explanations first, then live-verifying whatever
remained against the real source before treating it as a bug. Four real,
fixable issues, plus a few confirmed genuine limitations worth recording so
they aren't re-investigated.

**CABEI's `project_url` and `loan_type` were both 0% despite the source
disclosing them for every row.** Live-fetched CABEI's real CKAN CSV resource
(`actividades-web.csv`) directly: it has a `"URL IATI"` column (150/150 rows
populated, e.g. `https://d-portal.iatistandard.org/ctrack.html?...`) and a
`"Sector de Mercado"` column (150/150 populated, Sovereign/Non-sovereign
values like `"Sector Público Soberano"`) -- normalizing to `url_iati` and
`sector_de_mercado` respectively, neither previously in `FIELD_ALIASES`
(`mdbs_scraper/adapters/common.py`). Added both. `normalize_loan_type()`
(`mdbs_scraper/cleaning.py`) already handled the Spanish sovereignty terms
correctly, except one gap this surfaced: `"Sector Privado"` (16/150 records)
fell through unnormalized because only the English `"private"` was checked,
not Spanish `"privado"` -- fixed alongside. (The CSV's other classification
column, `"Sector Institucional"` / Público-Privado, is a different, coarser
axis with no clean schema slot -- deliberately left unmapped.) Live-verified:
`project_url` 0%→100%, `loan_type` 0%→100% (121 Sovereign, 29 Non-sovereign).
Tests: `tests/test_field_aliases.py::CabeiSpanishCsvAliasTests`,
`tests/test_cleaning.py`.

**BADEA's `status` was 0%, now inferred as `"Committed"`, per explicit
choice.** BADEA's only source (a Tableau dashboard, decoded in
`mdbs_scraper/adapters/badea.py`) exposes just (country, sector
classification, approval year, committed-loan amount) -- no status dimension
to extract. This is architecturally identical to EBRD's pre-fix gap, but
weaker evidence: EBRD's inferred `"Signed"` is backed by its source
workbook's own written glossary ("commitments made"); no equivalent text
exists for BADEA (checked badea.org and the dashboard's own Tableau metadata
directly) -- only the field's literal name, `"Commited Loans (in M$)"`.
Since `"Signed"` and `"Approved"` are both already distinct, real values
elsewhere in this dataset, inferring `"Signed"` here would overclaim a
specific pipeline stage. `BADEAScraper._record()` now sets `status =
"Committed"` (faithful to the source's own label, not translated into a
stage we can't confirm) with a note explaining the inference. Live-verified:
`status` 0%→100%, all 131 records (within this project's standard
`--min-year 2002 --max-year 2026` scope) `"Committed"`. Test:
`tests/test_badea.py::BADEAScraperIntegrationTests::test_status_is_inferred_as_committed_with_a_disclosure_note`.

**CDB's `country` was 92.6% (7/94 records) -- 6 of those 7 name a
country/territory in the description text, just not the structured Country
field.** Live-rendered all 7 affected detail pages (Playwright): each has a
full detail block with exactly 4 structured fields (Sector, Date of
Approval, Approved total, Status) and genuinely no Country field -- but the
page's "OVERVIEW" description paragraph explicitly names one (e.g. "... in
the Bahamas", "Government of the British Virgin Islands"). Only 1 of the 7
("Water Supply Improvement Project," naming only "the Water and Sewerage
Corporation") names no country and stays blank, correctly. Fix, validated by
prototyping the matching logic against all 7 real pages (plus 2 pages that
already had a structured Country field, confirming no regression) before
implementing: `CDBScraper.scrape()` (`mdbs_scraper/adapters/cdb.py`) now
falls back, when the structured Country field is absent, to scanning the
page's "OVERVIEW" section (the text window between the literal "OVERVIEW"
heading and "Last Updated," both stable across every page sampled) against a
small curated dict of CDB's ~19 member country/territory names and variants,
keyed by canonical name so a shorter variant ("Virgin Islands") doesn't
double-count against its own longer form ("British Virgin Islands"). Only
sets `country` when exactly one canonical country matches -- never guesses
between multiple or zero matches; adds a `data_quality_notes` entry when it
fires. Live-verified: `country` 92.6%→98.9% (93/94; the genuinely-unnamed
record stays blank). Tests:
`tests/test_cdb_ndb.py::CDBDetailPageScrapeTests` (two new cases: resolves,
and correctly stays blank).

**Confirmed genuine limitations, not bugs (documented so they aren't
re-investigated):**
- **IsDB's `project_url` is 0%**: confirmed live against all 3 of IsDB's real
  IATI activity files (`isdb_activities_a.xml`, `_b.xml`,
  `isdb_itfc_activities.xml`) -- zero `<document-link>` elements anywhere.
  Unlike EIB/adb/afdb/caf, IsDB simply never discloses this.
- **CABEI's `sector` is 61.3% (58/150 records)**: confirmed blank in the raw
  source CSV itself for those rows. The two other classification columns
  present for those same rows (`Sector Institucional`, `Sector de Mercado`)
  are Public/Private and Sovereign/Non-sovereign axes, not economic sector --
  using them as a sector substitute would misrepresent the data, so this
  stays a genuine gap.
- **201 cross-bank `project_id` collisions in `all_mdb_projects.csv`**:
  confirmed all 201 are between *different* banks (e.g. a World Bank-style ID
  coinciding with another bank's own numbering) -- `(bank_id, project_id)` is
  and remains the real dedup key; zero same-bank duplicates found.

## Recently resolved: interest_rate, last_repayment_date, and wider loan_type coverage

Asked whether `interest_rate`, `loan_type` (already existed, 0% for 8 of 14
banks), and `last_repayment_date` (didn't exist) could be added for all 15
banks. An initial pass checking each bank's project-portfolio transparency
source directly (the same sources this scraper already reads) found nothing
and nearly concluded a flat "not available" -- correctly pushed back on:
IBRD discloses per-loan terms, and AIIB applies a real pricing formula.
Digging further, live-verified across every bank into three tiers -- some
MDBs disclose loan-servicing detail (rates, repayment schedules) in a
*separate* financial-reporting dataset, distinct from the project-portfolio
transparency data this scraper otherwise reads, because they're bond-market-
active institutions with fiduciary reporting obligations most regional MDBs
don't have:

**Tier 1 -- a real, joinable, per-project number exists (implemented):**
IBRD and IDA. The World Bank's separate "Finances One" platform publishes
`IBRD Statement of Loans and Guarantees -- Latest Available Snapshot`
(`datasetId=DS00047`) and `IDA Statement of Credits, Grants and Guarantees
-- Latest Available Snapshot` (`datasetId=DS00001`), both live loan-level
datasets queried via a documented, public, unauthenticated SQL-over-HTTP API
(`POST https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice/sql`),
reverse-engineered by capturing what financesone.worldbank.org's own page
calls. Confirmed real columns: `interest_rate` (IBRD) / `service_charge_rate`
(IDA's own term for its concessional pricing -- not a market rate), plus
`last_repayment_date`, keyed by `project_id` -- the exact same ID scheme
(`"P037383"`) as the search API this scraper already uses for both banks.
1,613 of IBRD's 7,291 projects (22%) have more than one loan.

**Tier 2 -- a rate-setting formula/policy is published, but not tied to
individual projects (no per-project number exists to extract, not
implemented):** AIIB (reference rate + lending spread + maturity premium +
a borrowing-cost margin AIIB republishes every Jan/Jul 1 -- confirmed live:
fetched a real *approved* AIIB project page, zero rate/spread values
anywhere on it, only a nav link to the general pricing-policy PDF), IDB
(SOFR + Cost of Funding + IDB OC Spread, a published general policy), AfDB
("Applicable Lending Rates for Sovereign/Non-Sovereign" policy tables), ADB
("indicative lending rates," explicitly not in a per-project database per
ADB's own published FAQ). Meaningfully different from Tier 3 below -- a
formula and its current parameters *are* public -- but there's no
per-project figure to store without computing one from external market
data, which this project's "never guess" convention rules out. If revisited:
computing an effective rate per loan would need each loan's specific
formula variant/currency (not necessarily disclosed per-project either) plus
a live market reference rate feed -- a materially bigger undertaking than
this fix, kept here so it isn't confused with genuine non-disclosure.

**Tier 3 -- confirmed not disclosed at all, no formula offered either:**
EBRD (explicitly, per public sources: "the margin... is confidential to the
client and the EBRD"), CABEI/CDB/NDB (checked directly -- CDB's and NDB's
full detail-page text has zero rate/repayment/maturity/tenor mentions;
CABEI's 39-column CSV has none), BADEA (already exhaustively
reverse-engineered in an earlier session -- its Tableau dashboard has
exactly 4 dimensions, confirmed nothing else exists), adb/afdb/caf/isdb/eib
(zero `<loan-terms>`/`<crs-add>` IATI elements in any of their real activity
files, live-fetched directly). `last_repayment_date` follows the identical
Tier 1/2/3 split, since it lives in the same `<loan-terms>`-shaped
disclosure everywhere it exists at all.

**Implementation (Tier 1):** `schema.py` gained `interest_rate`/
`last_repayment_date` (both `str`, not `Decimal`/date-typed -- a multi-loan
project's value is multiple `"; "`-joined entries, matching the existing
`cofinancing_partners` convention for exactly this shape). `http.py`'s
`HttpClient` gained a `.post()` method (nothing in this codebase previously
needed one), refactored to share `.get()`'s existing retry/throttle/SSL-
fallback logic rather than duplicating it. `worldbank.py`'s new
`_fetch_loan_terms()` pages through the Finances One API (5,000 rows/request)
building a `project_id -> [loans]` lookup once per scrape (not per-project);
a fetch failure there is logged and degrades to an empty lookup rather than
failing the whole scrape, since this is a bonus enrichment layered on the
already-working, independent Projects-search-API scrape. `WorldBankScraper`
gained `asset_id`/`rate_field` class attributes (mirroring the existing
`financing_key`/`financing_label` pattern); `_record()` joins in distinct
rates/dates via the existing `join_values()` helper, and IDA rows get a
`data_quality_notes` entry clarifying `service_charge_rate` isn't a market
rate. The Finances One `loan_type` column (loan *product/pricing* codes
like `NPL`/`FSL`/`CPL`, confirmed via live query to be a completely
different axis from Sovereign/Non-sovereign) is deliberately left unmapped;
the existing `lendinginstr`-based `loan_type`/`financing_instrument` mapping
(already ~100% for ibrd/ida) is untouched.

**`loan_type` extension (adb/afdb/eib, implemented):** live-checked IATI's
official `OrganisationType` codelist (fetched from its authoritative source,
`IATI-Codelists-NonEmbedded`) against real activity files. adb/afdb/eib each
disclose a `role="4"` (Implementing) participating-org -- the org actually
carrying out/receiving the activity -- with a real `type` code, confirmed
generalizing across multiple ADB country packages (Afghanistan alone
skewed 97.5% government, but India/Vietnam show real ~45/35/20 splits, so
this isn't a small-sample artifact). caf's role="4" orgs never carry a
`type` attribute at all; isdb never discloses a role="3"/"4"
participating-org in any of its 3 files -- neither has a usable signal.
New `_iati_counterpart_org_type_field()` (`tabular.py`, mirrors the existing
`_iati_cofinancing_field()` pattern) maps public-sector codes (`10`/`11`/
`15`/`30`) to `"Government"` and private-sector codes (`70`/`71`/`72`/`73`)
to `"Private Sector"`; codes for NGOs/Multilateral/Foundation/Academic/Other
(`21`-`24`/`40`/`60`/`80`/`90`) are left untranslated, never guessed. New
`FIELD_ALIASES["loan_type"]` entry reuses `normalize_loan_type()`
(`cleaning.py`) unchanged for the "Private Sector" -> Non-sovereign
direction, but this surfaced a real, separate gap in that function: it
never recognized "government" (only "public"/"state"/"sovereign") as a
Sovereign signal at all, so the new `"Government"` text would have fallen
through unmapped -- fixed alongside, a generically-useful fix (not specific
to this new source) matching the function's existing English/Spanish
bilingual-synonym pattern.

**Also found and fixed along the way (unrelated to this request, but
blocking the combine's own refresh):** IDB's `banks.json` `source_url`
(`data.iadb.org/file/download/791d475c-...`) started 404ing --
data.iadb.org's CKAN instance renamed its download path from `/file/` to
`/files/` (singular to plural); the resource ID itself is unchanged. Fixed
by updating the URL; the same live IDB CSV export (16,696 rows, matching
the prior known-good count exactly) confirms this was a path rename, not a
dataset republish.

**Live-verified** (all with this project's standard `--min-year 2002
--max-year 2026` scope): `ibrd` `interest_rate`/`last_repayment_date`
0%->64.0% (2,227/3,481); `ida` 0%->50.6%/50.8% (2,645-2,656/5,230, each IDA
row's note correctly explains `service_charge_rate`); a multi-loan IBRD
project shows real distinct joined values (e.g. `"4.37; 0.0"` /
`"2028-05-15; 2038-08-01"`). `adb` `loan_type` 0%->99.3% (2,998 Sovereign /
474 Non-sovereign in the full live pull); `afdb` 0%->96.1% (3,516 Sovereign
/ 10 Non-sovereign -- confirmed genuine, not a bug: this package's real
portfolio is overwhelmingly sovereign-guaranteed lending); `eib` 0%->100%
(855 Non-sovereign / 540 Sovereign). Confirmed zero leakage: `interest_rate`/
`last_repayment_date` stayed blank for all 12 non-IBRD/IDA banks in a full,
fresh 14-bank combine (46,053 records); `caf`/`isdb`/`badea`/`cabei`/`cdb`/
`ebrd` correctly stayed at 0% `loan_type`. Tests:
`tests/test_registry_and_adapters.py` (World Bank loan-terms join, multi-loan
join, IDA note, fetch-failure resilience), `tests/test_tabular.py`
(counterpart-org-type mapping, including a real-shape regression added to
the existing `eib_iati_sample.xml` fixture), `tests/test_cleaning.py`
("Government" -> Sovereign).

## Recently resolved: EIB was undercounting its real portfolio by ~12x

A user reported that EIB's own downloaded project list for 2002-2026 showed
18,052 projects, while this scraper collected only 1,395 -- flagged as "a
critical error." Confirmed live and real, not a parsing bug: `eib.py`'s
IATI Registry activity file (`eib-act.xml`, the source added in the
"EIB switched to the IATI Registry" fix above) still had exactly 1,395
`<iati-activity>` elements when re-fetched -- our code faithfully extracted
everything in that file. The file itself was just a thin slice of EIB's
real portfolio.

**Root cause:** eib.org's Cloudflare block, documented 2026-08-25 (the
reason IATI was used as a substitute in the first place), was no longer in
effect as of 2026-09-03 -- confirmed live, matching a pattern this project
saw repeatedly this session with other bank domains (afdb.org, isdb.org,
iatiregistry.org, ebrd.com, ndb.int, aiib.org all flipped between
blocked/reachable at different points). With eib.org reachable again, its
real project database turned out to be a plain, unauthenticated JSON API
its own "All projects" page calls
(`https://www.eib.org/page-provider/projects/list`), reporting **`totalItems:
17013`** with no filter at all -- in the same ballpark as the user's 18,052
(EIB approves several projects a week, so a few days' difference between
counts is expected), and confirming the IATI file undercounted by roughly
12x. The per-project detail endpoint (`.../list/item/{id}`) returns the
identical shape to the list endpoint, so no separate detail-page walk is
needed -- every field this project's schema uses is already in the list
response.

Live-checked whether this same root cause (official site blocked, IATI
used as a thinner substitute) affects the other 3 banks with that same
history, since the risk is structural, not EIB-specific: `adb` (current
count 3,498) and `afdb` (3,668) both check out against their own recently-
published annual approval rates (ADB: 133 in 2023, 151 in 2024; AfDB: 180
in 2023, its second-highest year ever -- both consistent with the current
counts over a 24-year window). `caf` and `isdb` were not independently
re-verified against an official total (no equivalent clean public figure
found); if either is later found to have the same gap, treat it as a new
instance of this bug, not a one-off -- EIB's specific driver (very
high-frequency, often-small credit-line/intermediated lending -- confirmed
live, 20+ new EIB approvals in just the weeks before this fix) is a real,
identifiable reason its true volume so vastly exceeded a thin substitute
source, and neither caf's nor isdb's known scale suggests the same pattern,
but this wasn't proven, only reasoned about.

**Fix:** `eib.py` rewritten from a plain `TabularScraper` (IATI-XML-based)
to a bespoke `BaseScraper` subclass (closest existing precedent:
`WorldBankScraper`'s paginated-JSON-API-with-a-custom-`_record()` pattern)
querying the real project-list API directly, paginating at 1,000 rows/page
(confirmed live to work), deduplicating by `id` (defensive, in case ties on
the `statusDate` sort column cause page-boundary repeats -- the same
instability category already documented for EIB's old IATI-era listing).
Field mapping: `primaryTags` (`countries`/`sectors`) -> `country`/`sector`;
the positional `additionalInformation` tuple -> `status`, `approval_date`,
and two separate amounts -- confirmed live these can genuinely differ (e.g.
a real partially-signed multi-tranche facility, 150,000,000 approved /
65,000,000 signed), so both are kept (`loan_amount` = approved,
`total_commitment` = signed, correctly blank while still only Approved,
not a literal disclosed $0) rather than collapsed to one. `sector_category`
mostly resolved without new curation (this source's ~13-value taxonomy is
far cleaner than the old NACE narrative text) -- 3 new
`SECTOR_TEXT_TO_CATEGORY` entries added (`dac_sectors.py`): "Credit lines"
and "Telecom" map cleanly to existing DAC categories (Banking & Financial
Services, Communications); "Solid waste" joins the existing Water Supply &
Sanitation entries. "Services" and "Composite infrastructure" are
deliberately left unmapped -- too vague for one DAC category, same
convention as "Other"/"Infrastructure" elsewhere in that table.
`banks.json`'s `eib` entry updated accordingly; the old IATI mirror URL is
kept documented as a fallback if the Cloudflare block returns, just a
materially incomplete one.

**Trade-off, accepted deliberately, not silently absorbed:** this source
has no IATI-shaped `<participating-org>`/`<loan-terms>` disclosure, so
`cofinancing_partners` and `interest_rate`/`last_repayment_date` (all
previously populated for EIB via the IATI file, the last two only just
added this session) are not available here -- every EIB record now carries
an explicit `data_quality_notes` entry saying so. 12x more real projects is
the clear right trade.

**Live-verified** (`--min-year 2002 --max-year 2026`, this project's
standard scope): `eib` record count 1,395 -> 10,494 (the gap from the
unfiltered 17,013 is exactly the expected effect of the year filter --
EIB has lent since 1958, so a meaningful share of its full history
naturally falls before 2002). Field coverage on the new source:
`project_id`/`project_name`/`country`/`sector`/`status`/`loan_amount`/
`project_url` 100%; `approval_date`/`commitment_year` 99.6%;
`sector_category` 91.4% (beat the ~88% achieved on the old IATI source);
zero duplicate `project_id`s. `cofinancing_partners`/`interest_rate` now
correctly 0% (was up to 67.2%/100% under the old source) with the
trade-off note present on every record. Tests: `tests/test_eib.py` (new --
real-shape field mapping, the approved-vs-signed amount distinction,
`source_total` from the API's own `totalItems`, and pagination
dedup-by-id), plus `tests/test_cli.py` and
`tests/test_registry_and_adapters.py` updated with a new
`eib_projects.json` snapshot fixture (EIB's bespoke JSON shape needed its
own fixture, the same way `worldbank_projects.json` already does for
ibrd/ida -- both are excluded from the shared `common_projects.csv`
snapshot test for the same reason).

## Recently resolved: interest_rate came out as a string, even for single-loan projects

`interest_rate` (IBRD/IDA only, via the Finances One loan-terms join
documented above) was `str`-typed for every record, including the common
single-loan case, because the field's shape has to accommodate genuinely
distinct multi-loan rates (`"; "`-joined, matching `cofinancing_partners`'
existing convention). That's correct for the multi-loan case, but it forces
a manual `destring` before this column is usable numerically downstream
(e.g. in Stata) even for the ~98% of rated projects that have only one rate.

**Fix:** added `interest_rate_pct: Decimal | None`, a numeric companion to
`interest_rate`, populated only when a project's loans yield exactly one
*distinct* rate value (not one loan -- two loans at the same rate still
populate it; confirmed by a dedicated test). Left blank, with a
`data_quality_notes` entry explaining why, for the genuine multi-rate case,
rather than picking one value and discarding the rest -- `interest_rate`
itself is untouched and still carries the full joined list for that case.
Implemented in `worldbank.py`'s `_loan_terms()` (now also returns the
distinct-rate `Decimal` via `parse_decimal()`) and threaded through
`_record()`. `schema.py` gained the field plus a negative-value validation
check, matching the existing pattern for `Decimal` fields.

`last_repayment_date` was deliberately left as-is (still `str`, still
joined) -- it doesn't have the same "string blocks numeric analysis"
problem dates have in Stata, and collapsing genuinely distinct multi-loan
repayment dates would lose information for no typing benefit.

**Live-verified** (`--min-year 2002 --max-year 2026`): of 3,475 IBRD
records, `interest_rate` populated 64.0% (2,224) as before; `interest_rate_pct`
populated 62.9% (2,187) -- the gap is exactly the 37 genuine multi-rate
projects (e.g. `P101653`: `interest_rate` = `"4.37; 0.0"`,
`interest_rate_pct` blank with the new note present). Tests:
`tests/test_registry_and_adapters.py` (single-loan and IDA
`service_charge_rate` cases assert the new numeric value; the existing
multi-loan test asserts `interest_rate_pct is None` plus the note; a new
same-rate-multi-loan case confirms distinct-value count, not loan count, is
what's checked). Full suite (108 tests) green.

## Recently resolved: re-verified interest_rate is still genuinely unavailable for the other 12 banks (2026-09-03)

Given the EIB incident above -- a source previously "confirmed unavailable"
turned out to be a stale conclusion once eib.org's Cloudflare block quietly
lifted -- the Tier 2/3 "not disclosed" conclusions from the interest_rate
investigation (this doc, above) deserved a live recheck rather than being
taken as permanently settled, especially since several bank domains were
observed flipping blocked/reachable across that same session.

**Method:** for each of the 12 zero-coverage banks, re-checked live
reachability of the bank's own site (not just its IATI/CSV substitute), and
where reachable, fetched a real project detail page/section and searched for
`interest`/`rate`/`margin`/`spread`/`tenor`/`maturity`/`repayment` -- the same
terms the original tiering pass used.

**Findings, no bank promoted:**
- **AIIB** (Tier 2): live-fetched a real approved project's FINANCING
  section -- discloses only `APPROVED FUNDING` and `FINANCING TYPE`, still no
  rate/margin field. Unchanged.
- **ADB**: `www.adb.org`'s homepage is reachable (200), but `/projects`,
  `/projects/search`, and individual project pages (e.g.
  `/projects/53303-001/main`) all still return 403 -- the bot-protection is on
  content pages specifically, same as before. Unchanged.
- **AfDB**: both `www.afdb.org` and `mapafrica.afdb.org` still return 403 at
  the root and on content paths -- fully blocked, unlike EIB's case where the
  block had actually lifted. Unchanged.
- **IDB**: `www.iadb.org` (root and `/en/projects`) still 403; `data.iadb.org`
  (the CSV host already used as this project's source) is reachable but its
  shape is unchanged -- no rate column. Unchanged.
- **EBRD**: `ebrd.com` reachable; the existing finding rests on an explicit
  published confidentiality policy ("the margin... is confidential to the
  client and the EBRD"), not an absence-of-evidence conclusion, so it wasn't
  re-litigated with a full new PSD crawl. Unchanged.
- **NDB**: `ndb.int` reachable; live-fetched a real project detail page
  (Lucknow Metro Rail Phase 1B) and searched its full rendered text -- zero
  genuine `interest`/`margin`/`spread`/`tenor`/`maturity`/`repayment`
  mentions (the few `rate`/`margin` string hits were CSS boilerplate, not
  content). Unchanged.
- **IsDB**: `isdb.org`'s root is reachable (200, same flip-prone pattern
  noted in the EIB session), but `/projects` still returns 403 -- same
  root-open/content-blocked pattern as ADB/AfDB/IDB. Unchanged from the
  existing IATI-based finding (zero `<loan-terms>` elements).
- **CABEI, CDB, BADEA, CAF**: all four domains are reachable (200) at the
  root; not re-verified at content-page depth in this pass -- lowest priority
  by design, and each one's original finding was already reached by
  inspecting real content through a non-blocked channel (CABEI's full CSV,
  CDB's detail-page text, BADEA's Tableau dashboard's exact dimension count),
  not a blocked-domain guess the way EIB's or these other six banks' findings
  were. Flagged as not re-checked this pass, not re-confirmed.

**Conclusion:** the EIB pattern (a blocked official site hiding a materially
richer source) did not repeat for `interest_rate` specifically among the six
banks it was most likely to apply to. All Tier 1/2/3 conclusions from the
original investigation stand. No code changes from this pass; this is a
re-verification record only, dated so a future session knows how current the
"not disclosed" conclusion still is.
