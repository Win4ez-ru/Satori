"""Explicit OpenAI embedding space; no local-index conversion or silent truncation."""

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.core.embedding import (
    EmbeddingGenerationFailed,
    EmbeddingProviderUnavailable,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingSpace,
    InvalidEmbeddingResponse,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.infrastructure.providers.openai_http import (
    OpenAIHttpStatusError,
    OpenAITransportError,
)
from satori.infrastructure.providers.openai_structured import (
    OpenAIStructuredTransport,
    bounded_json,
)

OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
MAX_EMBEDDING_RESPONSE_BYTES = 8_000_000
MAX_EMBEDDING_INPUT_BYTES = 8192
MAX_EMBEDDING_BATCH = 32


class _EmbeddingItem(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    object: Literal["embedding"]
    index: int = Field(ge=0)
    embedding: list[float] = Field(min_length=1, max_length=1536)


class _EmbeddingUsage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    prompt_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000_000)
    total_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000_000)


class _EmbeddingEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    object: Literal["list"]
    model: Literal["text-embedding-3-small"]
    data: list[_EmbeddingItem] = Field(min_length=1, max_length=MAX_EMBEDDING_BATCH)
    usage: _EmbeddingUsage | None = None


@dataclass(frozen=True, slots=True)
class OpenAIEmbeddingAdapter:
    """One injected canonical transport, exact model/dimensions and ordered batches.

    The conservative 8192 UTF-8-byte per-text bound avoids exceeding the model's
    token limit without adding a tokenizer or truncating. Oversized input rejects;
    the caller must explicitly choose a separately reviewed chunking policy.
    """

    model: str
    dimensions: int
    input_schema_version: int
    timeout_seconds: float
    http_client: OpenAIStructuredTransport = field(repr=False)

    def __post_init__(self) -> None:
        if (
            self.model != OPENAI_EMBEDDING_MODEL
            or type(self.dimensions) is not int
            or not 1 <= self.dimensions <= 1536
            or type(self.input_schema_version) is not int
            or self.input_schema_version < 1
            or type(self.timeout_seconds) not in {int, float}
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 600
        ):
            raise ValueError("invalid_openai_embedding_configuration")

    @property
    def space(self) -> EmbeddingSpace:
        return EmbeddingSpace(
            provider="openai",
            model=self.model,
            dimensions=self.dimensions,
            input_schema_version=self.input_schema_version,
        )

    async def embed(self, request: EmbeddingRequest, /) -> EmbeddingResponse:
        try:
            if (
                request.schema_version != self.input_schema_version
                or len(request.texts) > MAX_EMBEDDING_BATCH
                or any(
                    len(text.encode("utf-8")) > MAX_EMBEDDING_INPUT_BYTES for text in request.texts
                )
            ):
                raise ValueError("request_bound")
        except (ValueError, UnicodeError):
            raise EmbeddingGenerationFailed(
                "openai", self.model, "embedding_request_rejected"
            ) from None
        return await asyncio.to_thread(self._embed_sync, request)

    def _embed_sync(self, request: EmbeddingRequest) -> EmbeddingResponse:
        started = time.perf_counter_ns()
        payload = {
            "model": self.model,
            "input": list(request.texts),
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        built = time.perf_counter_ns()
        try:
            admit_provider_dispatch()
            body = self.http_client.post_json(
                "/embeddings",
                payload,
                timeout_seconds=self.timeout_seconds,
                max_response_bytes=MAX_EMBEDDING_RESPONSE_BYTES,
            )
        except OpenAIHttpStatusError as error:
            kind = (
                EmbeddingProviderUnavailable
                if error.status in {408, 409, 425, 429} or error.status >= 500
                else EmbeddingGenerationFailed
            )
            raise kind("openai", self.model, "embedding_provider_rejected") from None
        except (OpenAITransportError, TimeoutError, OSError):
            raise EmbeddingProviderUnavailable(
                "openai", self.model, "embedding_transport_unavailable"
            ) from None
        received = time.perf_counter_ns()
        try:
            parsed = _EmbeddingEnvelope.model_validate(
                bounded_json(body, MAX_EMBEDDING_RESPONSE_BYTES)
            )
            if len(parsed.data) != len(request.texts):
                raise ValueError("embedding_count")
            # Preserve input order explicitly; reject duplicate, missing or reordered
            # indexes rather than silently associating a vector with another text.
            if [item.index for item in parsed.data] != list(range(len(request.texts))):
                raise ValueError("embedding_index")
            vectors = tuple(tuple(item.embedding) for item in parsed.data)
            if any(
                len(vector) != self.dimensions
                or not math.isfinite(math.hypot(*vector))
                or not math.isfinite(sum(value * value for value in vector))
                for vector in vectors
            ):
                raise ValueError("embedding_vector")
            if (
                parsed.usage is not None
                and parsed.usage.total_tokens is not None
                and (
                    parsed.usage.prompt_tokens is None
                    or parsed.usage.total_tokens != parsed.usage.prompt_tokens
                )
            ):
                raise ValueError("embedding_usage")
            response = EmbeddingResponse(
                space=self.space,
                vectors=vectors,
                metrics=ProviderExecutionMetrics(
                    prompt_eval_count=parsed.usage.prompt_tokens
                    if parsed.usage is not None
                    else None,
                    client_request_build_duration_ns=built - started,
                    http_roundtrip_duration_ns=received - built,
                    client_response_parse_duration_ns=time.perf_counter_ns() - received,
                ),
            )
            return response
        except (ValueError, TypeError, UnicodeError, ValidationError, RecursionError):
            raise InvalidEmbeddingResponse(
                "openai", self.model, "embedding_response_invalid"
            ) from None
