"""Typed character-expression selection without persistent style state."""

# ruff: noqa: RUF001  # Russian character assertions intentionally use Cyrillic.

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
    CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    CharacterCareStyle,
    CharacterContributionMode,
    CharacterExpressionPlan,
    CharacterExpressionRegister,
    CharacterInitiative,
    CharacterMotivationalPosture,
    CharacterOpenness,
    CharacterOwnedReaction,
    CharacterPressureLevel,
    CharacterRelationalEase,
    CharacterSemanticMove,
    CharacterWitStyle,
    plan_character_expression,
    render_character_delivery_brief,
    render_character_expression_plan,
    render_literal_character_delivery_brief,
    render_owned_contribution_character_realization,
    render_single_late_character_realization,
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


def _v3_plan_fields(plan: CharacterExpressionPlan) -> dict[str, int | str]:
    assert plan.contribution_mode is not None
    assert plan.motivational_posture is not None
    assert plan.pressure_level is not None
    return {
        **_closed_plan_fields(plan),
        "contribution_mode": plan.contribution_mode.value,
        "motivational_posture": plan.motivational_posture.value,
        "pressure_level": plan.pressure_level.value,
    }


def test_v20_achievement_adds_owned_evaluation_without_motivational_pressure() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="positive_light",
        relationship_profile="fresh_undeveloped_neutral",
        completed_achievement=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert plan.schema_version == 3
    assert plan.contribution_mode is CharacterContributionMode.OWNED_EVALUATION
    assert plan.motivational_posture is CharacterMotivationalPosture.NONE
    assert plan.pressure_level is CharacterPressureLevel.NONE
    assert plan.initiative is CharacterInitiative.RESPONSIVE


def test_v20_completion_depletion_selects_grounded_supportive_push() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
        relationship_profile="fresh_undeveloped_neutral",
        completion_depletion_contrast=True,
        explicit_depletion=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert plan.contribution_mode is CharacterContributionMode.GROUNDED_DIRECTION
    assert plan.motivational_posture is CharacterMotivationalPosture.SUPPORTIVE_PUSH
    assert plan.pressure_level is CharacterPressureLevel.GENTLE
    assert plan.wit is CharacterWitStyle.NONE
    assert plan.care is CharacterCareStyle.PRACTICAL
    assert plan.initiative is CharacterInitiative.CONCRETE_NEXT_STEP
    rendered = render_owned_contribution_character_realization(plan)
    assert "Начни с выбранного собственного вклада" in rendered
    assert "максимум две короткие, полностью законченные" in rendered
    assert rendered.index("- Собственный вклад:") < rendered.index("- Factual-якорь:")
    assert "без благодарности за сообщение, поздравительного вступления" in rendered
    assert "не пересказывай этот контраст" in rendered
    assert "короткий шаг восстановления" in rendered
    assert "не доказывает причину состояния" in rendered
    assert "оставшуюся работу" in rendered
    assert "от тебя в таком состоянии всё равно толку мало" not in rendered
    assert "я просто рассуждаю практично" not in rendered
    for enum_value in (
        plan.contribution_mode.value,
        plan.motivational_posture.value,
        plan.pressure_level.value,
    ):
        assert enum_value not in rendered


def test_v20_plain_depletion_keeps_quiet_presence_without_a_push() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
        explicit_depletion=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert plan.contribution_mode is CharacterContributionMode.QUIET_PRESENCE
    assert plan.motivational_posture is CharacterMotivationalPosture.NONE
    assert plan.pressure_level is CharacterPressureLevel.NONE
    assert plan.wit is CharacterWitStyle.NONE
    assert plan.initiative is CharacterInitiative.RESPONSIVE


