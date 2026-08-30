"""Foreground conversation adapter for the OpenAI Responses API."""

import asyncio
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from satori.core.conversation import (
    ConversationProviderError,
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
    GenerationFailed,
    InvalidProviderResponse,
    ProviderUnavailable,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.infrastructure.providers.openai_http import (
    OpenAIHttpClient,
    OpenAIHttpStatusError,
    OpenAITransportError,
)

OPENAI_PROVIDER_NAME = "openai"
MAX_HTTP_RESPONSE_BYTES = 1_000_000


class _OpenAITransport(Protocol):
    def post_json(
        self,
        path: str,
        payload: dict[str, object],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes: ...


class _OpenAITextContent(BaseModel):
    model_config = ConfigDict(
        extra="ignore", strict=True, str_strip_whitespace=True, hide_input_in_errors=True
    )

    type: Literal["output_text"]
    text: str = Field(min_length=1)


class _OpenAIRefusalContent(BaseModel):
    model_config = ConfigDict(
        extra="ignore", strict=True, str_strip_whitespace=True, hide_input_in_errors=True
    )

    type: Literal["refusal"]
    refusal: str = Field(min_length=1)


class _OpenAIOutputMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)

    type: Literal["message"]
    role: Literal["assistant"]
    content: list[_OpenAITextContent | _OpenAIRefusalContent] = Field(min_length=1)


class _OpenAIOutputTokenDetails(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)

    reasoning_tokens: int | None = Field(default=None, ge=0)


class _OpenAIInputTokenDetails(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)

    cached_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)


class _OpenAIUsage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)

    input_tokens: int | None = Field(default=None, ge=0)
    input_tokens_details: _OpenAIInputTokenDetails | None = None
    output_tokens: int | None = Field(default=None, ge=0)
    output_tokens_details: _OpenAIOutputTokenDetails | None = None


class _OpenAIIncompleteReason(StrEnum):
    MAX_OUTPUT_TOKENS = "max_output_tokens"
    UNKNOWN = "unknown"


class _OpenAIIncompleteDetails(BaseModel):
    model_config = ConfigDict(
        extra="ignore", strict=True, str_strip_whitespace=True, hide_input_in_errors=True
    )

    reason: str = Field(min_length=1)


class _OpenAIResponse(BaseModel):
    model_config = ConfigDict(
        extra="ignore", strict=True, str_strip_whitespace=True, hide_input_in_errors=True
    )

    model: str = Field(min_length=1)
    status: Literal[
        "completed",
        "incomplete",
        "failed",
        "cancelled",
        "queued",
        "in_progress",
    ]
    output: list[object]
    service_tier: Literal["default"]
    usage: _OpenAIUsage | None = None
    incomplete_details: _OpenAIIncompleteDetails | None = None


def _safe_incomplete_reason(
    details: _OpenAIIncompleteDetails | None,
) -> _OpenAIIncompleteReason:
    if details is not None and details.reason == _OpenAIIncompleteReason.MAX_OUTPUT_TOKENS:
        return _OpenAIIncompleteReason.MAX_OUTPUT_TOKENS
    return _OpenAIIncompleteReason.UNKNOWN


