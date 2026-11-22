"""Cognition-owned repair compatibility preserves existing position routes and summaries."""

# ruff: noqa: RUF001  # Exact Russian counterexamples intentionally exercise lexical intake.

from dataclasses import asdict, replace

import pytest

from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V1,
    INTENT_REGISTRY_VERSION_V2,
    CognitionArtifactStatus,
    CognitionDialogueSignals,
    NeedDimension,
    NeedWeight,
    PerceptionSignal,
    PositionStance,
    PreparedCognitionIntake,
)
from satori.application.cognition.position_selection import (
    repair_offer_intake_is_compatible,
    select_intake_position,
)
from satori.application.cognition.use_cases import DeterministicCognitionPlanner


def intake(
    *,
    signals: tuple[PerceptionSignal, ...] = (),
    weights: tuple[NeedWeight, ...] = (),
    uncertainty: float = 0.18,
    status: CognitionArtifactStatus = CognitionArtifactStatus.APPLIED,
) -> PreparedCognitionIntake:
    prepared = DeterministicCognitionPlanner().prepare_intake(
        user_text="An ordinary observation.",
        user_message_id="current-user",
        interaction_id="current-interaction",
        dialogue=CognitionDialogueSignals(),
    )
    return replace(
        prepared,
        perception=replace(prepared.perception, status=status, signals=signals),
        need_mix=replace(
            prepared.need_mix,
            status=status,
            uncertainty=uncertainty,
            needs=(NeedWeight(NeedDimension.INFORMATION, 0.45), *weights),
        ),
        retrieval_plan=replace(prepared.retrieval_plan, status=status),
        fallback_reasons=("fixture_fallback",)
        if status is CognitionArtifactStatus.FALLBACK
        else (),
    )


