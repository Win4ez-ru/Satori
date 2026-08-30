"""Database resource construction tests."""

import os
import stat
from pathlib import Path

import pytest
from sqlalchemy import text

from satori.infrastructure.persistence.database import create_database, ensure_sqlite_parent


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
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(database_path.parent.stat().st_mode) == 0o700


def test_database_initialization_tightens_file_without_changing_existing_parent(
    tmp_path: Path,
) -> None:
    """Existing shared directory modes are preserved while the exact DB is private."""

    parent = tmp_path / "existing"
    parent.mkdir(mode=0o755)
    os.chmod(parent, 0o755)
    database_path = parent / "satori.db"
    database_path.touch(mode=0o644)
    os.chmod(database_path, 0o644)

    database = create_database(f"sqlite+pysqlite:///{database_path}")
    database.dispose()

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600


def test_database_initialization_rejects_final_symlink(tmp_path: Path) -> None:
    """A database path cannot redirect persistence through a symbolic link."""

    target = tmp_path / "target.db"
    target.write_text("unchanged", encoding="utf-8")
    database_path = tmp_path / "satori.db"
    database_path.symlink_to(target)

    with pytest.raises(OSError, match="non-regular SQLite database path"):
        create_database(f"sqlite+pysqlite:///{database_path}")

    assert target.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+pysqlite:///:memory:",
        "sqlite+pysqlite:///file::memory:?cache=shared&uri=true",
        "sqlite+pysqlite:///file:satori-memory?mode=memory&cache=shared&uri=true",
    ],
)
def test_database_initialization_leaves_memory_targets_unchanged(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-memory SQLite URLs have no local artifact preparation."""

    monkeypatch.chdir(tmp_path)
    database = create_database(database_url)
    database.dispose()

    assert tuple(tmp_path.iterdir()) == ()


def test_database_parent_preparation_ignores_non_sqlite_url() -> None:
    """Non-SQLite persistence targets retain their existing behavior."""

    ensure_sqlite_parent("postgresql+psycopg://user:password@localhost/satori")
