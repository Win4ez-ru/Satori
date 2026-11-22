"""Strict policy-discriminated private receipts preserve the old episode JSON and head."""

import copy
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from satori.domain.erasure_bodies import (
    BodyErasureAction,
    body_actions_digest,
    body_reference_count,
)
from satori.domain.erasure_dialogue import (
    DialogueBodyAction,
    DialogueBodyProposal,
    DialogueBodyTarget,
    HistoricalEvidenceTable,
    HistoricalEvidenceWitness,
)
from satori.infrastructure.persistence.erasure_body_codec import (
    decode_body_action,
    decode_body_projection_rows,
    encode_body_action,
)
from tests.test_erasure_body_codec import _action


def _dialogue() -> DialogueBodyAction:
    episode = _action()
    original = episode.proposal
    proposal = DialogueBodyProposal(
        original.root,
        original.intent_request_id,
        "dialogue-request",
        1,
        1,
        1,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        (
            DialogueBodyTarget(
                original.root.message_id,
                1,
                "d" * 64,
                "e" * 64,
                (
                    HistoricalEvidenceWitness(
                        HistoricalEvidenceTable.POSITION, "evidence", "f" * 64
                    ),
                ),
            ),
        ),
    )
    return DialogueBodyAction(
        proposal,
        "dialogue-action",
        "floor",
        "installation",
        2,
        datetime(2026, 10, 6, tzinfo=UTC),
        "dialogue-trace",
    )


def test_old_episode_json_and_golden_whole_head_hash_are_unchanged() -> None:
    episode = _action()
    original = TypeAdapter(BodyErasureAction).dump_python(episode, mode="json")
    assert encode_body_action(episode) == original
    assert (
        body_actions_digest((episode,))
        == "81a4a8f4de7427ea7deb024e8b7c7b050504d7340533bf2c900285dcaf9eb7db"
    )
    assert decode_body_action(original) == episode


def test_dialogue_roundtrip_preserves_closed_witnesses_and_changed_witness_changes_head() -> None:
    dialogue = _dialogue()
    encoded = encode_body_action(dialogue)
    assert encoded["proposal"]["targets"][0]["witnesses"][0]["table"] == "satori_position_evidence"
    assert decode_body_action(encoded) == dialogue
    assert encode_body_action(decode_body_action(encoded)) == encoded
    target = dialogue.proposal.targets[0]
    changed = replace(
        dialogue,
        proposal=replace(
            dialogue.proposal,
            targets=(
                replace(
                    target,
                    witnesses=(replace(target.witnesses[0], digest="9" * 64),),
                ),
            ),
        ),
    )
    assert body_actions_digest((_action(), dialogue)) != body_actions_digest((_action(), changed))


@pytest.mark.parametrize("value", [True, 1.0])
@pytest.mark.parametrize(
    "field", ["expected_body_revision", "result_body_revision", "schema_version"]
)
def test_dialogue_integer_proofs_reject_bool_and_float(field: str, value: bool | float) -> None:
    encoded = encode_body_action(_dialogue())
    if field == "expected_body_revision":
        encoded["proposal"][field] = value
    elif field == "schema_version":
        encoded["proposal"]["targets"][0][field] = value
    else:
        encoded[field] = value
    with pytest.raises(ValidationError, match="int_type"):
        decode_body_action(encoded)


@pytest.mark.parametrize(
    "alteration",
    ["unknown_policy", "policy_swap", "extra_field", "unknown_table", "missing_witnesses"],
)
def test_codec_cannot_infer_or_coerce_a_different_policy(alteration: str) -> None:
    encoded = encode_body_action(_dialogue())
    if alteration == "unknown_policy":
        encoded["proposal"]["policy_version"] = "future_policy"
    elif alteration == "policy_swap":
        encoded["proposal"]["policy_version"] = "episode_body_erasure_v1"
    elif alteration == "extra_field":
        encoded["proposal"]["targets"][0]["unapproved"] = "value"
    elif alteration == "unknown_table":
        encoded["proposal"]["targets"][0]["witnesses"][0]["table"] = "arbitrary_table"
    else:
        del encoded["proposal"]["targets"][0]["witnesses"]
    with pytest.raises(ValueError, match=r"body_action_projection_corrupt|validation error"):
        decode_body_action(encoded)


def test_omitted_optional_field_is_not_an_equivalent_approved_envelope() -> None:
    encoded = copy.deepcopy(encode_body_action(_dialogue()))
    del encoded["approved_by"]
    with pytest.raises(ValueError, match="body_action_projection_corrupt"):
        decode_body_action(encoded)


