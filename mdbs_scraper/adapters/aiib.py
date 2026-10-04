"""Asian Infrastructure Investment Bank project-list adapter."""

from __future__ import annotations

from ..browser import render_page
from ..parsers import extract_heading, extract_label_values, extract_links, html_to_text
from .common import HTMLPortfolioScraper


class AIIBScraper(HTMLPortfolioScraper):
    @staticmethod
    def _summary_fields(html_text: str) -> dict[str, str]:
        lines = html_to_text(html_text).splitlines()
        fields: dict[str, str] = {}
        labels = {
            "STATUS": "status",
            "MEMBER": "country",
            "SECTOR": "sector",
            "PROJECT NUMBER": "project_id",
            "APPROVED FUNDING": "loan_amount",
            "PROPOSED FUNDING AMOUNT": "loan_amount",
            # "Sovereign"/"Nonsovereign" -- despite the label, this is the
            # same sovereign/non-sovereign concept as loan_type elsewhere
            # (e.g. idb/ndb/ibrd/ida), not a loan product/instrument type.
            "FINANCING TYPE": "loan_type",
            "FINANCING APPROVAL": "approval_date",
        }
        for index, line in enumerate(lines):
            key = labels.get(line.upper())
            if not key:
                continue
            for value in lines[index + 1:]:
                if value and value.upper() not in labels:
                    fields[key] = value
                    break
        return fields

    def _detail_records(self, html_text: str, source_url: str):
        links = extract_links(
            html_text,
            source_url,
            href_pattern=self.bank.project_href_pattern,
        )
        seen: set[str] = set()
        unique_links: list[tuple[str, str]] = []
        for href, label in links:
            if href in seen or href.rstrip("/") == source_url.rstrip("/"):
                continue
            seen.add(href)
            unique_links.append((href, label))
        self.run_metadata["source_total"] = len(unique_links)
        records = []
        for href, label in unique_links:
            if self.options.max_projects and len(records) >= self.options.max_projects:
                break
            response = self.client.get(href, accept="text/html,application/xhtml+xml")
            detail_url = response.url
            detail_html = response.text()
            fields = self._summary_fields(detail_html)
            fields.update({
                key: value for key, value in extract_label_values(detail_html).items()
                if key not in fields
            })
            if not fields.get("project_id"):
                detail = render_page(
                    href,
                    headless=self.options.headless,
                    timeout=self.options.timeout,
                )
                detail_url = detail.url
                detail_html = detail.html
                fields = self._summary_fields(detail_html)
                fields.update({
                    key: value for key, value in extract_label_values(detail_html).items()
                    if key not in fields
                })
            fields.setdefault("project_name", extract_heading(detail_html) or label)
            fields.setdefault("project_url", detail_url)
            record = self.map_row(fields, detail_url, "html")
            if record.project_name:
                records.append(record)
        return records

    def scrape(self):
        if self.options.source_file:
            return super().scrape()
        html_text, source_url, payloads = self._initial_content()
        if html_text:
            detail_records = self._detail_records(html_text, source_url)
            if detail_records:
                return detail_records
        return super().scrape()
