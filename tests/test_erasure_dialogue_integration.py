"""Actual composition/floor/owners; synthetic data, no live provider or private deletion."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Connection

from satori.application.erasure.body_contracts import BodyErasureCommand, BodyErasureRequest
from satori.application.erasure.dialogue_use_cases import DialogueBodyManager
from satori.application.retrieval.contracts import RetrievalQuery, RetrievalStatus
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure_dialogue import SQLiteDialogueBodyStore
from satori.infrastructure.persistence.models import ConversationMessageRow
from satori.infrastructure.source_policy.installation import bind_installation, repair_installation
from tests.erasure_support import ErasureCase, create_case, provider_counts
from tests.test_erasure_bodies_integration import command_for, prepare
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import RECALL, ROOT_TEXT, SECRET, _services, _turn


def prepare_dialogue(case: ErasureCase) -> tuple[DialogueBodyManager, BodyErasureRequest]:
    body, request = prepare(case)
    assert body.commit(command_for(body, request)).main_projection_current
    case.runtime.upgrade_dialogue_policy()
    return DialogueBodyManager(case.runtime, SQLiteDialogueBodyStore(case.database)), request


def dialogue_command(
    manager: DialogueBodyManager, request: BodyErasureRequest, request_id: str = "dialogue-action"
) -> BodyErasureCommand:
    preview = manager.preview(request)
    assert preview.readiness == "ready", preview.blockers
    return BodyErasureCommand(
        **request.model_dump(),
        request_id=request_id,
        expected_generation=preview.generation,
        expected_erasure_revision=preview.erasure_revision,
        expected_body_revision=preview.body_revision,
        approval_digest=preview.approval_digest,
    )


@pytest.fixture
def case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "dialogue-floor")


def test_dialogue_and_proved_descendants_removed_independent_user_retained(
    case: ErasureCase,
) -> None:
    manager, request = prepare_dialogue(case)
    before = stored_rows(case.path)
    calls = provider_counts(case.services)
    preview = manager.preview(request)
    assert (preview.user_bodies, preview.assistant_bodies) == (1, 2)
    result = manager.commit(dialogue_command(manager, request))
    assert result.main_projection_current
    assert result.selected_bodies_removed
    assert result.body_revision == 2
    assert result.physical_erasure is False
    after = stored_rows(case.path)
    for table, rows in before.items():
        if table not in {
            "conversation_messages",
            "erasure_body_applications",
            "erasure_body_bindings",
        }:
            assert after[table] == rows, table
    with case.database.session_factory() as session:
        messages = tuple(session.scalars(select(ConversationMessageRow)))
        assert sum(row.content is None for row in messages) == 3
        user = next(row for row in messages if row.message_id == case.derived_message_id)
        assert isinstance(user.content, str)
        assert user.body_state == "available"
    assert provider_counts(case.services) == calls
    assert manager.status(request, request_id="dialogue-action").main_projection_current
    assert manager.commit(
        BodyErasureCommand(
            **request.model_dump(),
            request_id="dialogue-action",
            expected_generation=preview.generation,
            expected_erasure_revision=preview.erasure_revision,
            expected_body_revision=preview.body_revision,
            approval_digest=preview.approval_digest,
        )
    ).main_projection_current


def test_atomic_owner_fault_retains_floor_then_restart_resume(case: ErasureCase) -> None:
    manager, request = prepare_dialogue(case)
    command = dialogue_command(manager, request)
    before = stored_rows(case.path)

    writes = 0

    def fail(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        nonlocal writes
        if statement.startswith("UPDATE conversation_messages"):
            writes += 1
            if writes == 3:
                assert (
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM conversation_messages WHERE content IS NULL"
                    ).scalar_one()
                    == 2
                )
                raise RuntimeError("synthetic owner interruption")

    event.listen(case.database.engine, "before_cursor_execute", fail)
    try:
        result = manager.commit(command)
        assert not result.main_projection_current
        assert writes == 3
        assert stored_rows(case.path) == before
        with case.runtime.exclusive() as snapshot:
            assert snapshot.body_revision == 2
    finally:
        event.remove(case.database.engine, "before_cursor_execute", fail)
    restarted = create_database(str(case.database.engine.url))
    try:
        with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
            bind_installation(restarted, case.directory)
        fresh = DialogueBodyManager(case.runtime, SQLiteDialogueBodyStore(restarted))
        assert fresh.resume(request, request_id=command.request_id).main_projection_current
        rebound = bind_installation(restarted, case.directory)
        with rebound.shared() as snapshot:
            assert snapshot.body_revision == 2
    finally:
        restarted.dispose()


def test_pre_dialogue_backup_cannot_regain_body_authority(
    case: ErasureCase, tmp_path: Path
) -> None:
    import sqlite3

    manager, request = prepare_dialogue(case)
    backup = tmp_path / "pre-dialogue.db"
    with sqlite3.connect(case.path) as origin, sqlite3.connect(backup) as target:
        origin.backup(target)
    assert manager.commit(dialogue_command(manager, request)).main_projection_current
    restored = create_database(f"sqlite+pysqlite:///{backup}")
    try:
        with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
            bind_installation(restored, case.directory)
        with case.runtime.exclusive() as snapshot:
            receipt = case.runtime.receipt(f"exclude-{case.message_id}")
            assert receipt is not None
            repair_installation(restored, case.runtime, snapshot, receipt)
        with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
            bind_installation(restored, case.directory)
        assert (
            DialogueBodyManager(case.runtime, SQLiteDialogueBodyStore(restored))
            .resume(request, request_id="dialogue-action")
            .main_projection_current
        )
        rebound = bind_installation(restored, case.directory)
        with rebound.shared() as snapshot:
            assert snapshot.body_revision == 2
    finally:
        restored.dispose()


def test_restart_recall_never_projects_forbidden_knowledge(case: ErasureCase) -> None:
    import asyncio

    manager, request = prepare_dialogue(case)
    assert manager.commit(dialogue_command(manager, request)).main_projection_current
    restarted = create_database(str(case.database.engine.url))
    try:
        services, provider, _embedding = _services(
            restarted, case.directory, "after-dialogue", 1, form=False
        )
        assert services.retrieve_memories is not None
        retrieved = asyncio.run(
            services.retrieve_memories.execute(
                RetrievalQuery(
                    text=RECALL,
                    trace_id="dialogue-retrieval",
                    cutoff=datetime.now(UTC),
                    current_interaction_id=None,
                    scope=case.scope,
                    operator_query=True,
                )
            )
        )
        assert retrieved.status is RetrievalStatus.NO_RELEVANT_MEMORY
        _turn(services, RECALL, "after-dialogue")
        assert len(provider.requests) == 1
        assert SECRET not in str(provider.requests[0])
    finally:
        restarted.dispose()


def test_unknown_assistant_ancestry_blocks_fresh_dialogue_plan(case: ErasureCase) -> None:
    manager, request = prepare_dialogue(case)
    with case.database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE source_payload_receipts SET "
            "proof_status='unknown' WHERE kind='assistant_reply' AND payload_id=?",
            (case.derived_interaction_id,),
        )
    preview = manager.preview(request)
    assert preview.readiness == "blocked"
    assert "dialogue_body_provenance_unproven" in preview.blockers


def test_episode_approval_cannot_replay_as_dialogue_approval(case: ErasureCase) -> None:
    manager, request = prepare_dialogue(case)
    command = dialogue_command(manager, request, "body-action")
    with pytest.raises(SourcePolicyError, match="body_action_unavailable_in_scope"):
        manager.commit(command)


def test_other_counterparty_matching_text_is_retained(case: ErasureCase) -> None:
    foreign, provider, _embedding = _services(
        case.database, case.directory, "bob-independent", 3, form=False, counterparty="bob"
    )
    reply = _turn(foreign, ROOT_TEXT, "bob-independent")
    with case.database.session_factory() as session:
        before = tuple(
            (row.message_id, row.content)
            for row in session.scalars(
                select(ConversationMessageRow).where(
                    ConversationMessageRow.interaction_id == reply.interaction_id
                )
            )
        )
    manager, request = prepare_dialogue(case)
    assert manager.commit(dialogue_command(manager, request)).main_projection_current
    with case.database.session_factory() as session:
        after = tuple(
            (row.message_id, row.content)
            for row in session.scalars(
                select(ConversationMessageRow).where(
                    ConversationMessageRow.interaction_id == reply.interaction_id
                )
            )
        )
    assert after == before
    assert len(provider.requests) == 1


def test_cross_scope_affected_assistant_blocks_fresh_plan(case: ErasureCase) -> None:
    import json

    foreign, _provider, _embedding = _services(
        case.database, case.directory, "bob-dependent", 3, form=False, counterparty="bob"
    )
    reply = _turn(foreign, "Независимый разговор другого человека.", "bob-dependent")
    manager, request = prepare_dialogue(case)
    with case.database.engine.begin() as connection:
        roots = json.loads(
            connection.exec_driver_sql(
                "SELECT root_message_ids FROM "
                "source_payload_receipts WHERE kind='assistant_reply' AND payload_id=?",
                (reply.interaction_id,),
            ).scalar_one()
        )
        connection.exec_driver_sql(
            "UPDATE source_payload_receipts SET "
            "root_message_ids=? WHERE kind='assistant_reply' AND payload_id=?",
            (json.dumps(sorted(set(roots) | {case.message_id})), reply.interaction_id),
        )
    preview = manager.preview(request)
    assert preview.readiness == "blocked"
    assert "cross_counterparty_body_policy_required" in preview.blockers


def test_forged_ack_does_not_replace_actual_terminal_body_proof(
    case: ErasureCase, tmp_path: Path
) -> None:
    import sqlite3

    manager, request = prepare_dialogue(case)
    backup = tmp_path / "forged-ack.db"
    with sqlite3.connect(case.path) as origin, sqlite3.connect(backup) as target:
        origin.backup(target)
    assert manager.commit(dialogue_command(manager, request)).main_projection_current
    with sqlite3.connect(case.path) as live, sqlite3.connect(backup) as old:
        applications = live.execute(
            "SELECT * FROM erasure_body_applications WHERE body_revision=2"
        ).fetchone()
        assert applications is not None
        old.execute(
            "INSERT INTO erasure_body_applications VALUES (?,?,?,?,?,?,?,?,?)", applications
        )
        old.execute("UPDATE erasure_body_bindings SET body_revision=2")
    forged = create_database(f"sqlite+pysqlite:///{backup}")
    try:
        with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
            bind_installation(forged, case.directory)
        assert (
            not DialogueBodyManager(case.runtime, SQLiteDialogueBodyStore(forged))
            .status(request, request_id="dialogue-action")
            .main_projection_current
        )
    finally:
        forged.dispose()


def test_old_client_request_cannot_regenerate_terminal_reply(case: ErasureCase) -> None:
    manager, request = prepare_dialogue(case)
    assert manager.commit(dialogue_command(manager, request)).main_projection_current
    before = stored_rows(case.path)
    services, provider, _embedding = _services(
        case.database, case.directory, "terminal-client-retry", 4, form=False
    )
    import asyncio

    from satori.application.conversation.contracts import TalkInput

    with pytest.raises(SourcePolicyError):
        asyncio.run(
            services.talk.execute(TalkInput(ROOT_TEXT, "terminal-client-retry", "erasure-origin"))
        )
    assert not provider.requests
    assert stored_rows(case.path) == before


def test_operator_review_rejects_unapproved_terminal_assistant_before_floor_append(
    case: ErasureCase,
) -> None:
    manager, request = prepare_dialogue(case)
    with case.runtime.exclusive():
        episode_action = case.runtime.body_action("body-action")
        assert episode_action is not None
    # A valid FK and exact retained pair cannot turn an episode approval into permission
    # to erase an assistant. Ordinary admission and fresh operator review must both deny.
    with case.database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE conversation_messages SET content=NULL,body_state='erased',body_action_id=? "
            "WHERE interaction_id=? AND role='assistant'",
            (episode_action.action_id, case.interaction_id),
        )
    before = stored_rows(case.path)
    floor_before = case.runtime.ledger.database_path.read_bytes()
    calls = provider_counts(case.services)
    with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
        manager.preview(request)
    with (
        pytest.raises(SourcePolicyError, match="body_reconciliation_required"),
        case.runtime.shared(),
    ):
        pytest.fail("unapproved terminal body was admitted")
    assert stored_rows(case.path) == before
    assert case.runtime.ledger.database_path.read_bytes() == floor_before
    assert provider_counts(case.services) == calls
