"""Archival migration preserves sources and refuses lossy rollback or recovery."""

import sqlite3
import zipfile
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from satori.application.recovery.contracts import RecoveryError
from satori.application.recovery.use_cases import WholeStateRecovery
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.migrations import downgrade_database, upgrade_database
from satori.infrastructure.persistence.recovery import SQLiteStoredSnapshot
from tests.test_memory_scope_migration import OBSERVED_AT, _claim, _episode, _history
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import activate

LEGACY_REVISION = "0014_memory_scope"
CURRENT_REVISION = "0015_memory_lifecycle"


@pytest.fixture
def migrated_database(sqlite_url: str, project_root: Path) -> Iterator[Database]:
    """Pin the archival schema so its downgrade transaction is tested independently.

    Later migrations have their own downgrade boundaries. A rollback of 0015 must
    preserve precisely the pre-state at 0015, including every source row and receipt.
    """

    upgrade_database(
        sqlite_url, config_path=project_root / "alembic.ini", revision=CURRENT_REVISION
    )
    database = create_database(sqlite_url)
    try:
        yield database
    finally:
        database.dispose()


def _decision(connection: sqlite3.Connection, identity_id: str, *, noop: bool = False) -> None:
    connection.execute(
        "INSERT INTO memory_lifecycle_decisions "
        "(decision_id,request_id,memory_id,identity_id,counterparty_id,from_status,to_status,"
        "expected_version,result_version,policy_version,reason_code,decided_at,trace_id,"
        "approved_by,preview_digest) VALUES "
        "('decision','request','memory-a',?,'alice','active',?,1,?,1,?,?,'trace',"
        "'local_operator',?)",
        (
            identity_id,
            "active" if noop else "archived",
            1 if noop else 2,
            "episode_lifecycle_noop" if noop else "episode_archived",
            OBSERVED_AT,
            "a" * 64,
        ),
    )


def _seed(database: Database) -> str:
    identity_id = activate(database).identity.identity_id
    with closing(sqlite3.connect(str(database.engine.url.database))) as connection:
        _episode(
            connection, identity_id=identity_id, suffix="a", counterparty_id="alice", verified=True
        )
        _claim(connection, "claim-a", "Retained fact", ("a",))
        _history(connection, "claim-a", "a")
        connection.commit()
    return identity_id


def test_migration_preserves_every_existing_record_and_defaults_episode_version(
    sqlite_url: str, project_root: Path
) -> None:
    config = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config, revision=LEGACY_REVISION)
    database = create_database(sqlite_url)
    try:
        _seed(database)
        path = Path(str(database.engine.url.database))
        before = stored_rows(path)
        upgrade_database(sqlite_url, config_path=config, revision=CURRENT_REVISION)
        after = stored_rows(path)
        for table, rows in before.items():
            if table == "alembic_version":
                assert after[table] == [(CURRENT_REVISION,)]
            elif table == "episodic_memories":
                assert after[table] == [(*row, 1) for row in rows]
            else:
                assert after[table] == rows
        assert after["memory_lifecycle_decisions"] == []
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            with pytest.raises(sqlite3.IntegrityError, match="aggregate_version_positive"):
                connection.execute("UPDATE episodic_memories SET aggregate_version=0")
            with pytest.raises(sqlite3.IntegrityError, match="lifecycle_status_valid"):
                connection.execute("UPDATE episodic_memories SET lifecycle_status='erased'")
        downgrade_database(sqlite_url, config_path=config, revision=LEGACY_REVISION)
        assert stored_rows(path) == before
        backend = SQLiteStoredSnapshot(sqlite_url, alembic_config=config)
        assert backend.inspect(path).alembic_revision == LEGACY_REVISION
        upgrade_database(sqlite_url, config_path=config, revision=CURRENT_REVISION)
        assert stored_rows(path) == after
        assert backend.inspect(path).alembic_revision == CURRENT_REVISION
    finally:
        database.dispose()


@pytest.mark.parametrize("state", ["archived", "receipt", "version"])
def test_downgrade_refuses_to_discard_archive_or_even_noop_receipt(
    migrated_database: Database, project_root: Path, state: str
) -> None:
    identity_id = _seed(migrated_database)
    path = Path(str(migrated_database.engine.url.database))
    with closing(sqlite3.connect(path)) as connection:
        if state == "archived":
            connection.execute(
                "UPDATE episodic_memories SET lifecycle_status='archived',aggregate_version=2"
            )
        elif state == "receipt":
            _decision(connection, identity_id, noop=True)
        else:
            connection.execute("UPDATE episodic_memories SET aggregate_version=2")
        connection.commit()
    before = stored_rows(path)
    with pytest.raises(RuntimeError, match="memory lifecycle downgrade would discard"):
        downgrade_database(
            str(migrated_database.engine.url),
            config_path=project_root / "alembic.ini",
            revision=LEGACY_REVISION,
        )
    assert stored_rows(path) == before


