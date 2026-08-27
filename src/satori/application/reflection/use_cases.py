"""Deterministic Stage 12-14 reflection triggering, generation and owner routing."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import cast

from satori.application.positions.ports import PositionsUnitOfWork
from satori.application.reflection.ports import (
    PersonalityReflectionContextPort,
    PersonalityReflectionRouter,
    ReflectionGenerationPort,
    ReflectionUnitOfWork,
)
from satori.core.clock import Clock
from satori.core.ids import IdGenerator
from satori.core.inclinations import (
    InclinationEvidenceSource,
    InclinationKind,
    InclinationProposal,
    InclinationStateReference,
)
from satori.core.personality import (
    PersonalityChangeProposal,
    PersonalityCitation,
    PersonalityCitationRole,
    PersonalityDirection,
    PersonalityTraitKey,
)
from satori.core.positions import (
    PositionEvidenceCitation,
    PositionEvidenceRole,
    PositionKind,
    PositionProposal,
    PositionSourceMessage,
    PositionStance,
    PositionStateReference,
    PositionValueReference,
)
from satori.core.reflection import (
    ReflectionGenerationRequest,
    ReflectionProviderError,
    ReflectionPurpose,
    ReflectionSource,
    ReflectionTargetOwner,
)
from satori.domain.inclinations import (
    INCLINATION_POLICY_VERSION,
    InclinationDecisionKind,
)
from satori.domain.personality_evolution import (
    MIN_CLUSTERS,
    MIN_LINEAGES,
    MIN_MONTH_BUCKETS,
    MIN_OBSERVATION_SPAN,
    MIN_ROOTS,
    MIN_SESSIONS,
    MIN_WEEK_BUCKETS,
    PERSONALITY_EVIDENCE_RESERVOIR_LIMIT,
    PersonalityEvidenceSource,
    PersonalityManager,
    personality_diversity,
)
from satori.domain.positions import (
    POSITION_POLICY_VERSION,
    PositionEvaluationOrigin,
    PositionManager,
)
from satori.domain.reflection import (
    REFLECTION_MAX_ATTEMPTS,
    REFLECTION_MAX_PROPOSALS,
    REFLECTION_MAX_SOURCE_CHARACTERS,
    REFLECTION_MAX_SOURCES,
    REFLECTION_MAX_TARGET_INCLINATIONS,
    REFLECTION_MAX_TARGET_POSITIONS,
    REFLECTION_POLICY_VERSION,
    REFLECTION_POLICY_VERSION_V3,
    REFLECTION_SCHEMA_VERSION,
    REFLECTION_SCHEMA_VERSION_V3,
    ReflectionAttempt,
    ReflectionAttemptStatus,
    ReflectionOutcome,
    ReflectionOutcomeDecision,
    ReflectionProposal,
    ReflectionRun,
    ReflectionRunStatus,
    ReflectionSourceRecord,
    ReflectionTriggerKind,
    candidate_evidence_source_ids,
    complete_reflection_run,
    proposal_payload,
    reflection_outcome_id,
    reflection_proposal_id,
    reflection_run_id,
    reflection_run_key,
    reflection_source_id,
    reflection_trigger_reason,
    source_set_hash,
    validate_candidate_sources,
)

ReflectionUnitOfWorkFactory = Callable[[], ReflectionUnitOfWork]
PositionsUnitOfWorkFactory = Callable[[], PositionsUnitOfWork]

_PERSONALITY_RUN_COOLDOWN = timedelta(days=30)
_ROLLING_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class ReflectionProcessReport:
    run: ReflectionRun | None
    reason_code: str
    created: bool = False
    provider_called: bool = False


@dataclass(frozen=True, slots=True)
class ReflectionInspection:
    run: ReflectionRun
    sources: tuple[ReflectionSourceRecord | ReflectionSource, ...]
    attempts: tuple[ReflectionAttempt, ...]
    proposals: tuple[ReflectionProposal, ...]
    outcomes: tuple[ReflectionOutcome, ...]


@dataclass(slots=True)
class GetReflections:
    reflection_uow_factory: ReflectionUnitOfWorkFactory

    def list(
        self,
        *,
        identity_id: str,
        limit: int = 50,
        purpose: ReflectionPurpose | None = None,
    ) -> tuple[ReflectionRun, ...]:
        with self.reflection_uow_factory() as unit:
            return unit.reflection.list_runs(
                identity_id=identity_id,
                limit=limit,
                purpose=purpose,
            )

    def inspect(
        self,
        run_id: str,
        *,
        identity_id: str,
        show_sources: bool = False,
    ) -> ReflectionInspection | None:
        with self.reflection_uow_factory() as unit:
            run = unit.reflection.get_run(run_id)
            if run is None or run.identity_id != identity_id:
                return None
            sources: tuple[ReflectionSourceRecord | ReflectionSource, ...]
            if show_sources:
                sources = unit.reflection.load_generation_sources(run_id)
            else:
                sources = unit.reflection.list_sources(run_id)
            return ReflectionInspection(
                run=run,
                sources=sources,
                attempts=unit.reflection.list_attempts(run_id),
                proposals=unit.reflection.list_proposals(run_id),
                outcomes=unit.reflection.list_outcomes(run_id),
            )


@dataclass(slots=True)
class ProcessReflection:
    reflection_uow_factory: ReflectionUnitOfWorkFactory
    positions_uow_factory: PositionsUnitOfWorkFactory
    provider: ReflectionGenerationPort
    clock: Clock
    id_generator: IdGenerator
    personality_context: PersonalityReflectionContextPort | None = None
    personality_manager: PersonalityManager = field(default_factory=PersonalityManager)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.reflection"))

    async def execute(
        self,
        identity_id: str,
        *,
        trigger: ReflectionTriggerKind,
        trace_id: str,
        purpose: ReflectionPurpose = ReflectionPurpose.GENERAL,
    ) -> ReflectionProcessReport:
        now = self.clock.now()
        with self.reflection_uow_factory() as unit:
            runs = unit.reflection.list_runs(
                identity_id=identity_id,
                limit=32,
                purpose=purpose,
            )
            resumable = next(
                (
                    item
                    for item in runs
                    if item.status
                    in {
                        ReflectionRunStatus.PENDING_GENERATION,
                        ReflectionRunStatus.RETRYABLE_FAILURE,
                    }
                ),
                None,
            )
            blocked = next(
                (
                    item
                    for item in runs
                    if item.status
                    in {ReflectionRunStatus.PROPOSALS_READY, ReflectionRunStatus.APPLYING}
                ),
                None,
            )
            if blocked is not None:
                return ReflectionProcessReport(blocked, "nonterminal_run_requires_routing")
            if resumable is not None:
                if trigger is ReflectionTriggerKind.AUTOMATIC:
                    return ReflectionProcessReport(resumable, "automatic_retry_not_allowed")
                run = resumable
                created = False
            else:
                source_limit = (
                    PERSONALITY_EVIDENCE_RESERVOIR_LIMIT
                    if purpose is ReflectionPurpose.PERSONALITY_EVOLUTION
                    else REFLECTION_MAX_SOURCES
                )
                candidates = unit.reflection.list_eligible_sources(
                    identity_id=identity_id,
                    limit=source_limit,
                    purpose=purpose,
                )
                if purpose is ReflectionPurpose.PERSONALITY_EVOLUTION:
                    if self.personality_context is None:
                        return ReflectionProcessReport(None, "personality_context_unavailable")
                    if self.personality_context.get_state_reference(identity_id) is None:
                        return ReflectionProcessReport(None, "personality_state_unavailable")
                    used_roots = self.personality_context.list_used_root_message_ids(identity_id)
                    reservoir = self._personality_sources(candidates, identity_id=identity_id)
                    owner_selected = self.personality_manager.select_evidence(
                        reservoir,
                        identity_id=identity_id,
                        used_root_message_ids=used_roots,
                        now=now,
                    )
                    candidate_by_id = {item.source_id: item for item in candidates}
                    selected = tuple(candidate_by_id[item.source_id] for item in owner_selected)
                else:
                    selected = self._bounded_sources(candidates)
                reason = self._eligibility_reason(
                    selected,
                    runs,
                    trigger=trigger,
                    now=now,
                    purpose=purpose,
                    identity_id=identity_id,
                )
                if reason is not None:
                    return ReflectionProcessReport(None, reason)
                schema_version = (
                    REFLECTION_SCHEMA_VERSION_V3
                    if purpose is ReflectionPurpose.PERSONALITY_EVOLUTION
                    else REFLECTION_SCHEMA_VERSION
                )
                policy_version = (
                    REFLECTION_POLICY_VERSION_V3
                    if purpose is ReflectionPurpose.PERSONALITY_EVOLUTION
                    else REFLECTION_POLICY_VERSION
                )
                provisional = tuple(
                    ReflectionSourceRecord(
                        source_id=item.source_id,
                        run_id="pending",
                        ordinal=ordinal,
                        kind=item.kind,
                        evidence_edge_id=item.evidence_edge_id,
                        evidence_edge_version=item.evidence_edge_version,
                        root_interaction_id=item.root_interaction_id,
                        root_message_id=item.root_message_id,
                        root_counterparty_id=item.root_counterparty_id,
                        observed_at=item.observed_at,
                        content_hash=item.content_hash,
                        affective_transition_id=(
                            item.affective.transition_id if item.affective is not None else None
                        ),
                        affective_state_version=(
                            item.affective.resulting_state_version
                            if item.affective is not None
                            else None
                        ),
                        affective_signal_hash=(
                            item.affective.signal_hash if item.affective is not None else None
                        ),
                        upstream_lineage_kind=(
                            item.upstream_lineage_kind
                            if purpose is ReflectionPurpose.PERSONALITY_EVOLUTION
                            else None
                        ),
                        upstream_lineage_id=(
                            item.upstream_lineage_id
                            if purpose is ReflectionPurpose.PERSONALITY_EVOLUTION
                            else None
                        ),
                    )
                    for ordinal, item in enumerate(selected)
                )
                digest = source_set_hash(
                    provisional,
                    schema_version=schema_version,
                    purpose=purpose,
                )
                key = reflection_run_key(
                    identity_id=identity_id,
                    source_hash=digest,
                    schema_version=schema_version,
                    policy_version=policy_version,
                    purpose=purpose,
                )
                prior = unit.reflection.get_run_by_key(key)
                if prior is not None:
                    return ReflectionProcessReport(prior, "existing_source_set")
                run_id = reflection_run_id(key)
                records = tuple(
                    replace(
                        item,
                        run_id=run_id,
                        source_id=reflection_source_id(run_id=run_id, ordinal=item.ordinal),
                    )
                    for item in provisional
                )
                run = ReflectionRun(
                    run_id=run_id,
                    run_key=key,
                    identity_id=identity_id,
                    schema_version=schema_version,
                    policy_version=policy_version,
                    trigger_kind=trigger,
                    source_set_hash=digest,
                    status=ReflectionRunStatus.PENDING_GENERATION,
                    aggregate_version=1,
                    attempt_count=0,
                    created_at=now,
                    updated_at=now,
                    purpose=purpose,
                )
                if not unit.reflection.create_run(run, records):
                    existing = unit.reflection.get_run_by_key(key)
                    return ReflectionProcessReport(existing, "concurrent_existing_source_set")
                unit.commit()
                created = True

        with self.reflection_uow_factory() as unit:
            source_records = unit.reflection.list_sources(run.run_id)
            sources = unit.reflection.load_generation_sources(run.run_id)
        if (
            source_set_hash(
                source_records,
                schema_version=run.schema_version,
                purpose=run.purpose,
            )
            != run.source_set_hash
        ):
            raise ValueError("persisted reflection source set hash mismatch")
        values: tuple[PositionValueReference, ...]
        inclinations: tuple[InclinationStateReference, ...]
        if run.purpose is ReflectionPurpose.PERSONALITY_EVOLUTION:
            if self.personality_context is None:
                return ReflectionProcessReport(run, "personality_context_unavailable", created)
            personality_state = self.personality_context.get_state_reference(identity_id)
            if personality_state is None:
                return ReflectionProcessReport(run, "personality_state_unavailable", created)
            positions: tuple[PositionStateReference, ...] = ()
            values = ()
            inclinations = ()
            max_proposals = 1
        else:
            personality_state = None
            with self.positions_uow_factory() as unit:
                stored_positions = unit.positions.list_positions(
                    identity_id=identity_id,
                    current_only=True,
                )
                values = unit.positions.get_value_references(identity_id)
                inclinations = (
                    unit.positions.list_inclination_references(identity_id=identity_id)
                    if run.schema_version >= 2
                    else ()
                )
            positions = tuple(
                PositionStateReference(
                    position_id=item.position_id,
                    aggregate_version=item.aggregate_version,
                    kind=item.kind,
                    stance=item.stance,
                    status=item.status.value,
                    proposition=item.proposition,
                    confidence=item.confidence,
                )
                for item in stored_positions[:REFLECTION_MAX_TARGET_POSITIONS]
            )
            max_proposals = REFLECTION_MAX_PROPOSALS
        request = ReflectionGenerationRequest(
            schema_version=run.schema_version,
            trace_id=trace_id,
            run_id=run.run_id,
            identity_id=identity_id,
            policy_version=run.policy_version,
            max_proposals=max_proposals,
            sources=sources,
            current_positions=positions,
            values=values,
            current_inclinations=inclinations[:REFLECTION_MAX_TARGET_INCLINATIONS],
            purpose=run.purpose,
            personality_state=personality_state,
        )
        started_at = self.clock.now()
        try:
            response = await self.provider.generate_structured(request)
            if response.document.schema_version != run.schema_version:
                raise ValueError("reflection response schema does not match its run")
            allowed = frozenset(item.source_id for item in sources)
            for candidate in response.document.proposals:
                validate_candidate_sources(candidate, allowed_source_ids=allowed)
        except (ReflectionProviderError, ValueError) as error:
            return self._record_failure(run, error=error, started_at=started_at, created=created)

        finished_at = self.clock.now()
        proposals = tuple(
            ReflectionProposal(
                proposal_id=reflection_proposal_id(
                    run_id=run.run_id, ordinal=ordinal, candidate=candidate
                ),
                run_id=run.run_id,
                ordinal=ordinal,
                target_owner=candidate.target_owner,
                payload=proposal_payload(candidate),
                evidence_source_ids=candidate_evidence_source_ids(candidate),
                created_at=finished_at,
            )
            for ordinal, candidate in enumerate(response.document.proposals)
        )
        next_status = (
            ReflectionRunStatus.PROPOSALS_READY if proposals else ReflectionRunStatus.COMPLETED
        )
        updated = replace(
            run,
            status=next_status,
            aggregate_version=run.aggregate_version + 1,
            attempt_count=run.attempt_count + 1,
            updated_at=finished_at,
            completed_at=finished_at if not proposals else None,
        )
        attempt = ReflectionAttempt(
            attempt_id=self.id_generator.new(),
            run_id=run.run_id,
            ordinal=updated.attempt_count,
            status=ReflectionAttemptStatus.SUCCEEDED,
            reason_code="proposals_ready" if proposals else "zero_proposals_completed",
            provider=response.provider,
            model=response.model,
            formation_method=response.formation_method,
            started_at=started_at,
            finished_at=finished_at,
            metrics=response.metrics.as_log_fields() if response.metrics is not None else {},
        )
        with self.reflection_uow_factory() as unit:
            unit.reflection.record_attempt(
                updated, attempt, proposals, expected_run_version=run.aggregate_version
            )
            unit.commit()
        return ReflectionProcessReport(updated, attempt.reason_code, created, True)

    def _record_failure(
        self,
        run: ReflectionRun,
        *,
        error: Exception,
        started_at: datetime,
        created: bool,
    ) -> ReflectionProcessReport:
        finished_at = self.clock.now()
        attempts = run.attempt_count + 1
        status = (
            ReflectionRunStatus.EXHAUSTED
            if attempts >= REFLECTION_MAX_ATTEMPTS
            else ReflectionRunStatus.RETRYABLE_FAILURE
        )
        updated = replace(
            run,
            status=status,
            aggregate_version=run.aggregate_version + 1,
            attempt_count=attempts,
            updated_at=finished_at,
        )
        provider = getattr(error, "provider", "unknown")
        model = getattr(error, "model", "unknown")
        attempt = ReflectionAttempt(
            attempt_id=self.id_generator.new(),
            run_id=run.run_id,
            ordinal=attempts,
            status=ReflectionAttemptStatus.FAILED,
            reason_code="provider_invalid_or_unavailable",
            provider=provider,
            model=model,
            formation_method="reflection.failed_before_valid_document",
            started_at=started_at,
            finished_at=finished_at,
            metrics={},
        )
        with self.reflection_uow_factory() as unit:
            unit.reflection.record_attempt(
                updated, attempt, (), expected_run_version=run.aggregate_version
            )
            unit.commit()
        return ReflectionProcessReport(updated, attempt.reason_code, created, True)

    @staticmethod
    def _bounded_sources(candidates: tuple[ReflectionSource, ...]) -> tuple[ReflectionSource, ...]:
        selected: list[ReflectionSource] = []
        characters = 0
        for item in candidates[:REFLECTION_MAX_SOURCES]:
            quote = item.quote
            if characters + len(quote) > REFLECTION_MAX_SOURCE_CHARACTERS:
                continue
            selected.append(item)
            characters += len(quote)
        return tuple(selected)

    @staticmethod
    def _personality_sources(
        candidates: tuple[ReflectionSource, ...],
        *,
        identity_id: str,
    ) -> tuple[PersonalityEvidenceSource, ...]:
        reservoir: list[PersonalityEvidenceSource] = []
        for item in candidates:
            if item.root_session_id is None or item.upstream_lineage_id is None:
                continue
            reservoir.append(
                PersonalityEvidenceSource(
                    source_id=item.source_id,
                    identity_id=identity_id,
                    evidence_edge_id=item.evidence_edge_id,
                    root_message_id=item.root_message_id,
                    root_interaction_id=item.root_interaction_id,
                    root_session_id=item.root_session_id,
                    root_counterparty_id=item.root_counterparty_id,
                    lineage_id=item.upstream_lineage_id,
                    observed_at=item.observed_at,
                    quote=item.quote,
                    content_hash=item.content_hash,
                )
            )
        return tuple(reservoir)

    @staticmethod
    def _eligibility_reason(
        selected: tuple[ReflectionSource, ...],
        runs: tuple[ReflectionRun, ...],
        *,
        trigger: ReflectionTriggerKind,
        now: datetime,
        purpose: ReflectionPurpose,
        identity_id: str,
    ) -> str | None:
        if purpose is ReflectionPurpose.PERSONALITY_EVOLUTION:
            if any(
                item.created_at > now or (item.completed_at is not None and item.completed_at > now)
                for item in runs
            ):
                return "personality_run_timestamp_from_future"
            personality_sources = ProcessReflection._personality_sources(
                selected,
                identity_id=identity_id,
            )
            diversity = personality_diversity(personality_sources)
            if diversity.observation_span < MIN_OBSERVATION_SPAN:
                return "personality_observation_span_too_short"
            if (
                diversity.root_count < MIN_ROOTS
                or diversity.interaction_count < MIN_ROOTS
                or diversity.session_count < MIN_SESSIONS
                or diversity.signature_count < MIN_ROOTS
                or diversity.cluster_count < MIN_CLUSTERS
                or diversity.week_bucket_count < MIN_WEEK_BUCKETS
                or diversity.month_bucket_count < MIN_MONTH_BUCKETS
                or diversity.lineage_count < MIN_LINEAGES
                or diversity.lineage_capped_root_count < MIN_ROOTS
            ):
                return "insufficient_personality_evidence_diversity"
            if any(now - item.created_at < _ROLLING_DAY for item in runs):
                return "personality_rolling_daily_cap"
            if trigger is ReflectionTriggerKind.AUTOMATIC and any(
                item.completed_at is not None
                and now - item.completed_at < _PERSONALITY_RUN_COOLDOWN
                for item in runs
            ):
                return "personality_reflection_cooldown"
            return None
        interactions = {item.root_interaction_id for item in selected}
        completed = [item for item in runs if item.status is ReflectionRunStatus.COMPLETED]
        completed_within_day = any(
            item.completed_at is not None and now - item.completed_at < timedelta(days=1)
            for item in completed
        )
        completed_within_cooldown = any(
            item.completed_at is not None and now - item.completed_at < timedelta(days=7)
            for item in completed
        )
        observed = [item.observed_at for item in selected]
        span = max(observed) - min(observed) if observed else timedelta(0)
        return reflection_trigger_reason(
            trigger,
            root_count=len(selected),
            interaction_count=len(interactions),
            observation_span=span,
            completed_within_day=completed_within_day,
            completed_within_cooldown=completed_within_cooldown,
        )


@dataclass(slots=True)
class ApplyReflectionProposals:
    """Route each persisted proposal through exactly one target owner transaction."""

    reflection_uow_factory: ReflectionUnitOfWorkFactory
    positions_uow_factory: PositionsUnitOfWorkFactory
    manager: PositionManager
    clock: Clock
    id_generator: IdGenerator
    personality_router: PersonalityReflectionRouter | None = None

    def execute(self, run_id: str, *, trace_id: str) -> ReflectionRun:
        with self.reflection_uow_factory() as unit:
            run = unit.reflection.get_run(run_id)
            if run is None:
                raise ValueError("reflection run does not exist")
            proposals = unit.reflection.list_proposals(run_id)
            outcomes = unit.reflection.list_outcomes(run_id)
            sources = unit.reflection.load_generation_sources(run_id)
        if run.status is ReflectionRunStatus.COMPLETED:
            return run
        if not run.status.requires_routing:
            raise ValueError("reflection run has no proposals ready for routing")
        if run.status is ReflectionRunStatus.PROPOSALS_READY:
            applying = replace(
                run,
                status=ReflectionRunStatus.APPLYING,
                aggregate_version=run.aggregate_version + 1,
                updated_at=self.clock.now(),
            )
            with self.reflection_uow_factory() as unit:
                unit.reflection.update_run(applying, expected_run_version=run.aggregate_version)
                unit.commit()
            run = applying

        completed_ids = {item.proposal_id for item in outcomes}
        source_by_id = {item.source_id: item for item in sources}
        for proposal in proposals:
            if proposal.proposal_id in completed_ids:
                continue
            if (
                run.purpose is ReflectionPurpose.PERSONALITY_EVOLUTION
                and proposal.target_owner is not ReflectionTargetOwner.PERSONALITY
            ):
                self._reject_disabled(run, proposal.proposal_id, trace_id=trace_id)
                continue
            if proposal.target_owner is ReflectionTargetOwner.SATORI_INCLINATIONS:
                self._apply_inclination(
                    run,
                    proposal,
                    source_by_id,
                    trace_id=trace_id,
                )
                continue
            if proposal.target_owner is ReflectionTargetOwner.PERSONALITY:
                if (
                    run.purpose is not ReflectionPurpose.PERSONALITY_EVOLUTION
                    or run.schema_version != REFLECTION_SCHEMA_VERSION_V3
                ):
                    self._reject_disabled(run, proposal.proposal_id, trace_id=trace_id)
                    continue
                if self.personality_router is None:
                    raise RuntimeError("personality reflection router is unavailable")
                self.personality_router.execute(
                    run.identity_id,
                    reflection_run_id=run.run_id,
                    reflection_proposal_id=proposal.proposal_id,
                    proposal=self._personality_proposal(proposal.payload),
                    trace_id=trace_id,
                )
                continue
            if proposal.target_owner is not ReflectionTargetOwner.SATORI_POSITIONS:
                self._reject_disabled(run, proposal.proposal_id, trace_id=trace_id)
                continue
            candidate = self._position_proposal(proposal.payload, source_by_id)
            outcome_id = reflection_outcome_id(
                proposal_id=proposal.proposal_id,
                target_policy_version=POSITION_POLICY_VERSION,
            )
            now = self.clock.now()
            with self.positions_uow_factory() as unit:
                existing = unit.positions.list_positions(
                    identity_id=run.identity_id, current_only=False
                )
                values = unit.positions.get_value_references(run.identity_id)
                plan = self.manager.evaluate(
                    (candidate,),
                    identity_id=run.identity_id,
                    current_message_id=next(iter(source_by_id.values())).root_message_id,
                    sources=tuple(
                        PositionSourceMessage(
                            message_id=item.root_message_id,
                            interaction_id=item.root_interaction_id,
                            identity_id=run.identity_id,
                            counterparty_id=item.root_counterparty_id,
                            observed_at=item.observed_at,
                            content=item.quote,
                        )
                        for item in source_by_id.values()
                    ),
                    value_keys=frozenset(item.key for item in values),
                    existing_positions=existing,
                    max_positions=1,
                    now=now,
                    decision_id=None,
                    new_id=self.id_generator.new,
                    origin=PositionEvaluationOrigin.REFLECTION,
                    reflection_outcome_id=outcome_id,
                )
                accepted = bool(plan.positions)
                outcome = ReflectionOutcome(
                    outcome_id=outcome_id,
                    proposal_id=proposal.proposal_id,
                    target_policy_version=POSITION_POLICY_VERSION,
                    decision=(
                        ReflectionOutcomeDecision.ACCEPTED
                        if accepted
                        else ReflectionOutcomeDecision.REJECTED
                    ),
                    reason_code=(
                        "position_owner_accepted" if accepted else "position_owner_rejected"
                    ),
                    target_aggregate_type="satori_positions" if accepted else None,
                    target_aggregate_id=(plan.positions[0].position_id if accepted else None),
                    decided_at=now,
                )
                unit.positions.record_reflection_decision(
                    outcome,
                    plan,
                    identity_id=run.identity_id,
                    trace_id=trace_id,
                    audit_event_id=self.id_generator.new(),
                )
                unit.commit()

        with self.reflection_uow_factory() as unit:
            terminal_outcomes = unit.reflection.list_outcomes(run_id)
            if len(terminal_outcomes) != len(proposals):
                raise RuntimeError("reflection routing left nonterminal proposals")
            latest = unit.reflection.get_run(run_id)
            if latest is None:
                raise RuntimeError("reflection run disappeared")
            completed = complete_reflection_run(latest, completed_at=self.clock.now())
            if completed is latest:
                return latest
            unit.reflection.update_run(completed, expected_run_version=latest.aggregate_version)
            unit.commit()
        return completed

    def _apply_inclination(
        self,
        run: ReflectionRun,
        proposal: ReflectionProposal,
        source_by_id: dict[str, ReflectionSource],
        *,
        trace_id: str,
    ) -> None:
        if run.schema_version < 2:
            raise ValueError("reflection V1 cannot route inclination proposals")
        candidate = self._inclination_proposal(proposal.payload)
        outcome_id = reflection_outcome_id(
            proposal_id=proposal.proposal_id,
            target_policy_version=INCLINATION_POLICY_VERSION,
        )
        owner_sources: list[InclinationEvidenceSource] = []
        for source in source_by_id.values():
            if source.affective is None or source.root_session_id is None:
                continue
            owner_sources.append(
                InclinationEvidenceSource(
                    source_id=source.source_id,
                    identity_id=run.identity_id,
                    root_message_id=source.root_message_id,
                    root_interaction_id=source.root_interaction_id,
                    root_session_id=source.root_session_id,
                    root_counterparty_id=source.root_counterparty_id,
                    observed_at=source.observed_at,
                    quote=source.quote,
                    content_hash=source.content_hash,
                    affective=source.affective,
                )
            )
        now = self.clock.now()
        with self.positions_uow_factory() as unit:
            existing = unit.positions.list_inclinations(identity_id=run.identity_id)
            evaluation = self.manager.evaluate_inclination(
                candidate,
                identity_id=run.identity_id,
                sources=tuple(owner_sources),
                existing_inclinations=existing,
                reflection_outcome_id=outcome_id,
                now=now,
                new_id=self.id_generator.new,
            )
            accepted = evaluation.kind is InclinationDecisionKind.APPLIED
            outcome = ReflectionOutcome(
                outcome_id=outcome_id,
                proposal_id=proposal.proposal_id,
                target_policy_version=INCLINATION_POLICY_VERSION,
                decision=(
                    ReflectionOutcomeDecision.ACCEPTED
                    if accepted
                    else ReflectionOutcomeDecision.REJECTED
                ),
                reason_code=evaluation.reason_code,
                target_aggregate_type="satori_inclinations" if accepted else None,
                target_aggregate_id=(
                    evaluation.inclination.inclination_id
                    if evaluation.inclination is not None
                    else None
                ),
                decided_at=now,
            )
            unit.positions.record_inclination_reflection_decision(
                outcome,
                evaluation,
                identity_id=run.identity_id,
                trace_id=trace_id,
                audit_event_id=self.id_generator.new(),
            )
            unit.commit()

    def _reject_disabled(self, run: ReflectionRun, proposal_id: str, *, trace_id: str) -> None:
        outcome = ReflectionOutcome(
            outcome_id=reflection_outcome_id(
                proposal_id=proposal_id,
                target_policy_version=run.policy_version,
            ),
            proposal_id=proposal_id,
            target_policy_version=run.policy_version,
            decision=ReflectionOutcomeDecision.REJECTED,
            reason_code="target_owner_not_enabled",
            target_aggregate_type=None,
            target_aggregate_id=None,
            decided_at=self.clock.now(),
        )
        with self.reflection_uow_factory() as unit:
            unit.reflection.record_outcome(
                outcome,
                identity_id=run.identity_id,
                trace_id=trace_id,
                audit_event_id=self.id_generator.new(),
            )
            unit.commit()

    @staticmethod
    def _position_proposal(
        payload: dict[str, object], source_by_id: dict[str, ReflectionSource]
    ) -> PositionProposal:
        raw_evidence = payload.get("evidence")
        if not isinstance(raw_evidence, list):
            raise ValueError("persisted reflection position evidence is invalid")
        citations: list[PositionEvidenceCitation] = []
        for item in raw_evidence:
            if not isinstance(item, dict):
                raise ValueError("persisted reflection citation is invalid")
            source_id = item.get("source_id")
            role = item.get("role")
            if not isinstance(source_id, str) or not isinstance(role, str):
                raise ValueError("persisted reflection citation fields are invalid")
            source = source_by_id.get(source_id)
            if source is None:
                raise ValueError("persisted reflection citation escaped fixed source set")
            citations.append(
                PositionEvidenceCitation(
                    message_id=source.root_message_id,
                    quote=source.quote,
                    role=PositionEvidenceRole(role),
                )
            )

        def optional_text(name: str) -> str | None:
            value = payload.get(name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"persisted reflection {name} is invalid")
            return value

        proposition = payload.get("proposition")
        kind = payload.get("kind")
        stance = payload.get("stance")
        confidence = payload.get("confidence")
        expected = payload.get("expected_target_version")
        if (
            not isinstance(proposition, str)
            or not isinstance(kind, str)
            or not isinstance(stance, str)
            or isinstance(confidence, bool)
            or not isinstance(confidence, int | float)
            or (expected is not None and (type(expected) is not int or expected < 1))
        ):
            raise ValueError("persisted reflection position payload is invalid")
        return PositionProposal(
            proposition=proposition,
            kind=PositionKind(kind),
            stance=PositionStance(stance),
            confidence=float(confidence),
            evidence=tuple(citations),
            value_key=optional_text("value_key"),
            revises_position_id=optional_text("revises_position_id"),
            opposes_position_id=optional_text("opposes_position_id"),
            challenges_position_id=optional_text("challenges_position_id"),
            expected_target_version=expected,
        )

    @staticmethod
    def _inclination_proposal(payload: dict[str, object]) -> InclinationProposal:
        kind = payload.get("kind")
        topic = payload.get("topic")
        alternative = payload.get("alternative_topic")
        confidence = payload.get("confidence")
        source_ids = payload.get("source_ids")
        target = payload.get("target_inclination_id")
        expected = payload.get("expected_target_version")
        if (
            not isinstance(kind, str)
            or not isinstance(topic, str)
            or (alternative is not None and not isinstance(alternative, str))
            or isinstance(confidence, bool)
            or not isinstance(confidence, int | float)
            or not isinstance(source_ids, list)
            or any(not isinstance(item, str) for item in source_ids)
            or (target is not None and not isinstance(target, str))
            or (expected is not None and (type(expected) is not int or expected < 1))
        ):
            raise ValueError("persisted reflection inclination payload is invalid")
        return InclinationProposal(
            kind=InclinationKind(kind),
            topic=topic,
            alternative_topic=alternative,
            confidence=float(confidence),
            source_ids=tuple(source_ids),
            target_inclination_id=target,
            expected_target_version=expected,
        )

    @staticmethod
    def _personality_proposal(payload: dict[str, object]) -> PersonalityChangeProposal:
        if (
            set(payload)
            != {
                "target_owner",
                "trait_key",
                "direction",
                "confidence",
                "citations",
                "expected_personality_version",
            }
            or payload.get("target_owner") != ReflectionTargetOwner.PERSONALITY.value
        ):
            raise ValueError("persisted reflection personality payload shape is invalid")
        trait_key = payload.get("trait_key")
        direction = payload.get("direction")
        confidence = payload.get("confidence")
        citations = payload.get("citations")
        expected = payload.get("expected_personality_version")
        if (
            not isinstance(trait_key, str)
            or not isinstance(direction, str)
            or isinstance(confidence, bool)
            or not isinstance(confidence, int | float)
            or not isinstance(citations, list)
            or type(expected) is not int
        ):
            raise ValueError("persisted reflection personality payload is invalid")
        mapped_citations: list[PersonalityCitation] = []
        for item in citations:
            if not isinstance(item, dict) or set(item) != {"source_id", "role"}:
                raise ValueError("persisted reflection personality citation is invalid")
            source_id = item.get("source_id")
            role = item.get("role")
            if not isinstance(source_id, str) or not isinstance(role, str):
                raise ValueError("persisted reflection personality citation fields are invalid")
            mapped_citations.append(
                PersonalityCitation(
                    source_id=source_id,
                    role=PersonalityCitationRole(role),
                )
            )
        return PersonalityChangeProposal(
            trait_key=cast(PersonalityTraitKey, trait_key),
            direction=PersonalityDirection(direction),
            confidence=float(confidence),
            citations=tuple(mapped_citations),
            expected_personality_version=expected,
        )
