"""Minimal structured logging with trace correlation."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from satori.config import LogLevel
from satori.observability.trace import current_trace_id, reset_trace_id, set_trace_id


class JsonFormatter(logging.Formatter):
    """Serialize stable log fields as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a record without serializing arbitrary process globals."""

        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        trace_id = current_trace_id()
        if trace_id is not None:
            payload["trace_id"] = trace_id
        structured_fields = getattr(record, "satori_fields", None)
        if isinstance(structured_fields, dict):
            payload["fields"] = structured_fields
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@contextmanager
def bind_trace_id(trace_id: str) -> Iterator[str]:
    """Bind a trace ID for logs emitted in the current sync/async context."""

    token = set_trace_id(trace_id)
    try:
        yield trace_id.strip()
    finally:
        reset_trace_id(token)


def configure_logging(
    level: LogLevel | str = LogLevel.INFO,
    *,
    stream: TextIO | None = None,
    console_level: LogLevel | str | None = None,
    file_path: str | None = None,
) -> None:
    """Configure the process root logger with one structured handler."""

    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.setLevel(str(console_level or level))
    root = logging.getLogger()
    for existing in root.handlers:
        existing.close()
    root.handlers.clear()
    root.addHandler(handler)
    level_mapping = logging.getLevelNamesMapping()
    configured_levels = [level_mapping[str(console_level or level)]]
    if file_path is not None:
        path = Path(file_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        file_handler.setLevel(str(level))
        root.addHandler(file_handler)
        configured_levels.append(level_mapping[str(level)])
    root.setLevel(min(configured_levels))
