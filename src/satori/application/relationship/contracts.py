"""Immutable relationship read and expression projections."""

from dataclasses import dataclass
from datetime import datetime

from satori.domain.relationship import RelationshipState, RelationshipTransition

RELATIONSHIP_EXPRESSION_CONTEXT_SCHEMA_VERSION = 2
_MATURITY_LEVELS = frozenset({"low", "developing", "established"})
_UNBOUNDED_AXIS_LEVELS = frozenset({"low", "emerging", "moderate", "high", "very_high"})
_CENTERED_AXIS_LEVELS = frozenset({"very_low", "low", "uncertain", "moderate", "high", "very_high"})


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
    recent_strain: bool = False

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {
            1,
            RELATIONSHIP_EXPRESSION_CONTEXT_SCHEMA_VERSION,
        }:
            raise ValueError("relationship expression schema_version is not supported")
        if type(self.state_version) is not int or self.state_version < 1:
            raise ValueError("relationship expression state_version must be positive")
        if self.maturity not in _MATURITY_LEVELS:
            raise ValueError("relationship expression maturity is not supported")
        if any(
            level not in _UNBOUNDED_AXIS_LEVELS
            for level in (self.familiarity, self.closeness, self.affection)
        ):
            raise ValueError("relationship expression unbounded axis level is not supported")
        if any(
            level not in _CENTERED_AXIS_LEVELS
            for level in (self.trust, self.comfort, self.intellectual_respect)
        ):
            raise ValueError("relationship expression centered axis level is not supported")
        if type(self.recent_strain) is not bool:
            raise ValueError("relationship recent_strain must be boolean")
        if self.schema_version == 1 and self.recent_strain:
            raise ValueError("relationship expression v1 cannot contain recent strain")


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
class RelationshipBackfillReport:
    """Bounded oldest-first recovery summary without raw relationship evidence."""

    considered: int
    attempted: int
    applied: int
    skipped: int
    rejected: int
    replayed: int
    failed: int


@dataclass(frozen=True, slots=True)
class RelationshipHistory:
    transitions: tuple[RelationshipTransition, ...]
