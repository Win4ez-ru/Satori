"""Offline production-wire regressions for the Checkpoint 14.2 v25 candidate."""

# ruff: noqa: RUF001  # Exact Russian production phrases are intentional.

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from satori.application.affect.contracts import (
    EmotionalExpressionContext,
    EmotionAppraisalStatus,
)
from satori.application.cognition.contracts import (
    CognitionDialogueSignals,
    CognitionPipelineTrace,
    PerceptionSignal,
)
from satori.application.cognition.use_cases import DeterministicCognitionPlanner
from satori.application.conversation.character_evidence import (
    analyze_character_request_evidence,
)
from satori.application.conversation.coherence import analyze_dialogue_coherence
from satori.application.conversation.context import (
    CharacterContextComposer,
    ConversationRequestBuilder,
    plan_conversational_disclosure,
)
from satori.application.conversation.contracts import (
    ConversationContextManifest,
    RecentConversationContext,
    RecentConversationTurn,
    RuntimeCharacterContext,
)
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosureMode,
    ConversationalDisclosurePlan,
    DisclosureFacet,
    DisclosureRequestKind,
    is_satori_self_disclosure_plan,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V24, BEHAVIOR_POLICY_V25
from satori.application.positions.contracts import SatoriInclinationsContext
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.core.conversation import ConversationProviderRequest
from satori.domain.affect import FastAffectiveState, MoodState
from satori.domain.initial_self import activate_from_seed
from satori.infrastructure.seeds.loader import JsonSeedLoader

_GREETING = "приветик, как ты?"
_RECIPROCAL = "и я тебя рад видеть"
_SELF_DISCLOSURE = "слушай, а расскажи о себе, кто ты, чем увлекаешься, как себя чувствуешь вообще"
_ACHIEVEMENT = "Привет. Я сегодня наконец закончил сложную часть проекта"
_DEPLETION = "Знаешь, я почему-то почти не рад этому. Скорее просто выжат"
_STOP = "Да, пожалуй, на сегодня хватит. Проект подождёт до завтра"
_DIRECTOR = "Единая request-local режиссура реплики Сатори"


def _runtime_context() -> RuntimeCharacterContext:
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="checkpoint142-v25-production-wire",
        activation_time=datetime(2026, 8, 28, tzinfo=UTC),
    )
    return CharacterContextComposer("openai", "gpt-5.6-terra").compose(
        snapshot,
        emotional_state_available=True,
        relationship_state_available=True,
        recent_conversation_available=True,
    )


def _relationship() -> RelationshipExpressionContext:
    return RelationshipExpressionContext(
        schema_version=1,
        state_version=2,
        maturity="developing",
        familiarity="moderate",
        trust="moderate",
        comfort="moderate",
        closeness="moderate",
        intellectual_respect="high",
        affection="moderate",
    )


def _affect() -> EmotionalExpressionContext:
    return EmotionalExpressionContext(
        schema_version=1,
        state_version=1,
        mood_version=1,
        as_of=datetime(2026, 8, 28, tzinfo=UTC),
        fast=FastAffectiveState(0.0, 0.2, 0.1, 0.3, 0.3, 0.1, 0.1, 0.0, 0.1),
        mood=MoodState(0.0, 0.2, 0.1),
        appraisal_status=EmotionAppraisalStatus.APPLIED,
    )


def _recent(*turns: tuple[str, str]) -> RecentConversationContext:
    projected = tuple(
        RecentConversationTurn(
            interaction_id=f"v25-recent-interaction-{index}",
            user_message_id=f"v25-recent-user-{index}",
            user_content=user,
            assistant_message_id=f"v25-recent-assistant-{index}",
            assistant_content=assistant,
        )
        for index, (user, assistant) in enumerate(turns, start=1)
    )
    return RecentConversationContext(
        schema_version=1,
        turns=projected,
        content_chars=sum(
            len(turn.user_content) + len(turn.assistant_content) for turn in projected
        ),
        excluded_turn_count=0,
    )


