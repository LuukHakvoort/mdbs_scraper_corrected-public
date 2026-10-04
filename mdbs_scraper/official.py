"""Official portfolio files, used as a backbone and as a backstop.

Several banks publish a complete portfolio file that the scraper cannot reach
automatically (bot protection, manual export buttons) or that is more
complete than the channel it can reach (IATI feeds that omit closed
projects). Downloaded copies live in ``online pulled data/`` and are
registered in ``official_sources.json`` with their publication date and
sha256, so every use is traceable to one exact file.

Each reader turns one file layout into unfinalized ``ProjectRecord``s, keyed
by bank. ``reconcile.py`` merges them into the scraped records and
``backstop.py`` checks the output against them.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .base import ScrapeOptions
from .cleaning import extract_year, join_values, normalize_loan_type, normalize_text, parse_date, parse_decimal
from .errors import OptionalDependencyMissing, SourceError
from .iati_geography import alpha3_from_alpha2, iso3_country_code_from_name
from .instruments import instrument_category_from
from .schema import ProjectRecord

LOG = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).parent / "official_sources.json"
_REPO_ROOT = Path(__file__).resolve().parents[1]
ROLES = ("backbone", "supplement", "extra-fields", "backstop")


@dataclass(frozen=True, slots=True)
class OfficialSource:
    id: str
    bank_ids: tuple[str, ...]
    file: str
    reader: str
    roles: tuple[str, ...]
    publisher_url: str
    as_of: str
    retrieved_on: str
    sha256: str
    unit: str
    scope: str = ""
    # For a backbone file: which scraped records it does not cover and so
    # must never displace ("absent_means_non_sovereign" for ADB, "itfc" for
    # IsDB); see reconcile._outside_scope.
    keep_live_if: str = ""
    visibility_fields: tuple[str, ...] = ()
    amount_check: bool = True

    def __post_init__(self) -> None:
        unknown = set(self.roles) - set(ROLES)
        if unknown:
            raise ValueError(f"{self.id}: unknown roles {sorted(unknown)}")
        if self.reader not in READERS:
            raise ValueError(f"{self.id}: unknown reader {self.reader!r}")


@dataclass(frozen=True, slots=True)
class OfficialRegistry:
    directory: str
    sources: tuple[OfficialSource, ...] = field(default_factory=tuple)

    def for_bank(self, bank_id: str, role: str | None = None) -> list[OfficialSource]:
        return [
            source for source in self.sources
            if bank_id in source.bank_ids and (role is None or role in source.roles)
        ]


def load_official_registry(path: Path = _REGISTRY_PATH) -> OfficialRegistry:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = []
    for entry in payload["sources"]:
        entry = dict(entry)
        for key in ("bank_ids", "roles", "visibility_fields"):
            entry[key] = tuple(entry.get(key, ()))
        sources.append(OfficialSource(**entry))
    ids = [source.id for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("official_sources.json: duplicate source ids")
    return OfficialRegistry(payload["directory"], tuple(sources))


def default_directory(registry: OfficialRegistry) -> Path:
    return _REPO_ROOT / registry.directory


def file_status(source: OfficialSource, directory: Path) -> dict[str, Any]:
    """Whether the registered file is present and still the exact registered copy."""

    path = directory / source.file
    if not path.is_file():
        return {"path": str(path), "present": False, "sha256": "", "matches_registry": False}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path), "present": True, "sha256": digest,
        "matches_registry": digest == source.sha256,
    }


_CACHE: dict[tuple[str, str], dict[str, list[ProjectRecord]]] = {}


def read_official(source: OfficialSource, directory: Path) -> dict[str, list[ProjectRecord]]:
    """Records per bank_id from one official file (cached per process)."""

    key = (source.id, str(directory))
    if key not in _CACHE:
        path = directory / source.file
        if not path.is_file():
            raise SourceError(f"Official file for {source.id} is missing: {path}")
        by_bank = READERS[source.reader](path, source)
        for bank_id, records in by_bank.items():
            for record in records:
                record.bank_id = bank_id
                record.official_source_id = source.id
                record.source_url = record.source_url or source.publisher_url
                record.source_format = record.source_format or "official-file"
                record.source_updated_at = record.source_updated_at or source.as_of
        _CACHE[key] = by_bank
    return _CACHE[key]


def clear_cache() -> None:
    _CACHE.clear()


# --- shared helpers --------------------------------------------------------


def _decode(path: Path, encoding: str = "utf-8-sig") -> str:
    text = path.read_bytes().decode(encoding, errors="replace")
    # The ADB statement of loans uses bare CR line endings (classic Mac).
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _csv_after(text: str, header_prefix: str) -> list[dict[str, str]]:
    """Rows of a CSV whose header line starts with ``header_prefix`` (skipping preamble lines)."""

    csv.field_size_limit(1 << 30)
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.lstrip('﻿"').startswith(header_prefix):
            reader = csv.DictReader(io.StringIO("\n".join(lines[index:])))
            return [
                {normalize_text(key): value for key, value in row.items() if key is not None}
                for row in reader
            ]
    raise SourceError(f"No header starting with {header_prefix!r} found")


def _fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that was decoded as Latin-1 once already ("TÃ¼rkiye")."""

    if "Ã" not in text and "Â" not in text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _decimal(value: Any, scale: Decimal = Decimal(1)) -> Decimal | None:
    # Structured numeric columns: a lone "." is always the decimal point
    # (never European thousands grouping) -- see cleaning.parse_decimal.
    number = parse_decimal(value, period_is_decimal=True)
    return None if number is None else number * scale


