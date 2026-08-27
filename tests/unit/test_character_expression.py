"""Typed character-expression selection without persistent style state."""

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest

from satori.application.cognition.contracts import (
    CognitionArtifactStatus,
    PositionStance,
    ResponseStrategy,
    ResponseTone,
    ResponseVerbosity,
)
from satori.application.conversation.character_expression import (
    BASELINE_CHARACTER_GUIDANCE_CODES,
    CharacterCareStyle,
    CharacterExpressionPlan,
    CharacterExpressionRegister,
    CharacterInitiative,
    CharacterOpenness,
    CharacterOwnedReaction,
    CharacterRelationalEase,
    CharacterSemanticMove,
    CharacterWitStyle,
    plan_character_expression,
    render_character_expression_plan,
)


def _strategy(
    stance: PositionStance,
    *,
    point_codes: tuple[str, ...] = ("answer_directly",),
    humor: float = 0.12,
) -> ResponseStrategy:
    return ResponseStrategy(
        schema_version=1,
        status=CognitionArtifactStatus.APPLIED,
        position_stance=stance,
        preserve_uncertainty=False,
        tone=ResponseTone.WARM_GENTLE,
        verbosity=ResponseVerbosity.BRIEF,
        humor=humor,
        softness=0.7,
        point_codes=point_codes,
        must_not_claim=("unsupported_memory",),
        source_refs=("current-user-message",),
    )


def _closed_plan_fields(plan: CharacterExpressionPlan) -> dict[str, int | str]:
    return {
        "schema_version": plan.schema_version,
        "register": plan.register.value,
        "owned_reaction": plan.owned_reaction.value,
        "semantic_move": plan.semantic_move.value,
        "wit": plan.wit.value,
        "care": plan.care.value,
        "openness": plan.openness.value,
        "initiative": plan.initiative.value,
        "relational_ease": plan.relational_ease.value,
    }


def test_generic_vulnerability_uses_open_care_without_wit_or_initiative() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
    )

    assert plan.register is CharacterExpressionRegister.QUIET_OPEN_CARE
    assert plan.owned_reaction is CharacterOwnedReaction.OPEN_CONCERN
    assert plan.semantic_move is CharacterSemanticMove.RESPOND_TO_EXPLICIT_VULNERABILITY
    assert plan.wit is CharacterWitStyle.NONE
    assert plan.care is CharacterCareStyle.OPEN
    assert plan.openness is CharacterOpenness.DIRECT
    assert plan.initiative is CharacterInitiative.RESPONSIVE


def test_achievement_marks_the_result_with_guarded_approval() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="positive_light",
        completed_achievement=True,
    )

    assert plan.register is CharacterExpressionRegister.WRY_WARMTH
    assert plan.owned_reaction is CharacterOwnedReaction.GUARDED_APPROVAL
    assert plan.semantic_move is CharacterSemanticMove.MARK_HARD_WON_RESULT
    assert plan.wit is CharacterWitStyle.SITUATION_DIRECTED
    assert plan.initiative is CharacterInitiative.RESPONSIVE
    rendered = render_character_expression_plan(plan)
    assert "упрямой задачи или ситуации" in rendered
    assert "не направляй её на уязвимость" in rendered
    assert "не копируй существующую вымышленную героиню" in rendered


def test_two_turn_completion_depletion_contrast_outranks_generic_listen() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
        completion_depletion_contrast=True,
    )

    assert plan.register is CharacterExpressionRegister.GUARDED_CONCERN
    assert plan.owned_reaction is CharacterOwnedReaction.SOBER_CONCERN
    assert plan.semantic_move is CharacterSemanticMove.CONNECT_EXPLICIT_CONTRAST
    assert plan.wit is CharacterWitStyle.SITUATION_DIRECTED
    assert plan.care is CharacterCareStyle.UNDERSTATED
    assert plan.openness is CharacterOpenness.BALANCED
    assert plan.initiative is CharacterInitiative.RESPONSIVE


def test_repetition_acknowledgement_outranks_answering_the_content_again() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="calm_even",
        repeated_turn=True,
    )

    assert plan.register is CharacterExpressionRegister.PLAYFUL_EDGE
    assert plan.owned_reaction is CharacterOwnedReaction.ENGAGED_SKEPTICISM
    assert plan.semantic_move is CharacterSemanticMove.ACKNOWLEDGE_REPETITION
    assert plan.initiative is CharacterInitiative.RESPONSIVE


