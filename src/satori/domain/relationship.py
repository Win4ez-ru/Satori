"""Slow, bounded, evidence-based relationship dynamics for one counterparty."""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.core.relationship import RelationshipAppraisalProposal
from satori.domain.validation import aware_utc, non_blank, positive_version, unit_interval

RELATIONSHIP_SCHEMA_VERSION = 1
RELATIONSHIP_POLICY_VERSION = 1
RELATIONSHIP_APPRAISAL_SCHEMA_VERSION = 1
MIN_RELATIONSHIP_CONFIDENCE = 0.45


class RelationshipEventCategory(StrEnum):
    """Small non-overlapping v1 taxonomy; disagreement is deliberately absent."""

    NEUTRAL_CONTACT = "neutral_contact"
    WARM_ENGAGEMENT = "warm_engagement"
    RESPECTFUL_ENGAGEMENT = "respectful_engagement"
    COLLABORATIVE_REASONING = "collaborative_reasoning"
    MEANINGFUL_DISCLOSURE = "meaningful_disclosure"
    RELIABILITY_POSITIVE = "reliability_positive"
    REPAIR_ATTEMPT = "repair_attempt"
    BOUNDARY_RESPECT = "boundary_respect"
    DISMISSIVENESS = "dismissiveness"
    HOSTILITY = "hostility"
    RELIABILITY_NEGATIVE = "reliability_negative"
    BOUNDARY_PRESSURE = "boundary_pressure"


class RelationshipDecisionKind(StrEnum):
    APPLIED = "applied"
    SKIPPED = "skipped"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RelationshipVector:
    familiarity: float
    trust: float
    comfort: float
    closeness: float
    intellectual_respect: float
    affection: float

    def __post_init__(self) -> None:
        for key, value in self.as_mapping().items():
            unit_interval(value, key)

    @staticmethod
    def field_names() -> tuple[str, ...]:
        return (
            "familiarity",
            "trust",
            "comfort",
            "closeness",
            "intellectual_respect",
            "affection",
        )

    def as_mapping(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in self.field_names()}

    @classmethod
    def from_mapping(cls, values: dict[str, float]) -> "RelationshipVector":
        if set(values) != set(cls.field_names()):
            raise ValueError("relationship vector has unknown or missing dimensions")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class RelationshipState:
    relationship_id: str
    identity_id: str
    counterparty_id: str
    schema_version: int
    state_version: int
    policy_version: int
    vector: RelationshipVector
    processed_interaction_count: int
    qualified_interaction_count: int
    distinct_session_count: int
    positive_evidence_count: int
    negative_evidence_count: int
    updated_at: datetime

    def __post_init__(self) -> None:
        for name in ("relationship_id", "identity_id", "counterparty_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        positive_version(self.schema_version, "relationship schema_version")
        positive_version(self.state_version, "relationship state_version")
        positive_version(self.policy_version, "relationship policy_version")
        for name in (
            "processed_interaction_count",
            "qualified_interaction_count",
            "distinct_session_count",
            "positive_evidence_count",
            "negative_evidence_count",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        object.__setattr__(self, "updated_at", aware_utc(self.updated_at, "updated_at"))

    @property
    def maturity(self) -> float:
        """Evidence breadth, not a relationship feeling or confidence claim."""

        interaction_mass = min(self.qualified_interaction_count / 40.0, 1.0)
        session_breadth = min(self.distinct_session_count / 8.0, 1.0)
        return 0.65 * interaction_mass + 0.35 * session_breadth


@dataclass(frozen=True, slots=True)
class RelationshipDelta:
    familiarity: float
    trust: float
    comfort: float
    closeness: float
    intellectual_respect: float
    affection: float

    def __post_init__(self) -> None:
        for key, value in self.as_mapping().items():
            if isinstance(value, bool) or not math.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError(f"relationship {key} delta must be finite and between -1 and 1")

    def as_mapping(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in RelationshipVector.field_names()}

    @classmethod
    def from_mapping(cls, values: dict[str, float]) -> "RelationshipDelta":
        if set(values) != set(RelationshipVector.field_names()):
            raise ValueError("relationship delta has unknown or missing dimensions")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class RelationshipDecision:
    decision_id: str
    relationship_id: str
    interaction_id: str
    source_user_message_id: str
    session_id: str
    trace_id: str
    kind: RelationshipDecisionKind
    reason_code: str
    categories: tuple[RelationshipEventCategory, ...]
    confidence: float
    provider: str
    model: str
    appraisal_method: str
    appraisal_schema_version: int
    policy_version: int
    decided_at: datetime
    transition_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "decision_id",
            "relationship_id",
            "interaction_id",
            "source_user_message_id",
            "session_id",
            "trace_id",
            "reason_code",
            "provider",
            "model",
            "appraisal_method",
        ):
            maximum = 256 if name == "model" else 128
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=maximum))
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("relationship decision categories must be unique")
        unit_interval(self.confidence, "relationship decision confidence")
        positive_version(self.appraisal_schema_version, "appraisal_schema_version")
        positive_version(self.policy_version, "relationship policy_version")
        object.__setattr__(self, "decided_at", aware_utc(self.decided_at, "decided_at"))
        if (self.kind is RelationshipDecisionKind.APPLIED) != (self.transition_id is not None):
            raise ValueError("only applied relationship decisions reference a transition")


