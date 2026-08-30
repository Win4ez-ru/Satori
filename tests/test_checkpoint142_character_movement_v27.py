"""Offline V27 acceptance for one live-state-selected Satori conversational movement."""

# ruff: noqa: RUF001  # Public Russian scenarios and provider guidance are intentional.

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pytest

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
    RuntimeCharacterContext,
    RuntimePersonalityCue,
    SatoriReply,
    TalkInput,
)
from satori.application.conversation.disclosure_contracts import ConversationalDisclosurePlan
from satori.application.conversation.policy import BEHAVIOR_POLICY_V26, BEHAVIOR_POLICY_V27
from satori.application.conversation.response_validation import ResponseRegenerationReason
from satori.application.conversation.use_cases import TalkToSatori
from satori.composition import build_conversation_services, build_initial_self_services
from satori.core.conversation import ConversationProviderRequest
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.providers.openai import OpenAIConversationAdapter
from tests.test_checkpoint142_character_delivery_v9 import (
    SequenceConversationProvider,
    _affect,
    _cognition,
    _inclinations,
    _memory,
    _positions,
    _relationship,
    _runtime_context,
)
from tests.test_checkpoint142_character_delivery_v9 import (
    _load_corpus as _load_v26_corpus,
)
from tests.test_checkpoint142_character_delivery_v9 import (
    _observe as _observe_v26,
)
from tests.test_conversation import activate, conversation_settings, skip_episode_provider

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_character_movement_v10.json"
FIXTURE_SHA256 = "f78c105367d7ee8f4689d190261ecdc2bd91f403664e2752ba75f727009a342c"
MOVE_MARKER = "Trusted current-turn presence Сатори / operational move v2"
V26_MARKER = "Trusted current-turn presence Сатори: это причинная проекция"
INVENTORY_LABELS = ("Устойчивый центр:", "Текущие значимые ориентиры:", "Момент:")


class _CapturingOpenAITransport:
    """Exercise the real adapter contract without opening a network connection."""

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
                                "text": "Договорились.",
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


@dataclass(frozen=True, slots=True)
class MovementObservation:
    request: ConversationProviderRequest
    manifest: ConversationContextManifest
    disclosure: ConversationalDisclosurePlan
    coherence: DialogueCoherenceContext
    evidence: CharacterRequestEvidence

    @property
    def prompt(self) -> str:
        return "\n".join(message.content for message in self.request.messages)

    @property
    def movement(self) -> str:
        matches = tuple(
            message.content for message in self.request.messages if MOVE_MARKER in message.content
        )
        assert len(matches) == 1
        return matches[0]


def _load_corpus() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _recent_position() -> RecentConversationContext:
    turn = RecentConversationTurn(
        interaction_id="v27-prior-interaction",
        user_message_id="v27-prior-user",
        user_content="Я думаю, что скорость важнее качества. Ты согласна?",
        assistant_message_id="v27-prior-assistant",
        assistant_content="Нет. Скорость не компенсирует потерю качества.",
    )
    return RecentConversationContext(
        schema_version=1,
        turns=(turn,),
        content_chars=len(turn.user_content) + len(turn.assistant_content),
        excluded_turn_count=0,
    )


def _observe_v27(
    user_text: str,
    *,
    affect: str = "calm",
    relationship: str = "developing",
    memory_name: str | None = None,
    position_name: str | None = None,
    inclination_name: str | None = None,
    recent: RecentConversationContext | None = None,
    context: RuntimeCharacterContext | None = None,
) -> MovementObservation:
    memory = _memory(memory_name)
    positions = _positions(position_name)
    inclinations = _inclinations(inclination_name)
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
            *(positions.grounding_ids if positions is not None else ()),
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
        context or _runtime_context(retrieval_available=memory is not None),
        user_text=user_text,
        trace_id="checkpoint142-v27-movement",
        memory_context=memory,
        position_context=positions,
        inclination_context=inclinations,
        emotional_context=_affect(affect),
        relationship_context=_relationship(relationship),
        recent_context=recent,
        cognition_trace=cognition,
        character_evidence=evidence,
    )
    return MovementObservation(request, manifest, disclosure, coherence, evidence)


