import argparse
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from contextlib import redirect_stderr
from datetime import date
import io

from mdbs_scraper.cli import _apply_default_year_filter, _child_argv, _needs_isolation, build_parser, main
from mdbs_scraper.config import BANKS, YearFilterDefault
from mdbs_scraper.coverage import coverage_by_year
from mdbs_scraper.output import write_records
from mdbs_scraper.schema import ProjectRecord


FIXTURES = Path(__file__).parent / "fixtures"

_PRE_2002_PROJECT_CSV = (
    "project_id,project_name,country,country_code,province,latitude,longitude,approval_date,"
    "commitment_date,completion_date,status,sector,subsector,loan_type,financing_instrument,"
    "loan_amount,loan_currency,total_commitment,commitment_currency,total_disbursement,"
    "disbursement_currency,first_disbursement_date,last_disbursement_date,cofinancing_partners,"
    "cofinancing_amount,cofinancing_currency,conditionality,conditionality_source_url,"
    "project_url,last_updated\n"
    "TEST-002,Pre-2002 Rural Roads,Exampleland,EXL,,,,1995-02-10,1995-03-05,1999-03-05,"
    "Closed,Transport,Rural roads,Sovereign,Investment loan,USD 5 million,USD,USD 5 million,"
    "USD,USD 5 million,USD,,,,,,,,,\n"
)


