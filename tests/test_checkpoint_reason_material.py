"""Fixed reason proof never binds the later mutable Personality aggregate."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import event

from satori.application.personality.use_cases import RestorePersonalityCheckpoint
from satori.core.personality import PersonalityRestoreProposal
from satori.domain.audit_metadata import AuditMetadataCoverageStatus
from satori.domain.erasure_bodies import body_actions_digest
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonKind,
    CheckpointReasonScope,
)
from satori.domain.personality_evolution import PersonalityManager
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.audit_metadata import AuditMetadataIndex
from satori.infrastructure.persistence.checkpoint_reason_material import (
    CheckpointReasonMaterialIndex,
    checkpoint_reason_target_terminal_proven,
    read_checkpoint_reason_material,
    validate_checkpoint_reason_projection,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_bodies import _ReadBudget
from satori.infrastructure.persistence.erasure_inventory import Store
from satori.infrastructure.persistence.erasure_personality_reasons import checkpoint_reasons_pending
from satori.infrastructure.persistence.repositories.personality import (
    SQLAlchemyPersonalityRepository,
)
from tests.fakes import FrozenClock
from tests.test_checkpoint_reason_owner import add_action, create_reason_records, reason_action
from tests.test_stage14_personality_persistence import NOW, PrefixIds, _uow

TABLES = (
    "personality_checkpoint_approvals",
    "personality_restore_events",
    "personality_checkpoints",
    "personality_checkpoint_traits",
    "personality_revisions",
    "audit_events",
    "audit_metadata_bindings",
    "satori_identities",
)


def reason_store(database: Database) -> Store:
    with database.engine.connect() as connection:
        return {
            table: tuple(
                dict(row)
                for row in connection.exec_driver_sql(f'SELECT * FROM "{table}"').mappings()
            )
            for table in TABLES
        }


def test_actual_owner_fixed_material_and_terminal_digest_are_exact(
    migrated_database: Database,
) -> None:
    scope, _approval, _restore = create_reason_records(migrated_database)
    action = reason_action(migrated_database, scope)
    index = CheckpointReasonMaterialIndex.build(reason_store(migrated_database))
    assert len(action.proposal.targets) == 2
    for target in action.proposal.targets:
        assert index.digest(target.selector) == target.body_digest
        assert index.digest(target.selector, terminal=True) == target.terminal_digest
        assert target.metadata_reference_count == (
            17 if target.selector.kind is CheckpointReasonKind.CHECKPOINT_APPROVAL else 34
        )
        assert not checkpoint_reason_target_terminal_proven(index, action, target)


@pytest.mark.parametrize(
    "mutation",
    [
        "foreign_checkpoint",
        "checkpoint_hash",
        "missing_trait",
        "trait_value",
        "bool_schema",
        "nonfinite_metric",
        "changed_diff",
        "malformed_diff",
        "changed_revision",
    ],
)
def test_malformed_fixed_closure_cannot_be_reviewed_as_a_valid_reason(
    migrated_database: Database, mutation: str
) -> None:
    scope, _approval, _restore = create_reason_records(migrated_database)
    action = reason_action(migrated_database, scope)
    store = deepcopy(reason_store(migrated_database))
    target = next(
        item
        for item in action.proposal.targets
        if item.selector.kind is CheckpointReasonKind.PERSONALITY_RESTORE
    )
    restore = store["personality_restore_events"][0]
    source_id = restore["source_checkpoint_id"]
    checkpoint = next(
        row for row in store["personality_checkpoints"] if row["checkpoint_id"] == source_id
    )
    revision = next(
        row
        for row in store["personality_revisions"]
        if row["revision_id"] == restore["revision_id"]
    )
    if mutation == "foreign_checkpoint":
        checkpoint["identity_id"] = "other-identity"
    elif mutation == "checkpoint_hash":
        checkpoint["checkpoint_hash"] = "0" * 64
    elif mutation == "missing_trait":
        store["personality_checkpoint_traits"] = tuple(
            row
            for row in store["personality_checkpoint_traits"]
            if not (row["checkpoint_id"] == source_id and row["trait_key"] == "curiosity")
        )
    elif mutation == "trait_value":
        next(
            row
            for row in store["personality_checkpoint_traits"]
            if row["checkpoint_id"] == source_id and row["trait_key"] == "curiosity"
        )["value"] = 0.123
    elif mutation == "bool_schema":
        checkpoint["hash_schema_version"] = True
    elif mutation == "nonfinite_metric":
        revision["rolling_total_path"] = float("nan")
    elif mutation == "changed_diff":
        restore["trait_diffs"] = '[{"trait_key":"curiosity","before_value":0.9,"after_value":0.7}]'
    elif mutation == "malformed_diff":
        restore["trait_diffs"] = '[{"trait_key":"curiosity","trait_key":"trust"}]'
    else:
        revision["after_aggregate_version"] = 999
    with pytest.raises((SourcePolicyError, ValueError)):
        CheckpointReasonMaterialIndex.build(store).digest(target.selector)


@pytest.mark.parametrize("terminal", [False, True])
def test_wrong_identity_denies_before_any_selected_reason_body_read(
    migrated_database: Database, terminal: bool
) -> None:
    scope, _approval, _restore = create_reason_records(migrated_database)
    action = reason_action(migrated_database, scope)
    selectors = tuple(target.selector for target in action.proposal.targets)
    statements: list[str] = []

    def observe(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        statements.append(statement)

    event.listen(migrated_database.engine, "before_cursor_execute", observe)
    try:
        with (
            migrated_database.session_factory() as session,
            pytest.raises(SourcePolicyError, match="checkpoint_reason_selector_unavailable"),
        ):
            read_checkpoint_reason_material(
                session,
                selectors,
                scope=CheckpointReasonScope("foreign", scope.lineage_id),
                require_terminal=terminal,
            )
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", observe)
    assert not any(
        statement.startswith("SELECT * FROM") and any(table in statement for table in TABLES[:2])
        for statement in statements
    )


def test_restored_reason_is_rejected_before_terminal_proof_loads_prose(
    migrated_database: Database,
) -> None:
    scope, _approval, _restore = create_reason_records(migrated_database)
    action = reason_action(migrated_database, scope)
    with (
        migrated_database.session_factory() as session,
        pytest.raises(SourcePolicyError, match="checkpoint_reason_selector_unavailable"),
    ):
        read_checkpoint_reason_material(
            session,
            tuple(target.selector for target in action.proposal.targets),
            scope=scope,
        )


def test_later_actual_restore_does_not_invalidate_old_fixed_terminal_proof(
    migrated_database: Database,
) -> None:
    scope, approval, _restore = create_reason_records(migrated_database)
    action = reason_action(migrated_database, scope)
    with migrated_database.session_factory() as session:
        add_action(session, action)
        SQLAlchemyPersonalityRepository(session).erase_reason_bodies(
            action.proposal.targets, action_id=action.action_id
        )
        session.commit()
    before = CheckpointReasonMaterialIndex.build(reason_store(migrated_database))
    result = RestorePersonalityCheckpoint(
        lambda: _uow(migrated_database),
        PersonalityManager(),
        FrozenClock(NOW + timedelta(days=5)),
        PrefixIds("later-real-restore"),
    ).execute(
        scope.identity_id,
        PersonalityRestoreProposal(
            checkpoint_id=approval.checkpoint_id,
            checkpoint_hash=approval.checkpoint_hash,
            expected_personality_version=3,
            reason="UNRELATED_LATER_OPERATOR_REASON",
        ),
        trace_id="later-real-restore",
    )
    assert result.restored
    after = CheckpointReasonMaterialIndex.build(reason_store(migrated_database))
    for target in action.proposal.targets:
        assert before.digest(target.selector) == after.digest(target.selector)
        assert checkpoint_reason_target_terminal_proven(after, action, target)
    snapshot = SourceEligibilitySnapshot(
        action.floor_id,
        action.installation_id,
        scope.lineage_id,
        scope.identity_id,
        0,
        frozenset(),
        body_revision=1,
        body_head_digest=body_actions_digest((action,)),
    )
    with migrated_database.engine.connect() as connection:
        validate_checkpoint_reason_projection(connection, snapshot, (action,), _ReadBudget())


def test_legacy_missing_binding_stays_unknown_without_blocking_exact_owner_material(
    migrated_database: Database,
) -> None:
    scope, _approval, _restore = create_reason_records(migrated_database)
    action = reason_action(migrated_database, scope)
    store = reason_store(migrated_database)
    unbound = dict(store, audit_metadata_bindings=())
    before = AuditMetadataIndex.build(unbound)
    assert before.coverage.unknown_events > 0
    assert all(
        item.status is AuditMetadataCoverageStatus.UNKNOWN for item in before.classifications
    )
    material = CheckpointReasonMaterialIndex.build(unbound)
    for target in action.proposal.targets:
        assert material.digest(target.selector) == target.body_digest
    for target in action.proposal.targets:
        row = material.rows[target.selector.key]
        row.update(reason=None, reason_state="erased", reason_action_id=action.action_id)
    assert AuditMetadataIndex.build(unbound) == before
    for target in action.proposal.targets:
        assert checkpoint_reason_target_terminal_proven(material, action, target)


def test_terminal_proof_cannot_claim_a_different_action_or_extra_identity_body(
    migrated_database: Database,
) -> None:
    scope, _approval, _restore = create_reason_records(migrated_database)
    action = reason_action(migrated_database, scope)
    store = reason_store(migrated_database)
    index = CheckpointReasonMaterialIndex.build(store)
    target = action.proposal.targets[0]
    index.rows[target.selector.key].update(
        reason=None, reason_state="erased", reason_action_id="unapproved-other-action"
    )
    assert not checkpoint_reason_target_terminal_proven(index, action, target)
    index.rows[target.selector.key]["reason_action_id"] = action.action_id
    assert not checkpoint_reason_target_terminal_proven(
        index,
        replace(
            action, proposal=replace(action.proposal, scope=CheckpointReasonScope("other", "x"))
        ),
        target,
    )


def test_pending_changed_reason_requires_fresh_review_with_shared_safe_reason(
    migrated_database: Database,
) -> None:
    scope, _approval, _restore = create_reason_records(migrated_database)
    action = reason_action(migrated_database, scope)
    store = reason_store(migrated_database)
    store["personality_checkpoint_approvals"][0]["reason"] = "CHANGED_PRIVATE_PENDING_REASON"
    snapshot = SourceEligibilitySnapshot(
        action.floor_id,
        action.installation_id,
        scope.lineage_id,
        scope.identity_id,
        0,
        frozenset(),
        body_revision=1,
        body_head_digest=body_actions_digest((action,)),
    )
    with pytest.raises(SourcePolicyError, match=r"^body_target_changed_review_required$"):
        checkpoint_reasons_pending(store, snapshot, (action,))
