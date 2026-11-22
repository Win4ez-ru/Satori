"""Evaluation-only OpenAI ports; production serializers and state owners stay authoritative.

No client or credential is constructed here. Every dispatch requires an injected transport
and shared reservation ledger. Structured adapters reuse the Ollama request builders and
proposal parsers, replacing only their wire transport and explicitly labelling its provenance.
"""

import asyncio
import json
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.core.affect import (
    AffectiveAppraisalProviderError,
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
)
from satori.core.embedding import (
    EmbeddingGenerationFailed,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingSpace,
    InvalidEmbeddingResponse,
)
from satori.core.episode import (
    EpisodeFormationProviderError,
    EpisodeFormationProviderResponse,
    EpisodeFormationRequest,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.core.relationship import (
    RelationshipAppraisalProviderError,
    RelationshipAppraisalRequest,
    RelationshipAppraisalResponse,
)
from satori.core.semantic import (
    SemanticFormationProviderError,
    SemanticFormationProviderResponse,
    SemanticFormationRequest,
)
from satori.infrastructure.providers.ollama_affect import OllamaAffectiveAppraisalAdapter
from satori.infrastructure.providers.ollama_episode import OllamaEpisodeFormationAdapter
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from satori.infrastructure.providers.ollama_relationship import OllamaRelationshipAppraisalAdapter
from satori.infrastructure.providers.ollama_semantic import OllamaSemanticFormationAdapter
from satori.infrastructure.providers.openai_http import (
    OpenAIHttpStatusError,
    OpenAITransportError,
)

CLOSEOUT_MODEL = "gpt-5.6-terra"
CLOSEOUT_RESPONSE_MODELS = (CLOSEOUT_MODEL, "gpt-5.6-terra-2026-08-01")
CLOSEOUT_EMBEDDING_MODEL = "text-embedding-3-small"
MAX_RESPONSE_BYTES = 1_000_000
MAX_EMBEDDING_RESPONSE_BYTES = 16_000_000


@dataclass(frozen=True, slots=True)
class CloseoutUsage:
    """Complete priced usage, never inferred from text length or missing metadata."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    reasoning_output_tokens: int = 0


class CloseoutAPITransport(Protocol):
    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes: ...


class CloseoutDispatchGuard(Protocol):
    def reserve(self, operation: str, model: str, payload: Mapping[str, Any]) -> str: ...

    def settle(
        self,
        reservation_id: str,
        *,
        usage: CloseoutUsage | None,
        response_model: str | None,
        outcome: str,
    ) -> None: ...


class CloseoutAPIError(RuntimeError):
    """Fatal diagnostic failure: callers must stop rather than continue with fallback."""


class _StrictResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)


class _InputDetails(_StrictResponse):
    cached_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)


class _OutputDetails(_StrictResponse):
    reasoning_tokens: int = Field(ge=0)


class _Usage(_StrictResponse):
    input_tokens: int = Field(ge=0)
    input_tokens_details: _InputDetails
    output_tokens: int = Field(ge=0)
    output_tokens_details: _OutputDetails
    total_tokens: int = Field(ge=0)


class _Response(_StrictResponse):
    model: str = Field(min_length=1)
    status: str
    service_tier: Literal["default"]
    output: list[dict[str, Any]]
    usage: _Usage


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json(body: bytes) -> object:
    return json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)


def _strict_schema(value: object) -> object:
    """Preserve proposal semantics while matching Responses' strict-schema subset.

    Uniqueness is checked by existing typed proposal constructors. All nullable fields
    become required, as Responses strict schemas require; this does not change values.
    """

    if isinstance(value, list):
        return [_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: _strict_schema(item)
        for key, item in value.items()
        if key not in {"title", "default", "uniqueItems"}
    }
    if result.get("type") == "object":
        properties = result.get("properties")
        if not isinstance(properties, dict):
            raise CloseoutAPIError("invalid proposal schema")
        result["additionalProperties"] = False
        result["required"] = list(properties)
    return result


def _usage(document: _Usage, output_cap: int) -> CloseoutUsage:
    cached = document.input_tokens_details.cached_tokens
    written = document.input_tokens_details.cache_write_tokens
    if (
        document.total_tokens != document.input_tokens + document.output_tokens
        or cached + written > document.input_tokens
        or document.output_tokens > output_cap
        or document.output_tokens_details.reasoning_tokens != 0
    ):
        raise CloseoutAPIError("inconsistent or unapproved usage")
    return CloseoutUsage(
        input_tokens=document.input_tokens,
        output_tokens=document.output_tokens,
        cached_input_tokens=cached,
        cache_write_input_tokens=written,
    )


@dataclass(slots=True)
class CloseoutOllamaResponsesBridge:
    """Translate exactly one existing structured request without executing Ollama."""

    operation: Literal["affect", "relationship", "episode", "semantic"]
    model: str
    http_client: CloseoutAPITransport
    guard: CloseoutDispatchGuard
    usage: CloseoutUsage | None = None
    reservation_id: str | None = None
    response_model: str | None = None

    def finish(self, *, validated: bool) -> None:
        """Settle only after the existing typed parser has accepted its document."""

        reservation = self.reservation_id
        self.reservation_id = None
        if reservation is not None:
            self.guard.settle(
                reservation,
                usage=self.usage,
                response_model=self.response_model,
                outcome="completed" if validated else "response_invalid",
            )

    def post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        if path != "/api/chat" or self.model != CLOSEOUT_MODEL:
            raise CloseoutAPIError("unsupported evaluation request")
        messages = payload.get("messages")
        options = payload.get("options")
        if (
            not isinstance(messages, list)
            or len(messages) != 2
            or [item.get("role") for item in messages] != ["system", "user"]
            or any(not isinstance(item.get("content"), str) for item in messages)
            or not isinstance(options, dict)
            or type(options.get("num_predict")) is not int
            or options["num_predict"] < 1
            or payload.get("model") != self.model
            or payload.get("think") is not False
            or payload.get("stream") is not False
            or "tools" in payload
        ):
            raise CloseoutAPIError("unsupported structured source payload")
        output_cap = cast(int, options["num_predict"])
        schema = _strict_schema(payload.get("format"))
        if not isinstance(schema, dict):
            raise CloseoutAPIError("missing structured schema")
        wire: dict[str, Any] = {
            "model": self.model,
            "input": messages,
            "reasoning": {"effort": "none"},
            "max_output_tokens": output_cap,
            "temperature": 0.0,
            "service_tier": "default",
            "prompt_cache_options": {"mode": "explicit"},
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": f"satori_closeout_{self.operation}_v1",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        reservation = self.guard.reserve(self.operation, self.model, wire)
        self.reservation_id = reservation
        outcome = "transport_unknown"
        response_model: str | None = None
        self.usage = None
        try:
            admit_provider_dispatch()
            body = self.http_client.post_json(
                "/responses",
                wire,
                timeout_seconds=timeout_seconds,
                max_response_bytes=min(max_response_bytes, MAX_RESPONSE_BYTES),
            )
            outcome = "response_invalid"
            if len(body) > min(max_response_bytes, MAX_RESPONSE_BYTES):
                raise CloseoutAPIError("response byte limit exceeded")
            response = _Response.model_validate(_json(body))
            response_model = response.model
            self.response_model = response_model
            self.usage = _usage(response.usage, output_cap)
            if response.model not in CLOSEOUT_RESPONSE_MODELS:
                raise CloseoutAPIError("unexpected response model")
            if response.status != "completed":
                raise CloseoutAPIError("response was not completed")
            if len(response.output) != 1:
                raise CloseoutAPIError("unexpected output item count")
            message = response.output[0]
            if message.get("type") != "message" or message.get("role") != "assistant":
                raise CloseoutAPIError("unsupported output item")
            parts = message.get("content")
            if not isinstance(parts, list) or len(parts) != 1:
                raise CloseoutAPIError("unexpected output content count")
            part = parts[0]
            if not isinstance(part, dict) or part.get("type") != "output_text":
                raise CloseoutAPIError("refusal or unsupported output content")
            text = part.get("text")
            if not isinstance(text, str) or not text.strip():
                raise CloseoutAPIError("missing proposal text")
            proposal = _json(text.encode("utf-8"))
            if not isinstance(proposal, dict):
                raise CloseoutAPIError("proposal must be a JSON object")
            # The inherited parser validates categories/shape/bounds; these checks bind
            # refs to the exact current request before any owner receives a proposal.
            if self.operation == "affect":
                source = json.loads(messages[1]["content"])
                refs = proposal.get("r")
                if (
                    not isinstance(refs, list)
                    or "e" not in refs
                    or any(ref not in source["allowed_refs"] for ref in refs)
                ):
                    raise CloseoutAPIError("unknown or missing current-event source ref")
            outcome = "response_received"
            return json.dumps(
                {
                    "model": response.model,
                    "message": {"role": "assistant", "content": text},
                    "done": True,
                    "prompt_eval_count": self.usage.input_tokens,
                    "eval_count": self.usage.output_tokens,
                },
                ensure_ascii=False,
            ).encode("utf-8")
        except (OpenAIHttpStatusError, OpenAITransportError, OSError, TimeoutError):
            raise CloseoutAPIError("evaluation transport failed; no retry") from None
        except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError):
            raise CloseoutAPIError("invalid evaluation response") from None
        finally:
            if outcome != "response_received":
                self.reservation_id = None
                self.guard.settle(
                    reservation,
                    usage=self.usage,
                    response_model=response_model,
                    outcome=outcome,
                )


async def _validated_call[Result](
    call: Awaitable[Result], bridge: CloseoutOllamaResponsesBridge
) -> Result:
    try:
        result = await call
    except (
        AffectiveAppraisalProviderError,
        RelationshipAppraisalProviderError,
        EpisodeFormationProviderError,
        SemanticFormationProviderError,
    ):
        bridge.finish(validated=False)
        raise CloseoutAPIError("existing typed proposal parser rejected API document") from None
    except BaseException:
        bridge.finish(validated=False)
        raise
    bridge.finish(validated=True)
    return result


@dataclass(frozen=True, slots=True)
class _StructuredAdapter:
    model: str
    timeout_seconds: float
    max_output_tokens: int
    http_client: CloseoutAPITransport
    guard: CloseoutDispatchGuard

    def __post_init__(self) -> None:
        if (
            self.model != CLOSEOUT_MODEL
            or self.timeout_seconds <= 0
            or type(self.max_output_tokens) is not int
            or self.max_output_tokens < 1
        ):
            raise ValueError("invalid closeout structured adapter configuration")

    def _bridge(
        self, operation: Literal["affect", "relationship", "episode", "semantic"]
    ) -> CloseoutOllamaResponsesBridge:
        return CloseoutOllamaResponsesBridge(operation, self.model, self.http_client, self.guard)

    def _metrics(self, original: ProviderExecutionMetrics | None) -> ProviderExecutionMetrics:
        return replace(
            original or ProviderExecutionMetrics(),
            requested_output_token_limit=self.max_output_tokens,
            provider_output_token_limit=self.max_output_tokens,
            reasoning_output_tokens=0,
            visible_output_tokens=original.eval_count if original is not None else None,
        )


class CloseoutAffectiveAppraisalAdapter(_StructuredAdapter):
    async def generate_structured(
        self, request: AffectiveAppraisalRequest, /
    ) -> AffectiveAppraisalProviderResponse:
        bridge = self._bridge("affect")
        delegate = OllamaAffectiveAppraisalAdapter(
            "https://api.openai.com/v1",
            self.model,
            self.timeout_seconds,
            self.max_output_tokens,
            http_client=cast(OllamaHttpClient, bridge),
        )
        result = await _validated_call(delegate.generate_structured(request), bridge)
        return replace(
            result,
            provider="openai",
            appraisal_method="evaluation.openai.categorical_affective_appraisal.v2"
            + (".previous_exchange.v1" if request.previous_exchange is not None else ""),
            metrics=self._metrics(result.metrics),
        )


class CloseoutRelationshipAppraisalAdapter(_StructuredAdapter):
    async def generate_structured(
        self, request: RelationshipAppraisalRequest, /
    ) -> RelationshipAppraisalResponse:
        bridge = self._bridge("relationship")
        delegate = OllamaRelationshipAppraisalAdapter(
            "https://api.openai.com/v1",
            self.model,
            self.timeout_seconds,
            self.max_output_tokens,
            http_client=cast(OllamaHttpClient, bridge),
        )
        result = await _validated_call(delegate.generate_structured(request), bridge)
        return replace(
            result,
            provider="openai",
            appraisal_method="evaluation.openai.categorical_relationship_appraisal.v1",
            metrics=self._metrics(result.metrics),
        )


class CloseoutEpisodeFormationAdapter(_StructuredAdapter):
    async def generate_structured(
        self, request: EpisodeFormationRequest, /
    ) -> EpisodeFormationProviderResponse:
        bridge = self._bridge("episode")
        delegate = OllamaEpisodeFormationAdapter(
            "https://api.openai.com/v1",
            self.model,
            self.timeout_seconds,
            self.max_output_tokens,
            http_client=cast(OllamaHttpClient, bridge),
        )
        result = await _validated_call(delegate.generate_structured(request), bridge)
        return replace(
            result,
            provider="openai",
            formation_method="evaluation.openai.structured_episode.v1",
            metrics=self._metrics(result.metrics),
        )


class CloseoutSemanticFormationAdapter(_StructuredAdapter):
    async def generate_structured(
        self, request: SemanticFormationRequest, /
    ) -> SemanticFormationProviderResponse:
        bridge = self._bridge("semantic")
        delegate = OllamaSemanticFormationAdapter(
            "https://api.openai.com/v1",
            self.model,
            self.timeout_seconds,
            self.max_output_tokens,
            http_client=cast(OllamaHttpClient, bridge),
        )
        result = await _validated_call(delegate.generate_structured(request), bridge)
        return replace(
            result,
            provider="openai",
            formation_method="evaluation.openai.structured_semantic.v1",
            metrics=self._metrics(result.metrics),
        )


class _EmbeddingUsage(_StrictResponse):
    prompt_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class _EmbeddingItem(_StrictResponse):
    object: Literal["embedding"]
    index: int = Field(ge=0)
    embedding: list[float] = Field(min_length=1)


class _EmbeddingEnvelope(_StrictResponse):
    object: Literal["list"]
    model: Literal["text-embedding-3-small"]
    data: list[_EmbeddingItem] = Field(min_length=1)
    usage: _EmbeddingUsage


@dataclass(frozen=True, slots=True)
class CloseoutEmbeddingAdapter:
    model: str
    dimensions: int
    input_schema_version: int
    timeout_seconds: float
    http_client: CloseoutAPITransport
    guard: CloseoutDispatchGuard
    operation: Literal["embedding_query", "embedding_index"]

    def __post_init__(self) -> None:
        if (
            self.model != CLOSEOUT_EMBEDDING_MODEL
            or self.dimensions != 768
            or self.input_schema_version < 1
            or self.timeout_seconds <= 0
        ):
            raise ValueError("invalid closeout embedding configuration")

    @property
    def space(self) -> EmbeddingSpace:
        return EmbeddingSpace("openai", self.model, self.dimensions, self.input_schema_version)

    async def embed(self, request: EmbeddingRequest, /) -> EmbeddingResponse:
        if request.schema_version != self.input_schema_version:
            raise EmbeddingGenerationFailed("openai", self.model, "embedding schema mismatch")
        if len(request.texts) != 1:
            raise CloseoutAPIError("evaluation embedding requires exactly one input per call")
        return await asyncio.to_thread(self._embed_sync, request)

    def _embed_sync(self, request: EmbeddingRequest) -> EmbeddingResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": request.texts[0],
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        reservation = self.guard.reserve(self.operation, self.model, payload)
        usage: CloseoutUsage | None = None
        response_model: str | None = None
        outcome = "transport_unknown"
        try:
            admit_provider_dispatch()
            body = self.http_client.post_json(
                "/embeddings",
                payload,
                timeout_seconds=self.timeout_seconds,
                max_response_bytes=MAX_EMBEDDING_RESPONSE_BYTES,
            )
            outcome = "response_invalid"
            if len(body) > MAX_EMBEDDING_RESPONSE_BYTES:
                raise CloseoutAPIError("embedding response byte limit exceeded")
            parsed = _EmbeddingEnvelope.model_validate(_json(body))
            response_model = parsed.model
            if parsed.usage.total_tokens != parsed.usage.prompt_tokens:
                raise CloseoutAPIError("inconsistent embedding usage")
            usage = CloseoutUsage(parsed.usage.prompt_tokens, 0, 0, 0)
            if len(parsed.data) != len(request.texts) or [
                item.index for item in parsed.data
            ] != list(range(len(request.texts))):
                raise CloseoutAPIError("embedding count or index mismatch")
            response = EmbeddingResponse(
                space=self.space,
                vectors=tuple(tuple(item.embedding) for item in parsed.data),
                metrics=ProviderExecutionMetrics(prompt_eval_count=usage.input_tokens),
            )
            outcome = "completed"
            return response
        except (OpenAIHttpStatusError, OpenAITransportError, OSError, TimeoutError):
            raise CloseoutAPIError("embedding transport failed; no retry") from None
        except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError):
            raise InvalidEmbeddingResponse(
                "openai", self.model, "invalid closeout embedding response"
            ) from None
        finally:
            self.guard.settle(
                reservation,
                usage=usage,
                response_model=response_model,
                outcome=outcome,
            )


@dataclass(frozen=True, slots=True)
class CloseoutForegroundTransport:
    """Account for the unchanged foreground adapter's one actual HTTP attempt."""

    http_client: CloseoutAPITransport
    guard: CloseoutDispatchGuard
    model: str = CLOSEOUT_MODEL

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        if (
            path != "/responses"
            or self.model != CLOSEOUT_MODEL
            or payload.get("model") != self.model
            or payload.get("reasoning") != {"effort": "none"}
            or payload.get("store") is not False
            or payload.get("service_tier") != "default"
            or "tools" in payload
            or "previous_response_id" in payload
            or "conversation" in payload
        ):
            raise CloseoutAPIError("unsupported foreground evaluation request")
        cap = payload.get("max_output_tokens")
        if type(cap) is not int or cap < 1:
            raise CloseoutAPIError("missing foreground output cap")
        reservation = self.guard.reserve("foreground", self.model, payload)
        usage: CloseoutUsage | None = None
        response_model: str | None = None
        outcome = "transport_unknown"
        try:
            admit_provider_dispatch()
            body = self.http_client.post_json(
                path,
                payload,
                timeout_seconds=timeout_seconds,
                max_response_bytes=min(max_response_bytes, MAX_RESPONSE_BYTES),
            )
            outcome = "response_invalid"
            if len(body) > min(max_response_bytes, MAX_RESPONSE_BYTES):
                raise CloseoutAPIError("foreground response byte limit exceeded")
            response = _Response.model_validate(_json(body))
            response_model = response.model
            usage = _usage(response.usage, cap)
            if response.model not in CLOSEOUT_RESPONSE_MODELS or response.status != "completed":
                raise CloseoutAPIError("unexpected foreground model or completion status")
            if len(response.output) != 1:
                raise CloseoutAPIError("unexpected foreground output item count")
            item = response.output[0]
            if item.get("type") != "message" or item.get("role") != "assistant":
                raise CloseoutAPIError("unsupported foreground output item")
            content = item.get("content")
            if (
                not isinstance(content, list)
                or len(content) != 1
                or not isinstance(content[0], dict)
                or content[0].get("type") != "output_text"
                or not isinstance(content[0].get("text"), str)
                or not content[0]["text"].strip()
            ):
                raise CloseoutAPIError("foreground refusal or invalid output content")
            outcome = "completed"
            return body
        except (OpenAIHttpStatusError, OpenAITransportError, OSError, TimeoutError):
            raise CloseoutAPIError("foreground transport failed; no retry") from None
        except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError):
            raise CloseoutAPIError("invalid foreground evaluation response") from None
        finally:
            self.guard.settle(
                reservation,
                usage=usage,
                response_model=response_model,
                outcome=outcome,
            )
