"""Provider-neutral immutable contracts for one conversational generation call."""

import math
from dataclasses import dataclass
from enum import StrEnum

from satori.core.provider_metrics import ProviderExecutionMetrics


def _non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _positive_int(value: int, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


class ConversationMessageRole(StrEnum):
    """Trust-preserving roles before an adapter maps them to a vendor API."""

    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """One provider-neutral message with an explicit trust role."""

    role: ConversationMessageRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, ConversationMessageRole):
            raise ValueError("message role must be a ConversationMessageRole")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("message content must not be blank")


@dataclass(frozen=True, slots=True)
class ConversationGenerationParameters:
    """Small cross-provider generation controls needed by Stage 3."""

    schema_version: int
    temperature: float
    max_output_tokens: int

    def __post_init__(self) -> None:
        _positive_int(self.schema_version, "generation schema_version")
        if isinstance(self.temperature, bool) or not math.isfinite(self.temperature):
            raise ValueError("temperature must be finite")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        _positive_int(self.max_output_tokens, "max_output_tokens")


@dataclass(frozen=True, slots=True)
class ConversationProviderRequest:
    """Complete immutable input to any compatible conversation provider."""

    schema_version: int
    trace_id: str
    context_schema_version: int
    messages: tuple[ConversationMessage, ...]
    parameters: ConversationGenerationParameters

    def __post_init__(self) -> None:
        _positive_int(self.schema_version, "provider request schema_version")
        object.__setattr__(self, "trace_id", _non_blank(self.trace_id, "trace_id"))
        _positive_int(self.context_schema_version, "context_schema_version")
        if not self.messages:
            raise ValueError("provider request messages must not be empty")
        object.__setattr__(self, "messages", tuple(self.messages))


@dataclass(frozen=True, slots=True)
class ConversationUsage:
    """Optional provider usage; either count may be unavailable."""

    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer or None")


@dataclass(frozen=True, slots=True)
class ConversationPastClaim:
    """Provider-declared shared-past claim for deterministic evidence gating."""

    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        evidence_ids = tuple(
            _non_blank(evidence_id, "past claim evidence_id") for evidence_id in self.evidence_ids
        )
        if not evidence_ids:
            raise ValueError("past claim evidence_ids must not be empty")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("past claim evidence_ids must be unique")
        object.__setattr__(self, "evidence_ids", evidence_ids)


@dataclass(frozen=True, slots=True)
class ConversationProviderResponse:
    """Provider-neutral result; application policy validates final text."""

    text: str
    provider: str
    model: str
    finish_status: str
    usage: ConversationUsage | None = None
    declared_past_claims: tuple[ConversationPastClaim, ...] = ()
    metrics: ProviderExecutionMetrics | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("provider response text must be a string")
        object.__setattr__(self, "provider", _non_blank(self.provider, "provider"))
        object.__setattr__(self, "model", _non_blank(self.model, "model"))
        object.__setattr__(
            self,
            "finish_status",
            _non_blank(self.finish_status, "finish_status"),
        )
        object.__setattr__(self, "declared_past_claims", tuple(self.declared_past_claims))


class ConversationProviderError(Exception):
    """Base typed error crossing from a provider adapter into application code."""

    def __init__(
        self,
        provider: str,
        model: str,
        message: str,
        *,
        metrics: ProviderExecutionMetrics | None = None,
    ) -> None:
        self.provider = _non_blank(provider, "provider")
        self.model = _non_blank(model, "model")
        self.metrics = metrics
        super().__init__(_non_blank(message, "message"))


class ProviderUnavailable(ConversationProviderError):
    """The configured provider cannot currently be reached or serve requests."""


class GenerationFailed(ConversationProviderError):
    """The provider was reached but could not generate the requested response."""


class InvalidProviderResponse(ConversationProviderError):
    """The provider returned a malformed or policy-invalid result."""
