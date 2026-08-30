"""Deterministic causal-presence regressions for Checkpoint 14.2 policy v26."""

# ruff: noqa: RUF001  # Russian provider guidance is intentional test evidence.

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from satori.application.affect.contracts import (
    EmotionalExpressionContext,
    EmotionAppraisalStatus,
)
from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V2,
    CognitionArtifactStatus,
    CognitionDialogueSignals,
    CognitionPipelineTrace,
    IntentSelection,
    PositionStance,
    ResponseStrategy,
    ResponseTone,
    ResponseVerbosity,
)
from satori.application.cognition.templates import COGNITION_TEMPLATE_REGISTRY_V3
from satori.application.cognition.use_cases import DeterministicCognitionPlanner
from satori.application.conversation.character_delivery import (
    CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION,
    CHARACTER_DELIVERY_DECISION_V3_SCHEMA_VERSION,
    CharacterAffectSignal,
    CharacterAffectSignalCode,
    CharacterDeliveryDecision,
    CharacterPersonalitySignal,
    CharacterPresenceProjection,
    CharacterPresenceStrength,
    CharacterRelationshipSignal,
    CharacterRelationshipSignalCode,
    CharacterValueSignal,
    decide_character_delivery,
    project_character_affect_profile,
    project_character_presence,
    project_character_relationship_profile,
    render_character_delivery_director,
    render_character_presence,
)
from satori.application.conversation.character_evidence import (
    analyze_character_request_evidence,
)
from satori.application.conversation.character_expression import (
    BASELINE_CHARACTER_GUIDANCE_CODES,
)
from satori.application.conversation.coherence import analyze_dialogue_coherence
from satori.application.conversation.context import (
    CharacterContextComposer,
    ConversationRequestBuilder,
    plan_conversational_disclosure,
)
from satori.application.conversation.contracts import (
    RuntimeCharacterContext,
    RuntimePersonalityCue,
    RuntimeTrait,
    RuntimeValue,
)
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosureMode,
    DisclosureFacet,
    DisclosureRequestKind,
    is_satori_self_disclosure_plan,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V25, BEHAVIOR_POLICY_V26
from satori.application.conversation.response_validation import ResponseRegenerationReason
from satori.application.conversation.use_cases import TalkToSatori
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.application.retrieval.contracts import (
    RetrievalStatus,
    RetrievedMemory,
    RetrievedMemoryContext,
)
from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderRequest,
)
from satori.domain.affect import FastAffectiveState, MoodState
from satori.domain.initial_self import activate_from_seed
from satori.infrastructure.seeds.loader import JsonSeedLoader

_NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
_PRESENCE_MARKER = "Trusted current-turn presence Сатори"
_HISTORICAL_DIRECTOR_MARKER = "Единая request-local режиссура реплики Сатори"
_HISTORICAL_CORE_MARKER = "Цельная trusted-проекция характера Сатори"


def _runtime_context() -> RuntimeCharacterContext:
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="checkpoint142-v26-presence",
        activation_time=_NOW,
    )
    return CharacterContextComposer("openai", "gpt-5.6-terra").compose(
        snapshot,
        emotional_state_available=True,
        relationship_state_available=True,
    )


def _strategy() -> ResponseStrategy:
    return ResponseStrategy(
        schema_version=1,
        status=CognitionArtifactStatus.APPLIED,
        position_stance=PositionStance.ANSWER,
        preserve_uncertainty=False,
        tone=ResponseTone.WARM_DIRECT,
        verbosity=ResponseVerbosity.MEDIUM,
        humor=0.12,
        softness=0.58,
        point_codes=("answer_directly", "address_current_request"),
        must_not_claim=(
            "unsupported_memory",
            "hidden_user_state",
            "durable_satori_belief",
            "false_certainty",
        ),
        source_refs=("current-user-message",),
    )


def _intent(strategy: ResponseStrategy) -> IntentSelection:
    return IntentSelection(
        schema_version=1,
        registry_version=INTENT_REGISTRY_VERSION_V2,
        status=strategy.status,
        primary_tag="answer_directly",
        tags=("answer_directly", "preserve_evidence_boundary"),
        priority=0.76,
        source_refs=strategy.source_refs,
    )


