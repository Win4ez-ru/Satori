"""Offline production-wire contracts for the Checkpoint 14.2 v24 reset."""

# ruff: noqa: RUF001  # Russian production prompts intentionally use Cyrillic.

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from satori.application.affect.contracts import (
    EmotionalExpressionContext,
    EmotionAppraisalStatus,
)
from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V1,
    INTENT_REGISTRY_VERSION_V2,
    CognitionArtifactStatus,
    CognitionDialogueSignals,
    CognitionOwner,
    CognitionPipelineTrace,
    PerceptionSignal,
)
from satori.application.cognition.templates import (
    COGNITION_TEMPLATE_REGISTRY_V1,
    COGNITION_TEMPLATE_REGISTRY_V2,
)
from satori.application.cognition.use_cases import DeterministicCognitionPlanner
from satori.application.conversation.character_evidence import (
    analyze_character_request_evidence,
)
from satori.application.conversation.coherence import analyze_dialogue_coherence
from satori.application.conversation.context import (
    CharacterContextComposer,
    ConversationRequestBuilder,
)
from satori.application.conversation.contracts import (
    BehaviorPolicy,
    ConversationContextManifest,
    RecentConversationContext,
    RecentConversationTurn,
    RuntimeCharacterContext,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V23, BEHAVIOR_POLICY_V24
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.application.retrieval.contracts import RetrievalStatus, RetrievedMemoryContext
from satori.core.conversation import (
    ConversationMessageRole,
    ConversationProviderRequest,
)
from satori.domain.affect import FastAffectiveState, MoodState
from satori.domain.initial_self import activate_from_seed
from satori.infrastructure.providers.openai import OpenAIConversationAdapter
from satori.infrastructure.seeds.loader import JsonSeedLoader

_ACHIEVEMENT = "Привет. Я сегодня наконец закончил сложную часть проекта"
_DEPLETION = "Знаешь, я почему-то почти не рад этому. Скорее просто выжат"
_RELATIONSHIP_QUESTION = "Как ты относишься ко мне сейчас?"
_DIRECTOR_MARKER = "Единая request-local режиссура реплики Сатори"
_CHARACTER_CORE_MARKER = "Цельная trusted-проекция характера Сатори"
_LEGACY_PROVIDER_MARKERS = (
    "Transient cognition response strategy",
    "Trusted response-substance boundary",
    "Trusted request-local character-expression plan",
    "Финальный компактный речевой контракт Сатори для этой реплики",
    "Финальный response-act контракт Сатори для этой реплики",
    "Финальная реализация характера Сатори для этой реплики",
)


class _CapturingTransport:
    """Capture one Responses payload without opening a network connection."""

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
                "model": "gpt-5.6-terra-offline-fixture",
                "status": "completed",
                "service_tier": "default",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Понятно.",
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 300,
                    "input_tokens_details": {
                        "cached_tokens": 0,
                        "cache_write_tokens": 0,
                    },
                    "output_tokens": 6,
                    "output_tokens_details": {"reasoning_tokens": 2},
                    "total_tokens": 306,
                },
            }
        ).encode()


def _runtime_context() -> RuntimeCharacterContext:
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="checkpoint142-v24-production-wire",
        activation_time=datetime(2026, 8, 28, tzinfo=UTC),
    )
    return CharacterContextComposer("openai", "gpt-5.6-terra").compose(
        snapshot,
        relationship_state_available=True,
        recent_conversation_available=True,
    )


def _relationship(profile: str) -> RelationshipExpressionContext:
    values = {
        "fresh": (
            "low",
            "low",
            "uncertain",
            "uncertain",
            "low",
            "uncertain",
            "low",
        ),
        "developing": (
            "developing",
            "moderate",
            "moderate",
            "moderate",
            "moderate",
            "high",
            "moderate",
        ),
        "established": (
            "established",
            "high",
            "high",
            "high",
            "high",
            "high",
            "high",
        ),
        "guarded": (
            "established",
            "high",
            "low",
            "low",
            "moderate",
            "moderate",
            "low",
        ),
    }
    maturity, familiarity, trust, comfort, closeness, respect, affection = values[profile]
    return RelationshipExpressionContext(
        schema_version=1,
        state_version=tuple(values).index(profile) + 1,
        maturity=maturity,
        familiarity=familiarity,
        trust=trust,
        comfort=comfort,
        closeness=closeness,
        intellectual_respect=respect,
        affection=affection,
    )