@dataclass(frozen=True, slots=True)
class RelationshipTransition:
    transition_id: str
    relationship_id: str
    interaction_id: str
    source_user_message_id: str
    session_id: str
    trace_id: str
    categories: tuple[RelationshipEventCategory, ...]
    confidence: float
    before: RelationshipState
    delta: RelationshipDelta
    after: RelationshipState
    provider: str
    model: str
    appraisal_method: str
    appraisal_schema_version: int
    policy_version: int
    committed_at: datetime

    def __post_init__(self) -> None:
        if self.after.state_version != self.before.state_version + 1:
            raise ValueError("relationship transition must increment state_version exactly once")
        if (
            self.before.relationship_id != self.relationship_id
            or self.after.relationship_id != self.relationship_id
        ):
            raise ValueError("relationship transition snapshots must match aggregate")
        object.__setattr__(self, "committed_at", aware_utc(self.committed_at, "committed_at"))


@dataclass(frozen=True, slots=True)
class RelationshipMutation:
    """Owner result before persistence; every source gets a terminal decision."""

    kind: RelationshipDecisionKind
    reason_code: str
    state_after_processing: RelationshipState
    delta: RelationshipDelta | None
    categories: tuple[RelationshipEventCategory, ...]


POSITIVE_CATEGORIES = frozenset(
    {
        RelationshipEventCategory.WARM_ENGAGEMENT,
        RelationshipEventCategory.RESPECTFUL_ENGAGEMENT,
        RelationshipEventCategory.COLLABORATIVE_REASONING,
        RelationshipEventCategory.MEANINGFUL_DISCLOSURE,
        RelationshipEventCategory.RELIABILITY_POSITIVE,
        RelationshipEventCategory.REPAIR_ATTEMPT,
        RelationshipEventCategory.BOUNDARY_RESPECT,
    }
)
NEGATIVE_CATEGORIES = frozenset(
    {
        RelationshipEventCategory.DISMISSIVENESS,
        RelationshipEventCategory.HOSTILITY,
        RelationshipEventCategory.RELIABILITY_NEGATIVE,
        RelationshipEventCategory.BOUNDARY_PRESSURE,
    }
)

