"""Closed metadata classification does not turn retained receipts into live evidence."""

import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from satori.domain.retention import RetentionClass, RetentionFacet, RetentionReason
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.erasure_inventory import Store
from satori.infrastructure.persistence.retention_contracts import RetentionWorkBudget
from satori.infrastructure.persistence.retention_metadata import MetadataRetentionIndex
from satori.infrastructure.persistence.retention_shapes import ROW_FIELDS

NOW = datetime(2026, 10, 14, tzinfo=UTC).isoformat()


def _snapshot() -> SourceEligibilitySnapshot:
    return SourceEligibilitySnapshot("floor", "installation", "lineage", "identity", 0, frozenset())


def _store() -> Store:
    store: Store = {table: () for table in ROW_FIELDS}
    store["satori_identities"] = (
        {
            "identity_id": "identity",
            "installation_slot": 1,
            "name": "Private initial name",
            "activation_time": NOW,
            "identity_version": 1,
            "seed_id": "seed",
            "seed_schema_version": 1,
            "seed_content_hash": "a" * 64,
        },
    )
    store["conversation_sessions"] = (
        {
            "session_id": "session",
            "identity_id": "identity",
            "counterparty_id": "alice",
            "scope_verified": 1,
            "schema_version": 1,
            "kind": "explicit",
            "status": "open",
            "started_at": NOW,
            "ended_at": None,
        },
    )
    interaction = dict.fromkeys(
        item.split(":")[0] for item in ROW_FIELDS["conversation_interactions"].split()
    )
    interaction.update(
        {
            "interaction_id": "interaction",
            "session_id": "session",
            "client_request_id": "client",
            "trace_id": "trace",
            "schema_version": 1,
            "status": "completed",
            "started_at": NOW,
            "completed_at": NOW,
            "provider": "replaceable-provider",
            "model": "opaque/model:identifier",
            "finish_status": "stop",
            "context_schema_version": 20,
            "context_manifest_schema_version": 20,
            "policy_id": "declared-context-policy",
            "policy_schema_version": 1,
            "relationship_processing_required": 0,
            "model_processing_required": 0,
            "position_processing_required": 0,
        }
    )
    store["conversation_interactions"] = (interaction,)
    store["conversation_messages"] = (
        {
            "message_id": "root",
            "session_id": "session",
            "interaction_id": "interaction",
            "schema_version": 1,
            "role": "user",
            "content": "Canonical private source.",
            "created_at": NOW,
            "sequence": 1,
            "body_state": "available",
            "body_action_id": None,
        },
    )
    return store


def _index(
    store: Store | None = None, *, budget: RetentionWorkBudget | None = None
) -> MetadataRetentionIndex:
    return MetadataRetentionIndex.build(
        _store() if store is None else store,
        snapshot=_snapshot(),
        historical=None,
        budget=RetentionWorkBudget() if budget is None else budget,
    )


def test_initial_name_is_owned_content_and_opaque_provider_labels_are_declared_metadata() -> None:
    store = _store()
    index = _index(store)
    identity = index.classify("satori_identities", store["satori_identities"][0])
    interaction = index.classify("conversation_interactions", store["conversation_interactions"][0])
    assert identity.category is RetentionClass.OWNED_CONTENT
    assert interaction.category is RetentionClass.SUPPORTED_RETAINED
    assert RetentionFacet.STRUCTURAL_METADATA in interaction.facets
    assert store["satori_identities"][0]["name"] == "Private initial name"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("scope_verified", 2),
        ("kind", "private prose"),
        ("identity_id", "foreign"),
        ("ended_at", NOW),
    ],
)
def test_session_unknown_contract_or_foreign_scope_is_not_retained(
    field: str, value: object
) -> None:
    store = _store()
    row = dict(store["conversation_sessions"][0])
    row[field] = value
    store["conversation_sessions"] = (row,)
    result = _index(store).classify("conversation_sessions", row)
    assert result.category is RetentionClass.UNKNOWN
    assert result.reasons


