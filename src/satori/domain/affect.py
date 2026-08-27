"""Bounded persistent affect, deterministic appraisal mapping, decay, and mood."""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.core.affect import AffectiveAppraisalProposal
from satori.domain.personality import Personality
from satori.domain.validation import aware_utc, non_blank, positive_version, unit_interval

AFFECTIVE_STATE_SCHEMA_VERSION = 1
APPRAISAL_SCHEMA_VERSION = 1
EMOTION_POLICY_VERSION = 1
MOOD_POLICY_VERSION = 1
MIN_APPRAISAL_CONFIDENCE = 0.35
_ZERO_TOLERANCE = 1e-12


def _signed_unit_interval(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and between -1 and 1")
    return value


def _finite_delta(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and between -1 and 1")
    return value


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


@dataclass(frozen=True, slots=True)
class DimensionPolicy:
    """One authoritative resting baseline, half-life, and event cap."""

    baseline: float
    half_life_seconds: float
    max_absolute_delta: float
    signed: bool = False

    def __post_init__(self) -> None:
        if self.signed:
            _signed_unit_interval(self.baseline, "dimension baseline")
        else:
            unit_interval(self.baseline, "dimension baseline")
        if (
            isinstance(self.half_life_seconds, bool)
            or not math.isfinite(self.half_life_seconds)
            or self.half_life_seconds <= 0.0
        ):
            raise ValueError("dimension half-life must be finite and positive")
        if (
            isinstance(self.max_absolute_delta, bool)
            or not math.isfinite(self.max_absolute_delta)
            or not 0.0 < self.max_absolute_delta <= 1.0
        ):
            raise ValueError("dimension max delta must be finite and in (0, 1]")


@dataclass(frozen=True, slots=True)
class AffectPolicy:
    """Single versioned source for every meaningful Stage 7 tuning parameter."""

    emotion_policy_version: int
    mood_policy_version: int
    fast: tuple[tuple[str, DimensionPolicy], ...]
    mood: tuple[tuple[str, DimensionPolicy], ...]
    mood_valence_gain: float
    mood_energy_arousal_gain: float
    mood_energy_interest_gain: float
    mood_energy_amusement_gain: float
    mood_tension_tension_gain: float
    mood_tension_concern_gain: float
    mood_tension_frustration_gain: float

    def __post_init__(self) -> None:
        positive_version(self.emotion_policy_version, "emotion policy version")
        positive_version(self.mood_policy_version, "mood policy version")
        for collection, expected in (
            (self.fast, set(FastAffectiveState.field_names())),
            (self.mood, set(MoodState.field_names())),
        ):
            keys = tuple(key for key, _ in collection)
            if len(keys) != len(set(keys)) or set(keys) != expected:
                raise ValueError("affect policy dimensions must exactly match the state schema")
        for field_name in (
            "mood_valence_gain",
            "mood_energy_arousal_gain",
            "mood_energy_interest_gain",
            "mood_energy_amusement_gain",
            "mood_tension_tension_gain",
            "mood_tension_concern_gain",
            "mood_tension_frustration_gain",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be finite and between 0 and 1")

    def fast_dimension(self, key: str) -> DimensionPolicy:
        return dict(self.fast)[key]

    def mood_dimension(self, key: str) -> DimensionPolicy:
        return dict(self.mood)[key]


@dataclass(frozen=True, slots=True)
class FastAffectiveState:
    """Authoritative continuous fast emotional state."""

    valence: float
    arousal: float
    tension: float
    curiosity: float
    interest: float
    amusement: float
    concern: float
    frustration: float
    situational_confidence: float

    def __post_init__(self) -> None:
        _signed_unit_interval(self.valence, "valence")
        for field_name in self.field_names()[1:]:
            unit_interval(getattr(self, field_name), field_name)

    @staticmethod
    def field_names() -> tuple[str, ...]:
        return (
            "valence",
            "arousal",
            "tension",
            "curiosity",
            "interest",
            "amusement",
            "concern",
            "frustration",
            "situational_confidence",
        )

    def as_mapping(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in self.field_names()}

    @classmethod
    def from_mapping(cls, values: dict[str, float]) -> "FastAffectiveState":
        if set(values) != set(cls.field_names()):
            raise ValueError("fast affect mapping has unknown or missing dimensions")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class MoodState:
    """Slower medium-term background state, distinct from fast affect."""

    valence: float
    energy: float
    tension: float

    def __post_init__(self) -> None:
        _signed_unit_interval(self.valence, "mood valence")
        unit_interval(self.energy, "mood energy")
        unit_interval(self.tension, "mood tension")

    @staticmethod
    def field_names() -> tuple[str, ...]:
        return ("valence", "energy", "tension")

    def as_mapping(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in self.field_names()}

    @classmethod
    def from_mapping(cls, values: dict[str, float]) -> "MoodState":
        if set(values) != set(cls.field_names()):
            raise ValueError("mood mapping has unknown or missing dimensions")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class AffectiveDelta:
    """Signed event deltas after deterministic modulation and caps."""

    valence: float
    arousal: float
    tension: float
    curiosity: float
    interest: float
    amusement: float
    concern: float
    frustration: float
    situational_confidence: float

    def __post_init__(self) -> None:
        for field_name in FastAffectiveState.field_names():
            _finite_delta(getattr(self, field_name), f"{field_name} delta")

    def as_mapping(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in FastAffectiveState.field_names()}

    @classmethod
    def from_mapping(cls, values: dict[str, float]) -> "AffectiveDelta":
        if set(values) != set(FastAffectiveState.field_names()):
            raise ValueError("affective delta has unknown or missing dimensions")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class MoodDelta:
    """Signed bounded mood impulse caused by an accepted fast-state event."""

    valence: float
    energy: float
    tension: float

    def __post_init__(self) -> None:
        for field_name in MoodState.field_names():
            _finite_delta(getattr(self, field_name), f"mood {field_name} delta")

    def as_mapping(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in MoodState.field_names()}

    @classmethod
    def from_mapping(cls, values: dict[str, float]) -> "MoodDelta":
        if set(values) != set(MoodState.field_names()):
            raise ValueError("mood delta has unknown or missing dimensions")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class AffectiveStateSnapshot:
    """Immutable current projection at one explicit UTC instant."""

    identity_id: str
    schema_version: int
    state_version: int
    mood_version: int
    as_of: datetime
    emotion_policy_version: int
    appraisal_schema_version: int
    mood_policy_version: int
    fast: FastAffectiveState
    mood: MoodState

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "identity_id", non_blank(self.identity_id, "identity_id", maximum=128)
        )
        positive_version(self.schema_version, "affective state schema_version")
        positive_version(self.state_version, "affective state_version")
        positive_version(self.mood_version, "mood version")
        object.__setattr__(self, "as_of", aware_utc(self.as_of, "affective state as_of"))
        positive_version(self.emotion_policy_version, "emotion policy version")
        positive_version(self.appraisal_schema_version, "appraisal schema version")
        positive_version(self.mood_policy_version, "mood policy version")


class AppraisalDecisionKind(StrEnum):
    """Deterministic owner outcome before persistence."""

    APPLIED = "applied"
    SKIPPED = "skipped"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class AffectiveTransitionDraft:
    """Owner-approved tentative mutation used for expression and atomic finalize."""

    proposal: AffectiveAppraisalProposal
    before: AffectiveStateSnapshot
    after: AffectiveStateSnapshot
    applied_delta: AffectiveDelta
    mood_delta: MoodDelta


@dataclass(frozen=True, slots=True)
class AppraisalDecision:
    """Accepted, skipped, or rejected proposal with no persistence capability."""

    kind: AppraisalDecisionKind
    reason_code: str
    materialized_state: AffectiveStateSnapshot
    transition: AffectiveTransitionDraft | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reason_code", non_blank(self.reason_code, "reason_code", maximum=64)
        )
        if (self.kind is AppraisalDecisionKind.APPLIED) != (self.transition is not None):
            raise ValueError("only applied appraisal decisions contain a transition")


@dataclass(frozen=True, slots=True)
class AffectiveTransition:
    """Durable source-linked state transition with structured appraisal metadata."""

    transition_id: str
    identity_id: str
    interaction_id: str
    source_message_id: str
    trace_id: str
    proposal: AffectiveAppraisalProposal
    before: AffectiveStateSnapshot
    after: AffectiveStateSnapshot
    applied_delta: AffectiveDelta
    mood_delta: MoodDelta
    provider: str
    model: str
    appraisal_method: str
    committed_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "transition_id",
            "identity_id",
            "interaction_id",
            "source_message_id",
            "trace_id",
            "provider",
            "model",
            "appraisal_method",
        ):
            maximum = 256 if field_name == "model" else 128
            object.__setattr__(
                self,
                field_name,
                non_blank(getattr(self, field_name), field_name, maximum=maximum),
            )
        object.__setattr__(
            self, "committed_at", aware_utc(self.committed_at, "transition committed_at")
        )
        if (
            self.identity_id != self.before.identity_id
            or self.identity_id != self.after.identity_id
        ):
            raise ValueError("transition snapshots must belong to its identity")
        if self.after.state_version != self.before.state_version + 1:
            raise ValueError("transition must increment affective state_version exactly once")
        if self.after.mood_version != self.before.mood_version + 1:
            raise ValueError("transition must increment mood_version exactly once")
        if self.after.as_of != self.before.as_of:
            raise ValueError("transition snapshots must use the same materialization instant")
        if self.committed_at < self.after.as_of:
            raise ValueError("transition cannot commit before its appraisal instant")