def _decision(
    *,
    schema_version: int = CHARACTER_DELIVERY_DECISION_V3_SCHEMA_VERSION,
    affect_profile: str | None = None,
    relationship_profile: str | None = "developing_neutral",
    relationship_relevant: bool = False,
    interests: bool = False,
    completed_achievement: bool = False,
    retrieved_memory_available: bool = False,
) -> CharacterDeliveryDecision:
    strategy = _strategy()
    affect_profile = affect_profile or project_character_affect_profile(_affect())
    facets = (
        (DisclosureFacet.IDENTITY, DisclosureFacet.AFFECT, DisclosureFacet.INTERESTS)
        if interests
        else ()
    )
    return decide_character_delivery(
        strategy,
        intent=_intent(strategy),
        affect_profile=affect_profile,
        personality_codes=BASELINE_CHARACTER_GUIDANCE_CODES,
        relationship_profile=relationship_profile,
        relationship_relevant=relationship_relevant,
        explicit_request=True,
        answer_required=True,
        completed_achievement=completed_achievement,
        retrieved_memory_available=retrieved_memory_available,
        decision_schema_version=schema_version,
        disclosure_mode=(ConversationalDisclosureMode.PERSONAL_IDENTITY if interests else None),
        required_disclosure_facets=facets,
        disclosure_request_kind=(
            DisclosureRequestKind.SATORI_SELF if interests else DisclosureRequestKind.NONE
        ),
    )


def _projection(
    context: RuntimeCharacterContext,
    decision: CharacterDeliveryDecision,
    *,
    emotional_context: EmotionalExpressionContext | None = None,
    relationship_context: RelationshipExpressionContext | None = None,
    affect_profile: str | None = None,
    affect_relevant: bool = False,
    relationship_profile: str | None = "developing_neutral",
    relationship_relevant: bool = False,
    memory_use_licensed: bool = False,
    topic_inclination_available: bool = False,
) -> CharacterPresenceProjection:
    if emotional_context is None:
        emotional_context = _affect()
    if affect_profile is None:
        affect_profile = project_character_affect_profile(emotional_context)
    if relationship_profile is not None and relationship_context is None:
        relationship_context = _relationship()
    return project_character_presence(
        decision,
        personality_aggregate_version=context.personality_aggregate_version,
        personality=context.personality_expression,
        traits=context.traits,
        values=context.values,
        emotional_context=emotional_context,
        relationship_context=relationship_context,
        affect_profile=affect_profile,
        affect_relevant=affect_relevant,
        relationship_profile=relationship_profile,
        relationship_relevant=relationship_relevant,
        memory_use_licensed=memory_use_licensed,
        canonical_position_available=False,
        topic_inclination_available=topic_inclination_available,
    )


def _render(projection: CharacterPresenceProjection) -> str:
    return render_character_presence(
        projection,
        cognition_template=COGNITION_TEMPLATE_REGISTRY_V3.active,
    )


def _affect() -> EmotionalExpressionContext:
    return EmotionalExpressionContext(
        schema_version=1,
        state_version=1,
        mood_version=1,
        as_of=_NOW,
        fast=FastAffectiveState(0.0, 0.2, 0.1, 0.3, 0.3, 0.1, 0.1, 0.0, 0.1),
        mood=MoodState(0.0, 0.2, 0.1),
        appraisal_status=EmotionAppraisalStatus.APPLIED,
    )


def _relationship() -> RelationshipExpressionContext:
    return RelationshipExpressionContext(
        schema_version=2,
        state_version=2,
        maturity="developing",
        familiarity="moderate",
        trust="moderate",
        comfort="moderate",
        closeness="moderate",
        intellectual_respect="high",
        affection="moderate",
    )