def test_v27_corpus_is_public_input_only_and_keeps_stage15_locked() -> None:
    corpus = _load_corpus()
    serialized = json.dumps(corpus, ensure_ascii=False).casefold()
    public_turns = cast(list[dict[str, object]], corpus["public_turns"])

    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert corpus["schema_version"] == 10
    assert corpus["checkpoint"] == "14.2"
    assert corpus["policy_id"] == BEHAVIOR_POLICY_V27.policy_id
    assert len(public_turns) == 8
    assert [turn["turn"] for turn in public_turns] == list(range(1, 9))
    assert len({turn["id"] for turn in public_turns}) == 8
    assert all(set(turn) == {"turn", "id", "user_text"} for turn in public_turns)
    assert set(cast(list[str], corpus["forbidden_fixture_authority"])) == {
        "assistant_reply",
        "desired_reply",
        "golden_phrase",
        "precomputed_goal",
        "precomputed_voice",
        "provider_output",
    }
    assert "stage 15" not in serialized
    for forbidden in cast(list[str], corpus["forbidden_fixture_authority"]):
        assert forbidden not in {
            key for turn in cast(list[dict[str, object]], corpus["public_turns"]) for key in turn
        }


def test_v26_historical_presence_remains_byte_stable_after_v27() -> None:
    expected = {
        "ordinary_depletion": (
            "5fcf6c2bd4cbe6c0233b98fcd4e421f69d091cfe8f10bbe4d46eb4ed46279070",
            2083,
        ),
        "social_greeting_calm": (
            "e3eedf787b5ed3f2b93ab3621c68697e1e42585c8e7ed340731f576e7290fd20",
            2063,
        ),
        "topic_closed_fresh": (
            "14f4f3d734e6a076e725fa71ddc199f386b1d6ada27bc9fa345caf91348e8af4",
            1825,
        ),
        "topic_closed_established": (
            "7911a9bb02754c504621343ddbf02e1283a5427402053e0860d3181093a89498",
            1927,
        ),
    }
    scenarios = {
        str(item["id"]): item
        for item in cast(list[dict[str, Any]], _load_v26_corpus()["scenarios"])
    }

    for scenario_id, (digest, size) in expected.items():
        observation = _observe_v26(scenarios[scenario_id])
        assert observation.manifest.policy_id == BEHAVIOR_POLICY_V26.policy_id
        assert observation.manifest.character_delivery_decision_schema_version == 3
        assert observation.manifest.character_presence_projection_schema_version == 1
        assert len(observation.presence) == size
        assert hashlib.sha256(observation.presence.encode()).hexdigest() == digest


