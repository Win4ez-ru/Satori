"""Offline production-composition contracts for the OpenAI foreground wire."""

import asyncio
import json
from datetime import UTC, datetime

import pytest

from satori.application.cognition.contracts import (
    CognitionDialogueSignals,
    CognitionPipelineTrace,
)
from satori.application.cognition.use_cases import DeterministicCognitionPlanner
from satori.application.conversation.context import (
    CharacterContextComposer,
    ConversationRequestBuilder,
)
from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
    RuntimeCharacterContext,
)
from satori.application.conversation.policy import (
    BEHAVIOR_POLICY_V19,
    BEHAVIOR_POLICY_V20,
    BEHAVIOR_POLICY_V22,
    BEHAVIOR_POLICY_V23,
)
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.core.conversation import (
    ConversationMessageRole,
    ConversationProviderRequest,
)
from satori.domain.initial_self import activate_from_seed
from satori.infrastructure.providers.openai import OpenAIConversationAdapter
from satori.infrastructure.seeds.loader import JsonSeedLoader

_REALIZATION_BLOCK = "Финальная реализация характера Сатори для этой реплики"
_V22_REALIZATION_BLOCK = "Финальный response-act контракт Сатори для этой реплики"
_V23_REALIZATION_BLOCK = "Финальный компактный речевой контракт Сатори для этой реплики"
_ACHIEVEMENT = "Привет. Я сегодня наконец закончил сложную часть проекта"
_DEPLETION = "Знаешь, я почему-то почти не рад этому. Скорее просто выжат"


class _CapturingTransport:
    """Capture one stateless Responses request without opening a network connection."""

    def __init__(self) -> None:
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
        return json.dumps(
            {
                "model": "gpt-5.6-terra-test",
                "status": "completed",
                "service_tier": "default",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Хорошо.",
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {
                        "cached_tokens": 0,
                        "cache_write_tokens": 0,
                    },
                    "output_tokens": 4,
                    "output_tokens_details": {"reasoning_tokens": 2},
                    "total_tokens": 104,
                },
            }
        ).encode()


def _builder() -> tuple[ConversationRequestBuilder, RuntimeCharacterContext]:
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="openai-v19-wire",
        activation_time=datetime(2026, 8, 27, tzinfo=UTC),
    )
    context = CharacterContextComposer("openai", "gpt-5.6-terra").compose(
        snapshot,
        relationship_state_available=True,
        recent_conversation_available=True,
    )
    return (
        ConversationRequestBuilder(BEHAVIOR_POLICY_V19, 12_000, 0.3, 768),
        context,
    )


def _v20_builder() -> tuple[ConversationRequestBuilder, RuntimeCharacterContext]:
    _, context = _builder()
    return (
        ConversationRequestBuilder(BEHAVIOR_POLICY_V20, 12_000, 0.3, 768),
        context,
    )


def _v22_builder() -> tuple[ConversationRequestBuilder, RuntimeCharacterContext]:
    _, context = _builder()
    return (
        ConversationRequestBuilder(BEHAVIOR_POLICY_V22, 12_000, 0.3, 768),
        context,
    )


def _v23_builder() -> tuple[ConversationRequestBuilder, RuntimeCharacterContext]:
    _, context = _builder()
    return (
        ConversationRequestBuilder(BEHAVIOR_POLICY_V23, 12_000, 0.3, 768),
        context,
    )


def _cognition(user_text: str, *, suffix: str) -> CognitionPipelineTrace:
    planner = DeterministicCognitionPlanner()
    interaction_id = f"openai-v19-{suffix}"
    intake = planner.prepare_intake(
        user_text=user_text,
        user_message_id=f"openai-v19-message-{suffix}",
        interaction_id=interaction_id,
        dialogue=CognitionDialogueSignals(),
    )
    return planner.complete(
        intake,
        interaction_id=interaction_id,
        available_evidence_ids=(),
        prepared_affect=None,
    )


