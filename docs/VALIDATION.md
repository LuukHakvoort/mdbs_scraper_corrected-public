# Validation and thesis replication protocol

## What has been validated in this package

1. The registry contains exactly 15 canonical IDs and 15 instantiable classes.
2. All Python files compile.
3. The offline test suite runs each adapter and the combined CLI end to end.
4. World Bank tests use the current dictionary-of-projects API response shape and
   check that IBRD and IDA commitments are not mixed.
5. Parser tests cover comma and dot thousands/decimal conventions, magnitude
   words, Spanish dates and risk labels, coordinates, duration, and missing
   currency behavior.
6. Outputs are checked for 15 bank rows, project counts, combined data, and a
   failure-aware manifest.

## What must be validated on the researcher's machine

Websites and their anti-automation controls change. Before generating the final
thesis dataset, run each bank separately with `--max-projects 5 --include-raw`.
For each result:

1. Compare five rows with the official project page or downloaded source.
2. Confirm the unit of every money column (units versus millions; USD, EUR, SDR,
   UA/XUA, or local currency).
3. Confirm whether “approved amount,” “bank financing,” and “total project cost”
   are distinct in that source.
4. Confirm that approval, commitment/signing, effectiveness, and closing dates
   are not being treated as interchangeable.
5. Confirm the source's historical and operation-type coverage.
6. Record the source download date and keep the raw file unchanged.
7. Inspect `data_quality_notes` and the coverage CSV before analysis.

Only after those checks should you remove `--max-projects` and run the full bank.

## Automated checks against official files (2026-09-16)

Many of the manual checks above now run on every collection. Each run
compares the output with the official portfolio files registered in
`mdbs_scraper/official_sources.json`, and writes
`reports/backstop_summary.csv` with three checks:

- counts per approval year;
- field fill rates;
- amount totals.

`reports/coverage_by_year.csv` flags thin (`low`) and outdated (`stale`)
sources. Use `--strict-backstop` to make a failing check fail the run. The
official files themselves are the "unchanged official downloads" below: they
live in `online pulled data/`, and the registry pins each one with its
publication date and sha256.

## Recommended raw/intermediate/final folders

```text
data/
  raw/          # unchanged official downloads, dated in filenames
                # (the registered official files live in "online pulled data/")
  normalized/   # this scraper's per-bank outputs
  analysis/     # frozen thesis sample and derived variables
```

Store each command and `manifest.json` with the thesis replication package. Hashes
in the manifest let you demonstrate that normalized outputs were not altered.

## Project counts

`project_count` is the number of unique `(bank_id, project_id)` records remaining
after the requested year filter. Records with no disclosed year are retained and
flagged, because silently dropping them would bias the count. Decide and document
whether your thesis sample should exclude those records after inspecting them.

Counts across banks are not automatically comparable. Some sources include
proposed projects, grants, technical cooperation, guarantees, funds, or multiple
financing records per broader program. Apply a documented operation-type rule
before cross-bank comparisons.

## Conditionality validation

Do not code an empty `conditionality` cell as zero. Suggested second-stage fields
include:

- document URL and publication date;
- condition text and page number;
- condition family (prior action, performance criterion, structural benchmark,
  disbursement condition, covenant, safeguard requirement);
- binding/non-binding status;
- number of conditions and coder confidence;
- second-coder agreement or adjudication result.

For IMF records, use program staff reports and letters/memoranda associated with
the arrangement. For policy-based MDB operations, use program/policy documents
and financing agreements. Investment-project legal covenants should not be mixed
with macro-policy conditionality without an explicit conceptual rule.

## Live run checklist

```bash
python main.py --bank BANK --max-projects 5 --include-raw --verbose
python main.py --bank BANK --min-year 2002 --max-year 2026 --include-raw
```

If a dynamic portal fails, rerun once with `--no-headless`. If the official portal
offers a download, prefer saving that file and using `--source-file`; it is more
replicable than a browser session.
