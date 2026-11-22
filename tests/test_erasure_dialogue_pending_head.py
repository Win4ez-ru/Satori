"""Trusted pending heads permit operator repair, never ordinary body admission."""

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.engine import Connection

from satori.application.erasure.body_contracts import BodyErasureRequest
from satori.domain.erasure_dialogue import DialogueBodyAction
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models import ConversationMessageRow
from tests.erasure_support import ErasureCase, create_case, provider_counts, stored_state
from tests.test_erasure_dialogue_integration import dialogue_command, prepare_dialogue
from tests.test_erasure_intent_integration import _command as intent_command
from tests.test_erasure_intent_integration import _manager as intent_manager
from tests.test_erasure_intent_integration import _request as intent_request
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import DERIVE_TEXT, _exclude, _turn


@pytest.fixture
def case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "dialogue-pending-floor")


def _assert_pending_fence(case: ErasureCase) -> None:
    """Both the runtime and the actual fake-provider turn stop without a new write."""
    before = stored_state(case)
    calls = provider_counts(case.services)
    with (
        pytest.raises(SourcePolicyError, match="body_reconciliation_required"),
        case.runtime.shared(),
    ):
        pytest.fail("pending body head admitted an ordinary reader")
    with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
        _turn(case.services[-1], "Synthetic pending-head admission probe.", "pending-probe")
    assert provider_counts(case.services) == calls
    assert stored_state(case) == before


def _assert_original_root_terminal(case: ErasureCase) -> None:
    with case.database.session_factory() as session:
        row = session.get(ConversationMessageRow, case.message_id)
        assert row is not None
        assert row.content is None
        assert row.body_state == "erased"


def test_pending_new_head_can_resolve_already_terminal_root_and_resume(
    case: ErasureCase,
) -> None:
    manager, request = prepare_dialogue(case)
    completed = manager.commit(dialogue_command(manager, request))
    assert completed.main_projection_current
    assert completed.body_revision == 2
    _assert_original_root_terminal(case)
    original_main = stored_rows(case.path)

    # A durable next floor receipt exists while main still proves the previous head.
    # It selects the same terminal bodies and cannot manufacture their old text.
    with case.runtime.exclusive() as snapshot:
        original_action = case.runtime.body_action("dialogue-action")
        assert isinstance(original_action, DialogueBodyAction)
        pending_action = case.runtime.commit_dialogue_action(
            replace(
                original_action.proposal,
                request_id="pending-terminal-root",
                expected_body_revision=snapshot.body_revision,
                approval_digest="f" * 64,
            )
        )
        assert pending_action.result_body_revision == 3
    assert stored_rows(case.path) == original_main
    _assert_pending_fence(case)

    before_status = stored_state(case)
    status = manager.status(request, request_id=pending_action.proposal.request_id)
    assert status.phase == "body_application_pending"
    assert status.body_revision == 3
    assert status.main_projection_current is False
    assert status.selected_bodies_removed is False
    assert stored_state(case) == before_status

    calls = provider_counts(case.services)
    floor_rows = stored_rows(case.runtime.ledger.database_path)
    resumed = manager.resume(request, request_id=pending_action.proposal.request_id)
    assert resumed.phase == "body_application_complete"
    assert resumed.main_projection_current
    assert resumed.selected_bodies_removed
    assert resumed.materialized_body_revision == 3
    after = stored_rows(case.path)
    for table, rows in original_main.items():
        if table not in {"erasure_body_applications", "erasure_body_bindings"}:
            assert after[table] == rows, table
    assert stored_rows(case.runtime.ledger.database_path) == floor_rows
    assert provider_counts(case.services) == calls
    with case.runtime.shared() as snapshot:
        assert snapshot.body_revision == 3
        assert case.runtime.body_action("dialogue-action") == original_action
        assert case.runtime.body_action("pending-terminal-root") == pending_action
    _assert_original_root_terminal(case)


