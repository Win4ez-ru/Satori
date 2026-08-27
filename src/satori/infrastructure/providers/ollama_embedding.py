"""Ollama adapter for provider-neutral, explicitly dimensioned embeddings."""

import asyncio
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from satori.core.embedding import (
    EmbeddingGenerationFailed,
    EmbeddingProviderUnavailable,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingSpace,
    InvalidEmbeddingResponse,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.infrastructure.providers.ollama_http import (
    OllamaHttpClient,
    OllamaHttpStatusError,
)

OLLAMA_PROVIDER_NAME = "ollama"
MAX_EMBEDDING_RESPONSE_BYTES = 16_000_000


class _OllamaEmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, str_strip_whitespace=True)

    model: str = Field(min_length=1)
    embeddings: list[list[float]] = Field(min_length=1)
    total_duration: int | None = Field(default=None, ge=0)
    load_duration: int | None = Field(default=None, ge=0)
    prompt_eval_count: int | None = Field(default=None, ge=0)


@dataclass(frozen=True, slots=True)
class OllamaEmbeddingAdapter:
    """Call `/api/embed` with batching, no truncation, and an exact vector space."""

    base_url: str
    model: str
    dimensions: int
    input_schema_version: int
    timeout_seconds: float
    http_client: OllamaHttpClient | None = None

    def __post_init__(self) -> None:
        base_url = self.base_url.strip().rstrip("/")
        model = self.model.strip()
        if not base_url or not model:
            raise ValueError("Ollama embedding base_url and model must not be blank")
        if self.dimensions < 1 or self.input_schema_version < 1:
            raise ValueError("Ollama embedding dimensions and input schema must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("Ollama embedding timeout_seconds must be positive")
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)

    @property
    def space(self) -> EmbeddingSpace:
        return EmbeddingSpace(
            provider=OLLAMA_PROVIDER_NAME,
            model=self.model,
            dimensions=self.dimensions,
            input_schema_version=self.input_schema_version,
        )

    async def embed(self, request: EmbeddingRequest, /) -> EmbeddingResponse:
        if request.schema_version != self.input_schema_version:
            raise EmbeddingGenerationFailed(
                OLLAMA_PROVIDER_NAME,
                self.model,
                "embedding request schema is incompatible with configured space",
            )
        return await asyncio.to_thread(self._embed_sync, request)

    def _embed_sync(self, request: EmbeddingRequest) -> EmbeddingResponse:
        payload = {
            "model": self.model,
            "input": list(request.texts),
            "truncate": False,
            "dimensions": self.dimensions,
        }
        try:
            if self.http_client is not None:
                body = self.http_client.post_json(
                    "/api/embed",
                    payload,
                    timeout_seconds=self.timeout_seconds,
                    max_response_bytes=MAX_EMBEDDING_RESPONSE_BYTES,
                )
            else:
                http_request = Request(
                    f"{self.base_url}/api/embed",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    method="POST",
                )
                with urlopen(http_request, timeout=self.timeout_seconds) as response:
                    body = response.read(MAX_EMBEDDING_RESPONSE_BYTES + 1)
        except HTTPError as error:
            error_type = (
                EmbeddingProviderUnavailable if error.code >= 500 else EmbeddingGenerationFailed
            )
            raise error_type(
                OLLAMA_PROVIDER_NAME,
                self.model,
                f"Ollama returned HTTP {error.code}",
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise EmbeddingProviderUnavailable(
                OLLAMA_PROVIDER_NAME,
                self.model,
                "Ollama embedding endpoint is unavailable or timed out",
            ) from error
        except OllamaHttpStatusError as error:
            error_type = (
                EmbeddingProviderUnavailable if error.status >= 500 else EmbeddingGenerationFailed
            )
            raise error_type(
                OLLAMA_PROVIDER_NAME,
                self.model,
                f"Ollama returned HTTP {error.status}",
            ) from error

        if len(body) > MAX_EMBEDDING_RESPONSE_BYTES:
            raise InvalidEmbeddingResponse(
                OLLAMA_PROVIDER_NAME,
                self.model,
                "Ollama embedding response exceeded the byte limit",
            )
        try:
            raw: object = json.loads(body.decode("utf-8"))
            parsed = _OllamaEmbeddingResponse.model_validate(raw)
            response = EmbeddingResponse(
                space=self.space,
                vectors=tuple(tuple(vector) for vector in parsed.embeddings),
                metrics=ProviderExecutionMetrics(
                    total_duration_ns=parsed.total_duration,
                    load_duration_ns=parsed.load_duration,
                    prompt_eval_count=parsed.prompt_eval_count,
                ),
            )
        except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
            raise InvalidEmbeddingResponse(
                OLLAMA_PROVIDER_NAME,
                self.model,
                "Ollama returned malformed or incompatible embedding JSON",
            ) from error
        if len(response.vectors) != len(request.texts):
            raise InvalidEmbeddingResponse(
                OLLAMA_PROVIDER_NAME,
                self.model,
                "Ollama returned the wrong embedding count",
            )
        return response
