"""Exchange-rate and price reference series for constant-USD conversion.

- IMF Exchange Rates (IMF.STA:ER), annual period averages of domestic
  currency per US dollar (XDC_USD, PA_RT), https://data.imf.org/en/datasets/IMF.STA:ER
- FRED A191RD3A086NBEA, US GDP implicit price deflator (annual, 2017=100),
  https://fred.stlouisfed.org/series/A191RD3A086NBEA
- World Bank WDI NY.GDP.MKTP.CD (GDP, current US$) and NY.GDP.MKTP.PP.CD
  (GDP, PPP, current international $, from the ICP PPP conversion factor),
  https://data.worldbank.org/indicator/NY.GDP.MKTP.PP.CD. Their ratio is the
  price level PPP / market rate. Taking it from two USD-denominated series,
  not from PA.NUS.PPP / PA.NUS.FCRF, avoids WDI's mismatched local currency
  units (Venezuela's and Zimbabwe's redenominations give ratios near 0) and
  uses the World Bank's own market rate, including its alternative
  conversion factor where there is no usable official rate.

All are cached under data/reference/ with a JSON sidecar (URL, retrieval
time, sha256) and committed, so a conversion is reproducible from the
repository alone; --refresh-reference downloads them again.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .errors import SourceError
from .http import HttpClient
from .schema import utc_now

LOG = logging.getLogger(__name__)

REFERENCE_DIR = Path(__file__).parent / "data" / "reference"
CURRENCIES_PATH = REFERENCE_DIR / "currencies.json"
IMF_URL = "https://api.imf.org/external/sdmx/2.1/data/IMF.STA,ER/{key}?startPeriod={start}"
IMF_CSV = "application/vnd.sdmx.data+csv;version=1.0.0"
FRED_SERIES = "A191RD3A086NBEA"
FRED_URL = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={FRED_SERIES}"
BASE_YEAR = 2025
FIRST_YEAR = 2000
WDI_GDP_USD = "NY.GDP.MKTP.CD"
WDI_GDP_PPP = "NY.GDP.MKTP.PP.CD"
WDI_URL = (
    "https://api.worldbank.org/v2/country/all/indicator/{indicator}"
    "?format=json&source=2&date={start}:{end}&per_page=20000"
)


@dataclass(frozen=True, slots=True)
class CurrencyRule:
    code: str
    imf_area: str = ""      # IMF country/area whose XDC_USD series is this currency's
    pegged_to: str = ""     # e.g. XUA and ISD are defined as 1 SDR
    note: str = ""


def load_currency_rules(path: Path = CURRENCIES_PATH) -> dict[str, CurrencyRule]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {code: CurrencyRule(code=code, **rule) for code, rule in payload["currencies"].items()}


def _write_with_sidecar(path: Path, text: str, url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    sidecar = {
        "url": url, "retrieved_at": utc_now(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")


def _imf_key(rule: CurrencyRule) -> str:
    # USD per SDR is published under the United States; everything else as
    # its issuing area's domestic currency per USD.
    if rule.code == "XDR":
        return "USA.XDC_XDR.PA_RT.A"
    return f"{rule.imf_area}.XDC_USD.PA_RT.A"


def imf_rate_path(code: str) -> Path:
    return REFERENCE_DIR / f"imf_er_{code}.csv"


def fetch_imf_rates(rule: CurrencyRule, client: HttpClient | None = None) -> Path:
    """Download one currency's annual average rate into data/reference/ (units per USD)."""

    client = client or HttpClient(request_delay=0.5)
    url = IMF_URL.format(key=_imf_key(rule), start=FIRST_YEAR)
    response = client.get(url, accept=IMF_CSV)
    rows = list(csv.DictReader(io.StringIO(response.text())))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["year", "units_per_usd", "series"])
    count = 0
    for row in rows:
        value = row.get("OBS_VALUE")
        period = row.get("TIME_PERIOD", "")
        if not value or not period.isdigit():
            continue
        rate = Decimal(value)
        if rule.code == "XDR":
            rate = Decimal(1) / rate  # published as USD per SDR
        writer.writerow([period, format(rate.quantize(Decimal("1e-12")).normalize(), "f"), _imf_key(rule)])
        count += 1
    if not count:
        raise SourceError(f"IMF returned no annual rates for {rule.code} ({url})")
    path = imf_rate_path(rule.code)
    _write_with_sidecar(path, out.getvalue(), url)
    return path


def fred_path() -> Path:
    return REFERENCE_DIR / f"fred_{FRED_SERIES}.csv"


def fetch_fred_deflator(client: HttpClient | None = None) -> Path:
    client = client or HttpClient(request_delay=0.5)
    response = client.get(FRED_URL, accept="text/csv")
    text = response.text()
    if FRED_SERIES not in text.splitlines()[0]:
        raise SourceError(f"Unexpected FRED response for {FRED_SERIES}")
    path = fred_path()
    _write_with_sidecar(path, text, FRED_URL)
    return path


def wdi_path(indicator: str) -> Path:
    return REFERENCE_DIR / f"wdi_{indicator.replace('.', '_')}.csv"


