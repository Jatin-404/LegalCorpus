from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from config import CollectorSettings
from models import DocumentMetadata, LoadedPage, SourceRunResult
from utils.browser import BrowserManager
from utils.http import RequestsClient, clean_text, looks_like_blocked_page, make_soup


@dataclass(slots=True)
class SourceRuntime:
    settings: CollectorSettings
    http: RequestsClient
    browser: BrowserManager | None
    logger: logging.Logger


class BaseSourceCollector(ABC):
    source_name: str

    @abstractmethod
    def discover(self, runtime: SourceRuntime) -> SourceRunResult:
        raise NotImplementedError

    def load_page(
        self,
        runtime: SourceRuntime,
        url: str,
        *,
        prefer_browser: bool = False,
    ) -> LoadedPage | None:
        if prefer_browser:
            return self._load_with_browser(runtime, url)

        result = runtime.http.get(url)
        if result.ok and not looks_like_blocked_page(result):
            soup = make_soup(result.text)
            page_title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
            return LoadedPage(
                url=result.url,
                html=result.text,
                title=page_title,
                via_browser=False,
                status_code=result.status_code,
            )

        runtime.logger.info(
            "[%s] Falling back to browser for %s (status=%s error=%s)",
            self.source_name,
            url,
            result.status_code,
            result.error,
        )
        return self._load_with_browser(runtime, url)

    def _load_with_browser(self, runtime: SourceRuntime, url: str) -> LoadedPage | None:
        if runtime.browser is None:
            runtime.logger.error("[%s] Browser is required but not available for %s", self.source_name, url)
            return None
        try:
            return runtime.browser.fetch_page(url)
        except Exception as exc:
            runtime.logger.warning("[%s] Browser fetch failed for %s: %s", self.source_name, url, exc)
            return None

    def make_record(
        self,
        *,
        title: str,
        document_type: str,
        year: int | None,
        document_url: str,
        pdf_url: str,
        parent_page_url: str,
        page_title: str,
        anchor_text: str,
        crawl_timestamp: str,
    ) -> DocumentMetadata:
        return DocumentMetadata(
            source=self.source_name,
            title=title,
            document_type=document_type,
            year=year,
            document_url=document_url,
            pdf_url=pdf_url,
            parent_page_url=parent_page_url,
            page_title=page_title,
            anchor_text=anchor_text,
            crawl_timestamp=crawl_timestamp,
        )
