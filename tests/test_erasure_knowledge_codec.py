"""Closed metadata and exact old receipts in the single versioned body stream."""

import copy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from satori.domain.erasure_bodies import body_actions_digest, body_reference_count
from satori.domain.erasure_dialogue import HistoricalEvidenceTable
from satori.domain.erasure_knowledge import (
    KnowledgeBodyAction,
    KnowledgeBodyKind,
    KnowledgeBodyProposal,
    KnowledgeBodyTarget,
    KnowledgeWitnessOrigin,
    KnowledgeWitnessTransition,
)
from satori.infrastructure.persistence.erasure_body_codec import (
    decode_body_action,
    encode_body_action,
)
from tests.test_erasure_body_codec import _action
from tests.test_erasure_dialogue_codec import _dialogue


def _knowledge() -> KnowledgeBodyAction:
    dialogue = _dialogue()
    witness = KnowledgeWitnessTransition(
        HistoricalEvidenceTable.POSITION,
        "evidence",
        dialogue.proposal.root,
        "f" * 64,
        "1" * 64,
        KnowledgeWitnessOrigin.DIALOGUE,
        dialogue.action_id,
    )
    proposal = KnowledgeBodyProposal(
        dialogue.proposal.root,
        dialogue.proposal.intent_request_id,
        "knowledge-request",
        1,
        1,
        2,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        (
            KnowledgeBodyTarget(
                KnowledgeBodyKind.POSITION, "position", 1, "d" * 64, "e" * 64, (witness,)
            ),
        ),
    )
    return KnowledgeBodyAction(
        proposal,
        "knowledge-action",
        "floor",
        "installation",
        3,
        datetime(2026, 10, 6, tzinfo=UTC),
        "knowledge-trace",
    )


def test_new_union_preserves_both_old_action_json_and_exact_golden_head() -> None:
    episode, dialogue = _action(), _dialogue()
    assert body_actions_digest((episode,)) == (
        "81a4a8f4de7427ea7deb024e8b7c7b050504d7340533bf2c900285dcaf9eb7db"
    )
    for action in (episode, dialogue, _knowledge()):
        encoded = encode_body_action(action)
        assert decode_body_action(encoded) == action
        assert encode_body_action(decode_body_action(encoded)) == encoded
    assert body_reference_count((episode, dialogue, _knowledge())) == 5


@pytest.mark.parametrize("field", ["kind", "origin", "table", "root", "extra", "missing"])
def test_unknown_or_incomplete_knowledge_shape_is_not_inferred(field: str) -> None:
    value = copy.deepcopy(encode_body_action(_knowledge()))
    target = value["proposal"]["targets"][0]
    witness = target["witness_transitions"][0]
    if field == "kind":
        target["kind"] = "arbitrary_table"
    elif field == "origin":
        witness["origin"] = "unknown"
    elif field == "table":
        witness["table"] = "user_model_claim_evidence"
    elif field == "root":
        witness["root"]["counterparty_id"] = "bob"
    elif field == "extra":
        target["arbitrary_column"] = "synthetic content"
    else:
        del witness["predecessor_action_id"]
    with pytest.raises(ValueError, match=r"body_action_projection_corrupt|validation error"):
        decode_body_action(value)


@pytest.mark.parametrize("value", [True, 1.0])
@pytest.mark.parametrize(
    "field", ["aggregate_version", "expected_body_revision", "result_body_revision"]
)
def test_numeric_action_proofs_do_not_coerce_boolean_or_float(
    field: str, value: bool | float
) -> None:
    encoded = encode_body_action(_knowledge())
    if field == "aggregate_version":
        encoded["proposal"]["targets"][0][field] = value
    elif field == "expected_body_revision":
        encoded["proposal"][field] = value
    else:
        encoded[field] = value
    with pytest.raises(ValidationError, match="int_type"):
        decode_body_action(encoded)


@pytest.mark.parametrize(
    "origin", [KnowledgeWitnessOrigin.DIALOGUE, KnowledgeWitnessOrigin.KNOWLEDGE]
)
def test_prior_origin_cannot_omit_predecessor(origin: KnowledgeWitnessOrigin) -> None:
    witness = _knowledge().proposal.targets[0].witness_transitions[0]
    with pytest.raises(ValueError, match="knowledge_witness_predecessor_required"):
        replace(witness, origin=origin, predecessor_action_id=None)


def test_live_origin_has_no_fabricated_predecessor() -> None:
    witness = _knowledge().proposal.targets[0].witness_transitions[0]
    live = replace(witness, origin=KnowledgeWitnessOrigin.LIVE, predecessor_action_id=None)
    assert live.root == witness.root
    with pytest.raises(ValueError, match="live_knowledge_witness_has_no_predecessor"):
        replace(live, predecessor_action_id="invented")


def test_target_keys_include_owner_kind_even_with_identical_aggregate_ids() -> None:
    original = _knowledge().proposal
    targets = tuple(
        KnowledgeBodyTarget(kind, "same-id", 1, "d" * 64, "e" * 64)
        for kind in sorted(KnowledgeBodyKind, key=lambda item: item.value)
    )
    assert len(replace(original, targets=targets).targets) == 5
    with pytest.raises(ValueError, match="knowledge_targets_must_be_unique_and_ordered"):
        replace(original, targets=(targets[0], targets[0]))


@pytest.mark.parametrize("count", [512, 513])
def test_transition_bound_is_exact_across_owner_targets(count: int) -> None:
    original = _knowledge().proposal
    witness = original.targets[0].witness_transitions[0]
    witnesses = tuple(replace(witness, evidence_id=f"edge-{i:04}") for i in range(count))
    if count == 513:
        with pytest.raises(ValueError, match="knowledge_witness_capacity_exceeded"):
            replace(original.targets[0], witness_transitions=witnesses)
    else:
        target = replace(original.targets[0], witness_transitions=witnesses)
        assert len(replace(original, targets=(target,)).targets[0].witness_transitions) == 512


def test_private_knowledge_action_contains_no_body_or_quote_values() -> None:
    value: dict[str, Any] = encode_body_action(_knowledge())
    assert set(value["proposal"]["targets"][0]) == {
        "kind",
        "aggregate_id",
        "aggregate_version",
        "body_digest",
        "terminal_digest",
        "witness_transitions",
    }
    assert set(value["proposal"]["targets"][0]["witness_transitions"][0]) == {
        "table",
        "evidence_id",
        "root",
        "before_digest",
        "after_digest",
        "origin",
        "predecessor_action_id",
    }