def _cognition(
    user_text: str,
    recent: RecentConversationContext | None,
    *,
    policy_schema_version: int = 25,
) -> CognitionPipelineTrace:
    coherence = analyze_dialogue_coherence(user_text, recent)
    evidence = analyze_character_request_evidence(user_text, recent)
    disclosure = plan_conversational_disclosure(
        user_text,
        coherence,
        policy_schema_version=policy_schema_version,
    )
    planner = DeterministicCognitionPlanner(intent_registry_version=2)
    intake = planner.prepare_intake(
        user_text=user_text,
        user_message_id="v25-cognition-message",
        interaction_id="v25-cognition-interaction",
        dialogue=CognitionDialogueSignals(
            repeated_turn=coherence.current_user_message_repeated,
            correction_active=any(
                (
                    coherence.current_no_routine_questions_correction,
                    coherence.current_informal_correction,
                    coherence.current_repetition_feedback,
                    coherence.current_relevance_feedback,
                    coherence.current_frustration_feedback,
                    coherence.current_contradiction_feedback,
                )
            ),
            no_routine_questions=coherence.active_no_routine_questions_correction,
            current_activity=coherence.current_activity_mention,
            explicit_listen_request=evidence.explicit_listen_request,
            high_distress=evidence.high_distress,
            harmful_overextension=evidence.harmful_overextension,
            explicit_motivation_request=evidence.explicit_motivation_request,
            explicit_task_abandonment=evidence.explicit_task_abandonment,
            explicit_repair_offer=evidence.explicit_repair_offer,
            self_disclosure_request=is_satori_self_disclosure_plan(disclosure),
        ),
    )
    return planner.complete(
        intake,
        interaction_id="v25-cognition-interaction",
        available_evidence_ids=(),
        prepared_affect=None,
    )


def _build(
    user_text: str,
    *,
    recent: RecentConversationContext | None = None,
) -> tuple[ConversationProviderRequest, ConversationContextManifest]:
    return ConversationRequestBuilder(BEHAVIOR_POLICY_V25, 12_000, 0.3, 768).build(
        _runtime_context(),
        user_text=user_text,
        trace_id="checkpoint142-v25-wire",
        relationship_context=_relationship(),
        emotional_context=_affect(),
        inclination_context=SatoriInclinationsContext(1, "empty", (), 0.0),
        recent_context=recent,
        cognition_trace=_cognition(user_text, recent),
    )


def test_v25_exact_manual_chat_inputs_have_complete_typed_disclosure() -> None:
    greeting = plan_conversational_disclosure(_GREETING)
    reciprocal = plan_conversational_disclosure(_RECIPROCAL)
    self_disclosure = plan_conversational_disclosure(_SELF_DISCLOSURE)

    assert (greeting.primary_mode, greeting.required_facets) == (
        ConversationalDisclosureMode.SOCIAL,
        (DisclosureFacet.AFFECT,),
    )
    assert (reciprocal.primary_mode, reciprocal.required_facets) == (
        ConversationalDisclosureMode.SOCIAL,
        (),
    )
    assert self_disclosure.primary_mode is ConversationalDisclosureMode.PERSONAL_IDENTITY
    assert set(self_disclosure.required_facets) == {
        DisclosureFacet.IDENTITY,
        DisclosureFacet.AFFECT,
        DisclosureFacet.INTERESTS,
    }


def test_v25_inverted_memory_question_keeps_the_full_self_disclosure_wire() -> None:
    user_text = "Помнишь ли ты мой первый запуск?"
    plan = plan_conversational_disclosure(user_text, policy_schema_version=25)
    request, manifest = _build(user_text)

    assert plan == ConversationalDisclosurePlan(
        primary_mode=ConversationalDisclosureMode.MEMORY,
        required_facets=(DisclosureFacet.MEMORY,),
        request_kind=DisclosureRequestKind.SATORI_SELF,
    )
    assert PerceptionSignal.SELF_DISCLOSURE_REQUEST.value in (manifest.cognition_perception_signals)
    assert manifest.cognition_position_stance == "answer"
    assert manifest.character_delivery_goal == "answer_precisely"
    assert manifest.character_delivery_grounding == "trusted_context"
    self_consistency_messages = tuple(
        message
        for message in request.messages
        if message.content.startswith("Trusted self-consistency DATA for this turn")
    )
    assert len(self_consistency_messages) == 1
    assert '"memory":' in self_consistency_messages[0].content