def _cognition(user_text: str) -> CognitionPipelineTrace:
    coherence = analyze_dialogue_coherence(user_text, None)
    evidence = analyze_character_request_evidence(user_text, None)
    disclosure = plan_conversational_disclosure(
        user_text,
        coherence,
        policy_schema_version=26,
    )
    planner = DeterministicCognitionPlanner(intent_registry_version=2)
    intake = planner.prepare_intake(
        user_text=user_text,
        user_message_id="v26-presence-user",
        interaction_id="v26-presence-interaction",
        dialogue=CognitionDialogueSignals(
            repeated_turn=coherence.current_user_message_repeated,
            correction_active=False,
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
        interaction_id="v26-presence-interaction",
        available_evidence_ids=(),
        prepared_affect=None,
    )


def test_live_trait_and_value_strengths_change_the_presence_projection_and_rendering() -> None:
    context = _runtime_context()
    decision = _decision()
    baseline = _projection(context, decision)

    changed_personality = replace(
        context.personality_expression,
        guidance=tuple(
            replace(
                item,
                strength={
                    "curious_analytical": 0.34,
                    "considered_directness": 0.97,
                    "independent_position": 0.83,
                }.get(item.code, item.strength),
            )
            for item in context.personality_expression.guidance
        ),
    )
    trait_context = replace(context, personality_expression=changed_personality)
    trait_changed = _projection(trait_context, decision)

    value_context = replace(
        context,
        values=tuple(
            replace(
                item,
                strength={
                    "truth": 0.31,
                    "intellectual_honesty": 0.99,
                    "competence": 0.52,
                }.get(item.key, item.strength),
            )
            for item in context.values
        ),
    )
    value_changed = _projection(value_context, decision)

    assert trait_changed.personality_signals != baseline.personality_signals
    assert tuple(item.code for item in trait_changed.personality_signals) != tuple(
        item.code for item in baseline.personality_signals
    )
    assert _render(trait_changed) != _render(baseline)

    assert value_changed.value_signals != baseline.value_signals
    assert tuple(item.strength for item in value_changed.value_signals) != tuple(
        item.strength for item in baseline.value_signals
    )
    assert tuple(item.level for item in value_changed.value_signals) != tuple(
        item.level for item in baseline.value_signals
    )
    assert _render(value_changed) != _render(baseline)
    with pytest.raises(ValueError, match="value key is not canonical"):
        CharacterValueSignal(
            key="invented_value",
            strength=0.8,
            level=baseline.value_signals[0].level,
        )


def test_runtime_character_owner_projection_rejects_invalid_or_duplicate_vectors() -> None:
    for invalid in (float("nan"), float("inf"), -0.01, 1.01, True):
        with pytest.raises(ValueError, match="runtime trait value"):
            RuntimeTrait("curiosity", invalid)
        with pytest.raises(ValueError, match="runtime value strength"):
            RuntimeValue("truth", invalid, "truth matters")
    with pytest.raises(ValueError, match="runtime trait key"):
        RuntimeTrait(" ", 0.5)
    with pytest.raises(ValueError, match="runtime value description"):
        RuntimeValue("truth", 0.5, " ")

    context = _runtime_context()
    with pytest.raises(ValueError, match="trait and value keys must be unique"):
        replace(context, traits=(context.traits[0], context.traits[0]))
    with pytest.raises(ValueError, match="trait and value keys must be unique"):
        replace(context, values=(context.values[0], context.values[0]))


def test_evolution_cue_reaches_the_same_presence_as_a_causal_modulation() -> None:
    context = _runtime_context()
    decision = _decision()
    baseline = _projection(context, decision)
    evolved_context = replace(
        context,
        personality_aggregate_version=context.personality_aggregate_version + 1,
        personality_expression=replace(
            context.personality_expression,
            cues=(
                RuntimePersonalityCue(
                    code="grounded_optimism",
                    direction="slightly_stronger",
                ),
            ),
        ),
    )

    evolved = _projection(evolved_context, decision)
    optimism_trait = next(item.value for item in context.traits if item.key == "optimism")

    assert evolved.personality_aggregate_version == baseline.personality_aggregate_version + 1
    assert evolved.personality_signals[0].code == "grounded_optimism"
    assert evolved.personality_signals[0].direction == "slightly_stronger"
    assert evolved.personality_signals[0].strength == pytest.approx(optimism_trait)
    assert all(item.code != "grounded_optimism" for item in baseline.personality_signals)
    assert "сохранять спокойный реалистичный оптимизм" in _render(evolved)
    assert "сейчас чуть заметнее исходного уровня" in _render(evolved)


def test_affect_and_relationship_contrasts_change_one_unified_presence() -> None:
    context = _runtime_context()
    decision = _decision(relationship_relevant=True)
    calm_context = _affect()
    calm_profile = project_character_affect_profile(calm_context)
    positive_context = replace(
        calm_context,
        fast=FastAffectiveState(0.45, 0.35, 0.05, 0.30, 0.35, 0.42, 0.05, 0.0, 0.55),
        mood=MoodState(0.28, 0.40, 0.05),
    )
    fresh_relationship = replace(
        _relationship(),
        maturity="low",
        familiarity="low",
        trust="uncertain",
        comfort="uncertain",
        closeness="low",
        intellectual_respect="uncertain",
        affection="low",
    )
    established_relationship = replace(
        _relationship(),
        maturity="established",
        familiarity="very_high",
        trust="high",
        comfort="very_high",
        closeness="high",
        intellectual_respect="very_high",
        affection="high",
    )
    calm_fresh = _projection(
        context,
        decision,
        emotional_context=calm_context,
        relationship_context=fresh_relationship,
        affect_profile=calm_profile,
        affect_relevant=True,
        relationship_profile="fresh_undeveloped_neutral",
        relationship_relevant=True,
    )
    positive_fresh = _projection(
        context,
        decision,
        emotional_context=positive_context,
        relationship_context=fresh_relationship,
        affect_profile="positive_light",
        affect_relevant=True,
        relationship_profile="fresh_undeveloped_neutral",
        relationship_relevant=True,
    )
    calm_established = _projection(
        context,
        decision,
        emotional_context=calm_context,
        relationship_context=established_relationship,
        affect_profile=calm_profile,
        affect_relevant=True,
        relationship_profile="established_positive",
        relationship_relevant=True,
    )

    assert positive_fresh.affect_signals != calm_fresh.affect_signals
    assert calm_established.relationship_signals != calm_fresh.relationship_signals
    assert positive_fresh.personality_signals == calm_fresh.personality_signals
    assert calm_established.value_signals == calm_fresh.value_signals
    assert _render(positive_fresh) != _render(calm_fresh)
    assert _render(calm_established) != _render(calm_fresh)

    for rendered in (
        _render(calm_fresh),
        _render(positive_fresh),
        _render(calm_established),
    ):
        assert rendered.count(_PRESENCE_MARKER) == 1
        assert "calm_even" not in rendered
        assert "positive_light" not in rendered
        assert "fresh_undeveloped_neutral" not in rendered
        assert "established_positive" not in rendered


def test_guarded_relationship_preserves_low_trust_and_comfort_in_presence() -> None:
    context = _runtime_context()
    decision = _decision(
        relationship_profile="guarded_only_when_relationally_relevant",
        relationship_relevant=True,
    )
    guarded_relationship = replace(
        _relationship(),
        maturity="established",
        familiarity="very_high",
        trust="very_low",
        comfort="low",
        closeness="high",
        intellectual_respect="very_high",
        affection="moderate",
    )

    guarded = _projection(
        context,
        decision,
        relationship_context=guarded_relationship,
        relationship_profile="guarded_only_when_relationally_relevant",
        relationship_relevant=True,
    )
    codes = tuple(item.code for item in guarded.relationship_signals)
    rendered = _render(guarded)

    assert codes[:2] == (
        CharacterRelationshipSignalCode.LIMITED_TRUST,
        CharacterRelationshipSignalCode.LOW_COMFORT,
    )
    assert "доверие сейчас ограничено" in rendered
    assert "комфорт сейчас низкий" in rendered
    assert "больше свободы для лёгкого teasing" not in rendered

    strained = _projection(
        context,
        decision,
        relationship_context=replace(guarded_relationship, recent_strain=True),
        relationship_profile="guarded_only_when_relationally_relevant",
        relationship_relevant=True,
    )
    assert tuple(item.code for item in strained.relationship_signals) == (
        CharacterRelationshipSignalCode.RECENT_STRAIN,
        CharacterRelationshipSignalCode.LIMITED_TRUST,
        CharacterRelationshipSignalCode.LOW_COMFORT,
    )


def test_uncertain_relationship_axes_do_not_become_earned_positive_affordances() -> None:
    context = _runtime_context()
    decision = _decision(relationship_profile="developing_neutral")
    uncertain_relationship = replace(
        _relationship(),
        maturity="developing",
        familiarity="moderate",
        trust="uncertain",
        comfort="uncertain",
        closeness="emerging",
        intellectual_respect="uncertain",
        affection="emerging",
    )

    projection = _projection(
        context,
        decision,
        relationship_context=uncertain_relationship,
        relationship_profile="developing_neutral",
    )
    codes = {item.code for item in projection.relationship_signals}

    assert CharacterRelationshipSignalCode.GROWING_FAMILIARITY in codes
    assert CharacterRelationshipSignalCode.GROWING_AFFECTION in codes
    assert CharacterRelationshipSignalCode.EARNED_TRUST not in codes
    assert CharacterRelationshipSignalCode.EASY_COMFORT not in codes
    assert CharacterRelationshipSignalCode.PERSONAL_CLOSENESS not in codes
    assert CharacterRelationshipSignalCode.INTELLECTUAL_RESPECT not in codes

    limited = _projection(
        context,
        decision,
        relationship_context=replace(
            uncertain_relationship,
            familiarity="low",
            affection="low",
        ),
        relationship_profile="developing_neutral",
    )
    assert tuple(item.code for item in limited.relationship_signals) == (
        CharacterRelationshipSignalCode.LIMITED_FAMILIARITY,
    )
    assert "знакомость пока ограничена" in _render(limited)


def test_presence_keeps_cognition_points_uncertainty_and_claim_boundaries() -> None:
    context = _runtime_context()
    baseline = _decision()
    decision = replace(
        baseline,
        preserve_uncertainty=True,
        required_point_codes=(
            *baseline.required_point_codes,
            "state_uncertainty",
            "presence_before_advice",
            "topic_relevant_inclination",
        ),
    )

    rendered = _render(_projection(context, decision, topic_inclination_available=True))

    assert "существенную неопределённость сохранить явно" in rendered
    assert "личное присутствие должно быть раньше" in rendered
    assert "supplied inclination только по текущей теме" in rendered
    assert "неподтверждённая память" in rendered
    assert "скрытое состояние, мотив" in rendered
    assert "новое устойчивое убеждение Сатори" in rendered
    assert "ложная определённость" in rendered


def test_absent_inclination_stays_silent_while_available_inclination_enables_taste() -> None:
    context = _runtime_context()
    decision = _decision(interests=True)
    absent = _projection(context, decision, topic_inclination_available=False)
    available = _projection(context, decision, topic_inclination_available=True)
    absent_rendered = _render(absent)
    available_rendered = _render(available)

    assert DisclosureFacet.INTERESTS in decision.required_disclosure_facets
    assert "topic inclination может" not in absent_rendered
    assert "topic inclination может" in available_rendered
    assert "не превращай это в отказ или disclaimer" in absent_rendered
    assert "у меня нет устойчивых интересов" not in absent_rendered.casefold()
    assert "я ничем не увлекаюсь" not in absent_rendered.casefold()
    assert absent_rendered != available_rendered


def test_retrieved_memory_can_ground_the_same_achievement_without_unsupported_facts() -> None:
    context = _runtime_context()
    without_memory = _decision(completed_achievement=True)
    with_memory = _decision(
        completed_achievement=True,
        retrieved_memory_available=True,
    )
    projected = _projection(
        context,
        with_memory,
        memory_use_licensed=True,
    )

    assert without_memory.grounding.value == "reaction_only"
    assert with_memory.grounding.value == "trusted_context"
    rendered = _render(projected)
    assert "grounded memory может сделать реакцию конкретнее" in rendered
    assert "Факты бери из текущих слов, supplied trusted state" in rendered


@pytest.mark.parametrize(
    "emotional_context",
    [
        replace(
            _affect(),
            fast=FastAffectiveState(0.45, 0.5, 0.35, 0.9, 0.9, 0.8, 0.0, 0.0, 0.5),
        ),
        replace(
            _affect(),
            fast=FastAffectiveState(0.45, 0.3, 0.0, 0.1, 0.1, 0.5, 0.0, 0.0, 0.4),
        ),
        replace(
            _affect(),
            fast=FastAffectiveState(-0.4, 0.1, 0.0, 0.1, 0.1, 0.0, 0.0, 0.0, 0.2),
            mood=MoodState(-0.3, 0.1, 0.0),
        ),
        replace(
            _affect(),
            fast=FastAffectiveState(0.0, 0.2, 0.0, 0.8, 0.8, 0.0, 0.0, 0.0, 0.4),
        ),
        replace(
            _affect(),
            fast=FastAffectiveState(0.0, 0.2, 0.0, 0.2, 0.2, 0.0, 0.0, 0.0, 0.3),
            mood=MoodState(0.0, 0.2, 0.0),
        ),
    ],
)
def test_v26_affect_profile_and_signals_share_one_canonical_projection(
    emotional_context: EmotionalExpressionContext,
) -> None:
    context = _runtime_context()
    profile = project_character_affect_profile(emotional_context)

    projection = _projection(
        context,
        _decision(affect_profile=profile),
        emotional_context=emotional_context,
        affect_profile=profile,
    )

    assert projection.affect_profile == profile
    assert projection.affect_signals
    if profile == "tense_non_hostile":
        assert {item.code for item in projection.affect_signals}.intersection(
            {
                CharacterAffectSignalCode.PROTECTIVE_CONCERN,
                CharacterAffectSignalCode.FRUSTRATED_EDGE,
                CharacterAffectSignalCode.TENSE_FOCUS,
            }
        )


def test_presence_contract_rejects_profile_signal_contradictions_and_weak_types() -> None:
    context = _runtime_context()
    baseline = _projection(context, _decision())
    with pytest.raises(ValueError, match="affect profile"):
        replace(
            baseline,
            affect_profile="positive_light",
            affect_signals=(
                CharacterAffectSignal(
                    code=CharacterAffectSignalCode.FRUSTRATED_EDGE,
                    level=CharacterPresenceStrength.DEFINING,
                ),
            ),
        )
    with pytest.raises(ValueError, match="fresh relationship profile"):
        replace(
            baseline,
            relationship_profile="fresh_undeveloped_neutral",
            relationship_signals=(
                CharacterRelationshipSignal(
                    code=CharacterRelationshipSignalCode.EARNED_TRUST,
                    level=CharacterPresenceStrength.DEFINING,
                ),
            ),
        )
    for invalid_version in (True, 1.0):
        with pytest.raises(ValueError, match="schema_version"):
            replace(baseline, schema_version=invalid_version)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="strength is invalid"):
        CharacterValueSignal(
            key="truth",
            strength="high",  # type: ignore[arg-type]
            level=CharacterPresenceStrength.DEFINING,
        )
    with pytest.raises(ValueError, match="value level contradicts"):
        CharacterValueSignal(
            key="truth",
            strength=0.01,
            level=CharacterPresenceStrength.DEFINING,
        )
    with pytest.raises(ValueError, match="personality level contradicts"):
        CharacterPersonalitySignal(
            code="curious_analytical",
            strength=0.01,
            level=CharacterPresenceStrength.DEFINING,
        )


