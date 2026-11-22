"""A local projection must preserve exact numeric types of its trusted action envelope."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from satori.domain.erasure_bodies import (
    BodyErasureAction,
    BodyErasureProposal,
    BodyErasureTarget,
)
from satori.domain.source_policy import CanonicalRootRef
from satori.infrastructure.persistence.erasure_body_codec import (
    decode_body_action,
    encode_body_action,
)


def _action() -> BodyErasureAction:
    proposal = BodyErasureProposal(
        root=CanonicalRootRef("lineage", "satori", "alice", "session", "interaction", "message"),
        intent_request_id="intent-request",
        request_id="body-request",
        expected_source_generation=1,
        expected_erasure_revision=1,
        expected_body_revision=0,
        approval_digest="a" * 64,
        store_digest="b" * 64,
        schema_digest="c" * 64,
        targets=(BodyErasureTarget("memory", 1, "d" * 64, "e" * 64),),
    )
    return BodyErasureAction(
        proposal=proposal,
        action_id="action",
        floor_id="floor",
        installation_id="installation",
        result_body_revision=1,
        decided_at=datetime(2026, 10, 6, tzinfo=UTC),
        trace_id="body-trace",
    )


def test_canonical_action_roundtrip_preserves_the_exact_approved_envelope() -> None:
    action = _action()
    encoded = encode_body_action(action)
    assert decode_body_action(encoded) == action
    assert encode_body_action(decode_body_action(encoded)) == encoded


@pytest.mark.parametrize("value", [True, 1.0])
@pytest.mark.parametrize(
    "field", ["expected_source_generation", "result_body_revision", "aggregate_version"]
)
def test_bool_and_float_cannot_impersonate_integer_action_proofs(
    field: str, value: bool | float
) -> None:
    encoded = encode_body_action(_action())
    if field == "expected_source_generation":
        encoded["proposal"][field] = value
    elif field == "aggregate_version":
        encoded["proposal"]["targets"][0][field] = value
    else:
        encoded[field] = value
    with pytest.raises(ValidationError, match="int_type"):
        decode_body_action(encoded)