def test_v25_exact_manual_chat_routes_social_and_self_disclosure_without_service_modes() -> None:
    first_request, first = _build(_GREETING)
    first_recent = _recent((_GREETING, "Привет. Я сегодня спокойна."))
    second_request, second = _build(_RECIPROCAL, recent=first_recent)
    second_recent = _recent(
        (_GREETING, "Привет. Я сегодня спокойна."),
        (_RECIPROCAL, "Ещё бы. Но это приятно слышать."),
    )
    third_request, third = _build(_SELF_DISCLOSURE, recent=second_recent)

    assert (
        first.cognition_position_stance,
        first.character_delivery_decision_schema_version,
        first.character_delivery_goal,
        first.character_delivery_voice,
        first.disclosure_facets,
    ) == ("answer", 2, "social_connect", "lively_dry_warmth", ("affect",))
    assert (
        second.cognition_position_stance,
        second.character_delivery_goal,
        second.character_delivery_voice,
    ) == ("answer", "social_connect", "lively_dry_warmth")
    assert (
        third.cognition_position_stance,
        third.character_delivery_goal,
        third.character_delivery_voice,
    ) == ("answer", "self_disclose", "warm_independence")
    assert PerceptionSignal.SELF_DISCLOSURE_REQUEST.value in third.cognition_perception_signals
    assert set(third.disclosure_facets) == {"identity", "affect", "interests"}
    assert third_request.parameters.max_output_tokens == 160
    assert all(
        request.messages[-2].content.count(_DIRECTOR) == 1
        for request in (first_request, second_request, third_request)
    )
    assert "вежливый шаблон ассистента" in first_request.messages[-2].content
    assert "отдельный абстрактный афоризм" in second_request.messages[-2].content
    assert "одной личной связной дуге" in third_request.messages[-2].content


def test_v25_relationship_feeling_question_is_not_misread_as_user_distress() -> None:
    request, manifest = _build("Что ты ко мне чувствуешь?")

    assert manifest.disclosure_primary_mode == "relationship_current"
    assert PerceptionSignal.SELF_DISCLOSURE_REQUEST.value in manifest.cognition_perception_signals
    assert manifest.cognition_position_stance == "answer"
    assert manifest.character_delivery_goal == "owned_response"
    assert "Останься с прямо выраженным переживанием" not in request.messages[-2].content


@pytest.mark.parametrize(
    "user_text",
    [
        "У меня что-то случилось",
        "Со мной что-то случилось",
        "У меня есть эмоции, и я в них запутался",
    ],
)
def test_v25_user_affect_is_never_promoted_to_satori_self_disclosure(
    user_text: str,
) -> None:
    plan = plan_conversational_disclosure(user_text, policy_schema_version=25)
    _, manifest = _build(user_text)

    assert plan.primary_mode is ConversationalDisclosureMode.GENERAL
    assert plan.required_facets == ()
    assert is_satori_self_disclosure_plan(plan) is False
    assert PerceptionSignal.SELF_DISCLOSURE_REQUEST.value not in (
        manifest.cognition_perception_signals
    )
    assert manifest.character_delivery_goal != "self_disclose"


