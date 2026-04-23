"""Structured JSON logging configuration for ProjectHermes."""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from typing import Any

# Pre-compute the set of standard LogRecord attributes to exclude from extras.
# We build this from a throwaway record so it stays in sync with the stdlib.
_STANDARD_RECORD_ATTRS: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    | {"message", "msg", "args", "asctime"}
)


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        record.getMessage()  # resolves % interpolation into record.message
        record.message = record.getMessage()

        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
        }

        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS:
                continue
            safe_key = f"ctx_{key}" if key in entry else key
            entry[safe_key] = value

        return json.dumps(entry, default=str)


def setup_logging(level: int = logging.INFO, json_format: bool = False) -> None:
    """Configure the root logger with either JSON or plain-text formatting.

    Safe to call multiple times — replaces existing StreamHandlers rather than
    stacking duplicates.
    """
    formatter: logging.Formatter = (
        JsonFormatter() if json_format else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Replace existing StreamHandlers (avoid duplicates on repeated calls).
    # FileHandlers are intentionally left untouched.
    existing_stream_handlers = [
        h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    for h in existing_stream_handlers:
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)
