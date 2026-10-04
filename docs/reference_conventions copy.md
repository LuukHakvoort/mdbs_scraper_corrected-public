# Reference conventions: `../mdbs_scraper_corrected/`

This documents how the existing project-level scraper is written, so the eligibility repo reads as if the same person wrote it. Everything is based on the code as read on 2026-09-15 (HEAD `d86365a`). File paths are relative to `../mdbs_scraper_corrected/`.

Each section has two parts, kept strictly apart:

- **Observed** is what the scraper does, with a short quote.
- **Would do differently** is a recommendation for this repo (and sometimes for the scraper).

Appendix A lists defects found in the scraper along the way.

---

## 0. Read first: four facts that affect the merge

1. **Bank codes do not match `docs/CLAUDE.md`.** The scraper's `bank_id` values are lowercase: `adb afdb aiib badea cabei caf cdb ebrd eib ibrd ida idb isdb ndb`, plus `imf`, which is disabled. Its `bank_abbreviation` values are `ADB AfDB AIIB BADEA CABEI CAF CDB EBRD EIB IBRD IDA IDB IsDB NDB`.
   - No `AsDB`, `ADF` or `AsDF` code exists anywhere in the scraper.
   - **AfDF** rows can be recovered from `afdb` rows, but only through the `funding_window` column (`"African Development Fund"`). That value can also be `"; "`-joined with other windows, in which case `concessional` is `Blended`.
   - **AsDF** cannot be separated at all. `funding_window` is blank on every `adb` row; only `concessional` (from IATI flow type) exists.
   - `cabei` is scraped but is not in the eligibility code list.

   "Must match exactly" therefore needs an explicit crosswalk, not string equality.
2. **The country normaliser misses names that membership lists will contain.** Tested against the real function, these all return `""`: `United Kingdom`, `United States`, `Netherlands` / `The Netherlands`, `Turkey`, `Cape Verde`, `The Gambia`, `The Bahamas`, `British Virgin Islands`, `Micronesia`, `Palestine`, `Korea, Rep.`, `Egypt, Arab Rep.`, `Yemen, Rep.`, `Congo, Dem. Rep.`, `Congo, Rep.`, `Venezuela, RB`, `Hong Kong SAR, China`, `Macedonia, FYR`, `Taiwan, China`. It was curated against borrower names in project data, so non-borrowing members never came up. See §7.
3. **Several things this repo needs do not exist in the scraper:** a raw-response cache, robots.txt handling, a configurable contact in the user agent, FX conversion, and deflation. They have to be built here (§3, §5, §8).
4. **The shared number parser misreads three-decimal numbers.** `parse_decimal("0.123")` returns `123` and `parse_decimal("12.345%")` returns `12345`. Voting-power and share percentages in capital subscription tables are exactly this shape. See §8.

---

## 1. Project structure and module organisation

### Observed

```
mdbs_scraper_corrected/
├── main.py                  # 7-line shim: from mdbs_scraper.cli import main
├── pyproject.toml           # setuptools; zero core deps; extras xlsx/browser/dev; script mdb-scrape
├── requirements.txt
├── mdbs_scraper/
│   ├── __init__.py __main__.py
│   ├── errors.py            # exception hierarchy (no internal imports)
│   ├── http.py              # stdlib HTTP client: retries, per-host throttle
│   ├── browser.py           # optional Playwright helpers
│   ├── cleaning.py          # text/number/date/currency normalisation (pure functions)
│   ├── parsers.py           # stdlib HTMLParser: links, tables, label/value pairs
│   ├── tabular.py           # CSV/TSV/JSON/XML(IATI)/XLSX -> list[dict]
│   ├── iati_geography.py    # country/region codelists + ISO-3 derivation
│   ├── dac_sectors.py       # OECD DAC sector codelists + category mapping
│   ├── schema.py            # ProjectRecord dataclass, validate(), to_row()
│   ├── config.py            # loads banks.json / year_filter.json into frozen dataclasses
│   ├── banks.json           # bank registry + per-bank knobs (data, not code)
│   ├── year_filter.json     # default 2002-2026 scope
│   ├── base.py              # ScrapeOptions, LoadedSource, BaseScraper (finalize/dedupe/notes)
│   ├── registry.py          # bank_id -> adapter class; fails at import on mismatch
│   ├── output.py            # CSV/JSON/XLSX writers, sha256, coverage summary
│   ├── report.py            # per-bank markdown quality report
│   ├── cli.py               # argparse, per-bank isolation, manifest.json
│   └── adapters/
│       ├── common.py        # FIELD_ALIASES, generic_record_from_row, reusable base scrapers
│       └── <bank_id>.py     # one file per bank; many are 7-15 lines (`pass` subclasses)
├── tests/                   # unittest, one file per module/bank, fixtures/ subdir
└── docs/                    # PROBLEMS.md (issue log), VALIDATION.md, CHANGES_*.md
```

The layering runs one way: `errors` → `http`/`browser` → `cleaning`/`parsers`/`tabular`/codelists → `schema` → `base` → `adapters/common` → `adapters/<bank>` → `registry` → `cli`.

Generic behaviour lives in reusable base classes (`TabularScraper`, `CkanOrganizationScraper`, `HTMLPortfolioScraper`). A bank gets its own module only for bespoke logic. Wiring is explicit and fails at import time:

```python
# mdbs_scraper/registry.py:43
_missing = set(BANKS) - set(SCRAPER_CLASSES)
if _missing:  # fail during development, not mid-collection
    raise RuntimeError(f"Adapter registry mismatch; missing concrete adapters for: {_missing}")
```

Every adapter has the same template-method shape: `scrape()` collects, `finalize()` normalises, dedupes and validates, and `run()` does both.

```python
# mdbs_scraper/base.py:78
    @abstractmethod
    def scrape(self) -> list[ProjectRecord]:
        """Collect unfinalized records from the bank-specific source."""

    def run(self) -> list[ProjectRecord]:
        return self.finalize(self.scrape())
```

### Would do differently