def test_low_trust_overrides_low_maturity_profile_while_preserving_new_contact_signal() -> None:
    context = _runtime_context()
    low_trust = replace(
        _relationship(),
        maturity="low",
        familiarity="low",
        trust="very_low",
        comfort="low",
        closeness="low",
        intellectual_respect="uncertain",
        affection="low",
    )
    profile = project_character_relationship_profile(low_trust)

    assert profile == "guarded_only_when_relationally_relevant"
    projection = _projection(
        context,
        _decision(relationship_profile=profile, relationship_relevant=True),
        relationship_context=low_trust,
        relationship_profile=profile,
        relationship_relevant=True,
    )
    codes = {item.code for item in projection.relationship_signals}
    assert CharacterRelationshipSignalCode.LIMITED_TRUST in codes
    assert CharacterRelationshipSignalCode.LOW_COMFORT in codes
    assert CharacterRelationshipSignalCode.NEW_CONTACT in codes
    assert "доверие сейчас ограничено" in _render(projection)

    with pytest.raises(ValueError, match="canonical owner projection"):
        _projection(
            context,
            _decision(relationship_profile="fresh_undeveloped_neutral"),
            relationship_context=low_trust,
            relationship_profile="fresh_undeveloped_neutral",
        )


def test_reaction_only_presence_does_not_expose_memory_support() -> None:
    context = _runtime_context()
    projection = _projection(
        context,
        _decision(completed_achievement=True),
    )

    assert projection.decision.grounding.value == "reaction_only"
    assert "grounded memory может" not in _render(projection)
    with pytest.raises(ValueError, match="memory use requires trusted-context grounding"):
        _projection(
            context,
            _decision(completed_achievement=True),
            memory_use_licensed=True,
        )