@pytest.mark.parametrize("extra", [{"unsupported": "private"}, {"schema_version": True}])
def test_exact_row_shape_and_integer_types_fail_closed(extra: dict[str, object]) -> None:
    row = dict(_store()["conversation_sessions"][0])
    row.update(extra)
    result = _index().validate_row("conversation_sessions", row)
    assert result.category is RetentionClass.UNKNOWN


def _attempt(
    metrics: object = None, *, reason: str = "provider_invalid_or_unavailable"
) -> dict[str, object]:
    return {
        "attempt_id": "attempt",
        "run_id": "run",
        "ordinal": 1,
        "status": "failed",
        "reason_code": reason,
        "provider": "opaque-provider",
        "model": "opaque-model",
        "formation_method": "reflection-provider-v1",
        "started_at": NOW,
        "finished_at": NOW,
        "metrics": {} if metrics is None else metrics,
    }


@pytest.mark.parametrize(
    "metrics",
    [
        '{"provider_total_ms":1,"provider_total_ms":2}',
        {"private explanation": "private text"},
        {"provider_total_ms": float("nan")},
        {"provider_prompt_tokens": True},
        {"provider_total_ms": -1},
    ],
)
def test_metrics_closed_keys_duplicate_json_and_finite_numbers(metrics: object) -> None:
    result = _index().validate_row("reflection_attempts", _attempt(metrics))
    assert result.category is RetentionClass.UNKNOWN


def test_supported_metrics_and_freeform_scalar_are_separate_contracts() -> None:
    index = _index()
    assert (
        index.validate_row(
            "reflection_attempts",
            _attempt({"provider_total_ms": 1.25, "provider_prompt_tokens": 0}),
        ).category
        is RetentionClass.SUPPORTED_RETAINED
    )
    result = index.validate_row("reflection_attempts", _attempt(reason="a private explanation"))
    assert result.category is RetentionClass.UNKNOWN
    assert result.reasons == (RetentionReason.SCALAR_CODE_UNSUPPORTED,)


def _input(
    *, status: str = "complete", roots: object = None, refs: object = None
) -> dict[str, object]:
    return {
        "input_id": "input",
        "identity_id": "identity",
        "policy_generation": 0,
        "proof_status": status,
        "legacy": 0,
        "root_message_ids": ["root"] if roots is None else roots,
        "payload_refs": [] if refs is None else refs,
    }


def test_known_unknown_provenance_status_is_retained_without_upgrading_it() -> None:
    store = _store()
    row = _input(status="unknown", roots=[])
    store["source_inputs"] = (row,)
    before = deepcopy(store)
    result = _index(store).classify("source_inputs", row)
    assert result.category is RetentionClass.SUPPORTED_RETAINED
    assert row["proof_status"] == "unknown"
    assert store == before


@pytest.mark.parametrize(
    ("roots", "refs"),
    [
        ([], []),
        (["missing-root"], []),
        (["root"], [{"kind": "episode", "payload_id": "missing", "version": 1}]),
        (["root"], [{"kind": "future-owner", "payload_id": "id", "version": 1}]),
    ],
)
def test_complete_receipt_needs_actual_canonical_or_inherited_closure(
    roots: object, refs: object
) -> None:
    store = _store()
    row = _input(roots=roots, refs=refs)
    store["source_inputs"] = (row,)
    result = _index(store).classify("source_inputs", row)
    assert result.category is RetentionClass.UNKNOWN


