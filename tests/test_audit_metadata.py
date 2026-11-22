"""Closed raw event classification: a matching name or seal cannot replace owner proof."""

import copy
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from satori.domain.audit_metadata import (
    AUDIT_METADATA_FIELDS_BY_FAMILY,
)
from satori.domain.audit_metadata import (
    AuditMetadataCoverageStatus as Status,
)
from satori.domain.audit_metadata import (
    AuditMetadataFamily as Family,
)
from satori.domain.audit_metadata import (
    AuditMetadataUnknownReason as Reason,
)
from satori.infrastructure.persistence.audit_metadata import AuditMetadataIndex
from satori.infrastructure.persistence.audit_metadata_schema import (
    MAX_AUDIT_DEPTH,
    MAX_AUDIT_NODES,
    document,
    event_digest,
    validate_event_shape,
)
from satori.infrastructure.persistence.erasure_inventory import ProvenOwners, Row, Store


def activation_store() -> Store:
    row = {
        "event_id": "activation-audit",
        "schema_version": 1,
        "event_type": "satori.activation",
        "aggregate_type": "identity",
        "aggregate_id": "identity",
        "trace_id": "activation-trace",
        "occurred_at": "2026-10-13 00:00:00.000000",
        "details": json.dumps(
            {"seed_id": "seed", "seed_schema_version": 1, "seed_content_hash": "a" * 64}
        ),
    }
    binding: Row = {
        str(slot): None
        for slot in set(AUDIT_METADATA_FIELDS_BY_FAMILY.values())
        if slot is not None
    }
    binding.update(
        event_id=row["event_id"],
        identity_id="identity",
        contract_version=1,
        family="activation",
        event_digest=event_digest(row),
        source_input_id=None,
    )
    return {
        "audit_events": (row,),
        "audit_metadata_bindings": (binding,),
        "satori_identities": (
            {
                "identity_id": "identity",
                "seed_id": "seed",
                "seed_schema_version": 1,
                "seed_content_hash": "a" * 64,
                "activation_time": "2026-10-13 00:00:00.000000",
            },
        ),
    }


def test_activation_requires_prospective_seal_and_exact_owner_provenance() -> None:
    store = activation_store()
    before = copy.deepcopy(store)
    index = AuditMetadataIndex.build(store)
    assert index.coverage.certified_metadata_events == 1
    assert index.unknown_binding_rows == 0
    assert store == before
    store["audit_metadata_bindings"] = ()
    legacy = AuditMetadataIndex.build(store)
    assert legacy.coverage.unknown_events == 1
    assert legacy.classifications[0].reason is Reason.MISSING_BINDING


@pytest.mark.parametrize(
    "mutation",
    ["extra-prose", "bool-version", "unsupported-version", "duplicate-key", "NaN", "deep-json"],
)
def test_closed_json_rejects_prose_duplicates_coercion_and_unsupported_versions(
    mutation: str,
) -> None:
    row = activation_store()["audit_events"][0]
    details = json.loads(str(row["details"]))
    if mutation == "extra-prose":
        details["private_reason"] = "SYNTHETIC_SECRET"
    elif mutation == "bool-version":
        details["seed_schema_version"] = True
    elif mutation == "unsupported-version":
        details["seed_schema_version"] = 2
    elif mutation == "duplicate-key":
        row["details"] = str(row["details"])[:-1] + ',"seed_id":"seed"}'
    elif mutation == "NaN":
        row["details"] = (
            '{"seed_id":"seed","seed_schema_version":NaN,"seed_content_hash":"' + "a" * 64 + '"}'
        )
    else:
        row["details"] = "[" * 2000 + "0" + "]" * 2000
    if mutation in {"extra-prose", "bool-version", "unsupported-version"}:
        row["details"] = json.dumps(details)
    with pytest.raises(ValueError, match="audit_metadata_invalid"):
        validate_event_shape(row, Family.ACTIVATION)


