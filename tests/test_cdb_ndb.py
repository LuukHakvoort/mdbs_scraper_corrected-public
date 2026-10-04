import unittest
from unittest.mock import Mock, patch

from mdbs_scraper.adapters.cdb import CDBScraper
from mdbs_scraper.adapters.ndb import NDBScraper
from mdbs_scraper.base import ScrapeOptions
from mdbs_scraper.config import get_bank


class CDBDetailPageScrapeTests(unittest.TestCase):
    def test_scrape_walks_detail_pages_instead_of_the_listing_table(self):
        listing_html = (
            "<table><tr><th>Project Title</th><th>Country</th><th>Sectors &amp; Themes</th>"
            "<th>Project Total</th><th>Approved</th></tr>"
            "<tr><td>6th Road Project</td><td>Belize</td><td>Transportation</td>"
            "<td>77708000</td><td>Dec, 2018</td></tr></table>"
            '<a href="https://www.caribank.org/our-work/projects-map/list-projects?order=title&sort=desc">Project Title</a>'
            '<a href="https://www.caribank.org/our-work/projects-map/6th-road-project-0">6th Road Project</a>'
        )
        detail_html = (
            "<h1>6th Road (Coastal Highway Upgrading) Project</h1>"
            "<p>Sector</p><p>Transportation</p>"
            "<p>Date of Approval</p><p>December, 2018</p>"
            "<p>Country</p><p>Belize</p>"
            "<p>Approved total</p><p>$ 77,708,000</p>"
            "<p>Status</p><p>Under Implementation</p>"
        )
        scraper = CDBScraper(get_bank("cdb"), ScrapeOptions())
        scraper.client.get = Mock(side_effect=[
            Mock(url="https://www.caribank.org/our-work/projects-map/6th-road-project-0", text=Mock(return_value=detail_html)),
        ])
        with patch(
            "mdbs_scraper.adapters.common.HTMLPortfolioScraper._initial_content",
            return_value=(listing_html, "https://www.caribank.org/our-work/projects-map/list-projects", []),
        ):
            with patch.object(CDBScraper, "_listing_pages", return_value=[
                ("https://www.caribank.org/our-work/projects-map/list-projects", listing_html)
            ]):
                records = scraper.run()

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.project_name, "6th Road (Coastal Highway Upgrading) Project")
        self.assertEqual(record.country, "Belize")
        self.assertEqual(record.sector, "Transportation")
        self.assertEqual(record.approval_date, "2018-12-01")
        # "Approved total" is CDB's own approved/committed amount, the only
        # financial figure the detail page discloses -- maps to loan_amount,
        # and the existing fallback then populates total_commitment too.
        self.assertEqual(str(record.loan_amount), "77708000")
        self.assertEqual(str(record.total_commitment), "77708000")
        self.assertIsNone(record.total_project_cost)
        self.assertEqual(record.project_url, "https://www.caribank.org/our-work/projects-map/6th-road-project-0")
        # The listing table's own sort-order link must not be treated as a project.
        self.assertEqual(scraper.client.get.call_count, 1)

    def _run_single_detail_page(self, detail_html: str):
        listing_html = (
            '<a href="https://www.caribank.org/our-work/projects-map/example-project-0">Example Project</a>'
        )
        scraper = CDBScraper(get_bank("cdb"), ScrapeOptions())
        scraper.client.get = Mock(return_value=Mock(
            url="https://www.caribank.org/our-work/projects-map/example-project-0",
            text=Mock(return_value=detail_html),
        ))
        with patch(
            "mdbs_scraper.adapters.common.HTMLPortfolioScraper._initial_content",
            return_value=(listing_html, "https://www.caribank.org/our-work/projects-map/list-projects", []),
        ):
            with patch.object(CDBScraper, "_listing_pages", return_value=[
                ("https://www.caribank.org/our-work/projects-map/list-projects", listing_html)
            ]):
                return scraper.run()

    def test_country_falls_back_to_the_description_when_not_structurally_disclosed(self):
        # Confirmed live: a handful of CDB detail pages have no structured
        # Country field at all, but the "OVERVIEW" description still
        # explicitly names a member territory.
        detail_html = (
            "<h1>NDM - RRL Hurricane Irma</h1>"
            "<p>Sector</p><p>Disaster Risk Reduction</p>"
            "<p>Date of Approval</p><p>December, 2017</p>"
            "<p>Approved total</p><p>$ 5,000,000</p>"
            "<p>Status</p><p>Under Implementation</p>"
            "<h5>OVERVIEW</h5>"
            "<p>The project will improve climate-resilient infrastructure in the Virgin Islands.</p>"
            "<p>Last Updated - 27/08/2026</p>"
        )
        records = self._run_single_detail_page(detail_html)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.country, "British Virgin Islands")
        self.assertIn("description text", record.data_quality_notes)

    def test_country_stays_blank_when_the_description_names_no_member_territory(self):
        # "Water and Sewerage Corporation" names no CDB member country
        # explicitly -- must not be guessed at.
        detail_html = (
            "<h1>Water Supply Improvement Project</h1>"
            "<p>Sector</p><p>Water and Sanitation</p>"
            "<p>Date of Approval</p><p>June, 2020</p>"
            "<p>Approved total</p><p>$ 10,000,000</p>"
            "<p>Status</p><p>Under Implementation</p>"
            "<h5>OVERVIEW</h5>"
            "<p>The project will improve the Water and Sewerage Corporation's operations.</p>"
            "<p>Last Updated - 27/08/2026</p>"
        )
        records = self._run_single_detail_page(detail_html)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.country, "")
        self.assertNotIn("description text", record.data_quality_notes)


