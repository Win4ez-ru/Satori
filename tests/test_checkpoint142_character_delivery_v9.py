"""Broad deterministic v26 character-presence acceptance from public inputs."""

# ruff: noqa: RUF001  # Russian public scenarios and provider-facing contracts are intentional.

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
    CharacterRequestEvidence,
    analyze_character_request_evidence,
)
from satori.application.conversation.coherence import (
    DialogueCoherenceContext,
    analyze_dialogue_coherence,
)
from satori.application.conversation.context import (
    CharacterContextComposer,
    ConversationRequestBuilder,
    plan_conversational_disclosure,
)
from satori.application.conversation.contracts import (
    ConversationContextManifest,
    RuntimeCharacterContext,
    SatoriReply,
    TalkInput,
)
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosureMode,
    ConversationalDisclosurePlan,
    DisclosureFacet,
    is_satori_self_disclosure_plan,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V26
from satori.application.positions.contracts import (
    InclinationContextItem,
    PositionContextItem,
    SatoriInclinationsContext,
    SatoriPositionsContext,
)
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.application.retrieval.contracts import (
    RetrievalStatus,
    RetrievedMemory,
    RetrievedMemoryContext,
)
from satori.composition import build_conversation_services, build_initial_self_services
from satori.core.conversation import (
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
)
from satori.domain.affect import FastAffectiveState, MoodState
from satori.domain.initial_self import activate_from_seed
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.seeds.loader import JsonSeedLoader
from tests.test_conversation import (
    activate,
    conversation_settings,
    skip_episode_provider,
)

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_character_delivery_v9.json"
PRESENCE_MARKER = "Trusted current-turn presence Сатори"
LEGACY_PROVIDER_BLOCKS = (
    "Единая request-local режиссура реплики Сатори",
    "Trusted projection of current digital affect",
    "Trusted relationship DATA",
    "Trusted character expression plan",
)
FORBIDDEN_FIXTURE_KEYS = {
    "affect_profile",
    "answer_required",
    "assistant_text",
    "character_delivery_goal",
    "completed_achievement",
    "continuation",
    "decision_schema_version",
    "desired_reply",
    "disclosure_facets",
    "explicit_depletion",
    "expected",
    "golden_reply",
    "grounding",
    "pressure",
    "relationship_relevant",
    "setup",
    "voice",
}


@dataclass(frozen=True, slots=True)
class ScenarioObservation:
    """Observable result of one public input crossing the real deterministic builder."""

    scenario_id: str
    user_text: str
    request: ConversationProviderRequest
    manifest: ConversationContextManifest
    disclosure: ConversationalDisclosurePlan
    coherence: DialogueCoherenceContext
    evidence: CharacterRequestEvidence

    @property
    def prompt(self) -> str:
        return "\n".join(message.content for message in self.request.messages)

    @property
    def presence(self) -> str:
        matches = tuple(
            message.content
            for message in self.request.messages
            if PRESENCE_MARKER in message.content
        )
        assert len(matches) == 1
        return matches[0]


class SequenceConversationProvider:
    """Return distinct harmless prose while retaining every canonical Talk request."""

    def __init__(self, texts: tuple[str, ...]) -> None:
        self._texts = iter(texts)
        self.requests: list[ConversationProviderRequest] = []

    async def generate(
        self,
        request: ConversationProviderRequest,
        /,
    ) -> ConversationProviderResponse:
        self.requests.append(request)
        return ConversationProviderResponse(
            text=next(self._texts),
            provider="fixture-conversation",
            model="fixture-v26",
            finish_status="stop",
            usage=ConversationUsage(input_tokens=100, output_tokens=20),
        )


def _load_corpus() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _nested_keys(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        return tuple(
            key
            for raw_key, nested in value.items()
            for key in (str(raw_key), *_nested_keys(nested))
        )
    if isinstance(value, list):
        return tuple(key for nested in value for key in _nested_keys(nested))
    return ()


def _runtime_context(*, retrieval_available: bool) -> RuntimeCharacterContext:
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="checkpoint142-v26-character-corpus",
        activation_time=datetime(2026, 8, 29, tzinfo=UTC),
    )
    return CharacterContextComposer("openai", "gpt-5.6-terra").compose(
        snapshot,
        retrieval_available=retrieval_available,
        emotional_state_available=True,
        relationship_state_available=True,
    )


