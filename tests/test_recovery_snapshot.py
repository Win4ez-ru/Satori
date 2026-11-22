"""Stored-state snapshot admission, WAL consistency and mutation-free recovery."""

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from satori.application.initial_self.use_cases import ActivateSatori
from satori.application.recovery.contracts import RecoveryError, SnapshotInventory
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.initial_self_uow import SQLAlchemyInitialSelfUnitOfWork
from satori.infrastructure.persistence.recovery import MAX_SNAPSHOT_BYTES, SQLiteStoredSnapshot
from satori.infrastructure.seeds.loader import JsonSeedLoader
from tests.fakes import FrozenClock, SequenceIdGenerator


@pytest.fixture
def stored_snapshot(sqlite_url: str, project_root: Path) -> SQLiteStoredSnapshot:
    return SQLiteStoredSnapshot(sqlite_url, alembic_config=project_root / "alembic.ini")


def _activate(database: Database) -> None:
    ActivateSatori(
        unit_of_work_factory=lambda: SQLAlchemyInitialSelfUnitOfWork(database.session_factory),
        clock=FrozenClock(datetime(2026, 10, 2, tzinfo=UTC)),
        id_generator=SequenceIdGenerator("identity-recovery", "audit-recovery"),
    ).execute(JsonSeedLoader().load_canonical(), trace_id="trace-recovery")


def _stored_rows(path: Path) -> dict[str, tuple[tuple[object, ...], ...]]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
        ).fetchall()
        return {
            name: tuple(connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall())
            for (name,) in tables
        }


def _append_audit(connection: sqlite3.Connection, event_id: str) -> None:
    connection.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            1,
            "synthetic_recovery_evidence",
            "identity",
            "identity-recovery",
            "2026-10-02T00:00:00.000000Z",
            "synthetic-recovery",
            '{"private_synthetic_value":"not in inventory"}',
        ),
    )


def _add_canonical_lineage(connection: sqlite3.Connection) -> None:
    observed_at = "2026-10-02T00:00:00.000000Z"
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO conversation_sessions "
        "(session_id, identity_id, counterparty_id, schema_version, kind, status, started_at) "
        "VALUES ('session-recovery', 'identity-recovery', 'synthetic-counterparty', 1, "
        "'explicit', 'open', ?)",
        (observed_at,),
    )
    connection.execute(
        "INSERT INTO conversation_interactions "
        "(interaction_id, session_id, client_request_id, trace_id, schema_version, status, "
        "started_at, completed_at, provider, model, finish_status, context_schema_version, "
        "context_manifest_schema_version, policy_id, policy_schema_version, "
        "model_processing_required) VALUES ('interaction-recovery', 'session-recovery', "
        "'request-recovery', 'trace-recovery', 1, 'completed', ?, ?, 'synthetic', 'stored', "
        "'stop', 1, 1, 'historical-test', 1, 1)",
        (observed_at, observed_at),
    )
    for role, sequence, content in (
        ("user", 1, "Synthetic project updated."),
        ("assistant", 2, "Synthetic stored acknowledgement."),
    ):
        connection.execute(
            "INSERT INTO conversation_messages "
            "(message_id,session_id,interaction_id,schema_version,role,content,"
            "created_at,sequence) "
            "VALUES (?, 'session-recovery', "
            "'interaction-recovery', 1, ?, ?, ?, ?)",
            (f"message-{role}", role, content, observed_at, sequence),
        )
    connection.execute(
        "INSERT INTO episodic_memories "
        "(memory_id,schema_version,source_interaction_id,occurred_at,summary,importance,"
        "confidence,created_at,formation_method,formation_version,lifecycle_status) "
        "VALUES ('memory-recovery', 1, 'interaction-recovery', "
        "?, 'Synthetic episode', 0.8, 0.9, ?, 'synthetic', 1, 'active')",
        (observed_at, observed_at),
    )
    connection.execute(
        "INSERT INTO memory_evidence "
        "(evidence_id,memory_id,source_message_id,provenance_kind,quote,observed_at) "
        "VALUES ('evidence-recovery', 'memory-recovery', "
        "'message-user', 'explicit_user_statement', 'Synthetic project updated.', ?)",
        (observed_at,),
    )
    connection.execute(
        "INSERT INTO episodic_memory_embeddings VALUES ('embedding-recovery', "
        "'memory-recovery', 'synthetic', 'stored', 2, 1, '[1.0,0.0]', ?)",
        (observed_at,),
    )