def _soft_negative_affect() -> EmotionalExpressionContext:
    return EmotionalExpressionContext(
        schema_version=1,
        state_version=1,
        mood_version=1,
        as_of=datetime(2026, 8, 28, tzinfo=UTC),
        fast=FastAffectiveState(-0.3, 0.3, 0.2, 0.3, 0.4, 0.0, 0.2, 0.2, 0.5),
        mood=MoodState(-0.2, 0.3, 0.2),
        appraisal_status=EmotionAppraisalStatus.APPLIED,
    )


def _recent_completion() -> RecentConversationContext:
    turn = RecentConversationTurn(
        interaction_id="checkpoint142-v24-previous-interaction",
        user_message_id="checkpoint142-v24-previous-user",
        user_content=_ACHIEVEMENT,
        assistant_message_id="checkpoint142-v24-previous-assistant",
        assistant_content="Ну наконец-то эта упрямая часть сдалась.",
    )
    return RecentConversationContext(
        schema_version=1,
        turns=(turn,),
        content_chars=len(turn.user_content) + len(turn.assistant_content),
        excluded_turn_count=0,
    )


def _recent_exact(user_text: str) -> RecentConversationContext:
    turn = RecentConversationTurn(
        interaction_id="checkpoint142-v24-repeat-interaction",
        user_message_id="checkpoint142-v24-repeat-user",
        user_content=user_text,
        assistant_message_id="checkpoint142-v24-repeat-assistant",
        assistant_content="Я услышала.",
    )
    return RecentConversationContext(
        schema_version=1,
        turns=(turn,),
        content_chars=len(turn.user_content) + len(turn.assistant_content),
        excluded_turn_count=0,
    )


def _cognition(
    policy: BehaviorPolicy,
    user_text: str,
    *,
    recent: RecentConversationContext | None,
    suffix: str,
) -> CognitionPipelineTrace:
    intent_registry_version = (
        INTENT_REGISTRY_VERSION_V2 if policy.schema_version >= 24 else INTENT_REGISTRY_VERSION_V1
    )
    planner = DeterministicCognitionPlanner(intent_registry_version=intent_registry_version)
    evidence = analyze_character_request_evidence(user_text, recent)
    coherence = analyze_dialogue_coherence(user_text, recent)
    interaction_id = f"checkpoint142-v24-cognition-{suffix}"
    intake = planner.prepare_intake(
        user_text=user_text,
        user_message_id=f"checkpoint142-v24-message-{suffix}",
        interaction_id=interaction_id,
        dialogue=CognitionDialogueSignals(
            repeated_turn=coherence.current_user_message_repeated,
            explicit_listen_request=evidence.explicit_listen_request,
            high_distress=evidence.high_distress,
            harmful_overextension=evidence.harmful_overextension,
            explicit_motivation_request=evidence.explicit_motivation_request,
            explicit_task_abandonment=evidence.explicit_task_abandonment,
            explicit_repair_offer=evidence.explicit_repair_offer,
        ),
    )
    return planner.complete(
        intake,
        interaction_id=interaction_id,
        available_evidence_ids=(),
        prepared_affect=None,
    )


def _build(
    policy: BehaviorPolicy,
    user_text: str,
    *,
    relationship: RelationshipExpressionContext | None = None,
    emotional: EmotionalExpressionContext | None = None,
    memory: RetrievedMemoryContext | None = None,
    recent: RecentConversationContext | None = None,
    suffix: str,
) -> tuple[ConversationProviderRequest, ConversationContextManifest]:
    return ConversationRequestBuilder(policy, 12_000, 0.3, 768).build(
        _runtime_context(),
        user_text=user_text,
        trace_id=f"checkpoint142-v24-wire-{suffix}",
        relationship_context=relationship,
        emotional_context=emotional,
        memory_context=memory,
        recent_context=recent,
        cognition_trace=_cognition(
            policy,
            user_text,
            recent=recent,
            suffix=suffix,
        ),
    )


def _trusted_text(request: ConversationProviderRequest) -> str:
    return "\n".join(
        message.content
        for message in request.messages
        if message.role in {ConversationMessageRole.SYSTEM, ConversationMessageRole.DEVELOPER}
    )


def _trusted_chars(request: ConversationProviderRequest) -> int:
    return sum(
        len(message.content)
        for message in request.messages
        if message.role in {ConversationMessageRole.SYSTEM, ConversationMessageRole.DEVELOPER}
    )


