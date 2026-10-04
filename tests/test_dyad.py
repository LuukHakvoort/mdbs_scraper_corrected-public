import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from mdbs_scraper.dyad import build_dyad_outputs, convert_row
from mdbs_scraper.output import write_dict_rows, write_records
from mdbs_scraper.reference import CurrencyRule, ReferenceData
from mdbs_scraper.schema import ProjectRecord

REFERENCE = ReferenceData(
    rules={
        "EUR": CurrencyRule("EUR", imf_area="G163"),
        "XDR": CurrencyRule("XDR"),
        "XUA": CurrencyRule("XUA", pegged_to="XDR"),
        "IDR": CurrencyRule("IDR", imf_area="IDN"),
    },
    rates={
        "EUR": {2010: Decimal("0.8"), 2025: Decimal("0.9")},
        "IDR": {2010: Decimal("9000")},
        "XDR": {2010: Decimal("0.5")},
    },
    deflator={2010: Decimal("50"), 2016: Decimal("80"), 2020: Decimal("100"), 2025: Decimal("100")},
    provenance={"test": {"url": "fixture"}},
    # Price-level ratios (GDP in US$ / GDP in PPP $): IND 0.25, HND 0.5; nothing for IDN or NPL.
    gdp_usd={("IND", 2010): Decimal("10"), ("IND", 2020): Decimal("20"), ("HND", 2010): Decimal("10"),
             ("NPL", 2010): Decimal("5")},
    gdp_ppp={("IND", 2010): Decimal("40"), ("IND", 2020): Decimal("80"), ("HND", 2010): Decimal("20")},
)


def _row(**fields):
    base = {"approval_year": "2010", "loan_amount": "", "loan_currency": "", "loan_amount_usd": ""}
    base.update(fields)
    return base