- Keep the one-way layering, the tiny `errors.py` at the bottom, and "data in JSON, behaviour in Python".
- The scraper is organised **by bank**. Eligibility data is organised **by source and component**: OGHIST, Wayback, annual reports, and manual CSVs feed components A–E. Don't force a `adapters/<bank>.py` tree onto that. The architecture session (setup guide step 3) should decide the layout; this doc only notes the mismatch.
- The scraper is a flat package at the repo root. This repo already has `src/`. Either is fine; pick one and note it in `coding_decisions.md`.

---

## 2. Naming conventions

### Observed

| Thing | Convention | Examples |
|---|---|---|
| Core modules | short noun per concern | `http.py`, `cleaning.py`, `parsers.py`, `tabular.py`, `schema.py`, `output.py`, `report.py` |
| Reference-table modules | named after the standard | `iati_geography.py`, `dac_sectors.py` |
| Adapter files | lowercase bank id | `adapters/afdb.py`, `adapters/isdb.py` |
| Adapter classes | `<Abbreviation>Scraper`, keeping the institution's casing | `AfDBScraper`, `IsDBScraper`, `CABEIScraper`, `IBRDScraper` |
| Functions | `verb_noun` | `parse_amount`, `normalize_text`, `extract_links`, `write_csv`, `load_source` |
| Lookups/derivations | `x_from_y` | `country_name_from_code`, `alpha3_from_alpha2`, `iso3_country_code_from_name`, `format_from_content_type` |
| Private helpers/methods | leading `_` | `_money`, `_throttle`, `_records_from_payloads`, `_listing_pages` |
| Public constants | `UPPER_SNAKE` | `USER_AGENT`, `FIELD_ALIASES`, `BANKS`, `STANDARD_FIELDS`, `IATI_COUNTRY_CODES` |
| Module-private constants | `_UPPER_SNAKE` | `_PAGE_SIZE`, `_REGISTRY_PATH`, `_AFDB_GROUP_WINDOW_REFS` |
| Logger | module-level `LOG` | `LOG = logging.getLogger(__name__)` |
| Variables | full words, no abbreviations | `response`, `payload`, `rows`, `row`, `record`, `fields`, `source`, `bank` |
| Booleans | adjective or `x_is_y` | `dynamic`, `enabled`, `include_raw`, `day_first`, `amount_period_is_decimal` |
| Provenance flags | `<field>_from_<origin>` | `country_from_code`, `status_from_code`, `sector_from_dac_code` |
| Output columns | snake_case, companions by suffix | `loan_amount` / `loan_currency` / `loan_amount_usd`; `concessional` / `concessional_flag`; `interest_rate` / `interest_rate_pct` |
| Coverage counters | `with_<field>` | `with_sector`, `with_cofinancing` |
| Output files | `{bank_id}_<thing>.<ext>` | `afdb_projects.csv`, `reports/afdb_quality.md` |
| Multi-value cells | `"; "`-joined, deduplicated case-insensitively | `join_values()` |
| Tests | `test_<module or bank>.py`; methods read as full sentences | `test_a_disclosed_window_outranks_a_contradictory_flow_type` |

Dataclasses are `@dataclass(slots=True)`; config dataclasses are also `frozen=True`. Most modules start with `from __future__ import annotations` and use builtin generics (`dict[str, str]`, `X | None`).

**Comment and docstring style** is a defining trait. Module docstrings explain *why* a source was chosen. Inline comments record the live evidence behind each rule, often dated, with `--` as the dash:

```python
# mdbs_scraper/adapters/common.py:415
    if bank_abbreviation and cofinancing_partners:
        # A funder can be the reporting bank itself, disclosed under its own
        # abbreviation rather than its full name (confirmed live: EIB lists
        # itself as "EIB", not "European Investment Bank", on 67% of its
        # activities) -- not a real external cofinancier.
```

Other traits:

- No linter or formatter config exists. Lines are mostly ≤110 characters.
- Commit subjects are imperative ("Fix EIB undercounting its portfolio by ~12x"), with a body explaining the root cause.

### Would do differently

- Keep all of the above, including the "confirmed live (date)" comments. For a thesis appendix they are the audit trail.
- Bank codes are the one place to deliberately diverge. This repo's canonical codes (`AsDB`, `ADF`, …) should live as data, with an explicit crosswalk to the scraper's `bank_id` and `funding_window` (see §0.1 and §10). Don't rename anything in the scraper.
- If you add a formatter, set the line length to ~110 so new code doesn't look reflowed next to old code.

---

## 3. HTTP requests

### Observed

- **Library:** standard library only (`urllib.request`); no `requests`/`httpx`. One class, `HttpClient` (`mdbs_scraper/http.py:128`).
- **Defaults:** timeout 45 s, 0.75 s minimum gap between requests **to the same host**, 3 retries (4 attempts in total). `--timeout` and `--request-delay` override the first two per run.
- **Retries:**
  - Retried: HTTP 408/425/429/500/502/503/504, plus any `URLError`/`TimeoutError`/`OSError`.
  - Backoff is `2**attempt + random()`, capped at 30 s.
  - `Retry-After` is honoured only in integer-seconds form.
  - The final failure raises `SourceError` naming the URL.
- **Throttle:** a dict of last-request times per host behind a `threading.Lock`. It lives on the client instance, and each scraper builds its own client (`base.py:44`), so throttling is **not** shared across banks in a `--parallel` run.
- **Headers:** `User-Agent`, `Accept` (callers pass e.g. `"application/json"` or `"text/html"`), `Accept-Encoding: gzip` (decompressed manually).
- **User agent:** one constant. It is never overridden: nothing passes `user_agent=`, and there is no CLI flag or env var.
- **Response decoding:** `HttpResponse.text()` tries the declared charset, then `utf-8-sig`, `utf-8`, `latin-1`.
- **TLS quirk handling:** if a server omits its intermediate certificate, the client fetches it from the leaf's AIA URL and retries with full verification (`http.py:61-100`). Verification is never disabled.
- **Not present:** robots.txt checks, response caching, conditional requests (ETag/If-Modified-Since), request logging.

```python
# mdbs_scraper/http.py:20
USER_AGENT = "MDB-Thesis-Scraper/2.0 (+academic research; contact configured by operator)"
```

