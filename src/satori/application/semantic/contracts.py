"""Bounded semantic-recall contracts kept separate from episodic retrieval."""

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievedSemanticClaim:
    """One active claim selected through retrieved episodic evidence."""

    claim_id: str
    subject: str
    predicate: str
    value_kind: str
    value: str | float | bool
    polarity: bool
    claim_kind: str
    confidence: float
    evidence_memory_ids: tuple[str, ...]
    root_message_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievedSemanticContext:
    """Deterministic bounded semantic layer projection for one conversation turn."""

    status: str
    claims: tuple[RetrievedSemanticClaim, ...]

    @property
    def claim_ids(self) -> tuple[str, ...]:
        return tuple(claim.claim_id for claim in self.claims)

    @property
    def grounding_ids(self) -> tuple[str, ...]:
        return self.claim_ids


def semantic_context_json(context: RetrievedSemanticContext) -> str:
    """Canonical untrusted JSON without source quotes or free-form claim text."""

    payload = {
        "schema_version": 1,
        "status": context.status,
        "claims": [
            {
                "claim_id": claim.claim_id,
                "subject": claim.subject,
                "predicate": claim.predicate,
                "value_kind": claim.value_kind,
                "value": claim.value,
                "polarity": claim.polarity,
                "claim_kind": claim.claim_kind,
                "confidence": claim.confidence,
                "evidence_memory_ids": list(claim.evidence_memory_ids),
                "root_message_ids": list(claim.root_message_ids),
            }
            for claim in context.claims
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