def test_request_flag_changes_initiative_without_changing_default_character() -> None:
    compliment = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="calm_even",
        explicit_request=False,
    )
    requested_help = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="calm_even",
        explicit_request=True,
    )

    assert compliment.register is CharacterExpressionRegister.WARM_INDEPENDENCE
    assert compliment.owned_reaction is CharacterOwnedReaction.RESERVED_INTEREST
    assert compliment.initiative is CharacterInitiative.RESPONSIVE
    assert requested_help.register is compliment.register
    assert requested_help.owned_reaction is compliment.owned_reaction
    assert requested_help.semantic_move is compliment.semantic_move
    assert requested_help.initiative is CharacterInitiative.CONCRETE_NEXT_STEP


def test_challenge_and_creative_collaboration_select_distinct_reactions() -> None:
    challenge = plan_character_expression(
        _strategy(PositionStance.CHALLENGE),
        affect_profile="calm_even",
    )
    creative = plan_character_expression(
        _strategy(
            PositionStance.COLLABORATE,
            point_codes=("support_decision", "collaborate_creatively"),
        ),
        affect_profile="positive_light",
    )

    assert challenge.register is CharacterExpressionRegister.PLAYFUL_EDGE
    assert challenge.owned_reaction is CharacterOwnedReaction.ENGAGED_SKEPTICISM
    assert challenge.semantic_move is CharacterSemanticMove.TEST_CURRENT_CLAIM
    assert creative.register is CharacterExpressionRegister.LIVELY_COLLABORATION
    assert creative.owned_reaction is CharacterOwnedReaction.ENERGIZED_INTEREST
    assert creative.semantic_move is CharacterSemanticMove.ADVANCE_SHARED_IDEA
    assert creative.initiative is CharacterInitiative.ACTIVE_COLLABORATION


def test_repair_and_technical_identity_take_priority_over_general_expression() -> None:
    repair = plan_character_expression(
        _strategy(PositionStance.ACKNOWLEDGE),
        affect_profile="tense_non_hostile",
        explicit_request=True,
    )
    technical = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="positive_light",
        completed_achievement=True,
        explicit_request=True,
        technical_identity=True,
    )

    assert repair.register is CharacterExpressionRegister.DIRECT_REPAIR
    assert repair.owned_reaction is CharacterOwnedReaction.ACCOUNTABLE_REGRET
    assert repair.semantic_move is CharacterSemanticMove.OWN_AND_REPAIR
    assert repair.initiative is CharacterInitiative.CONCRETE_NEXT_STEP
    assert technical.register is CharacterExpressionRegister.THOUGHTFUL_PRECISION
    assert technical.owned_reaction is CharacterOwnedReaction.FOCUSED_CONFIDENCE
    assert technical.semantic_move is CharacterSemanticMove.ANSWER_PRECISELY
    assert technical.initiative is CharacterInitiative.RESPONSIVE


def test_negative_affect_is_reflective_not_globally_hostile() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="tense_non_hostile",
    )

    assert plan.register is CharacterExpressionRegister.REFLECTIVE_CANDOR
    assert plan.owned_reaction is CharacterOwnedReaction.REFLECTIVE_CONCERN
    assert plan.semantic_move is CharacterSemanticMove.ADD_CONCRETE_OBSERVATION
    assert plan.wit is CharacterWitStyle.RESTRAINED
    assert plan.initiative is CharacterInitiative.RESPONSIVE


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("fresh_undeveloped_neutral", CharacterRelationalEase.FRESH),
        ("developing_neutral", CharacterRelationalEase.DEVELOPING),
        ("established_positive", CharacterRelationalEase.ESTABLISHED),
    ],
)
def test_ordinary_turns_preserve_distinct_positive_relationship_ease(
    profile: str,
    expected: CharacterRelationalEase,
) -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="calm_even",
        relationship_profile=profile,
        relationship_relevant=False,
    )

    assert plan.relational_ease is expected