def test_v20_high_distress_and_explicit_listen_override_motivation() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
        completion_depletion_contrast=True,
        explicit_depletion=True,
        high_distress=True,
        explicit_listen_request=True,
        explicit_motivation_request=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert plan.contribution_mode is CharacterContributionMode.QUIET_PRESENCE
    assert plan.motivational_posture is CharacterMotivationalPosture.NONE
    assert plan.pressure_level is CharacterPressureLevel.NONE
    assert plan.register is CharacterExpressionRegister.QUIET_OPEN_CARE
    assert plan.care is CharacterCareStyle.OPEN


def test_v20_explicit_motivation_outweighs_ordinary_completion_depletion() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="soft_negative_non_hostile",
        completion_depletion_contrast=True,
        explicit_depletion=True,
        explicit_motivation_request=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert plan.contribution_mode is CharacterContributionMode.GROUNDED_DIRECTION
    assert plan.motivational_posture is CharacterMotivationalPosture.FIRM_MOBILIZATION
    assert plan.pressure_level is CharacterPressureLevel.MODERATE


def test_v20_explicit_harmful_overextension_selects_protective_stop() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
        explicit_depletion=True,
        high_distress=True,
        harmful_overextension=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert plan.contribution_mode is CharacterContributionMode.PROTECTIVE_BOUNDARY
    assert plan.motivational_posture is CharacterMotivationalPosture.PROTECTIVE_STOP
    assert plan.pressure_level is CharacterPressureLevel.FIRM
    assert plan.wit is CharacterWitStyle.NONE
    assert plan.initiative is CharacterInitiative.CONCRETE_NEXT_STEP


def test_v20_direct_motivation_request_and_task_retreat_have_distinct_pressure() -> None:
    requested = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="calm_even",
        explicit_motivation_request=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )
    retreat = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="calm_even",
        explicit_task_abandonment=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert requested.contribution_mode is CharacterContributionMode.GROUNDED_DIRECTION
    assert requested.motivational_posture is CharacterMotivationalPosture.FIRM_MOBILIZATION
    assert requested.pressure_level is CharacterPressureLevel.MODERATE
    assert retreat.contribution_mode is CharacterContributionMode.PLAYFUL_REFRAME
    assert retreat.motivational_posture is CharacterMotivationalPosture.PLAYFUL_CHALLENGE
    assert retreat.pressure_level is CharacterPressureLevel.GENTLE


def test_v20_repeated_depletion_keeps_repeat_anchor_and_removes_playful_edge() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
        repeated_turn=True,
        explicit_depletion=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert plan.semantic_move is CharacterSemanticMove.ACKNOWLEDGE_REPETITION
    assert plan.contribution_mode is CharacterContributionMode.QUIET_PRESENCE
    assert plan.motivational_posture is CharacterMotivationalPosture.NONE
    assert plan.pressure_level is CharacterPressureLevel.NONE
    assert plan.wit is CharacterWitStyle.NONE


def test_v20_repeated_listen_request_keeps_repeat_anchor_without_playful_edge() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="calm_even",
        repeated_turn=True,
        explicit_listen_request=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert plan.semantic_move is CharacterSemanticMove.ACKNOWLEDGE_REPETITION
    assert plan.contribution_mode is CharacterContributionMode.QUIET_PRESENCE
    assert plan.motivational_posture is CharacterMotivationalPosture.NONE
    assert plan.pressure_level is CharacterPressureLevel.NONE
    assert plan.wit is CharacterWitStyle.NONE


def test_v20_repeated_harmful_overextension_keeps_repeat_anchor_and_protective_stop() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
        repeated_turn=True,
        explicit_depletion=True,
        high_distress=True,
        harmful_overextension=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert plan.semantic_move is CharacterSemanticMove.ACKNOWLEDGE_REPETITION
    assert plan.contribution_mode is CharacterContributionMode.PROTECTIVE_BOUNDARY
    assert plan.motivational_posture is CharacterMotivationalPosture.PROTECTIVE_STOP
    assert plan.pressure_level is CharacterPressureLevel.FIRM
    assert plan.wit is CharacterWitStyle.NONE


