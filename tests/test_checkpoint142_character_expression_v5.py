"""Versioned deterministic response-act and grounding contract for candidate v22."""

import json
from pathlib import Path
from typing import Any, cast

from satori.application.cognition.contracts import PositionStance
from satori.application.conversation.character_expression import (
    BASELINE_CHARACTER_GUIDANCE_CODES,
    CHARACTER_EXPRESSION_PLAN_V4_SCHEMA_VERSION,
    CHARACTER_RESPONSE_ACT_CONTRACT_SCHEMA_VERSION,
    derive_character_response_act_contract,
    plan_character_expression,
    render_response_act_character_realization,
)
from tests.unit.test_character_expression import _strategy

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_character_expression_v5.json"


def test_v22_response_act_corpus_selects_one_act_and_grounding_scope() -> None:
    corpus: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert corpus["schema_version"] == 5
    assert corpus["corpus_id"] == "satori.checkpoint142.character-expression.ru.v5"
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
        contract = derive_character_response_act_contract(plan)
        assert contract.schema_version == CHARACTER_RESPONSE_ACT_CONTRACT_SCHEMA_VERSION
        assert {
            "response_act": contract.response_act.value,
            "grounding_mode": contract.grounding_mode.value,
            "acknowledgement_mode": contract.acknowledgement_mode.value,
            "continuation_mode": contract.continuation_mode.value,
        } == scenario["expected"], scenario["id"]
        rendered = render_response_act_character_realization(plan)
        assert len(rendered) < 3_500
        assert scenario["id"] not in rendered
        assert "Фактическая граница:" not in rendered
        assert "Смысловой ход:" not in rendered
        assert "готовый ответ" in rendered


def test_v22_target_realizations_do_not_embed_the_failed_semantic_recap() -> None:
    target_setups: tuple[dict[str, str | bool], ...] = (
        {"stance": "answer", "completed_achievement": True},
        {"stance": "listen", "completion_depletion_contrast": True},
    )
    for setup in target_setups:
        completed_achievement = cast(bool, setup.get("completed_achievement", False))
        completion_depletion_contrast = cast(
            bool, setup.get("completion_depletion_contrast", False)
        )
        plan = plan_character_expression(
            _strategy(PositionStance(cast(str, setup["stance"])), humor=0.12),
            affect_profile="soft_negative_non_hostile",
            completed_achievement=completed_achievement,
            completion_depletion_contrast=completion_depletion_contrast,
            explicit_depletion=completion_depletion_contrast,
            plan_schema_version=CHARACTER_EXPRESSION_PLAN_V4_SCHEMA_VERSION,
        )
        rendered = render_response_act_character_realization(plan).casefold()
        for failed_anchor in (
            "сложная часть",
            "завершена",
            "результат",
            "отсутствие радости",
            "выжатость",
            "цена результата",
        ):
            assert failed_anchor not in rendered