class CLITests(unittest.TestCase):
    def test_all_enabled_adapters_run_end_to_end_with_snapshots(self):
        common = str(FIXTURES / "common_projects.csv")
        worldbank = str(FIXTURES / "worldbank_projects.json")
        eib = str(FIXTURES / "eib_projects.json")
        with tempfile.TemporaryDirectory() as directory:
            args = ["--all-banks", "--output-dir", directory, "--combine"]
            for bank_id in BANKS:
                if bank_id in {"ibrd", "ida"}:
                    source = worldbank
                elif bank_id == "eib":
                    source = eib
                else:
                    source = common
                args += ["--source-file", f"{bank_id}={source}"]
            exit_code = main(args)
            self.assertEqual(exit_code, 0)
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["record_count"], len(BANKS))
            self.assertFalse(manifest["failures"])
            self.assertEqual(len(manifest["successful_banks"]), len(BANKS))
            self.assertEqual(manifest["bank_metadata"]["cabei"]["rows_seen"], 1)
            self.assertEqual(
                manifest["bank_metadata"]["cabei"]["completeness"],
                "source-total-unavailable",
            )
            with (Path(directory) / "project_counts_and_coverage.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                summary = list(csv.DictReader(handle))
            self.assertEqual(len(summary), len(BANKS))
            self.assertTrue(all(row["project_count"] == "1" for row in summary))
            self.assertIn("completeness", summary[0])
            self.assertIn("rows_seen", summary[0])
            self.assertTrue((Path(directory) / "all_mdb_projects.csv").is_file())

    def test_partial_failure_is_explicit_in_manifest_and_coverage(self):
        worldbank = str(FIXTURES / "worldbank_projects.json")
        with tempfile.TemporaryDirectory() as directory:
            exit_code = main([
                "--banks", "adb", "ida", "--output-dir", directory,
                "--source-file", "adb=/definitely/missing/adb.csv",
                "--source-file", f"ida={worldbank}", "--allow-partial",
            ])
            self.assertEqual(exit_code, 0)
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("adb", manifest["failures"])
            self.assertEqual(manifest["successful_banks"], ["ida"])
            with (Path(directory) / "project_counts_and_coverage.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                rows = {row["bank_id"]: row for row in csv.DictReader(handle)}
            self.assertEqual(rows["adb"]["status"], "failed")
            self.assertEqual(rows["ida"]["status"], "success")

    @staticmethod
    def _default_run_metadata():
        return {
            "rows_seen": 0, "rows_retained": 0, "duplicates_removed": 0,
            "source_total": None, "completeness": "source-total-unavailable",
        }

    def test_dynamic_bank_retries_once_after_a_transient_failure(self):
        # ebrd is Playwright-driven (dynamic: true) -- the known transient
        # macOS/Playwright driver failure under concurrent runs should be
        # recovered from automatically, without the caller noticing.
        # (eib used to be the example here too, before it was switched to a
        # plain IATI XML download -- dynamic: false -- to work around
        # eib.org's Cloudflare block; see docs/PROBLEMS.md.)
        self.assertTrue(BANKS["ebrd"].dynamic)
        failing_scraper = Mock()
        failing_scraper.run.side_effect = RuntimeError("transient Playwright driver failure")
        succeeding_scraper = Mock()
        succeeding_scraper.run.return_value = []
        succeeding_scraper.run_metadata = self._default_run_metadata()

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "mdbs_scraper.cli.create_scraper",
                side_effect=[failing_scraper, succeeding_scraper],
            ) as mock_create:
                exit_code = main(["--bank", "ebrd", "--output-dir", directory, "--no-official"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(mock_create.call_count, 2)
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["successful_banks"], ["ebrd"])
            self.assertFalse(manifest["failures"])

    def test_non_dynamic_bank_does_not_retry(self):
        # adb is IATI-sourced (dynamic: false) -- a real failure there isn't
        # the transient Playwright issue the retry exists for, and retrying
        # unconditionally would just double the work for no benefit.
        self.assertFalse(BANKS["adb"].dynamic)
        failing_scraper = Mock()
        failing_scraper.run.side_effect = RuntimeError("a real, non-transient failure")

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "mdbs_scraper.cli.create_scraper", return_value=failing_scraper,
            ) as mock_create:
                exit_code = main([
                    "--bank", "adb", "--output-dir", directory, "--allow-partial", "--no-official",
                ])
            self.assertEqual(exit_code, 0)
            self.assertEqual(mock_create.call_count, 1)
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("adb", manifest["failures"])

    def test_default_year_filter_drops_pre_2002_records_unless_overridden(self):
        with tempfile.TemporaryDirectory() as directory:
            source = str(Path(directory) / "pre_2002.csv")
            Path(source).write_text(_PRE_2002_PROJECT_CSV, encoding="utf-8")

            exit_code = main(["--bank", "adb", "--output-dir", directory, "--source-file", f"adb={source}"])
            self.assertEqual(exit_code, 0)
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["bank_metadata"]["adb"]["rows_retained"], 0)
            self.assertEqual(manifest["filters"], {"min_year": 2002, "max_year": 2026})

            exit_code = main([
                "--bank", "adb", "--output-dir", directory, "--source-file", f"adb={source}",
                "--min-year", "1900", "--max-year", "2100",
            ])
            self.assertEqual(exit_code, 0)
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["bank_metadata"]["adb"]["rows_retained"], 1)


class ApplyDefaultYearFilterTests(unittest.TestCase):
    def test_explicit_cli_years_are_left_untouched(self):
        args = argparse.Namespace(min_year=2010, max_year=2015)
        _apply_default_year_filter(args, YearFilterDefault(enabled=True, min_year=2002, max_year=2026))
        self.assertEqual((args.min_year, args.max_year), (2010, 2015))

    def test_unset_years_are_filled_from_the_default(self):
        args = argparse.Namespace(min_year=None, max_year=None)
        _apply_default_year_filter(args, YearFilterDefault(enabled=True, min_year=2010, max_year=2015))
        self.assertEqual((args.min_year, args.max_year), (2010, 2015))

    def test_disabled_default_leaves_years_unset(self):
        args = argparse.Namespace(min_year=None, max_year=None)
        _apply_default_year_filter(args, YearFilterDefault(enabled=False, min_year=2010, max_year=2015))
        self.assertEqual((args.min_year, args.max_year), (None, None))