def _fresh_relationship() -> RelationshipExpressionContext:
    return RelationshipExpressionContext(
        schema_version=1,
        state_version=1,
        maturity="low",
        familiarity="low",
        trust="uncertain",
        comfort="uncertain",
        closeness="low",
        intellectual_respect="uncertain",
        affection="low",
    )


def _recent_completion() -> RecentConversationContext:
    turn = RecentConversationTurn(
        interaction_id="openai-v19-previous-interaction",
        user_message_id="openai-v19-previous-user",
        user_content=_ACHIEVEMENT,
        assistant_message_id="openai-v19-previous-assistant",
        assistant_content="Эта часть всё-таки закончена.",
    )
    return RecentConversationContext(
        schema_version=1,
        turns=(turn,),
        content_chars=len(turn.user_content) + len(turn.assistant_content),
        excluded_turn_count=0,
    )


def _production_request(*, depleted: bool) -> ConversationProviderRequest:
    builder, context = _builder()
    user_text = _DEPLETION if depleted else _ACHIEVEMENT
    request, _ = builder.build(
        context,
        user_text=user_text,
        trace_id="openai-v19-depletion" if depleted else "openai-v19-achievement",
        relationship_context=_fresh_relationship(),
        recent_context=_recent_completion() if depleted else None,
        cognition_trace=_cognition(
            user_text,
            suffix="depletion" if depleted else "achievement",
        ),
    )
    return request


def _v20_depletion_request() -> ConversationProviderRequest:
    builder, context = _v20_builder()
    request, manifest = builder.build(
        context,
        user_text=_DEPLETION,
        trace_id="openai-v20-depletion",
        relationship_context=_fresh_relationship(),
        recent_context=_recent_completion(),
        cognition_trace=_cognition(_DEPLETION, suffix="v20-depletion"),
    )
    assert manifest.character_expression_plan_schema_version == 3
    assert manifest.character_contribution_mode == "grounded_direction"
    assert manifest.character_motivational_posture == "supportive_push"
    assert manifest.character_pressure_level == "gentle"
    return request


def _v22_request(*, depleted: bool) -> ConversationProviderRequest:
    builder, context = _v22_builder()
    user_text = _DEPLETION if depleted else _ACHIEVEMENT
    request, manifest = builder.build(
        context,
        user_text=user_text,
        trace_id="openai-v22-depletion" if depleted else "openai-v22-achievement",
        relationship_context=_fresh_relationship(),
        recent_context=_recent_completion() if depleted else None,
        cognition_trace=_cognition(
            user_text,
            suffix="v22-depletion" if depleted else "v22-achievement",
        ),
    )
    assert manifest.policy_id == "satori.conversation.behavior.v22"
    assert manifest.character_expression_plan_schema_version == 4
    assert manifest.character_contribution_mode == (
        "emotional_reaction" if depleted else "owned_evaluation"
    )
    assert manifest.character_acknowledgement_mode == ("omit" if depleted else "implicit")
    return request


def _v23_request(*, depleted: bool) -> ConversationProviderRequest:
    builder, context = _v23_builder()
    user_text = _DEPLETION if depleted else _ACHIEVEMENT
    request, manifest = builder.build(
        context,
        user_text=user_text,
        trace_id="openai-v23-depletion" if depleted else "openai-v23-achievement",
        relationship_context=_fresh_relationship(),
        recent_context=_recent_completion() if depleted else None,
        cognition_trace=_cognition(
            user_text,
            suffix="v23-depletion" if depleted else "v23-achievement",
        ),
    )
    assert manifest.policy_id == "satori.conversation.behavior.v23"
    assert manifest.character_expression_plan_schema_version == 5
    assert manifest.character_contribution_mode == (
        "grounded_direction" if depleted else "owned_evaluation"
    )
    assert manifest.character_motivational_posture == ("supportive_push" if depleted else "none")
    assert manifest.character_acknowledgement_mode == ("omit" if depleted else "implicit")
    return request


