"""Check the output against every registered official portfolio file.

Three checks per file, over the approval years the file fully covers:

- counts: our operations per year must reach 98% (backbone files) or 95%
  (backstop files) of the official count;
- field visibility: for each field the file discloses, our fill rate must be
  at most five points below the file's;
- amounts (where comparable): our yearly total per currency must reach 98%
  of the official total.

A failure is reported, not raised, unless --strict-backstop is set.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .base import ScrapeOptions
from .cleaning import extract_year
from .official import OFFICIAL_REGISTRY, OfficialRegistry, OfficialSource, default_directory, file_status, read_official
from .output import write_dict_rows
from .schema import ProjectRecord

LOG = logging.getLogger(__name__)

SUMMARY_FIELDS = [
    "bank_id", "source_id", "check", "key", "official", "ours", "ratio", "threshold", "status",
]
BACKBONE_COUNT_SHARE = Decimal("0.98")
BACKSTOP_COUNT_SHARE = Decimal("0.95")
VISIBILITY_TOLERANCE = Decimal("0.05")
AMOUNT_SHARE = Decimal("0.98")

_official_in_scope_cache: dict[tuple[str, str, int, int], list[dict[str, Any]]] = {}


def _row_year(row: dict[str, Any]) -> int | None:
    for key in ("approval_year", "commitment_year"):
        value = str(row.get(key) or "")
        if value.isdigit():
            return int(value)
    return extract_year(row.get("approval_date"), row.get("commitment_date"))


def _filled(value: Any) -> bool:
    return value not in (None, "")


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if _filled(value) else None
    except InvalidOperation:
        return None


def covered_years(source: OfficialSource, min_year: int, max_year: int) -> range:
    """Years the file fully covers: up to its as_of year, minus that year unless it is 31 December."""

    as_of = date.fromisoformat(source.as_of)
    last = as_of.year if (as_of.month, as_of.day) == (12, 31) else as_of.year - 1
    return range(min_year, min(max_year, last) + 1)


def _official_rows(
    source: OfficialSource, bank_id: str, directory: Path, min_year: int, max_year: int
) -> list[dict[str, Any]]:
    key = (source.id, bank_id, min_year, max_year)
    if key not in _official_in_scope_cache:
        from .registry import create_scraper  # registry imports the adapters

        finalizer = create_scraper(bank_id, ScrapeOptions(min_year=min_year, max_year=max_year))
        records = [replace(record) for record in read_official(source, directory).get(bank_id, [])]
        _official_in_scope_cache[key] = [record.to_row() for record in finalizer.finalize(records)]
    return _official_in_scope_cache[key]


def backstop_bank(
    bank_id: str,
    rows: list[dict[str, Any]],
    min_year: int,
    max_year: int,
    directory: Path | None = None,
    registry: OfficialRegistry = OFFICIAL_REGISTRY,
) -> list[dict[str, Any]]:
    """All check rows for one bank; ``rows`` are this package's output rows (to_row() or CSV)."""

    directory = directory or default_directory(registry)
    results: list[dict[str, Any]] = []
    for source in registry.for_bank(bank_id):
        status = file_status(source, directory)
        base = {"bank_id": bank_id, "source_id": source.id}
        if not status["present"]:
            results.append({**base, "check": "file", "key": "present", "status": "skipped"})
            continue
        results.append({
            **base, "check": "file", "key": "sha256",
            "official": source.sha256[:12], "ours": status["sha256"][:12],
            "status": "pass" if status["matches_registry"] else "warn",
        })
        if "backstop" not in source.roles:
            continue
        official = _official_rows(source, bank_id, directory, min_year, max_year)
        years = covered_years(source, min_year, max_year)
        results.extend(_count_checks(base, source, official, rows, years))
        results.extend(_visibility_checks(base, source, official, rows, years))
        if source.amount_check:
            results.extend(_amount_checks(base, official, rows, years))
    return results


def _count_checks(base, source, official, rows, years) -> list[dict[str, Any]]:
    threshold = BACKBONE_COUNT_SHARE if "backbone" in source.roles else BACKSTOP_COUNT_SHARE
    official_counts: dict[int, int] = defaultdict(int)
    our_counts: dict[int, int] = defaultdict(int)
    for row in official:
        official_counts[_row_year(row) or 0] += 1
    for row in rows:
        our_counts[_row_year(row) or 0] += 1
    checks = []
    for year in years:
        expected, found = official_counts.get(year, 0), our_counts.get(year, 0)
        ratio = Decimal(found) / Decimal(expected) if expected else None
        checks.append({
            **base, "check": "count", "key": year, "official": expected, "ours": found,
            "ratio": None if ratio is None else round(ratio, 3), "threshold": threshold,
            "status": "pass" if ratio is None or ratio >= threshold else "fail",
        })
    return checks


