"""Minimal structured logging with trace correlation."""

import json
import logging
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from io import TextIOWrapper
from pathlib import Path
from typing import TextIO

from satori.config import LogLevel
from satori.observability.trace import current_trace_id, reset_trace_id, set_trace_id


def _ensure_private_parent(parent: Path) -> None:
    """Create missing parents privately without changing existing directory modes."""

    missing: list[Path] = []
    candidate = parent
    while not candidate.exists():
        missing.append(candidate)
        if candidate == candidate.parent:
            break
        candidate = candidate.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            continue
        os.chmod(directory, 0o700)


class _PrivateFileHandler(logging.FileHandler):
    """Append to one private regular file without following a final symlink."""

    def _open(self) -> TextIOWrapper:
        path = Path(self.baseFilename)
        try:
            current = path.lstat()
        except FileNotFoundError:
            current = None
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise OSError(f"refusing non-regular runtime log path: {path}")

        no_follow = getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | no_follow,
            0o600,
        )
        try:
            opened = os.fstat(descriptor)
            observed = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(opened.st_mode) or (
                opened.st_dev,
                opened.st_ino,
            ) != (observed.st_dev, observed.st_ino):
                raise OSError(f"refusing non-regular runtime log path: {path}")
            os.fchmod(descriptor, 0o600)
            stream = open(  # noqa: SIM115 - handler owns and closes the returned stream
                descriptor,
                mode=self.mode,
                encoding=self.encoding,
                errors=self.errors,
                closefd=True,
            )
            descriptor = -1
            if not isinstance(stream, TextIOWrapper):
                stream.close()
                raise OSError(f"could not open text runtime log: {path}")
            return stream
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise


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
        requested_path = Path(file_path).expanduser()
        if not requested_path.is_absolute():
            requested_path = Path.cwd() / requested_path
        path = requested_path.parent.resolve() / requested_path.name
        _ensure_private_parent(path.parent)
        file_handler = _PrivateFileHandler(path, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        file_handler.setLevel(str(level))
        root.addHandler(file_handler)
        configured_levels.append(level_mapping[str(level)])
    root.setLevel(min(configured_levels))
