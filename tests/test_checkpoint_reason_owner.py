"""Personality owns operator-reason disposition without changing numerical history."""

import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event

from satori.application.personality.ports import PersonalityCheckpointApprovalHeader
from satori.application.personality.use_cases import (
    ApprovePersonalityCheckpoint,
    GetPersonalityEvolution,
    PersonalityCheckpointApprovalProposal,
    RestorePersonalityCheckpoint,
)
from satori.core.personality import PersonalityRestoreProposal
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonAction,
    CheckpointReasonKind,
    CheckpointReasonProposal,
    CheckpointReasonScope,
    CheckpointReasonSelector,
    CheckpointReasonTarget,
)
from satori.domain.personality_evolution import PersonalityManager
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.checkpoint_reason_material import (
    read_checkpoint_reason_material,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_body_codec import encode_body_action
from satori.infrastructure.persistence.models.erasure_bodies import ErasureBodyApplicationRow
from satori.infrastructure.persistence.repositories.personality import (
    SQLAlchemyPersonalityRepository,
)
from tests.fakes import FrozenClock
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage14_personality_persistence import (
    NOW,
    PrefixIds,
    _apply,
    _seed_personality_run,
    _uow,
)


def create_reason_records(
    database: Database,
) -> tuple[
    CheckpointReasonScope, PersonalityCheckpointApprovalProposal, PersonalityRestoreProposal
]:
    fixture = _seed_personality_run(database, prefix="checkpoint-reason")
    result = _apply(database, fixture).apply(
        fixture.identity_id,
        reflection_run_id=fixture.run_id,
        reflection_proposal_id=fixture.proposal_id,
        proposal=fixture.proposal,
        trace_id="checkpoint-reason-evolution",
    )
    assert result.recorded
    with database.session_factory() as session:
        owner = SQLAlchemyPersonalityRepository(session)
        checkpoint = owner.get_checkpoint_for_version(fixture.identity_id, 2)
        activation = owner.get_activation_checkpoint(fixture.identity_id)
        assert checkpoint is not None
        assert activation is not None
    approval = PersonalityCheckpointApprovalProposal(
        checkpoint_id=checkpoint.snapshot.checkpoint_id,
        checkpoint_hash=checkpoint.snapshot.checkpoint_hash,
        expected_personality_version=2,
        reason="SYNTHETIC_PRIVATE_APPROVAL_REASON",
    )
    ApprovePersonalityCheckpoint(
        lambda: _uow(database), FrozenClock(NOW + timedelta(days=1)), PrefixIds("reason-approval")
    ).execute(fixture.identity_id, approval, trace_id="reason-approval-trace")
    restore = PersonalityRestoreProposal(
        checkpoint_id=activation.snapshot.checkpoint_id,
        checkpoint_hash=activation.snapshot.checkpoint_hash,
        expected_personality_version=2,
        reason="SYNTHETIC_PRIVATE_RESTORE_REASON",
    )
    restored = RestorePersonalityCheckpoint(
        lambda: _uow(database),
        PersonalityManager(),
        FrozenClock(NOW + timedelta(days=2)),
        PrefixIds("reason-restore"),
    ).execute(fixture.identity_id, restore, trace_id="reason-restore-trace")
    assert restored.restored
    return CheckpointReasonScope(fixture.identity_id, "reason-owner-lineage"), approval, restore


def reason_action(database: Database, scope: CheckpointReasonScope) -> CheckpointReasonAction:
    with database.session_factory() as session:
        owner = SQLAlchemyPersonalityRepository(session)
        selectors = tuple(
            sorted(
                (
                    *(
                        CheckpointReasonSelector(
                            CheckpointReasonKind.CHECKPOINT_APPROVAL, row.approval_id
                        )
                        for row in owner.list_checkpoint_approval_headers(scope.identity_id)
                    ),
                    *(
                        CheckpointReasonSelector(
                            CheckpointReasonKind.PERSONALITY_RESTORE, row.restore_id
                        )
                        for row in owner.list_restore_headers(scope.identity_id)
                    ),
                ),
                key=lambda item: item.key,
            )
        )
        material = read_checkpoint_reason_material(
            session, selectors, scope=scope, require_terminal=False
        )
        targets = tuple(
            CheckpointReasonTarget(
                selector,
                1,
                material.digest(selector),
                material.digest(selector, terminal=True),
                material.metadata_reference_count(selector),
            )
            for selector in selectors
        )
    proposal = CheckpointReasonProposal(
        scope, "reason-owner-request", 0, 0, 0, "a" * 64, "b" * 64, "c" * 64, targets
    )
    return CheckpointReasonAction(
        proposal,
        "reason-owner-action",
        "reason-floor",
        "reason-installation",
        1,
        NOW,
        "reason-owner-trace",
    )


def add_action(session: object, action: CheckpointReasonAction) -> None:
    # Tests use the same already-visible main application prerequisite as the coordinator.
    from sqlalchemy.orm import Session

    assert isinstance(session, Session)
    session.add(
        ErasureBodyApplicationRow(
            action_id=action.action_id,
            request_id=action.proposal.request_id,
            identity_id=action.proposal.scope.identity_id,
            root_message_id=None,
            floor_id=action.floor_id,
            lineage_id=action.proposal.scope.lineage_id,
            body_revision=action.result_body_revision,
            action=encode_body_action(action),
            applied_at=NOW,
        )
    )
    session.flush()


def test_owner_null_reasons_preserves_numerics_audit_budget_anchor_and_replay(
    migrated_database: Database,
) -> None:
    scope, approval, restore = create_reason_records(migrated_database)
    inspection = GetPersonalityEvolution(lambda: _uow(migrated_database), FrozenClock(NOW))
    before = inspection.inspect(scope.identity_id)
    assert before is not None
    rows_before = stored_rows(Path(migrated_database.engine.url.database or ""))
    action = reason_action(migrated_database, scope)
    with migrated_database.session_factory() as session:
        session.connection().exec_driver_sql("PRAGMA trusted_schema=OFF")
        add_action(session, action)
        SQLAlchemyPersonalityRepository(session).erase_reason_bodies(
            action.proposal.targets, action_id=action.action_id
        )
        session.commit()
    after = inspection.inspect(scope.identity_id)
    assert after is not None
    assert after.personality == before.personality
    assert after.budgets == before.budgets
    assert after.approved_checkpoint == before.approved_checkpoint
    assert after.revisions == before.revisions
    assert after.checkpoints == before.checkpoints
    assert {row.reason_state for row in after.approvals} == {"erased"}
    assert {row.reason_state for row in after.restores} == {"erased"}
    with migrated_database.session_factory() as session:
        owner = SQLAlchemyPersonalityRepository(session)
        assert owner.list_checkpoint_approvals(scope.identity_id) == ()
        assert owner.list_restore_events(scope.identity_id) == ()
        assert (
            session.connection()
            .exec_driver_sql("SELECT reason IS NULL FROM personality_checkpoint_approvals")
            .scalar_one()
            == 1
        )
        assert (
            session.connection()
            .exec_driver_sql("SELECT reason IS NULL FROM personality_restore_events")
            .scalar_one()
            == 1
        )
    exported = inspection.export_json(scope.identity_id)
    assert exported is not None
    document = json.loads(exported)
    for name in ("approvals", "restores"):
        assert document[name][0]["reason_state"] == "erased"
        assert document[name][0]["reason_action_id"] == action.action_id
        assert "reason" not in document[name][0]
    rows_after = stored_rows(Path(migrated_database.engine.url.database or ""))
    for table in (
        "audit_events",
        "audit_metadata_bindings",
        "personality_revisions",
        "personality_checkpoint_traits",
        "satori_personality_traits",
    ):
        assert rows_after[table] == rows_before[table]
    replay = ApprovePersonalityCheckpoint(
        lambda: _uow(migrated_database), FrozenClock(NOW), PrefixIds("reason-replay")
    ).execute(
        scope.identity_id,
        approval.model_copy(update={"reason": "DO_NOT_RESURRECT"}),
        trace_id="reason-replay-trace",
    )
    assert isinstance(replay, PersonalityCheckpointApprovalHeader)
    assert replay.reason_state == "erased"
    stale = RestorePersonalityCheckpoint(
        lambda: _uow(migrated_database), PersonalityManager(), FrozenClock(NOW), PrefixIds("stale")
    ).execute(scope.identity_id, restore, trace_id="stale-restore")
    assert stale.restored is False
    assert stale.evaluation.reason_code == "personality_target_version_conflict"
    assert stored_rows(Path(migrated_database.engine.url.database or "")) == rows_after


def test_metadata_inspection_and_budget_selection_never_select_reason_column(
    migrated_database: Database,
) -> None:
    scope, _approval, _restore = create_reason_records(migrated_database)
    statements: list[str] = []

    def observe(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(migrated_database.engine, "before_cursor_execute", observe)
    try:
        inspection = GetPersonalityEvolution(lambda: _uow(migrated_database), FrozenClock(NOW))
        assert inspection.inspect(scope.identity_id) is not None
        assert inspection.export_json(scope.identity_id) is not None
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", observe)
    assert statements
    assert all("personality_checkpoint_approvals.reason," not in sql for sql in statements)
    assert all("personality_restore_events.reason," not in sql for sql in statements)


def test_owner_preflight_all_targets_and_outer_rollback_preserve_original_bodies(
    migrated_database: Database,
) -> None:
    scope, _approval, _restore = create_reason_records(migrated_database)
    before = stored_rows(Path(migrated_database.engine.url.database or ""))
    action = reason_action(migrated_database, scope)
    changed = replace(action.proposal.targets[-1], body_digest="d" * 64)
    with migrated_database.session_factory() as session:
        add_action(session, action)
        with pytest.raises(SourcePolicyError, match="body_action_target_mismatch"):
            SQLAlchemyPersonalityRepository(session).erase_reason_bodies(
                (*action.proposal.targets[:-1], changed), action_id=action.action_id
            )
        session.rollback()
    assert stored_rows(Path(migrated_database.engine.url.database or "")) == before

    def fail_second(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        if statement.startswith("UPDATE personality_restore_events SET reason=NULL"):
            raise RuntimeError("controlled_reason_write_failure")

    event.listen(migrated_database.engine, "before_cursor_execute", fail_second)
    try:
        with migrated_database.session_factory() as session:
            add_action(session, action)
            with pytest.raises(RuntimeError, match="controlled_reason_write_failure"):
                SQLAlchemyPersonalityRepository(session).erase_reason_bodies(
                    action.proposal.targets, action_id=action.action_id
                )
            session.rollback()
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", fail_second)
    assert stored_rows(Path(migrated_database.engine.url.database or "")) == before


@pytest.mark.parametrize(
    ("table", "alias"),
    [
        ("personality_checkpoint_approvals", "approval_id"),
        ("personality_checkpoint_approvals", "checkpoint_id"),
        ("personality_restore_events", "restore_id"),
        ("personality_restore_events", "revision_id"),
        ("personality_restore_events", "identity_version"),
        ("personality_restore_events", "resulting_checkpoint_id"),
    ],
)
def test_terminal_replace_unique_aliases_deny_with_recursive_triggers_disabled(
    migrated_database: Database,
    table: str,
    alias: str,
) -> None:
    scope, _approval, _restore = create_reason_records(migrated_database)
    action = reason_action(migrated_database, scope)
    with migrated_database.session_factory() as session:
        add_action(session, action)
        SQLAlchemyPersonalityRepository(session).erase_reason_bodies(
            action.proposal.targets, action_id=action.action_id
        )
        session.commit()
    path = Path(migrated_database.engine.url.database or "")
    before = stored_rows(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA recursive_triggers=OFF")
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute(f"SELECT * FROM {table}").fetchone())
        key = "approval_id" if "approval" in table else "restore_id"
        row[key] = "replacement-owner"
        if "approval" in table:
            row["checkpoint_id"] = "replacement-checkpoint"
        else:
            row["revision_id"] = "replacement-revision"
            row["after_aggregate_version"] = 40
            row["before_aggregate_version"] = 39
            row["resulting_checkpoint_id"] = "replacement-checkpoint"
        original = dict(connection.execute(f"SELECT * FROM {table}").fetchone())
        if alias == "identity_version":
            row["after_aggregate_version"] = original["after_aggregate_version"]
            row["before_aggregate_version"] = original["before_aggregate_version"]
        else:
            row[alias] = original[alias]
        row.update(reason="DO_NOT_RESURRECT", reason_state="available", reason_action_id=None)
        with pytest.raises(sqlite3.IntegrityError, match="checkpoint_reason_terminal"):
            connection.execute(
                f"INSERT OR REPLACE INTO {table} ({','.join(row)}) "
                f"VALUES ({','.join('?' for _ in row)})",
                tuple(row.values()),
            )
    assert stored_rows(path) == before
