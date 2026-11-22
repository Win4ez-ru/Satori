"""Real SQLite terminal episode owners; authoritative floor approval is tested separately."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from satori.domain.erasure_bodies import BodyErasureAction, BodyErasureProposal, BodyErasureTarget
from satori.domain.memory_scope import MemoryScope
from satori.domain.source_policy import CanonicalRootRef, SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_body_codec import encode_body_action
from satori.infrastructure.persistence.models.conversation import ConversationInteractionRow
from satori.infrastructure.persistence.models.erasure_bodies import ErasureBodyApplicationRow
from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow, MemoryEvidenceRow
from satori.infrastructure.persistence.models.retrieval import EpisodicMemoryEmbeddingRow
from satori.infrastructure.persistence.repositories.memory import SQLAlchemyEpisodicMemoryRepository
from satori.infrastructure.persistence.repositories.retrieval import (
    SQLAlchemyEpisodicMemoryIndexRepository,
)
from tests.test_memory_lifecycle_integration import _fixture
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import INTERACTION_TIME


def _action(database: Database) -> tuple[BodyErasureAction, str]:
    probe, memory = _fixture(database)
    with database.session_factory() as session:
        source = session.get(ConversationInteractionRow, memory.source_interaction_id)
        assert source is not None
        session_id = source.session_id
    root = CanonicalRootRef(
        "owner-test-lineage",
        probe.scope.identity_id,
        probe.scope.counterparty_id,
        session_id,
        memory.source_interaction_id,
        memory.evidence[0].source_message_id,
    )
    target = BodyErasureTarget(memory.memory_id, memory.aggregate_version, "a" * 64, "b" * 64)
    proposal = BodyErasureProposal(
        root, "intent", "body-request", 1, 1, 0, "c" * 64, "d" * 64, "e" * 64, (target,)
    )
    action = BodyErasureAction(
        proposal,
        "approved-body",
        "owner-test-floor",
        "installation",
        1,
        INTERACTION_TIME + timedelta(days=2),
        "body-trace",
    )
    return action, memory.memory_id


def _receipt(action: BodyErasureAction) -> ErasureBodyApplicationRow:
    return ErasureBodyApplicationRow(
        action_id=action.action_id,
        request_id=action.proposal.request_id,
        identity_id=action.proposal.root.identity_id,
        root_message_id=action.proposal.root.message_id,
        floor_id=action.floor_id,
        lineage_id=action.proposal.root.lineage_id,
        body_revision=action.result_body_revision,
        action=encode_body_action(action),
        applied_at=action.decided_at,
    )


def _apply(database: Database, action: BodyErasureAction) -> None:
    with database.session_factory() as session:
        session.add(_receipt(action))
        session.flush()
        SQLAlchemyEpisodicMemoryIndexRepository(session).erase_bodies(
            tuple(target.memory_id for target in action.proposal.targets)
        )
        SQLAlchemyEpisodicMemoryRepository(session).erase_bodies(
            action.proposal.targets, action_id=action.action_id
        )
        session.commit()


def test_terminal_body_preserves_header_and_evidence_and_removes_every_vector(
    migrated_database: Database,
) -> None:
    action, memory_id = _action(migrated_database)
    with migrated_database.session_factory() as session:
        original = session.get(EpisodicMemoryRow, memory_id)
        assert original is not None
        numeric = (
            original.aggregate_version,
            original.importance,
            original.confidence,
            original.lifecycle_status,
            original.source_interaction_id,
        )
        evidence_ids = tuple(
            session.scalars(
                select(MemoryEvidenceRow.evidence_id)
                .where(MemoryEvidenceRow.memory_id == memory_id)
                .order_by(MemoryEvidenceRow.evidence_id)
            )
        )
        for dimension in (2, 3):
            session.add(
                EpisodicMemoryEmbeddingRow(
                    embedding_id=str(dimension) * 64,
                    memory_id=memory_id,
                    provider="owner-test",
                    model="two-spaces",
                    dimensions=dimension,
                    input_schema_version=1,
                    vector=[1.0] * dimension,
                    indexed_at=INTERACTION_TIME,
                )
            )
        session.commit()
    _apply(migrated_database, action)
    with migrated_database.session_factory() as session:
        row = session.get(EpisodicMemoryRow, memory_id)
        assert row is not None
        assert row.summary is None
        assert (
            row.aggregate_version,
            row.importance,
            row.confidence,
            row.lifecycle_status,
            row.source_interaction_id,
        ) == numeric
        repository = SQLAlchemyEpisodicMemoryRepository(session)
        header = repository.get_erased_header(memory_id)
        assert header is not None
        assert header.evidence_ids == evidence_ids
        assert header.action_id == action.action_id
        assert (
            repository.get_memory(
                memory_id,
                scope=MemoryScope(
                    action.proposal.root.identity_id, action.proposal.root.counterparty_id
                ),
            )
            is None
        )
        assert SQLAlchemyEpisodicMemoryIndexRepository(session).get_eligible(memory_id) is None
        assert (
            list(
                session.scalars(
                    select(EpisodicMemoryEmbeddingRow).where(
                        EpisodicMemoryEmbeddingRow.memory_id == memory_id
                    )
                )
            )
            == []
        )
        assert all(
            edge.quote is None and edge.body_action_id == action.action_id
            for edge in session.scalars(
                select(MemoryEvidenceRow).where(MemoryEvidenceRow.memory_id == memory_id)
            )
        )
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []


@pytest.mark.parametrize("fault", ["missing_action", "foreign_target", "stale_version"])
def test_owner_rejects_unapproved_or_stale_targets_before_body_mutation(
    migrated_database: Database,
    fault: str,
) -> None:
    action, memory_id = _action(migrated_database)
    with migrated_database.session_factory() as session:
        if fault != "missing_action":
            session.add(_receipt(action))
            session.flush()
        target = action.proposal.targets[0]
        if fault == "foreign_target":
            target = replace(target, memory_id="foreign-memory")
        elif fault == "stale_version":
            session.execute(
                text("UPDATE episodic_memories SET aggregate_version=2 WHERE memory_id=:id"),
                {"id": memory_id},
            )
            session.flush()
        with pytest.raises(SourcePolicyError):
            SQLAlchemyEpisodicMemoryRepository(session).erase_bodies(
                (target,), action_id=action.action_id
            )
        row = session.get(EpisodicMemoryRow, memory_id)
        assert row is not None
        assert row.summary is not None
        assert row.body_state == "available"
        assert all(edge.quote is not None for edge in session.scalars(select(MemoryEvidenceRow)))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE episodic_memories SET body_state='available',summary='resurrection',"
        "body_action_id=NULL",
        "UPDATE episodic_memories SET aggregate_version=aggregate_version+1",
        "UPDATE episodic_memories SET lifecycle_status='archived'",
        "DELETE FROM episodic_memories",
        "UPDATE memory_evidence SET body_state='available',quote='resurrection',"
        "body_action_id=NULL",
        "UPDATE memory_evidence SET source_message_id='foreign-root'",
        "DELETE FROM memory_evidence",
    ],
)
def test_migrated_sql_prevents_terminal_resurrection_and_header_rewriting(
    migrated_database: Database,
    statement: str,
) -> None:
    action, _memory_id = _action(migrated_database)
    _apply(migrated_database, action)
    with (
        migrated_database.engine.begin() as connection,
        pytest.raises(IntegrityError, match="body_terminal"),
    ):
        connection.execute(text(statement))


def test_sql_prevents_quote_and_vector_insert_for_terminal_episode(
    migrated_database: Database,
) -> None:
    action, memory_id = _action(migrated_database)
    _apply(migrated_database, action)
    with (
        migrated_database.engine.begin() as connection,
        pytest.raises(IntegrityError, match="body_terminal"),
    ):
        connection.execute(
            text(
                "INSERT INTO memory_evidence (evidence_id,memory_id,source_message_id,"
                "provenance_kind,quote,observed_at) "
                "VALUES ('new-evidence',:memory,:root,'explicit_user_statement','new-quote',:now)"
            ),
            {"memory": memory_id, "root": action.proposal.root.message_id, "now": INTERACTION_TIME},
        )
    with (
        migrated_database.engine.begin() as connection,
        pytest.raises(IntegrityError, match="body_terminal"),
    ):
        connection.execute(
            text(
                "INSERT INTO episodic_memory_embeddings (embedding_id,memory_id,provider,model,"
                "dimensions,input_schema_version,vector,indexed_at) "
                "VALUES (:embedding,:memory,'fixture','fixture',2,1,'[0.0,1.0]',:now)"
            ),
            {"embedding": "f" * 64, "memory": memory_id, "now": INTERACTION_TIME},
        )


def test_failed_quote_transition_rolls_back_summary_vectors_and_action_projection(
    migrated_database: Database,
) -> None:
    action, _memory_id = _action(migrated_database)
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)

    def fail_quote(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        if statement.startswith("UPDATE memory_evidence SET"):
            raise RuntimeError("injected_owner_write_failure")

    event.listen(migrated_database.engine, "before_cursor_execute", fail_quote)
    try:
        with pytest.raises(RuntimeError, match="injected_owner_write_failure"):
            _apply(migrated_database, action)
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", fail_quote)
    assert stored_rows(path) == before