@pytest.mark.parametrize(
    "user_text",
    [
        "Неважно, что ты чувствуешь — мне плохо.",
        "Речь не о том, что ты чувствуешь. Мне плохо.",
        "Важно не то, что ты чувствуешь, а что чувствую я.",
        "Не у тебя что-то случилось, а у меня.",
        "Это не твои эмоции, а мои.",
        "Неважно, есть ли у тебя эмоции; у меня проблема.",
        "Я не хочу знать, есть ли у тебя эмоции. Мне плохо.",
        "Я не спрашиваю, есть ли у тебя эмоции, мне плохо.",
        "Фраза «что ты чувствуешь?» — просто пример; мне плохо.",
        "Фраза „что ты чувствуешь?“ — просто пример; мне плохо.",
        "Фраза 'что ты чувствуешь?' — просто пример; мне плохо.",
        "Если бы я спросил, что ты чувствуешь, это было бы о тебе; но сейчас мне плохо.",
        "Вопрос был: что ты чувствуешь? Но сейчас мне плохо.",
        "Не имеет значения, что ты чувствуешь; мне плохо.",
        "Что ты чувствуешь — не имеет значения; мне плохо.",
        "Что ты чувствуешь? Это неважно, мне плохо.",
        "Не про то, что ты чувствуешь; мне плохо.",
        "Цитата: что ты чувствуешь? Но сейчас мне плохо.",
        "`Что ты чувствуешь?` Но сейчас мне плохо.",
        "Предположим, что ты чувствуешь грусть; но сейчас мне плохо.",
        "Я не говорю, что ты чувствуешь; я говорю, что чувствую я.",
    ],
)
def test_v25_dismissed_satori_affect_clause_keeps_user_distress_primary(user_text: str) -> None:
    plan = plan_conversational_disclosure(user_text, policy_schema_version=25)
    _, manifest = _build(user_text)

    assert plan == ConversationalDisclosurePlan(
        primary_mode=ConversationalDisclosureMode.GENERAL,
        required_facets=(),
    )
    assert PerceptionSignal.SELF_DISCLOSURE_REQUEST.value not in (
        manifest.cognition_perception_signals
    )
    assert manifest.cognition_position_stance == "listen"
    assert manifest.character_delivery_goal == "stay_present"


@pytest.mark.parametrize(
    "user_text",
    [
        "Не твои интересы, а мои сейчас важны.",
        "Твои предпочтения тут ни при чем, речь о моих.",
        "Я не спрашиваю, что тебе нравится; рассказываю, что нравится мне.",
        "Неважно, кто ты; мне плохо.",
        "Речь не о том, кто ты, а о том, кто я.",
        "Неважно, есть ли у тебя сознание; я говорю о своем.",
        "Неважно, как ты устроена; мне нужна помощь с моей архитектурой.",
        "Неважно, любишь ли ты меня; я говорю о своих чувствах.",
        "Память у тебя сейчас не важна, речь о моей памяти.",
        "Тело у тебя ни при чем, я говорю о своем теле.",
        "Я лишь цитирую «у тебя есть память?», а говорю о своей.",
        "Он спросил тебя, что ты чувствуешь; а я рассказываю о себе.",
        "Она спросила тебя, есть ли у тебя память; мне нужна помощь с моей.",
        "Я бы спросил, есть ли у тебя память, но сейчас говорю о своей.",
        "Раньше я спрашивал, есть ли у тебя память, но сейчас говорю о своей.",
        "Он спросил тебя: помнишь ли ты первый запуск; а я говорю о своей памяти.",
        "Что ты чувствуешь меня не интересует; я говорю о себе.",
    ],
)
def test_v25_all_dismissed_self_topics_share_one_safe_subject_scope(user_text: str) -> None:
    plan = plan_conversational_disclosure(user_text, policy_schema_version=25)
    _, manifest = _build(user_text)

    assert plan.primary_mode is ConversationalDisclosureMode.GENERAL
    assert plan.required_facets == ()
    assert plan.request_kind is DisclosureRequestKind.NONE
    assert PerceptionSignal.SELF_DISCLOSURE_REQUEST.value not in (
        manifest.cognition_perception_signals
    )


