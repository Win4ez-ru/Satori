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
)
from tests.checkpoint142_production_eval import (
    BudgetedConversationProvider,
    ProviderBudgetExhausted,
    ProviderBudgetLedger,
    _compact_turn,
)


def _request(
    *,
    content_chars: int = 100,
    max_output_tokens: int = 20,
    trace_id: str = "trace-checkpoint142-budget",
) -> ConversationProviderRequest:
    return ConversationProviderRequest(
        schema_version=1,
        context_schema_version=1,
        messages=(ConversationMessage(ConversationMessageRole.USER, "x" * content_chars),),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.3,
            max_output_tokens=max_output_tokens,
        ),
        trace_id=trace_id,
    )


@dataclass(slots=True)
class _FixedProvider:
    input_tokens: int = 50
    output_tokens: int = 10
    call_count: int = 0

    async def generate(
        self, _request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        self.call_count += 1
        return ConversationProviderResponse(
            text="Хорошо.",
            provider="yandex_ai_studio",
            model="yandexgpt/latest",
            finish_status="stop",
            usage=ConversationUsage(self.input_tokens, self.output_tokens),
        )


def test_budgeted_provider_counts_actual_attempt_usage() -> None:
    delegate = _FixedProvider()
    ledger = ProviderBudgetLedger(
        maximum_calls=2,
        maximum_cost_rub=1.0,
        required_base_calls=2,
    )
    provider = BudgetedConversationProvider(delegate, ledger)

    async def exercise() -> None:
        await provider.generate(_request())
        await provider.generate(_request(trace_id="trace-checkpoint142-budget-2"))

    asyncio.run(exercise())

    snapshot = ledger.snapshot()
    assert delegate.call_count == 2
    assert snapshot["provider_call_count"] == 2
    assert snapshot["input_tokens"] == 100
    assert snapshot["output_tokens"] == 20
    assert snapshot["actual_usage_cost_rub"] == pytest.approx(0.048)


def test_budgeted_provider_blocks_before_call_limit() -> None:
    delegate = _FixedProvider()
    ledger = ProviderBudgetLedger(
        maximum_calls=1,
        maximum_cost_rub=1.0,
        required_base_calls=1,
    )
    provider = BudgetedConversationProvider(delegate, ledger)

    async def exercise() -> None:
        await provider.generate(_request())
        with pytest.raises(ProviderBudgetExhausted, match="call limit"):
            await provider.generate(_request())

    asyncio.run(exercise())

    assert delegate.call_count == 1
    assert ledger.provider_call_count == 1


def test_budgeted_provider_reserves_calls_for_remaining_base_turns() -> None:
    delegate = _FixedProvider()
    ledger = ProviderBudgetLedger(
        maximum_calls=6,
        maximum_cost_rub=1.0,
        required_base_calls=6,
    )
    provider = BudgetedConversationProvider(delegate, ledger)

    async def exercise() -> None:
        await provider.generate(_request())
        with pytest.raises(ProviderBudgetExhausted, match="reserved"):
            await provider.generate(_request())

    asyncio.run(exercise())

    assert delegate.call_count == 1
    assert ledger.provider_call_count == 1


def test_budgeted_provider_blocks_conservative_cost_before_network() -> None:
    delegate = _FixedProvider()
    ledger = ProviderBudgetLedger(maximum_calls=9, maximum_cost_rub=0.01)
    provider = BudgetedConversationProvider(delegate, ledger)

    async def exercise() -> None:
        with pytest.raises(ProviderBudgetExhausted, match="RUB budget"):
            await provider.generate(_request(content_chars=1000, max_output_tokens=80))

    asyncio.run(exercise())

    assert delegate.call_count == 0
    assert ledger.provider_call_count == 0


def test_compact_turn_redacts_folder_scoped_model_and_keeps_real_timings() -> None:
    compact = _compact_turn(
        {
            "turn": 1,
            "id": "public-fixture",
            "user_text": "Публичная реплика",
            "reply": "Публичный ответ",
            "generation": {
                "provider": "yandex_ai_studio",
                "model": "gpt://private-folder/yandexgpt/latest",
                "finish_status": "stop",
            },
            "provider_attempt_count": 1,
            "provider_attempts": [],
            "usage": {"input_tokens": 10, "output_tokens": 2},
            "timings_ms": {
                "conversation_generation_ms": 12.5,
                "response_regeneration_ms": 0.0,
                "committed_reply_ms": 20.0,
                "emotion_appraisal_ms": 5.0,
                "canonical_commit_ms": 1.0,
            },
            "manifest": {
                "policy_id": "satori.conversation.behavior.v18",
                "character_expression_plan_schema_version": 2,
                "character_expression_register": "wry_warmth",
                "character_owned_reaction": "guarded_approval",
                "character_semantic_move": "mark_hard_won_result",
                "character_relational_ease": "fresh",
                "relationship_expression_profile": "fresh_undeveloped_neutral",
                "affect_expression_profile": "calm_even",
                "recent_conversation_turn_count": 0,
                "retrieved_memory_count": 0,
                "regeneration_attempted": False,
                "response_regenerated": False,
                "regeneration_reason": None,
            },
        }
    )

    assert compact["model"] == "yandexgpt/latest"
    assert compact["timings_ms"] == {
        "conversation_generation": 12.5,
        "response_regeneration": 0.0,
        "committed_reply": 20.0,
        "emotion_appraisal": 5.0,
        "canonical_commit": 1.0,
    }
