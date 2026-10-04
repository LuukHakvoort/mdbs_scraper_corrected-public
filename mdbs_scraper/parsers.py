"""Dependency-free HTML link, text, table, and label extraction."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from .cleaning import normalize_text, normalized_key


class _HTMLCollector(HTMLParser):
    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[tuple[str, str]] = []
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._anchor_href = ""
        self._anchor_text: list[str] | None = None
        self._text: list[str] = []
        self._skip_depth = 0
        self._block_tags = {
            "p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "h4",
            "h5", "h6", "dt", "dd", "tr", "td", "th",
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self._block_tags:
            self._text.append("\n")
        if tag == "a":
            self._anchor_href = urljoin(self.base_url, attrs_dict.get("href", ""))
            self._anchor_text = []
        elif tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "a" and self._anchor_text is not None:
            self.links.append((self._anchor_href, normalize_text(" ".join(self._anchor_text))))
            self._anchor_href = ""
            self._anchor_text = None
        elif tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(normalize_text(" ".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None
        if tag in self._block_tags:
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._text.append(data)
        if self._anchor_text is not None:
            self._anchor_text.append(data)
        if self._cell is not None:
            self._cell.append(data)

    @property
    def text(self) -> str:
        lines = [normalize_text(line) for line in "".join(self._text).splitlines()]
        return "\n".join(line for line in lines if line)


def collect_html(html_text: str, base_url: str = "") -> _HTMLCollector:
    parser = _HTMLCollector(base_url)
    parser.feed(html_text)
    parser.close()
    return parser


def extract_links(
    html_text: str,
    base_url: str = "",
    *,
    extensions: tuple[str, ...] | None = None,
    href_pattern: str | None = None,
) -> list[tuple[str, str]]:
    links = collect_html(html_text, base_url).links
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href, label in links:
        clean_href = href.split("#", 1)[0]
        if not clean_href or clean_href in seen:
            continue
        path = clean_href.lower().split("?", 1)[0]
        if extensions and not path.endswith(tuple(ext.lower() for ext in extensions)):
            continue
        if href_pattern and not re.search(href_pattern, clean_href, re.I):
            continue
        result.append((clean_href, label))
        seen.add(clean_href)
    return result


def html_to_text(html_text: str) -> str:
    return collect_html(html_text).text


def extract_heading(html_text: str, level: int = 1) -> str:
    match = re.search(
        rf"<h{level}\b[^>]*>(.*?)</h{level}>", html_text, flags=re.I | re.S
    )
    return normalize_text(html_to_text(match.group(1))) if match else ""


def parse_tables(html_text: str) -> list[list[list[str]]]:
    return collect_html(html_text).tables


def table_to_dicts(table: list[list[str]]) -> list[dict[str, str]]:
    if len(table) < 2:
        return []
    width = max(len(row) for row in table)
    headers = [normalized_key(cell) or f"column_{index + 1}" for index, cell in enumerate(table[0])]
    headers += [f"column_{index + 1}" for index in range(len(headers), width)]
    output: list[dict[str, str]] = []
    for values in table[1:]:
        values = values + [""] * (width - len(values))
        output.append(dict(zip(headers, values)))
    return output


def extract_label_values(html_text: str) -> dict[str, str]:
    """Extract two-column tables and ``Label: value`` text into normalized keys."""

    collector = collect_html(html_text)
    result: dict[str, str] = {}
    for table in collector.tables:
        for row in table:
            if len(row) == 2 and normalize_text(row[0]) and normalize_text(row[1]):
                result.setdefault(normalized_key(row[0]), normalize_text(row[1]))
    lines = collector.text.splitlines()
    for line in lines:
        match = re.match(r"^(.{2,80}?):\s*(.+)$", line)
        if match:
            key = normalized_key(match.group(1))
            if key:
                result.setdefault(key, normalize_text(match.group(2)))
    return result


def extract_labelled_sequence(
    text: str, labels: dict[str, str], *, case_sensitive: bool = True
) -> dict[str, str]:
    """Map a known set of exact-text labels to the next non-empty, non-label line.

    Some sites render summary fields as a label on its own line immediately
    followed by a value on the next line (a sibling pair of block elements),
    rather than ``Label: value`` on one line or a clean two-column table --
    neither of which ``extract_label_values()`` can parse. ``labels`` maps
    each expected label's exact text to the canonical field name it
    represents; matching is deliberately exact and site-specific (not a
    heuristic) to avoid misreading unrelated page text. Matching is
    case-sensitive by default; pass ``case_sensitive=False`` for a site whose
    own markup is inconsistent about label casing (confirmed live on NDB:
    the same "Financing Approval Date" fact renders as "Financing approval
    date" on some pages).
    """

    lookup = {label: key for label, key in labels.items()}
    label_keys = set(lookup)
    if not case_sensitive:
        lookup = {label.casefold(): key for label, key in lookup.items()}
        label_keys = {label.casefold() for label in label_keys}

    lines = text.splitlines()
    fields: dict[str, str] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        key = lookup.get(stripped if case_sensitive else stripped.casefold())
        if not key:
            continue
        for value in lines[index + 1 :]:
            value = value.strip()
            comparable = value if case_sensitive else value.casefold()
            if value and comparable not in label_keys:
                fields.setdefault(key, value)
                break
    return fields
