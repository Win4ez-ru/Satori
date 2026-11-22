"""Sidecar DDL preserves old rows/Appraisal guards and rejects raw replacement."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import downgrade_database, upgrade_database
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import activate

LEGACY = "0022_appraisal_bodies"
CURRENT = "0023_audit_metadata"


def _seed(sqlite_url: str, project_root: Path) -> tuple[Path, str, str]:
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=LEGACY)
    database = create_database(sqlite_url)
    try:
        snapshot = activate(database)
        path = Path(str(database.engine.url.database))
    finally:
        database.dispose()
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute("SELECT event_id FROM audit_events").fetchone()
        assert row is not None
        return path, snapshot.identity.identity_id, str(row[0])


def _bind(connection: sqlite3.Connection, event_id: str, identity_id: str) -> None:
    connection.execute(
        "INSERT INTO audit_metadata_bindings(event_id,identity_id,contract_version,"
        "family,event_digest) VALUES(?,?,1,'activation',?)",
        (event_id, identity_id, "a" * 64),
    )


def test_additive_migration_no_backfill_preserves_old_rows_and_guard_sql(
    sqlite_url: str, project_root: Path
) -> None:
    path, _identity, _event = _seed(sqlite_url, project_root)
    before = stored_rows(path)
    with closing(sqlite3.connect(path)) as connection:
        old_schema = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL "
            "ORDER BY type,name"
        ).fetchall()
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=CURRENT)
    after = stored_rows(path)
    for table, rows in before.items():
        assert after[table] == ([(CURRENT,)] if table == "alembic_version" else rows)
    assert after["audit_metadata_bindings"] == []
    with closing(sqlite3.connect(path)) as connection:
        new_schema = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL "
            "ORDER BY type,name"
        ).fetchall()
        assert set(old_schema) <= set(new_schema)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE type='trigger'"
        ).fetchone() == (88,)
    with pytest.raises(RuntimeError, match="offline_owner_policy"):
        downgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=LEGACY)
    assert stored_rows(path) == after


def test_ddl_failure_rolls_back_entire_sidecar_and_keeps_schema22(
    sqlite_url: str, project_root: Path
) -> None:
    path, _identity, _event = _seed(sqlite_url, project_root)
    before = stored_rows(path)

    def fail(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        if statement.startswith("CREATE TRIGGER audit_metadata_bindings_update"):
            raise RuntimeError("injected_audit_metadata_ddl_failure")

    event.listen(Engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected_audit_metadata_ddl_failure"):
            upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=CURRENT)
    finally:
        event.remove(Engine, "before_cursor_execute", fail)
    assert stored_rows(path) == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_metadata_bindings SET event_digest=? WHERE event_id=?",
        "DELETE FROM audit_metadata_bindings WHERE event_digest=? OR event_id=?",
        "UPDATE audit_events SET trace_id=? WHERE event_id=?",
        "DELETE FROM audit_events WHERE trace_id=? OR event_id=?",
    ],
)
def test_binding_and_bound_event_mutations_are_denied(
    sqlite_url: str, project_root: Path, statement: str
) -> None:
    path, identity, event_id = _seed(sqlite_url, project_root)
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=CURRENT)
    with closing(sqlite3.connect(path)) as connection:
        _bind(connection, event_id, identity)
        connection.commit()
        before = stored_rows(path)
        with pytest.raises(sqlite3.IntegrityError, match="audit_metadata_immutable"):
            connection.execute(statement, ("b" * 64, event_id))
        connection.rollback()
    assert stored_rows(path) == before


def test_replace_cannot_delete_bound_event_or_activation_slot_with_guards_off(
    sqlite_url: str, project_root: Path
) -> None:
    path, identity, event_id = _seed(sqlite_url, project_root)
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=CURRENT)
    with closing(sqlite3.connect(path)) as connection:
        _bind(connection, event_id, identity)
        connection.commit()
        connection.execute("PRAGMA recursive_triggers=OFF")
        connection.execute("PRAGMA foreign_keys=OFF")
        old = connection.execute(
            "SELECT * FROM audit_events WHERE event_id=?", (event_id,)
        ).fetchone()
        assert old is not None
        connection.execute(
            "INSERT INTO audit_events SELECT ?,schema_version,event_type,aggregate_type,"
            "aggregate_id,occurred_at,trace_id,details FROM audit_events WHERE event_id=?",
            ("other-event", event_id),
        )
        connection.commit()
        before = stored_rows(path)
        with pytest.raises(sqlite3.IntegrityError, match="audit_metadata_immutable"):
            connection.execute("INSERT OR REPLACE INTO audit_events VALUES(?,?,?,?,?,?,?,?)", old)
        with pytest.raises(sqlite3.IntegrityError, match="audit_metadata_immutable"):
            connection.execute(
                "INSERT OR REPLACE INTO audit_metadata_bindings(event_id,identity_id,"
                "contract_version,family,event_digest) VALUES(?,?,1,'activation',?)",
                ("other-event", identity, "b" * 64),
            )
        connection.rollback()
    assert stored_rows(path) == before


@pytest.mark.parametrize(
    ("family", "extra"),
    [
        ("appraisal_transition", ""),
        ("future_contract", ""),
        ("episode_formation", ""),
        ("activation", ",episode_decision_id"),
    ],
)
def test_unsupported_family_or_wrong_owner_slot_never_creates_binding(
    sqlite_url: str, project_root: Path, family: str, extra: str
) -> None:
    path, identity, event_id = _seed(sqlite_url, project_root)
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=CURRENT)
    with closing(sqlite3.connect(path)) as connection:
        fields = "event_id,identity_id,contract_version,family,event_digest" + extra
        params: tuple[object, ...] = (event_id, identity, 1, family, "a" * 64)
        if extra:
            params += ("wrong-owner",)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                "INSERT INTO audit_metadata_bindings("
                + fields
                + ") VALUES("
                + ",".join("?" for _ in params)
                + ")",
                params,
            )
        connection.rollback()
        assert connection.execute("SELECT count(*) FROM audit_metadata_bindings").fetchone() == (0,)


def test_appraisal_audit_cannot_be_frozen_by_forged_nonappraisal_binding(
    sqlite_url: str, project_root: Path
) -> None:
    path, identity, _event = _seed(sqlite_url, project_root)
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=CURRENT)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO audit_events(event_id,schema_version,event_type,aggregate_type,"
            "aggregate_id,occurred_at,trace_id,details) VALUES(?,1,?,?,?,?,'trace','{}')",
            (
                "appraisal-event",
                "emotion.transition_applied",
                "affective_state",
                identity,
                "2026-10-12 12:00:00.000000",
            ),
        )
        connection.commit()
        before = stored_rows(path)
        with pytest.raises(sqlite3.IntegrityError, match="audit_metadata_immutable"):
            _bind(connection, "appraisal-event", identity)
        connection.rollback()
    assert stored_rows(path) == before


def test_exact_owner_fk_is_required_and_nonpk_receipt_replace_is_denied(
    sqlite_url: str, project_root: Path
) -> None:
    path, identity, event_id = _seed(sqlite_url, project_root)
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=CURRENT)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            connection.execute(
                "INSERT INTO audit_metadata_bindings(event_id,identity_id,contract_version,"
                "family,event_digest,withdrawal_receipt_id) VALUES(?,?,1,'source_withdrawal',?,?)",
                (event_id, identity, "a" * 64, "missing-receipt"),
            )
        connection.rollback()
        connection.execute(
            "INSERT INTO owner_source_withdrawals(receipt_id,owner,aggregate_id,request_id,"
            "policy_generation,before_status,after_status,before_version,after_version,"
            "root_message_ids,recorded_at) VALUES(?,?,?, ?,1,'active','retracted',1,2,'[]',?)",
            ("receipt", "positions", "aggregate", "request", "2026-10-12 12:00:00.000000"),
        )
        connection.execute(
            "INSERT INTO audit_metadata_bindings(event_id,identity_id,contract_version,"
            "family,event_digest,withdrawal_receipt_id) VALUES(?,?,1,'source_withdrawal',?,?)",
            (event_id, identity, "a" * 64, "receipt"),
        )
        connection.execute(
            "INSERT INTO audit_events SELECT ?,schema_version,event_type,aggregate_type,"
            "aggregate_id,occurred_at,trace_id,details FROM audit_events WHERE event_id=?",
            ("other-event", event_id),
        )
        connection.commit()
        before = stored_rows(path)
        connection.execute("PRAGMA recursive_triggers=OFF")
        connection.execute("PRAGMA foreign_keys=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="audit_metadata_immutable"):
            connection.execute(
                "INSERT OR REPLACE INTO audit_metadata_bindings(event_id,identity_id,"
                "contract_version,family,event_digest,withdrawal_receipt_id) "
                "VALUES(?,?,1,'source_withdrawal',?,?)",
                ("other-event", identity, "b" * 64, "receipt"),
            )
        connection.rollback()
    assert stored_rows(path) == before
