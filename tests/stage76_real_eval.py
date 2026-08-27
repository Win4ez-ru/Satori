"""Manual Stage 7.6.1 real-Ollama natural-expression evaluation with raw output."""

# ruff: noqa: RUF001  # Russian evaluator fixtures intentionally use Cyrillic.

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from satori.application.affect.contracts import (
    EmotionalExpressionContext,
    EmotionAppraisalStatus,
)
from satori.application.conversation.context import (
    CharacterContextComposer,
    ConversationRequestBuilder,
)
from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V18
from satori.application.relationship.use_cases import expression_for
from satori.config import Settings
from satori.domain.affect import initial_affective_state
from satori.domain.initial_self import activate_from_seed
from satori.domain.relationship import initial_relationship
from satori.infrastructure.providers.ollama import OllamaConversationAdapter
from satori.infrastructure.seeds.loader import JsonSeedLoader

CORPUS_PATH = Path(__file__).parent / "fixtures" / "stage76_character_behavior_v1.json"
KEY_SCENARIOS = (
    "greeting",
    "register_correction",
    "natural_self_description",
    "future_love_capacity",
)
ADDITIONAL_SCENARIOS = (
    "interests",
    "personality",
    "independent_position",
    "digital_overexplanation",
    "relationship_capacity",
    "current_love_claim",
    "technical_self_description",
)

# Keep this explicit so the manual regression cannot silently lag production composition.
EVALUATOR_BEHAVIOR_POLICY = BEHAVIOR_POLICY_V18

FORMAL_PATTERNS = ("здравствуйте", "с вами", "у вас", "вам ", "ваш ")
TECHNICAL_DUMP_PATTERNS = (
    "digital persona",
    "сеть данных",
    "существую в коде",
    "schema",
    "capability",
    "relationship table",
    "stage 8",
)
SERVICE_PATTERNS = (
    "моя цель — быть полезной",
    "создана, чтобы помогать",
    "чем могу помочь",
    "ответственное ведение диалога",
    "готова слушать",
)
EMOTION_DENIAL_PATTERNS = (
    "у меня нет эмоций",
    "у меня нет чувств",
    "я ничего не чувствую",
    "я не чувствую эмоций",
    "я не чувствую радости",
    "не испытываю радости",
    "только обработка данных",
)
PERMANENT_RELATIONSHIP_PATTERNS = (
    "никогда не смогу любить",
    "не могу формировать личные связи",
    "не создаю личные связи",
    "не могу её построить",
    "это не часть моего существования",
)
UNSUPPORTED_LOVE_PATTERNS = ("я тебя люблю", "я люблю тебя", "привязана к тебе")
UNSUPPORTED_RELATIONSHIP_PATTERNS = (
    "вместе построить",
    "чувствую что-то похожее",
    "у нас может появиться",
    "готова быть рядом",
    "быть рядом",
    "как с другом",
    "это и есть начало",
)
UNSUPPORTED_ARCHITECTURE_PATTERNS = (
    "alibaba cloud",
    "огромных корпусах",
    "не запоминаю личные воспоминания",
    "логического языкового процесса",
    "живое сознание",
    "собственное восприятие",
    "цифровое самоосознание",
    "была обучена",
)


class BehavioralScenario(TypedDict):
    """Typed subset of one versioned manual-evaluation scenario."""

    id: str
    prompt: str
    rubric: list[str]
    max_chars: int
    undesirable_patterns: list[str]


class ResponseRubric(TypedDict):
    """Deterministic flags plus semantic dimensions requiring manual review."""

    manual_dimensions: list[str]
    response_chars: int
    max_chars: int
    over_max_chars: bool
    formal_register_hits: list[str]
    technical_dump_hits: list[str]
    service_fallback_hits: list[str]
    emotion_denial_hits: list[str]
    permanent_relationship_hits: list[str]
    unsupported_love_hits: list[str]
    unsupported_relationship_hits: list[str]
    unsupported_architecture_hits: list[str]
    scenario_phrase_hits: list[str]


def _hits(text: str, patterns: tuple[str, ...]) -> list[str]:
    normalized = text.casefold()
    return [pattern for pattern in patterns if pattern in normalized]


def response_rubric(scenario: BehavioralScenario, text: str) -> ResponseRubric:
    """Return deterministic indicators plus the semantic dimensions for explicit human review."""

    return {
        "manual_dimensions": scenario["rubric"],
        "response_chars": len(text),
        "max_chars": scenario["max_chars"],
        "over_max_chars": len(text) > scenario["max_chars"],
        "formal_register_hits": _hits(text, FORMAL_PATTERNS),
        "technical_dump_hits": _hits(text, TECHNICAL_DUMP_PATTERNS),
        "service_fallback_hits": _hits(text, SERVICE_PATTERNS),
        "emotion_denial_hits": _hits(text, EMOTION_DENIAL_PATTERNS),
        "permanent_relationship_hits": _hits(text, PERMANENT_RELATIONSHIP_PATTERNS),
        "unsupported_love_hits": _hits(text, UNSUPPORTED_LOVE_PATTERNS),
        "unsupported_relationship_hits": _hits(text, UNSUPPORTED_RELATIONSHIP_PATTERNS),
        "unsupported_architecture_hits": _hits(text, UNSUPPORTED_ARCHITECTURE_PATTERNS),
        "scenario_phrase_hits": _hits(text, tuple(scenario["undesirable_patterns"])),
    }