@dataclass(frozen=True, slots=True)
class AffectiveStatus:
    """Immutable developer read model with current materialization and last transition."""

    state: AffectiveStateSnapshot
    last_transition_id: str | None
    last_transition_at: datetime | None


class AffectiveStateConflict(Exception):
    """The projected base version became stale before canonical finalize."""


AFFECT_POLICY_V1 = AffectPolicy(
    emotion_policy_version=EMOTION_POLICY_VERSION,
    mood_policy_version=MOOD_POLICY_VERSION,
    fast=(
        ("valence", DimensionPolicy(0.0, 45.0 * 60.0, 0.22, signed=True)),
        ("arousal", DimensionPolicy(0.12, 12.0 * 60.0, 0.18)),
        ("tension", DimensionPolicy(0.08, 30.0 * 60.0, 0.16)),
        ("curiosity", DimensionPolicy(0.18, 45.0 * 60.0, 0.15)),
        ("interest", DimensionPolicy(0.16, 90.0 * 60.0, 0.16)),
        ("amusement", DimensionPolicy(0.05, 5.0 * 60.0, 0.18)),
        ("concern", DimensionPolicy(0.08, 120.0 * 60.0, 0.18)),
        ("frustration", DimensionPolicy(0.04, 40.0 * 60.0, 0.14)),
        ("situational_confidence", DimensionPolicy(0.55, 180.0 * 60.0, 0.12)),
    ),
    mood=(
        ("valence", DimensionPolicy(0.0, 12.0 * 3600.0, 0.04, signed=True)),
        ("energy", DimensionPolicy(0.30, 8.0 * 3600.0, 0.03)),
        ("tension", DimensionPolicy(0.10, 10.0 * 3600.0, 0.03)),
    ),
    mood_valence_gain=0.12,
    mood_energy_arousal_gain=0.10,
    mood_energy_interest_gain=0.04,
    mood_energy_amusement_gain=0.03,
    mood_tension_tension_gain=0.12,
    mood_tension_concern_gain=0.08,
    mood_tension_frustration_gain=0.10,
)