@pytest.mark.parametrize(
    ("depleted", "expected_roles", "visible_limit"),
    [
        (
            False,
            (
                ConversationMessageRole.SYSTEM,
                ConversationMessageRole.DEVELOPER,
                ConversationMessageRole.DEVELOPER,
                ConversationMessageRole.DEVELOPER,
                ConversationMessageRole.DEVELOPER,
                ConversationMessageRole.USER,
            ),
            80,
        ),
        (
            True,
            (
                ConversationMessageRole.SYSTEM,
                ConversationMessageRole.DEVELOPER,
                ConversationMessageRole.DEVELOPER,
                ConversationMessageRole.USER,
                ConversationMessageRole.ASSISTANT,
                ConversationMessageRole.DEVELOPER,
                ConversationMessageRole.DEVELOPER,
                ConversationMessageRole.USER,
            ),
            96,
        ),
    ],
)
def test_v19_production_composition_reaches_openai_wire_without_reordering(
    depleted: bool,
    expected_roles: tuple[ConversationMessageRole, ...],
    visible_limit: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = _production_request(depleted=depleted)
    final_developer = request.messages[-2]

    assert tuple(message.role for message in request.messages) == expected_roles
    assert final_developer.role is ConversationMessageRole.DEVELOPER
    assert final_developer.content.count(_REALIZATION_BLOCK) == 1
    assert final_developer.content.index("Обязательный доверенный контракт.") < (
        final_developer.content.index("Строго выполни")
    )
    assert final_developer.content.index("Строго выполни") < final_developer.content.index(
        _REALIZATION_BLOCK
    )
    assert final_developer.content.index("character-realization блок") < (
        final_developer.content.index(_REALIZATION_BLOCK)
    )
    assert request.parameters.max_output_tokens == visible_limit

    transport = _CapturingTransport()
    provider = OpenAIConversationAdapter(
        base_url="https://api.openai.com/v1",
        api_key="offline-test-key",
        model="gpt-5.6-terra",
        timeout_seconds=30.0,
        reasoning_effort="low",
        reasoning_token_allowance=1024,
        http_client=transport,
    )

    asyncio.run(provider.generate(request))

    assert len(transport.calls) == 1
    path, payload, timeout_seconds, max_response_bytes = transport.calls[0]
    assert path == "/responses"
    assert payload["input"] == [
        {"role": message.role.value, "content": message.content} for message in request.messages
    ]
    assert payload["max_output_tokens"] == visible_limit + 1024
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["service_tier"] == "default"
    assert payload["prompt_cache_options"] == {"mode": "explicit"}
    assert payload["store"] is False
    assert "temperature" not in payload
    assert timeout_seconds == 30.0
    assert max_response_bytes == 1_000_000
    assert _ACHIEVEMENT not in caplog.text
    assert _DEPLETION not in caplog.text
    assert _REALIZATION_BLOCK not in caplog.text
    assert "offline-test-key" not in caplog.text


def test_v20_supportive_push_reaches_stateless_openai_wire_without_private_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = _v20_depletion_request()
    final_developer = request.messages[-2]

    assert final_developer.content.count(_REALIZATION_BLOCK) == 1
    assert "выбранного собственного вклада" in final_developer.content
    assert "короткий шаг восстановления" in final_developer.content
    assert "grounded_direction" not in final_developer.content
    assert "supportive_push" not in final_developer.content
    assert "pressure_level" not in final_developer.content

    transport = _CapturingTransport()
    provider = OpenAIConversationAdapter(
        base_url="https://api.openai.com/v1",
        api_key="offline-v20-test-key",
        model="gpt-5.6-terra",
        timeout_seconds=30.0,
        reasoning_effort="low",
        reasoning_token_allowance=1024,
        http_client=transport,
    )

    asyncio.run(provider.generate(request))

    assert len(transport.calls) == 1
    path, payload, _, _ = transport.calls[0]
    assert path == "/responses"
    assert payload["input"] == [
        {"role": message.role.value, "content": message.content} for message in request.messages
    ]
    assert payload["max_output_tokens"] == request.parameters.max_output_tokens + 1024
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["service_tier"] == "default"
    assert payload["prompt_cache_options"] == {"mode": "explicit"}
    assert payload["store"] is False
    assert "temperature" not in payload
    assert _ACHIEVEMENT not in caplog.text
    assert _DEPLETION not in caplog.text
    assert _REALIZATION_BLOCK not in caplog.text
    assert "offline-v20-test-key" not in caplog.text


@pytest.mark.parametrize("depleted", [False, True])
def test_v22_response_act_reaches_stateless_openai_wire_without_failed_anchor_or_logging(
    depleted: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = _v22_request(depleted=depleted)
    final_developer = request.messages[-2]

    assert final_developer.content.count(_V22_REALIZATION_BLOCK) == 1
    assert "Речевой акт:" in final_developer.content
    assert "Evidence-граница:" in final_developer.content
    assert "Фактическая граница:" not in final_developer.content
    assert "цена результата" not in final_developer.content.casefold()
    assert "отсутствие радости" not in final_developer.content.casefold()
    assert "выжатость" not in final_developer.content.casefold()

    transport = _CapturingTransport()
    provider = OpenAIConversationAdapter(
        base_url="https://api.openai.com/v1",
        api_key="offline-v22-test-key",
        model="gpt-5.6-terra",
        timeout_seconds=30.0,
        reasoning_effort="low",
        reasoning_token_allowance=1024,
        http_client=transport,
    )

    asyncio.run(provider.generate(request))

    assert len(transport.calls) == 1
    path, payload, _, _ = transport.calls[0]
    assert path == "/responses"
    assert payload["input"] == [
        {"role": message.role.value, "content": message.content} for message in request.messages
    ]
    assert payload["max_output_tokens"] == request.parameters.max_output_tokens + 1024
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["service_tier"] == "default"
    assert payload["prompt_cache_options"] == {"mode": "explicit"}
    assert payload["store"] is False
    assert "temperature" not in payload
    assert _ACHIEVEMENT not in caplog.text
    assert _DEPLETION not in caplog.text
    assert _V22_REALIZATION_BLOCK not in caplog.text
    assert "offline-v22-test-key" not in caplog.text


@pytest.mark.parametrize("depleted", [False, True])
def test_v23_compact_contract_reaches_medium_reasoning_openai_wire_without_private_logging(
    depleted: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = _v23_request(depleted=depleted)
    final_developer = request.messages[-2]

    assert final_developer.content.count(_V23_REALIZATION_BLOCK) == 1
    assert final_developer.content.count("\n- Действие:") == 1
    assert final_developer.content.count("\n- Опора:") == 1
    assert final_developer.content.count("\n- Голос:") == 1
    assert final_developer.content.count("\n- Стоп:") == 1
    assert "Речевой акт:" not in final_developer.content
    assert "Фактическая граница:" not in final_developer.content
    assert "Смысловой ход:" not in final_developer.content

    transport = _CapturingTransport()
    provider = OpenAIConversationAdapter(
        base_url="https://api.openai.com/v1",
        api_key="offline-v23-test-key",
        model="gpt-5.6-terra",
        timeout_seconds=30.0,
        reasoning_effort="medium",
        reasoning_token_allowance=1024,
        http_client=transport,
    )

    asyncio.run(provider.generate(request))

    assert len(transport.calls) == 1
    path, payload, _, _ = transport.calls[0]
    assert path == "/responses"
    assert payload["input"] == [
        {"role": message.role.value, "content": message.content} for message in request.messages
    ]
    assert payload["max_output_tokens"] == request.parameters.max_output_tokens + 1024
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["service_tier"] == "default"
    assert payload["prompt_cache_options"] == {"mode": "explicit"}
    assert payload["store"] is False
    assert "temperature" not in payload
    assert _ACHIEVEMENT not in caplog.text
    assert _DEPLETION not in caplog.text
    assert _V23_REALIZATION_BLOCK not in caplog.text
    assert "offline-v23-test-key" not in caplog.text
