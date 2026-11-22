"""Appraisal schema keeps legacy rows UNKNOWN and rolls back interrupted DDL."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from satori.domain.affect import AffectiveTransition, EmotionManager, initial_affective_state
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import downgrade_database, upgrade_database
from satori.infrastructure.persistence.repositories.affect import SQLAlchemyAffectiveStateRepository
from tests.test_memory_scope_migration import _episode
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate
from tests.test_stage7_affect_integration import proposal_for

LEGACY = "0021_reflection_bodies"
CURRENT = "0022_appraisal_bodies"


def test_migration_retains_legacy_reason_numbers_and_incoming_fk_without_guessing(
    sqlite_url: str,
    project_root: Path,
) -> None:
    config = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config, revision=LEGACY)
    database = create_database(sqlite_url)
    path = Path(str(database.engine.url.database))
    try:
        snapshot = activate(database)
        response = proposal_for("interaction-appraisal-migration")
        draft = (
            EmotionManager()
            .evaluate(
                response.proposal,
                initial_affective_state(
                    snapshot.identity.identity_id, initialized_at=INTERACTION_TIME
                ),
                snapshot.personality,
                interaction_id="interaction-appraisal-migration",
                allowed_source_refs=("interaction-appraisal-migration",),
                event_time=INTERACTION_TIME,
            )
            .transition
        )
        assert draft is not None
        transition = AffectiveTransition(
            "migration-transition",
            snapshot.identity.identity_id,
            "interaction-appraisal-migration",
            "message-appraisal-migration",
            "migration-trace",
            draft.proposal,
            draft.before,
            draft.after,
            draft.applied_delta,
            draft.mood_delta,
            response.provider,
            response.model,
            response.appraisal_method,
            INTERACTION_TIME,
        )
        values = SQLAlchemyAffectiveStateRepository._transition_row(transition).__dict__
        with closing(sqlite3.connect(path)) as connection:
            _episode(
                connection,
                identity_id=snapshot.identity.identity_id,
                suffix="appraisal-migration",
                counterparty_id="alice",
                verified=True,
            )
            columns = [
                row[1] for row in connection.execute("PRAGMA table_info(affective_transitions)")
            ]
            parameters = tuple(
                json.dumps(values[name]) if isinstance(values[name], dict | list) else values[name]
                for name in columns
            )
            connection.execute(
                "INSERT INTO affective_transitions("
                + ",".join(columns)
                + ") VALUES("
                + ",".join("?" for _ in columns)
                + ")",
                parameters,
            )
            now = "2026-10-10 12:00:00.000000"
            connection.execute(
                "INSERT INTO "
                "reflection_runs(run_id,run_key,identity_id,schema_version,policy_versi"
                "on,purpose,trigger_kind,source_set_hash,status,aggregate_version,attem"
                "pt_count,created_at,updated_at,completed_at) VALUES('retained-run',?,?"
                ",2,2,'general','explicit_local',?,'exhausted',2,0,?,?,?)",
                ("r" * 64, snapshot.identity.identity_id, "s" * 64, now, now, now),
            )
            connection.execute(
                "INSERT INTO "
                "reflection_sources(source_id,run_id,ordinal,kind,evidence_edge_id,evid"
                "ence_edge_version,root_interaction_id,root_message_id,root_counterpart"
                "y_id,observed_at,content_hash,affective_transition_id,affective_state_"
                "version,affective_signal_hash) VALUES('retained-source','retained-run'"
                ",0,'episodic_memory_evidence','evidence-appraisal-migration',1,'intera"
                "ction-appraisal-migration','message-appraisal-migration','alice',?,?,'"
                "migration-transition',2,?)",
                (now, "h" * 64, "a" * 64),
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
            if statement.startswith("CREATE TRIGGER affective_transitions_explanation_update"):
                raise RuntimeError("injected_appraisal_migration_failure")

        event.listen(Engine, "before_cursor_execute", fail_ddl)
        try:
            with pytest.raises(RuntimeError, match="injected_appraisal_migration_failure"):
                upgrade_database(sqlite_url, config_path=config, revision=CURRENT)
        finally:
            event.remove(Engine, "before_cursor_execute", fail_ddl)
        assert stored_rows(path) == before
        upgrade_database(sqlite_url, config_path=config, revision=CURRENT)
        after = stored_rows(path)
        for table, rows in before.items():
            if table == "alembic_version":
                assert after[table] == [(CURRENT,)]
            elif table == "affective_transitions":
                assert after[table] == [(*row, "available", None, None, None) for row in rows]
            else:
                assert after[table] == rows
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute(
                "SELECT count(*) FROM sqlite_schema WHERE type='trigger'"
            ).fetchone() == (82,)
            assert connection.execute(
                "SELECT source_input_id,audit_event_id FROM affective_transitions"
            ).fetchone() == (None, None)
        with pytest.raises(RuntimeError, match="offline_owner_policy"):
            downgrade_database(sqlite_url, config_path=config, revision=LEGACY)
        assert stored_rows(path) == after
    finally:
        database.dispose()
