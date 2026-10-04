"""New Development Bank paginated project and detail-page adapter."""

from ..parsers import extract_label_values, extract_labelled_sequence, html_to_text
from .common import HTMLPortfolioScraper


class NDBScraper(HTMLPortfolioScraper):
    # NDB detail pages render their "Quick Facts" summary as a label on its
    # own line immediately followed by its value on the next line -- not
    # `Label: value` text or a clean two-column table, so the generic
    # extract_label_values() finds nothing there.
    _QUICK_FACTS_LABELS = {
        "Country": "country",
        "Status": "status",
        "Area Of Operation": "sector",
        "Type": "loan_type",
        # "Proposed"-status pages label these two facts "Concept Approval
        # Date"/"Proposed Limit of NDB Financing"; once a project moves past
        # Proposed (Approved/Completed/Cancelled), NDB relabels the same two
        # facts "Financing Approval Date"/"Current Limit of NDB Financing"
        # (confirmed live on one page of each status) -- both variants map
        # to the same canonical fields. Technical-assistance projects use a
        # third pair, "TA Approval Date"/"Limit of NDB Financing" (confirmed
        # live). Matching is case-insensitive (see below): NDB's own markup
        # is inconsistent about capitalization across pages/statuses --
        # confirmed live, "Financing approval date" (sentence case) and
        # "Financing Approval Date" (title case) both occur for the same
        # fact on different pages.
        "Concept Approval Date": "approval_date",
        "Proposed Limit of NDB Financing": "loan_amount",
        "Financing Approval Date": "approval_date",
        "Current Limit of NDB Financing": "loan_amount",
        "TA Approval Date": "approval_date",
        "Limit of NDB Financing": "loan_amount",
    }

    def _extract_detail_fields(self, detail_html: str) -> dict:
        fields = extract_label_values(detail_html)
        sequence = extract_labelled_sequence(
            html_to_text(detail_html), self._QUICK_FACTS_LABELS, case_sensitive=False
        )
        for key, value in sequence.items():
            fields.setdefault(key, value)
        return fields
