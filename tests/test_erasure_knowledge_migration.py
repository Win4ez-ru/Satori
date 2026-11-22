"The explicit knowledge migration preserves old evidence fingerprints and SQL values."

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from satori.infrastructure.persistence.canonical_provenance import canonical_row_digest
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import downgrade_database, upgrade_database
from tests.test_memory_scope_migration import _episode
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import activate

LEGACY = "0019_dialogue_bodies"
CURRENT = "0020_knowledge_bodies"


def test_knowledge_migration_preserves_old_position_witness_shape_and_ddl_rollback(
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
                suffix="knowledge-migration",
                counterparty_id="alice",
                verified=True,
            )
            source = connection.execute(
                "SELECT message_id,interaction_id FROM conversation_messages WHERE role='user'"
            ).fetchone()
            assert source is not None
            now = "2026-10-06 12:00:00.000000"
            connection.execute(
                (
                    "INSERT INTO satori_positions(position_id,position_key,identity_id,schema_ve"
                    "rsion,aggregate_version,policy_version,formation_version,normalization_vers"
                    "ion,proposition,normalized_proposition,kind,stance,confidence,status,create"
                    "d_at,updated_at) VALUES(?,?,?,1,1,1,1,1,?,?,'belief','support',0.6,'active'"
                    ",?,?)"
                ),
                (
                    "legacy-position",
                    "1" * 64,
                    identity,
                    "A synthetic historical body",
                    "a synthetic historical body",
                    now,
                    now,
                ),
            )
            connection.execute(
                (
                    "INSERT INTO satori_position_evidence(evidence_id,position_id,source_message"
                    "_id,source_interaction_id,source_counterparty_id,quote,normalized_signature"
                    ",role,observed_at) VALUES(?,?,?,?,?,?,?,?,?)"
                ),
                (
                    "legacy-evidence",
                    "legacy-position",
                    source[0],
                    source[1],
                    "alice",
                    "synthetic quote",
                    "synthetic quote",
                    "argument",
                    now,
                ),
            )
            connection.commit()
            connection.row_factory = sqlite3.Row
            old_evidence = dict(
                connection.execute("SELECT * FROM satori_position_evidence").fetchone()
            )
            old_digest = canonical_row_digest(old_evidence)
        before = stored_rows(path)

        def fail_ddl(
            _connection: Connection,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _many: bool,
        ) -> None:
            if statement.startswith("CREATE TRIGGER semantic_claims_body_update"):
                raise RuntimeError("injected_knowledge_migration_failure")

        event.listen(Engine, "before_cursor_execute", fail_ddl)
        try:
            with pytest.raises(RuntimeError, match="injected_knowledge_migration_failure"):
                upgrade_database(sqlite_url, config_path=config, revision=CURRENT)
        finally:
            event.remove(Engine, "before_cursor_execute", fail_ddl)
        assert stored_rows(path) == before
        upgrade_database(sqlite_url, config_path=config, revision=CURRENT)
        after = stored_rows(path)
        parents = {
            "semantic_claims",
            "user_model_claims",
            "world_model_claims",
            "satori_positions",
            "satori_inclinations",
        }
        for table, rows in before.items():
            if table == "alembic_version":
                assert after[table] == [(CURRENT,)]
            elif table in parents:
                assert after[table] == [(*row, "available", None) for row in rows]
            else:
                assert after[table] == rows
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            evidence = dict(connection.execute("SELECT * FROM satori_position_evidence").fetchone())
            assert tuple(evidence) == tuple(old_evidence)
            assert canonical_row_digest(evidence) == old_digest
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert tuple(connection.execute("PRAGMA integrity_check").fetchone()) == ("ok",)
            with pytest.raises(sqlite3.IntegrityError, match="knowledge_body_terminal"):
                connection.execute(
                    "UPDATE satori_position_evidence SET quote=NULL,normalized_signature=NULL"
                )
        with pytest.raises(RuntimeError, match="offline_owner_policy"):
            downgrade_database(sqlite_url, config_path=config, revision=LEGACY)
        assert stored_rows(path) == after
    finally:
        database.dispose()