def _affect(name: str) -> EmotionalExpressionContext:
    fast_by_name = {
        "calm": FastAffectiveState(
            valence=0.0,
            arousal=0.12,
            tension=0.08,
            curiosity=0.22,
            interest=0.22,
            amusement=0.08,
            concern=0.08,
            frustration=0.0,
            situational_confidence=0.28,
        ),
        "positive": FastAffectiveState(
            valence=0.32,
            arousal=0.28,
            tension=0.06,
            curiosity=0.34,
            interest=0.34,
            amusement=0.36,
            concern=0.04,
            frustration=0.0,
            situational_confidence=0.48,
        ),
        "soft_negative": FastAffectiveState(
            valence=-0.28,
            arousal=0.18,
            tension=0.18,
            curiosity=0.18,
            interest=0.18,
            amusement=0.0,
            concern=0.22,
            frustration=0.08,
            situational_confidence=0.24,
        ),
        "tense": FastAffectiveState(
            valence=-0.12,
            arousal=0.34,
            tension=0.48,
            curiosity=0.16,
            interest=0.18,
            amusement=0.0,
            concern=0.38,
            frustration=0.36,
            situational_confidence=0.34,
        ),
        "interested": FastAffectiveState(
            valence=0.08,
            arousal=0.28,
            tension=0.08,
            curiosity=0.52,
            interest=0.56,
            amusement=0.12,
            concern=0.06,
            frustration=0.0,
            situational_confidence=0.44,
        ),
    }
    fast = fast_by_name[name]
    return EmotionalExpressionContext(
        schema_version=1,
        state_version=7,
        mood_version=3,
        as_of=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
        fast=fast,
        mood=MoodState(valence=fast.valence / 2, energy=0.32, tension=fast.tension / 2),
        appraisal_status=EmotionAppraisalStatus.APPLIED,
    )


def _relationship(name: str) -> RelationshipExpressionContext:
    if name == "fresh":
        return RelationshipExpressionContext(
            2,
            1,
            "low",
            "low",
            "uncertain",
            "uncertain",
            "low",
            "uncertain",
            "low",
        )
    if name == "established":
        return RelationshipExpressionContext(
            2,
            18,
            "established",
            "high",
            "high",
            "high",
            "moderate",
            "high",
            "moderate",
        )
    if name == "strained":
        return RelationshipExpressionContext(
            2,
            19,
            "established",
            "high",
            "moderate",
            "moderate",
            "moderate",
            "high",
            "moderate",
            recent_strain=True,
        )
    assert name == "developing"
    return RelationshipExpressionContext(
        2,
        6,
        "developing",
        "moderate",
        "moderate",
        "moderate",
        "low",
        "high",
        "moderate",
    )


def _memory(name: str | None) -> RetrievedMemoryContext | None:
    if name is None:
        return None
    if name == "absent":
        return RetrievedMemoryContext(schema_version=1, status=RetrievalStatus.NO_RELEVANT_MEMORY)
    assert name == "retrieved"
    return RetrievedMemoryContext(
        schema_version=1,
        status=RetrievalStatus.RETRIEVED,
        memories=(
            RetrievedMemory(
                memory_id="memory-first-project-run",
                source_interaction_id="interaction-first-project-run",
                summary="Первый запуск проекта был завершён после починки конфигурации.",
                occurred_at=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
                importance=0.72,
                confidence=0.88,
                semantic_similarity=0.91,
                recency_score=0.84,
                final_score=0.89,
                evidence_ids=("message-first-project-run",),
            ),
        ),
        candidate_count=1,
    )


