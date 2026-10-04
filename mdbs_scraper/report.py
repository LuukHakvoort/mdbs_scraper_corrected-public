"""Per-bank data-quality report.

Aggregates what the scraper already tracks -- the coverage counts from
``output.coverage_summary`` and the per-row ``data_quality_notes`` written by
``BaseScraper.finalize``/``add_note`` -- into one readable markdown file per
bank, so a broken or thin source is diagnosable at a glance instead of
requiring a manual look at the raw CSV/JSON output.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .output import coverage_summary
from .schema import ProjectRecord

_COVERAGE_FIELDS = (
    ("with_commitment_year", "commitment_year"),
    ("with_loan_amount", "loan_amount"),
    ("with_total_commitment", "total_commitment"),
    ("with_total_disbursement", "total_disbursement"),
    ("with_sector", "sector"),
    ("with_sector_category", "standardized sector_category"),
    ("with_subnational_location", "subnational location"),
    ("with_conditionality", "conditionality"),
    ("with_cofinancing", "cofinancing"),
    ("with_disbursement_timing", "disbursement timing"),
)


def _note_frequency(records: list[ProjectRecord]) -> Counter:
    # A single note (e.g. "...; blank does not mean zero.") can itself contain
    # "; ", the same separator add_note/join_values uses between distinct
    # notes -- so re-splitting the joined string would fragment it. Group by
    # the complete, already-deduplicated combination instead.
    counter: Counter = Counter()
    for record in records:
        if record.data_quality_notes:
            counter[record.data_quality_notes] += 1
    return counter


def _report_lines(bank_id: str, records: list[ProjectRecord], metadata=None) -> list[str]:
    summary = coverage_summary(bank_id, records, metadata)
    total = summary["project_count"]
    lines = [f"# {bank_id} quality report", "", f"- project_count: {total}"]
    if metadata:
        lines.extend([
            f"- rows_seen: {summary.get('rows_seen')}",
            f"- duplicates_removed: {summary.get('duplicates_removed')}",
            f"- source_total: {summary.get('source_total')}",
            f"- completeness: {summary.get('completeness')}",
        ])
    if total:
        for field, label in _COVERAGE_FIELDS:
            count = summary[field]
            pct = round(100 * count / total)
            lines.append(f"- {label}: {count}/{total} ({pct}%)")
            if field == "with_loan_amount" and summary.get("zero_loan_amount"):
                lines.append(f"  - of which {summary['zero_loan_amount']} disclosed as exactly 0")
    notes = _note_frequency(records)
    if notes:
        lines += ["", "## Data quality notes", "", "Rows sharing the exact same combination of notes:"]
        for note, count in notes.most_common():
            lines.append(f"- ({count} rows) {note}")
    elif total:
        lines += ["", "No data quality notes were recorded for this run."]
    return lines


def write_bank_report(bank_id: str, records: list[ProjectRecord], output_dir: Path, metadata=None) -> Path:
    """Write a success report for ``bank_id`` under ``{output_dir}/reports/``."""

    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{bank_id}_quality.md"
    path.write_text("\n".join(_report_lines(bank_id, records, metadata)) + "\n", encoding="utf-8")
    return path


def write_bank_failure_report(bank_id: str, error: str, output_dir: Path) -> Path:
    """Write a failure report for ``bank_id`` under ``{output_dir}/reports/``."""

    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{bank_id}_quality.md"
    lines = [f"# {bank_id} quality report", "", "- project_count: 0", "", "## Failure", "", error]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
