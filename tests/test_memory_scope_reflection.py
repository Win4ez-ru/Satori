"""Canonical eligibility for global reflection without widening public memory recall."""

import pytest
from sqlalchemy import select, text

from satori.domain.reflection import REFLECTION_SCHEMA_VERSION_V1
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.memory import MemoryEvidenceRow
from satori.infrastructure.persistence.models.positions import PositionEvidenceRow
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from tests.test_stage4_conversation_memory import activate
from tests.test_stage11_positions_integration import create_interaction
from tests.test_stage13_inclinations_persistence import (
    _canonical_position_source,
    _create_reflection_source,
    _memory_source_citing_prior_interaction,
)


def _foreign_user(database: Database, *, content: str) -> tuple[str, str]:
    interaction_id = create_interaction(
        database,
        counterparty_id="bob",
        content=content,
        prefix="reflection-foreign-scope",
        day=2,
    )
    with database.session_factory() as session:
        interaction = session.get(ConversationInteractionRow, interaction_id)
        assert interaction is not None
        message = session.execute(
            select(ConversationMessageRow).where(
                ConversationMessageRow.interaction_id == interaction_id,
                ConversationMessageRow.role == "user",
            )
        ).scalar_one()
        return interaction.session_id, message.message_id


def _corrupt_message_session(database: Database, message_id: str, session_id: str) -> None:
    """Model a malformed imported row, beyond the normal composite foreign key."""

    with database.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            connection.execute(
                text("UPDATE conversation_messages SET session_id=:session WHERE message_id=:id"),
                {"session": session_id, "id": message_id},
            )
            connection.commit()
        finally:
            connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


@pytest.mark.parametrize("phase", ["selection", "fixed_reload"])
@pytest.mark.parametrize(
    "tamper", ["foreign_edge", "message_session", "unverified_source", "assistant_root", "quote"]
)
def test_reflection_episode_rejects_any_invalid_root_before_selection_or_reload(
    migrated_database: Database, phase: str, tamper: str
) -> None:
    snapshot = activate(migrated_database)
    identity_id = snapshot.identity.identity_id
    root_id, message_id, quote = _memory_source_citing_prior_interaction(
        migrated_database, identity_id=identity_id
    )
    run_id = None
    if phase == "fixed_reload":
        run, _, _ = _create_reflection_source(
            migrated_database, identity_id=identity_id, schema_version=REFLECTION_SCHEMA_VERSION_V1
        )
        run_id = run.run_id
    foreign_session_id, foreign_message_id = _foreign_user(
        migrated_database, content=f"{quote} — независимое наблюдение Боба."
    )
    with migrated_database.session_factory() as session:
        message = session.get(ConversationMessageRow, message_id)
        root = session.get(ConversationInteractionRow, root_id)
        edge = session.get(MemoryEvidenceRow, "inclination-prior-root-memory-evidence")
        assert message is not None
        assert root is not None
        assert edge is not None
        if tamper == "foreign_edge":
            session.add(
                MemoryEvidenceRow(
                    evidence_id="reflection-foreign-extra-evidence",
                    memory_id=edge.memory_id,
                    source_message_id=foreign_message_id,
                    provenance_kind="explicit_user_statement",
                    quote=quote,
                    observed_at=edge.observed_at,
                )
            )
        elif tamper == "unverified_source":
            canonical_session = session.get(ConversationSessionRow, root.session_id)
            assert canonical_session is not None
            canonical_session.scope_verified = False
        elif tamper == "assistant_root":
            assistant = session.execute(
                select(ConversationMessageRow).where(
                    ConversationMessageRow.interaction_id == root_id,
                    ConversationMessageRow.role == "assistant",
                )
            ).scalar_one()
            edge.source_message_id = assistant.message_id
            edge.quote = assistant.content
        elif tamper == "quote":
            edge.quote = "несуществующая точная цитата"
        session.commit()
    if tamper == "message_session":
        _corrupt_message_session(migrated_database, message_id, foreign_session_id)

    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        if phase == "selection":
            assert unit.reflection.list_eligible_sources(identity_id=identity_id, limit=12) == ()
        else:
            assert run_id is not None
            with pytest.raises(
                ValueError, match=r"^reflection source provenance or content hash mismatch$"
            ):
                unit.reflection.load_generation_sources(run_id)


@pytest.mark.parametrize("phase", ["selection", "fixed_reload"])
@pytest.mark.parametrize("tamper", ["foreign_message", "message_session", "counterparty"])
def test_reflection_position_requires_canonical_message_interaction_session_and_counterparty(
    migrated_database: Database, phase: str, tamper: str
) -> None:
    snapshot = activate(migrated_database)
    identity_id = snapshot.identity.identity_id
    _, message_id, _, quote = _canonical_position_source(migrated_database, identity_id=identity_id)
    run_id = None
    if phase == "fixed_reload":
        run, _, _ = _create_reflection_source(
            migrated_database, identity_id=identity_id, schema_version=REFLECTION_SCHEMA_VERSION_V1
        )
        run_id = run.run_id
    foreign_session_id, foreign_message_id = _foreign_user(
        migrated_database, content=f"{quote} — независимое наблюдение Боба."
    )
    with migrated_database.session_factory() as session:
        message = session.get(ConversationMessageRow, message_id)
        edge = session.get(PositionEvidenceRow, "inclination-position-evidence")
        assert message is not None
        assert edge is not None
        if tamper == "foreign_message":
            edge.source_message_id = foreign_message_id
        elif tamper == "counterparty":
            edge.source_counterparty_id = "bob"
        session.commit()
    if tamper == "message_session":
        _corrupt_message_session(migrated_database, message_id, foreign_session_id)

    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        if phase == "selection":
            assert unit.reflection.list_eligible_sources(identity_id=identity_id, limit=12) == ()
        else:
            assert run_id is not None
            with pytest.raises(
                ValueError, match=r"^reflection source provenance or content hash mismatch$"
            ):
                unit.reflection.load_generation_sources(run_id)
