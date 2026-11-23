"""Recovery uses identical connection protections on both CPython build variants."""

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest

from satori.application.recovery.contracts import RecoveryError
from satori.infrastructure.persistence.recovery import _open_query_only
from satori.infrastructure.source_policy.binding_recovery import _read_database


def snapshot_read(path: Path) -> AbstractContextManager[sqlite3.Connection]:
    return _open_query_only(path, immutable=False)


def immutable_read(path: Path) -> AbstractContextManager[sqlite3.Connection]:
    return _open_query_only(path, immutable=True)


def private_replica(path: Path) -> AbstractContextManager[sqlite3.Connection]:
    return _open_query_only(path, immutable=False, read_only=False)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "harmless.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE test_rows (value TEXT)")
        connection.execute("INSERT INTO test_rows VALUES ('known')")
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.mark.parametrize(
    ("opener", "uri_suffix"),
    [
        (_read_database, "?mode=ro"),
        (snapshot_read, "?mode=ro"),
        (immutable_read, "?mode=ro&immutable=1"),
        (private_replica, "?mode=rw"),
    ],
)
@pytest.mark.parametrize("mode", ["supported", "absent", "disable-error"])
def test_recovery_reader_handles_optional_extension_api_without_weakening_controls(
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    opener: Callable[[Path], AbstractContextManager[sqlite3.Connection]],
    uri_suffix: str,
    mode: str,
) -> None:
    native_connect = sqlite3.connect
    disable_calls: list[bool] = []
    connections_closed: list[bool] = []
    paths: list[str] = []

    class Connection(sqlite3.Connection):
        def __getattribute__(self, name: str) -> Any:
            if mode == "absent" and name in {"enable_load_extension", "load_extension"}:
                raise AttributeError(name)
            return super().__getattribute__(name)

        def enable_load_extension(self, enabled: bool, /) -> None:
            disable_calls.append(enabled)
            if mode == "disable-error":
                raise sqlite3.OperationalError("test: unavailable")
            native_disable = getattr(super(), "enable_load_extension", None)
            if native_disable is not None:
                native_disable(enabled)

        def close(self) -> None:
            super().close()
            connections_closed.append(True)

    def connect(database: str, **kwargs: Any) -> sqlite3.Connection:
        assert kwargs["uri"] is True
        paths.append(database)
        return native_connect(database, factory=Connection, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    original = db_path.read_bytes()
    if mode == "disable-error":
        expected_error = sqlite3.OperationalError if opener is _read_database else RecoveryError
        with pytest.raises(expected_error), opener(db_path):
            pytest.fail("unsafe reader was opened after explicit disable failed")
    else:
        with opener(db_path) as connection:
            assert connection.execute("PRAGMA query_only").fetchone() == (1,)
            assert connection.execute("PRAGMA trusted_schema").fetchone() == (0,)
            assert connection.in_transaction
            assert connection.execute("SELECT value FROM test_rows").fetchall() == [("known",)]
    assert paths == [db_path.as_uri() + uri_suffix]
    assert disable_calls == ([] if mode == "absent" else [False])
    assert connections_closed == [True]
    assert db_path.read_bytes() == original
