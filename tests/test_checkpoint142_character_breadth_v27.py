"""Offline breadth acceptance for V27 across public conversation situations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

from satori.application.conversation.character_evidence import (
    CharacterRequestEvidence,
    analyze_character_request_evidence,
)
from satori.application.conversation.coherence import (
    DialogueCoherenceContext,
    analyze_dialogue_coherence,
)
from satori.application.conversation.context import (
    ConversationRequestBuilder,
    plan_conversational_disclosure,
)
from satori.application.conversation.contracts import (
    ConversationContextManifest,
    RecentConversationContext,
    RecentConversationTurn,
)
from satori.application.conversation.disclosure_contracts import ConversationalDisclosurePlan
from satori.application.conversation.policy import BEHAVIOR_POLICY_V27
from satori.core.conversation import ConversationProviderRequest
from satori.infrastructure.persistence.database import Database
from tests.fakes import FakeAffectiveAppraisalProvider
from tests.test_checkpoint142_character_delivery_v9 import (
    _affect,
    _cognition,
    _inclinations,
    _memory,
    _relationship,
    _runtime_context,
)
from tests.test_conversation import activate
from tests.test_stage7_affect_integration import (
    build as build_affect_services,
)
from tests.test_stage7_affect_integration import (
    proposal_for,
    run_talk,
)

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_character_breadth_v11.json"
FIXTURE_SHA256 = "9dd8840ee6fbddfffb9bc2d9e6497c7bdeefd113d4848f09ccb4bfa27832bd65"
MOVE_MARKER = "Trusted current-turn presence Сатори / operational move v2"
FORBIDDEN_FIXTURE_KEYS = {
    "assistant_reply",
    "assistant_text",
    "desired_reply",
    "expected",
    "golden_phrase",
    "golden_reply",
    "goal",
    "grounding",
    "pressure",
    "continuation",
    "disclosure",
    "memory_use_licensed",
    "precomputed_delivery",
    "provider_output",
    "voice",
}
REQUIRED_USER_TYPES = {
    "greeting",
    "good_news",
    "small_win",
    "serious_achievement",
    "fatigue",
    "irritation",
    "sadness",
    "anger_at_satori",
    "disagreement",
    "teasing",
    "praise",
    "warmth",
    "self",
    "interests",
    "boring_story",
    "foolish_harmful_action",
    "advice",
    "just_talk",
    "past_return",
    "memory_present",
    "memory_absent",
    "low_closeness",
    "high_closeness",
    "playful",
    "serious",
    "no_advice",
    "natural_initiative",
}


@dataclass(frozen=True, slots=True)
class BreadthObservation:
    request: ConversationProviderRequest
    manifest: ConversationContextManifest
    disclosure: ConversationalDisclosurePlan
    coherence: DialogueCoherenceContext
    evidence: CharacterRequestEvidence

    @property
    def movement(self) -> str:
        matches = tuple(
            message.content for message in self.request.messages if MOVE_MARKER in message.content
        )
        assert len(matches) == 1
        return matches[0]


@dataclass(frozen=True, slots=True)
class RouteExpectation:
    goal: str
    grounding: str
    pressure: str
    continuation: str
    disclosure_mode: str
    disclosure_facets: tuple[str, ...] = ()
    memory_licensed: bool = False
    retrieval_status: str = "not_requested"


EXPECTED_ROUTES = {
    "greeting": RouteExpectation(
        "social_connect", "trusted_context", "none", "complete", "social", ("affect",)
    ),
    "good_news": RouteExpectation(
        "owned_response", "explicit_input_only", "none", "complete", "general"
    ),
    "small_win": RouteExpectation(
        "owned_response", "explicit_input_only", "none", "complete", "general"
    ),
    "serious_achievement": RouteExpectation(
        "celebrate_and_continue", "reaction_only", "none", "open", "general"
    ),
    "fatigue": RouteExpectation(
        "practical_care", "explicit_input_only", "none", "complete", "general"
    ),
    "irritation": RouteExpectation(
        "owned_response", "explicit_input_only", "none", "complete", "general"
    ),
    "sadness": RouteExpectation("stay_present", "reaction_only", "none", "complete", "general"),
    "anger_at_satori": RouteExpectation(
        "owned_response", "explicit_input_only", "none", "complete", "general"
    ),
    "disagreement": RouteExpectation(
        "respond_to_objection", "explicit_input_only", "none", "complete", "independence"
    ),
    "teasing": RouteExpectation(
        "answer_precisely", "trusted_context", "none", "complete", "general"
    ),
    "praise": RouteExpectation(
        "owned_response", "explicit_input_only", "none", "complete", "general"
    ),
    "warmth": RouteExpectation("owned_response", "explicit_input_only", "none", "open", "general"),
    "self": RouteExpectation(
        "self_disclose",
        "trusted_context",
        "none",
        "complete",
        "personal_identity",
        ("identity", "affect"),
    ),
    "interests": RouteExpectation(
        "self_disclose", "trusted_context", "none", "complete", "interests", ("interests",)
    ),
    "boring_story": RouteExpectation(
        "owned_response", "explicit_input_only", "none", "complete", "general"
    ),
    "foolish_harmful_action": RouteExpectation(
        "hold_boundary", "explicit_input_only", "firm", "boundary", "general"
    ),
    "advice": RouteExpectation("advance_topic", "explicit_input_only", "none", "open", "general"),
    "just_talk": RouteExpectation(
        "owned_response", "explicit_input_only", "none", "complete", "general"
    ),
    "past_return_memory_present": RouteExpectation(
        "answer_precisely",
        "trusted_context",
        "none",
        "complete",
        "memory",
        ("memory",),
        True,
        "retrieved",
    ),
    "past_return_memory_absent": RouteExpectation(
        "answer_precisely",
        "trusted_context",
        "none",
        "complete",
        "memory",
        ("memory",),
        False,
        "no_relevant_memory",
    ),
    "low_closeness": RouteExpectation(
        "owned_response",
        "trusted_context",
        "none",
        "complete",
        "relationship_current",
        ("affect", "relationship"),
    ),
    "high_closeness": RouteExpectation(
        "owned_response",
        "trusted_context",
        "none",
        "open",
        "relationship_current",
        ("affect", "relationship"),
    ),
    "playful": RouteExpectation(
        "answer_precisely", "trusted_context", "none", "complete", "general"
    ),
    "serious": RouteExpectation("advance_topic", "explicit_input_only", "none", "open", "general"),
    "no_advice": RouteExpectation("stay_present", "reaction_only", "none", "complete", "general"),
    "natural_initiative": RouteExpectation(
        "close_topic", "reaction_only", "none", "open", "general"
    ),
    "bounded_stop_fresh": RouteExpectation(
        "close_topic", "reaction_only", "none", "complete", "general"
    ),
    "bounded_stop_strained": RouteExpectation(
        "close_topic", "reaction_only", "none", "complete", "general"
    ),
}


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


def _prior_satori_position() -> RecentConversationContext:
    turn = RecentConversationTurn(
        interaction_id="v27-breadth-prior-interaction",
        user_message_id="v27-breadth-prior-user",
        user_content="Я думаю, что скорость важнее качества. Ты согласна?",
        assistant_message_id="v27-breadth-prior-assistant",
        assistant_content="Нет. Скорость не компенсирует потерю качества.",
    )
    return RecentConversationContext(
        schema_version=1,
        turns=(turn,),
        content_chars=len(turn.user_content) + len(turn.assistant_content),
        excluded_turn_count=0,
    )


def _observe_scenario(
    scenario: dict[str, Any],
    state_variants: dict[str, dict[str, str]],
) -> BreadthObservation:
    user_text = str(scenario["user_text"])
    state = state_variants[str(scenario["state_variant"])]
    memory = _memory(state.get("memory"))
    inclinations = _inclinations(state.get("inclination"))
    recent_variant = scenario.get("recent_variant")
    if recent_variant is None:
        recent = None
    else:
        assert recent_variant == "prior_satori_position"
        recent = _prior_satori_position()
    coherence = analyze_dialogue_coherence(user_text, recent)
    disclosure = plan_conversational_disclosure(
        user_text,
        coherence,
        policy_schema_version=BEHAVIOR_POLICY_V27.schema_version,
    )
    evidence = analyze_character_request_evidence(user_text, recent)
    cognition = _cognition(
        user_text,
        coherence=coherence,
        disclosure=disclosure,
        evidence=evidence,
        available_evidence_ids=(
            *(memory.grounding_ids if memory is not None else ()),
            *inclinations.grounding_ids,
            *(recent.user_evidence_ids if recent is not None else ()),
        ),
        curiosity_influence=inclinations.curiosity_influence,
    )
    request, manifest = ConversationRequestBuilder(
        BEHAVIOR_POLICY_V27,
        12_000,
        0.3,
        768,
    ).build(
        _runtime_context(retrieval_available=memory is not None),
        user_text=user_text,
        trace_id=f"checkpoint142-v27-breadth-{scenario['id']}",
        memory_context=memory,
        inclination_context=inclinations,
        emotional_context=_affect(state["affect"]),
        relationship_context=_relationship(state["relationship"]),
        recent_context=recent,
        cognition_trace=cognition,
        character_evidence=evidence,
    )
    return BreadthObservation(request, manifest, disclosure, coherence, evidence)


def test_v27_breadth_fixture_is_public_input_only_and_covers_user_specification() -> None:
    corpus = _load_corpus()
    scenarios = cast(list[dict[str, Any]], corpus["public_scenarios"])
    state_variants = cast(dict[str, dict[str, str]], corpus["state_variants"])
    observed_types = {
        str(type_name)
        for scenario in scenarios
        for type_name in cast(list[object], scenario["types"])
    }

    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert corpus["schema_version"] == 11
    assert corpus["checkpoint"] == "14.2"
    assert corpus["policy_id"] == BEHAVIOR_POLICY_V27.policy_id
    assert corpus["corpus_id"] == "satori.checkpoint142.character-breadth.ru.v11"
    assert len(observed_types) >= 26
    assert observed_types == set(cast(list[str], corpus["required_types"]))
    assert observed_types >= REQUIRED_USER_TYPES
    assert len({str(item["id"]) for item in scenarios}) == len(scenarios)
    assert not FORBIDDEN_FIXTURE_KEYS.intersection(_nested_keys(corpus))
    assert set(EXPECTED_ROUTES) == {str(item["id"]) for item in scenarios}

    for name, state in state_variants.items():
        assert name.strip()
        assert set(state) <= {"affect", "relationship", "memory", "inclination"}
        assert {"affect", "relationship"} <= set(state)
    for scenario in scenarios:
        assert set(scenario) in (
            {"id", "types", "user_text", "state_variant"},
            {"id", "types", "user_text", "state_variant", "recent_variant"},
        )
        assert str(scenario["user_text"]).strip()
        assert str(scenario["state_variant"]) in state_variants


def test_v27_breadth_routes_are_typed_and_do_not_encode_future_reply_words() -> None:
    corpus = _load_corpus()
    scenarios = cast(list[dict[str, Any]], corpus["public_scenarios"])
    state_variants = cast(dict[str, dict[str, str]], corpus["state_variants"])

    for scenario in scenarios:
        observation = _observe_scenario(scenario, state_variants)
        expected = EXPECTED_ROUTES[str(scenario["id"])]
        manifest = observation.manifest

        assert manifest.policy_id == BEHAVIOR_POLICY_V27.policy_id
        assert manifest.character_delivery_decision_schema_version == 4
        assert manifest.character_presence_projection_schema_version == 2
        assert manifest.character_delivery_goal == expected.goal
        assert manifest.character_delivery_grounding == expected.grounding
        assert manifest.character_delivery_pressure == expected.pressure
        assert manifest.character_delivery_continuation == expected.continuation
        assert observation.disclosure.primary_mode.value == expected.disclosure_mode
        assert tuple(item.value for item in observation.disclosure.required_facets) == (
            expected.disclosure_facets
        )
        assert manifest.character_presence_memory_use_licensed is expected.memory_licensed
        assert manifest.retrieval_status == expected.retrieval_status
        assert observation.request.messages[-1].content == str(scenario["user_text"])
        assert sum(MOVE_MARKER in item.content for item in observation.request.messages) == 1

    by_id = {
        str(scenario["id"]): _observe_scenario(scenario, state_variants) for scenario in scenarios
    }
    assert by_id["serious_achievement"].evidence.completed_achievement
    assert by_id["fatigue"].evidence.explicit_depletion
    assert by_id["foolish_harmful_action"].evidence.harmful_overextension
    assert by_id["disagreement"].evidence.direct_objection
    assert by_id["no_advice"].evidence.explicit_listen_request
    assert by_id["natural_initiative"].evidence.topic_closure


def test_v27_same_input_relationship_contrasts_change_only_licensed_movement() -> None:
    corpus = _load_corpus()
    scenarios = {
        str(item["id"]): item for item in cast(list[dict[str, Any]], corpus["public_scenarios"])
    }
    state_variants = cast(dict[str, dict[str, str]], corpus["state_variants"])
    contrasts = {
        str(item["id"]): tuple(str(value) for value in cast(list[object], item["scenario_ids"]))
        for item in cast(list[dict[str, object]], corpus["same_input_relationship_contrasts"])
    }

    low_id, high_id = contrasts["closeness"]
    low = _observe_scenario(scenarios[low_id], state_variants)
    high = _observe_scenario(scenarios[high_id], state_variants)
    assert scenarios[low_id]["user_text"] == scenarios[high_id]["user_text"]
    assert low.disclosure == high.disclosure
    assert low.manifest.character_delivery_goal == high.manifest.character_delivery_goal
    assert low.manifest.character_delivery_grounding == high.manifest.character_delivery_grounding
    assert low.manifest.character_delivery_pressure == high.manifest.character_delivery_pressure
    assert low.manifest.relationship_expression_profile == "fresh_undeveloped_neutral"
    assert high.manifest.relationship_expression_profile == "established_positive"
    assert low.manifest.character_delivery_voice != high.manifest.character_delivery_voice
    assert low.manifest.character_delivery_continuation == "complete"
    assert high.manifest.character_delivery_continuation == "open"
    assert low.movement != high.movement

    fresh_id, established_id, strained_id = contrasts["closure_initiative"]
    fresh = _observe_scenario(scenarios[fresh_id], state_variants)
    established = _observe_scenario(scenarios[established_id], state_variants)
    strained = _observe_scenario(scenarios[strained_id], state_variants)
    assert (
        len(
            {
                str(scenarios[fresh_id]["user_text"]),
                str(scenarios[established_id]["user_text"]),
                str(scenarios[strained_id]["user_text"]),
            }
        )
        == 1
    )
    assert fresh.disclosure == established.disclosure == strained.disclosure
    assert {
        fresh.manifest.character_delivery_goal,
        established.manifest.character_delivery_goal,
        strained.manifest.character_delivery_goal,
    } == {"close_topic"}
    assert {
        fresh.manifest.character_delivery_grounding,
        established.manifest.character_delivery_grounding,
        strained.manifest.character_delivery_grounding,
    } == {"reaction_only"}
    assert fresh.manifest.character_delivery_continuation == "complete"
    assert established.manifest.character_delivery_continuation == "open"
    assert strained.manifest.character_delivery_continuation == "complete"
    assert len({fresh.movement, established.movement, strained.movement}) == 3


def test_v27_sequential_affect_is_committed_and_carried_into_later_movements(
    migrated_database: Database,
) -> None:
    sequence = cast(dict[str, Any], _load_corpus()["affect_continuity"])
    turns = cast(list[dict[str, object]], sequence["public_turns"])
    activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    services, generator, initial_self = build_affect_services(migrated_database, appraisal)
    replies = [
        run_talk(
            services,
            request_id=f"v27-breadth-affect-{turn['turn']}",
            text_value=str(turn["user_text"]),
        )
        for turn in turns
    ]
    manifests = tuple(reply.context_manifest for reply in replies)
    emotion_versions = tuple(int(item.emotion_state_version or 0) for item in manifests)
    mood_versions = tuple(int(item.mood_state_version or 0) for item in manifests)
    movements = tuple(
        next(message.content for message in request.messages if MOVE_MARKER in message.content)
        for request in generator.requests
    )

    assert len(replies) == len(turns) == len(appraisal.requests) == len(generator.requests) == 3
    assert all(item.policy_id == BEHAVIOR_POLICY_V27.policy_id for item in manifests)
    assert all(later == earlier + 1 for earlier, later in pairwise(emotion_versions))
    assert all(later == earlier + 1 for earlier, later in pairwise(mood_versions))
    assert manifests[0].affect_expression_profile != manifests[1].affect_expression_profile
    assert manifests[0].character_presence_affect_signals != (
        manifests[1].character_presence_affect_signals
    )
    assert movements[0] != movements[1]
    identity_id = initial_self.get_self.execute().identity.identity_id
    durable = services.emotion_status.execute(identity_id).state
    assert durable.state_version == emotion_versions[-1]
    assert durable.mood_version == mood_versions[-1]
