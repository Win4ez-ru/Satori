"""Affective initialization, appraisal, reads, and atomic conversation finalize."""

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from satori.application.affect.contracts import (
    EMOTIONAL_EXPRESSION_CONTEXT_SCHEMA_VERSION,
    EmotionalExpressionContext,
    EmotionAppraisalStatus,
    PreparedAffectiveContext,
)
from satori.application.affect.ports import (
    AffectiveConversationUnitOfWork,
    AffectiveStateUnitOfWork,
)
from satori.application.retrieval.contracts import RetrievedMemoryContext
from satori.application.semantic.contracts import RetrievedSemanticContext
from satori.core.affect import (
    AffectiveAppraisalProviderError,
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
    AppraisalEpisodicContext,
    AppraisalFastState,
    AppraisalMoodState,
    AppraisalSemanticContext,
    AppraisalTrait,
    AppraisalValue,
)
from satori.core.clock import Clock
from satori.core.ids import IdGenerator
from satori.core.ports.providers import StructuredGenerationPort
from satori.domain.affect import (
    AffectiveStateConflict,
    AffectiveStateSnapshot,
    AffectiveStatus,
    AffectiveTransition,
    AppraisalDecisionKind,
    EmotionManager,
    initial_affective_state,
    materialize_affective_state,
)
from satori.domain.conversation_history import (
    ConversationInteraction,
    HistoricalMessage,
    HistoricalMessageRole,
    InteractionProviderMetadata,
    InteractionStatus,
    SessionKind,
)
from satori.domain.initial_self import InitialSelfSnapshot

AFFECTIVE_APPRAISAL_REQUEST_SCHEMA_VERSION = 1
AffectiveStateUnitOfWorkFactory = Callable[[], AffectiveStateUnitOfWork]
AffectiveConversationUnitOfWorkFactory = Callable[[], AffectiveConversationUnitOfWork]
AffectiveAppraisalProvider = StructuredGenerationPort[
    AffectiveAppraisalRequest, AffectiveAppraisalProviderResponse
]


def _log_fields(**fields: object) -> dict[str, object]:
    return {"satori_fields": fields}


@dataclass(slots=True)
class EnsureAffectiveState:
    """Deterministically initialize neutral Stage 7 state for an activated identity."""

    unit_of_work_factory: AffectiveStateUnitOfWorkFactory

    def execute(self, identity_id: str, *, initialized_at: datetime) -> AffectiveStateSnapshot:
        """Return existing state or atomically create the neutral projection."""

        with self.unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.affective_state.get_state(identity_id)
            if existing is not None:
                return existing
            state = initial_affective_state(identity_id, initialized_at=initialized_at)
            if unit_of_work.affective_state.add_initial_state(state):
                unit_of_work.commit()
                return state
        with self.unit_of_work_factory() as unit_of_work:
            concurrent = unit_of_work.affective_state.get_state(identity_id)
            if concurrent is None:
                raise RuntimeError("affective state initialization disappeared")
            return concurrent


@dataclass(frozen=True, slots=True)
class GetAffectiveStatus:
    """Pure materialized status read after deterministic initialization."""

    ensure_state: EnsureAffectiveState
    unit_of_work_factory: AffectiveStateUnitOfWorkFactory
    clock: Clock

    def execute(self, identity_id: str) -> AffectiveStatus:
        now = self.clock.now()
        stored = self.ensure_state.execute(identity_id, initialized_at=now)
        with self.unit_of_work_factory() as unit_of_work:
            transitions = tuple(unit_of_work.affective_state.list_transitions(limit=1))
        current = materialize_affective_state(stored, at=now)
        return AffectiveStatus(
            state=current,
            last_transition_id=transitions[0].transition_id if transitions else None,
            last_transition_at=transitions[0].committed_at if transitions else None,
        )


@dataclass(frozen=True, slots=True)
class GetAffectiveHistory:
    """Read append-only metadata-rich transitions without raw conversation text."""

    unit_of_work_factory: AffectiveStateUnitOfWorkFactory

    def execute(self, *, limit: int | None = None) -> Sequence[AffectiveTransition]:
        with self.unit_of_work_factory() as unit_of_work:
            return tuple(unit_of_work.affective_state.list_transitions(limit=limit))