def test_v25_negated_predicate_is_still_a_real_satori_self_question() -> None:
    plan = plan_conversational_disclosure(
        "У тебя нет эмоций?",
        policy_schema_version=25,
    )

    assert plan == ConversationalDisclosurePlan(
        primary_mode=ConversationalDisclosureMode.EMOTION,
        required_facets=(DisclosureFacet.AFFECT,),
        request_kind=DisclosureRequestKind.SATORI_SELF,
    )


def test_v25_second_person_affect_and_greeting_with_substantive_request_keep_subjects() -> None:
    satori_affect = plan_conversational_disclosure(
        "С тобой что-то случилось?",
        policy_schema_version=25,
    )
    substantive = plan_conversational_disclosure(
        "Привет, можешь объяснить этот план?",
        policy_schema_version=25,
    )
    _, substantive_manifest = _build("Привет, можешь объяснить этот план?")

    assert satori_affect == ConversationalDisclosurePlan(
        primary_mode=ConversationalDisclosureMode.EMOTION,
        required_facets=(DisclosureFacet.AFFECT,),
        request_kind=DisclosureRequestKind.SATORI_SELF,
    )
    assert is_satori_self_disclosure_plan(satori_affect) is True
    assert substantive.primary_mode is ConversationalDisclosureMode.GENERAL
    assert substantive.required_facets == ()
    assert substantive_manifest.character_delivery_goal == "answer_precisely"


@pytest.mark.parametrize(
    ("user_text", "primary_mode", "expected_facets"),
    [
        (
            "Кто ты, у тебя есть память, эмоции и физическое тело?",
            ConversationalDisclosureMode.PERSONAL_IDENTITY,
            (
                DisclosureFacet.IDENTITY,
                DisclosureFacet.MEMORY,
                DisclosureFacet.AFFECT,
                DisclosureFacet.EMBODIMENT,
            ),
        ),
        (
            "У тебя есть память, эмоции и физическое тело?",
            ConversationalDisclosureMode.MEMORY,
            (
                DisclosureFacet.MEMORY,
                DisclosureFacet.AFFECT,
                DisclosureFacet.EMBODIMENT,
            ),
        ),
        (
            "А тело, память и эмоции у тебя есть?",
            ConversationalDisclosureMode.MEMORY,
            (
                DisclosureFacet.MEMORY,
                DisclosureFacet.AFFECT,
                DisclosureFacet.EMBODIMENT,
            ),
        ),
        (
            "Тело, память, эмоции — у тебя всё это есть?",
            ConversationalDisclosureMode.MEMORY,
            (
                DisclosureFacet.MEMORY,
                DisclosureFacet.AFFECT,
                DisclosureFacet.EMBODIMENT,
            ),
        ),
        (
            "Есть ли у тебя тело? Память? Эмоции?",
            ConversationalDisclosureMode.MEMORY,
            (
                DisclosureFacet.MEMORY,
                DisclosureFacet.AFFECT,
                DisclosureFacet.EMBODIMENT,
            ),
        ),
        (
            "Есть ли эмоции, память и тело у тебя?",
            ConversationalDisclosureMode.MEMORY,
            (
                DisclosureFacet.MEMORY,
                DisclosureFacet.AFFECT,
                DisclosureFacet.EMBODIMENT,
            ),
        ),
        (
            "Расскажи, есть ли у тебя тело, память, эмоции и интересы?",
            ConversationalDisclosureMode.MEMORY,
            (
                DisclosureFacet.MEMORY,
                DisclosureFacet.AFFECT,
                DisclosureFacet.INTERESTS,
                DisclosureFacet.EMBODIMENT,
            ),
        ),
    ],
)
def test_v25_compound_self_question_keeps_all_facets_through_full_wire(
    user_text: str,
    primary_mode: ConversationalDisclosureMode,
    expected_facets: tuple[DisclosureFacet, ...],
) -> None:
    plan = plan_conversational_disclosure(user_text, policy_schema_version=25)
    request, manifest = _build(user_text)

    assert plan.primary_mode is primary_mode
    assert plan.required_facets == expected_facets
    assert plan.request_kind is DisclosureRequestKind.SATORI_SELF
    assert PerceptionSignal.SELF_DISCLOSURE_REQUEST.value in manifest.cognition_perception_signals
    assert manifest.cognition_position_stance == "answer"
    assert manifest.character_delivery_goal == "self_disclose"
    assert manifest.character_delivery_grounding == "trusted_context"
    assert manifest.disclosure_facets == tuple(facet.value for facet in expected_facets)
    director = request.messages[-2].content
    for facet in expected_facets:
        assert facet.value in director