@pytest.mark.parametrize(
    "mutation",
    [
        "bad-seal",
        "wrong-owner",
        "wrong-time",
        "extra-slot",
        "unsupported-contract",
        "orphan-binding",
        "unsealed-copy",
    ],
)
def test_false_marker_or_changed_owner_never_certifies_metadata(mutation: str) -> None:
    store = activation_store()
    row, binding = store["audit_events"][0], store["audit_metadata_bindings"][0]
    if mutation == "bad-seal":
        binding["event_digest"] = "f" * 64
    elif mutation == "wrong-owner":
        binding["identity_id"] = "foreign"
    elif mutation == "wrong-time":
        store["satori_identities"][0]["activation_time"] = "2026-10-14 00:00:00.000000"
    elif mutation == "extra-slot":
        binding["episode_decision_id"] = "foreign-decision"
    elif mutation == "unsupported-contract":
        binding["contract_version"] = 2
    elif mutation == "orphan-binding":
        binding["event_id"] = "missing-event"
    else:
        store["audit_events"] += (dict(row, event_id="unsealed-copy", schema_version=2),)
    result = AuditMetadataIndex.build(store)
    assert result.coverage.certified_metadata_events == 0
    assert result.unknown_binding_rows == 1


def test_header_digest_normalizes_only_timestamp_representation() -> None:
    row = activation_store()["audit_events"][0]
    sql_digest = event_digest(row)
    row["occurred_at"] = datetime(2026, 10, 13, 3, tzinfo=UTC) + timedelta(hours=-3)
    assert event_digest(row) == sql_digest
    row["trace_id"] = "changed-trace"
    assert event_digest(row) != sql_digest


def test_unknown_report_contains_only_counts_and_closed_enums() -> None:
    store = activation_store()
    store["audit_events"][0]["event_type"] = "SYNTHETIC_SECRET"
    result = AuditMetadataIndex.build(store)
    payload = json.dumps(asdict(result.coverage))
    assert "SYNTHETIC_SECRET" not in payload
    assert result.classifications[0].status is Status.UNKNOWN


@pytest.mark.parametrize("encoded", [False, True])
def test_document_enforces_depth_and_node_limits_below_parser_recursion(encoded: bool) -> None:
    nested: object = 0
    for _ in range(MAX_AUDIT_DEPTH + 1):
        nested = [nested]
    for value in (nested, [0] * MAX_AUDIT_NODES):
        with pytest.raises(ValueError, match="audit_metadata_invalid"):
            document(json.dumps(value) if encoded else value)
    assert document([0] * (MAX_AUDIT_NODES - 1)) == [0] * (MAX_AUDIT_NODES - 1)


def _two_identity_store() -> Store:
    from tests.appraisal_material_support import appraisal_store

    store = appraisal_store()
    store["affective_transitions"] = ()
    store["source_inputs"] = ()
    store["audit_events"] = ()
    store["audit_metadata_bindings"] = ()
    store["episode_formation_decisions"] = ()
    for name in ("conversation_sessions", "conversation_interactions", "conversation_messages"):
        original = store[name][0]
        store[name] = tuple(
            {
                key: value + suffix if key.endswith("_id") and isinstance(value, str) else value
                for key, value in original.items()
            }
            for suffix in ("-a", "-b")
        )
    for suffix in ("-a", "-b"):
        details = {
            "decision_id": "decision" + suffix,
            "source_interaction_id": "interaction" + suffix,
            "formation_version": 1,
            "policy_version": 1,
            "reason_code": "provider_selected_skip",
        }
        event: Row = {
            "event_id": "audit" + suffix,
            "schema_version": 1,
            "event_type": "memory.episode_skipped",
            "aggregate_type": "interaction",
            "aggregate_id": "interaction" + suffix,
            "trace_id": "trace" + suffix,
            "occurred_at": "2026-10-13 00:00:00.000000",
            "details": json.dumps(details),
        }
        binding: Row = {
            str(slot): None
            for slot in set(AUDIT_METADATA_FIELDS_BY_FAMILY.values())
            if slot is not None
        }
        binding.update(
            event_id=event["event_id"],
            identity_id="identity" + suffix,
            contract_version=1,
            family="episode_formation",
            event_digest=event_digest(event),
            episode_decision_id=details["decision_id"],
            source_input_id=None,
        )
        store["audit_events"] += (event,)
        store["audit_metadata_bindings"] += (binding,)
        store["episode_formation_decisions"] += (
            dict(details, kind="skipped", memory_id=None, decided_at=event["occurred_at"]),
        )
    return store