```python
# mdbs_scraper/http.py:205 (trimmed)
        for attempt in range(self.retries + 1):
            self._throttle(url)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    ...
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt >= self.retries:
                    break
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                wait = float(retry_after) if retry_after.isdigit() else 2**attempt + random.random()
                time.sleep(min(wait, 30.0))
        ...
        raise SourceError(f"Could not retrieve {url}: {last_error}") from last_error
```

Pagination is written inline per adapter as a `while True` loop that stops on a short page (`adapters/eib.py:64-84`, `adapters/worldbank.py:137-156`).

### Would do differently

- **Put a real contact in the user agent**, read from config or an env var (e.g. `MDB_ELIGIBILITY_CONTACT`). Fail at startup if it is unset. The current string claims a contact is "configured by operator", but nothing lets the operator configure one. `docs/CLAUDE.md` requires a descriptive user agent with contact details.
- **Check robots.txt** with stdlib `urllib.robotparser`, cached per host. This keeps the dependency-free style.
- **Share throttle state per host across the whole process**, not per client instance. Use a slower delay for `web.archive.org`, since Wayback reconstruction makes many requests to one host.
- Parse the HTTP-date form of `Retry-After` too.
- Log each request at DEBUG: method, URL, status, bytes, cache hit/miss.
- Keep the stdlib-only approach and the retry/backoff numbers. They are sensible and the new repo should behave the same.

---

## 4. Playwright / Chromium

### Observed

- **Optional dependency.** Playwright is the `browser` extra and is imported lazily inside each function. If it is missing, the call raises `OptionalDependencyMissing` with the exact install command.
- **Three helpers in `browser.py`:**
  - `render_page`: renders the page, captures JSON responses up to 25 MB, and scrolls up to 12 times to trigger lazy loading.
  - `download_via_browser`: navigates to a file URL and captures either the download or the inline body.
  - `download_from_last_button`: clicks the last visible exact-text "Download" control.
- **Nothing is reused.** Every call opens `sync_playwright()`, launches Chromium, creates a context and page, and closes both. There is no shared browser, context, or storage state.
- **Launcher:** `_launch_chromium` globs for a macOS cached "Google Chrome for Testing" binary and falls back to Playwright's default. `download_from_last_button` bypasses it (`browser.py:188`). `adapters/badea.py:30` imports the private `_launch_chromium` directly.
- **User agent is inconsistent:**
  - `render_page` and BADEA send `"MDB-Thesis-Scraper/2.0 (academic research)"`.
  - Both download helpers send Chromium's default.
- **Opt-in** is per bank: `"dynamic": true` in `banks.json`.
- **Error wrapping:** `PlaywrightError` becomes `SourceError`.
- **Known instability:** `cli._run_bank` retries a dynamic bank once. `docs/PROBLEMS.md:471-534` documents that several `sync_playwright()` launches in one process can segfault on macOS (Network.framework atfork handler). The in-process retry cannot recover from that. The only reliable workaround is one process per browser-driven bank.

```python
# mdbs_scraper/browser.py:51 (trimmed)
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise OptionalDependencyMissing(
            "This bank's portal requires Playwright. Run: python -m pip install '.[browser]' "
            "and then: playwright install chromium"
        ) from exc
    ...
        with sync_playwright() as playwright:
            browser = _launch_chromium(playwright, headless=headless)
            context = browser.new_context(
                locale="en-US",
                user_agent="MDB-Thesis-Scraper/2.0 (academic research)",
                accept_downloads=False,
            )
            page = context.new_page()
            page.set_default_timeout(int(timeout * 1000))
            ...
            context.close()
            browser.close()
    except PlaywrightError as exc:
        raise SourceError(f"Browser rendering failed for {url}: {exc}") from exc
```

### Would do differently

- **Avoid Playwright in this repo unless a source proves it's needed.** OGHIST and the income-guideline files, annual report PDFs, the Wayback CDX API and snapshots, and EUR-Lex are all static HTTP. Snapshots served by `web.archive.org` with an `id_` suffix return the original bytes without the Wayback toolbar.
- If a JS-only page is unavoidable:
  - Keep the lazy import and the install message.
  - Use one launcher everywhere.
  - Send the same contact-bearing user agent as HTTP.
  - Write the rendered HTML or downloaded bytes into the raw cache (§5), so the browser runs once per URL, not once per pipeline run.
  - Run each browser job in its own subprocess rather than retrying in-process, given the documented macOS crash.

---

## 5. Raw response caching

### Observed

There is no cache. Every run re-fetches every page and file. The nearest mechanisms:

- **`--source-file BANK=PATH` snapshots** (`base.py:62-67`). A hand-saved official download replaces the network fetch. This is also how the whole test suite runs offline.
- **`--include-raw`** appends a per-row `source_fields_json` column to the *output*, not the raw response.
- **`manifest.json`** records SHA-256 of *output* files only, not inputs.
- **`docs/VALIDATION.md`** recommends manually keeping "unchanged official downloads, dated in filenames" under `data/raw/`.
- Browser downloads are read into memory and discarded.

```python
# mdbs_scraper/base.py:62
        if self.options.source_file:
            path = Path(self.options.source_file).expanduser()
            if not path.is_file():
                raise SourceError(f"Local source file does not exist: {path}")
            fmt = self.options.source_format or infer_format(str(path), source_format or self.bank.source_format)
            return LoadedSource(path.read_bytes(), path.resolve().as_uri(), fmt)
        target = self.options.source_url or url or self.bank.source_url
        response = self.client.get(target, accept=accept)
```

### Would do differently

This repo requires a cache (`docs/CLAUDE.md`: "Cache every fetch"). A shape that fits the scraper's style:

- **Wrap, don't fork, the client.** A `CachedHttpClient` (or a `fetch()` function) checks the cache *before* throttling and only calls `HttpClient.get` on a miss.
- **Key** = `sha256` of the URL, including the query string. For POST, hash method + URL + body. Store the body at `data/raw/<key>.<ext>`, with a sidecar `data/raw/<key>.json` holding:
  - `url` and `final_url`
  - `status` and `content_type`
  - `fetched_at` (in `utc_now()` format)
  - `sha256` of the body
