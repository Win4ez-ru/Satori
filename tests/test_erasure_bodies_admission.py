"""Trusted body-head admission rejects plausible local receipts and terminal corruption."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, update

from satori.application.erasure.body_contracts import BodyErasureRequest
from satori.application.erasure.body_use_cases import BodyErasureManager
from satori.domain.erasure_bodies import BodyErasureAction, BodyErasureProposal
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_body_codec import encode_body_action
from satori.infrastructure.persistence.models.erasure_bodies import ErasureBodyApplicationRow
from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow
from satori.infrastructure.persistence.repositories.memory import SQLAlchemyEpisodicMemoryRepository
from satori.infrastructure.persistence.repositories.retrieval import (
    SQLAlchemyEpisodicMemoryIndexRepository,
)
from satori.infrastructure.source_policy.installation import bind_installation
from tests.erasure_support import ErasureCase, create_case, provider_counts
from tests.test_erasure_bodies_integration import command_for, prepare
from tests.test_source_exclusion_production import ROOT_TEXT, _exclude, _services, _turn


@pytest.fixture
def case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "body-admission-floor")


def _forged_action(
    case: ErasureCase, manager: BodyErasureManager, request: BodyErasureRequest
) -> BodyErasureAction:
    preview = manager.preview(request)
    assert preview.readiness == "ready"
    with case.runtime.exclusive() as snapshot:
        plan = manager.store.preview(preview.root, snapshot=snapshot)
        assert snapshot.body_revision == 0
        return BodyErasureAction(
            proposal=BodyErasureProposal(
                root=preview.root,
                intent_request_id=request.intent_request_id,
                request_id="unapproved-main-only-request",
                expected_source_generation=snapshot.generation,
                expected_erasure_revision=snapshot.erasure_revision,
                expected_body_revision=0,
                approval_digest=preview.approval_digest,
                store_digest=plan.store_digest,
                schema_digest=plan.schema_digest,
                targets=plan.targets,
            ),
            action_id="unapproved-main-only-action",
            floor_id=snapshot.floor_id,
            installation_id=snapshot.installation_id,
            result_body_revision=1,
            decided_at=datetime.now(UTC),
            trace_id="synthetic-main-only-action",
        )


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


def _assert_projection_denied(case: ErasureCase, manager: BodyErasureManager) -> None:
    floor = case.runtime.ledger.database_path.read_bytes()
    with case.runtime.exclusive() as snapshot:
        assert manager.store.current(case.runtime.body_actions(), snapshot=snapshot) is False
    with pytest.raises(SourcePolicyError):
        bind_installation(case.database, case.directory)
    assert case.runtime.ledger.database_path.read_bytes() == floor
    assert provider_counts(case.services) == case.initial_provider_calls


@pytest.mark.parametrize("erase_bodies", [False, True])
def test_empty_trusted_body_head_rejects_main_only_action(
    case: ErasureCase, erase_bodies: bool
) -> None:
    manager, request = prepare(case)
    floor = case.runtime.ledger.database_path.read_bytes()
    action = _forged_action(case, manager, request)
    with case.database.session_factory() as session:
        session.add(_receipt(action))
        session.flush()
        if erase_bodies:
            SQLAlchemyEpisodicMemoryIndexRepository(session).erase_bodies(
                tuple(target.memory_id for target in action.proposal.targets)
            )
            SQLAlchemyEpisodicMemoryRepository(session).erase_bodies(
                action.proposal.targets, action_id=action.action_id
            )
        session.commit()
    with case.runtime.exclusive() as snapshot:
        assert snapshot.body_revision == 0
        assert case.runtime.body_actions() == ()
    assert case.runtime.ledger.database_path.read_bytes() == floor
    _assert_projection_denied(case, manager)


def test_current_head_rejects_additional_terminal_body_outside_approved_selection(
    migrated_database: Database, tmp_path: Path
) -> None:
    case = create_case(migrated_database, tmp_path / "extra-body-floor", excluded=False)
    bob, _, _ = _services(case.database, case.directory, "unrelated-bob", 3, counterparty="bob")
    reply = _turn(bob, ROOT_TEXT, "unrelated-bob")
    with case.database.session_factory() as session:
        bob_memory = session.scalar(
            select(EpisodicMemoryRow.memory_id).where(
                EpisodicMemoryRow.source_interaction_id == reply.interaction_id
            )
        )
    assert bob_memory is not None
    _exclude(case.database, case.runtime, case.message_id)
    manager, request = prepare(case)
    command = command_for(manager, request)
    result = manager.commit(command)
    assert result.main_projection_current
    with case.runtime.exclusive():
        action = case.runtime.body_action(command.request_id)
        assert isinstance(action, BodyErasureAction)
        assert bob_memory not in {target.memory_id for target in action.proposal.targets}
    with case.database.engine.begin() as connection:
        connection.exec_driver_sql(
            "DELETE FROM episodic_memory_embeddings WHERE memory_id=?", (bob_memory,)
        )
        connection.exec_driver_sql(
            "UPDATE episodic_memories SET summary=NULL,body_state='erased',body_action_id=? "
            "WHERE memory_id=?",
            (result.action_id, bob_memory),
        )
        connection.exec_driver_sql(
            "UPDATE memory_evidence SET quote=NULL,body_state='erased',body_action_id=? "
            "WHERE memory_id=?",
            (result.action_id, bob_memory),
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    _assert_projection_denied(case, manager)


def test_missing_terminal_guard_rejects_exact_schema_and_normal_binding(
    case: ErasureCase,
) -> None:
    manager, request = prepare(case)
    command = command_for(manager, request)
    assert manager.commit(command).main_projection_current
    with case.database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER episode_vector_update")
    with pytest.raises(SourcePolicyError, match="erasure_schema_unsupported"):
        manager.preview(request)
    _assert_projection_denied(case, manager)


def test_changed_private_action_projection_cannot_match_trusted_head(case: ErasureCase) -> None:
    manager, request = prepare(case)
    command = command_for(manager, request)
    assert manager.commit(command).main_projection_current
    with case.runtime.exclusive():
        action = case.runtime.body_action(command.request_id)
        assert isinstance(action, BodyErasureAction)
    target = action.proposal.targets[0]
    changed = replace(
        action,
        proposal=replace(
            action.proposal,
            targets=(replace(target, terminal_digest="f" * 64), *action.proposal.targets[1:]),
        ),
    )
    with case.database.session_factory() as session:
        session.execute(
            update(ErasureBodyApplicationRow)
            .where(ErasureBodyApplicationRow.action_id == action.action_id)
            .values(action=encode_body_action(changed))
        )
        session.commit()
    _assert_projection_denied(case, manager)


def test_terminal_numeric_corruption_denies_even_after_exact_guard_is_restored(
    case: ErasureCase,
) -> None:
    manager, request = prepare(case)
    assert manager.commit(command_for(manager, request)).main_projection_current
    with case.database.engine.begin() as connection:
        guard = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name='episode_body_terminal'"
        ).scalar_one()
        assert isinstance(guard, str)
        connection.exec_driver_sql("DROP TRIGGER episode_body_terminal")
        connection.exec_driver_sql(
            "UPDATE episodic_memories SET importance=0.125 WHERE memory_id=?",
            (case.episode_ids[0],),
        )
        connection.exec_driver_sql(guard)
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    _assert_projection_denied(case, manager)
