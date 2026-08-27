"""Provider-neutral contracts for structured affective appraisal."""

import math
from dataclasses import dataclass
from datetime import datetime

from satori.core.provider_metrics import ProviderExecutionMetrics


def _non_blank(value: str, field_name: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} must be at most {maximum} characters")
    return normalized


def _unit_interval(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")
    return value


def _signed_unit_interval(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and between -1 and 1")
    return value


@dataclass(frozen=True, slots=True)
class AppraisalTrait:
    """One immutable personality input used only for semantic interpretation."""

    key: str
    value: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _non_blank(self.key, "trait key", maximum=64))
        _unit_interval(self.value, f"trait {self.key}")


@dataclass(frozen=True, slots=True)
class AppraisalValue:
    """One immutable value input; it is not mutated by appraisal."""

    key: str
    strength: float
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _non_blank(self.key, "value key", maximum=64))
        _unit_interval(self.strength, f"value {self.key}")
        object.__setattr__(
            self,
            "description",
            _non_blank(self.description, f"value {self.key} description"),
        )


@dataclass(frozen=True, slots=True)
class AppraisalFastState:
    """Current materialized fast affect supplied as read-only provider data."""

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
        for field_name in (
            "arousal",
            "tension",
            "curiosity",
            "interest",
            "amusement",
            "concern",
            "frustration",
            "situational_confidence",
        ):
            _unit_interval(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class AppraisalMoodState:
    """Current materialized slower mood supplied independently from fast affect."""

    valence: float
    energy: float
    tension: float

    def __post_init__(self) -> None:
        _signed_unit_interval(self.valence, "mood valence")
        _unit_interval(self.energy, "mood energy")
        _unit_interval(self.tension, "mood tension")


@dataclass(frozen=True, slots=True)
class AppraisalEpisodicContext:
    """One selected untrusted episode used only to interpret the current event."""

    memory_id: str
    summary: str
    importance: float
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_id", _non_blank(self.memory_id, "memory_id", maximum=128))
        object.__setattr__(self, "summary", _non_blank(self.summary, "memory summary"))
        _unit_interval(self.importance, "memory importance")
        _unit_interval(self.confidence, "memory confidence")


@dataclass(frozen=True, slots=True)
class AppraisalSemanticContext:
    """One selected untrusted semantic claim without mutation authority."""

    claim_id: str
    predicate: str
    value: str
    claim_kind: str
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _non_blank(self.claim_id, "claim_id", maximum=128))
        object.__setattr__(self, "predicate", _non_blank(self.predicate, "predicate", maximum=64))
        object.__setattr__(self, "value", _non_blank(self.value, "semantic value"))
        object.__setattr__(
            self, "claim_kind", _non_blank(self.claim_kind, "claim_kind", maximum=64)
        )
        _unit_interval(self.confidence, "semantic confidence")


@dataclass(frozen=True, slots=True)
class AffectiveAppraisalRequest:
    """Bounded trust-separated input for one current-user-event appraisal."""

    schema_version: int
    trace_id: str
    interaction_id: str
    appraised_at: datetime
    user_content: str
    traits: tuple[AppraisalTrait, ...]
    values: tuple[AppraisalValue, ...]
    fast_state: AppraisalFastState
    mood_state: AppraisalMoodState
    episodic_context: tuple[AppraisalEpisodicContext, ...] = ()
    semantic_context: tuple[AppraisalSemanticContext, ...] = ()

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("appraisal request schema_version must be positive")
        object.__setattr__(self, "trace_id", _non_blank(self.trace_id, "trace_id", maximum=128))
        object.__setattr__(
            self,
            "interaction_id",
            _non_blank(self.interaction_id, "interaction_id", maximum=128),
        )
        if self.appraised_at.tzinfo is None or self.appraised_at.utcoffset() is None:
            raise ValueError("appraised_at must be timezone-aware")
        if not isinstance(self.user_content, str) or not self.user_content.strip():
            raise ValueError("user_content must not be blank")
        object.__setattr__(self, "traits", tuple(self.traits))
        object.__setattr__(self, "values", tuple(self.values))
        object.__setattr__(self, "episodic_context", tuple(self.episodic_context))
        object.__setattr__(self, "semantic_context", tuple(self.semantic_context))
        if not self.traits or not self.values:
            raise ValueError("appraisal requires personality and values")


@dataclass(frozen=True, slots=True)
class AffectiveAppraisalProposal:
    """Untrusted semantic scores; the owner derives and bounds every state delta."""

    schema_version: int
    pleasantness: float
    activation: float
    novelty: float
    salience: float
    uncertainty: float
    curiosity_signal: float
    interest_signal: float
    humor_signal: float
    concern_signal: float
    frustration_signal: float
    confidence_signal: float
    appraisal_confidence: float
    source_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("appraisal proposal schema_version must be positive")
        _signed_unit_interval(self.pleasantness, "pleasantness")
        _signed_unit_interval(self.confidence_signal, "confidence_signal")
        for field_name in (
            "activation",
            "novelty",
            "salience",
            "uncertainty",
            "curiosity_signal",
            "interest_signal",
            "humor_signal",
            "concern_signal",
            "frustration_signal",
            "appraisal_confidence",
        ):
            _unit_interval(getattr(self, field_name), field_name)
        source_refs = tuple(
            _non_blank(item, "source_ref", maximum=128) for item in self.source_refs
        )
        if not source_refs or len(source_refs) != len(set(source_refs)):
            raise ValueError("source_refs must be non-empty and unique")
        reason_codes = tuple(
            _non_blank(item, "reason_code", maximum=64) for item in self.reason_codes
        )
        if not reason_codes or len(reason_codes) > 8 or len(reason_codes) != len(set(reason_codes)):
            raise ValueError("reason_codes must contain one to eight unique values")
        object.__setattr__(self, "source_refs", source_refs)
        object.__setattr__(self, "reason_codes", reason_codes)


@dataclass(frozen=True, slots=True)
class AffectiveAppraisalProviderResponse:
    """Structured appraisal plus reproducibility metadata."""

    proposal: AffectiveAppraisalProposal
    provider: str
    model: str
    appraisal_method: str
    metrics: ProviderExecutionMetrics | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _non_blank(self.provider, "provider", maximum=128))
        object.__setattr__(self, "model", _non_blank(self.model, "model", maximum=256))
        object.__setattr__(
            self,
            "appraisal_method",
            _non_blank(self.appraisal_method, "appraisal_method", maximum=128),
        )


class AffectiveAppraisalProviderError(Exception):
    """Typed structured-provider failure; conversation may continue without mutation."""

    def __init__(self, provider: str, model: str, message: str) -> None:
        self.provider = _non_blank(provider, "provider", maximum=128)
        self.model = _non_blank(model, "model", maximum=256)
        super().__init__(_non_blank(message, "message"))
