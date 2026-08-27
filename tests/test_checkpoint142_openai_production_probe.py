"""Offline safety tests for the one-call OpenAI production probe."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
    GenerationFailed,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from tests.checkpoint142_openai_production_probe import (
    EVALUATION_BEHAVIOR_POLICY,
    OneCallConversationProvider,
    OneCallLedger,
    ProviderCallLimitExceeded,
)
from tests.stage81_real_eval import ProviderAttempt, _attempt_output_at_application_limit


def _request(trace_id: str = "trace-openai-probe") -> ConversationProviderRequest:
    return ConversationProviderRequest(
        schema_version=1,
        context_schema_version=16,
        messages=(ConversationMessage(ConversationMessageRole.USER, "public fixture"),),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.2,
            max_output_tokens=48,
        ),
        trace_id=trace_id,
    )


def test_one_call_probe_is_pinned_to_accepted_policy_v10() -> None:
    assert EVALUATION_BEHAVIOR_POLICY.policy_id == "satori.conversation.behavior.v10"
    assert EVALUATION_BEHAVIOR_POLICY.schema_version == 10


@dataclass(slots=True)
class _FixedProvider:
    error: BaseException | None = None
    call_count: int = 0

    async def generate(
        self,
        _request: ConversationProviderRequest,
        /,
    ) -> ConversationProviderResponse:
        self.call_count += 1
        if self.error is not None:
            raise self.error
        return ConversationProviderResponse(
            text="Ну наконец-то.",  # noqa: RUF001 - intentional Russian fixture
            provider="openai",
            model="gpt-5.6-terra",
            finish_status="completed",
            usage=ConversationUsage(input_tokens=100, output_tokens=30),
            metrics=ProviderExecutionMetrics(
                requested_output_token_limit=48,
                provider_output_token_limit=1072,
                reasoning_output_tokens=10,
                visible_output_tokens=20,
            ),
        )


def test_one_call_probe_records_safe_budget_metadata_and_blocks_retry() -> None:
    delegate = _FixedProvider()
    ledger = OneCallLedger()
    provider = OneCallConversationProvider(delegate, ledger)

    async def exercise() -> None:
        await provider.generate(_request())
        with pytest.raises(ProviderCallLimitExceeded, match="already consumed"):
            await provider.generate(_request("trace-retry"))

    asyncio.run(exercise())

    assert delegate.call_count == 1
    assert ledger.snapshot() == {
        "maximum_provider_calls": 1,
        "provider_call_count": 1,
        "within_call_limit": True,
        "calls": [
            {
                "call_number": 1,
                "status": "succeeded",
                "message_count": 1,
                "request_content_chars": 14,
                "temperature": 0.2,
                "requested_visible_output_token_limit": 48,
                "provider": "openai",
                "model": "gpt-5.6-terra",
                "finish_status": "completed",
                "input_tokens": 100,
                "total_output_tokens": 30,
                "provider_metrics": {
                    **{
                        key: None
                        for key in (
                            "provider_total_ms",
                            "provider_load_ms",
                            "provider_prompt_eval_ms",
                            "provider_eval_ms",
                            "provider_prompt_tokens",
                            "provider_output_tokens",
                            "provider_prompt_tokens_per_second",
                            "provider_output_tokens_per_second",
                            "client_request_build_ms",
                            "client_http_roundtrip_ms",
                            "client_response_parse_ms",
                        )
                    },
                    "requested_output_token_limit": 48,
                    "provider_output_token_limit": 1072,
                    "reasoning_output_tokens": 10,
                    "visible_output_tokens": 20,
                },
            }
        ],
    }


def test_one_call_probe_preserves_safe_metrics_on_provider_failure() -> None:
    delegate = _FixedProvider(
        error=GenerationFailed(
            "openai",
            "gpt-5.6-terra",
            "safe failure",
            metrics=ProviderExecutionMetrics(
                requested_output_token_limit=48,
                provider_output_token_limit=1072,
                reasoning_output_tokens=1024,
                visible_output_tokens=0,
            ),
        )
    )
    ledger = OneCallLedger()
    provider = OneCallConversationProvider(delegate, ledger)

    with pytest.raises(GenerationFailed):
        asyncio.run(provider.generate(_request()))

    call = ledger.snapshot()["calls"][0]
    assert call["status"] == "failed"
    assert call["error_type"] == "GenerationFailed"
    assert call["provider_metrics"]["provider_output_token_limit"] == 1072
    assert "safe failure" not in str(call)


def test_openai_total_output_does_not_fake_visible_limit_exhaustion() -> None:
    attempt = ProviderAttempt(
        wall_ms=5000.0,
        request_schema_version=1,
        context_schema_version=16,
        message_count=8,
        message_role_counts={"system": 1, "developer": 6, "user": 1},
        request_content_chars=6074,
        temperature=0.0,
        max_output_tokens=48,
        input_tokens=1487,
        output_tokens=105,
        provider_metrics={"visible_output_tokens": 47},
        finish_status="completed",
        succeeded=True,
        error_type=None,
    )

    assert _attempt_output_at_application_limit(attempt) is False


def test_provider_without_visible_breakdown_keeps_total_output_limit_check() -> None:
    attempt = ProviderAttempt(
        wall_ms=1000.0,
        request_schema_version=1,
        context_schema_version=16,
        message_count=2,
        message_role_counts={"system": 1, "user": 1},
        request_content_chars=100,
        temperature=0.0,
        max_output_tokens=48,
        input_tokens=100,
        output_tokens=48,
        provider_metrics=None,
        finish_status="length",
        succeeded=True,
        error_type=None,
    )

    assert _attempt_output_at_application_limit(attempt) is True
