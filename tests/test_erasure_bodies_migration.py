"""The terminal-body schema adds no fabricated content and never permits lossy rollback."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import downgrade_database, upgrade_database
from tests.test_memory_scope_migration import _episode
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import activate

LEGACY = "0017_erasure_intent"
CURRENT = "0018_erasure_bodies"


def test_body_migration_preserves_all_legacy_rows_and_defaults_available(
    sqlite_url: str,
    project_root: Path,
) -> None:
    config = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config, revision=LEGACY)
    database = create_database(sqlite_url)
    path = Path(str(database.engine.url.database))
    try:
        identity = activate(database).identity.identity_id
        with closing(sqlite3.connect(path)) as connection:
            _episode(
                connection,
                identity_id=identity,
                suffix="body",
                counterparty_id="alice",
                verified=True,
            )
            connection.commit()
        before = stored_rows(path)

        def fail_ddl(
            _connection: Connection,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _many: bool,
        ) -> None:
            if statement.startswith("CREATE TRIGGER evidence_body_terminal"):
                raise RuntimeError("injected_body_migration_failure")

        event.listen(Engine, "before_cursor_execute", fail_ddl)
        try:
            with pytest.raises(RuntimeError, match="injected_body_migration_failure"):
                upgrade_database(sqlite_url, config_path=config, revision=CURRENT)
        finally:
            event.remove(Engine, "before_cursor_execute", fail_ddl)
        assert stored_rows(path) == before
        upgrade_database(sqlite_url, config_path=config, revision=CURRENT)
        after = stored_rows(path)
        for table, rows in before.items():
            if table == "alembic_version":
                assert after[table] == [(CURRENT,)]
            elif table in {"episodic_memories", "memory_evidence"}:
                assert after[table] == [(*row, "available", None) for row in rows]
            else:
                assert after[table] == rows
        assert after["erasure_body_applications"] == []
        assert after["erasure_body_bindings"] == []
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            with pytest.raises(sqlite3.IntegrityError, match="body_consistent"):
                connection.execute("UPDATE episodic_memories SET summary=NULL")
            with pytest.raises(sqlite3.IntegrityError, match="body_consistent"):
                connection.execute("UPDATE memory_evidence SET quote=NULL")
            assert (
                len(
                    connection.execute(
                        "SELECT name FROM sqlite_schema WHERE type='trigger' "
                        "AND (name LIKE '%body%' OR name LIKE 'episode_vector_%')"
                    ).fetchall()
                )
                == 8
            )
        with pytest.raises(RuntimeError, match="offline_owner_policy"):
            downgrade_database(sqlite_url, config_path=config, revision=LEGACY)
        assert stored_rows(path) == after
    finally:
        database.dispose()
