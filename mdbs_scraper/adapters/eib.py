"""European Investment Bank project-list adapter.

EIB's IATI Registry activity file ("eib-act", 1,395 activities -- the
previous source here) is a thin slice of EIB's real portfolio, not a
representative export of it. Confirmed live (2026-09-03): EIB's own website
is reachable again (the Cloudflare block documented 2026-08-25 that forced
the original move to IATI is no longer in effect, at least for now -- this
session saw several other bank domains flip between blocked/reachable the
same way) and exposes its real project database through a plain,
unauthenticated JSON API its own "All projects" page calls
(`page-provider/projects/list`), reporting on the order of 17,000 projects
with no filter applied at all -- roughly matching an independent user-
reported count of 18,052 for the same 2002-2026 window this project
otherwise uses, and a ~12x jump from the old IATI-derived count. This
adapter queries that API directly instead; no separate detail-page walk is
needed since the per-project detail endpoint returns the identical shape.

Trade-off, accepted deliberately: this source carries no IATI-shaped
<participating-org>/<loan-terms> disclosure, so `cofinancing_partners` and
`interest_rate`/`last_repayment_date` (all previously populated for EIB via
the IATI file) are not available here and are intentionally left blank,
with a note. 12x more real projects is the clear right trade.
"""

from __future__ import annotations

import json
from typing import Any

from ..base import BaseScraper, parse_common_dates
from ..cleaning import join_values, normalize_text, parse_amount
from ..dac_sectors import sector_category_from
from ..errors import SourceLayoutChanged
from ..iati_geography import iso3_country_code_from_name
from ..schema import ProjectRecord

_PAGE_SIZE = 1000


def _tag(item: dict[str, Any], sub_type: str) -> str:
    for tag in item.get("primaryTags") or []:
        if tag.get("subType") == sub_type:
            return normalize_text(tag.get("label"))
    return ""


def _amount_text(value: Any) -> Any:
    # This API returns amounts as real JSON numbers, not formatted strings
    # like most other sources' downloads -- Python's default float-to-str
    # keeps a trailing ".0" even for whole-euro amounts (the only kind seen
    # live), which parse_amount() would otherwise preserve verbatim.
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


class EIBScraper(BaseScraper):
    def _pages(self, source_url: str):
        if self.options.source_file:
            source = self.load_source(source_format="json")
            payload = json.loads(source.data.decode("utf-8-sig"))
            yield payload, payload.get("data") or []
            return
        page = 0
        while True:
            response = self.client.get(
                source_url,
                params={
                    "sortColumn": "statusDate",
                    "sortDir": "desc",
                    "pageNumber": page,
                    "itemPerPage": _PAGE_SIZE,
                    "pageable": "true",
                    "language": "EN",
                    "defaultLanguage": "EN",
                },
                accept="application/json",
            )
            payload = json.loads(response.text())
            items = payload.get("data") or []
            yield payload, items
            if len(items) < _PAGE_SIZE:
                break
            page += 1

    def _record(self, item: dict[str, Any], source_url: str) -> ProjectRecord:
        # additionalInformation is a positional [status, statusDate,
        # approvedAmount, signedAmount] tuple -- confirmed live across many
        # samples: signedAmount is 0 while only Approved, and populated (up
        # to, but not always equal to, the approved amount -- e.g. a real
        # partially-signed multi-tranche facility: 150,000,000 approved /
        # 65,000,000 signed) once Signed. approvedAmount itself is also
        # zeroed out by EIB's API once a project is signed, so loan_amount
        # falls back to signed_amount in that case rather than reporting 0.
        info = item.get("additionalInformation") or []
        status = normalize_text(info[0]) if len(info) > 0 else ""
        status_date = info[1] if len(info) > 1 else ""
        approval, commitment, _, year = parse_common_dates(
            status_date, status_date, "", day_first=True
        )
        approved_amount, _ = parse_amount(_amount_text(info[2] if len(info) > 2 else None), "EUR")
        signed_amount, _ = parse_amount(_amount_text(info[3] if len(info) > 3 else None), "EUR")
        loan_amount = approved_amount if approved_amount else (signed_amount if signed_amount else None)
        country = _tag(item, "countries")
        country_code = iso3_country_code_from_name(country)
        sector = _tag(item, "sectors")
        sector_category, sector_category_code = sector_category_from(sector, "", "")
        project_id = normalize_text(item.get("id"))
        notes = (
            "This bank's data comes from its own website's project-list API, not IATI -- "
            "cofinancing_partners and interest_rate/last_repayment_date are not disclosed by "
            "this source and are intentionally blank."
        )
        if country and not country_code:
            notes = join_values([
                notes,
                f"No ISO-3 country code could be determined for \"{country}\" -- likely a "
                "multi-country/regional/institutional grouping rather than a single country; left "
                "blank rather than guessed.",
            ])
        return ProjectRecord(
            project_id=project_id,
            project_name=normalize_text(item.get("title")),
            country=country,
            country_code=country_code,
            approval_date=approval,
            commitment_date=commitment,
            commitment_year=year,
            status=status,
            sector=sector,
            sector_category=sector_category,
            sector_category_code=sector_category_code,
            loan_amount=loan_amount,
            loan_currency="EUR" if loan_amount is not None else "",
            loan_amount_usd=None,
            total_commitment=signed_amount if signed_amount else None,
            commitment_currency="EUR" if signed_amount else "",
            project_url=f"https://www.eib.org/en/projects/all/{project_id}" if project_id else "",
            source_url=source_url,
            source_format="json",
            data_quality_notes=notes,
            source_fields=item,
        )

    def scrape(self) -> list[ProjectRecord]:
        source_url = self.options.source_url or self.bank.source_url
        records: list[ProjectRecord] = []
        seen_ids: set[str] = set()
        total_items = None
        for payload, items in self._pages(source_url):
            if total_items is None:
                total_items = payload.get("totalItems")
                self.run_metadata["source_total"] = total_items
            for item in items:
                project_id = normalize_text(item.get("id"))
                if not project_id or project_id in seen_ids:
                    continue
                seen_ids.add(project_id)
                record = self._record(item, source_url)
                if record.project_name:
                    records.append(record)
                if self.options.max_projects and len(records) >= self.options.max_projects:
                    break
            if self.options.max_projects and len(records) >= self.options.max_projects:
                break
        if not records:
            raise SourceLayoutChanged(
                f"{self.bank.abbreviation}: project-list API returned no recognizable projects"
            )
        return records