def _country(name: str, fallback_iso3: str = "") -> tuple[str, str]:
    name = _fix_mojibake(normalize_text(name))
    code = iso3_country_code_from_name(name)
    if not code and re.fullmatch(r"[A-Z]{3}", fallback_iso3 or ""):
        code = fallback_iso3
    return name, code


def _synthetic_id(prefix: str, occurrences: dict[str, int], *parts: Any) -> str:
    fingerprint = "|".join(normalize_text(part) for part in parts)
    base = f"{prefix}-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]
    occurrences[base] += 1
    return base if occurrences[base] == 1 else f"{base}-{occurrences[base]}"


def _usd(record: ProjectRecord, amount: Decimal | None) -> None:
    record.loan_amount = amount
    record.loan_currency = "USD" if amount is not None else ""
    record.loan_amount_usd = amount
    record.total_commitment = amount
    record.commitment_currency = record.loan_currency
    record.total_commitment_usd = amount


# --- ADB -------------------------------------------------------------------


def adb_project_number(project_id: str) -> str:
    """ADB's project number ("44198-013") from an IATI id or a bare number."""

    match = re.search(r"(?:46004-)?(\d{4,5}-\d{3})", project_id or "")
    return match.group(1) if match else ""


def _approval_numbers(text: str) -> list[str]:
    """Loan numbers among a project's approval numbers ("2776 | 0095 | 10403").

    Zero-padded numbers are grants ("0095") and are skipped, so that grant
    0095 is never mistaken for loan 95 once the padding is gone.
    """

    return [str(int(token)) for token in re.findall(r"\d+", text or "") if not token.startswith("0")]


def _read_adb_sovereign(path: Path, source: OfficialSource) -> dict[str, list[ProjectRecord]]:
    records = []
    for row in _csv_after(_decode(path), "Project Number"):
        project_number = normalize_text(row.get("Project Number"))
        if not project_number:
            continue
        country, country_code = _country(row.get("Country", ""))
        modality = normalize_text(row.get("Project Type or Modality"))
        record = ProjectRecord(
            project_id=project_number,
            project_name=_fix_mojibake(normalize_text(row.get("Project Name"))),
            country=country,
            country_code=country_code,
            location_text=_fix_mojibake(normalize_text(row.get("Geographical Location"))),
            approval_date=parse_date(row.get("Approval Date"), day_first=False),
            status=normalize_text(row.get("Status")),
            sector=_fix_mojibake(normalize_text(row.get("Sector"))),
            subsector=_fix_mojibake(normalize_text(row.get("Subsector"))),
            loan_type="Sovereign",
            financing_instrument=modality,
            instrument_category=instrument_category_from(modality),
            project_url=f"https://www.adb.org/projects/{project_number}/main",
            source_fields={"approval_numbers": _approval_numbers(row.get("Approval Number", ""))},
        )
        _usd(record, _decimal(row.get("ADB Financing (US$)")))
        records.append(record)
    return {"adb": records}