def test_v26_retry_instruction_precedes_and_preserves_the_unified_presence() -> None:
    presence = "Trusted current-turn presence Сатори: immutable realization"
    request = ConversationProviderRequest(
        schema_version=1,
        trace_id="v26-retry-order",
        context_schema_version=16,
        messages=(
            ConversationMessage(ConversationMessageRole.SYSTEM, "policy"),
            ConversationMessage(
                ConversationMessageRole.DEVELOPER,
                f"invariants\n{presence}",
            ),
            ConversationMessage(ConversationMessageRole.USER, "Привет"),
        ),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.3,
            max_output_tokens=768,
        ),
    )
    retried = TalkToSatori._response_regeneration_request(
        request,
        ResponseRegenerationReason.MASCULINE_SELF_REFERENCE,
        analyze_dialogue_coherence("Привет", None),
        (),
    )

    final_guidance = retried.messages[-2].content
    assert final_guidance.count(_PRESENCE_MARKER) == 1
    assert final_guidance.endswith(presence)
    assert final_guidance.index("Bounded response-contract retry") < final_guidance.index(
        _PRESENCE_MARKER
    )
    assert retried.messages[-1] == request.messages[-1]


def test_v25_director_is_reproducible_and_isolated_from_v26_presence() -> None:
    context = _runtime_context()
    v25 = _decision(schema_version=CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION)
    historical_first = render_character_delivery_director(
        v25,
        cognition_template=COGNITION_TEMPLATE_REGISTRY_V3.active,
    )
    historical_second = render_character_delivery_director(
        v25,
        cognition_template=COGNITION_TEMPLATE_REGISTRY_V3.active,
    )

    assert historical_first == historical_second
    assert historical_first.count(_HISTORICAL_DIRECTOR_MARKER) == 1
    assert _PRESENCE_MARKER not in historical_first
    with pytest.raises(ValueError, match="requires delivery decision v3"):
        _projection(context, v25)

    current = _render(_projection(context, _decision()))
    assert current.count(_PRESENCE_MARKER) == 1
    assert _HISTORICAL_DIRECTOR_MARKER not in current


