"""Shared mapping for official downloads and public portfolio pages."""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from ..base import BaseScraper, LoadedSource, parse_common_dates
from ..browser import download_via_browser, render_page
from ..cleaning import (
    detect_currency,
    extract_year,
    join_values,
    normalize_loan_type,
    normalize_text,
    normalized_key,
    parse_amount,
    parse_coordinate,
    parse_date,
)
from ..dac_sectors import (
    IATI_CONCESSIONAL_FLOW_TYPE_CODES,
    IATI_NON_CONCESSIONAL_FLOW_TYPE_CODES,
    finance_type_name_from_code,
    sector_category_from,
    sector_name_from_dac_code,
    status_name_from_code,
)
from ..errors import SourceLayoutChanged
from ..iati_geography import (
    alpha3_from_alpha2,
    country_name_from_code,
    iso3_country_code_from_name,
    region_name_from_code,
)
from ..parsers import extract_heading, extract_label_values, extract_links, parse_tables, table_to_dicts
from ..schema import ProjectRecord
from ..tabular import first, infer_format, json_rows, normalized_row, read_rows


LOG = logging.getLogger(__name__)


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "project_id": (
        "project_id", "project_number", "project_no", "project_code", "operation_number",
        "operation_no", "numero_de_operacion", "id", "projectid", "operation_id",
        "iati_identifier", "@iati-identifier", "proyecto", "oper_num",
    ),
    "project_name": (
        "project_name", "project_title", "operation_name", "operation_title", "title", "name",
        "project", "activity_title", "titulo_del_proyecto", "oper_nm",
    ),
    "country": (
        "country", "country_name", "member", "location", "recipient_country", "borrower_country",
        "beneficiary_country", "pais", "economy", "countries", "cntry_nm",
    ),
    "country_code": ("country_code", "iso3", "iso_code", "recipient_country_code", "cntry_cd"),
    "region_code": ("recipient_region_code",),
    "region_vocabulary": ("recipient_region_vocabulary",),
    "province": (
        "province", "state", "region", "district", "municipality", "department", "provincia",
        "subnational_location", "ubicacion_subnacional",
    ),
    "location_text": (
        "precise_location", "project_location", "location_description", "address", "city",
        "geographic_location", "locations",
    ),
    "latitude": ("latitude", "lat", "project_latitude"),
    "longitude": ("longitude", "lon", "lng", "project_longitude"),
    "approval_date": (
        "approval_date", "approved_date", "date_approved", "board_approval_date", "board_date",
        "date_of_board_approval", "fecha_de_aprobacion", "approval_year", "year_approved",
        "apprvl_dt",
    ),
    "commitment_date": (
        "commitment_date", "signing_date", "signed_date", "contract_date", "fecha_de_contrato",
        "start_date", "effective_date", "fecha_de_firma", "sign_dt", "original_signing_date",
        "iati_start_date", "iati_commitment_date",
    ),
    "completion_date": (
        "completion_date", "closing_date", "end_date", "expected_completion_date", "expiry_date",
        "expiration_date", "final_date", "iati_end_date",
    ),
    "status": (
        "status", "project_status", "operation_status", "stage", "situacion", "estado",
        "publc_sts_nm",
    ),
    "status_code": ("activity_status_code",),
    "sector": (
        "sector", "sector_name", "industry_sector", "major_sector", "area", "theme", "sectors",
        "sectores", "sector_nm",
    ),
    "sector_code": ("sector_code",),
    "sector_vocabulary": ("sector_vocabulary",),
    "subsector": (
        "subsector", "sub_sector", "industry", "activity_sector", "sub_sectores", "subsector_nm",
    ),
    "loan_type": (
        "loan_type", "financing_type", "lending_type", "risk_type", "tipo_de_riesgo",
        "notice_type", "sovereign_non_sovereign", "sovereign_status", "lending_typ_nm",
        "sector_de_mercado", "iati_counterpart_org_type", "soberano_no_soberano",
    ),
    "financing_instrument": (
        "financing_instrument", "instrument", "lending_instrument", "operation_type", "product",
        "instrumento_de_financiacion", "arrangement_type", "opertyp_nm", "instrumento_de_inversion",
    ),
    "finance_type_code": ("default_finance_type_code",),
    "flow_type_code": ("default_flow_type_code",),
    "funding_window": ("funding_window", "iati_funding_window"),
    "funding_window_ref": ("iati_funding_window_ref",),
    "total_project_cost": (
        "total_project_cost", "project_cost", "total_cost", "total_original_cost", "project_total",
        "costo_del_proyecto", "costo_del_proyecto_usd", "estimated_total_project_cost",
        "totl_cost_orig", "costo_total_de_la_inversion",
    ),
    "project_cost_currency": (
        "project_cost_currency", "total_cost_currency", "currency", "currency_code", "moneda",
    ),
    "loan_amount": (
        "loan_amount", "financing_amount", "bank_financing", "approved_financing", "adb_financing",
        "ebrd_finance", "eib_finance", "caf_loan", "prestamo_caf", "amount_agreed",
        "monto_del_prestamo_caf_usd", "proposed_loan_amount", "arrangement_amount",
        "signed_amount", "finance_contract_amount", "monto_aprobado", "orig_apprvd_useq_amnt",
        "iati_commitment_value",
    ),
    "loan_currency": (
        "loan_currency", "financing_currency", "currency", "currency_code", "amount_currency",
        "moneda", "iati_commitment_currency",
    ),
    "total_commitment": (
        "total_commitment", "commitment_amount", "total_commitments", "original_approved_amount",
        "approved_amount", "amount_approved", "current_approved_amount", "signed_amount",
        "total_financing", "bank_commitment",
    ),
    "commitment_currency": (
        "commitment_currency", "approved_amount_currency", "currency", "currency_code", "moneda",
    ),
    "total_disbursement": (
        "total_disbursement", "total_disbursements", "disbursed_amount", "amount_disbursed",
        "caf_disbursed", "desembolsado_caf", "monto_desembolsado_caf_usd",
        "desembolsado_caf_usd", "disbursements", "disbursed", "monto_desembolsado",
        "iati_disbursement_value",
    ),
    "disbursement_currency": (
        "disbursement_currency", "disbursed_currency", "currency", "currency_code", "moneda",
        "iati_disbursement_currency",
    ),
    "first_disbursement_date": (
        "first_disbursement_date", "date_of_first_disbursement", "initial_disbursement_date",
        "first_disbursement", "fecha_primer_desembolso", "iati_first_disbursement_date",
    ),
    "last_disbursement_date": (
        "last_disbursement_date", "date_of_last_disbursement", "most_recent_disbursement_date",
        "most_recent_disbursement", "latest_disbursement_date", "disbursement_date",
        "fecha_ultimo_desembolso", "fecha_de_desembolso", "iati_last_disbursement_date",
    ),
    "cofinancing_partners": (
        "cofinancing_partners", "co_financiers", "cofinanciers", "co_financing_partners",
        "cofinancing", "co_financing", "joint_financing", "parallel_financing",
        "partner_institutions", "development_partners", "participating_organisation",
        "participating_organisations", "cofinanciadores", "cofinanciamiento",
        "iati_cofinancing_partners", "fuente_financiamiento",
        "nombre_de_la_fuente_externa",
    ),
    "cofinancing_amount": (
        "cofinancing_amount", "co_financing_amount", "cofinanced_amount", "parallel_financing_amount",
        "monto_cofinanciado", "monto_fuente_financiamiento", "monto_de_la_fuente_externa",
    ),
    "cofinancing_currency": (
        "cofinancing_currency", "co_financing_currency", "currency", "currency_code", "moneda",
    ),
    "conditionality": (
        "conditionality", "conditions", "loan_conditions", "policy_conditions", "policy_actions",
        "prior_actions", "covenants", "terms_and_conditions", "condiciones", "condicionalidad",
    ),
    "conditionality_source_url": (
        "conditionality_source_url", "conditions_url", "program_document", "policy_document",
    ),
    "project_url": (
        "project_url", "url", "link", "project_link", "operation_url", "document_link_url",
        # CABEI: its own project page before the d-portal IATI viewer link.
        "documentos_por_operacion", "url_iati",
    ),
    "source_updated_at": (
        "last_updated", "updated_at", "data_date", "actualizado", "publication_date",
        "fecha_de_actualizacion",
    ),
}