_ADB_FUNDS = {
    "regular ocr": ("ADB ordinary capital resources (regular)", "No"),
    "concessional ocr": ("ADB concessional OCR lending (Asian Development Fund before 2017)", "Yes"),
}


def _adb_date(value: str) -> str:
    """ADB statement dates look like 23-Jan-68; two-digit years pivot at 60 (ADB began lending in 1968)."""

    text = normalize_text(value)
    match = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{2})", text)
    if not match:
        return parse_date(text, day_first=True)
    year = int(match.group(3))
    year += 1900 if year >= 60 else 2000
    try:
        return datetime.strptime(f"{match.group(1)}-{match.group(2)}-{year}", "%d-%b-%Y").date().isoformat()
    except ValueError:
        return ""


def _read_adb_statement_of_loans(path: Path, source: OfficialSource) -> dict[str, list[ProjectRecord]]:
    records = []
    thousand = Decimal(1000)
    for raw in _csv_after(_decode(path, "latin-1"), "Fund"):
        row = {key.strip(): normalize_text(value) for key, value in raw.items()}
        loan_number = row.get("Loan No.", "")
        if not loan_number.isdigit():
            continue
        country, country_code = _country(row.get("ADB Member", ""), row.get("ISO Country Code", ""))
        fund_window, concessional = _ADB_FUNDS.get(row.get("Fund", "").casefold(), ("", ""))
        grace_rate = row.get("Interest Rate (Grace period)", "")
        amortization_rate = row.get("Interest Rate (Amortization Period)", "")
        rates = join_values([grace_rate, amortization_rate])
        product = row.get("Product Type/Modality", "")
        record = ProjectRecord(
            project_id=f"ADB-LOAN-{int(loan_number):04d}",
            project_name=row.get("Title", ""),
            country=country,
            country_code=country_code,
            approval_date=_adb_date(row.get("Approval Date", "")),
            commitment_date=_adb_date(row.get("Signing Date", "")),
            completion_date=_adb_date(row.get("Closing Date", "")),
            status=row.get("Status", ""),
            sector=row.get("Sector Description", ""),
            loan_type="Non-sovereign" if "PRIVATE" in product.upper() else "Sovereign",
            financing_instrument=join_values([f"{product} loan" if product else "", row.get("Loan Type", "")]),
            instrument_category="loan",
            funding_window=fund_window,
            concessional=concessional,
            concessional_flag=Decimal(1) if concessional == "Yes" else Decimal(0) if concessional == "No" else None,
            interest_rate=rates,
            interest_rate_pct=_decimal(grace_rate) if grace_rate and not amortization_rate else None,
            total_disbursement=_decimal(row.get("Disbursement"), thousand),
            last_repayment_date=_adb_date(row.get("Last Repayment Date", "")),
            source_fields={"loan_number": str(int(loan_number)),
                           "cancellation_usd": str(_decimal(row.get("Cancellation"), thousand) or "")},
        )
        _usd(record, _decimal(row.get("Original Approved Amount"), thousand))
        if record.total_disbursement is not None:
            record.disbursement_currency = "USD"
            record.total_disbursement_usd = record.total_disbursement
        record.data_quality_notes = (
            "From ADB's Statement of Loans as of 31 Dec 2017 (amounts in USD thousands, scaled to USD); "
            "this loan is not listed in ADB's sovereign-projects file."
        )
        records.append(record)
    return {"adb": records}


# --- AfDB ------------------------------------------------------------------

_AFDB_WINDOWS = {
    "adf": ("African Development Fund", "Yes"),
    "adb": ("African Development Bank", "No"),
    "blend": ("African Development Bank; African Development Fund", "Blended"),
}


def afdb_identifier(project_id: str) -> str:
    """AfDB's own identifier ("P-Z1-IBE-025") from an IATI id ("46002-P-Z1-IBE-025")."""

    return re.sub(r"^(?:XM-DAC-)?4600\d-", "", normalize_text(project_id), flags=re.IGNORECASE).casefold()


