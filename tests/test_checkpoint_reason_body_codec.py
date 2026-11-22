"""Sixth identity-only envelope preserves all five source-action formats."""

import copy
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from satori.domain.erasure_bodies import (
    BodyAction,
    action_identity,
    action_lineage,
    body_actions_digest,
    body_reference_count,
    source_root,
)
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonAction,
    CheckpointReasonKind,
    CheckpointReasonProposal,
    CheckpointReasonScope,
    CheckpointReasonSelector,
    CheckpointReasonTarget,
)
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.canonical_provenance import _validate_action_head
from satori.infrastructure.persistence.erasure_body_codec import (
    decode_body_action,
    encode_body_action,
)
from satori.infrastructure.source_policy.body_policy import proposal_references
from tests.test_erasure_appraisal_codec import _appraisal
from tests.test_erasure_body_codec import _action
from tests.test_erasure_dialogue_codec import _dialogue
from tests.test_erasure_knowledge_codec import _knowledge
from tests.test_erasure_reflection_codec import _reflection


def _reason_action(*, prior: int = 0) -> CheckpointReasonAction:
    proposal = CheckpointReasonProposal(
        CheckpointReasonScope("satori", "lineage"),
        "reason-request",
        0,
        0,
        prior,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        (
            CheckpointReasonTarget(
                CheckpointReasonSelector(CheckpointReasonKind.CHECKPOINT_APPROVAL, "approval"),
                1,
                "d" * 64,
                "e" * 64,
                2,
            ),
        ),
    )
    return CheckpointReasonAction(
        proposal,
        "reason-action",
        "floor",
        "installation",
        prior + 1,
        datetime(2026, 10, 13, tzinfo=UTC),
        "reason-trace",
    )


def test_sixth_roundtrip_never_fabricates_source_attribution_and_preserves_old_five() -> None:
    old = (_action(), _dialogue(), _knowledge(), _reflection(), _appraisal())
    assert (
        body_actions_digest(old)
        == "ecf5f17f5eaef627dcef0ffc9343961fac599ac526ffad37dfcaf858f21a2760"
    )
    for action in (*old, _reason_action(prior=5)):
        encoded = encode_body_action(action)
        assert decode_body_action(encoded) == action
        assert encode_body_action(decode_body_action(encoded)) == encoded
    action = _reason_action()
    assert action_identity(action) == "satori"
    assert action_lineage(action) == "lineage"
    assert source_root(action) is None
    encoded = encode_body_action(action)
    assert "root" not in encoded["proposal"]
    assert "intent_request_id" not in encoded["proposal"]
    assert proposal_references(action.proposal) == 3
    assert body_reference_count((*old, _reason_action(prior=5))) == 19
    assert source_root(old[0]) == old[0].proposal.root


@pytest.mark.parametrize(
    "field",
    [
        "root",
        "intent_request_id",
        "scope_missing",
        "selector_extra",
        "future_kind",
        "version",
        "boolean_count",
    ],
)
def test_unknown_identity_shape_has_no_source_fallback(field: str) -> None:
    encoded = copy.deepcopy(encode_body_action(_reason_action()))
    proposal = encoded["proposal"]
    target = proposal["targets"][0]
    if field == "root":
        proposal["root"] = encode_body_action(_action())["proposal"]["root"]
    elif field == "intent_request_id":
        proposal["intent_request_id"] = "invented-source-intent"
    elif field == "scope_missing":
        del proposal["scope"]["identity_id"]
    elif field == "selector_extra":
        target["selector"]["reason"] = "synthetic private prose"
    elif field == "future_kind":
        target["selector"]["kind"] = "generic_owner"
    elif field == "version":
        target["payload_version"] = 2
    else:
        target["metadata_reference_count"] = True
    with pytest.raises(ValueError, match=r"body_action_projection_corrupt|validation error"):
        decode_body_action(encoded)


def test_identity_action_participates_in_historical_head_without_granting_root_proof() -> None:
    action = _reason_action()
    snapshot = SourceEligibilitySnapshot(
        "floor",
        "installation",
        "lineage",
        "satori",
        0,
        frozenset(),
        body_revision=1,
        body_head_digest=body_actions_digest((action,)),
    )
    _validate_action_head((action,), snapshot)
    with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
        _validate_action_head((action,), replace(snapshot, identity_id="foreign"))
    with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
        _validate_action_head((action,), replace(snapshot, body_head_digest="9" * 64))


def test_absence_of_a_root_does_not_admit_an_unknown_action_type() -> None:
    unknown = cast(BodyAction, object())
    for helper in (action_identity, action_lineage, source_root):
        with pytest.raises(ValueError, match="unsupported_body_action"):
            helper(unknown)
