"""Actual-input closure selects uncited sources and never guesses legacy causality."""

import json

import pytest

from satori.infrastructure.persistence.erasure_appraisal import AppraisalInputClosure
from tests.appraisal_material_support import (
    ROOT,
    add_exchange,
    add_reply_receipt,
    appraisal_store,
    historical,
    set_actual_input,
)


def test_uncited_prior_root_is_selected_through_full_actual_reply_dependency() -> None:
    store = appraisal_store()
    add_exchange(store)
    add_reply_receipt(store, root_ids=("message", "message-b"))
    row = store["affective_transitions"][0]
    row.update(
        source_message_id="message-b",
        interaction_id="interaction-b",
        source_refs='["interaction-b"]',
    )
    set_actual_input(store, roots=("message-b",), refs=(("assistant_reply", "interaction-b", 1),))
    closure = AppraisalInputClosure.build(store, "identity", historical(store))
    valid, affected, people = closure.prove(row, ROOT)
    assert valid
    assert affected
    assert people == {"alice"}
    assert "message" not in json.loads(str(row["source_refs"]))


def test_fully_proven_independent_counterparty_input_is_not_selected() -> None:
    store = appraisal_store()
    add_exchange(store, counterparty="bob")
    row = store["affective_transitions"][0]
    row.update(source_message_id="message-b", interaction_id="interaction-b")
    set_actual_input(store, roots=("message-b",))
    valid, affected, people = AppraisalInputClosure.build(
        store, "identity", historical(store)
    ).prove(row, ROOT)
    assert valid
    assert not affected
    assert people == {"bob"}


def test_cross_counterparty_parent_chain_cannot_become_same_scope_authority() -> None:
    store = appraisal_store()
    add_exchange(store, counterparty="bob")
    add_reply_receipt(store, root_ids=("message", "message-b"))
    set_actual_input(store, roots=("message",), refs=(("assistant_reply", "interaction-b", 1),))
    valid, affected, people = AppraisalInputClosure.build(
        store, "identity", historical(store)
    ).prove(store["affective_transitions"][0], ROOT)
    assert not valid
    assert affected
    assert people == {"alice", "bob"}


@pytest.mark.parametrize(
    "change",
    ["legacy", "unknown", "missing-ref", "cycle", "malformed", "unverified-scope", "wrong-schema"],
)
def test_incomplete_or_unverified_actual_input_remains_unknown(change: str) -> None:
    store = appraisal_store()
    add_exchange(store)
    add_reply_receipt(store, root_ids=("message",))
    set_actual_input(store, roots=("message",), refs=(("assistant_reply", "interaction-b", 1),))
    if change == "legacy":
        store["source_inputs"][0]["legacy"] = 1
    elif change == "unknown":
        store["source_payload_receipts"][0]["proof_status"] = "unknown"
    elif change == "missing-ref":
        set_actual_input(store, roots=("message",), refs=(("assistant_reply", "missing", 1),))
    elif change == "cycle":
        store["source_payload_receipts"][0]["payload_refs"] = (
            '[{"kind":"assistant_reply","payload_id":"interaction-b","version":1}]'
        )
    elif change == "malformed":
        store["source_inputs"][0]["payload_refs"] = (
            '[{"kind":"episode","kind":"assistant_reply","payload_id":"interaction-b","version":1}]'
        )
    elif change == "unverified-scope":
        store["conversation_sessions"][0]["scope_verified"] = 0
    else:
        store["conversation_interactions"][0]["schema_version"] = 2
    valid, _affected, _people = AppraisalInputClosure.build(
        store, "identity", historical(store)
    ).prove(store["affective_transitions"][0], ROOT)
    assert not valid
