"""Strict fifth envelope, exact old body serialization and reviewed metadata bounds."""

import copy
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from satori.domain.erasure_appraisal import (
    MAX_APPRAISAL_TARGETS,
    AppraisalBodyAction,
    AppraisalBodyProposal,
    AppraisalBodyTarget,
)
from satori.domain.erasure_bodies import body_actions_digest, body_reference_count
from satori.infrastructure.persistence.erasure_body_codec import (
    decode_body_action,
    encode_body_action,
)
from satori.infrastructure.source_policy.body_policy import proposal_references
from tests.test_erasure_body_codec import _action
from tests.test_erasure_dialogue_codec import _dialogue
from tests.test_erasure_knowledge_codec import _knowledge
from tests.test_erasure_reflection_codec import _reflection


def _appraisal() -> AppraisalBodyAction:
    prior = _reflection()
    proposal = AppraisalBodyProposal(
        prior.proposal.root,
        prior.proposal.intent_request_id,
        "appraisal-request",
        1,
        1,
        4,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        (AppraisalBodyTarget("transition", "input", "audit", 1, "d" * 64, "e" * 64, 3, "f" * 64),),
    )
    return AppraisalBodyAction(
        proposal,
        "appraisal-action",
        "floor",
        "installation",
        5,
        datetime(2026, 10, 9, tzinfo=UTC),
        "appraisal-trace",
    )


def test_fifth_envelope_roundtrip_keeps_old_golden_and_reference_accounting() -> None:
    old = (_action(), _dialogue(), _knowledge(), _reflection())
    assert body_actions_digest((old[0],)) == (
        "81a4a8f4de7427ea7deb024e8b7c7b050504d7340533bf2c900285dcaf9eb7db"
    )
    for action in (*old, _appraisal()):
        encoded = encode_body_action(action)
        assert decode_body_action(encoded) == action
        assert encode_body_action(decode_body_action(encoded)) == encoded
    assert body_reference_count(old) == 12
    assert body_reference_count((*old, _appraisal())) == 16
    assert proposal_references(_appraisal().proposal) == 4


@pytest.mark.parametrize("field", ["extra", "missing", "closure_missing", "version", "policy"])
def test_unknown_or_incomplete_shape_is_never_inferred(field: str) -> None:
    value = copy.deepcopy(encode_body_action(_appraisal()))
    target = value["proposal"]["targets"][0]
    if field == "extra":
        target["payload"] = "synthetic private content"
    elif field == "missing":
        del target["metadata_reference_count"]
    elif field == "closure_missing":
        del target["input_closure_digest"]
    elif field == "version":
        target["payload_version"] = 2
    else:
        value["proposal"]["policy_version"] = "future_appraisal_policy"
    with pytest.raises(ValueError, match=r"body_action_projection_corrupt|validation error"):
        decode_body_action(value)


@pytest.mark.parametrize("value", [True, 1.0])
@pytest.mark.parametrize("field", ["payload_version", "metadata_reference_count"])
def test_reviewed_integer_proofs_do_not_coerce_boolean_or_float(
    field: str, value: bool | float
) -> None:
    encoded = encode_body_action(_appraisal())
    encoded["proposal"]["targets"][0][field] = value
    with pytest.raises(ValidationError, match="int_type"):
        decode_body_action(encoded)


@pytest.mark.parametrize("count", [0, -1, 2, 4, 1_000_001])
def test_invalid_metadata_reference_count_is_rejected(count: int) -> None:
    with pytest.raises(ValueError, match="invalid_appraisal_metadata_reference_count"):
        replace(_appraisal().proposal.targets[0], metadata_reference_count=count)


@pytest.mark.parametrize("digest", ["", "f" * 63, "G" * 64, "f" * 64 + "extra"])
def test_closure_fingerprint_requires_exact_sha256(digest: str) -> None:
    with pytest.raises(ValueError, match="input_closure_digest"):
        replace(_appraisal().proposal.targets[0], input_closure_digest=digest)
    encoded = encode_body_action(_appraisal())
    encoded["proposal"]["targets"][0]["input_closure_digest"] = digest
    with pytest.raises(ValueError, match="input_closure_digest"):
        decode_body_action(encoded)


def test_targets_are_unique_ordered_and_bound_by_exact_transition_ids() -> None:
    original = _appraisal().proposal
    target = original.targets[0]
    for invalid_targets in ((target, target), (replace(target, transition_id="z"), target)):
        with pytest.raises(ValueError, match="appraisal_targets_must_be_unique_and_ordered"):
            replace(original, targets=invalid_targets)
    targets = tuple(
        replace(target, transition_id=f"transition-{index:04}")
        for index in range(MAX_APPRAISAL_TARGETS)
    )
    assert len(replace(original, targets=targets).targets) == MAX_APPRAISAL_TARGETS
    with pytest.raises(ValueError, match="appraisal_target_capacity_exceeded"):
        replace(original, targets=(*targets, replace(target, transition_id="z")))


def test_private_target_contains_fingerprints_and_headers_without_payload_content() -> None:
    value = encode_body_action(_appraisal())
    assert set(value["proposal"]["targets"][0]) == {
        "transition_id",
        "source_input_id",
        "audit_event_id",
        "payload_version",
        "body_digest",
        "terminal_digest",
        "metadata_reference_count",
        "input_closure_digest",
    }
