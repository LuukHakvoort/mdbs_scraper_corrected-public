"""Verified public-source registry for the supported institutions.

Bank definitions live in ``banks.json`` next to this module, not in Python
code, so enabling/disabling a bank or correcting its metadata is a data edit,
not a code edit. A URL is a data download when an official structured
download exists; otherwise it is the official portfolio page and the adapter
performs explicit HTML extraction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_REGISTRY_PATH = Path(__file__).parent / "banks.json"
_YEAR_FILTER_PATH = Path(__file__).parent / "year_filter.json"


@dataclass(frozen=True, slots=True)
class BankDefinition:
    id: str
    name: str
    abbreviation: str
    website: str
    source_url: str
    source_format: str
    method: str
    coverage_notes: str
    dynamic: bool = False
    enabled: bool = True
    # Declarative per-bank scraping knobs. Defaults match the generic
    # TabularScraper/HTMLPortfolioScraper behavior in adapters/common.py, so a
    # bank only needs an entry in banks.json when it deviates from the default.
    default_currency: str = ""
    day_first: bool | None = None
    project_href_pattern: str = r"project|operation|loan"
    pagination_href_pattern: str = r"(?:[?&]page=\d+|/page/\d+/?)"
    max_listing_pages: int = 100
    # Forces a lone "." in an amount to always mean the decimal point. Only
    # needed for sources whose amount column has a fixed decimal-digit count
    # (e.g. "14630855.000") that the shared European-thousands heuristic would
    # otherwise misread as thousands grouping.
    amount_period_is_decimal: bool = False
    # First approval year this bank's sources genuinely cover (the bank's
    # founding, or a documented publication floor such as CDB's 2013 map).
    # Years before it are "not covered", not "low": coverage_by_year does not
    # flag them and the balanced country-year panel leaves them blank rather
    # than claiming a zero. None means the whole default scope is covered.
    coverage_start_year: int | None = None
    # Last approval year covered, when a source is known to stop early (e.g.
    # BADEA's map ends in 2022). None means "up to the newest approval seen".
    coverage_end_year: int | None = None


def _load_registry(path: Path) -> tuple[dict[str, BankDefinition], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    banks = {entry["id"]: BankDefinition(**entry) for entry in payload["banks"]}
    aliases = dict(payload["aliases"])
    return banks, aliases


_ALL_BANKS, BANK_ALIASES = _load_registry(_REGISTRY_PATH)


@dataclass(frozen=True, slots=True)
class YearFilterDefault:
    """Default --min-year/--max-year applied when a run doesn't pass either.

    Lives in year_filter.json (not code) so the default scope, or whether it
    applies at all, is a data edit -- same rationale as banks.json above.
    """

    enabled: bool = True
    min_year: int | None = 2002
    max_year: int | None = 2026


def _load_year_filter(path: Path) -> YearFilterDefault:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return YearFilterDefault(**payload)


DEFAULT_YEAR_FILTER: YearFilterDefault = _load_year_filter(_YEAR_FILTER_PATH)

# Public registry: only banks enabled in banks.json. Disabled banks (e.g. IMF,
# whose arrangements are not MDB investment projects) stay documented in the
# config file but are excluded here, same as if their entry did not exist.
BANKS: dict[str, BankDefinition] = {
    bank_id: bank for bank_id, bank in _ALL_BANKS.items() if bank.enabled
}


def canonical_bank_id(bank_id: str) -> str:
    normalized = bank_id.strip().lower()
    return BANK_ALIASES.get(normalized, normalized)


def get_bank(bank_id: str) -> BankDefinition:
    canonical = canonical_bank_id(bank_id)
    try:
        return BANKS[canonical]
    except KeyError as exc:
        valid = ", ".join(BANKS)
        raise KeyError(f"Unknown bank '{bank_id}'. Choose one of: {valid}") from exc
