"""Selected-root routes consume the same proved per-row retention decisions."""

from copy import deepcopy
from pathlib import Path

from satori.domain.erasure import ErasureDisposition
from satori.domain.retention import RetentionClass
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_inventory import assess_dependencies
from satori.infrastructure.persistence.erasure_routes import build_routes
from satori.infrastructure.persistence.retention_context import RetentionContext
from tests.checkpoint_reason_support import create_checkpoint_reason_fixture
from tests.erasure_support import create_case
from tests.test_retention_context import context_for, finish_reasons


def test_all_terminal_family_routes_use_retained_metadata_and_numeric_facets(
    migrated_database: Database, tmp_path: Path
) -> None:
    fixture = create_checkpoint_reason_fixture(migrated_database, tmp_path / "floor")
    finish_reasons(fixture)
    case = fixture.case
    context = context_for(migrated_database, case.runtime)
    assessment = assess_dependencies(
        context._store, case.scope.identity_id, case.message_id, historical=context.historical
    )
    routes, _blockers, digest = build_routes(
        context._store, assessment, case.message_id, context._primary_keys, retention=context
    )
    for table in (
        "reflection_proposals",
        "personality_checkpoint_approvals",
        "personality_restore_events",
    ):
        own = [route for route in routes if route.table == table and route.rows]
        assert own, table
        assert all(
            route.disposition
            in {ErasureDisposition.RETAIN_METADATA, ErasureDisposition.RETAIN_NUMERIC}
            for route in own
        )
    erased_ids = context._terminal["affective_transitions"]
    metadata = sum(
        route.rows
        for route in routes
        if route.table == "affective_transitions"
        and route.disposition is ErasureDisposition.RETAIN_METADATA
    )
    assert metadata == len(erased_ids)
    assert len(digest) == 64
    assert any(
        route.table == "satori_identities"
        and route.disposition is ErasureDisposition.REDACT_CONTENT
        and route.content_columns == ("name",)
        for route in routes
    )
    assert any(
        route.table == "satori_values"
        and route.disposition is ErasureDisposition.REDACT_CONTENT
        and route.content_columns == ("description",)
        for route in routes
    )


def test_unsupported_metadata_scalar_is_reviewed_instead_of_retained_by_table(
    migrated_database: Database, tmp_path: Path
) -> None:
    case = create_case(migrated_database, tmp_path / "floor")
    original = context_for(migrated_database, case.runtime)
    store = deepcopy(original._store)
    row = store["episode_formation_decisions"][0]
    row["reason_code"] = "SYNTHETIC_PRIVATE_REASON_PROSE"
    context = RetentionContext.build(
        store,
        snapshot=original._snapshot,
        primary_keys=original._primary_keys,
        schema_digest=original._schema_digest,
        store_digest="e" * 64,
    )
    assert context.classify("episode_formation_decisions", row).category is RetentionClass.UNKNOWN
    assessment = assess_dependencies(
        store, case.scope.identity_id, case.message_id, historical=context.historical
    )
    routes, blockers, _digest = build_routes(
        store, assessment, case.message_id, original._primary_keys, retention=context
    )
    reviews = [
        route
        for route in routes
        if route.table == "episode_formation_decisions"
        and route.disposition is ErasureDisposition.REVIEW_UNCERTAIN
    ]
    assert sum(route.uncertain_rows for route in reviews) >= 1
    assert "owner_retention_contract_unproven" in blockers
    assert "SYNTHETIC_PRIVATE" not in repr(routes)
    assert {route.owner for route in routes} == {
        "interaction_log",
        "memory_manager",
        "retrieval_index",
        "semantic_memory",
        "current_models",
        "positions",
        "reflection",
        "affect",
        "relationship",
        "initial_self",
        "personality",
        "initial_values",
        "audit",
        "source_policy",
        "schema_registry",
    }


def test_tampered_audit_shape_with_old_binding_does_not_certify_either_row(
    migrated_database: Database, tmp_path: Path
) -> None:
    case = create_case(migrated_database, tmp_path / "floor")
    original = context_for(migrated_database, case.runtime)
    store = deepcopy(original._store)
    binding = store["audit_metadata_bindings"][0]
    event = next(row for row in store["audit_events"] if row["event_id"] == binding["event_id"])
    event["details"] = '{"anything_id":"SYNTHETIC_PRIVATE_AUDIT_PROSE"}'
    context = RetentionContext.build(
        store,
        snapshot=original._snapshot,
        primary_keys=original._primary_keys,
        schema_digest=original._schema_digest,
        store_digest="d" * 64,
    )
    assert context.classify("audit_events", event).category is RetentionClass.UNKNOWN
    assert context.classify("audit_metadata_bindings", binding).category is RetentionClass.UNKNOWN
