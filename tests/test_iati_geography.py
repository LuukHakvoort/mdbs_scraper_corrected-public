import unittest

from mdbs_scraper.adapters.common import generic_record_from_row
from mdbs_scraper.iati_geography import (
    alpha3_from_alpha2,
    country_name_from_code,
    iso3_country_code_from_name,
    region_name_from_code,
)


class IatiGeographyLookupTests(unittest.TestCase):
    def test_country_code_known_and_unknown(self):
        self.assertEqual(country_name_from_code("DZ"), "Algeria")
        self.assertEqual(country_name_from_code("dz"), "Algeria")
        self.assertEqual(country_name_from_code("ZZ"), "")
        self.assertEqual(country_name_from_code(""), "")

    def test_region_code_known_and_unknown(self):
        self.assertEqual(region_name_from_code("298"), "Africa, regional")
        self.assertEqual(region_name_from_code("99999"), "")
        self.assertEqual(region_name_from_code(""), "")


class IatiGeographyWiringTests(unittest.TestCase):
    def test_bare_country_code_resolves_to_a_name(self):
        row = {"project_name": "Test", "country_code": "DZ"}
        record = generic_record_from_row(row)
        self.assertEqual(record.country, "Algeria")
        # The disclosed alpha-2 code has already been proven genuine by the
        # very fact it translated to a name, so it's converted to alpha-3
        # directly rather than round-tripped through name matching.
        self.assertEqual(record.country_code, "DZA")
        self.assertIn("IATI country code DZ", record.data_quality_notes)

    def test_explicit_country_text_takes_priority_over_code(self):
        row = {"project_name": "Test", "country": "Somewhere", "country_code": "DZ"}
        record = generic_record_from_row(row)
        self.assertEqual(record.country, "Somewhere")
        # "Somewhere" isn't a real country, and the disclosed "DZ" is not
        # trusted here (country text, not the code, drove `country`) -- so
        # country_code must stay blank rather than leak in Algeria's code.
        self.assertEqual(record.country_code, "")
        self.assertIn("No ISO-3 country code could be determined", record.data_quality_notes)

    def test_country_text_overrides_a_mismatched_disclosed_code(self):
        # Regression for the exact bug this feature fixes: IDB discloses its
        # own non-ISO 2-letter scheme where "PR" means Peru, not Puerto Rico
        # (confirmed live). Trusting that code would silently mislabel the
        # country; deriving from the real `country` name text instead fixes it.
        row = {"project_name": "Test", "country": "Peru", "country_code": "PR"}
        record = generic_record_from_row(row)
        self.assertEqual(record.country_code, "PER")

    def test_unrecognized_country_code_leaves_country_blank(self):
        row = {"project_name": "Test", "country_code": "ZZ"}
        record = generic_record_from_row(row)
        self.assertEqual(record.country, "")
        self.assertEqual(record.country_code, "")

    def test_region_code_used_only_when_no_country_code_present(self):
        row = {"project_name": "Test", "recipient_region_code": "298"}
        record = generic_record_from_row(row)
        self.assertEqual(record.country, "Africa, regional")
        self.assertEqual(record.country_code, "")
        self.assertIn("region", record.data_quality_notes.lower())

    def test_country_code_takes_priority_over_region_code(self):
        row = {"project_name": "Test", "country_code": "DZ", "recipient_region_code": "298"}
        record = generic_record_from_row(row)
        self.assertEqual(record.country, "Algeria")
        self.assertEqual(record.country_code, "DZA")


