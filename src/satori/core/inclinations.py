"""Provider-neutral Stage 13 contracts for Satori inclinations."""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


def _text(value: str, field_name: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    return normalized


def _unit(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and in [0, 1]")
    return value


def _signed(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and in [-1, 1]")
    return value


class InclinationKind(StrEnum):
    INTEREST = "interest"
    PREFERENCE = "preference"


@dataclass(frozen=True, slots=True)
class InclinationStateReference:
    """Bounded owner-produced target state for reflection and current-turn reads."""

    inclination_id: str
    aggregate_version: int
    kind: InclinationKind
    topic: str
    alternative_topic: str | None
    score: float
    confidence: float
    stability: float
    state_as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "inclination_id",
            _text(self.inclination_id, "inclination_id", maximum=128),
        )
        if type(self.aggregate_version) is not int or self.aggregate_version < 1:
            raise ValueError("aggregate_version must be positive")
        if not isinstance(self.kind, InclinationKind):
            raise ValueError("kind must be an InclinationKind")
        object.__setattr__(self, "topic", _text(self.topic, "topic", maximum=96))
        if self.kind is InclinationKind.INTEREST:
            if self.alternative_topic is not None:
                raise ValueError("interest cannot have alternative_topic")
            _unit(self.score, "interest score")
        else:
            if self.alternative_topic is None:
                raise ValueError("preference requires alternative_topic")
            alternative = _text(self.alternative_topic, "alternative_topic", maximum=96)
            if alternative.casefold() == self.topic.casefold():
                raise ValueError("preference topics must be distinct")
            object.__setattr__(self, "alternative_topic", alternative)
            _signed(self.score, "preference score")
        _unit(self.confidence, "inclination confidence")
        _unit(self.stability, "inclination stability")
        if self.state_as_of.tzinfo is None or self.state_as_of.utcoffset() is None:
            raise ValueError("state_as_of must be timezone-aware")


@dataclass(frozen=True, slots=True)
class InclinationAffectiveSignal:
    """Verified owner-approved appraisal projection attached to one canonical source."""

    transition_id: str
    resulting_state_version: int
    signal_hash: str
    pleasantness: float
    novelty: float
    salience: float
    curiosity_signal: float
    interest_signal: float
    concern_signal: float
    frustration_signal: float
    appraisal_confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "transition_id", _text(self.transition_id, "transition_id", maximum=128)
        )
        if type(self.resulting_state_version) is not int or self.resulting_state_version < 1:
            raise ValueError("resulting_state_version must be positive")
        object.__setattr__(self, "signal_hash", _text(self.signal_hash, "signal_hash", maximum=64))
        _signed(self.pleasantness, "pleasantness")
        for field_name in (
            "novelty",
            "salience",
            "curiosity_signal",
            "interest_signal",
            "concern_signal",
            "frustration_signal",
            "appraisal_confidence",
        ):
            _unit(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class InclinationEvidenceSource:
    """One fixed reflection source mapped into the inclination owner boundary."""

    source_id: str
    identity_id: str
    root_message_id: str
    root_interaction_id: str
    root_session_id: str
    root_counterparty_id: str
    observed_at: datetime
    quote: str
    content_hash: str
    affective: InclinationAffectiveSignal

    def __post_init__(self) -> None:
        for field_name in (
            "source_id",
            "identity_id",
            "root_message_id",
            "root_interaction_id",
            "root_session_id",
            "root_counterparty_id",
        ):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name, maximum=128)
            )
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(self, "quote", _text(self.quote, "quote", maximum=512))
        object.__setattr__(
            self, "content_hash", _text(self.content_hash, "content_hash", maximum=64)
        )


@dataclass(frozen=True, slots=True)
class InclinationProposal:
    """Untrusted semantic candidate; it deliberately contains no state delta."""

    kind: InclinationKind
    topic: str
    alternative_topic: str | None
    confidence: float
    source_ids: tuple[str, ...]
    target_inclination_id: str | None = None
    expected_target_version: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "topic", _text(self.topic, "topic", maximum=96))
        if self.kind is InclinationKind.INTEREST:
            if self.alternative_topic is not None:
                raise ValueError("interest cannot have alternative_topic")
        else:
            if self.alternative_topic is None:
                raise ValueError("preference requires alternative_topic")
            alternative = _text(self.alternative_topic, "alternative_topic", maximum=96)
            if alternative.casefold() == self.topic.casefold():
                raise ValueError("preference topics must be distinct")
            object.__setattr__(self, "alternative_topic", alternative)
        _unit(self.confidence, "inclination proposal confidence")
        source_ids = tuple(_text(item, "source_id", maximum=128) for item in self.source_ids)
        if not source_ids or len(source_ids) > 8 or len(source_ids) != len(set(source_ids)):
            raise ValueError("inclination proposal requires one to eight unique source_ids")
        object.__setattr__(self, "source_ids", source_ids)
        if self.target_inclination_id is not None:
            object.__setattr__(
                self,
                "target_inclination_id",
                _text(self.target_inclination_id, "target_inclination_id", maximum=128),
            )
        if self.expected_target_version is not None and (
            type(self.expected_target_version) is not int or self.expected_target_version < 1
        ):
            raise ValueError("expected_target_version must be positive")
        if (self.target_inclination_id is None) != (self.expected_target_version is None):
            raise ValueError("target inclination and expected version must appear together")
