from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_SOURCE_NAMES = ("indiacode", "egazette")
DEFAULT_OUTPUT_JSONL = "legal_corpus_links.jsonl"
DEFAULT_OUTPUT_CSV = "legal_corpus_links.csv"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "LegalCorpusCollector/1.0"
)


def _read_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw.strip())


def _read_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw.strip())


def _read_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    values = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return tuple(values) or default


@dataclass(frozen=True, slots=True)
class RetrySettings:
    attempts: int
    backoff_seconds: float


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    browser_name: str
    browser_channel: str | None
    headless: bool
    navigation_timeout_ms: int
    download_timeout_ms: int


@dataclass(frozen=True, slots=True)
class IndiaCodeSettings:
    browse_type: str
    results_per_page: int
    max_browse_pages: int


@dataclass(frozen=True, slots=True)
class EGazetteSettings:
    max_listing_pages: int
    max_follow_links_per_entrypoint: int
    capture_download_urls: bool
    entrypoints: tuple[str, ...]
    max_rows_per_page: int | None


@dataclass(frozen=True, slots=True)
class CollectorSettings:
    output_dir: Path
    jsonl_output: Path
    csv_output: Path
    log_level: str
    request_timeout_seconds: int
    polite_delay_seconds: float
    user_agent: str
    sources: tuple[str, ...]
    retry: RetrySettings
    browser: BrowserSettings
    indiacode: IndiaCodeSettings
    egazette: EGazetteSettings

    @classmethod
    def from_env(
        cls,
        *,
        output_dir: Path | None = None,
        sources: tuple[str, ...] | None = None,
        browser_headless: bool | None = None,
        log_level: str | None = None,
    ) -> "CollectorSettings":
        load_dotenv()

        resolved_output_dir = (output_dir or Path(os.getenv("LEGAL_CORPUS_OUTPUT_DIR", "."))).resolve()
        resolved_sources = sources or _read_csv("LEGAL_CORPUS_SOURCES", DEFAULT_SOURCE_NAMES)
        resolved_headless = (
            browser_headless
            if browser_headless is not None
            else _read_bool("LEGAL_CORPUS_BROWSER_HEADLESS", True)
        )
        resolved_log_level = (log_level or os.getenv("LEGAL_CORPUS_LOG_LEVEL", "INFO")).upper()

        browser_name = os.getenv("LEGAL_CORPUS_BROWSER_NAME", "chromium")
        browser_channel = os.getenv("LEGAL_CORPUS_BROWSER_CHANNEL")
        if browser_channel is None and platform.system().lower().startswith("win"):
            browser_channel = "msedge"

        return cls(
            output_dir=resolved_output_dir,
            jsonl_output=resolved_output_dir / DEFAULT_OUTPUT_JSONL,
            csv_output=resolved_output_dir / DEFAULT_OUTPUT_CSV,
            log_level=resolved_log_level,
            request_timeout_seconds=_read_int("LEGAL_CORPUS_REQUEST_TIMEOUT_SECONDS", 30),
            polite_delay_seconds=_read_float("LEGAL_CORPUS_POLITE_DELAY_SECONDS", 1.25),
            user_agent=os.getenv("LEGAL_CORPUS_USER_AGENT", DEFAULT_USER_AGENT),
            sources=resolved_sources,
            retry=RetrySettings(
                attempts=_read_int("LEGAL_CORPUS_RETRY_ATTEMPTS", 3),
                backoff_seconds=_read_float("LEGAL_CORPUS_RETRY_BACKOFF_SECONDS", 1.5),
            ),
            browser=BrowserSettings(
                browser_name=browser_name,
                browser_channel=browser_channel,
                headless=resolved_headless,
                navigation_timeout_ms=_read_int("LEGAL_CORPUS_BROWSER_TIMEOUT_MS", 45000),
                download_timeout_ms=_read_int("LEGAL_CORPUS_DOWNLOAD_TIMEOUT_MS", 8000),
            ),
            indiacode=IndiaCodeSettings(
                browse_type=os.getenv("LEGAL_CORPUS_INDIACODE_BROWSE_TYPE", "shorttitle"),
                results_per_page=_read_int("LEGAL_CORPUS_INDIACODE_RESULTS_PER_PAGE", 100),
                max_browse_pages=_read_int("LEGAL_CORPUS_INDIACODE_MAX_BROWSE_PAGES", 12),
            ),
            egazette=EGazetteSettings(
                max_listing_pages=_read_int("LEGAL_CORPUS_EGAZETTE_MAX_LISTING_PAGES", 10),
                max_follow_links_per_entrypoint=_read_int(
                    "LEGAL_CORPUS_EGAZETTE_MAX_FOLLOW_LINKS_PER_ENTRYPOINT",
                    8,
                ),
                capture_download_urls=_read_bool("LEGAL_CORPUS_EGAZETTE_CAPTURE_DOWNLOAD_URLS", True),
                entrypoints=_read_csv("LEGAL_CORPUS_EGAZETTE_ENTRYPOINTS", ()),
                max_rows_per_page=(
                    None
                    if _read_int("LEGAL_CORPUS_EGAZETTE_MAX_ROWS_PER_PAGE", 0) <= 0
                    else _read_int("LEGAL_CORPUS_EGAZETTE_MAX_ROWS_PER_PAGE", 0)
                ),
            ),
        )