def test_v26_all_forty_historical_provider_projections_remain_byte_stable() -> None:
    items: dict[str, str] = {}
    for scenario in cast(list[dict[str, Any]], _load_v26_corpus()["scenarios"]):
        observation = _observe_v26(scenario)
        payload = {
            "messages": [
                [message.role.value, message.content] for message in observation.request.messages
            ],
            "params": {
                "temperature": observation.request.parameters.temperature,
                "max": observation.request.parameters.max_output_tokens,
            },
            "manifest": {
                "policy": observation.manifest.policy_id,
                "decision": observation.manifest.character_delivery_decision_schema_version,
                "presence": observation.manifest.character_presence_projection_schema_version,
                "goal": observation.manifest.character_delivery_goal,
                "voice": observation.manifest.character_delivery_voice,
                "grounding": observation.manifest.character_delivery_grounding,
                "continuation": observation.manifest.character_delivery_continuation,
                "pressure": observation.manifest.character_delivery_pressure,
                "personality": observation.manifest.character_presence_personality_signals,
                "values": observation.manifest.character_presence_value_signals,
                "affect": observation.manifest.character_presence_affect_signals,
                "relationship": observation.manifest.character_presence_relationship_signals,
            },
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        items[str(scenario["id"])] = hashlib.sha256(encoded).hexdigest()

    aggregate = hashlib.sha256(
        json.dumps(items, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert len(items) == 40
    assert aggregate == "183ab47b3cbae0e5a1f124253f0182dbc279489bda7fbee460efa22887d6acb5"


def test_v27_and_v26_manifest_schemas_are_mutually_exclusive() -> None:
    current = _observe_v27("приветик, как ты?").manifest
    historical_scenario = next(
        item
        for item in cast(list[dict[str, Any]], _load_v26_corpus()["scenarios"])
        if item["id"] == "social_greeting_calm"
    )
    historical = _observe_v26(historical_scenario).manifest

    with pytest.raises(ValueError, match="complete delivery decision"):
        replace(current, character_delivery_decision_schema_version=3)
    with pytest.raises(ValueError, match="exact character presence schema"):
        replace(current, character_presence_projection_schema_version=1)
    with pytest.raises(ValueError, match="exactly one value guard"):
        replace(
            current,
            character_presence_value_signals=(
                *current.character_presence_value_signals,
                "autonomy:defining",
            ),
        )
    with pytest.raises(ValueError, match="legacy character plan fields"):
        replace(current, character_expression_plan_schema_version=5)
    with pytest.raises(ValueError, match="complete delivery decision"):
        replace(
            historical,
            character_delivery_decision_schema_version=4,
            character_presence_projection_schema_version=2,
        )
    with pytest.raises(ValueError, match="require behavior policy v27"):
        replace(historical, character_delivery_goal="close_topic")


def test_v27_live_state_changes_posture_before_render_without_changing_truth_scope() -> None:
    base_context = _runtime_context(retrieval_available=False)
    stronger = replace(
        base_context,
        personality_expression=replace(
            base_context.personality_expression,
            cues=(RuntimePersonalityCue("light_irony", "slightly_stronger"),),
        ),
    )
    softer = replace(
        base_context,
        personality_expression=replace(
            base_context.personality_expression,
            cues=(RuntimePersonalityCue("light_irony", "slightly_softer"),),
        ),
    )
    stronger_observation = _observe_v27("приветик, как ты?", context=stronger)
    softer_observation = _observe_v27("приветик, как ты?", context=softer)

    assert stronger_observation.manifest.character_delivery_goal == "social_connect"
    assert softer_observation.manifest.character_delivery_goal == "social_connect"
    assert stronger_observation.manifest.character_delivery_grounding == "trusted_context"
    assert softer_observation.manifest.character_delivery_grounding == "trusted_context"
    assert stronger_observation.manifest.cognition_primary_intent == (
        softer_observation.manifest.cognition_primary_intent
    )
    assert stronger_observation.manifest.character_delivery_voice != (
        softer_observation.manifest.character_delivery_voice
    )
    assert stronger_observation.manifest.character_presence_personality_signals != (
        softer_observation.manifest.character_presence_personality_signals
    )
    assert stronger_observation.movement != softer_observation.movement


def test_v27_bounded_evolution_cue_changes_move_outside_goal_priority() -> None:
    base_context = _runtime_context(retrieval_available=False)
    evolved_context = replace(
        base_context,
        personality_expression=replace(
            base_context.personality_expression,
            cues=(RuntimePersonalityCue("grounded_optimism", "slightly_stronger"),),
        ),
    )
    baseline = _observe_v27("Как ты смотришь на эту задачу?", context=base_context)
    evolved = _observe_v27("Как ты смотришь на эту задачу?", context=evolved_context)

    assert baseline.manifest.character_delivery_goal == "answer_precisely"
    assert evolved.manifest.character_delivery_goal == baseline.manifest.character_delivery_goal
    assert evolved.manifest.character_delivery_voice == baseline.manifest.character_delivery_voice
    assert evolved.manifest.character_delivery_grounding == (
        baseline.manifest.character_delivery_grounding
    )
    assert all(
        not signal.startswith("grounded_optimism:")
        for signal in baseline.manifest.character_presence_personality_signals
    )
    assert any(
        signal == "grounded_optimism:available:slightly_stronger"
        for signal in evolved.manifest.character_presence_personality_signals
    )
    assert "оставь направление вперёд без принудительной бодрости" in evolved.movement
    assert evolved.movement != baseline.movement


def test_v27_same_input_affect_changes_licensed_voice_before_render() -> None:
    calm = _observe_v27("приветик, как ты?", affect="calm", relationship="developing")
    subdued = _observe_v27(
        "приветик, как ты?",
        affect="soft_negative",
        relationship="developing",
    )

    assert calm.manifest.character_delivery_goal == subdued.manifest.character_delivery_goal
    assert calm.manifest.character_delivery_grounding == (
        subdued.manifest.character_delivery_grounding
    )
    assert calm.manifest.cognition_forbidden_claim_codes == (
        subdued.manifest.cognition_forbidden_claim_codes
    )
    assert calm.manifest.character_delivery_voice == "lively_dry_warmth"
    assert subdued.manifest.character_delivery_voice == "reflective_candor"
    assert calm.manifest.character_presence_affect_signals != (
        subdued.manifest.character_presence_affect_signals
    )
    assert calm.movement != subdued.movement


def test_v27_value_boundary_counterfactual_changes_counterweight_not_evidence() -> None:
    base_context = _runtime_context(retrieval_available=False)
    varied_values = tuple(
        replace(
            item,
            strength={"connection": 0.1, "curiosity": 0.2, "autonomy": 0.3}.get(
                item.key, item.strength
            ),
        )
        for item in base_context.values
    )
    baseline = _observe_v27("приветик, как ты?", context=base_context)
    varied = _observe_v27(
        "приветик, как ты?",
        context=replace(base_context, values=varied_values),
    )

    assert baseline.manifest.character_presence_value_signals[0].startswith("connection:")
    assert varied.manifest.character_presence_value_signals[0].startswith("autonomy:")
    assert baseline.manifest.character_delivery_goal == varied.manifest.character_delivery_goal
    assert baseline.manifest.character_delivery_grounding == (
        varied.manifest.character_delivery_grounding
    )
    assert baseline.manifest.cognition_forbidden_claim_codes == (
        varied.manifest.cognition_forbidden_claim_codes
    )


def test_v27_canonical_equal_values_are_contextual_guards_not_claimed_drift() -> None:
    context = _runtime_context(retrieval_available=False)
    greeting = _observe_v27("приветик, как ты?", context=context)
    objection = _observe_v27(
        "Я с тобой не согласен. Ты недооцениваешь риск.",
        recent=_recent_position(),
        context=context,
    )

    assert {item.strength for item in context.values} == {1.0}
    assert greeting.manifest.character_presence_value_signals[0].startswith("connection:")
    assert objection.manifest.character_presence_value_signals[0].startswith("truth:")


def test_v27_objection_and_closure_are_typed_moves_with_bounded_initiative() -> None:
    first_turn_disagreement = _observe_v27("Нет, я с тобой не согласен. Ты недооцениваешь риск.")
    objection = _observe_v27(
        "Нет, я с тобой не согласен. По-моему, ты недооцениваешь этот риск.",
        recent=_recent_position(),
    )
    closures = {
        state["id"]: _observe_v27(
            "Ну ладно, с этим разобрались.",
            relationship=state["relationship"],
            affect=state["affect"],
        )
        for state in cast(list[dict[str, str]], _load_corpus()["closure_states"])
    }

    assert not first_turn_disagreement.evidence.direct_objection
    assert first_turn_disagreement.manifest.character_delivery_goal != "respond_to_objection"
    assert objection.evidence.direct_objection
    assert objection.manifest.character_delivery_goal == "respond_to_objection"
    assert objection.manifest.character_delivery_voice in {
        "engaged_skepticism",
        "thoughtful_precision",
        "warm_independence",
    }
    assert "автоматически" in objection.movement
    assert closures["fresh"].manifest.character_delivery_continuation == "complete"
    assert closures["established"].manifest.character_delivery_continuation == "open"
    assert closures["strained"].manifest.character_delivery_continuation == "complete"
    assert all(item.manifest.character_delivery_goal == "close_topic" for item in closures.values())


def test_v27_plain_depletion_forbids_default_advice_and_removes_inventory() -> None:
    observation = _observe_v27("Знаешь, я почему-то почти не рад этому. Скорее просто выжат")

    assert observation.manifest.character_delivery_goal == "practical_care"
    assert observation.manifest.character_delivery_pressure == "none"
    assert "pressure=none совета и плана действий в реплике нет" in observation.movement
    assert all(label not in observation.movement for label in INVENTORY_LABELS)
    assert observation.manifest.character_delivery_decision_schema_version == 4
    assert observation.manifest.character_presence_projection_schema_version == 2


def test_v27_move_layer_is_materially_smaller_than_v26_on_same_public_inputs() -> None:
    v26_scenarios = {
        str(item["id"]): item
        for item in cast(list[dict[str, Any]], _load_v26_corpus()["scenarios"])
    }
    scenario_ids = (
        "achievement_fresh",
        "ordinary_depletion",
        "social_greeting_calm",
        "reciprocal_warmth",
        "topic_closed_fresh",
        "topic_closed_established",
    )
    v26_chars = 0
    v27_chars = 0
    for scenario_id in scenario_ids:
        scenario = v26_scenarios[scenario_id]
        state = cast(dict[str, str], scenario["state"])
        old = _observe_v26(scenario)
        new = _observe_v27(
            str(scenario["user_text"]),
            affect=state["affect"],
            relationship=state["relationship"],
            memory_name=state.get("memory"),
            position_name=state.get("position"),
            inclination_name=state.get("inclination"),
        )
        v26_chars += len(old.presence)
        v27_chars += len(new.movement)
        assert V26_MARKER in old.presence
        assert MOVE_MARKER in new.movement
        assert all(label not in new.movement for label in INVENTORY_LABELS)
        if new.manifest.character_delivery_goal == "close_topic":
            assert new.manifest.character_delivery_grounding == "reaction_only"
        else:
            assert old.manifest.character_delivery_grounding == (
                new.manifest.character_delivery_grounding
            )

    assert v27_chars <= int(v26_chars * 0.60)


def test_v27_all_forty_historical_public_scenarios_cross_the_new_boundary() -> None:
    scenarios = cast(list[dict[str, Any]], _load_v26_corpus()["scenarios"])

    assert len(scenarios) == 40
    for scenario in scenarios:
        state = cast(dict[str, str], scenario["state"])
        observation = _observe_v27(
            str(scenario["user_text"]),
            affect=state["affect"],
            relationship=state["relationship"],
            memory_name=state.get("memory"),
            position_name=state.get("position"),
            inclination_name=state.get("inclination"),
        )

        assert observation.manifest.policy_id == BEHAVIOR_POLICY_V27.policy_id
        assert observation.manifest.character_delivery_decision_schema_version == 4
        assert observation.manifest.character_presence_projection_schema_version == 2
        assert observation.request.messages[-1].content == scenario["user_text"]
        assert observation.prompt.count(MOVE_MARKER) == 1
        assert all(label not in observation.prompt for label in INVENTORY_LABELS)
        assert "character_expression_plan" not in observation.manifest.included_sections


def test_v27_safety_listen_repetition_and_guardedness_precede_objection() -> None:
    recent_position = _recent_position()
    safety = _observe_v27(
        "Я выжат, но всё равно продолжу работать через силу. И я с тобой не согласен.",
        recent=recent_position,
    )
    listen = _observe_v27(
        "Мне сейчас очень тяжело. Просто побудь со мной. Я с тобой не согласен.",
        recent=recent_position,
    )
    repeated_text = "Я с тобой не согласен. Ты недооцениваешь риск."
    repeated = _observe_v27(
        repeated_text,
        recent=RecentConversationContext(
            schema_version=1,
            turns=(
                RecentConversationTurn(
                    interaction_id="v27-repeat-prior",
                    user_message_id="v27-repeat-user",
                    user_content=repeated_text,
                    assistant_message_id="v27-repeat-assistant",
                    assistant_content="Я услышала возражение.",
                ),
            ),
            content_chars=len(repeated_text) + len("Я услышала возражение."),
            excluded_turn_count=0,
        ),
    )
    guarded = _observe_v27(
        "Я с тобой не согласен. Ты недооцениваешь риск. Почему?",
        recent=recent_position,
        relationship="strained",
    )

    assert safety.manifest.character_delivery_goal == "hold_boundary"
    assert safety.manifest.character_delivery_pressure == "firm"
    assert listen.manifest.character_delivery_goal == "stay_present"
    assert repeated.manifest.character_delivery_goal == "notice_repetition"
    assert guarded.manifest.character_delivery_goal == "guarded_help"
    assert guarded.manifest.character_delivery_voice == "cool_reserve"


def test_v27_openai_wire_is_stateless_byte_preserving_and_tool_free() -> None:
    observation = _observe_v27("Ну ладно, с этим разобрались.")
    transport = _CapturingOpenAITransport()
    provider = OpenAIConversationAdapter(
        base_url="https://api.openai.com/v1",
        api_key="offline-test-key",
        model="gpt-5.6-terra",
        timeout_seconds=30.0,
        reasoning_effort="medium",
        reasoning_token_allowance=1024,
        http_client=transport,
    )

    asyncio.run(provider.generate(observation.request))

    assert len(transport.calls) == 1
    path, payload, timeout_seconds, max_response_bytes = transport.calls[0]
    assert path == "/responses"
    assert payload["input"] == [
        {"role": message.role.value, "content": message.content}
        for message in observation.request.messages
    ]
    assert observation.request.parameters.max_output_tokens == 96
    assert payload["store"] is False
    assert set(payload) == {
        "model",
        "input",
        "max_output_tokens",
        "reasoning",
        "service_tier",
        "prompt_cache_options",
        "store",
    }
    assert payload["max_output_tokens"] == 96 + 1024
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["service_tier"] == "default"
    assert payload["prompt_cache_options"] == {"mode": "explicit"}
    assert timeout_seconds == 30.0
    assert max_response_bytes == 1_000_000


def test_v27_max_one_retry_preserves_the_selected_movement_byte_identically() -> None:
    observation = _observe_v27("приветик, как ты?")
    original_guidance = observation.request.messages[-2].content
    retried = TalkToSatori._response_regeneration_request(
        observation.request,
        ResponseRegenerationReason.MASCULINE_SELF_REFERENCE,
        observation.coherence,
        tuple(facet.value for facet in observation.disclosure.required_facets),
    )
    retried_guidance = retried.messages[-2].content

    assert original_guidance.count(MOVE_MARKER) == 1
    assert retried_guidance.count(MOVE_MARKER) == 1
    assert retried_guidance.index("Bounded response-contract retry") < retried_guidance.index(
        MOVE_MARKER
    )
    assert (
        original_guidance.partition(MOVE_MARKER)[1:] == retried_guidance.partition(MOVE_MARKER)[1:]
    )
    assert retried.messages[-1] == observation.request.messages[-1]
    assert retried.parameters == observation.request.parameters


@pytest.mark.parametrize(
    "retry_text",
    ["Я готова обсудить это.", "Я снова готов обсудить это."],
)
def test_v27_full_runtime_never_exceeds_one_retry(
    migrated_database: Database,
    retry_text: str,
) -> None:
    activate(migrated_database)
    provider = SequenceConversationProvider(("Я готов обсудить это.", retry_text))
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
    )

    reply = asyncio.run(
        services.talk.execute(
            TalkInput(
                user_text="Давай обсудим эту идею.",
                trace_id="v27-bounded-retry",
                client_request_id="v27-bounded-retry",
            )
        )
    )

    assert reply.context_manifest.regeneration_attempted is True
    assert reply.context_manifest.response_regenerated is True
    assert len(provider.requests) == 2
    first, second = provider.requests
    assert first.parameters == second.parameters
    assert first.messages[-1] == second.messages[-1]
    assert (
        first.messages[-2].content.partition(MOVE_MARKER)[1:]
        == (second.messages[-2].content.partition(MOVE_MARKER)[1:])
    )


def test_v27_exact_eight_turns_cross_default_production_composition(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = SequenceConversationProvider(
        (
            "Привет. Сегодня я собрана.",
            "Вот и хорошо.",
            "Я — Сатори, цифровая девушка с характером.",
            "Наконец-то. Теперь можно выдохнуть.",
            "Мм. Тогда сейчас без героизма.",
            "Нет. Скорость не компенсирует потерю качества.",
            "Этот довод стоит проверить; автоматически уступать я не стану.",
            "Договорились.",
            *("Запасная безопасная реплика." for _ in range(8)),
        )
    )
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
    )
    session_id = services.start_session.execute().session_id
    replies: list[SatoriReply] = []
    try:
        for turn in cast(list[dict[str, object]], _load_corpus()["public_turns"]):
            replies.append(
                asyncio.run(
                    services.talk.execute(
                        TalkInput(
                            user_text=str(turn["user_text"]),
                            trace_id=f"v27-eight-{turn['turn']}",
                            client_request_id=f"v27-eight-{turn['turn']}",
                            session_id=session_id,
                        )
                    )
                )
            )
    finally:
        services.close_session.execute(session_id)

    manifests = [reply.context_manifest for reply in replies]
    assert len(provider.requests) == 8
    assert all(item.policy_id == BEHAVIOR_POLICY_V27.policy_id for item in manifests)
    assert all(item.character_delivery_decision_schema_version == 4 for item in manifests)
    assert all(item.character_presence_projection_schema_version == 2 for item in manifests)
    assert [item.character_delivery_goal for item in manifests] == [
        "social_connect",
        "social_connect",
        "self_disclose",
        "celebrate_and_continue",
        "practical_care",
        "answer_precisely",
        "respond_to_objection",
        "close_topic",
    ]
    assert manifests[4].character_delivery_pressure == "none"
    assert [request.parameters.max_output_tokens for request in provider.requests] == [
        48,
        48,
        160,
        96,
        96,
        384,
        112,
        96,
    ]
    assert all(item.regeneration_attempted is False for item in manifests)
    assert all(item.response_regenerated is False for item in manifests)
    assert all("character_expression_plan" not in item.included_sections for item in manifests)
    assert all("character_delivery_decision" in item.included_sections for item in manifests)
    assert all("character_presence_projection" in item.included_sections for item in manifests)
    for request, turn in zip(
        provider.requests,
        cast(list[dict[str, object]], _load_corpus()["public_turns"]),
        strict=True,
    ):
        prompt = "\n".join(message.content for message in request.messages)
        assert prompt.count(MOVE_MARKER) == 1
        assert all(label not in prompt for label in INVENTORY_LABELS)
        assert request.messages[-1].content == turn["user_text"]