@pytest.mark.parametrize(
    "failure_point",
    [
        "CREATE TABLE episodic_memories",
        "INSERT INTO episodic_memories",
        "DROP TABLE temp._satori_lifecycle_legacy_rows",
    ],
)
def test_downgrade_write_failure_rolls_back_schema_and_all_source_rows(
    migrated_database: Database, project_root: Path, failure_point: str
) -> None:
    _seed(migrated_database)
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)

    def fail_restore(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if statement.strip().startswith(failure_point):
            raise RuntimeError("injected_legacy_restore_failure")

    event.listen(Engine, "before_cursor_execute", fail_restore)
    try:
        with pytest.raises(RuntimeError, match="injected_legacy_restore_failure"):
            downgrade_database(
                str(migrated_database.engine.url),
                config_path=project_root / "alembic.ini",
                revision=LEGACY_REVISION,
            )
    finally:
        event.remove(Engine, "before_cursor_execute", fail_restore)
    assert stored_rows(path) == before
    backend = SQLiteStoredSnapshot(
        str(migrated_database.engine.url), alembic_config=project_root / "alembic.ini"
    )
    assert backend.inspect(path).alembic_revision == CURRENT_REVISION


@pytest.mark.parametrize(
    "corruption",
    [
        "UPDATE memory_lifecycle_decisions SET from_status='erased'",
        "UPDATE memory_lifecycle_decisions SET result_version=1",
        "UPDATE memory_lifecycle_decisions SET expected_version=0",
        "UPDATE memory_lifecycle_decisions SET approved_by='provider'",
        "UPDATE memory_lifecycle_decisions SET reason_code='episode_reactivated'",
        "UPDATE memory_lifecycle_decisions SET preview_digest='bad-digest'",
    ],
)
def test_lifecycle_receipt_sql_constraints_reject_corrupt_approvals(
    migrated_database: Database, corruption: str
) -> None:
    identity_id = _seed(migrated_database)
    with closing(sqlite3.connect(str(migrated_database.engine.url.database))) as connection:
        _decision(connection, identity_id)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(corruption)
        with pytest.raises(sqlite3.IntegrityError, match="request_id"):
            connection.execute(
                "INSERT INTO memory_lifecycle_decisions SELECT 'duplicate',request_id,memory_id,"
                "identity_id,counterparty_id,from_status,to_status,expected_version,result_version,"
                "policy_version,reason_code,decided_at,trace_id,approved_by,preview_digest "
                "FROM memory_lifecycle_decisions"
            )


def test_recovery_preserves_archived_bytes_version_and_receipt_without_owner_execution(
    migrated_database: Database, project_root: Path, tmp_path: Path
) -> None:
    identity_id = _seed(migrated_database)
    path = Path(str(migrated_database.engine.url.database))
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE episodic_memories SET lifecycle_status='archived',aggregate_version=2"
        )
        _decision(connection, identity_id)
        connection.commit()
    before = stored_rows(path)
    source_bytes = path.read_bytes()
    recovery = WholeStateRecovery(
        SQLiteStoredSnapshot(
            str(migrated_database.engine.url), alembic_config=project_root / "alembic.ini"
        ),
        PrivateArtifactStore(),
    )
    archive = tmp_path / "archived.state"
    exported = recovery.export(archive)
    assert exported.manifest.alembic_revision == CURRENT_REVISION
    assert exported.manifest.table_counts["memory_lifecycle_decisions"] == 1
    assert recovery.preview(archive) == exported
    target = tmp_path / "restored.db"
    recovery.restore(archive, target, expected_sha256=exported.archive_sha256)
    with zipfile.ZipFile(archive) as bundle:
        assert target.read_bytes() == bundle.read("snapshot.sqlite")
    assert stored_rows(target) == before
    assert path.read_bytes() == source_bytes
    assert stored_rows(path) == before


def test_recovery_rejects_lifecycle_constraint_bypass_without_modifying_input(
    migrated_database: Database, project_root: Path
) -> None:
    identity_id = _seed(migrated_database)
    path = Path(str(migrated_database.engine.url.database))
    with closing(sqlite3.connect(path)) as connection:
        _decision(connection, identity_id)
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE memory_lifecycle_decisions SET result_version=99")
        connection.commit()
    before = path.read_bytes()
    backend = SQLiteStoredSnapshot(
        str(migrated_database.engine.url), alembic_config=project_root / "alembic.ini"
    )
    with pytest.raises(RecoveryError, match="snapshot_integrity_failed"):
        backend.inspect(path)
    assert path.read_bytes() == before
