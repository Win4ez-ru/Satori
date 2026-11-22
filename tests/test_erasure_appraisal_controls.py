"""Application/floor sequencing with a deterministic owner port and no providers."""

from dataclasses import replace
from pathlib import Path

import pytest

from satori.application.erasure.appraisal_use_cases import AppraisalBodyManager
from satori.application.erasure.body_contracts import BodyErasureCommand, BodyErasureRequest
from satori.domain.erasure_appraisal import (
    AppraisalBodyPlan,
    AppraisalBodyTarget,
)
from satori.domain.erasure_bodies import BodyAction
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.test_erasure_intent_floor import _commit, _exclude, _proposal, _root


class OwnerPort:
    """Only models the owner protocol; actual body/FK integration has a separate gate."""

    def __init__(self) -> None:
        self.plan = AppraisalBodyPlan(
            root=_root(),
            targets=(
                AppraisalBodyTarget(
                    "transition", "input", "audit", 1, "d" * 64, "e" * 64, 3, "f" * 64
                ),
            ),
            store_digest="b" * 64,
            schema_digest="c" * 64,
            scanned_rows=100,
            appraisal_transitions=1,
            audit_duplicates=1,
            input_receipts=1,
            metadata_references=3,
            already_erased=0,
            blockers=(),
        )
        self.applied: tuple[BodyAction, ...] = ()
        self.apply_failure: str | None = None
        self.inspection_failure = False
        self.resolve_calls = 0

    def resolve_root(
        self,
        message_id: str,
        *,
        counterparty_id: str,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> CanonicalRootRef:
        self.resolve_calls += 1
        if (message_id, counterparty_id) != (_root().message_id, _root().counterparty_id):
            raise SourcePolicyError("canonical_source_unavailable_in_scope")
        return _root()

    def preview(
        self,
        root: CanonicalRootRef,
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> AppraisalBodyPlan:
        assert root == _root()
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


@pytest.fixture
def controls(
    tmp_path: Path,
) -> tuple[SourcePolicyRuntime, OwnerPort, AppraisalBodyManager, BodyErasureRequest]:
    runtime = SourcePolicyRuntime(tmp_path / "controls-floor", "satori")
    runtime.enroll(reviewed=True, new_installation=True, lineage_id="lineage")
    _exclude(runtime, _root(), "source-request")
    _commit(runtime, _proposal(runtime, _root(), "intent-request"))
    runtime.upgrade_appraisal_policy()
    store = OwnerPort()
    request = BodyErasureRequest(
        message_id=_root().message_id, counterparty_id="alice", intent_request_id="intent-request"
    )
    return runtime, store, AppraisalBodyManager(runtime, store), request


def _command(manager: AppraisalBodyManager, request: BodyErasureRequest) -> BodyErasureCommand:
    preview = manager.preview(request)
    assert preview.readiness == "ready"
    return BodyErasureCommand(
        **request.model_dump(),
        request_id="appraisal-action",
        expected_generation=preview.generation,
        expected_erasure_revision=preview.erasure_revision,
        expected_body_revision=preview.body_revision,
        approval_digest=preview.approval_digest,
    )


def test_same_generation_store_drift_and_wrong_digest_deny_before_floor(
    controls: tuple[SourcePolicyRuntime, OwnerPort, AppraisalBodyManager, BodyErasureRequest],
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
    controls: tuple[SourcePolicyRuntime, OwnerPort, AppraisalBodyManager, BodyErasureRequest],
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
    controls: tuple[SourcePolicyRuntime, OwnerPort, AppraisalBodyManager, BodyErasureRequest],
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
    controls: tuple[SourcePolicyRuntime, OwnerPort, AppraisalBodyManager, BodyErasureRequest],
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
    foreign = request.model_copy(update={"counterparty_id": "bob"})
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="canonical_source_unavailable_in_scope"):
        manager.status(foreign, request_id="private-other-action")
    assert lookups == 0
    assert runtime.ledger.database_path.read_bytes() == before


def test_work_limit_blocks_preview_and_commit_without_floor_mutation(
    controls: tuple[SourcePolicyRuntime, OwnerPort, AppraisalBodyManager, BodyErasureRequest],
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
