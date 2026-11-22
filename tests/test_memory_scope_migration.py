"""Scope migration preserves owners and quarantines unprovable historical roots."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import select

from satori.domain.memory_scope import MemoryScope, scope_semantic_key
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.memory_scope import (
    eligible_episode_predicate,
    eligible_interaction_predicate,
    eligible_semantic_claim_predicate,
    resolve_memory_scope,
)
from satori.infrastructure.persistence.migrations import downgrade_database, upgrade_database
from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow
from satori.infrastructure.persistence.models.semantic import SemanticClaimRow
from tests.test_stage4_conversation_memory import activate

LEGACY_REVISION = "0013_conversation_failure_reason"
CURRENT_REVISION = "0014_memory_scope"
OBSERVED_AT = "2026-10-03 09:00:00.000000"


@pytest.fixture
def legacy_memory_store(
    sqlite_url: str, project_root: Path
) -> Iterator[tuple[Database, Path, str]]:
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=LEGACY_REVISION)
    database = create_database(sqlite_url)
    identity_id = activate(database).identity.identity_id
    try:
        yield database, Path(str(database.engine.url.database)), identity_id
    finally:
        database.dispose()


def _episode(
    connection: sqlite3.Connection,
    *,
    identity_id: str,
    suffix: str,
    counterparty_id: str,
    verified: bool | None = None,
) -> None:
    columns = "session_id, identity_id, counterparty_id, schema_version, kind, status, started_at"
    values: tuple[object, ...] = (
        f"session-{suffix}",
        identity_id,
        counterparty_id,
        1,
        "explicit",
        "open",
        OBSERVED_AT,
    )
    if verified is not None:
        columns += ", scope_verified"
        values += (verified,)
    connection.execute(
        f"INSERT INTO conversation_sessions ({columns}) VALUES ({','.join('?' for _ in values)})",
        values,
    )
    connection.execute(
        "INSERT INTO conversation_interactions "
        "(interaction_id, session_id, client_request_id, trace_id, schema_version, status, "
        "started_at, completed_at, provider, model, finish_status, context_schema_version, "
        "context_manifest_schema_version, policy_id, policy_schema_version) "
        "VALUES (?, ?, ?, 'synthetic', 1, 'completed', ?, ?, 'synthetic', 'fixture', "
        "'stop', 1, 1, 'fixture', 1)",
        (
            f"interaction-{suffix}",
            f"session-{suffix}",
            f"request-{suffix}",
            OBSERVED_AT,
            OBSERVED_AT,
        ),
    )
    quote = f"Private synthetic fact {suffix}."
    connection.execute(
        "INSERT INTO conversation_messages "
        "(message_id,session_id,interaction_id,schema_version,role,content,created_at,sequence) "
        "VALUES (?, ?, ?, 1, 'user', ?, ?, 1)",
        (f"message-{suffix}", f"session-{suffix}", f"interaction-{suffix}", quote, OBSERVED_AT),
    )
    connection.execute(
        "INSERT INTO episodic_memories "
        "(memory_id,schema_version,source_interaction_id,occurred_at,summary,importance,"
        "confidence,created_at,formation_method,formation_version,lifecycle_status) VALUES "
        "(?, 1, ?, ?, ?, 0.8, 0.9, ?, 'synthetic', 1, 'active')",
        (f"memory-{suffix}", f"interaction-{suffix}", OBSERVED_AT, quote, OBSERVED_AT),
    )
    connection.execute(
        "INSERT INTO memory_evidence "
        "(evidence_id, memory_id, source_message_id, provenance_kind, quote, observed_at) "
        "VALUES (?, ?, ?, 'explicit_user_statement', ?, ?)",
        (f"evidence-{suffix}", f"memory-{suffix}", f"message-{suffix}", quote, OBSERVED_AT),
    )


def _base_key(value: str) -> str:
    material = {
        "claim_kind": "explicit_fact",
        "normalization_version": 1,
        "polarity": True,
        "predicate": "name",
        "subject": "user",
        "value": value.casefold(),
        "value_kind": "text",
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _claim(
    connection: sqlite3.Connection, claim_id: str, value: str, roots: tuple[str, ...]
) -> str:
    base_key = _base_key(value)
    connection.execute(
        "INSERT INTO semantic_claims "
        "(claim_id, claim_key, schema_version, aggregate_version, subject, predicate, value_kind, "
        "value, normalized_value, polarity, claim_kind, confidence, status, valid_from, "
        "created_at, updated_at, formation_method, formation_version, normalization_version) "
        "VALUES (?, ?, 1, 1, 'user', 'name', 'text', ?, ?, 1, 'explicit_fact', 0.9, 'active', "
        "?, ?, ?, 'synthetic', 1, 1)",
        (
            claim_id,
            base_key,
            json.dumps(value),
            value.casefold(),
            OBSERVED_AT,
            OBSERVED_AT,
            OBSERVED_AT,
        ),
    )
    for suffix in roots:
        connection.execute(
            "INSERT INTO semantic_claim_evidence VALUES (?, ?, ?, ?, ?, ?, "
            "'explicit_user_statement', ?)",
            (
                f"edge-{claim_id}-{suffix}",
                claim_id,
                f"memory-{suffix}",
                f"evidence-{suffix}",
                f"message-{suffix}",
                f"interaction-{suffix}",
                OBSERVED_AT,
            ),
        )
    return base_key


def _history(connection: sqlite3.Connection, claim_id: str, suffix: str) -> None:
    connection.execute(
        "INSERT INTO semantic_formation_decisions VALUES ('decision-history', 'key-history', "
        "?, 1, 1, 'applied', 'created', 1, 0, 0, 0, 0, ?, ?, "
        "'synthetic', 'synthetic', 'fake', 'fake')",
        (f"memory-{suffix}", json.dumps([claim_id]), OBSERVED_AT),
    )
    connection.execute(
        "INSERT INTO semantic_claim_revisions VALUES "
        "('revision-history', ?, 1, 'decision-history', "
        "'created', NULL, 'active', NULL, 0.9, 'created', ?)",
        (claim_id, OBSERVED_AT),
    )


def _rows(connection: sqlite3.Connection, table: str) -> list[tuple[object, ...]]:
    return list(connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall())


def test_legacy_migration_proves_roots_without_rewriting_owners(
    legacy_memory_store: tuple[Database, Path, str], project_root: Path
) -> None:
    database, path, identity_id = legacy_memory_store
    with closing(sqlite3.connect(path)) as connection:
        for suffix, person in (
            ("a", "person-a"),
            ("b", "person-b"),
            ("anon", "local-default"),
            ("bad", "person-a"),
        ):
            _episode(connection, identity_id=identity_id, suffix=suffix, counterparty_id=person)
        connection.execute(
            "UPDATE memory_evidence SET quote='invented quote' WHERE evidence_id='evidence-bad'"
        )
        keys = {
            "claim-a": _claim(connection, "claim-a", "Alice", ("a",)),
            "claim-b": _claim(connection, "claim-b", "Bob", ("b",)),
            "claim-anon": _claim(connection, "claim-anon", "Anonymous", ("anon",)),
            "claim-mixed": _claim(connection, "claim-mixed", "Mixed", ("a", "b")),
            "claim-bad": _claim(connection, "claim-bad", "Bad", ("bad",)),
        }
        _history(connection, "claim-a", "a")
        connection.commit()
        preserved_tables = (
            "conversation_interactions",
            "conversation_messages",
            "episodic_memories",
            "memory_evidence",
            "semantic_claim_evidence",
            "semantic_formation_decisions",
            "semantic_claim_revisions",
        )
        baseline = {table: _rows(connection, table) for table in preserved_tables}
        claims_before = _rows(connection, "semantic_claims")
    upgrade_database(
        str(database.engine.url),
        config_path=project_root / "alembic.ini",
        revision=CURRENT_REVISION,
    )
    with closing(sqlite3.connect(path)) as connection:
        assert {table: _rows(connection, table) for table in preserved_tables} == baseline
        claims_after = _rows(connection, "semantic_claims")
        for before, after in zip(claims_before, claims_after, strict=True):
            assert before[:1] + before[2:] == after[:1] + after[2:]
        current = dict(connection.execute("SELECT claim_id, claim_key FROM semantic_claims"))
        for claim_id, person in (("claim-a", "person-a"), ("claim-b", "person-b")):
            assert current[claim_id] == scope_semantic_key(
                keys[claim_id], MemoryScope(identity_id, person)
            )
        for claim_id in ("claim-anon", "claim-mixed", "claim-bad"):
            assert current[claim_id] == keys[claim_id]
        assert dict(
            connection.execute("SELECT counterparty_id, scope_verified FROM conversation_sessions")
        ) == {"person-a": 1, "person-b": 1, "local-default": 0}
        audits = connection.execute(
            "SELECT details FROM audit_events WHERE event_type='memory_scope_migrated'"
        ).fetchall()
        assert len(audits) == 1
        assert json.loads(audits[0][0]) == {
            "migration_revision": CURRENT_REVISION,
            "policy_version": 1,
            "rekeyed_claim_count": 2,
            "quarantined_claim_count": 3,
        }
        assert "Private" not in audits[0][0]
    upgrade_database(
        str(database.engine.url),
        config_path=project_root / "alembic.ini",
        revision=CURRENT_REVISION,
    )
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM audit_events WHERE event_type='memory_scope_migrated'"
        ).fetchone() == (1,)
    # Historical migration preservation is proved above at its exact revision.
    # Current owner reads require the complete current body representation.
    upgrade_database(str(database.engine.url), config_path=project_root / "alembic.ini")
    scope = MemoryScope(identity_id, "person-a")
    with database.session_factory() as session:
        assert resolve_memory_scope(session, "memory-a") == scope
        assert resolve_memory_scope(session, "memory-anon") is None
        assert resolve_memory_scope(session, "memory-bad") is None
        assert session.scalars(
            select(EpisodicMemoryRow.memory_id).where(eligible_episode_predicate(scope))
        ).all() == ["memory-a"]
        assert session.scalars(
            select(SemanticClaimRow.claim_id).where(eligible_semantic_claim_predicate(scope))
        ).all() == ["claim-a"]


@pytest.mark.parametrize(
    "corruption",
    [
        "UPDATE memory_evidence SET quote='unquoted invented content' WHERE memory_id='memory-a'",
        "UPDATE memory_evidence SET source_message_id='message-b' WHERE memory_id='memory-a'",
        "UPDATE episodic_memories SET source_interaction_id='interaction-b', "
        "formation_version=2 WHERE memory_id='memory-a'",
        "UPDATE conversation_messages SET role='assistant' WHERE message_id='message-a'",
        "UPDATE conversation_sessions SET scope_verified=0 WHERE session_id='session-a'",
        "DELETE FROM memory_evidence WHERE memory_id='memory-a'",
    ],
)
def test_episode_scope_refuses_broken_or_unverified_lineage(
    migrated_database: Database, corruption: str
) -> None:
    identity_id = activate(migrated_database).identity.identity_id
    with closing(sqlite3.connect(str(migrated_database.engine.url.database))) as connection:
        for suffix, person in (("a", "person-a"), ("b", "person-b")):
            _episode(
                connection,
                identity_id=identity_id,
                suffix=suffix,
                counterparty_id=person,
                verified=True,
            )
        connection.execute(corruption)
        connection.commit()
    with migrated_database.session_factory() as session:
        assert resolve_memory_scope(session, "memory-a") is None
        ids = session.scalars(
            select(EpisodicMemoryRow.memory_id).where(eligible_episode_predicate(None))
        ).all()
        assert "memory-a" not in ids


def test_new_verified_local_default_is_distinct_from_quarantined_history(
    migrated_database: Database,
) -> None:
    identity_id = activate(migrated_database).identity.identity_id
    with closing(sqlite3.connect(str(migrated_database.engine.url.database))) as connection:
        _episode(
            connection,
            identity_id=identity_id,
            suffix="new",
            counterparty_id="local-default",
            verified=True,
        )
        _episode(
            connection,
            identity_id=identity_id,
            suffix="legacy",
            counterparty_id="local-default",
            verified=False,
        )
        connection.commit()
    scope = MemoryScope(identity_id, "local-default")
    with migrated_database.session_factory() as session:
        assert resolve_memory_scope(session, "memory-new") == scope
        assert resolve_memory_scope(session, "memory-legacy") is None
        from satori.infrastructure.persistence.models.conversation import ConversationInteractionRow

        assert session.scalars(
            select(ConversationInteractionRow.interaction_id).where(
                eligible_interaction_predicate(scope)
            )
        ).all() == ["interaction-new"]


def test_downgrade_rejects_collapsing_active_counterparty_namespaces(
    legacy_memory_store: tuple[Database, Path, str], project_root: Path
) -> None:
    database, path, identity_id = legacy_memory_store
    upgrade_database(
        str(database.engine.url),
        config_path=project_root / "alembic.ini",
        revision=CURRENT_REVISION,
    )
    with closing(sqlite3.connect(path)) as connection:
        _episode(
            connection,
            identity_id=identity_id,
            suffix="a",
            counterparty_id="person-a",
            verified=True,
        )
        _episode(
            connection,
            identity_id=identity_id,
            suffix="b",
            counterparty_id="person-b",
            verified=True,
        )
        base_key = _claim(connection, "same-a", "Same", ("a",))
        connection.execute(
            "UPDATE semantic_claims SET claim_key=? WHERE claim_id='same-a'",
            (scope_semantic_key(base_key, MemoryScope(identity_id, "person-a")),),
        )
        _claim(connection, "same-b", "Same", ("b",))
        connection.execute(
            "UPDATE semantic_claims SET claim_key=? WHERE claim_id='same-b'",
            (scope_semantic_key(base_key, MemoryScope(identity_id, "person-b")),),
        )
        connection.commit()
        before = _rows(connection, "semantic_claims")
    with pytest.raises(RuntimeError, match="collapse distinct active partitions"):
        downgrade_database(
            str(database.engine.url),
            config_path=project_root / "alembic.ini",
            revision=LEGACY_REVISION,
        )
    with closing(sqlite3.connect(path)) as connection:
        assert _rows(connection, "semantic_claims") == before
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            CURRENT_REVISION,
        )
        assert "scope_verified" in {
            row[1] for row in connection.execute("PRAGMA table_info(conversation_sessions)")
        }
