"""Command-line entrypoint for the shared ingestion service."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from nitka.config import Settings
from nitka.db import create_db_engine
from nitka.ingestion import IngestionBusyError, ingest

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="nitka")
    subcommands = parser.add_subparsers(dest="command", required=True)
    ingest_command = subcommands.add_parser("ingest", help="Import one JSONL or NDJSON file")
    ingest_command.add_argument("--input", type=Path, required=True, help="Path to the file to import")
    arguments = parser.parse_args(argv)
    settings = Settings()
    logger.info("Starting ingestion for %s", arguments.input)
    try:
        summary = ingest(create_db_engine(settings), arguments.input)
    except IngestionBusyError:
        logger.error("Ingestion is already running")
        return 1
    except (RuntimeError, ValueError) as error:
        logger.error("Ingestion failed: %s", error)
        return 1
    logger.info("Ingestion completed: %s", json.dumps(summary.model_dump(mode="json"), ensure_ascii=False))
    return 0