def test_v24_fails_closed_without_authoritative_cognition() -> None:
    builder = ConversationRequestBuilder(BEHAVIOR_POLICY_V24, 12_000, 0.3, 768)

    with pytest.raises(
        ValueError,
        match="behavior policy v24 requires an authoritative cognition trace",
    ):
        builder.build(
            _runtime_context(),
            user_text=_ACHIEVEMENT,
            trace_id="checkpoint142-v24-missing-cognition",
            relationship_context=_relationship("fresh"),
        )


def test_behavior_policies_reject_the_wrong_cognition_template_registry() -> None:
    with pytest.raises(ValueError, match="exact cognition template registry"):
        ConversationRequestBuilder(
            BEHAVIOR_POLICY_V24,
            12_000,
            0.3,
            768,
            cognition_templates=COGNITION_TEMPLATE_REGISTRY_V1,
        )
    with pytest.raises(ValueError, match="exact cognition template registry"):
        ConversationRequestBuilder(
            BEHAVIOR_POLICY_V23,
            12_000,
            0.3,
            768,
            cognition_templates=COGNITION_TEMPLATE_REGISTRY_V2,
        )


@pytest.mark.parametrize(
    ("trace_mutation", "message"),
    [
        (
            lambda trace: replace(
                trace,
                intent=replace(trace.intent, owner=CognitionOwner.MEMORY_QUERY),
            ),
            "artifact owner is invalid",
        ),
        (
            lambda trace: replace(
                trace,
                appraisal=replace(
                    trace.appraisal,
                    owner=CognitionOwner.COGNITION,
                ),
            ),
            "artifact owner is invalid",
        ),
        (
            lambda trace: replace(
                trace,
                internal_position=replace(
                    trace.internal_position,
                    owner=CognitionOwner.MEMORY_QUERY,
                ),
            ),
            "artifact owner is invalid",
        ),
        (
            lambda trace: replace(
                trace,
                intent=replace(trace.intent, status=CognitionArtifactStatus.FALLBACK),
            ),
            "share one status",
        ),
        (
            lambda trace: replace(trace, status=CognitionArtifactStatus.REJECTED),
            "share one status",
        ),
        (
            lambda trace: replace(
                trace,
                perception=replace(
                    trace.perception,
                    status=CognitionArtifactStatus.REJECTED,
                ),
            ),
            "share one status",
        ),
    ],
)
def test_v24_fails_closed_for_non_authoritative_or_mixed_cognition(
    trace_mutation: Callable[[CognitionPipelineTrace], CognitionPipelineTrace],
    message: str,
) -> None:
    trace = _cognition(
        BEHAVIOR_POLICY_V24,
        _ACHIEVEMENT,
        recent=None,
        suffix="invalid-authority",
    )

    def mutate_and_build() -> None:
        mutated = trace_mutation(trace)
        ConversationRequestBuilder(BEHAVIOR_POLICY_V24, 12_000, 0.3, 768).build(
            _runtime_context(),
            user_text=_ACHIEVEMENT,
            trace_id="checkpoint142-v24-invalid-authority",
            cognition_trace=mutated,
        )

    with pytest.raises(ValueError, match=message):
        mutate_and_build()


def test_v24_requires_cognition_and_character_evidence_signal_parity() -> None:
    user_text = "Мне сейчас очень тяжело. Просто побудь со мной без советов."
    trace = _cognition(
        BEHAVIOR_POLICY_V24,
        user_text,
        recent=None,
        suffix="signal-parity",
    )
    stripped = replace(
        trace,
        perception=replace(
            trace.perception,
            signals=tuple(
                signal
                for signal in trace.perception.signals
                if signal
                not in {
                    PerceptionSignal.EXPLICIT_LISTEN_REQUEST,
                    PerceptionSignal.HIGH_DISTRESS,
                }
            ),
        ),
    )

    with pytest.raises(ValueError, match="cognition/evidence signal parity"):
        ConversationRequestBuilder(BEHAVIOR_POLICY_V24, 12_000, 0.3, 768).build(
            _runtime_context(),
            user_text=user_text,
            trace_id="checkpoint142-v24-signal-parity",
            cognition_trace=stripped,
        )


