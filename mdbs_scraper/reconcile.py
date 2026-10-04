"""Merge official portfolio files into scraped records.

Three merge roles (see official_sources.json):

- backbone: the official file decides which operations exist. Each official
  record keeps its own identity fields (id, name, country, approval date,
  status, amount) and takes any field it leaves blank from the scraped
  record(s) that match it. A scraped record with no official match is kept
  only when the file cannot contain it -- approved after the file's as_of
  date, or outside its scope (ADB non-sovereign, IsDB's ITFC) -- and is
  otherwise dropped and listed in reports/<bank>_reconciliation.csv.
- supplement: adds operations the backbone lacks and fills loan terms.
- extra-fields: fills blank fields by id; never adds or drops rows.
"""

from __future__ import annotations

import difflib
import logging
import re
from collections import defaultdict
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .base import ScrapeOptions
from .cleaning import extract_year, join_values, normalize_text
from .official import (
    OFFICIAL_REGISTRY,
    OfficialRegistry,
    OfficialSource,
    adb_project_number,
    afdb_identifier,
    default_directory,
    file_status,
    read_official,
)
from .schema import STANDARD_FIELDS, ProjectRecord

LOG = logging.getLogger(__name__)

# Fields the official record always keeps, even when a scraped record differs.
_OFFICIAL_WINS = {
    "bank_id", "bank_name", "bank_abbreviation", "project_id", "project_name", "country",
    "country_code", "approval_date", "approval_year", "commitment_year", "status", "loan_amount",
    "loan_currency", "loan_amount_usd", "total_commitment", "commitment_currency",
    "total_commitment_usd", "official_source_id", "source_url", "source_format",
    "source_updated_at", "scraped_at", "data_quality_notes", "source_record_ids",
}
# Extra-fields sources may add their wording in front of these even when set.
_PREPEND_FIELDS = {"financing_instrument"}
_AMOUNT_CONFLICT = Decimal("0.01")
_ISDB_NAME_RATIO = 0.8
_ISDB_NAME_RATIO_WITH_AMOUNT = 0.5
_ISDB_AMOUNT_TOLERANCE = Decimal("0.02")

EXCLUDED_FIELDS = [
    "bank_id", "project_id", "project_name", "country", "approval_date", "approval_year",
    "loan_amount", "loan_currency", "status", "reason", "official_source_id",
]


def _blank(value: Any) -> bool:
    return value is None or value == ""


def _year(record: ProjectRecord) -> int | None:
    return record.approval_year or extract_year(record.approval_date, record.commitment_date)


def _name_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize_text(text).casefold()).strip()


def apply_official_sources(
    bank_id: str,
    records: list[ProjectRecord],
    options: ScrapeOptions,
    directory: Path | None = None,
    registry: OfficialRegistry = OFFICIAL_REGISTRY,
) -> tuple[list[ProjectRecord], dict[str, Any]]:
    """Merge every registered backbone/supplement/extra-fields file for ``bank_id``."""

    from .registry import create_scraper  # registry imports the adapters

    directory = directory or default_directory(registry)
    report: dict[str, Any] = {"sources": {}, "excluded": []}
    finalizer = create_scraper(bank_id, ScrapeOptions(min_year=options.min_year, max_year=options.max_year))
    for role, merge in (("backbone", _merge_backbone), ("supplement", _merge_supplement),
                        ("extra-fields", _fill_extra_fields)):
        for source in registry.for_bank(bank_id, role):
            status = file_status(source, directory)
            entry = report["sources"].setdefault(source.id, {"roles": list(source.roles), **status})
            if not status["present"]:
                LOG.warning("%s: official file %s is missing; %s step skipped", bank_id, status["path"], role)
                continue
            if not status["matches_registry"]:
                LOG.warning(
                    "%s: %s differs from the registered copy (sha256 %s); update as_of and sha256 in "
                    "official_sources.json after checking it", bank_id, source.file, status["sha256"][:12],
                )
            official = [replace(record) for record in read_official(source, directory).get(bank_id, [])]
            records, info, excluded = merge(bank_id, source, official, records, finalizer)
            entry[role] = info
            report["excluded"].extend(excluded)
    return records, report


# --- backbone ---------------------------------------------------------------


def _live_key_function(bank_id: str) -> Callable[[ProjectRecord], str] | None:
    if bank_id == "adb":
        return lambda record: adb_project_number(record.project_id)
    if bank_id == "afdb":
        return lambda record: afdb_identifier(record.project_id)
    return None


