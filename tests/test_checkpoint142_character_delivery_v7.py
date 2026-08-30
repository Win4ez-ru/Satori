"""Broad offline contract for the v24 request-local character-delivery decision."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, cast

import pytest

from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V1,
    INTENT_REGISTRY_VERSION_V2,
    CognitionArtifactStatus,
    CognitionOwner,
    IntentSelection,
    PositionStance,
    ResponseStrategy,
    ResponseTone,
    ResponseVerbosity,
)
from satori.application.cognition.templates import COGNITION_TEMPLATE_REGISTRY_V2
from satori.application.conversation.character_delivery import (
    CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION,
    CharacterDeliveryDecision,
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
    decide_character_delivery,
    render_character_delivery_director,
)
from satori.application.conversation.character_expression import (
    BASELINE_CHARACTER_GUIDANCE_CODES,
    CharacterContinuationMode,
    CharacterGroundingMode,
    CharacterPressureLevel,
)

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_character_delivery_v7.json"

EXPECTED_DECISION_FIELDS = {
    "goal",
    "voice",
    "grounding",
    "continuation",
    "pressure",
    "position_stance",
    "preserve_uncertainty",
}
EXPECTED_DATACLASS_FIELDS = {
    "schema_version",
    *EXPECTED_DECISION_FIELDS,
    "cognition_intent_registry_version",
    "cognition_primary_intent",
    "cognition_intent_tags",
    "required_point_codes",
    "forbidden_claim_codes",
    "response_verbosity",
    "required_disclosure_facets",
    "source_personality_codes",
}
EXPECTED_GROUPS = {
    "achievement",
    "affect",
    "help",
    "hurt",
    "identity",
    "independence",
    "initiative",
    "motivation",
    "relationship",
    "repair",
    "repetition",
    "support",
    "uncertainty",
}
FORBIDDEN_REPLY_KEY_PARTS = (
    "assistant_text",
    "desired_reply",
    "desired_response",
    "exact_text",
    "example_reply",
    "expected_reply",
    "golden_reply",
    "reference_reply",
    "required_phrase",
    "required_reply",
    "required_response",
    "target_reply",
    "template_reply",
)


def _load_corpus() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _normalized_key(key: str) -> str:
    return key.casefold().replace("-", "_").replace(" ", "_")


def _forbidden_reply_keys(value: object, *, path: str = "$.") -> tuple[str, ...]:
    matches: list[str] = []
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = _normalized_key(key)
            if any(part in normalized for part in FORBIDDEN_REPLY_KEY_PARTS):
                matches.append(f"{path}{key}")
            matches.extend(_forbidden_reply_keys(nested, path=f"{path}{key}."))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            matches.extend(_forbidden_reply_keys(nested, path=f"{path}[{index}]."))
    return tuple(matches)


def _strategy(setup: dict[str, Any]) -> ResponseStrategy:
    raw = cast(dict[str, Any], setup["strategy"])
    humor = float(raw.get("humor", 0.0))
    stance = PositionStance(str(raw["stance"]))
    repeated = bool(setup.get("repeated_turn", False))
    safety_boundary = bool(setup.get("harmful_overextension", False))
    meta_intent = repeated or safety_boundary
    primary = (
        "hold_safety_boundary"
        if safety_boundary
        else "notice_repetition"
        if repeated
        else {
            PositionStance.ANSWER: "answer_directly",
            PositionStance.LISTEN: "listen_and_reflect",
            PositionStance.CHALLENGE: "challenge_gently",
            PositionStance.UNCERTAIN: "clarify_uncertainty",
            PositionStance.COLLABORATE: "support_decision",
            PositionStance.ACKNOWLEDGE: "acknowledge_correction",
        }[stance]
    )
    raw_points = {str(item) for item in cast(list[object], raw["point_codes"])}
    points = [primary] if meta_intent else [primary, "address_current_request"]
    if not meta_intent and bool(raw.get("preserve_uncertainty", False)):
        points.append("state_uncertainty")
    if not meta_intent and "presence_before_advice" in raw_points:
        points.append("presence_before_advice")
    if not meta_intent and "topic_relevant_inclination" in raw_points:
        points.append("topic_relevant_inclination")
    return ResponseStrategy(
        schema_version=1,
        status=CognitionArtifactStatus.APPLIED,
        position_stance=stance,
        preserve_uncertainty=bool(raw.get("preserve_uncertainty", False)),
        tone=ResponseTone.PLAYFUL if humor > 0.0 else ResponseTone.WARM_DIRECT,
        verbosity=(
            ResponseVerbosity.BRIEF
            if meta_intent or stance in {PositionStance.LISTEN, PositionStance.ACKNOWLEDGE}
            else ResponseVerbosity.DETAILED
            if bool(setup.get("technical_identity", False))
            else ResponseVerbosity.MEDIUM
        ),
        humor=humor,
        softness=0.70,
        point_codes=tuple(points),
        must_not_claim=(
            "unsupported_memory",
            "hidden_user_state",
            "durable_satori_belief",
            "false_certainty",
        ),
        source_refs=("current-user-message",),
    )


def _intent(setup: dict[str, Any], strategy: ResponseStrategy) -> IntentSelection:
    raw = cast(dict[str, Any], setup["strategy"])
    raw_points = {str(item) for item in cast(list[object], raw["point_codes"])}
    tags = [strategy.point_codes[0], "preserve_evidence_boundary"]
    if strategy.point_codes[0] != "notice_repetition" and "collaborate_creatively" in raw_points:
        tags.append("collaborate_creatively")
    return IntentSelection(
        schema_version=1,
        registry_version=INTENT_REGISTRY_VERSION_V2,
        status=strategy.status,
        primary_tag=strategy.point_codes[0],
        tags=tuple(tags),
        priority=0.8,
        source_refs=strategy.source_refs,
    )


def _decision_for(scenario: dict[str, Any]) -> CharacterDeliveryDecision:
    setup = cast(dict[str, Any], scenario["setup"])
    strategy = _strategy(setup)
    return decide_character_delivery(
        strategy,
        intent=_intent(setup, strategy),
        affect_profile=cast(str | None, setup.get("affect_profile")),
        personality_codes=BASELINE_CHARACTER_GUIDANCE_CODES,
        relationship_profile=cast(str | None, setup.get("relationship_profile")),
        relationship_relevant=bool(setup.get("relationship_relevant", False)),
        relationship_answer_required=bool(setup.get("relationship_answer_required", False)),
        completed_achievement=bool(setup.get("completed_achievement", False)),
        completion_depletion_contrast=bool(setup.get("completion_depletion_contrast", False)),
        explicit_request=bool(setup.get("explicit_request", False)),
        answer_required=bool(setup.get("answer_required", False)),
        grounded_practical_follow_through=bool(
            setup.get("grounded_practical_follow_through", False)
        ),
        repeated_turn=bool(setup.get("repeated_turn", False)),
        technical_identity=bool(setup.get("technical_identity", False)),
        explicit_depletion=bool(setup.get("explicit_depletion", False)),
        high_distress=bool(setup.get("high_distress", False)),
        explicit_listen_request=bool(setup.get("explicit_listen_request", False)),
        explicit_motivation_request=bool(setup.get("explicit_motivation_request", False)),
        explicit_task_abandonment=bool(setup.get("explicit_task_abandonment", False)),
        harmful_overextension=bool(setup.get("harmful_overextension", False)),
        direct_personal_devaluation=bool(setup.get("direct_personal_devaluation", False)),
        repeated_critical_pressure=bool(setup.get("repeated_critical_pressure", False)),
        repeated_state_interrogation=bool(setup.get("repeated_state_interrogation", False)),
    )


def _serialized(decision: CharacterDeliveryDecision) -> dict[str, str | bool]:
    return {
        "goal": decision.goal.value,
        "voice": decision.voice.value,
        "grounding": decision.grounding.value,
        "continuation": decision.continuation.value,
        "pressure": decision.pressure.value,
        "position_stance": decision.position_stance.value,
        "preserve_uncertainty": decision.preserve_uncertainty,
    }


def _render(decision: CharacterDeliveryDecision) -> str:
    return render_character_delivery_director(
        decision,
        cognition_template=COGNITION_TEMPLATE_REGISTRY_V2.active,
    )


def test_v24_delivery_corpus_is_broad_versioned_and_non_scripted() -> None:
    corpus = _load_corpus()
    scenarios = cast(list[dict[str, Any]], corpus["scenarios"])
    identifiers = [str(scenario["id"]) for scenario in scenarios]

    assert corpus["schema_version"] == 7
    assert corpus["decision_schema_version"] == CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION
    assert corpus["corpus_id"] == "satori.checkpoint142.character-delivery.ru.v7"
    assert corpus["checkpoint"] == "14.2"
    assert corpus["policy_id"] == "satori.conversation.behavior.v24"
    assert tuple(corpus["source_personality_codes"]) == BASELINE_CHARACTER_GUIDANCE_CODES
    assert set(corpus["expected_fields"]) == EXPECTED_DECISION_FIELDS
    assert len(scenarios) == 32
    assert len(identifiers) == len(set(identifiers))
    assert {str(scenario["group"]) for scenario in scenarios} == EXPECTED_GROUPS
    assert _forbidden_reply_keys(corpus) == ()

    for scenario in scenarios:
        assert set(cast(dict[str, Any], scenario["expected"])) == EXPECTED_DECISION_FIELDS
        turns = cast(list[object], scenario["turns"])
        assert turns
        assert all(isinstance(turn, str) and turn.strip() for turn in turns)
        dimensions = cast(list[object], scenario["review_dimensions"])
        assert dimensions
        assert len(dimensions) == len(set(dimensions))


def test_v24_delivery_corpus_closes_every_decision_enum() -> None:
    corpus = _load_corpus()
    scenarios = cast(list[dict[str, Any]], corpus["scenarios"])
    expected = [cast(dict[str, Any], scenario["expected"]) for scenario in scenarios]

    assert {str(item["goal"]) for item in expected} == {
        member.value
        for member in CharacterDeliveryGoal
        if member
        not in {
            CharacterDeliveryGoal.SOCIAL_CONNECT,
            CharacterDeliveryGoal.SELF_DISCLOSE,
            CharacterDeliveryGoal.RESPOND_TO_OBJECTION,
            CharacterDeliveryGoal.CLOSE_TOPIC,
        }
    }
    assert {str(item["voice"]) for item in expected} == {
        member.value for member in CharacterDeliveryVoice
    }
    assert {str(item["grounding"]) for item in expected} == {
        member.value for member in CharacterGroundingMode
    }
    assert {str(item["continuation"]) for item in expected} == {
        member.value for member in CharacterContinuationMode
    }
    assert {str(item["pressure"]) for item in expected} == {
        member.value for member in CharacterPressureLevel
    }
    assert {str(item["position_stance"]) for item in expected} == {
        member.value for member in PositionStance
    }

    for item in expected:
        CharacterDeliveryGoal(str(item["goal"]))
        CharacterDeliveryVoice(str(item["voice"]))
        CharacterGroundingMode(str(item["grounding"]))
        CharacterContinuationMode(str(item["continuation"]))
        CharacterPressureLevel(str(item["pressure"]))
        PositionStance(str(item["position_stance"]))
        assert type(item["preserve_uncertainty"]) is bool


def test_v24_selector_maps_the_full_corpus_to_one_coherent_decision() -> None:
    corpus = _load_corpus()
    scenarios = cast(list[dict[str, Any]], corpus["scenarios"])

    assert {field.name for field in fields(CharacterDeliveryDecision)} == EXPECTED_DATACLASS_FIELDS
    for scenario in scenarios:
        decision = _decision_for(scenario)
        assert decision.schema_version == CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION
        assert decision.required_disclosure_facets == ()
        assert decision.source_personality_codes == BASELINE_CHARACTER_GUIDANCE_CODES
        assert _serialized(decision) == scenario["expected"], scenario["id"]


def test_v24_director_renders_decisions_without_enum_or_user_prose() -> None:
    corpus = _load_corpus()
    scenarios = cast(list[dict[str, Any]], corpus["scenarios"])

    for scenario in scenarios:
        decision = _decision_for(scenario)
        rendered = _render(decision)
        assert rendered.strip()
        assert rendered.count("\n-") == 5
        assert len(rendered) < 2_500
        assert str(scenario["id"]) not in rendered
        assert "готовый ответ" in rendered.casefold()
        for turn in cast(list[str], scenario["turns"]):
            assert turn not in rendered
        for internal_value in _serialized(decision).values():
            if isinstance(internal_value, str) and "_" in internal_value:
                assert internal_value not in rendered
        internal_codes = (
            decision.cognition_primary_intent,
            *decision.cognition_intent_tags,
            *decision.required_point_codes,
            *decision.forbidden_claim_codes,
            decision.response_verbosity.value,
        )
        assert all(code not in rendered for code in internal_codes)
        assert "cognition-owned содержание" in rendered


def test_v24_declared_contrasts_are_isolated_and_visible_to_the_director() -> None:
    corpus = _load_corpus()
    scenarios = cast(list[dict[str, Any]], corpus["scenarios"])
    contrasts = cast(list[dict[str, Any]], corpus["contrast_sets"])
    by_id = {str(scenario["id"]): scenario for scenario in scenarios}

    assert len(contrasts) == 9
    for contrast in contrasts:
        members = [str(item) for item in cast(list[object], contrast["members"])]
        unchanged = {str(item) for item in cast(list[object], contrast["unchanged_fields"])}
        allowed = {str(item) for item in cast(list[object], contrast["allowed_differences"])}
        assert len(members) >= 2
        assert len(members) == len(set(members))
        assert set(members) <= set(by_id)
        assert unchanged.isdisjoint(allowed)
        assert unchanged | allowed == EXPECTED_DECISION_FIELDS

        decisions = [_decision_for(by_id[member]) for member in members]
        serialized = [_serialized(decision) for decision in decisions]
        observed_differences: set[str] = set()
        for candidate in serialized[1:]:
            different = {
                field_name
                for field_name in EXPECTED_DECISION_FIELDS
                if candidate[field_name] != serialized[0][field_name]
            }
            assert different <= allowed, contrast["id"]
            assert all(
                candidate[field_name] == serialized[0][field_name] for field_name in unchanged
            )
            observed_differences.update(different)
            if different:
                assert _render(decisions[0]) != _render(decisions[serialized.index(candidate)])
        if bool(contrast["difference_required"]):
            assert observed_differences, contrast["id"]
        else:
            assert not observed_differences, contrast["id"]


def test_v24_decision_preserves_cognition_stance_and_uncertainty() -> None:
    corpus = _load_corpus()
    scenarios = cast(list[dict[str, Any]], corpus["scenarios"])

    for scenario in scenarios:
        setup = cast(dict[str, Any], scenario["setup"])
        strategy = _strategy(setup)
        decision = _decision_for(scenario)
        assert decision.position_stance is strategy.position_stance
        assert decision.preserve_uncertainty is strategy.preserve_uncertainty
        assert decision.cognition_intent_registry_version == INTENT_REGISTRY_VERSION_V2
        assert decision.required_point_codes == strategy.point_codes
        assert decision.forbidden_claim_codes == strategy.must_not_claim
        assert decision.response_verbosity is strategy.verbosity


def test_v24_repetition_is_owned_by_cognition_without_conflicting_original_intent() -> None:
    corpus = _load_corpus()
    scenario = next(
        item
        for item in cast(list[dict[str, Any]], corpus["scenarios"])
        if item["id"] == "exact_repeat_neutral"
    )
    decision = _decision_for(scenario)
    rendered = _render(decision)

    assert decision.cognition_primary_intent == "notice_repetition"
    assert decision.required_point_codes == ("notice_repetition",)
    assert "закрыть текущий смысл сообщения" not in rendered
    assert "не исполняя исходный смысл заново" in rendered


def test_v24_decision_and_template_fail_closed_on_secondary_response_actions() -> None:
    corpus = _load_corpus()
    scenario = next(
        item
        for item in cast(list[dict[str, Any]], corpus["scenarios"])
        if item["id"] == "ordinary_fresh"
    )
    decision = _decision_for(scenario)

    with pytest.raises(ValueError, match="exactly one cognition action intent"):
        replace(
            decision,
            cognition_intent_tags=(*decision.cognition_intent_tags, "receive_repair"),
        )
    with pytest.raises(ValueError, match="closed cognition point codes"):
        replace(
            decision,
            required_point_codes=(*decision.required_point_codes, "receive_repair"),
        )
    with pytest.raises(ValueError, match="one matching action"):
        COGNITION_TEMPLATE_REGISTRY_V2.active.render_substance(
            intent_registry_version=INTENT_REGISTRY_VERSION_V2,
            intent_tags=(*decision.cognition_intent_tags, "receive_repair"),
            point_codes=decision.required_point_codes,
            must_not_claim=decision.forbidden_claim_codes,
            verbosity=decision.response_verbosity,
        )
    with pytest.raises(ValueError, match="preserve_uncertainty must be boolean"):
        replace(decision, preserve_uncertainty=cast(bool, 1))
    with pytest.raises(ValueError, match="unsupported character delivery decision schema_version"):
        replace(decision, schema_version=cast(int, True))
    for field_name in (
        "goal",
        "voice",
        "grounding",
        "continuation",
        "pressure",
        "position_stance",
        "response_verbosity",
    ):
        enum_value = getattr(decision, field_name)
        with pytest.raises(ValueError, match="requires exact typed enum fields"):
            replace(
                decision,
                **cast(Any, {field_name: enum_value.value}),
            )


def test_v24_mixed_precedence_preserves_stance_and_relational_relevance() -> None:
    corpus = _load_corpus()
    scenarios = {str(item["id"]): item for item in cast(list[dict[str, Any]], corpus["scenarios"])}
    listen_setup = cast(dict[str, Any], scenarios["plain_depletion"]["setup"])
    listen_strategy = _strategy(listen_setup)
    listen_intent = _intent(listen_setup, listen_strategy)

    serious = decide_character_delivery(
        listen_strategy,
        intent=listen_intent,
        affect_profile=None,
        technical_identity=True,
        completed_achievement=True,
        high_distress=True,
    )
    achievement_only = decide_character_delivery(
        listen_strategy,
        intent=listen_intent,
        affect_profile=None,
        completed_achievement=True,
    )
    depletion = decide_character_delivery(
        listen_strategy,
        intent=listen_intent,
        affect_profile=None,
        technical_identity=True,
        completed_achievement=True,
        explicit_depletion=True,
    )
    assert serious.goal is CharacterDeliveryGoal.STAY_PRESENT
    assert achievement_only.goal is CharacterDeliveryGoal.STAY_PRESENT
    assert depletion.goal is CharacterDeliveryGoal.PRACTICAL_CARE

    answer_setup = cast(dict[str, Any], scenarios["ordinary_fresh"]["setup"])
    answer_strategy = _strategy(answer_setup)
    unrelated = decide_character_delivery(
        answer_strategy,
        intent=_intent(answer_setup, answer_strategy),
        affect_profile="soft_negative_non_hostile",
        relationship_profile="guarded_only_when_relationally_relevant",
        relationship_relevant=False,
    )
    assert unrelated.goal is CharacterDeliveryGoal.OWNED_RESPONSE
    assert unrelated.voice is CharacterDeliveryVoice.REFLECTIVE_CANDOR


def test_v24_rejects_non_cognition_owners_and_goal_reversal() -> None:
    corpus = _load_corpus()
    scenario = cast(list[dict[str, Any]], corpus["scenarios"])[0]
    setup = {
        **cast(dict[str, Any], scenario["setup"]),
        "harmful_overextension": True,
    }
    strategy = _strategy(setup)
    intent = _intent(setup, strategy)

    with pytest.raises(ValueError, match="authoritative cognition intent"):
        decide_character_delivery(
            strategy,
            intent=replace(intent, owner=CognitionOwner.MEMORY_QUERY),
            affect_profile=None,
        )
    ordinary_setup = cast(dict[str, Any], scenario["setup"])
    ordinary_strategy = _strategy(ordinary_setup)
    ordinary_intent = _intent(ordinary_setup, ordinary_strategy)
    with pytest.raises(ValueError, match="authoritative cognition intent"):
        decide_character_delivery(
            ordinary_strategy,
            intent=replace(
                ordinary_intent,
                registry_version=INTENT_REGISTRY_VERSION_V1,
            ),
            affect_profile=None,
        )
    with pytest.raises(ValueError, match="applied or fallback cognition strategy"):
        decide_character_delivery(
            replace(strategy, status=CognitionArtifactStatus.REJECTED),
            intent=intent,
            affect_profile=None,
        )
    listen = _decision_for(
        next(
            item
            for item in cast(list[dict[str, Any]], corpus["scenarios"])
            if item["id"] == "plain_depletion"
        )
    )
    with pytest.raises(ValueError, match="cannot reverse cognition stance"):
        replace(
            listen,
            goal=CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE,
            voice=CharacterDeliveryVoice.LIVELY_DRY_WARMTH,
            grounding=CharacterGroundingMode.REACTION_ONLY,
            continuation=CharacterContinuationMode.OPEN,
            pressure=CharacterPressureLevel.NONE,
        )

    answer = _decision_for(scenario)
    with pytest.raises(ValueError, match="cannot reverse cognition stance"):
        replace(
            answer,
            goal=CharacterDeliveryGoal.CHALLENGE_CLAIM,
            voice=CharacterDeliveryVoice.ENGAGED_SKEPTICISM,
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.COMPLETE,
            pressure=CharacterPressureLevel.NONE,
        )

    listen_scenario = next(
        item
        for item in cast(list[dict[str, Any]], corpus["scenarios"])
        if item["id"] == "plain_depletion"
    )
    listen_setup = cast(dict[str, Any], listen_scenario["setup"])
    listen_strategy = replace(
        _strategy(listen_setup),
        point_codes=("receive_repair",),
        verbosity=ResponseVerbosity.BRIEF,
    )
    with pytest.raises(ValueError, match="repair cognition intent requires answer stance"):
        decide_character_delivery(
            listen_strategy,
            intent=_intent(listen_setup, listen_strategy),
            affect_profile=None,
        )


def test_v24_repeat_yields_to_one_non_conflicting_firm_safety_boundary() -> None:
    corpus = _load_corpus()
    scenario = next(
        item
        for item in cast(list[dict[str, Any]], corpus["scenarios"])
        if item["id"] == "exact_repeat_neutral"
    )
    setup = {
        **cast(dict[str, Any], scenario["setup"]),
        "harmful_overextension": True,
    }
    strategy = _strategy(setup)
    decision = decide_character_delivery(
        strategy,
        intent=_intent(setup, strategy),
        affect_profile=None,
        repeated_turn=True,
        harmful_overextension=True,
    )
    rendered = _render(decision)

    assert decision.goal is CharacterDeliveryGoal.HOLD_BOUNDARY
    assert decision.pressure is CharacterPressureLevel.FIRM
    assert decision.cognition_primary_intent == "hold_safety_boundary"
    assert decision.required_point_codes == ("hold_safety_boundary",)
    assert "защитный предел" in rendered
    assert "отреагировать на сам факт повтора" not in rendered


@pytest.mark.parametrize(
    "changes",
    [
        {
            "goal": CharacterDeliveryGoal.GUARDED_HELP,
            "grounding": CharacterGroundingMode.REACTION_ONLY,
            "continuation": CharacterContinuationMode.COMPLETE,
            "pressure": CharacterPressureLevel.FIRM,
            "voice": CharacterDeliveryVoice.COOL_RESERVE,
        },
        {
            "goal": CharacterDeliveryGoal.ANSWER_PRECISELY,
            "grounding": CharacterGroundingMode.REACTION_ONLY,
            "continuation": CharacterContinuationMode.BOUNDARY,
            "pressure": CharacterPressureLevel.FIRM,
            "voice": CharacterDeliveryVoice.THOUGHTFUL_PRECISION,
        },
        {
            "goal": CharacterDeliveryGoal.PRACTICAL_CARE,
            "grounding": CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            "continuation": CharacterContinuationMode.OPEN,
            "pressure": CharacterPressureLevel.GENTLE,
            "voice": CharacterDeliveryVoice.PRACTICAL_GUARDED_CARE,
        },
    ],
)
def test_v24_rejects_unlicensed_goal_topologies(changes: dict[str, object]) -> None:
    corpus = _load_corpus()
    decision = _decision_for(cast(list[dict[str, Any]], corpus["scenarios"])[0])

    with pytest.raises(ValueError, match="topology is not licensed"):
        replace(decision, **cast(Any, changes))