def test_capture_preserves_all_relational_rows_and_schema_after_restart(
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
) -> None:
    _activate(migrated_database)
    with closing(sqlite3.connect(stored_snapshot.source_path)) as connection:
        _append_audit(connection, "audit-additional")
        _add_canonical_lineage(connection)
        connection.commit()
    expected = _stored_rows(stored_snapshot.source_path)
    source_bytes = stored_snapshot.source_path.read_bytes()
    destination = tmp_path / "private" / "snapshot.db"

    captured = stored_snapshot.capture(destination)

    assert captured.identity_id == "identity-recovery"
    assert captured.table_counts["audit_events"] == 2
    assert captured.table_counts["satori_personality_traits"] == 15
    assert captured.table_counts["satori_values"] == 9
    assert captured.table_counts["conversation_messages"] == 2
    assert captured.table_counts["memory_evidence"] == 1
    assert captured.table_counts["episodic_memory_embeddings"] == 1
    assert _stored_rows(destination) == expected
    assert stored_snapshot.source_path.read_bytes() == source_bytes
    reopened = SQLiteStoredSnapshot(
        f"sqlite+pysqlite:///{destination}", alembic_config=Path("alembic.ini")
    )
    assert reopened.inspect(destination) == captured
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
    assert "private_synthetic_value" not in captured.model_dump_json()