def test_v26_production_request_has_one_character_presence_layer() -> None:
    user_text = "Привет, Сатори"
    runtime_context = _runtime_context()
    relationship = _relationship()
    affect = _affect()
    cognition = _cognition(user_text)
    request, manifest = ConversationRequestBuilder(
        BEHAVIOR_POLICY_V26,
        12_000,
        0.3,
        768,
    ).build(
        runtime_context,
        user_text=user_text,
        trace_id="checkpoint142-v26-presence-wire",
        relationship_context=relationship,
        emotional_context=affect,
        cognition_trace=cognition,
    )
    historical_request, _ = ConversationRequestBuilder(
        BEHAVIOR_POLICY_V25,
        12_000,
        0.3,
        768,
    ).build(
        runtime_context,
        user_text=user_text,
        trace_id="checkpoint142-v25-comparison-wire",
        relationship_context=relationship,
        emotional_context=affect,
        cognition_trace=cognition,
    )
    developer_messages = tuple(
        message.content
        for message in request.messages
        if message.role is ConversationMessageRole.DEVELOPER
    )
    combined = "\n".join(message.content for message in request.messages)

    assert manifest.policy_schema_version == 26
    assert manifest.character_delivery_decision_schema_version == 3
    assert manifest.character_presence_projection_schema_version == 1
    assert "character_presence_projection" in manifest.included_sections
    assert manifest.character_presence_personality_signals
    assert manifest.character_presence_value_signals
    assert manifest.character_presence_affect_signals
    assert manifest.character_presence_relationship_signals
    assert sum(_PRESENCE_MARKER in content for content in developer_messages) == 1
    assert combined.count(_PRESENCE_MARKER) == 1
    assert _HISTORICAL_DIRECTOR_MARKER not in combined
    assert _HISTORICAL_CORE_MARKER not in combined
    assert "Trusted current affect DATA" not in combined
    assert "Trusted relationship DATA" not in combined
    presence = next(content for content in developer_messages if _PRESENCE_MARKER in content)
    assert "Момент:" in presence
    assert "живое любопытство" in presence
    assert "интеллектуальное уважение" in presence
    current_trusted_chars = sum(len(message.content) for message in request.messages[:-1])
    historical_trusted_chars = sum(
        len(message.content) for message in historical_request.messages[:-1]
    )
    assert current_trusted_chars < historical_trusted_chars

    invalid_signal_mutations = (
        {"character_presence_personality_signals": ("made_up:available",)},
        {"character_presence_value_signals": ("made_up:available",)},
        {"character_presence_affect_signals": ("made_up:available",)},
        {"character_presence_relationship_signals": ("made_up:available",)},
        {
            "character_presence_affect_signals": (
                "steady:available",
                "steady:strong",
            )
        },
        {
            "included_sections": tuple(
                section
                for section in manifest.included_sections
                if section != "character_presence_projection"
            )
        },
    )
    for mutation in invalid_signal_mutations:
        with pytest.raises(ValueError, match=r"character(?:_| )presence"):
            replace(manifest, **mutation)

    invalid_manifest_mutations: tuple[tuple[dict[str, object], str], ...] = (
        ({"schema_version": True}, "schema_version"),
        (
            {"included_sections": (*manifest.included_sections, "unknown_section")},
            "unknown section",
        ),
        ({"policy_id": "satori.conversation.behavior.v25"}, "policy_id"),
        (
            {"personality_expression_cues": ("curious_analytical:slightly_stronger",)},
            "presence directions",
        ),
        ({"character_presence_memory_use_licensed": True}, "memory-use license"),
    )
    for manifest_mutation, error_pattern in invalid_manifest_mutations:
        with pytest.raises(ValueError, match=error_pattern):
            replace(manifest, **manifest_mutation)  # type: ignore[arg-type]


