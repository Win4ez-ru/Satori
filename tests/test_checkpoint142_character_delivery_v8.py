"""Deterministic v25 social and self-disclosure delivery contract."""

# ruff: noqa: RUF001  # Russian director constraints intentionally use Cyrillic.

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V2,
    CognitionArtifactStatus,
    IntentSelection,
    PositionStance,
    ResponseStrategy,
    ResponseTone,
    ResponseVerbosity,
)
from satori.application.cognition.templates import (
    COGNITION_TEMPLATE_REGISTRY_V2,
    COGNITION_TEMPLATE_REGISTRY_V3,
)
from satori.application.conversation.character_delivery import (
    CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION,
    CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION,
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
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosureMode,
    DisclosureFacet,
    DisclosureRequestKind,
)

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_character_delivery_v8.json"

EXPECTED_FIELDS = {
    "goal",
    "voice",
    "grounding",
    "continuation",
    "pressure",
    "position_stance",
    "preserve_uncertainty",
    "required_disclosure_facets",
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
FORBIDDEN_OUTPUT_JUDGING_KEY_PARTS = (
    "humanity_score",
    "human_likeness",
    "phrase_hit",
    "phrase_match",
    "sentiment_score",
    "style_score",
)


def _load_corpus() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _normalized_key(key: str) -> str:
    return key.casefold().replace("-", "_").replace(" ", "_")


def _forbidden_keys(value: object, *, path: str = "$.") -> tuple[str, ...]:
    matches: list[str] = []
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = _normalized_key(key)
            if any(part in normalized for part in FORBIDDEN_REPLY_KEY_PARTS):
                matches.append(f"{path}{key}")
            if any(part in normalized for part in FORBIDDEN_OUTPUT_JUDGING_KEY_PARTS):
                matches.append(f"{path}{key}")
            matches.extend(_forbidden_keys(nested, path=f"{path}{key}."))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            matches.extend(_forbidden_keys(nested, path=f"{path}[{index}]."))
    return tuple(matches)


def _strategy(setup: dict[str, Any]) -> ResponseStrategy:
    raw = cast(dict[str, Any], setup["strategy"])
    stance = PositionStance(str(raw["stance"]))
    assert stance is PositionStance.ANSWER
    return ResponseStrategy(
        schema_version=1,
        status=CognitionArtifactStatus.APPLIED,
        position_stance=stance,
        preserve_uncertainty=bool(raw.get("preserve_uncertainty", False)),
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


def _facets(setup: dict[str, Any]) -> tuple[DisclosureFacet, ...]:
    return tuple(
        DisclosureFacet(str(item))
        for item in cast(list[object], setup.get("required_disclosure_facets", []))
    )


def _decision_for(
    scenario: dict[str, Any],
    *,
    corpus_schema_version: int,
) -> CharacterDeliveryDecision:
    setup = cast(dict[str, Any], scenario["setup"])
    strategy = _strategy(setup)
    decision_schema_version = int(setup.get("decision_schema_version", corpus_schema_version))
    disclosure_mode = ConversationalDisclosureMode(str(setup["disclosure_mode"]))
    disclosure_facets = _facets(setup)
    disclosure_request_kind = (
        DisclosureRequestKind.SATORI_SELF
        if decision_schema_version >= CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION
        and (
            disclosure_mode is ConversationalDisclosureMode.PERSONAL_IDENTITY
            or (
                disclosure_mode is ConversationalDisclosureMode.SOCIAL
                and DisclosureFacet.AFFECT in disclosure_facets
            )
        )
        else DisclosureRequestKind.NONE
    )
    return decide_character_delivery(
        strategy,
        intent=_intent(strategy),
        affect_profile=cast(str | None, setup.get("affect_profile")),
        personality_codes=BASELINE_CHARACTER_GUIDANCE_CODES,
        relationship_profile=cast(str | None, setup.get("relationship_profile")),
        relationship_relevant=bool(setup.get("relationship_relevant", False)),
        explicit_request=bool(setup.get("explicit_request", False)),
        answer_required=bool(setup.get("answer_required", False)),
        depletion_follow_through=bool(setup.get("depletion_follow_through", False)),
        decision_schema_version=decision_schema_version,
        disclosure_mode=disclosure_mode,
        required_disclosure_facets=disclosure_facets,
        disclosure_request_kind=disclosure_request_kind,
    )


def _serialized(decision: CharacterDeliveryDecision) -> dict[str, str | bool | list[str]]:
    return {
        "goal": decision.goal.value,
        "voice": decision.voice.value,
        "grounding": decision.grounding.value,
        "continuation": decision.continuation.value,
        "pressure": decision.pressure.value,
        "position_stance": decision.position_stance.value,
        "preserve_uncertainty": decision.preserve_uncertainty,
        "required_disclosure_facets": [
            facet.value for facet in decision.required_disclosure_facets
        ],
    }


def _director(decision: CharacterDeliveryDecision) -> str:
    registry = (
        COGNITION_TEMPLATE_REGISTRY_V3
        if decision.schema_version >= CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION
        else COGNITION_TEMPLATE_REGISTRY_V2
    )
    return render_character_delivery_director(
        decision,
        cognition_template=registry.active,
    )


def test_v25_delivery_corpus_is_versioned_typed_and_contains_no_reference_replies() -> None:
    corpus = _load_corpus()
    scenarios = cast(list[dict[str, Any]], corpus["scenarios"])
    identifiers = [str(scenario["id"]) for scenario in scenarios]

    assert corpus["schema_version"] == 8
    assert corpus["decision_schema_version"] == CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION
    assert corpus["corpus_id"] == "satori.checkpoint142.character-delivery.ru.v8"
    assert corpus["checkpoint"] == "14.2"
    assert corpus["policy_id"] == "satori.conversation.behavior.v25"
    assert tuple(corpus["source_personality_codes"]) == BASELINE_CHARACTER_GUIDANCE_CODES
    assert set(corpus["expected_fields"]) == EXPECTED_FIELDS
    assert len(scenarios) == 12
    assert len(identifiers) == len(set(identifiers))
    assert {str(scenario["group"]) for scenario in scenarios} == {
        "schema_isolation",
        "self_disclosure",
        "social",
        "support",
    }
    assert _forbidden_keys(corpus) == ()

    for scenario in scenarios:
        assert set(cast(dict[str, Any], scenario["expected"])) == EXPECTED_FIELDS
        turns = cast(list[object], scenario["turns"])
        assert turns
        assert all(isinstance(turn, str) and turn.strip() for turn in turns)
        dimensions = cast(list[object], scenario["review_dimensions"])
        assert dimensions
        assert len(dimensions) == len(set(dimensions))


def test_v25_selector_maps_social_self_disclosure_and_follow_through_corpus() -> None:
    corpus = _load_corpus()
    schema_version = int(corpus["decision_schema_version"])

    for scenario in cast(list[dict[str, Any]], corpus["scenarios"]):
        decision = _decision_for(scenario, corpus_schema_version=schema_version)
        assert decision.source_personality_codes == BASELINE_CHARACTER_GUIDANCE_CODES
        assert _serialized(decision) == scenario["expected"], scenario["id"]


def test_v25_director_preserves_typed_scope_without_user_or_reference_prose() -> None:
    corpus = _load_corpus()
    schema_version = int(corpus["decision_schema_version"])

    for scenario in cast(list[dict[str, Any]], corpus["scenarios"]):
        decision = _decision_for(scenario, corpus_schema_version=schema_version)
        rendered = _director(decision)

        assert rendered.strip()
        assert rendered.count("Единая request-local режиссура") == 1
        assert rendered.count("\n-") == (6 if decision.required_disclosure_facets else 5)
        assert len(rendered) < 3_000
        assert str(scenario["id"]) not in rendered
        for turn in cast(list[str], scenario["turns"]):
            assert turn not in rendered
        for internal_code in (
            decision.goal.value,
            decision.voice.value,
            decision.grounding.value,
            decision.continuation.value,
            decision.pressure.value,
            decision.cognition_primary_intent,
            *decision.cognition_intent_tags,
            *decision.required_point_codes,
            *decision.forbidden_claim_codes,
            decision.response_verbosity.value,
        ):
            assert internal_code not in rendered

        if decision.required_disclosure_facets:
            assert rendered.count("Запрошенные self-facets") == 1
            scope_line = next(
                line
                for line in rendered.splitlines()
                if line.startswith("- Запрошенные self-facets:")
            )
            for facet in decision.required_disclosure_facets:
                assert scope_line.count(facet.value) == 1
        else:
            assert "Запрошенные self-facets" not in rendered


def test_v25_goal_directors_encode_scope_without_scripted_reply_text() -> None:
    corpus = _load_corpus()
    scenarios = {str(item["id"]): item for item in cast(list[dict[str, Any]], corpus["scenarios"])}
    schema_version = int(corpus["decision_schema_version"])

    social = _director(
        _decision_for(
            scenarios["manual_social_greeting_developing"],
            corpus_schema_version=schema_version,
        )
    )
    disclosure = _director(
        _decision_for(
            scenarios["manual_broad_self_disclosure_developing"],
            corpus_schema_version=schema_version,
        )
    )
    follow_through = _director(
        _decision_for(
            scenarios["depletion_follow_through"],
            corpus_schema_version=schema_version,
        )
    )

    assert "инвентаризацию состояния" in social
    assert "абстрактный афоризм" in social
    assert "все прямо запрошенные" in disclosure
    assert "одной личной связной дуге" in disclosure
    assert "причинное психологическое объяснение" in follow_through
    assert "Без программы восстановления" in follow_through
    assert "Не дави и не мобилизуй" in follow_through


def test_v25_expression_variants_change_only_licensed_delivery_fields() -> None:
    corpus = _load_corpus()
    scenarios = {str(item["id"]): item for item in cast(list[dict[str, Any]], corpus["scenarios"])}
    schema_version = int(corpus["decision_schema_version"])

    greeting = _decision_for(
        scenarios["manual_social_greeting_developing"],
        corpus_schema_version=schema_version,
    )
    negative_greeting = _decision_for(
        scenarios["social_greeting_negative_affect"],
        corpus_schema_version=schema_version,
    )
    assert replace(negative_greeting, voice=greeting.voice) == greeting

    disclosure = _decision_for(
        scenarios["manual_broad_self_disclosure_developing"],
        corpus_schema_version=schema_version,
    )
    established_disclosure = _decision_for(
        scenarios["broad_self_disclosure_established"],
        corpus_schema_version=schema_version,
    )
    assert (
        replace(
            established_disclosure,
            voice=disclosure.voice,
            continuation=disclosure.continuation,
        )
        == disclosure
    )
    assert (
        established_disclosure.required_disclosure_facets == disclosure.required_disclosure_facets
    )


def test_v24_schema_isolated_from_v25_goals_facets_and_director() -> None:
    corpus = _load_corpus()
    scenarios = {str(item["id"]): item for item in cast(list[dict[str, Any]], corpus["scenarios"])}
    schema_version = int(corpus["decision_schema_version"])

    greeting = _decision_for(
        scenarios["v24_social_greeting_isolation"],
        corpus_schema_version=schema_version,
    )
    reciprocal = _decision_for(
        scenarios["v24_reciprocal_warmth_isolation"],
        corpus_schema_version=schema_version,
    )
    assert greeting.schema_version == CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION
    assert reciprocal.schema_version == CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION
    assert greeting.goal is CharacterDeliveryGoal.ANSWER_PRECISELY
    assert greeting.voice is CharacterDeliveryVoice.THOUGHTFUL_PRECISION
    assert reciprocal.goal is CharacterDeliveryGoal.OWNED_RESPONSE
    assert reciprocal.voice is CharacterDeliveryVoice.WARM_INDEPENDENCE
    assert greeting.required_disclosure_facets == ()
    assert reciprocal.required_disclosure_facets == ()
    assert "Запрошенные self-facets" not in _director(greeting)
    assert "Запрошенные self-facets" not in _director(reciprocal)

    v25_setup = cast(dict[str, Any], scenarios["manual_social_greeting_developing"]["setup"])
    strategy = _strategy(v25_setup)
    with pytest.raises(ValueError, match="v1 cannot contain disclosure facets"):
        decide_character_delivery(
            strategy,
            intent=_intent(strategy),
            affect_profile="calm_even",
            decision_schema_version=CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION,
            disclosure_mode=ConversationalDisclosureMode.SOCIAL,
            required_disclosure_facets=(DisclosureFacet.AFFECT,),
        )

    with pytest.raises(ValueError, match="social connection requires character delivery v2"):
        replace(
            greeting,
            goal=CharacterDeliveryGoal.SOCIAL_CONNECT,
            voice=CharacterDeliveryVoice.LIVELY_DRY_WARMTH,
            grounding=CharacterGroundingMode.REACTION_ONLY,
            continuation=CharacterContinuationMode.COMPLETE,
            pressure=CharacterPressureLevel.NONE,
        )


def test_v25_decision_rejects_goal_facet_mismatches_before_rendering() -> None:
    corpus = _load_corpus()
    scenarios = {str(item["id"]): item for item in cast(list[dict[str, Any]], corpus["scenarios"])}
    schema_version = int(corpus["decision_schema_version"])
    social = _decision_for(
        scenarios["manual_social_greeting_developing"],
        corpus_schema_version=schema_version,
    )
    disclosure = _decision_for(
        scenarios["manual_broad_self_disclosure_developing"],
        corpus_schema_version=schema_version,
    )

    with pytest.raises(ValueError, match="optional affect facet"):
        replace(social, required_disclosure_facets=(DisclosureFacet.INTERESTS,))
    with pytest.raises(ValueError, match="closed personal facet set"):
        replace(disclosure, required_disclosure_facets=(DisclosureFacet.RELATIONSHIP,))