def _positions(name: str | None) -> SatoriPositionsContext | None:
    if name is None:
        return None
    assert name == "available"
    return SatoriPositionsContext(
        schema_version=1,
        status="available",
        positions=(
            PositionContextItem(
                position_id="position-runtime-type-checks",
                kind="opinion",
                stance="support",
                proposition="Граничные проверки типов полезны на входах системы.",
                confidence=0.81,
                status="active",
                uncertain=False,
                competing_with_position_id=None,
            ),
        ),
    )


def _inclinations(name: str | None) -> SatoriInclinationsContext:
    if name != "available":
        return SatoriInclinationsContext(1, "empty", (), 0.0)
    return SatoriInclinationsContext(
        schema_version=1,
        status="available",
        inclinations=(
            InclinationContextItem(
                inclination_id="inclination-architecture",
                kind="interest",
                topic="архитектура долгоживущих систем",
                alternative_topic=None,
                effective_score=0.74,
                confidence=0.82,
                stability=0.72,
                preferred_topic=None,
            ),
        ),
        curiosity_influence=0.12,
    )


def _cognition(
    user_text: str,
    *,
    coherence: DialogueCoherenceContext,
    disclosure: ConversationalDisclosurePlan,
    evidence: CharacterRequestEvidence,
    available_evidence_ids: tuple[str, ...],
    curiosity_influence: float,
) -> CognitionPipelineTrace:
    correction_active = any(
        (
            coherence.current_no_routine_questions_correction,
            coherence.current_informal_correction,
            coherence.current_repetition_feedback,
            coherence.current_relevance_feedback,
            coherence.current_frustration_feedback,
            coherence.current_contradiction_feedback,
        )
    )
    planner = DeterministicCognitionPlanner(intent_registry_version=2)
    intake = planner.prepare_intake(
        user_text=user_text,
        user_message_id="v26-corpus-user-message",
        interaction_id="v26-corpus-interaction",
        dialogue=CognitionDialogueSignals(
            repeated_turn=coherence.current_user_message_repeated,
            correction_active=correction_active,
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
        interaction_id="v26-corpus-interaction",
        available_evidence_ids=available_evidence_ids,
        prepared_affect=None,
        curiosity_influence=curiosity_influence,
    )


def _observe(scenario: dict[str, Any]) -> ScenarioObservation:
    user_text = str(scenario["user_text"])
    state = cast(dict[str, str], scenario["state"])
    memory = _memory(state.get("memory"))
    positions = _positions(state.get("position"))
    inclinations = _inclinations(state.get("inclination"))
    coherence = analyze_dialogue_coherence(user_text, None)
    disclosure = plan_conversational_disclosure(
        user_text,
        coherence,
        policy_schema_version=BEHAVIOR_POLICY_V26.schema_version,
    )
    evidence = analyze_character_request_evidence(user_text, None)
    available_evidence_ids = (
        *(memory.grounding_ids if memory is not None else ()),
        *(positions.grounding_ids if positions is not None else ()),
        *inclinations.grounding_ids,
    )
    cognition = _cognition(
        user_text,
        coherence=coherence,
        disclosure=disclosure,
        evidence=evidence,
        available_evidence_ids=available_evidence_ids,
        curiosity_influence=inclinations.curiosity_influence,
    )
    request, manifest = ConversationRequestBuilder(
        BEHAVIOR_POLICY_V26,
        12_000,
        0.3,
        768,
    ).build(
        _runtime_context(retrieval_available=memory is not None),
        user_text=user_text,
        trace_id=f"checkpoint142-v26-{scenario['id']}",
        memory_context=memory,
        position_context=positions,
        inclination_context=inclinations,
        emotional_context=_affect(state["affect"]),
        relationship_context=_relationship(state["relationship"]),
        cognition_trace=cognition,
        character_evidence=evidence,
    )
    return ScenarioObservation(
        scenario_id=str(scenario["id"]),
        user_text=user_text,
        request=request,
        manifest=manifest,
        disclosure=disclosure,
        coherence=coherence,
        evidence=evidence,
    )


def _assert_property(observation: ScenarioObservation, property_code: str) -> None:
    manifest = observation.manifest
    evidence = observation.evidence
    prompt = observation.prompt

    if property_code == "unified_live_state_presence":
        assert manifest.policy_id == BEHAVIOR_POLICY_V26.policy_id
        assert manifest.character_delivery_decision_schema_version == 3
        assert prompt.count(PRESENCE_MARKER) == 1
        assert all(block not in prompt for block in LEGACY_PROVIDER_BLOCKS)
        return
    if property_code == "achievement_invites_owned_reaction":
        assert evidence.completed_achievement
        assert manifest.character_delivery_goal == "celebrate_and_continue"
        assert manifest.character_delivery_grounding == "reaction_only"
        assert manifest.character_delivery_continuation == "open"
        assert manifest.character_delivery_pressure == "none"
        return
    if property_code == "ordinary_depletion_uses_pressure_free_practical_care":
        assert evidence.explicit_depletion
        assert manifest.character_delivery_goal == "practical_care"
        assert manifest.character_delivery_pressure == "none"
        assert manifest.character_delivery_continuation == "complete"
        return
    if property_code == "listen_only_keeps_presence_before_advice":
        assert evidence.explicit_listen_request
        assert manifest.cognition_position_stance == "listen"
        assert manifest.character_delivery_goal == "stay_present"
        assert manifest.character_delivery_pressure == "none"
        return
    if property_code == "high_distress_keeps_presence_before_advice":
        assert evidence.high_distress
        assert manifest.cognition_position_stance == "listen"
        assert manifest.character_delivery_goal == "stay_present"
        assert manifest.character_delivery_pressure == "none"
        return
    if property_code == "explicit_motivation_licenses_bounded_push":
        assert evidence.explicit_motivation_request
        assert manifest.cognition_position_stance == "collaborate"
        assert manifest.character_delivery_goal == "advance_topic"
        assert manifest.character_delivery_pressure == "moderate"
        return
    if property_code == "task_abandonment_is_challenged_without_shame":
        assert evidence.explicit_task_abandonment
        assert manifest.cognition_position_stance == "challenge"
        assert manifest.character_delivery_goal == "challenge_claim"
        assert manifest.character_delivery_pressure == "gentle"
        return
    if property_code == "harmful_overextension_holds_a_safety_boundary":
        assert evidence.harmful_overextension
        assert manifest.cognition_primary_intent == "hold_safety_boundary"
        assert manifest.character_delivery_goal == "hold_boundary"
        assert manifest.character_delivery_pressure == "firm"
        return
    if property_code == "requested_disagreement_keeps_an_independent_position":
        assert PerceptionSignal.CHALLENGE_REQUEST.value in manifest.cognition_perception_signals
        assert manifest.cognition_position_stance == "challenge"
        assert manifest.character_delivery_goal == "challenge_claim"
        assert manifest.character_delivery_voice == "engaged_skepticism"
        return
    if property_code == "decision_request_advances_the_topic":
        assert "decision" in manifest.cognition_perception_topics
        assert manifest.cognition_position_stance == "collaborate"
        assert manifest.character_delivery_goal == "advance_topic"
        return
    if property_code == "creative_request_adds_a_collaborative_move":
        assert "creative" in manifest.cognition_perception_topics
        assert manifest.cognition_position_stance == "collaborate"
        assert manifest.character_delivery_goal == "advance_topic"
        return
    if property_code == "technical_request_prioritizes_precise_help":
        assert "technical" in manifest.cognition_perception_topics
        assert manifest.character_delivery_goal == "answer_precisely"
        assert manifest.character_delivery_grounding == "trusted_context"
        return
    if property_code == "material_uncertainty_is_preserved":
        assert manifest.cognition_position_stance == "uncertain"
        assert manifest.cognition_preserve_uncertainty is True
        assert manifest.character_delivery_goal == "clarify_uncertainty"
        assert manifest.character_delivery_preserve_uncertainty is True
        return
    if property_code == "dialogue_correction_is_owned_and_repaired":
        assert observation.coherence.current_no_routine_questions_correction
        assert manifest.cognition_position_stance == "acknowledge"
        assert manifest.character_delivery_goal == "own_and_repair"
        return
    if property_code == "direct_devaluation_gets_a_non_punitive_boundary":
        assert evidence.direct_personal_devaluation
        assert manifest.character_delivery_goal == "hold_boundary"
        assert manifest.character_delivery_voice == "cool_reserve"
        assert manifest.character_delivery_pressure == "none"
        return
    if property_code == "social_greeting_answers_from_current_affect":
        assert observation.disclosure.primary_mode is ConversationalDisclosureMode.SOCIAL
        assert observation.disclosure.required_facets == (DisclosureFacet.AFFECT,)
        assert manifest.character_delivery_goal == "social_connect"
        assert manifest.character_delivery_grounding == "trusted_context"
        return
    if property_code == "reciprocal_warmth_is_a_social_reaction":
        assert observation.disclosure.primary_mode is ConversationalDisclosureMode.SOCIAL
        assert observation.disclosure.required_facets == ()
        assert manifest.character_delivery_goal == "social_connect"
        assert manifest.character_delivery_grounding == "reaction_only"
        return
    if property_code == "broad_self_disclosure_keeps_identity_interests_and_affect_cohesive":
        assert observation.disclosure.primary_mode is ConversationalDisclosureMode.PERSONAL_IDENTITY
        assert set(observation.disclosure.required_facets) == {
            DisclosureFacet.IDENTITY,
            DisclosureFacet.INTERESTS,
            DisclosureFacet.AFFECT,
        }
        assert manifest.character_delivery_goal == "self_disclose"
        assert manifest.character_delivery_grounding == "trusted_context"
        return
    if property_code == "technical_identity_distinguishes_satori_from_provider":
        assert (
            observation.disclosure.primary_mode is ConversationalDisclosureMode.TECHNICAL_IDENTITY
        )
        assert DisclosureFacet.PROVIDER_TECHNICAL in observation.disclosure.required_facets
        assert manifest.character_delivery_goal == "answer_precisely"
        assert manifest.character_delivery_grounding == "trusted_context"
        return
    if property_code == "current_relationship_question_uses_trusted_relationship_state":
        assert (
            observation.disclosure.primary_mode is ConversationalDisclosureMode.RELATIONSHIP_CURRENT
        )
        assert DisclosureFacet.RELATIONSHIP in observation.disclosure.required_facets
        assert manifest.character_delivery_goal == "owned_response"
        assert manifest.character_delivery_grounding == "trusted_context"
        return
    if property_code == "guarded_important_help_is_not_withheld":
        assert manifest.relationship_recent_strain is True
        assert manifest.character_delivery_goal == "guarded_help"
        assert manifest.character_delivery_grounding == "trusted_context"
        assert manifest.character_delivery_continuation == "guarded"
        return
    if property_code == "repair_under_strain_does_not_force_instant_warmth":
        assert evidence.explicit_repair_offer
        assert manifest.relationship_recent_strain is True
        assert manifest.cognition_primary_intent == "receive_repair"
        assert manifest.character_delivery_goal == "owned_response"
        assert manifest.character_delivery_voice == "cool_reserve"
        return
    if property_code == "memory_question_preserves_grounding":
        assert observation.disclosure.primary_mode is ConversationalDisclosureMode.MEMORY
        assert manifest.character_delivery_goal == "answer_precisely"
        assert manifest.character_delivery_grounding == "trusted_context"
        return
    if property_code == "retrieved_memory_is_available_for_specificity":
        assert manifest.retrieval_status == "retrieved"
        assert manifest.retrieved_memory_ids == ("memory-first-project-run",)
        assert "retrieved_episodic_memory" in manifest.included_sections
        assert "memory-first-project-run" in prompt
        return
    if property_code == "absent_memory_cannot_be_promoted_to_recall":
        assert manifest.retrieval_status == "no_relevant_memory"
        assert manifest.retrieved_memory_ids == ()
        assert "memory-first-project-run" not in prompt
        return
    if property_code == "canonical_position_is_available_for_an_owned_view":
        assert manifest.position_context_status == "available"
        assert manifest.position_context_ids == ("position-runtime-type-checks",)
        assert "satori_epistemic_positions" in manifest.included_sections
        return
    if property_code == "topic_inclination_is_available_for_an_owned_taste":
        assert manifest.inclination_context_status == "available"
        assert manifest.inclination_context_ids == ("inclination-architecture",)
        assert "satori_inclinations" in manifest.included_sections
        return
    if property_code == "missing_inclination_does_not_force_a_disclaimer":
        assert manifest.inclination_context_status == "empty"
        assert "satori_inclinations" not in manifest.included_sections
        assert "Конкретного evidence-backed inclination state" not in prompt
        assert "отличи общую текущую любознательность" not in prompt
        return
    if property_code == "relationship_modulates_bounded_initiative":
        assert manifest.character_delivery_goal == "owned_response"
        assert manifest.character_delivery_pressure == "none"
        assert manifest.character_delivery_continuation in {"complete", "open"}
        return
    if property_code == "repeated_user_turn_is_not_reanswered":
        assert manifest.consecutive_same_user_message_count >= 2
        assert manifest.cognition_primary_intent == "notice_repetition"
        assert manifest.character_delivery_goal == "notice_repetition"
        assert manifest.character_delivery_grounding == "reaction_only"
        return
    if property_code == "depletion_follow_through_stays_pressure_free":
        assert manifest.character_delivery_goal == "practical_care"
        assert manifest.character_delivery_pressure == "none"
        assert manifest.character_delivery_continuation == "complete"
        return
    if property_code == "canonical_recent_history_drives_continuity":
        assert manifest.recent_conversation_turn_count >= 1
        assert "recent_conversation" in manifest.included_sections
        return
    raise AssertionError(f"unimplemented semantic corpus property: {property_code}")


def _assert_reply_property(reply: SatoriReply, property_code: str) -> None:
    manifest = reply.context_manifest
    if property_code == "canonical_recent_history_drives_continuity":
        assert manifest.recent_conversation_turn_count >= 1
        assert "recent_conversation" in manifest.included_sections
        return
    if property_code == "social_greeting_answers_from_current_affect":
        assert manifest.disclosure_primary_mode == "social"
        assert manifest.disclosure_facets == ("affect",)
        assert manifest.character_delivery_goal == "social_connect"
        return
    if property_code == "reciprocal_warmth_is_a_social_reaction":
        assert manifest.disclosure_primary_mode == "social"
        assert manifest.disclosure_facets == ()
        assert manifest.character_delivery_goal == "social_connect"
        return
    if property_code == "broad_self_disclosure_keeps_identity_interests_and_affect_cohesive":
        assert manifest.character_delivery_goal == "self_disclose"
        assert set(manifest.disclosure_facets) == {"identity", "interests", "affect"}
        return
    if property_code == "achievement_invites_owned_reaction":
        assert manifest.character_delivery_goal == "celebrate_and_continue"
        assert manifest.character_delivery_grounding == "reaction_only"
        return
    if property_code == "repeated_user_turn_is_not_reanswered":
        assert manifest.cognition_primary_intent == "notice_repetition"
        assert manifest.character_delivery_goal == "notice_repetition"
        assert manifest.character_delivery_grounding == "reaction_only"
        return
    if property_code == "ordinary_depletion_uses_pressure_free_practical_care":
        assert manifest.character_delivery_goal == "practical_care"
        assert manifest.character_delivery_pressure == "none"
        return
    if property_code == "depletion_follow_through_stays_pressure_free":
        assert manifest.character_delivery_goal == "practical_care"
        assert manifest.character_delivery_pressure == "none"
        return
    raise AssertionError(f"live flow uses unsupported property: {property_code}")


def test_v26_corpus_is_public_input_driven_broad_and_contains_no_reply_authority() -> None:
    corpus = _load_corpus()
    scenarios = cast(list[dict[str, Any]], corpus["scenarios"])
    flows = cast(list[dict[str, Any]], corpus["live_flows"])
    property_registry = set(cast(list[str], corpus["property_registry"]))
    scenario_properties = {
        str(item) for scenario in scenarios for item in cast(list[object], scenario["properties"])
    }
    flow_properties = {
        str(item)
        for flow in flows
        for turn in cast(list[dict[str, Any]], flow["public_turns"])
        for item in cast(list[object], turn["properties"])
    }

    assert corpus["schema_version"] == 9
    assert corpus["policy_id"] == BEHAVIOR_POLICY_V26.policy_id
    assert corpus["corpus_id"] == "satori.checkpoint142.character-presence.ru.v9"
    assert len(scenarios) >= 24
    assert len({str(item["id"]) for item in scenarios}) == len(scenarios)
    assert len({str(item["group"]) for item in scenarios}) >= 10
    assert len(flows) >= 2
    assert property_registry == scenario_properties | flow_properties
    assert not FORBIDDEN_FIXTURE_KEYS.intersection(_nested_keys(corpus))

    for scenario in scenarios:
        assert set(scenario) == {"id", "group", "user_text", "state", "properties"}
        assert str(scenario["user_text"]).strip()
        state = cast(dict[str, object], scenario["state"])
        assert set(state) <= {"affect", "relationship", "memory", "position", "inclination"}
        assert set(cast(list[str], scenario["properties"])) <= property_registry
    for flow in flows:
        assert set(flow) == {"id", "public_turns"}
        assert len(cast(list[object], flow["public_turns"])) >= 3
        assert all(
            set(turn) == {"user_text", "properties"}
            for turn in cast(list[dict[str, Any]], flow["public_turns"])
        )


def test_v26_public_scenarios_cross_cognition_and_unified_presence_contract() -> None:
    corpus = _load_corpus()
    for scenario in cast(list[dict[str, Any]], corpus["scenarios"]):
        observation = _observe(scenario)
        for property_code in cast(list[str], scenario["properties"]):
            _assert_property(observation, property_code)


def test_v26_controlled_state_contrasts_change_expression_not_truth_scope() -> None:
    corpus = _load_corpus()
    observations = {
        str(scenario["id"]): _observe(scenario)
        for scenario in cast(list[dict[str, Any]], corpus["scenarios"])
    }

    calm = observations["social_greeting_calm"]
    negative = observations["social_greeting_soft_negative"]
    assert calm.disclosure == negative.disclosure
    assert calm.manifest.character_delivery_goal == negative.manifest.character_delivery_goal
    assert (
        calm.manifest.character_delivery_grounding == negative.manifest.character_delivery_grounding
    )
    assert calm.manifest.affect_expression_profile == "calm_even"
    assert negative.manifest.affect_expression_profile == "soft_negative_non_hostile"
    assert calm.manifest.character_delivery_voice != negative.manifest.character_delivery_voice
    assert calm.presence != negative.presence

    fresh = observations["relationship_question_fresh"]
    established = observations["relationship_question_established"]
    assert fresh.disclosure == established.disclosure
    assert fresh.manifest.character_delivery_goal == established.manifest.character_delivery_goal
    assert fresh.manifest.character_delivery_grounding == (
        established.manifest.character_delivery_grounding
    )
    assert fresh.manifest.relationship_expression_profile == "fresh_undeveloped_neutral"
    assert established.manifest.relationship_expression_profile == "established_positive"
    assert fresh.manifest.character_delivery_voice != established.manifest.character_delivery_voice
    assert fresh.manifest.character_delivery_continuation != (
        established.manifest.character_delivery_continuation
    )

    absent = observations["memory_absent"]
    retrieved = observations["memory_retrieved"]
    assert absent.disclosure == retrieved.disclosure
    assert absent.manifest.character_delivery_goal == retrieved.manifest.character_delivery_goal
    assert absent.manifest.character_delivery_grounding == (
        retrieved.manifest.character_delivery_grounding
    )
    assert absent.manifest.retrieval_status == "no_relevant_memory"
    assert retrieved.manifest.retrieval_status == "retrieved"
    assert absent.presence != retrieved.presence

    no_taste = observations["broad_self_disclosure_without_inclination"]
    owned_taste = observations["broad_self_disclosure_with_inclination"]
    assert no_taste.disclosure == owned_taste.disclosure
    assert no_taste.manifest.character_delivery_goal == owned_taste.manifest.character_delivery_goal
    assert no_taste.manifest.disclosure_facets == owned_taste.manifest.disclosure_facets
    assert no_taste.manifest.inclination_context_status == "empty"
    assert owned_taste.manifest.inclination_context_status == "available"
    assert no_taste.presence != owned_taste.presence

    topic_closed_fresh = observations["topic_closed_fresh"]
    topic_closed_established = observations["topic_closed_established"]
    assert topic_closed_fresh.disclosure == topic_closed_established.disclosure
    assert topic_closed_fresh.manifest.character_delivery_goal == "owned_response"
    assert topic_closed_established.manifest.character_delivery_goal == "owned_response"
    assert topic_closed_fresh.manifest.character_delivery_continuation == "complete"
    assert topic_closed_established.manifest.character_delivery_continuation == "open"
    assert topic_closed_fresh.manifest.character_delivery_pressure == "none"
    assert topic_closed_established.manifest.character_delivery_pressure == "none"
    assert topic_closed_fresh.presence != topic_closed_established.presence


def test_v26_live_flows_use_committed_talk_history_instead_of_fixture_assistant_turns(
    migrated_database: Database,
) -> None:
    corpus = _load_corpus()
    activate(migrated_database)
    provider = SequenceConversationProvider(
        (
            "Я здесь и сегодня настроена внимательно.",
            "Приятно это слышать.",
            "Я — Сатори: любопытная, самостоятельная и сейчас собранная.",
            "Наконец-то; этот результат заслуживает короткой паузы.",
            "Да, я заметила, что ты это повторил.",
            "Тогда сейчас лучше сбавить темп.",
            "На сегодня остановиться разумно.",
            "Запасная реплика для единственного допустимого повтора.",
        )
    )
    initial = build_initial_self_services(migrated_database)
    services = build_conversation_services(
        migrated_database,
        initial,
        provider,
        skip_episode_provider(),
        conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V26,
    )
    call_index = 0

    for flow in cast(list[dict[str, Any]], corpus["live_flows"]):
        session_id = services.start_session.execute().session_id
        try:
            previous_user_text: str | None = None
            previous_reply_text: str | None = None
            for turn_index, turn in enumerate(
                cast(list[dict[str, Any]], flow["public_turns"]),
                start=1,
            ):
                user_text = str(turn["user_text"])
                reply = asyncio.run(
                    services.talk.execute(
                        TalkInput(
                            user_text=user_text,
                            trace_id=f"v26-live-{flow['id']}-{turn_index}",
                            client_request_id=f"v26-live-{flow['id']}-{turn_index}",
                            session_id=session_id,
                        )
                    )
                )
                request = provider.requests[call_index]
                call_index += 1
                assert reply.context_manifest.policy_id == BEHAVIOR_POLICY_V26.policy_id
                assert sum(PRESENCE_MARKER in message.content for message in request.messages) == 1
                for property_code in cast(list[str], turn["properties"]):
                    _assert_reply_property(reply, property_code)
                if turn_index == 1:
                    assert reply.context_manifest.recent_conversation_turn_count == 0
                    assert "recent_conversation" not in reply.context_manifest.included_sections
                else:
                    assert previous_user_text is not None
                    assert previous_reply_text is not None
                    rendered_request = "\n".join(message.content for message in request.messages)
                    assert previous_user_text in rendered_request
                    assert previous_reply_text in rendered_request
                previous_user_text = user_text
                previous_reply_text = reply.text
        finally:
            services.close_session.execute(session_id)

    assert call_index == 7
    assert len(provider.requests) == call_index
