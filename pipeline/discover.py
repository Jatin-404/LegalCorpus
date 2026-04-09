from __future__ import annotations

import logging
from dataclasses import dataclass

from config import CollectorSettings
from models import DocumentMetadata, SourceRunResult, SourceStats
from sources.base import BaseSourceCollector, SourceRuntime
from utils.files import ensure_directory, write_csv, write_jsonl
from utils.http import RequestsClient, canonicalize_url


@dataclass(slots=True)
class PipelineResult:
    records: list[DocumentMetadata]
    source_results: list[SourceRunResult]


def deduplicate_records(records: list[DocumentMetadata]) -> list[DocumentMetadata]:
    unique: list[DocumentMetadata] = []
    seen_keys: set[str] = set()

    for record in records:
        primary_key = canonicalize_url(record.document_url, source=record.source)
        if not primary_key:
            primary_key = canonicalize_url(record.pdf_url, source=record.source)
        if primary_key in seen_keys:
            continue
        seen_keys.add(primary_key)
        unique.append(record)
    return unique


class MetadataDiscoveryPipeline:
    def __init__(self, settings: CollectorSettings, sources: list[BaseSourceCollector]) -> None:
        self.settings = settings
        self.sources = sources
        self.logger = logging.getLogger(self.__class__.__name__)

    def run(self) -> PipelineResult:
        ensure_directory(self.settings.output_dir)
        http = RequestsClient(
            user_agent=self.settings.user_agent,
            timeout_seconds=self.settings.request_timeout_seconds,
            retry_settings=self.settings.retry,
            polite_delay_seconds=self.settings.polite_delay_seconds,
        )

        source_results: list[SourceRunResult] = []
        browser_context_manager = None

        try:
            if any(source.source_name in {"indiacode", "egazette"} for source in self.sources):
                from utils.browser import BrowserManager

                browser_context_manager = BrowserManager(self.settings.browser, self.settings.user_agent)
                browser_manager = browser_context_manager.__enter__()
            else:
                browser_manager = None

            for source in self.sources:
                source_logger = logging.getLogger(f"sources.{source.source_name}")
                runtime = SourceRuntime(
                    settings=self.settings,
                    http=http,
                    browser=browser_manager,
                    logger=source_logger,
                )
                source_result = source.discover(runtime)
                source_results.append(source_result)

            combined = [record for result in source_results for record in result.records]
            deduplicated = deduplicate_records(combined)
            write_jsonl(self.settings.jsonl_output, deduplicated)
            write_csv(self.settings.csv_output, deduplicated)
            return PipelineResult(records=deduplicated, source_results=source_results)
        finally:
            if browser_context_manager is not None:
                browser_context_manager.__exit__(None, None, None)
            http.close()


def log_pipeline_summary(result: PipelineResult) -> None:
    logger = logging.getLogger("summary")
    logger.info("Exported %s deduplicated documents", len(result.records))
    for source_result in result.source_results:
        stats = source_result.stats or SourceStats(source=source_result.source)
        logger.info(
            "[%s] pages_visited=%s documents_found=%s pdfs_found=%s failures=%s",
            source_result.source,
            stats.pages_visited,
            stats.documents_found,
            stats.pdfs_found,
            stats.failures,
        )
