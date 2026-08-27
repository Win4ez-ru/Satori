"""Shared pytest fixtures for the deterministic SATORI core."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.migrations import upgrade_database


@pytest.fixture
def project_root() -> Path:
    """Return the repository root containing pyproject.toml."""

    return Path(__file__).resolve().parents[1]


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    """Provide a disposable file-backed SQLite URL."""

    return f"sqlite+pysqlite:///{tmp_path / 'satori-test.db'}"


@pytest.fixture
def migrated_database(sqlite_url: str, project_root: Path) -> Iterator[Database]:
    """Provide a clean database migrated to the current head."""

    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini")
    database = create_database(sqlite_url)
    try:
        yield database
    finally:
        database.dispose()
