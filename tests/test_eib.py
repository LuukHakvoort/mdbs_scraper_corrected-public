import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from mdbs_scraper.adapters.eib import EIBScraper
from mdbs_scraper.base import ScrapeOptions
from mdbs_scraper.config import get_bank

FIXTURES = Path(__file__).parent / "fixtures"


class EIBSnapshotShapeTests(unittest.TestCase):
    def test_scrape_maps_real_project_shape_correctly(self):
        # EIB's own website project-list API (used since 2026-09-03 instead
        # of its thin IATI Registry activity file, which undercounted EIB's
        # real portfolio by roughly 12x -- see banks.json's eib entry) has a
        # different shape from every other adapter's source: no
        # read_rows()/FIELD_ALIASES involved, just this fixture's real,
        # captured response shape.
        source = str(FIXTURES / "eib_projects.json")
        records = EIBScraper(get_bank("eib"), ScrapeOptions(source_file=source)).run()

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.project_id, "20250219")
        self.assertEqual(record.project_name, "SIKELIA GREEN LOAN WIND AND SOLAR PV")
        self.assertEqual(record.country, "Italy")
        self.assertEqual(record.country_code, "ITA")
        self.assertEqual(record.sector, "Energy")
        self.assertEqual(record.sector_category, "ENERGY GENERATION AND SUPPLY")
        self.assertEqual(record.status, "Signed")
        self.assertEqual(record.approval_date, "2026-08-28")
        self.assertEqual(record.commitment_year, 2026)
        # additionalInformation[2]/[3]: approved amount vs. signed amount --
        # confirmed live these can genuinely differ (a partially-signed
        # multi-tranche facility), so both are kept, not collapsed to one.
        self.assertEqual(str(record.loan_amount), "92000000")
        self.assertEqual(record.loan_currency, "EUR")
        self.assertEqual(str(record.total_commitment), "65000000")
        self.assertEqual(record.commitment_currency, "EUR")
        self.assertEqual(record.project_url, "https://www.eib.org/en/projects/all/20250219")
        # This source has no IATI-shaped <participating-org>/<loan-terms>
        # disclosure -- the trade-off for 12x more real projects must be
        # visible in the data, not silently blank.
        self.assertEqual(record.cofinancing_partners, "")
        self.assertEqual(record.interest_rate, "")
        self.assertIn("not disclosed by this source", record.data_quality_notes)

    def test_source_total_recorded_from_the_apis_own_total(self):
        source = str(FIXTURES / "eib_projects.json")
        scraper = EIBScraper(get_bank("eib"), ScrapeOptions(source_file=source))
        scraper.run()
        self.assertEqual(scraper.run_metadata["source_total"], 17013)

    def test_unsigned_project_has_no_total_commitment(self):
        # additionalInformation[3] (signed amount) is 0 while a project is
        # only Approved, not yet Signed -- must not be recorded as if a real
        # $0 commitment were disclosed.
        scraper = EIBScraper(get_bank("eib"), ScrapeOptions())
        item = {
            "id": "20250283",
            "title": "AFRICA WOMEN ENTREPRENEURS FACILITY",
            "primaryTags": [
                {"label": "Regional - Africa", "subType": "countries"},
                {"label": "Services", "subType": "sectors"},
            ],
            "additionalInformation": ["Approved", "27/08/2026", 20000000.0, 0.0],
        }
        record = scraper._record(item, "https://www.eib.org/page-provider/projects/list")
        self.assertEqual(str(record.loan_amount), "20000000")
        self.assertIsNone(record.total_commitment)
        # "Regional - Africa" is not a single country -- never guessed.
        self.assertEqual(record.country_code, "")
        # "Services" is deliberately unmapped (too vague for one DAC category).
        self.assertEqual(record.sector_category, "")

    def test_signed_project_falls_back_to_signed_amount_when_approved_is_zeroed(self):
        # EIB's API zeroes out additionalInformation[2] (approved amount)
        # once a project is Signed -- the real number then only lives in
        # additionalInformation[3] (signed amount). loan_amount must fall
        # back to it rather than reporting a misleading 0.
        scraper = EIBScraper(get_bank("eib"), ScrapeOptions())
        item = {
            "id": "20230582",
            "title": "SECOND INTERCONNECTOR MALTA SICILIA",
            "primaryTags": [
                {"label": "Malta", "subType": "countries"},
                {"label": "Energy", "subType": "sectors"},
            ],
            "additionalInformation": ["Signed", "27/08/2026", 0.0, 100000000.0],
        }
        record = scraper._record(item, "https://www.eib.org/page-provider/projects/list")
        self.assertEqual(str(record.loan_amount), "100000000")
        self.assertEqual(record.loan_currency, "EUR")
        self.assertEqual(str(record.total_commitment), "100000000")

    def test_pagination_dedupes_by_id_and_stops_on_a_short_page(self):
        page_1 = {
            "data": [
                {"id": "1", "title": "Project One", "primaryTags": [], "additionalInformation": ["Signed", "01/01/2020", 1.0, 1.0]},
                {"id": "2", "title": "Project Two", "primaryTags": [], "additionalInformation": ["Signed", "01/01/2020", 1.0, 1.0]},
            ],
            "totalItems": 3,
        }
        page_2 = {
            "data": [
                # Same id as the last row of page 1 -- a real, documented
                # instability when many rows share a sort key (statusDate)
                # with no secondary tiebreaker; must not be double-counted.
                {"id": "2", "title": "Project Two", "primaryTags": [], "additionalInformation": ["Signed", "01/01/2020", 1.0, 1.0]},
                {"id": "3", "title": "Project Three", "primaryTags": [], "additionalInformation": ["Signed", "01/01/2020", 1.0, 1.0]},
            ],
            "totalItems": 3,
        }
        page_3 = {"data": [], "totalItems": 3}
        scraper = EIBScraper(get_bank("eib"), ScrapeOptions())
        responses = [
            Mock(text=Mock(return_value=json.dumps(page_1))),
            Mock(text=Mock(return_value=json.dumps(page_2))),
            Mock(text=Mock(return_value=json.dumps(page_3))),
        ]
        scraper.client.get = Mock(side_effect=responses)

        # Force a 2-row "page size" so a 2-item page reads as "full" (keep
        # paginating) without needing a 1000-row fixture.
        import mdbs_scraper.adapters.eib as eib_module
        original_page_size = eib_module._PAGE_SIZE
        eib_module._PAGE_SIZE = 2
        try:
            records = scraper.run()
        finally:
            eib_module._PAGE_SIZE = original_page_size

        self.assertEqual(sorted(r.project_id for r in records), ["1", "2", "3"])
        self.assertEqual(scraper.client.get.call_count, 3)


if __name__ == "__main__":
    unittest.main()