# Desired pre-saturation impulses. The owner applies confidence, maturity ceilings,
# per-event caps, and cumulative per-session caps after combining categories.
EVENT_IMPULSES: dict[RelationshipEventCategory, dict[str, float]] = {
    RelationshipEventCategory.NEUTRAL_CONTACT: {"familiarity": 0.010},
    RelationshipEventCategory.WARM_ENGAGEMENT: {
        "familiarity": 0.010,
        "comfort": 0.010,
        "affection": 0.008,
    },
    RelationshipEventCategory.RESPECTFUL_ENGAGEMENT: {
        "familiarity": 0.010,
        "comfort": 0.008,
        "intellectual_respect": 0.010,
    },
    RelationshipEventCategory.COLLABORATIVE_REASONING: {
        "familiarity": 0.010,
        "comfort": 0.006,
        "closeness": 0.006,
        "intellectual_respect": 0.012,
    },
    RelationshipEventCategory.MEANINGFUL_DISCLOSURE: {
        "familiarity": 0.010,
        "comfort": 0.008,
        "closeness": 0.010,
        "affection": 0.006,
    },
    RelationshipEventCategory.RELIABILITY_POSITIVE: {"trust": 0.008, "comfort": 0.005},
    RelationshipEventCategory.REPAIR_ATTEMPT: {"trust": 0.004, "comfort": 0.007},
    RelationshipEventCategory.BOUNDARY_RESPECT: {"trust": 0.007, "comfort": 0.008},
    RelationshipEventCategory.DISMISSIVENESS: {
        "comfort": -0.018,
        "intellectual_respect": -0.012,
        "affection": -0.008,
    },
    RelationshipEventCategory.HOSTILITY: {
        "trust": -0.020,
        "comfort": -0.030,
        "closeness": -0.015,
        "intellectual_respect": -0.018,
        "affection": -0.015,
    },
    RelationshipEventCategory.RELIABILITY_NEGATIVE: {"trust": -0.026, "comfort": -0.012},
    RelationshipEventCategory.BOUNDARY_PRESSURE: {
        "trust": -0.018,
        "comfort": -0.030,
        "closeness": -0.012,
        "affection": -0.012,
    },
}
PER_EVENT_CAP = {
    "familiarity": 0.010,
    "trust": 0.015,
    "comfort": 0.020,
    "closeness": 0.010,
    "intellectual_respect": 0.015,
    "affection": 0.010,
}
SESSION_POSITIVE_CAP = {
    "familiarity": 0.080,
    "trust": 0.040,
    "comfort": 0.050,
    "closeness": 0.035,
    "intellectual_respect": 0.050,
    "affection": 0.035,
}
SESSION_NEGATIVE_CAP = {
    "familiarity": 0.0,
    "trust": 0.120,
    "comfort": 0.150,
    "closeness": 0.080,
    "intellectual_respect": 0.100,
    "affection": 0.080,
}


def initial_relationship(
    relationship_id: str,
    identity_id: str,
    counterparty_id: str,
    *,
    initialized_at: datetime,
) -> RelationshipState:
    """Little evidence: neutral midpoints are not evidence of distrust/disrespect."""

    return RelationshipState(
        relationship_id=relationship_id,
        identity_id=identity_id,
        counterparty_id=counterparty_id,
        schema_version=RELATIONSHIP_SCHEMA_VERSION,
        state_version=1,
        policy_version=RELATIONSHIP_POLICY_VERSION,
        vector=RelationshipVector(0.0, 0.5, 0.5, 0.0, 0.5, 0.0),
        processed_interaction_count=0,
        qualified_interaction_count=0,
        distinct_session_count=0,
        positive_evidence_count=0,
        negative_evidence_count=0,
        updated_at=initialized_at,
    )


