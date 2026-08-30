"""Versioned deterministic corpus for non-echoing and guarded v21 expression."""

import json
from pathlib import Path
from typing import Any

from satori.application.cognition.contracts import PositionStance
from satori.application.conversation.character_expression import (
    BASELINE_CHARACTER_GUIDANCE_CODES,
    CHARACTER_EXPRESSION_PLAN_V4_SCHEMA_VERSION,
    plan_character_expression,
    render_non_echoing_character_realization,
)
from tests.unit.test_character_expression import _strategy

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_character_expression_v4.json"


def test_v21_character_expression_corpus_selects_exact_closed_axes() -> None:
    corpus: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert corpus["schema_version"] == 4
    assert corpus["corpus_id"] == "satori.checkpoint142.character-expression.ru.v4"
    assert len(corpus["scenarios"]) == 8

    for scenario in corpus["scenarios"]:
        setup = scenario["setup"]
        plan = plan_character_expression(
            _strategy(
                PositionStance(setup["stance"]),
                point_codes=tuple(setup.get("point_codes", ("answer_directly",))),
                humor=0.12,
            ),
            affect_profile=setup["affect_profile"],
            personality_codes=BASELINE_CHARACTER_GUIDANCE_CODES,
            completed_achievement=setup.get("completed_achievement", False),
            completion_depletion_contrast=setup.get("completion_depletion_contrast", False),
            explicit_request=setup.get("explicit_request", False),
            explicit_depletion=setup.get("explicit_depletion", False),
            direct_personal_devaluation=setup.get("direct_personal_devaluation", False),
            repeated_critical_pressure=setup.get("repeated_critical_pressure", False),
            repeated_state_interrogation=setup.get("repeated_state_interrogation", False),
            plan_schema_version=CHARACTER_EXPRESSION_PLAN_V4_SCHEMA_VERSION,
        )
        assert plan.contribution_mode is not None
        assert plan.motivational_posture is not None
        assert plan.pressure_level is not None
        assert plan.acknowledgement_mode is not None
        assert plan.continuation_mode is not None
        selected = {
            "contribution_mode": plan.contribution_mode.value,
            "motivational_posture": plan.motivational_posture.value,
            "pressure_level": plan.pressure_level.value,
            "acknowledgement_mode": plan.acknowledgement_mode.value,
            "continuation_mode": plan.continuation_mode.value,
        }
        assert selected == scenario["expected"], scenario["id"]
        rendered = render_non_echoing_character_realization(plan)
        assert len(rendered) < 4_000
        assert scenario["id"] not in rendered
        assert "от тебя в таком состоянии всё равно толку мало" not in rendered
