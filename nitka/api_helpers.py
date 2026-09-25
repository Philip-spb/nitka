"""Supporting utilities for FastAPI request handlers."""

from __future__ import annotations

import logging
from pathlib import Path
from shutil import copyfileobj
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from nitka.ingestion import SUPPORTED_SUFFIXES


def configure_default_logging() -> None:
    """Configure a basic logger only when the application has none."""
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)


def store_upload(upload: UploadFile, destination_dir: Path) -> Path:
    """Persist one supported upload under a generated name in ``destination_dir``."""
    original_name = Path((upload.filename or "").replace("\\", "/")).name
    suffix = Path(original_name).suffix.casefold()
    if not original_name or suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=422, detail="file must have a .jsonl or .ndjson extension")

    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{uuid4().hex}_{original_name}"
    temporary = destination_dir / f".{uuid4().hex}.upload"
    try:
        with temporary.open("xb") as target:
            copyfileobj(upload.file, target)
        temporary.replace(destination)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="unable to store uploaded file") from error
    return destination
