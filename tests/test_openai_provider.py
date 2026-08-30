"""Daemon-free OpenAI Responses API conversation adapter contract tests."""

import asyncio
import json
import traceback

import pytest

from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderFailureReason,
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
            "service_tier": "default",
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
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                },
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
                "service_tier": "default",
                "prompt_cache_options": {"mode": "explicit"},
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
    assert result.usage.cached_input_tokens == 0
    assert result.usage.cache_write_input_tokens == 0
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
    assert result.usage.cached_input_tokens is None
    assert result.usage.cache_write_input_tokens is None


def test_openai_rejects_cache_token_details_above_total_input() -> None:
    raw = json.loads(response_body())
    raw["usage"]["input_tokens_details"] = {
        "cached_tokens": 80,
        "cache_write_tokens": 40,
    }

    with pytest.raises(InvalidProviderResponse, match="usage metadata") as failure:
        asyncio.run(
            adapter(FakeOpenAITransport(json.dumps(raw).encode())).generate(provider_request())
        )

    assert failure.value.reason is ConversationProviderFailureReason.USAGE_METADATA_INVALID


def test_openai_parse_failure_traceback_never_contains_private_provider_body() -> None:
    private_marker = "PRIVATE_PROVIDER_SENTINEL_7f4d"
    raw = json.loads(response_body())
    raw["usage"]["input_tokens"] = {"private": private_marker}

    with pytest.raises(InvalidProviderResponse) as failure:
        asyncio.run(
            adapter(FakeOpenAITransport(json.dumps(raw).encode())).generate(provider_request())
        )

    rendered_traceback = "".join(traceback.format_exception(failure.value))
    assert private_marker not in rendered_traceback


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
    assert failure.value.reason is ConversationProviderFailureReason.USAGE_METADATA_INVALID


def test_openai_rejects_visible_output_above_application_limit() -> None:
    body = response_body(text="private oversized output", output_tokens=400, reasoning_tokens=50)

    with pytest.raises(InvalidProviderResponse, match="visible output exceeded") as failure:
        asyncio.run(adapter(FakeOpenAITransport(body)).generate(provider_request()))

    assert failure.value.metrics is not None
    assert failure.value.metrics.reasoning_output_tokens == 50
    assert failure.value.metrics.visible_output_tokens == 350
    assert failure.value.reason is ConversationProviderFailureReason.VISIBLE_OUTPUT_LIMIT_EXCEEDED
    assert "private oversized output" not in str(failure.value)


def test_openai_rejects_reasoning_count_above_total_output_count() -> None:
    body = response_body(output_tokens=17, reasoning_tokens=18)

    with pytest.raises(InvalidProviderResponse, match="usage metadata") as failure:
        asyncio.run(adapter(FakeOpenAITransport(body)).generate(provider_request()))

    assert failure.value.reason is ConversationProviderFailureReason.USAGE_METADATA_INVALID


def test_openai_rejects_incomplete_text_instead_of_committing_partial_reply() -> None:
    raw = json.loads(response_body(status="incomplete", text="private partial text"))
    raw["incomplete_details"] = {"reason": "max_output_tokens"}
    transport = FakeOpenAITransport(json.dumps(raw).encode())

    with pytest.raises(GenerationFailed, match="status incomplete") as failure:
        asyncio.run(adapter(transport).generate(provider_request()))

    assert len(transport.calls) == 1
    assert failure.value.reason is ConversationProviderFailureReason.OUTPUT_TOKEN_LIMIT
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
    assert failure.value.reason is ConversationProviderFailureReason.INCOMPLETE_UNKNOWN