class RelationshipManager:
    """Sole deterministic owner of relationship vector mutations."""

    def apply(
        self,
        state: RelationshipState,
        proposal: RelationshipAppraisalProposal,
        *,
        session_id: str,
        session_delta: RelationshipDelta,
        session_is_new_evidence: bool,
        observed_at: datetime,
    ) -> RelationshipMutation:
        try:
            categories = tuple(RelationshipEventCategory(item) for item in proposal.categories)
        except ValueError:
            return self._terminal_without_delta(
                state, "unknown_category", (), observed_at=observed_at
            )
        qualified = any(
            item is not RelationshipEventCategory.NEUTRAL_CONTACT for item in categories
        )
        counters = self._updated_counters(
            state,
            qualified=qualified,
            session_is_new_evidence=session_is_new_evidence,
            categories=categories,
            observed_at=observed_at,
        )
        if proposal.confidence < MIN_RELATIONSHIP_CONFIDENCE:
            return RelationshipMutation(
                kind=RelationshipDecisionKind.SKIPPED,
                reason_code="low_confidence",
                state_after_processing=counters,
                delta=None,
                categories=categories,
            )

        raw = {key: 0.0 for key in RelationshipVector.field_names()}
        for category in categories:
            for key, value in EVENT_IMPULSES[category].items():
                raw[key] += value * proposal.confidence

        maturity = counters.maturity
        current = state.vector.as_mapping()
        session_used = session_delta.as_mapping()
        applied: dict[str, float] = {}
        for key, impulse in raw.items():
            if impulse >= 0.0:
                ceiling = self._positive_ceiling(key, maturity)
                saturated = impulse * max(0.0, ceiling - current[key])
                remaining = max(0.0, SESSION_POSITIVE_CAP[key] - max(0.0, session_used[key]))
                applied[key] = min(saturated, PER_EVENT_CAP[key], remaining)
            else:
                saturated = impulse * current[key]
                remaining = max(0.0, SESSION_NEGATIVE_CAP[key] - max(0.0, -session_used[key]))
                applied[key] = max(saturated, -PER_EVENT_CAP[key], -remaining)
        # Familiarity is accumulated history and does not decrease from ordinary events.
        applied["familiarity"] = max(0.0, applied["familiarity"])
        if all(abs(value) <= 1e-12 for value in applied.values()):
            return RelationshipMutation(
                kind=RelationshipDecisionKind.SKIPPED,
                reason_code="bounded_no_effect",
                state_after_processing=counters,
                delta=None,
                categories=categories,
            )
        vector = RelationshipVector.from_mapping(
            {key: min(1.0, max(0.0, current[key] + applied[key])) for key in current}
        )
        after = RelationshipState(
            relationship_id=state.relationship_id,
            identity_id=state.identity_id,
            counterparty_id=state.counterparty_id,
            schema_version=state.schema_version,
            state_version=state.state_version + 1,
            policy_version=RELATIONSHIP_POLICY_VERSION,
            vector=vector,
            processed_interaction_count=counters.processed_interaction_count,
            qualified_interaction_count=counters.qualified_interaction_count,
            distinct_session_count=counters.distinct_session_count,
            positive_evidence_count=counters.positive_evidence_count,
            negative_evidence_count=counters.negative_evidence_count,
            updated_at=observed_at,
        )
        return RelationshipMutation(
            kind=RelationshipDecisionKind.APPLIED,
            reason_code="bounded_relationship_event_applied",
            state_after_processing=after,
            delta=RelationshipDelta.from_mapping(applied),
            categories=categories,
        )

    @staticmethod
    def _positive_ceiling(key: str, maturity: float) -> float:
        if key == "familiarity":
            return 1.0
        if key in {"closeness", "affection"}:
            return maturity
        return 0.5 + 0.5 * maturity

    @staticmethod
    def _updated_counters(
        state: RelationshipState,
        *,
        qualified: bool,
        session_is_new_evidence: bool,
        categories: tuple[RelationshipEventCategory, ...],
        observed_at: datetime,
    ) -> RelationshipState:
        return RelationshipState(
            relationship_id=state.relationship_id,
            identity_id=state.identity_id,
            counterparty_id=state.counterparty_id,
            schema_version=state.schema_version,
            state_version=state.state_version,
            policy_version=state.policy_version,
            vector=state.vector,
            processed_interaction_count=state.processed_interaction_count + 1,
            qualified_interaction_count=state.qualified_interaction_count + int(qualified),
            distinct_session_count=state.distinct_session_count
            + int(qualified and session_is_new_evidence),
            positive_evidence_count=state.positive_evidence_count
            + int(any(item in POSITIVE_CATEGORIES for item in categories)),
            negative_evidence_count=state.negative_evidence_count
            + int(any(item in NEGATIVE_CATEGORIES for item in categories)),
            updated_at=observed_at,
        )

    @staticmethod
    def _terminal_without_delta(
        state: RelationshipState,
        reason: str,
        categories: tuple[RelationshipEventCategory, ...],
        *,
        observed_at: datetime,
    ) -> RelationshipMutation:
        counters = RelationshipState(
            relationship_id=state.relationship_id,
            identity_id=state.identity_id,
            counterparty_id=state.counterparty_id,
            schema_version=state.schema_version,
            state_version=state.state_version,
            policy_version=state.policy_version,
            vector=state.vector,
            processed_interaction_count=state.processed_interaction_count + 1,
            qualified_interaction_count=state.qualified_interaction_count,
            distinct_session_count=state.distinct_session_count,
            positive_evidence_count=state.positive_evidence_count,
            negative_evidence_count=state.negative_evidence_count,
            updated_at=observed_at,
        )
        return RelationshipMutation(
            RelationshipDecisionKind.REJECTED,
            reason,
            counters,
            None,
            categories,
        )