- **Cache only 2xx responses.** Write to a temp file and rename, so an interrupted run never leaves a truncated PDF that is treated as a hit.
- **Add `--refresh`** (all entries, or a URL pattern) to force a re-fetch. The default is cache-forever. That is correct for timestamped Wayback URLs, which are immutable, and for dated annual-report PDFs.
- **Record every raw file's hash in `manifest.json`.** This closes the gap above: the outputs become provably tied to specific downloads.
- Keep a `--source-file`-style override. Tests should feed fixtures the same way the scraper's tests do.

---

## 6. Error handling and logging

### Observed

**A typed hierarchy**, the whole of `errors.py`:

```python
class ScraperError(RuntimeError):
    """Base class for an expected collection failure."""

class SourceError(ScraperError):
    """The configured source could not be retrieved or decoded."""

class SourceLayoutChanged(ScraperError):
    """The source responded, but no longer matches the supported layout."""

class OptionalDependencyMissing(ScraperError):
    """A collector needs an optional dependency that is not installed."""

class ValidationError(ScraperError):
    """A record violates a non-negotiable schema rule."""
```

How each is used:

- **`SourceError`**: network, browser, missing local file, invalid JSON/XML. Low-level exceptions are chained with `raise ... from exc`.
- **`SourceLayoutChanged`**: the source answered but yielded zero records or lacks an expected structure. The message says what to do next:

  ```python
  # mdbs_scraper/adapters/common.py:913
              raise SourceLayoutChanged(
                  f"{self.bank.abbreviation}: no recognizable projects were exposed by the official "
                  "page. Save an official CSV/JSON export and rerun with --source-file BANK=PATH, "
                  "or update the bank adapter after verifying the site's new layout."
              )
  ```

- **`ValidationError`**: raised by `ProjectRecord.validate()` inside `finalize()`. Examples: missing id or name, year outside 1900–2200, negative amounts.
- **Per-bank isolation at the CLI boundary.** A broad `except Exception` stores `"{type}: {message}"` in `manifest.json["failures"]` and writes a failure report. The run exits with code **2** unless `--allow-partial` is passed. There is never a silent empty success.

  ```python
  # mdbs_scraper/cli.py:202
              try:
                  collected[bank_id] = _run_bank(args, bank_id)
              except Exception as exc:  # manifest must record bank-specific failures
                  LOG.error("%s failed: %s", bank_id, exc)
                  failures[bank_id] = f"{type(exc).__name__}: {exc}"
  ```

- **Optional enrichments degrade and warn** rather than fail:

  ```python
  # mdbs_scraper/adapters/worldbank.py:109
      except Exception as exc:  # noqa: BLE001 -- a bonus enrichment must not fail the primary scrape
          LOG.warning("Finances One loan-terms fetch failed for %s (interest_rate/last_repayment_date "
                      "will be blank this run): %s", asset_id, exc)
          return {}
  ```

- **Soft data problems are written into the data, not raised.** Blanks are never silent: `add_note` appends an explanation to `data_quality_notes`, deduplicated by `join_values`.

  ```python
  # mdbs_scraper/base.py:127
              if record.total_disbursement is None:
                  self.add_note(
                      record,
                      "Total disbursement is not exposed by this source; blank does not mean zero.",
                  )
  ```

- **Lookups never guess.** Codelist functions return `""` for unknown input instead of raising or approximating. The caller adds a note.

**Logging is sparse:**

- Only `cli.py` (`LOG = logging.getLogger("mdbs_scraper")`) and `adapters/worldbank.py` (`getLogger(__name__)`) log.
- `logging.basicConfig(level=DEBUG if --verbose else INFO, format="%(asctime)s %(levelname)s %(message)s")` runs once in `main()`.
- Messages use %-style arguments.
- The final summary is a `print`; failures go to stderr.
- No module emits DEBUG, so `--verbose` changes nothing.

There are a few silent swallows: `HTMLPortfolioScraper._records_from_payloads` does `except Exception: continue`, and `CkanOrganizationScraper.rows_from_source` returns `[]` on undecodable JSON.

### Would do differently

- Keep the hierarchy and the fail-loud manifest/exit-code contract. Keep the "explain every blank" rule. In this repo it maps onto the `note` column plus `data/manual/unresolved.csv`.
- **An unresolved fact must not become a silent blank.** Where the scraper writes `""` and a note, this repo should write `null` **and** append a row to `unresolved.csv`. A small writer function makes that a one-liner.
- Add DEBUG logging for each fetch and cache hit/miss, plus an INFO summary per source (e.g. "OGHIST: 1 fetched, 0 cached, 218 countries, 3 unresolved names"). Use `getLogger(__name__)` everywhere, not a literal name.
- Keep broad `except Exception` only at the CLI boundary and for explicitly optional enrichments, with the same `# noqa: BLE001 -- reason` style.

---

## 7. Country names → ISO codes

### Observed

All logic lives in `mdbs_scraper/iati_geography.py`. The module has no internal imports.

Its data tables:

- **`IATI_COUNTRY_CODES`**: alpha-2 → name. 257 entries, generated from IATI's Country codelist, including withdrawn codes.
- **`IATI_ALPHA2_TO_ALPHA3`**: alpha-2 → alpha-3, sourced from `lukes/ISO-3166-Countries-with-Regional-Codes`.
- **`COUNTRY_NAME_TO_ISO2`**: 28 hand-curated aliases.
- **`_NAME_TO_ALPHA2`**: built from IATI names, plus a generic pass that strips a trailing `" (the)"`.

`iso3_country_code_from_name` then works in four steps:

1. Normalise the name: NFKD accent fold, lowercase, non-alphanumerics to spaces.
2. Match exact IATI names, with and without the `(the)` suffix.
3. Match the alias table.
4. Convert alpha-2 → alpha-3. Anything else returns `""`.

```python
# mdbs_scraper/iati_geography.py:640
def _normalize_geo_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()
```

```python
# mdbs_scraper/iati_geography.py:683
    if not name:
        return ""
    normalized = _normalize_geo_text(name)
    code = _NAME_TO_ALPHA2.get(normalized) or COUNTRY_NAME_TO_ISO2.get(normalized)
    return alpha3_from_alpha2(code) if code else ""
```

