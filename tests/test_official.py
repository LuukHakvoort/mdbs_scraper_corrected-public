import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from mdbs_scraper import official
from mdbs_scraper.backstop import backstop_bank, covered_years
from mdbs_scraper.base import ScrapeOptions
from mdbs_scraper.official import (
    OFFICIAL_REGISTRY,
    OfficialRegistry,
    OfficialSource,
    default_directory,
    file_status,
    read_official,
)
from mdbs_scraper.reconcile import apply_official_sources
from mdbs_scraper.schema import ProjectRecord

FIXTURES = Path(__file__).parent / "fixtures" / "official"


def _source(**overrides) -> OfficialSource:
    values = dict(
        id="test-source", bank_ids=("adb",), file="adb_sovereign_sample.csv", reader="adb_sovereign",
        roles=("backbone", "backstop"), publisher_url="https://publisher.example/", as_of="2026-01-27",
        retrieved_on="2026-09-16", sha256="", unit="project",
    )
    values.update(overrides)
    if not values["sha256"] and (FIXTURES / values["file"]).is_file():
        values["sha256"] = hashlib.sha256((FIXTURES / values["file"]).read_bytes()).hexdigest()
    return OfficialSource(**values)


def _live(project_id, name="Live", **fields) -> ProjectRecord:
    return ProjectRecord(bank_id=fields.pop("bank_id", "adb"), project_id=project_id, project_name=name,
                         source_url="https://live.example/", **fields)


