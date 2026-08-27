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
from satori.application.conversation.policy import BEHAVIOR_POLICY_V19
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.core.conversation import (
    ConversationMessageRole,
    ConversationProviderRequest,
)
from satori.domain.initial_self import activate_from_seed
from satori.infrastructure.providers.openai import OpenAIConversationAdapter
from satori.infrastructure.seeds.loader import JsonSeedLoader

_REALIZATION_BLOCK = "Финальная реализация характера Сатори для этой реплики"
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
    assert payload["store"] is False
    assert "temperature" not in payload
    assert timeout_seconds == 30.0
    assert max_response_bytes == 1_000_000
    assert _ACHIEVEMENT not in caplog.text
    assert _DEPLETION not in caplog.text
    assert _REALIZATION_BLOCK not in caplog.text
    assert "offline-test-key" not in caplog.text
