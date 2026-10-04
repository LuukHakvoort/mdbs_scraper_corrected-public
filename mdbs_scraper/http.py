"""Small standard-library HTTP client with retries and per-host throttling."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import random
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .errors import SourceError


LOG = logging.getLogger(__name__)

CONTACT_ENV_VAR = "MDBS_SCRAPER_CONTACT"
_USER_AGENT_BASE = "MDB-Thesis-Scraper/2.0 (+academic research"


def build_user_agent() -> str:
    """The one user agent every request (HTTP and browser) sends.

    The contact comes from the MDBS_SCRAPER_CONTACT environment variable
    (e.g. an e-mail address) so it is never committed. Unset, the agent says
    so plainly rather than promising a contact that does not exist.
    """

    contact = os.environ.get(CONTACT_ENV_VAR, "").strip()
    return f"{_USER_AGENT_BASE}; contact: {contact})" if contact else f"{_USER_AGENT_BASE}; no contact configured)"


USER_AGENT = build_user_agent()

# Per-host request spacing is shared by every client in the process: each
# scraper builds its own HttpClient, so a per-instance throttle let several
# banks under --parallel hit the same host (iatiregistry.org serves four of
# them) without any coordination. One lock per host keeps hosts independent.
_HOST_LOCKS: dict[str, threading.Lock] = {}
_HOST_LAST_REQUEST: dict[str, float] = {}
_HOST_LOCKS_GUARD = threading.Lock()

# Every successful fetch in this process, for manifest.json's input hashes.
_FETCH_LOG: list[dict[str, object]] = []
_FETCH_LOG_LOCK = threading.Lock()
_RAW_ARCHIVE_DIR: Path | None = None
_CONTACT_WARNING_EMITTED = False


def _host_lock(host: str) -> threading.Lock:
    with _HOST_LOCKS_GUARD:
        return _HOST_LOCKS.setdefault(host, threading.Lock())


def configure_raw_archive(directory: str | Path | None) -> None:
    """Save every fetched body under ``directory`` (``--save-raw``); None disables it."""

    global _RAW_ARCHIVE_DIR
    _RAW_ARCHIVE_DIR = Path(directory).expanduser().resolve() if directory else None
    if _RAW_ARCHIVE_DIR:
        _RAW_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_log() -> list[dict[str, object]]:
    """A copy of every successful fetch so far (url, status, bytes, sha256, time)."""

    with _FETCH_LOG_LOCK:
        return [dict(entry) for entry in _FETCH_LOG]


def extend_fetch_log(entries: list[dict[str, object]]) -> None:
    """Add fetches recorded by a child process (see cli._run_bank_isolated)."""

    with _FETCH_LOG_LOCK:
        _FETCH_LOG.extend(dict(entry) for entry in entries)


def reset_fetch_log() -> None:
    with _FETCH_LOG_LOCK:
        _FETCH_LOG.clear()


def _retry_after_seconds(value: str) -> float | None:
    """Parse Retry-After in either of its two forms: delta-seconds or an HTTP-date."""

    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _record_fetch(method: str, url: str, response: "HttpResponse", request_body: bytes | None) -> None:
    digest = hashlib.sha256(response.body).hexdigest()
    entry: dict[str, object] = {
        "method": method,
        "url": url,
        "final_url": response.url,
        "status": response.status,
        "bytes": len(response.body),
        "sha256": digest,
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if _RAW_ARCHIVE_DIR is not None:
        key = hashlib.sha256(f"{method} {url}".encode("utf-8") + (request_body or b"")).hexdigest()
        content_type = response.headers.get_content_type() if response.headers else ""
        extension = {
            "application/json": ".json", "text/html": ".html", "text/csv": ".csv",
            "application/xml": ".xml", "text/xml": ".xml",
        }.get(content_type, ".bin")
        body_path = _RAW_ARCHIVE_DIR / f"{key}{extension}"
        temporary = body_path.with_suffix(body_path.suffix + ".part")
        temporary.write_bytes(response.body)
        temporary.replace(body_path)  # never leave a truncated file behind
        sidecar = dict(entry, content_type=content_type, path=body_path.name)
        body_path.with_suffix(".meta.json").write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        entry["raw_path"] = str(body_path)
    with _FETCH_LOG_LOCK:
        _FETCH_LOG.append(entry)

# DER encoding of the AIA "CA Issuers" access-method OID (1.3.6.1.5.5.7.48.2).
_CA_ISSUERS_OID = bytes.fromhex("06082b06010505073002")


def _ca_issuers_uri_from_der(der: bytes) -> str | None:
    """Extract an AIA "CA Issuers" URI from a DER-encoded certificate.

    Deliberately minimal (no general ASN.1 parser, to keep this project
    dependency-free): locates the CA-Issuers OID and reads the [6] IMPLICIT
    IA5String (URI) GeneralName that immediately follows it, per RFC 5280's
    AccessDescription structure.
    """

    index = der.find(_CA_ISSUERS_OID)
    if index == -1:
        return None
    pos = index + len(_CA_ISSUERS_OID)
    if pos >= len(der) or der[pos] != 0x86:  # [6] IMPLICIT IA5String (uniformResourceIdentifier)
        return None
    pos += 1
    if pos >= len(der):
        return None
    length = der[pos]
    pos += 1
    if length & 0x80:  # long-form DER length
        num_bytes = length & 0x7F
        if num_bytes == 0 or pos + num_bytes > len(der):
            return None
        length = int.from_bytes(der[pos : pos + num_bytes], "big")
        pos += num_bytes
    uri = der[pos : pos + length]
    if len(uri) != length:
        return None
    try:
        return uri.decode("ascii")
    except UnicodeDecodeError:
        return None


def _fetch_supplemental_ssl_context(host: str, port: int, timeout: float) -> ssl.SSLContext | None:
    """Build a trust-completing SSLContext for a server with an incomplete chain.

    Some official sources present only their leaf certificate, omitting the
    intermediate CA (a server misconfiguration, not a local trust-store
    problem -- confirmed by successful verification once the intermediate is
    supplied). This fetches that missing intermediate from the AIA "CA
    Issuers" URI embedded in the server's own leaf certificate and returns a
    default context with it added, so verification can complete. Returns
    ``None`` if any step fails, so the caller can fall back to the original
    error rather than silently weakening verification.
    """

    try:
        unverified = ssl._create_unverified_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with unverified.wrap_socket(sock, server_hostname=host) as tls:
                leaf_der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError):
        return None
    if not leaf_der:
        return None
    uri = _ca_issuers_uri_from_der(leaf_der)
    if not uri or not uri.startswith(("http://", "https://")):
        return None
    try:
        with urlopen(uri, timeout=timeout) as response:
            issuer_bytes = response.read()
    except (URLError, OSError):
        return None
    try:
        issuer_pem = ssl.DER_cert_to_PEM_cert(issuer_bytes)
    except ssl.SSLError:
        issuer_pem = issuer_bytes.decode("ascii", errors="ignore")
    context = ssl.create_default_context()
    try:
        context.load_verify_locations(cadata=issuer_pem)
    except ssl.SSLError:
        return None
    return context


def _is_missing_issuer_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ssl.SSLCertVerificationError) and reason.verify_code == 20


@dataclass(slots=True)
class HttpResponse:
    url: str
    status: int
    headers: Message
    body: bytes

    def text(self) -> str:
        content_type = self.headers.get_content_charset() if self.headers else None
        candidates = [content_type, "utf-8-sig", "utf-8", "latin-1"]
        for encoding in candidates:
            if not encoding:
                continue
            try:
                return self.body.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return self.body.decode("utf-8", errors="replace")


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float = 45.0,
        request_delay: float = 0.75,
        retries: int = 3,
        user_agent: str | None = None,
    ) -> None:
        global _CONTACT_WARNING_EMITTED
        self.timeout = timeout
        self.request_delay = max(0.0, request_delay)
        self.retries = max(0, retries)
        self.user_agent = user_agent or build_user_agent()
        self._supplemental_ssl_contexts: dict[str, ssl.SSLContext | None] = {}
        if not os.environ.get(CONTACT_ENV_VAR) and not _CONTACT_WARNING_EMITTED:
            _CONTACT_WARNING_EMITTED = True
            LOG.warning("%s is not set; requests carry no contact address in their user agent",
                        CONTACT_ENV_VAR)

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        with _host_lock(host):
            elapsed = time.monotonic() - _HOST_LAST_REQUEST.get(host, 0.0)
            wait = self.request_delay - elapsed
            if wait > 0:
                time.sleep(wait)
            _HOST_LAST_REQUEST[host] = time.monotonic()

    def _supplemental_context_for(self, url: str) -> ssl.SSLContext | None:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host in self._supplemental_ssl_contexts:
            return self._supplemental_ssl_contexts[host]
        # Fetched outside the throttle lock: this hits the network, and a rare
        # duplicate fetch from a concurrent thread racing on the same host is
        # harmless (idempotent), whereas holding the lock here would stall
        # every other host's request throttling for the fetch's full duration.
        context = _fetch_supplemental_ssl_context(host, parsed.port or 443, self.timeout)
        self._supplemental_ssl_contexts[host] = context
        return context

    def get(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        accept: str = "*/*",
    ) -> HttpResponse:
        if params:
            from urllib.parse import urlencode

            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode(params)}"
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": accept,
                "Accept-Encoding": "gzip",
            },
        )
        return self._send(request, url)

    def post(self, url: str, *, json_body: str, accept: str = "*/*") -> HttpResponse:
        request = Request(
            url,
            data=json_body.encode("utf-8"),
            headers={
                "User-Agent": self.user_agent,
                "Accept": accept,
                "Accept-Encoding": "gzip",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._send(request, url)

    def _send(self, request: Request, url: str) -> HttpResponse:
        result = self._send_with_retries(request, url)
        LOG.debug("%s %s -> %s (%d bytes)", request.get_method(), url, result.status, len(result.body))
        _record_fetch(request.get_method(), url, result, request.data if isinstance(request.data, bytes) else None)
        return result

    def _send_with_retries(self, request: Request, url: str) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle(url)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                    if response.headers.get("Content-Encoding", "").lower() == "gzip":
                        body = gzip.decompress(body)
                    return HttpResponse(
                        response.geturl(), int(response.status), response.headers, body
                    )
            except HTTPError as exc:
                last_error = exc
                LOG.debug("%s %s -> HTTP %s (attempt %d)", request.get_method(), url, exc.code, attempt + 1)
                if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt >= self.retries:
                    break
                retry_after = _retry_after_seconds(exc.headers.get("Retry-After", "") if exc.headers else "")
                wait = retry_after if retry_after is not None else 2**attempt + random.random()
                time.sleep(min(wait, 30.0))
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                LOG.debug("%s %s -> %s (attempt %d)", request.get_method(), url, exc, attempt + 1)
                if _is_missing_issuer_error(exc):
                    context = self._supplemental_context_for(url)
                    if context is not None:
                        try:
                            with urlopen(request, timeout=self.timeout, context=context) as response:
                                body = response.read()
                                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                                    body = gzip.decompress(body)
                                return HttpResponse(
                                    response.geturl(), int(response.status), response.headers, body
                                )
                        except (URLError, TimeoutError, OSError) as retry_exc:
                            last_error = retry_exc
                if attempt >= self.retries:
                    break
                time.sleep(min(2**attempt + random.random(), 30.0))
        raise SourceError(f"Could not retrieve {url}: {last_error}") from last_error
