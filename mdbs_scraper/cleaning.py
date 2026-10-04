"""Conservative parsing helpers that never invent currency conversions."""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Iterable


NULL_TEXT = {"", "-", "--", "n/a", "na", "n.a.", "none", "null", "not available"}
CURRENCY_SYMBOLS = {
    "US$": "USD", "€": "EUR", "£": "GBP",
}
CURRENCY_NAMES = {
    "US DOLLAR": "USD", "US DOLLARS": "USD", "EURO": "EUR", "EUROS": "EUR",
    "SPECIAL DRAWING RIGHTS": "XDR", "SDR": "XDR", "UA": "XUA",
    # NDB discloses Chinese renminbi loans as "RMB", the colloquial name, not
    # the ISO code (confirmed in the 2026-09-15 output: 29 NDB rows).
    "RMB": "CNY",
}
ISO_4217_CODES = frozenset("""
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN
    BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD
    FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY
    KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP
    MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR
    RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS
    TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XCG XOF XPF YER
    ZAR ZMW ZWG ZWL
    XDR XUA
""".split())
"""Currency codes ``detect_currency`` accepts from a bare three-letter token:
active ISO 4217 codes plus the SDR (XDR) and the AfDB unit of account (XUA).
Anything else -- bank acronyms such as "IDA", "ADF" or "OCR", or placeholders
such as "TBC"/"TBD" (AIIB) -- is not a currency and is never returned."""
MULTIPLIERS = {
    "k": Decimal("1000"), "thousand": Decimal("1000"),
    "mil": Decimal("1000"),
    "m": Decimal("1000000"), "mn": Decimal("1000000"),
    "million": Decimal("1000000"), "millions": Decimal("1000000"),
    "millon": Decimal("1000000"), "millones": Decimal("1000000"),
    "b": Decimal("1000000000"), "bn": Decimal("1000000000"),
    "billion": Decimal("1000000000"), "billions": Decimal("1000000000"),
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value)).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return "" if text.casefold() in NULL_TEXT else text


def ascii_fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", normalize_text(value))
    return "".join(char for char in text if not unicodedata.combining(char))


def normalized_key(value: Any) -> str:
    if value is None:
        return ""
    return _normalized_key(value if isinstance(value, str) else str(value))


@lru_cache(maxsize=65536)
def _normalized_key(value: str) -> str:
    # Cached: generic row mapping normalizes the same few hundred column
    # names and aliases for every row (27k IDB rows took ~100s without it).
    text = ascii_fold(value)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _normalize_number_token(token: str, *, period_is_decimal: bool = False) -> str:
    token = token.replace("\u202f", "").replace(" ", "").replace("'", "")
    if "," in token and "." in token:
        # Whichever separator appears last is normally the decimal separator.
        if token.rfind(",") > token.rfind("."):
            return token.replace(".", "").replace(",", ".")
        return token.replace(",", "")
    if token.count(",") == 1:
        left, right = token.split(",")
        return left + ("." if len(right) != 3 else "") + right
    if token.count(",") > 1:
        return token.replace(",", "")
    if token.count(".") == 1:
        if period_is_decimal:
            return token
        left, right = token.split(".")
        return left + ("" if len(right) == 3 and len(left) > 0 else ".") + right
    if token.count(".") > 1:
        return token.replace(".", "")
    return token


def parse_decimal(value: Any, *, period_is_decimal: bool = False) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = normalize_text(value)
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    match = re.search(r"[-+]?\d[\d\s.,'\u202f]*", text)
    if not match:
        return None
    token = _normalize_number_token(match.group(0), period_is_decimal=period_is_decimal)
    try:
        number = Decimal(token)
    except InvalidOperation:
        return None
    return -number if negative else number


def detect_currency(value: Any, default: str = "") -> str:
    text = normalize_text(value)
    upper = text.upper()
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    for name, code in CURRENCY_NAMES.items():
        if re.search(rf"(?<![A-Z]){re.escape(name)}(?![A-Z])", upper):
            return code
    for match in re.finditer(r"(?<![A-Z])([A-Z]{3})(?![A-Z])", upper):
        if match.group(1) in ISO_4217_CODES:
            return match.group(1)
    return default.upper()


def parse_amount(
    value: Any, default_currency: str = "", *, period_is_decimal: bool = False
) -> tuple[Decimal | None, str]:
    """Parse one disclosed amount and its currency without conversion.

    Magnitude suffixes (million, bn, etc.) are expanded.  The function does not
    infer USD merely because a bare dollar sign occurs when a caller supplies a
    different default currency; explicit text always wins. ``period_is_decimal``
    forces a lone "." to always mean the decimal point, for sources whose amount
    column has a fixed decimal-digit count that would otherwise be misread as
    European thousands grouping (see BankDefinition.amount_period_is_decimal).
    """

    text = normalize_text(value)
    if not text:
        return None, default_currency.upper()
    folded = ascii_fold(text).lower()
    suffix = re.search(
        r"(?:\d|\s)(k|m|mn|b|bn|mil|millon(?:es)?|thousand|millions?|billions?)\b",
        folded,
    )
    # With an English magnitude word, "1.448 billion" can only mean 1.448 --
    # English never groups thousands with "." (NDB's "RMB 1.448 billion" came
    # out as 1.448 trillion before 2026-09-16). Spanish can ("1.448 millones"
    # is 1,448 million), so those words keep the usual rule.
    english_magnitude = bool(suffix) and suffix.group(1) not in {"mil", "millon", "millones"}
    number = parse_decimal(text, period_is_decimal=period_is_decimal or english_magnitude)
    if number is None:
        return None, detect_currency(text, default_currency)
    if suffix:
        number *= MULTIPLIERS[suffix.group(1)]
    return number, detect_currency(text, default_currency)


