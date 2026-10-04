"""BADEA interactive-map adapter (Tableau Public embed).

BADEA's public project map (https://www.badea.org/interactive-map/) renders
entirely through an embedded Tableau Public dashboard -- there is no plain
HTML/JSON project listing and no IATI Registry presence to fall back on.
The dashboard's *initial* ``bootstrapSession`` response, captured before any
user interaction, already contains the full per-record breakdown (country,
sector classification, approval year, committed-loan amount) for every
country the map plots -- decoding it is a matter of walking Tableau's own
vizql payload shape, not an incremental crawl of one country at a time.

The vizql session/data-dictionary protocol is undocumented and specific to
this one dashboard, so the decoding logic below is kept local to this module
rather than added to ``browser.py`` as shared infrastructure.

Confirmed, source-side limitations (not something a smarter decode would
recover): the dashboard exposes financial totals bucketed by
(country/beneficiary, sector classification, approval year) -- it never
discloses an individual project name or ID, and amounts are only available
at one-decimal-place (millions of USD) precision, the same precision BADEA's
own map tooltips show.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..browser import launch_chromium
from ..http import build_user_agent
from ..errors import OptionalDependencyMissing, SourceError, SourceLayoutChanged
from ..schema import ProjectRecord
from .common import HTMLPortfolioScraper

_BOOTSTRAP_URL_FRAGMENT = "bootstrapSession"
_PIE_WORKSHEET = "pie"
_MAP_WORKSHEET = "map"
_NULL_MARKERS = {"", "%null%", "none", "null", "n/a"}


def _clean(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.casefold() in _NULL_MARKERS else text


def _parse_tableau_segments(text: str) -> list[dict[str, Any]]:
    """Split a Tableau vizql response body into its length-prefixed JSON segments.

    Each segment is framed as ``"<length>;<json>"``, but the length is a
    JS-string (UTF-16 code unit) count, not a UTF-8 byte count: it drifts out
    of sync with a byte offset the moment a country name has an accented
    character (e.g. "Sao Tome", "Cote d'Ivoire"), corrupting a byte-sliced
    parse partway through. Scanning past the digits and decoding the next
    complete JSON value length-agnostically avoids the mismatch entirely.
    """

    decoder = json.JSONDecoder()
    segments = []
    pos = 0
    length = len(text)
    while pos < length:
        match = re.match(r"\d+;", text[pos:])
        if not match:
            break
        pos += match.end()
        try:
            obj, end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError as exc:
            raise SourceLayoutChanged(
                f"BADEA: could not parse a Tableau vizql response segment: {exc}"
            ) from exc
        segments.append(obj)
        pos = end
    return segments


def _on_data_value(index: int, pool: list[Any], cstring: list[Any]) -> Any:
    return pool[index] if index >= 0 else cstring[abs(index) - 1]


def _data_pools(pres_model_map: dict[str, Any]) -> dict[str, list[Any]]:
    try:
        data_segments = pres_model_map["dataDictionary"]["presModelHolder"][
            "genDataDictionaryPresModel"
        ]["dataSegments"]
    except (KeyError, TypeError) as exc:
        raise SourceLayoutChanged("BADEA: Tableau response is missing its data dictionary") from exc
    pools: dict[str, list[Any]] = {}
    for segment in data_segments.values():
        for column in segment.get("dataColumns", []):
            pools.setdefault(column["dataType"], []).extend(column["dataValues"])
    return pools


def _worksheet_columns(pres_model_map: dict[str, Any], worksheet: str) -> list[dict[str, Any]]:
    try:
        worksheets = pres_model_map["vizData"]["presModelHolder"]["genPresModelMapPresModel"][
            "presModelMap"
        ]
        columns_data = worksheets[worksheet]["presModelHolder"]["genVizDataPresModel"][
            "paneColumnsData"
        ]
    except (KeyError, TypeError) as exc:
        raise SourceLayoutChanged(
            f"BADEA: Tableau worksheet '{worksheet}' is missing its expected vizData shape"
        ) from exc
    columns = []
    for field in columns_data.get("vizDataColumns", []):
        caption = field.get("fieldCaption")
        if not caption:
            continue
        for pane_index, column_index in zip(field["paneIndices"], field["columnIndices"]):
            pane_column = columns_data["paneColumnsList"][pane_index]["vizPaneColumns"][column_index]
            columns.append({
                "field": caption,
                "data_type": field.get("dataType", ""),
                "value_indices": pane_column.get("valueIndices", []),
                "alias_indices": pane_column.get("aliasIndices", []),
            })
    return columns


def _decode(pools: dict[str, list[Any]], data_type: str, indices: list[int]) -> list[Any]:
    cstring = pools.get("cstring", [])
    pool = pools.get(data_type, cstring)
    return [_on_data_value(index, pool, cstring) for index in indices]


def _country_coordinates(pres_model_map: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """The map worksheet's own per-country marker position (raw, unrounded)."""

    pools = _data_pools(pres_model_map)
    columns = {column["field"]: column for column in _worksheet_columns(pres_model_map, _MAP_WORKSHEET)}
    required = {"Beneficiary", "Latitude (generated)", "Longitude (generated)"}
    if not required.issubset(columns):
        return {}
    countries = _decode(pools, columns["Beneficiary"]["data_type"], columns["Beneficiary"]["alias_indices"])
    lats = _decode(
        pools, columns["Latitude (generated)"]["data_type"], columns["Latitude (generated)"]["value_indices"]
    )
    lons = _decode(
        pools, columns["Longitude (generated)"]["data_type"], columns["Longitude (generated)"]["value_indices"]
    )
    return {
        country: (lat, lon)
        for country, lat, lon in zip(countries, lats, lons)
    }


