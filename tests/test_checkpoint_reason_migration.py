"""Populated 0023 upgrades preserve every numeric/audit byte without retroactive deletion."""

import sqlite3
from pathlib import Path

import pytest

from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.migrations import upgrade_database
from tests.test_checkpoint_reason_owner import create_reason_records


def test_populated_previous_revision_upgrade_preserves_owners_and_audit_bindings(
    migrated_database: Database,
    project_root: Path,
    tmp_path: Path,
) -> None:
    create_reason_records(migrated_database)
    source = Path(migrated_database.engine.url.database or "")
    prior = tmp_path / "populated-0023.db"
    url = f"sqlite+pysqlite:///{prior}"
    upgrade_database(url, config_path=project_root / "alembic.ini", revision="0023_audit_metadata")
    with sqlite3.connect(prior) as connection:
        old_triggers = dict(
            connection.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger'")
        )
    copied: dict[str, list[tuple[object, ...]]] = {}
    columns: dict[str, tuple[str, ...]] = {}
    with sqlite3.connect(source) as original, sqlite3.connect(prior) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name!='alembic_version' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (table,) in tables:
            names = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
            selected = ",".join(f'"{name}"' for name in names)
            rows = original.execute(f'SELECT {selected} FROM "{table}" ORDER BY rowid').fetchall()
            if rows:
                connection.executemany(
                    f'INSERT INTO "{table}" ({selected}) VALUES ({",".join("?" for _ in names)})',
                    rows,
                )
            columns[table] = names
            copied[table] = rows
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    upgrade_database(url, config_path=project_root / "alembic.ini")
    with sqlite3.connect(prior) as connection:
        new_triggers = dict(
            connection.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger'")
        )
        assert all(new_triggers[name] == sql for name, sql in old_triggers.items())
        for table, names in columns.items():
            selected = ",".join(f'"{name}"' for name in names)
            assert (
                connection.execute(f'SELECT {selected} FROM "{table}" ORDER BY rowid').fetchall()
                == copied[table]
            )
        for table in ("personality_checkpoint_approvals", "personality_restore_events"):
            assert connection.execute(
                f"SELECT reason_state,reason_action_id,reason IS NOT NULL FROM {table}"
            ).fetchall() == [("available", None, 1)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT COUNT(*) FROM audit_metadata_bindings").fetchone()[0] > 0
        assert connection.execute("SELECT COUNT(*) FROM erasure_body_applications").fetchone() == (
            0,
        )


def test_terminal_schema_rejects_missing_scope_even_with_null_check_result(
    migrated_database: Database,
) -> None:
    scope, _approval, _restore = create_reason_records(migrated_database)
    path = Path(migrated_database.engine.url.database or "")
    with (
        sqlite3.connect(path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="closed_scope_binding"),
    ):
        connection.execute(
            "INSERT INTO erasure_body_applications "
            "(action_id,request_id,identity_id,root_message_id,floor_id,lineage_id,"
            "body_revision,action,applied_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "invalid-scope",
                "invalid-scope",
                scope.identity_id,
                None,
                "floor",
                scope.lineage_id,
                1,
                "{}",
                "2026-10-13 00:00:00.000000",
            ),
        )