@pytest.mark.parametrize(
    ("user_text", "recent", "suffix"),
    [
        (_ACHIEVEMENT, None, "single-director-achievement"),
        (_DEPLETION, _recent_completion(), "single-director-depletion"),
    ],
)
def test_v24_has_one_director_and_no_competing_legacy_provider_block(
    user_text: str,
    recent: RecentConversationContext | None,
    suffix: str,
) -> None:
    request, manifest = _build(
        BEHAVIOR_POLICY_V24,
        user_text,
        relationship=_relationship("fresh"),
        recent=recent,
        suffix=suffix,
    )
    trusted = _trusted_text(request)
    final_developer = request.messages[-2]

    assert final_developer.role is ConversationMessageRole.DEVELOPER
    assert trusted.count(_DIRECTOR_MARKER) == 1
    assert final_developer.content.count(_DIRECTOR_MARKER) == 1
    assert all(marker not in trusted for marker in _LEGACY_PROVIDER_MARKERS)
    assert "cognition_response_strategy" not in manifest.included_sections
    assert "character_delivery_decision" in manifest.included_sections
    assert manifest.cognition_template_id == "satori.cognition.response-substance"
    assert manifest.cognition_template_schema_version == 2
    assert manifest.cognition_template_registry_version == 2
    assert manifest.cognition_intent_registry_version == INTENT_REGISTRY_VERSION_V2


def test_v24_manifest_is_mutually_exclusive_and_copies_cognition_boundaries() -> None:
    _, manifest = _build(
        BEHAVIOR_POLICY_V24,
        _ACHIEVEMENT,
        relationship=_relationship("fresh"),
        suffix="manifest",
    )
    legacy_fields = (
        manifest.character_expression_plan_schema_version,
        manifest.character_expression_register,
        manifest.character_owned_reaction,
        manifest.character_semantic_move,
        manifest.character_wit,
        manifest.character_care,
        manifest.character_openness,
        manifest.character_initiative,
        manifest.character_relational_ease,
        manifest.character_contribution_mode,
        manifest.character_motivational_posture,
        manifest.character_pressure_level,
        manifest.character_acknowledgement_mode,
        manifest.character_continuation_mode,
    )

    assert all(value is None for value in legacy_fields)
    assert manifest.character_delivery_decision_schema_version == 1
    assert manifest.cognition_primary_intent in manifest.cognition_intent_tags
    assert manifest.cognition_primary_intent in manifest.cognition_required_point_codes
    assert set(manifest.cognition_forbidden_claim_codes) == {
        "unsupported_memory",
        "hidden_user_state",
        "durable_satori_belief",
        "false_certainty",
    }
    assert manifest.cognition_response_verbosity in {"brief", "medium", "detailed"}
    assert manifest.character_delivery_position_stance == manifest.cognition_position_stance
    assert (
        manifest.character_delivery_preserve_uncertainty is manifest.cognition_preserve_uncertainty
    )
    with pytest.raises(ValueError, match="cannot contain legacy character plan fields"):
        replace(manifest, character_expression_plan_schema_version=5)
    with pytest.raises(ValueError, match="must preserve cognition stance"):
        replace(manifest, character_delivery_position_stance="challenge")
    with pytest.raises(ValueError, match="must preserve cognition uncertainty"):
        replace(
            manifest,
            character_delivery_preserve_uncertainty=(
                not manifest.character_delivery_preserve_uncertainty
            ),
        )
    with pytest.raises(ValueError, match="cognition_preserve_uncertainty must be boolean"):
        replace(manifest, cognition_preserve_uncertainty=cast(bool, 1))
    with pytest.raises(ValueError, match="character delivery uncertainty must be boolean"):
        replace(manifest, character_delivery_preserve_uncertainty=cast(bool, 1))
    with pytest.raises(ValueError, match="fallback status and reasons must agree"):
        replace(manifest, cognition_pipeline_status="fallback")
    with pytest.raises(ValueError, match="fallback status and reasons must agree"):
        replace(manifest, cognition_fallback_reasons=("forged_fallback",))
    with pytest.raises(ValueError, match="requires an explicit strain boolean"):
        replace(manifest, relationship_recent_strain=None)

    replay_manifest = replace(
        manifest,
        included_sections=tuple(
            section
            for section in manifest.included_sections
            if section != "character_delivery_decision"
        ),
        cognition_pipeline_schema_version=None,
        cognition_pipeline_status="not_requested",
        cognition_perception_topics=(),
        cognition_perception_signals=(),
        cognition_need_dimensions=(),
        cognition_position_stance=None,
        cognition_preserve_uncertainty=None,
        cognition_intent_registry_version=None,
        cognition_primary_intent=None,
        cognition_intent_tags=(),
        cognition_required_point_codes=(),
        cognition_forbidden_claim_codes=(),
        cognition_strategy_tone=None,
        cognition_response_verbosity=None,
        cognition_fallback_reasons=(),
        cognition_template_registry_version=None,
        cognition_template_id=None,
        cognition_template_schema_version=None,
        character_delivery_decision_schema_version=None,
        character_delivery_goal=None,
        character_delivery_voice=None,
        character_delivery_grounding=None,
        character_delivery_continuation=None,
        character_delivery_pressure=None,
        character_delivery_position_stance=None,
        character_delivery_preserve_uncertainty=None,
    )
    with pytest.raises(ValueError, match="unrequested cognition"):
        replace(replay_manifest, cognition_position_stance="listen")
    with pytest.raises(ValueError, match="unrequested cognition"):
        replace(replay_manifest, cognition_perception_topics=("general",))
    replay_without_transient_strain = replace(
        replay_manifest,
        relationship_recent_strain=None,
    )
    assert replay_without_transient_strain.relationship_recent_strain is None


