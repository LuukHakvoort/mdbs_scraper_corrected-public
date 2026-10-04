"""Islamic Development Bank IATI Registry adapter.

IsDB's approvals data platform (data.isdb.org) only exposes a dashboard-
aggregate JSON payload, not a per-project list (see docs/PROBLEMS.md). IsDB
separately publishes its full project portfolio as IATI activity files
(covering IsDB proper and its ITFC trade-finance arm) through the IATI
Registry (CKAN publisher "isdb"), hosted on isdb.org without any
bot-protection -- a fully official, purpose-built open-data channel, used
here instead of the dashboard-only platform.
"""

from typing import Any

from ..base import LoadedSource
from ..cleaning import join_values
from ..schema import ProjectRecord
from .common import CkanOrganizationScraper


class IsDBScraper(CkanOrganizationScraper):
    # ITFC is IsDB's own trade-finance arm, already treated as part of this
    # same "isdb" bank rather than a separate one (see _package_note below).
    # Confirmed live: every one of its 1,287 activities lists both "Islamic
    # Development Bank" and "International Islamic Trade Finance Corporation"
    # as role="1" (Funding) participants with no variation -- IsDB disclosing
    # its own group entity, not a real external cofinancier, and with no
    # shared org `ref` to key off of the way IsDB's internal fund/window
    # self-references are (see generic_record_from_row's ref-based check).
    _SELF_REFERENCE_NAMES = {"international islamic trade finance corporation"}
    _ITFC_NOTE = (
        "This record is from IsDB's ITFC (trade-finance) arm's IATI file, which "
        "discloses transaction-level disbursements far less consistently than IsDB's "
        "own activities -- a missing total_disbursement here reflects that publisher-side "
        "gap, not necessarily an undisbursed loan."
    )

    def map_row(self, row: dict[str, Any], source: LoadedSource) -> ProjectRecord:
        record = super().map_row(row, source)
        if row.get("_source_note") == self._ITFC_NOTE:
            # ITFC extends short-term trade finance, not project lending; kept
            # under isdb but categorised so totals can separate it.
            record.instrument_category = "trade_finance"
        if record.cofinancing_partners:
            parts = [
                part for part in record.cofinancing_partners.split("; ")
                if part.casefold() not in self._SELF_REFERENCE_NAMES
            ]
            record.cofinancing_partners = join_values(parts)
        return record

    def _package_note(self, package: dict[str, Any]) -> str:
        # Confirmed by cross-referencing all 3 activity files directly: ITFC
        # activities have total_disbursement populated far less often than
        # IsDB's own (proper) activities, in every approval year including
        # 15+-year-old ones -- a structural publisher-side gap in ITFC's IATI
        # file, not a timing/"not yet disbursed" artifact.
        if "itfc" in str(package.get("name", "")).lower():
            return self._ITFC_NOTE
        return ""