def _record(bank_id: str, project_id: str, year: int, status: str = "Closed") -> ProjectRecord:
    return ProjectRecord(
        bank_id=bank_id, bank_name=bank_id.upper(), bank_abbreviation=bank_id.upper(),
        project_id=project_id, project_name=f"Project {project_id}", country="Kenya",
        country_code="KEN", approval_date=f"{year}-05-01", approval_year=year,
        commitment_year=year, status=status, source_url="https://example.org",
    )


class CombineOnlyTests(unittest.TestCase):
    def _write_bank(self, directory: str, bank_id: str, count: int) -> None:
        records = [_record(bank_id, f"{bank_id}-{index}", 2010) for index in range(count)]
        write_records(Path(directory) / f"{bank_id}_projects.csv", records, "csv")

    def test_the_combined_file_is_rebuilt_from_per_bank_files_in_registry_order(self):
        with tempfile.TemporaryDirectory() as directory:
            self._write_bank(directory, "ebrd", 2)
            self._write_bank(directory, "adb", 3)
            exit_code = main(["--banks", "ebrd", "adb", "--combine-only", "--output-dir", directory, "--no-official"])
            self.assertEqual(exit_code, 0)
            with (Path(directory) / "all_mdb_projects.csv").open(encoding="utf-8-sig") as handle:
                banks = [row["bank_id"] for row in csv.DictReader(handle)]
            self.assertEqual(banks, ["ebrd", "ebrd", "adb", "adb", "adb"])
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["mode"], "combine-only")
            self.assertEqual(manifest["record_count"], 5)
            self.assertEqual([entry["rows"] for entry in manifest["inputs"]["per_bank_files"]], [2, 3])
            self.assertTrue((Path(directory) / "reports" / "coverage_by_year.csv").is_file())

    def test_a_missing_per_bank_file_is_an_error_unless_partial_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            self._write_bank(directory, "adb", 1)
            with redirect_stderr(io.StringIO()) as stderr:
                exit_code = main(["--banks", "adb", "aiib", "--combine-only", "--output-dir", directory, "--no-official"])
            self.assertEqual(exit_code, 2)
            self.assertIn("aiib", stderr.getvalue())
            self.assertFalse((Path(directory) / "all_mdb_projects.csv").exists())
            with redirect_stderr(io.StringIO()):
                exit_code = main([
                    "--banks", "adb", "aiib", "--combine-only", "--allow-partial", "--output-dir", directory,
                    "--no-official",
                ])
            self.assertEqual(exit_code, 0)
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["missing_banks"], ["aiib"])

    def test_a_file_with_an_older_column_layout_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "adb_projects.csv").write_text(
                "bank_id,project_id,project_name\nadb,1,Old layout\n", encoding="utf-8-sig"
            )
            with redirect_stderr(io.StringIO()) as stderr:
                exit_code = main(["--bank", "adb", "--combine-only", "--output-dir", directory, "--no-official"])
            self.assertEqual(exit_code, 2)
            self.assertIn("column layout", stderr.getvalue())

    def test_a_partial_combine_names_the_banks_it_left_out(self):
        worldbank = str(FIXTURES / "worldbank_projects.json")
        with tempfile.TemporaryDirectory() as directory:
            with redirect_stderr(io.StringIO()) as stderr:
                exit_code = main([
                    "--bank", "ida", "--combine", "--output-dir", directory, "--source-file", f"ida={worldbank}",
                ])
            self.assertEqual(exit_code, 0)
            self.assertIn("holds only 1 of", stderr.getvalue())
            self.assertIn("aiib", stderr.getvalue())
            self.assertIn("--combine-only", stderr.getvalue())