def test_capture_includes_committed_wal_and_excludes_uncommitted_changes(
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
) -> None:
    _activate(migrated_database)
    destination = tmp_path / "wal-snapshot.db"
    with closing(sqlite3.connect(stored_snapshot.source_path)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        _append_audit(writer, "wal-committed")
        writer.commit()
        _append_audit(writer, "wal-uncommitted")
        wal = Path(str(stored_snapshot.source_path) + "-wal")
        assert wal.stat().st_size > 0
        source_digest = hashlib.sha256(stored_snapshot.source_path.read_bytes()).hexdigest()

        inventory = stored_snapshot.capture(destination)

        assert inventory.table_counts["audit_events"] == 2
        assert hashlib.sha256(stored_snapshot.source_path.read_bytes()).hexdigest() == source_digest
        with closing(sqlite3.connect(destination)) as restored:
            events = restored.execute(
                "SELECT event_id FROM audit_events ORDER BY event_id"
            ).fetchall()
        assert events == [("audit-recovery",), ("wal-committed",)]
        writer.rollback()


def test_blank_installation_capture_does_not_initialize_any_owner(
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
) -> None:
    before = _stored_rows(stored_snapshot.source_path)
    inventory = stored_snapshot.capture(tmp_path / "unactivated.db")
    assert inventory.identity_id is None
    assert all(
        count == 0 for table, count in inventory.table_counts.items() if table != "alembic_version"
    )
    assert _stored_rows(stored_snapshot.source_path) == before


def test_inspection_does_not_need_or_create_configured_source(
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
    project_root: Path,
) -> None:
    destination = tmp_path / "snapshot.db"
    captured = stored_snapshot.capture(destination)
    missing_source = tmp_path / "missing" / "source.db"
    backend = SQLiteStoredSnapshot(
        f"sqlite+pysqlite:///{missing_source}", alembic_config=project_root / "alembic.ini"
    )
    assert backend.source_path == missing_source
    assert backend.inspect(destination) == captured
    assert not missing_source.parent.exists()
    with pytest.raises(RecoveryError, match=r"^snapshot_unavailable$"):
        backend.capture(tmp_path / "never-created" / "snapshot.db")
    assert not (tmp_path / "never-created").exists()


@pytest.mark.parametrize(
    "database_url",
    [
        "invalid-url",
        "postgresql://localhost/satori",
        "sqlite://",
        "sqlite:///:memory:",
        "sqlite+pysqlite:///file:memory?mode=memory&uri=true",
        "sqlite+pysqlite:///satori.db?mode=ro",
        "sqlite+pysqlite://user:private-secret@localhost/satori.db",
    ],
)
def test_constructor_rejects_nonlocal_or_ambiguous_urls_without_secret_diagnostics(
    database_url: str, project_root: Path
) -> None:
    with pytest.raises(RecoveryError, match=r"^unsupported_database_url$"):
        SQLiteStoredSnapshot(database_url, alembic_config=project_root / "alembic.ini")


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE imported_extra (private_payload TEXT)",
        "CREATE VIEW imported_view AS SELECT unknown_imported_function()",
        "CREATE TRIGGER imported_trigger AFTER INSERT ON audit_events BEGIN SELECT 1; END",
        "CREATE INDEX imported_index ON audit_events(trace_id)",
        "ALTER TABLE audit_events ADD COLUMN imported_column TEXT",
    ],
)
def test_unknown_schema_is_rejected_before_data_queries(
    statement: str,
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "altered.db"
    stored_snapshot.capture(destination)
    with closing(sqlite3.connect(destination)) as connection:
        connection.execute(statement)
        connection.commit()
    with pytest.raises(RecoveryError, match=r"^unsupported_snapshot_schema$"):
        stored_snapshot.inspect(destination)


def test_unknown_revision_and_foreign_key_corruption_are_rejected(
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
) -> None:
    _activate(migrated_database)
    revision_snapshot = tmp_path / "revision.db"
    stored_snapshot.capture(revision_snapshot)
    with closing(sqlite3.connect(revision_snapshot)) as connection:
        connection.execute("UPDATE alembic_version SET version_num='untrusted-version'")
        connection.commit()
    with pytest.raises(RecoveryError, match=r"^unsupported_snapshot_revision$"):
        stored_snapshot.inspect(revision_snapshot)

    orphan_snapshot = tmp_path / "orphan.db"
    stored_snapshot.capture(orphan_snapshot)
    with closing(sqlite3.connect(orphan_snapshot)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO satori_personality_states VALUES (?, ?, ?, ?)",
            ("orphan-private-name", 1, 1, "2026-10-02T00:00:00.000000Z"),
        )
        connection.commit()
    with pytest.raises(RecoveryError, match=r"^snapshot_foreign_keys_failed$"):
        stored_snapshot.inspect(orphan_snapshot)


def test_changed_constraint_definition_cannot_pass_order_normalization(
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "changed-constraint.db"
    stored_snapshot.capture(destination)
    with closing(sqlite3.connect(destination)) as connection:
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_schema SET sql=replace(sql, "
            "'value >= 0.0 AND value <= 1.0', 'value >= -1.0 AND value <= 1.0') "
            "WHERE name='satori_personality_traits'"
        )
        connection.commit()
    with pytest.raises(RecoveryError, match=r"^unsupported_snapshot_schema$"):
        stored_snapshot.inspect(destination)


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM satori_personality_traits WHERE trait_key='curiosity'",
        "DELETE FROM satori_values WHERE value_key='truth'",
        "DELETE FROM satori_value_sets",
        "UPDATE satori_personality_states SET schema_version=2",
        "UPDATE satori_values SET origin='unknown'",
        "UPDATE satori_identities SET activation_time='invalid-private-date'",
        "UPDATE satori_identities SET seed_content_hash='" + "g" * 64 + "'",
    ],
)
def test_missing_or_unsupported_initial_owner_shape_is_rejected(
    statement: str,
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
) -> None:
    _activate(migrated_database)
    destination = tmp_path / "incomplete.db"
    stored_snapshot.capture(destination)
    with closing(sqlite3.connect(destination)) as connection:
        connection.execute(statement)
        connection.commit()
    with pytest.raises(
        RecoveryError, match=r"^(identity_state_invalid|snapshot_foreign_keys_failed)$"
    ):
        stored_snapshot.inspect(destination)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE satori_personality_traits SET value=2.0",
        "UPDATE episodic_memories SET confidence=2.0",
    ],
)
def test_check_constraint_corruption_fails_integrity_validation_without_input_mutation(
    statement: str,
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
) -> None:
    _activate(migrated_database)
    with closing(sqlite3.connect(stored_snapshot.source_path)) as connection:
        _add_canonical_lineage(connection)
        connection.commit()
    destination = tmp_path / "invalid-check.db"
    stored_snapshot.capture(destination)
    with closing(sqlite3.connect(destination)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(statement)
        connection.commit()
    snapshot_before = destination.read_bytes()
    source_before = stored_snapshot.source_path.read_bytes()
    with pytest.raises(RecoveryError, match=r"^snapshot_integrity_failed$"):
        stored_snapshot.inspect(destination)
    assert destination.read_bytes() == snapshot_before
    assert stored_snapshot.source_path.read_bytes() == source_before
    assert not Path(str(destination) + "-journal").exists()


def test_schema_fingerprint_is_independent_of_record_counts(
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
) -> None:
    before = stored_snapshot.capture(tmp_path / "before.db")
    _activate(migrated_database)
    after = stored_snapshot.capture(tmp_path / "after.db")
    assert before.schema_sha256 == after.schema_sha256
    assert before.table_counts != after.table_counts


def test_final_symlinks_nonregular_files_and_existing_destination_are_preserved(
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
    project_root: Path,
) -> None:
    symlink = tmp_path / "source-link.db"
    symlink.symlink_to(stored_snapshot.source_path)
    backend = SQLiteStoredSnapshot(
        f"sqlite+pysqlite:///{symlink}", alembic_config=project_root / "alembic.ini"
    )
    with pytest.raises(RecoveryError, match=r"^snapshot_not_regular$"):
        backend.capture(tmp_path / "not-created.db")
    with pytest.raises(RecoveryError, match=r"^snapshot_not_regular$"):
        stored_snapshot.inspect(symlink)
    with pytest.raises(RecoveryError, match=r"^snapshot_not_regular$"):
        stored_snapshot.inspect(tmp_path)
    before = stored_snapshot.source_path.read_bytes()
    with pytest.raises(RecoveryError, match=r"^snapshot_destination_exists$"):
        stored_snapshot.capture(stored_snapshot.source_path)
    with pytest.raises(RecoveryError, match=r"^snapshot_destination_exists$"):
        stored_snapshot.capture(symlink)
    assert stored_snapshot.source_path.read_bytes() == before


def test_corrupt_empty_and_oversized_snapshot_fail_with_safe_reason_codes(
    stored_snapshot: SQLiteStoredSnapshot, tmp_path: Path
) -> None:
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"private arbitrary payload, not a database")
    with pytest.raises(RecoveryError, match=r"^snapshot_invalid$"):
        stored_snapshot.inspect(corrupt)
    empty = tmp_path / "empty.db"
    empty.touch()
    with pytest.raises(RecoveryError, match=r"^snapshot_empty$"):
        stored_snapshot.inspect(empty)
    oversized = tmp_path / "oversized.db"
    with oversized.open("wb") as output:
        output.truncate(MAX_SNAPSHOT_BYTES + 1)
    with pytest.raises(RecoveryError, match=r"^snapshot_too_large$"):
        stored_snapshot.inspect(oversized)


def test_failure_after_backup_removes_private_staging_without_changing_source(
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate(migrated_database)
    before = stored_snapshot.source_path.read_bytes()
    destination = tmp_path / "failed.db"

    def rejected(_path: Path) -> SnapshotInventory:
        raise RecoveryError("injected_validation_failure")

    monkeypatch.setattr(stored_snapshot, "inspect", rejected)
    with pytest.raises(RecoveryError, match=r"^injected_validation_failure$"):
        stored_snapshot.capture(destination)
    assert not destination.exists()
    assert stored_snapshot.source_path.read_bytes() == before


def test_new_destination_mode_is_private_despite_permissive_umask(
    migrated_database: Database,
    stored_snapshot: SQLiteStoredSnapshot,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "private-a" / "private-b" / "snapshot.db"
    previous = os.umask(0)
    try:
        stored_snapshot.capture(destination)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(destination.parent.parent.stat().st_mode) == 0o700