def test_v26_full_prompt_has_one_consistent_memory_use_license() -> None:
    user_text = "Я сегодня наконец закончил сложную часть проекта"
    memory = RetrievedMemoryContext(
        schema_version=1,
        status=RetrievalStatus.RETRIEVED,
        memories=(
            RetrievedMemory(
                memory_id="memory-project-plan",
                source_interaction_id="interaction-project-plan",
                summary="Собеседник раньше рассказывал о сложной части этого проекта.",
                occurred_at=_NOW,
                importance=0.7,
                confidence=0.9,
                semantic_similarity=0.8,
                recency_score=0.8,
                final_score=0.8,
                evidence_ids=("user-project-plan",),
            ),
        ),
        candidate_count=1,
    )
    request, manifest = ConversationRequestBuilder(
        BEHAVIOR_POLICY_V26,
        12_000,
        0.3,
        768,
    ).build(
        _runtime_context(),
        user_text=user_text,
        trace_id="v26-memory-license-positive",
        memory_context=memory,
        relationship_context=_relationship(),
        emotional_context=_affect(),
        cognition_trace=_cognition(user_text),
    )
    combined = "\n".join(message.content for message in request.messages)

    assert manifest.character_delivery_goal == "celebrate_and_continue"
    assert manifest.character_delivery_grounding == "trusted_context"
    assert manifest.character_presence_memory_use_licensed is True
    assert combined.count("Memory is inside current factual scope") == 1
    assert combined.count("grounded memory может сделать реакцию") == 1
    assert "Memory is outside current factual scope" not in combined
    with pytest.raises(ValueError, match="trusted celebration grounding"):
        replace(manifest, character_presence_memory_use_licensed=False)


