"""European Bank for Reconstruction and Development project CSV adapter."""

from ..base import LoadedSource
from ..browser import download_from_last_button
from ..errors import SourceLayoutChanged
from ..schema import ProjectRecord
from ..tabular import infer_format
from .common import TabularScraper


class EBRDScraper(TabularScraper):
    def map_row(self, row, source: LoadedSource) -> ProjectRecord:
        record = super().map_row(row, source)
        # The downloaded workbook has no status column at all (confirmed
        # live). But it's titled "Net Cumulative Bank Investment" and its
        # own glossary defines that as "stock of all commitments made by
        # the Bank since inception" -- which only happens at/after signing,
        # even though EBRD's live project pipeline separately has earlier
        # stages (Exploratory/Concept/Structure/Final Review) that
        # wouldn't appear in a "commitments made" ledger. So every row here
        # is inferred, not disclosed, to be at least Signed.
        record.status = "Signed"
        self.add_note(
            record,
            "Status is inferred as \"Signed\" from the source workbook's own definition "
            "(Net Cumulative Bank Investment = stock of commitments made) -- not "
            "individually disclosed per project.",
        )
        return record

    def scrape(self):
        if self.options.source_file:
            return super().scrape()
        downloaded = download_from_last_button(
            self.options.source_url or self.bank.source_url,
            headless=self.options.headless,
            timeout=self.options.timeout,
        )
        source = LoadedSource(
            downloaded.body,
            downloaded.url or self.bank.source_url,
            infer_format(downloaded.suggested_filename, "csv"),
        )
        rows = self.rows_from_source(source)
        records = [self.map_row(row, source) for row in rows]
        records = [record for record in records if record.project_name]
        if not records:
            raise SourceLayoutChanged("EBRD project download contained no recognizable project rows")
        return records