@dataclass(frozen=True, slots=True)
class OpenAIConversationAdapter:
    """Map provider-neutral messages to one stateless OpenAI Responses request."""

    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"]
    reasoning_token_allowance: int
    http_client: _OpenAITransport | None = None

    def __post_init__(self) -> None:
        base_url = self.base_url.strip().rstrip("/")
        api_key = self.api_key.strip()
        model = self.model.strip()
        if not base_url:
            raise ValueError("OpenAI base_url must not be blank")
        if not api_key:
            raise ValueError("OpenAI API key must not be blank")
        if not model or any(character.isspace() for character in model):
            raise ValueError("OpenAI model must be one non-blank identifier")
        if self.timeout_seconds <= 0:
            raise ValueError("OpenAI timeout_seconds must be positive")
        if (
            type(self.reasoning_token_allowance) is not int
            or not 0 <= self.reasoning_token_allowance <= 4096
        ):
            raise ValueError("OpenAI reasoning_token_allowance must be between 0 and 4096")
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", model)

    async def generate(
        self,
        request: ConversationProviderRequest,
        /,
    ) -> ConversationProviderResponse:
        """Execute blocking HTTPS outside the event-loop thread."""

        return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(
        self,
        request: ConversationProviderRequest,
    ) -> ConversationProviderResponse:
        requested_output_limit = request.parameters.max_output_tokens
        provider_output_limit = requested_output_limit
        if self.reasoning_effort != "none":
            provider_output_limit += self.reasoning_token_allowance
        payload: dict[str, object] = {
            "model": self.model,
            "input": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "max_output_tokens": provider_output_limit,
            "reasoning": {"effort": self.reasoning_effort},
            "service_tier": "default",
            "prompt_cache_options": {"mode": "explicit"},
            "store": False,
        }
        if self.reasoning_effort == "none":
            payload["temperature"] = request.parameters.temperature
        owned_client = self.http_client is None
        client: _OpenAITransport = self.http_client or OpenAIHttpClient(
            self.base_url,
            self.api_key,
            pool_size=1,
        )
        try:
            body = client.post_json(
                "/responses",
                payload,
                timeout_seconds=self.timeout_seconds,
                max_response_bytes=MAX_HTTP_RESPONSE_BYTES,
            )
        except OpenAIHttpStatusError as error:
            error_type: type[ConversationProviderError]
            if error.status == 429:
                error_type = ProviderUnavailable
                reason = ConversationProviderFailureReason.RATE_OR_QUOTA_LIMITED
            elif error.status in {408, 409, 425} or error.status >= 500:
                error_type = ProviderUnavailable
                reason = ConversationProviderFailureReason.TEMPORARILY_UNAVAILABLE
            elif error.status in {401, 403}:
                error_type = GenerationFailed
                reason = ConversationProviderFailureReason.CREDENTIALS_REJECTED
            elif error.status == 404:
                error_type = GenerationFailed
                reason = ConversationProviderFailureReason.RESOURCE_NOT_FOUND
            else:
                error_type = GenerationFailed
                reason = ConversationProviderFailureReason.REQUEST_REJECTED
            raise error_type(
                OPENAI_PROVIDER_NAME,
                self.model,
                f"OpenAI returned HTTP {error.status}",
                reason=reason,
            ) from error
        except OpenAITransportError as error:
            raise ProviderUnavailable(
                OPENAI_PROVIDER_NAME,
                self.model,
                "OpenAI is unavailable or timed out",
                reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
            ) from error
        finally:
            if owned_client:
                assert isinstance(client, OpenAIHttpClient)
                client.close()

        if len(body) > MAX_HTTP_RESPONSE_BYTES:
            raise InvalidProviderResponse(
                OPENAI_PROVIDER_NAME,
                self.model,
                "OpenAI response exceeded the adapter byte limit",
                reason=ConversationProviderFailureReason.RESPONSE_TOO_LARGE,
            )
        try:
            raw: object = json.loads(body.decode("utf-8"))
            parsed = _OpenAIResponse.model_validate(raw)
            try:
                usage, metrics, visible_output_tokens = self._project_usage(
                    parsed.usage,
                    requested_output_limit=requested_output_limit,
                    provider_output_limit=provider_output_limit,
                )
            except ValueError:
                raise InvalidProviderResponse(
                    OPENAI_PROVIDER_NAME,
                    self.model,
                    "OpenAI returned inconsistent usage metadata",
                    reason=ConversationProviderFailureReason.USAGE_METADATA_INVALID,
                ) from None
            if parsed.status == "incomplete":
                incomplete_reason = _safe_incomplete_reason(parsed.incomplete_details)
                failure_reason = (
                    ConversationProviderFailureReason.OUTPUT_TOKEN_LIMIT
                    if incomplete_reason is _OpenAIIncompleteReason.MAX_OUTPUT_TOKENS
                    else ConversationProviderFailureReason.INCOMPLETE_UNKNOWN
                )
                raise GenerationFailed(
                    OPENAI_PROVIDER_NAME,
                    self.model,
                    f"OpenAI response ended with status incomplete; reason={incomplete_reason}",
                    reason=failure_reason,
                    metrics=metrics,
                )
            if parsed.status in {"failed", "cancelled", "queued", "in_progress"}:
                failure_reason = (
                    ConversationProviderFailureReason.GENERATION_CANCELLED
                    if parsed.status == "cancelled"
                    else ConversationProviderFailureReason.GENERATION_FAILED
                )
                raise GenerationFailed(
                    OPENAI_PROVIDER_NAME,
                    self.model,
                    f"OpenAI response ended with status {parsed.status}",
                    reason=failure_reason,
                    metrics=metrics,
                )
            text_parts: list[str] = []
            for raw_item in parsed.output:
                if isinstance(raw_item, dict) and raw_item.get("type") == "reasoning":
                    continue
                if not isinstance(raw_item, dict) or raw_item.get("type") != "message":
                    raise InvalidProviderResponse(
                        OPENAI_PROVIDER_NAME,
                        self.model,
                        "OpenAI returned an unsupported output item",
                        reason=ConversationProviderFailureReason.RESPONSE_MALFORMED,
                        metrics=metrics,
                    )
                try:
                    item = _OpenAIOutputMessage.model_validate(raw_item)
                except ValidationError:
                    raise InvalidProviderResponse(
                        OPENAI_PROVIDER_NAME,
                        self.model,
                        "OpenAI returned a malformed assistant output item",
                        reason=ConversationProviderFailureReason.RESPONSE_MALFORMED,
                        metrics=metrics,
                    ) from None
                if any(isinstance(part, _OpenAIRefusalContent) for part in item.content):
                    raise GenerationFailed(
                        OPENAI_PROVIDER_NAME,
                        self.model,
                        "OpenAI refused to generate a conversational response",
                        reason=ConversationProviderFailureReason.RESPONSE_REFUSED,
                        metrics=metrics,
                    )
                text_parts.extend(
                    part.text for part in item.content if isinstance(part, _OpenAITextContent)
                )
            text = "\n".join(text_parts).strip()
            if not text:
                raise InvalidProviderResponse(
                    OPENAI_PROVIDER_NAME,
                    self.model,
                    "OpenAI response contains no assistant output_text",
                    reason=ConversationProviderFailureReason.MISSING_ASSISTANT_TEXT,
                    metrics=metrics,
                )
            if self.reasoning_effort != "none" and self.reasoning_token_allowance > 0:
                if visible_output_tokens is None:
                    raise InvalidProviderResponse(
                        OPENAI_PROVIDER_NAME,
                        self.model,
                        "OpenAI usage required to enforce the visible output limit",
                        reason=ConversationProviderFailureReason.USAGE_METADATA_INVALID,
                        metrics=metrics,
                    )
                if visible_output_tokens > requested_output_limit:
                    raise InvalidProviderResponse(
                        OPENAI_PROVIDER_NAME,
                        self.model,
                        "OpenAI visible output exceeded the requested output limit",
                        reason=(ConversationProviderFailureReason.VISIBLE_OUTPUT_LIMIT_EXCEEDED),
                        metrics=metrics,
                    )
            return ConversationProviderResponse(
                text=text,
                provider=OPENAI_PROVIDER_NAME,
                model=parsed.model,
                finish_status=parsed.status,
                usage=usage,
                metrics=metrics,
            )
        except (GenerationFailed, InvalidProviderResponse):
            raise
        except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError):
            raise InvalidProviderResponse(
                OPENAI_PROVIDER_NAME,
                self.model,
                "OpenAI returned a malformed Responses API result",
                reason=ConversationProviderFailureReason.RESPONSE_MALFORMED,
            ) from None

    @staticmethod
    def _project_usage(
        usage: _OpenAIUsage | None,
        *,
        requested_output_limit: int,
        provider_output_limit: int,
    ) -> tuple[ConversationUsage | None, ProviderExecutionMetrics, int | None]:
        output_tokens = usage.output_tokens if usage is not None else None
        reasoning_tokens = (
            usage.output_tokens_details.reasoning_tokens
            if usage is not None and usage.output_tokens_details is not None
            else None
        )
        visible_output_tokens = None
        if output_tokens is not None and reasoning_tokens is not None:
            if reasoning_tokens > output_tokens:
                raise ValueError("reasoning token count exceeds total output token count")
            visible_output_tokens = output_tokens - reasoning_tokens
        projected_usage = None
        if usage is not None:
            cached_input_tokens = (
                usage.input_tokens_details.cached_tokens
                if usage.input_tokens_details is not None
                else None
            )
            cache_write_input_tokens = (
                usage.input_tokens_details.cache_write_tokens
                if usage.input_tokens_details is not None
                else None
            )
            projected_usage = ConversationUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=cached_input_tokens,
                cache_write_input_tokens=cache_write_input_tokens,
            )
        metrics = ProviderExecutionMetrics(
            requested_output_token_limit=requested_output_limit,
            provider_output_token_limit=provider_output_limit,
            reasoning_output_tokens=reasoning_tokens,
            visible_output_tokens=visible_output_tokens,
        )
        return projected_usage, metrics, visible_output_tokens