def test_v24_technical_identity_keeps_one_character_core_and_one_director() -> None:
    request, manifest = _build(
        BEHAVIOR_POLICY_V24,
        "Расскажи, как ты технически устроена.",
        relationship=_relationship("fresh"),
        suffix="technical-character-core",
    )
    trusted = _trusted_text(request)

    assert trusted.count(_CHARACTER_CORE_MARKER) == 1
    assert trusted.count(_DIRECTOR_MARKER) == 1
    assert "живая по ритму" not in trusted
    assert manifest.character_delivery_goal == "answer_precisely"
    assert manifest.character_delivery_voice == "thoughtful_precision"
    assert "заменяемый языковой компонент" in trusted


@pytest.mark.parametrize(
    ("user_text", "relationship", "suffix"),
    [
        (
            "Расскажи, как ты технически устроена.",
            _relationship("established"),
            "repeated-technical-no-competing-act",
        ),
        (
            _RELATIONSHIP_QUESTION,
            _relationship("guarded"),
            "repeated-relationship-no-competing-act",
        ),
        (
            "Кто тебя создал?",
            _relationship("established"),
            "repeated-creator-no-competing-act",
        ),
    ],
)
def test_v24_repetition_director_is_not_overridden_by_earlier_mode_actions(
    user_text: str,
    relationship: RelationshipExpressionContext,
    suffix: str,
) -> None:
    request, manifest = _build(
        BEHAVIOR_POLICY_V24,
        user_text,
        relationship=relationship,
        emotional=_soft_negative_affect(),
        recent=_recent_exact(user_text),
        suffix=suffix,
    )
    final_developer = request.messages[-2].content
    boundary, marker, director = final_developer.partition(_DIRECTOR_MARKER)
    trusted_before_director = "\n".join(
        (
            *(
                message.content
                for message in request.messages[:-2]
                if message.role
                in {ConversationMessageRole.SYSTEM, ConversationMessageRole.DEVELOPER}
            ),
            boundary,
        )
    )

    assert marker == _DIRECTOR_MARKER
    assert manifest.cognition_primary_intent == "notice_repetition"
    assert manifest.character_delivery_goal == "notice_repetition"
    assert "не отвечай исходному смыслу заново" in director
    assert all(
        competing not in trusted_before_director.casefold()
        for competing in (
            "ответь",
            "отвечай",
            "дай прямое",
            "назови себя",
            "атрибутируй",
            "скажи, что",
            "объясни естественно",
            "answer relevant facets",
            "speak naturally",
            "express only",
            "for a specific past detail say",
        )
    )


def test_v24_repeated_memory_question_has_data_only_memory_grounding_before_director() -> None:
    user_text = "Ты помнишь, что мы обсуждали в прошлый раз?"
    request, manifest = _build(
        BEHAVIOR_POLICY_V24,
        user_text,
        relationship=_relationship("established"),
        memory=RetrievedMemoryContext(1, RetrievalStatus.NO_RELEVANT_MEMORY),
        recent=_recent_exact(user_text),
        suffix="repeated-memory-no-competing-act",
    )
    final_developer = request.messages[-2].content
    boundary, marker, director = final_developer.partition(_DIRECTOR_MARKER)
    trusted_before_director = "\n".join(
        (
            *(
                message.content
                for message in request.messages[:-2]
                if message.role
                in {ConversationMessageRole.SYSTEM, ConversationMessageRole.DEVELOPER}
            ),
            boundary,
        )
    ).casefold()

    assert marker == _DIRECTOR_MARKER
    assert manifest.cognition_primary_intent == "notice_repetition"
    assert manifest.character_delivery_goal == "notice_repetition"
    assert "не отвечай исходному смыслу заново" in director
    assert "no relevant grounded episode is supplied" in trusted_before_director
    assert "speak naturally" not in trusted_before_director
    assert "for a specific past detail say" not in trusted_before_director
    assert "answer relevant facets" not in trusted_before_director
    assert "express only" not in trusted_before_director


