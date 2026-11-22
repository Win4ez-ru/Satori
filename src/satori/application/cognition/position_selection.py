"""Pure cognition-owned stance selection and narrowly scoped repair compatibility.

Conversation consumers may ask whether a new repair cue is compatible with the
existing position route; they do not duplicate or change cognition's precedence.
"""

from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V2,
    CognitionArtifactStatus,
    NeedDimension,
    PerceptionSignal,
    PositionStance,
    PreparedCognitionIntake,
)


def select_intake_position(
    intake: PreparedCognitionIntake,
    *,
    status: CognitionArtifactStatus,
    intent_registry_version: int,
) -> tuple[PositionStance, str]:
    """Select the existing stance and exact summary without evidence, appraisal or writes."""

    needs = intake.need_mix
    signals = set(intake.perception.signals)
    explicit_presence = bool(
        signals.intersection(
            {
                PerceptionSignal.EXPLICIT_LISTEN_REQUEST,
                PerceptionSignal.HIGH_DISTRESS,
                PerceptionSignal.HARMFUL_OVEREXTENSION,
            }
        )
    )
    if intent_registry_version >= INTENT_REGISTRY_VERSION_V2 and explicit_presence:
        stance = PositionStance.LISTEN
        summary = "Prioritize explicitly requested or safety-relevant presence."
    elif (
        intent_registry_version >= INTENT_REGISTRY_VERSION_V2
        and PerceptionSignal.SELF_DISCLOSURE_REQUEST in signals
    ):
        stance = PositionStance.ANSWER
        summary = "Answer the explicit request about Satori from trusted self state."
    elif status is CognitionArtifactStatus.FALLBACK or needs.uncertainty >= 0.55:
        stance = PositionStance.UNCERTAIN
        summary = "Respond conservatively and make material uncertainty explicit."
    elif PerceptionSignal.CORRECTION in signals:
        stance = PositionStance.ACKNOWLEDGE
        summary = "Acknowledge the correction before addressing the current request."
    elif (
        intent_registry_version >= INTENT_REGISTRY_VERSION_V2
        and PerceptionSignal.EXPLICIT_TASK_ABANDONMENT in signals
    ):
        stance = PositionStance.CHALLENGE
        summary = "Challenge the stated abandonment without attacking the person."
    elif (
        intent_registry_version >= INTENT_REGISTRY_VERSION_V2
        and PerceptionSignal.EXPLICIT_MOTIVATION_REQUEST in signals
    ):
        stance = PositionStance.COLLABORATE
        summary = "Provide the explicitly requested bounded motivational push."
    elif needs.weight(NeedDimension.EMOTIONAL_PRESENCE) >= 0.7:
        stance = PositionStance.LISTEN
        summary = "Prioritize attentive presence before analysis or advice."
    elif needs.weight(NeedDimension.CHALLENGE) >= 0.7:
        stance = PositionStance.CHALLENGE
        summary = "Offer an honest bounded challenge instead of automatic agreement."
    elif (
        needs.weight(NeedDimension.DECISION_SUPPORT) >= 0.7
        or needs.weight(NeedDimension.CREATIVE_COLLABORATION) >= 0.7
    ):
        stance = PositionStance.COLLABORATE
        summary = "Work through the request collaboratively while preserving evidence limits."
    else:
        stance = PositionStance.ANSWER
        summary = "Address the current request directly and preserve evidence limits."
    return stance, summary


def repair_offer_intake_is_compatible(intake: PreparedCognitionIntake) -> bool:
    """For an otherwise unprotected V2 intake, require the unchanged owner ANSWER route."""

    if intake.fallback_reasons or any(
        artifact.status is not CognitionArtifactStatus.APPLIED
        for artifact in (intake.perception, intake.need_mix, intake.retrieval_plan)
    ):
        return False
    stance, _summary = select_intake_position(
        intake,
        status=CognitionArtifactStatus.APPLIED,
        intent_registry_version=INTENT_REGISTRY_VERSION_V2,
    )
    return stance is PositionStance.ANSWER
