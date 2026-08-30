"""OpenAI-compatible foreground conversation adapter for Yandex AI Studio."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from satori.core.conversation import (
    ConversationMessageRole,
    ConversationProviderError,
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
    GenerationFailed,
    InvalidProviderResponse,
    ProviderUnavailable,
)
from satori.infrastructure.providers.yandex_ai_studio_http import (
    YandexAIStudioHttpClient,
    YandexAIStudioHttpStatusError,
    YandexAIStudioTransportError,
)

YANDEX_AI_STUDIO_PROVIDER_NAME = "yandex_ai_studio"
MAX_HTTP_RESPONSE_BYTES = 1_000_000


class _YandexTransport(Protocol):
    def post_json(
        self,
        path: str,
        payload: dict[str, object],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes: ...


class _YandexMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, str_strip_whitespace=True)

    role: Literal["assistant"]
    content: str = Field(min_length=1)


class _YandexChoice(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, str_strip_whitespace=True)

    index: int = Field(ge=0)
    message: _YandexMessage
    finish_reason: str | None = None


class _YandexUsage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class _YandexChatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, str_strip_whitespace=True)

    model: str = Field(min_length=1)
    choices: list[_YandexChoice] = Field(min_length=1, max_length=1)
    usage: _YandexUsage | None = None


def yandex_model_uri(model: str, folder_id: str | None) -> str:
    """Resolve either an explicit URI or a folder-scoped model identifier."""

    normalized_model = model.strip()
    if normalized_model.startswith("gpt://"):
        parsed = urlsplit(normalized_model)
        if (
            parsed.scheme != "gpt"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.path in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in normalized_model)
        ):
            raise ValueError("Yandex AI Studio gpt model URI is invalid")
        return normalized_model
    if not normalized_model or normalized_model.startswith("/") or ".." in normalized_model:
        raise ValueError("Yandex AI Studio model identifier is invalid")
    if any(character.isspace() for character in normalized_model):
        raise ValueError("Yandex AI Studio model identifier must not contain whitespace")
    normalized_folder = folder_id.strip() if folder_id is not None else ""
    if (
        not normalized_folder
        or "/" in normalized_folder
        or any(character.isspace() for character in normalized_folder)
    ):
        raise ValueError("Yandex AI Studio folder_id is required for a model identifier")
    return f"gpt://{normalized_folder}/{normalized_model}"


@dataclass(frozen=True, slots=True)
class YandexAIStudioConversationAdapter:
    """Map provider-neutral messages to Yandex's OpenAI-compatible chat endpoint."""

    base_url: str
    api_key: str = field(repr=False)
    model: str
    folder_id: str | None
    timeout_seconds: float
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    http_client: _YandexTransport | None = None

    def __post_init__(self) -> None:
        base_url = self.base_url.strip().rstrip("/")
        api_key = self.api_key.strip()
        if not base_url:
            raise ValueError("Yandex AI Studio base_url must not be blank")
        if not api_key:
            raise ValueError("Yandex AI Studio API key must not be blank")
        if self.timeout_seconds <= 0:
            raise ValueError("Yandex AI Studio timeout_seconds must be positive")
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", yandex_model_uri(self.model, self.folder_id))

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
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {
                    "role": self._provider_role(message.role),
                    "content": message.content,
                }
                for message in request.messages
            ],
            "stream": False,
            "temperature": request.parameters.temperature,
            "max_tokens": request.parameters.max_output_tokens,
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        owned_client = self.http_client is None
        client: _YandexTransport = self.http_client or YandexAIStudioHttpClient(
            self.base_url,
            self.api_key,
            pool_size=1,
        )
        try:
            body = client.post_json(
                "/chat/completions",
                payload,
                timeout_seconds=self.timeout_seconds,
                max_response_bytes=MAX_HTTP_RESPONSE_BYTES,
            )
        except YandexAIStudioHttpStatusError as error:
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
                YANDEX_AI_STUDIO_PROVIDER_NAME,
                self.model,
                f"Yandex AI Studio returned HTTP {error.status}",
                reason=reason,
            ) from error
        except YandexAIStudioTransportError as error:
            raise ProviderUnavailable(
                YANDEX_AI_STUDIO_PROVIDER_NAME,
                self.model,
                "Yandex AI Studio is unavailable or timed out",
                reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
            ) from error
        finally:
            if owned_client:
                assert isinstance(client, YandexAIStudioHttpClient)
                client.close()

        if len(body) > MAX_HTTP_RESPONSE_BYTES:
            raise InvalidProviderResponse(
                YANDEX_AI_STUDIO_PROVIDER_NAME,
                self.model,
                "Yandex AI Studio response exceeded the adapter byte limit",
                reason=ConversationProviderFailureReason.RESPONSE_TOO_LARGE,
            )
        try:
            raw: object = json.loads(body.decode("utf-8"))
            parsed = _YandexChatResponse.model_validate(raw)
            choice = parsed.choices[0]
            usage = None
            if parsed.usage is not None:
                usage = ConversationUsage(
                    input_tokens=parsed.usage.prompt_tokens,
                    output_tokens=parsed.usage.completion_tokens,
                )
            return ConversationProviderResponse(
                text=choice.message.content,
                provider=YANDEX_AI_STUDIO_PROVIDER_NAME,
                model=parsed.model,
                finish_status=choice.finish_reason or "completed",
                usage=usage,
            )
        except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
            raise InvalidProviderResponse(
                YANDEX_AI_STUDIO_PROVIDER_NAME,
                self.model,
                "Yandex AI Studio returned a malformed chat response",
                reason=ConversationProviderFailureReason.RESPONSE_MALFORMED,
            ) from error

    @staticmethod
    def _provider_role(role: ConversationMessageRole) -> str:
        if role is ConversationMessageRole.DEVELOPER:
            return "system"
        return role.value
