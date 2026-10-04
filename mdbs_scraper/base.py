"""Base collectors, shared record finalization, and local-snapshot support."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .cleaning import duration_years, extract_year, join_values, normalize_text, parse_date
from .instruments import instrument_category_from
from .config import BankDefinition
from .errors import SourceError
from .http import HttpClient
from .schema import ProjectRecord, utc_now
from .tabular import format_from_content_type, infer_format

# Every bank's own vocabulary for "proposed/under preparation, not yet
# approved" -- confirmed live (2026-09-22) against the full status
# vocabulary of all 14 banks: adb ("Proposed", "Pipeline/identification"),
# aiib ("Proposed"), eib ("Under appraisal"), ibrd/ida ("Pipeline"), idb
# ("Preparation"), cabei ("En Preparación"). Deliberately an explicit list,
# never pattern-matched/guessed (matching this codebase's convention
# elsewhere, e.g. dac_sectors.py's SECTOR_TEXT_TO_CATEGORY) -- a status not
# in this list is treated as already approved even if its own approval date
# happens to be blank for some other reason. AIIB's "On Hold" and IDB's/
# NDB's "Cancelled" are deliberately excluded: confirmed live/by majority
# that those statuses mostly describe projects that were already approved.
_PIPELINE_STATUSES = {
    "proposed", "pipeline", "pipeline/identification", "preparation",
    "en preparación", "under appraisal",
}


@dataclass(slots=True)
class ScrapeOptions:
    min_year: int | None = None
    max_year: int | None = None
    max_projects: int | None = None
    source_file: str = ""
    source_url: str = ""
    source_format: str = ""
    headless: bool = True
    timeout: float = 45.0
    request_delay: float = 0.75
    include_raw: bool = False


@dataclass(slots=True)
class LoadedSource:
    data: bytes
    url: str
    source_format: str


class BaseScraper(ABC):
    def __init__(self, bank: BankDefinition, options: ScrapeOptions | None = None) -> None:
        self.bank = bank
        self.options = options or ScrapeOptions()
        self.client = HttpClient(
            timeout=self.options.timeout, request_delay=self.options.request_delay
        )
        self.run_metadata: dict[str, int | str | None] = {
            "rows_seen": 0,
            "rows_retained": 0,
            "duplicates_removed": 0,
            "source_total": None,
            "completeness": "source-total-unavailable",
        }

    def load_source(
        self,
        *,
        url: str | None = None,
        source_format: str | None = None,
        accept: str = "*/*",
    ) -> LoadedSource:
        if self.options.source_file:
            path = Path(self.options.source_file).expanduser()
            if not path.is_file():
                raise SourceError(f"Local source file does not exist: {path}")
            fmt = self.options.source_format or infer_format(str(path), source_format or self.bank.source_format)
            return LoadedSource(path.read_bytes(), path.resolve().as_uri(), fmt)
        target = self.options.source_url or url or self.bank.source_url
        response = self.client.get(target, accept=accept)
        fmt = (
            self.options.source_format
            or source_format
            or format_from_content_type(response.headers.get("Content-Type", ""))
            or infer_format(response.url, self.bank.source_format)
        )
        return LoadedSource(response.body, response.url, fmt)

    @abstractmethod
    def scrape(self) -> list[ProjectRecord]:
        """Collect unfinalized records from the bank-specific source."""

    def run(self) -> list[ProjectRecord]:
        return self.finalize(self.scrape())

    def finalize(self, records: Iterable[ProjectRecord]) -> list[ProjectRecord]:
        records = list(records)
        self.run_metadata["rows_seen"] = len(records)
        now = utc_now()
        result: list[ProjectRecord] = []
        seen: set[str] = set()
        for record in records:
            record.bank_id = self.bank.id
            record.bank_name = self.bank.name
            record.bank_abbreviation = self.bank.abbreviation
            record.source_url = record.source_url or self.options.source_url or self.bank.source_url
            record.source_format = record.source_format or self.options.source_format or self.bank.source_format
            record.scraped_at = record.scraped_at or now
            record.project_name = normalize_text(record.project_name)
            record.project_id = normalize_text(record.project_id)
            if not record.project_id and record.project_name:
                record.project_id = self.synthetic_id(record)
                self.add_note(record, "Project ID was not disclosed; a stable synthetic ID was generated.")
            if record.pipeline_stage is None and record.status:
                record.pipeline_stage = (
                    Decimal(1) if record.status.strip().casefold() in _PIPELINE_STATUSES else Decimal(0)
                )
            if record.commitment_year is None:
                record.commitment_year = extract_year(
                    record.commitment_date, record.approval_date
                )
            if record.approval_year is None:
                record.approval_year = extract_year(record.approval_date)
                if record.approval_year is None and record.commitment_year is not None:
                    # No approval date disclosed at all -- the commitment or
                    # signing date is the closest the source offers.
                    record.approval_year = record.commitment_year
                    self.add_note(
                        record,
                        "approval_year is the commitment/signing year: the source discloses no "
                        "approval date.",
                    )
            if not record.instrument_category:
                record.instrument_category = instrument_category_from(
                    record.financing_instrument, record.loan_type
                )
            if record.duration_years is None:
                record.duration_years = duration_years(
                    record.commitment_date or record.approval_date, record.completion_date
                )
            year = record.approval_year
            if year is not None and self.options.min_year is not None and year < self.options.min_year:
                continue
            if year is not None and self.options.max_year is not None and year > self.options.max_year:
                continue
            if self.options.min_year is not None and year is None:
                self.add_note(record, "Commitment/approval year is missing; record was retained.")
            if record.duration_years is None:
                self.add_note(
                    record,
                    "Project duration cannot be derived because valid start and completion dates are not both exposed.",
                )
            if record.loan_amount is None:
                self.add_note(record, "A distinct bank loan/financing amount is not exposed by this source.")
            if record.total_commitment is None:
                self.add_note(record, "Total bank commitment is not exposed by this source.")
            if record.total_disbursement is None:
                self.add_note(
                    record,
                    "Total disbursement is not exposed by this source; blank does not mean zero.",
                )
            if not record.first_disbursement_date and not record.last_disbursement_date:
                self.add_note(
                    record,
                    "Disbursement timing (dates) is not exposed by this source; a total, if present, "
                    "does not indicate when it occurred.",
                )
            if not record.cofinancing_partners and record.cofinancing_amount is None:
                self.add_note(
                    record,
                    "No cofinancing/collaborating institution was disclosed by this source.",
                )
            if not record.conditionality:
                self.add_note(
                    record,
                    "Project-level conditionality is not exposed by this source; blank does not mean no conditions.",
                )
            if not any((record.province, record.location_text)) and all(
                value is None for value in (record.latitude, record.longitude)
            ):
                self.add_note(
                    record,
                    "No subnational location or coordinates were exposed by this source.",
                )
            if not record.sector:
                self.add_note(record, "Project sector is not exposed by this source.")
            key = f"{record.bank_id}:{record.project_id}".casefold()
            if key in seen:
                self.run_metadata["duplicates_removed"] = int(self.run_metadata["duplicates_removed"] or 0) + 1
                continue
            record.validate()
            seen.add(key)
            result.append(record)
            if self.options.max_projects and len(result) >= self.options.max_projects:
                self.run_metadata["completeness"] = "partial/limit-reached"
                break
        self.run_metadata["rows_retained"] = len(result)
        if self.run_metadata["source_total"] is not None and self.run_metadata["completeness"] != "partial/limit-reached":
            self.run_metadata["completeness"] = (
                "complete" if len(result) >= int(self.run_metadata["source_total"]) else "partial"
            )
        return result

    @staticmethod
    def add_note(record: ProjectRecord, note: str) -> None:
        record.data_quality_notes = join_values([record.data_quality_notes, note])

    @staticmethod
    def synthetic_id(record: ProjectRecord) -> str:
        fingerprint = "|".join(
            (record.project_url, record.project_name, record.country, record.approval_date)
        ).encode("utf-8")
        return "synthetic-" + hashlib.sha256(fingerprint).hexdigest()[:16]


def parse_common_dates(
    approval: Any = "", commitment: Any = "", completion: Any = "", *, day_first: bool | None = None
) -> tuple[str, str, str, int | None]:
    approval_date = parse_date(approval, day_first=day_first)
    commitment_date = parse_date(commitment, day_first=day_first) or approval_date
    completion_date = parse_date(completion, day_first=day_first)
    return approval_date, commitment_date, completion_date, extract_year(commitment_date, approval_date)
