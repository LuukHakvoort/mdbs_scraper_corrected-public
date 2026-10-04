import unittest

from mdbs_scraper.adapters.ebrd import EBRDScraper
from mdbs_scraper.base import LoadedSource, ScrapeOptions
from mdbs_scraper.config import get_bank


class EBRDStatusInferenceTests(unittest.TestCase):
    def test_status_is_inferred_as_signed_with_a_disclosure_note(self):
        # The downloaded workbook discloses no status column at all (confirmed
        # live). It's titled "Net Cumulative Bank Investment" and defined in
        # its own glossary as "stock of all commitments made" -- so every row
        # is inferred, not disclosed, to be at least Signed.
        scraper = EBRDScraper(get_bank("ebrd"), ScrapeOptions())
        source = LoadedSource(
            data=b"",
            url="https://www.ebrd.com/home/what-we-do/projects.html",
            source_format="csv",
        )
        row = {
            "operation_name": "Example Municipal Water Project",
            "country": "Georgia",
            "sector": "Municipal & Env Inf",
            "original_signing_date": "2021-05-14",
            "loan_amount": "12500000",
        }

        record = scraper.map_row(row, source)

        self.assertEqual(record.status, "Signed")
        self.assertIn("inferred", record.data_quality_notes)
        self.assertIn("Signed", record.data_quality_notes)
        self.assertIn("commitments made", record.data_quality_notes)
        # banks.json's default_currency ("EUR") flows through unrelated to status.
        self.assertEqual(record.loan_currency, "EUR")
        self.assertEqual(record.sector_category, "Water Supply & Sanitation")

    def test_status_is_inferred_even_when_other_fields_are_sparse(self):
        scraper = EBRDScraper(get_bank("ebrd"), ScrapeOptions())
        source = LoadedSource(data=b"", url="https://www.ebrd.com/home/what-we-do/projects.html", source_format="csv")

        record = scraper.map_row({"operation_name": "Minimal Row"}, source)

        self.assertEqual(record.status, "Signed")
        self.assertIn("inferred", record.data_quality_notes)


if __name__ == "__main__":
    unittest.main()
