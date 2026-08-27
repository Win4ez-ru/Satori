"""Retryable derived-state processing after canonical reply delivery eligibility."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from satori.application.conversation.history import InteractionLog
from satori.application.memory.use_cases import FormEpisodeForInteraction
from satori.application.models.use_cases import FormCurrentModels
from satori.application.positions.use_cases import FormSatoriPositions
from satori.application.reflection.use_cases import (
    ApplyReflectionProposals,
    ProcessReflection,
)
from satori.application.relationship.use_cases import ProcessRelationshipForInteraction
from satori.application.retrieval.use_cases import IndexEpisodicMemory
from satori.application.semantic.use_cases import FormSemanticMemory
from satori.core.reflection import ReflectionPurpose
from satori.domain.conversation_history import InteractionStatus
from satori.domain.reflection import ReflectionRunStatus, ReflectionTriggerKind


def _log_fields(**fields: object) -> dict[str, object]:
    return {"satori_fields": fields}


@dataclass(frozen=True, slots=True)
class PostResponseReport:
    """Metadata-only result for one idempotent downstream processing attempt."""

    interaction_id: str
    episode_formation_ms: float
    episode_embedding_ms: float
    semantic_consolidation_ms: float
    total_ms: float
    relationship_appraisal_ms: float = 0.0
    relationship_commit_ms: float = 0.0
    relationship_total_ms: float = 0.0
    model_formation_ms: float = 0.0
    position_formation_ms: float = 0.0
    reflection_processing_ms: float = 0.0
    personality_reflection_processing_ms: float = 0.0
    failure_phases: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return not self.failure_phases


@dataclass(slots=True)
class ProcessPostResponse:
    """Run derived owners and rare reflection without invalidating a committed reply."""

    interaction_log: InteractionLog
    form_episode: FormEpisodeForInteraction
    index_memory: IndexEpisodicMemory | None = None
    form_semantic: FormSemanticMemory | None = None
    process_relationship: ProcessRelationshipForInteraction | None = None
    form_models: FormCurrentModels | None = None
    form_positions: FormSatoriPositions | None = None
    process_reflection: ProcessReflection | None = None
    apply_reflection: ApplyReflectionProposals | None = None
    identity_id_provider: Callable[[], str] | None = None
    monotonic: Callable[[], float] = time.perf_counter
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("satori.post_response")
    )

    async def execute(self, interaction_id: str, *, trace_id: str) -> PostResponseReport:
        """Return failures as retryable phase metadata; never invalidate the committed reply."""

        total_started = self.monotonic()
        episode_ms = embedding_ms = semantic_ms = 0.0
        failures: list[str] = []
        interaction = self.interaction_log.get(interaction_id)
        if interaction is None or interaction.status is not InteractionStatus.COMPLETED:
            raise ValueError("post-response processing requires a completed interaction")

        relationship_appraisal_ms = relationship_commit_ms = relationship_total_ms = 0.0
        if self.process_relationship is not None and interaction.relationship_processing_required:
            try:
                relationship_report = await self.process_relationship.execute(
                    interaction_id, trace_id=trace_id
                )
                relationship_appraisal_ms = relationship_report.relationship_appraisal_ms
                relationship_commit_ms = relationship_report.relationship_commit_ms
                relationship_total_ms = relationship_report.total_ms
            except Exception as error:
                failures.append("relationship_processing")
                self.logger.warning(
                    "post_response_processing_degraded",
                    extra=_log_fields(
                        interaction_id=interaction_id,
                        phase="relationship_processing",
                        error_type=type(error).__name__,
                    ),
                )

        model_formation_ms = 0.0
        if self.form_models is not None and interaction.model_processing_required:
            model_started = self.monotonic()
            try:
                await self.form_models.execute(interaction_id, trace_id=trace_id)
            except Exception as error:
                failures.append("current_models")
                self.logger.warning(
                    "post_response_processing_degraded",
                    extra=_log_fields(
                        interaction_id=interaction_id,
                        phase="current_models",
                        error_type=type(error).__name__,
                    ),
                )
            model_formation_ms = (self.monotonic() - model_started) * 1000

        position_formation_ms = 0.0
        if self.form_positions is not None and interaction.position_processing_required:
            position_started = self.monotonic()
            try:
                await self.form_positions.execute(interaction_id, trace_id=trace_id)
            except Exception as error:
                failures.append("satori_positions")
                self.logger.warning(
                    "post_response_processing_degraded",
                    extra=_log_fields(
                        interaction_id=interaction_id,
                        phase="satori_positions",
                        error_type=type(error).__name__,
                    ),
                )
            position_formation_ms = (self.monotonic() - position_started) * 1000

        episode_started = self.monotonic()
        try:
            decision = await self.form_episode.execute(interaction, trace_id=trace_id)
        except Exception as error:
            episode_ms = (self.monotonic() - episode_started) * 1000
            failures.append("episode_formation")
            self.logger.warning(
                "post_response_processing_degraded",
                extra=_log_fields(
                    interaction_id=interaction_id,
                    phase="episode_formation",
                    error_type=type(error).__name__,
                ),
            )
            reflection_ms, personality_reflection_ms = await self._run_reflections(
                trace_id=trace_id,
                failures=failures,
            )
            return self._report(
                interaction_id,
                episode_ms,
                embedding_ms,
                semantic_ms,
                total_started,
                failures,
                relationship_appraisal_ms,
                relationship_commit_ms,
                relationship_total_ms,
                model_formation_ms,
                position_formation_ms,
                reflection_ms,
                personality_reflection_ms,
            )
        episode_ms = (self.monotonic() - episode_started) * 1000
        if decision.memory is not None and self.index_memory is not None:
            embedding_started = self.monotonic()
            try:
                await self.index_memory.execute(decision.memory, trace_id=trace_id)
            except Exception as error:
                failures.append("episode_embedding")
                self.logger.warning(
                    "post_response_processing_degraded",
                    extra=_log_fields(
                        interaction_id=interaction_id,
                        memory_id=decision.memory.memory_id,
                        phase="episode_embedding",
                        error_type=type(error).__name__,
                    ),
                )
            embedding_ms = (self.monotonic() - embedding_started) * 1000

        if decision.memory is not None and self.form_semantic is not None:
            semantic_started = self.monotonic()
            try:
                await self.form_semantic.execute(decision.memory.memory_id, trace_id=trace_id)
            except Exception as error:
                failures.append("semantic_consolidation")
                self.logger.warning(
                    "post_response_processing_degraded",
                    extra=_log_fields(
                        interaction_id=interaction_id,
                        memory_id=decision.memory.memory_id,
                        phase="semantic_consolidation",
                        error_type=type(error).__name__,
                    ),
                )
            semantic_ms = (self.monotonic() - semantic_started) * 1000

        reflection_ms, personality_reflection_ms = await self._run_reflections(
            trace_id=trace_id,
            failures=failures,
        )
        return self._report(
            interaction_id,
            episode_ms,
            embedding_ms,
            semantic_ms,
            total_started,
            failures,
            relationship_appraisal_ms,
            relationship_commit_ms,
            relationship_total_ms,
            model_formation_ms,
            position_formation_ms,
            reflection_ms,
            personality_reflection_ms,
        )

    async def _run_reflections(
        self,
        *,
        trace_id: str,
        failures: list[str],
    ) -> tuple[float, float]:
        general_ms = await self._run_reflection(
            purpose=ReflectionPurpose.GENERAL,
            trace_id=trace_id,
            failures=failures,
            failure_phase="reflection_processing",
            completed_event="reflection_processing_completed",
        )
        personality_ms = await self._run_reflection(
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
            trace_id=trace_id,
            failures=failures,
            failure_phase="personality_reflection_processing",
            completed_event="personality_reflection_processing_completed",
        )
        return general_ms, personality_ms

    async def _run_reflection(
        self,
        *,
        purpose: ReflectionPurpose,
        trace_id: str,
        failures: list[str],
        failure_phase: str,
        completed_event: str,
    ) -> float:
        if (
            self.process_reflection is None
            or self.apply_reflection is None
            or self.identity_id_provider is None
        ):
            return 0.0
        started = self.monotonic()
        try:
            report = await self.process_reflection.execute(
                self.identity_id_provider(),
                trigger=ReflectionTriggerKind.AUTOMATIC,
                trace_id=trace_id,
                purpose=purpose,
            )
            processed_run = report.run
            if processed_run is not None and processed_run.status.requires_routing:
                processed_run = self.apply_reflection.execute(
                    processed_run.run_id, trace_id=trace_id
                )
            run_status = processed_run.status if processed_run is not None else None
            self.logger.info(
                completed_event,
                extra=_log_fields(
                    purpose=purpose.value,
                    run_id=processed_run.run_id if processed_run is not None else None,
                    run_status=run_status.value if run_status is not None else None,
                    reason_code=report.reason_code,
                    created=report.created,
                    provider_called=report.provider_called,
                ),
            )
            if report.provider_called and run_status in {
                ReflectionRunStatus.RETRYABLE_FAILURE,
                ReflectionRunStatus.EXHAUSTED,
            }:
                failures.append(failure_phase)
        except Exception as error:
            failures.append(failure_phase)
            self.logger.warning(
                "post_response_processing_degraded",
                extra=_log_fields(
                    phase=failure_phase,
                    purpose=purpose.value,
                    error_type=type(error).__name__,
                ),
            )
        return (self.monotonic() - started) * 1000

    def _report(
        self,
        interaction_id: str,
        episode_ms: float,
        embedding_ms: float,
        semantic_ms: float,
        total_started: float,
        failures: list[str],
        relationship_appraisal_ms: float = 0.0,
        relationship_commit_ms: float = 0.0,
        relationship_total_ms: float = 0.0,
        model_formation_ms: float = 0.0,
        position_formation_ms: float = 0.0,
        reflection_processing_ms: float = 0.0,
        personality_reflection_processing_ms: float = 0.0,
    ) -> PostResponseReport:
        report = PostResponseReport(
            interaction_id=interaction_id,
            episode_formation_ms=episode_ms,
            episode_embedding_ms=embedding_ms,
            semantic_consolidation_ms=semantic_ms,
            total_ms=(self.monotonic() - total_started) * 1000,
            relationship_appraisal_ms=relationship_appraisal_ms,
            relationship_commit_ms=relationship_commit_ms,
            relationship_total_ms=relationship_total_ms,
            model_formation_ms=model_formation_ms,
            position_formation_ms=position_formation_ms,
            reflection_processing_ms=reflection_processing_ms,
            personality_reflection_processing_ms=personality_reflection_processing_ms,
            failure_phases=tuple(failures),
        )
        self.logger.info(
            "post_response_processing_completed",
            extra=_log_fields(
                interaction_id=interaction_id,
                episode_formation_latency_ms=round(report.episode_formation_ms, 3),
                episode_embedding_latency_ms=round(report.episode_embedding_ms, 3),
                semantic_consolidation_latency_ms=round(report.semantic_consolidation_ms, 3),
                relationship_appraisal_latency_ms=round(report.relationship_appraisal_ms, 3),
                relationship_commit_latency_ms=round(report.relationship_commit_ms, 3),
                relationship_total_latency_ms=round(report.relationship_total_ms, 3),
                model_formation_latency_ms=round(report.model_formation_ms, 3),
                position_formation_latency_ms=round(report.position_formation_ms, 3),
                reflection_processing_latency_ms=round(report.reflection_processing_ms, 3),
                personality_reflection_processing_latency_ms=round(
                    report.personality_reflection_processing_ms,
                    3,
                ),
                total_background_completion_latency_ms=round(report.total_ms, 3),
                failure_phases=list(report.failure_phases),
            ),
        )
        return report
