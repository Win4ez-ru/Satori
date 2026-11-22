"""Reflection schema upgrades preserve exact old rows and roll back DDL failures."""

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

LEGACY = "0020_knowledge_bodies"
CURRENT = "0021_reflection_bodies"


def test_reflection_migration_preserves_payload_and_retained_history_with_atomic_ddl(
    sqlite_url: str, project_root: Path
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
                suffix="reflection-migration",
                counterparty_id="alice",
                verified=True,
            )
            now = "2026-10-09 12:00:00.000000"
            connection.execute(
                "INSERT INTO reflection_runs(run_id,run_key,identity_id,schema_version,"
                "policy_version,purpose,trigger_kind,source_set_hash,status,aggregate_version,"
                "attempt_count,created_at,updated_at,completed_at) "
                "VALUES('retained-run',?,?,1,1,'general','explicit_local',?,'exhausted',2,1,?,?,?)",
                ("r" * 64, identity, "s" * 64, now, now, now),
            )
            connection.execute(
                "INSERT INTO reflection_sources(source_id,run_id,ordinal,kind,evidence_edge_id,"
                "evidence_edge_version,root_interaction_id,root_message_id,root_counterparty_id,"
                "observed_at,content_hash) VALUES('retained-source','retained-run',0,"
                "'episodic_memory_evidence','evidence-reflection-migration',1,"
                "'interaction-reflection-migration','message-reflection-migration','alice',?,?)",
                (now, "h" * 64),
            )
            connection.execute(
                "INSERT INTO reflection_attempts(attempt_id,run_id,ordinal,status,reason_code,"
                "provider,model,formation_method,started_at,finished_at,metrics) "
                "VALUES('retained-attempt','retained-run',1,'succeeded','proposals_ready',"
                "'fake','fixture','fixture.reflection',?,?,'{\"tokens\":10}')",
                (now, now),
            )
            connection.execute(
                "INSERT INTO reflection_proposals(proposal_id,run_id,ordinal,target_owner,payload,"
                "evidence_source_ids,created_at) VALUES('retained-proposal','retained-run',0,"
                "'values','{\"private_observation\":\"SYNTHETIC_LEGACY_COPY\"}',"
                "'[\"retained-source\"]',?)",
                (now,),
            )
            connection.execute(
                "INSERT INTO reflection_outcomes(outcome_id,proposal_id,target_policy_version,"
                "decision,reason_code,decided_at) VALUES('retained-outcome','retained-proposal',1,"
                "'rejected','owner_disabled',?)",
                (now,),
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
            if statement.startswith("CREATE TRIGGER reflection_proposals_body_update"):
                raise RuntimeError("injected_reflection_migration_failure")

        event.listen(Engine, "before_cursor_execute", fail_ddl)
        try:
            with pytest.raises(RuntimeError, match="injected_reflection_migration_failure"):
                upgrade_database(sqlite_url, config_path=config, revision=CURRENT)
        finally:
            event.remove(Engine, "before_cursor_execute", fail_ddl)
        assert stored_rows(path) == before
        upgrade_database(sqlite_url, config_path=config, revision=CURRENT)
        after = stored_rows(path)
        for table, rows in before.items():
            if table == "alembic_version":
                assert after[table] == [(CURRENT,)]
            elif table == "reflection_proposals":
                assert after[table] == [(*row, "available", None) for row in rows]
            else:
                assert after[table] == rows
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute(
                "SELECT count(*) FROM sqlite_schema WHERE type='trigger' "
                "AND name LIKE 'reflection_%_body_%'"
            ).fetchone() == (15,)
            with pytest.raises(sqlite3.IntegrityError, match="body_consistent"):
                connection.execute("UPDATE reflection_proposals SET payload='null'")
            with pytest.raises(sqlite3.IntegrityError, match="body_consistent"):
                connection.execute("UPDATE reflection_proposals SET payload=NULL")
        with pytest.raises(RuntimeError, match="offline_owner_policy"):
            downgrade_database(sqlite_url, config_path=config, revision=LEGACY)
        assert stored_rows(path) == after
    finally:
        database.dispose()