def test_v25_interest_request_and_activity_relevance_correction_have_distinct_scope() -> None:
    direct = plan_conversational_disclosure(
        "Что тебе самой интересно?",
        policy_schema_version=25,
    )
    correction_text = "Тебе не интересно, что за фильм?"
    correction_context = analyze_dialogue_coherence(correction_text, None)
    inferred_correction = plan_conversational_disclosure(
        correction_text,
        policy_schema_version=25,
    )
    explicit_correction = plan_conversational_disclosure(
        correction_text,
        correction_context,
        policy_schema_version=25,
    )
    _, direct_manifest = _build("Что тебе самой интересно?")
    direct_negative = plan_conversational_disclosure(
        "Тебе не интересно искусство?",
        policy_schema_version=25,
    )
    _, direct_negative_manifest = _build("Тебе не интересно искусство?")
    _, correction_manifest = _build(correction_text)

    assert direct == ConversationalDisclosurePlan(
        primary_mode=ConversationalDisclosureMode.INTERESTS,
        required_facets=(DisclosureFacet.INTERESTS,),
        request_kind=DisclosureRequestKind.SATORI_SELF,
    )
    assert direct_manifest.cognition_position_stance == "answer"
    assert direct_manifest.character_delivery_goal == "self_disclose"
    assert direct_negative == ConversationalDisclosurePlan(
        primary_mode=ConversationalDisclosureMode.INTERESTS,
        required_facets=(DisclosureFacet.INTERESTS,),
        request_kind=DisclosureRequestKind.SATORI_SELF,
    )
    assert direct_negative_manifest.cognition_position_stance == "answer"
    assert direct_negative_manifest.character_delivery_goal == "self_disclose"
    assert (
        inferred_correction
        == explicit_correction
        == ConversationalDisclosurePlan(
            primary_mode=ConversationalDisclosureMode.STYLE_CALIBRATION,
            required_facets=(DisclosureFacet.EMBODIMENT,),
        )
    )
    assert PerceptionSignal.SELF_DISCLOSURE_REQUEST.value not in (
        correction_manifest.cognition_perception_signals
    )
    assert correction_manifest.cognition_position_stance == "acknowledge"
    assert correction_manifest.character_delivery_goal == "own_and_repair"


@pytest.mark.parametrize("modifier", ["вообще", "совсем", "совершенно"])
def test_v25_interest_subject_scope_preserves_bounded_adverb_variants(modifier: str) -> None:
    direct_text = f"Тебе {modifier} не интересно искусство?"
    correction_text = f"Тебе {modifier} не интересно, что за фильм?"
    direct = plan_conversational_disclosure(direct_text, policy_schema_version=25)
    correction = plan_conversational_disclosure(correction_text, policy_schema_version=25)
    _, direct_manifest = _build(direct_text)
    _, correction_manifest = _build(correction_text)

    assert direct == ConversationalDisclosurePlan(
        primary_mode=ConversationalDisclosureMode.INTERESTS,
        required_facets=(DisclosureFacet.INTERESTS,),
        request_kind=DisclosureRequestKind.SATORI_SELF,
    )
    assert direct_manifest.character_delivery_goal == "self_disclose"
    assert correction == ConversationalDisclosurePlan(
        primary_mode=ConversationalDisclosureMode.STYLE_CALIBRATION,
        required_facets=(DisclosureFacet.EMBODIMENT,),
    )
    assert correction_manifest.character_delivery_goal == "own_and_repair"


