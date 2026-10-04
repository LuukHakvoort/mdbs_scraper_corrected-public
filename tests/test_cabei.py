import unittest
from decimal import Decimal

from mdbs_scraper.adapters.cabei import CABEIScraper
from mdbs_scraper.base import ScrapeOptions
from mdbs_scraper.config import get_bank
from mdbs_scraper.schema import ProjectRecord


def _public(country, year, description, amount):
    return {"PAIS": country, "ANIO_APROBACION": str(year), "SECTOR_INSTITUCIONAL": "Sector Público",
            "DESCRIPCION_PROYECTO": description, "MONTO_BRUTO_USD": amount}


class CABEILoanApprovalsTests(unittest.TestCase):
    def setUp(self):
        self.scraper = CABEIScraper(get_bank("cabei"), ScrapeOptions(min_year=2002, max_year=2026))
        self.listed = [
            ProjectRecord(project_id="500437", project_name="Programa de Obras Estratégicas de Infraestructura Vial",
                          country="Costa Rica", approval_date="2019-12-10", loan_amount=Decimal("90055000")),
            ProjectRecord(project_id="500900", project_name="Programa de Agua Potable Rural",
                          country="Honduras", approval_date="2021-03-02", loan_amount=Decimal("40000000")),
        ]

    def test_an_approval_already_in_the_project_list_is_not_added_again(self):
        added = self.scraper.public_loans_not_in(self.listed, [
            _public("Costa Rica", 2019, "Programa de Obras (ampliación)", "90055000.0"),
            _public("Honduras", 2021, "Programa de Agua Potable Rural", "45000000"),  # amount changed
        ], "https://example.org/publico.csv")
        self.assertEqual(added, [])
        self.assertEqual(self.scraper.run_metadata["prestamos_matched_to_project_list"], 2)

    def test_a_missing_public_loan_is_added_with_its_disclosed_fields_only(self):
        [loan] = self.scraper.public_loans_not_in(self.listed, [
            _public("Costa Rica", 2020, "OPD para atender efectos del COVID-19", "300000000"),
        ], "https://example.org/publico.csv")
        self.assertTrue(loan.project_id.startswith("bcie-prestamo-"))
        self.assertEqual(loan.country_code, "CRI")
        self.assertEqual(loan.approval_year, 2020)
        self.assertEqual(loan.approval_date, "")
        self.assertEqual(loan.loan_amount, Decimal("300000000"))
        self.assertEqual(loan.loan_type, "Sovereign")
        self.assertEqual(loan.instrument_category, "loan")
        self.assertIn("discloses only the approval year", loan.data_quality_notes)

    def test_identical_approval_lines_get_distinct_stable_ids(self):
        rows = [_public("Honduras", 2005, "Programa de Vivienda", "1000000.0")] * 2
        first = self.scraper.public_loans_not_in([], rows, "u")
        second = CABEIScraper(get_bank("cabei"), ScrapeOptions()).public_loans_not_in([], rows, "u")
        self.assertEqual(len({loan.project_id for loan in first}), 2)
        self.assertEqual([loan.project_id for loan in first], [loan.project_id for loan in second])

    def test_an_existing_operation_absorbs_at_most_one_approval(self):
        added = self.scraper.public_loans_not_in(self.listed, [
            _public("Honduras", 2021, "Programa de Agua Potable Rural", "40000000"),
            _public("Honduras", 2021, "Programa de Agua Potable Rural", "40000000"),
        ], "u")
        self.assertEqual(len(added), 1)

    def test_private_sector_totals_keep_counts_and_amounts_per_country_year(self):
        rows = [
            {"PAIS": "Honduras", "ANIO_APROBACION": "2004", "SECTOR_INSTITUCIONAL": "Sector Privado",
             "MONTO_BRUTO_USD": "12500000.0", "CANTIDAD_APROBACIONES": "7"},
            {"PAIS": "Honduras", "ANIO_APROBACION": "2004", "SECTOR_INSTITUCIONAL": "Sector Público",
             "MONTO_BRUTO_USD": "99", "CANTIDAD_APROBACIONES": "3"},
            {"PAIS": "Honduras", "ANIO_APROBACION": "1999", "SECTOR_INSTITUCIONAL": "Sector Privado",
             "MONTO_BRUTO_USD": "1", "CANTIDAD_APROBACIONES": "1"},
        ]
        [total] = self.scraper.private_totals(rows, "https://example.org/all.csv")
        self.assertEqual(total["country_code"], "HND")
        self.assertEqual(total["year"], 2004)
        self.assertEqual(total["approvals_count"], 7)
        self.assertEqual(total["gross_amount_usd"], Decimal("12500000.0"))


if __name__ == "__main__":
    unittest.main()
