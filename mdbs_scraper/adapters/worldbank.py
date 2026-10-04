"""IBRD and IDA adapters for the World Bank Projects search API (v3).

The v2 endpoint (search.worldbank.org/api/v2/projects) stopped updating on
2024-12-20 -- confirmed 2026-09-16: its newest board approval is that date and
a 2025+ date filter returns 0 projects -- and it also lists far fewer projects
per year than the Bank's own records (IDA 2014: 155 vs 251). The v3 endpoint
matches the "World Bank Projects" Excel export and the Finances One loan
records year by year, so it is the source now.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from ..base import BaseScraper, parse_common_dates
from ..cleaning import join_values, normalize_text, parse_amount, parse_date, parse_decimal
from ..dac_sectors import sector_category_from
from ..errors import SourceLayoutChanged
from ..iati_geography import iso3_country_code_from_name
from ..schema import ProjectRecord

LOG = logging.getLogger(__name__)

# The World Bank's separate "Finances One" financial-reporting platform --
# distinct from the search.worldbank.org Projects API above, and the only
# place IBRD/IDA disclose per-loan interest_rate/last_repayment_date
# (confirmed live: the Projects API's full ~37-key project dict has neither).
# A documented, public, unauthenticated SQL-over-HTTP endpoint; reverse-
# engineered by capturing what financesone.worldbank.org's own page calls.
_FINANCES_ONE_SQL_URL = "https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice/sql"
_FINANCES_ONE_PAGE_SIZE = 5000

_V3_PAGE_SIZE = 1000
# Only what _record() reads: fl=* also returns theme trees, ISR ratings and
# milestones, several times the payload for nothing.
_V3_FIELDS = ",".join((
    "id", "project_name", "countryshortname", "countryname", "boardapprovaldate", "closingdate",
    "status", "curr_ibrd_commitment", "curr_ida_commitment", "idacommamt", "curr_total_commitment",
    "curr_project_cost", "lendprojectcost", "grantamt", "totalamt", "financers", "major_sectors",
    "sector_name", "projectfinancialtype", "borrower", "impagency",
))
_PROJECT_URL = "https://projects.worldbank.org/en/projects-operations/project-detail/{}"
# A Dropped project was never approved and a Pipeline one not yet: the API
# still gives both a board approval date, but it is only a planned one.
_UNAPPROVED_STATUSES = {"dropped", "pipeline"}
# v3 ``financers`` ids for the Bank's own money, and the instrument each
# stands for (IBRD's entry is just the institution's name).
_OWN_FINANCER_INSTRUMENTS = {"IBRD": "IBRD Loan", "IDA": "IDA Credit", "IDAT": "IDA Grant"}


def _names(value: Any) -> str:
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                items.extend(item.values())
            else:
                items.append(item)
        return join_values(items)
    if isinstance(value, dict):
        return join_values(value.values())
    return normalize_text(value)


def _sector_name(value: Any) -> Any:
    """Extract a sector entry's name, whether it's a name dict or a bare string."""

    if isinstance(value, dict):
        return value.get("Name") or value.get("name") or value.get("sector")
    return value


def _sector(project: dict[str, Any]) -> str:
    values: list[Any] = []
    for key in ("sector1", "sector2", "sector3", "sector4", "sector5", "sector", "sector_namecode"):
        value = project.get(key)
        if isinstance(value, list):
            values.extend(name for item in value if (name := _sector_name(item)))
        elif isinstance(value, dict):
            # A dict with no name (e.g. {"Name": "", "Percent": 0}, the API's
            # own empty-sector placeholder) has nothing worth keeping --
            # unlike the list branch above, don't fall back to its raw values
            # (that's how a bare "0" from "Percent" used to leak in as if it
            # were a sector name).
            name = _sector_name(value)
            if name:
                values.append(name)
        elif value:
            values.append(value)
    return join_values(values)


def _v3_sectors(project: dict[str, Any]) -> str:
    """Sector names from v3's structured ``major_sectors`` list.

    ``sector_name`` is a comma-joined string whose names themselves contain
    commas ("Other - Industry, Trade, and Services"), so it cannot be split.
    The "FY17 - " prefix marks the taxonomy version, not part of the name.
    """

    names: list[str] = []
    for entry in project.get("major_sectors") or []:
        sectors = ((entry or {}).get("major_sector") or {}).get("sectors") or []
        for sector in sectors:
            name = normalize_text((sector or {}).get("sector_name"))
            if name:
                names.append(name.removeprefix("FY17 - "))
    return join_values(names)


def _fetch_loan_terms(client: Any, asset_id: str) -> dict[str, list[dict[str, Any]]]:
    """Fetch every loan/credit in a Finances One "latest snapshot" dataset.

    Returns a project_id -> [loan row, ...] lookup (a project can have
    several loans -- confirmed live: 1,613 of IBRD's 7,291 projects do).
    This is a bonus enrichment layered on the already-working Projects
    search API above; a fetch failure here is logged and returns an empty
    lookup rather than raising, so it never takes down the primary scrape.
    """

    lookup: dict[str, list[dict[str, Any]]] = {}
    offset = 0
    try:
        while True:
            body = json.dumps({
                "sql": (
                    "SELECT * FROM {0} WHERE 1=1 ORDER BY 1 OFFSET "
                    f"{offset} ROWS FETCH NEXT {_FINANCES_ONE_PAGE_SIZE} ROWS ONLY"
                ),
                "assetId": asset_id,
                "params": {
                    "startRow": offset,
                    "endRow": offset + _FINANCES_ONE_PAGE_SIZE,
                    "sortModel": [],
                    "filterModel": {},
                },
            })
            response = client.post(_FINANCES_ONE_SQL_URL, json_body=body, accept="application/json")
            payload = json.loads(response.text())
            rows = payload.get("data", [])
            for row in rows:
                project_id = normalize_text(row.get("project_id"))
                if project_id:
                    lookup.setdefault(project_id, []).append(row)
            if len(rows) < _FINANCES_ONE_PAGE_SIZE:
                break
            offset += _FINANCES_ONE_PAGE_SIZE
    except Exception as exc:  # noqa: BLE001 -- a bonus enrichment must not fail the primary scrape
        LOG.warning("Finances One loan-terms fetch failed for %s (interest_rate/last_repayment_date "
                    "will be blank this run): %s", asset_id, exc)
        return {}
    return lookup


class WorldBankScraper(BaseScraper):
    financing_key = ""
    financing_label = ""
    # Finances One dataset ID for this institution's loan/credit terms
    # ("latest available snapshot" variant -- already just-in-time, no
    # historical-snapshot filtering needed) and the raw column its rate
    # lives under (IBRD calls it interest_rate; IDA's concessional
    # equivalent is literally named service_charge_rate).
    asset_id = ""
    rate_field = ""
    # Finances One columns for the original approved principal and the
    # amount disbursed so far (both USD); IDA's carry a "_us_" suffix.
    principal_field = ""
    disbursed_field = ""
    # v3 amount fields for this arm, in order of preference. IBRD has only a
    # current (net of cancellations) commitment; IDA also has the original.
    amount_keys: tuple[str, ...] = ()
    # Ids this arm uses in v3's ``financers`` list.
    financer_ids: frozenset[str] = frozenset()
    # Unlike the banks whose windows have to be read out of an IATI feed, the
    # World Bank's split is the bank identity itself: this scraper filters the
    # shared projects API down to one institution's commitments, so every row
    # it emits is by definition that institution's. IDA is the concessional
    # window, IBRD is not.
    concessional = False

    def _payloads(self):
        if self.options.source_file:
            source = self.load_source(source_format="json")
            return [(source.url, json.loads(source.data.decode("utf-8-sig")))]
        offset = 0
        payloads = []
        while True:
            response = self.client.get(
                self.options.source_url or self.bank.source_url,
                params={"format": "json", "rows": _V3_PAGE_SIZE, "os": offset, "fl": _V3_FIELDS},
                accept="application/json",
            )
            payload = json.loads(response.text())
            payloads.append((self.options.source_url or self.bank.source_url, payload))
            projects = payload.get("projects", {})
            count = len(projects) if isinstance(projects, (dict, list)) else 0
            total = int(str(payload.get("total") or count))  # v3 sends it as a string
            offset += count
            if count == 0 or offset >= total:
                break
            if self.options.max_projects and offset >= self.options.max_projects * 4:
                # Over-fetch because IBRD and IDA records coexist in the endpoint.
                break
        return payloads

    def _arm_amount(self, project: dict[str, Any]) -> Decimal | None:
        """This arm's own commitment in the API record (v3 fields first, then v2's)."""

        amounts = [parse_amount(project.get(key), "USD")[0] for key in self.amount_keys + (self.financing_key,)]
        present = [amount for amount in amounts if amount is not None]
        return max(present) if present else None

    def _loan_amounts(
        self, project_id: str, loan_lookup: dict[str, list[dict[str, Any]]]
    ) -> tuple[Decimal | None, Decimal | None]:
        """Original approved principal and disbursed amount, summed over the project's loans."""

        loans = loan_lookup.get(project_id, [])
        if not loans:
            return None, None

        def total(field: str) -> Decimal | None:
            values = [parse_decimal(loan.get(field)) for loan in loans if loan.get(field) is not None]
            values = [value for value in values if value is not None]
            return sum(values, Decimal(0)) if values else None

        return total(self.principal_field), total(self.disbursed_field)

    def _loan_terms(
        self, project_id: str, loan_lookup: dict[str, list[dict[str, Any]]]
    ) -> tuple[str, Decimal | None, str, bool]:
        """Distinct interest_rate/last_repayment_date across a project's loan(s).

        A project can have multiple loans (confirmed live: 1,613 of IBRD's
        7,291 do) with different rates/dates -- all distinct values are
        disclosed, "; "-joined, rather than picking one and discarding the
        rest (per explicit choice, mirroring cofinancing_partners).
        """

        loans = loan_lookup.get(project_id, [])
        raw_rates = [loan.get(self.rate_field) for loan in loans if loan.get(self.rate_field) is not None]
        rates = join_values(raw_rates)
        distinct_rates = {parse_decimal(raw) for raw in raw_rates}
        rate_pct = next(iter(distinct_rates)) if len(distinct_rates) == 1 else None
        dates = join_values(
            parse_date(loan.get("last_repayment_date"), day_first=False)
            for loan in loans
            if loan.get("last_repayment_date")
        )
        return rates, rate_pct, dates, bool(loans)

    def _record(
        self,
        project: dict[str, Any],
        source_url: str,
        loan_lookup: dict[str, list[dict[str, Any]]] | None = None,
    ) -> ProjectRecord:
        project_id = normalize_text(project.get("id") or project.get("project_id"))
        loan_lookup = loan_lookup or {}
        api_amount = self._arm_amount(project)
        original_principal, disbursed = self._loan_amounts(project_id, loan_lookup)
        # Inclusion rests on this arm's own money: a positive commitment in
        # the API, or an actual loan/credit in Finances One (which also keeps
        # approved loans that were later fully cancelled).
        if not ((api_amount is not None and api_amount > 0) or original_principal):
            return ProjectRecord()
        status = normalize_text(project.get("status") or project.get("projectstatusdisplay"))
        if isinstance(project.get("status"), list):
            status = join_values(project.get("status"))
        unapproved = status.casefold() in _UNAPPROVED_STATUSES
        board_date = project.get("boardapprovaldate") or project.get("board_approval_date")
        loan_approval_dates = sorted(
            parse_date(loan.get("board_approval_date"), day_first=False)
            for loan in loan_lookup.get(project_id, []) if loan.get("board_approval_date")
        )
        board_date_from_finances_one = False
        if status.casefold() == "dropped" and loan_approval_dates:
            # Approved, then terminated: Finances One's board date is real.
            unapproved = False
            board_date = loan_approval_dates[0]
        elif not board_date and loan_approval_dates:
            # The Projects API doesn't always expose boardapprovaldate for an
            # otherwise-approved project (confirmed live, e.g. old archival
            # project P008921, status Closed) -- Finances One's own loan
            # record, already fetched for interest_rate/last_repayment_date,
            # carries the real board approval date.
            board_date = loan_approval_dates[0]
            board_date_from_finances_one = True
        if status.casefold() == "dropped" and not original_principal:
            # Never approved: the API's amount is only the planned one
            # (confirmed 2026-09-16: 1,140 such IBRD projects, none with a
            # Finances One loan). A Dropped project that does have a loan
            # record was approved and later terminated, and is kept.
            return ProjectRecord()
        project_cost, _ = parse_amount(project.get("lendprojectcost") or project.get("curr_project_cost"), "USD")
        approval, commitment, completion, year = parse_common_dates(
            "" if unapproved else board_date,
            "" if unapproved else board_date,
            project.get("closingdate") or project.get("closing_date"),
            day_first=False,
        )
        project_url = normalize_text(project.get("url"))
        if project_url and not project_url.startswith(("http://", "https://")):
            project_url = urljoin("https://projects.worldbank.org/", project_url)
        if not project_url and project_id:
            project_url = _PROJECT_URL.format(project_id)
        sector = _v3_sectors(project) or _sector(project)
        sector_category, sector_category_code = sector_category_from(sector, "", "")
        country = _names(project.get("countryshortname") or project.get("countryname"))
        country_code = iso3_country_code_from_name(country)
        interest_rate, interest_rate_pct, last_repayment_date, has_loan_terms = self._loan_terms(
            project_id, loan_lookup
        )
        financers = [entry for entry in project.get("financers") or [] if isinstance(entry, dict)]
        own_ids = [
            normalize_text(entry.get("fincr_id")) for entry in financers
            if normalize_text(entry.get("fincr_id")) in self.financer_ids
        ]
        instrument = normalize_text(project.get("lendinginstr") or project.get("lending_instrument"))
        if not instrument:
            instrument = join_values(_OWN_FINANCER_INSTRUMENTS[financer_id] for financer_id in own_ids)
        partners = [
            entry for entry in financers
            if normalize_text(entry.get("fincr_id")) not in set(_OWN_FINANCER_INSTRUMENTS) | {"BORR"}
            and normalize_text(entry.get("projectfinancialtype")) != "Local Contributor"
        ]
        cofinancing_amounts = [parse_amount(entry.get("fincr_usd_amt"), "USD")[0] for entry in partners]
        cofinancing_amount = (
            sum((amount for amount in cofinancing_amounts if amount is not None), Decimal(0))
            if any(amount is not None for amount in cofinancing_amounts) else None
        )
        notes = (
            "The Projects search API does not expose project conditionality; that field is "
            "intentionally blank. Commitment is institution-specific, not combined IBRD+IDA."
        )
        if original_principal is not None:
            loan_amount = original_principal
            notes = join_values([
                notes,
                "loan_amount is the original approved principal of this project's "
                f"{self.financing_label} loans/credits (Finances One); total_commitment is the "
                "current commitment after any cancellations.",
            ])
        else:
            loan_amount = api_amount
            notes = join_values([
                notes,
                f"No {self.financing_label} loan/credit record in Finances One; loan_amount is the "
                "Projects API's current commitment, which is net of any cancellations.",
            ])
        if unapproved:
            notes = join_values([
                notes,
                f"Status is {status}: the project was not (yet) approved, so the API's board "
                "approval date -- a planned date -- is not used as an approval date.",
            ])
        if board_date_from_finances_one:
            notes = join_values([
                notes,
                "approval_date recovered from Finances One's board_approval_date; the Projects "
                "API did not expose boardapprovaldate for this project.",
            ])
        if country and not country_code:
            notes = join_values([
                notes,
                f"No ISO-3 country code could be determined for \"{country}\" -- likely a "
                "multi-country/regional/institutional grouping rather than a single country; left "
                "blank rather than guessed.",
            ])
        if has_loan_terms and self.rate_field == "service_charge_rate":
            notes = join_values([
                notes,
                "interest_rate is IDA's service_charge_rate -- its own term for concessional "
                "lending's periodic charge, not a market interest rate.",
            ])
        if interest_rate and interest_rate_pct is None:
            notes = join_values([
                notes,
                "interest_rate_pct is blank because this project's loans carry more than one "
                "distinct rate -- see interest_rate for the full '; '-joined list rather than "
                "picking one.",
            ])
        record = ProjectRecord(
            project_id=project_id,
            project_name=normalize_text(project.get("project_name") or project.get("projectname")),
            country=country,
            country_code=country_code,
            approval_date=approval,
            commitment_date=commitment,
            commitment_year=year,
            completion_date=completion,
            status=status,
            sector=sector,
            sector_category=sector_category,
            sector_category_code=sector_category_code,
            # IBRD and IDA lend only to member governments or against a
            # government guarantee.
            loan_type="Sovereign",
            financing_instrument=instrument,
            funding_window=self.financing_label,
            concessional="Yes" if self.concessional else "No",
            concessional_flag=Decimal(1) if self.concessional else Decimal(0),
            total_project_cost=project_cost,
            project_cost_currency="USD" if project_cost is not None else "",
            total_project_cost_usd=project_cost,
            loan_amount=loan_amount,
            loan_currency="USD" if loan_amount is not None else "",
            loan_amount_usd=loan_amount,
            interest_rate=interest_rate,
            interest_rate_pct=interest_rate_pct,
            total_commitment=api_amount,
            commitment_currency="USD" if api_amount is not None else "",
            total_commitment_usd=api_amount,
            total_disbursement=disbursed,
            disbursement_currency="USD" if disbursed is not None else "",
            total_disbursement_usd=disbursed,
            last_repayment_date=last_repayment_date,
            cofinancing_partners=join_values(normalize_text(entry.get("fincrname")) for entry in partners),
            cofinancing_amount=cofinancing_amount,
            cofinancing_currency="USD" if cofinancing_amount is not None else "",
            cofinancing_amount_usd=cofinancing_amount,
            project_url=project_url,
            source_url=source_url,
            source_format="json",
            data_quality_notes=notes,
            source_fields=project,
        )
        return record

    def scrape(self) -> list[ProjectRecord]:
        loan_lookup: dict[str, list[dict[str, Any]]] = {}
        if self.asset_id and not self.options.source_file:
            loan_lookup = _fetch_loan_terms(self.client, self.asset_id)
        records: list[ProjectRecord] = []
        for source_url, payload in self._payloads():
            projects = payload.get("projects", {})
            iterable = projects.values() if isinstance(projects, dict) else projects
            for project in iterable if isinstance(iterable, (list, type({}.values()))) else []:
                if not isinstance(project, dict):
                    continue
                record = self._record(project, source_url, loan_lookup)
                if record.project_name:
                    records.append(record)
        if not records:
            raise SourceLayoutChanged(
                f"World Bank API returned no projects with {self.financing_label} commitments"
            )
        return records


class IBRDScraper(WorldBankScraper):
    financing_key = "ibrdcommamt"
    financing_label = "IBRD"
    asset_id = "DS00047"
    rate_field = "interest_rate"
    principal_field = "original_principal_amount"
    disbursed_field = "disbursed_amount"
    amount_keys = ("curr_ibrd_commitment",)
    financer_ids = frozenset({"IBRD"})


class IDAScraper(WorldBankScraper):
    financing_key = "idacommamt"
    financing_label = "IDA"
    asset_id = "DS00001"
    rate_field = "service_charge_rate"
    principal_field = "original_principal_amount_us_"
    disbursed_field = "disbursed_amount_us_"
    amount_keys = ("idacommamt", "curr_ida_commitment")
    financer_ids = frozenset({"IDA", "IDAT"})
    concessional = True
