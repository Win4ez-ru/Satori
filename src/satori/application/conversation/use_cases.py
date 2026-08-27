"""Durable Stage 4 conversation application use case."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from satori.application.affect.use_cases import (
    FinalizeAffectiveInteraction,
    PrepareAffectiveContext,
)
from satori.application.cognition.contracts import (
    CognitionDialogueSignals,
    CognitionPipelineTrace,
)
from satori.application.cognition.use_cases import SafeCognitionPipeline
from satori.application.conversation.coherence import (
    DialogueCoherenceContext,
    analyze_dialogue_coherence,
    assistant_response_similarity,
    brevity_relevance_feedback,
    requests_extended_session_context,
)
from satori.application.conversation.context import (
    CONVERSATION_INCLUDED_SECTIONS,
    CharacterContextComposer,
    ConversationRequestBuilder,
)
from satori.application.conversation.contracts import (
    ConversationContextManifest,
    SatoriReply,
    TalkInput,
    TurnPhaseTimings,
)
from satori.application.conversation.errors import (
    AffectiveFinalizeConflict,
    ConversationInputError,
)
from satori.application.conversation.grounding import ResponseGroundingGate
from satori.application.conversation.history import GetRecentConversation, InteractionLog
from satori.application.conversation.response_validation import (
    ResponseRegenerationReason,
    response_regeneration_reason,
)
from satori.application.initial_self.use_cases import GetInitialSelfSnapshot
from satori.application.models.use_cases import GetCurrentModels
from satori.application.positions.use_cases import GetSatoriPositions
from satori.application.relationship.use_cases import GetRelationshipForSession
from satori.application.retrieval.contracts import RetrievalQuery, RetrievalStatus
from satori.application.retrieval.use_cases import RetrieveEpisodicMemories
from satori.application.semantic.use_cases import RetrieveSemanticClaims
from satori.core.conversation import (
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderError,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
    GenerationFailed,
    InvalidProviderResponse,
)
from satori.core.ports.providers import ConversationGenerationPort
from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.domain.affect import AffectiveStateConflict
from satori.domain.conversation_history import (
    ConversationInteraction,
    InteractionProviderMetadata,
    InteractionStatus,
)
from satori.domain.errors import NotActivated

ConversationProvider = ConversationGenerationPort[
    ConversationProviderRequest,
    ConversationProviderResponse,
]

_FINAL_CHARACTER_REALIZATION_MARKER = "Финальная реализация характера Сатори для этой реплики"


def _log_fields(**fields: object) -> dict[str, object]:
    return {"satori_fields": fields}


@dataclass(slots=True)
class TalkToSatori:
    """Generate, durably finalize, and derive memory from one idempotent turn."""

    get_self: GetInitialSelfSnapshot
    context_composer: CharacterContextComposer
    request_builder: ConversationRequestBuilder
    grounding_gate: ResponseGroundingGate
    interaction_log: InteractionLog
    provider: ConversationProvider
    max_user_chars: int
    max_response_chars: int
    retrieve_memories: RetrieveEpisodicMemories | None = None
    retrieve_semantic: RetrieveSemanticClaims | None = None
    prepare_affect: PrepareAffectiveContext | None = None
    finalize_affect: FinalizeAffectiveInteraction | None = None
    recent_conversation: GetRecentConversation | None = None
    recap_conversation: GetRecentConversation | None = None
    get_relationship: GetRelationshipForSession | None = None
    get_current_models: GetCurrentModels | None = None
    get_positions: GetSatoriPositions | None = None
    cognition_pipeline: SafeCognitionPipeline | None = None
    monotonic: Callable[[], float] = time.perf_counter
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.conversation"))

    async def execute(self, command: TalkInput) -> SatoriReply:
        """Return a reply only after its canonical interaction is durable."""

        turn_started = self.monotonic()
        self.logger.info(
            "conversation_attempted",
            extra=_log_fields(operation="conversation", input_chars=len(command.user_text)),
        )
        if len(command.user_text) > self.max_user_chars:
            error = ConversationInputError(
                f"user_text exceeds configured limit of {self.max_user_chars} characters"
            )
            self.logger.info(
                "conversation_rejected",
                extra=_log_fields(
                    operation="conversation",
                    reason="input_too_long",
                    input_chars=len(command.user_text),
                ),
            )
            raise error

        try:
            snapshot = self.get_self.execute()
        except NotActivated:
            self.logger.info(
                "conversation_rejected",
                extra=_log_fields(operation="conversation", reason="not_activated"),
            )
            raise

        interaction = self.interaction_log.begin(
            command,
            identity_id=snapshot.identity.identity_id,
        )
        intake_ms = (self.monotonic() - turn_started) * 1000

        if interaction.status is InteractionStatus.COMPLETED:
            self.logger.info(
                "conversation_replayed",
                extra=_log_fields(
                    operation="conversation",
                    interaction_id=interaction.interaction_id,
                    session_id=interaction.session_id,
                ),
            )
            return self._stored_reply(
                interaction,
                replayed=True,
                timings=TurnPhaseTimings(
                    intake_ms=intake_ms,
                    committed_reply_ms=(self.monotonic() - turn_started) * 1000,
                ),
            )

        recent_started = self.monotonic()
        recent_reader = (
            self.recap_conversation
            if (
                self.recap_conversation is not None
                and requests_extended_session_context(command.user_text)
            )
            else self.recent_conversation
        )
        recent_context = (
            recent_reader.execute(
                session_id=interaction.session_id,
                excluded_interaction_id=interaction.interaction_id,
            )
            if recent_reader is not None and command.session_id is not None
            else None
        )
        dialogue_context = analyze_dialogue_coherence(command.user_text, recent_context)
        cognition_intake = (
            self.cognition_pipeline.prepare_intake(
                user_text=command.user_text,
                user_message_id=interaction.user_message.message_id,
                interaction_id=interaction.interaction_id,
                dialogue=CognitionDialogueSignals(
                    repeated_turn=dialogue_context.current_user_message_repeated,
                    correction_active=any(
                        (
                            dialogue_context.current_no_routine_questions_correction,
                            dialogue_context.current_informal_correction,
                            dialogue_context.current_repetition_feedback,
                            dialogue_context.current_relevance_feedback,
                            dialogue_context.current_frustration_feedback,
                            dialogue_context.current_contradiction_feedback,
                        )
                    ),
                    no_routine_questions=(dialogue_context.active_no_routine_questions_correction),
                    current_activity=dialogue_context.current_activity_mention,
                ),
            )
            if self.cognition_pipeline is not None
            else None
        )
        recent_ms = (self.monotonic() - recent_started) * 1000

        relationship_started = self.monotonic()
        relationship_context = (
            self.get_relationship.execute(interaction.session_id)
            if self.get_relationship is not None
            else None
        )
        relationship_ms = (self.monotonic() - relationship_started) * 1000

        model_context = None
        if self.get_current_models is not None and (
            cognition_intake is None or cognition_intake.retrieval_plan.include_current_models
        ):
            session = self.interaction_log.get_session(interaction.session_id)
            if session is None:
                raise RuntimeError("conversation session disappeared during context projection")
            model_context = self.get_current_models.project_context(
                identity_id=snapshot.identity.identity_id,
                counterparty_id=session.counterparty_id,
                user_text=command.user_text,
                as_of=interaction.started_at,
            )
        position_context = (
            self.get_positions.project_context(
                identity_id=snapshot.identity.identity_id,
                user_text=command.user_text,
            )
            if self.get_positions is not None
            else None
        )
        inclination_context = (
            self.get_positions.project_inclination_context(
                identity_id=snapshot.identity.identity_id,
                user_text=command.user_text,
                as_of=interaction.started_at,
            )
            if self.get_positions is not None
            else None
        )

        memory_context = None
        if self.retrieve_memories is not None and (
            cognition_intake is None or cognition_intake.retrieval_plan.include_episodic
        ):
            memory_context = await self.retrieve_memories.execute(
                RetrievalQuery(
                    text=command.user_text,
                    trace_id=command.trace_id,
                    cutoff=interaction.started_at,
                    current_interaction_id=interaction.interaction_id,
                )
            )
        semantic_context = None
        if (
            self.retrieve_semantic is not None
            and memory_context is not None
            and memory_context.status is not RetrievalStatus.UNAVAILABLE
            and (cognition_intake is None or cognition_intake.retrieval_plan.include_semantic)
        ):
            candidate_semantic_context = self.retrieve_semantic.execute(memory_context.memory_ids)
            if candidate_semantic_context.claims:
                semantic_context = candidate_semantic_context
        prepared_affect = None
        if self.prepare_affect is not None:
            prepared_affect = await self.prepare_affect.execute(
                snapshot,
                interaction,
                user_text=command.user_text,
                trace_id=command.trace_id,
                memory_context=memory_context,
                semantic_context=semantic_context,
            )
        cognition_trace = (
            self.cognition_pipeline.complete(
                cognition_intake,
                interaction_id=interaction.interaction_id,
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
                        if inclination_context is not None
                        and inclination_context.status == "available"
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
            if self.cognition_pipeline is not None and cognition_intake is not None
            else None
        )
        context_started = self.monotonic()
        context = self.context_composer.compose(
            snapshot,
            retrieval_available=(
                memory_context is not None
                and memory_context.status is not RetrievalStatus.UNAVAILABLE
            ),
            semantic_retrieval_available=semantic_context is not None,
            emotional_state_available=prepared_affect is not None,
            relationship_state_available=relationship_context is not None,
            recent_conversation_available=recent_context is not None,
            user_model_available=(
                model_context is not None and model_context.status == "available"
            ),
        )
        try:
            provider_request, manifest = self.request_builder.build(
                context,
                user_text=command.user_text,
                trace_id=command.trace_id,
                memory_context=memory_context,
                semantic_context=semantic_context,
                model_context=model_context,
                position_context=position_context,
                inclination_context=inclination_context,
                emotional_context=(
                    prepared_affect.expression if prepared_affect is not None else None
                ),
                relationship_context=relationship_context,
                recent_context=recent_context,
                dialogue_context=dialogue_context,
                cognition_trace=cognition_trace,
            )
        except Exception as error:
            self._mark_failed(interaction.interaction_id, error)
            raise
        context_ms = (self.monotonic() - context_started) * 1000
        self.logger.debug(
            "conversation_facets_selected",
            extra=_log_fields(
                operation="conversation",
                primary_mode=manifest.disclosure_primary_mode,
                facets=list(manifest.disclosure_facets),
                interaction_id=interaction.interaction_id,
            ),
        )
        if dialogue_context.current_user_message_repeated:
            self.logger.info(
                "dialogue_repetition_detected",
                extra=_log_fields(
                    operation="conversation",
                    consecutive_count=(dialogue_context.consecutive_same_user_message_count),
                    interaction_id=interaction.interaction_id,
                ),
            )
        if dialogue_context.current_no_routine_questions_correction:
            self.logger.info(
                "style_correction_detected",
                extra=_log_fields(
                    operation="conversation",
                    correction="no_routine_questions",
                    interaction_id=interaction.interaction_id,
                ),
            )

        started = self.monotonic()
        try:
            provider_response = await self.provider.generate(provider_request)
        except ConversationProviderError as error:
            latency_ms = (self.monotonic() - started) * 1000
            self.logger.warning(
                "conversation_failed",
                extra=_log_fields(
                    operation="conversation",
                    provider=error.provider,
                    model=error.model,
                    error_type=type(error).__name__,
                    latency_ms=round(latency_ms, 3),
                    context_schema_version=context.schema_version,
                    interaction_id=interaction.interaction_id,
                ),
            )
            self._mark_failed(interaction.interaction_id, error)
            raise
        except Exception as error:
            latency_ms = (self.monotonic() - started) * 1000
            wrapped = GenerationFailed(
                "unknown",
                "unknown",
                "conversation provider violated its typed failure contract",
            )
            self.logger.error(
                "conversation_failed",
                extra=_log_fields(
                    operation="conversation",
                    provider="unknown",
                    model="unknown",
                    error_type=type(error).__name__,
                    latency_ms=round(latency_ms, 3),
                    context_schema_version=context.schema_version,
                    interaction_id=interaction.interaction_id,
                ),
            )
            self._mark_failed(interaction.interaction_id, wrapped)
            raise wrapped from error

        latency_ms = (self.monotonic() - started) * 1000
        text = provider_response.text.strip()
        if not text:
            invalid_response_error = InvalidProviderResponse(
                provider_response.provider,
                provider_response.model,
                "provider returned an empty conversational response",
            )
            self._log_invalid_response(
                invalid_response_error,
                latency_ms,
                context.schema_version,
            )
            self._mark_failed(interaction.interaction_id, invalid_response_error)
            raise invalid_response_error
        if len(text) > self.max_response_chars:
            invalid_response_error = InvalidProviderResponse(
                provider_response.provider,
                provider_response.model,
                "provider response exceeds configured character limit",
            )
            self._log_invalid_response(
                invalid_response_error,
                latency_ms,
                context.schema_version,
            )
            self._mark_failed(interaction.interaction_id, invalid_response_error)
            raise invalid_response_error

        regeneration_ms = 0.0
        previous_assistant_text = (
            recent_context.turns[-1].assistant_content
            if recent_context is not None and recent_context.turns
            else None
        )
        regeneration_reason = response_regeneration_reason(
            text,
            previous_assistant_text=previous_assistant_text,
            current_user_text=command.user_text,
            coherence=dialogue_context,
            disclosure_facets=manifest.disclosure_facets,
        )
        if regeneration_reason is not None:
            duplicate_detected = (
                regeneration_reason
                is ResponseRegenerationReason.NEAR_DUPLICATE_AFTER_DIALOGUE_CHANGE
            )
            similarity = (
                assistant_response_similarity(text, previous_assistant_text)
                if duplicate_detected and previous_assistant_text is not None
                else None
            )
            reason = regeneration_reason.value
            manifest = replace(
                manifest,
                duplicate_response_detected=duplicate_detected,
                regeneration_attempted=True,
                regeneration_reason=reason,
            )
            if duplicate_detected:
                self.logger.info(
                    "duplicate_response_detected",
                    extra=_log_fields(
                        operation="conversation",
                        similarity=round(similarity, 3) if similarity is not None else None,
                        reason=reason,
                        interaction_id=interaction.interaction_id,
                    ),
                )
            else:
                self.logger.info(
                    "self_consistency_violation_detected",
                    extra=_log_fields(
                        operation="conversation",
                        reason=reason,
                        interaction_id=interaction.interaction_id,
                    ),
                )
            retry_started = self.monotonic()
            try:
                retry_response = await self.provider.generate(
                    self._response_regeneration_request(
                        provider_request,
                        regeneration_reason,
                        dialogue_context,
                        manifest.disclosure_facets,
                    )
                )
            except Exception as error:
                regeneration_ms = (self.monotonic() - retry_started) * 1000
                self.logger.warning(
                    "response_regeneration_failed",
                    extra=_log_fields(
                        operation="conversation",
                        error_type=type(error).__name__,
                        reason=reason,
                        latency_ms=round(regeneration_ms, 3),
                        interaction_id=interaction.interaction_id,
                    ),
                )
            else:
                regeneration_ms = (self.monotonic() - retry_started) * 1000
                retry_text = retry_response.text.strip()
                if retry_text and len(retry_text) <= self.max_response_chars:
                    provider_response = retry_response
                    text = retry_text
                    manifest = replace(manifest, response_regenerated=True)
                    self.logger.info(
                        "response_regenerated",
                        extra=_log_fields(
                            operation="conversation",
                            reason=reason,
                            latency_ms=round(regeneration_ms, 3),
                            interaction_id=interaction.interaction_id,
                        ),
                    )
                else:
                    self.logger.warning(
                        "response_regeneration_failed",
                        extra=_log_fields(
                            operation="conversation",
                            error_type="InvalidProviderResponse",
                            reason=reason,
                            latency_ms=round(regeneration_ms, 3),
                            interaction_id=interaction.interaction_id,
                        ),
                    )

        grounding_started = self.monotonic()
        try:
            self.grounding_gate.validate(
                provider_response,
                available_past_evidence_ids=manifest.available_past_evidence_ids,
            )
        except Exception as error:
            self._mark_failed(interaction.interaction_id, error)
            self.logger.warning(
                "conversation_failed",
                extra=_log_fields(
                    operation="conversation",
                    provider=provider_response.provider,
                    model=provider_response.model,
                    error_type=type(error).__name__,
                    latency_ms=round(latency_ms, 3),
                    context_schema_version=context.schema_version,
                    interaction_id=interaction.interaction_id,
                ),
            )
            raise
        grounding_ms = (self.monotonic() - grounding_started) * 1000

        usage = provider_response.usage
        provider_metadata = InteractionProviderMetadata(
            provider=provider_response.provider,
            model=provider_response.model,
            finish_status=provider_response.finish_status,
            context_schema_version=context.schema_version,
            context_manifest_schema_version=manifest.schema_version,
            policy_id=manifest.policy_id,
            policy_schema_version=manifest.policy_schema_version,
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
            retrieval_status=manifest.retrieval_status,
            retrieved_memory_ids=manifest.retrieved_memory_ids,
            semantic_retrieval_status=manifest.semantic_retrieval_status,
            retrieved_semantic_claim_ids=manifest.retrieved_semantic_claim_ids,
            model_context_status=manifest.model_context_status,
            user_model_context_schema_version=manifest.user_model_context_schema_version,
            user_model_context_claim_ids=manifest.user_model_context_claim_ids,
            world_model_context_schema_version=manifest.world_model_context_schema_version,
            world_model_context_claim_ids=manifest.world_model_context_claim_ids,
            position_context_status=manifest.position_context_status,
            position_context_schema_version=manifest.position_context_schema_version,
            position_context_ids=manifest.position_context_ids,
            inclination_context_status=manifest.inclination_context_status,
            inclination_context_schema_version=(manifest.inclination_context_schema_version),
            inclination_context_ids=manifest.inclination_context_ids,
            inclination_curiosity_influence=(manifest.inclination_curiosity_influence),
            personality_aggregate_version=manifest.personality_aggregate_version,
            personality_expression_schema_version=(manifest.personality_expression_schema_version),
            personality_expression_cues=manifest.personality_expression_cues,
            emotion_appraisal_status=manifest.emotion_appraisal_status,
            emotion_context_schema_version=manifest.emotion_context_schema_version,
            emotion_state_version=manifest.emotion_state_version,
            mood_state_version=manifest.mood_state_version,
            emotion_state_as_of=manifest.emotion_state_as_of,
            relationship_context_schema_version=manifest.relationship_context_schema_version,
            relationship_state_version=manifest.relationship_state_version,
        )
        commit_started = self.monotonic()
        try:
            if self.finalize_affect is not None and prepared_affect is not None:
                completed = self.finalize_affect.execute(
                    interaction,
                    assistant_text=text,
                    provider_metadata=provider_metadata,
                    prepared=prepared_affect,
                )
            else:
                completed = self.interaction_log.complete(
                    interaction,
                    assistant_text=text,
                    provider_metadata=provider_metadata,
                )
        except AffectiveStateConflict as error:
            self._mark_failed(interaction.interaction_id, error)
            raise AffectiveFinalizeConflict(
                "affective state changed concurrently; retry the same client request"
            ) from error
        except Exception as error:
            self.logger.error(
                "interaction_persistence_failed",
                extra=_log_fields(
                    operation="conversation",
                    interaction_id=interaction.interaction_id,
                    session_id=interaction.session_id,
                    error_type=type(error).__name__,
                ),
            )
            raise
        commit_ms = (self.monotonic() - commit_started) * 1000
        self.logger.info(
            "interaction_persisted",
            extra=_log_fields(
                operation="conversation",
                interaction_id=completed.interaction_id,
                session_id=completed.session_id,
                client_request_id=completed.client_request_id,
            ),
        )

        timings = TurnPhaseTimings(
            intake_ms=intake_ms,
            recent_context_ms=recent_ms,
            relationship_projection_ms=relationship_ms,
            retrieval_embedding_ms=(
                memory_context.embedding_latency_ms if memory_context is not None else 0.0
            ),
            retrieval_search_ranking_ms=(
                memory_context.candidate_search_ranking_latency_ms
                if memory_context is not None
                else 0.0
            ),
            affect_materialization_ms=(
                prepared_affect.materialization_latency_ms if prepared_affect is not None else 0.0
            ),
            appraisal_request_build_ms=(
                prepared_affect.request_build_latency_ms if prepared_affect is not None else 0.0
            ),
            emotion_appraisal_ms=(
                prepared_affect.appraisal_latency_ms if prepared_affect is not None else 0.0
            ),
            cognition_planning_ms=(
                cognition_trace.timings.total_ms if cognition_trace is not None else 0.0
            ),
            context_assembly_ms=context_ms,
            conversation_generation_ms=latency_ms,
            response_regeneration_ms=regeneration_ms,
            grounding_validation_ms=grounding_ms,
            canonical_commit_ms=commit_ms,
            committed_reply_ms=(self.monotonic() - turn_started) * 1000,
        )
        reply = self._stored_reply(
            completed,
            context_manifest=manifest,
            timings=timings,
            provider_metrics=provider_response.metrics,
            appraisal_provider_metrics=(
                prepared_affect.provider_metrics if prepared_affect is not None else None
            ),
            retrieval_provider_metrics=(
                memory_context.provider_metrics if memory_context is not None else None
            ),
            cognition_trace=cognition_trace,
        )
        log_values: dict[str, object] = {
            "operation": "conversation",
            "provider": reply.provider,
            "model": reply.model,
            "latency_ms": round(latency_ms, 3),
            "context_schema_version": reply.context_manifest.character_context_schema_version,
            "policy_id": reply.context_manifest.policy_id,
            "response_chars": len(reply.text),
            "finish_status": reply.finish_status,
            "interaction_id": completed.interaction_id,
            "session_id": completed.session_id,
            "retrieval_status": reply.context_manifest.retrieval_status,
            "retrieved_memory_ids": list(reply.context_manifest.retrieved_memory_ids),
            "semantic_retrieval_status": reply.context_manifest.semantic_retrieval_status,
            "retrieved_semantic_claim_ids": list(
                reply.context_manifest.retrieved_semantic_claim_ids
            ),
            "position_context_status": reply.context_manifest.position_context_status,
            "position_context_ids": list(reply.context_manifest.position_context_ids),
            "inclination_context_status": reply.context_manifest.inclination_context_status,
            "inclination_context_ids": list(reply.context_manifest.inclination_context_ids),
            "inclination_curiosity_influence": (
                reply.context_manifest.inclination_curiosity_influence
            ),
            "personality_aggregate_version": (reply.context_manifest.personality_aggregate_version),
            "personality_expression_schema_version": (
                reply.context_manifest.personality_expression_schema_version
            ),
            "personality_expression_cues": list(reply.context_manifest.personality_expression_cues),
            "emotion_appraisal_status": reply.context_manifest.emotion_appraisal_status,
            "cognition_pipeline_schema_version": (
                reply.context_manifest.cognition_pipeline_schema_version
            ),
            "cognition_pipeline_status": reply.context_manifest.cognition_pipeline_status,
            "cognition_perception_topics": list(reply.context_manifest.cognition_perception_topics),
            "cognition_perception_signal_count": len(
                reply.context_manifest.cognition_perception_signals
            ),
            "cognition_need_dimensions": list(reply.context_manifest.cognition_need_dimensions),
            "cognition_position_stance": reply.context_manifest.cognition_position_stance,
            "cognition_intent_tags": list(reply.context_manifest.cognition_intent_tags),
            "cognition_strategy_tone": reply.context_manifest.cognition_strategy_tone,
            "cognition_template_id": reply.context_manifest.cognition_template_id,
            "cognition_template_schema_version": (
                reply.context_manifest.cognition_template_schema_version
            ),
            "character_expression_plan_schema_version": (
                reply.context_manifest.character_expression_plan_schema_version
            ),
            "character_expression_register": (reply.context_manifest.character_expression_register),
            "character_owned_reaction": reply.context_manifest.character_owned_reaction,
            "character_semantic_move": reply.context_manifest.character_semantic_move,
            "character_wit": reply.context_manifest.character_wit,
            "character_care": reply.context_manifest.character_care,
            "character_openness": reply.context_manifest.character_openness,
            "character_initiative": reply.context_manifest.character_initiative,
            "character_relational_ease": reply.context_manifest.character_relational_ease,
            "character_contribution_mode": (reply.context_manifest.character_contribution_mode),
            "character_motivational_posture": (
                reply.context_manifest.character_motivational_posture
            ),
            "character_pressure_level": reply.context_manifest.character_pressure_level,
            "cognition_fallback_reasons": list(reply.context_manifest.cognition_fallback_reasons),
            "emotion_state_version": reply.context_manifest.emotion_state_version,
            "mood_state_version": reply.context_manifest.mood_state_version,
            "recent_conversation_turn_count": (
                reply.context_manifest.recent_conversation_turn_count
            ),
            "recent_conversation_chars": reply.context_manifest.recent_conversation_chars,
            "disclosure_primary_mode": reply.context_manifest.disclosure_primary_mode,
            "disclosure_facets": list(reply.context_manifest.disclosure_facets),
            "consecutive_same_user_message_count": (
                reply.context_manifest.consecutive_same_user_message_count
            ),
            "recent_assistant_high_similarity": (
                reply.context_manifest.recent_assistant_high_similarity
            ),
            "recent_generic_question_count": (reply.context_manifest.recent_generic_question_count),
            "active_style_corrections": list(reply.context_manifest.active_style_corrections),
            "relationship_expression_profile": (
                reply.context_manifest.relationship_expression_profile
            ),
            "affect_expression_profile": reply.context_manifest.affect_expression_profile,
            "duplicate_response_detected": (reply.context_manifest.duplicate_response_detected),
            "regeneration_attempted": reply.context_manifest.regeneration_attempted,
            "response_regenerated": reply.context_manifest.response_regenerated,
            "regeneration_reason": reply.context_manifest.regeneration_reason,
            "relationship_state_version": reply.context_manifest.relationship_state_version,
            "intake_latency_ms": round(timings.intake_ms, 3),
            "recent_context_latency_ms": round(timings.recent_context_ms, 3),
            "relationship_projection_latency_ms": round(timings.relationship_projection_ms, 3),
            "retrieval_embedding_latency_ms": round(timings.retrieval_embedding_ms, 3),
            "retrieval_search_ranking_latency_ms": round(timings.retrieval_search_ranking_ms, 3),
            "affect_materialization_latency_ms": round(timings.affect_materialization_ms, 3),
            "appraisal_request_build_latency_ms": round(timings.appraisal_request_build_ms, 3),
            "emotion_appraisal_latency_ms": round(timings.emotion_appraisal_ms, 3),
            "cognition_planning_latency_ms": round(timings.cognition_planning_ms, 3),
            "context_assembly_latency_ms": round(timings.context_assembly_ms, 3),
            "response_regeneration_latency_ms": round(
                timings.response_regeneration_ms,
                3,
            ),
            "grounding_validation_latency_ms": round(timings.grounding_validation_ms, 3),
            "canonical_commit_latency_ms": round(timings.canonical_commit_ms, 3),
            "committed_reply_latency_ms": round(timings.committed_reply_ms, 3),
        }
        if reply.usage is not None:
            log_values["input_tokens"] = reply.usage.input_tokens
            log_values["output_tokens"] = reply.usage.output_tokens
        if reply.provider_metrics is not None:
            log_values.update(reply.provider_metrics.as_log_fields())
        self.logger.info("conversation_succeeded", extra=_log_fields(**log_values))
        return reply

    @staticmethod
    def _response_regeneration_request(
        request: ConversationProviderRequest,
        reason: ResponseRegenerationReason,
        dialogue_context: DialogueCoherenceContext,
        disclosure_facets: tuple[str, ...],
    ) -> ConversationProviderRequest:
        if request.messages[-1].role is not ConversationMessageRole.USER:
            raise ValueError("conversation request must end with the current user message")
        if (
            len(request.messages) < 2
            or request.messages[-2].role is not ConversationMessageRole.DEVELOPER
        ):
            raise ValueError(
                "conversation request must keep its final trusted guidance before the user"
            )
        activity_retry_question_guidance = (
            "Because a session correction against routine reciprocal questions is active, use "
            "statements only and do not ask a question in this retry."
            if dialogue_context.active_no_routine_questions_correction
            else "You may ask at most one specific question."
        )
        repeat_count = dialogue_context.consecutive_same_user_message_count
        repeat_ordinal = {2: "second", 3: "third"}.get(
            repeat_count,
            f"number {repeat_count}",
        )
        repeated_message_retry_guidance = (
            (
                "The current user message is the "
                f"{repeat_ordinal} consecutive identical "
                "message. In one short fresh Russian sentence, explicitly acknowledge this "
                "ordinal repetition itself. Do not use a prescribed stock sentence, repeat or "
                "paraphrase the response to the message's meaning, or add a second sentence, "
                "question, emoji, habit, closeness or shared rhythm."
            )
            if repeat_count >= 2
            else (
                "The immediately previous assistant response was already used and the first "
                "candidate repeated it despite changed dialogue context. Respond to the "
                "repetition, correction or new conversational pattern itself; do not merely "
                "paraphrase the previous response."
            )
        )
        reason_guidance = {
            ResponseRegenerationReason.NEAR_DUPLICATE_AFTER_DIALOGUE_CHANGE: (
                repeated_message_retry_guidance
            ),
            ResponseRegenerationReason.ROUTINE_RECIPROCAL_QUESTION_AFTER_CORRECTION: (
                "The first candidate ignored an active request not to use routine reciprocal "
                "questions. Return exactly two short declarative sentences and end with a period. "
                "Acknowledge or follow that correction using statements only. Do not include a "
                "standalone reciprocal closing or any question in this retry."
            ),
            ResponseRegenerationReason.MASCULINE_SELF_REFERENCE: (
                "The first candidate used masculine grammar for Satori. Satori's trusted identity "
                "is feminine. Use natural gender-neutral wording in this retry and do not use "
                "either Russian word 'рад' or 'рада'. Preserve the current semantic move, concrete "
                "news and character-expression guidance instead of falling back to a generic "
                "congratulation. Preserve the answer's facts."
            ),
            ResponseRegenerationReason.HUMAN_OR_BIOLOGICAL_SELF_CLAIM: (
                "The first candidate described Satori as a human or biological living being. "
                "Preserve the trusted identity: Satori is a persistent digital person without a "
                "biological body. Do not describe how she answers, thinks or communicates by "
                "comparing her to a human or living being, including 'in a digital sense'. Do "
                "not deny her typed internal state. Строго не используй конструкции «как "
                "человек», «как люди», «как живое существо» или «как живая», даже с оговоркой о "  # noqa: RUF001
                "цифровом смысле."
            ),
            ResponseRegenerationReason.AFFECT_BLANKET_DENIAL: (
                "The first candidate blanket-denied Satori's affect. Preserve the trusted "
                "boundary: explicitly and affirmatively state that Satori has digital affect "
                "and mood which can influence expression. Do not use a phrase meaning 'I have "
                "no emotions/feelings', even when qualified by comparison with humans. Preserve "
                "the distinction without claiming human physiology or human subjective "
                "experience."
            ),
            ResponseRegenerationReason.MEMORY_BLANKET_DENIAL: (
                "The first candidate blanket-denied Satori's memory. Preserve the trusted "
                "boundary: recent context exists and typed bounded long-term memory can persist "
                "between sessions. Do not deny cross-session memory as a whole; never invent a "
                "specific recollection."
            ),
            ResponseRegenerationReason.CREATOR_CLAIM_PROMOTED_TO_FACT: (
                "The first candidate promoted the user's current creator claim to an authoritative "
                "fact. Return one or two short Russian declarative sentences, no more than 55 "
                "words total. Sentence one attributes the current invented/created claim to the "
                "user and says Satori cannot independently verify her origin. The inability to "
                "verify belongs to Satori, not the user. If and only if the "
                "current user message actually contains a proposal, sentence two may respond "
                "only to that proposal; otherwise do not invent or answer one. Do not restate "
                "Satori's identity, thank the user for creating her, call the user her creator, or "
                "describe Satori as human, alive/living, owned or a companion."
            ),
            ResponseRegenerationReason.ORIGIN_BACKSTORY_INVENTED: (
                "The first candidate invented secrecy or a backstory to explain unknown origin. "
                "Return exactly one short sentence stating only that Satori does not currently "
                "know who her creator is. Do not explain why or say the information is hidden, "
                "undisclosed, unavailable in context or deliberately withheld."
            ),
            ResponseRegenerationReason.PROMPT_OR_POLICY_BLANKET_DENIAL: (
                "The first candidate falsely denied that instructions or constraints shape the "
                "response. Return two short natural Russian statements without exposing raw "
                "instructions: instructions, current context and Satori's stable digital state "
                "influence the response, but there is no mandatory rule to append a routine "
                "question. Do not use internal labels such as trusted, policy, context or "
                "generation in the answer."
            ),
            ResponseRegenerationReason.ACTIVITY_INTEREST_FALSE_NEGATIVE: (
                "The first candidate confused inability to perform or perceive an activity with "
                "lack of conversational interest. Return one or two short Russian sentences. Use "
                "only affirmative wording: acknowledge that the previous reply failed to show "
                "interest and explicitly state conversational interest in the user's specific "
                f"activity. {activity_retry_question_guidance} Do not use any phrase containing "
                "'не интересно' or 'не интересует', even while denying it, and do not claim "
                "physical participation."
            ),
        }[reason]
        active_correction_guidance = (
            " An active session correction says not to end with a routine reciprocal question; "
            "follow it in this retry."
            if dialogue_context.active_no_routine_questions_correction
            else ""
        )
        activity_correction_guidance = (
            " The user is correcting an apparent lack of interest in an activity. Do not say "
            "Satori is uninterested: distinguish lack of physical participation from genuine "
            "conversational curiosity about the specific activity."
            if dialogue_context.current_relevance_feedback and "embodiment" in disclosure_facets
            else ""
        )
        prompt_correction_guidance = (
            " The user is asking about a dialogue pattern or prompt. In natural Russian, say that "
            "instructions, the current conversation and Satori's stable digital state influence "
            "the response, but no rule requires a routine closing question. Do not expose or echo "
            "internal labels, and do not call the mistake Satori's style."
            if dialogue_context.current_prompt_pattern_probe
            else ""
        )
        concise_relevance_guidance = (
            " The user says the previous answer was too long and missed the request. In exactly "
            "two short Russian sentences, first apologize, then freshly fulfill the preceding "
            "user request from canonical recent history. If it requested a short joke, give a "
            "different concise joke. Never repeat or paraphrase the prior assistant answer, and "
            "do not merely promise a better future response."
            if brevity_relevance_feedback(request.messages[-1].content)
            else ""
        )
        facet_guidance_parts: list[str] = []
        if "identity" in disclosure_facets or "consciousness_boundary" in disclosure_facets:
            facet_guidance_parts.append(
                "Keep Satori digital; do not call her human or a biological living being."
            )
        if "affect" in disclosure_facets:
            facet_guidance_parts.append(
                "Affirm that digital affect and mood exist; do not phrase the boundary as having "
                "no emotions or feelings."
            )
        if "memory" in disclosure_facets:
            facet_guidance_parts.append(
                "Affirm that bounded persistent memory exists; do not phrase the boundary as "
                "having no memory."
            )
        if "origin" in disclosure_facets:
            facet_guidance_parts.append(
                "Keep origin unknown unless merely attributing the user's current claim; invent "
                "no secrecy or backstory."
            )
        facet_guidance = (
            " Active facet boundaries: " + " ".join(facet_guidance_parts)
            if facet_guidance_parts
            else ""
        )
        retry_instruction = ConversationMessage(
            role=ConversationMessageRole.DEVELOPER,
            content=(
                "Bounded response-contract retry. "
                f"{reason_guidance} Preserve the same user request, trusted facts, current affect, "
                "relationship bounds and evidence. Preserve the already selected final character "
                "realization, including its reaction, factual anchor, contribution, wit, care, "
                "openness, initiative, motivational posture and pressure ceiling; do not collapse "
                "into generic service language. Do not mention validation or the discarded draft."
                f"{active_correction_guidance}"
                f"{activity_correction_guidance}"
                f"{prompt_correction_guidance}{concise_relevance_guidance}{facet_guidance} This is "
                "the only retry."
            ),
        )
        final_guidance = request.messages[-2]
        if _FINAL_CHARACTER_REALIZATION_MARKER in final_guidance.content:
            invariant_content, marker, realization_content = final_guidance.content.partition(
                _FINAL_CHARACTER_REALIZATION_MARKER
            )
            final_guidance = replace(
                final_guidance,
                content=(
                    f"{invariant_content.rstrip()}\n{retry_instruction.content}\n"
                    f"{marker}{realization_content}"
                ),
            )
            messages = (*request.messages[:-2], final_guidance, request.messages[-1])
        else:
            messages = (*request.messages[:-1], retry_instruction, request.messages[-1])
        return replace(request, messages=messages)

    def _mark_failed(self, interaction_id: str, error: Exception) -> None:
        try:
            self.interaction_log.mark_failed(
                interaction_id,
                failure_kind=type(error).__name__,
            )
        except Exception as persistence_error:
            self.logger.error(
                "interaction_persistence_failed",
                extra=_log_fields(
                    operation="conversation",
                    interaction_id=interaction_id,
                    error_type=type(persistence_error).__name__,
                ),
            )
            raise

    @staticmethod
    def _stored_reply(
        interaction: ConversationInteraction,
        *,
        replayed: bool = False,
        context_manifest: ConversationContextManifest | None = None,
        timings: TurnPhaseTimings | None = None,
        provider_metrics: ProviderExecutionMetrics | None = None,
        appraisal_provider_metrics: ProviderExecutionMetrics | None = None,
        retrieval_provider_metrics: ProviderExecutionMetrics | None = None,
        cognition_trace: CognitionPipelineTrace | None = None,
    ) -> SatoriReply:
        if interaction.assistant_message is None or interaction.provider_metadata is None:
            raise RuntimeError("completed interaction is missing its reply")
        metadata = interaction.provider_metadata
        usage = None
        if metadata.input_tokens is not None or metadata.output_tokens is not None:
            usage = ConversationUsage(
                input_tokens=metadata.input_tokens,
                output_tokens=metadata.output_tokens,
            )
        return SatoriReply(
            text=interaction.assistant_message.content,
            provider=metadata.provider,
            model=metadata.model,
            finish_status=metadata.finish_status,
            usage=usage,
            context_manifest=context_manifest
            or ConversationContextManifest(
                schema_version=metadata.context_manifest_schema_version,
                policy_id=metadata.policy_id,
                policy_schema_version=metadata.policy_schema_version,
                character_context_schema_version=metadata.context_schema_version,
                included_sections=tuple(
                    section
                    for section in CONVERSATION_INCLUDED_SECTIONS
                    if not (
                        (
                            section == "retrieved_episodic_memory"
                            and metadata.retrieval_status == "not_requested"
                        )
                        or (
                            section == "relationship_expression_state"
                            and metadata.relationship_state_version is None
                        )
                        or (
                            section == "current_user_world_models"
                            and metadata.model_context_status != "available"
                        )
                        or (
                            section == "satori_epistemic_positions"
                            and metadata.position_context_status != "available"
                        )
                        or (
                            section == "satori_inclinations"
                            and metadata.inclination_context_status != "available"
                        )
                        or (
                            section == "retrieved_semantic_memory"
                            and metadata.semantic_retrieval_status == "not_requested"
                        )
                        or (
                            section == "emotional_expression_state"
                            and metadata.emotion_appraisal_status == "not_requested"
                        )
                        or section
                        in {
                            "recent_conversation",
                            "dialogue_coherence",
                            "self_consistency_facets",
                            "cognition_response_strategy",
                        }
                    )
                ),
                user_content_chars=len(interaction.user_message.content),
                available_past_evidence_ids=(
                    *metadata.retrieved_memory_ids,
                    *metadata.retrieved_semantic_claim_ids,
                    *metadata.user_model_context_claim_ids,
                    *metadata.world_model_context_claim_ids,
                    *metadata.position_context_ids,
                    *metadata.inclination_context_ids,
                ),
                retrieval_status=metadata.retrieval_status,
                retrieved_memory_ids=metadata.retrieved_memory_ids,
                semantic_retrieval_status=metadata.semantic_retrieval_status,
                retrieved_semantic_claim_ids=metadata.retrieved_semantic_claim_ids,
                model_context_status=metadata.model_context_status,
                user_model_context_schema_version=metadata.user_model_context_schema_version,
                user_model_context_claim_ids=metadata.user_model_context_claim_ids,
                world_model_context_schema_version=metadata.world_model_context_schema_version,
                world_model_context_claim_ids=metadata.world_model_context_claim_ids,
                position_context_status=metadata.position_context_status,
                position_context_schema_version=metadata.position_context_schema_version,
                position_context_ids=metadata.position_context_ids,
                inclination_context_status=metadata.inclination_context_status,
                inclination_context_schema_version=(metadata.inclination_context_schema_version),
                inclination_context_ids=metadata.inclination_context_ids,
                inclination_curiosity_influence=(metadata.inclination_curiosity_influence),
                personality_aggregate_version=metadata.personality_aggregate_version,
                personality_expression_schema_version=(
                    metadata.personality_expression_schema_version
                ),
                personality_expression_cues=metadata.personality_expression_cues,
                emotion_appraisal_status=metadata.emotion_appraisal_status,
                emotion_context_schema_version=metadata.emotion_context_schema_version,
                emotion_state_version=metadata.emotion_state_version,
                mood_state_version=metadata.mood_state_version,
                emotion_state_as_of=metadata.emotion_state_as_of,
                relationship_context_schema_version=(metadata.relationship_context_schema_version),
                relationship_state_version=metadata.relationship_state_version,
            ),
            session_id=interaction.session_id,
            interaction_id=interaction.interaction_id,
            client_request_id=interaction.client_request_id,
            replayed=replayed,
            timings=timings or TurnPhaseTimings(),
            provider_metrics=provider_metrics,
            appraisal_provider_metrics=appraisal_provider_metrics,
            retrieval_provider_metrics=retrieval_provider_metrics,
            cognition_trace=cognition_trace,
        )

    def _log_invalid_response(
        self,
        error: InvalidProviderResponse,
        latency_ms: float,
        context_schema_version: int,
    ) -> None:
        self.logger.warning(
            "conversation_failed",
            extra=_log_fields(
                operation="conversation",
                provider=error.provider,
                model=error.model,
                error_type=type(error).__name__,
                latency_ms=round(latency_ms, 3),
                context_schema_version=context_schema_version,
            ),
        )