class RegistryTests(unittest.TestCase):
    def test_every_registered_file_is_present_and_unchanged(self):
        directory = default_directory(OFFICIAL_REGISTRY)
        if not directory.is_dir():
            self.skipTest("official files are not in this checkout")
        for source in OFFICIAL_REGISTRY.sources:
            with self.subTest(source=source.id):
                status = file_status(source, directory)
                self.assertTrue(status["present"], status["path"])
                self.assertTrue(status["matches_registry"], f"{source.file} changed; update official_sources.json")

    def test_an_unknown_role_or_reader_is_refused(self):
        with self.assertRaises(ValueError):
            _source(roles=("backbone", "decoration"))
        with self.assertRaises(ValueError):
            _source(reader="no_such_reader")

    def test_a_file_is_covered_up_to_its_last_complete_year(self):
        self.assertEqual(list(covered_years(_source(as_of="2026-01-27"), 2020, 2026))[-1], 2025)
        self.assertEqual(list(covered_years(_source(as_of="2025-12-31"), 2020, 2026))[-1], 2025)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        official.clear_cache()
        self.addCleanup(official.clear_cache)

    def _read(self, **overrides):
        source = _source(**overrides)
        return read_official(source, FIXTURES)[source.bank_ids[0]]

    def test_adb_sovereign_projects_skip_the_preamble_and_repair_mojibake(self):
        records = {record.project_id: record for record in self._read()}
        nepal = records["34304-043"]
        self.assertEqual(nepal.approval_date, "2011-09-16")
        self.assertEqual(nepal.loan_amount, Decimal("170000000.00"))
        self.assertEqual(nepal.loan_currency, "USD")
        self.assertEqual(nepal.loan_type, "Sovereign")
        self.assertEqual(nepal.official_source_id, "test-source")
        self.assertEqual(nepal.source_fields["approval_numbers"], ["2776", "3255"])
        turkiye = records["59127-001"]
        self.assertEqual(turkiye.country, "Türkiye")
        self.assertEqual(turkiye.country_code, "TUR")
        self.assertEqual(records["35174-102"].instrument_category, "technical_assistance")

    def test_zero_padded_grant_numbers_are_not_loan_numbers(self):
        records = {record.project_id: record for record in self._read()}
        self.assertEqual(records["36353-013"].source_fields["approval_numbers"], ["2372", "2373", "2988"])

    def test_adb_statement_of_loans_reads_classic_mac_lines_and_thousands(self):
        loans = {record.project_id: record for record in self._read(
            file="adb_statement_of_loans_sample.csv", reader="adb_statement_of_loans", roles=("supplement",),
            as_of="2017-12-31",
        )}
        first = loans["ADB-LOAN-0001"]
        self.assertEqual(first.approval_date, "1968-01-23")
        self.assertEqual(first.loan_amount, Decimal("5000000"))
        self.assertEqual(first.country_code, "THA")
        self.assertEqual(first.funding_window, "ADB ordinary capital resources (regular)")
        self.assertEqual(first.concessional, "No")
        self.assertEqual(first.source_fields["loan_number"], "1")
        self.assertTrue(any(loan.concessional == "Yes" for loan in loans.values()))

    def test_afdb_windows_and_identifiers(self):
        records = {record.project_id: record for record in self._read(
            bank_ids=("afdb",), file="afdb_sample.csv", reader="afdb_projects", as_of="2026-09-16",
        )}
        self.assertEqual(records["P-AO-D00-011"].funding_window, "African Development Bank")
        self.assertEqual(records["P-AO-D00-011"].concessional, "No")
        self.assertEqual(records["P-SN-K00-014"].concessional, "Yes")
        self.assertEqual(records["P-SN-K00-014"].loan_type, "Sovereign")
        blend = next(record for record in records.values() if record.concessional == "Blended")
        self.assertIsNone(blend.concessional_flag)
        multinational = next(record for record in records.values() if record.country == "Multinational")
        self.assertEqual(multinational.country_code, "")

    def test_isdb_approvals_scale_millions_and_get_stable_ids(self):
        source = _source(bank_ids=("isdb",), file="isdb_sample.csv", reader="isdb_approvals", as_of="2026-06-30")
        first = read_official(source, FIXTURES)["isdb"]
        official.clear_cache()
        second = read_official(source, FIXTURES)["isdb"]
        self.assertEqual([record.project_id for record in first], [record.project_id for record in second])
        iran = next(record for record in first if record.country == "Iran")
        self.assertEqual(iran.country_code, "IRN")
        self.assertEqual(iran.approval_year, 2016)
        self.assertEqual(iran.loan_amount, Decimal("103706203.000000"))
        self.assertEqual(iran.instrument_category, "loan")
        self.assertEqual(iran.funding_window, "IsDB - Ordinary Capital Resources")
        self.assertEqual(first[0].instrument_category, "technical_assistance")

    def test_world_bank_export_keeps_approved_projects_per_arm_with_locations(self):
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as directory:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "World Bank Projects"
            sheet.append(["World Bank Projects, data as of 09/15/2026"])
            sheet.append(["Project ID", "Country", "Project Status", "Project Name", "Board Approval Date",
                          "Project Closing Date", "IBRD Commitment", "IDA Commitment", "Lending Instrument"])
            sheet.append(["id", "countryshortname", "status", "project_name", "boardapprovaldate",
                          "closingdate", "curr_ibrd_commitment", "idacommamt", "lendinginstr"])
            sheet.append(["P1", "Kenya", "Active", "Roads", "2024-05-01T00:00:00Z", "2030-01-01", None,
                          "50000000", "Investment Project Financing"])
            sheet.append(["P2", "Peru", "Dropped", "Dropped", "2019-01-01T00:00:00Z", None, "10000000", None, None])
            sheet.append(["P3", "India", "Closed", "Blend", "2010-01-01T00:00:00Z", None, "20000000", "5000000",
                          "Development Policy Lending"])
            geo = workbook.create_sheet("GEO Locations")
            geo.append(["World Bank Projects, data as of 09/15/2026"])
            geo.append(["Project ID", "GEO Loc Name", "GEO Latitude Number", "GEO Longitude Number",
                        "Admin Unit1 Name"])
            geo.append(["P1", "Nakuru", "-0.3", "36.07", "Nakuru County"])
            path = Path(directory) / "wb.xlsx"
            workbook.save(path)
            source = _source(bank_ids=("ibrd", "ida"), file="wb.xlsx", reader="worldbank_export",
                             roles=("backstop", "extra-fields"), as_of="2026-09-15")
            by_bank = read_official(source, Path(directory))
        self.assertEqual([record.project_id for record in by_bank["ibrd"]], ["P3"])
        self.assertEqual(sorted(record.project_id for record in by_bank["ida"]), ["P1", "P3"])
        kenya = next(record for record in by_bank["ida"] if record.project_id == "P1")
        self.assertEqual(kenya.province, "Nakuru County")
        self.assertEqual(kenya.latitude, Decimal("-0.3"))
        self.assertEqual(kenya.financing_instrument, "Investment Project Financing")

    def test_aiib_list_is_read_from_its_committed_extract(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "list.pdf").write_bytes(b"%PDF-1.4 not parsed when an extract exists")
            (Path(directory) / "extracted").mkdir()
            (Path(directory) / "extracted" / "aiib_project_list.csv").write_text(
                "approval_year,head,financing_type,project_name,financing,status\n"
                "2024,India Transport,Sovereign,India: Kochi Metro Rail Project - Phase II,USD122.32 million,Approved\n"
                "2026,Brazil Digital,Nonsovereign,Brazil: Patria Fund,EUR10 million,Approved\n",
                encoding="utf-8-sig",
            )
            source = _source(bank_ids=("aiib",), file="list.pdf", reader="aiib_pdf", roles=("backstop",))
            records = read_official(source, Path(directory))["aiib"]
        india, brazil = records
        self.assertEqual((india.approval_year, india.country_code), (2024, "IND"))
        self.assertEqual(india.loan_amount, Decimal("122320000.00"))
        self.assertEqual(brazil.loan_type, "Non-sovereign")
        self.assertEqual(brazil.loan_currency, "EUR")
        self.assertIsNone(brazil.loan_amount_usd)


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        official.clear_cache()
        self.addCleanup(official.clear_cache)
        self.options = ScrapeOptions(min_year=2002, max_year=2026)

    def test_adb_backbone_merges_iati_activities_and_keeps_what_the_file_cannot_hold(self):
        registry = OfficialRegistry("unused", (_source(keep_live_if="absent_means_non_sovereign"),))
        live = [
            _live("XM-DAC-46004-34304-043-LN2776", "Kathmandu water (IATI)", commitment_date="2011-10-01",
                  completion_date="2019-12-31", total_disbursement=Decimal("10"), disbursement_currency="USD"),
            _live("XM-DAC-46004-34304-043-LN3255", "Kathmandu water AF (IATI)", commitment_date="2015-10-01",
                  total_disbursement=Decimal("5"), disbursement_currency="USD"),
            _live("XM-DAC-46004-50146-001-LN3540", "Electric Networks of Armenia", loan_type="Sovereign",
                  commitment_date="2017-06-01", approval_year=2017),
            _live("XM-DAC-46004-60000-001-LN4999", "Approved after the file", loan_type="Sovereign",
                  commitment_date="2026-05-01", approval_year=2026),
        ]
        records, report = apply_official_sources("adb", live, self.options, FIXTURES, registry)
        by_id = {record.project_id: record for record in records}
        nepal = by_id["34304-043"]
        self.assertTrue(nepal.project_name.startswith("Kathmandu Valley Water Supply Improvement"))
        self.assertEqual(nepal.completion_date, "2019-12-31")  # blank official field filled from IATI
        self.assertEqual(nepal.total_disbursement, Decimal("15"))
        self.assertIn("XM-DAC-46004-34304-043-LN2776", nepal.source_record_ids)
        armenia = by_id["50146-001"]
        self.assertEqual(armenia.loan_type, "Non-sovereign")
        self.assertIn("treated as non-sovereign", armenia.data_quality_notes)
        self.assertEqual(by_id["60000-001"].loan_type, "Sovereign")  # newer than the file, left as scraped
        info = report["sources"]["test-source"]["backbone"]
        self.assertEqual(info["live_rows_kept_approved_after_as_of"], 1)
        self.assertEqual(info["live_rows_kept_outside_file_scope"], 1)
        self.assertEqual(info["live_rows_excluded"], 0)

    def test_the_statement_of_loans_adds_terms_and_missing_loans(self):
        registry = OfficialRegistry("unused", (
            _source(),
            _source(id="loans", file="adb_statement_of_loans_sample.csv", reader="adb_statement_of_loans",
                    roles=("supplement",), as_of="2017-12-31"),
        ))
        records, report = apply_official_sources("adb", [], self.options, FIXTURES, registry)
        by_id = {record.project_id: record for record in records}
        nepal = by_id["34304-043"]
        self.assertTrue(nepal.funding_window)
        self.assertIn("ADB-LOAN-2776", nepal.source_record_ids)
        info = report["sources"]["loans"]["supplement"]
        self.assertGreaterEqual(info["loans_attached_to_existing_projects"], 2)
        self.assertNotIn("ADB-LOAN-0001", by_id)  # 1968: outside the scope
        added = [record for record in records if record.project_id.startswith("ADB-LOAN-")]
        self.assertTrue(all(record.official_source_id == "loans" for record in added))

    def test_afdb_live_rows_missing_from_the_file_are_reported_not_kept(self):
        registry = OfficialRegistry("unused", (
            _source(bank_ids=("afdb",), file="afdb_sample.csv", reader="afdb_projects", as_of="2026-09-16"),
        ))
        live = [
            _live("46002-P-AO-D00-011", "IATI copy", bank_id="afdb", approval_date="2026-07-17",
                  province="Luanda", funding_window="African Development Bank"),
            _live("46002-P-ZZ-OLD-001", "Old IATI-only", bank_id="afdb", approval_date="2010-01-01"),
        ]
        records, report = apply_official_sources("afdb", live, self.options, FIXTURES, registry)
        angola = next(record for record in records if record.project_id == "P-AO-D00-011")
        self.assertEqual(angola.province, "Luanda")
        self.assertNotIn("46002-P-ZZ-OLD-001", {record.project_id for record in records})
        [excluded] = report["excluded"]
        self.assertEqual(excluded["project_id"], "46002-P-ZZ-OLD-001")

    def test_isdb_is_matched_by_name_and_itfc_is_kept(self):
        registry = OfficialRegistry("unused", (
            _source(bank_ids=("isdb",), file="isdb_sample.csv", reader="isdb_approvals", as_of="2026-06-30",
                    keep_live_if="itfc"),
        ))
        official_rows = read_official(registry.sources[0], FIXTURES)["isdb"]
        iran = next(record for record in official_rows if record.country == "Iran")
        live = [
            _live("XM-DAC-46025-IRN0001", iran.project_name.upper(), bank_id="isdb", country_code="IRN",
                  approval_year=2017, commitment_date="2017-02-01", sector="Energy"),
            _live("XM-DAC-46025-ITFC-1", "Trade line", bank_id="isdb", instrument_category="trade_finance",
                  commitment_date="2012-01-01"),
        ]
        records, report = apply_official_sources("isdb", live, self.options, FIXTURES, registry)
        merged = next(record for record in records if record.country == "Iran")
        self.assertEqual(merged.sector, "Energy")
        self.assertEqual(merged.approval_year, 2016)  # the official year wins
        self.assertIn("XM-DAC-46025-ITFC-1", {record.project_id for record in records})
        self.assertEqual(report["sources"]["test-source"]["backbone"]["live_rows_matched"], 1)

    def test_a_missing_official_file_is_skipped_with_the_live_rows_untouched(self):
        registry = OfficialRegistry("unused", (_source(file="not-there.csv", sha256="0" * 64),))
        live = [_live("XM-DAC-46004-1-001-LN1", "Live only")]
        records, report = apply_official_sources("adb", live, self.options, FIXTURES, registry)
        self.assertEqual(records, live)
        self.assertFalse(report["sources"]["test-source"]["present"])


