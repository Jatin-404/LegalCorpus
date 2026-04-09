from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from html import unescape
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urldefrag, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import RetrySettings


LOGGER = logging.getLogger(__name__)
BLOCKED_STATUS_CODES = {401, 403, 429, 500, 503}
SESSION_SEGMENT_RE = re.compile(r"/\(S\([^)]+\)\)/", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(18\d{2}|19\d{2}|20\d{2})\b")


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    status_code: int | None
    text: str
    headers: dict[str, str]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300 and not self.error


class RequestsClient:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: int,
        retry_settings: RetrySettings,
        polite_delay_seconds: float,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._polite_delay_seconds = polite_delay_seconds
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Upgrade-Insecure-Requests": "1",
            }
        )

        retries = Retry(
            total=max(retry_settings.attempts - 1, 0),
            read=max(retry_settings.attempts - 1, 0),
            connect=max(retry_settings.attempts - 1, 0),
            backoff_factor=retry_settings.backoff_seconds,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def close(self) -> None:
        self.session.close()

    def get(self, url: str, *, referer: str | None = None) -> FetchResult:
        self._sleep_if_needed()
        headers: dict[str, str] = {}
        if referer:
            headers["Referer"] = referer

        try:
            response = self.session.get(
                url,
                headers=headers or None,
                timeout=self._timeout_seconds,
                allow_redirects=True,
            )
            self._last_request_at = time.monotonic()
            return FetchResult(
                url=response.url,
                status_code=response.status_code,
                text=response.text,
                headers=dict(response.headers),
            )
        except requests.RequestException as exc:
            self._last_request_at = time.monotonic()
            LOGGER.warning("HTTP GET failed for %s: %s", url, exc)
            return FetchResult(
                url=url,
                status_code=None,
                text="",
                headers={},
                error=str(exc),
            )

    def _sleep_if_needed(self) -> None:
        if not self._last_request_at:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._polite_delay_seconds:
            time.sleep(self._polite_delay_seconds - elapsed)


def make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", unescape(value)).strip()


def absolutize_url(base_url: str, href: str | None) -> str:
    if not href:
        return ""
    href, _ = urldefrag(href.strip())
    return urljoin(base_url, href)


def canonicalize_url(url: str, *, source: str | None = None) -> str:
    if not url:
        return ""

    raw_url, _ = urldefrag(url.strip())
    parsed = urlsplit(raw_url)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = SESSION_SEGMENT_RE.sub("/", parsed.path or "/")
    path = re.sub(r"/{2,}", "/", path).rstrip("/")
    path = path or "/"

    query_items = parse_qsl(parsed.query, keep_blank_values=False)
    if source == "indiacode" or "indiacode" in netloc:
        query_items = [(key, value) for key, value in query_items if key not in {"view_type", "col", "locale"}]
        if path.startswith("/handle/"):
            path = path.rstrip("/")
    if source == "egazette" or "egazette" in netloc:
        query_items = [(key, value) for key, value in query_items if key.lower() not in {"aspsessionid"}]

    normalized_query = urlencode(sorted(query_items))
    return urlunsplit((scheme, netloc, path, normalized_query, ""))


def extract_year(*values: str) -> int | None:
    for value in values:
        if not value:
            continue
        match = YEAR_RE.search(value)
        if match:
            return int(match.group(1))
    return None


def extract_page_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.get_text(strip=True):
        return clean_text(soup.title.get_text(" ", strip=True))
    for selector in ("h1", "h2", "title"):
        node = soup.select_one(selector)
        if node:
            return clean_text(node.get_text(" ", strip=True))
    return ""


def classify_document_type(title: str, fallback: str = "Document") -> str:
    lowered = title.lower()
    checks: Iterable[tuple[str, str]] = (
        ("recruitment rules", "Recruitment Rules"),
        ("rules", "Rules"),
        ("regulations", "Regulations"),
        ("regulation", "Regulation"),
        ("notification", "Notification"),
        ("order", "Order"),
        ("circular", "Circular"),
        ("ordinance", "Ordinance"),
        ("statute", "Statute"),
        ("by-law", "By-Law"),
        ("act", "Act"),
        ("bill", "Bill"),
        ("gazette", "Gazette"),
    )
    for needle, document_type in checks:
        if needle in lowered:
            return document_type
    return fallback


def looks_like_blocked_page(result: FetchResult) -> bool:
    if result.status_code in BLOCKED_STATUS_CODES:
        return True
    lowered = result.text.lower()
    markers = (
        "access denied",
        "request rejected",
        "temporarily unavailable",
        "forbidden",
        "page not found",
    )
    return any(marker in lowered for marker in markers)
