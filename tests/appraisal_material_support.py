"""Closed synthetic rows for internal appraisal proof tests; no runtime grants."""

import json
from datetime import UTC, datetime

from satori.domain.affect import FastAffectiveState, MoodState
from satori.domain.affect_history import APPRAISAL_FIELDS
from satori.domain.erasure_appraisal import (
    AppraisalBodyAction,
    AppraisalBodyProposal,
    AppraisalBodyTarget,
)
from satori.domain.source_policy import CanonicalRootRef, SourceEligibilitySnapshot
from satori.infrastructure.persistence.appraisal_material import AppraisalMaterialIndex
from satori.infrastructure.persistence.canonical_provenance import HistoricalProvenance
from satori.infrastructure.persistence.erasure_inventory import PAYLOAD_TABLES, Row, Store

TIME = "2026-10-10 00:00:00.000000"
ROOT = CanonicalRootRef("lineage", "identity", "alice", "session", "interaction", "message")


def appraisal_store() -> Store:
    store: Store = {table: () for table, _key, _version in PAYLOAD_TABLES.values()}
    store.update(
        {
            table: ()
            for table in (
                "memory_evidence",
                "semantic_claim_evidence",
                "user_model_claim_evidence",
                "world_model_claim_evidence",
                "satori_position_evidence",
                "satori_inclination_evidence",
                "reflection_sources",
                "reflection_runs",
                "source_payload_receipts",
                "erasure_body_applications",
            )
        }
    )
    store["conversation_sessions"] = (
        {
            "session_id": "session",
            "identity_id": "identity",
            "counterparty_id": "alice",
            "scope_verified": 1,
            "schema_version": 1,
        },
    )
    store["conversation_interactions"] = (
        {
            "interaction_id": "interaction",
            "session_id": "session",
            "status": "completed",
            "schema_version": 1,
        },
    )
    store["conversation_messages"] = (
        {
            "message_id": "message",
            "interaction_id": "interaction",
            "session_id": "session",
            "schema_version": 1,
            "role": "user",
            "sequence": 1,
            "content": "SYNTHETIC_CANONICAL_BODY",
            "body_state": "available",
            "body_action_id": None,
        },
    )
    snapshot: Row = {
        "identity_id": "identity",
        "schema_version": 1,
        "state_version": 1,
        "mood_version": 1,
        "as_of": "2026-10-10T00:00:00+00:00",
        "emotion_policy_version": 1,
        "appraisal_schema_version": 1,
        "mood_policy_version": 1,
        "fast": {name: 0.0 for name in FastAffectiveState.field_names()},
        "mood": {name: 0.0 for name in MoodState.field_names()},
    }
    row: Row = {
        "transition_id": "transition",
        "identity_id": "identity",
        "interaction_id": "interaction",
        "source_message_id": "message",
        "trace_id": "trace",
        "appraisal_schema_version": 1,
        "emotion_policy_version": 1,
        "mood_policy_version": 1,
        "base_state_version": 1,
        "resulting_state_version": 2,
        "base_mood_version": 1,
        "resulting_mood_version": 2,
        "appraised_at": TIME,
        "committed_at": TIME,
        "appraisal_confidence": 0.75,
        "appraisal_payload": json.dumps({name: 0.25 for name in APPRAISAL_FIELDS}),
        "source_refs": '["interaction"]',
        "reason_codes": '["SYNTHETIC_REASON"]',
        "applied_delta": json.dumps({name: 0.0 for name in FastAffectiveState.field_names()}),
        "mood_delta": json.dumps({name: 0.0 for name in MoodState.field_names()}),
        "state_before": json.dumps(snapshot),
        "state_after": json.dumps(dict(snapshot, state_version=2, mood_version=2)),
        "provider": "fake",
        "model": "synthetic",
        "appraisal_method": "provider",
        "explanation_state": "available",
        "explanation_action_id": None,
        "source_input_id": "input",
        "audit_event_id": "audit",
    }
    store["affective_transitions"] = (row,)
    store["source_inputs"] = (
        {
            "input_id": "input",
            "identity_id": "identity",
            "policy_generation": 0,
            "proof_status": "complete",
            "legacy": 0,
            "root_message_ids": '["message"]',
            "payload_refs": "[]",
        },
    )
    details = {
        name: row[name]
        for name in (
            "transition_id",
            "interaction_id",
            "source_message_id",
            "base_state_version",
            "resulting_state_version",
            "base_mood_version",
            "resulting_mood_version",
            "emotion_policy_version",
            "appraisal_schema_version",
            "mood_policy_version",
        )
    }
    details.update(
        source_refs=["interaction"],
        decision_reason_code="bounded_appraisal_applied",
        appraisal_reason_codes=["SYNTHETIC_REASON"],
    )
    store["audit_events"] = (
        {
            "event_id": "audit",
            "schema_version": 1,
            "event_type": "emotion.transition_applied",
            "aggregate_type": "affective_state",
            "aggregate_id": "identity",
            "trace_id": "trace",
            "occurred_at": TIME,
            "details": json.dumps(details),
        },
    )
    return store


