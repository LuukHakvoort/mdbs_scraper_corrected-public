import json
import unittest
from pathlib import Path
from unittest.mock import patch

from mdbs_scraper.adapters.badea import (
    BADEAScraper,
    _country_coordinates,
    _parse_tableau_segments,
    _pie_rows,
    _project_name,
)
from mdbs_scraper.base import ScrapeOptions
from mdbs_scraper.config import get_bank

FIXTURE = Path(__file__).parent / "fixtures" / "badea_bootstrap_secondary_info.json"


def _load_pres_model_map() -> dict:
    with FIXTURE.open(encoding="utf-8") as handle:
        secondary_info = json.load(handle)
    return secondary_info["secondaryInfo"]["presModelMap"]


def _bootstrap_response_bytes() -> bytes:
    # Real BADEA bootstrapSession responses are two length-prefixed JSON
    # segments concatenated back to back; segment 0 (the "info" block) isn't
    # used by the decoder at all, so a minimal placeholder stands in for it
    # here while segment 1 is the real captured secondaryInfo fixture.
    with FIXTURE.open(encoding="utf-8") as handle:
        secondary_info_text = handle.read()
    placeholder = json.dumps({"sheetName": "overview"})
    return (
        f"{len(placeholder)};{placeholder}{len(secondary_info_text)};{secondary_info_text}"
    ).encode("utf-8")


class ParseTableauSegmentsTests(unittest.TestCase):
    def test_two_segments_with_multibyte_characters_parse_correctly(self):
        seg0 = {"note": "plain"}
        seg1 = {"secondaryInfo": {"country": "Côte d'Ivoire"}}
        json0 = json.dumps(seg0)
        json1 = json.dumps(seg1)
        # Tableau's own length prefix is a JS-string (UTF-16 code unit)
        # count, which undercounts the UTF-8 byte length whenever the JSON
        # contains a multi-byte character -- exactly the mismatch a naive
        # byte-offset slice trips over.
        text = f"{len(json0)};{json0}{len(json1)};{json1}"
        segments = _parse_tableau_segments(text)
        self.assertEqual(segments, [seg0, seg1])


class PieWorksheetDecodeTests(unittest.TestCase):
    def test_decodes_the_full_pie_worksheet(self):
        pres_model_map = _load_pres_model_map()
        rows = _pie_rows(pres_model_map)
        self.assertEqual(len(rows), 274)
        self.assertEqual(len({row["country"] for row in rows}), 49)

    def test_angola_breakdown_matches_the_live_map_tooltip_total(self):
        pres_model_map = _load_pres_model_map()
        rows = [row for row in _pie_rows(pres_model_map) if row["country"] == "Angola"]
        self.assertEqual(
            sorted((row["classification"], row["year"], row["amount"]) for row in rows),
            sorted([
                ("%null%", 2020, "0.5"),
                ("Agriculture, Rural Development and food security", 1981, "10.9"),
                ("Capacity Development", 1999, "0.4"),
                ("Microfinance,\xa0SMEs and Entrepreneurship Development", "%null%", "4.0"),
                ("Social", 2001, "30.4"),
                ("Transport", "%null%", "10.0"),
            ]),
        )
        # BADEA's own map tooltip reports Angola's total as 56.10 M$; these
        # one-decimal bucketed amounts sum to within their own rounding of
        # that figure (the per-record breakdown is rounded to one decimal
        # place independently of the total, so summing it does not perfectly
        # reproduce the total's own two-decimal precision).
        total = sum(float(row["amount"]) for row in rows)
        self.assertAlmostEqual(total, 56.1, delta=0.15)

    def test_country_coordinates_are_the_maps_own_marker_position(self):
        pres_model_map = _load_pres_model_map()
        coordinates = _country_coordinates(pres_model_map)
        self.assertEqual(coordinates["Angola"], (-12.836, 17.808))


class ProjectNameTests(unittest.TestCase):
    def test_includes_classification_and_year_when_both_are_known(self):
        self.assertEqual(
            _project_name("Angola", "Social", "2001"), "Angola — Social (2001)"
        )

    def test_falls_back_to_a_generic_descriptor_when_classification_is_missing(self):
        self.assertEqual(_project_name("Angola", "", "2001"), "Angola — financing (2001)")

    def test_omits_the_year_parenthetical_when_year_is_missing(self):
        self.assertEqual(_project_name("Angola", "Transport", ""), "Angola — Transport")


class BADEAScraperIntegrationTests(unittest.TestCase):
    def test_scrape_decodes_bootstrap_response_into_records(self):
        scraper = BADEAScraper(get_bank("badea"), ScrapeOptions())
        with patch(
            "mdbs_scraper.adapters.badea._fetch_bootstrap_body",
            return_value=_bootstrap_response_bytes(),
        ):
            records = scraper.run()

        self.assertEqual(len(records), 274)
        self.assertEqual(scraper.run_metadata["source_total"], 274)

        capacity_development = next(
            record
            for record in records
            if record.country == "Angola" and record.sector == "Capacity Development"
        )
        self.assertEqual(capacity_development.project_name, "Angola — Capacity Development (1999)")
        self.assertEqual(capacity_development.commitment_year, 1999)
        self.assertEqual(str(capacity_development.loan_amount), "400000.0")
        self.assertEqual(capacity_development.loan_currency, "USD")
        self.assertIsNotNone(capacity_development.latitude)
        self.assertIsNotNone(capacity_development.longitude)
        self.assertTrue(capacity_development.project_id)
        self.assertIn("not a single, individually identified project", capacity_development.data_quality_notes)

        # A row with no disclosed classification/year still produces a valid,
        # uniquely identified record rather than being dropped.
        angola_records = [record for record in records if record.country == "Angola"]
        self.assertEqual(len(angola_records), 6)
        self.assertEqual(len({record.project_id for record in angola_records}), 6)

    def test_status_is_inferred_as_committed_with_a_disclosure_note(self):
        # The dashboard discloses no status dimension at all -- only the
        # field name itself ("Commited Loans") signals anything, unlike
        # EBRD's identical gap (backed by an explicit source glossary). Every
        # record gets the same inferred value, not just some.
        scraper = BADEAScraper(get_bank("badea"), ScrapeOptions())
        with patch(
            "mdbs_scraper.adapters.badea._fetch_bootstrap_body",
            return_value=_bootstrap_response_bytes(),
        ):
            records = scraper.run()

        self.assertTrue(records)
        for record in records:
            self.assertEqual(record.status, "Committed")
        self.assertIn("inferred", records[0].data_quality_notes)
        self.assertIn("Committed", records[0].data_quality_notes)


if __name__ == "__main__":
    unittest.main()