class IsolationTests(unittest.TestCase):
    def test_browser_banks_are_isolated_only_in_multi_bank_runs_without_a_snapshot(self):
        args = build_parser().parse_args(["--banks", "ebrd", "adb"])
        self.assertTrue(_needs_isolation(args, ["ebrd", "adb"], "ebrd"))
        self.assertFalse(_needs_isolation(args, ["ebrd", "adb"], "adb"))
        self.assertFalse(_needs_isolation(args, ["ebrd"], "ebrd"))
        snapshot = build_parser().parse_args(["--banks", "ebrd", "adb", "--source-file", "ebrd=/tmp/x.xlsx"])
        self.assertFalse(_needs_isolation(snapshot, ["ebrd", "adb"], "ebrd"))
        opted_out = build_parser().parse_args(["--banks", "ebrd", "adb", "--no-isolation"])
        self.assertFalse(_needs_isolation(opted_out, ["ebrd", "adb"], "ebrd"))

    def test_the_child_process_gets_the_same_scope_and_only_its_own_overrides(self):
        args = build_parser().parse_args([
            "--banks", "ebrd", "adb", "--min-year", "2005", "--max-year", "2010", "--no-headless",
            "--source-url", "ebrd=https://example.org/ebrd", "--source-url", "adb=https://example.org/adb",
        ])
        argv = _child_argv(args, "ebrd", Path("/tmp/result.pickle"))
        self.assertEqual(argv[1:5], ["-m", "mdbs_scraper", "--bank", "ebrd"])
        self.assertIn("--child-result", argv)
        self.assertEqual(argv[argv.index("--min-year") + 1], "2005")
        self.assertEqual(argv[argv.index("--max-year") + 1], "2010")
        self.assertIn("--no-headless", argv)
        self.assertIn("ebrd=https://example.org/ebrd", argv)
        self.assertNotIn("adb=https://example.org/adb", argv)

    def test_an_isolated_bank_is_collected_through_its_child_result(self):
        def fake_child(argv, **kwargs):
            import pickle
            if "--child-result" not in argv:  # the manifest's git-commit lookup
                return Mock(stdout="abc123\n", returncode=0)
            result_path = Path(argv[argv.index("--child-result") + 1])
            with result_path.open("wb") as handle:
                pickle.dump({
                    "error": None,
                    "records": [_record("ebrd", "E-1", 2012)],
                    "metadata": {"rows_seen": 1, "rows_retained": 1, "duplicates_removed": 0,
                                 "source_total": None, "completeness": "source-total-unavailable"},
                    "fetches": [{"url": "https://example.org/x", "sha256": "0" * 64}],
                }, handle)
            return Mock(returncode=0)

        worldbank = str(FIXTURES / "worldbank_projects.json")
        with tempfile.TemporaryDirectory() as directory:
            with patch("mdbs_scraper.cli.subprocess.run", side_effect=fake_child) as run:
                exit_code = main([
                    "--banks", "ebrd", "ida", "--output-dir", directory, "--source-file", f"ida={worldbank}",
                    "--no-official",
                ])
            self.assertEqual(exit_code, 0)
            [child_call] = [call for call in run.call_args_list if "--child-result" in call.args[0]]
            # posix_spawn on macOS: forked children segfault after the parent
            # has used the system networking frameworks.
            self.assertIs(child_call.kwargs.get("close_fds"), False)
            self.assertNotIn("cwd", child_call.kwargs)
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["successful_banks"], ["ebrd", "ida"])
            self.assertEqual(manifest["inputs"]["fetched"][0]["url"], "https://example.org/x")
            self.assertIn("ida", manifest["inputs"]["source_files"])