@pytest.mark.parametrize(
    ("supplied", "version", "stance", "summary"),
    [
        (
            intake(),
            1,
            PositionStance.ANSWER,
            "Address the current request directly and preserve evidence limits.",
        ),
        (
            intake(),
            2,
            PositionStance.ANSWER,
            "Address the current request directly and preserve evidence limits.",
        ),
        (
            intake(signals=(PerceptionSignal.HIGH_DISTRESS,), uncertainty=0.8),
            2,
            PositionStance.LISTEN,
            "Prioritize explicitly requested or safety-relevant presence.",
        ),
        (
            intake(signals=(PerceptionSignal.HIGH_DISTRESS,), uncertainty=0.8),
            1,
            PositionStance.UNCERTAIN,
            "Respond conservatively and make material uncertainty explicit.",
        ),
        (
            intake(
                signals=(PerceptionSignal.HIGH_DISTRESS, PerceptionSignal.SELF_DISCLOSURE_REQUEST),
                uncertainty=0.8,
            ),
            2,
            PositionStance.LISTEN,
            "Prioritize explicitly requested or safety-relevant presence.",
        ),
        (
            intake(signals=(PerceptionSignal.SELF_DISCLOSURE_REQUEST,), uncertainty=0.8),
            2,
            PositionStance.ANSWER,
            "Answer the explicit request about Satori from trusted self state.",
        ),
        (
            intake(signals=(PerceptionSignal.SELF_DISCLOSURE_REQUEST,), uncertainty=0.8),
            1,
            PositionStance.UNCERTAIN,
            "Respond conservatively and make material uncertainty explicit.",
        ),
        (
            intake(uncertainty=0.55, signals=(PerceptionSignal.CORRECTION,)),
            2,
            PositionStance.UNCERTAIN,
            "Respond conservatively and make material uncertainty explicit.",
        ),
        (
            intake(
                signals=(PerceptionSignal.CORRECTION, PerceptionSignal.EXPLICIT_MOTIVATION_REQUEST)
            ),
            2,
            PositionStance.ACKNOWLEDGE,
            "Acknowledge the correction before addressing the current request.",
        ),
        (
            intake(signals=(PerceptionSignal.EXPLICIT_TASK_ABANDONMENT,)),
            2,
            PositionStance.CHALLENGE,
            "Challenge the stated abandonment without attacking the person.",
        ),
        (
            intake(signals=(PerceptionSignal.EXPLICIT_TASK_ABANDONMENT,)),
            1,
            PositionStance.ANSWER,
            "Address the current request directly and preserve evidence limits.",
        ),
        (
            intake(
                signals=(PerceptionSignal.EXPLICIT_MOTIVATION_REQUEST,),
                weights=(NeedWeight(NeedDimension.EMOTIONAL_PRESENCE, 0.86),),
            ),
            2,
            PositionStance.COLLABORATE,
            "Provide the explicitly requested bounded motivational push.",
        ),
        (
            intake(
                signals=(PerceptionSignal.EXPLICIT_MOTIVATION_REQUEST,),
                weights=(NeedWeight(NeedDimension.EMOTIONAL_PRESENCE, 0.86),),
            ),
            1,
            PositionStance.LISTEN,
            "Prioritize attentive presence before analysis or advice.",
        ),
        (
            intake(
                weights=(
                    NeedWeight(NeedDimension.EMOTIONAL_PRESENCE, 0.7),
                    NeedWeight(NeedDimension.CHALLENGE, 0.84),
                )
            ),
            2,
            PositionStance.LISTEN,
            "Prioritize attentive presence before analysis or advice.",
        ),
        (
            intake(
                weights=(
                    NeedWeight(NeedDimension.CHALLENGE, 0.7),
                    NeedWeight(NeedDimension.DECISION_SUPPORT, 0.82),
                )
            ),
            2,
            PositionStance.CHALLENGE,
            "Offer an honest bounded challenge instead of automatic agreement.",
        ),
        (
            intake(weights=(NeedWeight(NeedDimension.DECISION_SUPPORT, 0.7),)),
            2,
            PositionStance.COLLABORATE,
            "Work through the request collaboratively while preserving evidence limits.",
        ),
        (
            intake(weights=(NeedWeight(NeedDimension.CREATIVE_COLLABORATION, 0.7),)),
            2,
            PositionStance.COLLABORATE,
            "Work through the request collaboratively while preserving evidence limits.",
        ),
        (
            intake(status=CognitionArtifactStatus.FALLBACK),
            2,
            PositionStance.UNCERTAIN,
            "Respond conservatively and make material uncertainty explicit.",
        ),
    ],
)
def test_shared_owner_selection_retains_exact_historical_precedence_and_summary(
    supplied: PreparedCognitionIntake, version: int, stance: PositionStance, summary: str
) -> None:
    before = asdict(supplied)
    selected = select_intake_position(
        supplied,
        status=supplied.perception.status,
        intent_registry_version=version,
    )
    completed = DeterministicCognitionPlanner(intent_registry_version=version).complete(
        supplied,
        interaction_id="current-interaction",
        available_evidence_ids=(),
        prepared_affect=None,
    )
    assert selected == (stance, summary)
    assert completed.internal_position.stance is stance
    assert completed.internal_position.summary.encode("utf-8") == summary.encode("utf-8")
    assert asdict(supplied) == before


