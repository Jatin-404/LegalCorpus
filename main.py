from __future__ import annotations

import argparse
import logging
from pathlib import Path

from config import CollectorSettings, DEFAULT_SOURCE_NAMES
from pipeline.discover import MetadataDiscoveryPipeline, log_pipeline_summary
from sources.egazette import EGazetteCollector
from sources.indiacode import IndiaCodeCollector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect legal corpus metadata from official Indian legal and regulatory sources.",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=DEFAULT_SOURCE_NAMES,
        default=list(DEFAULT_SOURCE_NAMES),
        help="Source collectors to run.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory where JSONL and CSV exports will be written.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run Playwright with a visible browser window.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )


def build_collectors(source_names: tuple[str, ...]) -> list:
    collectors = []
    for source_name in source_names:
        if source_name == "indiacode":
            collectors.append(IndiaCodeCollector())
        elif source_name == "egazette":
            collectors.append(EGazetteCollector())
    return collectors


def main() -> int:
    args = parse_args()
    settings = CollectorSettings.from_env(
        output_dir=args.output_dir,
        sources=tuple(args.sources),
        browser_headless=not args.headed,
        log_level=args.log_level,
    )
    configure_logging(settings.log_level)

    pipeline = MetadataDiscoveryPipeline(settings, build_collectors(settings.sources))
    result = pipeline.run()
    log_pipeline_summary(result)
    logging.getLogger("summary").info("JSONL: %s", settings.jsonl_output)
    logging.getLogger("summary").info("CSV:   %s", settings.csv_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
