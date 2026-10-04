import unittest

from mdbs_scraper.instruments import INSTRUMENT_CATEGORIES, instrument_category_from


class InstrumentCategoryTests(unittest.TestCase):
    def test_real_instrument_wording_from_each_source_is_classified(self):
        cases = {
            # World Bank lending instruments
            "Investment Project Financing": "loan",
            "Development Policy Lending": "loan",
            "Program-for-Results Financing": "loan",
            "Technical Assistance Loan": "loan",
            # IATI finance-type names (ADB, AfDB, CAF)
            "Standard loan": "loan",
            "Standard grant": "grant",
            "Common equity": "equity",
            "Guarantees/insurance": "guarantee",
            "Purchase of securities from issuing agencies": "other",
            # IDB operation types
            "Technical Cooperation": "technical_assistance",
            "Loan Operation": "loan",
            "Investment Grants": "grant",
            "Grant Financing Product": "grant",
            "Container": "other",
            # ADB modalities
            "Technical Assistance": "technical_assistance",
            "Grant | Loan": "loan",
            "Loan | Technical Assistance": "loan",
            "Grant | Technical Assistance": "grant",
            "Private Sector Loan": "loan",
            # IsDB modes
            "Project Financing": "loan",
            "Technical Assistance Operations": "technical_assistance",
            "Special Assistance Operations": "other",
            "ITFC trade finance": "trade_finance",
            "Istisna'a": "loan",
            "Instalment Sale": "loan",
            "Leasing": "loan",
            "Mudaraba": "equity",
            "Profit Sharing (Musharaka)": "equity",
            # CABEI (Spanish)
            "Proyecto - Fondos Ordinarios": "loan",
            "Línea Global de Crédito": "loan",
            "Préstamo Sindicado": "loan",
            "Proyecto - Fondos No Reembolsables, Proyecto - Fondos Ordinarios": "loan",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(instrument_category_from(text), expected)

    def test_unknown_or_blank_text_is_left_blank_not_guessed(self):
        self.assertEqual(instrument_category_from(""), "")
        self.assertEqual(instrument_category_from("Sovereign"), "")
        self.assertEqual(instrument_category_from("Signed"), "")

    def test_every_rule_returns_an_allowed_category(self):
        for text in ("loan", "grant", "equity", "guarantee", "trade finance", "container",
                     "technical cooperation"):
            self.assertIn(instrument_category_from(text), INSTRUMENT_CATEGORIES)


if __name__ == "__main__":
    unittest.main()
