"""Readers and field matching for official CSV, JSON, XML, TSV, and XLSX data."""

from __future__ import annotations

import csv
import io
import json
import warnings
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

from .cleaning import normalize_text, normalized_key
from .errors import OptionalDependencyMissing, SourceError


def decode_bytes(data: bytes) -> str:
    encodings = ("utf-16", "utf-8-sig", "utf-8", "cp1252", "latin-1") if data.startswith(
        (b"\xff\xfe", b"\xfe\xff")
    ) else ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def delimited_rows(data: bytes, delimiter: str | None = None) -> list[dict[str, Any]]:
    text = decode_bytes(data)
    sample = text[:8192]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
        except csv.Error:
            delimiter = "\t" if "\t" in sample else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise SourceError("Delimited source has no header row")
    return [{normalize_text(key): value for key, value in row.items() if key is not None} for row in reader]


def _largest_record_list(value: Any) -> list[dict[str, Any]]:
    candidates: list[list[dict[str, Any]]] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            records = [item for item in node if isinstance(item, dict)]
            if records:
                candidates.append(records)
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            for item in node.values():
                visit(item)

    visit(value)
    return max(candidates, key=len) if candidates else []


def json_rows(data: bytes) -> list[dict[str, Any]]:
    try:
        payload = json.loads(decode_bytes(data))
    except json.JSONDecodeError as exc:
        raise SourceError(f"Invalid JSON source: {exc}") from exc
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("records", "results", "data", "projects", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return _largest_record_list(payload)
    return []


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# IATI's <transaction-type> codelist (subset relevant to this schema): 2 is
# Commitment, 3 is Disbursement. A single activity can report several
# disbursement transactions (tranches); the commitment is normally singular.
_IATI_DISBURSEMENT_CODE = "3"
_IATI_COMMITMENT_CODE = "2"

# IATI's <activity-date type=".."> codelist: 1/3 are planned, 2/4 are actual.
# Prefer actual over planned when both are present.
_IATI_START_DATE_CODES = ("2", "1")
_IATI_END_DATE_CODES = ("4", "3")

# IATI's <participating-org role=".."> codelist: 1 is Funding -- the closest
# structured signal for "collaboration/cofinancing with other institutions".
_IATI_FUNDING_ROLE_CODE = "1"

# IATI's <participating-org role=".."> codelist: 4 is Implementing -- the
# org actually carrying out/receiving the activity (e.g. a government
# ministry or a private company), the closest structured signal for
# Sovereign/Non-sovereign. Confirmed live (adb/afdb/eib): its `type`
# attribute uses IATI's official OrganisationType codelist. Only codes
# unambiguously on one side of the sovereign/private line are translated;
# NGOs/Multilateral/Foundation/Academic/Other (21-24, 40, 60, 80, 90) are
# left untranslated rather than guessed.
_IATI_IMPLEMENTING_ROLE_CODE = "4"
_IATI_PUBLIC_SECTOR_ORG_TYPES = {"10", "11", "15", "30"}
_IATI_PRIVATE_SECTOR_ORG_TYPES = {"70", "71", "72", "73"}

# AfDB publishes each activity's financing window as a Funding-role
# <participating-org>, mixed in among its genuine external cofinanciers and
# indistinguishable from them by shape alone -- both use an "XM-DAC-*" ref,
# and unlike IsDB's windows (below) AfDB's carry refs that differ from its own
# <reporting-org>. Confirmed live (2026-09-15, all 56 country packages /
# 5,498 activities): a Funding org is present on 100% of them, and these are
# the refs belonging to the AfDB Group itself rather than to an outside
# funder. Everything not listed here (XM-DAC-EU, XM-DAC-World Bank,
# XM-DAC-EIB, XM-DAC-GEF, ...) is a real cofinancier and stays one.
_AFDB_GROUP_WINDOW_REFS = {
    "XM-DAC-46002",     # African Development Bank -- ordinary capital window
    "XM-DAC-46003",     # African Development Fund -- the concessional window
    "XM-DAC-TSF",       # Transition Support Facility (ADF-administered)
    "XM-DAC-NTF",       # Nigerian Trust Fund
    "XM-DAC-SRF",       # Special Relief Funds
    "XM-DAC-RWSSI",     # Rural Water Supply & Sanitation Initiative
    "XM-DAC-CAW",       # Climate Action Window (ADF-16)
    "XM-DAC-MIC Fund",  # Middle Income Countries technical assistance fund
    "XM-DAC-AWF",       # African Water Facility
    "XM-DAC-FAPA",      # Fund for African Private Sector Assistance
    "XM-DAC-SEFA",      # Sustainable Energy Fund for Africa
    "XM-DAC-CBFF",      # Congo Basin Forest Fund
    "XM-DAC-AGTF",      # Africa Growing Together Fund
    "XM-DAC-PSF",       # Private Sector Credit Enhancement Facility
    "XM-DAC-Zim-Fund",  # Zimbabwe Multi-donor Trust Fund
    "XM-DAC-TFCT",      # Multi donor Trust Fund for Countries in Transition
}


def _iati_transaction_fields(activity: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    disbursement_amounts: list[str] = []
    disbursement_corrections: list[str] = []
    disbursement_currency = ""
    disbursement_dates: list[str] = []
    # A <value> element's own currency attribute is optional per the IATI
    # standard -- it can be omitted when the activity declares one default
    # for all its values (e.g. AfDB/IsDB: <iati-activity default-currency=
    # "XDR" ...>, every <value> bare). Falling back to that default is the
    # difference between correctly labeling these amounts "XDR" and silently
    # leaving loan_currency blank (which reads as "unknown", not "not USD").
    activity_default_currency = activity.get("default-currency", "")
    for transaction in activity.findall("transaction"):
        type_element = transaction.find("transaction-type")
        code = type_element.get("code") if type_element is not None else None
        value_element = transaction.find("value")
        date_element = transaction.find("transaction-date")
        amount = normalize_text(value_element.text) if value_element is not None else ""
        if not amount:
            continue
        currency = (
            value_element.get("currency") or activity_default_currency or ""
        ) if value_element is not None else ""
        iso_date = date_element.get("iso-date", "") if date_element is not None else ""
        if code == _IATI_COMMITMENT_CODE:
            fields.setdefault("iati_commitment_value", amount)
            fields.setdefault("iati_commitment_currency", currency)
            fields.setdefault("iati_commitment_date", iso_date)
        elif code == _IATI_DISBURSEMENT_CODE:
            try:
                is_negative = Decimal(amount) < 0
            except InvalidOperation:
                is_negative = False
            if is_negative:
                # A negative Disbursement-type transaction is sometimes used
                # by a publisher (confirmed live: ADB) to record a large
                # balance correction/reversal rather than IATI's own
                # dedicated Reimbursement type (7). Netting it straight into
                # the total produces a "total_disbursement" that can go
                # negative, which is nonsensical for a "money paid out"
                # field. Tracked separately instead of summed in.
                disbursement_corrections.append(amount)
                continue
            disbursement_amounts.append(amount)
            disbursement_currency = disbursement_currency or currency
            if iso_date:
                disbursement_dates.append(iso_date)
    if disbursement_amounts:
        try:
            total = sum(Decimal(amount) for amount in disbursement_amounts)
        except InvalidOperation:
            total = None
        if total is not None:
            fields["iati_disbursement_value"] = format(total, "f")
            fields["iati_disbursement_currency"] = disbursement_currency
    if disbursement_corrections:
        try:
            correction_total = sum(Decimal(amount) for amount in disbursement_corrections)
        except InvalidOperation:
            correction_total = None
        if correction_total is not None:
            fields["iati_disbursement_correction"] = format(correction_total, "f")
    if disbursement_dates:
        fields["iati_first_disbursement_date"] = min(disbursement_dates)
        fields["iati_last_disbursement_date"] = max(disbursement_dates)
    return fields


def _iati_activity_date_fields(activity: Any) -> dict[str, str]:
    by_code: dict[str, str] = {}
    for date_element in activity.findall("activity-date"):
        code = date_element.get("type") or ""
        iso_date = date_element.get("iso-date", "")
        if code and iso_date:
            by_code.setdefault(code, iso_date)
    fields: dict[str, str] = {}
    for code in _IATI_START_DATE_CODES:
        if code in by_code:
            fields["iati_start_date"] = by_code[code]
            if code == "1":  # planned, not actual -- no type "2" was disclosed
                fields["iati_start_date_planned"] = "1"
            break
    for code in _IATI_END_DATE_CODES:
        if code in by_code:
            fields["iati_end_date"] = by_code[code]
            if code == "3":  # planned, not actual -- no type "4" was disclosed
                fields["iati_end_date_planned"] = "1"
            break
    return fields


def _iati_cofinancing_field(activity: Any) -> dict[str, str]:
    reporting_org = activity.find("reporting-org")
    reporting_narrative = reporting_org.find("narrative") if reporting_org is not None else None
    reporting_name = normalize_text(reporting_narrative.text) if reporting_narrative is not None else ""
    reporting_ref = normalize_text(reporting_org.get("ref")) if reporting_org is not None else ""
    partners: list[str] = []
    windows: list[str] = []
    window_refs: list[str] = []
    for org in activity.findall("participating-org"):
        if org.get("role") != _IATI_FUNDING_ROLE_CODE:
            continue
        narrative = org.find("narrative")
        name = normalize_text(narrative.text) if narrative is not None else normalize_text(org.get("ref"))
        if not name:
            continue
        # A Funding org can be one of the reporting bank's own financing
        # windows rather than a real external cofinancier, and the two
        # publishers that do this mark it differently. IsDB names the window
        # in the narrative ("IsDB - Islamic Solidarity Fund for Development")
        # while reusing its own <reporting-org> ref; AfDB gives each window a
        # distinct ref of its own (XM-DAC-46003 for the African Development
        # Fund), indistinguishable in shape from the refs it gives genuine
        # cofinanciers, so those are recognised from a curated list instead.
        # Either way the window belongs in funding_window -- calling AfDF a
        # cofinancier of an AfDB Group project misreports who lent the money.
        org_ref = normalize_text(org.get("ref"))
        is_self_by_ref = bool(org_ref) and bool(reporting_ref) and org_ref == reporting_ref
        is_self_by_name = name.casefold() == reporting_name.casefold()
        if org_ref in _AFDB_GROUP_WINDOW_REFS:
            # Deliberately checked before the self-reference tests below:
            # AfDB's <reporting-org> is XM-DAC-46002, the very same ref and
            # name as its ordinary-capital window, so treating that entry as
            # "the bank listing itself" would discard the non-concessional
            # half of the AfDB/AfDF split this field exists to capture.
            is_window = True
        elif is_self_by_ref and not is_self_by_name:
            is_window = True  # IsDB: own ref, window named in the narrative
        else:
            is_window = False
        if is_window:
            if name not in windows:
                windows.append(name)
                window_refs.append(org_ref)
        elif not is_self_by_name and not is_self_by_ref and name not in partners:
            partners.append(name)
    fields: dict[str, str] = {}
    if partners:
        fields["iati_cofinancing_partners"] = "; ".join(partners)
    if windows:
        # Sorted so a blended activity reads the same whichever order the
        # publisher happened to list its windows in -- AfDB emits both
        # "Transition Support Facility; African Development Fund" and the
        # reverse for the same pairing, which would otherwise split one real
        # category into two when grouping downstream.
        ordered = sorted(zip(windows, window_refs))
        fields["iati_funding_window"] = "; ".join(name for name, _ in ordered)
        fields["iati_funding_window_ref"] = "; ".join(ref for _, ref in ordered)
    return fields


def _iati_counterpart_org_type_field(activity: Any) -> dict[str, str]:
    for org in activity.findall("participating-org"):
        if org.get("role") != _IATI_IMPLEMENTING_ROLE_CODE:
            continue
        org_type = org.get("type")
        if org_type in _IATI_PUBLIC_SECTOR_ORG_TYPES:
            return {"iati_counterpart_org_type": "Government"}
        if org_type in _IATI_PRIVATE_SECTOR_ORG_TYPES:
            return {"iati_counterpart_org_type": "Private Sector"}
    return {}


def xml_rows(data: bytes) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise SourceError(f"Invalid XML source: {exc}") from exc
    likely = {"iati-activity", "project", "record", "item", "operation"}
    elements = [element for element in root.iter() if _local_name(element.tag).lower() in likely]
    if not elements:
        elements = list(root)
    rows: list[dict[str, Any]] = []
    for element in elements:
        row: dict[str, Any] = {f"@{_local_name(key)}": value for key, value in element.attrib.items()}
        # IATI's <transaction>, <activity-date>, and <participating-org> children
        # carry type/role codes that disambiguate several same-named siblings
        # (e.g. a Commitment transaction vs. several Disbursement transactions);
        # the generic flatten below can't tell those apart and would silently
        # keep only the first of each, so those subtrees are extracted with
        # IATI-aware logic instead and excluded from the generic pass.
        is_iati_activity = _local_name(element.tag) == "iati-activity"
        skip: set[Any] = set()
        if is_iati_activity:
            for tag in ("transaction", "activity-date", "participating-org"):
                for special_child in element.findall(tag):
                    skip.add(special_child)
                    skip.update(special_child.iter())
        parents = {child: parent for parent in element.iter() for child in parent}
        for child in element.iter():
            if child is element or child in skip:
                continue
            local_name = _local_name(child.tag)
            # IATI-style schemas wrap all human-readable text in <narrative>,
            # e.g. <title><narrative>Text</narrative></title>. Key that text by
            # the wrapping element's name (here "title"), not the literal
            # child tag "narrative" -- otherwise every wrapped field collapses
            # onto a single "narrative" key and only the first one survives.
            if local_name == "narrative":
                container = parents.get(child)
                key = _local_name(container.tag) if container is not None else local_name
            else:
                key = local_name
            value = normalize_text(child.text)
            if value:
                row.setdefault(key, value)
            for attr, attr_value in child.attrib.items():
                row.setdefault(f"{key}@{_local_name(attr)}", attr_value)
        if is_iati_activity:
            row.update(_iati_transaction_fields(element))
            row.update(_iati_activity_date_fields(element))
            row.update(_iati_cofinancing_field(element))
            row.update(_iati_counterpart_org_type_field(element))
        if row:
            rows.append(row)
    return rows


def xlsx_rows(data: bytes) -> list[dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise OptionalDependencyMissing(
            "XLSX source requires openpyxl. Run: python -m pip install '.[xlsx]'"
        ) from exc
    with warnings.catch_warnings():
        # EBRD's and the World Bank's exports carry no default style; harmless.
        warnings.filterwarnings("ignore", message="Workbook contains no default style")
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    best_rows: list[dict[str, Any]] = []
    for sheet in workbook.worksheets:
        values = list(sheet.iter_rows(values_only=True))
        if not values:
            continue
        # Locate the densest plausible header among the first 25 rows.
        header_index = max(
            range(min(25, len(values))),
            key=lambda index: sum(cell not in (None, "") for cell in values[index]),
        )
        headers = [normalize_text(cell) or f"column_{index + 1}" for index, cell in enumerate(values[header_index])]
        rows = [
            dict(zip(headers, row))
            for row in values[header_index + 1 :]
            if any(cell not in (None, "") for cell in row)
        ]
        if len(rows) > len(best_rows):
            best_rows = rows
    workbook.close()
    return best_rows


def read_rows(data: bytes, source_format: str) -> list[dict[str, Any]]:
    fmt = source_format.lower().lstrip(".")
    if fmt == "csv":
        return delimited_rows(data, ",")
    if fmt == "tsv":
        return delimited_rows(data, "\t")
    if fmt in {"json", "jsonld"}:
        return json_rows(data)
    if fmt in {"xml", "iati"}:
        return xml_rows(data)
    if fmt in {"xlsx", "xlsm"}:
        return xlsx_rows(data)
    raise SourceError(f"Unsupported source format: {source_format}")


_CONTENT_TYPE_FORMATS = {
    "text/csv": "csv",
    "application/csv": "csv",
    "text/tab-separated-values": "tsv",
    "application/json": "json",
    "application/geo+json": "json",
    "application/xml": "xml",
    "text/xml": "xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xlsx",
}


def format_from_content_type(content_type: str) -> str:
    """Map an HTTP Content-Type to a known source format, or "" if unrecognized.

    Useful when a download URL has no meaningful file extension (e.g. it
    redirects to a randomized filename), where extension-based guessing in
    ``infer_format`` would otherwise misfire.
    """
    mime = content_type.split(";", 1)[0].strip().lower()
    return _CONTENT_TYPE_FORMATS.get(mime, "")


def infer_format(path_or_url: str, fallback: str = "") -> str:
    suffix = Path(path_or_url.split("?", 1)[0]).suffix.lower().lstrip(".")
    return suffix or fallback


class NormalizedRow(dict):
    """A row whose keys are already normalized_key()-ed (normalizing it again is a no-op)."""


def normalized_row(row: dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, NormalizedRow):
        return row
    return NormalizedRow((normalized_key(key), value) for key, value in row.items())


def first(row: dict[str, Any], aliases: Iterable[str], default: Any = "") -> Any:
    normalized = normalized_row(row)
    for alias in aliases:
        value = normalized.get(normalized_key(alias))
        if value is not None and normalize_text(value):
            return value
    return default