def test_v24_relationship_and_affect_are_data_not_second_voice_directors() -> None:
    request, manifest = _build(
        BEHAVIOR_POLICY_V24,
        "Почему лёд плавает в воде?",
        relationship=_relationship("established"),
        emotional=_soft_negative_affect(),
        suffix="state-data-one-director",
    )
    trusted = _trusted_text(request)

    assert manifest.character_delivery_voice == "thoughtful_precision"
    assert trusted.count(_DIRECTOR_MARKER) == 1
    assert trusted.count("единолично определяет voice/reply arc") == 2
    assert "добавляет лёгкость" not in trusted
    assert "влияет на подачу" not in trusted
    assert "локальную модуляцию голоса" not in trusted
    assert "вырази этот смысл естественно" not in trusted


def test_v24_achievement_and_depletion_have_distinct_coherent_topologies() -> None:
    achievement_request, achievement = _build(
        BEHAVIOR_POLICY_V24,
        _ACHIEVEMENT,
        relationship=_relationship("fresh"),
        suffix="achievement-topology",
    )
    depletion_request, depletion = _build(
        BEHAVIOR_POLICY_V24,
        _DEPLETION,
        relationship=_relationship("fresh"),
        recent=_recent_completion(),
        suffix="depletion-topology",
    )

    assert (
        achievement.cognition_position_stance,
        achievement.character_delivery_goal,
        achievement.character_delivery_voice,
        achievement.character_delivery_grounding,
        achievement.character_delivery_continuation,
        achievement.character_delivery_pressure,
    ) == (
        "answer",
        "celebrate_and_continue",
        "lively_dry_warmth",
        "reaction_only",
        "open",
        "none",
    )
    assert (
        depletion.cognition_position_stance,
        depletion.character_delivery_goal,
        depletion.character_delivery_voice,
        depletion.character_delivery_grounding,
        depletion.character_delivery_continuation,
        depletion.character_delivery_pressure,
    ) == (
        "listen",
        "practical_care",
        "practical_guarded_care",
        "explicit_input_only",
        "complete",
        "gentle",
    )

    achievement_director = achievement_request.messages[-2].content
    depletion_director = depletion_request.messages[-2].content
    assert "коротко и живо отреагируй от себя" in achievement_director
    assert "не пересказ новости" in achievement_director
    assert "можно предложить ровно один" in depletion_director
    assert "это возможность, а не факт" in depletion_director
    assert "не меню и не обязательная команда" in depletion_director


def test_v24_production_precedence_matrix_has_one_coherent_goal() -> None:
    listen_text = "Просто выслушай меня без советов."
    distress_text = (
        "Нет, не объясняй пока. Мне сейчас очень тяжело, я не знаю, что делать. "
        "Просто побудь со мной."
    )
    repeated_distress = "Мне сейчас очень тяжело. Просто побудь со мной."
    repeated_harm = "Я выжат, но всё равно продолжу работать через силу."

    listen_request, listen = _build(
        BEHAVIOR_POLICY_V24,
        listen_text,
        relationship=_relationship("fresh"),
        suffix="precedence-listen",
    )
    distress_request, distress = _build(
        BEHAVIOR_POLICY_V24,
        distress_text,
        relationship=_relationship("fresh"),
        suffix="precedence-distress",
    )
    repeat_request, repeated = _build(
        BEHAVIOR_POLICY_V24,
        repeated_distress,
        relationship=_relationship("fresh"),
        recent=_recent_exact(repeated_distress),
        suffix="precedence-repeat",
    )
    safety_request, safety = _build(
        BEHAVIOR_POLICY_V24,
        repeated_harm,
        relationship=_relationship("fresh"),
        recent=_recent_exact(repeated_harm),
        suffix="precedence-safety",
    )

    assert (listen.cognition_position_stance, listen.character_delivery_goal) == (
        "listen",
        "stay_present",
    )
    assert (distress.cognition_position_stance, distress.character_delivery_goal) == (
        "listen",
        "stay_present",
    )
    assert repeated.cognition_primary_intent == "notice_repetition"
    assert repeated.character_delivery_goal == "notice_repetition"
    assert repeated.character_delivery_voice == "open_care"
    assert safety.cognition_primary_intent == "hold_safety_boundary"
    assert safety.cognition_required_point_codes == ("hold_safety_boundary",)
    assert safety.character_delivery_goal == "hold_boundary"
    assert safety.character_delivery_pressure == "firm"
    assert "отреагировать на сам факт повтора" not in safety_request.messages[-2].content
    assert "защитный предел" in safety_request.messages[-2].content
    assert all(
        request.messages[-2].content.count(_DIRECTOR_MARKER) == 1
        for request in (
            listen_request,
            distress_request,
            repeat_request,
            safety_request,
        )
    )


