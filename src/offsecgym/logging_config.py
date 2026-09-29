"""Structured logging with recursive secret-key redaction."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_SECRET_KEYS = frozenset({"password", "token", "secret", "authorization", "api_key", "cookie"})


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in _SECRET_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("run_id", "worker_id", "action_id", "fields"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(redact(payload), default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