class ApprovalYearTests(unittest.TestCase):
    def test_the_scope_filter_uses_the_approval_year_not_the_signing_year(self):
        # IDB: approved 1999, signed 2003 -- used to be kept (signing year),
        # making IDB disagree with its own approval-year project list.
        source_csv = _PRE_2002_PROJECT_CSV.replace("1995-02-10,1995-03-05", "1999-02-10,2003-03-05")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "idb.csv"
            source.write_text(source_csv, encoding="utf-8")
            main(["--bank", "adb", "--output-dir", directory, "--source-file", f"adb={source}"])
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["bank_metadata"]["adb"]["rows_retained"], 0)

    def test_without_an_approval_date_the_commitment_year_stands_in_with_a_note(self):
        from mdbs_scraper.base import ScrapeOptions
        from mdbs_scraper.registry import create_scraper
        scraper = create_scraper("adb", ScrapeOptions())
        record = ProjectRecord(project_id="X-1", project_name="No approval date",
                               commitment_date="2011-06-30", source_url="https://example.org")
        [finalized] = scraper.finalize([record])
        self.assertEqual(finalized.approval_year, 2011)
        self.assertIn("no approval date", finalized.data_quality_notes)


class CoverageByYearTests(unittest.TestCase):
    def test_thin_years_are_flagged_low_and_uncovered_years_are_not(self):
        with tempfile.TemporaryDirectory() as directory:
            records = [_record("aiib", f"A-{year}-{n}", year) for year in range(2016, 2024) for n in range(10)]
            records += [_record("aiib", "A-2024-0", 2024)]
            write_records(Path(directory) / "aiib_projects.csv", records, "csv")
            rows = coverage_by_year(Path(directory), ["aiib"], 2002, 2026, today=date(2025, 3, 1))
            by_year = {row["year"]: row for row in rows}
            self.assertEqual(by_year[2010]["flag"], "not_covered")  # AIIB was founded in 2016
            self.assertEqual(by_year[2010]["covered"], 0)
            self.assertEqual(by_year[2020]["flag"], "")
            self.assertEqual(by_year[2024]["flag"], "low")
            self.assertEqual(by_year[2020]["not_active"], 10)

    def test_a_source_whose_newest_approval_is_old_is_flagged_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            records = [_record("ida", f"P{year}", year, "Active") for year in range(2002, 2025)]
            write_records(Path(directory) / "ida_projects.csv", records, "csv")
            rows = coverage_by_year(Path(directory), ["ida"], 2002, 2026, today=date(2026, 9, 16))
            self.assertIn("stale", [row for row in rows if row["year"] == 2026][0]["flag"])
            self.assertEqual([row for row in rows if row["year"] == 2010][0]["not_active"], 0)

    def test_a_current_year_approval_without_a_date_is_not_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            records = [_record("isdb", f"I{year}", year, "Active") for year in range(2002, 2025)]
            dateless = _record("isdb", "I2026", 2026, "Active")
            dateless.approval_date = ""
            write_records(Path(directory) / "isdb_projects.csv", records + [dateless], "csv")
            rows = coverage_by_year(Path(directory), ["isdb"], 2002, 2026, today=date(2026, 9, 16))
            self.assertNotIn("stale", [row for row in rows if row["year"] == 2026][0]["flag"])


class StrictBackstopTests(unittest.TestCase):
    def test_a_failing_official_check_only_changes_the_exit_code_when_strict(self):
        failing_result = [{"bank_id": "adb", "source_id": "x", "check": "count", "key": 2010, "status": "fail"}]
        with tempfile.TemporaryDirectory() as directory:
            records = [_record("adb", "A-1", 2010)]
            write_records(Path(directory) / "adb_projects.csv", records, "csv")
            summary = Path(directory) / "reports" / "backstop_summary.csv"
            summary.parent.mkdir(parents=True)
            summary.write_text("x\n", encoding="utf-8")
            with patch("mdbs_scraper.cli.run_backstop", return_value=(summary, failing_result)):
                with redirect_stderr(io.StringIO()) as stderr:
                    relaxed = main(["--bank", "adb", "--combine-only", "--output-dir", directory])
                    strict = main(["--bank", "adb", "--combine-only", "--output-dir", directory,
                                   "--strict-backstop"])
            self.assertEqual((relaxed, strict), (0, 3))
            self.assertIn("below the official files", stderr.getvalue())
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["backstop"], {"x": {"fail": 1}})


if __name__ == "__main__":
    unittest.main()