def _read_afdb_projects(path: Path, source: OfficialSource) -> dict[str, list[ProjectRecord]]:
    records = []
    for row in _csv_after(_decode(path), "identifier"):
        identifier = normalize_text(row.get("identifier"))
        if not identifier:
            continue
        codes = [code for code in normalize_text(row.get("country_codes")).split(",") if code.strip()]
        country = normalize_text(row.get("country"))
        country_code = alpha3_from_alpha2(codes[0].strip()) if len(codes) == 1 else ""
        if not country_code:
            country_code = iso3_country_code_from_name(country)
        window, concessional = _AFDB_WINDOWS.get(normalize_text(row.get("afdb_status")).casefold(), ("", ""))
        sovereign = normalize_text(row.get("sovereign")).casefold()
        record = ProjectRecord(
            project_id=identifier,
            project_name=normalize_text(row.get("title")),
            country=country,
            country_code=country_code,
            approval_date=parse_date(row.get("Approval Date")),
            commitment_date=parse_date(row.get("Signature Date")),
            completion_date=parse_date(row.get("Completion Date")) or parse_date(row.get("Planned Completion Date")),
            status=normalize_text(row.get("activity_status")),
            sector=normalize_text(row.get("AfDB Sector")),
            loan_type="Sovereign" if sovereign == "true" else "Non-sovereign" if sovereign == "false" else "",
            funding_window=window,
            concessional=concessional,
            concessional_flag={"Yes": Decimal(1), "No": Decimal(0)}.get(concessional),
            project_url=f"https://mapafrica.afdb.org/en/projects/46002-{identifier}",
        )
        _usd(record, _decimal(row.get("total_commitments (USD)")))
        disbursed = _decimal(row.get("total_disbursements (USD)"))
        if disbursed is not None:
            record.total_disbursement = disbursed
            record.disbursement_currency = "USD"
            record.total_disbursement_usd = disbursed
        records.append(record)
    return {"afdb": records}


# --- IsDB ------------------------------------------------------------------

_ISDB_SOURCES = {
    "isdb ocr": "IsDB - Ordinary Capital Resources",
    "other funds": "IsDB - Other Funds",
}


def _read_isdb_approvals(path: Path, source: OfficialSource) -> dict[str, list[ProjectRecord]]:
    records = []
    occurrences: dict[str, int] = defaultdict(int)
    million = Decimal(1_000_000)
    for row in _csv_after(_decode(path), "Economy"):
        name = normalize_text(row.get("Project Name"))
        year = extract_year(row.get("Year"))
        if not name or year is None:
            continue
        country, country_code = _country(row.get("Economy", ""))
        amount = _decimal(row.get("US$ Million"), million)
        mode = normalize_text(row.get("Mode of Finance"))
        major_mode = normalize_text(row.get("Major Mode"))
        window = _ISDB_SOURCES.get(normalize_text(row.get("Major Source")).casefold(), "")
        record = ProjectRecord(
            project_id=_synthetic_id("isdb-approval", occurrences, country, year, name, amount),
            project_name=name,
            country=country,
            country_code=country_code,
            approval_year=year,
            commitment_year=year,
            status=normalize_text(row.get("Status")),
            financing_instrument=mode,
            instrument_category=instrument_category_from(mode, major_mode),
            funding_window=window,
            concessional="No" if window.endswith("Ordinary Capital Resources") else "",
            concessional_flag=Decimal(0) if window.endswith("Ordinary Capital Resources") else None,
            source_fields={"description": normalize_text(row.get("Description"))},
            data_quality_notes=(
                "IsDB's approvals list discloses the approval year but not the date, and no project id; "
                "the id here is derived from economy, year, name and amount."
            ),
        )
        _usd(record, amount)
        records.append(record)
    return {"isdb": records}


# --- World Bank ------------------------------------------------------------


