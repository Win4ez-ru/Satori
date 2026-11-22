"""Eligibility for one optional repair-targeting interpretation, without state changes."""

from satori.application.cognition.contracts import PerceptionSignal, PreparedCognitionIntake
from satori.application.cognition.position_selection import repair_offer_intake_is_compatible
from satori.application.conversation.character_evidence import CharacterRequestEvidence
from satori.application.conversation.coherence import DialogueCoherenceContext


def repair_interpretation_is_eligible(
    intake: PreparedCognitionIntake,
    evidence: CharacterRequestEvidence,
    dialogue: DialogueCoherenceContext,
) -> bool:
    """Preserve existing obligations, closure and the cognition-owned position route.

    Lexical repair remains eligible so a supported third-party interpretation can clear
    a false target. Compound repair plus requested work is intentionally not broadened
    by this candidate. No emotion or relationship projection determines user intention.
    """

    protected = set(PerceptionSignal) - {
        PerceptionSignal.EXPLICIT_REPAIR_OFFER,
        PerceptionSignal.UNCERTAINTY_LANGUAGE,
    }
    return repair_offer_intake_is_compatible(intake) and not (
        protected.intersection(intake.perception.signals)
        or evidence.topic_closure
        or dialogue.current_user_message_repeated
        or dialogue.current_repetition_feedback
        or dialogue.current_frustration_feedback
    )
