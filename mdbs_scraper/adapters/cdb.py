"""Caribbean Development Bank public projects-list adapter."""

import re

from ..parsers import extract_heading, extract_label_values, extract_labelled_sequence, extract_links, html_to_text
from .common import HTMLPortfolioScraper

# CDB's 19 borrowing member countries/territories, with the name variants
# confirmed live in project description text (not just their canonical
# name) -- used as a fallback when a detail page's structured Country field
# is absent (confirmed live: a small minority of pages genuinely omit it,
# see _country_from_description below). Keyed by canonical name so a
# shorter variant (e.g. "Virgin Islands") doesn't double-count as a second,
# ambiguous match against its own longer form ("British Virgin Islands").
_MEMBER_COUNTRIES = {
    "Anguilla": ["Anguilla"],
    "Antigua and Barbuda": ["Antigua and Barbuda", "Antigua & Barbuda"],
    "The Bahamas": ["The Bahamas", "Bahamas"],
    "Barbados": ["Barbados"],
    "Belize": ["Belize"],
    "British Virgin Islands": ["British Virgin Islands", "Virgin Islands"],
    "Cayman Islands": ["Cayman Islands"],
    "Dominica": ["Dominica"],
    "Grenada": ["Grenada"],
    "Guyana": ["Guyana"],
    "Haiti": ["Haiti"],
    "Jamaica": ["Jamaica"],
    "Montserrat": ["Montserrat"],
    "Saint Kitts and Nevis": ["Saint Kitts and Nevis", "St. Kitts and Nevis", "St Kitts and Nevis"],
    "Saint Lucia": ["Saint Lucia", "St. Lucia", "St Lucia"],
    "Saint Vincent and the Grenadines": [
        "Saint Vincent and the Grenadines", "St. Vincent and the Grenadines",
    ],
    "Trinidad and Tobago": ["Trinidad and Tobago"],
    "Turks and Caicos Islands": ["Turks and Caicos Islands", "Turks and Caicos"],
}


def _country_from_description(detail_html: str) -> str:
    """Fall back to the page's own "OVERVIEW" description text for country.

    Confirmed live: a handful of CDB detail pages disclose no structured
    Country field at all, but still explicitly name a member country/
    territory in their description paragraph (e.g. "... in the Bahamas").
    The "OVERVIEW" heading and the "Last Updated" line that follows the
    description are both stable across every page sampled, so the text
    between them scopes the search to just that paragraph -- avoiding false
    matches from unrelated country names elsewhere on the page (nav menus,
    footers). Only returns a country when exactly one is named; never
    guesses between multiple or zero matches.
    """

    text = html_to_text(detail_html)
    start = text.find("OVERVIEW")
    if start == -1:
        return ""
    end = text.find("Last Updated", start)
    window = text[start:end if end != -1 else start + 2000]
    matches = set()
    for canonical, variants in _MEMBER_COUNTRIES.items():
        if any(re.search(r"\b" + re.escape(variant) + r"\b", window) for variant in variants):
            matches.add(canonical)
    return next(iter(matches)) if len(matches) == 1 else ""


class CDBScraper(HTMLPortfolioScraper):
    # The listing page's own summary <table> (Project Title, Country,
    # Sectors & Themes, Project Total, Approved) is far thinner than what
    # each project's own detail page discloses, and carries no project URL
    # at all -- so this adapter always walks detail pages instead of relying
    # on HTMLPortfolioScraper.scrape()'s table-first path.
    _DETAIL_LABELS = {
        "Sector": "sector",
        "Date of Approval": "approval_date",
        "Country": "country",
        # "Approved total" is CDB's own approved/committed amount -- the
        # only financial figure the detail page discloses at all (confirmed
        # live: no separate, broader total-project-cost figure exists on the
        # page) -- so it maps to loan_amount, not total_project_cost. The
        # existing loan-amount-to-commitment fallback then populates
        # total_commitment too.
        "Approved total": "loan_amount",
        "Status": "status",
    }
    _GENERIC_LABELS = {"read more", "view details", "view project", "details", "learn more"}

    def _extract_detail_fields(self, detail_html: str) -> dict:
        fields = extract_label_values(detail_html)
        sequence = extract_labelled_sequence(html_to_text(detail_html), self._DETAIL_LABELS)
        for key, value in sequence.items():
            fields.setdefault(key, value)
        return fields

    def scrape(self):
        if self.options.source_file:
            return super().scrape()
        html_text, source_url, _ = self._initial_content()
        pages = self._listing_pages(html_text, source_url)
        project_links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for page_url, page_html in pages:
            for href, label in extract_links(
                page_html, page_url, href_pattern=self.bank.project_href_pattern
            ):
                # The listing table's sort-order links (e.g. "...?order=title&
                # sort=desc") and the listing page's own URL match the broad
                # project_href_pattern too; real detail pages have no query
                # string and a distinct slug, not "list-projects" itself.
                if "?" in href or href.rstrip("/").endswith("list-projects"):
                    continue
                if href in seen or not label or len(label) < 3:
                    continue
                seen.add(href)
                project_links.append((href, label))
                if self.options.max_projects and len(project_links) >= self.options.max_projects:
                    break
            if self.options.max_projects and len(project_links) >= self.options.max_projects:
                break
        records = []
        for href, label in project_links:
            response = self.client.get(href, accept="text/html")
            detail_html = response.text()
            fields = self._extract_detail_fields(detail_html)
            heading = extract_heading(detail_html)
            project_name = heading or ("" if label.casefold() in self._GENERIC_LABELS else label)
            fields.setdefault("project_name", project_name)
            fields.setdefault("project_url", response.url)
            country_from_description = not fields.get("country")
            if country_from_description:
                fields["country"] = _country_from_description(detail_html)
                country_from_description = bool(fields["country"])
            record = self.map_row(fields, response.url, "html")
            if country_from_description:
                self.add_note(
                    record,
                    "Country is derived from the project description text (matched against "
                    "CDB's own member country/territory list), not the source's structured "
                    "Country field, which this page does not disclose.",
                )
            if record.project_name:
                records.append(record)
        if records:
            return records
        return super().scrape()
