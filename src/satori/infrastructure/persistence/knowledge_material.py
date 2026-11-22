"""Closed, pure fingerprints for owned knowledge bodies and retained trajectories."""

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from satori.domain.erasure_knowledge import (
    KnowledgeBodyAction,
    KnowledgeBodyKind,
    KnowledgeBodyTarget,
)
from satori.domain.source_policy import SourcePolicyError

if TYPE_CHECKING:
    from satori.infrastructure.persistence.erasure_inventory import Row, Store


@dataclass(frozen=True, slots=True)
class KnowledgeOwnerSpec:
    table: str
    key: str
    evidence_table: str
    evidence_key: str
    revision_table: str
    content_columns: tuple[str, ...]


KNOWLEDGE_OWNER_SPECS: dict[KnowledgeBodyKind, KnowledgeOwnerSpec] = {
    KnowledgeBodyKind.SEMANTIC_CLAIM: KnowledgeOwnerSpec(
        "semantic_claims",
        "claim_id",
        "semantic_claim_evidence",
        "semantic_evidence_id",
        "semantic_claim_revisions",
        ("value", "normalized_value"),
    ),
    KnowledgeBodyKind.USER_MODEL_CLAIM: KnowledgeOwnerSpec(
        "user_model_claims",
        "claim_id",
        "user_model_claim_evidence",
        "evidence_id",
        "user_model_claim_revisions",
        ("value", "normalized_value"),
    ),
    KnowledgeBodyKind.WORLD_MODEL_CLAIM: KnowledgeOwnerSpec(
        "world_model_claims",
        "claim_id",
        "world_model_claim_evidence",
        "evidence_id",
        "world_model_claim_revisions",
        ("value", "normalized_value", "subject_label", "normalized_subject_label"),
    ),
    KnowledgeBodyKind.POSITION: KnowledgeOwnerSpec(
        "satori_positions",
        "position_id",
        "satori_position_evidence",
        "evidence_id",
        "satori_position_revisions",
        ("proposition", "normalized_proposition"),
    ),
    KnowledgeBodyKind.INCLINATION: KnowledgeOwnerSpec(
        "satori_inclinations",
        "inclination_id",
        "satori_inclination_evidence",
        "evidence_id",
        "satori_inclination_revisions",
        ("topic", "normalized_topic", "alternative_topic", "normalized_alternative_topic"),
    ),
}


def knowledge_material(
    kind: KnowledgeBodyKind,
    aggregate_row: "Row",
    evidence_rows: tuple["Row", ...],
    revision_rows: tuple["Row", ...],
    *,
    terminal: bool,
) -> dict[str, object]:
    """Retain exact physical values; model only the approved body transition."""
    spec = KNOWLEDGE_OWNER_SPECS[kind]
    required = {spec.key, "aggregate_version", "body_state", "body_action_id"} | set(
        spec.content_columns
    )
    if not required <= aggregate_row.keys():
        raise SourcePolicyError("knowledge_body_target_unproven")
    aggregate = dict(aggregate_row)
    # The action ID is assigned after preview; actual receipt binding is verified separately.
    aggregate["body_action_id"] = None
    if terminal:
        aggregate["body_state"] = "erased"
        for column in spec.content_columns:
            aggregate[column] = None
    evidence = [dict(row) for row in sorted(evidence_rows, key=lambda r: str(r[spec.evidence_key]))]
    revisions = [dict(row) for row in sorted(revision_rows, key=lambda r: str(r["revision_id"]))]
    if any(row.get(spec.key) != aggregate[spec.key] for row in (*evidence, *revisions)):
        raise SourcePolicyError("knowledge_body_target_unproven")
    if len({row[spec.evidence_key] for row in evidence}) != len(evidence) or len(
        {row["revision_id"] for row in revisions}
    ) != len(revisions):
        raise SourcePolicyError("knowledge_body_target_unproven")
    if kind == KnowledgeBodyKind.POSITION:
        for row in evidence:
            if not {"quote", "normalized_signature"} <= row.keys():
                raise SourcePolicyError("knowledge_body_target_unproven")
            if terminal:
                row["quote"] = row["normalized_signature"] = None
    return {
        "kind": kind.value,
        "aggregate": aggregate,
        "evidence": evidence,
        "revisions": revisions,
    }


def knowledge_material_digest(
    kind: KnowledgeBodyKind,
    aggregate_row: "Row",
    evidence_rows: tuple["Row", ...],
    revision_rows: tuple["Row", ...],
    *,
    terminal: bool,
) -> str:
    material = knowledge_material(
        kind, aggregate_row, evidence_rows, revision_rows, terminal=terminal
    )
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(slots=True)
class KnowledgeMaterialIndex:
    """One bounded census index; each target reads only its own evidence/history."""

    aggregates: dict[tuple[KnowledgeBodyKind, str], "Row"]
    evidence: dict[tuple[KnowledgeBodyKind, str], tuple["Row", ...]]
    revisions: dict[tuple[KnowledgeBodyKind, str], tuple["Row", ...]]

    @classmethod
    def build(cls, store: "Store") -> "KnowledgeMaterialIndex":
        aggregates: dict[tuple[KnowledgeBodyKind, str], Row] = {}
        evidence: dict[tuple[KnowledgeBodyKind, str], tuple[Row, ...]] = {}
        revisions: dict[tuple[KnowledgeBodyKind, str], tuple[Row, ...]] = {}
        for kind, spec in KNOWLEDGE_OWNER_SPECS.items():
            for row in store.get(spec.table, ()):
                aggregates[kind, str(row[spec.key])] = row
            for table, target in (
                (spec.evidence_table, evidence),
                (spec.revision_table, revisions),
            ):
                grouped: dict[str, list[Row]] = {}
                for row in store.get(table, ()):
                    grouped.setdefault(str(row[spec.key]), []).append(row)
                target.update({(kind, key): tuple(rows) for key, rows in grouped.items()})
        return cls(aggregates, evidence, revisions)

    def material(
        self, kind: KnowledgeBodyKind, aggregate_id: str, *, terminal: bool
    ) -> dict[str, object]:
        key = kind, aggregate_id
        if key not in self.aggregates:
            raise SourcePolicyError("knowledge_body_target_unproven")
        return knowledge_material(
            kind,
            self.aggregates[key],
            self.evidence.get(key, ()),
            self.revisions.get(key, ()),
            terminal=terminal,
        )

    def digest(self, kind: KnowledgeBodyKind, aggregate_id: str, *, terminal: bool) -> str:
        return hashlib.sha256(
            json.dumps(
                self.material(kind, aggregate_id, terminal=terminal),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()


def knowledge_target_terminal_proven(
    store: "Store",
    action: KnowledgeBodyAction,
    target: KnowledgeBodyTarget,
    *,
    index: KnowledgeMaterialIndex | None = None,
) -> bool:
    """Prove actual NULL material and its exact approved owner/action binding."""
    material = index if index is not None else KnowledgeMaterialIndex.build(store)
    row = material.aggregates.get((target.kind, target.aggregate_id))
    if (
        row is None
        or row.get("body_state") != "erased"
        or row.get("body_action_id") != action.action_id
        or row.get("aggregate_version") != target.aggregate_version
        or any(
            row.get(column) is not None
            for column in KNOWLEDGE_OWNER_SPECS[target.kind].content_columns
        )
    ):
        return False
    try:
        return (
            material.digest(target.kind, target.aggregate_id, terminal=False)
            == target.terminal_digest
        )
    except (SourcePolicyError, ValueError, TypeError, KeyError):
        return False