def _official_key_function(bank_id: str) -> Callable[[ProjectRecord], str]:
    if bank_id == "afdb":
        return lambda record: afdb_identifier(record.project_id)
    return lambda record: normalize_text(record.project_id).casefold()


def _merge_backbone(
    bank_id: str, source: OfficialSource, official: list[ProjectRecord],
    live: list[ProjectRecord], finalizer,
) -> tuple[list[ProjectRecord], dict[str, Any], list[dict[str, Any]]]:
    live_key = _live_key_function(bank_id)
    if live_key is None:
        matches, unmatched = _fuzzy_matches(official, live)
    else:
        official_key = _official_key_function(bank_id)
        by_key = {official_key(record): record for record in official}
        matches: dict[int, list[ProjectRecord]] = defaultdict(list)
        unmatched = []
        for record in live:
            target = by_key.get(live_key(record).casefold())
            if target is None:
                unmatched.append(record)
            else:
                matches[id(target)].append(record)
    merged = [_merge_into(record, matches.get(id(record), []), source) for record in official]
    as_of = date.fromisoformat(source.as_of)
    kept_recent, kept_scope, excluded = [], [], []
    for record in unmatched:
        if _approved_after(record, as_of):
            kept_recent.append(record)
        elif _outside_scope(record, source):
            kept_scope.append(record)
        else:
            excluded.append(record)
    if bank_id == "adb":
        kept_recent = _group_by_project(kept_recent)
        kept_scope = _group_by_project(kept_scope)
    finalized = finalizer.finalize(merged)
    info = {
        "official_rows_in_scope": len(finalized),
        "official_rows_matched": sum(1 for record in official if matches.get(id(record))),
        "live_rows": len(live),
        "live_rows_matched": len(live) - len(unmatched),
        "live_rows_kept_approved_after_as_of": len(kept_recent),
        "live_rows_kept_outside_file_scope": len(kept_scope),
        "live_rows_excluded": len(excluded),
    }
    rows = [_excluded_row(record, source, "not in the official file and approved before its as_of date")
            for record in excluded]
    return finalized + kept_recent + kept_scope, info, rows


# Summed across all matched records rather than copied from the first one.
_SUMMED = {"total_disbursement", "disbursement_currency", "total_disbursement_usd"}


def _merge_into(official: ProjectRecord, group: list[ProjectRecord], source: OfficialSource) -> ProjectRecord:
    if not group:
        return official
    if official.total_disbursement is None:
        _sum_into(official, group, "total_disbursement", "disbursement_currency", "total_disbursement_usd")
    for name in STANDARD_FIELDS:
        if name in _OFFICIAL_WINS or name in _SUMMED or not _blank(getattr(official, name)):
            continue
        for record in group:
            value = getattr(record, name)
            if not _blank(value):
                setattr(official, name, value)
                break
    same_currency = [record.loan_amount for record in group
                     if record.loan_amount is not None and record.loan_currency == official.loan_currency]
    if official.loan_amount is None:
        _sum_into(official, group, "loan_amount", "loan_currency", "loan_amount_usd")
    elif same_currency and len(same_currency) == len(group):
        scraped = sum(same_currency, Decimal(0))
        if official.loan_amount and abs(scraped - official.loan_amount) > abs(official.loan_amount) * _AMOUNT_CONFLICT:
            official.data_quality_notes = join_values([
                official.data_quality_notes,
                f"The scraped source reports {format(scraped, 'f')} {official.loan_currency} for this "
                f"operation; the official file's {format(official.loan_amount, 'f')} is used.",
            ])
    official.source_record_ids = join_values(record.project_id for record in group)
    official.data_quality_notes = join_values([
        official.data_quality_notes,
        f"Row defined by the official file {source.id}; blank fields filled from {len(group)} scraped "
        "record(s) listed in source_record_ids.",
    ])
    return official


def _sum_into(target: ProjectRecord, group: list[ProjectRecord], amount: str, currency: str, usd: str) -> None:
    currencies = {getattr(record, currency) for record in group if getattr(record, amount) is not None}
    if len(currencies) != 1:
        return
    values = [getattr(record, amount) for record in group if getattr(record, amount) is not None]
    total = sum(values, Decimal(0))
    setattr(target, amount, total)
    setattr(target, currency, currencies.pop())
    if getattr(target, currency) == "USD":
        setattr(target, usd, total)


def _approved_after(record: ProjectRecord, as_of: date) -> bool:
    # IATI feeds often carry only a commitment date; it stands in for approval.
    known_date = record.approval_date or record.commitment_date
    if known_date:
        return known_date > as_of.isoformat()
    year = _year(record)
    return year is not None and year > as_of.year