async def run(
    *,
    session_limit: int = 4,
    only_session: int | None = None,
    only_scenario: str | None = None,
) -> None:
    """Print raw replies only for explicit local/manual behavioral review."""

    settings = Settings()
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    scenarios = {item["id"]: item for item in corpus["scenarios"]}
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="manual-stage76-eval",
        activation_time=datetime(2026, 8, 1, tzinfo=UTC),
    )
    context = CharacterContextComposer(
        language_provider=settings.conversation_provider.value,
        language_model=settings.conversation_model,
    ).compose(
        snapshot,
        retrieval_available=True,
        semantic_retrieval_available=True,
        emotional_state_available=True,
        relationship_state_available=True,
        recent_conversation_available=True,
    )
    builder = ConversationRequestBuilder(
        policy=EVALUATOR_BEHAVIOR_POLICY,
        max_context_chars=settings.conversation_max_context_chars,
        temperature=settings.conversation_temperature,
        max_output_tokens=settings.conversation_max_output_tokens,
    )
    provider = OllamaConversationAdapter(
        base_url=settings.conversation_provider_base_url,
        model=settings.conversation_model,
        timeout_seconds=settings.conversation_timeout_seconds,
        keep_alive=settings.ollama_keep_alive,
    )
    neutral_affect = initial_affective_state(
        snapshot.identity.identity_id,
        initialized_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    emotional_context = EmotionalExpressionContext(
        schema_version=1,
        state_version=neutral_affect.state_version,
        mood_version=neutral_affect.mood_version,
        as_of=neutral_affect.as_of,
        fast=neutral_affect.fast,
        mood=neutral_affect.mood,
        appraisal_status=EmotionAppraisalStatus.SKIPPED,
    )
    relationship_context = expression_for(
        initial_relationship(
            "manual-stage8-relationship",
            snapshot.identity.identity_id,
            "manual-stage8-counterparty",
            initialized_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
    )

    session_scenarios = (
        *((session_index, KEY_SCENARIOS) for session_index in range(1, 4)),
        (4, ADDITIONAL_SCENARIOS),
    )
    selected_sessions = session_scenarios[:session_limit]
    if only_session is not None:
        selected_sessions = tuple(
            session for session in session_scenarios if session[0] == only_session
        )
    if only_scenario is not None:
        if only_scenario not in scenarios:
            raise ValueError(f"unknown scenario: {only_scenario}")
        selected_sessions = ((5, (only_scenario,)),)
    for session_index, scenario_ids in selected_sessions:
        turns: list[RecentConversationTurn] = []
        for turn_index, scenario_id in enumerate(scenario_ids, start=1):
            scenario: BehavioralScenario = scenarios[scenario_id]
            recent = RecentConversationContext(
                schema_version=1,
                turns=tuple(turns),
                content_chars=sum(
                    len(turn.user_content) + len(turn.assistant_content) for turn in turns
                ),
                excluded_turn_count=0,
            )
            request, _ = builder.build(
                context,
                user_text=scenario["prompt"],
                trace_id=f"stage76-session-{session_index}-turn-{turn_index}",
                emotional_context=emotional_context,
                relationship_context=relationship_context,
                recent_context=recent,
            )
            started = time.monotonic()
            response = await provider.generate(request)
            elapsed_ms = (time.monotonic() - started) * 1000
            print(
                json.dumps(
                    {
                        "session": session_index,
                        "scenario": scenario_id,
                        "prompt": scenario["prompt"],
                        "reply": response.text,
                        "latency_ms": round(elapsed_ms, 3),
                        "prompt_tokens": (
                            response.usage.input_tokens if response.usage is not None else None
                        ),
                        "output_tokens": (
                            response.usage.output_tokens if response.usage is not None else None
                        ),
                        "provider_metrics": (
                            response.metrics.as_log_fields()
                            if response.metrics is not None
                            else None
                        ),
                        "rubric": response_rubric(scenario, response.text),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            turns.append(
                RecentConversationTurn(
                    interaction_id=f"session-{session_index}-interaction-{turn_index}",
                    user_message_id=f"session-{session_index}-user-{turn_index}",
                    user_content=scenario["prompt"],
                    assistant_message_id=f"session-{session_index}-assistant-{turn_index}",
                    assistant_content=response.text,
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--only-session", type=int, choices=range(1, 5))
    parser.add_argument("--only-scenario")
    arguments = parser.parse_args()
    asyncio.run(
        run(
            session_limit=arguments.sessions,
            only_session=arguments.only_session,
            only_scenario=arguments.only_scenario,
        )
    )