**Wiring.** In `generic_record_from_row` (`adapters/common.py:340-378`), a code the source disclosed is *not* trusted by default. IDB's `"PR"` means Peru, not Puerto Rico. The ISO-3 code is derived from the name text. The only exception is when the name itself came from an IATI alpha-2 code; that code is converted directly. World Bank and EIB build records themselves and call `iso3_country_code_from_name` directly (`worldbank.py:221`, `eib.py:105`).

```python
# mdbs_scraper/adapters/common.py:374
    if country_from_code:
        iso3_code = alpha3_from_alpha2(country_code)
    else:
        iso3_code = iso3_country_code_from_name(country)
    country_code_unresolved = bool(country) and not iso3_code
```

**Decisions already encoded** (verified by calling the function):

| Input | Result | Note |
|---|---|---|
| `Kosovo` | `XKX` | User-assigned code (no official ISO code); the World Bank uses the same |
| `Serbia` / `Montenegro` | `SRB` / `MNE` | |
| `Serbia and Montenegro`, `Yugoslavia` | `""` | Deliberately unmapped: "no single unambiguous successor state" |
| `South Sudan` / `Sudan` | `SSD` / `SDN` | |
| `Timor-Leste`, `East Timor` | `TLS` | |
| `Burma` → `MMR`; `Zaire` → `COD`; `Swaziland` → `SWZ` | | Historical names with one successor |
| `West Bank and Gaza` | `PSE` | Alias table |
| `Gambia, The`, `Bahamas, The` | `GMB`, `BHS` | Work by accident: they normalise to IATI's `"gambia the"` |
| `Regional`, `Africa, regional`, `OECS Countries` | `""` | Groupings are never guessed |

**Names that currently return `""`**, even though they are single countries (tested 2026-09-15):

- **Plain names:** `United Kingdom`, `United States`, `Netherlands`, `The Netherlands`, `Turkey`, `Cape Verde`, `The Gambia`, `The Bahamas`, `British Virgin Islands`, `Micronesia`, `Palestine`, `Channel Islands`, `St. Martin (French part)`.
- **World Bank short forms:** `Korea, Rep.`, `Korea, Dem. People's Rep.`, `Egypt, Arab Rep.`, `Yemen, Rep.`, `Congo, Dem. Rep.`, `Congo, Rep.`, `Venezuela, RB`, `Hong Kong SAR, China`, `Macao SAR, China`, `Macedonia, FYR`, `Taiwan, China`.

**Impact already visible in the scraper's output.** `output/all_mdb_projects.csv` (55,157 rows) has about **634 rows with a real single country but a blank `country_code`**:

- **EIB:** `United Kingdom` 359, `The Netherlands` 223, `Palestine*` 16, `Cape Verde` 10, `Lao People's Democratic Rep.` 6, `Congo (Democratic Republic)` 3, `Kingdom of Eswatini` 3, `The Gambia` 2, `São Tomé e Principe` 2, `St. Maarten` 2, `Micronesia` 1, `Saint Vincent and Grenadines` 1.
- **CDB:** `The Bahamas` 3, `British Virgin Islands` 3. These are CDB's *own* canonical names from `adapters/cdb.py:15-36`.

`docs/PROBLEMS.md` says "zero real countries slipped through". That was measured on 2026-08-25, before EIB switched source on 2026-09-03.

Also documented there: a multi-country IATI activity keeps only its first `<recipient-country>`.

### Would do differently

- **Import this module; do not copy it** (§12). If the eligibility grid and the project data resolve a name differently, the merge drops rows silently. That is exactly the risk `docs/CLAUDE.md` names.
- **Fix the gaps in the scraper's `COUNTRY_NAME_TO_ISO2`, not in a local fork or a local alias file.** One table, one truth. This needs a deliberate exception to "`mdbs_scraper_corrected` is read-only" for that one table, plus a scraper test pinning each new alias. **Decision for you:** allow that exception, or maintain a documented wrapper here and accept the divergence risk.
- **In this repo an unresolved name is an error, not a blank.** Wrap the import:
  1. `require_iso3(name, source)` calls `iso3_country_code_from_name`.
  2. On `""` it logs to `unresolved.csv` and raises (or returns `None`) under an explicit policy.
  3. The (bank, iso3, year) grid never receives a blank key.
- When a source ships its own codes (OGHIST has a code column), still cross-check that the name resolves to the same code. The IDB `"PR"` lesson applies, and World Bank files include non-ISO codes.
- Territorial changes need decisions logged in `coding_decisions.md` before building the grid:
  - Serbia and Montenegro, 2002–June 2006. The scraper leaves it unmapped, so project rows naming it will not merge regardless.
  - South Sudan, from 2011.
  - Kosovo, `XKX`.
- Add a test here that pins the §7 table above. It then fails loudly if an upstream change alters any mapping.

---

## 8. Currency conversion and deflation

### Observed

**Neither exists.** A grep for exchange/deflate/CPI/constant-USD finds nothing. The policy is stated in the README:

> Original currencies are preserved. USD fields are filled only when USD is explicit in the source; the code performs no undocumented FX conversion.

It is implemented as an explicit-USD check. `*_usd` is a copy of the amount when USD was stated, otherwise `None`:

```python
# mdbs_scraper/adapters/common.py:221
    explicitly_usd = currency == "USD" or "usd" in key or "us_dollar" in key
    return amount, currency, explicitly_usd
```

```python
# mdbs_scraper/adapters/common.py:451
        loan_amount=loan,
        loan_currency=loan_currency,
        loan_amount_usd=loan if loan_usd else None,
```

**Parsing helpers** (`cleaning.py`):

- **`parse_amount(value, default_currency)`** returns `(Decimal | None, currency_code)`. It expands magnitude words (`k`, `m`, `mn`, `million`, `millones`, `bn`, …) and understands US/European separators and parenthesised negatives.
- **`detect_currency`** checks, in order:
  1. symbols (`US$`, `€`, `£`)
  2. names (`SDR` → `XDR`, `UA` → `XUA`, `EURO` → `EUR`)
  3. any isolated three-letter uppercase token
