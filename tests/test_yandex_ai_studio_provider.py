"""Daemon-free Yandex AI Studio conversation adapter contract tests."""

import asyncio
import json

import pytest

from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderRequest,
    GenerationFailed,
    InvalidProviderResponse,
    ProviderUnavailable,
)
from satori.infrastructure.providers.yandex_ai_studio import (
    YandexAIStudioConversationAdapter,
    yandex_model_uri,
)
from satori.infrastructure.providers.yandex_ai_studio_http import (
    YandexAIStudioHttpStatusError,
    YandexAIStudioTransportError,
)


class FakeYandexTransport:
    """Capture one adapter call or raise a configured transport exception."""

    def __init__(self, body: bytes = b"{}", error: Exception | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[tuple[str, dict[str, object], float, int]] = []

    def post_json(
        self,
        path: str,
        payload: dict[str, object],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        self.calls.append((path, payload, timeout_seconds, max_response_bytes))
        if self.error is not None:
            raise self.error
        return self.body


def provider_request() -> ConversationProviderRequest:
    return ConversationProviderRequest(
        schema_version=1,
        trace_id="trace-yandex",
        context_schema_version=16,
        messages=(
            ConversationMessage(ConversationMessageRole.SYSTEM, "trusted policy"),
            ConversationMessage(ConversationMessageRole.DEVELOPER, "trusted character data"),
            ConversationMessage(ConversationMessageRole.USER, "untrusted user text"),
        ),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.4,
            max_output_tokens=321,
        ),
    )


def adapter(transport: FakeYandexTransport) -> YandexAIStudioConversationAdapter:
    return YandexAIStudioConversationAdapter(
        base_url="https://ai.api.cloud.yandex.net/v1",
        api_key="private-test-key",
        model="deepseek-v4-flash",
        folder_id="folder-1",
        timeout_seconds=30.0,
        http_client=transport,
    )


def test_yandex_maps_roles_parameters_model_uri_and_usage() -> None:
    transport = FakeYandexTransport(
        json.dumps(
            {
                "model": "gpt://folder-1/deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Привет."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 111, "completion_tokens": 7},
            }
        ).encode()
    )

    result = asyncio.run(adapter(transport).generate(provider_request()))

    assert transport.calls == [
        (
            "/chat/completions",
            {
                "model": "gpt://folder-1/deepseek-v4-flash",
                "messages": [
                    {"role": "system", "content": "trusted policy"},
                    {"role": "system", "content": "trusted character data"},
                    {"role": "user", "content": "untrusted user text"},
                ],
                "stream": False,
                "temperature": 0.4,
                "max_tokens": 321,
            },
            30.0,
            1_000_000,
        )
    ]
    assert result.text == "Привет."
    assert result.provider == "yandex_ai_studio"
    assert result.model == "gpt://folder-1/deepseek-v4-flash"
    assert result.finish_status == "stop"
    assert result.usage is not None
    assert result.usage.input_tokens == 111
    assert result.usage.output_tokens == 7


def test_yandex_accepts_an_explicit_model_uri_and_hides_key_from_repr() -> None:
    transport = FakeYandexTransport()
    configured = YandexAIStudioConversationAdapter(
        base_url="https://ai.api.cloud.yandex.net/v1",
        api_key="private-test-key",
        model="gpt://folder-2/yandexgpt/latest",
        folder_id=None,
        timeout_seconds=30.0,
        http_client=transport,
    )

    assert configured.model == "gpt://folder-2/yandexgpt/latest"
    assert "private-test-key" not in repr(configured)


def test_yandex_maps_explicit_reasoning_effort_without_requesting_reasoning_content() -> None:
    transport = FakeYandexTransport(
        json.dumps(
            {
                "model": "gpt://folder-1/deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Короткий ответ."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            }
        ).encode()
    )
    configured = YandexAIStudioConversationAdapter(
        base_url="https://ai.api.cloud.yandex.net/v1",
        api_key="private-test-key",
        model="deepseek-v4-flash",
        folder_id="folder-1",
        timeout_seconds=30.0,
        reasoning_effort="low",
        http_client=transport,
    )

    result = asyncio.run(configured.generate(provider_request()))

    assert result.text == "Короткий ответ."
    payload = transport.calls[0][1]
    assert payload["reasoning_effort"] == "low"
    assert "reasoning" not in payload


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (YandexAIStudioTransportError("timeout"), ProviderUnavailable),
        (YandexAIStudioHttpStatusError(401), GenerationFailed),
        (YandexAIStudioHttpStatusError(429), ProviderUnavailable),
        (YandexAIStudioHttpStatusError(503), ProviderUnavailable),
    ],
)
def test_yandex_maps_transport_and_http_failures(
    error: Exception,
    expected: type[Exception],
) -> None:
    with pytest.raises(expected):
        asyncio.run(adapter(FakeYandexTransport(error=error)).generate(provider_request()))


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"[]",
        json.dumps({"model": "m", "choices": []}).encode(),
        json.dumps(
            {
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": ""},
                        "finish_reason": "stop",
                    }
                ],
            }
        ).encode(),
    ],
)
def test_yandex_rejects_malformed_results(body: bytes) -> None:
    with pytest.raises(InvalidProviderResponse):
        asyncio.run(adapter(FakeYandexTransport(body)).generate(provider_request()))


@pytest.mark.parametrize(
    ("model", "folder_id"),
    [
        ("../model", "folder"),
        ("model", None),
        ("model name", "folder"),
        ("gpt://", None),
        ("gpt://folder", None),
        ("gpt://folder/model?secret=value", None),
    ],
)
def test_yandex_model_uri_rejects_ambiguous_identifiers(
    model: str,
    folder_id: str | None,
) -> None:
    with pytest.raises(ValueError, match=r"model identifier|folder_id|model URI"):
        yandex_model_uri(model, folder_id)


def test_yandex_response_byte_limit_is_enforced() -> None:
    with pytest.raises(InvalidProviderResponse, match="byte limit"):
        asyncio.run(adapter(FakeYandexTransport(b"x" * 1_000_001)).generate(provider_request()))
