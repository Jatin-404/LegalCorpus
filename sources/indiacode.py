from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from models import DocumentMetadata, SourceRunResult, SourceStats
from sources.base import BaseSourceCollector, SourceRuntime
from utils.files import utc_now_iso
from utils.http import (
    absolutize_url,
    canonicalize_url,
    clean_text,
    extract_page_title,
    extract_year,
    make_soup,
)


LOGGER = logging.getLogger(__name__)
SHOWING_RE = re.compile(r"Showing items \d+ to \d+ of (\d+)", re.IGNORECASE)


@dataclass(slots=True)
class ActListingRow:
    title: str
    detail_url: str
    enactment_date: str
    act_number: str
    parent_page_url: str
    page_title: str


class IndiaCodeCollector(BaseSourceCollector):
    source_name = "indiacode"
    base_url = "https://www.indiacode.nic.in"
    collection_handle = "123456789/1362"
    subordinate_table_map = {
        "Rules": "myTableRules",
        "Regulations": "myTableRegulation",
        "Notifications": "myTableNotification",
        "Orders": "myTableOrders",
        "Circulars": "myTableCircular",
        "Ordinances": "myTableOrdinances",
        "Statutes": "myTableStatutes",
        "By-Laws": "myTableByLaws",
    }

    def discover(self, runtime: SourceRuntime) -> SourceRunResult:
        stats = SourceStats(source=self.source_name)
        records: list[DocumentMetadata] = []
        seen_detail_pages: set[str] = set()
        seen_documents: set[str] = set()
        crawl_timestamp = utc_now_iso()
        total_expected: int | None = None

        for page_number in range(runtime.settings.indiacode.max_browse_pages):
            offset = page_number * runtime.settings.indiacode.results_per_page
            browse_url = self._build_browse_url(
                browse_type=runtime.settings.indiacode.browse_type,
                offset=offset,
                results_per_page=runtime.settings.indiacode.results_per_page,
            )
            browse_page = self.load_page(runtime, browse_url)
            stats.register_page()
            if browse_page is None:
                stats.register_failure()
                continue

            listing_rows, total_expected = self._parse_browse_page(
                browse_page.html,
                page_url=browse_page.url,
                fallback_page_title=browse_page.title,
            )
            runtime.logger.info(
                "[%s] Browse page %s yielded %s act detail links",
                self.source_name,
                page_number + 1,
                len(listing_rows),
            )
            if not listing_rows:
                break

            for listing_row in listing_rows:
                detail_key = canonicalize_url(listing_row.detail_url, source=self.source_name)
                if detail_key in seen_detail_pages:
                    continue
                seen_detail_pages.add(detail_key)

                detail_page = self.load_page(runtime, listing_row.detail_url)
                stats.register_page()
                if detail_page is None:
                    stats.register_failure()
                    continue

                for record in self._extract_detail_records(
                    detail_page.html,
                    detail_page_url=detail_page.url,
                    listing_row=listing_row,
                    crawl_timestamp=crawl_timestamp,
                ):
                    dedupe_key = canonicalize_url(record.document_url, source=self.source_name)
                    if dedupe_key in seen_documents:
                        continue
                    seen_documents.add(dedupe_key)
                    records.append(record)
                    stats.register_document(has_pdf=bool(record.pdf_url))

            if len(listing_rows) < runtime.settings.indiacode.results_per_page:
                break
            if total_expected is not None and offset + len(listing_rows) >= total_expected:
                break

        return SourceRunResult(source=self.source_name, records=records, stats=stats)

    def _build_browse_url(self, *, browse_type: str, offset: int, results_per_page: int) -> str:
        params = {
            "type": browse_type,
            "order": "ASC",
            "rpp": str(results_per_page),
            "offset": str(offset),
        }
        if browse_type == "shorttitle":
            params["sort_by"] = "3"
        return f"{self.base_url}/handle/{self.collection_handle}/browse?{urlencode(params)}"

    def _parse_browse_page(
        self,
        html: str,
        *,
        page_url: str,
        fallback_page_title: str,
    ) -> tuple[list[ActListingRow], int | None]:
        soup = make_soup(html)
        page_title = extract_page_title(soup) or fallback_page_title
        rows: list[ActListingRow] = []

        for anchor in soup.find_all("a", href=True):
            if clean_text(anchor.get_text(" ", strip=True)).lower() != "view...":
                continue
            detail_url = absolutize_url(page_url, anchor["href"])
            if "/handle/" not in detail_url:
                continue

            row = anchor.find_parent("tr")
            if row is None:
                continue

            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            rows.append(
                ActListingRow(
                    title=clean_text(cells[2].get_text(" ", strip=True)),
                    detail_url=detail_url,
                    enactment_date=clean_text(cells[0].get_text(" ", strip=True)),
                    act_number=clean_text(cells[1].get_text(" ", strip=True)),
                    parent_page_url=page_url,
                    page_title=page_title,
                )
            )

        total_match = SHOWING_RE.search(clean_text(soup.get_text(" ", strip=True)))
        total_expected = int(total_match.group(1)) if total_match else None
        return rows, total_expected

    def _extract_detail_records(
        self,
        html: str,
        *,
        detail_page_url: str,
        listing_row: ActListingRow,
        crawl_timestamp: str,
    ) -> list[DocumentMetadata]:
        soup = make_soup(html)
        page_title = extract_page_title(soup) or listing_row.title
        records: list[DocumentMetadata] = []
        seen_pdf_urls: set[str] = set()

        for record in self._extract_primary_act_records(
            soup,
            detail_page_url=detail_page_url,
            listing_row=listing_row,
            page_title=page_title,
            crawl_timestamp=crawl_timestamp,
        ):
            if record.pdf_url in seen_pdf_urls:
                continue
            seen_pdf_urls.add(record.pdf_url)
            records.append(record)

        for document_type, table_id in self.subordinate_table_map.items():
            table = soup.find("table", id=table_id)
            if table is None:
                continue
            for record in self._extract_subordinate_table_records(
                table,
                document_type=document_type,
                detail_page_url=detail_page_url,
                page_title=page_title,
                crawl_timestamp=crawl_timestamp,
            ):
                if record.pdf_url in seen_pdf_urls:
                    continue
                seen_pdf_urls.add(record.pdf_url)
                records.append(record)

        return records

    def _extract_primary_act_records(
        self,
        soup: BeautifulSoup,
        *,
        detail_page_url: str,
        listing_row: ActListingRow,
        page_title: str,
        crawl_timestamp: str,
    ) -> list[DocumentMetadata]:
        records: list[DocumentMetadata] = []
        top_title_nodes = soup.select("p#short_title")

        if top_title_nodes:
            for title_node in top_title_nodes:
                anchor = title_node.find_parent("a", href=True)
                if anchor is None:
                    continue
                pdf_url = absolutize_url(detail_page_url, anchor["href"])
                if not pdf_url:
                    continue
                title = clean_text(title_node.get_text(" ", strip=True)) or listing_row.title
                year = extract_year(title, listing_row.enactment_date, page_title)
                records.append(
                    self.make_record(
                        title=title,
                        document_type="Act",
                        year=year,
                        document_url=canonicalize_url(pdf_url, source=self.source_name),
                        pdf_url=pdf_url,
                        parent_page_url=canonicalize_url(detail_page_url, source=self.source_name),
                        page_title=page_title,
                        anchor_text=title,
                        crawl_timestamp=crawl_timestamp,
                    )
                )

        if records:
            return records

        citation_pdf = soup.find("meta", attrs={"name": "citation_pdf_url"})
        if citation_pdf and citation_pdf.get("content"):
            pdf_url = absolutize_url(detail_page_url, citation_pdf["content"])
            title = clean_text(listing_row.title) or page_title
            year = extract_year(title, listing_row.enactment_date, page_title)
            records.append(
                self.make_record(
                    title=title,
                    document_type="Act",
                    year=year,
                    document_url=canonicalize_url(pdf_url, source=self.source_name),
                    pdf_url=pdf_url,
                    parent_page_url=canonicalize_url(detail_page_url, source=self.source_name),
                    page_title=page_title,
                    anchor_text=title,
                    crawl_timestamp=crawl_timestamp,
                )
            )
        return records

    def _extract_subordinate_table_records(
        self,
        table: BeautifulSoup,
        *,
        document_type: str,
        detail_page_url: str,
        page_title: str,
        crawl_timestamp: str,
    ) -> list[DocumentMetadata]:
        records: list[DocumentMetadata] = []

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            row_texts = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
            if not any(row_texts):
                continue

            published_text = row_texts[0]
            english_title = row_texts[1] if len(row_texts) > 1 else page_title
            hindi_title = row_texts[2] if len(row_texts) > 2 else ""

            pdf_anchors = [
                anchor
                for anchor in row.find_all("a", href=True)
                if "/bitstream/" in anchor["href"] or ".pdf" in anchor["href"].lower()
            ]
            if not pdf_anchors:
                continue

            english_pdf = pdf_anchors[0] if len(pdf_anchors) >= 1 else None
            hindi_pdf = pdf_anchors[1] if len(pdf_anchors) >= 2 else None

            if english_pdf is not None:
                english_pdf_url = absolutize_url(detail_page_url, english_pdf["href"])
                records.append(
                    self.make_record(
                        title=english_title or page_title,
                        document_type=document_type,
                        year=extract_year(published_text, english_title, page_title),
                        document_url=canonicalize_url(english_pdf_url, source=self.source_name),
                        pdf_url=english_pdf_url,
                        parent_page_url=canonicalize_url(detail_page_url, source=self.source_name),
                        page_title=page_title,
                        anchor_text=english_title or document_type,
                        crawl_timestamp=crawl_timestamp,
                    )
                )

            if hindi_pdf is not None:
                hindi_pdf_url = absolutize_url(detail_page_url, hindi_pdf["href"])
                hindi_label = hindi_title or english_title or page_title
                records.append(
                    self.make_record(
                        title=hindi_label,
                        document_type=document_type,
                        year=extract_year(published_text, hindi_title, english_title, page_title),
                        document_url=canonicalize_url(hindi_pdf_url, source=self.source_name),
                        pdf_url=hindi_pdf_url,
                        parent_page_url=canonicalize_url(detail_page_url, source=self.source_name),
                        page_title=page_title,
                        anchor_text=hindi_label,
                        crawl_timestamp=crawl_timestamp,
                    )
                )

        return records