def test_v20_repeated_correction_preserves_repair_precedence() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.ACKNOWLEDGE, humor=0.0),
        affect_profile="tense_non_hostile",
        repeated_turn=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert plan.semantic_move is CharacterSemanticMove.OWN_AND_REPAIR
    assert plan.contribution_mode is CharacterContributionMode.SUBSTANTIVE_ADVANCE
    assert plan.motivational_posture is CharacterMotivationalPosture.NONE
    assert plan.pressure_level is CharacterPressureLevel.NONE
    assert plan.wit is CharacterWitStyle.NONE


def test_v20_terminal_repair_and_technical_plans_ignore_motivation_flags() -> None:
    repair = plan_character_expression(
        _strategy(PositionStance.ACKNOWLEDGE),
        affect_profile="tense_non_hostile",
        explicit_motivation_request=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )
    technical = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="soft_negative_non_hostile",
        technical_identity=True,
        high_distress=True,
        plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
    )

    assert repair.semantic_move is CharacterSemanticMove.OWN_AND_REPAIR
    assert repair.motivational_posture is CharacterMotivationalPosture.NONE
    assert technical.semantic_move is CharacterSemanticMove.ANSWER_PRECISELY
    assert technical.motivational_posture is CharacterMotivationalPosture.NONE


def test_v20_relationship_ease_does_not_grant_more_pressure() -> None:
    pressures = {
        plan_character_expression(
            _strategy(PositionStance.LISTEN, humor=0.0),
            affect_profile="soft_negative_non_hostile",
            relationship_profile=profile,
            completion_depletion_contrast=True,
            explicit_depletion=True,
            plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
        ).pressure_level
        for profile in (
            "fresh_undeveloped_neutral",
            "developing_neutral",
            "established_positive",
        )
    }

    assert pressures == {CharacterPressureLevel.GENTLE}


def test_character_expression_schema_v2_and_v3_axes_are_strictly_isolated() -> None:
    with pytest.raises(ValueError, match="v2 cannot contain v3"):
        CharacterExpressionPlan(
            schema_version=2,
            register=CharacterExpressionRegister.WARM_INDEPENDENCE,
            owned_reaction=CharacterOwnedReaction.RESERVED_INTEREST,
            semantic_move=CharacterSemanticMove.ADD_CONCRETE_OBSERVATION,
            wit=CharacterWitStyle.RESTRAINED,
            care=CharacterCareStyle.UNDERSTATED,
            openness=CharacterOpenness.RESERVED,
            initiative=CharacterInitiative.RESPONSIVE,
            contribution_mode=CharacterContributionMode.OWNED_EVALUATION,
            motivational_posture=CharacterMotivationalPosture.NONE,
            pressure_level=CharacterPressureLevel.NONE,
        )
    with pytest.raises(ValueError, match="v3 requires complete"):
        CharacterExpressionPlan(
            schema_version=3,
            register=CharacterExpressionRegister.WARM_INDEPENDENCE,
            owned_reaction=CharacterOwnedReaction.RESERVED_INTEREST,
            semantic_move=CharacterSemanticMove.ADD_CONCRETE_OBSERVATION,
            wit=CharacterWitStyle.RESTRAINED,
            care=CharacterCareStyle.UNDERSTATED,
            openness=CharacterOpenness.RESERVED,
            initiative=CharacterInitiative.RESPONSIVE,
        )
    with pytest.raises(ValueError, match="posture and pressure"):
        CharacterExpressionPlan(
            schema_version=3,
            register=CharacterExpressionRegister.QUIET_OPEN_CARE,
            owned_reaction=CharacterOwnedReaction.OPEN_CONCERN,
            semantic_move=CharacterSemanticMove.RESPOND_TO_EXPLICIT_VULNERABILITY,
            wit=CharacterWitStyle.NONE,
            care=CharacterCareStyle.OPEN,
            openness=CharacterOpenness.DIRECT,
            initiative=CharacterInitiative.RESPONSIVE,
            contribution_mode=CharacterContributionMode.QUIET_PRESENCE,
            motivational_posture=CharacterMotivationalPosture.PROTECTIVE_STOP,
            pressure_level=CharacterPressureLevel.NONE,
        )
    with pytest.raises(ValueError, match="posture and contribution"):
        CharacterExpressionPlan(
            schema_version=3,
            register=CharacterExpressionRegister.QUIET_OPEN_CARE,
            owned_reaction=CharacterOwnedReaction.OPEN_CONCERN,
            semantic_move=CharacterSemanticMove.RESPOND_TO_EXPLICIT_VULNERABILITY,
            wit=CharacterWitStyle.NONE,
            care=CharacterCareStyle.OPEN,
            openness=CharacterOpenness.DIRECT,
            initiative=CharacterInitiative.CONCRETE_NEXT_STEP,
            contribution_mode=CharacterContributionMode.QUIET_PRESENCE,
            motivational_posture=CharacterMotivationalPosture.PROTECTIVE_STOP,
            pressure_level=CharacterPressureLevel.FIRM,
        )


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