@pytest.mark.parametrize(
    ("error", "expected", "reason"),
    [
        (
            OpenAITransportError("timeout"),
            ProviderUnavailable,
            ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
        ),
        (
            OpenAIHttpStatusError(401),
            GenerationFailed,
            ConversationProviderFailureReason.CREDENTIALS_REJECTED,
        ),
        (
            OpenAIHttpStatusError(403),
            GenerationFailed,
            ConversationProviderFailureReason.CREDENTIALS_REJECTED,
        ),
        (
            OpenAIHttpStatusError(404),
            GenerationFailed,
            ConversationProviderFailureReason.RESOURCE_NOT_FOUND,
        ),
        (
            OpenAIHttpStatusError(429),
            ProviderUnavailable,
            ConversationProviderFailureReason.RATE_OR_QUOTA_LIMITED,
        ),
        (
            OpenAIHttpStatusError(500),
            ProviderUnavailable,
            ConversationProviderFailureReason.TEMPORARILY_UNAVAILABLE,
        ),
        (
            OpenAIHttpStatusError(502),
            ProviderUnavailable,
            ConversationProviderFailureReason.TEMPORARILY_UNAVAILABLE,
        ),
        (
            OpenAIHttpStatusError(503),
            ProviderUnavailable,
            ConversationProviderFailureReason.TEMPORARILY_UNAVAILABLE,
        ),
    ],
)
def test_openai_maps_transport_and_http_failures(
    error: Exception,
    expected: type[Exception],
    reason: ConversationProviderFailureReason,
) -> None:
    with pytest.raises(expected) as failure:
        asyncio.run(adapter(FakeOpenAITransport(error=error)).generate(provider_request()))

    assert isinstance(failure.value, (ProviderUnavailable, GenerationFailed))
    assert failure.value.reason is reason


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
    with pytest.raises(InvalidProviderResponse) as failure:
        asyncio.run(adapter(FakeOpenAITransport(body)).generate(provider_request()))

    assert failure.value.reason in {
        ConversationProviderFailureReason.MISSING_ASSISTANT_TEXT,
        ConversationProviderFailureReason.RESPONSE_MALFORMED,
    }


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("failed", ConversationProviderFailureReason.GENERATION_FAILED),
        ("cancelled", ConversationProviderFailureReason.GENERATION_CANCELLED),
        ("queued", ConversationProviderFailureReason.GENERATION_FAILED),
        ("in_progress", ConversationProviderFailureReason.GENERATION_FAILED),
    ],
)
def test_openai_rejects_non_completed_response_status(
    status: str,
    reason: ConversationProviderFailureReason,
) -> None:
    with pytest.raises(GenerationFailed, match=f"status {status}") as failure:
        asyncio.run(
            adapter(FakeOpenAITransport(response_body(status=status))).generate(provider_request())
        )

    assert failure.value.reason is reason


def test_openai_maps_refusal_without_exposing_refusal_body() -> None:
    raw = json.loads(response_body())
    raw["output"][1]["content"] = [{"type": "refusal", "refusal": "private refusal body"}]

    with pytest.raises(GenerationFailed, match="refused") as failure:
        asyncio.run(
            adapter(FakeOpenAITransport(json.dumps(raw).encode())).generate(provider_request())
        )

    assert "private refusal body" not in str(failure.value)
    assert failure.value.reason is ConversationProviderFailureReason.RESPONSE_REFUSED


def test_openai_fails_closed_when_malformed_message_precedes_valid_text() -> None:
    raw = json.loads(response_body())
    raw["output"].insert(
        1,
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "unknown_private_content", "value": "secret"}],
        },
    )

    with pytest.raises(InvalidProviderResponse) as failure:
        asyncio.run(
            adapter(FakeOpenAITransport(json.dumps(raw).encode())).generate(provider_request())
        )

    assert failure.value.reason is ConversationProviderFailureReason.RESPONSE_MALFORMED
    assert "secret" not in str(failure.value)


def test_openai_adapter_hides_key_from_repr_and_enforces_byte_limit() -> None:
    configured = adapter(FakeOpenAITransport(b"x" * 1_000_001))

    assert "private-test-key" not in repr(configured)
    with pytest.raises(InvalidProviderResponse, match="byte limit") as failure:
        asyncio.run(configured.generate(provider_request()))

    assert failure.value.reason is ConversationProviderFailureReason.RESPONSE_TOO_LARGE


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
