"""Minimal asynchronous adapter for Ollama's non-streaming local chat API."""

import asyncio
import json
from dataclasses import dataclass
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from satori.core.conversation import (
    ConversationMessageRole,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
    GenerationFailed,
    InvalidProviderResponse,
    ProviderUnavailable,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.infrastructure.providers.inference_scheduler import (
    InferencePriority,
    OllamaInferenceScheduler,
)
from satori.infrastructure.providers.ollama_http import (
    OllamaHttpClient,
    OllamaHttpStatusError,
)

OLLAMA_PROVIDER_NAME = "ollama"
MAX_HTTP_RESPONSE_BYTES = 1_000_000


class _OllamaMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, str_strip_whitespace=True)

    role: Literal["assistant"]
    content: str


class _OllamaChatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, str_strip_whitespace=True)

    model: str = Field(min_length=1)
    message: _OllamaMessage
    done: bool
    done_reason: str | None = None
    prompt_eval_count: int | None = Field(default=None, ge=0)
    eval_count: int | None = Field(default=None, ge=0)
    total_duration: int | None = Field(default=None, ge=0)
    load_duration: int | None = Field(default=None, ge=0)
    prompt_eval_duration: int | None = Field(default=None, ge=0)
    eval_duration: int | None = Field(default=None, ge=0)


@dataclass(frozen=True, slots=True)
class OllamaConversationAdapter:
    """Map provider-neutral messages to Ollama without exposing vendor types."""

    base_url: str
    model: str
    timeout_seconds: float
    keep_alive: str = "5m"
    http_client: OllamaHttpClient | None = None
    scheduler: OllamaInferenceScheduler | None = None

    def __post_init__(self) -> None:
        base_url = self.base_url.strip().rstrip("/")
        model = self.model.strip()
        if not base_url:
            raise ValueError("Ollama base_url must not be blank")
        if not model:
            raise ValueError("Ollama model must not be blank")
        if self.timeout_seconds <= 0:
            raise ValueError("Ollama timeout_seconds must be positive")
        keep_alive = self.keep_alive.strip()
        if not keep_alive:
            raise ValueError("Ollama keep_alive must not be blank")
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "keep_alive", keep_alive)

    async def generate(
        self,
        request: ConversationProviderRequest,
        /,
    ) -> ConversationProviderResponse:
        """Execute blocking stdlib HTTP outside the event-loop thread."""

        if self.scheduler is None:
            return await asyncio.to_thread(self._generate_sync, request)
        async with self.scheduler.reserve(InferencePriority.CONVERSATION):
            return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(
        self,
        request: ConversationProviderRequest,
    ) -> ConversationProviderResponse:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": self._ollama_role(message.role),
                    "content": message.content,
                }
                for message in request.messages
            ],
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": request.parameters.temperature,
                "num_predict": request.parameters.max_output_tokens,
            },
        }
        try:
            if self.http_client is not None:
                body = self.http_client.post_json(
                    "/api/chat",
                    payload,
                    timeout_seconds=self.timeout_seconds,
                    max_response_bytes=MAX_HTTP_RESPONSE_BYTES,
                )
            else:
                http_request = Request(
                    f"{self.base_url}/api/chat",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    method="POST",
                )
                with urlopen(http_request, timeout=self.timeout_seconds) as response:
                    body = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
        except HTTPError as error:
            error_type = ProviderUnavailable if error.code >= 500 else GenerationFailed
            raise error_type(
                OLLAMA_PROVIDER_NAME,
                self.model,
                f"Ollama returned HTTP {error.code}",
            ) from error
        except OllamaHttpStatusError as error:
            error_type = ProviderUnavailable if error.status >= 500 else GenerationFailed
            raise error_type(
                OLLAMA_PROVIDER_NAME,
                self.model,
                f"Ollama returned HTTP {error.status}",
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise ProviderUnavailable(
                OLLAMA_PROVIDER_NAME,
                self.model,
                "Ollama is unavailable or timed out",
            ) from error

        if len(body) > MAX_HTTP_RESPONSE_BYTES:
            raise InvalidProviderResponse(
                OLLAMA_PROVIDER_NAME,
                self.model,
                "Ollama response exceeded the adapter byte limit",
            )
        try:
            raw: object = json.loads(body.decode("utf-8"))
            parsed = _OllamaChatResponse.model_validate(raw)
        except (UnicodeError, json.JSONDecodeError, ValidationError) as error:
            raise InvalidProviderResponse(
                OLLAMA_PROVIDER_NAME,
                self.model,
                "Ollama returned malformed chat JSON",
            ) from error
        if not parsed.done:
            raise InvalidProviderResponse(
                OLLAMA_PROVIDER_NAME,
                self.model,
                "Ollama returned an incomplete non-streaming response",
            )

        try:
            usage = None
            if parsed.prompt_eval_count is not None or parsed.eval_count is not None:
                usage = ConversationUsage(
                    input_tokens=parsed.prompt_eval_count,
                    output_tokens=parsed.eval_count,
                )
            return ConversationProviderResponse(
                text=parsed.message.content,
                provider=OLLAMA_PROVIDER_NAME,
                model=parsed.model,
                finish_status=parsed.done_reason or "completed",
                usage=usage,
                metrics=ProviderExecutionMetrics(
                    total_duration_ns=parsed.total_duration,
                    load_duration_ns=parsed.load_duration,
                    prompt_eval_duration_ns=parsed.prompt_eval_duration,
                    eval_duration_ns=parsed.eval_duration,
                    prompt_eval_count=parsed.prompt_eval_count,
                    eval_count=parsed.eval_count,
                ),
            )
        except ValueError as error:
            raise InvalidProviderResponse(
                OLLAMA_PROVIDER_NAME,
                self.model,
                "Ollama response violates the provider-neutral contract",
            ) from error

    @staticmethod
    def _ollama_role(role: ConversationMessageRole) -> str:
        if role is ConversationMessageRole.DEVELOPER:
            return "system"
        return role.value
