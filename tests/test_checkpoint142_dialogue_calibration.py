"""Deterministic contracts for corrective checkpoint 14.2 dialogue calibration."""

# ruff: noqa: RUF001  # Russian behavioral contract text intentionally uses Cyrillic.

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from satori.application.affect.contracts import (
    EmotionalExpressionContext,
    EmotionAppraisalStatus,
)
from satori.application.cognition.contracts import (
    CognitionDialogueSignals,
    CognitionPipelineTrace,
    PositionStance,
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
    BEHAVIOR_POLICY_V16,
    BEHAVIOR_POLICY_V17,
    BEHAVIOR_POLICY_V18,
    BEHAVIOR_POLICY_V19,
)
from satori.application.conversation.response_validation import ResponseRegenerationReason
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.application.retrieval.contracts import RetrievalStatus, RetrievedMemoryContext
from satori.core.conversation import ConversationProviderRequest
from satori.domain.affect import FastAffectiveState, MoodState
from satori.domain.initial_self import activate_from_seed
from satori.infrastructure.seeds.loader import JsonSeedLoader


def _builder() -> tuple[ConversationRequestBuilder, RuntimeCharacterContext]:
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="checkpoint142-dialogue-calibration",
        activation_time=datetime(2026, 8, 23, tzinfo=UTC),
    )
    context = CharacterContextComposer("yandex_ai_studio", "yandexgpt/latest").compose(
        snapshot,
        retrieval_available=True,
        semantic_retrieval_available=True,
        emotional_state_available=True,
        relationship_state_available=True,
        recent_conversation_available=True,
    )
    return (
        ConversationRequestBuilder(BEHAVIOR_POLICY_V19, 12_000, 0.3, 768),
        context,
    )


def _neutral_affect() -> EmotionalExpressionContext:
    return EmotionalExpressionContext(
        schema_version=1,
        state_version=3,
        mood_version=2,
        as_of=datetime(2026, 8, 23, tzinfo=UTC),
        fast=FastAffectiveState(0.0, 0.2, 0.1, 0.3, 0.3, 0.1, 0.1, 0.1, 0.5),
        mood=MoodState(0.0, 0.3, 0.1),
        appraisal_status=EmotionAppraisalStatus.APPLIED,
    )


def _trusted_text(request: ConversationProviderRequest) -> str:
    return "\n".join(message.content for message in request.messages[:-1])


def _cognition(user_text: str, *, suffix: str) -> CognitionPipelineTrace:
    planner = DeterministicCognitionPlanner()
    interaction_id = f"checkpoint142-cognition-{suffix}"
    intake = planner.prepare_intake(
        user_text=user_text,
        user_message_id=f"checkpoint142-message-{suffix}",
        interaction_id=interaction_id,
        dialogue=CognitionDialogueSignals(),
    )
    return planner.complete(
        intake,
        interaction_id=interaction_id,
        available_evidence_ids=(),
        prepared_affect=None,
    )


def _recent_completion() -> RecentConversationContext:
    turn = RecentConversationTurn(
        interaction_id="checkpoint142-previous-interaction",
        user_message_id="checkpoint142-previous-user",
        user_content="Привет. Я сегодня наконец закончил сложную часть проекта",
        assistant_message_id="checkpoint142-previous-assistant",
        assistant_content="Ну наконец-то эта упрямая часть сдалась.",
    )
    return RecentConversationContext(
        schema_version=1,
        turns=(turn,),
        content_chars=len(turn.user_content) + len(turn.assistant_content),
        excluded_turn_count=0,
    )


def _relationship(
    *,
    state_version: int,
    maturity: str,
    familiarity: str,
    trust: str,
    comfort: str,
) -> RelationshipExpressionContext:
    return RelationshipExpressionContext(
        schema_version=1,
        state_version=state_version,
        maturity=maturity,
        familiarity=familiarity,
        trust=trust,
        comfort=comfort,
        closeness="moderate",
        intellectual_respect="high",
        affection="moderate",
    )