def test_v26_full_prompt_keeps_retrieved_memory_outside_reaction_only_scope() -> None:
    user_text = "Ну наконец-то"
    memory = RetrievedMemoryContext(
        schema_version=1,
        status=RetrievalStatus.RETRIEVED,
        memories=(
            RetrievedMemory(
                memory_id="memory-unrelated",
                source_interaction_id="interaction-unrelated",
                summary="Раньше собеседник упоминал другую тему.",
                occurred_at=_NOW,
                importance=0.5,
                confidence=0.8,
                semantic_similarity=0.7,
                recency_score=0.7,
                final_score=0.7,
                evidence_ids=("user-unrelated",),
            ),
        ),
        candidate_count=1,
    )
    request, manifest = ConversationRequestBuilder(
        BEHAVIOR_POLICY_V26,
        12_000,
        0.3,
        768,
    ).build(
        _runtime_context(),
        user_text=user_text,
        trace_id="v26-memory-license-negative",
        memory_context=memory,
        relationship_context=_relationship(),
        emotional_context=_affect(),
        cognition_trace=_cognition(user_text),
    )
    combined = "\n".join(message.content for message in request.messages)

    assert manifest.character_delivery_grounding != "trusted_context"
    assert manifest.character_presence_memory_use_licensed is False
    assert combined.count("Memory is outside current factual scope") == 1
    assert "grounded memory может сделать реакцию" not in combined
