"""Daemon-free OpenAI Responses API conversation adapter contract tests."""

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
from satori.infrastructure.providers.openai import OpenAIConversationAdapter
from satori.infrastructure.providers.openai_http import (
    OpenAIHttpStatusError,
    OpenAITransportError,
)


class FakeOpenAITransport:
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
        trace_id="trace-openai",
        context_schema_version=16,
        messages=(
            ConversationMessage(ConversationMessageRole.SYSTEM, "trusted policy"),
            ConversationMessage(ConversationMessageRole.DEVELOPER, "trusted character data"),
            ConversationMessage(ConversationMessageRole.USER, "untrusted user text"),
        ),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.2,
            max_output_tokens=321,
        ),
    )


def response_body(
    *,
    status: str = "completed",
    text: str = "Привет.",
    output_tokens: int = 17,
    reasoning_tokens: int = 5,
) -> bytes:
    return json.dumps(
        {
            "model": "gpt-5.6-terra-2026-08-01",
            "status": status,
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                },
            ],
            "usage": {
                "input_tokens": 111,
                "output_tokens": output_tokens,
                "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
                "total_tokens": 111 + output_tokens,
            },
        }
    ).encode()


def adapter(transport: FakeOpenAITransport) -> OpenAIConversationAdapter:
    return OpenAIConversationAdapter(
        base_url="https://api.openai.com/v1",
        api_key="private-test-key",
        model="gpt-5.6-terra",
        timeout_seconds=30.0,
        reasoning_effort="low",
        reasoning_token_allowance=1024,
        http_client=transport,
    )


def test_openai_maps_roles_parameters_privacy_reasoning_and_usage() -> None:
    transport = FakeOpenAITransport(response_body())

    result = asyncio.run(adapter(transport).generate(provider_request()))

    assert transport.calls == [
        (
            "/responses",
            {
                "model": "gpt-5.6-terra",
                "input": [
                    {"role": "system", "content": "trusted policy"},
                    {"role": "developer", "content": "trusted character data"},
                    {"role": "user", "content": "untrusted user text"},
                ],
                "max_output_tokens": 1345,
                "reasoning": {"effort": "low"},
                "store": False,
            },
            30.0,
            1_000_000,
        )
    ]
    assert result.text == "Привет."
    assert result.provider == "openai"
    assert result.model == "gpt-5.6-terra-2026-08-01"
    assert result.finish_status == "completed"
    assert result.usage is not None
    assert result.usage.input_tokens == 111
    assert result.usage.output_tokens == 17
    assert result.metrics is not None
    assert result.metrics.requested_output_token_limit == 321
    assert result.metrics.provider_output_token_limit == 1345
    assert result.metrics.reasoning_output_tokens == 5
    assert result.metrics.visible_output_tokens == 12


def test_openai_maps_temperature_only_without_reasoning() -> None:
    transport = FakeOpenAITransport(response_body())
    configured = OpenAIConversationAdapter(
        base_url="https://api.openai.com/v1",
        api_key="private-test-key",
        model="gpt-5.6-terra",
        timeout_seconds=30.0,
        reasoning_effort="none",
        reasoning_token_allowance=1024,
        http_client=transport,
    )

    asyncio.run(configured.generate(provider_request()))

    assert transport.calls[0][1]["temperature"] == 0.2
    assert transport.calls[0][1]["max_output_tokens"] == 321


def test_openai_preserves_partial_usage_without_inventing_missing_counts() -> None:
    raw = json.loads(response_body())
    raw["usage"] = {"input_tokens": 111}
    transport = FakeOpenAITransport(json.dumps(raw).encode())
    configured = OpenAIConversationAdapter(
        base_url="https://api.openai.com/v1",
        api_key="private-test-key",
        model="gpt-5.6-terra",
        timeout_seconds=30.0,
        reasoning_effort="none",
        reasoning_token_allowance=1024,
        http_client=transport,
    )

    result = asyncio.run(configured.generate(provider_request()))

    assert result.usage is not None
    assert result.usage.input_tokens == 111
    assert result.usage.output_tokens is None


def test_openai_reasoning_fails_closed_without_usage_breakdown() -> None:
    raw = json.loads(response_body())
    raw["usage"] = {"input_tokens": 111, "output_tokens": 17}

    with pytest.raises(InvalidProviderResponse, match="usage required") as failure:
        asyncio.run(
            adapter(FakeOpenAITransport(json.dumps(raw).encode())).generate(provider_request())
        )

    assert failure.value.metrics is not None
    assert failure.value.metrics.requested_output_token_limit == 321
    assert failure.value.metrics.provider_output_token_limit == 1345
    assert failure.value.metrics.visible_output_tokens is None