def test_policy_v16_preserves_grounding_and_uses_owned_semantic_reaction() -> None:
    principles = {item.code: item.instruction for item in BEHAVIOR_POLICY_V16.principles}

    assert BEHAVIOR_POLICY_V16.policy_id == "satori.conversation.behavior.v16"
    assert BEHAVIOR_POLICY_V16.schema_version == 16
    assert "не предлагай даже правдоподобное значение" in principles["grounded_claims"]
    assert "supplied semantic move" in principles["natural_brevity"]
    assert "owned reaction" in principles["natural_brevity"]
    assert "общую эмпатию" in principles["natural_brevity"]
    assert "слегка колкая" in principles["independent_character"]
    assert "прятать заботу за наблюдением" in principles["independent_character"]
    assert "естественно от первого лица" in principles["affect_truth"]
    assert len(ResponseRegenerationReason) == 10


def test_policy_v17_keeps_character_durable_and_delivery_concrete() -> None:
    principles = {item.code: item.instruction for item in BEHAVIOR_POLICY_V17.principles}

    assert BEHAVIOR_POLICY_V17.policy_id == "satori.conversation.behavior.v17"
    assert BEHAVIOR_POLICY_V17.schema_version == 17
    assert "самостоятельная собеседница на равных" in principles["independent_character"]
    assert "сухая колкость иногда скрывает заботу" in principles["independent_character"]
    assert "supplied semantic move" not in principles["natural_brevity"]
    assert "owned reaction" not in principles["natural_brevity"]
    assert "поздравительной формулой" in principles["natural_brevity"]
    assert "не предлагай даже правдоподобное значение" in principles["grounded_claims"]
    assert len(ResponseRegenerationReason) == 10


def test_policy_v18_preserves_v17_principles_for_literal_projection() -> None:
    assert BEHAVIOR_POLICY_V18.policy_id == "satori.conversation.behavior.v18"
    assert BEHAVIOR_POLICY_V18.schema_version == 18
    assert BEHAVIOR_POLICY_V18.principles == BEHAVIOR_POLICY_V17.principles
    assert len(ResponseRegenerationReason) == 10


def test_policy_v19_allows_only_grounded_timely_practical_advice() -> None:
    principles = {item.code: item.instruction for item in BEHAVIOR_POLICY_V19.principles}

    assert BEHAVIOR_POLICY_V19.policy_id == "satori.conversation.behavior.v19"
    assert BEHAVIOR_POLICY_V19.schema_version == 19
    assert "дежурным советом" in principles["natural_brevity"]
    assert "явные данные текущего разговора" in principles["natural_brevity"]
    assert "не должен вытеснять реакцию на уязвимость" in principles["natural_brevity"]
    assert len(ResponseRegenerationReason) == 10


def test_canonical_completion_depletion_pair_selects_guarded_concern() -> None:
    builder, context = _builder()
    user_text = "Знаешь, я почему-то почти не рад этому. Скорее просто выжат"
    cognition = _cognition(user_text, suffix="completion-depletion")

    request, manifest = builder.build(
        context,
        user_text=user_text,
        trace_id="checkpoint142-humanity",
        cognition_trace=cognition,
        recent_context=_recent_completion(),
        relationship_context=_relationship(
            state_version=1,
            maturity="low",
            familiarity="low",
            trust="uncertain",
            comfort="uncertain",
        ),
    )
    reminder = request.messages[-2].content

    assert cognition.internal_position.stance is PositionStance.LISTEN
    assert "presence_before_advice" in cognition.response_strategy.point_codes
    trusted = _trusted_text(request)
    assert reminder.count("Финальная реализация характера Сатори") == 1
    assert reminder.index("Обязательный доверенный контракт") < reminder.index(
        "Финальная реализация характера Сатори"
    )
    assert "силы ушли на завершение" not in reminder
    assert "почти все силы ушли на результат" not in reminder
    assert "сдержанное беспокойство только в пределах явно сказанного" in reminder
    assert "Не добавляй шутку или сарказм" in reminder
    assert "заботу сдержанной" in reminder
    assert "Отношения свежие" in reminder
    assert "register=" not in trusted
    assert "owned_reaction=" not in trusted
    assert "semantic_move=" not in trusted
    assert "relational_ease=" not in trusted
    assert manifest.character_expression_plan_schema_version == 2
    assert manifest.character_owned_reaction == "sober_concern"
    assert manifest.character_semantic_move == "connect_explicit_contrast"
    assert manifest.character_wit == "none"
    assert manifest.character_care == "understated"
    assert manifest.character_openness == "balanced"
    assert manifest.character_initiative == "responsive"
    assert manifest.character_relational_ease == "fresh"
    assert request.parameters.temperature == 0.3
    assert request.parameters.max_output_tokens == 96