def test_exact_legacy_operator_repair_uses_historical_columns_without_schema_upgrade(
    sqlite_url: str, project_root: Path
) -> None:
    from datetime import UTC, datetime

    from satori.infrastructure.persistence.repositories.positions import (
        SQLAlchemyPositionsRepository,
    )
    from satori.infrastructure.persistence.repositories.semantic import (
        SQLAlchemySemanticMemoryRepository,
    )
    from satori.infrastructure.persistence.source_policy import materialize_mirror
    from tests.test_memory_scope_migration import _claim
    from tests.test_source_policy_memory import _snapshot

    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=LEGACY)
    database = create_database(sqlite_url)
    path = Path(str(database.engine.url.database))
    try:
        identity = activate(database).identity.identity_id
        with closing(sqlite3.connect(path)) as connection:
            _episode(
                connection,
                identity_id=identity,
                suffix="legacy-owner",
                counterparty_id="alice",
                verified=True,
            )
            _claim(connection, "legacy-semantic", "Synthetic", ("legacy-owner",))
            now = "2026-10-06 12:00:00.000000"
            connection.execute(
                (
                    "INSERT INTO satori_positions(position_id,position_key,identity_id,schema_ve"
                    "rsion,aggregate_version,policy_version,formation_version,normalization_vers"
                    "ion,proposition,normalized_proposition,kind,stance,confidence,status,create"
                    "d_at,updated_at) VALUES(?,?,?,1,1,1,1,1,?,?,'belief','support',0.6,'active'"
                    ",?,?)"
                ),
                (
                    "legacy-position",
                    "1" * 64,
                    identity,
                    "Synthetic historical body",
                    "synthetic historical body",
                    now,
                    now,
                ),
            )
            connection.execute(
                (
                    "INSERT INTO satori_position_evidence(evidence_id,position_id,source_message"
                    "_id,source_interaction_id,source_counterparty_id,quote,normalized_signature"
                    ",role,observed_at) VALUES(?,?,?,?,?,?,?,?,?)"
                ),
                (
                    "legacy-evidence",
                    "legacy-position",
                    "message-legacy-owner",
                    "interaction-legacy-owner",
                    "alice",
                    "Private synthetic fact legacy-owner.",
                    "synthetic quote",
                    "argument",
                    now,
                ),
            )
            connection.commit()
        snapshot = _snapshot(identity, frozenset({"message-legacy-owner"}))
        with database.session_factory() as session:
            # Controlled fixture; production sets this flag after exact known-schema admission.
            session.info["source_snapshot"] = snapshot
            session.info["source_operator_knowledge_legacy"] = True
            materialize_mirror(session, snapshot)
            assert (
                SQLAlchemySemanticMemoryRepository(session).withdraw_source_support(
                    excluded_message_ids=snapshot.excluded_message_ids,
                    request_id="legacy-owner-repair",
                    policy_generation=snapshot.generation,
                    now=datetime(2026, 10, 7, tzinfo=UTC),
                )
                == 1
            )
            SQLAlchemyPositionsRepository(session).withdraw_source_support(
                excluded_message_ids=snapshot.excluded_message_ids,
                request_id="legacy-owner-repair",
                policy_generation=snapshot.generation,
                now=datetime(2026, 10, 7, tzinfo=UTC),
            )
            session.commit()
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                LEGACY,
            )
            assert connection.execute(
                "SELECT status,aggregate_version,confidence FROM semantic_claims"
            ).fetchone() == ("retracted", 2, 0.9)
            assert connection.execute(
                "SELECT status,aggregate_version,confidence,proposition FROM satori_positions"
            ).fetchone() == ("retracted", 2, 0.6, "Synthetic historical body")
            assert "body_state" not in {
                row[1] for row in connection.execute("PRAGMA table_info(satori_positions)")
            }
    finally:
        database.dispose()
