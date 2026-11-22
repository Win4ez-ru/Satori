"""Strict fourth envelope, exact old body serialization and reviewed metadata bounds."""

import copy
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from satori.domain.erasure_bodies import body_actions_digest, body_reference_count
from satori.domain.erasure_reflection import (
    MAX_REFLECTION_TARGETS,
    ReflectionBodyAction,
    ReflectionBodyProposal,
    ReflectionBodyTarget,
)
from satori.infrastructure.persistence.erasure_body_codec import (
    decode_body_action,
    encode_body_action,
)
from satori.infrastructure.source_policy.body_policy import proposal_references
from tests.test_erasure_body_codec import _action
from tests.test_erasure_dialogue_codec import _dialogue
from tests.test_erasure_knowledge_codec import _knowledge


def _reflection() -> ReflectionBodyAction:
    prior = _knowledge()
    proposal = ReflectionBodyProposal(
        prior.proposal.root,
        prior.proposal.intent_request_id,
        "reflection-request",
        1,
        1,
        3,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        (ReflectionBodyTarget("proposal", "run", 2, 1, "d" * 64, "e" * 64, 6),),
    )
    return ReflectionBodyAction(
        proposal,
        "reflection-action",
        "floor",
        "installation",
        4,
        datetime(2026, 10, 9, tzinfo=UTC),
        "reflection-trace",
    )


def test_fourth_envelope_roundtrip_keeps_old_golden_and_reference_accounting() -> None:
    old = (_action(), _dialogue(), _knowledge())
    assert body_actions_digest((old[0],)) == (
        "81a4a8f4de7427ea7deb024e8b7c7b050504d7340533bf2c900285dcaf9eb7db"
    )
    for action in (*old, _reflection()):
        encoded = encode_body_action(action)
        assert decode_body_action(encoded) == action
        assert encode_body_action(decode_body_action(encoded)) == encoded
    assert body_reference_count(old) == 5
    assert body_reference_count((*old, _reflection())) == 12
    assert proposal_references(_reflection().proposal) == 7


@pytest.mark.parametrize("field", ["extra", "missing", "version", "policy"])
def test_unknown_or_incomplete_shape_is_never_inferred(field: str) -> None:
    value = copy.deepcopy(encode_body_action(_reflection()))
    target = value["proposal"]["targets"][0]
    if field == "extra":
        target["payload"] = "synthetic private content"
    elif field == "missing":
        del target["metadata_reference_count"]
    elif field == "version":
        target["payload_version"] = 2
    else:
        value["proposal"]["policy_version"] = "future_reflection_policy"
    with pytest.raises(ValueError, match=r"body_action_projection_corrupt|validation error"):
        decode_body_action(value)


@pytest.mark.parametrize("value", [True, 1.0])
@pytest.mark.parametrize("field", ["run_version", "payload_version", "metadata_reference_count"])
def test_reviewed_integer_proofs_do_not_coerce_boolean_or_float(
    field: str, value: bool | float
) -> None:
    encoded = encode_body_action(_reflection())
    encoded["proposal"]["targets"][0][field] = value
    with pytest.raises(ValidationError, match="int_type"):
        decode_body_action(encoded)


@pytest.mark.parametrize("count", [0, -1, 1_000_001])
def test_invalid_metadata_reference_count_is_rejected(count: int) -> None:
    with pytest.raises(ValueError, match="reflection_metadata_capacity_exceeded"):
        replace(_reflection().proposal.targets[0], metadata_reference_count=count)


def test_targets_are_unique_ordered_and_bound_by_exact_proposal_ids() -> None:
    original = _reflection().proposal
    target = original.targets[0]
    for invalid_targets in ((target, target), (replace(target, proposal_id="z"), target)):
        with pytest.raises(ValueError, match="reflection_targets_must_be_unique_and_ordered"):
            replace(original, targets=invalid_targets)
    targets = tuple(
        replace(target, proposal_id=f"proposal-{index:04}")
        for index in range(MAX_REFLECTION_TARGETS)
    )
    assert len(replace(original, targets=targets).targets) == MAX_REFLECTION_TARGETS
    with pytest.raises(ValueError, match="reflection_target_capacity_exceeded"):
        replace(original, targets=(*targets, replace(target, proposal_id="z")))


def test_private_target_contains_fingerprints_and_headers_without_payload_content() -> None:
    value = encode_body_action(_reflection())
    assert set(value["proposal"]["targets"][0]) == {
        "proposal_id",
        "run_id",
        "run_version",
        "payload_version",
        "body_digest",
        "terminal_digest",
        "metadata_reference_count",
    }
