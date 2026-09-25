"""Structured events for CLI ingestion runs."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def emit(event: str, **fields: Any) -> None:
    payload = {"timestamp": datetime.now(UTC).isoformat(), "event": event, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False, default=str))