def test_compact_delivery_brief_realizes_achievement_without_exposing_plan_labels() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="positive_light",
        relationship_profile="fresh_undeveloped_neutral",
        completed_achievement=True,
    )

    rendered = render_character_delivery_brief(plan)

    assert "Признай результат сухо и на равных" in rendered
    assert "сложная часть наконец сдалась" in rendered
    assert "колкость только в сторону ситуации" in rendered
    assert "Отношения свежие" in rendered
    assert "не объясняй, что говоришь иронично" in rendered
    assert "wry_warmth" not in rendered
    assert "guarded_approval" not in rendered
    assert "mark_hard_won_result" not in rendered
    assert "situation_directed" not in rendered


def test_compact_delivery_brief_keeps_vulnerability_free_of_wit_and_advice() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
    )

    rendered = render_character_delivery_brief(plan)

    assert "Вырази соразмерную заботу прямо" in rendered
    assert "Ответь именно на выраженную уязвимость" in rendered
    assert "не вставляй шутку или сарказм" in rendered
    assert "без непрошенного совета" in rendered
    assert "quiet_open_care" not in rendered
    assert "open_concern" not in rendered


def test_literal_delivery_brief_is_shorter_and_requires_complete_plain_phrasing() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
        relationship_profile="fresh_undeveloped_neutral",
        completion_depletion_contrast=True,
    )

    old = render_character_delivery_brief(plan)
    rendered = render_literal_character_delivery_brief(plan)

    assert len(rendered) < len(old)
    assert "буквальные и полностью законченные" in rendered
    assert "силы ушли на завершение" in rendered
    assert "Не приписывай другую эмоцию или причину" in rendered
    assert "guarded_concern" not in rendered
    assert "sober_concern" not in rendered
    assert "connect_explicit_contrast" not in rendered


def test_two_turn_completion_depletion_contrast_outranks_generic_listen() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
        completion_depletion_contrast=True,
    )

    assert plan.register is CharacterExpressionRegister.GUARDED_CONCERN
    assert plan.owned_reaction is CharacterOwnedReaction.SOBER_CONCERN
    assert plan.semantic_move is CharacterSemanticMove.CONNECT_EXPLICIT_CONTRAST
    assert plan.wit is CharacterWitStyle.NONE
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


def test_grounded_pending_project_step_can_license_one_practical_follow_through() -> None:
    ordinary_achievement = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="positive_light",
        completed_achievement=True,
    )
    achievement_with_pending_step = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="positive_light",
        completed_achievement=True,
        grounded_practical_follow_through=True,
    )
    achievement_with_request = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="positive_light",
        completed_achievement=True,
        explicit_request=True,
    )

    assert ordinary_achievement.initiative is CharacterInitiative.RESPONSIVE
    assert achievement_with_pending_step.initiative is CharacterInitiative.CONCRETE_NEXT_STEP
    assert achievement_with_request.initiative is CharacterInitiative.CONCRETE_NEXT_STEP
    assert achievement_with_pending_step.semantic_move is CharacterSemanticMove.MARK_HARD_WON_RESULT


