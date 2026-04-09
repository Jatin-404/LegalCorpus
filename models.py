from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DocumentMetadata:
    source: str
    title: str
    document_type: str
    year: int | None
    document_url: str
    pdf_url: str
    parent_page_url: str
    page_title: str
    anchor_text: str
    crawl_timestamp: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "title": self.title,
            "document_type": self.document_type,
            "year": "" if self.year is None else str(self.year),
            "document_url": self.document_url,
            "pdf_url": self.pdf_url,
            "parent_page_url": self.parent_page_url,
            "page_title": self.page_title,
            "anchor_text": self.anchor_text,
            "crawl_timestamp": self.crawl_timestamp,
        }


@dataclass(slots=True)
class SourceStats:
    source: str
    pages_visited: int = 0
    documents_found: int = 0
    pdfs_found: int = 0
    failures: int = 0

    def register_page(self) -> None:
        self.pages_visited += 1

    def register_failure(self) -> None:
        self.failures += 1

    def register_document(self, *, has_pdf: bool) -> None:
        self.documents_found += 1
        if has_pdf:
            self.pdfs_found += 1


@dataclass(slots=True)
class SourceRunResult:
    source: str
    records: list[DocumentMetadata] = field(default_factory=list)
    stats: SourceStats | None = None


@dataclass(slots=True)
class LoadedPage:
    url: str
    html: str
    title: str
    via_browser: bool
    status_code: int | None = None
