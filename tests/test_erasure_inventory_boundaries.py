"""Real SQLite proof gaps and private row-routing binding regressions."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from satori.domain.erasure import ErasureDisposition
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_inventory import DependencyAssessment, Store
from satori.infrastructure.persistence.erasure_routes import ROUTE_SPECS, build_routes
from satori.infrastructure.persistence.models import (
    AuditEventRow,
    EpisodicMemoryRow,
    SourceInputRow,
    SourcePayloadRow,
)
from tests.erasure_support import ErasureCase, create_case, provider_counts, stored_state
from tests.test_source_exclusion_production import SECRET


@pytest.fixture
def boundary_case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "erasure-boundary-policy")


def test_current_episode_version_requires_its_exact_receipt(
    boundary_case: ErasureCase,
) -> None:
    case = boundary_case
    memory_id = case.episode_ids[0]
    with case.database.session_factory() as session:
        memory = session.get(EpisodicMemoryRow, memory_id)
        assert memory is not None
        assert memory.aggregate_version == 1
        assert session.get(SourcePayloadRow, ("episode", memory_id, 1)) is not None
        assert session.get(SourcePayloadRow, ("episode", memory_id, 2)) is None
    # A typed head version alone cannot certify current text against an older
    # immutable receipt. This corruption is confined to the synthetic fixture.
    with case.database.engine.begin() as connection:
        connection.execute(
            update(EpisodicMemoryRow)
            .where(EpisodicMemoryRow.memory_id == memory_id)
            .values(aggregate_version=2)
        )
    before = stored_state(case)
    preview = case.planner().preview(case.request)
    assert preview.assessment_status == "blocked"
    assert "payload_receipt_missing" in preview.assessment_blockers
    assert any(
        route.table == "episodic_memories" and route.uncertain_rows >= 1 for route in preview.routes
    )
    assert stored_state(case) == before
    assert provider_counts(case.services) == case.initial_provider_calls


def test_arbitrary_audit_id_wording_is_reviewed_and_structure_retained(
    boundary_case: ErasureCase,
) -> None:
    case = boundary_case
    original = case.planner().preview(case.request)
    original_audit_routes = tuple(
        route for route in original.routes if route.table == "audit_events"
    )
    original_review_rows = sum(
        route.rows
        for route in original_audit_routes
        if route.disposition is ErasureDisposition.REVIEW_UNCERTAIN
    )
    original_metadata_rows = sum(
        route.rows
        for route in original_audit_routes
        if route.disposition is ErasureDisposition.RETAIN_METADATA
    )
    with case.database.session_factory() as session:
        session.add(
            AuditEventRow(
                event_id="synthetic-opaque-erasure-audit",
                schema_version=1,
                event_type="memory.created",
                aggregate_type="memory",
                aggregate_id=case.episode_ids[0],
                occurred_at=datetime(2026, 10, 3, tzinfo=UTC),
                trace_id="synthetic-opaque-erasure-audit-trace",
                details={"anything_id": SECRET},
            )
        )
        session.commit()
        count = session.scalar(select(func.count()).select_from(AuditEventRow))
        assert count is not None
    before = stored_state(case)
    preview = case.planner().preview(case.request)
    review = next(
        route
        for route in preview.routes
        if route.table == "audit_events"
        and route.disposition is ErasureDisposition.REVIEW_UNCERTAIN
    )
    retained = next(
        route
        for route in preview.routes
        if route.table == "audit_events" and route.disposition is ErasureDisposition.RETAIN_METADATA
    )
    assert review.rows == review.uncertain_rows == original_review_rows + 1
    assert retained.rows == original_metadata_rows
    assert sum(route.rows for route in preview.routes if route.table == "audit_events") == count
    assert review.content_columns == ("details",)
    assert "owner_retention_contract_unproven" in preview.assessment_blockers
    assert preview.assessment_status == "blocked"
    assert not preview.execution_authorized
    assert SECRET not in preview.model_dump_json()
    assert stored_state(case) == before


@pytest.mark.parametrize("corruption", ["malformed_roots", "malformed_refs", "missing_parent"])
def test_source_input_proof_gap_blocks_inventory(
    boundary_case: ErasureCase, corruption: str
) -> None:
    case = boundary_case
    with case.database.session_factory() as session:
        input_id = session.scalar(select(SourceInputRow.input_id).order_by(SourceInputRow.input_id))
        assert input_id is not None
    value: object
    if corruption == "malformed_roots":
        column, value = "root_message_ids", SECRET
    elif corruption == "malformed_refs":
        column, value = "payload_refs", {"anything_id": SECRET}
    else:
        column, value = (
            "payload_refs",
            [{"kind": "episode", "payload_id": "synthetic-missing-parent", "version": 1}],
        )
    with case.database.engine.begin() as connection:
        connection.exec_driver_sql(
            f"UPDATE source_inputs SET {column}=? WHERE input_id=?",
            (json.dumps(value), input_id),
        )
    before = stored_state(case)
    preview = case.planner().preview(case.request)
    assert "source_input_dependency_unproven" in preview.assessment_blockers
    assert preview.assessment_status == "blocked"
    assert not preview.execution_authorized
    assert SECRET not in preview.model_dump_json()
    assert stored_state(case) == before
    assert provider_counts(case.services) == case.initial_provider_calls


def test_routing_digest_binds_private_keys_even_when_public_counts_are_equal() -> None:
    """Pure selection binding; the unchanged raw store is identical for both plans."""
    store: Store = {table: () for table in ROUTE_SPECS}
    store["conversation_messages"] = (
        {"message_id": "synthetic-root", "interaction_id": "synthetic-root-turn", "role": "user"},
    )
    store["episodic_memories"] = (
        {"memory_id": "synthetic-episode-a", "source_interaction_id": "synthetic-independent-a"},
        {"memory_id": "synthetic-episode-b", "source_interaction_id": "synthetic-independent-b"},
    )
    store["episodic_memory_embeddings"] = (
        {"embedding_id": "synthetic-vector-a", "memory_id": "synthetic-episode-a"},
        {"embedding_id": "synthetic-vector-b", "memory_id": "synthetic-episode-b"},
    )
    primary_keys: dict[str, tuple[str, ...]] = {
        table: ("unused-empty-table-key",) for table in ROUTE_SPECS
    }
    primary_keys.update(
        conversation_messages=("message_id",),
        episodic_memories=("memory_id",),
        episodic_memory_embeddings=("embedding_id",),
    )
    first = DependencyAssessment(
        frozenset({("episode", "synthetic-episode-a", 1)}), frozenset(), frozenset()
    )
    second = DependencyAssessment(
        frozenset({("episode", "synthetic-episode-b", 1)}), frozenset(), frozenset()
    )
    routes_a, blockers_a, digest_a = build_routes(store, first, "synthetic-root", primary_keys)
    routes_b, blockers_b, digest_b = build_routes(store, second, "synthetic-root", primary_keys)
    assert routes_a == routes_b
    assert blockers_a == blockers_b == frozenset()
    assert digest_a != digest_b