@pytest.mark.parametrize(
    "user_text",
    [
        "Я тебя люблю.",
        "Люблю тебя, но мне сейчас плохо.",
    ],
)
def test_v25_user_relationship_declaration_is_not_a_satori_self_request(user_text: str) -> None:
    plan = plan_conversational_disclosure(user_text, policy_schema_version=25)
    _, manifest = _build(user_text)

    assert plan.primary_mode is ConversationalDisclosureMode.RELATIONSHIP_CURRENT
    assert plan.request_kind is DisclosureRequestKind.NONE
    assert is_satori_self_disclosure_plan(plan) is False
    assert PerceptionSignal.SELF_DISCLOSURE_REQUEST.value not in (
        manifest.cognition_perception_signals
    )


def test_v25_relationship_question_remains_a_direct_satori_self_request() -> None:
    plan = plan_conversational_disclosure(
        "Что ты ко мне чувствуешь?",
        policy_schema_version=25,
    )

    assert plan.request_kind is DisclosureRequestKind.SATORI_SELF
    assert is_satori_self_disclosure_plan(plan) is True


def test_v24_reciprocal_and_broad_self_request_remain_historically_stable() -> None:
    reciprocal_trace = _cognition(_RECIPROCAL, None, policy_schema_version=24)
    reciprocal_request, reciprocal = ConversationRequestBuilder(
        BEHAVIOR_POLICY_V24,
        12_000,
        0.3,
        768,
    ).build(
        _runtime_context(),
        user_text=_RECIPROCAL,
        trace_id="checkpoint142-v24-reciprocal-stability",
        relationship_context=_relationship(),
        emotional_context=_affect(),
        cognition_trace=reciprocal_trace,
    )
    broad = plan_conversational_disclosure(
        _SELF_DISCLOSURE,
        policy_schema_version=24,
    )

    assert reciprocal.disclosure_primary_mode == "general"
    assert reciprocal.disclosure_facets == ()
    assert reciprocal.character_delivery_goal == "owned_response"
    assert reciprocal_request.parameters.max_output_tokens == 384
    assert PerceptionSignal.SELF_DISCLOSURE_REQUEST.value not in (
        reciprocal.cognition_perception_signals
    )
    assert broad.primary_mode is ConversationalDisclosureMode.PERSONAL_IDENTITY
    assert broad.required_facets == (DisclosureFacet.IDENTITY, DisclosureFacet.AFFECT)


def test_v24_rejects_a_forged_v25_self_disclosure_signal() -> None:
    forged_v25_trace = _cognition(_SELF_DISCLOSURE, None, policy_schema_version=25)

    with pytest.raises(ValueError, match="cognition/evidence signal parity"):
        ConversationRequestBuilder(BEHAVIOR_POLICY_V24, 12_000, 0.3, 768).build(
            _runtime_context(),
            user_text=_SELF_DISCLOSURE,
            trace_id="checkpoint142-v24-forged-v25-signal",
            relationship_context=_relationship(),
            emotional_context=_affect(),
            cognition_trace=forged_v25_trace,
        )