def test_openai_rejects_visible_output_above_application_limit() -> None:
    body = response_body(text="private oversized output", output_tokens=400, reasoning_tokens=50)

    with pytest.raises(InvalidProviderResponse, match="visible output exceeded") as failure:
        asyncio.run(adapter(FakeOpenAITransport(body)).generate(provider_request()))

    assert failure.value.metrics is not None
    assert failure.value.metrics.reasoning_output_tokens == 50
    assert failure.value.metrics.visible_output_tokens == 350
    assert "private oversized output" not in str(failure.value)


def test_openai_rejects_reasoning_count_above_total_output_count() -> None:
    body = response_body(output_tokens=17, reasoning_tokens=18)

    with pytest.raises(InvalidProviderResponse, match="malformed"):
        asyncio.run(adapter(FakeOpenAITransport(body)).generate(provider_request()))


def test_openai_rejects_incomplete_text_instead_of_committing_partial_reply() -> None:
    raw = json.loads(response_body(status="incomplete", text="private partial text"))
    raw["incomplete_details"] = {"reason": "max_output_tokens"}
    transport = FakeOpenAITransport(json.dumps(raw).encode())

    with pytest.raises(GenerationFailed, match="status incomplete") as failure:
        asyncio.run(adapter(transport).generate(provider_request()))

    assert len(transport.calls) == 1
    assert "reason=max_output_tokens" in str(failure.value)
    assert "private partial text" not in str(failure.value)
    assert failure.value.metrics is not None
    assert failure.value.metrics.requested_output_token_limit == 321
    assert failure.value.metrics.provider_output_token_limit == 1345
    assert failure.value.metrics.reasoning_output_tokens == 5
    assert failure.value.metrics.visible_output_tokens == 12


@pytest.mark.parametrize(
    "incomplete_details",
    [None, {"reason": "Private vendor detail!"}],
)
def test_openai_maps_unrecognized_incomplete_reason_to_safe_unknown(
    incomplete_details: dict[str, str] | None,
) -> None:
    raw = json.loads(response_body(status="incomplete"))
    if incomplete_details is not None:
        raw["incomplete_details"] = incomplete_details

    with pytest.raises(GenerationFailed, match="reason=unknown") as failure:
        asyncio.run(
            adapter(FakeOpenAITransport(json.dumps(raw).encode())).generate(provider_request())
        )

    assert "Private vendor detail!" not in str(failure.value)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OpenAITransportError("timeout"), ProviderUnavailable),
        (OpenAIHttpStatusError(401), GenerationFailed),
        (OpenAIHttpStatusError(403), GenerationFailed),
        (OpenAIHttpStatusError(429), ProviderUnavailable),
        (OpenAIHttpStatusError(500), ProviderUnavailable),
        (OpenAIHttpStatusError(502), ProviderUnavailable),
        (OpenAIHttpStatusError(503), ProviderUnavailable),
    ],
)
def test_openai_maps_transport_and_http_failures(
    error: Exception,
    expected: type[Exception],
) -> None:
    with pytest.raises(expected):
        asyncio.run(adapter(FakeOpenAITransport(error=error)).generate(provider_request()))


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"[]",
        json.dumps({"model": "m", "status": "completed", "output": []}).encode(),
        response_body(text=" "),
    ],
)
def test_openai_rejects_malformed_results(body: bytes) -> None:
    with pytest.raises(InvalidProviderResponse):
        asyncio.run(adapter(FakeOpenAITransport(body)).generate(provider_request()))


def test_openai_rejects_successful_failed_response() -> None:
    with pytest.raises(GenerationFailed, match="status failed"):
        asyncio.run(
            adapter(FakeOpenAITransport(response_body(status="failed"))).generate(
                provider_request()
            )
        )


def test_openai_maps_refusal_without_exposing_refusal_body() -> None:
    raw = json.loads(response_body())
    raw["output"][1]["content"] = [{"type": "refusal", "refusal": "private refusal body"}]

    with pytest.raises(GenerationFailed, match="refused") as failure:
        asyncio.run(
            adapter(FakeOpenAITransport(json.dumps(raw).encode())).generate(provider_request())
        )

    assert "private refusal body" not in str(failure.value)


def test_openai_adapter_hides_key_from_repr_and_enforces_byte_limit() -> None:
    configured = adapter(FakeOpenAITransport(b"x" * 1_000_001))

    assert "private-test-key" not in repr(configured)
    with pytest.raises(InvalidProviderResponse, match="byte limit"):
        asyncio.run(configured.generate(provider_request()))


@pytest.mark.parametrize("allowance", [-1, 4097, True])
def test_openai_adapter_rejects_invalid_reasoning_allowance(allowance: int) -> None:
    with pytest.raises(ValueError, match="reasoning_token_allowance"):
        OpenAIConversationAdapter(
            base_url="https://api.openai.com/v1",
            api_key="private-test-key",
            model="gpt-5.6-terra",
            timeout_seconds=30.0,
            reasoning_effort="low",
            reasoning_token_allowance=allowance,
            http_client=FakeOpenAITransport(),
        )