def value_with_key(row: dict[str, Any], aliases: Iterable[str]) -> tuple[str, Any]:
    normalized = normalized_row(row)
    for alias in aliases:
        key = normalized_key(alias)
        value = normalized.get(key)
        if value is not None and normalize_text(value):
            return key, value
    return "", ""


def scalar(value: Any) -> str:
    if isinstance(value, list):
        return join_values(value)
    if isinstance(value, dict):
        return join_values(value.values())
    return normalize_text(value)


def _money(
    row: dict[str, Any],
    amount_field: str,
    currency_field: str,
    default_currency: str = "",
    *,
    amount_period_is_decimal: bool = False,
) -> tuple[Decimal | None, str, bool]:
    key, raw_amount = value_with_key(row, FIELD_ALIASES[amount_field])
    _, raw_currency = value_with_key(row, FIELD_ALIASES[currency_field])
    currency = detect_currency(raw_currency, default_currency)
    amount, detected = parse_amount(raw_amount, currency, period_is_decimal=amount_period_is_decimal)
    currency = detected or currency
    explicitly_usd = currency == "USD" or "usd" in key or "us_dollar" in key
    return amount, currency, explicitly_usd


# Which financing window means concessional terms and which does not.
# AfDB Group's windows are identified by org ref; IsDB's share one ref
# (XM-DAC-46025) and are only distinguishable by their narrative text, so
# they are matched by name. A window that is on neither list (e.g. AfDB's
# Africa Growing Together Fund, which co-lends at near-market terms, or its
# Endowment Fund) is deliberately absent rather than guessed at.
_CONCESSIONAL_WINDOW_REFS = {
    "XM-DAC-46003",  # African Development Fund
    "XM-DAC-TSF",    # Transition Support Facility, an ADF resource
    "XM-DAC-NTF",    # Nigerian Trust Fund
    "XM-DAC-SRF",    # Special Relief Funds
    "XM-DAC-RWSSI",  # Rural Water Supply & Sanitation Initiative
    "XM-DAC-CAW",    # Climate Action Window, an ADF-16 resource
}
_NON_CONCESSIONAL_WINDOW_REFS = {
    "XM-DAC-46002",  # African Development Bank -- ordinary capital
}
_CONCESSIONAL_WINDOW_NAMES = {"isdb - islamic solidarity fund for development"}
_NON_CONCESSIONAL_WINDOW_NAMES = {"isdb - ordinary capital resources"}

