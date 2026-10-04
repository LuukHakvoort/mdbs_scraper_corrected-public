# MDB project scraper — corrected research edition

> **This is a public-release export.** It's a sanitized, single-commit copy
> of a private development repository — squashed history, no per-commit
> author log carried over. It's a hard dependency of the companion
> [`mdb_eligibility`](https://github.com/LuukHakvoort/mdb_eligibility)
> repo, which imports it directly; clone it as a sibling folder named
> exactly `mdbs_scraper_corrected` (regardless of what this repo itself is
> named) for those imports to resolve.


This is a clean replacement for the uploaded scraper. It implements **15 concrete
institution adapters**, a single documented project schema, bank-level project
counts, provenance, explicit missing-data notes, local snapshot support, and an
offline test suite.

The uploaded archive said “15” but configured only 14 IDs. This edition retains
those 14 institutions, corrects `cabi` to **CABEI** (`cabi` remains an accepted
alias), and uses the **European Investment Bank (EIB)** as the fifteenth.

Which banks are active is data, not code: every institution — including which
ones are enabled — is defined in [`mdbs_scraper/banks.json`](mdbs_scraper/banks.json),
not hardcoded in Python. Set a bank's `"enabled"` field to `false` to exclude it
from `--list-banks`/`--all-banks` without touching any `.py` file (the IMF entry
ships disabled by default: its lending arrangements are country programs, not
MDB investment projects). Re-enabling a bank only requires a concrete adapter
class to already exist for its `id` in `mdbs_scraper/registry.py`; the CLI
fails fast at startup if `banks.json` enables an id with no matching adapter.

Per-bank scraping tuning is also data, not per-bank Python subclasses. Each
entry in `banks.json` may set:

- `default_currency`, `day_first` — parsing hints for amounts/dates.
- `project_href_pattern`, `pagination_href_pattern`, `max_listing_pages` — how
  an HTML portfolio adapter recognizes project links and pagination.
- `amount_period_is_decimal` — forces a lone `.` in an amount to always mean
  the decimal point, for sources whose amount column has a fixed decimal-digit
  count (e.g. IDB's `14630855.000`) that would otherwise be misread as
  European thousands grouping.

A bank only needs its own file under `mdbs_scraper/adapters/` when it needs
genuinely bespoke logic beyond these knobs (e.g. CABEI's CKAN resource
discovery, EBRD's browser-driven download button, or the World Bank/IMF
adapters' different API shapes).

## Important research safeguards

- Blank financial or conditionality fields mean **not exposed by that source**,
  not zero or “no conditions.”
- Original currencies are preserved. USD fields are filled only when USD is
  explicit in the source; the code performs no undocumented FX conversion.
- A bank financing amount is never treated as total project cost. Those concepts
  have separate columns.
- `project_counts_and_coverage.csv` reports the filtered, deduplicated record
  count and field coverage for each bank.
- Every row contains its source URL, retrieval time, and data-quality notes.
- A requested bank that fails makes the command exit non-zero by default. The
  failure is recorded in `manifest.json`; there is no silent empty success.

Public databases do not consistently disclose disbursement, subnational location,
or conditionality. Those variables require source-specific follow-up and, for
conditionality in particular, often document coding rather than a project-list
scrape. This scraper preserves that distinction instead of inventing values.

## Installation in VS Code

Python 3.10 or newer is required. Open this folder in VS Code and select the new
virtual environment as the Python interpreter.

### Windows PowerShell

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
playwright install chromium
```

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
playwright install chromium
```

CSV, JSON, XML, HTML, and the World Bank API use only the Python standard
library. `openpyxl` is needed for XLSX sources/output; Playwright and Chromium are
needed for JavaScript-rendered portals and the EBRD project download control.

## Quick start

List the exact registry:

```bash
python main.py --list-banks
```

Every run defaults to the 2002-2026 project scope (`mdbs_scraper/year_filter.json`) unless
`--min-year`/`--max-year` are passed explicitly, or the file's `"enabled"` is set to `false` to
collect full, unfiltered history instead.

Start with the World Bank adapters, which do not need a browser:

```bash
python main.py --banks ibrd ida --combine
```

Run all enabled banks sequentially, merge the official portfolio files, check
the result against them, and build the constant-USD country-year files:

```bash
python main.py --all-banks --combine --dyad      # or: make scrape
```

Browser-driven banks (`aiib`, `badea`, `ebrd`) run in their own child process
within a multi-bank run, so one command always yields all 14 banks. If you do
collect banks in separate runs, rebuild the combined file from the per-bank
CSVs afterwards instead of relying on the last run's `--combine`:

```bash
python main.py --all-banks --combine-only        # or: make combine
```

A `--combine` run that leaves banks out prints which ones are missing.

Set a contact address for the user agent (it is never committed):

```bash
export MDBS_SCRAPER_CONTACT="you@university.edu"
```

Use limited parallelism only after single-bank runs work on your machine:

```bash
python main.py --all-banks --combine --parallel 3
```

Useful debugging options:

```bash
python main.py --bank aiib --max-projects 10 --no-headless --verbose
```

By default, a failure returns exit code 2. For an exploratory all-bank run where
successful banks should still count as a successful command, add
`--allow-partial`. Failures are still retained in the manifest.

## Reproducible snapshot workflow

For a thesis, keep the exact official downloads used in the analysis. Every
adapter accepts CSV, TSV, JSON, XML/IATI, XLSX, or HTML snapshots when their
columns expose recognizable project labels:

```bash
python main.py --bank idb \
  --source-file idb=data/raw/idb_projects_2026-08-11.csv \
  --include-raw
```

Multiple overrides can be supplied in one run:

```bash
python main.py --banks adb afdb ebrd \
  --source-file adb=data/raw/adb.csv \
  --source-file afdb=data/raw/afdb.csv \
  --source-file ebrd=data/raw/ebrd.csv \
  --combine
```

If a download URL or local file has no meaningful extension, declare its format:

```bash
python main.py --bank cabei --source-file cabei=data/raw/bcie_export \
  --source-format cabei=xml
```

This mode is also how the test suite validates dynamic-site adapters without
depending on a live website.

## Supported institutions and official sources

The registry lives in [`mdbs_scraper/banks.json`](mdbs_scraper/banks.json) and
was reviewed against official pages on **11 August 2026** (`reviewed_on` in
that file). The table below lists every defined institution; `imf` is present
but disabled by default (see above).

| ID | Institution | Default official source | Collection path | Principal caveat |
|---|---|---|---|---|
| `adb` | Asian Development Bank | Official sovereign-projects file + Statement of Loans (see below) with the [IATI Registry (publisher `asdb`)](https://iatiregistry.org/publisher/asdb) | Official files define sovereign projects; IATI adds non-sovereign projects and extra fields | One row per ADB project number; data.adb.org sits behind bot-protection, so the official files are downloaded by hand |
| `aiib` | Asian Infrastructure Investment Bank | [Project list](https://www.aiib.org/en/projects/list/index.html) | Rendered table/JSON | Detail fields vary |
| `afdb` | African Development Bank | Official MapAfrica project list (see below) with the [IATI Registry (publisher `afdb`)](https://iatiregistry.org/publisher/afdb) | Official file defines the projects; IATI fills extra fields | MapAfrica sits behind bot-protection, so its export is downloaded by hand |
| `badea` | Arab Bank for Economic Development in Africa | [Interactive map](https://www.badea.org/interactive-map/) | Tableau Public vizql session, decoded directly | Not on the IATI Registry; totals are bucketed by (country, sector classification, approval year), not individual projects, at one-decimal-place (millions USD) precision |
| `cabei` | Central American Bank for Economic Integration | [CABEI/BCIE CKAN package](https://datosabiertos.bcie.org/api/3/action/package_show?id=iati) + the [`prestamos`](https://datosabiertos.bcie.org/dataset/prestamos) loan approvals | Project list CSV, plus public-sector loan approvals it lacks | Private-sector approvals exist only as country-year totals (`cabei_private_approvals_country_year.csv`); `cabi` remains a CLI alias |
| `caf` | Development Bank of Latin America and the Caribbean | [IATI Registry (publisher `caf`)](https://iatiregistry.org/publisher/caf) + five archived CAF IATI files (2024-08 to 2025-10) | CKAN resource → IATI XML, then archived projects CAF has since removed | CAF's 2026-09 file dropped most completed projects; nothing before 2006 was ever published |
| `cdb` | Caribbean Development Bank | [Project list](https://www.caribank.org/our-work/projects-map/list-projects?order=title&sort=asc) | List + details | A selection of projects under implementation, 2013-2023 only |
| `ebrd` | European Bank for Reconstruction and Development | [Project portal](https://www.ebrd.com/home/what-we-do/projects.html) | Browser-generated project CSV | Requires Playwright; not the aggregate overview workbook |
| `ibrd` | International Bank for Reconstruction and Development | [World Bank Projects API](https://search.worldbank.org/api/v3/projects) + Finances One (DS00047) | JSON API | Projects with an IBRD commitment or IBRD loan; v2 froze on 2024-12-20 |
| `ida` | International Development Association | [World Bank Projects API](https://search.worldbank.org/api/v3/projects) + Finances One (DS00001) | JSON API | Projects with an IDA commitment or IDA credit/grant; v2 froze on 2024-12-20 |
| `idb` | Inter-American Development Bank | [English project CSV](https://data.iadb.org/files/download/791d475c-0f61-411c-ae92-2242c913e73a) | CSV | Includes many operation types, not loans only |
| `imf` *(disabled by default)* | International Monetary Fund | [IMF Lending Commitments](https://www.imf.org/external/np/fin/tad/extarr1.aspx) | Member arrangement tables/TSV | Arrangements are country programs, not MDB projects; normally SDR/XDR |
| `isdb` | Islamic Development Bank | Official IsDB Group approvals list (see below) with the [IATI Registry (publisher `isdb`)](https://iatiregistry.org/publisher/isdb) | Official list defines IsDB's own operations (matched to IATI by name); ITFC trade finance comes from IATI | The approvals list has no project ids; ITFC rows are `instrument_category = trade_finance` |
| `ndb` | New Development Bank | [All projects](https://www.ndb.int/projects/all-projects/) | Paginated list + details | Disclosure varies by project |
| `eib` | European Investment Bank | [Financed projects](https://www.eib.org/en/projects/loans/index) | Rendered table/JSON | Signed financing contracts |

## Official portfolio files: backbone and backstop

Several banks publish a complete portfolio file that the scraper cannot fetch
itself (bot protection, export buttons), or that is more complete than the
channel it can reach. Downloaded copies live in
[`online pulled data/`](online%20pulled%20data/), and each is registered in
[`mdbs_scraper/official_sources.json`](mdbs_scraper/official_sources.json) with
its publisher URL, `as_of` date, retrieval date, sha256 and role:

| Role | Files | What happens |
|---|---|---|
| `backbone` | ADB sovereign projects (2026-01-27), AfDB project list (2026-09-16), IsDB Group approvals (2026-06-30) | The file decides which operations exist and keeps its own identity fields. Scraped records only fill blank fields. A scraped record without an official match is kept only if it was approved after `as_of`, or if the file cannot contain it (ADB non-sovereign, IsDB ITFC); otherwise it is left out and listed in `reports/{bank}_reconciliation.csv`. |
| `supplement` | ADB Statement of Loans (2017-12-31) | Adds loans the backbone lacks (mainly 2002-2004) and fills fund, interest and repayment terms. |
| `extra-fields` | World Bank projects export (2026-09-15) | Fills blank fields (locations, lending instrument) by project id. |
| `backstop` | all of the above except the loan statement, plus CABEI, IDB, EBRD and the AIIB list print | Checked, not merged (see below). |

After every run, `reports/backstop_summary.csv` compares the output with each
file over the years it fully covers:

- **counts per approval year:** at least 98% of the official count for backbone files, 95% for the others;
- **field visibility:** each field the file fills must be filled in our output to within 5 points;
- **amount totals per year:** at least 98% of the official total, where amounts are comparable.

Failures are warnings unless `--strict-backstop` is given, in which case the
exit code is 3. `--no-official` skips both the merge and the checks, and a
bank run with `--source-file` skips its merge.

To refresh a file:

1. Download the new version into `online pulled data/`.
2. Update its `as_of`, `retrieved_on` and `sha256` in the registry (a
   `sha256` mismatch is reported until you do).
3. Rerun.

The AIIB list is a PDF print, so it is read through a committed text extract;
`make aiib-extract` regenerates it (needs `pip install '.[pdf]'`).

## IATI Registry sources: adb, afdb, caf, isdb

`adb`, `afdb`, `caf`, and `isdb`'s official websites/downloads sit behind
bot-protection (Cloudflare or Incapsula) that also blocks headless-browser
automation — confirmed even from a genuine residential IP, ruling out an
IP-reputation-based fix. All four institutions separately publish their full
project portfolios as official IATI Standard activity files through the
[IATI Registry](https://iatiregistry.org), hosted without any bot-protection.
Their adapters (`adapters/{adb,afdb,caf,isdb}.py`) fetch and combine those
files via `CkanOrganizationScraper` (`adapters/common.py`) instead of
scraping the protected sources. See `docs/PROBLEMS.md`'s "Recently resolved"
section for the full detail, including the two banks (`adb`, `isdb`) whose
`sector` field doesn't populate because their IATI data uses a bare numeric
DAC code with no human-readable name.

## Per-bank quality report

Every run writes `{output_dir}/reports/{bank_id}_quality.md` for each
requested bank — including failed ones. It aggregates what the scraper
already tracks: the same field-coverage counts as
`project_counts_and_coverage.csv`, plus a frequency count of every distinct
`data_quality_notes` string across that bank's records (e.g. how many rows
got a synthetic ID, or were missing a subnational location). Failed banks get
a report containing just the error. Use this to see at a glance which field
is actually missing/thin for a bank, without opening the raw CSV.

## Output schema

All bank files use the same columns. The most important groups are:

| Group | Columns | Interpretation |
|---|---|---|
| Identity | `bank_id`, `project_id`, `project_name` | Bank-native ID where disclosed; otherwise a labelled synthetic hash |
| Geography | `country`, `country_code`, `province`, `location_text`, `latitude`, `longitude` | `country` preserves the source's own terminology; `country_code` is a standardized ISO 3166-1 alpha-3 code derived from it the same way across all 15 banks, for cross-bank aggregation -- blank for multi-country/regional/institutional entries (e.g. "Regional", "Africa, regional"), never guessed. Subnational fields stay blank unless disclosed |
| Timing | `approval_date`, `commitment_date`, `commitment_year`, `approval_year`, `completion_date`, `duration_years` | `approval_year` is the one year definition used for the 2002-2026 scope and the country-year files. It falls back to the commitment/signing year, with a note, only where no approval date is disclosed (EBRD, IATI-only rows). Duration is derived only when both boundary dates are valid |
| Classification | `status`, `pipeline_stage`, `sector`, `subsector`, `sector_category`, `sector_category_code`, `loan_type`, `financing_instrument`, `instrument_category` | `sector`/`loan_type` preserve the source's own terminology (except explicit sovereign normalization); `sector_category`/`sector_category_code` are a standardized OECD DAC 3-digit bucket (e.g. `"Transport & Storage"` / `"210"`) derived the same way across all 15 banks, for cross-bank aggregation -- blank when no confident mapping exists, never guessed. `instrument_category` is one of `loan`, `grant`, `technical_assistance`, `guarantee`, `equity`, `trade_finance`, `other`, derived from each bank's own instrument wording (blank when the source does not say). `pipeline_stage` is a numeric 1/0 companion to `status`: 1 when the bank's own status means the project has not yet been approved (`Proposed`, `Pipeline`, `Preparation`, `Under appraisal`, or a translated equivalent) -- explaining why `approval_date`/`approval_year` are genuinely blank rather than a data gap -- 0 for an ordinary post-approval status, blank only when `status` itself is undisclosed. Drop `pipeline_stage == 1` rows before any approval-date/commitment-amount analysis. |
| Financing terms | `funding_window`, `concessional`, `concessional_flag` | Which of a bank group's windows financed the project, under the window's own name (`"African Development Fund"` vs `"African Development Bank"`, `"IDA"` vs `"IBRD"`), and whether that means concessional terms — `Yes`/`No`/`Blended`, with `concessional_flag` as a numeric 1/0 companion left blank for `Blended`. Blank where the publisher does not disclose it, with the reason in `data_quality_notes`; ADB, for instance, reports concessionality but never names the window |
| Project cost | `total_project_cost`, `project_cost_currency`, `total_project_cost_usd` | Total operation/project cost, kept separate from bank financing |
| Bank loan | `loan_amount`, `loan_currency`, `loan_amount_usd` | Loan/financing amount disclosed by the institution |
| Commitment | `total_commitment`, `commitment_currency`, `total_commitment_usd` | Bank commitment; not combined cross-bank project finance |
| Disbursement | `total_disbursement`, `disbursement_currency`, `total_disbursement_usd`, `first_disbursement_date`, `last_disbursement_date` | Total blank if not published in the chosen source; the two dates capture *when* disbursement activity occurred, where disclosed — not a full multi-tranche schedule |
| Collaboration | `cofinancing_partners`, `cofinancing_amount`, `cofinancing_currency`, `cofinancing_amount_usd` | Co-financing/collaborating institutions named by the source; blank means not disclosed, not "none" |
| Conditions | `conditionality`, `conditionality_source_url` | Only explicit source text; no NLP inference in the default run |
| Provenance | `project_url`, `source_url`, `source_format`, `official_source_id`, `source_record_ids`, `source_updated_at`, `scraped_at`, `data_quality_notes` | `official_source_id` names the official file that defines the row; `source_record_ids` lists the scraped/official records merged into it |

Add `--include-raw` to append `source_fields_json`, which preserves the matched
source row or label/value fields for later remapping.

## Output files

A run creates:

- `{bank_id}_projects.csv` (or JSON/XLSX) for every successful bank;
- `all_mdb_projects.csv` when `--combine` is used;
- `project_counts_and_coverage.csv`, including project count and non-missing
  counts for the requested thesis variables;
- `manifest.json`, including run times, filters, source overrides, failures,
  record totals, output paths, and SHA-256 checksums.
- `reports/{bank_id}_quality.md`, one per requested bank (see "Per-bank
  quality report" above);
- `reports/coverage_by_year.csv`: bank × approval year counts, flagged
  `low` (under half the bank's median), `not_covered` (outside the bank's
  `coverage_start_year`/`coverage_end_year`) or `stale` (newest approval more
  than nine months old);
- `reports/backstop_summary.csv`, `reports/backstop_{bank}.md` and
  `reports/{bank}_reconciliation.csv` (see "Official portfolio files");
- `cabei_private_approvals_country_year.csv`: CABEI's private-sector approvals,
  which it discloses only as country-year counts and totals;
- with `--dyad`: `all_mdb_projects_usd.csv`, `dyad_country_year.csv`,
  `dyad_country_year_balanced.csv`, `dyad_unallocated_year.csv` and
  `dyad_manifest.json` (see below).

`manifest.json` also records the sha256 of every input: every fetched
response, every `--source-file`, and the official files. It also records the
git commit. `--save-raw DIR` additionally archives every fetched response
body with a JSON sidecar.

CSV is UTF-8 with a byte-order mark for smooth import into Excel, Stata, and R.
Financial numbers are emitted as plain decimal strings rather than scientific
notation.

## Constant 2025 USD and country-year totals

`python main.py --dyad` (or `make dyad`) reads `all_mdb_projects.csv` and
writes four files.

- **`all_mdb_projects_usd.csv`**: every project row plus `usd_nominal`,
  `usd_constant_2025` and how they were obtained. Original amounts and
  currencies are never overwritten.
  - `usd_conversion` is `source-usd` when the bank reports USD (used as is).
  - It is `imf-annual-average` otherwise: the amount is divided by the
    [IMF Exchange Rates](https://data.imf.org/en/datasets/IMF.STA:ER) annual
    period-average rate (domestic currency per USD, `XDC_USD.PA_RT.A`) of the
    approval year. EUR uses the Euro Area series. SDR uses the US series of
    USD per SDR, inverted. AfDB units of account (XUA) and IsDB dinars (ISD)
    are valued at one SDR each.
  - Nominal USD is multiplied by the ratio D(2025) / D(approval year) of the US
    GDP implicit price deflator,
    [FRED A191RD3A086NBEA](https://fred.stlouisfed.org/series/A191RD3A086NBEA).
  - A non-sovereign amount from a scraped feed that is labelled USD, is above
    $1bn, and belongs to a country whose currency trades at 10 or more per
    dollar is flagged `excluded-currency-suspect` and left out of totals. ADB's
    IATI files mislabel a few such local-currency amounts as USD; see
    `docs/PROBLEMS.md`.
  - **2026 approvals are not priced**, because neither series has an annual
    2026 value yet. Their constant-USD columns are blank, and their nominal
    USD is filled only when the bank reports USD. `price_basis_note` says so on
    each row.
  - **PPP-adjusted amounts** value each flow at the recipient's purchasing
    power, in international dollars.
    - `ppp_price_level_ratio` is the recipient's price level relative to the
      US in the approval year: World Bank GDP in current US$
      ([NY.GDP.MKTP.CD](https://data.worldbank.org/indicator/NY.GDP.MKTP.CD))
      divided by GDP in current PPP international $
      ([NY.GDP.MKTP.PP.CD](https://data.worldbank.org/indicator/NY.GDP.MKTP.PP.CD),
      based on the ICP GDP PPP conversion factor). That equals
      PPP / market exchange rate.
    - `ppp_intl_nominal` = `usd_nominal` / ratio. `ppp_intl_constant_2025` is
      that times the same deflator ratio as above. So $1m approved for India in
      2025 (ratio 0.23) is about 4.3m constant 2025 international $.
    - The ratio comes from two USD series rather than from `PA.NUS.PPP` /
      `PA.NUS.FCRF`, because those two are not always in the same local
      currency unit in WDI: Venezuela's and Zimbabwe's redenominations give
      ratios near zero.
    - Left blank, with a reason in `price_basis_note`, for: rows with no single
      recipient country (regional, multi-country); 2026; and country-years
      the World Bank does not cover (mostly Venezuela, Djibouti, South Sudan,
      Yemen, Syria, Kosovo before 2008, the Cook Islands and some territories).
  - The reference series are cached with their retrieval metadata in
    `mdbs_scraper/data/reference/` and committed; `--refresh-reference`
    downloads them again. The currency-to-series mapping is
    `data/reference/currencies.json`.
- **`dyad_country_year.csv`**: one row per bank, ISO-3 country and approval
  year with at least one operation.
  - Counts and USD totals, overall and by `instrument_category` and
    concessionality.
  - `n_unconverted` counts rows whose amount could not be converted or was
    excluded as a suspect currency.
  - `ppp_2025_total` sums `ppp_intl_constant_2025`. It covers only rows with a
    PPP value: `n_ppp_unpriced` counts the rows in `usd_2025_total` that it
    leaves out. In `dyad_unallocated_year.csv` it is always 0.
  - `count_unit` is `aggregate-buckets` for BADEA, whose source reports totals
    rather than projects.
  - CABEI's private-sector totals are separate `cabei_private_*` columns
    (including `cabei_private_ppp_2025`), never added to the operation totals.
- **`dyad_country_year_balanced.csv`**: each bank × every country it lent to
  × 2002-2026.
  - Years inside the bank's coverage window with no operation are zeros
    (`observed = 0`, `in_coverage = 1`).
  - Years outside the window (before `coverage_start_year`, or after the
    source's last covered year) are left blank (`in_coverage = 0`), never
    claimed as zero.
  - Balancing against eligibility is left to `mdb_eligibility`.
- **`dyad_unallocated_year.csv`**: bank-year totals of rows with no single
  country (regional, multi-country, unresolved).
  - The run checks that the country-year and unallocated totals add up to the
    project rows for every bank-year.

## OECD CRS (manual download)

The OECD's project-level CRS files are the only official source found that
might add 2002-2007 operations for ADB, IsDB, CDB and CAF, mostly their
concessional windows. They could not be fetched or located by script, so:

1. Find the bulk files for the OECD Data Explorer dataset "CRS: Creditor
   Reporting System (flows)". The OECD's own dataflow metadata
   (`https://sdmx.oecd.org/public/rest/dataflow/OECD.DCD.FSD/DSD_CRS@DF_CRS/latest`)
   names them `CRS 2002-03 data.zip`, `CRS 2004-05 data.zip`,
   `CRS 2006 data.zip` and `CRS 2007 data.zip`.
   - **No download link has been verified.** The addresses that metadata gives
     returned only a Cloudflare challenge page when checked (2026-09-16), so
     where to download them in a browser is still to be confirmed.
2. Save them in `data/external/oecd_crs/`, which is git-ignored.
3. Run `python main.py --crs-profile` (or `make crs-profile`).

The profile (`reports/crs_profile.csv`) shows, per bank and year:

- the CRS rows, commitments and ODA share;
- how many CRS project numbers match ids already in the output.

It changes no output; backfilling from CRS is a separate, per-bank decision.

## Conditionality: what this code can and cannot establish

Project databases rarely include conditionality as a structured field. The
adapters collect fields labelled conditions, covenants, prior actions, policy
actions, or their Spanish equivalents when those are present. They do **not**
infer conditions from a project title, sector, status, or generic loan terms.

For defensible thesis coding, treat the output as an index for a second-stage
document workflow: retrieve the linked program/appraisal/legal documents, define
a coding protocol, keep page-level citations, and distinguish policy conditions
from disbursement conditions and standard legal covenants.

## Tests

Run the complete offline suite:

```bash
python -m unittest discover -s tests -v
```

The suite covers:

- money, date, currency and loan-type normalization;
- the pinned country-name table and instrument categories;
- the World Bank v3 response shape and the CAF archive merge;
- CABEI's loan approvals;
- each official-file reader, the reconciliation rules and the backstop checks;
- the constant-USD conversion and the country-year aggregation;
- the CRS profiler;
- the HTTP client (throttle, `Retry-After`, input hashing);
- the config-driven registry, every concrete adapter using a local official-snapshot shape;
- full CLI runs, including `--combine-only`, child-process isolation and
  `--strict-backstop`.

All of it runs offline. It deliberately does not pretend that a fixture proves a live
website has not changed; live failures are explicit and actionable.

## Source layout changes

If a portal changes, the adapter raises `SourceLayoutChanged`. First inspect the
official page and its download options. If an official export exists, save it and
use `--source-file`; then update the relevant adapter and add a redacted fixture
representing the new headers. Do not replace a failing official source with an
unverified third-party API.

See [VALIDATION.md](docs/VALIDATION.md) for the live-check protocol,
[CHANGES_FROM_UPLOADED_ARCHIVE.md](docs/CHANGES_FROM_UPLOADED_ARCHIVE.md) for the
specific corrections, and [PROBLEMS.md](docs/PROBLEMS.md) for currently open
issues (per-bank, ready to hand to an AI assistant one topic at a time).
