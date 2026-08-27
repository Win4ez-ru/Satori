"""Relationship initialization, post-response processing, and immutable reads."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from satori.application.relationship.contracts import (
    RelationshipExpressionContext,
    RelationshipHistory,
    RelationshipProcessingReport,
    RelationshipStatus,
)
from satori.application.relationship.ports import RelationshipUnitOfWork
from satori.core.clock import Clock
from satori.core.ids import IdGenerator
from satori.core.ports.providers import StructuredGenerationPort
from satori.core.relationship import (
    RelationshipAppraisalProviderError,
    RelationshipAppraisalRequest,
    RelationshipAppraisalResponse,
)
from satori.domain.relationship import (
    RELATIONSHIP_APPRAISAL_SCHEMA_VERSION,
    RELATIONSHIP_POLICY_VERSION,
    RelationshipDecision,
    RelationshipManager,
    RelationshipState,
    RelationshipTransition,
    initial_relationship,
)

RelationshipProvider = StructuredGenerationPort[
    RelationshipAppraisalRequest, RelationshipAppraisalResponse
]
RelationshipUnitOfWorkFactory = Callable[[], RelationshipUnitOfWork]


def _level(value: float) -> str:
    if value < 0.20:
        return "low"
    if value < 0.45:
        return "emerging"
    if value < 0.70:
        return "moderate"
    if value < 0.88:
        return "high"
    return "very_high"


def _centered_level(value: float, maturity: float) -> str:
    """Render neutral baselines as uncertainty, never as earned positive evidence."""

    if maturity < 0.25 or 0.45 <= value <= 0.55:
        return "uncertain"
    if value < 0.20:
        return "very_low"
    if value < 0.45:
        return "low"
    if value < 0.70:
        return "moderate"
    if value < 0.88:
        return "high"
    return "very_high"


def expression_for(state: RelationshipState) -> RelationshipExpressionContext:
    """Project evidence maturity separately so neutral midpoints do not claim certainty."""

    maturity = state.maturity
    vector = state.vector
    return RelationshipExpressionContext(
        schema_version=1,
        state_version=state.state_version,
        maturity=("low" if maturity < 0.25 else "developing" if maturity < 0.6 else "established"),
        familiarity=_level(vector.familiarity),
        trust=_centered_level(vector.trust, maturity),
        comfort=_centered_level(vector.comfort, maturity),
        closeness=_level(vector.closeness),
        intellectual_respect=_centered_level(vector.intellectual_respect, maturity),
        affection=_level(vector.affection),
    )


@dataclass(slots=True)
class EnsureRelationship:
    unit_of_work_factory: RelationshipUnitOfWorkFactory
    clock: Clock
    id_generator: IdGenerator

    def execute(self, identity_id: str, counterparty_id: str) -> RelationshipState:
        with self.unit_of_work_factory() as unit_of_work:
            stored = unit_of_work.relationship.get_state(identity_id, counterparty_id)
            if stored is not None:
                return stored
            initial = initial_relationship(
                self.id_generator.new(),
                identity_id,
                counterparty_id,
                initialized_at=self.clock.now(),
            )
            if unit_of_work.relationship.add_initial_state(initial):
                unit_of_work.commit()
                return initial
        with self.unit_of_work_factory() as unit_of_work:
            stored = unit_of_work.relationship.get_state(identity_id, counterparty_id)
            if stored is None:
                raise RuntimeError("relationship initialization lost a concurrent insert")
            return stored


@dataclass(frozen=True, slots=True)
class GetRelationshipForSession:
    unit_of_work_factory: RelationshipUnitOfWorkFactory
    ensure: EnsureRelationship

    def execute(self, session_id: str) -> RelationshipExpressionContext:
        with self.unit_of_work_factory() as unit_of_work:
            target = unit_of_work.relationship.get_counterparty_for_session(session_id)
        if target is None:
            raise ValueError("relationship session does not exist")
        identity_id, counterparty_id = target
        return expression_for(self.ensure.execute(identity_id, counterparty_id))


@dataclass(slots=True)
class ProcessRelationshipForInteraction:
    """Classify canonical user evidence then let the deterministic owner commit it."""

    unit_of_work_factory: RelationshipUnitOfWorkFactory
    ensure: EnsureRelationship
    provider: RelationshipProvider
    manager: RelationshipManager
    clock: Clock
    id_generator: IdGenerator
    monotonic: Callable[[], float] = time.perf_counter
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.relationship"))

    async def execute(self, interaction_id: str, *, trace_id: str) -> RelationshipProcessingReport:
        total_started = self.monotonic()
        with self.unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.relationship.get_decision(interaction_id)
            source = unit_of_work.relationship.get_source(interaction_id)
            if existing is not None:
                return self._replay(existing, total_started)
            if source is None or not source.processing_required:
                raise ValueError("interaction is not eligible for Stage 8 relationship processing")
            if unit_of_work.relationship.has_earlier_undecided_source(source):
                raise ValueError("an earlier relationship source must be processed first")
        self.ensure.execute(source.identity_id, source.counterparty_id)

        appraisal_started = self.monotonic()
        self.logger.info(
            "relationship_appraisal_attempted",
            extra={"satori_fields": {"interaction_id": interaction_id}},
        )
        try:
            response = await self.provider.generate_structured(
                RelationshipAppraisalRequest(
                    schema_version=RELATIONSHIP_APPRAISAL_SCHEMA_VERSION,
                    interaction_id=source.interaction_id,
                    user_message_id=source.user_message_id,
                    user_content=source.user_content,
                    observed_at=source.completed_at,
                    trace_id=trace_id,
                )
            )
        except Exception as error:
            self.logger.warning(
                "relationship_appraisal_failed",
                extra={
                    "satori_fields": {
                        "interaction_id": interaction_id,
                        "error_type": type(error).__name__,
                    }
                },
            )
            raise
        appraisal_ms = (self.monotonic() - appraisal_started) * 1000
        required_refs = {source.interaction_id, source.user_message_id}
        if set(response.proposal.source_refs) != required_refs:
            self.logger.warning(
                "relationship_appraisal_failed",
                extra={
                    "satori_fields": {
                        "interaction_id": interaction_id,
                        "error_type": "invalid_source_refs",
                    }
                },
            )
            raise RelationshipAppraisalProviderError(
                response.provider,
                response.model,
                "relationship appraisal returned unknown or incomplete source refs",
            )
        self.logger.info(
            "relationship_appraisal_succeeded",
            extra={
                "satori_fields": {
                    "interaction_id": interaction_id,
                    "category_count": len(response.proposal.categories),
                    "relationship_appraisal_latency_ms": round(appraisal_ms, 3),
                }
            },
        )

        commit_started = self.monotonic()
        with self.unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.relationship
            existing = repository.get_decision(interaction_id)
            if existing is not None:
                return self._replay(existing, total_started, appraisal_ms=appraisal_ms)
            before = repository.get_state(source.identity_id, source.counterparty_id)
            if before is None:
                raise RuntimeError("relationship state disappeared before decision")
            mutation = self.manager.apply(
                before,
                response.proposal,
                session_id=source.session_id,
                session_delta=repository.session_delta(before.relationship_id, source.session_id),
                session_is_new_evidence=not repository.session_has_qualified_evidence(
                    before.relationship_id, source.session_id
                ),
                observed_at=self.clock.now(),
            )
            transition_id = self.id_generator.new() if mutation.delta is not None else None
            transition = (
                RelationshipTransition(
                    transition_id=transition_id,
                    relationship_id=before.relationship_id,
                    interaction_id=source.interaction_id,
                    source_user_message_id=source.user_message_id,
                    session_id=source.session_id,
                    trace_id=trace_id,
                    categories=mutation.categories,
                    confidence=response.proposal.confidence,
                    before=before,
                    delta=mutation.delta,
                    after=mutation.state_after_processing,
                    provider=response.provider,
                    model=response.model,
                    appraisal_method=response.appraisal_method,
                    appraisal_schema_version=response.proposal.schema_version,
                    policy_version=RELATIONSHIP_POLICY_VERSION,
                    committed_at=self.clock.now(),
                )
                if mutation.delta is not None and transition_id is not None
                else None
            )
            decision = RelationshipDecision(
                decision_id=self.id_generator.new(),
                relationship_id=before.relationship_id,
                interaction_id=source.interaction_id,
                source_user_message_id=source.user_message_id,
                session_id=source.session_id,
                trace_id=trace_id,
                kind=mutation.kind,
                reason_code=mutation.reason_code,
                categories=mutation.categories,
                confidence=response.proposal.confidence,
                provider=response.provider,
                model=response.model,
                appraisal_method=response.appraisal_method,
                appraisal_schema_version=response.proposal.schema_version,
                policy_version=RELATIONSHIP_POLICY_VERSION,
                decided_at=self.clock.now(),
                transition_id=transition_id,
            )
            if not repository.record(
                decision=decision,
                before=before,
                after=mutation.state_after_processing,
                transition=transition,
                audit_event_id=self.id_generator.new(),
            ):
                replay = repository.get_decision(interaction_id)
                if replay is None:
                    self.logger.warning(
                        "relationship_transition_conflict",
                        extra={"satori_fields": {"interaction_id": interaction_id}},
                    )
                    raise RuntimeError("relationship optimistic write conflicted")
                return self._replay(replay, total_started, appraisal_ms=appraisal_ms)
            unit_of_work.commit()
        commit_ms = (self.monotonic() - commit_started) * 1000
        metrics = response.metrics.as_log_fields() if response.metrics is not None else None
        report = RelationshipProcessingReport(
            interaction_id=interaction_id,
            decision_kind=decision.kind.value,
            reason_code=decision.reason_code,
            relationship_appraisal_ms=appraisal_ms,
            relationship_commit_ms=commit_ms,
            total_ms=(self.monotonic() - total_started) * 1000,
            provider_metrics=metrics,
        )
        self.logger.info(
            (
                "relationship_transition_applied"
                if decision.transition_id is not None
                else "relationship_transition_skipped"
            ),
            extra={
                "satori_fields": {
                    "interaction_id": interaction_id,
                    "decision": decision.kind.value,
                    "reason_code": decision.reason_code,
                    "relationship_appraisal_latency_ms": round(appraisal_ms, 3),
                    "relationship_commit_latency_ms": round(commit_ms, 3),
                    "relationship_total_latency_ms": round(report.total_ms, 3),
                    **(metrics or {}),
                }
            },
        )
        return report

    def _replay(
        self,
        decision: RelationshipDecision,
        total_started: float,
        *,
        appraisal_ms: float = 0.0,
    ) -> RelationshipProcessingReport:
        self.logger.info(
            "relationship_transition_replayed",
            extra={
                "satori_fields": {
                    "interaction_id": decision.interaction_id,
                    "decision": decision.kind.value,
                    "reason_code": decision.reason_code,
                }
            },
        )
        return RelationshipProcessingReport(
            interaction_id=decision.interaction_id,
            decision_kind=decision.kind.value,
            reason_code=decision.reason_code,
            relationship_appraisal_ms=appraisal_ms,
            relationship_commit_ms=0.0,
            total_ms=(self.monotonic() - total_started) * 1000,
            replayed=True,
        )


@dataclass(frozen=True, slots=True)
class GetRelationshipStatus:
    unit_of_work_factory: RelationshipUnitOfWorkFactory
    ensure: EnsureRelationship

    def execute(self, identity_id: str, counterparty_id: str) -> RelationshipStatus:
        state = self.ensure.execute(identity_id, counterparty_id)
        with self.unit_of_work_factory() as unit_of_work:
            transitions = unit_of_work.relationship.list_transitions(state.relationship_id, limit=1)
        latest = transitions[0] if transitions else None
        return RelationshipStatus(
            state=state,
            last_transition_id=latest.transition_id if latest else None,
            last_transition_at=latest.committed_at if latest else None,
        )


@dataclass(frozen=True, slots=True)
class GetRelationshipHistory:
    unit_of_work_factory: RelationshipUnitOfWorkFactory
    ensure: EnsureRelationship

    def execute(self, identity_id: str, counterparty_id: str, *, limit: int) -> RelationshipHistory:
        if limit < 1:
            raise ValueError("relationship history limit must be positive")
        state = self.ensure.execute(identity_id, counterparty_id)
        with self.unit_of_work_factory() as unit_of_work:
            items = unit_of_work.relationship.list_transitions(state.relationship_id, limit=limit)
        return RelationshipHistory(tuple(items))
