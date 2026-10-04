"""Map each bank's own instrument wording onto one small, comparable set.

The categories exist so the country-year aggregation can report totals by
instrument (a technical-assistance grant and a sovereign loan should not be
added up silently). Banks describe instruments very differently -- World
Bank lending instruments, IATI finance-type names, IDB operation types, ADB
modalities -- so the mapping is keyword-based and ordered, and anything it
cannot place is left blank rather than guessed.
"""

from __future__ import annotations

import re

from .cleaning import ascii_fold

INSTRUMENT_CATEGORIES = (
    "loan", "grant", "technical_assistance", "guarantee", "equity", "trade_finance", "other",
)
"""Allowed non-blank values of ProjectRecord.instrument_category."""

# Order matters: the first rule whose pattern matches wins. Mixed modalities
# such as ADB's "Grant | Loan" or "Loan | Technical Assistance" are classed by
# the instrument that normally carries the money (the loan), and a World Bank
# "Technical Assistance Loan" is still a loan.
_RULES: tuple[tuple[str, str], ...] = (
    ("trade_finance", r"trade financ|itfc"),
    ("guarantee", r"guarantee"),
    # IsDB's profit-sharing modes are equity-like; its sale/lease modes
    # (Istisna'a, Instalment Sale, Leasing, Murabaha) are debt-like.
    ("equity", r"\bequity\b|mudaraba|musharaka"),
    ("loan", r"\bloan|\bcredit\b|\blending\b|investment project financing|program for results"
             r"|development policy|project financing|debt"
             # CABEI (Spanish): loans from ordinary funds, credit lines, syndicated loans
             r"|\bcredito\b|\bprestamo|fondos ordinarios"
             r"|istisna|instalment sale|installment sale|\bleasing\b|murabaha"),
    ("grant", r"\bgrant|\bdonacion|no reembolsable"),
    ("technical_assistance", r"technical assistance|technical cooperation|capacity development"
                             r"|asistencia tecnica|cooperacion tecnica"),
    ("other", r"container|securities|special assistance"),
)


def instrument_category_from(*texts: str) -> str:
    """Return the first category whose keywords appear in any of ``texts``, or ""."""

    folded = " ".join(ascii_fold(text).lower() for text in texts if text)
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    if not folded.strip():
        return ""
    for category, pattern in _RULES:
        if re.search(pattern, folded):
            return category
    return ""