def test_witnesses_are_bound_to_original_user_root_not_an_assistant_target() -> None:
    dialogue = _dialogue()
    target = replace(dialogue.proposal.targets[0], message_id="assistant-message")
    with pytest.raises(ValueError, match="dialogue_witness_root_mismatch"):
        replace(dialogue.proposal, targets=(target,))


@pytest.mark.parametrize("count", [512, 513])
def test_witness_bound_is_exact_and_keeps_structured_proofs(count: int) -> None:
    witnesses = tuple(
        HistoricalEvidenceWitness(
            HistoricalEvidenceTable.POSITION, f"evidence-{index:04}", "f" * 64
        )
        for index in range(count)
    )
    if count == 513:
        with pytest.raises(ValueError, match="dialogue_witness_capacity_exceeded"):
            DialogueBodyTarget("message", 1, "d" * 64, "e" * 64, witnesses)
    else:
        target = DialogueBodyTarget("message", 1, "d" * 64, "e" * 64, witnesses)
        assert len(target.witnesses) == 512


@pytest.mark.parametrize("value", ["arbitrary_table", "memory_evidence"])
def test_domain_does_not_accept_untyped_table_names(value: str) -> None:
    with pytest.raises(ValueError, match="invalid_historical_evidence_table"):
        HistoricalEvidenceWitness(value, "evidence", "a" * 64)  # type: ignore[arg-type]


def test_private_receipt_has_no_raw_body_or_quote_fields() -> None:
    encoded: dict[str, Any] = encode_body_action(_dialogue())
    assert set(encoded["proposal"]["targets"][0]) == {
        "message_id",
        "schema_version",
        "body_digest",
        "terminal_digest",
        "witnesses",
    }
    assert set(encoded["proposal"]["targets"][0]["witnesses"][0]) == {
        "table",
        "evidence_id",
        "digest",
    }


def test_shared_work_count_includes_old_approvals_and_dialogue_witnesses() -> None:
    episode, dialogue = _action(), _dialogue()
    assert body_reference_count((episode, dialogue)) == 3
    assert body_reference_count((episode, dialogue, episode)) == 4


@pytest.mark.parametrize("value", [None, [], "synthetic private body", 1])
def test_malformed_stored_json_top_level_is_a_content_free_boundary_error(value: object) -> None:
    with pytest.raises(ValueError, match=r"^body_action_projection_corrupt$"):
        decode_body_action(value)  # type: ignore[arg-type]


def test_projection_rows_decode_both_json_storage_shapes_in_revision_order() -> None:
    episode, dialogue = _action(), _dialogue()
    rows = (
        {"body_revision": 2, "action": json.dumps(encode_body_action(dialogue))},
        {"body_revision": 1, "action": encode_body_action(episode)},
    )
    assert decode_body_projection_rows(iter(rows)) == (episode, dialogue)


@pytest.mark.parametrize("raw", [None, [], "not JSON", "[]", "null", {"proposal": "private"}])
def test_projection_row_malformed_action_has_only_closed_reason(raw: object) -> None:
    with pytest.raises(ValueError, match=r"^body_action_projection_corrupt$"):
        decode_body_projection_rows(({"body_revision": 1, "action": raw},))


@pytest.mark.parametrize("revision", [None, True, 1.0, "1", 0, -1])
def test_projection_row_revision_shape_never_coerces(revision: object) -> None:
    with pytest.raises(ValueError, match=r"^body_action_projection_corrupt$"):
        decode_body_projection_rows(
            ({"body_revision": revision, "action": encode_body_action(_action())},)
        )


def test_projection_parser_does_not_claim_complete_floor_head_authority() -> None:
    # The separate mirror validator must reject missing revisions or a different trusted head.
    episode = _action()
    assert decode_body_projection_rows(
        ({"body_revision": 7, "action": encode_body_action(episode)},)
    ) == (episode,)


def test_deeply_nested_stored_json_action_has_only_closed_reason() -> None:
    depth = sys.getrecursionlimit() + 100
    raw = "[" * depth + '"synthetic private content"' + "]" * depth
    with pytest.raises(ValueError, match=r"^body_action_projection_corrupt$"):
        decode_body_projection_rows(({"body_revision": 1, "action": raw},))


def test_deeply_nested_in_memory_action_cannot_escape_via_json_encoder() -> None:
    encoded = encode_body_action(_dialogue())
    nested: list[Any] = ["synthetic private content"]
    for _ in range(sys.getrecursionlimit() + 100):
        nested = [nested]
    encoded["unapproved_extra"] = nested
    with pytest.raises(ValueError, match=r"^body_action_projection_corrupt$"):
        decode_body_action(encoded)
