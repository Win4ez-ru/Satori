"""Untrusted intent metadata cannot bypass review or forge erasure permission."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from satori.application.erasure.contracts import ErasurePreview
from satori.application.erasure.intent_contracts import (
    ErasureIntentCommand,
    ErasureIntentPreview,
    ErasureIntentRequest,
    ErasureIntentResult,
)
from satori.domain.erasure_intent import (
    ERASURE_RETENTION_POLICY_VERSION,
    ErasureIntent,
)
from satori.domain.erasure_preparation import (
    ErasureBodyReadiness,
    ErasureOwnerAssessment,
    ErasureOwnerPreparation,
    ErasurePreparationOwner,
)
from satori.domain.source_policy import CanonicalRootRef


def _root() -> CanonicalRootRef:
    return CanonicalRootRef("lineage", "satori", "alice", "session", "turn", "user-root")


def _intent(request_id: str = "request") -> ErasureIntent:
    return ErasureIntent(
        root=_root(),
        request_id=request_id,
        decision_id="decision",
        floor_id="floor",
        installation_id="installation",
        expected_source_generation=1,
        expected_erasure_revision=0,
        result_erasure_revision=1,
        reviewed_preview_digest="a" * 64,
        approval_digest="b" * 64,
        schema_revision="current_schema",
        schema_digest="c" * 64,
        store_digest="d" * 64,
        routing_digest="e" * 64,
        assessment_status="blocked",
        retention_policy=ERASURE_RETENTION_POLICY_VERSION,
        decided_at=datetime(2026, 10, 4, tzinfo=UTC),
        trace_id="trace",
    )


def _inventory() -> ErasurePreview:
    return ErasurePreview(
        root=_root(),
        floor_id="floor",
        installation_id="installation",
        generation=1,
        policy_version="erasure_preview_v1",
        source_policy_version="canonical_source_policy_v1",
        schema_revision="current_schema",
        schema_digest="c" * 64,
        store_digest="d" * 64,
        routing_digest="e" * 64,
        preview_digest="a" * 64,
        routes=(),
        scanned_rows=0,
        affected_payload_versions=0,
        uncertain_payload_versions=1,
        assessment_status="blocked",
        assessment_blockers=("unknown_legacy_provenance",),
        unassessed_surfaces=("external_copies",),
        execution_blockers=("physical_erasure_executor_not_implemented",),
    )


def _command(**changes: object) -> ErasureIntentCommand:
    return ErasureIntentCommand.model_validate(
        {
            "message_id": "user-root",
            "counterparty_id": "alice",
            "expected_generation": 1,
            "expected_erasure_revision": 0,
            "approval_digest": "b" * 64,
            "request_id": "request",
            **changes,
        }
    )


def _preview(**changes: object) -> ErasureIntentPreview:
    return ErasureIntentPreview.model_validate(
        {
            "inventory": _inventory(),
            "erasure_revision": 0,
            "approval_digest": "b" * 64,
            "retention_policy": ERASURE_RETENTION_POLICY_VERSION,
            **changes,
        }
    )


def _preparation(intent: ErasureIntent) -> ErasureOwnerPreparation:
    assessments = tuple(
        ErasureOwnerAssessment(
            owner=owner,
            body_readiness=ErasureBodyReadiness.RETAIN_ONLY,
            reason_codes=("owner_retained_metadata_only",),
            content_rows=0,
            numeric_rows=0,
            metadata_rows=0,
            uncertain_rows=0,
        )
        for owner in ErasurePreparationOwner
    )
    return ErasureOwnerPreparation(
        intent=intent,
        source_generation=1,
        schema_revision="current_schema",
        schema_digest="c" * 64,
        store_digest="d" * 64,
        routing_digest="e" * 64,
        retention_digest="f" * 64,
        assessment_status="blocked",
        assessments=assessments,
        prepared_at=datetime(2026, 10, 4, tzinfo=UTC),
    )


@pytest.mark.parametrize("field", ["expected_generation", "expected_erasure_revision"])
@pytest.mark.parametrize("value", [True, False, 0.0, "0", -1])
def test_intent_revisions_are_strict_nonnegative_integers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _command(**{field: value})


@pytest.mark.parametrize("value", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64])
def test_intent_approval_requires_exact_lowercase_sha256(value: str) -> None:
    with pytest.raises(ValidationError):
        _command(approval_digest=value)


@pytest.mark.parametrize("value", ["", " ", " padded", "padded ", "r" * 129])
def test_request_identity_cannot_be_blank_padded_or_unbounded(value: str) -> None:
    with pytest.raises(ValidationError):
        _command(request_id=value)


def test_intent_requests_are_frozen_and_forbid_untrusted_extra_scope() -> None:
    request = ErasureIntentRequest(message_id="user-root", counterparty_id="alice")
    with pytest.raises(ValidationError, match="frozen_instance"):
        request.__setattr__("counterparty_id", "bob")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _command(identity_id="foreign-identity")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _preview(private_selectors=("foreign-row",))


def test_blocked_inventory_remains_blocked_in_the_reviewed_intent_envelope() -> None:
    preview = _preview()
    assert preview.inventory.assessment_status == "blocked"
    assert preview.inventory.assessment_blockers == ("unknown_legacy_provenance",)
    assert preview.inventory.preview_digest != preview.approval_digest
    assert preview.operation == "record_terminal_erasure_intent"
    assert preview.physical_execution_authorized is False
    assert preview.execution_authorized is False
    with pytest.raises(ValidationError, match="frozen_instance"):
        preview.__setattr__("approval_digest", "c" * 64)


@pytest.mark.parametrize("field", ["physical_execution_authorized", "execution_authorized"])
@pytest.mark.parametrize("value", [True, 0, 1, None, "false"])
def test_preview_cannot_coerce_permission_or_accept_false_lookalikes(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError, match="erasure_intent_never_authorizes_execution"):
        _preview(**{field: value})


@pytest.mark.parametrize("field", ["physical_erasure", "execution_authorized"])
@pytest.mark.parametrize("value", [True, 0, 1, None, "false"])
def test_result_cannot_forge_erasure_completion_or_execution(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ErasureIntentResult.model_validate(
            {
                "intent": _intent(),
                "main_projection_current": False,
                "phase": "intent_committed_preparation_pending",
                field: value,
            }
        )


def test_current_preparation_cannot_certify_a_stale_main_projection() -> None:
    intent = _intent()
    with pytest.raises(ValidationError, match="erasure_preparation_cannot_be_current"):
        ErasureIntentResult(
            intent=intent,
            main_projection_current=False,
            preparation=_preparation(intent),
            preparation_current=True,
            phase="intent_committed_preparation_blocked",
        )


def test_result_cannot_attach_another_requests_owner_preparation() -> None:
    with pytest.raises(ValidationError, match="erasure_preparation_intent_mismatch"):
        ErasureIntentResult(
            intent=_intent(),
            main_projection_current=True,
            preparation=_preparation(_intent("foreign-request")),
            preparation_current=True,
            phase="intent_committed_preparation_blocked",
        )


def test_matching_blocked_preparation_never_claims_deletion() -> None:
    intent = _intent()
    result = ErasureIntentResult(
        intent=intent,
        main_projection_current=True,
        preparation=_preparation(intent),
        preparation_current=True,
        phase="intent_committed_preparation_blocked",
    )
    assert result.preparation_current is True
    assert result.preparation is not None
    assert result.preparation.assessment_status == "blocked"
    assert result.physical_erasure is False
    assert result.execution_authorized is False
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ErasureIntentResult.model_validate(
            {
                "intent": intent,
                "main_projection_current": True,
                "phase": "intent_committed_preparation_blocked",
                "raw_owner_text": "untrusted",
            }
        )
