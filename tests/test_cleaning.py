from decimal import Decimal
import unittest

from mdbs_scraper.cleaning import (
    detect_currency,
    duration_years,
    normalize_loan_type,
    normalized_key,
    parse_amount,
    parse_coordinate,
    parse_date,
    parse_decimal,
)


class CleaningTests(unittest.TestCase):
    def test_amount_multiplier_and_currency(self):
        amount, currency = parse_amount("US$ 12.5 million")
        self.assertEqual(amount, Decimal("12500000.0"))
        self.assertEqual(currency, "USD")

    def test_european_decimal(self):
        self.assertEqual(parse_decimal("EUR 1.234,50"), Decimal("1234.50"))

    def test_bank_acronyms_and_placeholders_are_never_read_as_currencies(self):
        # Confirmed in real output: "IDA credit", "ADF grant", "OCR loans"
        # used to come back as currencies "IDA"/"ADF"/"OCR", and AIIB's
        # "TBC" amount placeholder as currency "TBC".
        self.assertEqual(detect_currency("IDA credit"), "")
        self.assertEqual(detect_currency("ADF grant"), "")
        self.assertEqual(detect_currency("OCR loans"), "")
        self.assertEqual(detect_currency("TBC", "USD"), "USD")
        self.assertEqual(detect_currency("TBD"), "")
        self.assertEqual(detect_currency("ADF grant of EUR 5m"), "EUR")

    def test_renminbi_is_normalised_to_its_iso_code(self):
        self.assertEqual(detect_currency("RMB"), "CNY")
        self.assertEqual(parse_amount("RMB 500 million")[1], "CNY")

    def test_a_magnitude_word_makes_a_three_decimal_amount_unambiguous(self):
        self.assertEqual(parse_amount("RMB 1.448 billion"), (Decimal("1448000000.000"), "CNY"))
        self.assertEqual(parse_amount("USD 2.500 million")[0], Decimal("2500000.000"))
        self.assertEqual(parse_amount("USD 2,500 million")[0], Decimal("2500000000"))
        # Spanish groups thousands with ".": 1.448 millones is 1,448 million.
        self.assertEqual(parse_amount("USD 1.448 millones")[0], Decimal("1448000000"))

    def test_three_decimal_amounts_need_the_explicit_decimal_flag(self):
        # The default rule reads a lone "." before exactly three digits as a
        # thousands separator (European grouping) -- documented, not changed,
        # because European-format sources rely on it. Structured numeric
        # columns must pass period_is_decimal=True.
        self.assertEqual(parse_decimal("2.500"), Decimal("2500"))
        self.assertEqual(parse_decimal("0.123"), Decimal("123"))
        self.assertEqual(parse_decimal("0.123", period_is_decimal=True), Decimal("0.123"))
        self.assertEqual(parse_decimal("14630855.000", period_is_decimal=True), Decimal("14630855.000"))

    def test_amount_does_not_invent_currency(self):
        amount, currency = parse_amount("250000")
        self.assertEqual(amount, Decimal("250000"))
        self.assertEqual(currency, "")
        _, bare_symbol_currency = parse_amount("$ 250000")
        self.assertEqual(bare_symbol_currency, "")

    def test_non_sovereign_checked_before_sovereign(self):
        self.assertEqual(normalize_loan_type("Non-sovereign financing"), "Non-sovereign")
        self.assertEqual(normalize_loan_type("No soberano"), "Non-sovereign")
        self.assertEqual(normalize_loan_type("NSG"), "Non-sovereign")
        self.assertEqual(normalize_loan_type("Cuasi - soberano"), "Quasi-sovereign")
        # "Privado" (Spanish "private") -- confirmed live in CABEI's own
        # "Sector de Mercado" column ("Sector Privado", 16/150 records) --
        # previously fell through unnormalized since only the English
        # "private" was checked.
        self.assertEqual(normalize_loan_type("Sector Privado"), "Non-sovereign")

    def test_dates_are_conservative(self):
        self.assertEqual(parse_date("30 diciembre 2022", day_first=True), "2022-12-30")
        self.assertEqual(parse_date("03/04/2022"), "")
        self.assertEqual(parse_date("03/04/2022", day_first=True), "2022-04-03")
        self.assertEqual(parse_date("2022-04-03T12:30:00.000Z"), "2022-04-03")

    def test_month_year_only_dates_default_to_the_first(self):
        self.assertEqual(parse_date("December, 2018"), "2018-12-01")
        self.assertEqual(parse_date("Dec, 2018"), "2018-12-01")
        self.assertEqual(parse_date("December 2018"), "2018-12-01")

    def test_day_month_order_with_a_comma_before_the_year(self):
        # Confirmed live on NDB: "25 March, 2021" -- day-month order, unlike
        # "%B %d, %Y" (month-day order), which is a separate format already
        # in the list.
        self.assertEqual(parse_date("25 March, 2021", day_first=True), "2021-03-25")
        self.assertEqual(parse_date("07 Dec, 2020", day_first=True), "2020-12-07")

    def test_a_comma_glued_to_the_next_token_still_parses(self):
        # AIIB's own "FINANCING APPROVAL" text (confirmed live): a
        # full-width comma, which ascii_fold() NFKD-normalizes to a plain
        # "," but with no space after it -- "%B %d, %Y" requires the space
        # literally, so this used to fail every format and return "".
        self.assertEqual(parse_date("October 15，2020", day_first=False), "2020-10-15")
        # Same fix, plain ASCII comma with no space (not full-width-specific).
        self.assertEqual(parse_date("October 15,2020", day_first=False), "2020-10-15")

    def test_duration(self):
        self.assertEqual(duration_years("2020-01-01", "2021-01-01"), Decimal("1.002"))

    def test_coordinates_and_camel_case_keys(self):
        self.assertEqual(parse_coordinate("12.345"), Decimal("12.345"))
        self.assertEqual(normalized_key("approvalDate"), "approval_date")


if __name__ == "__main__":
    unittest.main()
