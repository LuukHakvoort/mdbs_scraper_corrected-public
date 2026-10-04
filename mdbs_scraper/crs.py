"""Profile OECD CRS project-level files for the banks whose early years are thin.

The OECD's bulk CRS files ("CRS 2002-03 data.zip", ...; names as listed in
the CRS dataflow metadata at sdmx.oecd.org) could not be fetched by script:
every request to the addresses that metadata gives returned a Cloudflare
challenge page (2026-09-16), and no working download link has been verified.
They are downloaded by hand into data/external/oecd_crs/ (README, "OECD CRS"). This module only measures
what they would add -- rows, commitments, concessional share, and how many
CRS project numbers match ids already in the output -- so a decision on
backfilling can be made per bank. It does not change any output.

Column names follow the OECD "CRS bulk data - codebook"
(https://webfs.oecd.org/oda/DataCollection/Resources/DAC-tables-CRS-codebook.xlsx).
USD_Commitment is in USD millions in the bulk files.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

from .output import read_csv_rows, write_dict_rows

LOG = logging.getLogger(__name__)

DEFAULT_CRS_DIR = Path(__file__).resolve().parents[1] / "data" / "external" / "oecd_crs"
# CRS reporter names (DonorName) for each bank; matched case-insensitively.
CRS_DONORS: dict[str, tuple[str, ...]] = {
    "adb": ("asian development bank", "asian development fund"),
    "afdb": ("african development bank", "african development fund"),
    "isdb": ("islamic development bank",),
    "cdb": ("caribbean development bank",),
    "caf": ("development bank of latin america", "corporacion andina de fomento", "caf"),
}
PROFILE_FIELDS = [
    "bank_id", "year", "crs_rows", "crs_rows_with_commitment", "crs_projects", "crs_commitment_usd",
    "crs_oda_share", "our_operations", "crs_projects_matching_our_ids",
]


def _crs_rows(path: Path) -> Iterator[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith((".txt", ".csv")):
                continue
            raw = archive.read(name)
            if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                text = raw.decode("utf-16")
            else:
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = raw.decode("cp1252", errors="replace")
            first_line = text.split("\n", 1)[0]
            delimiter = "|" if first_line.count("|") > first_line.count(",") else ","
            yield from csv.DictReader(io.StringIO(text), delimiter=delimiter)


def _bank_for(donor_name: str) -> str:
    folded = (donor_name or "").strip().casefold()
    for bank_id, names in CRS_DONORS.items():
        if any(folded == name or folded.startswith(name) for name in names):
            return bank_id
    return ""


def _number_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").casefold())


def profile_crs(crs_dir: Path, combined_path: Path | None, output_dir: Path) -> tuple[Path, list[dict[str, Any]]]:
    archives = sorted(crs_dir.glob("*.zip"))
    if not archives:
        raise FileNotFoundError(
            f"No CRS zip files in {crs_dir}. Save the OECD bulk files 'CRS 2002-03 data.zip', "
            "'CRS 2004-05 data.zip', 'CRS 2006 data.zip' and 'CRS 2007 data.zip' there; see the README "
            "section 'OECD CRS (manual download)' (no download link has been verified yet)."
        )
    our_ids: dict[str, set[str]] = defaultdict(set)
    our_counts: dict[tuple[str, int], int] = defaultdict(int)
    if combined_path and combined_path.is_file():
        for row in read_csv_rows(combined_path)[1]:
            bank_id = row.get("bank_id", "")
            if bank_id not in CRS_DONORS:
                continue
            for text in (row.get("project_id", ""), *(row.get("source_record_ids", "").split("; "))):
                if text:
                    our_ids[bank_id].add(_number_key(text))
            year = row.get("approval_year") or ""
            if year.isdigit():
                our_counts[(bank_id, int(year))] += 1
    stats: dict[tuple[str, int], dict[str, Any]] = {}
    match_cache: dict[tuple[str, str], bool] = {}

    def matches(bank_id: str, key: str) -> bool:
        if (bank_id, key) not in match_cache:
            match_cache[(bank_id, key)] = bool(key) and any(key in our_id for our_id in our_ids[bank_id])
        return match_cache[(bank_id, key)]

    for archive in archives:
        LOG.info("Reading %s", archive.name)
        for row in _crs_rows(archive):
            bank_id = _bank_for(row.get("DonorName", ""))
            year = (row.get("Year") or "").strip()
            if not bank_id or not year.isdigit():
                continue
            entry = stats.setdefault((bank_id, int(year)), {
                "rows": 0, "with_commitment": 0, "projects": set(), "commitment": Decimal(0),
                "oda_commitment": Decimal(0), "matched": set(),
            })
            entry["rows"] += 1
            try:
                commitment = Decimal((row.get("USD_Commitment") or "0").strip() or "0")
            except InvalidOperation:
                commitment = Decimal(0)
            number = (row.get("ProjectNumber") or row.get("CrsID") or "").strip()
            if commitment > 0:
                entry["with_commitment"] += 1
                entry["commitment"] += commitment
                if "oda" in (row.get("FlowName") or "").casefold():
                    entry["oda_commitment"] += commitment
                if number:
                    entry["projects"].add(number)
                    if matches(bank_id, _number_key(number)):
                        entry["matched"].add(number)
    rows = []
    for (bank_id, year), entry in sorted(stats.items()):
        rows.append({
            "bank_id": bank_id, "year": year, "crs_rows": entry["rows"],
            "crs_rows_with_commitment": entry["with_commitment"], "crs_projects": len(entry["projects"]),
            "crs_commitment_usd": (entry["commitment"] * 1_000_000).quantize(Decimal(1)),
            "crs_oda_share": round(entry["oda_commitment"] / entry["commitment"], 3) if entry["commitment"] else "",
            "our_operations": our_counts.get((bank_id, year), 0),
            "crs_projects_matching_our_ids": len(entry["matched"]),
        })
    path = write_dict_rows(output_dir / "reports" / "crs_profile.csv", PROFILE_FIELDS, rows)
    return path, rows
