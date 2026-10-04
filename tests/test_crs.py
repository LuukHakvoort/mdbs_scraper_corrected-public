import csv
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from mdbs_scraper.crs import profile_crs
from mdbs_scraper.output import write_records
from mdbs_scraper.schema import ProjectRecord

_HEADER = ["Year", "DonorCode", "DonorName", "ProjectNumber", "RecipientName", "FlowName", "USD_Commitment",
           "ProjectTitle"]


def _crs_zip(path: Path, rows: list[list[str]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter="|")
    writer.writerow(_HEADER)
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("CRS 2003 data.txt", buffer.getvalue())


class CRSProfileTests(unittest.TestCase):
    def test_bank_years_are_profiled_against_the_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "crs").mkdir()
            _crs_zip(root / "crs" / "CRS 2002-03 data.zip", [
                ["2003", "915", "Asian Development Bank", "1985", "Nepal", "ODA Loans", "12.5", "Road"],
                ["2003", "915", "Asian Development Bank", "1986", "Nepal", "Other Official Flows (non Export Credit)", "7.5", "Power"],
                ["2003", "915", "Asian Development Bank", "1987", "Nepal", "ODA Loans", "0", "Cancelled"],
                ["2003", "1", "Austria", "X", "Nepal", "ODA Grants", "1", "Not an MDB"],
            ])
            write_records(root / "all_mdb_projects.csv", [ProjectRecord(
                bank_id="adb", project_id="ADB-LOAN-1985", project_name="Road", approval_year=2003,
                source_url="https://example.org",
            )], "csv")
            path, rows = profile_crs(root / "crs", root / "all_mdb_projects.csv", root)
            [adb] = rows
            self.assertEqual((adb["bank_id"], adb["year"]), ("adb", 2003))
            self.assertEqual(adb["crs_rows"], 3)
            self.assertEqual(adb["crs_rows_with_commitment"], 2)
            self.assertEqual(adb["crs_commitment_usd"], 20_000_000)
            self.assertEqual(adb["crs_oda_share"], 0.625)
            self.assertEqual(adb["our_operations"], 1)
            self.assertEqual(adb["crs_projects_matching_our_ids"], 1)
            self.assertTrue(path.is_file())

    def test_missing_files_explain_where_to_get_them(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError) as caught:
                profile_crs(Path(directory), None, Path(directory))
            message = str(caught.exception)
            self.assertIn("CRS 2002-03 data.zip", message)
            self.assertIn("OECD CRS (manual download)", message)
            self.assertNotIn("http", message)  # no unverified link


if __name__ == "__main__":
    unittest.main()
