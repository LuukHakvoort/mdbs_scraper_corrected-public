import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mdbs_scraper.adapters.caf import ARCHIVED_SNAPSHOTS, ArchivedSnapshot, CAFScraper
from mdbs_scraper.adapters.common import CkanOrganizationScraper
from mdbs_scraper.base import ScrapeOptions
from mdbs_scraper.config import get_bank
from mdbs_scraper.schema import ProjectRecord


def _activity(identifier: str, title: str, year: int, status: str = "4") -> str:
    return f"""
  <iati-activity default-currency="USD">
    <iati-identifier>{identifier}</iati-identifier>
    <reporting-org ref="XM-DAC-46008"><narrative>CAF</narrative></reporting-org>
    <title><narrative>{title}</narrative></title>
    <activity-status code="{status}"/>
    <activity-date type="2" iso-date="{year}-03-01"/>
    <recipient-country code="PE"/>
    <transaction>
      <transaction-type code="2"/>
      <transaction-date iso-date="{year}-03-01"/>
      <value currency="USD" value-date="{year}-03-01">1000000</value>
    </transaction>
  </iati-activity>"""


def _file(*activities: str) -> bytes:
    return ('<?xml version="1.0"?><iati-activities version="2.03">' + "".join(activities)
            + "</iati-activities>").encode("utf-8")


class CAFArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        archive = Path(self.directory.name)
        (archive / "newer.xml.gz").write_bytes(gzip.compress(_file(
            _activity("XM-DAC-46008-cfa000001", "Newer archived copy of a live project", 2015),
            _activity("XM-DAC-46008-cfa000002", "Completed road project", 2009),
        )))
        (archive / "older.xml.gz").write_bytes(gzip.compress(_file(
            _activity("XM-DAC-46008-CFA000002", "Older copy, upper-case id", 2009),
            _activity("XM-DAC-46008-CFA000003", "Completed water project", 2011),
            _activity("XM-DAC-46008-CFA000004", "Out-of-scope 1995 project", 1995),
        )))
        self.snapshots = (
            ArchivedSnapshot("2025-10-10", "newer.xml.gz", "https://archive.example/newer.xml"),
            ArchivedSnapshot("2024-08-14", "older.xml.gz", "https://archive.example/older.xml"),
        )
        self.live = [ProjectRecord(
            project_id="XM-DAC-46008-cfa000001", project_name="Live project",
            approval_date="2015-03-01", source_url="https://live.example",
        )]

    def _scraper(self, options: ScrapeOptions) -> CAFScraper:
        scraper = CAFScraper(get_bank("caf"), options)
        scraper.archive_dir = Path(self.directory.name)
        scraper.snapshots = self.snapshots
        return scraper

    def _run(self, options: ScrapeOptions):
        scraper = self._scraper(options)
        with patch.object(CkanOrganizationScraper, "scrape", return_value=list(self.live)):
            records = scraper.run()
        return scraper, {record.project_id.casefold(): record for record in records}

    def test_the_live_file_wins_and_each_archived_project_is_added_once(self):
        scraper, records = self._run(ScrapeOptions(min_year=2002, max_year=2026))
        self.assertEqual(sorted(records), [
            "xm-dac-46008-cfa000001", "xm-dac-46008-cfa000002", "xm-dac-46008-cfa000003",
        ])
        self.assertEqual(records["xm-dac-46008-cfa000001"].project_name, "Live project")
        # The newest snapshot's copy wins even though ids differ in case.
        self.assertEqual(records["xm-dac-46008-cfa000002"].project_name, "Completed road project")
        self.assertEqual(scraper.run_metadata["archived_rows_added"], 3)

    def test_archive_only_rows_say_where_they_came_from(self):
        _, records = self._run(ScrapeOptions())
        water = records["xm-dac-46008-cfa000003"]
        self.assertEqual(water.source_url, "https://archive.example/older.xml")
        self.assertIn("archived file of 2024-08-14", water.data_quality_notes)
        self.assertNotIn("archived", records["xm-dac-46008-cfa000001"].data_quality_notes)

    def test_an_explicit_snapshot_run_does_not_merge_the_archive(self):
        scraper, records = self._run(ScrapeOptions(source_file="/tmp/caf.xml"))
        self.assertEqual(list(records), ["xm-dac-46008-cfa000001"])
        self.assertEqual(scraper.run_metadata["archived_rows_added"], 0)

    def test_every_packaged_snapshot_is_present_and_readable(self):
        scraper = CAFScraper(get_bank("caf"), ScrapeOptions())
        for snapshot in ARCHIVED_SNAPSHOTS:
            with self.subTest(snapshot=snapshot.filename):
                body = gzip.decompress((scraper.archive_dir / snapshot.filename).read_bytes())
                self.assertIn(b"<iati-activities", body[:500])


if __name__ == "__main__":
    unittest.main()