- **Per-bank default currency** comes from `banks.json` (`"default_currency": "EUR"`). IATI values fall back to the activity's `default-currency`.
- Amounts stay `Decimal` end to end and are written as plain strings (`format(value, "f")`), never floats or scientific notation.

**Edge cases, verified by calling the functions** (these matter for annual report tables):

| Call | Result | Why |
|---|---|---|
| `parse_decimal("0.123")` | `123` | A lone `.` followed by exactly 3 digits is read as a thousands separator |
| `parse_decimal("12.345%")` | `12345` | Same rule |
| `parse_decimal("2.500")` | `2500` | Same rule |
| `parse_decimal("0.123", period_is_decimal=True)` | `0.123` | The escape hatch (`banks.json` `amount_period_is_decimal`) |
| `parse_decimal("(1,234.5)")` | `-1234.5` | Accounting negatives work |
| `detect_currency("IDA credit")` | `"IDA"` | The three-letter fallback grabs acronyms |
| `detect_currency("ADF grant")`, `("OCR loans")` | `"ADF"`, `"OCR"` | Same |
| `detect_currency("ISD 500")` | `"ISD"` | IsDB's Islamic Dinar is not mapped to an ISO-style code |
| `parse_amount("UA 12.5 million")` | `(12500000.0, "XUA")` | |
| `parse_date("2015")` | `"2015-01-01"` | A bare year becomes 1 January |
| `parse_date("FY2015")` | `""` | Fiscal-year labels are not understood |
| `parse_date("03/04/2022")` | `""` | Ambiguous slash dates are refused unless `day_first` is set |

### Would do differently

- **The eligibility panel shouldn't need FX at all.** Use **shares or voting-power percentages** from capital subscription tables as weights. They are unitless and comparable across banks. Keep subscribed capital in its original currency and unit (USD, XDR, XUA, ISD, EUR; thousands vs millions) in separate columns, exactly as the scraper does.
- **Don't deflate income thresholds.** Compare Atlas GNI per capita (current USD) with the *same fiscal year's* threshold, both nominal. Record the fiscal-to-calendar year mapping once in `coding_decisions.md`.
- **If conversion is ever needed** (e.g. a USD capital series): make it a separate, documented step with year-specific rates from a cited source (e.g. IMF SDR rates), written to new `*_usd` columns. Never overwrite the original. This matches the scraper's philosophy.
- **Don't feed annual-report numbers through `parse_decimal`/`parse_amount` without `period_is_decimal=True`,** and never let `detect_currency` see row text containing bank acronyms. Pass the currency explicitly from the table header. Better still, write a table-specific numeric parser and test it against real extracted rows.

---

## 9. Output formats and schemas

### Observed

- **Schema is a dataclass.** `ProjectRecord` is `@dataclass(slots=True)`. Its field order *is* the CSV column order (`STANDARD_FIELDS`). Strings default to `""`; numbers are `Decimal | None`. `validate()` enforces invariants; `to_row()` serialises:

  ```python
  # mdbs_scraper/schema.py:123
          for key, value in raw.items():
              if value is None:
                  row[key] = ""
              elif isinstance(value, Decimal):
                  row[key] = format(value, "f")
              else:
                  row[key] = value
  ```

- **CSV** is UTF-8 **with BOM** (`utf-8-sig`, for Excel/Stata/R), `newline=""`, `csv.DictWriter`:

  ```python
  # mdbs_scraper/output.py:26
      with path.open("w", encoding="utf-8-sig", newline="") as handle:
          writer = csv.DictWriter(handle, fieldnames=_fieldnames(include_raw), extrasaction="ignore")
  ```

- **JSON:** `indent=2`, `ensure_ascii=False`, trailing newline. **XLSX** (optional): bold header, frozen first row, auto-filter.
- **Dates** are ISO `YYYY-MM-DD` strings. **Timestamps** come from `utc_now()`: UTC, second precision, e.g. `2026-09-09T14:36:33+00:00`.
- **Yes/No values** get a text column plus a numeric companion: `concessional` is `Yes`/`No`/`Blended`, and `concessional_flag` is `1`/`0`/blank (blank for Blended, rather than forcing a side).
- **Blank means "not disclosed", never zero.** Every row carries provenance: `source_url` (the stable requested URL, not a short-lived redirect target), `source_format`, `source_updated_at`, `scraped_at`, `data_quality_notes`.
- **Row identity:** dedupe key `f"{bank_id}:{project_id}".casefold()`. When a source has no id, a labelled synthetic id is used: `"synthetic-" + sha256(url|name|country|date)[:16]`.

**Run artefacts** in `output/` (gitignored):

- `{bank_id}_projects.csv`
- `all_mdb_projects.csv` (with `--combine`)
- `project_counts_and_coverage.csv`: `bank_id`, `status`, `error`, `project_count`, `with_<field>` counts, `rows_seen`, `rows_retained`, `duplicates_removed`, `source_total`, `completeness`
- `reports/{bank_id}_quality.md`: coverage percentages plus frequency of each distinct note combination
- `manifest.json`, whose top-level keys are shown here:

```json
{
  "schema_version": "2.0",
  "started_at": "...", "finished_at": "...",
  "requested_banks": [...], "successful_banks": [...], "failures": {},
  "record_count": 8084,
  "bank_metadata": {"ebrd": {"rows_seen": 9415, "rows_retained": 8084, "duplicates_removed": 71,
                             "source_total": null, "completeness": "source-total-unavailable"}},
  "coverage_summary_path": "...",
  "outputs": {"ebrd": {"path": "...", "sha256": "73ee..."}},
  "filters": {"min_year": 2002, "max_year": 2026},
  "source_overrides": {"files": {}, "urls": {}, "formats": {}}
}
```

### Would do differently