# IATI FinanceType 110 (Standard grant). A grant carries a 100% grant element
# whichever facility paid it, so it settles concessionality on its own.
_IATI_GRANT_FINANCE_TYPE = "110"


def _concessionality(
    window_names: str, window_refs: str, finance_type_code: str, flow_type_code: str
) -> tuple[str, Decimal | None, str]:
    """Classify an activity's financing terms as (concessional, flag, note).

    A disclosed funding window always wins over the activity's flow type.
    That ordering is deliberate and load-bearing: AfDB discloses a window on
    every activity but mislabels flow type badly (confirmed live 2026-09-15 --
    799 of its 1,289 ordinary-capital activities are tagged ODA, which would
    invert their classification), while ADB never discloses a window and its
    flow type is reliable. Checking the window first is therefore right for
    both without either needing a bank-specific branch.
    """
    refs = {ref for ref in window_refs.split("; ") if ref}
    names = {name.casefold() for name in window_names.split("; ") if name}
    if window_names:
        concessional = (refs & _CONCESSIONAL_WINDOW_REFS) | (names & _CONCESSIONAL_WINDOW_NAMES)
        non_concessional = (
            (refs & _NON_CONCESSIONAL_WINDOW_REFS) | (names & _NON_CONCESSIONAL_WINDOW_NAMES)
        )
        if concessional and non_concessional:
            return "Blended", None, (
                f"Financed by both concessional and non-concessional windows ({window_names}); "
                "concessional_flag is left blank rather than forcing one side."
            )
        if concessional:
            return "Yes", Decimal(1), ""
        if non_concessional:
            return "No", Decimal(0), ""
        if finance_type_code == _IATI_GRANT_FINANCE_TYPE:
            return "Yes", Decimal(1), (
                f"Concessional inferred from the financing being a grant; the window that paid "
                f"({window_names}) is a special fund whose terms the source does not state."
            )
        return "", None, (
            f"Concessionality not determined: financed by {window_names}, a special fund whose "
            "terms the source does not state, and the financing is not disclosed as a grant."
        )
    if flow_type_code in IATI_CONCESSIONAL_FLOW_TYPE_CODES:
        return "Yes", Decimal(1), (
            "Concessional derived from the activity being reported as ODA; this publisher does "
            "not disclose which of its financing windows paid, so funding_window is blank."
        )
    if flow_type_code in IATI_NON_CONCESSIONAL_FLOW_TYPE_CODES:
        return "No", Decimal(0), (
            "Non-concessional derived from the activity being reported as OOF (other official "
            "flows); this publisher does not disclose which of its financing windows paid, so "
            "funding_window is blank."
        )
    return "", None, ""