def _xlsx_sheet_rows(path: Path, sheet: str, header_row: int) -> list[dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise OptionalDependencyMissing(
            "Reading the official World Bank export requires openpyxl. Run: python -m pip install '.[xlsx]'"
        ) from exc
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Workbook contains no default style")
        workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = workbook[sheet].iter_rows(values_only=True)
        header: list[str] = []
        result = []
        for index, values in enumerate(rows):
            if index < header_row:
                continue
            if index == header_row:
                header = [normalize_text(value) for value in values]
                continue
            result.append({key: value for key, value in zip(header, values) if key})
        return result
    finally:
        workbook.close()


def _read_worldbank_export(path: Path, source: OfficialSource) -> dict[str, list[ProjectRecord]]:
    locations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _xlsx_sheet_rows(path, "GEO Locations", 1):
        project_id = normalize_text(row.get("Project ID"))
        if project_id and project_id != "id":
            locations[project_id].append(row)
    by_bank: dict[str, list[ProjectRecord]] = {"ibrd": [], "ida": []}
    for row in _xlsx_sheet_rows(path, "World Bank Projects", 1):
        project_id = normalize_text(row.get("Project ID"))
        status = normalize_text(row.get("Project Status"))
        # The export repeats the API field names as a second header row.
        if not project_id or project_id == "id" or status.casefold() in {"dropped", "pipeline"}:
            continue
        country, country_code = _country(str(row.get("Country") or ""))
        places = locations.get(project_id, [])
        for bank_id, column in (("ibrd", "IBRD Commitment"), ("ida", "IDA Commitment")):
            amount = _decimal(row.get(column))
            if amount is None or amount <= 0:
                continue
            record = ProjectRecord(
                project_id=project_id,
                project_name=normalize_text(row.get("Project Name")),
                country=country,
                country_code=country_code,
                province=join_values(normalize_text(place.get("Admin Unit1 Name")) for place in places),
                location_text=join_values(normalize_text(place.get("GEO Loc Name")) for place in places),
                approval_date=parse_date(row.get("Board Approval Date"), day_first=False),
                completion_date=parse_date(row.get("Project Closing Date"), day_first=False),
                status=status,
                financing_instrument=normalize_text(row.get("Lending Instrument")),
                project_url=f"https://projects.worldbank.org/en/projects-operations/project-detail/{project_id}",
            )
            if len(places) == 1:
                record.latitude = _decimal(places[0].get("GEO Latitude Number"))
                record.longitude = _decimal(places[0].get("GEO Longitude Number"))
            _usd(record, amount)
            by_bank[bank_id].append(record)
    return by_bank


# --- banks whose adapter already reads this exact file ----------------------


def _read_with_adapter(path: Path, source: OfficialSource) -> dict[str, list[ProjectRecord]]:
    from .registry import create_scraper  # imported here: registry imports the adapters

    by_bank = {}
    for bank_id in source.bank_ids:
        scraper = create_scraper(bank_id, ScrapeOptions(source_file=str(path)))
        by_bank[bank_id] = scraper.scrape()
    return by_bank


# --- AIIB (browser print of the project list) -------------------------------

AIIB_EXTRACT = Path("extracted") / "aiib_project_list.csv"
AIIB_EXTRACT_FIELDS = ["approval_year", "head", "financing_type", "project_name", "financing", "status"]
_AIIB_HEADER = re.compile(
    r"APPROVAL\s+YEAR\s+MEMBER\s+SECTOR\s+FINANCING\s+TYPE\s+PROJECT\s+NAME\s+FINANCING\s+AMOUNT\s+STATUS"
)
_AIIB_FOOTER = re.compile(r"\d{2}/\d{2}/\d{4}, \d{2}:\d{2} AIIB Project List\s+about:blank \d+/\d+")
_AIIB_STATUS = r"Approved|Proposed|Terminated / Cancelled|Terminated|Cancelled|On Hold|Completed"
_AIIB_AMOUNT = r"(?:USD|EUR)\s?[\d.,]+\s?million|TBD|TBC"
_AIIB_CARD = re.compile(
    rf"\s*(?:(?P<approval_year>(?:19|20)\d{{2}}) )?(?P<head>.+?) (?P<financing_type>Sovereign|Nonsovereign) "
    rf"(?P<project_name>.+?) (?:Approved|Proposed) Financing: (?P<financing>{_AIIB_AMOUNT}) "
    rf"(?P<status>{_AIIB_STATUS})"
)
# One card is split by a page break: its amount lands after the status.
_AIIB_SPLIT_CARD = re.compile(
    rf"\s*(?:(?P<approval_year>(?:19|20)\d{{2}}) )?(?P<head>.+?) (?P<financing_type>Sovereign|Nonsovereign) "
    rf"(?P<name_start>.+?) (?:Approved|Proposed) Financing: (?P<status>{_AIIB_STATUS}) "
    rf"(?P<name_end>.+?) (?P<financing>{_AIIB_AMOUNT})\s*$"
)


def extract_aiib_pdf(pdf_path: Path) -> list[dict[str, str]]:
    """Text-extract the AIIB project-list print into card rows (needs pypdf)."""

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise OptionalDependencyMissing(
            "Extracting the AIIB project-list PDF requires pypdf. Run: python -m pip install '.[pdf]'"
        ) from exc
    pages = []
    for page in PdfReader(str(pdf_path)).pages:
        text = _AIIB_FOOTER.sub(" ", _AIIB_HEADER.sub(" ", page.extract_text() or ""))
        pages.append(text)
    text = re.sub(r"\s+", " ", " ".join(pages))
    cards = [match.groupdict() for match in _AIIB_CARD.finditer(text)]
    leftover = _AIIB_CARD.sub(" ", text)
    split = _AIIB_SPLIT_CARD.match(leftover)
    if split:
        card = split.groupdict()
        card["project_name"] = f"{card.pop('name_start')} {card.pop('name_end')}"
        cards.append(card)
    elif leftover.strip():
        LOG.warning("AIIB PDF: text not matched to any project card: %s", leftover.strip()[:200])
    rows = []
    for card in cards:
        row = {key: normalize_text(card.get(key)) for key in AIIB_EXTRACT_FIELDS}
        if not row["approval_year"]:
            # A page break can push the tail of the previous card in front of
            # this one's year ("..., and Reconstruction 2021 China Transport").
            tail = re.search(r"((?:19|20)\d{2}) (\D*)$", row["head"])
            if tail:
                row["approval_year"], row["head"] = tail.group(1), tail.group(2)
        rows.append(row)
    return rows


def write_aiib_extract(pdf_path: Path) -> Path:
    target = pdf_path.parent / AIIB_EXTRACT
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AIIB_EXTRACT_FIELDS)
        writer.writeheader()
        writer.writerows(extract_aiib_pdf(pdf_path))
    return target