- Keep: dataclass schema with `validate()`, `utf-8-sig` CSV, `Decimal` written as plain strings, ISO dates, `utc_now()`, blank-not-zero, `manifest.json` with SHA-256, and a markdown quality report per bank.
- **Booleans in `eligibility.csv`:** write `1`/`0`, with blank for null, to match `concessional_flag` and import into Stata without `destring`. `docs/CLAUDE.md` says `bool`; state the on-disk encoding once in `coding_decisions.md`.
- **Make `source` non-optional in `validate()`.** An empty source should raise `ValidationError`, just as an empty `project_id` does in the scraper.
- **Hash inputs as well as outputs** in the manifest (raw cache files and manual CSVs), and record the scraper's git commit for the imported normaliser.
- Sort `eligibility.csv` by (`bank`, `iso3`, `year`), so reviewing diffs of the output is practical.

---

## 10. Config management

### Observed

- **All bank metadata and per-bank tuning is data** in `mdbs_scraper/banks.json`:
  - top level: `reviewed_on`, `aliases`, `banks[]`
  - per bank: `id`, `name`, `abbreviation`, `website`, `source_url`, `source_format`, `method`, `coverage_notes`, `dynamic`, `enabled`, plus optional knobs
  - `coverage_notes` doubles as a dated evidence log ("Confirmed live (2026-09-15): …")

  ```json
  {
    "id": "ndb",
    "name": "New Development Bank",
    "abbreviation": "NDB",
    "website": "https://www.ndb.int",
    "source_url": "https://www.ndb.int/projects/all-projects/",
    "source_format": "html",
    "method": "official-portfolio",
    "coverage_notes": "NDB published project cards and detail pages.",
    "dynamic": false,
    "enabled": true,
    "day_first": false,
    "project_href_pattern": "ndb\\.int/project/(?!all-projects|page/)[^?#]+/?$",
    "pagination_href_pattern": "(?:/page/\\d+/?|[?&](?:page|paged)=\\d+)",
    "max_listing_pages": 60
  }
  ```

- **Loaded at import time** into a frozen dataclass whose defaults document each knob. `BankDefinition(**entry)` is strict: an unknown key raises `TypeError` at import.

  ```python
  # mdbs_scraper/config.py:47
  def _load_registry(path: Path) -> tuple[dict[str, BankDefinition], dict[str, str]]:
      payload = json.loads(path.read_text(encoding="utf-8"))
      banks = {entry["id"]: BankDefinition(**entry) for entry in payload["banks"]}
      aliases = dict(payload["aliases"])
      return banks, aliases
  ```

- **Derived views:**
  - `BANKS` keeps only enabled banks.
  - `canonical_bank_id()` lowercases and applies aliases (`cabi` → `cabei`, `worldbank` → `ibrd`).
  - `get_bank()` raises with the list of valid ids.
- **`year_filter.json`** holds `{"enabled": true, "min_year": 2002, "max_year": 2026}`, loaded into `YearFilterDefault`. An explicit CLI flag wins.
- **Per-run settings** go CLI → `ScrapeOptions` dataclass → scraper. There are no env vars, no `.env` loading (though `.env` is gitignored), and no secrets.
- **Packaging:** `pyproject.toml` `[tool.setuptools.package-data]` lists only `banks.json`.

### Would do differently

- Use the same pattern here: a bank registry JSON loaded into a frozen dataclass with a strict loader, plus a `_load_*(path)` seam for tests. Suggested fields:
  - canonical eligibility code (`AfDB`, `ADF`, …)
  - `project_bank_id` (`afdb`) and `project_funding_window` (`"African Development Fund"`, or null)
  - founding year
  - member-page and annual-report URLs
- **Keep judgement facts out of JSON.** Accession dates, mandate timelines and sanctions belong in `data/manual/*.csv`, per `docs/CLAUDE.md`, with reasoning in `coding_decisions.md` rather than free-text `coverage_notes`.
- Put all JSON files in package data (`"*.json"`). The scraper's omission of `year_filter.json` breaks a non-editable install (Appendix A).
- Put the user-agent contact in config or an env var (§3). It is the one setting that should not be committed.

---

## 11. Testing approach

### Observed

- **Standard library only.** `unittest`, `unittest.mock`, `tempfile`; no pytest. Run with `python -m unittest discover -s tests -v`. All **127 tests pass offline in ~1.3 s** (checked 2026-09-15).
- **Layout:** one test file per module or bank. Fixtures live in `tests/fixtures/` and are small, redacted, real-shaped captures (CSV, JSON, IATI XML, HTML, a Tableau payload). Each file locates them with `FIXTURES = Path(__file__).parent / "fixtures"`.
- **Network isolation** without extra libraries:
  - `ScrapeOptions(source_file=...)`
  - replacing `scraper.client.get` with a `Mock`
  - `patch("mdbs_scraper.adapters.common.render_page", ...)`
  - temporarily patching module constants (`eib_module._PAGE_SIZE = 2`)
- **End to end:** `test_cli.py` runs every enabled bank from fixtures into a `TemporaryDirectory` and asserts on `manifest.json` and the coverage CSV. It also tests partial failure, the exit code, and the dynamic-bank retry.
- **Tests assert on explanations, not just values**, e.g. `assertIn("blank does not mean zero", ...)`. Regression tests carry a comment citing the live evidence that motivated them.
- There is no CI, coverage configuration, or linting.

```python
# tests/test_registry_and_adapters.py:42
        scraper = AIIBScraper(get_bank("aiib"), ScrapeOptions())
        scraper.client.get = Mock(return_value=Mock(
            url=detail.url,
            text=Mock(return_value=detail.html),
        ))
        with patch("mdbs_scraper.adapters.common.render_page", return_value=listing):
            records = scraper.run()
```

```python
# tests/test_iati_geography.py:109
    def test_regional_and_institutional_text_is_never_guessed(self):
        self.assertEqual(iso3_country_code_from_name("Regional"), "")
        self.assertEqual(iso3_country_code_from_name("Africa, regional"), "")
        self.assertEqual(iso3_country_code_from_name("OECS Countries"), "")
        self.assertEqual(iso3_country_code_from_name("BADEA"), "")
```

### Would do differently

