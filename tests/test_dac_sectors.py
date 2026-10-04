import unittest

from mdbs_scraper.adapters.common import generic_record_from_row
from mdbs_scraper.dac_sectors import sector_category_from, sector_name_from_dac_code


class DacSectorLookupTests(unittest.TestCase):
    def test_purpose_codelist_known_and_unknown_codes(self):
        self.assertEqual(sector_name_from_dac_code("31161"), "Food crop production")
        self.assertEqual(sector_name_from_dac_code("11220"), "Primary education")
        self.assertEqual(sector_name_from_dac_code("99999"), "")
        self.assertEqual(sector_name_from_dac_code(""), "")

    def test_category_codelist_selected_via_vocabulary_2(self):
        self.assertEqual(sector_name_from_dac_code("236", vocabulary="2"), "Energy distribution")
        # A code that's valid in the purpose list must not leak into category lookups.
        self.assertEqual(sector_name_from_dac_code("31161", vocabulary="2"), "")

    def test_unsupported_vocabulary_never_translates(self):
        self.assertEqual(sector_name_from_dac_code("31161", vocabulary="99"), "")


class DacSectorWiringTests(unittest.TestCase):
    def test_bare_purpose_code_resolves_when_vocabulary_is_default(self):
        row = {"project_name": "Test", "sector_code": "31161"}
        record = generic_record_from_row(row)
        self.assertEqual(record.sector, "Food crop production")
        self.assertIn("5-digit purpose code", record.data_quality_notes)

    def test_bare_category_code_resolves_when_vocabulary_is_2(self):
        row = {"project_name": "Test", "sector_code": "236", "sector_vocabulary": "2"}
        record = generic_record_from_row(row)
        self.assertEqual(record.sector, "Energy distribution")
        self.assertIn("3-digit category code", record.data_quality_notes)

    def test_bare_code_under_an_unsupported_vocabulary_is_not_translated(self):
        row = {"project_name": "Test", "sector_code": "31161", "sector_vocabulary": "99"}
        record = generic_record_from_row(row)
        self.assertEqual(record.sector, "")

    def test_explicit_sector_text_takes_priority_over_dac_code(self):
        row = {"project_name": "Test", "sector": "Agriculture", "sector_code": "31161"}
        record = generic_record_from_row(row)
        self.assertEqual(record.sector, "Agriculture")

    def test_sector_category_wires_into_generic_record_from_row(self):
        row = {"project_name": "Test", "sector": "Transport"}
        record = generic_record_from_row(row)
        self.assertEqual(record.sector_category, "Transport & Storage")
        self.assertEqual(record.sector_category_code, "210")

    def test_unmapped_sector_leaves_category_blank_with_a_note(self):
        row = {"project_name": "Test", "sector": "Something Nobody Has Ever Disclosed Before"}
        record = generic_record_from_row(row)
        self.assertEqual(record.sector_category, "")
        self.assertEqual(record.sector_category_code, "")
        self.assertIn("could not be mapped to a standardized sector_category", record.data_quality_notes)


class SectorCategoryFromTests(unittest.TestCase):
    """Each tier of sector_category_from(), tried in isolation."""

    def test_tier1_disclosed_purpose_code_derives_its_category(self):
        # 21020 (some 5-digit purpose code under category 210) -> Transport.
        self.assertEqual(
            sector_category_from("", "21020", "1"), ("Transport & Storage", "210")
        )

    def test_tier1_disclosed_category_code_used_directly(self):
        self.assertEqual(sector_category_from("", "121", "2"), ("Health, General", "121"))

    def test_tier2_exact_dac_purpose_name_match(self):
        self.assertEqual(
            sector_category_from("National road construction", "", ""),
            ("Transport & Storage", "210"),
        )

    def test_tier3_exact_dac_category_name_match(self):
        # Some publishers (e.g. CABEI) disclose category-level text directly.
        self.assertEqual(
            sector_category_from("Water Supply & Sanitation", "", ""),
            ("Water Supply & Sanitation", "140"),
        )

    def test_tier4_hand_curated_bank_specific_vocabulary(self):
        self.assertEqual(sector_category_from("Transporte", "", ""), ("Transport & Storage", "210"))
        self.assertEqual(sector_category_from("Multisectorial", "", ""), ("Other Multisector", "430"))

    def test_multi_sector_text_matches_on_its_first_listed_entry(self):
        self.assertEqual(
            sector_category_from("Health; Public Administration - Health", "", ""),
            ("Health, General", "121"),
        )

    def test_unresolvable_text_returns_blank_not_a_guess(self):
        self.assertEqual(sector_category_from("Something Nobody Has Ever Disclosed Before", "", ""), ("", ""))
        self.assertEqual(sector_category_from("", "", ""), ("", ""))


if __name__ == "__main__":
    unittest.main()