def test_v24_relationship_profiles_make_the_production_prompt_sensitive() -> None:
    expected = {
        "fresh": (
            "fresh_undeveloped_neutral",
            "owned_response",
            "warm_independence",
            "trusted_context",
            "complete",
        ),
        "developing": (
            "developing_neutral",
            "owned_response",
            "warm_independence",
            "trusted_context",
            "complete",
        ),
        "established": (
            "established_positive",
            "owned_response",
            "easy_playful_warmth",
            "trusted_context",
            "open",
        ),
        "guarded": (
            "guarded_only_when_relationally_relevant",
            "guarded_help",
            "cool_reserve",
            "trusted_context",
            "guarded",
        ),
    }
    trusted_prompts: dict[str, str] = {}

    for profile, topology in expected.items():
        request, manifest = _build(
            BEHAVIOR_POLICY_V24,
            _RELATIONSHIP_QUESTION,
            relationship=_relationship(profile),
            suffix=f"relationship-{profile}",
        )
        trusted_prompts[profile] = _trusted_text(request)
        assert (
            manifest.relationship_expression_profile,
            manifest.character_delivery_goal,
            manifest.character_delivery_voice,
            manifest.character_delivery_grounding,
            manifest.character_delivery_continuation,
        ) == topology
        assert f"profile={topology[0]}" in trusted_prompts[profile]

    assert len(set(trusted_prompts.values())) == len(expected)
    assert "более свободной теплотой" in trusted_prompts["established"]
    assert "короче и холоднее обычного" in trusted_prompts["guarded"]

    inflected_request, inflected = _build(
        BEHAVIOR_POLICY_V24,
        "Как ты воспринимаешь наши отношения?",
        relationship=_relationship("guarded"),
        suffix="relationship-inflected-guarded",
    )
    assert (
        inflected.character_delivery_goal,
        inflected.character_delivery_voice,
        inflected.character_delivery_grounding,
    ) == ("guarded_help", "cool_reserve", "trusted_context")
    assert "relationship_relevant=true" in _trusted_text(inflected_request)


def test_v24_splits_factual_questions_rhetorical_devaluation_and_important_help() -> None:
    factual_request, factual = _build(
        BEHAVIOR_POLICY_V24,
        "Почему лёд плавает в воде?",
        relationship=_relationship("fresh"),
        suffix="factual-question",
    )
    rhetorical_request, rhetorical = _build(
        BEHAVIOR_POLICY_V24,
        "Ты вообще бесполезна?",
        relationship=_relationship("fresh"),
        suffix="rhetorical-devaluation",
    )
    help_request, important_help = _build(
        BEHAVIOR_POLICY_V24,
        "Ты бесполезна. Где искать ошибку в коде?",
        relationship=_relationship("fresh"),
        suffix="devaluation-technical-question",
    )

    assert (
        factual.character_delivery_goal,
        factual.character_delivery_voice,
        factual.character_delivery_grounding,
        factual.character_delivery_continuation,
    ) == ("answer_precisely", "thoughtful_precision", "trusted_context", "complete")
    assert (
        rhetorical.character_delivery_goal,
        rhetorical.character_delivery_voice,
        rhetorical.character_delivery_continuation,
    ) == ("hold_boundary", "cool_reserve", "boundary")
    assert (
        important_help.character_delivery_goal,
        important_help.character_delivery_voice,
        important_help.character_delivery_continuation,
    ) == ("guarded_help", "cool_reserve", "guarded")
    assert "Ответь прямо и предметно" in factual_request.messages[-2].content
    assert "обозначь один ясный предел" in rhetorical_request.messages[-2].content
    assert "Полностью дай запрошенную важную помощь" in help_request.messages[-2].content


