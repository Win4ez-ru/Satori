"""Composition-root bootstrap test."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from satori.bootstrap import bootstrap
from satori.config import Environment, LogLevel, Settings


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

    bootstrap(settings, alembic_config=project_root / "alembic.ini")

    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()
    assert revision == "0012_personality_evolution"

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