class NdbDetailFieldsTests(unittest.TestCase):
    def test_extract_detail_fields_reads_quick_facts_block(self):
        detail_html = (
            "<h1>HUDCO Affordable Housing Project</h1>"
            "<p>Country</p><p>India</p>"
            "<p>Status</p><p>Proposed</p>"
            "<p>Area Of Operation</p><p>Social Infrastructure</p>"
            "<p>Type</p><p>Non-Sovereign</p>"
            "<p>Quick Facts</p>"
            "<p>Concept Approval Date</p><p>17 August 2026</p>"
            "<p>Proposed Limit of NDB Financing</p><p>USD 300 million</p>"
        )
        scraper = NDBScraper(get_bank("ndb"), ScrapeOptions())

        fields = scraper._extract_detail_fields(detail_html)

        self.assertEqual(fields["country"], "India")
        self.assertEqual(fields["status"], "Proposed")
        self.assertEqual(fields["sector"], "Social Infrastructure")
        self.assertEqual(fields["loan_type"], "Non-Sovereign")
        self.assertEqual(fields["approval_date"], "17 August 2026")
        self.assertEqual(fields["loan_amount"], "USD 300 million")

    def test_extract_detail_fields_matches_labels_regardless_of_case(self):
        # Confirmed live (2026-09-22): once a project moves past Proposed,
        # NDB's own markup renders these same Quick Facts labels in
        # sentence case on most pages ("Financing approval date"), not the
        # title case originally hardcoded ("Financing Approval Date") --
        # silently dropping approval_date/loan_amount for every such page.
        detail_html = (
            "<h1>Hunan Ecological Development Project</h1>"
            "<p>Country</p><p>China</p>"
            "<p>Status</p><p>Cancelled</p>"
            "<p>Quick Facts</p>"
            "<p>Financing approval date</p><p>30 August 2017</p>"
            "<p>Current limit of financing</p><p>CNY 2.0 billion</p>"
        )
        scraper = NDBScraper(get_bank("ndb"), ScrapeOptions())

        fields = scraper._extract_detail_fields(detail_html)

        self.assertEqual(fields["approval_date"], "30 August 2017")

    def test_extract_detail_fields_reads_ta_label_variant(self):
        # Technical-assistance projects use a third label pair (confirmed
        # live), not "Concept"/"Financing Approval Date" at all.
        detail_html = (
            "<h1>Upgrade of Kaliningrad Sea Port Project</h1>"
            "<p>Country</p><p>Russia</p>"
            "<p>Status</p><p>Cancelled</p>"
            "<p>Quick Facts</p>"
            "<p>TA Approval Date</p><p>25 March 2020</p>"
            "<p>Limit of NDB Financing</p><p>USD 400,000</p>"
        )
        scraper = NDBScraper(get_bank("ndb"), ScrapeOptions())

        fields = scraper._extract_detail_fields(detail_html)

        self.assertEqual(fields["approval_date"], "25 March 2020")
        self.assertEqual(fields["loan_amount"], "USD 400,000")

    def test_project_href_pattern_excludes_plural_projects_hub_pages(self):
        bank = get_bank("ndb")
        self.assertRegex("https://www.ndb.int/project/hudco-affordable-housing-project/", bank.project_href_pattern)
        import re

        self.assertIsNone(re.search(
            bank.project_href_pattern,
            "https://www.ndb.int/projects/environment-and-social-sustainability/",
        ))


if __name__ == "__main__":
    unittest.main()
