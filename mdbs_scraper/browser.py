"""Optional Playwright rendering for JavaScript-only official portfolios."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import OptionalDependencyMissing, SourceError
from .http import build_user_agent


@dataclass(slots=True)
class CapturedPayload:
    url: str
    body: bytes


@dataclass(slots=True)
class RenderedPage:
    url: str
    html: str
    json_payloads: list[CapturedPayload] = field(default_factory=list)


@dataclass(slots=True)
class DownloadedFile:
    url: str
    suggested_filename: str
    body: bytes


def launch_chromium(playwright, *, headless: bool):
    """Launch Chromium the same way for every helper (and for BADEA's adapter)."""
    candidates = sorted(
        Path.home().glob(
            "Library/Caches/ms-playwright/chromium-*/**/"
            "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
        )
    )
    if candidates:
        return playwright.chromium.launch(headless=headless, executable_path=str(candidates[-1]))
    return playwright.chromium.launch(headless=headless)


def render_page(url: str, *, headless: bool = True, timeout: float = 45.0) -> RenderedPage:
    """Render a page and retain JSON responses used to populate its interface.

    The Playwright manager, browser, context, and page all live inside this
    function.  This avoids the lifecycle error in the uploaded AIIB scraper,
    where the manager was stopped before the page was used.
    """

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise OptionalDependencyMissing(
            "This bank's portal requires Playwright. Run: python -m pip install '.[browser]' "
            "and then: playwright install chromium"
        ) from exc

    captured: list[CapturedPayload] = []
    try:
        with sync_playwright() as playwright:
            browser = launch_chromium(playwright, headless=headless)
            context = browser.new_context(
                locale="en-US",
                user_agent=build_user_agent(),
                accept_downloads=False,
            )
            page = context.new_page()
            page.set_default_timeout(int(timeout * 1000))

            def retain_json(response) -> None:  # Playwright response type is optional at import time.
                content_type = response.headers.get("content-type", "").lower()
                if "json" not in content_type:
                    return
                try:
                    body = response.body()
                except PlaywrightError:
                    return
                if body and len(body) <= 25_000_000:
                    captured.append(CapturedPayload(response.url, body))

            page.on("response", retain_json)
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=min(int(timeout * 1000), 20_000))
            except PlaywrightTimeoutError:
                pass
            # Trigger lazy-loaded rows without scrolling indefinitely.
            previous_height = 0
            for _ in range(12):
                height = int(page.evaluate("document.body.scrollHeight"))
                if height == previous_height:
                    break
                previous_height = height
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(350)
            html = page.content()
            final_url = page.url
            context.close()
            browser.close()
    except PlaywrightError as exc:
        raise SourceError(f"Browser rendering failed for {url}: {exc}") from exc
    return RenderedPage(final_url, html, captured)


def download_via_browser(
    url: str, *, headless: bool = True, timeout: float = 45.0
) -> DownloadedFile:
    """Fetch a file URL through a real browser engine.

    Some official downloads sit behind bot-challenge protection (e.g.
    Cloudflare) that rejects plain HTTP clients regardless of headers, but
    allows a real browser. This drives an actual Chromium navigation to the
    URL so any JS challenge can run, then returns either the browser download
    it triggers, or -- if the server serves the file inline instead of as an
    attachment -- the raw response body of the navigated request.
    """

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise OptionalDependencyMissing(
            "This official download requires Playwright. Run: python -m pip install "
            "'.[browser]' and then: playwright install chromium"
        ) from exc

    captured: dict[str, bytes] = {}

    def retain_body(response) -> None:
        if response.request.is_navigation_request():
            try:
                captured.setdefault(response.url, response.body())
            except PlaywrightError:
                pass

    try:
        with sync_playwright() as playwright:
            browser = launch_chromium(playwright, headless=headless)
            context = browser.new_context(
                accept_downloads=True, locale="en-US", user_agent=build_user_agent()
            )
            page = context.new_page()
            page.set_default_timeout(int(timeout * 1000))
            page.on("response", retain_body)
            try:
                with page.expect_download(timeout=int(timeout * 1000)) as pending:
                    page.goto(url, wait_until="commit")
                download = pending.value
                temporary_path = download.path()
                if not temporary_path:
                    raise SourceError(f"Download at {url} did not create a readable file")
                result = DownloadedFile(
                    download.url, download.suggested_filename, Path(temporary_path).read_bytes()
                )
            except PlaywrightTimeoutError:
                # No download event fired; the file was served inline instead.
                final_url = page.url
                body = captured.get(final_url) or next(iter(captured.values()), b"")
                if not body:
                    raise SourceError(f"No downloadable content found at {url}")
                suggested_filename = final_url.rstrip("/").rsplit("/", 1)[-1] or "download"
                result = DownloadedFile(final_url, suggested_filename, body)
            context.close()
            browser.close()
            return result
    except PlaywrightError as exc:
        raise SourceError(f"Could not download {url} via browser: {exc}") from exc


def download_from_last_button(
    url: str, *, button_text: str = "Download", headless: bool = True, timeout: float = 45.0
) -> DownloadedFile:
    """Click the last exact-text download control and return its bytes in memory."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise OptionalDependencyMissing(
            "This official download control requires Playwright. Run: python -m pip install "
            "'.[browser]' and then: playwright install chromium"
        ) from exc
    try:
        with sync_playwright() as playwright:
            browser = launch_chromium(playwright, headless=headless)
            context = browser.new_context(
                accept_downloads=True, locale="en-US", user_agent=build_user_agent()
            )
            page = context.new_page()
            page.set_default_timeout(int(timeout * 1000))
            page.goto(url, wait_until="domcontentloaded")
            try:
                # The download control can be client-rendered a moment after
                # domcontentloaded; without this, visibility checks below run
                # too early and never see it.
                page.wait_for_load_state("networkidle", timeout=min(int(timeout * 1000), 20_000))
            except PlaywrightTimeoutError:
                pass
            candidates = page.get_by_text(button_text, exact=True)
            count = candidates.count()
            if count == 0:
                raise SourceError(f"No exact '{button_text}' download control found at {url}")
            selected = None
            for index in range(count - 1, -1, -1):
                candidate = candidates.nth(index)
                if candidate.is_visible():
                    selected = candidate
                    break
            if selected is None:
                raise SourceError(f"No visible '{button_text}' download control found at {url}")
            with page.expect_download() as pending:
                selected.click()
            download = pending.value
            temporary_path = download.path()
            if not temporary_path:
                raise SourceError(f"Download at {url} did not create a readable file")
            body = Path(temporary_path).read_bytes()
            result = DownloadedFile(download.url, download.suggested_filename, body)
            context.close()
            browser.close()
            return result
    except PlaywrightError as exc:
        raise SourceError(f"Could not trigger official download at {url}: {exc}") from exc
