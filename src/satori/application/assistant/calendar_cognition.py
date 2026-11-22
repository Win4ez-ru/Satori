"""Reuse the existing Core's deterministic cognition for one transient user operation."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from satori.application.affect.contracts import EmotionAppraisalStatus, PreparedAffectiveContext
from satori.application.assistant.calendar_planning_contracts import CalendarPlanningError
from satori.application.cognition.contracts import (
    CognitionArtifactStatus,
    CognitionDialogueSignals,
    CognitionPipelineTrace,
)
from satori.application.conversation.character_agency import CharacterAgencyDecision
from satori.application.conversation.character_evidence import (
    CharacterRequestEvidence,
    analyze_character_request_evidence,
)
from satori.application.conversation.coherence import (
    DialogueCoherenceContext,
    analyze_dialogue_coherence,
)
from satori.application.conversation.context import plan_conversational_disclosure
from satori.application.conversation.contracts import (
    RecentConversationContext,
    RuntimeCharacterContext,
)
from satori.application.conversation.disclosure_contracts import is_satori_self_disclosure_plan
from satori.application.conversation.use_cases import TalkToSatori
from satori.application.models.contracts import CurrentModelsContext
from satori.application.positions.contracts import SatoriInclinationsContext, SatoriPositionsContext
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.application.retrieval.contracts import RetrievedMemoryContext
from satori.application.semantic.contracts import RetrievedSemanticContext


@dataclass(frozen=True, slots=True)
class CalendarCognitionBundle:
    """RAM-only builder inputs; operation refs are never canonical root/evidence IDs."""

    dialogue: DialogueCoherenceContext
    evidence: CharacterRequestEvidence
    agency: CharacterAgencyDecision | None
    trace: CognitionPipelineTrace | None
    current_user_ref: str
    interaction_ref: str


def prepare_calendar_cognition(
    *,
    core: TalkToSatori,
    character: RuntimeCharacterContext,
    user_text: str,
    request_id: str,
    recent_context: RecentConversationContext | None,
    prepared_affect: PreparedAffectiveContext | None,
    relationship: RelationshipExpressionContext | None,
    positions: SatoriPositionsContext | None,
    memory_context: RetrievedMemoryContext | None,
    semantic_context: RetrievedSemanticContext | None,
    model_context: CurrentModelsContext | None = None,
    inclination_context: SatoriInclinationsContext | None = None,
    operation_kind: Literal["calendar", "source_answer"] = "calendar",
) -> CalendarCognitionBundle:
    """Project only the actual user's request using existing V35 Core components.

    No Calendar observation is accepted by this helper. It neither constructs a
    planner/Core nor calls an appraisal model, repository or canonical Send. The
    caller retains source-policy admission for eligible history/memory and the
    separate provider-disclosure guard for the Calendar envelope.
    """
    try:
        identifier = UUID(request_id)
        if not identifier.int or str(identifier) != request_id:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise CalendarPlanningError("calendar_cognition_request_invalid") from None
    if (
        not isinstance(user_text, str)
        or not user_text.strip()
        or len(user_text) > core.max_user_chars
    ):
        raise CalendarPlanningError("calendar_cognition_input_invalid")
    if prepared_affect is not None and (
        prepared_affect.appraisal_status is not EmotionAppraisalStatus.SKIPPED
        or prepared_affect.expression.appraisal_status is not EmotionAppraisalStatus.SKIPPED
        or prepared_affect.transition is not None
        or prepared_affect.source_input_id is not None
        or prepared_affect.actual_input_root_message_ids
        or prepared_affect.actual_input_payload_refs
    ):
        raise CalendarPlanningError("calendar_cognition_appraisal_not_allowed")

    if operation_kind not in {"calendar", "source_answer"}:
        raise CalendarPlanningError("transient_cognition_operation_invalid")
    current_user_ref = f"{operation_kind}-input:{request_id}"
    interaction_ref = f"{operation_kind}-operation:{request_id}"
    version = core.request_builder.policy.schema_version
    dialogue = analyze_dialogue_coherence(user_text, recent_context, policy_schema_version=version)
    evidence = analyze_character_request_evidence(
        user_text, recent_context, policy_schema_version=version
    )
    disclosure = (
        plan_conversational_disclosure(user_text, dialogue, policy_schema_version=version)
        if version >= 25
        else None
    )
    signals = CognitionDialogueSignals(
        repeated_turn=dialogue.current_user_message_repeated,
        correction_active=any(
            (
                dialogue.current_no_routine_questions_correction,
                dialogue.current_informal_correction,
                dialogue.current_repetition_feedback,
                dialogue.current_relevance_feedback,
                dialogue.current_frustration_feedback,
                dialogue.current_contradiction_feedback,
            )
        ),
        no_routine_questions=dialogue.active_no_routine_questions_correction,
        current_activity=dialogue.current_activity_mention,
        explicit_listen_request=evidence.explicit_listen_request,
        high_distress=evidence.high_distress,
        harmful_overextension=evidence.harmful_overextension,
        explicit_motivation_request=evidence.explicit_motivation_request,
        explicit_task_abandonment=evidence.explicit_task_abandonment,
        explicit_repair_offer=evidence.explicit_repair_offer,
        self_disclosure_request=(
            disclosure is not None and is_satori_self_disclosure_plan(disclosure)
        ),
    )
    pipeline, kernel = core.cognition_pipeline, core.character_agency_kernel
    if version >= 28 and (pipeline is None or kernel is None or disclosure is None):
        raise CalendarPlanningError("calendar_cognition_unavailable")
    intake = (
        pipeline.prepare_intake(
            user_text=user_text,
            user_message_id=current_user_ref,
            interaction_id=interaction_ref,
            dialogue=signals,
        )
        if pipeline is not None
        else None
    )
    agency: CharacterAgencyDecision | None = None
    if version >= 28:
        assert kernel is not None
        assert intake is not None
        assert disclosure is not None
        agency = kernel.select(
            context=character,
            intake=intake,
            evidence=evidence,
            dialogue=dialogue,
            disclosure_plan=disclosure,
            emotional_context=prepared_affect.expression if prepared_affect is not None else None,
            relationship_context=relationship,
            position_context=positions,
            inclination_context=inclination_context,
        )
    trace = (
        pipeline.complete(
            intake,
            interaction_id=interaction_ref,
            available_evidence_ids=(
                *(recent_context.user_evidence_ids if recent_context is not None else ()),
                *(memory_context.grounding_ids if memory_context is not None else ()),
                *(semantic_context.grounding_ids if semantic_context is not None else ()),
                *(
                    model_context.grounding_ids
                    if model_context is not None and model_context.status == "available"
                    else ()
                ),
                *(
                    inclination_context.grounding_ids
                    if inclination_context is not None and inclination_context.status == "available"
                    else ()
                ),
            ),
            prepared_affect=prepared_affect,
            curiosity_influence=(
                inclination_context.curiosity_influence
                if inclination_context is not None and inclination_context.status == "available"
                else 0.0
            ),
        )
        if pipeline is not None and intake is not None
        else None
    )
    if version >= 28 and trace is not None and trace.status is CognitionArtifactStatus.FALLBACK:
        assert kernel is not None
        assert intake is not None
        agency = kernel.conservative_fallback(
            context=character,
            intake=intake,
            decision_schema_version=2 if version >= 36 else 1,
        )
    return CalendarCognitionBundle(
        dialogue, evidence, agency, trace, current_user_ref, interaction_ref
    )
