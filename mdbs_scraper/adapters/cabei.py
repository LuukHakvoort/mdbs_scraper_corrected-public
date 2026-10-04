"""CABEI/BCIE CKAN and IATI resource adapter.

The "iati" package's project list (actividades-web.csv, 167 operations)
covers almost nothing before 2015 and misses many later public-sector loans
too (e.g. Costa Rica's 2020 USD 300m COVID-19 policy loan). CABEI's separate
"prestamos" package lists every public-sector loan approval since 1962 --
country, year, description and gross USD amount, but no project id -- and
private-sector approvals only as country-year totals. Loans from the first
are added where the project list has no matching operation; the totals are
written as their own country-year dataset, never mixed into project rows.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import defaultdict
from decimal import Decimal
from typing import Any

from ..base import LoadedSource
from ..cleaning import extract_year, normalize_loan_type, normalize_text, parse_decimal
from ..errors import SourceLayoutChanged
from ..iati_geography import iso3_country_code_from_name
from ..schema import ProjectRecord
from ..tabular import infer_format, normalized_row, read_rows
from .common import TabularScraper

PRESTAMOS_PACKAGE_URL = "https://datosabiertos.bcie.org/api/3/action/package_show?id=prestamos"
_PUBLIC_APPROVALS_RESOURCE = "aprobaciones-prestamos-sector-publico"
_ALL_APPROVALS_RESOURCE = "aprobaciones-prestamos"
PRIVATE_TOTALS_OUTPUT = "cabei_private_approvals_country_year"
PRIVATE_TOTALS_FIELDS = [
    "bank_id", "country", "country_code", "year", "sector_institucional",
    "approvals_count", "gross_amount_usd", "source_url",
]
_AMOUNT_TOLERANCE = Decimal("0.01")
_NAME_MATCH_RATIO = 0.8


def _name_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize_text(text).casefold()).strip()


class CABEIScraper(TabularScraper):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.supplementary_outputs: dict[str, dict[str, Any]] = {}

    def rows_from_source(self, source: LoadedSource):
        # A direct local CSV/XML snapshot bypasses CKAN resource discovery.
        if source.source_format.lower() not in {"json", "jsonld"}:
            return read_rows(source.data, source.source_format)
        try:
            payload = json.loads(source.data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return super().rows_from_source(source)
        resources = payload.get("result", {}).get("resources", [])
        if not resources:
            return super().rows_from_source(source)
        ranked = sorted(
            resources,
            key=lambda resource: (
                str(resource.get("format", "")).lower() == "csv",
                str(resource.get("format", "")).lower() in {"xml", "iati"},
            ),
            reverse=True,
        )
        for resource in ranked:
            url = resource.get("url")
            if not url:
                continue
            fmt = str(resource.get("format") or infer_format(url)).lower()
            if fmt not in {"csv", "xml", "iati", "json", "xlsx"}:
                continue
            response = self.client.get(url)
            rows = read_rows(response.body, fmt)
            if rows:
                return rows
        raise SourceLayoutChanged("CABEI CKAN package contains no readable project resource")

    def scrape(self) -> list[ProjectRecord]:
        records = super().scrape()
        if self.options.source_file or self.options.source_url:
            return records
        resources = self._prestamos_resources()
        public_url = resources.get(_PUBLIC_APPROVALS_RESOURCE, "")
        all_url = resources.get(_ALL_APPROVALS_RESOURCE, "")
        if not public_url or not all_url:
            raise SourceLayoutChanged(
                "CABEI 'prestamos' package no longer lists its approvals resources "
                f"({_PUBLIC_APPROVALS_RESOURCE}, {_ALL_APPROVALS_RESOURCE})"
            )
        public_rows = read_rows(self.client.get(public_url).body, "csv")
        records.extend(self.public_loans_not_in(records, public_rows, public_url))
        all_rows = read_rows(self.client.get(all_url).body, "csv")
        self.supplementary_outputs[PRIVATE_TOTALS_OUTPUT] = {
            "fields": PRIVATE_TOTALS_FIELDS,
            "rows": self.private_totals(all_rows, all_url),
        }
        return records

    def _prestamos_resources(self) -> dict[str, str]:
        payload = json.loads(self.client.get(PRESTAMOS_PACKAGE_URL, accept="application/json").text())
        found: dict[str, str] = {}
        for resource in payload.get("result", {}).get("resources", []):
            url = str(resource.get("url") or "")
            name = url.rsplit("/", 1)[-1].removesuffix(".csv").lower()
            if name:
                found[name] = url
        return found

    def public_loans_not_in(
        self, records: list[ProjectRecord], rows: list[dict[str, Any]], source_url: str
    ) -> list[ProjectRecord]:
        """Public-sector approvals with no matching operation in ``records``.

        A match is the same country and approval year plus either an amount
        within 1% or a near-identical name (a later amount change). Each
        existing operation can absorb at most one approval.
        """

        existing: dict[tuple[str, int | None], list[ProjectRecord]] = defaultdict(list)
        for record in records:
            year = extract_year(record.approval_date, record.commitment_date)
            existing[(iso3_country_code_from_name(record.country), year)].append(record)
        used: set[int] = set()
        added: list[ProjectRecord] = []
        occurrences: dict[str, int] = defaultdict(int)
        matched = 0
        for raw in rows:
            row = normalized_row(raw)
            country = normalize_text(row.get("pais"))
            description = normalize_text(row.get("descripcion_proyecto"))
            year = extract_year(row.get("anio_aprobacion"))
            amount = parse_decimal(row.get("monto_bruto_usd"), period_is_decimal=True)
            if not description or year is None:
                continue
            country_code = iso3_country_code_from_name(country)
            match = self._matching_record(existing[(country_code, year)], description, amount, used)
            if match is not None:
                used.add(id(match))
                matched += 1
                continue
            fingerprint = "|".join((country, str(year), description, format(amount or 0, "f")))
            base_id = "bcie-prestamo-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]
            occurrences[base_id] += 1
            project_id = base_id if occurrences[base_id] == 1 else f"{base_id}-{occurrences[base_id]}"
            record = ProjectRecord(
                project_id=project_id,
                project_name=description,
                country=country,
                country_code=country_code,
                approval_year=year,
                commitment_year=year,
                loan_type=normalize_loan_type(row.get("sector_institucional")),
                financing_instrument="Public-sector loan",
                instrument_category="loan",
                loan_amount=amount,
                loan_currency="USD" if amount is not None else "",
                loan_amount_usd=amount,
                total_commitment=amount,
                commitment_currency="USD" if amount is not None else "",
                total_commitment_usd=amount,
                source_url=source_url,
                source_format="csv",
                data_quality_notes=(
                    "From CABEI's public-sector loan approvals list (open-data package 'prestamos'), "
                    "which discloses only the approval year, a description and the gross approved "
                    "amount; the operation is not in CABEI's project list. An increase to an "
                    "existing loan is listed as its own approval."
                ),
                source_fields=dict(raw),
            )
            if not country_code and country:
                record.data_quality_notes += (
                    f" No ISO-3 country code could be determined for \"{country}\"; left blank."
                )
            added.append(record)
        self.run_metadata["prestamos_public_rows"] = len(rows)
        self.run_metadata["prestamos_matched_to_project_list"] = matched
        self.run_metadata["prestamos_rows_added"] = len(added)
        return added

    @staticmethod
    def _matching_record(
        candidates: list[ProjectRecord], description: str, amount: Decimal | None, used: set[int]
    ) -> ProjectRecord | None:
        free = [record for record in candidates if id(record) not in used]
        if amount:
            for record in free:
                if record.loan_amount is not None and abs(record.loan_amount - amount) <= amount * _AMOUNT_TOLERANCE:
                    return record
        wanted = _name_key(description)
        best, best_ratio = None, 0.0
        for record in free:
            ratio = difflib.SequenceMatcher(None, wanted, _name_key(record.project_name)).ratio()
            if ratio > best_ratio:
                best, best_ratio = record, ratio
        return best if best_ratio >= _NAME_MATCH_RATIO else None

    def private_totals(self, rows: list[dict[str, Any]], source_url: str) -> list[dict[str, Any]]:
        """Private-sector approvals as disclosed: counts and gross USD per country-year."""

        totals = []
        for raw in rows:
            row = normalized_row(raw)
            sector = normalize_text(row.get("sector_institucional"))
            if normalize_loan_type(sector) != "Non-sovereign":
                continue
            year = extract_year(row.get("anio_aprobacion"))
            if year is None:
                continue
            if self.options.min_year is not None and year < self.options.min_year:
                continue
            if self.options.max_year is not None and year > self.options.max_year:
                continue
            country = normalize_text(row.get("pais"))
            count = parse_decimal(row.get("cantidad_aprobaciones"), period_is_decimal=True)
            totals.append({
                "bank_id": self.bank.id,
                "country": country,
                "country_code": iso3_country_code_from_name(country),
                "year": year,
                "sector_institucional": sector,
                "approvals_count": int(count) if count is not None else "",
                "gross_amount_usd": parse_decimal(row.get("monto_bruto_usd"), period_is_decimal=True),
                "source_url": source_url,
            })
        totals.sort(key=lambda row: (row["country_code"], row["year"]))
        return totals