class BackstopTests(unittest.TestCase):
    """Uses the IsDB fixture: one approval in 2016, one in 2021, one in 2026 (not yet covered)."""

    def setUp(self):
        official.clear_cache()
        self.addCleanup(official.clear_cache)

    def _results(self, rows, **overrides):
        values = dict(bank_ids=("isdb",), file="isdb_sample.csv", reader="isdb_approvals", as_of="2026-06-30",
                      roles=("backstop",), visibility_fields=("status",))
        values.update(overrides)
        return backstop_bank("isdb", rows, 2002, 2026, FIXTURES, OfficialRegistry("unused", (_source(**values),)))

    def test_a_year_below_the_official_count_fails(self):
        results = self._results([{"approval_year": "2021", "status": "Active", "loan_amount": "1100000",
                                  "loan_currency": "USD"}])
        counts = {row["key"]: row["status"] for row in results if row["check"] == "count"}
        self.assertEqual(counts[2021], "pass")
        self.assertEqual(counts[2016], "fail")
        self.assertEqual(counts[2010], "pass")  # nothing expected
        self.assertNotIn(2026, counts)  # the file's own year is not complete
        amounts = {row["key"]: row["status"] for row in results if row["check"] == "amount"}
        self.assertEqual(amounts["2021 USD"], "pass")
        self.assertEqual(amounts["2016 USD"], "fail")

    def test_fields_the_official_file_fills_must_be_visible_in_the_output(self):
        rows = [{"approval_year": "2016", "status": ""}, {"approval_year": "2021", "status": ""}]
        [visibility] = [row for row in self._results(rows) if row["check"] == "field_visibility"]
        self.assertEqual((visibility["key"], visibility["status"]), ("status", "fail"))

    def test_visibility_compares_the_same_operations_when_ids_match(self):
        official = read_official(_source(bank_ids=("isdb",), file="isdb_sample.csv", reader="isdb_approvals"),
                                 FIXTURES)["isdb"]
        rows = [{"project_id": record.project_id, "approval_year": str(record.approval_year), "status": "Active"}
                for record in official]
        # Many extra rows from another source, all without a status.
        rows += [{"project_id": f"extra-{n}", "approval_year": "2016", "status": ""} for n in range(20)]
        [visibility] = [row for row in self._results(rows) if row["check"] == "field_visibility"]
        self.assertEqual(visibility["status"], "pass")

    def test_a_replaced_file_is_flagged_and_a_missing_one_skipped(self):
        replaced = self._results([], sha256="f" * 64)
        self.assertEqual([row["status"] for row in replaced if row["check"] == "file"], ["warn"])
        missing = self._results([], file="gone.csv", sha256="0" * 64)
        self.assertEqual([row["status"] for row in missing], ["skipped"])


if __name__ == "__main__":
    unittest.main()