def generic_record_from_row(
    row: dict[str, Any],
    *,
    default_currency: str = "",
    source_url: str = "",
    source_format: str = "",
    day_first: bool | None = None,
    amount_period_is_decimal: bool = False,
    bank_abbreviation: str = "",
) -> ProjectRecord:
    source_row = row
    row = normalized_row(row)  # once, instead of once per field lookup
    get = lambda field: scalar(first(row, FIELD_ALIASES[field]))
    commitment_date_key, _ = value_with_key(row, FIELD_ALIASES["commitment_date"])
    completion_date_key, _ = value_with_key(row, FIELD_ALIASES["completion_date"])
    approval, commitment, completion, year = parse_common_dates(
        get("approval_date"), get("commitment_date"), get("completion_date"), day_first=day_first
    )
    loan, loan_currency, loan_usd = _money(
        row, "loan_amount", "loan_currency", default_currency,
        amount_period_is_decimal=amount_period_is_decimal,
    )
    project_cost, project_cost_currency, project_cost_usd = _money(
        row, "total_project_cost", "project_cost_currency", default_currency,
        amount_period_is_decimal=amount_period_is_decimal,
    )
    commitment_amount, commitment_currency, commitment_usd = _money(
        row, "total_commitment", "commitment_currency", default_currency,
        amount_period_is_decimal=amount_period_is_decimal,
    )
    disbursement, disbursement_currency, disbursement_usd = _money(
        row, "total_disbursement", "disbursement_currency", default_currency,
        amount_period_is_decimal=amount_period_is_decimal,
    )
    cofinancing_amount, cofinancing_currency, cofinancing_usd = _money(
        row, "cofinancing_amount", "cofinancing_currency", default_currency,
        amount_period_is_decimal=amount_period_is_decimal,
    )
    first_disbursement_date = parse_date(get("first_disbursement_date"), day_first=day_first)
    last_disbursement_date = parse_date(get("last_disbursement_date"), day_first=day_first)
    country = get("country")
    country_code = get("country_code")
    country_from_code = False
    country_from_region = False
    if not country and country_code:
        # IATI's <recipient-country code="DZ"/> discloses only a bare ISO2
        # code, no <narrative> name, for most activities in this project's
        # IATI-sourced banks -- country_code is already correctly populated
        # (via the generic XML attribute flattening), just never translated.
        country = country_name_from_code(country_code)
        country_from_code = bool(country)
    if not country:
        # Some activities (e.g. AfDB's "multinational" package) disclose a
        # <recipient-region> instead of a <recipient-country> for projects
        # that aren't country-specific -- there's no ISO code for "a region",
        # so this is the only geography signal available. Using it for
        # `country` matches how several banks already disclose broad
        # groupings ("Regional", "Multi-Regional", "Africa") in that field.
        region_code = get("region_code")
        if region_code:
            country = region_name_from_code(region_code)
            country_from_region = bool(country)
    # `country_code` above is whatever the source disclosed verbatim --
    # adb/afdb/caf/isdb's real IATI alpha-2 codes, but also e.g. IDB's own
    # *non-ISO* 2-letter scheme (confirmed live: IDB's "PR" means Peru, not
    # Puerto Rico) and nothing at all for most other banks. The record's
    # own `country_code` is repurposed to hold a uniform ISO-3 code instead,
    # derived from the country *name* text wherever possible -- which also
    # naturally discards non-standard codes like IDB's, since name text is
    # used instead of trusting whatever code a source happened to disclose.
    # The one exception: when `country` above was itself just derived from
    # `country_code` (country_from_code), that code has already been proven
    # genuine by the very fact the translation succeeded, so it's converted
    # directly rather than round-tripped through name matching.
    if country_from_code:
        iso3_code = alpha3_from_alpha2(country_code)
    else:
        iso3_code = iso3_country_code_from_name(country)
    country_code_unresolved = bool(country) and not iso3_code
    status = get("status")
    status_from_code = False
    if not status:
        # adb/afdb/caf/isdb disclose <activity-status code="X"/> but never a
        # human-readable status text -- translate it via IATI's own small,
        # stable ActivityStatus codelist.
        status = status_name_from_code(get("status_code"))
        status_from_code = bool(status)
    sector = get("sector")
    sector_from_dac_code = False
    if not sector:
        # Some IATI publishers (e.g. ADB, IsDB) disclose only a bare OECD DAC
        # code, no human-readable name -- translate it using whichever
        # DAC codelist its vocabulary attribute names ("1" 5-digit purpose,
        # "2" 3-digit category; IATI defaults absent vocabulary to "1").
        # Other vocabularies use unrelated numbering that could otherwise
        # coincidentally collide with a DAC code, so they're left untranslated.
        vocabulary = get("sector_vocabulary") or "1"
        sector = sector_name_from_dac_code(get("sector_code"), vocabulary)
        sector_from_dac_code = bool(sector)
    sector_category, sector_category_code = sector_category_from(
        sector, get("sector_code"), get("sector_vocabulary")
    )
    financing_instrument = get("financing_instrument")
    financing_instrument_from_code = False
    if not financing_instrument:
        # adb/afdb/caf disclose <default-finance-type code="421"/> and no
        # instrument text at all -- without this the column is 100% blank
        # for all three.
        financing_instrument = finance_type_name_from_code(get("finance_type_code"))
        financing_instrument_from_code = bool(financing_instrument)
    funding_window = get("funding_window")
    concessional, concessional_flag, concessional_note = _concessionality(
        funding_window, get("funding_window_ref"), get("finance_type_code"), get("flow_type_code")
    )
    cofinancing_partners = get("cofinancing_partners")
    if bank_abbreviation and cofinancing_partners:
        # A funder can be the reporting bank itself, disclosed under its own
        # abbreviation rather than its full name (confirmed live: EIB lists
        # itself as "EIB", not "European Investment Bank", on 67% of its
        # activities) -- not a real external cofinancier.
        parts = [
            part for part in cofinancing_partners.split("; ")
            if part.casefold() != bank_abbreviation.casefold()
        ]
        cofinancing_partners = join_values(parts)
    record = ProjectRecord(
        project_id=get("project_id"),
        project_name=get("project_name"),
        country=country,
        country_code=iso3_code,
        province=get("province"),
        location_text=get("location_text"),
        latitude=parse_coordinate(get("latitude")),
        longitude=parse_coordinate(get("longitude")),
        approval_date=approval,
        commitment_date=commitment,
        commitment_year=year,
        completion_date=completion,
        status=status,
        sector=sector,
        subsector=get("subsector"),
        sector_category=sector_category,
        sector_category_code=sector_category_code,
        loan_type=normalize_loan_type(get("loan_type")),
        financing_instrument=financing_instrument,
        funding_window=funding_window,
        concessional=concessional,
        concessional_flag=concessional_flag,
        total_project_cost=project_cost,
        project_cost_currency=project_cost_currency,
        total_project_cost_usd=project_cost if project_cost_usd else None,
        loan_amount=loan,
        loan_currency=loan_currency,
        loan_amount_usd=loan if loan_usd else None,
        total_commitment=commitment_amount,
        commitment_currency=commitment_currency,
        total_commitment_usd=commitment_amount if commitment_usd else None,
        total_disbursement=disbursement,
        disbursement_currency=disbursement_currency,
        total_disbursement_usd=disbursement if disbursement_usd else None,
        first_disbursement_date=first_disbursement_date,
        last_disbursement_date=last_disbursement_date,
        cofinancing_partners=cofinancing_partners,
        cofinancing_amount=cofinancing_amount,
        cofinancing_currency=cofinancing_currency,
        cofinancing_amount_usd=cofinancing_amount if cofinancing_usd else None,
        conditionality=get("conditionality"),
        conditionality_source_url=get("conditionality_source_url"),
        project_url=get("project_url"),
        source_url=source_url,
        source_format=source_format,
        source_updated_at=get("source_updated_at"),
        source_fields=source_row,
    )
    if country_from_code:
        record.data_quality_notes = join_values([
            record.data_quality_notes,
            f"Country name derived from IATI country code {country_code} "
            "(not disclosed as text by the source).",
        ])
    elif country_from_region:
        record.data_quality_notes = join_values([
            record.data_quality_notes,
            f"The source discloses a region ({record.country}), not a specific country, "
            f"via IATI region code {get('region_code')}.",
        ])
    if country_code_unresolved:
        record.data_quality_notes = join_values([
            record.data_quality_notes,
            f"No ISO-3 country code could be determined for \"{record.country}\" -- likely a "
            "multi-country/regional/institutional grouping rather than a single country; left "
            "blank rather than guessed.",
        ])
    if status_from_code:
        record.data_quality_notes = join_values([
            record.data_quality_notes,
            f"Status derived from IATI activity-status code {get('status_code')} "
            "(not disclosed as text by the source).",
        ])
    if sector_from_dac_code:
        codelist = "3-digit category" if get("sector_vocabulary") == "2" else "5-digit purpose"
        record.data_quality_notes = join_values([
            record.data_quality_notes,
            f"Sector name derived from OECD DAC {codelist} code {get('sector_code')} "
            "(not disclosed as text by the source).",
        ])
    if financing_instrument_from_code:
        record.data_quality_notes = join_values([
            record.data_quality_notes,
            f"Financing instrument derived from IATI finance-type code "
            f"{get('finance_type_code')} (not disclosed as text by the source).",
        ])
    if concessional_note:
        record.data_quality_notes = join_values([record.data_quality_notes, concessional_note])
    if record.total_commitment is None and record.loan_amount is not None:
        record.total_commitment = record.loan_amount
        record.commitment_currency = record.loan_currency
        record.total_commitment_usd = record.loan_amount_usd
        record.data_quality_notes = join_values([
            record.data_quality_notes,
            "The source reports one bank financing/loan amount; it is also recorded as the "
            "bank commitment, rather than treated as total project cost.",
        ])
    # IATI activity-dates come in planned/actual pairs (see
    # tabular.py's _iati_activity_date_fields); commitment_date/completion_date
    # prefer actual but fall back to planned when no actual date is disclosed
    # yet (e.g. a still-open project). Flag it when that fallback fired, so a
    # reader doesn't mistake an estimate for a realized date/duration.
    if (
        commitment_date_key == "iati_start_date"
        and normalize_text(row.get("iati_start_date_planned"))
        and record.commitment_date
    ):
        record.data_quality_notes = join_values([
            record.data_quality_notes,
            "Commitment/start date is the source's planned start date; an actual start date "
            "has not been disclosed.",
        ])
    if (
        completion_date_key == "iati_end_date"
        and normalize_text(row.get("iati_end_date_planned"))
        and record.completion_date
    ):
        record.data_quality_notes = join_values([
            record.data_quality_notes,
            "Completion date is the source's planned/expected end date, not a confirmed "
            "actual end date -- duration_years reflects this estimate, not a realized figure.",
        ])
    disbursement_correction = normalize_text(row.get("iati_disbursement_correction"))
    if disbursement_correction:
        try:
            correction_amount = -Decimal(disbursement_correction)
        except InvalidOperation:
            correction_amount = None
        if correction_amount is not None:
            currency_label = f" {record.disbursement_currency}" if record.disbursement_currency else ""
            record.data_quality_notes = join_values([
                record.data_quality_notes,
                f"The source also discloses a {format(correction_amount, 'f')}{currency_label} "
                "disbursement-type correction/reversal transaction (a negative-valued entry, not a "
                "genuine negative disbursement) -- excluded from total_disbursement rather than "
                "netted in.",
            ])
    if record.sector and not record.sector_category:
        record.data_quality_notes = join_values([
            record.data_quality_notes,
            f"Sector \"{record.sector}\" could not be mapped to a standardized "
            "sector_category (no confident match); left blank rather than guessed.",
        ])
    source_note = normalize_text(source_row.get("_source_note"))
    if source_note:
        record.data_quality_notes = join_values([record.data_quality_notes, source_note])
    return record