def fetch_wdi(indicator: str, client: HttpClient | None = None) -> Path:
    """Download one WDI indicator for every economy into data/reference/ (country_code, year, value)."""

    client = client or HttpClient(request_delay=0.5)
    url = WDI_URL.format(indicator=indicator, start=FIRST_YEAR, end=BASE_YEAR)
    payload = json.loads(client.get(url, accept="application/json").text())
    if not isinstance(payload, list) or len(payload) < 2 or "message" in payload[0]:
        raise SourceError(f"Unexpected World Bank response for {indicator} ({url}): {payload!r:.300}")
    if int(payload[0].get("pages") or 1) > 1:
        raise SourceError(f"World Bank response for {indicator} is paginated; raise per_page ({url})")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["country_code", "year", "value"])
    count = 0
    for row in sorted(payload[1] or [], key=lambda row: (row.get("countryiso3code") or "", row.get("date") or "")):
        code, period, value = row.get("countryiso3code") or "", row.get("date") or "", row.get("value")
        if not code or not period.isdigit() or value is None:
            continue
        writer.writerow([code, period, format(Decimal(str(value)).normalize(), "f")])
        count += 1
    if not count:
        raise SourceError(f"World Bank returned no values for {indicator} ({url})")
    path = wdi_path(indicator)
    _write_with_sidecar(path, out.getvalue(), url)
    return path


def _read_panel(path: Path) -> dict[tuple[str, int], Decimal]:
    panel: dict[tuple[str, int], Decimal] = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                panel[(row["country_code"], int(row["year"]))] = Decimal(row["value"])
            except (ValueError, InvalidOperation, TypeError, KeyError):
                continue
    return panel


def _read_series(path: Path, key_column: str, value_column: str) -> dict[int, Decimal]:
    series: dict[int, Decimal] = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                series[int(row[key_column][:4])] = Decimal(row[value_column])
            except (ValueError, InvalidOperation, TypeError):
                continue
    return series


@dataclass
class ReferenceData:
    """Annual units-per-USD rates, the GDP deflator and recipient PPPs, as used for one conversion run."""

    rules: dict[str, CurrencyRule]
    rates: dict[str, dict[int, Decimal]]
    deflator: dict[int, Decimal]
    provenance: dict[str, dict[str, str]]
    gdp_usd: dict[tuple[str, int], Decimal] = field(default_factory=dict)  # (ISO-3, year) -> current US$
    gdp_ppp: dict[tuple[str, int], Decimal] = field(default_factory=dict)  # (ISO-3, year) -> current intl $

    def units_per_usd(self, currency: str, year: int) -> Decimal | None:
        rule = self.rules.get(currency)
        if rule is None:
            return None
        series_code = rule.pegged_to or currency
        return self.rates.get(series_code, {}).get(year)

    def local_currency(self, country_code: str) -> str:
        """The currency whose IMF series is quoted for ``country_code`` (ISO-3), or ""."""

        for code, rule in self.rules.items():
            if country_code and rule.imf_area == country_code:
                return code
        return ""

    def deflator_factor(self, year: int) -> Decimal | None:
        """Multiplier from year-``year`` dollars to BASE_YEAR dollars."""

        if year not in self.deflator or BASE_YEAR not in self.deflator:
            return None
        return self.deflator[BASE_YEAR] / self.deflator[year]

    def price_level_ratio(self, country_code: str, year: int) -> Decimal | None:
        """PPP / market rate for ``country_code`` (ISO-3): USD divided by this is international $."""

        usd = self.gdp_usd.get((country_code, year))
        ppp = self.gdp_ppp.get((country_code, year))
        if not usd or not ppp:
            return None
        return usd / ppp


def _provenance(path: Path) -> dict[str, str]:
    sidecar = path.with_suffix(".json")
    info = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
    info["path"] = str(path)
    info["file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return info


def load_reference(currencies: set[str], *, refresh: bool = False) -> ReferenceData:
    """Load (downloading where missing, or everything with ``refresh``) what ``currencies`` need."""

    rules = load_currency_rules()
    client = None
    needed: set[str] = set()
    for code in currencies - {"USD", ""}:
        rule = rules.get(code)
        if rule is None:
            LOG.warning("No conversion rule for currency %s; its amounts stay unconverted", code)
            continue
        needed.add(rule.pegged_to or code)
    rates: dict[str, dict[int, Decimal]] = {}
    provenance: dict[str, dict[str, str]] = {}
    for code in sorted(needed):
        path = imf_rate_path(code)
        if refresh or not path.is_file():
            client = client or HttpClient(request_delay=0.5)
            LOG.info("Downloading IMF exchange rates for %s", code)
            fetch_imf_rates(rules[code], client)
        rates[code] = _read_series(path, "year", "units_per_usd")
        provenance[f"imf_er_{code}"] = _provenance(path)
    path = fred_path()
    if refresh or not path.is_file():
        client = client or HttpClient(request_delay=0.5)
        LOG.info("Downloading FRED %s", FRED_SERIES)
        fetch_fred_deflator(client)
    deflator = _read_series(path, "observation_date", FRED_SERIES)
    provenance[f"fred_{FRED_SERIES}"] = _provenance(path)
    panels: dict[str, dict[tuple[str, int], Decimal]] = {}
    for indicator in (WDI_GDP_USD, WDI_GDP_PPP):
        path = wdi_path(indicator)
        if refresh or not path.is_file():
            client = client or HttpClient(request_delay=0.5)
            LOG.info("Downloading World Bank %s", indicator)
            fetch_wdi(indicator, client)
        panels[indicator] = _read_panel(path)
        provenance[path.stem] = _provenance(path)
    return ReferenceData(
        rules, rates, deflator, provenance,
        gdp_usd=panels[WDI_GDP_USD], gdp_ppp=panels[WDI_GDP_PPP],
    )