def _pie_rows(pres_model_map: dict[str, Any]) -> list[dict[str, Any]]:
    pools = _data_pools(pres_model_map)
    columns = {column["field"]: column for column in _worksheet_columns(pres_model_map, _PIE_WORKSHEET)}
    if "Beneficiary" not in columns:
        raise SourceLayoutChanged(
            "BADEA: the 'pie' worksheet did not expose a Beneficiary/country column"
        )
    countries = _decode(pools, columns["Beneficiary"]["data_type"], columns["Beneficiary"]["alias_indices"])
    n = len(countries)

    def field(name: str) -> list[Any]:
        column = columns.get(name)
        if not column:
            return [None] * n
        return _decode(pools, column["data_type"], column["alias_indices"])

    years = field("MIN(Approval Year)")
    classifications = field("Classification")
    amounts = field("SUM(Commited Loans (in M$))")
    return [
        {
            "country": countries[i],
            "year": years[i],
            "classification": classifications[i],
            "amount": amounts[i],
        }
        for i in range(n)
    ]


def _extract_rows(raw_response: bytes) -> list[dict[str, Any]]:
    segments = _parse_tableau_segments(raw_response.decode("utf-8"))
    secondary = next((segment for segment in segments if "secondaryInfo" in segment), None)
    if secondary is None:
        raise SourceLayoutChanged(
            "BADEA: no Tableau vizql segment carried secondaryInfo.presModelMap "
            "(the bootstrap response shape may have changed)"
        )
    pres_model_map = secondary["secondaryInfo"]["presModelMap"]
    coordinates = _country_coordinates(pres_model_map)
    rows = _pie_rows(pres_model_map)
    for row in rows:
        row["coordinates"] = coordinates.get(row["country"])
    return rows