def test_multi_identity_work_budget_denies_before_second_whole_store_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import satori.infrastructure.persistence.audit_metadata as implementation

    store = _two_identity_store()
    actual = ProvenOwners
    constructed: list[str] = []

    def observe(*args: object, **kwargs: object) -> object:
        constructed.append(str(args[1]))
        return actual(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(implementation, "ProvenOwners", observe)
    monkeypatch.setattr(implementation, "MAX_AUDIT_WORK", 320)
    result = AuditMetadataIndex.build(store)
    assert result.classifications[0].status is Status.CERTIFIED_METADATA
    assert result.classifications[1].reason is Reason.CAPACITY_EXCEEDED
    assert len(constructed) == 1
    assert result.unknown_binding_rows == 1


@pytest.mark.parametrize("foreign_counterparty", [False, True])
def test_explicit_input_requires_uncited_transitive_roots_in_actual_owner_scope(
    foreign_counterparty: bool,
) -> None:
    store = _two_identity_store()
    store["conversation_sessions"][1]["identity_id"] = "identity-a"
    if not foreign_counterparty:
        store["conversation_sessions"][1]["counterparty_id"] = "alice-a"
    store["audit_metadata_bindings"][0]["source_input_id"] = "actual-input"
    store["source_inputs"] = (
        {
            "input_id": "actual-input",
            "identity_id": "identity-a",
            "proof_status": "complete",
            "legacy": 0,
            "policy_generation": 0,
            "root_message_ids": '["message-a"]',
            "payload_refs": '[{"kind":"assistant_reply","payload_id":"interaction-b","version":1}]',
        },
    )
    store["source_payload_receipts"] = (
        {
            "kind": "assistant_reply",
            "payload_id": "interaction-b",
            "payload_version": 1,
            "identity_id": "identity-a",
            "proof_status": "complete",
            "legacy": 0,
            "root_message_ids": '["message-b"]',
            "payload_refs": "[]",
            "eligible": False,
            "policy_generation": 99,
        },
    )
    result = AuditMetadataIndex.build(store)
    if foreign_counterparty:
        assert result.classifications[0].reason is Reason.SCOPE_UNPROVEN
    else:
        assert result.classifications[0].status is Status.CERTIFIED_METADATA
        # Historical policy projection flags are excluded from immutable ancestry.
        store["source_payload_receipts"][0]["eligible"] = True
        assert AuditMetadataIndex.build(store).classifications[0] == result.classifications[0]


def test_withdrawal_seal_cannot_supply_missing_target_scope_or_canonical_evidence() -> None:
    store = activation_store()
    event, binding = store["audit_events"][0], store["audit_metadata_bindings"][0]
    details = {
        "owner": "semantic_memory",
        "aggregate_id": "unattributed-claim",
        "request_id": "withdrawal-request",
        "policy_generation": 1,
        "receipt_id": "withdrawal-receipt",
        "before_version": 1,
        "after_version": 2,
    }
    event.update(
        event_type="source_support_withdrawn",
        aggregate_type="semantic_memory",
        aggregate_id=details["aggregate_id"],
        trace_id=details["request_id"],
        details=json.dumps(details),
    )
    binding.update(
        family="source_withdrawal",
        withdrawal_receipt_id=details["receipt_id"],
        event_digest=event_digest(event),
    )
    store["owner_source_withdrawals"] = (
        dict(details, recorded_at=event["occurred_at"], root_message_ids="[]"),
    )
    from tests.appraisal_material_support import appraisal_store

    for table in appraisal_store():
        store.setdefault(table, ())
    store["semantic_claims"] = ({"claim_id": details["aggregate_id"], "aggregate_version": 2},)
    result = AuditMetadataIndex.build(store)
    assert result.classifications[0].reason is Reason.SCOPE_UNPROVEN
    assert result.coverage.certified_metadata_events == 0
    assert result.unknown_binding_rows == 1


@pytest.mark.parametrize("inherited_root", ["owner", "other", "missing", "empty"])
def test_complete_payload_only_input_requires_inherited_actual_owner_root(
    inherited_root: str,
) -> None:
    store = _two_identity_store()
    store["conversation_sessions"][1]["identity_id"] = "identity-a"
    store["conversation_sessions"][1]["counterparty_id"] = "alice-a"
    store["audit_metadata_bindings"][0]["source_input_id"] = "payload-only-input"
    store["source_inputs"] = (
        {
            "input_id": "payload-only-input",
            "identity_id": "identity-a",
            "proof_status": "complete",
            "legacy": 0,
            "policy_generation": 1,
            "root_message_ids": "[]",
            "payload_refs": '[{"kind":"episode","payload_id":"episode","version":1}]',
        },
    )
    root = "message-a" if inherited_root == "owner" else "message-b"
    store["source_payload_receipts"] = (
        {
            "kind": "episode",
            "payload_id": "episode",
            "payload_version": 1,
            "identity_id": "identity-a",
            "proof_status": "complete",
            "legacy": 0,
            "root_message_ids": json.dumps([] if inherited_root == "empty" else [root]),
            "payload_refs": "[]",
            "eligible": False,
            "policy_generation": 1,
        },
    )
    if inherited_root == "missing":
        store["source_payload_receipts"] = ()
    result = AuditMetadataIndex.build(store)
    if inherited_root == "owner":
        assert result.classifications[0].status is Status.CERTIFIED_METADATA
    else:
        assert result.classifications[0].reason is Reason.SCOPE_UNPROVEN


@pytest.mark.parametrize("version", [1, 2, 3])
def test_disabled_reflection_metadata_uses_exact_supported_run_policy(version: int) -> None:
    store = _two_identity_store()
    event, binding = store["audit_events"][0], store["audit_metadata_bindings"][0]
    details = {
        "outcome_id": "disabled-outcome",
        "target_policy_version": version,
        "reason_code": "target_owner_not_enabled",
        "identity_id": "identity-a",
    }
    event.update(
        event_type="reflection.rejected",
        aggregate_type="reflection_proposal",
        aggregate_id="disabled-proposal",
        details=json.dumps(details),
    )
    binding.update(
        family="reflection_disabled",
        episode_decision_id=None,
        reflection_outcome_id=details["outcome_id"],
        event_digest=event_digest(event),
    )
    store["reflection_outcomes"] = (
        dict(
            details,
            proposal_id="disabled-proposal",
            decision="rejected",
            target_aggregate_type=None,
            target_aggregate_id=None,
            decided_at=event["occurred_at"],
        ),
    )
    store["reflection_proposals"] = ({"proposal_id": "disabled-proposal", "run_id": "run"},)
    store["reflection_runs"] = (
        {
            "run_id": "run",
            "identity_id": "identity-a",
            "schema_version": version,
            "policy_version": version,
        },
    )
    store["reflection_sources"] = (
        {"run_id": "run", "root_message_id": "message-a", "root_counterparty_id": "alice-a"},
    )
    result = AuditMetadataIndex.build(store)
    assert result.classifications[0].status is Status.CERTIFIED_METADATA
    store["reflection_runs"][0]["schema_version"] = version % 3 + 1
    store["reflection_runs"][0]["policy_version"] = version % 3 + 1
    assert AuditMetadataIndex.build(store).classifications[0].status is Status.UNKNOWN
    store["reflection_runs"][0]["schema_version"] = version
    store["reflection_runs"][0]["policy_version"] = version
    store["reflection_outcomes"][0]["target_policy_version"] = 4
    details["target_policy_version"] = 4
    event["details"] = json.dumps(details)
    binding["event_digest"] = event_digest(event)
    assert AuditMetadataIndex.build(store).classifications[0].status is Status.UNKNOWN