def test_completed_achievement_avoids_gendered_self_congratulation() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Привет. Я сегодня наконец закончил сложную часть проекта",
        trace_id="checkpoint142-achievement",
        relationship_context=_relationship(
            state_version=2,
            maturity="low",
            familiarity="low",
            trust="uncertain",
            comfort="uncertain",
        ),
    )
    reminder = request.messages[-2].content

    trusted = _trusted_text(request)
    assert reminder.count("Финальная реализация характера Сатори") == 1
    assert reminder.index("Обязательный доверенный контракт") < reminder.index(
        "Финальная реализация характера Сатори"
    )
    assert "Пусть одобрение читается за сухой реакцией" in reminder
    assert "явно завершённую работу или часть" in reminder
    assert "Значимость и трудность бери только из текущей реплики" in reminder
    assert "мягкий сухой штрих" in reminder
    assert "После краткого приветствия" not in reminder
    assert "сложная часть наконец уступила" not in reminder
    assert "register=" not in trusted
    assert "owned_reaction=" not in trusted
    assert "semantic_move=" not in trusted
    assert manifest.character_relational_ease == "fresh"
    assert manifest.character_wit == "situation_directed"
    assert manifest.character_care == "understated"
    assert manifest.character_openness == "balanced"
    assert manifest.character_initiative == "responsive"
    assert request.parameters.temperature == 0.3
    assert request.parameters.max_output_tokens == 80


def test_unrelated_listen_turn_does_not_receive_a_project_backstory() -> None:
    builder, context = _builder()
    user_text = "Мне правда страшно начинать. Я боюсь всё испортить"
    cognition = _cognition(user_text, suffix="unrelated-vulnerability")

    request, manifest = builder.build(
        context,
        user_text=user_text,
        trace_id="checkpoint142-unrelated-vulnerability",
        cognition_trace=cognition,
    )
    reminder = request.messages[-2].content
    trusted = _trusted_text(request)

    assert cognition.internal_position.stance is PositionStance.LISTEN
    assert "Вырази соразмерную заботу прямо" in reminder
    assert "Ответь на явно выраженную уязвимость" in reminder
    assert "сложная часть уже закончена" not in reminder
    assert "цена результата" not in reminder
    assert "register=" not in trusted
    assert "owned_reaction=" not in trusted
    assert manifest.character_semantic_move == "respond_to_explicit_vulnerability"
    assert manifest.character_wit == "none"
    assert manifest.character_care == "open"
    assert manifest.character_openness == "direct"
    assert manifest.character_initiative == "responsive"
    assert request.parameters.temperature == 0.3
    assert request.parameters.max_output_tokens == 96


@pytest.mark.parametrize(
    "user_text",
    [
        "Я не закончил проект",
        "Я так и не завершил проект",
        "Я ещё не закончил проект",
        "Если бы я закончил проект, я бы выдохнул",
        "Я не уверен, что закончил проект",
    ],
)
def test_negated_conditional_or_uncertain_completion_is_not_an_achievement(
    user_text: str,
) -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text=user_text,
        trace_id="checkpoint142-non-achievement",
    )
    trusted = _trusted_text(request)

    assert manifest.character_semantic_move != "mark_hard_won_result"
    assert "semantic_move=mark_hard_won_result" not in trusted
    assert "supplied mark_hard_won_result" not in request.messages[-2].content


