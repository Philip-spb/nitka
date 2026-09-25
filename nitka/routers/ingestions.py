"""Routes for importing document datasets."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from nitka.api_dependencies import AppSettings, DatabaseEngine
from nitka.api_helpers import store_upload
from nitka.ingestion import IngestionBusyError, IngestionResult, ingest

router = APIRouter(tags=["ingestions"])


@router.post("/ingestions", response_model=IngestionResult)
def create_ingestion(
    settings: AppSettings,
    engine: DatabaseEngine,
    file: Annotated[UploadFile, File(description="JSONL or NDJSON file")],
) -> IngestionResult:
    uploaded_file = store_upload(file, settings.input_dir)
    try:
        return ingest(engine, uploaded_file)
    except IngestionBusyError as error:
        raise HTTPException(status_code=409, detail="an ingestion is already running") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail="uploaded dataset is unavailable") from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail="ingestion failed") from error