class ConvertRowTests(unittest.TestCase):
    def test_a_bank_reported_usd_amount_is_only_deflated(self):
        result = convert_row(_row(loan_amount="100", loan_currency="USD", loan_amount_usd="100"), REFERENCE)
        self.assertEqual(result["usd_conversion"], "source-usd")
        self.assertEqual(result["usd_nominal"], Decimal("100"))
        self.assertEqual(result["usd_constant_2025"], Decimal("200.00"))  # D2025 / D2010 = 2

    def test_other_currencies_use_the_approval_years_imf_rate(self):
        result = convert_row(_row(loan_amount="80", loan_currency="EUR"), REFERENCE)
        self.assertEqual(result["usd_conversion"], "imf-annual-average")
        self.assertEqual(result["fx_units_per_usd"], Decimal("0.8"))
        self.assertEqual(result["usd_nominal"], Decimal("100.00"))
        self.assertEqual(result["usd_constant_2025"], Decimal("200.00"))

    def test_a_unit_defined_as_one_sdr_uses_the_sdr_rate(self):
        result = convert_row(_row(loan_amount="50", loan_currency="XUA"), REFERENCE)
        self.assertEqual(result["usd_nominal"], Decimal("100.00"))
        self.assertIn("XUA valued at 1 XDR", result["price_basis_note"])

    def test_2026_is_not_priced_and_non_usd_2026_amounts_stay_unconverted(self):
        euro = convert_row(_row(approval_year="2026", loan_amount="10", loan_currency="EUR"), REFERENCE)
        self.assertEqual(euro["usd_conversion"], "unconverted")
        self.assertIsNone(euro["usd_nominal"])
        self.assertIn("2026 rates are not yet published", euro["price_basis_note"])
        dollar = convert_row(_row(approval_year="2026", loan_amount="10", loan_currency="USD"), REFERENCE)
        self.assertEqual(dollar["usd_nominal"], Decimal("10"))
        self.assertIsNone(dollar["usd_constant_2025"])
        self.assertIn("has no 2026 value yet", dollar["price_basis_note"])

    def test_a_huge_non_sovereign_usd_amount_in_a_high_unit_currency_country_is_excluded(self):
        suspect = _row(loan_amount="59900456000", loan_currency="USD", loan_amount_usd="59900456000",
                       country_code="IDN", loan_type="Non-sovereign")
        result = convert_row(suspect, REFERENCE)
        self.assertEqual(result["usd_conversion"], "excluded-currency-suspect")
        self.assertIsNone(result["usd_constant_2025"])
        self.assertIn("local currency", result["price_basis_note"])
        # The same amount from an official file, or for a sovereign loan, stands.
        official = convert_row(dict(suspect, official_source_id="adb_sovereign"), REFERENCE)
        sovereign = convert_row(dict(suspect, loan_type="Sovereign"), REFERENCE)
        self.assertEqual((official["usd_conversion"], sovereign["usd_conversion"]), ("source-usd", "source-usd"))

    def test_recipient_ppp_turns_nominal_usd_into_constant_international_dollars(self):
        result = convert_row(_row(loan_amount="100", loan_currency="USD", country_code="IND"), REFERENCE)
        self.assertEqual(result["ppp_price_level_ratio"], Decimal("0.250000"))
        self.assertEqual(result["ppp_intl_nominal"], Decimal("400.00"))
        self.assertEqual(result["ppp_intl_constant_2025"], Decimal("800.00"))  # 100 x 2 / 0.25
        self.assertEqual(result["price_basis_note"], "")

    def test_ppp_is_left_blank_without_a_country_or_a_world_bank_factor(self):
        regional = convert_row(_row(loan_amount="100", loan_currency="USD"), REFERENCE)
        self.assertIsNone(regional["ppp_intl_constant_2025"])
        self.assertIn("No single recipient country", regional["price_basis_note"])
        uncovered = convert_row(_row(loan_amount="100", loan_currency="USD", country_code="NPL"), REFERENCE)
        self.assertEqual(uncovered["usd_constant_2025"], Decimal("200.00"))
        self.assertIsNone(uncovered["ppp_intl_nominal"])
        self.assertIn("No World Bank PPP price level (NY.GDP.MKTP.CD / NY.GDP.MKTP.PP.CD) for NPL in 2010",
                      uncovered["price_basis_note"])

    def test_an_excluded_suspect_amount_gets_no_ppp_value(self):
        suspect = _row(loan_amount="59900456000", loan_currency="USD", loan_amount_usd="59900456000",
                       country_code="IDN", loan_type="Non-sovereign")
        self.assertIsNone(convert_row(suspect, REFERENCE)["ppp_intl_nominal"])

    def test_rows_without_an_amount_or_a_year_are_left_blank_with_a_reason(self):
        self.assertIn("No bank amount", convert_row(_row(), REFERENCE)["price_basis_note"])
        undated = convert_row(_row(approval_year="", loan_amount="5", loan_currency="USD"), REFERENCE)
        self.assertIsNone(undated["usd_nominal"])
        self.assertIn("No approval year", undated["price_basis_note"])


def _project(bank_id, project_id, country, code, year, amount, currency="USD", **fields):
    return ProjectRecord(
        bank_id=bank_id, bank_abbreviation=bank_id.upper(), project_id=project_id,
        project_name=project_id, country=country, country_code=code, approval_year=year,
        loan_amount=Decimal(amount) if amount is not None else None, loan_currency=currency if amount else "",
        loan_amount_usd=Decimal(amount) if amount is not None and currency == "USD" else None,
        source_url="https://example.org", **fields,
    )


class BuildDyadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        records = [
            _project("aiib", "A1", "India", "IND", 2020, "10", instrument_category="loan", concessional="No"),
            _project("aiib", "A2", "India", "IND", 2020, "5", instrument_category="grant"),
            _project("aiib", "A3", "Regional", "", 2020, "7"),
            _project("aiib", "A4", "Nepal", "NPL", 2016, "8", currency="EUR"),
            _project("cabei", "C1", "Honduras", "HND", 2010, None),
        ]
        write_records(self.output / "all_mdb_projects.csv", records, "csv")
        write_dict_rows(self.output / "cabei_private_approvals_country_year.csv",
                        ["bank_id", "country", "country_code", "year", "approvals_count", "gross_amount_usd"],
                        [{"bank_id": "cabei", "country": "Honduras", "country_code": "HND", "year": 2010,
                          "approvals_count": 7, "gross_amount_usd": "3"}])
        with patch("mdbs_scraper.dyad.load_reference", return_value=REFERENCE):
            self.entry = build_dyad_outputs(self.output / "all_mdb_projects.csv", self.output)

    def _read(self, name):
        with (self.output / name).open(encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))

    def test_observed_rows_sum_operations_by_bank_country_and_year(self):
        rows = {(row["bank_id"], row["country_code"], row["year"]): row for row in self._read("dyad_country_year.csv")}
        india = rows[("aiib", "IND", "2020")]
        self.assertEqual(india["n_operations"], "2")
        self.assertEqual(Decimal(india["usd_2025_total"]), Decimal("15.00"))
        self.assertEqual(Decimal(india["usd_2025_loan"]), Decimal("10.00"))
        self.assertEqual(india["n_grant"], "1")
        self.assertEqual(Decimal(india["usd_2025_concessional_no"]), Decimal("10.00"))
        self.assertEqual(Decimal(india["usd_2025_concessional_unknown"]), Decimal("5.00"))
        self.assertEqual(Decimal(india["ppp_2025_total"]), Decimal("60.00"))  # 15 / 0.25
        self.assertEqual(india["n_ppp_unpriced"], "0")
        self.assertEqual(india["cabei_private_n"], "")
        self.assertEqual(rows[("aiib", "NPL", "2016")]["n_unconverted"], "1")  # no 2016 EUR rate

    def test_private_sector_totals_are_their_own_columns(self):
        rows = {(row["bank_id"], row["country_code"], row["year"]): row for row in self._read("dyad_country_year.csv")}
        honduras = rows[("cabei", "HND", "2010")]
        self.assertEqual(honduras["n_operations"], "1")
        self.assertEqual(honduras["cabei_private_n"], "7")
        self.assertEqual(Decimal(honduras["cabei_private_usd_2025"]), Decimal("6.00"))
        self.assertEqual(Decimal(honduras["cabei_private_ppp_2025"]), Decimal("12.00"))  # 6 / 0.5
        self.assertEqual(Decimal(honduras["usd_2025_total"]), Decimal("0"))

    def test_rows_without_a_country_go_to_the_unallocated_file(self):
        [regional] = self._read("dyad_unallocated_year.csv")
        self.assertEqual((regional["bank_id"], regional["year"], regional["countries"]), ("aiib", "2020", "Regional"))
        self.assertEqual(Decimal(regional["usd_2025_total"]), Decimal("7.00"))
        self.assertEqual((regional["n_ppp_unpriced"], Decimal(regional["ppp_2025_total"])), ("1", Decimal("0")))

    def test_the_balanced_panel_claims_zeros_only_inside_the_coverage_window(self):
        rows = {(row["bank_id"], row["country_code"], row["year"]): row
                for row in self._read("dyad_country_year_balanced.csv")}
        before_founding = rows[("aiib", "IND", "2010")]  # AIIB covers 2016 onward
        self.assertEqual((before_founding["in_coverage"], before_founding["n_operations"]), ("0", ""))
        quiet_year = rows[("aiib", "IND", "2018")]
        self.assertEqual((quiet_year["observed"], quiet_year["in_coverage"], quiet_year["n_operations"]),
                         ("0", "1", "0"))
        self.assertEqual(rows[("aiib", "IND", "2020")]["observed"], "1")
        after_last_approval = rows[("aiib", "IND", "2024")]
        self.assertEqual(after_last_approval["in_coverage"], "0")
        self.assertEqual(len([key for key in rows if key[:2] == ("aiib", "IND")]), 25)

    def test_outputs_are_sorted_and_recorded_in_the_manifest(self):
        keys = [(row["bank_id"], row["country_code"], int(row["year"])) for row in self._read("dyad_country_year.csv")]
        self.assertEqual(keys, sorted(keys))
        manifest = json.loads((self.output / "dyad_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["rows"]["dyad"], 3)
        self.assertIn("dyad_balanced", manifest["outputs"])
        self.assertEqual(manifest["amount_conversions"]["source-usd"], 3)
        self.assertEqual(manifest["amount_conversions"]["unconverted"], 1)
        self.assertEqual(manifest["largest_operations_usd_2025"][0]["project_id"], "A1")
        self.assertEqual(manifest["ppp"], {"priced": 2, "no_country": 1, "no_factor": 0, "no_factor_by_country": {}})
        projects = self._read("all_mdb_projects_usd.csv")
        self.assertEqual(len(projects), 5)
        self.assertIn("usd_constant_2025", projects[0])
        self.assertIn("ppp_intl_constant_2025", projects[0])


if __name__ == "__main__":
    unittest.main()
