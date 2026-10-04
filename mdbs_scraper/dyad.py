"""Constant-2025-USD project amounts and bank-country-year totals.

Built from the combined project file (all_mdb_projects.csv):

1. all_mdb_projects_usd.csv -- every project row plus its amount in nominal
   USD and in constant 2025 USD. The original amount and currency are never
   overwritten.
   - A bank-reported USD figure is used as is ("source-usd").
   - Any other currency is converted at the IMF annual average rate of the
     approval year ("imf-annual-average").
   - Nominal USD is then multiplied by FRED's GDP deflator ratio
     D(2025) / D(approval year).
   - 2026 is not priced: neither series has a 2026 annual value yet.
   - The same nominal USD is also put in international dollars of the
     recipient country: divided by the World Bank price-level ratio (GDP in
     current US$ / GDP in current PPP international $) of the approval year,
     then multiplied by the same deflator ratio, giving constant 2025
     international $.
2. dyad_country_year.csv -- one row per bank, country (ISO-3) and approval
   year with at least one operation.
3. dyad_country_year_balanced.csv -- every bank x every country it lent to x
   2002-2026. Years inside the bank's coverage window with no operation are
   zeros; years outside it are left blank, never a claimed zero.
4. dyad_unallocated_year.csv -- bank-year totals of rows with no single
   country (regional, multi-country, unresolved), so the files add up to the
   project file.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .config import BANKS
from .instruments import INSTRUMENT_CATEGORIES
from .output import read_csv_rows, sha256, write_dict_rows
from .reference import BASE_YEAR, FRED_SERIES, WDI_GDP_PPP, WDI_GDP_USD, ReferenceData, load_currency_rules, load_reference
from .schema import utc_now

LOG = logging.getLogger(__name__)

CENT = Decimal("0.01")
USD_FIELDS = [
    "analysis_year", "amount_basis", "usd_nominal", "usd_conversion", "fx_units_per_usd",
    "deflator_factor_2025", "usd_constant_2025",
    "ppp_price_level_ratio", "ppp_intl_nominal", "ppp_intl_constant_2025",
    "price_basis_note",
]
# BADEA discloses financing totals per country/sector/year, not operations.
AGGREGATE_BANKS = {"badea"}
INSTRUMENT_BUCKETS = list(INSTRUMENT_CATEGORIES) + ["unspecified"]
CONCESSIONAL_BUCKETS = {"Yes": "yes", "No": "no", "Blended": "blended"}
PRIVATE_TOTALS_FILE = "cabei_private_approvals_country_year.csv"

KEY_FIELDS = ["bank_id", "bank_abbreviation", "country_code", "country", "year"]
MEASURE_FIELDS = (
    ["n_operations", "n_with_amount", "n_unconverted", "usd_nominal_total", "usd_2025_total",
     "n_ppp_unpriced", "ppp_2025_total"]
    + [f"n_{bucket}" for bucket in INSTRUMENT_BUCKETS]
    + [f"usd_2025_{bucket}" for bucket in INSTRUMENT_BUCKETS]
    + [f"usd_2025_concessional_{bucket}" for bucket in ("yes", "no", "blended", "unknown")]
    + ["cabei_private_n", "cabei_private_usd_nominal", "cabei_private_usd_2025", "cabei_private_ppp_2025"]
)
CABEI_PRIVATE_FIELDS = [name for name in MEASURE_FIELDS if name.startswith("cabei_private")]
DYAD_FIELDS = KEY_FIELDS + ["count_unit"] + MEASURE_FIELDS
BALANCED_FIELDS = KEY_FIELDS + ["observed", "in_coverage", "count_unit"] + MEASURE_FIELDS
UNALLOCATED_FIELDS = ["bank_id", "bank_abbreviation", "year", "countries", "count_unit"] + MEASURE_FIELDS


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _analysis_year(row: dict[str, str]) -> int | None:
    for key in ("approval_year", "commitment_year"):
        value = row.get(key) or ""
        if value.isdigit():
            return int(value)
    return None


def convert_row(row: dict[str, str], reference: ReferenceData) -> dict[str, Any]:
    """The USD_FIELDS for one project row."""

    year = _analysis_year(row)
    amount = _decimal(row.get("loan_amount"))
    currency = (row.get("loan_currency") or "").upper()
    reported_usd = _decimal(row.get("loan_amount_usd"))
    result: dict[str, Any] = {
        "analysis_year": year or "", "amount_basis": "loan_amount" if amount is not None else "",
        "usd_nominal": None, "usd_conversion": "", "fx_units_per_usd": None,
        "deflator_factor_2025": None, "usd_constant_2025": None,
        "ppp_price_level_ratio": None, "ppp_intl_nominal": None,
        "ppp_intl_constant_2025": None, "price_basis_note": "",
    }
    if amount is None:
        result["price_basis_note"] = "No bank amount disclosed."
        return result
    if year is None:
        result["price_basis_note"] = "No approval year, so no year-specific rate or deflator applies."
        return result
    notes = []
    if (reported_usd is not None or currency == "USD") and _currency_suspect(row, reported_usd or amount, year, reference):
        result["usd_conversion"] = "excluded-currency-suspect"
        result["price_basis_note"] = (
            f"Excluded from totals: {format(reported_usd or amount, 'f')} is labelled USD but, for a non-sovereign "
            f"operation in {row.get('country_code')}, is far more likely an amount in the local currency "
            "(the publisher's IATI record states no currency of its own)."
        )
        return result
    if reported_usd is not None:
        result["usd_nominal"], result["usd_conversion"] = reported_usd, "source-usd"
    elif currency == "USD":
        result["usd_nominal"], result["usd_conversion"] = amount, "source-usd"
    else:
        rate = reference.units_per_usd(currency, year)
        if rate:
            result["fx_units_per_usd"] = rate
            result["usd_nominal"] = (amount / rate).quantize(CENT)
            result["usd_conversion"] = "imf-annual-average"
            rule = reference.rules.get(currency)
            if rule and rule.pegged_to:
                notes.append(f"{currency} valued at 1 {rule.pegged_to}.")
        else:
            result["usd_conversion"] = "unconverted"
            notes.append(
                f"No IMF annual {currency or 'currency'} rate for {year}"
                + (" (2026 rates are not yet published)." if year > BASE_YEAR else ".")
            )
    factor = reference.deflator_factor(year)
    if factor is None:
        notes.append(
            f"Not priced in constant {BASE_YEAR} USD: FRED {FRED_SERIES} has no {year} value"
            + (" yet." if year > BASE_YEAR else ".")
        )
    elif result["usd_nominal"] is not None:
        result["deflator_factor_2025"] = factor.quantize(Decimal("1e-6"))
        result["usd_constant_2025"] = (result["usd_nominal"] * factor).quantize(CENT)
    if result["usd_nominal"] is not None:
        notes.extend(_ppp_prices(result, row.get("country_code") or "", year, factor, reference))
    result["price_basis_note"] = " ".join(notes)
    return result


def _ppp_prices(
    result: dict[str, Any], country_code: str, year: int, factor: Decimal | None, reference: ReferenceData
) -> list[str]:
    """Fill the PPP fields of ``result`` from its usd_nominal; return notes on what could not be priced."""

    if not country_code:
        return ["No single recipient country, so no PPP applies."]
    ratio = reference.price_level_ratio(country_code, year)
    if ratio is None:
        return [f"No World Bank PPP price level ({WDI_GDP_USD} / {WDI_GDP_PPP}) for {country_code} in {year}."]
    result["ppp_price_level_ratio"] = ratio.quantize(Decimal("1e-6"))
    result["ppp_intl_nominal"] = (result["usd_nominal"] / ratio).quantize(CENT)
    if factor is not None:
        result["ppp_intl_constant_2025"] = (result["usd_nominal"] * factor / ratio).quantize(CENT)
    return []


# A scraped (not official-file) non-sovereign operation above this, labelled
# USD, in a country whose currency trades at 10+ per dollar, is treated as a
# mislabelled local-currency amount. Confirmed 2026-09-16 in ADB's IATI files,
# which give e.g. Cimory (Indonesia, equity) 59,900,456,000 "USD" -- rupiah --
# and Arnur Credit (Kazakhstan) 2,432,200,000 "USD" -- tenge.
SUSPECT_USD_AMOUNT = Decimal(1_000_000_000)
SUSPECT_UNITS_PER_USD = Decimal(10)


def _currency_suspect(row: dict[str, str], amount: Decimal, year: int, reference: ReferenceData) -> bool:
    if row.get("official_source_id") or row.get("loan_type") != "Non-sovereign" or amount <= SUSPECT_USD_AMOUNT:
        return False
    local = reference.local_currency(row.get("country_code") or "")
    rate = reference.units_per_usd(local, year) if local else None
    return rate is not None and rate >= SUSPECT_UNITS_PER_USD


def _empty_measures() -> dict[str, Any]:
    measures: dict[str, Any] = {name: 0 for name in MEASURE_FIELDS if name.startswith("n_")}
    measures.update({name: Decimal(0) for name in MEASURE_FIELDS if not name.startswith("n_")})
    for name in CABEI_PRIVATE_FIELDS:
        measures[name] = None
    return measures


def _add(measures: dict[str, Any], row: dict[str, Any]) -> None:
    measures["n_operations"] += 1
    bucket = row.get("instrument_category") or "unspecified"
    measures[f"n_{bucket}"] += 1
    if row.get("amount_basis"):
        measures["n_with_amount"] += 1
    if row.get("usd_conversion") in {"unconverted", "excluded-currency-suspect"}:
        measures["n_unconverted"] += 1
    if row.get("usd_nominal") is not None:
        measures["usd_nominal_total"] += row["usd_nominal"]
    constant = row.get("usd_constant_2025")
    if constant is not None:
        measures["usd_2025_total"] += constant
        measures[f"usd_2025_{bucket}"] += constant
        concessional = CONCESSIONAL_BUCKETS.get(row.get("concessional") or "", "unknown")
        measures[f"usd_2025_concessional_{concessional}"] += constant
        if row.get("ppp_intl_constant_2025") is None:
            measures["n_ppp_unpriced"] += 1
        else:
            measures["ppp_2025_total"] += row["ppp_intl_constant_2025"]


def _coverage_window(bank_id: str, observed_years: set[int], min_year: int, max_year: int) -> range:
    bank = BANKS.get(bank_id)
    start = max(min_year, (bank.coverage_start_year if bank else None) or min_year)
    last_seen = max(observed_years) if observed_years else max_year
    end = min(max_year, (bank.coverage_end_year if bank else None) or last_seen)
    return range(start, end + 1)


def build_dyad_outputs(
    combined_path: Path,
    output_dir: Path,
    *,
    min_year: int = 2002,
    max_year: int = 2026,
    refresh_reference: bool = False,
) -> dict[str, Any]:
    """Write the four files and return their manifest entry."""

    _, rows = read_csv_rows(combined_path)
    currencies = {(row.get("loan_currency") or "").upper() for row in rows}
    rules = load_currency_rules()
    # Local currencies of countries with large non-sovereign "USD" amounts,
    # for the mislabelled-currency check.
    currencies |= {
        code for code, rule in rules.items()
        if rule.imf_area in {
            row.get("country_code") for row in rows
            if row.get("loan_type") == "Non-sovereign" and not row.get("official_source_id")
            and (_decimal(row.get("loan_amount")) or 0) > SUSPECT_USD_AMOUNT
        }
    }
    reference = load_reference(currencies, refresh=refresh_reference)

    converted = []
    for row in rows:
        converted.append({**row, **convert_row(row, reference)})
    project_fields = list(rows[0].keys()) + USD_FIELDS if rows else USD_FIELDS
    usd_path = write_dict_rows(output_dir / "all_mdb_projects_usd.csv", project_fields, converted)

    abbreviations = {row["bank_id"]: row.get("bank_abbreviation", "") for row in rows}
    observed: dict[tuple[str, str, int], dict[str, Any]] = {}
    names: dict[tuple[str, str], str] = {}
    unallocated: dict[tuple[str, int], dict[str, Any]] = {}
    unallocated_names: dict[tuple[str, int], set[str]] = defaultdict(set)
    in_scope_totals: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    in_scope_ppp: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    for row in converted:
        year = row["analysis_year"]
        if not isinstance(year, int) or not min_year <= year <= max_year:
            continue
        bank_id = row["bank_id"]
        if row.get("usd_constant_2025") is not None:
            in_scope_totals[(bank_id, year)] += row["usd_constant_2025"]
        if row.get("ppp_intl_constant_2025") is not None:
            in_scope_ppp[(bank_id, year)] += row["ppp_intl_constant_2025"]
        code = row.get("country_code") or ""
        if code:
            key = (bank_id, code, year)
            names.setdefault((bank_id, code), row.get("country") or "")
            _add(observed.setdefault(key, _empty_measures()), row)
        else:
            _add(unallocated.setdefault((bank_id, year), _empty_measures()), row)
            if row.get("country"):
                unallocated_names[(bank_id, year)].add(row["country"])

    private_rows = _private_totals(output_dir, reference, min_year, max_year)
    for (code, year), totals in private_rows.items():
        key = ("cabei", code, year)
        measures = observed.setdefault(key, _empty_measures())
        names.setdefault(("cabei", code), totals.pop("country"))
        measures.update(totals)
        abbreviations.setdefault("cabei", "CABEI")

    _check_totals(observed, unallocated, in_scope_totals, "usd_2025_total")
    _check_totals(observed, unallocated, in_scope_ppp, "ppp_2025_total")

    dyad_rows = []
    for (bank_id, code, year), measures in sorted(observed.items()):
        dyad_rows.append({
            "bank_id": bank_id, "bank_abbreviation": abbreviations.get(bank_id, ""),
            "country_code": code, "country": names.get((bank_id, code), ""), "year": year,
            "count_unit": "aggregate-buckets" if bank_id in AGGREGATE_BANKS else "operations",
            **measures,
        })
    dyad_path = write_dict_rows(output_dir / "dyad_country_year.csv", DYAD_FIELDS, dyad_rows)

    balanced_rows = []
    years_by_bank: dict[str, set[int]] = defaultdict(set)
    countries_by_bank: dict[str, set[str]] = defaultdict(set)
    for bank_id, code, year in observed:
        years_by_bank[bank_id].add(year)
        countries_by_bank[bank_id].add(code)
    for bank_id in sorted(countries_by_bank):
        window = _coverage_window(bank_id, years_by_bank[bank_id], min_year, max_year)
        for code in sorted(countries_by_bank[bank_id]):
            for year in range(min_year, max_year + 1):
                measures = observed.get((bank_id, code, year))
                covered = year in window
                row = {
                    "bank_id": bank_id, "bank_abbreviation": abbreviations.get(bank_id, ""),
                    "country_code": code, "country": names.get((bank_id, code), ""), "year": year,
                    "observed": int(measures is not None), "in_coverage": int(covered),
                    "count_unit": "aggregate-buckets" if bank_id in AGGREGATE_BANKS else "operations",
                }
                if measures is not None:
                    row.update(measures)
                elif covered:
                    row.update({name: value for name, value in _empty_measures().items()
                                if name not in CABEI_PRIVATE_FIELDS})
                balanced_rows.append(row)
    balanced_path = write_dict_rows(output_dir / "dyad_country_year_balanced.csv", BALANCED_FIELDS, balanced_rows)

    unallocated_rows = [
        {
            "bank_id": bank_id, "bank_abbreviation": abbreviations.get(bank_id, ""), "year": year,
            "countries": "; ".join(sorted(unallocated_names[(bank_id, year)])),
            "count_unit": "aggregate-buckets" if bank_id in AGGREGATE_BANKS else "operations",
            **measures,
        }
        for (bank_id, year), measures in sorted(unallocated.items())
    ]
    unallocated_path = write_dict_rows(output_dir / "dyad_unallocated_year.csv", UNALLOCATED_FIELDS, unallocated_rows)

    conversions: dict[str, int] = defaultdict(int)
    ppp_counts: dict[str, Any] = {"priced": 0, "no_country": 0, "no_factor": 0}
    ppp_missing: dict[str, int] = defaultdict(int)
    for row in converted:
        if row["amount_basis"]:
            conversions[row["usd_conversion"] or "no-year"] += 1
        if row.get("usd_nominal") is None:
            continue
        if row.get("ppp_intl_nominal") is not None:
            ppp_counts["priced"] += 1
        elif not row.get("country_code"):
            ppp_counts["no_country"] += 1
        else:
            ppp_counts["no_factor"] += 1
            ppp_missing[row["country_code"]] += 1
    ppp_counts["no_factor_by_country"] = dict(sorted(ppp_missing.items(), key=lambda item: (-item[1], item[0])))
    entry = {
        "built_at": utc_now(),
        "base_year": BASE_YEAR,
        "scope": {"min_year": min_year, "max_year": max_year},
        "inputs": {
            "combined": {"path": str(combined_path), "sha256": sha256(combined_path)},
            "reference": reference.provenance,
            "cabei_private_totals": bool(private_rows),
        },
        "outputs": {
            name: {"path": str(path), "sha256": sha256(path)}
            for name, path in (("projects_usd", usd_path), ("dyad", dyad_path),
                               ("dyad_balanced", balanced_path), ("dyad_unallocated", unallocated_path))
        },
        "rows": {"projects": len(converted), "dyad": len(dyad_rows), "dyad_balanced": len(balanced_rows),
                 "dyad_unallocated": len(unallocated_rows)},
        "amount_conversions": dict(conversions),
        # Rows with a nominal USD amount, by whether they could also be put in international $.
        "ppp": ppp_counts,
        # For eyeballing: a parsing slip (e.g. thousands vs decimal) shows up
        # here long before it is noticed in a country total.
        "largest_operations_usd_2025": [
            {key: str(row.get(key, "")) for key in
             ("bank_id", "project_id", "project_name", "country_code", "analysis_year",
              "loan_amount", "loan_currency", "usd_constant_2025")}
            for row in sorted(
                (row for row in converted if row.get("usd_constant_2025") is not None),
                key=lambda row: row["usd_constant_2025"], reverse=True,
            )[:10]
        ],
    }
    (output_dir / "dyad_manifest.json").write_text(
        json.dumps(entry, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return entry


def _private_totals(
    output_dir: Path, reference: ReferenceData, min_year: int, max_year: int
) -> dict[tuple[str, int], dict[str, Any]]:
    path = output_dir / PRIVATE_TOTALS_FILE
    if not path.is_file():
        return {}
    totals: dict[tuple[str, int], dict[str, Any]] = {}
    for row in read_csv_rows(path)[1]:
        code, year_text = row.get("country_code") or "", row.get("year") or ""
        if not code or not year_text.isdigit() or not min_year <= int(year_text) <= max_year:
            continue
        year = int(year_text)
        amount = _decimal(row.get("gross_amount_usd"))
        factor = reference.deflator_factor(year)
        ratio = reference.price_level_ratio(code, year)
        entry = totals.setdefault((code, year), {
            "country": row.get("country", ""), "cabei_private_n": 0,
            "cabei_private_usd_nominal": Decimal(0), "cabei_private_usd_2025": Decimal(0),
            "cabei_private_ppp_2025": Decimal(0),
        })
        entry["cabei_private_n"] += int(row.get("approvals_count") or 0)
        if amount is not None:
            entry["cabei_private_usd_nominal"] += amount
            if factor is None:
                entry["cabei_private_usd_2025"] = None  # not priced (no deflator for this year)
            elif entry["cabei_private_usd_2025"] is not None:
                entry["cabei_private_usd_2025"] += (amount * factor).quantize(CENT)
            if factor is None or ratio is None:
                entry["cabei_private_ppp_2025"] = None  # not priced (no deflator or PPP for this year)
            elif entry["cabei_private_ppp_2025"] is not None:
                entry["cabei_private_ppp_2025"] += (amount * factor / ratio).quantize(CENT)
    return totals


def _check_totals(observed, unallocated, in_scope_totals, measure: str) -> None:
    """The dyad and unallocated files must add up to the project rows, bank-year by bank-year."""

    sums: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    for (bank_id, _, year), measures in observed.items():
        sums[(bank_id, year)] += measures[measure]
    for (bank_id, year), measures in unallocated.items():
        sums[(bank_id, year)] += measures[measure]
    for key in set(sums) | set(in_scope_totals):
        if sums.get(key, Decimal(0)) != in_scope_totals.get(key, Decimal(0)):
            raise AssertionError(
                f"Country-year {measure} for {key} ({sums.get(key)}) does not add up to the project rows "
                f"({in_scope_totals.get(key)})"
            )
