"""Ids and structured logging.

Every log line is one JSON object carrying the ids needed to follow a conversation end to
end (conversation_id, message_id, quote_id, attempt...). Log fields never hold raw personal
data: callers pass masked text only.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("autoseguro")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


def log_event(event: str, **fields: object) -> None:
    logger.info(event, extra={"fields": fields})


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(path: Path | None = None, level: int = logging.INFO) -> None:
    """Send autoseguro logs as JSON lines to `path` (or stderr when None)."""
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(JsonLinesFormatter())
    logger.handlers[:] = [handler]
    logger.setLevel(level)
    logger.propagate = False