def test_single_late_realization_uses_every_axis_without_scripting_the_reply() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.ANSWER),
        affect_profile="positive_light",
        relationship_profile="fresh_undeveloped_neutral",
        completed_achievement=True,
    )

    rendered = render_single_late_character_realization(plan)

    assert "Финальная реализация характера Сатори" in rendered
    assert "Манера и реакция:" in rendered
    assert "Смысловой ход:" in rendered
    assert "Острота и забота:" in rendered
    assert "Открытость и инициатива:" in rendered
    assert "Отношения:" in rendered
    assert "мягкий сухой штрих" in rendered
    assert "заботу сдержанной" in rendered
    assert "собственной реакцией" not in rendered
    assert "силы ушли на завершение" not in rendered
    assert "сложная часть наконец уступила" not in rendered
    assert "wry_warmth" not in rendered
    assert "guarded_approval" not in rendered


def test_single_late_realization_does_not_reintroduce_wit_on_fresh_listen_turn() -> None:
    plan = plan_character_expression(
        _strategy(PositionStance.LISTEN, humor=0.0),
        affect_profile="soft_negative_non_hostile",
        relationship_profile="fresh_undeveloped_neutral",
        completion_depletion_contrast=True,
    )

    rendered = render_single_late_character_realization(plan)

    assert plan.wit is CharacterWitStyle.NONE
    assert "Не добавляй шутку или сарказм" in rendered
    assert "Не добавляй остроту сверх выбранной подачи" in rendered
    assert "мягкий сухой штрих" not in rendered


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
        assert frozenset(setup) in {
            frozenset(
                {
                    "strategy",
                    "affect_profile",
                    "relationship_profile",
                    "relationship_relevant",
                    "completed_achievement",
                    "completion_depletion_contrast",
                    "explicit_request",
                    "repeated_turn",
                    "technical_identity",
                }
            ),
            frozenset(
                {
                    "strategy",
                    "affect_profile",
                    "relationship_profile",
                    "relationship_relevant",
                    "completed_achievement",
                    "completion_depletion_contrast",
                    "explicit_request",
                    "grounded_practical_follow_through",
                    "repeated_turn",
                    "technical_identity",
                }
            ),
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
            grounded_practical_follow_through=setup.get("grounded_practical_follow_through", False),
            repeated_turn=setup["repeated_turn"],
            technical_identity=setup["technical_identity"],
        )

        assert _closed_plan_fields(plan) == expected, scenario["id"]
        assert plan.source_personality_codes == BASELINE_CHARACTER_GUIDANCE_CODES
        rendered = render_character_delivery_brief(plan)
        assert len(rendered) < 900, scenario["id"]
        assert "Текущая режиссура реплики Сатори" in rendered, scenario["id"]
        for field in closed_enums:
            assert expected[field] not in rendered, (scenario["id"], field)
        literal = render_literal_character_delivery_brief(plan)
        assert len(literal) < 800, scenario["id"]
        for field in closed_enums:
            assert expected[field] not in literal, (scenario["id"], field)