@pytest.mark.parametrize("user_text", ["Я закончил проект", "Проект уже завершён"])
def test_explicit_completed_work_remains_a_positive_control(user_text: str) -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text=user_text,
        trace_id="checkpoint142-positive-achievement",
    )
    reminder = request.messages[-2].content

    assert manifest.character_semantic_move == "mark_hard_won_result"
    assert manifest.character_owned_reaction == "guarded_approval"
    assert "явно завершённую работу или часть" in reminder
    assert "Значимость и трудность бери только из текущей реплики" in reminder
    assert "завершённую трудную часть" not in reminder


def test_explicit_pending_commit_licenses_one_grounded_practical_step() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Я закончил работу над рефакторингом. Осталось закоммитить изменения",
        trace_id="checkpoint142-grounded-commit",
    )

    reminder = request.messages[-2].content
    assert manifest.character_semantic_move == "mark_hard_won_result"
    assert manifest.character_initiative == "concrete_next_step"
    assert "явно названным незавершённым практическим шагом" in reminder


@pytest.mark.parametrize(
    "user_text",
    [
        "Я закончил работу и уже закоммитил изменения",
        "Я закончил работу; коммитить изменения не нужно",
        "Я закончил сложную часть проекта",
        "Я уже закоммитил изменения, но осталось отдохнуть",
        "Я сохранил изменения, осталось только уйти",
        "Я решил не закоммитить изменения",
        "Если нужно, я уже прогнал тесты",
        "Пока не закоммитил изменения и не буду",
    ],
)
def test_practical_follow_through_is_not_licensed_without_an_explicit_pending_step(
    user_text: str,
) -> None:
    builder, context = _builder()
    _, manifest = builder.build(
        context,
        user_text=user_text,
        trace_id="checkpoint142-no-grounded-step",
    )

    assert manifest.character_initiative == "responsive"


def test_relationship_ordinals_include_very_high_and_keep_very_low_scoped() -> None:
    builder, context = _builder()
    established = _relationship(
        state_version=3,
        maturity="established",
        familiarity="very_high",
        trust="very_high",
        comfort="high",
    )
    damaged = _relationship(
        state_version=4,
        maturity="established",
        familiarity="high",
        trust="very_low",
        comfort="very_low",
    )

    _, established_manifest = builder.build(
        context,
        user_text="Продолжим обсуждение архитектуры",
        trace_id="checkpoint142-established-very-high",
        relationship_context=established,
    )
    _, damaged_relational_manifest = builder.build(
        context,
        user_text="Ты мне доверяешь?",
        trace_id="checkpoint142-damaged-relational",
        relationship_context=damaged,
    )
    _, damaged_technical_manifest = builder.build(
        context,
        user_text="Как работает транзакция?",
        trace_id="checkpoint142-damaged-technical",
        relationship_context=damaged,
    )

    assert established_manifest.relationship_expression_profile == "established_positive"
    assert established_manifest.character_relational_ease == "established"
    assert (
        damaged_relational_manifest.relationship_expression_profile
        == "guarded_only_when_relationally_relevant"
    )
    assert damaged_relational_manifest.character_relational_ease == "guarded"
    assert damaged_technical_manifest.character_relational_ease == "baseline"


def test_no_relevant_memory_is_current_turn_uncertainty_not_global_amnesia() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Ты помнишь, как звали моего первого питомца?",
        trace_id="checkpoint142-no-memory",
        memory_context=RetrievedMemoryContext(1, RetrievalStatus.NO_RELEVANT_MEMORY),
    )
    trusted = _trusted_text(request)

    assert manifest.policy_schema_version == 19
    assert manifest.retrieval_status == "no_relevant_memory"
    assert "did not recall a relevant grounded episode" in trusted
    assert "«не вспомнила»/«не помню»" in trusted
    assert "never «не нашла в памяти/контексте»" in trusted
    assert "say «был похожий разговор»" in trusted
    assert "provide no guessed value" in trusted


