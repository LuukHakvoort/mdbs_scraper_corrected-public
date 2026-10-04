"""Canonical, loss-conscious project record schema."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from .errors import ValidationError
from .instruments import INSTRUMENT_CATEGORIES


@dataclass(slots=True)
class ProjectRecord:
    bank_id: str = ""
    bank_name: str = ""
    bank_abbreviation: str = ""
    project_id: str = ""
    project_name: str = ""
    country: str = ""
    country_code: str = ""
    province: str = ""
    location_text: str = ""
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    approval_date: str = ""
    commitment_date: str = ""
    commitment_year: int | None = None
    # The year the operation was approved -- the one year definition used for
    # the default 2002-2026 scope and for the country-year aggregation.
    # Falls back to the commitment/signing year only when a source discloses
    # no approval date at all (EBRD's signing-date workbook, IATI feeds that
    # only carry a commitment transaction), with a note saying so.
    approval_year: int | None = None
    completion_date: str = ""
    duration_years: Decimal | None = None
    status: str = ""
    # 1 when this bank's own status means the project has not yet been
    # approved (Proposed/Pipeline/Preparation/Under appraisal, or a
    # translated equivalent) -- there is genuinely no approval date to
    # disclose yet, not a data gap. 0 when status is disclosed and doesn't
    # mean that. Blank only when status itself is blank/undisclosed. Same
    # Decimal-1/0 convention as concessional_flag, so pipeline-stage rows
    # can be dropped from date/amount analysis (e.g. `drop if
    # pipeline_stage == 1` in Stata) without guessing at a bank's status
    # vocabulary by hand.
    pipeline_stage: Decimal | None = None
    sector: str = ""
    subsector: str = ""
    sector_category: str = ""
    sector_category_code: str = ""
    loan_type: str = ""
    financing_instrument: str = ""
    # One of instruments.INSTRUMENT_CATEGORIES, or blank when the source does
    # not say (never guessed).
    instrument_category: str = ""
    # Which of a bank group's financing windows paid for this project, under
    # the window's own name (e.g. "African Development Fund" vs "African
    # Development Bank"). "; "-joined when genuinely blended across windows.
    funding_window: str = ""
    # "Yes"/"No"/"Blended", or blank where the source does not disclose it.
    concessional: str = ""
    # Numeric companion to concessional above, 1/0, left blank for "Blended"
    # rather than forcing an ambiguous case onto one side -- the same
    # convention interest_rate_pct uses for genuinely multi-rate projects.
    concessional_flag: Decimal | None = None
    total_project_cost: Decimal | None = None
    project_cost_currency: str = ""
    total_project_cost_usd: Decimal | None = None
    loan_amount: Decimal | None = None
    loan_currency: str = ""
    loan_amount_usd: Decimal | None = None
    # A multi-loan project's value here is multiple "; "-joined entries, not
    # a single number -- str, not Decimal, matching cofinancing_partners'
    # existing convention for the same shape.
    interest_rate: str = ""
    # Numeric companion to interest_rate above, populated only when a
    # project's loans yield exactly one distinct rate -- left blank (not
    # guessed) for the genuine multi-rate case, which stays fully visible in
    # interest_rate itself. Lets the common single-loan case be used as a
    # real number (e.g. in Stata) without a manual destring.
    interest_rate_pct: Decimal | None = None
    total_commitment: Decimal | None = None
    commitment_currency: str = ""
    total_commitment_usd: Decimal | None = None
    total_disbursement: Decimal | None = None
    disbursement_currency: str = ""
    total_disbursement_usd: Decimal | None = None
    first_disbursement_date: str = ""
    last_disbursement_date: str = ""
    last_repayment_date: str = ""
    cofinancing_partners: str = ""
    cofinancing_amount: Decimal | None = None
    cofinancing_currency: str = ""
    cofinancing_amount_usd: Decimal | None = None
    conditionality: str = ""
    conditionality_source_url: str = ""
    project_url: str = ""
    source_url: str = ""
    source_format: str = ""
    # Which official portfolio file (official_sources.json id) defines this
    # row, if any, and the "; "-joined ids of every scraped/official record
    # merged into it -- the audit trail for reconciliation.
    official_source_id: str = ""
    source_record_ids: str = ""
    source_updated_at: str = ""
    scraped_at: str = ""
    data_quality_notes: str = ""
    source_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def validate(self) -> None:
        if not self.bank_id:
            raise ValidationError("bank_id is required")
        if not self.project_id:
            raise ValidationError(f"{self.bank_id}: project_id is required")
        if not self.project_name:
            raise ValidationError(f"{self.bank_id}/{self.project_id}: project_name is required")
        for year_field in ("commitment_year", "approval_year"):
            year = getattr(self, year_field)
            if year is not None and not 1900 <= year <= 2200:
                raise ValidationError(f"{self.bank_id}/{self.project_id}: invalid {year_field} {year}")
        if not self.source_url:
            raise ValidationError(f"{self.bank_id}/{self.project_id}: source_url is required")
        if self.instrument_category and self.instrument_category not in INSTRUMENT_CATEGORIES:
            raise ValidationError(
                f"{self.bank_id}/{self.project_id}: unknown instrument_category {self.instrument_category!r}"
            )
        if self.latitude is not None and not Decimal("-90") <= self.latitude <= Decimal("90"):
            raise ValidationError(f"{self.bank_id}/{self.project_id}: invalid latitude")
        if self.longitude is not None and not Decimal("-180") <= self.longitude <= Decimal("180"):
            raise ValidationError(f"{self.bank_id}/{self.project_id}: invalid longitude")
        # total_disbursement/_usd are deliberately excluded here: a net-negative
        # disbursement is a legitimate, disclosed figure (e.g. IATI reports
        # refund/reversal transactions as negative-valued disbursements), not
        # a data error.
        for field_name in (
            "total_project_cost", "total_project_cost_usd", "loan_amount", "loan_amount_usd",
            "total_commitment", "total_commitment_usd", "cofinancing_amount",
            "cofinancing_amount_usd", "duration_years", "interest_rate_pct",
            "concessional_flag",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValidationError(
                    f"{self.bank_id}/{self.project_id}: {field_name} cannot be negative"
                )

    def to_row(self, include_raw: bool = False) -> dict[str, str | int]:
        raw = asdict(self)
        raw.pop("source_fields", None)
        row: dict[str, str | int] = {}
        for key, value in raw.items():
            if value is None:
                row[key] = ""
            elif isinstance(value, Decimal):
                row[key] = format(value, "f")
            else:
                row[key] = value
        if include_raw:
            row["source_fields_json"] = json.dumps(
                self.source_fields, ensure_ascii=False, sort_keys=True, default=str
            )
        return row


STANDARD_FIELDS = [
    name for name in ProjectRecord.__dataclass_fields__ if name != "source_fields"
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def iso_date(value: date | datetime | None) -> str:
    if value is None:
        return ""
    return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