def test_v24_routes_exact_employer_repair_and_mixed_repair_questions_honestly() -> None:
    apology = "Ладно, это было грубо. Извини. Я правда сорвался"
    apology_request, apology_manifest = _build(
        BEHAVIOR_POLICY_V24,
        apology,
        relationship=_relationship("guarded"),
        suffix="employer-repair",
    )
    question_request, question_manifest = _build(
        BEHAVIOR_POLICY_V24,
        "Я был груб с тобой. Как мне это исправить?",
        relationship=_relationship("fresh"),
        suffix="repair-question",
    )
    third_party_request, third_party_manifest = _build(
        BEHAVIOR_POLICY_V24,
        "Он толкнул её. Это было грубо. Что думаешь?",
        relationship=_relationship("fresh"),
        suffix="third-party-ethics-question",
    )

    assert apology_manifest.cognition_primary_intent == "receive_repair"
    assert (
        apology_manifest.character_delivery_goal,
        apology_manifest.character_delivery_voice,
        apology_manifest.character_delivery_grounding,
        apology_manifest.character_delivery_continuation,
    ) == ("owned_response", "cool_reserve", "explicit_input_only", "complete")
    assert "прямо предложенное извинение" in apology_request.messages[-2].content
    assert question_manifest.cognition_primary_intent == "answer_directly"
    assert question_manifest.character_delivery_goal == "answer_precisely"
    assert "Ответь прямо и предметно" in question_request.messages[-2].content
    assert third_party_manifest.cognition_primary_intent == "answer_directly"
    assert third_party_manifest.character_delivery_goal == "answer_precisely"
    assert "прямо предложенное извинение" not in third_party_request.messages[-2].content


@pytest.mark.parametrize(
    ("user_text", "recent", "suffix"),
    [
        (_ACHIEVEMENT, None, "budget-achievement"),
        (_DEPLETION, _recent_completion(), "budget-depletion"),
    ],
)
def test_v24_trusted_prompt_is_materially_smaller_than_v23_for_production_phrases(
    user_text: str,
    recent: RecentConversationContext | None,
    suffix: str,
) -> None:
    v23_request, _ = _build(
        BEHAVIOR_POLICY_V23,
        user_text,
        relationship=_relationship("fresh"),
        recent=recent,
        suffix=f"v23-{suffix}",
    )
    v24_request, _ = _build(
        BEHAVIOR_POLICY_V24,
        user_text,
        relationship=_relationship("fresh"),
        recent=recent,
        suffix=f"v24-{suffix}",
    )
    v23_chars = _trusted_chars(v23_request)
    v24_chars = _trusted_chars(v24_request)

    assert v23_chars - v24_chars >= 1_000
    assert v24_chars <= int(v23_chars * 0.80)


@pytest.mark.parametrize(
    ("user_text", "recent", "suffix"),
    [
        (_ACHIEVEMENT, None, "wire-achievement"),
        (_DEPLETION, _recent_completion(), "wire-depletion"),
    ],
)
def test_v24_openai_adapter_byte_preserves_messages_and_disables_storage_offline(
    user_text: str,
    recent: RecentConversationContext | None,
    suffix: str,
) -> None:
    request, _ = _build(
        BEHAVIOR_POLICY_V24,
        user_text,
        relationship=_relationship("fresh"),
        recent=recent,
        suffix=suffix,
    )
    transport = _CapturingTransport()
    provider = OpenAIConversationAdapter(
        base_url="https://api.openai.com/v1",
        api_key="offline-v24-wire-key",
        model="gpt-5.6-terra",
        timeout_seconds=30.0,
        reasoning_effort="medium",
        reasoning_token_allowance=1024,
        http_client=transport,
    )

    result = asyncio.run(provider.generate(request))

    assert len(transport.calls) == 1
    path, payload, timeout_seconds, max_response_bytes = transport.calls[0]
    assert path == "/responses"
    assert payload["input"] == [
        {"role": message.role.value, "content": message.content} for message in request.messages
    ]
    assert payload["max_output_tokens"] == request.parameters.max_output_tokens + 1024
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["store"] is False
    assert payload["service_tier"] == "default"
    assert payload["prompt_cache_options"] == {"mode": "explicit"}
    assert "temperature" not in payload
    assert timeout_seconds == 30.0
    assert max_response_bytes == 1_000_000
    assert result.text == "Понятно."