def test_new_root_stale_pending_target_can_be_reapproved_without_restoring_old_root(
    case: ErasureCase,
) -> None:
    manager, request = prepare_dialogue(case)
    assert manager.commit(dialogue_command(manager, request)).main_projection_current
    _assert_original_root_terminal(case)

    # B has independent canonical wording; its paired assistant was already erased
    # because that reply consumed A. No second episode action is needed for B.
    _exclude(case.database, case.runtime, case.derived_message_id)
    second_case = replace(
        case,
        message_id=case.derived_message_id,
        interaction_id=case.derived_interaction_id,
    )
    intents = intent_manager(second_case)
    second_intent = intent_command(
        intents, intent_request(second_case), request_id="second-root-intent"
    )
    assert intents.commit(second_intent).main_projection_current
    second_request = BodyErasureRequest(
        message_id=second_case.message_id,
        counterparty_id=case.scope.counterparty_id,
        intent_request_id=second_intent.request_id,
    )
    preview = manager.preview(second_request)
    assert preview.readiness == "ready", preview.blockers
    assert preview.user_bodies == 1
    assert preview.assistant_bodies == 0
    assert preview.already_erased >= 1
    command = dialogue_command(manager, second_request, "second-root-pending")
    before = stored_rows(case.path)
    calls = provider_counts(case.services)
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
            raise RuntimeError("synthetic owner interruption")

    event.listen(case.database.engine, "before_cursor_execute", fail)
    try:
        pending = manager.commit(command)
    finally:
        event.remove(case.database.engine, "before_cursor_execute", fail)
    assert writes == 1
    assert pending.body_revision == 3
    assert pending.main_projection_current is False
    assert stored_rows(case.path) == before
    assert provider_counts(case.services) == calls
    _assert_pending_fence(case)

    # Deliberate synthetic preimage drift, with every earlier literal quotation
    # still present. The old review cannot authorize the new canonical body.
    with case.database.session_factory() as session:
        content = session.scalar(
            select(ConversationMessageRow.content).where(
                ConversationMessageRow.message_id == second_case.message_id
            )
        )
        assert isinstance(content, str)
        assert DERIVE_TEXT in content
    with case.database.engine.begin() as connection:
        connection.execute(
            update(ConversationMessageRow)
            .where(ConversationMessageRow.message_id == second_case.message_id)
            .values(content=content + " Synthetic additional wording.")
        )
    changed_state = stored_state(case)
    refused = manager.resume(second_request, request_id=command.request_id)
    assert refused.phase == "body_application_pending"
    assert refused.failure_reason == "body_target_changed_review_required"
    assert refused.main_projection_current is False
    assert stored_state(case) == changed_state
    _assert_original_root_terminal(case)
    _assert_pending_fence(case)

    # Full floor-authorized history resolves A even though B keeps head 3 pending.
    # A fresh exact review supersedes only B's effective target and repairs head 4.
    fresh_preview = manager.preview(second_request)
    assert fresh_preview.readiness == "ready", fresh_preview.blockers
    assert fresh_preview.body_revision == 3
    assert fresh_preview.user_bodies == 1
    assert fresh_preview.assistant_bodies == 0
    fresh_command = dialogue_command(manager, second_request, "second-root-reapproved")
    repaired = manager.commit(fresh_command)
    assert repaired.body_revision == 4
    assert repaired.main_projection_current
    assert repaired.selected_bodies_removed
    after = stored_rows(case.path)
    for table, rows in before.items():
        if table not in {
            "conversation_messages",
            "erasure_body_applications",
            "erasure_body_bindings",
        }:
            assert after[table] == rows, table
    with case.database.session_factory() as session:
        second_row = session.get(ConversationMessageRow, second_case.message_id)
        assert second_row is not None
        assert second_row.content is None
        assert second_row.body_state == "erased"
    _assert_original_root_terminal(case)
    with case.runtime.shared() as snapshot:
        assert snapshot.body_revision == 4
        historical_pending = case.runtime.body_action(command.request_id)
        assert historical_pending is not None
        assert historical_pending.result_body_revision == 3
    assert provider_counts(case.services) == calls
