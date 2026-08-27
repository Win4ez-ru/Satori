"""Database resource construction tests."""

from pathlib import Path

from sqlalchemy import text

from satori.infrastructure.persistence.database import create_database


def test_database_initialization_creates_parent_and_connects(
    tmp_path: Path,
) -> None:
    """A clean file-backed SQLite target is usable without metadata.create_all."""

    database_path = tmp_path / "nested" / "satori.db"
    database = create_database(f"sqlite+pysqlite:///{database_path}")
    try:
        with database.engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    finally:
        database.dispose()

    assert database_path.is_file()
