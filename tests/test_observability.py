"""Structured logging and trace-correlation tests."""

import json
import logging
import os
import stat
from io import StringIO
from pathlib import Path

import pytest

from satori.observability.logging import bind_trace_id, configure_logging
from satori.observability.trace import current_trace_id


def test_structured_logging_carries_and_resets_trace_id() -> None:
    """All events in one trace context can be correlated without HTTP."""

    stream = StringIO()
    configure_logging("INFO", stream=stream)
    logger = logging.getLogger("satori.test")

    with bind_trace_id("trace-123"):
        assert current_trace_id() == "trace-123"
        logger.info(
            "foundation_event",
            extra={"satori_fields": {"identity_id": "identity-1", "seed_version": 1}},
        )

    assert current_trace_id() is None
    payload = json.loads(stream.getvalue())
    assert payload["level"] == "INFO"
    assert payload["logger"] == "satori.test"
    assert payload["message"] == "foundation_event"
    assert payload["trace_id"] == "trace-123"
    assert payload["fields"] == {"identity_id": "identity-1", "seed_version": 1}


def test_file_logging_creates_private_parent_and_file(tmp_path: Path) -> None:
    """A newly created runtime-log location is private and remains structured."""

    log_path = tmp_path / "private" / "runtime.jsonl"
    configure_logging("INFO", stream=StringIO(), file_path=str(log_path))

    logging.getLogger("satori.test.file").info("private_event")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert stat.S_IMODE(log_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    assert json.loads(log_path.read_text(encoding="utf-8"))["message"] == "private_event"


def test_file_logging_tightens_file_without_changing_existing_parent(
    tmp_path: Path,
) -> None:
    """Only the exact existing log is chmod'ed, never its existing parent."""

    parent = tmp_path / "existing"
    parent.mkdir(mode=0o755)
    os.chmod(parent, 0o755)
    log_path = parent / "runtime.jsonl"
    log_path.touch(mode=0o644)
    os.chmod(log_path, 0o644)

    configure_logging("INFO", stream=StringIO(), file_path=str(log_path))

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600


def test_file_logging_rejects_final_symlink(tmp_path: Path) -> None:
    """The runtime log cannot be redirected through a symbolic link."""

    target = tmp_path / "target.jsonl"
    target.write_text("unchanged", encoding="utf-8")
    log_path = tmp_path / "runtime.jsonl"
    log_path.symlink_to(target)

    with pytest.raises(OSError, match="non-regular runtime log path"):
        configure_logging("INFO", stream=StringIO(), file_path=str(log_path))

    assert target.read_text(encoding="utf-8") == "unchanged"
