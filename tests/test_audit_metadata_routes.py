"""Inventory removes opaque blockers only for the exact proved metadata subset."""

import json

from satori.domain.audit_metadata import AuditMetadataCoverageStatus as Status
from satori.domain.erasure import ErasureDisposition as Disposition
from satori.domain.erasure import ErasureRoute
from satori.infrastructure.persistence.audit_metadata import AuditMetadataIndex
from satori.infrastructure.persistence.erasure_inventory import DependencyAssessment, Store
from satori.infrastructure.persistence.erasure_routes import ROUTE_SPECS, build_routes
from tests.appraisal_material_support import appraisal_store
from tests.test_audit_metadata import activation_store


def route_store() -> Store:
    store: Store = {name: () for name in ROUTE_SPECS}
    store.update(activation_store())
    store["conversation_messages"] = (
        {
            "message_id": "root",
            "interaction_id": "interaction",
            "session_id": "session",
            "role": "user",
            "sequence": 1,
            "content": "SYNTHETIC_ROOT",
            "body_state": "available",
            "body_action_id": None,
            "schema_version": 1,
        },
    )
    return store


def routes(store: Store) -> tuple[tuple[ErasureRoute, ...], frozenset[str], str]:
    primary: dict[str, tuple[str, ...]] = {
        table: (next(iter(rows[0])),) if rows else () for table, rows in store.items()
    }
    primary["audit_metadata_bindings"] = ("event_id",)
    return build_routes(
        store,
        DependencyAssessment(frozenset(), frozenset(), frozenset()),
        "root",
        primary,
        audit_index=AuditMetadataIndex.build(store),
    )


def test_exact_certified_subset_removes_blanket_audit_uncertainty() -> None:
    result, blockers, _digest = routes(route_store())
    audit = [item for item in result if item.table == "audit_events"]
    assert sum(item.uncertain_rows for item in audit) == 0
    assert sum(item.rows for item in audit if item.disposition is Disposition.RETAIN_METADATA) == 1
    assert "opaque_owner_content_unproven" not in blockers


def test_unknown_audit_remains_unknown_and_equal_public_counts_cannot_hide_routing_change() -> None:
    store = route_store()
    original = store["audit_events"][0]
    store["audit_events"] += (
        dict(
            original,
            event_id="legacy-private",
            event_type="SYNTHETIC_SECRET",
            details='{"private":"SYNTHETIC_SECRET"}',
        ),
    )
    result, blockers, digest = routes(store)
    audit = [item for item in result if item.table == "audit_events"]
    assert sum(item.uncertain_rows for item in audit) == 1
    assert "opaque_owner_content_unproven" in blockers
    assert "SYNTHETIC_SECRET" not in json.dumps([item.table for item in result])
    store["audit_events"][1]["event_id"] = "different-legacy-private"
    changed, otherblockers, new_digest = routes(store)
    assert changed == result
    assert otherblockers == blockers
    assert new_digest != digest


def test_orphan_binding_is_reviewed_instead_of_receiving_table_level_metadata_badge() -> None:
    store = route_store()
    store["audit_metadata_bindings"] += (
        dict(store["audit_metadata_bindings"][0], event_id="absent-event"),
    )
    result, blockers, _digest = routes(store)
    assert "audit_binding_unproven" in blockers
    assert (
        sum(item.uncertain_rows for item in result if item.table == "audit_metadata_bindings") == 1
    )
    assert AuditMetadataIndex.build(store).unknown_binding_rows == 1


def test_available_proved_appraisal_is_owned_content_and_never_generic_metadata() -> None:
    store = appraisal_store()
    index = AuditMetadataIndex.build(store)
    assert index.classifications[0].status is Status.OWNED_CONTENT
    assert index.coverage.owned_content_events == 1
    merged: Store = {name: () for name in ROUTE_SPECS}
    merged.update(store)
    primary: dict[str, tuple[str, ...]] = {
        table: (next(iter(rows[0])),) if rows else () for table, rows in merged.items()
    }
    result, _blockers, _digest = build_routes(
        merged,
        DependencyAssessment(frozenset(), frozenset(), frozenset()),
        "message",
        primary,
        audit_index=index,
    )
    content = [item for item in result if item.table == "audit_events" and item.owner == "affect"]
    assert len(content) == 1
    assert content[0].disposition is Disposition.REDACT_CONTENT
    assert content[0].uncertain_rows == 0
    assert not any(
        item.rows
        for item in result
        if item.table == "audit_events" and item.disposition is Disposition.RETAIN_METADATA
    )
