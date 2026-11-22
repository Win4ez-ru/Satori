"""Exact structured V1/V2/V3 attribution proof; no provider or Character execution."""

import copy
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest

from satori.core.personality import PersonalityCitationRole, PersonalityDirection
from satori.core.reflection import (
    ReflectionCandidate,
    ReflectionLineageKind,
    ReflectionOwnerObservation,
    ReflectionPersonalityCandidate,
    ReflectionPersonalityCitation,
    ReflectionPurpose,
    ReflectionSourceKind,
    ReflectionTargetOwner,
)
from satori.domain.reflection import (
    ReflectionSourceRecord,
    content_hash,
    proposal_payload,
    reflection_proposal_id,
    reflection_run_id,
    reflection_run_key,
    reflection_source_id,
    source_set_hash,
)
from satori.infrastructure.persistence.erasure_inventory import PAYLOAD_TABLES, ProvenOwners, Store


def _closed_store(schema: int) -> tuple[Store, str]:
    """Owner metadata proof only; selection separately requires actual-input receipts."""
    tables = {table for table, _key, _version in PAYLOAD_TABLES.values()}
    tables.update(
        {
            "conversation_messages",
            "conversation_sessions",
            "reflection_sources",
            "memory_evidence",
            "satori_position_evidence",
            "semantic_claim_evidence",
            "user_model_claim_evidence",
            "world_model_claim_evidence",
            "satori_inclination_evidence",
            "affective_transitions",
        }
    )
    store: Store = {table: () for table in tables}
    quote = "Проверка оснований полезна."
    purpose = ReflectionPurpose.PERSONALITY_EVOLUTION if schema == 3 else ReflectionPurpose.GENERAL
    records = tuple(
        ReflectionSourceRecord(
            "placeholder",
            "placeholder",
            i,
            ReflectionSourceKind.POSITION_EVIDENCE,
            f"evidence-{i}",
            1,
            f"interaction-{i}",
            f"message-{i}",
            "alice",
            datetime(2026, 10, 1, tzinfo=UTC),
            content_hash(quote),
            upstream_lineage_kind=ReflectionLineageKind.POSITION if schema == 3 else None,
            upstream_lineage_id=f"position-{i}" if schema == 3 else None,
        )
        for i in range(8 if schema == 3 else 1)
    )
    source_hash = source_set_hash(records, schema_version=schema, purpose=purpose)
    key = reflection_run_key(
        identity_id="identity",
        source_hash=source_hash,
        schema_version=schema,
        policy_version=schema,
        purpose=purpose,
    )
    run_id = reflection_run_id(key)
    records = tuple(
        replace(r, run_id=run_id, source_id=reflection_source_id(run_id=run_id, ordinal=r.ordinal))
        for r in records
    )
    candidate: ReflectionCandidate
    if schema == 3:
        candidate = ReflectionPersonalityCandidate(
            ReflectionTargetOwner.PERSONALITY,
            "curiosity",
            PersonalityDirection.INCREASE,
            0.8,
            tuple(
                ReflectionPersonalityCitation(r.source_id, PersonalityCitationRole.SUPPORT)
                for r in records
            ),
            1,
        )
    else:
        candidate = ReflectionOwnerObservation(
            ReflectionTargetOwner.VALUES, "Проверяемое наблюдение.", (records[0].source_id,)
        )
    proposal_id = reflection_proposal_id(run_id=run_id, ordinal=0, candidate=candidate)
    store["conversation_sessions"] = (
        {
            "session_id": "session",
            "identity_id": "identity",
            "counterparty_id": "alice",
            "scope_verified": 1,
        },
    )
    store["conversation_messages"] = tuple(
        {
            "message_id": r.root_message_id,
            "interaction_id": r.root_interaction_id,
            "session_id": "session",
            "role": "user",
            "sequence": 1,
            "content": quote,
        }
        for r in records
    )
    store["conversation_interactions"] = tuple(
        {"interaction_id": r.root_interaction_id, "session_id": "session", "status": "completed"}
        for r in records
    )
    store["satori_positions"] = tuple(
        {"position_id": f"position-{r.ordinal}", "identity_id": "identity", "aggregate_version": 1}
        for r in records
    )
    store["satori_position_evidence"] = tuple(
        {
            "evidence_id": r.evidence_edge_id,
            "position_id": f"position-{r.ordinal}",
            "source_message_id": r.root_message_id,
            "source_interaction_id": r.root_interaction_id,
            "source_counterparty_id": "alice",
            "quote": quote,
        }
        for r in records
    )
    store["reflection_runs"] = (
        {
            "run_id": run_id,
            "run_key": key,
            "identity_id": "identity",
            "status": "completed",
            "schema_version": schema,
            "policy_version": schema,
            "purpose": purpose.value,
            "source_set_hash": source_hash,
        },
    )
    store["reflection_sources"] = tuple(
        json.loads(json.dumps(asdict(r), default=lambda value: value.isoformat())) for r in records
    )
    store["reflection_proposals"] = (
        {
            "proposal_id": proposal_id,
            "run_id": run_id,
            "ordinal": 0,
            "target_owner": candidate.target_owner.value,
            "body_state": "available",
            "body_action_id": None,
            "payload": json.dumps(proposal_payload(candidate), ensure_ascii=False),
            "evidence_source_ids": json.dumps([r.source_id for r in records]),
        },
    )
    return store, proposal_id


@pytest.mark.parametrize("schema", [1, 2, 3])
def test_all_closed_schemas_and_disabled_owner_observations_have_exact_typed_proof(
    schema: int,
) -> None:
    store, key = _closed_store(schema)
    assert ProvenOwners(store, "identity").reflection_proven(key)


@pytest.mark.parametrize("schema", [1, 2, 3])
@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("reflection_sources", "source_id", "invented-source"),
        ("reflection_sources", "evidence_edge_version", 2),
        ("reflection_runs", "source_set_hash", "0" * 64),
        ("reflection_sources", "root_counterparty_id", "bob"),
    ],
)
def test_each_schema_rejects_forged_ids_versions_hash_or_scope(
    table: str, column: str, value: object, schema: int
) -> None:
    store, key = _closed_store(schema)
    changed = copy.deepcopy(store)
    changed[table][0][column] = value
    assert not ProvenOwners(changed, "identity").reflection_proven(key)


def test_v3_recomputed_proposal_id_cannot_admit_a_disabled_owner_observation() -> None:
    store, key = _closed_store(3)
    row = store["reflection_proposals"][0]
    source_id = str(store["reflection_sources"][0]["source_id"])
    candidate = ReflectionOwnerObservation(
        ReflectionTargetOwner.PERSONALITY, "Наблюдение.", (source_id,)
    )
    replacement_id = reflection_proposal_id(
        run_id=str(row["run_id"]), ordinal=0, candidate=candidate
    )
    row.update(proposal_id=replacement_id, payload=json.dumps(proposal_payload(candidate)))
    assert replacement_id != key
    assert not ProvenOwners(store, "identity").reflection_proven(replacement_id)
