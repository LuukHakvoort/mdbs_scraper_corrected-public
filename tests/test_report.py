from pathlib import Path
import tempfile
import unittest

from mdbs_scraper.report import write_bank_failure_report, write_bank_report
from mdbs_scraper.schema import ProjectRecord


class WriteBankReportTests(unittest.TestCase):
    def test_success_report_includes_coverage_and_notes(self):
        records = [
            ProjectRecord(project_id="1", project_name="A", commitment_year=2020, sector="Health"),
            ProjectRecord(
                project_id="2",
                project_name="B",
                data_quality_notes="Missing sector classification",
                last_disbursement_date="2023-05-01",
                cofinancing_partners="Regional Development Fund",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = write_bank_report(
                "testbank",
                records,
                Path(directory),
                {"rows_seen": 3, "duplicates_removed": 1, "source_total": 3, "completeness": "complete"},
            )
            text = path.read_text(encoding="utf-8")
        self.assertIn("project_count: 2", text)
        self.assertIn("sector: 1/2", text)
        self.assertIn("cofinancing: 1/2", text)
        self.assertIn("disbursement timing: 1/2", text)
        self.assertIn("rows_seen: 3", text)
        self.assertIn("completeness: complete", text)
        self.assertIn("Missing sector classification", text)

    def test_loan_amount_line_flags_disclosed_zeros(self):
        records = [
            ProjectRecord(project_id="1", project_name="A", loan_amount=0),
            ProjectRecord(project_id="2", project_name="B", loan_amount=100),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = write_bank_report("testbank", records, Path(directory))
            text = path.read_text(encoding="utf-8")
        self.assertIn("loan_amount: 2/2", text)
        self.assertIn("of which 1 disclosed as exactly 0", text)

    def test_loan_amount_line_omits_zero_note_when_none_are_zero(self):
        records = [ProjectRecord(project_id="1", project_name="A", loan_amount=100)]
        with tempfile.TemporaryDirectory() as directory:
            path = write_bank_report("testbank", records, Path(directory))
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("disclosed as exactly 0", text)

    def test_a_note_containing_its_own_semicolon_is_not_fragmented(self):
        # Regression: some individual notes contain "; " as normal English
        # punctuation (e.g. common.py's loan/commitment overlap note), which
        # is also the separator join_values uses between distinct notes. The
        # report must not re-split those notes into misleading fragments.
        note = "The source reports one bank financing/loan amount; it is also recorded as the bank commitment."
        records = [ProjectRecord(project_id="1", project_name="A", data_quality_notes=note)]
        with tempfile.TemporaryDirectory() as directory:
            path = write_bank_report("testbank", records, Path(directory))
            text = path.read_text(encoding="utf-8")
        self.assertIn(note, text)
        self.assertNotIn("(1 rows) it is also recorded", text)

    def test_failure_report_includes_the_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_bank_failure_report("testbank", "SourceError: boom", Path(directory))
            text = path.read_text(encoding="utf-8")
        self.assertIn("project_count: 0", text)
        self.assertIn("SourceError: boom", text)


if __name__ == "__main__":
    unittest.main()
