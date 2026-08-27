"""Provider-neutral Stage 11 contracts for durable Satori position formation."""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.core.provider_metrics import ProviderExecutionMetrics


def _non_blank(value: str, field_name: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    return normalized


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class PositionKind(StrEnum):
    FACT = "fact"
    BELIEF = "belief"
    OPINION = "opinion"
    HYPOTHESIS = "hypothesis"


class PositionStance(StrEnum):
    SUPPORT = "support"
    OPPOSE = "oppose"
    UNCERTAIN = "uncertain"


class PositionEvidenceRole(StrEnum):
    ARGUMENT = "argument"
    OBSERVATION = "observation"
    COUNTEREXAMPLE = "counterexample"
    VERIFIED_RECORD = "verified_record"


@dataclass(frozen=True, slots=True)
class PositionSourceMessage:
    message_id: str
    interaction_id: str
    identity_id: str
    counterparty_id: str
    observed_at: datetime
    content: str

    def __post_init__(self) -> None:
        for field_name in ("message_id", "interaction_id", "identity_id", "counterparty_id"):
            object.__setattr__(
                self, field_name, _non_blank(getattr(self, field_name), field_name, maximum=128)
            )
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "observed_at"))
        object.__setattr__(
            self, "content", _non_blank(self.content, "source content", maximum=8000)
        )


@dataclass(frozen=True, slots=True)
class PositionEvidenceCitation:
    message_id: str
    quote: str
    role: PositionEvidenceRole

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "message_id", _non_blank(self.message_id, "message_id", maximum=128)
        )
        object.__setattr__(self, "quote", _non_blank(self.quote, "quote", maximum=512))


@dataclass(frozen=True, slots=True)
class PositionStateReference:
    position_id: str
    aggregate_version: int
    kind: PositionKind
    stance: PositionStance
    status: str
    proposition: str
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "position_id", _non_blank(self.position_id, "position_id", maximum=128)
        )
        if type(self.aggregate_version) is not int or self.aggregate_version < 1:
            raise ValueError("position aggregate_version must be positive")
        object.__setattr__(self, "status", _non_blank(self.status, "position status", maximum=32))
        object.__setattr__(
            self, "proposition", _non_blank(self.proposition, "proposition", maximum=240)
        )
        if (
            isinstance(self.confidence, bool)
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("position confidence must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class PositionValueReference:
    key: str
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _non_blank(self.key, "value key", maximum=64))
        object.__setattr__(
            self, "description", _non_blank(self.description, "value description", maximum=500)
        )


@dataclass(frozen=True, slots=True)
class PositionProposal:
    proposition: str
    kind: PositionKind
    stance: PositionStance
    confidence: float
    evidence: tuple[PositionEvidenceCitation, ...]
    value_key: str | None = None
    revises_position_id: str | None = None
    opposes_position_id: str | None = None
    challenges_position_id: str | None = None
    expected_target_version: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "proposition", _non_blank(self.proposition, "proposition", maximum=240)
        )
        if (
            isinstance(self.confidence, bool)
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("position proposal confidence must be in [0, 1]")
        evidence = tuple(self.evidence)
        if not evidence:
            raise ValueError("position proposal evidence must not be empty")
        object.__setattr__(self, "evidence", evidence)
        for field_name in (
            "value_key",
            "revises_position_id",
            "opposes_position_id",
            "challenges_position_id",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _non_blank(value, field_name, maximum=128))
        targets = (
            self.revises_position_id,
            self.opposes_position_id,
            self.challenges_position_id,
        )
        if sum(item is not None for item in targets) > 1:
            raise ValueError("position proposal accepts at most one target operation")
        if self.expected_target_version is not None and (
            type(self.expected_target_version) is not int or self.expected_target_version < 1
        ):
            raise ValueError("expected_target_version must be positive")
        if (
            self.expected_target_version is not None
            and self.revises_position_id is None
            and self.opposes_position_id is None
            and self.challenges_position_id is None
        ):
            raise ValueError("expected_target_version requires a target position")


@dataclass(frozen=True, slots=True)
class PositionFormationProposal:
    schema_version: int
    positions: tuple[PositionProposal, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("position proposal schema_version must be positive")
        object.__setattr__(self, "positions", tuple(self.positions))


@dataclass(frozen=True, slots=True)
class PositionFormationRequest:
    schema_version: int
    trace_id: str
    source_interaction_id: str
    source_message_id: str
    identity_id: str
    formation_version: int
    max_positions: int
    messages: tuple[PositionSourceMessage, ...]
    current_positions: tuple[PositionStateReference, ...]
    values: tuple[PositionValueReference, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("position request schema_version must be positive")
        for field_name in (
            "trace_id",
            "source_interaction_id",
            "source_message_id",
            "identity_id",
        ):
            object.__setattr__(
                self, field_name, _non_blank(getattr(self, field_name), field_name, maximum=128)
            )
        if type(self.formation_version) is not int or self.formation_version < 1:
            raise ValueError("position formation_version must be positive")
        if type(self.max_positions) is not int or self.max_positions < 1:
            raise ValueError("position max_positions must be positive")
        messages = tuple(self.messages)
        message_ids = tuple(item.message_id for item in messages)
        if (
            not messages
            or self.source_message_id not in message_ids
            or len(message_ids) != len(set(message_ids))
            or any(item.identity_id != self.identity_id for item in messages)
        ):
            raise ValueError("position request requires unique same-identity messages and source")
        positions = tuple(self.current_positions)
        position_ids = tuple(item.position_id for item in positions)
        values = tuple(self.values)
        value_keys = tuple(item.key for item in values)
        if len(position_ids) != len(set(position_ids)) or len(value_keys) != len(set(value_keys)):
            raise ValueError("position request references must be unique")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "current_positions", positions)
        object.__setattr__(self, "values", values)


@dataclass(frozen=True, slots=True)
class PositionFormationProviderResponse:
    proposal: PositionFormationProposal
    provider: str
    model: str
    formation_method: str
    metrics: ProviderExecutionMetrics | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _non_blank(self.provider, "provider", maximum=128))
        object.__setattr__(self, "model", _non_blank(self.model, "model", maximum=256))
        object.__setattr__(
            self,
            "formation_method",
            _non_blank(self.formation_method, "formation_method", maximum=128),
        )


class PositionFormationProviderError(Exception):
    def __init__(self, provider: str, model: str, message: str) -> None:
        self.provider = _non_blank(provider, "provider", maximum=128)
        self.model = _non_blank(model, "model", maximum=256)
        super().__init__(_non_blank(message, "message", maximum=512))