@pytest.mark.parametrize(
    ("current", "expected_stance", "compatible"),
    [
        ("An ordinary observation.", PositionStance.ANSWER, True),
        (
            "Я зря приписал тебе безразличие. Беру эти слова назад.",
            PositionStance.ANSWER,
            True,
        ),
        (
            "Мне кажется, я был слишком резок с тобой. Прости.",
            PositionStance.UNCERTAIN,
            False,
        ),
        (
            "Я зря думал, что у тебя нет чувств. Беру свои слова назад.",
            PositionStance.LISTEN,
            False,
        ),
        (
            "Я зря отверг то, что у тебя появилась идея. Возвращаюсь к сказанному: "
            "это было грубо с моей стороны.",
            PositionStance.COLLABORATE,
            False,
        ),
        ("Надо выбрать подходящий вариант.", PositionStance.COLLABORATE, False),
    ],
)
def test_actual_nonanswer_routes_are_incompatible_without_reclassifying_input(
    current: str, expected_stance: PositionStance, compatible: bool
) -> None:
    planner = DeterministicCognitionPlanner(intent_registry_version=INTENT_REGISTRY_VERSION_V2)
    supplied = planner.prepare_intake(
        user_text=current,
        user_message_id="current-user",
        interaction_id="current-interaction",
        dialogue=CognitionDialogueSignals(),
    )
    before = asdict(supplied)
    selected_stance, _summary = select_intake_position(
        supplied,
        status=CognitionArtifactStatus.APPLIED,
        intent_registry_version=INTENT_REGISTRY_VERSION_V2,
    )
    assert selected_stance is expected_stance
    assert repair_offer_intake_is_compatible(supplied) is compatible
    assert asdict(supplied) == before


@pytest.mark.parametrize("uncertainty", [0.0, 0.4999, 0.5, 0.5499, 0.55, 0.58, 1.0])
def test_compatibility_reuses_owner_uncertainty_boundary(uncertainty: float) -> None:
    supplied = intake(uncertainty=uncertainty)
    assert repair_offer_intake_is_compatible(supplied) is (uncertainty < 0.55)


@pytest.mark.parametrize(
    "dimension",
    [
        NeedDimension.EMOTIONAL_PRESENCE,
        NeedDimension.CHALLENGE,
        NeedDimension.DECISION_SUPPORT,
        NeedDimension.CREATIVE_COLLABORATION,
    ],
)
@pytest.mark.parametrize("weight", [0.0, 0.6999, 0.7, 1.0])
def test_compatibility_reuses_all_existing_nonanswer_weight_routes(
    dimension: NeedDimension, weight: float
) -> None:
    supplied = intake(weights=(NeedWeight(dimension, weight),))
    assert repair_offer_intake_is_compatible(supplied) is (weight < 0.7)


@pytest.mark.parametrize("status", list(CognitionArtifactStatus))
def test_nonapplied_intakes_cannot_reenter_as_accepted_repair(
    status: CognitionArtifactStatus,
) -> None:
    supplied = intake(status=status)
    assert repair_offer_intake_is_compatible(supplied) is (
        status is CognitionArtifactStatus.APPLIED
    )


def test_explicit_presence_and_self_disclosure_keep_their_old_fallback_precedence() -> None:
    supplied = intake(
        signals=(PerceptionSignal.HIGH_DISTRESS,), status=CognitionArtifactStatus.FALLBACK
    )
    assert select_intake_position(
        supplied,
        status=CognitionArtifactStatus.FALLBACK,
        intent_registry_version=INTENT_REGISTRY_VERSION_V2,
    ) == (
        PositionStance.LISTEN,
        "Prioritize explicitly requested or safety-relevant presence.",
    )
    assert not repair_offer_intake_is_compatible(supplied)
    supplied = replace(
        supplied,
        perception=replace(
            supplied.perception, signals=(PerceptionSignal.SELF_DISCLOSURE_REQUEST,)
        ),
    )
    assert select_intake_position(
        supplied,
        status=CognitionArtifactStatus.FALLBACK,
        intent_registry_version=INTENT_REGISTRY_VERSION_V2,
    ) == (
        PositionStance.ANSWER,
        "Answer the explicit request about Satori from trusted self state.",
    )
    assert not repair_offer_intake_is_compatible(supplied)


def test_registry_v1_selection_stays_distinct_from_the_v2_compatibility_projection() -> None:
    supplied = intake(signals=(PerceptionSignal.EXPLICIT_TASK_ABANDONMENT,))
    assert (
        select_intake_position(
            supplied,
            status=CognitionArtifactStatus.APPLIED,
            intent_registry_version=INTENT_REGISTRY_VERSION_V1,
        )[0]
        is PositionStance.ANSWER
    )
    assert not repair_offer_intake_is_compatible(supplied)
