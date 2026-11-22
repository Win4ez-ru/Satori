"""Explicit current-schema retention census; unknown text never becomes metadata."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from satori.domain.audit_metadata import AuditMetadataCoverageStatus
from satori.domain.erasure import ErasureDisposition, ErasureRoute
from satori.domain.retention import RetentionClass, RetentionFacet
from satori.infrastructure.persistence.erasure_inventory import (
    DependencyAssessment,
    Row,
    Store,
)

if TYPE_CHECKING:
    from satori.infrastructure.persistence.audit_metadata import AuditMetadataIndex
    from satori.infrastructure.persistence.retention_context import RetentionContext


@dataclass(frozen=True, slots=True)
class RouteSpec:
    owner: str
    disposition: ErasureDisposition = ErasureDisposition.RETAIN_METADATA
    content_columns: tuple[str, ...] = ()
    kind: str | None = None
    key: str | None = None


_TEXT = ErasureDisposition.REDACT_CONTENT
_NUMERIC = ErasureDisposition.RETAIN_NUMERIC
_REVIEW = ErasureDisposition.REVIEW_UNCERTAIN
ROUTE_SPECS: dict[str, RouteSpec] = {
    "alembic_version": RouteSpec("schema_registry"),
    "conversation_sessions": RouteSpec("interaction_log"),
    "conversation_interactions": RouteSpec("interaction_log"),
    "conversation_messages": RouteSpec("interaction_log", _TEXT, ("content",)),
    "episodic_memories": RouteSpec("memory_manager", _TEXT, ("summary",), "episode", "memory_id"),
    "memory_evidence": RouteSpec("memory_manager", _TEXT, ("quote",), "episode", "memory_id"),
    "episode_formation_decisions": RouteSpec("memory_manager"),
    "memory_lifecycle_decisions": RouteSpec("memory_manager"),
    "episodic_memory_embeddings": RouteSpec(
        "retrieval_index", ErasureDisposition.REMOVE_INDEX, ("vector",), "episode", "memory_id"
    ),
    "semantic_claims": RouteSpec(
        "semantic_memory", _TEXT, ("value", "normalized_value"), "semantic_claim", "claim_id"
    ),
    "semantic_claim_evidence": RouteSpec("semantic_memory"),
    "semantic_claim_revisions": RouteSpec("semantic_memory", _NUMERIC),
    "semantic_formation_decisions": RouteSpec("semantic_memory"),
    "user_model_claims": RouteSpec(
        "current_models", _TEXT, ("value", "normalized_value"), "user_model_claim", "claim_id"
    ),
    "world_model_claims": RouteSpec(
        "current_models",
        _TEXT,
        ("subject_label", "normalized_subject_label", "value", "normalized_value"),
        "world_model_claim",
        "claim_id",
    ),
    "user_model_claim_evidence": RouteSpec("current_models"),
    "world_model_claim_evidence": RouteSpec("current_models"),
    "user_model_claim_revisions": RouteSpec("current_models", _NUMERIC),
    "world_model_claim_revisions": RouteSpec("current_models", _NUMERIC),
    "model_formation_decisions": RouteSpec("current_models"),
    "satori_positions": RouteSpec(
        "positions", _TEXT, ("proposition", "normalized_proposition"), "position", "position_id"
    ),
    "satori_position_evidence": RouteSpec(
        "positions", _TEXT, ("quote", "normalized_signature"), "position", "position_id"
    ),
    "satori_position_revisions": RouteSpec("positions", _NUMERIC),
    "position_formation_decisions": RouteSpec("positions"),
    "satori_inclinations": RouteSpec(
        "positions",
        _TEXT,
        ("topic", "normalized_topic", "alternative_topic", "normalized_alternative_topic"),
        "inclination",
        "inclination_id",
    ),
    "satori_inclination_evidence": RouteSpec("positions"),
    "satori_inclination_revisions": RouteSpec("positions", _NUMERIC),
    "reflection_runs": RouteSpec("reflection"),
    "reflection_sources": RouteSpec("reflection"),
    "reflection_attempts": RouteSpec("reflection"),
    "reflection_proposals": RouteSpec(
        "reflection", _TEXT, ("payload",), "reflection_proposal", "proposal_id"
    ),
    "reflection_outcomes": RouteSpec("reflection"),
    "affective_states": RouteSpec("affect", _NUMERIC),
    "affective_transitions": RouteSpec(
        "affect",
        _REVIEW,
        (
            "reason_codes",
            "appraisal_payload",
            "source_refs",
            "applied_delta",
            "mood_delta",
            "state_before",
            "state_after",
        ),
    ),
    "relationship_states": RouteSpec("relationship", _NUMERIC),
    "relationship_decisions": RouteSpec("relationship", _NUMERIC),
    "relationship_transitions": RouteSpec("relationship", _NUMERIC),
    "satori_identities": RouteSpec("initial_self"),
    "satori_personality_states": RouteSpec("personality", _NUMERIC),
    "satori_personality_traits": RouteSpec("personality", _NUMERIC),
    "satori_value_sets": RouteSpec("initial_values"),
    "satori_values": RouteSpec("initial_values"),
    "personality_checkpoints": RouteSpec("personality", _NUMERIC),
    "personality_checkpoint_traits": RouteSpec("personality", _NUMERIC),
    "personality_revisions": RouteSpec("personality", _NUMERIC),
    "personality_evidence": RouteSpec("personality"),
    "personality_checkpoint_approvals": RouteSpec("personality", _REVIEW, ("reason",)),
    "personality_restore_events": RouteSpec("personality", _REVIEW, ("reason",)),
    "audit_events": RouteSpec("audit", _REVIEW, ("details",)),
    "audit_metadata_bindings": RouteSpec("audit"),
    "source_policy_bindings": RouteSpec("source_policy"),
    "source_excluded_roots": RouteSpec("source_policy"),
    "source_inputs": RouteSpec("source_policy"),
    "source_payload_receipts": RouteSpec("source_policy"),
    "owner_source_withdrawals": RouteSpec("source_policy"),
    "source_owner_repairs": RouteSpec("source_policy"),
    "erasure_policy_bindings": RouteSpec("source_policy"),
    "erasure_preparations": RouteSpec("source_policy"),
    "erasure_body_bindings": RouteSpec("source_policy"),
    "erasure_body_applications": RouteSpec("source_policy"),
}


def _terminal_body(table: str, row: Row, terminal_positions: set[str]) -> bool:
    if table == "satori_position_evidence":
        return (
            str(row["position_id"]) in terminal_positions
            and row.get("quote") is None
            and row.get("normalized_signature") is None
        )
    return row.get("body_state") == "erased"


def build_routes(
    store: Store,
    assessment: DependencyAssessment,
    message_id: str,
    primary_keys: dict[str, tuple[str, ...]],
    *,
    audit_index: AuditMetadataIndex | None = None,
    retention: RetentionContext | None = None,
) -> tuple[tuple[ErasureRoute, ...], frozenset[str], str]:
    affected: dict[str, set[str]] = {}
    uncertain: dict[str, set[str]] = {}
    for refs, target in ((assessment.affected, affected), (assessment.uncertain, uncertain)):
        for kind, payload_id, _version in refs:
            target.setdefault(kind, set()).add(payload_id)
    messages = {str(row["message_id"]): row for row in store["conversation_messages"]}
    root_interaction = str(messages[message_id]["interaction_id"])
    affected.setdefault("assistant_reply", set()).add(root_interaction)
    for kind, table, root_key, owner_key in (
        ("episode", "memory_evidence", "source_message_id", "memory_id"),
        ("semantic_claim", "semantic_claim_evidence", "root_message_id", "claim_id"),
        ("user_model_claim", "user_model_claim_evidence", "source_message_id", "claim_id"),
        ("world_model_claim", "world_model_claim_evidence", "source_message_id", "claim_id"),
        ("position", "satori_position_evidence", "source_message_id", "position_id"),
        ("inclination", "satori_inclination_evidence", "source_message_id", "inclination_id"),
    ):
        affected.setdefault(kind, set()).update(
            str(row[owner_key]) for row in store[table] if row[root_key] == message_id
        )
    affected.setdefault("episode", set()).update(
        str(row["memory_id"])
        for row in store["episodic_memories"]
        if row["source_interaction_id"] == root_interaction
    )
    runs = {
        str(row["run_id"])
        for row in store["reflection_sources"]
        if row["root_message_id"] == message_id
    }
    affected.setdefault("reflection_proposal", set()).update(
        str(row["proposal_id"])
        for row in store["reflection_proposals"]
        if str(row["run_id"]) in runs
    )
    routes: list[ErasureRoute] = []
    blockers: set[str] = set()
    routing_hash = hashlib.sha256()
    terminal_positions = {
        str(row["position_id"])
        for row in store["satori_positions"]
        if row.get("body_state") == "erased"
    }
    if retention is not None:
        audit_index = retention.audit_index
    if audit_index is None:
        from satori.infrastructure.persistence.audit_metadata import AuditMetadataIndex

        audit_index = AuditMetadataIndex.build(store)
    audit_status = {item.event_id: item.status for item in audit_index.classifications}
    audit_family = {item.event_id: item.family for item in audit_index.classifications}

    def route(
        spec: RouteSpec,
        table: str,
        selected: list[Row],
        uncertain_count: int,
        *,
        retained: ErasureDisposition | None = None,
    ) -> None:
        disposition = retained if retained is not None else spec.disposition
        columns = () if retained is not None else spec.content_columns
        result = ErasureRoute(
            spec.owner, table, disposition, len(selected), columns, uncertain_count
        )
        routes.append(result)
        uncertain_ids = uncertain.get(spec.kind or "", set())
        selection = [
            [
                [row[column] for column in primary_keys[table]],
                bool(
                    retained is None
                    and (
                        spec.disposition is _REVIEW
                        or (spec.key is not None and str(row[spec.key]) in uncertain_ids)
                        or (
                            table == "conversation_messages"
                            and row["role"] == "assistant"
                            and str(row["interaction_id"])
                            in uncertain.get("assistant_reply", set())
                        )
                    )
                ),
            ]
            for row in selected
        ]
        encoded = json.dumps(
            [spec.owner, table, disposition.value, columns, selection],
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        routing_hash.update(len(encoded).to_bytes(8, "big"))
        routing_hash.update(encoded)

    for table, spec in ROUTE_SPECS.items():
        if table not in store:
            continue
        rows = store[table]
        if retention is not None:
            decisions = {id(row): retention.classify(table, row) for row in rows}
            unknown = [row for row in rows if decisions[id(row)].category is RetentionClass.UNKNOWN]
            unknown_keys = {id(row) for row in unknown}
            if unknown:
                route(
                    RouteSpec(spec.owner, _REVIEW, spec.content_columns),
                    table,
                    unknown,
                    len(unknown),
                )
                blockers.add("owner_retention_contract_unproven")
            if table == "conversation_messages":
                reply_ids = affected.get("assistant_reply", set()) | uncertain.get(
                    "assistant_reply", set()
                )
                selected = [
                    row
                    for row in rows
                    if row["message_id"] == message_id
                    or (row["role"] == "assistant" and str(row["interaction_id"]) in reply_ids)
                ]
            elif spec.kind is not None and spec.key is not None:
                selected_ids = affected.get(spec.kind, set()) | uncertain.get(spec.kind, set())
                selected = [row for row in rows if str(row[spec.key]) in selected_ids]
            else:
                selected = list(rows)
            known = [row for row in selected if id(row) not in unknown_keys]
            content = [
                row for row in known if decisions[id(row)].category is RetentionClass.OWNED_CONTENT
            ]
            retained = [
                row
                for row in known
                if decisions[id(row)].category is RetentionClass.SUPPORTED_RETAINED
            ]
            if content:
                columns = (
                    ("name",)
                    if table == "satori_identities"
                    else ("description",)
                    if table == "satori_values"
                    else ("reason_codes",)
                    if table == "affective_transitions"
                    else spec.content_columns
                )
                disposition = (
                    ErasureDisposition.REMOVE_INDEX
                    if table == "episodic_memory_embeddings"
                    else _TEXT
                )
                route(
                    RouteSpec(spec.owner, disposition, columns, spec.kind, spec.key),
                    table,
                    content,
                    sum(
                        spec.key is not None
                        and str(row[spec.key]) in uncertain.get(spec.kind or "", set())
                        for row in content
                    ),
                )
            if retained or not rows or (not content and not unknown):
                disposition = (
                    _NUMERIC if spec.disposition is _NUMERIC else ErasureDisposition.RETAIN_METADATA
                )
                route(spec, table, retained, 0, retained=disposition)
            if table in {
                "affective_transitions",
                "satori_inclinations",
                "personality_checkpoint_approvals",
                "personality_restore_events",
            }:
                numeric = [
                    row
                    for row in rows
                    if RetentionFacet.NUMERIC_HISTORY in decisions[id(row)].facets
                ]
                route(spec, table, numeric, 0, retained=_NUMERIC)
            continue
        if table == "audit_metadata_bindings":
            metadata = [
                row
                for row in rows
                if audit_status.get(str(row["event_id"]))
                is AuditMetadataCoverageStatus.CERTIFIED_METADATA
                and audit_family.get(str(row["event_id"])) == row.get("family")
                and str(row.get("family")) != "appraisal_transition"
            ]
            known_ids = {str(row["event_id"]) for row in metadata}
            unknown = [row for row in rows if str(row["event_id"]) not in known_ids]
            if metadata:
                route(spec, table, metadata, 0, retained=ErasureDisposition.RETAIN_METADATA)
            if unknown:
                route(RouteSpec("audit", _REVIEW, ()), table, unknown, len(unknown))
                blockers.add("audit_binding_unproven")
            if not rows:
                route(spec, table, [], 0, retained=ErasureDisposition.RETAIN_METADATA)
            continue
        if table == "audit_events":
            metadata = [
                row
                for row in rows
                if audit_status.get(str(row["event_id"]))
                is AuditMetadataCoverageStatus.CERTIFIED_METADATA
            ]
            content = [
                row
                for row in rows
                if audit_status.get(str(row["event_id"]))
                is AuditMetadataCoverageStatus.OWNED_CONTENT
            ]
            unknown = [
                row
                for row in rows
                if audit_status.get(str(row["event_id"]))
                not in {
                    AuditMetadataCoverageStatus.CERTIFIED_METADATA,
                    AuditMetadataCoverageStatus.OWNED_CONTENT,
                }
            ]
            route(spec, table, unknown, len(unknown))
            if unknown:
                blockers.add("opaque_owner_content_unproven")
            if metadata:
                route(spec, table, metadata, 0, retained=ErasureDisposition.RETAIN_METADATA)
            if content:
                route(RouteSpec("affect", _TEXT, ("details",)), table, content, 0)
            continue
        selected = []
        uncertain_count = 0
        if table == "conversation_messages":
            reply_ids = affected.get("assistant_reply", set()) | uncertain.get(
                "assistant_reply", set()
            )
            selected = [
                row
                for row in rows
                if row["message_id"] == message_id
                or (row["role"] == "assistant" and str(row["interaction_id"]) in reply_ids)
            ]
            uncertain_count = sum(
                row["role"] == "assistant"
                and str(row["interaction_id"]) in uncertain.get("assistant_reply", set())
                for row in selected
            )
        elif spec.kind is not None and spec.key is not None:
            definite_ids = affected.get(spec.kind, set())
            uncertain_ids = uncertain.get(spec.kind, set())
            selected_ids = definite_ids | uncertain_ids
            selected = [row for row in rows if str(row[spec.key]) in selected_ids]
            uncertain_count = sum(str(row[spec.key]) in uncertain_ids for row in selected)
        elif spec.disposition is _REVIEW:
            if table == "audit_events":
                # Audit JSON has no typed retained-content/provenance contract.
                # Field names or familiar events cannot certify arbitrary text.
                selected = list(rows)
            elif table == "affective_transitions":
                # JSON columns have no SQL-level numeric shape guarantee. The
                # owner must prove and retain the valid numeric components;
                # preview never infers that arbitrary JSON is content-free.
                selected = list(rows)
            else:
                selected = list(rows)
            uncertain_count = len(selected)
            if selected:
                blockers.add("opaque_owner_content_unproven")
        else:
            selected = list(rows)
        if table in {
            "episodic_memories",
            "memory_evidence",
            "conversation_messages",
            "semantic_claims",
            "user_model_claims",
            "world_model_claims",
            "satori_positions",
            "satori_inclinations",
            "satori_position_evidence",
        }:
            terminal = [row for row in selected if _terminal_body(table, row, terminal_positions)]
            available = [
                row for row in selected if not _terminal_body(table, row, terminal_positions)
            ]
            uncertain_kind = "assistant_reply" if table == "conversation_messages" else "episode"
            if table not in {"episodic_memories", "memory_evidence", "conversation_messages"}:
                uncertain_kind = spec.kind or ""
            uncertain_key = "interaction_id" if table == "conversation_messages" else spec.key
            route(
                spec,
                table,
                available,
                sum(
                    str(row[uncertain_key]) in uncertain.get(uncertain_kind, set())
                    for row in available
                )
                if uncertain_key
                else 0,
            )
            if terminal:
                route(spec, table, terminal, 0, retained=ErasureDisposition.RETAIN_METADATA)
        else:
            route(spec, table, selected, uncertain_count)
        if table in {
            "affective_transitions",
            "satori_inclinations",
            "personality_checkpoint_approvals",
            "personality_restore_events",
        }:
            route(spec, table, list(rows), 0, retained=_NUMERIC)
    return tuple(routes), frozenset(blockers), routing_hash.hexdigest()
