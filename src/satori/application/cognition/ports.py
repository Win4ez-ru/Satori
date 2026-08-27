"""Replaceable Stage 10 cognition planning port."""

from typing import Protocol

from satori.application.affect.contracts import PreparedAffectiveContext
from satori.application.cognition.contracts import (
    CognitionDialogueSignals,
    CognitionPipelineTrace,
    PreparedCognitionIntake,
)


class CognitionPlannerPort(Protocol):
    """Plan transient artifacts without persistence or domain mutation capability."""

    def prepare_intake(
        self,
        *,
        user_text: str,
        user_message_id: str,
        interaction_id: str,
        dialogue: CognitionDialogueSignals,
    ) -> PreparedCognitionIntake:
        """Build perception, need mix and retrieval plan."""

    def complete(
        self,
        intake: PreparedCognitionIntake,
        *,
        interaction_id: str,
        available_evidence_ids: tuple[str, ...],
        prepared_affect: PreparedAffectiveContext | None,
        curiosity_influence: float = 0.0,
    ) -> CognitionPipelineTrace:
        """Build appraisal projection, position, intent and response strategy."""
