# LegalCorpus

Production-oriented Python pipeline for collecting structured metadata for Indian legal and regulatory documents from official sources.

This repository is built for one-time corpus creation for downstream RAG and knowledge-base systems. It discovers official documents and exports structured metadata only. It does not download PDFs in the current version.

## Features

- Collects metadata from official legal and regulatory sources
- Uses source-specific collectors instead of generic crawling
- Supports browser-based collection with Playwright where needed
- Deduplicates records by canonical document URL
- Exports both JSONL and CSV
- Keeps source discovery, extraction, deduplication, and export clearly separated

## Supported Sources

- India Code
- eGazette of India

The architecture is modular so additional official sources can be added later.

## Scope

Included:

- Acts
- Rules
- Regulations
- Notifications
- Orders
- Circulars
- Ordinances
- Statutes
- Official gazette document metadata

Excluded in this version:

- Criminal-law-focused crawling
- Civil court judgment collection
- Full PDF downloads
- Brute-force guessing of document IDs

## Why This Project Exists

The original scraping approach failed because these sources do not behave like normal static websites:

- India Code can return blocked or degraded responses for some detail-page flows
- eGazette is an ASP.NET application with state, sessioned URLs, postbacks, popups, and non-anchor PDF actions
- Generic anchor crawling misses real document and PDF flows

This project solves that by using:

- `requests` where server-rendered HTML is enough
- `playwright` where browser state and interaction are required
- source-specific parsers instead of a generic crawler

## Tech Stack

- Python 3.12+
- `uv`
- `requests`
- `playwright`
- `beautifulsoup4`
- `lxml`
- `python-dotenv`

## Architecture

```text
main.py
  -> config.py
  -> pipeline/discover.py
      -> sources/indiacode.py
      -> sources/egazette.py
      -> utils/http.py
      -> utils/browser.py
      -> utils/files.py
      -> models.py
```

Responsibilities are separated into:

- Source discovery
- Metadata extraction
- Deduplication
- Export

## Repository Structure

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

## How It Works

### India Code

- Starts from official Central Act browse pages
- Paginates using official browse parameters like `offset` and `rpp`
- Extracts act detail page links from browse tables
- Visits each act detail page
- Extracts:
  - primary act PDFs
  - subordinate legislation tables such as Rules, Regulations, Notifications, Orders, Circulars, Ordinances, and Statutes
- Falls back to Playwright when request-based retrieval looks blocked or degraded

### eGazette

- Starts from the official homepage
- Uses Playwright because the site relies on ASP.NET state and browser interactions
- Opens official entry points such as Bills & Acts and Land Acquisition
- Parses structured gazette result tables
- Resolves real PDF URLs using the official popup flow:
  - click the official download control
  - open `ViewPDF.aspx`
  - read the popup iframe `src`
  - resolve the final `WriteReadData/...pdf` URL

## Output Files

The pipeline writes:

- `legal_corpus_links.jsonl`
- `legal_corpus_links.csv`

## Output Schema

Each record contains:

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

## Installation

### 1. Install dependencies

```powershell
uv sync
```

### 2. Install Playwright browser if needed

If Playwright cannot launch the local browser:

```powershell
uv run playwright install chromium
```

## Configuration

Optional:

```powershell
Copy-Item .env.example .env
```

Main runtime settings live in `.env.example`.

Useful knobs:

- `LEGAL_CORPUS_SOURCES`
- `LEGAL_CORPUS_OUTPUT_DIR`
- `LEGAL_CORPUS_BROWSER_HEADLESS`
- `LEGAL_CORPUS_INDIACODE_MAX_BROWSE_PAGES`
- `LEGAL_CORPUS_EGAZETTE_MAX_LISTING_PAGES`
- `LEGAL_CORPUS_EGAZETTE_ENTRYPOINTS`
- `LEGAL_CORPUS_EGAZETTE_MAX_ROWS_PER_PAGE`

Useful eGazette staged-run values:

- `LEGAL_CORPUS_EGAZETTE_ENTRYPOINTS=Bills & Acts`
- `LEGAL_CORPUS_EGAZETTE_MAX_ROWS_PER_PAGE=25`

## Usage

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

Run with a visible browser:

```powershell
uv run main.py --headed
```

Write outputs to a different directory:

```powershell
uv run main.py --output-dir .\out
```

## Recommended First Run

India Code smoke test:

```powershell
uv run main.py --sources indiacode
```

Inspect first rows:

```powershell
Get-Content .\legal_corpus_links.jsonl -TotalCount 5
```

Constrained eGazette smoke test:

```powershell
$env:LEGAL_CORPUS_EGAZETTE_ENTRYPOINTS='Bills & Acts'
$env:LEGAL_CORPUS_EGAZETTE_MAX_ROWS_PER_PAGE='5'
$env:LEGAL_CORPUS_EGAZETTE_MAX_LISTING_PAGES='1'
$env:LEGAL_CORPUS_EGAZETTE_MAX_FOLLOW_LINKS_PER_ENTRYPOINT='0'
uv run main.py --sources egazette
```

## Logging and Summary

At the end of a run, the collector prints:

- `pages_visited`
- `documents_found`
- `pdfs_found`
- `failures`

It also prints the final JSONL and CSV paths.

## Current Status

### India Code

India Code is currently the stronger implementation and has already produced large metadata exports successfully from real official browse and detail pages.

### eGazette

eGazette support is working and resolves real PDF URLs from official download flows, but coverage is still less complete than India Code because the site is more stateful and navigation-heavy.

## Known Limitations

- No PDF downloader in this version
- Some `document_type` values are heuristic classifications
- Some `year` extraction is best-effort, not source-perfect
- eGazette coverage is currently partial compared to India Code

## Design Principles

- Use official entry points only
- Avoid abusive request patterns
- Do not brute-force unknown IDs
- Prefer structured parsing over generic crawling
- Use browser automation only where it is actually needed
- Keep output simple and ingestion-friendly

## Future Improvements

- Add more official Indian legal and regulatory sources
- Improve eGazette entry-point coverage and navigation stability
- Improve metadata normalization for type and year
- Add optional incremental runs
- Add optional PDF download as a separate later stage

## License

Add your preferred license before publishing the repository.
