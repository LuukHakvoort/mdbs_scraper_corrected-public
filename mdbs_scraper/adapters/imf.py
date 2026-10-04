"""IMF lending-arrangement adapter using the official IMF Finances pages."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from ..base import BaseScraper, LoadedSource
from ..cleaning import normalize_text
from ..errors import SourceLayoutChanged
from ..parsers import parse_tables, table_to_dicts
from ..schema import ProjectRecord
from ..tabular import delimited_rows, infer_format, read_rows
from .common import generic_record_from_row


class _SelectParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.selects: dict[str, list[tuple[str, str]]] = {}
        self._select = ""
        self._option_value = ""
        self._option_text: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "select":
            self._select = values.get("name") or values.get("id") or f"select_{len(self.selects)}"
            self.selects.setdefault(self._select, [])
        elif tag.lower() == "option" and self._select:
            self._option_value = values.get("value", "")
            self._option_text = []

    def handle_data(self, data):
        if self._option_text is not None:
            self._option_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "option" and self._option_text is not None:
            self.selects[self._select].append(
                (self._option_value, normalize_text(" ".join(self._option_text)))
            )
            self._option_value = ""
            self._option_text = None
        elif tag.lower() == "select":
            self._select = ""


class IMFScraper(BaseScraper):
    """Treat each IMF arrangement as the closest analogue to an MDB project."""

    def _map(self, row, source_url: str) -> ProjectRecord:
        record = generic_record_from_row(
            row, default_currency="XDR", source_url=source_url, source_format="tsv", day_first=False
        )
        member = record.country or normalize_text(
            row.get("Member") or row.get("member") or row.get("Country")
        )
        instrument = record.financing_instrument or normalize_text(
            row.get("Arrangement") or row.get("Facility") or row.get("Type")
        )
        if not record.project_name and member:
            date_label = record.approval_date or str(record.commitment_year or "undated")
            record.project_name = f"{member} — {instrument or 'IMF lending arrangement'} ({date_label})"
        record.country = member
        record.loan_type = "Sovereign"
        record.sector = record.sector or "Macroeconomic stabilization / balance-of-payments support"
        record.data_quality_notes = (
            "IMF arrangements are country programs, not investment projects. Amounts are preserved in "
            "SDR/XDR unless the source explicitly states another currency. Program conditions are published "
            "in separate staff reports and are not inferred from this finance table."
        )
        return record

    def _records_from_bytes(self, source: LoadedSource) -> list[ProjectRecord]:
        fmt = source.source_format.lower()
        if fmt in {"html", "htm"}:
            text = source.data.decode("utf-8", errors="replace")
            rows = [row for table in parse_tables(text) for row in table_to_dicts(table)]
        else:
            rows = read_rows(source.data, fmt)
        return [self._map(row, source.url) for row in rows]

    def scrape(self) -> list[ProjectRecord]:
        if self.options.source_file:
            source = self.load_source()
            records = [record for record in self._records_from_bytes(source) if record.project_name]
            if records:
                return records
            raise SourceLayoutChanged("Local IMF snapshot contains no recognizable arrangements")

        landing = self.load_source(accept="text/html")
        html_text = landing.data.decode("utf-8", errors="replace")
        parser = _SelectParser()
        parser.feed(html_text)
        date_options: list[tuple[str, str]] = []
        member_options: list[tuple[str, str]] = []
        for name, options in parser.selects.items():
            sample = " ".join(label for _, label in options[:20])
            if re.search(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", sample) or "date" in name.lower():
                date_options = options
            elif len(options) > 20 or "member" in name.lower() or "country" in name.lower():
                member_options = options
        dates = [value for value, label in date_options if value and re.search(r"\d{4}", value + label)]
        members = [
            (value, label) for value, label in member_options
            if value and label and "select" not in label.casefold()
        ]
        if not dates or not members:
            # The site may have added client-side form generation. Never return a
            # fake empty result; require a supported official snapshot instead.
            raise SourceLayoutChanged(
                "IMF Finances no longer exposes recognizable date/member selectors. Download the official "
                "Lending Commitments TSV and pass it with --source-file imf=FILE.tsv."
            )
        latest_date = max(dates)
        endpoint = urljoin(landing.url, "extarr2.aspx")
        records: list[ProjectRecord] = []
        for member_value, _member_label in members:
            response = self.client.get(
                endpoint,
                params={"date1key": latest_date, "memberkey1": member_value, "tsvflag": "Y"},
                accept="text/tab-separated-values,text/plain,text/html",
            )
            body_text = response.text()
            if "<html" in body_text[:1000].lower():
                rows = [
                    row for table in parse_tables(body_text) for row in table_to_dicts(table)
                ]
            else:
                rows = delimited_rows(response.body, "\t")
            records.extend(self._map(row, response.url) for row in rows)
            records = [record for record in records if record.project_name]
            if self.options.max_projects and len(records) >= self.options.max_projects:
                break
        if not records:
            raise SourceLayoutChanged("IMF Finances returned no recognizable lending arrangements")
        return records
