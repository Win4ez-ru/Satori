"""Read-only file admission rejects unsafe journals before a SQLite connection exists."""

import sqlite3
import sqlite3.dbapi2
import stat
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

import satori.infrastructure.persistence.erasure_database as erasure_database
from satori.domain.source_policy import SourcePolicyError

CONTENT = "synthetic-retained-private-content"


def _create(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE retained_content (content TEXT NOT NULL)")
        connection.execute("INSERT INTO retained_content VALUES (?)", (CONTENT,))


def _files(directory: Path) -> dict[str, bytes]:
    """Private assertions only; contents are never emitted as scenario metadata."""
    return {
        path.name: path.read_bytes()
        for path in directory.iterdir()
        if path.is_file() and not path.is_symlink()
    }


def _forbid_connections(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    attempted: list[bool] = []

    def forbidden(*args: object, **kwargs: object) -> sqlite3.Connection:
        attempted.append(True)
        raise AssertionError("erasure_rejection_attempted_dbapi_connection")

    # Patch the captured creator alias and both public DBAPI module entry points.
    monkeypatch.setattr(erasure_database, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(sqlite3.dbapi2, "connect", forbidden)
    return attempted


def _open_and_read(path: Path) -> None:
    database = erasure_database.open_existing_erasure_database(f"sqlite+pysqlite:///{path}")
    try:
        with database.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1").scalar_one()
    finally:
        database.dispose()


def _assert_rejected(path: Path, reason: str) -> None:
    with pytest.raises(SourcePolicyError, match=reason):
        _open_and_read(path)


def test_missing_database_never_connects_or_creates_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "absent.db"
    before = set(tmp_path.iterdir())
    attempted = _forbid_connections(monkeypatch)
    _assert_rejected(target, "erasure_database_unavailable")
    assert attempted == []
    assert set(tmp_path.iterdir()) == before
    assert not target.exists()


def test_final_database_symlink_is_rejected_without_touching_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "original.db"
    _create(original)
    target = tmp_path / "selected.db"
    target.symlink_to(original)
    before = _files(tmp_path)
    mode = stat.S_IMODE(original.stat().st_mode)
    attempted = _forbid_connections(monkeypatch)
    _assert_rejected(target, "erasure_database_not_regular")
    assert attempted == []
    assert target.is_symlink()
    assert target.readlink() == original
    assert _files(tmp_path) == before
    assert stat.S_IMODE(original.stat().st_mode) == mode


def test_existing_content_is_readable_but_updates_and_file_changes_are_denied(
    tmp_path: Path,
) -> None:
    target = tmp_path / "private readonly π.db"
    _create(target)
    before = _files(tmp_path)
    mode = stat.S_IMODE(target.stat().st_mode)
    database = erasure_database.open_existing_erasure_database(f"sqlite+pysqlite:///{target}")
    try:
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA query_only").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert (
                connection.exec_driver_sql("SELECT content FROM retained_content").scalar_one()
                == CONTENT
            )
            with pytest.raises(OperationalError, match="readonly"):
                connection.exec_driver_sql(
                    "UPDATE retained_content SET content=?", ("synthetic-forbidden-change",)
                )
            connection.rollback()
            assert (
                connection.exec_driver_sql("SELECT content FROM retained_content").scalar_one()
                == CONTENT
            )
    finally:
        database.dispose()
    assert _files(tmp_path) == before
    assert stat.S_IMODE(target.stat().st_mode) == mode


@pytest.mark.parametrize("retained_wal", [False, True])
def test_wal_header_and_missing_shm_are_rejected_before_dbapi_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retained_wal: bool
) -> None:
    original_directory = tmp_path / "source"
    original_directory.mkdir()
    original = original_directory / "source.db"
    writer = sqlite3.connect(original)
    target_directory = tmp_path / "selected"
    target_directory.mkdir()
    target = target_directory / "selected.db"
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("CREATE TABLE retained_content (content TEXT NOT NULL)")
        writer.execute("INSERT INTO retained_content VALUES (?)", (CONTENT,))
        writer.commit()
        if retained_wal:
            target.write_bytes(original.read_bytes())
            Path(f"{target}-wal").write_bytes(Path(f"{original}-wal").read_bytes())
        else:
            writer.close()
            target.write_bytes(original.read_bytes())
        assert target.read_bytes()[18:20] == b"\x02\x02"
        assert not Path(f"{target}-shm").exists()
        before = _files(target_directory)
        attempted = _forbid_connections(monkeypatch)
        _assert_rejected(target, "erasure_database_journal_unsupported")
        assert attempted == []
        assert _files(target_directory) == before
        assert not Path(f"{target}-shm").exists()
    finally:
        writer.close()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("sidecar_kind", ["regular", "dangling_symlink"])
def test_any_existing_sidecar_is_rejected_without_connecting_or_changing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str, sidecar_kind: str
) -> None:
    target = tmp_path / "selected.db"
    _create(target)
    assert target.read_bytes()[18:20] == b"\x01\x01"
    sidecar = Path(f"{target}{suffix}")
    if sidecar_kind == "regular":
        sidecar.write_bytes(b"synthetic-retained-journal-bytes")
    else:
        sidecar.symlink_to(tmp_path / "absent-private-sidecar-target")
    before = _files(tmp_path)
    names = set(tmp_path.iterdir())
    attempted = _forbid_connections(monkeypatch)
    _assert_rejected(target, "erasure_database_journal_unsupported")
    assert attempted == []
    assert _files(tmp_path) == before
    assert set(tmp_path.iterdir()) == names
    if sidecar_kind == "dangling_symlink":
        assert sidecar.is_symlink()
        assert not sidecar.exists()


def test_replaced_inode_is_rejected_at_creator_before_dbapi_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "selected.db"
    _create(target)
    database = erasure_database.open_existing_erasure_database(f"sqlite+pysqlite:///{target}")
    try:
        retained = tmp_path / "retained-original.db"
        target.rename(retained)
        target.write_bytes(retained.read_bytes())
        before = _files(tmp_path)
        attempted = _forbid_connections(monkeypatch)
        with (
            pytest.raises(SourcePolicyError, match="erasure_database_replaced"),
            database.engine.connect(),
        ):
            pytest.fail("replaced source unexpectedly admitted")
        assert attempted == []
        assert _files(tmp_path) == before
    finally:
        database.dispose()