def test_unrelated_no_recall_stays_silent_about_memory() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Привет. Я сегодня наконец закончил сложную часть проекта",
        trace_id="checkpoint142-memory-irrelevant",
        memory_context=RetrievedMemoryContext(1, RetrievalStatus.NO_RELEVANT_MEMORY),
    )
    trusted = _trusted_text(request)

    assert manifest.policy_schema_version == 19
    assert "do not mention memory, remembering or forgetting" in trusted
    assert "«не вспомнила»/«не помню»" not in trusted
    assert "say «был похожий разговор»" not in trusted


def test_character_expression_corpus_is_versioned_unique_and_non_scripted() -> None:
    path = Path(__file__).parent / "fixtures" / "checkpoint142_character_expression_v2.json"
    corpus = json.loads(path.read_text(encoding="utf-8"))
    scenarios = corpus["scenarios"]
    identifiers = [item["id"] for item in scenarios]

    assert corpus["schema_version"] == 2
    assert corpus["corpus_id"] == "satori.checkpoint142.character-expression.ru.v2"
    assert len(scenarios) >= 14
    assert len(identifiers) == len(set(identifiers))
    assert all(item["turns"] and item["review_dimensions"] for item in scenarios)
    forbidden_script_keys = {
        "required_reply",
        "required_response",
        "desired_response",
        "assistant_text",
        "exact_text",
        "required_phrase",
        "golden_reply",
    }

    def _keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {key for item in value.values() for key in _keys(item)}
        if isinstance(value, list):
            return {key for item in value for key in _keys(item)}
        return set()

    assert not (forbidden_script_keys & _keys(corpus))


def test_retrieval_outage_is_not_rendered_as_empty_memory_or_permission_to_guess() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Ты помнишь, где мы впервые обсуждали Aurora?",
        trace_id="checkpoint142-memory-unavailable",
        memory_context=RetrievedMemoryContext(
            1,
            RetrievalStatus.UNAVAILABLE,
            failure_kind="ProviderUnavailable",
        ),
    )
    trusted = _trusted_text(request)

    assert manifest.retrieval_status == "unavailable"
    assert "Do not describe an internal search or outage" in trusted
    assert "do not present this as proven forgetting" in trusted
    assert "cannot now answer confidently from memory" in trusted
    assert "invent no replacement detail" in trusted


def test_general_turn_requires_a_specific_response_instead_of_generic_help_offer() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text=(
            "Я собираю офлайн-прототип Aurora и больше всего сомневаюсь в сложности первого "
            "запуска."
        ),
        trace_id="checkpoint142-specificity",
    )
    reminder = request.messages[-2].content
    trusted = _trusted_text(request)

    assert manifest.disclosure_primary_mode == "general"
    assert "конкретную деталь из текущей реплики" in reminder
    assert "наблюдение или выполнимый следующий шаг" in reminder
    assert "предложение своей помощи не считаются содержательным ответом" in reminder
    assert "могу помочь" in trusted


def test_direct_affect_uses_natural_first_person_without_internal_state_dump() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Что ты сейчас чувствуешь?",
        trace_id="checkpoint142-natural-affect",
        emotional_context=_neutral_affect(),
    )
    reminder = request.messages[-2].content
    emotional_projection = next(
        message.content
        for message in request.messages
        if "Trusted projection of current digital affect" in message.content
    )

    assert manifest.disclosure_primary_mode == "emotion"
    assert manifest.affect_expression_profile == "calm_even"
    assert "естественным предложением от первого лица" in reminder
    assert "ты спокойна и настроена ровно" in reminder
    assert "не называй state/profile" in reminder
    assert "Верни дословно одно предложение" not in reminder
    assert "express it naturally in first person" in emotional_projection
    assert "Do not recite expression_hint" in emotional_projection
    assert "Do not copy the tone adjective list" in emotional_projection