def test_v25_disclosure_and_manifest_contracts_reject_open_or_incomplete_codes() -> None:
    with pytest.raises(ValueError, match="primary_mode must be typed"):
        ConversationalDisclosurePlan(
            primary_mode="emotion",  # type: ignore[arg-type]
            required_facets=(DisclosureFacet.AFFECT,),
        )
    with pytest.raises(ValueError, match="unique typed values"):
        ConversationalDisclosurePlan(
            primary_mode=ConversationalDisclosureMode.EMOTION,
            required_facets=("affect",),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="authoritative facets"):
        ConversationalDisclosurePlan(
            primary_mode=ConversationalDisclosureMode.PERSONAL_IDENTITY,
            required_facets=(DisclosureFacet.AFFECT,),
        )
    with pytest.raises(ValueError, match="optional affect facet"):
        ConversationalDisclosurePlan(
            primary_mode=ConversationalDisclosureMode.SOCIAL,
            required_facets=(DisclosureFacet.IDENTITY,),
        )
    with pytest.raises(ValueError, match="requires behavior policy v25"):
        ConversationalDisclosurePlan(
            primary_mode=ConversationalDisclosureMode.INTERESTS,
            required_facets=(DisclosureFacet.INTERESTS,),
            policy_schema_version=24,
        )

    _, manifest = _build(_SELF_DISCLOSURE)
    with pytest.raises(ValueError, match="disclosure mode is not supported"):
        replace(manifest, disclosure_primary_mode="open-text")
    with pytest.raises(ValueError, match="unique closed codes"):
        replace(manifest, disclosure_facets=("open-text",))
    with pytest.raises(ValueError, match="authoritative facets"):
        replace(manifest, disclosure_facets=())
    with pytest.raises(ValueError, match="exact parity"):
        replace(
            manifest,
            cognition_perception_signals=tuple(
                signal
                for signal in manifest.cognition_perception_signals
                if signal != PerceptionSignal.SELF_DISCLOSURE_REQUEST.value
            ),
        )
    with pytest.raises(ValueError, match="answer-bound personal disclosure"):
        replace(
            manifest,
            disclosure_primary_mode=ConversationalDisclosureMode.MEMORY.value,
            disclosure_facets=(DisclosureFacet.MEMORY.value,),
        )
    with pytest.raises(ValueError, match="voice is not licensed"):
        replace(manifest, character_delivery_voice="cool_reserve")
    with pytest.raises(ValueError, match="licensed personal delivery"):
        replace(
            manifest,
            character_delivery_goal="answer_precisely",
            character_delivery_voice="thoughtful_precision",
        )


def test_v25_full_core_emotional_wire_has_distinct_non_therapeutic_arcs() -> None:
    achievement_request, achievement = _build(_ACHIEVEMENT)
    after_achievement = _recent((_ACHIEVEMENT, "Ну наконец-то. Что теперь?"))
    depletion_request, depletion = _build(_DEPLETION, recent=after_achievement)
    after_depletion = _recent(
        (_ACHIEVEMENT, "Ну наконец-то. Что теперь?"),
        (_DEPLETION, "Тогда передохни немного. И не смотри так."),
    )
    stop_request, stop = _build(_STOP, recent=after_depletion)

    assert (achievement.character_delivery_goal, achievement.character_delivery_pressure) == (
        "celebrate_and_continue",
        "none",
    )
    assert (depletion.character_delivery_goal, depletion.character_delivery_pressure) == (
        "practical_care",
        "gentle",
    )
    assert (stop.character_delivery_goal, stop.character_delivery_pressure) == (
        "practical_care",
        "none",
    )
    achievement_director = achievement_request.messages[-2].content
    depletion_director = depletion_request.messages[-2].content
    stop_director = stop_request.messages[-2].content
    assert "Сначала коротко и живо" not in achievement_director
    assert "Сначала покажи личную реакцию" not in depletion_director
    assert "Затем можно предложить ровно один" not in depletion_director
    assert "не нормализуй и не диагностируй" in depletion_director
    assert "уже прямо решил остановиться" in stop_director


def test_v24_wire_remains_on_delivery_v1_and_cognition_template_v2() -> None:
    recent = None
    trace = _cognition(_ACHIEVEMENT, recent, policy_schema_version=24)
    request, manifest = ConversationRequestBuilder(BEHAVIOR_POLICY_V24, 12_000, 0.3, 768).build(
        _runtime_context(),
        user_text=_ACHIEVEMENT,
        trace_id="checkpoint142-v24-stability",
        relationship_context=_relationship(),
        emotional_context=_affect(),
        recent_context=recent,
        cognition_trace=trace,
    )

    assert manifest.character_delivery_decision_schema_version == 1
    assert manifest.cognition_template_registry_version == 2
    assert manifest.cognition_template_schema_version == 2
    assert manifest.character_delivery_goal == "celebrate_and_continue"
    assert "Сначала коротко и живо отреагируй от себя" in request.messages[-2].content
