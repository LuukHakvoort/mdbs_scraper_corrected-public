# Corrections relative to the uploaded archive

The uploaded code was retained separately and was not overwritten. This project
addresses the following defects found during review.

## Scope and registry

- The README claimed 15 institutions, but the configuration contained 14.
- EIB is added as the provisional fifteenth institution.
- `cabi` is corrected to `cabei`, with the old spelling retained as a CLI alias.
- Placeholder/fabricated API hosts for ADB, AfDB, EBRD, IDB, and IMF are removed.
- Every canonical ID maps to a concrete class; there is no abstract base fallback.

## Runtime defects

- Imports and package exports are internally consistent.
- Constructor signatures are unified through `ScrapeOptions`.
- The World Bank adapter uses `search.worldbank.org/api/v2/projects`, its current
  `projects` dictionary response, and `rows`/`os` pagination.
- IBRD and IDA are filtered by their institution-specific commitment fields.
- Playwright now stays alive for the full browser/context/page lifecycle.
- JavaScript adapters can extract rendered tables and captured JSON responses.
- EBRD uses the project portal's generated project download rather than presenting
  an aggregate overview workbook as project-level data.

## Data integrity

- Monetary parsing supports explicit magnitude words and US/European separators.
- Three-decimal coordinates are no longer parsed as thousands.
- Non-sovereign values are checked before the substring “sovereign”; Spanish
  `no soberano` is handled.
- Ambiguous slash dates are not guessed without a bank-specific day order.
- Project cost, bank loan, commitment, and disbursement have separate fields.
- USD values are not inferred through undocumented currency conversion.
- Missing values receive source-specific notes and remain blank, not zero.
- Stable synthetic IDs are labelled when a publisher provides no project ID.

## Thesis variables and reproducibility

- Added commitment year, duration, sector/subsector, subnational place and
  coordinates, commitments, disbursements, and conditionality/provenance fields.
- Added raw source-field preservation with `--include-raw`.
- Added per-bank field-coverage counts, combined output, checksums, and a manifest.
- Added source-file and source-format overrides for frozen official snapshots.
- Added explicit non-zero exit behavior for partial failures.
- Added a full standard-library `unittest` suite and fixtures for all 15 adapters.