SPANISH_MONTHS = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04", "mayo": "05",
    "junio": "06", "julio": "07", "agosto": "08", "septiembre": "09",
    "setiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}


def parse_date(value: Any, *, day_first: bool | None = None) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    # Strip time while preserving ISO dates.
    text = re.sub(r"\s*\(.*?\)\s*$", "", text).strip()
    folded = ascii_fold(text).lower()
    # A comma glued to the next token with no space (e.g. AIIB's own
    # "October 15,2020" once ascii_fold() has already NFKD-normalized a
    # full-width "，" down to a plain ","; confirmed live) fails every
    # "%B %d, %Y"-style strptime format below, which requires the space
    # literally. Not a full-width-comma-specific fix -- any "glued" comma
    # benefits.
    folded = re.sub(r",(?=\S)", ", ", folded)
    for month, number in SPANISH_MONTHS.items():
        folded = re.sub(rf"\b{month}\b", number, folded)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%d %B %Y", "%d %b %Y", "%d-%B-%Y", "%d-%b-%Y",
        "%B %d, %Y", "%b %d, %Y",
        # Day-month order with a comma before the year (confirmed live on
        # NDB: "25 March, 2021") -- distinct from "%B %d, %Y" above, which is
        # month-day order.
        "%d %B, %Y", "%d %b, %Y",
        # Month/year only (no day disclosed); day defaults to the 1st.
        "%B, %Y", "%b, %Y", "%B %Y", "%b %Y",
        "%d %m %Y", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y",
        "%Y", "%m/%d/%y", "%d/%m/%y",
    ]
    if day_first is True:
        formats.remove("%m/%d/%Y")
        formats.insert(formats.index("%d/%m/%Y"), "%d/%m/%Y")
    elif day_first is False:
        formats.remove("%d/%m/%Y")
        formats.insert(formats.index("%m/%d/%Y"), "%m/%d/%Y")
    # Avoid guessing ambiguous slash dates unless the adapter tells us the order.
    if day_first is None and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", folded):
        return ""
    for fmt in formats:
        try:
            return datetime.strptime(folded, fmt).date().isoformat()
        except ValueError:
            continue
    match = re.search(r"\b(19|20|21)\d{2}\b", folded)
    return match.group(0) if match and folded.strip() == match.group(0) else ""


def extract_year(*values: Any) -> int | None:
    for value in values:
        text = normalize_text(value)
        match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", text)
        if match:
            return int(match.group(1))
    return None


def parse_coordinate(value: Any) -> Decimal | None:
    """Parse a geographic coordinate without treating three decimals as grouping."""

    text = normalize_text(value).replace("\u202f", "").replace(" ", "")
    if not text:
        return None
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", "."))
    except InvalidOperation:
        return None


def duration_years(start: str, end: str) -> Decimal | None:
    if len(start) != 10 or len(end) != 10:
        return None
    try:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None
    if days < 0:
        return None
    return (Decimal(days) / Decimal("365.2425")).quantize(Decimal("0.001"))


def normalize_loan_type(value: Any) -> str:
    folded = ascii_fold(value).lower().replace("_", " ")
    if not folded:
        return ""
    collapsed = re.sub(r"[^a-z0-9]+", " ", folded).strip()
    # Check non-sovereign first: it contains the word sovereign. Match both
    # "non sovereign" (space) and "nonsovereign" (concatenated, e.g. AIIB's
    # own "Nonsovereign") -- without the second form, "nonsovereign" falls
    # through to the plain "sovereign" substring check below and is
    # misclassified as Sovereign, the opposite of what it says.
    if collapsed == "nsg" or any(token in collapsed for token in (
        "non sovereign", "nonsovereign", "private", "privado",
        "sin garantia soberana", "no soberano", "no soberana",
    )):
        return "Non-sovereign"
    if any(token in collapsed for token in ("quasi sovereign", "cuasi soberano")):
        return "Quasi-sovereign"
    if collapsed == "sg" or any(token in collapsed for token in (
        "sovereign", "public", "soberano", "soberana", "state", "government", "gobierno",
    )):
        return "Sovereign"
    return normalize_text(value)


def join_values(values: Iterable[Any], separator: str = "; ") -> str:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = normalize_text(value)
        if text and text.casefold() not in seen:
            result.append(text)
            seen.add(text.casefold())
    return separator.join(result)