- Same framework, same fixture layout, same sentence-style names, same "assert the note too" habit.
- **Fixtures for PDFs:** commit one or two extracted pages per bank table layout, or the table extractor's JSON output, not whole annual reports. Test each per-bank parser against them.
- **Test the cache** (hit, miss, refresh, no caching of non-2xx) and **the normaliser pin** from §7.
- **The four validation checks** from `docs/CLAUDE.md` are data assertions over the built panel. Implement them as functions that `make validate` calls, and unit-test the functions themselves on tiny synthetic panels.
- `docs/CLAUDE.md` assumes `make`. Add a `Makefile` whose `test` target runs the `unittest` command above.

---

## 12. What to import, copy, or leave

**Rule of thumb:** *import* what must stay identical across the two repos, *copy* what this repo will need to change, and *leave* anything tied to `ProjectRecord`.

| Item | Location | Action | Notes |
|---|---|---|---|
| `iso3_country_code_from_name`, `alpha3_from_alpha2`, `country_name_from_code`, `IATI_COUNTRY_CODES` | `mdbs_scraper/iati_geography.py` | **Import** | Must be identical for the merge. Fix gaps upstream (§7) and wrap in `require_iso3` here |
| `normalize_text`, `ascii_fold`, `normalized_key`, `join_values`, `extract_year`, `NULL_TEXT` | `mdbs_scraper/cleaning.py` | **Import** | Pure, well-tested |
| `parse_date` | `cleaning.py` | Import, with care | Bare `"2015"` → `2015-01-01`; `FY` labels unsupported |
| `parse_decimal`, `parse_amount`, `detect_currency` | `cleaning.py` | Import only with `period_is_decimal=True` and an explicit currency | See §8 hazards |
| `parse_tables`, `table_to_dicts`, `extract_links`, `html_to_text`, `extract_label_values`, `extract_labelled_sequence` | `mdbs_scraper/parsers.py` | **Import** | Stdlib-only; good for member pages and Wayback snapshots |
| `decode_bytes`, `delimited_rows`, `json_rows`, `format_from_content_type`, `infer_format` | `mdbs_scraper/tabular.py` | **Import** | |
| `xlsx_rows` | `tabular.py` | **Don't use for OGHIST** | It takes the sheet with the most rows and guesses the header as the densest of the first 25 rows. OGHIST has several sheets and multi-row headers: open the named sheet with `openpyxl` and read explicit header rows |
| `HttpClient`, `HttpResponse`, retry/throttle/AIA logic | `mdbs_scraper/http.py` | **Copy**, then add cache, robots.txt, contact user agent, process-wide throttle | ~240 self-contained lines. Importing would tie this repo's exceptions to `mdbs_scraper.errors` |
| Exception hierarchy | `mdbs_scraper/errors.py` | **Copy** (rename base to fit this package) | Keep the same five-class shape |
| `utc_now`, `iso_date` | `mdbs_scraper/schema.py` | Copy (two small functions) | |
| `sha256`, manifest structure, coverage CSV, quality report | `output.py`, `cli.py:269-294`, `report.py` | **Copy the pattern** | The writers are typed to `ProjectRecord` |
| `BankDefinition` + `_load_registry` + aliases + registry fail-fast | `config.py`, `registry.py` | **Copy the pattern** | New fields (§10) |
| Playwright helpers | `mdbs_scraper/browser.py` | Leave unless forced (§4) | Per-call launch, macOS-specific path, known crash |
| `FIELD_ALIASES`, `generic_record_from_row`, `BaseScraper.finalize`, adapters, `dac_sectors.py` | `adapters/`, `base.py` | **Leave** | Project-record specific |

**How to import.** Install the scraper into this repo's venv in editable mode: `python -m pip install -e ../mdbs_scraper_corrected`. Editable is required until the package-data bug in Appendix A is fixed.

`import mdbs_scraper.iati_geography` runs `mdbs_scraper/__init__.py`, which loads `config` (reads the two JSON files) and `schema`. It makes no network calls and does not import adapters, the registry, or Playwright. Record the scraper's commit hash in this repo's manifest.

---

## Appendix A: defects noticed in the scraper

These are for fixing in `mdbs_scraper_corrected`, not here. Ordered by impact on the thesis data.

1. **Country normaliser gaps.** About 634 current output rows have a real country but a blank `country_code` (§7). `docs/PROBLEMS.md`'s "zero real countries slipped through" is stale.
2. **CDB's own canonical member names don't resolve.** `adapters/cdb.py:18,21` emits `"The Bahamas"` and `"British Virgin Islands"`, and neither maps to a code.
3. **Three-decimal numbers are read as thousands** by `parse_decimal` for any bank without `amount_period_is_decimal`, e.g. `"2.500"` → `2500` (§8).
4. **`detect_currency` treats any isolated three-letter capitalised word as a currency** (`"IDA"`, `"ADF"`, `"OCR"`).
5. **`year_filter.json` is missing from package data** (`pyproject.toml:27-28` lists only `banks.json`). A non-editable install raises `FileNotFoundError` when `config.py` is imported.
6. **User agents:**
   - The HTTP user agent promises a contact that cannot be configured.
   - `download_via_browser` and `download_from_last_button` send Chromium's default.
   - `download_from_last_button` bypasses `_launch_chromium` (`browser.py:188`).
7. **Self-reference stripping is skipped for HTML banks.** `HTMLPortfolioScraper.map_row` (`adapters/common.py:753`) does not pass `bank_abbreviation`, unlike `TabularScraper.map_row`, so this cofinancing clean-up never runs for `aiib`/`cdb`/`ndb`/`badea`.
8. **The throttle is per client, not per host process-wide.** Under `--parallel`, several banks hitting `iatiregistry.org` aren't coordinated.
9. **`CkanOrganizationScraper` reads one `package_search` page** (`rows=100` in `banks.json`) without paging. It would silently truncate a publisher with more than 100 packages (AfDB has 57 today).
10. **`--verbose` does nothing:** it enables DEBUG, but nothing logs at DEBUG.
11. **Minor:**
    - unreachable `return ""` at `dac_sectors.py:815`
    - the failure row in `cli.py:236-256` omits `with_sector_category` (harmless, since `DictWriter` fills blanks)
    - README's IDB URL uses `file/download`, while `banks.json` uses `files/download`