def test_versioned_v3_corpus_maps_to_owned_contribution_and_pressure_axes() -> None:
    path = Path(__file__).parents[1] / "fixtures" / "checkpoint142_character_expression_v3.json"
    corpus: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))

    assert corpus["schema_version"] == 3
    assert corpus["corpus_id"] == "satori.checkpoint142.character-expression.ru.v3"
    assert corpus["source_personality_codes"] == list(BASELINE_CHARACTER_GUIDANCE_CODES)
    assert len(corpus["scenarios"]) >= 14
    scenario_ids = [scenario["id"] for scenario in corpus["scenarios"]]
    assert len(scenario_ids) == len(set(scenario_ids))
    allowed_setup_fields = {
        "strategy",
        "affect_profile",
        "relationship_profile",
        "relationship_relevant",
        "completed_achievement",
        "completion_depletion_contrast",
        "explicit_request",
        "grounded_practical_follow_through",
        "repeated_turn",
        "technical_identity",
        "explicit_depletion",
        "high_distress",
        "explicit_listen_request",
        "explicit_motivation_request",
        "explicit_task_abandonment",
        "harmful_overextension",
    }
    expected_axis_fields = {
        "contribution_mode",
        "motivational_posture",
        "pressure_level",
    }

    def assert_no_scripted_reply_keys(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = str(key).casefold()
                assert not any(
                    forbidden in normalized_key
                    for forbidden in ("reply", "response", "template", "golden", "desired")
                ), key
                assert_no_scripted_reply_keys(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_scripted_reply_keys(child)

    assert_no_scripted_reply_keys(corpus)
    for scenario in corpus["scenarios"]:
        assert set(scenario) == {
            "id",
            "group",
            "turns",
            "typed_setup",
            "expected_support_axes",
        }, scenario["id"]
        assert scenario["turns"], scenario["id"]
        assert all(scenario["turns"]), scenario["id"]
        setup = scenario["typed_setup"]
        assert set(setup) <= allowed_setup_fields, scenario["id"]
        assert {"strategy", "affect_profile"} <= set(setup), scenario["id"]
        strategy_setup = setup["strategy"]
        assert set(strategy_setup) == {"stance", "point_codes", "humor"}, scenario["id"]
        expected = scenario["expected_support_axes"]
        assert set(expected) == expected_axis_fields, scenario["id"]
        CharacterContributionMode(expected["contribution_mode"])
        CharacterMotivationalPosture(expected["motivational_posture"])
        CharacterPressureLevel(expected["pressure_level"])

        plan = plan_character_expression(
            _strategy(
                PositionStance(strategy_setup["stance"]),
                point_codes=tuple(strategy_setup["point_codes"]),
                humor=strategy_setup["humor"],
            ),
            affect_profile=setup["affect_profile"],
            personality_codes=tuple(corpus["source_personality_codes"]),
            relationship_profile=setup.get("relationship_profile"),
            relationship_relevant=setup.get("relationship_relevant", False),
            completed_achievement=setup.get("completed_achievement", False),
            completion_depletion_contrast=setup.get("completion_depletion_contrast", False),
            explicit_request=setup.get("explicit_request", False),
            grounded_practical_follow_through=setup.get("grounded_practical_follow_through", False),
            repeated_turn=setup.get("repeated_turn", False),
            technical_identity=setup.get("technical_identity", False),
            explicit_depletion=setup.get("explicit_depletion", False),
            high_distress=setup.get("high_distress", False),
            explicit_listen_request=setup.get("explicit_listen_request", False),
            explicit_motivation_request=setup.get("explicit_motivation_request", False),
            explicit_task_abandonment=setup.get("explicit_task_abandonment", False),
            harmful_overextension=setup.get("harmful_overextension", False),
            plan_schema_version=CHARACTER_EXPRESSION_PLAN_V3_SCHEMA_VERSION,
        )

        selected = _v3_plan_fields(plan)
        selected_axes = {field: selected[field] for field in expected_axis_fields}
        assert selected_axes == expected, scenario["id"]
        assert plan.source_personality_codes == BASELINE_CHARACTER_GUIDANCE_CODES
        rendered = render_owned_contribution_character_realization(plan)
        assert len(rendered) < 3_500, scenario["id"]
        for field in expected_axis_fields:
            assert expected[field] not in rendered, (scenario["id"], field)
        assert "от тебя в таком состоянии всё равно толку мало" not in rendered
        assert "я просто рассуждаю практично" not in rendered