def initial_affective_state(
    identity_id: str,
    *,
    initialized_at: datetime,
    policy: AffectPolicy = AFFECT_POLICY_V1,
) -> AffectiveStateSnapshot:
    """Create a deterministic neutral resting state without an LLM or seed mutation."""

    fast_baselines = {key: item.baseline for key, item in policy.fast}
    mood_baselines = {key: item.baseline for key, item in policy.mood}
    return AffectiveStateSnapshot(
        identity_id=identity_id,
        schema_version=AFFECTIVE_STATE_SCHEMA_VERSION,
        state_version=1,
        mood_version=1,
        as_of=initialized_at,
        emotion_policy_version=policy.emotion_policy_version,
        appraisal_schema_version=APPRAISAL_SCHEMA_VERSION,
        mood_policy_version=policy.mood_policy_version,
        fast=FastAffectiveState.from_mapping(fast_baselines),
        mood=MoodState.from_mapping(mood_baselines),
    )


def _decayed(value: float, baseline: float, elapsed_seconds: float, half_life: float) -> float:
    return float(baseline + (value - baseline) * 2.0 ** (-elapsed_seconds / half_life))


def materialize_affective_state(
    state: AffectiveStateSnapshot,
    *,
    at: datetime,
    policy: AffectPolicy = AFFECT_POLICY_V1,
) -> AffectiveStateSnapshot:
    """Pure lazy decay; repeated reads never persist or increment versions."""

    normalized_at = aware_utc(at, "materialization time")
    if normalized_at < state.as_of:
        raise ValueError("affective state cannot be materialized backwards in time")
    if (
        state.emotion_policy_version != policy.emotion_policy_version
        or state.mood_policy_version != policy.mood_policy_version
    ):
        raise ValueError("affective state policy version is unsupported")
    elapsed = (normalized_at - state.as_of).total_seconds()
    if elapsed == 0.0:
        return state
    fast = {
        key: _decayed(getattr(state.fast, key), item.baseline, elapsed, item.half_life_seconds)
        for key, item in policy.fast
    }
    mood = {
        key: _decayed(getattr(state.mood, key), item.baseline, elapsed, item.half_life_seconds)
        for key, item in policy.mood
    }
    return AffectiveStateSnapshot(
        identity_id=state.identity_id,
        schema_version=state.schema_version,
        state_version=state.state_version,
        mood_version=state.mood_version,
        as_of=normalized_at,
        emotion_policy_version=state.emotion_policy_version,
        appraisal_schema_version=state.appraisal_schema_version,
        mood_policy_version=state.mood_policy_version,
        fast=FastAffectiveState.from_mapping(fast),
        mood=MoodState.from_mapping(mood),
    )


