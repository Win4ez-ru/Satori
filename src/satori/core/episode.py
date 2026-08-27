"""Provider-neutral contracts for selective episodic-memory formation."""

import math
from dataclasses import dataclass
from datetime import datetime

from satori.core.conversation import ConversationMessageRole
from satori.core.provider_metrics import ProviderExecutionMetrics


def _non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _unit_interval(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")
    return value


@dataclass(frozen=True, slots=True)
class EpisodeSourceMessage:
    """One untrusted historical message supplied as extraction data."""

    message_id: str
    role: ConversationMessageRole
    content: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", _non_blank(self.message_id, "message_id"))
        if self.role not in {ConversationMessageRole.USER, ConversationMessageRole.ASSISTANT}:
            raise ValueError("episode source role must be user or assistant")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("episode source content must not be blank")


@dataclass(frozen=True, slots=True)
class EpisodeFormationRequest:
    """Immutable interaction snapshot for one versioned formation attempt."""

    schema_version: int
    trace_id: str
    interaction_id: str
    occurred_at: datetime
    formation_version: int
    messages: tuple[EpisodeSourceMessage, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("episode request schema_version must be positive")
        object.__setattr__(self, "trace_id", _non_blank(self.trace_id, "trace_id"))
        object.__setattr__(
            self,
            "interaction_id",
            _non_blank(self.interaction_id, "interaction_id"),
        )
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("episode occurred_at must be timezone-aware")
        if type(self.formation_version) is not int or self.formation_version < 1:
            raise ValueError("formation_version must be positive")
        messages = tuple(self.messages)
        if not messages:
            raise ValueError("episode request messages must not be empty")
        message_ids = tuple(message.message_id for message in messages)
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("episode request message IDs must be unique")
        object.__setattr__(self, "messages", messages)


@dataclass(frozen=True, slots=True)
class EpisodeEvidenceProposal:
    """Exact source span claimed to support a proposed episode."""

    message_id: str
    quote: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", _non_blank(self.message_id, "message_id"))
        object.__setattr__(self, "quote", _non_blank(self.quote, "evidence quote"))


@dataclass(frozen=True, slots=True)
class EpisodeFormationProposal:
    """Untrusted typed proposal; deterministic policy decides create/skip/reject."""

    schema_version: int
    should_create: bool
    summary: str | None
    importance: float | None
    confidence: float | None
    evidence: tuple[EpisodeEvidenceProposal, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("episode proposal schema_version must be positive")
        if type(self.should_create) is not bool:
            raise ValueError("should_create must be a boolean")
        if self.summary is not None and not isinstance(self.summary, str):
            raise ValueError("episode summary must be a string or None")
        if self.importance is not None:
            _unit_interval(self.importance, "episode importance")
        if self.confidence is not None:
            _unit_interval(self.confidence, "episode confidence")
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True, slots=True)
class EpisodeFormationProviderResponse:
    """Structured provider result with reproducibility metadata."""

    proposal: EpisodeFormationProposal
    provider: str
    model: str
    formation_method: str
    metrics: ProviderExecutionMetrics | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _non_blank(self.provider, "provider"))
        object.__setattr__(self, "model", _non_blank(self.model, "model"))
        object.__setattr__(
            self,
            "formation_method",
            _non_blank(self.formation_method, "formation_method"),
        )


class EpisodeFormationProviderError(Exception):
    """Typed failure at the structured episode-formation boundary."""

    def __init__(self, provider: str, model: str, message: str) -> None:
        self.provider = _non_blank(provider, "provider")
        self.model = _non_blank(model, "model")
        super().__init__(_non_blank(message, "message"))