def _outside_scope(record: ProjectRecord, source: OfficialSource) -> bool:
    if source.keep_live_if == "absent_means_non_sovereign":
        # ADB's sovereign-projects file lists every sovereign operation, so an
        # older operation missing from it is non-sovereign -- whatever IATI's
        # implementing-org heuristic said (confirmed 2026-09-16: e.g. 50146-001,
        # a loan to the privately owned Electric Networks of Armenia, came
        # through IATI as "Sovereign").
        if record.loan_type != "Non-sovereign":
            record.loan_type = "Non-sovereign"
            record.data_quality_notes = join_values([
                record.data_quality_notes,
                f"Not in {source.id}, which lists all ADB sovereign operations, so treated as "
                "non-sovereign.",
            ])
        return True
    if source.keep_live_if == "itfc":
        return record.instrument_category == "trade_finance"
    return False


def _group_by_project(records: list[ProjectRecord]) -> list[ProjectRecord]:
    """One row per ADB project number (IATI publishes one activity per loan/grant)."""

    groups: dict[str, list[ProjectRecord]] = defaultdict(list)
    loose = []
    for record in records:
        number = adb_project_number(record.project_id)
        (groups[number] if number else loose).append(record)
    grouped = []
    for number, members in groups.items():
        if len(members) == 1:
            member = members[0]
            member.source_record_ids = member.project_id
            member.project_id = number
            grouped.append(member)
            continue
        base = replace(members[0])
        base.project_id = number
        base.source_record_ids = join_values(member.project_id for member in members)
        base.approval_year = min(year for year in (_year(member) for member in members) if year is not None) \
            if any(_year(member) is not None for member in members) else None
        base.approval_date = min((member.approval_date for member in members if member.approval_date), default="")
        base.loan_amount = base.loan_amount_usd = None
        base.total_disbursement = base.total_disbursement_usd = None
        _sum_into(base, members, "loan_amount", "loan_currency", "loan_amount_usd")
        _sum_into(base, members, "total_disbursement", "disbursement_currency", "total_disbursement_usd")
        base.total_commitment, base.commitment_currency = base.loan_amount, base.loan_currency
        base.total_commitment_usd = base.loan_amount_usd
        categories = {member.instrument_category for member in members if member.instrument_category}
        if len(categories) > 1:
            base.instrument_category = "loan" if "loan" in categories else sorted(categories)[0]
        base.data_quality_notes = join_values([
            base.data_quality_notes,
            f"{len(members)} IATI activities of ADB project {number} combined into one row "
            "(amounts summed where they share a currency).",
        ])
        grouped.append(base)
    return grouped + loose


def _fuzzy_matches(
    official: list[ProjectRecord], live: list[ProjectRecord]
) -> tuple[dict[int, list[ProjectRecord]], list[ProjectRecord]]:
    """One-to-one name/country/year matching for files without ids (IsDB)."""

    by_country_year: dict[tuple[str, int | None], list[ProjectRecord]] = defaultdict(list)
    for record in live:
        if record.instrument_category == "trade_finance":
            continue
        by_country_year[(record.country_code, _year(record))].append(record)
    candidates = []
    for target in official:
        year = _year(target)
        wanted = _name_key(target.project_name)
        for offset in (0, -1, 1):
            for record in by_country_year.get((target.country_code, None if year is None else year + offset), []):
                ratio = difflib.SequenceMatcher(None, wanted, _name_key(record.project_name)).ratio()
                amount_close = (
                    target.loan_amount and record.loan_amount is not None and record.loan_currency == "USD"
                    and abs(record.loan_amount - target.loan_amount) <= target.loan_amount * _ISDB_AMOUNT_TOLERANCE
                )
                if ratio >= _ISDB_NAME_RATIO or (amount_close and ratio >= _ISDB_NAME_RATIO_WITH_AMOUNT):
                    candidates.append((ratio + (0.1 if amount_close else 0) - 0.01 * abs(offset), target, record))
    candidates.sort(key=lambda item: item[0], reverse=True)
    matches: dict[int, list[ProjectRecord]] = {}
    used: set[int] = set()
    for _, target, record in candidates:
        if id(target) in matches or id(record) in used:
            continue
        matches[id(target)] = [record]
        used.add(id(record))
    unmatched = [record for record in live if id(record) not in used]
    return matches, unmatched


