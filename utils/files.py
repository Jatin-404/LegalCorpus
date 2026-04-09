from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from models import DocumentMetadata


CSV_FIELDNAMES = [
    "source",
    "title",
    "document_type",
    "year",
    "document_url",
    "pdf_url",
    "parent_page_url",
    "page_title",
    "anchor_text",
    "crawl_timestamp",
]


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_jsonl(path: Path, records: list[DocumentMetadata]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def write_csv(path: Path, records: list[DocumentMetadata]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_dict())
