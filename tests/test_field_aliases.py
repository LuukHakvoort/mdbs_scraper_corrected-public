import unittest

from mdbs_scraper.adapters.common import generic_record_from_row
from mdbs_scraper.config import BankDefinition
from mdbs_scraper.adapters.common import HTMLPortfolioScraper
from mdbs_scraper.base import ScrapeOptions


class CabeiSpanishCsvAliasTests(unittest.TestCase):
    def test_spanish_csv_headers_map_to_canonical_fields(self):
        row = {
            "Proyecto": "500701",
            "Titulo del Proyecto": "Proyecto de Ejemplo",
            "Pais": "Nicaragua",
            "Estado": "Implementation",
            "Sectores": "Salud",
            "Sub-Sectores": "Infraestructura de salud",
            "Fecha de Firma": "2021-06-11",
            "Fecha de Aprobacion": "2021-04-27",
            "Monto Aprobado": "100000000",
            "Monto Desembolsado": "34217693.87",
            "Moneda": "USD",
            # Disclosed for every CABEI row (confirmed live) but previously
            # unmapped -- a real per-project IATI URL and a Sovereign/
            # Non-sovereign classification, not just CABEI-specific quirks.
            "URL IATI": "https://d-portal.iatistandard.org/ctrack.html?aid=XM-DAC-46007-500701",
            "Sector de Mercado": "Sector Público Soberano",
        }
        record = generic_record_from_row(row, default_currency="USD", day_first=True)
        self.assertEqual(record.project_id, "500701")
        self.assertEqual(record.project_name, "Proyecto de Ejemplo")
        self.assertEqual(record.country, "Nicaragua")
        self.assertEqual(record.country_code, "NIC")
        self.assertEqual(record.status, "Implementation")
        self.assertEqual(record.sector, "Salud")
        self.assertEqual(record.subsector, "Infraestructura de salud")
        self.assertEqual(record.commitment_date, "2021-06-11")
        self.assertEqual(record.approval_date, "2021-04-27")
        self.assertEqual(str(record.loan_amount), "100000000")
        self.assertEqual(record.loan_currency, "USD")
        self.assertEqual(str(record.total_disbursement), "34217693.87")
        self.assertEqual(
            record.project_url, "https://d-portal.iatistandard.org/ctrack.html?aid=XM-DAC-46007-500701"
        )
        self.assertEqual(record.loan_type, "Sovereign")

    def test_fuente_financiamiento_maps_to_cofinancing(self):
        row = {
            "Proyecto": "500701",
            "Titulo del Proyecto": "Proyecto de Ejemplo",
            "Fuente Financiamiento": "Banco Mundial",
            "Monto Fuente Financiamiento": "5000000",
        }
        record = generic_record_from_row(row, default_currency="USD")
        self.assertEqual(record.cofinancing_partners, "Banco Mundial")
        self.assertEqual(str(record.cofinancing_amount), "5000000")

    def test_fuente_financiamiento_na_stays_blank(self):
        # "N/A" is treated as null text (cleaning.NULL_TEXT), not a real
        # co-financier name -- most CABEI projects disclose this as N/A.
        row = {"Proyecto": "500701", "Titulo del Proyecto": "X", "Fuente Financiamiento": "N/A"}
        record = generic_record_from_row(row, default_currency="USD")
        self.assertEqual(record.cofinancing_partners, "")


class EbrdCsvAliasTests(unittest.TestCase):
    def test_original_signing_date_maps_to_commitment_date(self):
        row = {
            "Country": "ALBANIA",
            "Operation Name": "AASF - Fondi Besa",
            "EBRD Finance": "5583694.96",
            "Sector": "Non-depository Credit (non-bank)",
            "Original Signing Date": "2016-12-06 00:00:00",
        }
        record = generic_record_from_row(row, default_currency="EUR", day_first=True)
        self.assertEqual(record.commitment_date, "2016-12-06")
        self.assertEqual(record.commitment_year, 2016)
        # EBRD's workbook reports country names in ALL CAPS; the ISO-3
        # derivation is case-insensitive.
        self.assertEqual(record.country_code, "ALB")


class IdbAbbreviatedCsvAliasTests(unittest.TestCase):
    def test_abbreviated_headers_map_to_canonical_fields(self):
        row = {
            "oper_num": "AR-G1002",
            "oper_nm": "Energy Efficiency and Renewable Energy in Low-income Housing",
            "apprvl_dt": "2015-07-29T00:00",
            "sign_dt": "2017-03-15T00:00",
            "cntry_cd": "AR",
            "cntry_nm": "Argentina",
            "publc_sts_nm": "Implementation",
            "sector_nm": "URBAN DEVELOPMENT AND HOUSING",
            "lending_typ_nm": "Sovereign Guaranteed",
            "opertyp_nm": "Investment Grants",
            "orig_apprvd_useq_amnt": "14630855.000",
            "totl_cost_orig": "85411079.00",
        }
        record = generic_record_from_row(
            row, default_currency="USD", day_first=False, amount_period_is_decimal=True
        )
        self.assertEqual(record.project_id, "AR-G1002")
        self.assertEqual(record.project_name, "Energy Efficiency and Renewable Energy in Low-income Housing")
        self.assertEqual(record.country, "Argentina")
        # country_code is derived from the `country` name text as a
        # standardized ISO-3 code, not passed through from IDB's own
        # "cntry_cd" (a non-ISO scheme elsewhere, e.g. "PR" = Peru there --
        # only coincidentally identical to the real ISO alpha-2 for Argentina).
        self.assertEqual(record.country_code, "ARG")
        self.assertEqual(record.status, "Implementation")
        self.assertEqual(record.sector, "URBAN DEVELOPMENT AND HOUSING")
        self.assertEqual(record.financing_instrument, "Investment Grants")
        self.assertEqual(str(record.loan_amount), "14630855.000")
        self.assertEqual(str(record.total_project_cost), "85411079.00")

    def test_without_period_is_decimal_the_same_amount_is_misread_as_thousands(self):
        # Documents *why* the override exists: the shared heuristic alone
        # would silently inflate this amount 1000x.
        row = {"orig_apprvd_useq_amnt": "14630855.000"}
        record = generic_record_from_row(row, default_currency="USD")
        self.assertEqual(str(record.loan_amount), "14630855000")


class DeclarativeOverrideWiringTests(unittest.TestCase):
    def test_bank_project_href_pattern_reaches_the_scraper(self):
        bank = BankDefinition(
            id="test", name="Test Bank", abbreviation="TB", website="https://example.org",
            source_url="https://example.org/projects", source_format="html", method="official-portfolio",
            coverage_notes="", project_href_pattern=r"only-this-project-\d+",
        )
        scraper = HTMLPortfolioScraper(bank, ScrapeOptions())
        self.assertEqual(scraper.bank.project_href_pattern, r"only-this-project-\d+")
        # The pattern must come from the bank definition (banks.json), not a
        # class-level default baked into the scraper subclass.
        self.assertNotIn("project_href_pattern", HTMLPortfolioScraper.__dict__)


if __name__ == "__main__":
    unittest.main()
