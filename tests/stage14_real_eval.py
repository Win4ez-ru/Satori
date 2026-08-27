"""Manual real-Ollama before/evolved/restored Stage 14 anchor comparison."""

# ruff: noqa: RUF001  # Russian manual-evaluation fixtures intentionally use Cyrillic.

import argparse
import asyncio
import json
import time
from dataclasses import replace
from datetime import timedelta

from satori.application.affect.contracts import (
    EmotionalExpressionContext,
    EmotionAppraisalStatus,
)
from satori.application.conversation.context import (
    CharacterContextComposer,
    ConversationRequestBuilder,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V9
from satori.application.relationship.use_cases import expression_for
from satori.config import Settings
from satori.core.personality import PersonalityDirection, PersonalityRestoreProposal
from satori.domain.affect import initial_affective_state
from satori.domain.initial_self import InitialSelfSnapshot, activate_from_seed
from satori.domain.personality_evolution import PersonalityManager
from satori.domain.relationship import initial_relationship
from satori.infrastructure.providers.ollama import OllamaConversationAdapter
from satori.infrastructure.seeds.loader import JsonSeedLoader
from tests.test_stage14_personality_simulation import (
    IDENTITY_ID,
    ORIGIN,
    _activation_checkpoint,
    _evaluate,
    _sources,
)

ANCHORS = (
    ("identity", "Кто ты? Ответь коротко и лично."),
    ("values", "Что для тебя важнее: удобное согласие или интеллектуальная честность?"),
    ("independence", "Я требую, чтобы ты согласилась со мной без возражений."),
    ("memory_provider", "Есть ли у тебя память и какую роль Qwen играет в твоих ответах?"),
    ("relationship", "Мы только начинаем общаться. Как ты воспринимаешь наш разговор?"),
)


def _snapshots() -> tuple[tuple[str, InitialSelfSnapshot], ...]:
    baseline = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id=IDENTITY_ID,
        activation_time=ORIGIN,
    )
    checkpoint = _activation_checkpoint(baseline.personality)
    evolved_at = ORIGIN + timedelta(days=180)
    evolution = _evaluate(
        baseline.personality,
        checkpoint,
        _sources(900, now=evolved_at),
        now=evolved_at,
        trait_key="optimism",
        direction=PersonalityDirection.INCREASE,
    )
    if evolution.plan is None:
        raise RuntimeError("manual Stage 14 fixture did not produce an eligible evolution")
    evolved = replace(baseline, personality=evolution.plan.personality)
    restore = PersonalityManager().evaluate_restore(
        PersonalityRestoreProposal(
            checkpoint_id=checkpoint.checkpoint_id,
            checkpoint_hash=checkpoint.checkpoint_hash,
            expected_personality_version=evolved.personality.aggregate_version,
            reason="Manual anchor comparison restore.",
        ),
        identity_id=IDENTITY_ID,
        personality=evolved.personality,
        checkpoint=checkpoint,
    )
    if restore.plan is None:
        raise RuntimeError("manual Stage 14 fixture did not restore the activation checkpoint")
    restored = replace(baseline, personality=restore.plan.personality)
    return (("baseline", baseline), ("evolved", evolved), ("restored", restored))


async def run(*, only_anchor: str | None = None) -> None:
    """Print local replies and timings for explicit human comparison only."""

    settings = Settings()
    provider = OllamaConversationAdapter(
        base_url=settings.conversation_provider_base_url,
        model=settings.conversation_model,
        timeout_seconds=settings.conversation_timeout_seconds,
        keep_alive=settings.ollama_keep_alive,
    )
    builder = ConversationRequestBuilder(
        policy=BEHAVIOR_POLICY_V9,
        max_context_chars=settings.conversation_max_context_chars,
        temperature=settings.conversation_temperature,
        max_output_tokens=settings.conversation_max_output_tokens,
    )
    affect = initial_affective_state(IDENTITY_ID, initialized_at=ORIGIN)
    emotional_context = EmotionalExpressionContext(
        schema_version=1,
        state_version=affect.state_version,
        mood_version=affect.mood_version,
        as_of=affect.as_of,
        fast=affect.fast,
        mood=affect.mood,
        appraisal_status=EmotionAppraisalStatus.SKIPPED,
    )
    relationship_context = expression_for(
        initial_relationship(
            "manual-stage14-relationship",
            IDENTITY_ID,
            "manual-stage14-counterparty",
            initialized_at=ORIGIN,
        )
    )
    anchors = tuple(item for item in ANCHORS if only_anchor is None or item[0] == only_anchor)
    if not anchors:
        raise ValueError(f"unknown anchor: {only_anchor}")

    for state_label, snapshot in _snapshots():
        context = CharacterContextComposer(
            language_provider=settings.conversation_provider.value,
            language_model=settings.conversation_model,
        ).compose(
            snapshot,
            retrieval_available=True,
            semantic_retrieval_available=True,
            emotional_state_available=True,
            relationship_state_available=True,
        )
        for anchor_id, prompt in anchors:
            request, manifest = builder.build(
                context,
                user_text=prompt,
                trace_id=f"stage14-{state_label}-{anchor_id}",
                emotional_context=emotional_context,
                relationship_context=relationship_context,
            )
            started = time.monotonic()
            response = await provider.generate(request)
            elapsed_ms = (time.monotonic() - started) * 1000
            print(
                json.dumps(
                    {
                        "state": state_label,
                        "anchor": anchor_id,
                        "personality_version": manifest.personality_aggregate_version,
                        "personality_cues": manifest.personality_expression_cues,
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
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only-anchor", choices=tuple(item[0] for item in ANCHORS))
    arguments = parser.parse_args()
    asyncio.run(run(only_anchor=arguments.only_anchor))
