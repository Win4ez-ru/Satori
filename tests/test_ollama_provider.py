"""Ollama HTTP adapter contract tests without requiring a running provider."""

import asyncio
import json
from email.message import Message
from typing import Self
from urllib.error import HTTPError, URLError
from urllib.request import Request

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
from satori.infrastructure.providers.ollama import OllamaConversationAdapter


class FakeHttpResponse:
    """Small context-managed urllib response fixture."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


def provider_request() -> ConversationProviderRequest:
    """Return one layered request with deterministic generation settings."""

    return ConversationProviderRequest(
        schema_version=1,
        trace_id="trace-ollama",
        context_schema_version=1,
        messages=(
            ConversationMessage(ConversationMessageRole.SYSTEM, "trusted policy"),
            ConversationMessage(ConversationMessageRole.DEVELOPER, "trusted character data"),
            ConversationMessage(ConversationMessageRole.USER, "untrusted user text"),
        ),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.6,
            max_output_tokens=321,
        ),
    )


def adapter() -> OllamaConversationAdapter:
    return OllamaConversationAdapter(
        base_url="http://127.0.0.1:11434",
        model="qwen3:4b-instruct",
        timeout_seconds=30.0,
    )


def test_ollama_maps_roles_disables_streaming_and_validates_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vendor fields stay adapter-local and developer context remains trusted/system."""

    captured: dict[str, object] = {}
    response_body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": "Привет."},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 111,
            "eval_count": 7,
        }
    ).encode()

    def fake_urlopen(request: Request, *, timeout: float) -> FakeHttpResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeHttpResponse(response_body)

    monkeypatch.setattr("satori.infrastructure.providers.ollama.urlopen", fake_urlopen)

    result = asyncio.run(adapter().generate(provider_request()))
    http_request = captured["request"]
    assert isinstance(http_request, Request)
    assert isinstance(http_request.data, bytes)
    payload = json.loads(http_request.data.decode())
    assert payload == {
        "model": "qwen3:4b-instruct",
        "messages": [
            {"role": "system", "content": "trusted policy"},
            {"role": "system", "content": "trusted character data"},
            {"role": "user", "content": "untrusted user text"},
        ],
        "stream": False,
        "think": False,
        "keep_alive": "5m",
        "options": {"temperature": 0.6, "num_predict": 321},
    }
    assert captured["timeout"] == 30.0
    assert result.text == "Привет."
    assert result.provider == "ollama"
    assert result.usage is not None
    assert result.usage.input_tokens == 111
    assert result.usage.output_tokens == 7


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (URLError("connection refused"), ProviderUnavailable),
        (
            HTTPError("http://localhost", 404, "not found", hdrs=Message(), fp=None),
            GenerationFailed,
        ),
        (
            HTTPError("http://localhost", 503, "unavailable", hdrs=Message(), fp=None),
            ProviderUnavailable,
        ),
    ],
)
def test_ollama_wraps_transport_and_http_failures(
    monkeypatch: pytest.MonkeyPatch,
    raised: Exception,
    expected: type[Exception],
) -> None:
    """No urllib exception crosses the provider port."""

    def failing_urlopen(_request: Request, *, timeout: float) -> FakeHttpResponse:
        assert timeout == 30.0
        raise raised

    monkeypatch.setattr("satori.infrastructure.providers.ollama.urlopen", failing_urlopen)

    with pytest.raises(expected):
        asyncio.run(adapter().generate(provider_request()))


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"[]",
        json.dumps(
            {
                "model": "qwen3:4b-instruct",
                "message": {"role": "assistant", "content": "partial"},
                "done": False,
            }
        ).encode(),
    ],
)
def test_ollama_rejects_malformed_or_incomplete_results(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    """Malformed vendor results become one typed provider-neutral error."""

    def fake_urlopen(_request: Request, *, timeout: float) -> FakeHttpResponse:
        assert timeout == 30.0
        return FakeHttpResponse(body)

    monkeypatch.setattr("satori.infrastructure.providers.ollama.urlopen", fake_urlopen)

    with pytest.raises(InvalidProviderResponse):
        asyncio.run(adapter().generate(provider_request()))


def test_ollama_rejects_length_limited_partial_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token-limited fragment never crosses the provider boundary as a canonical reply."""

    response_body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": "partial private reply"},
            "done": True,
            "done_reason": "length",
            "prompt_eval_count": 111,
            "eval_count": 321,
        }
    ).encode()

    def fake_urlopen(_request: Request, *, timeout: float) -> FakeHttpResponse:
        assert timeout == 30.0
        return FakeHttpResponse(response_body)

    monkeypatch.setattr("satori.infrastructure.providers.ollama.urlopen", fake_urlopen)

    with pytest.raises(GenerationFailed) as raised:
        asyncio.run(adapter().generate(provider_request()))

    assert "partial private reply" not in str(raised.value)
    assert raised.value.metrics is not None
    assert raised.value.metrics.eval_count == 321