def historical(_store: Store) -> HistoricalProvenance:
    return HistoricalProvenance(frozenset(), {})


def appraisal_action(store: Store) -> AppraisalBodyAction:
    index = AppraisalMaterialIndex.build(store)
    target = AppraisalBodyTarget(
        "transition",
        "input",
        "audit",
        1,
        index.digest("transition"),
        index.digest("transition", terminal=True),
        3,
        index.input_closure_digest("transition"),
    )
    return AppraisalBodyAction(
        AppraisalBodyProposal(
            ROOT, "intent", "request", 1, 1, 0, "a" * 64, "b" * 64, "c" * 64, (target,)
        ),
        "action",
        "floor",
        "installation",
        1,
        datetime(2026, 10, 10, tzinfo=UTC),
        "operator-trace",
    )


def appraisal_snapshot() -> SourceEligibilitySnapshot:
    return SourceEligibilitySnapshot(
        "floor", "installation", "lineage", "identity", 1, frozenset({"message"})
    )


def erase_pair(store: Store, *, action_id: str = "action") -> None:
    row = store["affective_transitions"][0]
    row.update(explanation_state="erased", explanation_action_id=action_id, reason_codes=None)
    details = json.loads(str(store["audit_events"][0]["details"]))
    del details["appraisal_reason_codes"]
    store["audit_events"][0]["details"] = json.dumps(details)


def add_exchange(store: Store, *, counterparty: str = "alice") -> None:
    store["conversation_sessions"] += (
        {
            "session_id": "session-b",
            "identity_id": "identity",
            "counterparty_id": counterparty,
            "scope_verified": 1,
            "schema_version": 1,
        },
    )
    store["conversation_interactions"] += (
        {
            "interaction_id": "interaction-b",
            "session_id": "session-b",
            "status": "completed",
            "schema_version": 1,
        },
    )
    store["conversation_messages"] += (
        {
            "message_id": "message-b",
            "interaction_id": "interaction-b",
            "session_id": "session-b",
            "schema_version": 1,
            "role": "user",
            "sequence": 1,
            "content": "SYNTHETIC_OTHER_BODY",
            "body_state": "available",
            "body_action_id": None,
        },
        {
            "message_id": "reply-b",
            "interaction_id": "interaction-b",
            "session_id": "session-b",
            "schema_version": 1,
            "role": "assistant",
            "sequence": 2,
            "content": "SYNTHETIC_REPLY",
            "body_state": "available",
            "body_action_id": None,
        },
    )


def set_actual_input(
    store: Store, *, roots: tuple[str, ...], refs: tuple[tuple[str, str, int], ...] = ()
) -> None:
    store["source_inputs"][0]["root_message_ids"] = json.dumps(roots)
    store["source_inputs"][0]["payload_refs"] = json.dumps(
        [{"kind": kind, "payload_id": key, "version": version} for kind, key, version in refs]
    )


def add_reply_receipt(
    store: Store, *, root_ids: tuple[str, ...], parents: tuple[tuple[str, str, int], ...] = ()
) -> None:
    store["source_payload_receipts"] += (
        {
            "kind": "assistant_reply",
            "payload_id": "interaction-b",
            "payload_version": 1,
            "identity_id": "identity",
            "policy_generation": 0,
            "proof_status": "complete",
            "legacy": 0,
            "root_message_ids": json.dumps(root_ids),
            "payload_refs": json.dumps(
                [
                    {"kind": kind, "payload_id": key, "version": version}
                    for kind, key, version in parents
                ]
            ),
            "eligible": 1,
        },
    )