def _in_years(rows: list[dict[str, Any]], years: range) -> list[dict[str, Any]]:
    return [row for row in rows if (_row_year(row) or 0) in years]


def _visibility_checks(base, source, official, rows, years) -> list[dict[str, Any]]:
    official_scope, our_scope = _in_years(official, years), _in_years(rows, years)
    # Compare the same operations where the file has usable ids: rows the file
    # does not cover (ADB non-sovereign, IsDB ITFC, CABEI loans added from its
    # separate approvals list) cannot be held to its field coverage. Files
    # whose ids are not ours (AIIB's list print) fall back to all rows.
    official_ids = {str(row.get("project_id") or "").casefold() for row in official_scope}
    matched = [row for row in our_scope if str(row.get("project_id") or "").casefold() in official_ids]
    if official_scope and len(matched) >= len(official_scope) / 2:
        our_scope = matched
    checks = []
    for name in source.visibility_fields:
        if not official_scope or not our_scope:
            continue
        expected = Decimal(sum(_filled(row.get(name)) for row in official_scope)) / len(official_scope)
        found = Decimal(sum(_filled(row.get(name)) for row in our_scope)) / len(our_scope)
        checks.append({
            **base, "check": "field_visibility", "key": name,
            "official": round(expected, 3), "ours": round(found, 3),
            "threshold": VISIBILITY_TOLERANCE,
            "status": "pass" if found >= expected - VISIBILITY_TOLERANCE else "fail",
        })
    return checks


def _amount_checks(base, official, rows, years) -> list[dict[str, Any]]:
    def totals(source_rows):
        result: dict[tuple[int, str], Decimal] = defaultdict(Decimal)
        for row in source_rows:
            amount = _decimal(row.get("loan_amount"))
            if amount is not None and row.get("loan_currency"):
                result[(_row_year(row) or 0, row["loan_currency"])] += amount
        return result

    expected, found = totals(official), totals(rows)
    checks = []
    for (year, currency), total in sorted(expected.items()):
        if year not in years or not total:
            continue
        ours = found.get((year, currency), Decimal(0))
        ratio = ours / total
        checks.append({
            **base, "check": "amount", "key": f"{year} {currency}",
            "official": round(total), "ours": round(ours), "ratio": round(ratio, 3),
            "threshold": AMOUNT_SHARE, "status": "pass" if ratio >= AMOUNT_SHARE else "fail",
        })
    return checks


def run_backstop(
    rows_by_bank: dict[str, list[dict[str, Any]]],
    output_dir: Path,
    min_year: int,
    max_year: int,
    directory: Path | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    for bank_id, rows in rows_by_bank.items():
        try:
            results.extend(backstop_bank(bank_id, rows, min_year, max_year, directory))
        except Exception as exc:  # noqa: BLE001 -- a check that cannot run is reported, not fatal
            LOG.warning("Backstop for %s could not run: %s", bank_id, exc)
            results.append({"bank_id": bank_id, "check": "error", "key": type(exc).__name__,
                            "status": "error", "ours": str(exc)[:200]})
    path = write_dict_rows(output_dir / "reports" / "backstop_summary.csv", SUMMARY_FIELDS, results)
    for bank_id in rows_by_bank:
        _write_bank_markdown(output_dir, bank_id, [row for row in results if row["bank_id"] == bank_id])
    return path, results


def _write_bank_markdown(output_dir: Path, bank_id: str, results: list[dict[str, Any]]) -> None:
    if not results:
        return
    lines = [f"# {bank_id} backstop against official files", ""]
    failures = [row for row in results if row.get("status") in {"fail", "error"}]
    lines.append(f"- checks: {len(results)}; failing: {len(failures)}")
    for row in failures:
        lines.append(
            f"  - {row.get('source_id', '')} {row['check']} {row.get('key', '')}: "
            f"official {row.get('official', '')}, ours {row.get('ours', '')} (ratio {row.get('ratio', '')})"
        )
    lines += ["", "| source | check | key | official | ours | ratio | status |", "|---|---|---|---|---|---|---|"]
    for row in results:
        lines.append(
            f"| {row.get('source_id', '')} | {row['check']} | {row.get('key', '')} | {row.get('official', '')} "
            f"| {row.get('ours', '')} | {row.get('ratio', '')} | {row.get('status', '')} |"
        )
    reports = output_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"backstop_{bank_id}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in results:
        by_source[row.get("source_id") or row["bank_id"]][row.get("status") or "info"] += 1
    return {source: dict(counts) for source, counts in by_source.items()}


def failing(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in results if row.get("status") in {"fail", "error"}]


def rows_for(records: list[ProjectRecord]) -> list[dict[str, Any]]:
    return [record.to_row() for record in records]