class TabularScraper(BaseScraper):
    """Base for a bank's official structured download.

    ``default_currency``/``day_first`` come from the bank's registry entry
    (``banks.json``), not from a subclass override, so a bank only needs a
    Python file when it needs bespoke parsing logic (see e.g. CABEIScraper).
    """

    def rows_from_source(self, source: LoadedSource) -> list[dict[str, Any]]:
        return read_rows(source.data, source.source_format)

    def map_row(self, row: dict[str, Any], source: LoadedSource) -> ProjectRecord:
        return generic_record_from_row(
            row,
            default_currency=self.bank.default_currency,
            # The stable, always-refetchable requested URL -- not
            # source.url (the response's *resolved* URL). Some official
            # downloads (e.g. IDB's) redirect through a signed, short-lived
            # token URL; recording that as record.source_url would leave a
            # citation link that's already expired by the time anyone opens
            # it (its own JWT expiry decoded to ~2 hours after generation).
            source_url=self.options.source_url or self.bank.source_url,
            source_format=source.source_format,
            day_first=self.bank.day_first,
            amount_period_is_decimal=self.bank.amount_period_is_decimal,
            bank_abbreviation=self.bank.abbreviation,
        )

    def scrape(self) -> list[ProjectRecord]:
        source = self.load_source()
        rows = self.rows_from_source(source)
        records = [self.map_row(row, source) for row in rows]
        records = [record for record in records if record.project_name]
        if not records:
            raise SourceLayoutChanged(
                f"{self.bank.abbreviation}: source returned no recognizable project rows"
            )
        return records


