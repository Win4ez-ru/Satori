"""Provider-neutral contracts for versioned text embeddings."""

import math
from dataclasses import dataclass

from satori.core.provider_metrics import ProviderExecutionMetrics


def _non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    """One bounded batch embedded in a single explicitly versioned input schema."""

    schema_version: int
    trace_id: str
    texts: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("embedding request schema_version must be positive")
        object.__setattr__(self, "trace_id", _non_blank(self.trace_id, "trace_id"))
        texts = tuple(_non_blank(text, "embedding text") for text in self.texts)
        if not texts:
            raise ValueError("embedding request texts must not be empty")
        object.__setattr__(self, "texts", texts)


@dataclass(frozen=True, slots=True)
class EmbeddingSpace:
    """Exact compatibility identity for vectors that may be compared."""

    provider: str
    model: str
    dimensions: int
    input_schema_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _non_blank(self.provider, "embedding provider"))
        object.__setattr__(self, "model", _non_blank(self.model, "embedding model"))
        if type(self.dimensions) is not int or self.dimensions < 1:
            raise ValueError("embedding dimensions must be positive")
        if type(self.input_schema_version) is not int or self.input_schema_version < 1:
            raise ValueError("embedding input_schema_version must be positive")

    @property
    def key(self) -> str:
        """Stable human-readable identifier used in logs and debug output."""

        return f"{self.provider}/{self.model}/d{self.dimensions}/input-v{self.input_schema_version}"


@dataclass(frozen=True, slots=True)
class EmbeddingResponse:
    """Validated vectors plus their exact comparison space."""

    space: EmbeddingSpace
    vectors: tuple[tuple[float, ...], ...]
    metrics: ProviderExecutionMetrics | None = None

    def __post_init__(self) -> None:
        vectors = tuple(tuple(vector) for vector in self.vectors)
        if not vectors:
            raise ValueError("embedding response vectors must not be empty")
        for vector in vectors:
            if len(vector) != self.space.dimensions:
                raise ValueError("embedding vector dimension does not match its space")
            if any(isinstance(value, bool) or not math.isfinite(value) for value in vector):
                raise ValueError("embedding vectors must contain only finite numbers")
            if math.sqrt(sum(value * value for value in vector)) == 0.0:
                raise ValueError("embedding vector norm must be non-zero")
        object.__setattr__(self, "vectors", vectors)


class EmbeddingProviderError(Exception):
    """Base typed error crossing the embedding adapter boundary."""

    def __init__(self, provider: str, model: str, message: str) -> None:
        self.provider = _non_blank(provider, "provider")
        self.model = _non_blank(model, "model")
        super().__init__(_non_blank(message, "message"))


class EmbeddingProviderUnavailable(EmbeddingProviderError):
    """The configured embedding capability cannot currently be reached."""


class EmbeddingGenerationFailed(EmbeddingProviderError):
    """The provider rejected or failed one embedding request."""


class InvalidEmbeddingResponse(EmbeddingProviderError):
    """The provider returned malformed or incompatible vectors."""
