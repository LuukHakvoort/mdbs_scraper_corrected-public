import unittest

from mdbs_scraper.config import DEFAULT_YEAR_FILTER


class DefaultYearFilterTests(unittest.TestCase):
    def test_shipped_year_filter_json_is_2002_2026_enabled(self):
        self.assertTrue(DEFAULT_YEAR_FILTER.enabled)
        self.assertEqual(DEFAULT_YEAR_FILTER.min_year, 2002)
        self.assertEqual(DEFAULT_YEAR_FILTER.max_year, 2026)


if __name__ == "__main__":
    unittest.main()