def rows_from_ckan_resources(client, resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Download and parse the best-available resource from a CKAN package's resource list.

    Prefers IATI-XML/XML/CSV. Used by banks whose official data is published
    as a CKAN package (e.g. an IATI Registry publisher, or a national/
    institutional open-data portal) rather than a direct file download or a
    scrapeable HTML page. Shared by CABEIScraper (single package) and
    ``CkanOrganizationScraper`` subclasses (many packages combined).
    """

    def normalized_format(resource: dict[str, Any], url: str) -> str:
        declared = str(resource.get("format") or "").strip().lower()
        if declared in {"iati-xml", "iati"}:
            return "xml"
        if declared in {"csv", "xml", "json", "xlsx", "tsv"}:
            return declared
        return infer_format(url).lower()

    ranked = sorted(
        resources,
        key=lambda resource: str(resource.get("format", "")).lower() in {"iati-xml", "iati", "xml", "csv"},
        reverse=True,
    )
    for resource in ranked:
        url = resource.get("url")
        if not url:
            continue
        fmt = normalized_format(resource, url)
        if fmt not in {"csv", "xml", "json", "xlsx", "tsv"}:
            continue
        response = client.get(url)
        rows = read_rows(response.body, fmt)
        if rows:
            return rows
    return []


class CkanOrganizationScraper(TabularScraper):
    """Combines every relevant package published by one CKAN organization.

    ``source_url`` in banks.json should be that organization's
    ``package_search`` endpoint (e.g. an IATI Registry publisher:
    ``.../api/3/action/package_search?fq=organization:<org>&rows=100``), used
    when a bank's official data is split across many packages (e.g. one IATI
    activity file per recipient country) rather than published as a single
    file. Override ``_is_relevant_package`` to skip non-project packages
    (e.g. a publisher's own organisation-file alongside its activity files).
    """

    def _is_relevant_package(self, package: dict[str, Any]) -> bool:
        name = str(package.get("name") or "").lower()
        if name.endswith("-org") or "orgfile" in name:
            return False
        # Some publishers don't mark their organisation-file package's name
        # distinctly (e.g. IsDB's "isdb-activity" package -- no "-org" suffix,
        # no "orgfile" substring -- whose one resource is nonetheless
        # isdb-organisation.xml). Fall back to checking the resource
        # filename/URL itself when the name-based check above misses it.
        resources = package.get("resources") or []
        if resources and all(
            "organisation" in str(resource.get("url", "")).lower()
            or "organization" in str(resource.get("url", "")).lower()
            for resource in resources
        ):
            return False
        return True

    def _remaining_packages(
        self, source: LoadedSource, payload: dict[str, Any], already: int
    ) -> list[dict[str, Any]]:
        """Fetch any package_search pages beyond the first.

        ``package_search`` returns at most ``rows`` packages per call (100 in
        banks.json); a publisher with more would otherwise be truncated
        without any error. ``count`` is CKAN's own total.
        """

        total = int(payload.get("result", {}).get("count") or 0)
        if total <= already or self.options.source_file:
            return []
        extra: list[dict[str, Any]] = []
        base_url = source.url.split("&start=")[0]
        while already + len(extra) < total:
            response = self.client.get(base_url, params={"start": already + len(extra)}, accept="application/json")
            page = json.loads(response.body.decode("utf-8-sig")).get("result", {}).get("results", [])
            if not page:
                break
            extra.extend(page)
        return extra

    def _package_note(self, package: dict[str, Any]) -> str:
        """Optional note attached to every row sourced from one CKAN package.

        Default: none. Override when a publisher's packages mix data of
        meaningfully different completeness/reliability (see IsDBScraper,
        whose ITFC trade-finance package discloses far fewer transaction-level
        disbursements than its own activities).
        """
        return ""

    def rows_from_source(self, source: LoadedSource) -> list[dict[str, Any]]:
        if source.source_format.lower() not in {"json", "jsonld"}:
            # A local --source-file override supplies project rows directly,
            # bypassing CKAN package discovery entirely.
            return read_rows(source.data, source.source_format)
        try:
            payload = json.loads(source.data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            LOG.warning("%s: CKAN package_search response is not JSON (%s)", self.bank.id, exc)
            return []
        packages = list(payload.get("result", {}).get("results", []))
        packages.extend(self._remaining_packages(source, payload, len(packages)))
        rows: list[dict[str, Any]] = []
        for package in packages:
            if not isinstance(package, dict) or not self._is_relevant_package(package):
                continue
            package_rows = rows_from_ckan_resources(self.client, package.get("resources", []))
            note = self._package_note(package)
            if note:
                for row in package_rows:
                    row["_source_note"] = note
            rows.extend(package_rows)
        return rows


class BrowserDownloadTabularScraper(TabularScraper):
    """A TabularScraper whose official download requires a real browser engine.

    Some official downloads are only reachable through a JS-capable browser
    (e.g. sites behind bot-challenge protection that reject plain HTTP
    clients regardless of headers). When the bank's registry entry marks it
    ``dynamic`` and no local snapshot/explicit source override is supplied,
    the download is fetched through Playwright instead of a plain HTTP GET.
    """

    def load_source(
        self,
        *,
        url: str | None = None,
        source_format: str | None = None,
        accept: str = "*/*",
    ) -> LoadedSource:
        if self.options.source_file or not self.bank.dynamic:
            return super().load_source(url=url, source_format=source_format, accept=accept)
        target = self.options.source_url or url or self.bank.source_url
        downloaded = download_via_browser(
            target, headless=self.options.headless, timeout=self.options.timeout
        )
        fmt = self.options.source_format or source_format or infer_format(
            downloaded.suggested_filename, self.bank.source_format
        )
        return LoadedSource(downloaded.body, downloaded.url, fmt)


class HTMLPortfolioScraper(BaseScraper):
    """Official portfolio adapter with table, captured-JSON, and detail-page paths.

    Per-bank tuning (``default_currency``, ``day_first``, ``project_href_pattern``,
    ``pagination_href_pattern``, ``max_listing_pages``) comes from the bank's
    registry entry (``banks.json``); a subclass is only needed for bespoke
    behavior beyond these declarative knobs.
    """

    def map_row(self, row: dict[str, Any], source_url: str, source_format: str) -> ProjectRecord:
        return generic_record_from_row(
            row,
            default_currency=self.bank.default_currency,
            source_url=source_url,
            source_format=source_format,
            day_first=self.bank.day_first,
            amount_period_is_decimal=self.bank.amount_period_is_decimal,
            # Same self-reference clean-up the tabular path gets: without it a
            # portfolio page listing the bank itself as a cofinancier (e.g.
            # "AIIB") kept that entry as an external partner.
            bank_abbreviation=self.bank.abbreviation,
        )

    @staticmethod
    def _signal_score(record: ProjectRecord) -> int:
        return sum((
            bool(record.project_id),
            bool(record.country or record.country_code),
            bool(record.approval_date or record.commitment_date or record.commitment_year),
            bool(record.status),
            bool(record.sector or record.subsector),
            record.loan_amount is not None,
            record.total_commitment is not None,
            record.total_project_cost is not None,
            bool(record.project_url),
        ))

    @classmethod
    def _credible_structured_project(cls, record: ProjectRecord, source_url: str) -> bool:
        score = cls._signal_score(record)
        source_is_project_data = bool(
            re.search(r"project|operation|approval|loan|finance|portfolio", source_url, re.I)
        )
        return bool(record.project_name) and (score >= 2 or (score >= 1 and source_is_project_data))

    def _initial_content(self) -> tuple[str, str, list[tuple[str, bytes]]]:
        if self.options.source_file:
            source = self.load_source()
            if source.source_format.lower() not in {"html", "htm"}:
                rows = read_rows(source.data, source.source_format)
                # Encode structured local snapshots as captured pseudo-payloads.
                import json

                return "", source.url, [(source.url, json.dumps(rows, default=str).encode())]
            return source.data.decode("utf-8", errors="replace"), source.url, []
        if self.bank.dynamic:
            rendered = render_page(
                self.options.source_url or self.bank.source_url,
                headless=self.options.headless,
                timeout=self.options.timeout,
            )
            return rendered.html, rendered.url, [
                (payload.url, payload.body) for payload in rendered.json_payloads
            ]
        source = self.load_source(accept="text/html,application/xhtml+xml")
        return source.data.decode("utf-8", errors="replace"), source.url, []

    def _records_from_html_tables(self, html_text: str, source_url: str) -> list[ProjectRecord]:
        records: list[ProjectRecord] = []
        for table in parse_tables(html_text):
            for row in table_to_dicts(table):
                record = self.map_row(row, source_url, "html")
                if self._credible_structured_project(record, source_url):
                    records.append(record)
        return records

    def _records_from_payloads(self, payloads: list[tuple[str, bytes]]) -> list[ProjectRecord]:
        records: list[ProjectRecord] = []
        for payload_url, body in payloads:
            try:
                rows = json_rows(body)
            except Exception as exc:  # noqa: BLE001 -- captured page JSON is often unrelated (analytics etc.)
                LOG.debug("Skipping undecodable captured payload %s: %s", payload_url, exc)
                continue
            for row in rows:
                record = self.map_row(row, payload_url, "json")
                if self._credible_structured_project(record, payload_url):
                    records.append(record)
        return records

    def _listing_pages(self, initial_html: str, initial_url: str) -> list[tuple[str, str]]:
        pages: list[tuple[str, str]] = []
        if self.bank.dynamic or self.options.source_file:
            return [(initial_url, initial_html)]
        initial_host = urlparse(initial_url).netloc
        seen = {initial_url}
        queue = [(initial_url, initial_html)]
        page_cap = self.bank.max_listing_pages
        if self.options.max_projects:
            page_cap = min(page_cap, max(2, (self.options.max_projects + 9) // 10))
        while queue and len(pages) < page_cap:
            current_url, current_html = queue.pop(0)
            pages.append((current_url, current_html))
            links = extract_links(
                current_html, current_url, href_pattern=self.bank.pagination_href_pattern
            )
            for page_url, _ in links:
                if len(pages) + len(queue) >= page_cap:
                    break
                if urlparse(page_url).netloc != initial_host or page_url in seen:
                    continue
                response = self.client.get(page_url, accept="text/html")
                seen.add(page_url)
                queue.append((response.url, response.text()))
        return pages

    def _extract_detail_fields(self, detail_html: str) -> dict[str, Any]:
        """Extract labelled fields from one project's detail page.

        Default: the generic ``Label: value`` / two-column-table extractor.
        Override when a site's detail pages use a different shape (e.g. a
        label on its own line immediately followed by its value on the next
        line) that the generic extractor can't parse -- see
        ``extract_labelled_sequence()`` in ``parsers.py``.
        """
        return extract_label_values(detail_html)

    def _records_from_details(self, pages: list[tuple[str, str]]) -> list[ProjectRecord]:
        project_links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for page_url, html_text in pages:
            for href, label in extract_links(
                html_text, page_url, href_pattern=self.bank.project_href_pattern
            ):
                if href in seen or href.rstrip("/") == page_url.rstrip("/"):
                    continue
                if re.search(self.bank.pagination_href_pattern, href, re.I):
                    continue
                if not label or len(label) < 3:
                    continue
                seen.add(href)
                project_links.append((href, label))
                if self.options.max_projects and len(project_links) >= self.options.max_projects:
                    break
            if self.options.max_projects and len(project_links) >= self.options.max_projects:
                break
        records: list[ProjectRecord] = []
        for href, label in project_links:
            response = self.client.get(href, accept="text/html")
            detail_html = response.text()
            fields = self._extract_detail_fields(detail_html)
            generic_labels = {"read more", "view details", "view project", "details", "learn more"}
            heading = extract_heading(detail_html)
            project_name = heading or ("" if label.casefold() in generic_labels else label)
            fields.setdefault("project_name", project_name)
            fields.setdefault("project_url", response.url)
            record = self.map_row(fields, response.url, "html")
            if record.project_name:
                if len(fields) <= 2:
                    self.add_note(
                        record, "The detail page exposed no recognized labelled fields; inspect source_fields."
                    )
                records.append(record)
        return records

    def scrape(self) -> list[ProjectRecord]:
        html_text, source_url, payloads = self._initial_content()
        records = self._records_from_payloads(payloads)
        if html_text:
            records.extend(self._records_from_html_tables(html_text, source_url))
            if not records:
                pages = self._listing_pages(html_text, source_url)
                records.extend(self._records_from_details(pages))
        if not records:
            raise SourceLayoutChanged(
                f"{self.bank.abbreviation}: no recognizable projects were exposed by the official "
                "page. Save an official CSV/JSON export and rerun with --source-file BANK=PATH, "
                "or update the bank adapter after verifying the site's new layout."
            )
        if self.bank.dynamic and not self.options.source_file:
            for record in records:
                self.add_note(
                    record,
                    "This record came from a browser-rendered portal; verify the final project count against "
                    "the portal or an official full export because server-side pagination can limit loaded rows.",
                )
        return records