def _excluded_row(record: ProjectRecord, source: OfficialSource, reason: str) -> dict[str, Any]:
    row = {name: getattr(record, name) for name in EXCLUDED_FIELDS if name not in {"reason", "official_source_id"}}
    row.update(reason=reason, official_source_id=source.id)
    return row


# --- supplement (ADB statement of loans) -------------------------------------


def _merge_supplement(
    bank_id: str, source: OfficialSource, loans: list[ProjectRecord],
    records: list[ProjectRecord], finalizer,
) -> tuple[list[ProjectRecord], dict[str, Any], list[dict[str, Any]]]:
    by_loan: dict[str, ProjectRecord] = {}
    for record in records:
        for number in (record.source_fields or {}).get("approval_numbers", []):
            by_loan.setdefault(number, record)
        for number in re.findall(r"LN(\d+)", record.source_record_ids or record.project_id, flags=re.IGNORECASE):
            by_loan.setdefault(str(int(number)), record)
    attached: dict[int, list[ProjectRecord]] = defaultdict(list)
    targets: dict[int, ProjectRecord] = {}
    added = []
    for loan in loans:
        target = by_loan.get(loan.source_fields.get("loan_number", ""))
        if target is not None and not _plausibly_same_operation(loan, target):
            # ADB reuses number ranges across loans and technical assistance
            # (e.g. a regional TA 6008 and a 2016 loan 6008).
            target = None
        if target is None:
            added.append(loan)
        else:
            attached[id(target)].append(loan)
            targets[id(target)] = target
    for key, target in targets.items():
        _attach_loan_terms(target, attached[key], source)
    added = finalizer.finalize(added)
    info = {
        "loans_in_file": len(loans),
        "loans_attached_to_existing_projects": sum(len(group) for group in attached.values()),
        "projects_enriched": len(targets),
        "loans_added_in_scope": len(added),
    }
    return records + added, info, []


def _plausibly_same_operation(loan: ProjectRecord, project: ProjectRecord) -> bool:
    if project.country_code and loan.country_code and project.country_code != loan.country_code:
        return False
    loan_year, project_year = _year(loan), _year(project)
    return loan_year is None or project_year is None or abs(loan_year - project_year) <= 6


def _attach_loan_terms(target: ProjectRecord, loans: list[ProjectRecord], source: OfficialSource) -> None:
    windows = {loan.funding_window for loan in loans if loan.funding_window}
    flags = {loan.concessional for loan in loans if loan.concessional}
    if not target.funding_window and windows:
        target.funding_window = join_values(sorted(windows))
    if not target.concessional and flags:
        target.concessional = flags.pop() if len(flags) == 1 else "Blended"
        target.concessional_flag = {"Yes": Decimal(1), "No": Decimal(0)}.get(target.concessional)
    if not target.interest_rate:
        target.interest_rate = join_values(loan.interest_rate for loan in loans)
    if not target.last_repayment_date:
        target.last_repayment_date = max((loan.last_repayment_date for loan in loans), default="")
    if target.total_disbursement is None:
        _sum_into(target, loans, "total_disbursement", "disbursement_currency", "total_disbursement_usd")
    target.source_record_ids = join_values([target.source_record_ids] + [loan.project_id for loan in loans])
    target.data_quality_notes = join_values([
        target.data_quality_notes,
        f"Fund, interest and repayment terms from {len(loans)} loan(s) in {source.id}.",
    ])


# --- extra fields (World Bank export) ----------------------------------------


def _fill_extra_fields(
    bank_id: str, source: OfficialSource, official: list[ProjectRecord],
    records: list[ProjectRecord], finalizer,
) -> tuple[list[ProjectRecord], dict[str, Any], list[dict[str, Any]]]:
    by_id = {normalize_text(record.project_id).casefold(): record for record in official}
    filled: dict[str, int] = defaultdict(int)
    matched = 0
    for record in records:
        extra = by_id.get(normalize_text(record.project_id).casefold())
        if extra is None:
            continue
        matched += 1
        for name in ("province", "location_text", "latitude", "longitude", "completion_date", "project_url"):
            if _blank(getattr(record, name)) and not _blank(getattr(extra, name)):
                setattr(record, name, getattr(extra, name))
                filled[name] += 1
        for name in _PREPEND_FIELDS:
            wording = getattr(extra, name)
            current = getattr(record, name)
            if wording and wording.casefold() not in current.casefold():
                setattr(record, name, join_values([wording, current]))
                filled[name] += 1
    return records, {"records_matched": matched, "fields_filled": dict(filled)}, []
