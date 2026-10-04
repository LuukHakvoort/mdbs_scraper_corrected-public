"""Bank x approval-year coverage, built from the per-bank CSVs on disk.

Written on every run (and on --combine-only) so a bank whose early or recent
years quietly thinned out -- CAF republishing its IATI file without its
completed projects, the World Bank's frozen v2 API -- shows up as a flag
instead of being noticed weeks later as a smaller combined file.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median

from .config import BANKS, BankDefinition
from .output import read_csv_rows, write_dict_rows

COVERAGE_FIELDS = [
    "bank_id", "year", "covered", "operations", "not_active", "with_loan_amount",
    "loan_amount_usd_total", "flag", "bank_median", "latest_approval_date",
]
LOW_SHARE_OF_MEDIAN = Decimal("0.5")
STALE_AFTER_DAYS = 274  # ~9 months

# Status words meaning an operation is still running (or not yet running);
# everything else (closed, completed, cancelled, repaid...) counts as not
# active, which is how an active-only source such as CDB's map shows up.
_ACTIVE_STATUS_WORDS = (
    "active", "implementation", "ejecucion", "ejecución", "ongoing", "approved", "signed",
    "disbursing", "effective", "proposed", "pipeline", "preparation", "committed", "aprobacion",
    "aprobación",
)


def covered_years(bank: BankDefinition, min_year: int, max_year: int, latest_year: int | None) -> range:
    start = max(min_year, bank.coverage_start_year or min_year)
    end = min(max_year, bank.coverage_end_year or latest_year or max_year)
    return range(start, end + 1)


def _is_active(status: str) -> bool:
    folded = status.casefold()
    return any(word in folded for word in _ACTIVE_STATUS_WORDS)


def coverage_by_year(
    output_dir: Path, bank_ids: list[str], min_year: int, max_year: int, *, today: date | None = None
) -> list[dict[str, object]]:
    today = today or date.today()
    rows: list[dict[str, object]] = []
    for bank_id in bank_ids:
        path = output_dir / f"{bank_id}_projects.csv"
        if not path.is_file():
            continue
        _, records = read_csv_rows(path)
        counts: dict[int, dict[str, object]] = defaultdict(
            lambda: {"operations": 0, "not_active": 0, "with_loan_amount": 0, "usd": Decimal(0)}
        )
        latest = ""
        latest_year = 0
        for record in records:
            try:
                year = int(record.get("approval_year") or record.get("commitment_year") or 0)
            except ValueError:
                continue
            latest = max(latest, record.get("approval_date") or record.get("commitment_date") or "")
            latest_year = max(latest_year, year)
            if not min_year <= year <= max_year:
                continue
            bucket = counts[year]
            bucket["operations"] = int(bucket["operations"]) + 1
            if not _is_active(record.get("status", "")):
                bucket["not_active"] = int(bucket["not_active"]) + 1
            if record.get("loan_amount"):
                bucket["with_loan_amount"] = int(bucket["with_loan_amount"]) + 1
            try:
                bucket["usd"] = Decimal(bucket["usd"]) + Decimal(record.get("loan_amount_usd") or 0)
            except InvalidOperation:
                pass
        bank = BANKS[bank_id]
        window = covered_years(bank, min_year, max_year, latest_year or None)
        in_window = [int(counts[year]["operations"]) for year in window if year < today.year]
        bank_median = Decimal(str(median(in_window))) if in_window else Decimal(0)
        # Some sources give only an approval year (IsDB's official list), so a
        # current-year approval also counts as fresh.
        stale = (
            bool(latest) and (today - date.fromisoformat(latest[:10])).days > STALE_AFTER_DAYS
            and latest_year < today.year
        )
        for year in range(min_year, max_year + 1):
            bucket = counts.get(year, {"operations": 0, "not_active": 0, "with_loan_amount": 0, "usd": 0})
            covered = year in window
            flag = ""
            if not covered:
                flag = "not_covered"
            elif year < today.year and Decimal(int(bucket["operations"])) < bank_median * LOW_SHARE_OF_MEDIAN:
                flag = "low"
            if stale and year == max_year:
                flag = "; ".join(filter(None, [flag, "stale"]))
            rows.append({
                "bank_id": bank_id, "year": year, "covered": int(covered),
                "operations": bucket["operations"], "not_active": bucket["not_active"],
                "with_loan_amount": bucket["with_loan_amount"],
                "loan_amount_usd_total": bucket["usd"], "flag": flag,
                "bank_median": bank_median, "latest_approval_date": latest,
            })
    return rows


def write_coverage_by_year(
    output_dir: Path, bank_ids: list[str], min_year: int, max_year: int
) -> tuple[Path, list[dict[str, object]]]:
    rows = coverage_by_year(output_dir, bank_ids, min_year, max_year)
    path = write_dict_rows(output_dir / "reports" / "coverage_by_year.csv", COVERAGE_FIELDS, rows)
    return path, rows


def flag_summary(rows: list[dict[str, object]]) -> str:
    flagged: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for flag in str(row["flag"]).split("; "):
            if flag in {"low", "stale"}:
                flagged[f"{row['bank_id']} {flag}"].append(str(row["year"]))
    if not flagged:
        return "coverage_by_year: no low or stale years"
    parts = []
    for key, years in sorted(flagged.items()):
        parts.append(key if key.endswith("stale") else f"{key} {','.join(years)}")
    return "coverage_by_year flags: " + "; ".join(parts)
