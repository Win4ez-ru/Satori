"""Composition-root bootstrap test."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from satori.bootstrap import bootstrap
from satori.config import Environment, LogLevel, Settings


@contextmanager
def _isolated_root_logging() -> Iterator[None]:
    """Keep bootstrap-owned handlers inside this captured test invocation."""

    root = logging.getLogger()
    original_handlers = root.handlers.copy()
    original_level = root.level
    for handler in original_handlers:
        root.removeHandler(handler)
    try:
        yield
    finally:
        try:
            for handler in root.handlers.copy():
                root.removeHandler(handler)
                if handler not in original_handlers:
                    handler.close()
        finally:
            root.handlers[:] = original_handlers
            root.setLevel(original_level)


def test_bootstrap_migrates_clean_database(
    sqlite_url: str,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bootstrap applies migrations and emits structured lifecycle logs."""

    settings = Settings(
        environment=Environment.TEST,
        database_url=sqlite_url,
        log_level=LogLevel.INFO,
    )

    with _isolated_root_logging():
        bootstrap(settings, alembic_config=project_root / "alembic.ini")

    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()
    assert revision == "0024_checkpoint_reasons"

    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM satori_identities")).scalar_one() == 0
            )
    finally:
        engine.dispose()

    captured = capsys.readouterr()
    log_lines = [
        payload
        for line in captured.err.splitlines()
        if line.startswith("{") and (payload := json.loads(line))["logger"] == "satori.bootstrap"
    ]
    assert [line["message"] for line in log_lines] == [
        "foundation_bootstrap_started",
        "foundation_bootstrap_completed",
    ]
