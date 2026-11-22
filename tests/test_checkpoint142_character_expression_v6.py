"""Versioned deterministic action/evidence/voice/stop contract for candidate v23."""

import json
from pathlib import Path
from typing import Any, cast

from satori.application.cognition.contracts import PositionStance
from satori.application.conversation.character_expression import (
    BASELINE_CHARACTER_GUIDANCE_CODES,
    CHARACTER_EXPRESSION_PLAN_V5_SCHEMA_VERSION,
    CHARACTER_RESPONSE_ACT_CONTRACT_SCHEMA_VERSION,
    derive_character_response_act_contract,
    plan_character_expression,
    render_compact_response_act_character_realization,
)
from tests.unit.test_character_expression import _strategy

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_character_expression_v6.json"


def test_v23_character_corpus_selects_one_act_and_bounded_evidence_scope() -> None:
    corpus: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert corpus["schema_version"] == 6
    assert corpus["corpus_id"] == "satori.checkpoint142.character-expression.ru.v6"
    assert len(corpus["scenarios"]) == 9

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
            explicit_depletion=setup.get("explicit_depletion", False),
            high_distress=setup.get("high_distress", False),
            explicit_listen_request=setup.get("explicit_listen_request", False),
            repeated_critical_pressure=setup.get("repeated_critical_pressure", False),
            technical_identity=setup.get("technical_identity", False),
            plan_schema_version=CHARACTER_EXPRESSION_PLAN_V5_SCHEMA_VERSION,
        )
        contract = derive_character_response_act_contract(plan)
        assert plan.schema_version == CHARACTER_EXPRESSION_PLAN_V5_SCHEMA_VERSION
        assert contract.schema_version == CHARACTER_RESPONSE_ACT_CONTRACT_SCHEMA_VERSION
        assert {
            "response_act": contract.response_act.value,
            "grounding_mode": contract.grounding_mode.value,
            "acknowledgement_mode": contract.acknowledgement_mode.value,
            "continuation_mode": contract.continuation_mode.value,
        } == scenario["expected"], scenario["id"]
        rendered = render_compact_response_act_character_realization(plan)
        assert rendered.count("\n-") == 4
        assert len(rendered) < 2_500
        assert scenario["id"] not in rendered
        assert "Фактическая граница:" not in rendered
        assert "Смысловой ход:" not in rendered
        assert "готовый ответ" in rendered


def test_v23_target_projection_contains_no_substantive_input_recap() -> None:
    for setup in (
        {"stance": PositionStance.ANSWER, "completed_achievement": True},
        {
            "stance": PositionStance.LISTEN,
            "completion_depletion_contrast": True,
            "explicit_depletion": True,
        },
    ):
        stance = cast(PositionStance, setup["stance"])
        completed_achievement = cast(bool, setup.get("completed_achievement", False))
        completion_depletion_contrast = cast(
            bool, setup.get("completion_depletion_contrast", False)
        )
        explicit_depletion = cast(bool, setup.get("explicit_depletion", False))
        plan = plan_character_expression(
            _strategy(stance, humor=0.0),
            affect_profile="soft_negative_non_hostile",
            completed_achievement=completed_achievement,
            completion_depletion_contrast=completion_depletion_contrast,
            explicit_depletion=explicit_depletion,
            plan_schema_version=CHARACTER_EXPRESSION_PLAN_V5_SCHEMA_VERSION,
        )
        rendered = render_compact_response_act_character_realization(plan).casefold()
        for failed_anchor in (
            "сложная часть",
            "завершена",
            "результат",
            "отсутствие радости",
            "выжатость",
            "цена результата",
        ):
            assert failed_anchor not in rendered