def test_damaged_relationship_is_guarded_only_when_relationally_relevant() -> None:
    irrelevant = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="calm_even",
        relationship_profile="guarded_only_when_relationally_relevant",
        relationship_relevant=False,
    )
    relevant = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="calm_even",
        relationship_profile="guarded_only_when_relationally_relevant",
        relationship_relevant=True,
    )

    assert irrelevant.relational_ease is CharacterRelationalEase.BASELINE
    assert relevant.relational_ease is CharacterRelationalEase.GUARDED


def test_expression_schema_and_personality_source_are_closed() -> None:
    with pytest.raises(ValueError, match="unsupported character expression plan"):
        CharacterExpressionPlan(
            schema_version=1,
            register=CharacterExpressionRegister.WARM_INDEPENDENCE,
            owned_reaction=CharacterOwnedReaction.RESERVED_INTEREST,
            semantic_move=CharacterSemanticMove.ADD_CONCRETE_OBSERVATION,
            wit=CharacterWitStyle.RESTRAINED,
            care=CharacterCareStyle.UNDERSTATED,
            openness=CharacterOpenness.RESERVED,
            initiative=CharacterInitiative.RESPONSIVE,
        )

    with pytest.raises(ValueError, match="canonical personality guidance"):
        plan_character_expression(
            _strategy(PositionStance.ANSWER),
            affect_profile="calm_even",
            personality_codes=("second-personality-source",),
        )


def test_versioned_v2_corpus_maps_to_every_closed_plan_field() -> None:
    path = Path(__file__).parents[1] / "fixtures" / "checkpoint142_character_expression_v2.json"
    corpus: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))

    assert corpus["schema_version"] == 2
    assert corpus["corpus_id"] == "satori.checkpoint142.character-expression.ru.v2"
    assert corpus["source_personality_codes"] == list(BASELINE_CHARACTER_GUIDANCE_CODES)
    expected_fields = {
        "schema_version",
        "register",
        "owned_reaction",
        "semantic_move",
        "wit",
        "care",
        "openness",
        "initiative",
        "relational_ease",
    }
    closed_enums: dict[str, type[StrEnum]] = {
        "register": CharacterExpressionRegister,
        "owned_reaction": CharacterOwnedReaction,
        "semantic_move": CharacterSemanticMove,
        "wit": CharacterWitStyle,
        "care": CharacterCareStyle,
        "openness": CharacterOpenness,
        "initiative": CharacterInitiative,
        "relational_ease": CharacterRelationalEase,
    }

    for scenario in corpus["scenarios"]:
        assert set(scenario) == {
            "id",
            "group",
            "turns",
            "typed_setup",
            "expected_plan",
            "review_dimensions",
            "undesirable_patterns",
        }, scenario["id"]
        setup = scenario["typed_setup"]
        assert set(setup) == {
            "strategy",
            "affect_profile",
            "relationship_profile",
            "relationship_relevant",
            "completed_achievement",
            "completion_depletion_contrast",
            "explicit_request",
            "repeated_turn",
            "technical_identity",
        }, scenario["id"]
        strategy_setup = setup["strategy"]
        assert set(strategy_setup) == {"stance", "point_codes", "humor"}, scenario["id"]
        expected = scenario["expected_plan"]
        assert set(expected) == expected_fields, scenario["id"]
        assert expected["schema_version"] == 2, scenario["id"]
        for field, enum_type in closed_enums.items():
            enum_type(expected[field])

        plan = plan_character_expression(
            _strategy(
                PositionStance(strategy_setup["stance"]),
                point_codes=tuple(strategy_setup["point_codes"]),
                humor=strategy_setup["humor"],
            ),
            affect_profile=setup["affect_profile"],
            personality_codes=tuple(corpus["source_personality_codes"]),
            relationship_profile=setup["relationship_profile"],
            relationship_relevant=setup["relationship_relevant"],
            completed_achievement=setup["completed_achievement"],
            completion_depletion_contrast=setup["completion_depletion_contrast"],
            explicit_request=setup["explicit_request"],
            repeated_turn=setup["repeated_turn"],
            technical_identity=setup["technical_identity"],
        )

        assert _closed_plan_fields(plan) == expected, scenario["id"]
        assert plan.source_personality_codes == BASELINE_CHARACTER_GUIDANCE_CODES