@dataclass(slots=True)
class PrepareAffectiveContext:
    """Appraise one current user event and prepare a tentative expression snapshot."""

    ensure_state: EnsureAffectiveState
    manager: EmotionManager
    provider: AffectiveAppraisalProvider | None
    clock: Clock
    monotonic: Callable[[], float] = time.perf_counter
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.affect"))

    async def execute(
        self,
        snapshot: InitialSelfSnapshot,
        interaction: ConversationInteraction,
        *,
        user_text: str,
        trace_id: str,
        memory_context: RetrievedMemoryContext | None,
        semantic_context: RetrievedSemanticContext | None,
    ) -> PreparedAffectiveContext:
        """Use only the current user event and already-selected bounded context."""

        materialization_started = self.monotonic()
        appraised_at = self.clock.now()
        stored = self.ensure_state.execute(
            snapshot.identity.identity_id,
            initialized_at=appraised_at,
        )
        materialized = materialize_affective_state(stored, at=appraised_at)
        materialization_ms = (self.monotonic() - materialization_started) * 1000
        if self.provider is None:
            self.logger.info(
                "emotion_appraisal_failed",
                extra=_log_fields(
                    interaction_id=interaction.interaction_id,
                    error_type="ProviderNotConfigured",
                ),
            )
            return self._without_transition(
                materialized,
                EmotionAppraisalStatus.UNAVAILABLE,
                "appraisal_provider_unavailable",
                materialization_latency_ms=materialization_ms,
            )

        request_build_started = self.monotonic()
        request = self._build_request(
            snapshot,
            interaction,
            user_text=user_text,
            trace_id=trace_id,
            appraised_at=appraised_at,
            materialized=materialized,
            memory_context=memory_context,
            semantic_context=semantic_context,
        )
        request_build_ms = (self.monotonic() - request_build_started) * 1000
        self.logger.info(
            "emotion_appraisal_attempted",
            extra=_log_fields(
                interaction_id=interaction.interaction_id,
                memory_count=len(request.episodic_context),
                semantic_claim_count=len(request.semantic_context),
            ),
        )
        started = self.monotonic()
        try:
            response = await self.provider.generate_structured(request)
        except AffectiveAppraisalProviderError as error:
            self.logger.warning(
                "emotion_appraisal_failed",
                extra=_log_fields(
                    interaction_id=interaction.interaction_id,
                    provider=error.provider,
                    model=error.model,
                    error_type=type(error).__name__,
                    reason_code=str(error),
                    latency_ms=round((self.monotonic() - started) * 1000, 3),
                ),
            )
            return self._without_transition(
                materialized,
                EmotionAppraisalStatus.UNAVAILABLE,
                "appraisal_provider_unavailable",
                materialization_latency_ms=materialization_ms,
                request_build_latency_ms=request_build_ms,
                appraisal_latency_ms=(self.monotonic() - started) * 1000,
            )
        except Exception as error:
            self.logger.warning(
                "emotion_appraisal_failed",
                extra=_log_fields(
                    interaction_id=interaction.interaction_id,
                    provider="unknown",
                    model="unknown",
                    error_type=type(error).__name__,
                    latency_ms=round((self.monotonic() - started) * 1000, 3),
                ),
            )
            return self._without_transition(
                materialized,
                EmotionAppraisalStatus.UNAVAILABLE,
                "appraisal_provider_contract_failure",
                materialization_latency_ms=materialization_ms,
                request_build_latency_ms=request_build_ms,
                appraisal_latency_ms=(self.monotonic() - started) * 1000,
            )

        allowed_refs = (
            interaction.interaction_id,
            *(memory_context.memory_ids if memory_context is not None else ()),
            *(semantic_context.claim_ids if semantic_context is not None else ()),
        )
        decision = self.manager.evaluate(
            response.proposal,
            stored,
            snapshot.personality,
            interaction_id=interaction.interaction_id,
            allowed_source_refs=allowed_refs,
            event_time=appraised_at,
        )
        status = {
            AppraisalDecisionKind.APPLIED: EmotionAppraisalStatus.APPLIED,
            AppraisalDecisionKind.SKIPPED: EmotionAppraisalStatus.SKIPPED,
            AppraisalDecisionKind.REJECTED: EmotionAppraisalStatus.REJECTED,
        }[decision.kind]
        expression_state = (
            decision.transition.after
            if decision.transition is not None
            else decision.materialized_state
        )
        appraisal_ms = (self.monotonic() - started) * 1000
        provider_fields = response.metrics.as_log_fields() if response.metrics else {}
        self.logger.info(
            "emotion_appraisal_succeeded",
            extra=_log_fields(
                interaction_id=interaction.interaction_id,
                provider=response.provider,
                model=response.model,
                appraisal_status=status.value,
                reason_code=decision.reason_code,
                source_ref_count=len(response.proposal.source_refs),
                latency_ms=round(appraisal_ms, 3),
                materialization_latency_ms=round(materialization_ms, 3),
                request_build_latency_ms=round(request_build_ms, 3),
                **provider_fields,
            ),
        )
        return PreparedAffectiveContext(
            expression=self._expression(expression_state, status),
            materialized_pre_event=decision.materialized_state,
            transition=decision.transition,
            appraisal_status=status,
            reason_code=decision.reason_code,
            provider=response.provider,
            model=response.model,
            appraisal_method=response.appraisal_method,
            materialization_latency_ms=materialization_ms,
            request_build_latency_ms=request_build_ms,
            appraisal_latency_ms=appraisal_ms,
            provider_metrics=response.metrics,
        )

    @staticmethod
    def _build_request(
        snapshot: InitialSelfSnapshot,
        interaction: ConversationInteraction,
        *,
        user_text: str,
        trace_id: str,
        appraised_at: datetime,
        materialized: AffectiveStateSnapshot,
        memory_context: RetrievedMemoryContext | None,
        semantic_context: RetrievedSemanticContext | None,
    ) -> AffectiveAppraisalRequest:
        return AffectiveAppraisalRequest(
            schema_version=AFFECTIVE_APPRAISAL_REQUEST_SCHEMA_VERSION,
            trace_id=trace_id,
            interaction_id=interaction.interaction_id,
            appraised_at=appraised_at,
            user_content=user_text,
            traits=tuple(
                AppraisalTrait(item.key, item.value) for item in snapshot.personality.traits
            ),
            values=tuple(
                AppraisalValue(item.key, item.strength, item.description)
                for item in snapshot.values.items
            ),
            fast_state=AppraisalFastState(**materialized.fast.as_mapping()),
            mood_state=AppraisalMoodState(**materialized.mood.as_mapping()),
            episodic_context=tuple(
                AppraisalEpisodicContext(
                    memory.memory_id,
                    memory.summary,
                    memory.importance,
                    memory.confidence,
                )
                for memory in (memory_context.memories if memory_context is not None else ())
            ),
            semantic_context=tuple(
                AppraisalSemanticContext(
                    claim.claim_id,
                    claim.predicate,
                    json.dumps(
                        claim.value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    claim.claim_kind,
                    claim.confidence,
                )
                for claim in (semantic_context.claims if semantic_context is not None else ())
            ),
        )

    @classmethod
    def _without_transition(
        cls,
        state: AffectiveStateSnapshot,
        status: EmotionAppraisalStatus,
        reason_code: str,
        *,
        materialization_latency_ms: float,
        request_build_latency_ms: float = 0.0,
        appraisal_latency_ms: float = 0.0,
    ) -> PreparedAffectiveContext:
        return PreparedAffectiveContext(
            expression=cls._expression(state, status),
            materialized_pre_event=state,
            transition=None,
            appraisal_status=status,
            reason_code=reason_code,
            materialization_latency_ms=materialization_latency_ms,
            request_build_latency_ms=request_build_latency_ms,
            appraisal_latency_ms=appraisal_latency_ms,
        )

    @staticmethod
    def _expression(
        state: AffectiveStateSnapshot,
        status: EmotionAppraisalStatus,
    ) -> EmotionalExpressionContext:
        return EmotionalExpressionContext(
            schema_version=EMOTIONAL_EXPRESSION_CONTEXT_SCHEMA_VERSION,
            state_version=state.state_version,
            mood_version=state.mood_version,
            as_of=state.as_of,
            fast=state.fast,
            mood=state.mood,
            appraisal_status=status,
        )


@dataclass(slots=True)
class FinalizeAffectiveInteraction:
    """Commit canonical reply and accepted affective transition in one transaction."""

    unit_of_work_factory: AffectiveConversationUnitOfWorkFactory
    clock: Clock
    id_generator: IdGenerator
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.affect"))

    def execute(
        self,
        interaction: ConversationInteraction,
        *,
        assistant_text: str,
        provider_metadata: InteractionProviderMetadata,
        prepared: PreparedAffectiveContext,
    ) -> ConversationInteraction:
        completed_at = self.clock.now()
        assistant_message = HistoricalMessage(
            message_id=self.id_generator.new(),
            session_id=interaction.session_id,
            interaction_id=interaction.interaction_id,
            schema_version=1,
            role=HistoricalMessageRole.ASSISTANT,
            content=assistant_text,
            created_at=completed_at,
            sequence=2,
        )
        transition = None
        if prepared.transition is not None:
            if (
                prepared.provider is None
                or prepared.model is None
                or prepared.appraisal_method is None
            ):
                raise RuntimeError("applied affective transition is missing provider metadata")
            draft = prepared.transition
            transition = AffectiveTransition(
                transition_id=self.id_generator.new(),
                identity_id=draft.before.identity_id,
                interaction_id=interaction.interaction_id,
                source_message_id=interaction.user_message.message_id,
                trace_id=interaction.trace_id,
                proposal=draft.proposal,
                before=draft.before,
                after=draft.after,
                applied_delta=draft.applied_delta,
                mood_delta=draft.mood_delta,
                provider=prepared.provider,
                model=prepared.model,
                appraisal_method=prepared.appraisal_method,
                committed_at=completed_at,
            )

        try:
            with self.unit_of_work_factory() as unit_of_work:
                current = unit_of_work.conversation_history.get_interaction(
                    interaction.interaction_id
                )
                if current is None:
                    raise RuntimeError("interaction is missing during affective finalize")
                if current.status is InteractionStatus.COMPLETED:
                    if transition is not None:
                        self.logger.info(
                            "emotion_transition_replayed",
                            extra=_log_fields(interaction_id=interaction.interaction_id),
                        )
                    return current
                session = unit_of_work.conversation_history.get_session(interaction.session_id)
                if session is None:
                    raise RuntimeError("interaction session is missing during finalize")
                if transition is not None:
                    recorded = unit_of_work.affective_state.apply_transition(
                        transition,
                        audit_event_id=self.id_generator.new(),
                    )
                    if not recorded:
                        raise RuntimeError(
                            "affective transition exists while interaction is incomplete"
                        )
                completed = unit_of_work.conversation_history.complete_interaction(
                    interaction.interaction_id,
                    assistant_message=assistant_message,
                    completed_at=completed_at,
                    provider_metadata=provider_metadata,
                    close_session=session.kind is SessionKind.IMPLICIT,
                )
                unit_of_work.commit()
        except AffectiveStateConflict:
            self.logger.warning(
                "emotion_transition_conflict",
                extra=_log_fields(
                    interaction_id=interaction.interaction_id,
                    expected_state_version=(
                        transition.before.state_version if transition is not None else None
                    ),
                ),
            )
            raise

        if transition is not None:
            self.logger.info(
                "emotion_transition_applied",
                extra=_log_fields(
                    transition_id=transition.transition_id,
                    interaction_id=interaction.interaction_id,
                    base_state_version=transition.before.state_version,
                    resulting_state_version=transition.after.state_version,
                    emotion_policy_version=transition.after.emotion_policy_version,
                    appraisal_schema_version=transition.after.appraisal_schema_version,
                ),
            )
            self.logger.info(
                "mood_updated",
                extra=_log_fields(
                    transition_id=transition.transition_id,
                    interaction_id=interaction.interaction_id,
                    mood_version=transition.after.mood_version,
                    mood_policy_version=transition.after.mood_policy_version,
                ),
            )
        return completed