class Iso3CountryCodeFromNameTests(unittest.TestCase):
    def test_exact_iati_name_match(self):
        self.assertEqual(iso3_country_code_from_name("Algeria"), "DZA")

    def test_case_and_accent_insensitive_match(self):
        self.assertEqual(iso3_country_code_from_name("côte d'ivoire"), "CIV")
        self.assertEqual(iso3_country_code_from_name("CÔTE D'IVOIRE"), "CIV")

    def test_trailing_the_suffix_is_handled_generically(self):
        # IATI's own name is "Bahamas (the)"; almost no bank source repeats
        # the trailing definite article.
        self.assertEqual(iso3_country_code_from_name("Bahamas"), "BHS")
        self.assertEqual(iso3_country_code_from_name("Sudan"), "SDN")
        self.assertEqual(iso3_country_code_from_name("Philippines"), "PHL")

    def test_curated_alias_match(self):
        self.assertEqual(iso3_country_code_from_name("Bolivia"), "BOL")
        self.assertEqual(iso3_country_code_from_name("KYRGYZ REPUBLIC"), "KGZ")
        self.assertEqual(iso3_country_code_from_name("Egypt, Arab Republic of"), "EGY")
        self.assertEqual(iso3_country_code_from_name("República Dominicana"), "DOM")
        self.assertEqual(iso3_country_code_from_name("St. Lucia"), "LCA")
        self.assertEqual(iso3_country_code_from_name("West Bank and Gaza"), "PSE")

    def test_historical_code_with_no_current_iso_equivalent_is_blank(self):
        # Yugoslavia/Serbia and Montenegro/Netherlands Antilles/Neutral Zone
        # each split into multiple successor states with no single correct
        # modern answer -- left unmapped rather than guessed.
        self.assertEqual(iso3_country_code_from_name("Yugoslavia"), "")
        self.assertEqual(iso3_country_code_from_name("Serbia and Montenegro"), "")

    def test_historical_code_with_an_unambiguous_successor(self):
        self.assertEqual(iso3_country_code_from_name("Burma"), "MMR")
        self.assertEqual(iso3_country_code_from_name("Zaire"), "COD")

    def test_regional_and_institutional_text_is_never_guessed(self):
        self.assertEqual(iso3_country_code_from_name("Regional"), "")
        self.assertEqual(iso3_country_code_from_name("Africa, regional"), "")
        self.assertEqual(iso3_country_code_from_name("OECS Countries"), "")
        self.assertEqual(iso3_country_code_from_name("BADEA"), "")

    def test_blank_input_is_blank_output(self):
        self.assertEqual(iso3_country_code_from_name(""), "")


class Alpha3FromAlpha2Tests(unittest.TestCase):
    def test_known_and_unknown_and_blank(self):
        self.assertEqual(alpha3_from_alpha2("dz"), "DZA")
        self.assertEqual(alpha3_from_alpha2("YU"), "")
        self.assertEqual(alpha3_from_alpha2(""), "")


class CountryNamePinTests(unittest.TestCase):
    """Pins every country-name decision the per-country aggregation relies on.

    A change to any mapping here silently moves money between dyads (or out
    of them entirely), so it must fail loudly rather than drift.
    """

    PINNED = {
        # Real single countries that resolved to "" before 2026-09-16, found
        # in the scraped output (EIB 359 "United Kingdom" rows, CDB's own
        # member names) and in the official portfolio files.
        "United Kingdom": "GBR", "United States": "USA", "Netherlands": "NLD",
        "The Netherlands": "NLD", "Turkey": "TUR", "T\u00fcrkiye": "TUR", "Cape Verde": "CPV",
        "The Gambia": "GMB", "The Bahamas": "BHS", "British Virgin Islands": "VGB",
        "Micronesia": "FSM", "Palestine": "PSE", "Palestine*": "PSE", "Iran": "IRN",
        "Syria": "SYR", "Brunei": "BRN", "U.A.E.": "ARE", "St. Maarten": "SXM",
        "St Maarten": "SXM", "St. Martin (French part)": "MAF",
        "Saint Vincent and Grenadines": "VCT", "Kingdom of Eswatini": "SWZ",
        "S\u00e3o Tom\u00e9 e Principe": "STP", "Lao People's Democratic Rep.": "LAO",
        "Congo (Democratic Republic)": "COD", "Democratic Republic of Congo": "COD",
        "Somalia, Federal Republic of": "SOM", "TimorLeste": "TLS",
        "China, People's Republic of": "CHN", "Taipei,China": "TWN",
        "Korea, Rep.": "KOR", "Korea, Dem. People's Rep.": "PRK", "Egypt, Arab Rep.": "EGY",
        "Yemen, Rep.": "YEM", "Congo, Dem. Rep.": "COD", "Congo, Rep.": "COG",
        "Venezuela, RB": "VEN", "Hong Kong SAR, China": "HKG", "Macao SAR, China": "MAC",
        "Macedonia, FYR": "MKD", "Taiwan, China": "TWN",
        # Decisions that were already encoded and must not change.
        "Kosovo": "XKX", "Serbia": "SRB", "Montenegro": "MNE", "South Sudan": "SSD",
        "Sudan": "SDN", "Timor-Leste": "TLS", "East Timor": "TLS", "Burma": "MMR",
        "Zaire": "COD", "Swaziland": "SWZ", "West Bank and Gaza": "PSE",
        "Gambia, The": "GMB", "Bahamas, The": "BHS",
        # Deliberately unresolved: no single successor state, or not a country.
        "Serbia and Montenegro": "", "Yugoslavia": "", "Yugoslavia, former": "",
        "Czechoslovakia": "", "Channel Islands": "", "Regional": "",
        "Africa, regional": "", "OECS Countries": "", "Multinational": "",
        "EU Countries": "", "Western Balkans": "",
    }

    def test_every_pinned_name_resolves_exactly_as_recorded(self):
        for name, expected in self.PINNED.items():
            with self.subTest(name=name):
                self.assertEqual(iso3_country_code_from_name(name), expected)


if __name__ == "__main__":
    unittest.main()
