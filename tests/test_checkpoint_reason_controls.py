"""Application/floor sequencing with a deterministic owner port and no providers."""

from dataclasses import replace
from pathlib import Path

import pytest

from satori.application.erasure.personality_reason_contracts import (
    CheckpointReasonCommand,
    CheckpointReasonRequest,
)
from satori.application.erasure.personality_reason_use_cases import CheckpointReasonManager
from satori.domain.erasure_bodies import BodyAction
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonKind,
    CheckpointReasonPlan,
    CheckpointReasonScope,
    CheckpointReasonSelector,
    CheckpointReasonTarget,
)
from satori.domain.source_policy import (
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime

SCOPE = CheckpointReasonScope("satori", "lineage")
SELECTOR = CheckpointReasonSelector(CheckpointReasonKind.CHECKPOINT_APPROVAL, "approval")


class OwnerPort:
    """Only models the owner protocol; actual body/FK integration has a separate gate."""

    def __init__(self) -> None:
        self.plan = CheckpointReasonPlan(
            scope=SCOPE,
            targets=(CheckpointReasonTarget(SELECTOR, 1, "d" * 64, "e" * 64, 3),),
            store_digest="b" * 64,
            schema_digest="c" * 64,
            scanned_rows=100,
            approval_reasons=1,
            restore_reasons=0,
            metadata_references=3,
            already_erased=0,
            blockers=(),
        )
        self.applied: tuple[BodyAction, ...] = ()
        self.apply_failure: str | None = None
        self.inspection_failure = False
        self.resolve_calls = 0

    def resolve_scope(
        self,
        identity_id: str,
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> CheckpointReasonScope:
        self.resolve_calls += 1
        if identity_id != SCOPE.identity_id:
            raise SourcePolicyError("checkpoint_reason_scope_unavailable")
        return SCOPE

    def preview(
        self,
        scope: CheckpointReasonScope,
        selectors: tuple[CheckpointReasonSelector, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> CheckpointReasonPlan:
        assert scope == SCOPE
        assert selectors == (SELECTOR,)
        return self.plan

    def apply(
        self,
        actions: tuple[BodyAction, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
        reviewed_store_digest: str | None = None,
    ) -> None:
        if self.apply_failure is not None:
            raise SourcePolicyError(self.apply_failure)
        self.applied = actions

    def current(
        self, actions: tuple[BodyAction, ...], *, snapshot: SourceEligibilitySnapshot
    ) -> bool:
        if self.inspection_failure:
            raise RuntimeError("synthetic private inspection error")
        return self.applied == actions


type Controls = tuple[
    SourcePolicyRuntime, OwnerPort, CheckpointReasonManager, CheckpointReasonRequest
]


@pytest.fixture
def controls(
    tmp_path: Path,
) -> Controls:
    runtime = SourcePolicyRuntime(tmp_path / "controls-floor", "satori")
    runtime.enroll(reviewed=True, new_installation=True, lineage_id="lineage")
    runtime.upgrade_checkpoint_reason_policy()
    store = OwnerPort()
    request = CheckpointReasonRequest(identity_id="satori", selectors=(SELECTOR,))
    return runtime, store, CheckpointReasonManager(runtime, store), request


def _command(
    manager: CheckpointReasonManager, request: CheckpointReasonRequest
) -> CheckpointReasonCommand:
    preview = manager.preview(request)
    assert preview.readiness == "ready"
    return CheckpointReasonCommand(
        identity_id=request.identity_id,
        selectors=request.selectors,
        request_id="reason-action",
        expected_generation=preview.generation,
        expected_erasure_revision=preview.erasure_revision,
        expected_body_revision=preview.body_revision,
        approval_digest=preview.approval_digest,
    )


def test_same_generation_store_drift_and_wrong_digest_deny_before_floor(
    controls: tuple[
        SourcePolicyRuntime, OwnerPort, CheckpointReasonManager, CheckpointReasonRequest
    ],
) -> None:
    runtime, store, manager, request = controls
    command = _command(manager, request)
    before = runtime.ledger.database_path.read_bytes()
    store.plan = replace(store.plan, store_digest="9" * 64)
    with pytest.raises(SourcePolicyError, match="body_erasure_review_stale"):
        manager.commit(command)
    assert runtime.ledger.database_path.read_bytes() == before
    assert not store.applied


def test_floor_precedes_owner_failure_and_explicit_resume_reuses_original_receipt(
    controls: tuple[
        SourcePolicyRuntime, OwnerPort, CheckpointReasonManager, CheckpointReasonRequest
    ],
) -> None:
    runtime, store, manager, request = controls
    command = _command(manager, request)
    store.apply_failure = "body_target_changed_review_required"
    pending = manager.commit(command)
    assert pending.phase == "body_application_pending"
    assert pending.failure_reason == "body_target_changed_review_required"
    with runtime.exclusive() as snapshot:
        assert snapshot.body_revision == 1
        action = runtime.body_action(command.request_id)
        assert action is not None
    committed = runtime.ledger.database_path.read_bytes()
    store.apply_failure = None
    result = manager.resume(request, request_id=command.request_id)
    assert result.main_projection_current
    assert result.physical_erasure is False
    assert manager.commit(command) == result
    assert manager.status(request, request_id=command.request_id) == result
    assert runtime.ledger.database_path.read_bytes() == committed
    assert store.applied == (action,)


def test_failure_after_owner_commit_reports_pending_without_disclosing_error_text(
    controls: tuple[
        SourcePolicyRuntime, OwnerPort, CheckpointReasonManager, CheckpointReasonRequest
    ],
) -> None:
    _runtime, store, manager, request = controls
    command = _command(manager, request)
    store.inspection_failure = True
    result = manager.commit(command)
    assert len(store.applied) == 1
    assert result.main_projection_current is False
    assert result.failure_reason == "body_application_unavailable"
    assert "synthetic private" not in result.model_dump_json()
    store.inspection_failure = False
    assert manager.status(request, request_id=command.request_id).main_projection_current


def test_scope_proof_precedes_requested_action_lookup(
    controls: tuple[
        SourcePolicyRuntime, OwnerPort, CheckpointReasonManager, CheckpointReasonRequest
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _store, manager, request = controls
    lookups = 0
    original = runtime.body_action

    def tracked(request_id: str) -> BodyAction | None:
        nonlocal lookups
        lookups += 1
        return original(request_id)

    monkeypatch.setattr(runtime, "body_action", tracked)
    foreign = request.model_copy(update={"identity_id": "foreign"})
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="checkpoint_reason_scope_unavailable"):
        manager.status(foreign, request_id="private-other-action")
    assert lookups == 0
    assert runtime.ledger.database_path.read_bytes() == before


def test_work_limit_blocks_preview_and_commit_without_floor_mutation(
    controls: tuple[
        SourcePolicyRuntime, OwnerPort, CheckpointReasonManager, CheckpointReasonRequest
    ],
) -> None:
    runtime, store, manager, request = controls
    command = _command(manager, request)
    store.plan = replace(store.plan, scanned_rows=1_000_001)
    before = runtime.ledger.database_path.read_bytes()
    preview = manager.preview(request)
    assert preview.readiness == "blocked"
    assert "body_application_capacity_exceeded" in preview.blockers
    with pytest.raises(SourcePolicyError, match="body_erasure_plan_blocked"):
        manager.commit(command)
    assert runtime.ledger.database_path.read_bytes() == before


def test_action_needs_no_source_exclusion_or_intent(
    controls: tuple[
        SourcePolicyRuntime, OwnerPort, CheckpointReasonManager, CheckpointReasonRequest
    ],
) -> None:
    runtime, _store, manager, request = controls
    result = manager.commit(_command(manager, request))
    with runtime.shared() as snapshot:
        assert snapshot.generation == 0
        assert snapshot.erasure_revision == 0
        assert snapshot.excluded_message_ids == frozenset()
    assert result.selected_bodies_removed
    assert not result.execution_authorized


def test_replay_cannot_change_selected_record(
    controls: tuple[
        SourcePolicyRuntime, OwnerPort, CheckpointReasonManager, CheckpointReasonRequest
    ],
) -> None:
    runtime, _store, manager, request = controls
    command = _command(manager, request)
    manager.commit(command)
    before = runtime.ledger.database_path.read_bytes()
    changed = command.model_copy(
        update={
            "selectors": (
                CheckpointReasonSelector(CheckpointReasonKind.PERSONALITY_RESTORE, "restore"),
            )
        }
    )
    with pytest.raises(SourcePolicyError, match="body_action_unavailable_in_scope"):
        manager.commit(changed)
    assert runtime.ledger.database_path.read_bytes() == before


def test_false_physical_claims_and_duplicate_or_empty_selectors_rejected(
    controls: tuple[
        SourcePolicyRuntime, OwnerPort, CheckpointReasonManager, CheckpointReasonRequest
    ],
) -> None:
    from pydantic import ValidationError

    _runtime, _store, manager, request = controls
    with pytest.raises(ValidationError):
        CheckpointReasonRequest(identity_id="satori", selectors=(SELECTOR, SELECTOR))
    with pytest.raises(SourcePolicyError, match="checkpoint_reason_selectors_required"):
        manager.preview(CheckpointReasonRequest(identity_id="satori"))
    preview = manager.preview(request)
    with pytest.raises(ValidationError):
        type(preview).model_validate({**preview.model_dump(), "physical_erasure": True})
