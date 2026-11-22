"""InteractionLog terminal bodies preserve attribution and never become live messages."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from satori.application.conversation.contracts import TalkInput
from satori.domain.conversation_history import DialogueBodyState
from satori.domain.erasure_dialogue import (
    DialogueBodyAction,
    DialogueBodyProposal,
    DialogueBodyTarget,
)
from satori.domain.memory_scope import MemoryScope
from satori.domain.source_policy import CanonicalRootRef, PayloadRef, SourcePolicyError
from satori.infrastructure.persistence.canonical_provenance import dialogue_message_digest
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_body_codec import encode_body_action
from satori.infrastructure.persistence.models.conversation import (
    ConversationMessageRow,
)
from satori.infrastructure.persistence.models.erasure_bodies import ErasureBodyApplicationRow
from satori.infrastructure.persistence.repositories.conversation import (
    SQLAlchemyConversationHistoryRepository,
)
from satori.infrastructure.persistence.repositories.models import SQLAlchemyCurrentModelsRepository
from satori.infrastructure.persistence.repositories.positions import SQLAlchemyPositionsRepository
from satori.infrastructure.persistence.repositories.relationship import (
    SQLAlchemyRelationshipRepository,
)
from satori.infrastructure.persistence.source_policy import require_current_payloads
from tests.test_memory_scope_integration import ScopeProbe, _captured, _probe, _turn
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate


def _action(
    database: Database, *, only_assistant: bool = False
) -> tuple[ScopeProbe, DialogueBodyAction]:
    activate(database)
    probe = _probe(database, counterparty="alice", prefix="dialogue-owner", day=1)
    active_session = probe.services.start_session.execute()
    _turn(
        probe,
        "Меня зовут DIALOGUE_PRIVATE_SYNTHETIC.",
        "lifecycle-source",
        session_id=active_session.session_id,
    )
    memory = probe.services.memories.execute(scope=probe.scope)[0]
    with database.engine.connect() as connection:
        messages = tuple(
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT * FROM conversation_messages WHERE interaction_id=? ORDER BY sequence",
                (memory.source_interaction_id,),
            ).mappings()
        )
    root = CanonicalRootRef(
        "dialogue-owner-lineage",
        probe.scope.identity_id,
        probe.scope.counterparty_id,
        str(messages[0]["session_id"]),
        memory.source_interaction_id,
        str(messages[0]["message_id"]),
    )
    targets = tuple(
        sorted(
            (
                DialogueBodyTarget(
                    str(row["message_id"]),
                    int(str(row["schema_version"])),
                    dialogue_message_digest(row),
                    dialogue_message_digest(row, terminal=True),
                )
                for row in messages
                if not only_assistant or row["role"] == "assistant"
            ),
            key=lambda target: target.message_id,
        )
    )
    proposal = DialogueBodyProposal(
        root,
        "owner-intent",
        "dialogue-owner-request",
        1,
        1,
        0,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        targets,
    )
    action = DialogueBodyAction(
        proposal,
        "dialogue-owner-action",
        "dialogue-owner-floor",
        "installation",
        1,
        INTERACTION_TIME + timedelta(days=2),
        "body-trace",
    )
    return probe, action


def _receipt(action: DialogueBodyAction) -> ErasureBodyApplicationRow:
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


def _apply(database: Database, action: DialogueBodyAction) -> None:
    with database.session_factory() as session:
        session.add(_receipt(action))
        session.flush()
        SQLAlchemyConversationHistoryRepository(session).erase_bodies(
            action.proposal.targets,
            action_id=action.action_id,
        )
        session.commit()


def test_terminal_messages_preserve_headers_metadata_and_downstream_evidence(
    migrated_database: Database,
) -> None:
    probe, action = _action(migrated_database)
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)
    _apply(migrated_database, action)
    after = stored_rows(path)
    for table, rows in before.items():
        if table not in {"conversation_messages", "erasure_body_applications"}:
            assert after[table] == rows
    with migrated_database.session_factory() as session:
        repository = SQLAlchemyConversationHistoryRepository(session)
        header = repository.get_interaction_header(action.proposal.root.interaction_id)
        assert header is not None
        assert header.has_terminal_body
        assert header.user_message.body_state is DialogueBodyState.ERASED
        assert header.assistant_message is not None
        assert header.assistant_message.body_state is DialogueBodyState.ERASED
        assert not hasattr(header.user_message, "content")
        for target in action.proposal.targets:
            row = session.get(ConversationMessageRow, target.message_id)
            assert row is not None
            assert row.content is None
            assert row.body_action_id == action.action_id
            assert repository.get_erased_header(target.message_id, scope=probe.scope) is not None
            assert (
                repository.get_erased_header(
                    target.message_id,
                    scope=MemoryScope(probe.scope.identity_id, "bob"),
                )
                is None
            )
        assert repository.get_history(scope=probe.scope).interactions == ()
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []
        for method, key in (
            (repository.get_interaction, action.proposal.root.interaction_id),
            (repository.get_by_client_request_id, "lifecycle-source"),
        ):
            with pytest.raises(SourcePolicyError, match="dialogue_body_unavailable"):
                method(key)
        with pytest.raises(SourcePolicyError, match="dialogue_body_unavailable"):
            require_current_payloads(
                session, (PayloadRef("assistant_reply", action.proposal.root.interaction_id),)
            )
    captured = _captured(probe)
    with pytest.raises(SourcePolicyError, match="dialogue_body_unavailable"):
        asyncio.run(
            probe.services.talk.execute(TalkInput("retry", "retry-trace", "lifecycle-source"))
        )
    report = asyncio.run(
        probe.services.post_response.execute(
            action.proposal.root.interaction_id,
            trace_id="terminal-post",
        )
    )
    assert report.succeeded
    assert report.failure_phases == ()
    assert (
        report.episode_formation_ms
        == report.episode_embedding_ms
        == report.semantic_consolidation_ms
        == 0
    )
    assert _captured(probe) == captured
    assert stored_rows(path) == after


def test_assistant_erasure_keeps_independent_user_sources_and_filters_pairs_before_limit(
    migrated_database: Database,
) -> None:
    probe, action = _action(migrated_database, only_assistant=True)
    first_interaction = action.proposal.root.interaction_id
    second = _turn(
        probe,
        "Вторая синтетическая реплика.",
        "dialogue-live-second",
        session_id=action.proposal.root.session_id,
    )
    with migrated_database.engine.connect() as connection:
        messages = tuple(
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT * FROM conversation_messages WHERE interaction_id=? ORDER BY sequence",
                (second.interaction_id,),
            ).mappings()
        )
    root = replace(
        action.proposal.root,
        interaction_id=second.interaction_id,
        message_id=str(messages[0]["message_id"]),
    )
    assistant = messages[1]
    target = DialogueBodyTarget(
        str(assistant["message_id"]),
        int(str(assistant["schema_version"])),
        dialogue_message_digest(assistant),
        dialogue_message_digest(assistant, terminal=True),
    )
    action = replace(action, proposal=replace(action.proposal, root=root, targets=(target,)))
    _apply(migrated_database, action)
    with migrated_database.session_factory() as session:
        assert SQLAlchemyCurrentModelsRepository(session).get_source_messages(
            second.interaction_id, limit=10
        )
        assert SQLAlchemyPositionsRepository(session).get_source_messages(
            second.interaction_id, limit=10
        )
        assert (
            SQLAlchemyRelationshipRepository(session).get_source(second.interaction_id) is not None
        )
        repository = SQLAlchemyConversationHistoryRepository(session)
        selected = repository.list_recent_completed(
            scope=probe.scope,
            session_id=action.proposal.root.session_id,
            excluded_interaction_id="other",
            limit=1,
        )
        assert tuple(item.interaction_id for item in selected) == (first_interaction,)
        assert tuple(
            item.interaction_id for item in repository.get_history(scope=probe.scope).interactions
        ) == (first_interaction,)


@pytest.mark.parametrize(
    "drift", ["content", "retained_metadata", "missing_action", "wrong_selector", "wrong_terminal"]
)
def test_owner_preimage_and_exact_approval_fail_before_mutation(
    migrated_database: Database,
    drift: str,
) -> None:
    _probe, action = _action(migrated_database)
    if drift in {"content", "retained_metadata"}:
        with migrated_database.engine.begin() as connection:
            field = (
                "content='different synthetic wording'"
                if drift == "content"
                else "schema_version=2"
            )
            connection.exec_driver_sql(
                f"UPDATE conversation_messages SET {field} WHERE message_id=?",
                (action.proposal.root.message_id,),
            )
    targets = action.proposal.targets
    if drift == "wrong_selector":
        targets = (replace(targets[0], body_digest="f" * 64),)
    if drift == "wrong_terminal":
        targets = (replace(targets[0], terminal_digest="f" * 64), *targets[1:])
        action = replace(action, proposal=replace(action.proposal, targets=targets))
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)
    with migrated_database.session_factory() as session:
        if drift != "missing_action":
            session.add(_receipt(action))
            session.flush()
        with pytest.raises(SourcePolicyError):
            SQLAlchemyConversationHistoryRepository(session).erase_bodies(
                targets, action_id=action.action_id
            )
        session.rollback()
    assert stored_rows(path) == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE conversation_messages SET content=content WHERE body_state='erased'",
        "UPDATE conversation_messages SET content='restore',body_state='available',"
        "body_action_id=NULL WHERE body_state='erased'",
        "UPDATE conversation_messages SET schema_version=schema_version+1 "
        "WHERE body_state='erased'",
        "DELETE FROM conversation_messages WHERE body_state='erased'",
        "UPDATE conversation_interactions SET trace_id='changed' WHERE interaction_id=:interaction",
        "DELETE FROM conversation_interactions WHERE interaction_id=:interaction",
        "INSERT INTO conversation_messages SELECT 'replacement',session_id,interaction_id,"
        "schema_version,role,'replacement',created_at,sequence,'available',NULL "
        "FROM conversation_messages WHERE role='assistant'",
    ],
)
def test_sql_terminal_guards_prevent_resurrection_or_retained_metadata_mutation(
    migrated_database: Database,
    statement: str,
) -> None:
    _probe, action = _action(migrated_database)
    _apply(migrated_database, action)
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)
    with migrated_database.engine.connect() as connection:
        with pytest.raises(IntegrityError, match="dialogue_body_terminal"):
            connection.execute(
                text(statement), {"interaction": action.proposal.root.interaction_id}
            )
        connection.rollback()
    assert stored_rows(path) == before


def test_mid_owner_failure_rolls_back_receipt_and_all_message_bodies(
    migrated_database: Database,
) -> None:
    _probe, action = _action(migrated_database)
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)
    writes = 0

    def fail_second(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        nonlocal writes
        if statement.startswith("UPDATE conversation_messages"):
            writes += 1
            if writes == 2:
                raise RuntimeError("injected_dialogue_owner_failure")

    event.listen(migrated_database.engine, "before_cursor_execute", fail_second)
    try:
        with pytest.raises(RuntimeError, match="injected_dialogue_owner_failure"):
            _apply(migrated_database, action)
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", fail_second)
    assert writes == 2
    assert stored_rows(path) == before


@pytest.mark.parametrize("corruption", ["missing_assistant", "wrong_sequence"])
def test_incomplete_retained_pair_is_denied_by_owner_and_initial_sql_transition(
    migrated_database: Database,
    corruption: str,
) -> None:
    _probe, action = _action(migrated_database)
    with migrated_database.engine.begin() as connection:
        if corruption == "missing_assistant":
            connection.exec_driver_sql("DELETE FROM conversation_messages WHERE role='assistant'")
        else:
            # Deliberately corrupt one synthetic row while retaining the admitted schema.
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            try:
                connection.exec_driver_sql(
                    "UPDATE conversation_messages SET sequence=3 WHERE role='assistant'",
                )
            finally:
                connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    user_target = next(
        target
        for target in action.proposal.targets
        if target.message_id == action.proposal.root.message_id
    )
    action = replace(action, proposal=replace(action.proposal, targets=(user_target,)))
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)
    with migrated_database.session_factory() as session:
        session.add(_receipt(action))
        session.flush()
        with pytest.raises(SourcePolicyError, match="body_action_target_mismatch"):
            SQLAlchemyConversationHistoryRepository(session).erase_bodies(
                action.proposal.targets,
                action_id=action.action_id,
            )
        session.rollback()
    assert stored_rows(path) == before
    with migrated_database.session_factory() as session:
        session.add(_receipt(action))
        session.flush()
        with pytest.raises(IntegrityError, match="dialogue_body_terminal"):
            session.execute(
                text(
                    "UPDATE conversation_messages SET content=NULL,body_state='erased',"
                    "body_action_id=:action WHERE message_id=:message",
                ),
                {"action": action.action_id, "message": user_target.message_id},
            )
        session.rollback()
    assert stored_rows(path) == before
