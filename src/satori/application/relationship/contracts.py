"""Immutable relationship read and expression projections."""

from dataclasses import dataclass
from datetime import datetime

from satori.domain.relationship import RelationshipState, RelationshipTransition


@dataclass(frozen=True, slots=True)
class RelationshipExpressionContext:
    """Compact trusted qualitative projection; normal generation never sees raw axes."""

    schema_version: int
    state_version: int
    maturity: str
    familiarity: str
    trust: str
    comfort: str
    closeness: str
    intellectual_respect: str
    affection: str


@dataclass(frozen=True, slots=True)
class RelationshipStatus:
    state: RelationshipState
    last_transition_id: str | None
    last_transition_at: datetime | None


@dataclass(frozen=True, slots=True)
class RelationshipProcessingReport:
    interaction_id: str
    decision_kind: str
    reason_code: str
    relationship_appraisal_ms: float
    relationship_commit_ms: float
    total_ms: float
    provider_metrics: dict[str, int | float | None] | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class RelationshipHistory:
    transitions: tuple[RelationshipTransition, ...]
