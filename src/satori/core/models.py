"""Provider-neutral Stage 9 contracts for user/world model formation."""

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


class ModelEpistemicKind(StrEnum):
    """Epistemic origin is immutable for the lifetime of a model claim."""

    EXPLICIT_FACT = "explicit_fact"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"


class ModelValueKind(StrEnum):
    """Small scalar algebra; Stage 9 does not create a generic graph."""

    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"


ModelScalar = str | float | bool


def validate_model_scalar(kind: ModelValueKind, value: ModelScalar) -> None:
    if kind is ModelValueKind.TEXT:
        if not isinstance(value, str):
            raise ValueError("text model value must be a string")
        _non_blank(value, "text model value", maximum=160)
        return
    if kind is ModelValueKind.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("number model value must be numeric")
        if not math.isfinite(float(value)):
            raise ValueError("number model value must be finite")
        return
    if type(value) is not bool:
        raise ValueError("boolean model value must be a boolean")


@dataclass(frozen=True, slots=True)
class ModelSourceMessage:
    """One canonical same-counterparty user-message root supplied to formation."""

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
class ModelEvidenceCitation:
    """Exact source span proposed by an untrusted structured provider."""

    message_id: str
    quote: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "message_id", _non_blank(self.message_id, "message_id", maximum=128)
        )
        object.__setattr__(self, "quote", _non_blank(self.quote, "evidence quote"))


@dataclass(frozen=True, slots=True)
class UserModelClaimProposal:
    """Untrusted proposal about the current counterparty."""

    predicate: str
    value_kind: ModelValueKind
    value: ModelScalar
    epistemic_kind: ModelEpistemicKind
    confidence: float
    evidence: tuple[ModelEvidenceCitation, ...]
    corrects_claim_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "predicate", _non_blank(self.predicate, "user predicate", maximum=64)
        )
        validate_model_scalar(self.value_kind, self.value)
        _validate_proposal(self.confidence, self.evidence, self.corrects_claim_id)


@dataclass(frozen=True, slots=True)
class WorldModelClaimProposal:
    """Untrusted counterparty-relative current-world proposal."""

    subject_kind: str
    subject_label: str
    predicate: str
    value_kind: ModelValueKind
    value: ModelScalar
    epistemic_kind: ModelEpistemicKind
    confidence: float
    evidence: tuple[ModelEvidenceCitation, ...]
    corrects_claim_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "subject_kind", _non_blank(self.subject_kind, "world subject_kind", maximum=32)
        )
        object.__setattr__(
            self,
            "subject_label",
            _non_blank(self.subject_label, "world subject_label", maximum=120),
        )
        object.__setattr__(
            self, "predicate", _non_blank(self.predicate, "world predicate", maximum=64)
        )
        validate_model_scalar(self.value_kind, self.value)
        _validate_proposal(self.confidence, self.evidence, self.corrects_claim_id)


def _validate_proposal(
    confidence: float,
    evidence: tuple[ModelEvidenceCitation, ...],
    corrects_claim_id: str | None,
) -> None:
    if (
        isinstance(confidence, bool)
        or not math.isfinite(confidence)
        or not 0.0 <= confidence <= 1.0
    ):
        raise ValueError("model proposal confidence must be in [0, 1]")
    citations = tuple(evidence)
    message_ids = tuple(item.message_id for item in citations)
    if not citations or len(message_ids) != len(set(message_ids)):
        raise ValueError("model proposal evidence must be non-empty with unique message IDs")
    if corrects_claim_id is not None:
        _non_blank(corrects_claim_id, "corrects_claim_id", maximum=128)


@dataclass(frozen=True, slots=True)
class ModelFormationProposal:
    """Bounded zero-or-more proposal document for both independent owners."""

    schema_version: int
    user_claims: tuple[UserModelClaimProposal, ...]
    world_claims: tuple[WorldModelClaimProposal, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("model proposal schema_version must be positive")
        object.__setattr__(self, "user_claims", tuple(self.user_claims))
        object.__setattr__(self, "world_claims", tuple(self.world_claims))


@dataclass(frozen=True, slots=True)
class ModelFormationRequest:
    """Bounded canonical input for one post-response formation attempt."""

    schema_version: int
    trace_id: str
    source_interaction_id: str
    source_message_id: str
    identity_id: str
    counterparty_id: str
    formation_version: int
    max_user_claims: int
    max_world_claims: int
    messages: tuple[ModelSourceMessage, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("model request schema_version must be positive")
        for field_name in (
            "trace_id",
            "source_interaction_id",
            "source_message_id",
            "identity_id",
            "counterparty_id",
        ):
            object.__setattr__(
                self, field_name, _non_blank(getattr(self, field_name), field_name, maximum=128)
            )
        for field_name in ("formation_version", "max_user_claims", "max_world_claims"):
            if type(getattr(self, field_name)) is not int or getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be positive")
        messages = tuple(self.messages)
        message_ids = tuple(item.message_id for item in messages)
        if (
            not messages
            or self.source_message_id not in message_ids
            or len(message_ids) != len(set(message_ids))
        ):
            raise ValueError("model request requires unique messages including its source")
        if any(
            item.identity_id != self.identity_id or item.counterparty_id != self.counterparty_id
            for item in messages
        ):
            raise ValueError("model request messages must share identity and counterparty")
        object.__setattr__(self, "messages", messages)


@dataclass(frozen=True, slots=True)
class ModelFormationProviderResponse:
    """Structured result with reproducibility metadata."""

    proposal: ModelFormationProposal
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


class ModelFormationProviderError(Exception):
    """Typed failure at the Stage 9 structured-generation boundary."""

    def __init__(self, provider: str, model: str, message: str) -> None:
        self.provider = _non_blank(provider, "provider", maximum=128)
        self.model = _non_blank(model, "model", maximum=256)
        super().__init__(_non_blank(message, "message", maximum=512))
