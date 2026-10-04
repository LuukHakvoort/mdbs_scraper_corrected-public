"""Concrete adapter registry and factory."""

from __future__ import annotations

from typing import Type

from .adapters.adb import ADBScraper
from .adapters.afdb import AfDBScraper
from .adapters.aiib import AIIBScraper
from .adapters.badea import BADEAScraper
from .adapters.cabei import CABEIScraper
from .adapters.caf import CAFScraper
from .adapters.cdb import CDBScraper
from .adapters.ebrd import EBRDScraper
from .adapters.eib import EIBScraper
from .adapters.idb import IDBScraper
from .adapters.imf import IMFScraper
from .adapters.isdb import IsDBScraper
from .adapters.ndb import NDBScraper
from .adapters.worldbank import IBRDScraper, IDAScraper
from .base import BaseScraper, ScrapeOptions
from .config import BANKS, canonical_bank_id, get_bank


SCRAPER_CLASSES: dict[str, Type[BaseScraper]] = {
    "adb": ADBScraper,
    "aiib": AIIBScraper,
    "afdb": AfDBScraper,
    "badea": BADEAScraper,
    "cabei": CABEIScraper,
    "caf": CAFScraper,
    "cdb": CDBScraper,
    "ebrd": EBRDScraper,
    "ibrd": IBRDScraper,
    "ida": IDAScraper,
    "idb": IDBScraper,
    "imf": IMFScraper,
    "isdb": IsDBScraper,
    "ndb": NDBScraper,
    "eib": EIBScraper,
}

_missing = set(BANKS) - set(SCRAPER_CLASSES)
if _missing:  # fail during development, not mid-collection
    raise RuntimeError(f"Adapter registry mismatch; missing concrete adapters for: {_missing}")


def create_scraper(bank_id: str, options: ScrapeOptions | None = None) -> BaseScraper:
    canonical = canonical_bank_id(bank_id)
    bank = get_bank(canonical)
    return SCRAPER_CLASSES[canonical](bank, options)