def test_generic_retained_footprint_can_include_identity_global_counterparties() -> None:
    store = _store()
    other_session = dict(
        store["conversation_sessions"][0], session_id="bob-session", counterparty_id="bob"
    )
    other_interaction = dict(
        store["conversation_interactions"][0],
        interaction_id="bob-interaction",
        session_id="bob-session",
        client_request_id="bob-client",
    )
    other_message = dict(
        store["conversation_messages"][0],
        message_id="bob-root",
        session_id="bob-session",
        interaction_id="bob-interaction",
    )
    store["conversation_sessions"] += (other_session,)
    store["conversation_interactions"] += (other_interaction,)
    store["conversation_messages"] += (other_message,)
    row = _input(roots=["root", "bob-root"])
    store["source_inputs"] = (row,)
    result = _index(store).classify("source_inputs", row)
    assert result.category is RetentionClass.SUPPORTED_RETAINED


def test_work_capacity_stops_build_or_classification_without_partial_badge() -> None:
    with pytest.raises(SourcePolicyError, match="retention_assessment_capacity_exceeded"):
        _index(budget=RetentionWorkBudget(limit=1))
    budget = RetentionWorkBudget()
    index = _index(budget=budget)
    budget.limit = budget.work
    with pytest.raises(SourcePolicyError, match="retention_assessment_capacity_exceeded"):
        index.classify("conversation_sessions", index.store["conversation_sessions"][0])


def test_no_unknown_table_or_extra_fields_default_to_metadata() -> None:
    result = _index().classify("new_provider_cache", {"name": "private text"})
    assert result.category is RetentionClass.UNKNOWN


def test_complete_input_can_inherit_exact_parent_receipt_roots() -> None:
    store = _store()
    store["conversation_messages"] += (
        dict(store["conversation_messages"][0], message_id="reply", role="assistant", sequence=2),
    )
    payload = dict(
        _input(), kind="assistant_reply", payload_id="interaction", payload_version=1, eligible=0
    )
    del payload["input_id"]
    store["source_payload_receipts"] = (payload,)
    row = _input(
        roots=[], refs=[{"kind": "assistant_reply", "payload_id": "interaction", "version": 1}]
    )
    store["source_inputs"] = (row,)
    result = _index(store).classify("source_inputs", row)
    assert result.category is RetentionClass.SUPPORTED_RETAINED


def test_source_receipt_cycle_does_not_bypass_root_or_work_proof() -> None:
    store = _store()
    store["conversation_messages"] += (
        dict(store["conversation_messages"][0], message_id="reply", role="assistant", sequence=2),
    )
    ref = {"kind": "assistant_reply", "payload_id": "interaction", "version": 1}
    payload = dict(
        _input(refs=[ref]),
        kind="assistant_reply",
        payload_id="interaction",
        payload_version=1,
        eligible=0,
    )
    del payload["input_id"]
    store["source_payload_receipts"] = (payload,)
    row = _input(roots=[], refs=[ref])
    store["source_inputs"] = (row,)
    result = _index(store).classify("source_inputs", row)
    assert result.category is RetentionClass.UNKNOWN
    assert result.reasons == (RetentionReason.INPUT_PROVENANCE_UNPROVEN,)


@pytest.mark.parametrize(
    "owner",
    [
        "semantic_memory",
        "current_models",
        "positions",
        "reflection_backlog",
        "relationship_backlog",
    ],
)
def test_exact_declared_source_owner_watermarks_are_supported(owner: str) -> None:
    store = _store()
    row = {"owner": owner, "identity_id": "identity", "policy_generation": 0}
    store["source_owner_repairs"] = (row,)
    result = _index(store).classify("source_owner_repairs", row)
    assert result.category is RetentionClass.SUPPORTED_RETAINED


def test_freeform_source_owner_name_is_not_a_metadata_badge() -> None:
    row = {"owner": "private owner prose", "identity_id": "identity", "policy_generation": 0}
    result = _index().classify("source_owner_repairs", row)
    assert result.category is RetentionClass.UNKNOWN


def _embedding(vector: object, *, dimensions: object = 3) -> dict[str, object]:
    return {
        "embedding_id": "embedding",
        "memory_id": "memory",
        "provider": "provider-neutral",
        "model": "opaque/model-identifier",
        "dimensions": dimensions,
        "input_schema_version": 1,
        "vector": vector,
        "indexed_at": NOW,
    }