class EmotionManager:
    """Single deterministic writer-owner policy for fast affect and mood."""

    def __init__(self, policy: AffectPolicy = AFFECT_POLICY_V1) -> None:
        self.policy = policy

    def evaluate(
        self,
        proposal: AffectiveAppraisalProposal,
        state: AffectiveStateSnapshot,
        personality: Personality,
        *,
        interaction_id: str,
        allowed_source_refs: tuple[str, ...],
        event_time: datetime,
    ) -> AppraisalDecision:
        """Validate provenance and derive one capped tentative transition or no-op."""

        current = materialize_affective_state(state, at=event_time, policy=self.policy)
        if proposal.schema_version != APPRAISAL_SCHEMA_VERSION:
            return AppraisalDecision(
                AppraisalDecisionKind.REJECTED,
                "unsupported_appraisal_schema",
                current,
            )
        allowed = set(allowed_source_refs)
        if interaction_id not in proposal.source_refs or not set(proposal.source_refs) <= allowed:
            return AppraisalDecision(
                AppraisalDecisionKind.REJECTED,
                "unknown_or_missing_source_reference",
                current,
            )
        if proposal.appraisal_confidence < MIN_APPRAISAL_CONFIDENCE:
            return AppraisalDecision(
                AppraisalDecisionKind.REJECTED,
                "appraisal_confidence_too_low",
                current,
            )

        deltas = self._derive_fast_delta(proposal, personality)
        next_fast: dict[str, float] = {}
        applied: dict[str, float] = {}
        for key, dimension in self.policy.fast:
            lower = -1.0 if dimension.signed else 0.0
            prior = getattr(current.fast, key)
            updated = _clamp(prior + deltas[key], lower, 1.0)
            next_fast[key] = updated
            applied[key] = updated - prior
        if all(abs(value) <= _ZERO_TOLERANCE for value in applied.values()):
            return AppraisalDecision(
                AppraisalDecisionKind.SKIPPED,
                "neutral_appraisal_no_delta",
                current,
            )

        mood_impulse = self._derive_mood_delta(applied)
        next_mood: dict[str, float] = {}
        applied_mood: dict[str, float] = {}
        for key, dimension in self.policy.mood:
            lower = -1.0 if dimension.signed else 0.0
            prior = getattr(current.mood, key)
            updated = _clamp(prior + mood_impulse[key], lower, 1.0)
            next_mood[key] = updated
            applied_mood[key] = updated - prior

        after = AffectiveStateSnapshot(
            identity_id=current.identity_id,
            schema_version=current.schema_version,
            state_version=current.state_version + 1,
            mood_version=current.mood_version + 1,
            as_of=current.as_of,
            emotion_policy_version=self.policy.emotion_policy_version,
            appraisal_schema_version=APPRAISAL_SCHEMA_VERSION,
            mood_policy_version=self.policy.mood_policy_version,
            fast=FastAffectiveState.from_mapping(next_fast),
            mood=MoodState.from_mapping(next_mood),
        )
        transition = AffectiveTransitionDraft(
            proposal=proposal,
            before=current,
            after=after,
            applied_delta=AffectiveDelta.from_mapping(applied),
            mood_delta=MoodDelta.from_mapping(applied_mood),
        )
        return AppraisalDecision(
            AppraisalDecisionKind.APPLIED,
            "bounded_appraisal_applied",
            current,
            transition,
        )

    def _derive_fast_delta(
        self,
        proposal: AffectiveAppraisalProposal,
        personality: Personality,
    ) -> dict[str, float]:
        authority = proposal.salience * proposal.appraisal_confidence
        raw = {
            "valence": proposal.pleasantness * 0.22,
            "arousal": proposal.activation * 0.18,
            "tension": (
                0.45 * proposal.uncertainty
                + 0.35 * proposal.concern_signal
                + 0.35 * proposal.frustration_signal
            )
            * 0.16,
            "curiosity": (
                0.65 * proposal.curiosity_signal
                + 0.25 * proposal.novelty
                + 0.10 * proposal.uncertainty
            )
            * 0.15,
            "interest": (
                0.65 * proposal.interest_signal
                + 0.20 * proposal.salience
                + 0.15 * proposal.curiosity_signal
            )
            * 0.16,
            "amusement": (0.85 * proposal.humor_signal + 0.15 * max(proposal.pleasantness, 0.0))
            * 0.18,
            "concern": proposal.concern_signal * 0.18,
            "frustration": proposal.frustration_signal * 0.14,
            "situational_confidence": (proposal.confidence_signal - 0.35 * proposal.uncertainty)
            * 0.12,
        }
        sensitivity = 0.75 + 0.50 * personality.trait("emotional_sensitivity").value
        patience = 1.15 - 0.45 * personality.trait("patience").value
        curiosity = 0.80 + 0.40 * personality.trait("curiosity").value
        playfulness = (
            0.80
            + 0.40
            * (personality.trait("playfulness").value + personality.trait("humor").value)
            / 2.0
        )
        self_confidence = personality.trait("self_confidence").value
        confidence_stability = 1.10 - 0.40 * self_confidence
        confidence_growth = 0.90 + 0.20 * self_confidence
        result: dict[str, float] = {}
        for key, value in raw.items():
            multiplier = sensitivity
            if key in {"frustration", "tension"}:
                multiplier *= patience
            elif key in {"curiosity", "interest"}:
                multiplier *= curiosity
            elif key == "amusement":
                multiplier *= playfulness
            elif key == "situational_confidence":
                multiplier *= confidence_stability if value < 0.0 else confidence_growth
            cap = self.policy.fast_dimension(key).max_absolute_delta
            result[key] = _clamp(value * authority * multiplier, -cap, cap)
        return result

    def _derive_mood_delta(self, applied: dict[str, float]) -> dict[str, float]:
        raw = {
            "valence": self.policy.mood_valence_gain * applied["valence"],
            "energy": (
                self.policy.mood_energy_arousal_gain * applied["arousal"]
                + self.policy.mood_energy_interest_gain * applied["interest"]
                + self.policy.mood_energy_amusement_gain * applied["amusement"]
            ),
            "tension": (
                self.policy.mood_tension_tension_gain * applied["tension"]
                + self.policy.mood_tension_concern_gain * applied["concern"]
                + self.policy.mood_tension_frustration_gain * applied["frustration"]
            ),
        }
        return {
            key: _clamp(value, -item.max_absolute_delta, item.max_absolute_delta)
            for key, item in self.policy.mood
            for value in (raw[key],)
        }