def _fetch_bootstrap_body(url: str, *, headless: bool, timeout: float) -> bytes:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise OptionalDependencyMissing(
            "BADEA's interactive map requires Playwright. Run: python -m pip install '.[browser]' "
            "and then: playwright install chromium"
        ) from exc

    captured: list[bytes] = []
    try:
        with sync_playwright() as playwright:
            browser = launch_chromium(playwright, headless=headless)
            context = browser.new_context(
                locale="en-US",
                user_agent=build_user_agent(),
            )
            page = context.new_page()
            page.set_default_timeout(int(timeout * 1000))

            def retain(response) -> None:
                if _BOOTSTRAP_URL_FRAGMENT in response.url:
                    try:
                        captured.append(response.body())
                    except PlaywrightError:
                        pass

            page.on("response", retain)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            context.close()
            browser.close()
    except PlaywrightError as exc:
        raise SourceError(f"Browser rendering failed for BADEA's interactive map at {url}: {exc}") from exc
    if not captured:
        raise SourceLayoutChanged(
            "BADEA: no Tableau bootstrapSession response was observed -- the embed may have changed"
        )
    return captured[-1]


def _project_name(country: str, classification: str, year: str) -> str:
    descriptor = classification or "financing"
    if year:
        return f"{country} — {descriptor} ({year})"
    return f"{country} — {descriptor}"


class BADEAScraper(HTMLPortfolioScraper):
    def _row_fields(self, row: dict[str, Any]) -> dict[str, Any]:
        country = row["country"]
        classification = _clean(row["classification"])
        year = _clean(row["year"])
        amount = _clean(row["amount"])
        fields: dict[str, Any] = {
            "country": country,
            "project_name": _project_name(country, classification, year),
        }
        if classification:
            fields["sector"] = classification
        if year:
            fields["approval_date"] = year
        if amount:
            fields["loan_amount"] = f"{amount} million"
            fields["loan_currency"] = "USD"
        coordinates = row.get("coordinates")
        if coordinates:
            fields["latitude"] = str(coordinates[0])
            fields["longitude"] = str(coordinates[1])
        return fields

    def _record(self, row: dict[str, Any], source_url: str) -> ProjectRecord:
        record = self.map_row(self._row_fields(row), source_url, "json")
        self.add_note(
            record,
            "This figure is a bucketed total the source's interactive map discloses by "
            "(country, sector classification, approval year), reported to one decimal place "
            "(millions of USD); it is not a single, individually identified project, and no "
            "project name or ID is disclosed by the source.",
        )
        # The dashboard discloses no status dimension at all -- only the field
        # name itself ("Commited Loans (in M$)") signals anything about the
        # data's status, unlike EBRD's identical gap (where the source
        # workbook's own glossary explicitly defined "commitments made").
        # "Committed" is used verbatim, not translated into "Signed", since
        # "Signed" and "Approved" are both already distinct, real values
        # elsewhere in this dataset and there's no basis here to pick between
        # them or any other specific pipeline stage.
        record.status = "Committed"
        self.add_note(
            record,
            "Status is inferred as \"Committed\" from the source dashboard's own field name "
            "(\"Commited Loans\") -- not individually disclosed per project, and no further "
            "detail (e.g. whether disbursement has started) is available from this source.",
        )
        if record.latitude is not None or record.longitude is not None:
            self.add_note(
                record,
                "Coordinates are the map's own country-level marker position, not a "
                "project-specific site.",
            )
        return record

    def scrape(self) -> list[ProjectRecord]:
        if self.options.source_file:
            # A local --source-file override supplies generic project rows
            # directly (see e.g. AIIBScraper/EIBScraper), bypassing the
            # Tableau-specific decode below entirely.
            return super().scrape()
        source_url = self.options.source_url or self.bank.source_url
        raw = _fetch_bootstrap_body(source_url, headless=self.options.headless, timeout=self.options.timeout)
        rows = _extract_rows(raw)
        self.run_metadata["source_total"] = len(rows)
        records = [self._record(row, source_url) for row in rows]
        if not records:
            raise SourceLayoutChanged(
                f"{self.bank.abbreviation}: the Tableau embed's 'pie' worksheet returned no rows"
            )
        return records