@pytest.mark.parametrize("dimensions", [1, 768, 1536, 8192])
def test_declared_embedding_sizes_have_a_closed_vector_role(dimensions: int) -> None:
    from satori.core.embedding import EmbeddingResponse, EmbeddingSpace

    values = [1.0] + [0.0] * (dimensions - 1)
    EmbeddingResponse(
        EmbeddingSpace("provider-neutral", "opaque/model", dimensions, 1), (tuple(values),)
    )
    row = _embedding(json.dumps(values), dimensions=dimensions)
    index = _index()
    before = deepcopy(row)
    decision = index.validate_row("episodic_memory_embeddings", row)
    assert decision.category is RetentionClass.SUPPORTED_RETAINED
    work = index.budget.work
    assert index.validate_row("episodic_memory_embeddings", row) == decision
    assert index.budget.work == work
    assert row == before


def test_embedding_finite_magnitude_and_large_valid_json_do_not_inherit_general_json_caps() -> None:
    from satori.infrastructure.persistence.retention_values import UnprovedRetention, document

    index = _index()
    assert (
        index.validate_row("episodic_memory_embeddings", _embedding([1e100, 0, 0])).category
        is RetentionClass.SUPPORTED_RETAINED
    )
    values = [0.1234567890123456] * 8192
    raw = json.dumps(values)
    assert len(raw) > 131_072
    row = _embedding(raw, dimensions=8192)
    assert (
        index.validate_row("episodic_memory_embeddings", row).category
        is RetentionClass.SUPPORTED_RETAINED
    )
    with pytest.raises(UnprovedRetention, match="structured_value_unproven"):
        document(json.dumps([0] * 768), RetentionWorkBudget())


@pytest.mark.parametrize(
    "values",
    [
        [0, 0, 0],
        [True, 0, 0],
        ["1", 0, 0],
        [[1], 0, 0],
        [{"x": 1}, 0, 0],
        [None, 0, 0],
        [float("nan"), 0, 0],
        [float("inf"), 0, 0],
        [1e-200, 0, 0],
    ],
)
def test_same_length_invalid_vectors_are_unknown(values: object) -> None:
    result = _index().validate_row("episodic_memory_embeddings", _embedding(values))
    assert result.category is RetentionClass.UNKNOWN
    assert result.reasons == (RetentionReason.STRUCTURED_VALUE_UNPROVEN,)


@pytest.mark.parametrize(
    ("dimensions", "values"),
    [
        (0, []),
        (8193, [1]),
        (True, [1]),
        (3.0, [1, 0, 0]),
        (3, [1, 0]),
        (3, {"x": 1}),
        (3, "not-json"),
        (3, "[1,0,0] trailing"),
    ],
)
def test_vector_dimension_and_top_level_contracts_fail_closed(
    dimensions: object, values: object
) -> None:
    result = _index().validate_row(
        "episodic_memory_embeddings", _embedding(values, dimensions=dimensions)
    )
    assert result.category is RetentionClass.UNKNOWN


def test_vector_role_rejects_oversized_encoded_bytes_before_parsing() -> None:
    from satori.infrastructure.persistence.retention_values import MAX_RETAINED_EMBEDDING_BYTES

    row = _embedding(" " * MAX_RETAINED_EMBEDDING_BYTES + "[1]", dimensions=1)
    result = _index().validate_row("episodic_memory_embeddings", row)
    assert result.category is RetentionClass.UNKNOWN
    assert result.reasons == (RetentionReason.STRUCTURED_VALUE_UNPROVEN,)


def test_vector_role_uses_shared_work_budget_without_partial_classification() -> None:
    from satori.infrastructure.persistence.retention_values import embedding_vector

    with pytest.raises(SourcePolicyError, match="retention_assessment_capacity_exceeded"):
        embedding_vector([1.0] + [0.0] * 767, 768, RetentionWorkBudget(limit=768))
