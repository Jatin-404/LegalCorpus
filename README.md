# LegalCorpus

Python metadata collector for official Indian legal and regulatory documents, with source-specific support for:

- India Code
- eGazette of India

The current version discovers documents and exports structured metadata only. It does not download PDFs.

## What This Collector Does

1. Discovers official document records from source-specific entry points.
2. Extracts structured metadata suitable for downstream RAG / knowledge-base ingestion.
3. Deduplicates records by canonical document URL.
4. Exports:
   - `legal_corpus_links.jsonl`
   - `legal_corpus_links.csv`

## Why The Old Scraper Failed

- India Code frequently blocks or degrades plain request-only access for some detail flows.
- eGazette uses ASP.NET state, sessioned URLs, postbacks, and image-button download controls rather than simple PDF anchors.
- Generic anchor crawling misses real download/document actions on both sites.

This project addresses that by using:

- `requests` where server-rendered HTML is enough
- Playwright where browser state or rendered interactions are required
- source-specific parsers instead of a single generic crawler

## Folder Structure

```text
.
|-- .env.example
|-- config.py
|-- main.py
|-- models.py
|-- pipeline
|   |-- __init__.py
|   `-- discover.py
|-- sources
|   |-- __init__.py
|   |-- base.py
|   |-- egazette.py
|   `-- indiacode.py
|-- utils
|   |-- __init__.py
|   |-- browser.py
|   |-- files.py
|   `-- http.py
|-- pyproject.toml
`-- README.md
```

## Output Schema

Both JSONL and CSV use these fields:

- `source`
- `title`
- `document_type`
- `year`
- `document_url`
- `pdf_url`
- `parent_page_url`
- `page_title`
- `anchor_text`
- `crawl_timestamp`

## Install

```powershell
uv sync
```

If Playwright cannot launch your local Edge installation, install Chromium for Playwright:

```powershell
uv run playwright install chromium
```

## Configure

Optional:

```powershell
Copy-Item .env.example .env
```

Defaults are already aimed at Windows + `uv` usage. On Windows, the browser launcher defaults to `msedge` if available.

Useful eGazette tuning knobs for staged runs:

- `LEGAL_CORPUS_EGAZETTE_ENTRYPOINTS=Bills & Acts`
- `LEGAL_CORPUS_EGAZETTE_MAX_ROWS_PER_PAGE=25`

## Run

Run both sources:

```powershell
uv run main.py
```

Run only India Code:

```powershell
uv run main.py --sources indiacode
```

Run only eGazette:

```powershell
uv run main.py --sources egazette
```

Run with a visible browser for debugging:

```powershell
uv run main.py --headed
```

Write outputs to a different folder:

```powershell
uv run main.py --output-dir .\out
```

## How Each Source Works

### India Code

- Starts from official Central Act browse pages
- Walks browse pagination via `offset` and `rpp`
- Opens act detail pages
- Extracts primary act PDFs from the detail view
- Extracts subordinate legislation from source-specific tables such as Rules, Regulations, Notifications, Orders, Circulars, Ordinances, and Statutes
- Falls back to Playwright when request-based fetches are blocked or degraded

### eGazette

- Starts from the official homepage
- Uses Playwright because the site relies on sessioned URLs, ASP.NET postbacks, and download controls
- Parses homepage recent gazettes, official category entry points, and navigable search/directory paths
- Extracts real download URLs by triggering the official download control without adding a PDF download stage to the pipeline
- Uses safe page-pattern and pagination heuristics instead of brute-forcing opaque IDs

## Testing On Your Machine

### Quick smoke test

1. Run India Code only:

```powershell
uv run main.py --sources indiacode
```

2. Confirm these files were created:

```powershell
Get-Item .\legal_corpus_links.jsonl, .\legal_corpus_links.csv
```

3. Inspect the first few JSONL rows:

```powershell
Get-Content .\legal_corpus_links.jsonl -TotalCount 5
```

### eGazette verification

Run with a headed browser so you can observe navigation:

```powershell
uv run main.py --sources egazette --headed
```

You should see the browser open the official eGazette homepage, move through official entry points, and export records with populated `pdf_url` values where the site exposes them through the download control.

## Notes

- No PDF downloader is implemented in this version.
- Deduplication is by canonical document URL, with source-aware normalization for sessioned eGazette paths and India Code handle variants.
- The pipeline is intentionally conservative about where it navigates and does not brute-force guessed document IDs.