def _read_aiib_pdf(path: Path, source: OfficialSource) -> dict[str, list[ProjectRecord]]:
    extract = path.parent / AIIB_EXTRACT
    if extract.is_file():
        rows = list(csv.DictReader(io.StringIO(extract.read_text(encoding="utf-8-sig"))))
    else:
        rows = extract_aiib_pdf(path)
    records = []
    occurrences: dict[str, int] = defaultdict(int)
    for row in rows:
        name = normalize_text(row.get("project_name"))
        country_text = name.split(":", 1)[0] if ":" in name else ""
        country, country_code = _country(country_text)
        financing = normalize_text(row.get("financing"))
        amount = _decimal(re.sub(r"[^\d.]", "", financing), Decimal(1_000_000)) if "million" in financing else None
        record = ProjectRecord(
            project_id=_synthetic_id("aiib-list", occurrences, name),
            project_name=name,
            country=country,
            country_code=country_code,
            approval_year=extract_year(row.get("approval_year")),
            status=normalize_text(row.get("status")),
            loan_type=normalize_loan_type(row.get("financing_type")),
        )
        record.commitment_year = record.approval_year
        if amount is not None:
            currency = "EUR" if financing.upper().startswith("EUR") else "USD"
            _usd(record, amount)
            record.loan_currency = record.commitment_currency = currency
            if currency != "USD":
                record.loan_amount_usd = record.total_commitment_usd = None
        records.append(record)
    return {"aiib": records}


READERS: dict[str, Callable[[Path, OfficialSource], dict[str, list[ProjectRecord]]]] = {
    "adb_sovereign": _read_adb_sovereign,
    "adb_statement_of_loans": _read_adb_statement_of_loans,
    "afdb_projects": _read_afdb_projects,
    "isdb_approvals": _read_isdb_approvals,
    "worldbank_export": _read_worldbank_export,
    "adapter_snapshot": _read_with_adapter,
    "aiib_pdf": _read_aiib_pdf,
}

OFFICIAL_REGISTRY = load_official_registry()
