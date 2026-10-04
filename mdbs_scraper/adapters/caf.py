"""CAF (Development Bank of Latin America and the Caribbean) IATI Registry adapter.

CAF's public project portfolio (caf.com/es/proyectos/) is protected site-wide
by Incapsula bot-challenge, which also blocks headless-browser automation
(see docs/PROBLEMS.md). CAF separately publishes its full project portfolio
as an IATI activity file through the IATI Registry (CKAN publisher "caf"),
hosted without any bot-protection -- a fully official, purpose-built
open-data channel, used here instead of the protected website.

CAF replaces that file wholesale rather than appending to it. Its 2026-09-09
republication (286 activities) dropped 226 in-scope projects approved
2007-2025, 218 of them completed, that its 2025-10-10 file (478 activities)
still carried -- $30.3bn of the older file's $49.9bn. Every earlier file that
could still be found is therefore kept in the package (data/caf_iati_archive/)
and merged in behind the live one.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from pathlib import Path

from ..base import LoadedSource
from ..schema import ProjectRecord
from ..tabular import read_rows
from .common import CkanOrganizationScraper

_ARCHIVE_DIR = Path(__file__).resolve().parents[1] / "data" / "caf_iati_archive"


@dataclass(frozen=True, slots=True)
class ArchivedSnapshot:
    published: str
    filename: str
    original_url: str


# Newest first: when a project appears in several snapshots, the newest wins.
# Found 2026-09-16 via the registry's current and archived dataset pages and
# the Wayback Machine; the two caf.com files survive only as Wayback copies.
ARCHIVED_SNAPSHOTS: tuple[ArchivedSnapshot, ...] = (
    ArchivedSnapshot(
        "2025-10-10", "CAF-ActivityFile-2025-10-10.xml.gz",
        "https://stgintgcr01.blob.core.windows.net/iati-public-files/CAF-ActivityFile-2025-10-10.xml",
    ),
    ArchivedSnapshot(
        "2025-05-15", "CAF-ActivityFile-2025-05-15v2.xml.gz",
        "https://stgintgcr01.blob.core.windows.net/iati-public-files/CAF-ActivityFile-2025-05-15v2.xml",
    ),
    ArchivedSnapshot(
        "2025-04-02", "CAF-ActivityFile-2025-04-02.xml.gz",
        "https://stgintgcr01.blob.core.windows.net/iati-public-files/CAF-ActivityFile-2025-04-02.xml",
    ),
    ArchivedSnapshot(
        "2024-12-30", "caf-activityfile-2024-12-30.xml.gz",
        "https://web.archive.org/web/2025id_/https://www.caf.com/media/4673015/caf-activityfile-2024-12-30.xml",
    ),
    ArchivedSnapshot(
        "2024-08-14", "caf-activity-2024-08-14-01.xml.gz",
        "https://web.archive.org/web/2025id_/https://www.caf.com/media/4669914/caf-activity-2024-08-14-01.xml",
    ),
)


class CAFScraper(CkanOrganizationScraper):
    archive_dir = _ARCHIVE_DIR
    snapshots = ARCHIVED_SNAPSHOTS

    def scrape(self) -> list[ProjectRecord]:
        records = super().scrape()
        self.run_metadata["archived_rows_added"] = 0
        if self.options.source_file or self.options.source_url:
            # An explicit snapshot/URL is the whole source for that run.
            return records
        # IDs differ only in case between files (the 2024-08-14 file writes
        # "CFA007671", later ones "cfa..."), so compare them case-insensitively.
        seen = {record.project_id.casefold() for record in records if record.project_id}
        for snapshot in self.snapshots:
            body = gzip.decompress((self.archive_dir / snapshot.filename).read_bytes())
            source = LoadedSource(body, snapshot.original_url, "xml")
            for row in read_rows(body, "xml"):
                record = self.map_row(row, source)
                key = record.project_id.casefold()
                if not record.project_name or not key or key in seen:
                    continue
                seen.add(key)
                record.source_url = snapshot.original_url
                self.add_note(
                    record,
                    "Not in CAF's current IATI activity file; taken from CAF's archived file of "
                    f"{snapshot.published}, so status and amounts are as of that date.",
                )
                records.append(record)
                self.run_metadata["archived_rows_added"] = int(self.run_metadata["archived_rows_added"]) + 1
        return records
