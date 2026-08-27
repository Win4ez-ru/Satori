"""SQLAlchemy adapter for Stage 12-14 reflection lifecycle records."""

from datetime import datetime

from sqlalchemy import and_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.core.inclinations import InclinationAffectiveSignal
from satori.core.reflection import (
    ReflectionLineageKind,
    ReflectionPurpose,
    ReflectionSource,
    ReflectionSourceKind,
    ReflectionTargetOwner,
)
from satori.domain.personality_evolution import PERSONALITY_EVIDENCE_RESERVOIR_LIMIT
from satori.domain.reflection import (
    ReflectionAttempt,
    ReflectionAttemptStatus,
    ReflectionOutcome,
    ReflectionOutcomeDecision,
    ReflectionProposal,
    ReflectionRun,
    ReflectionRunStatus,
    ReflectionSourceRecord,
    ReflectionTriggerKind,
    affective_signal_hash,
    content_hash,
)
from satori.infrastructure.persistence.models.affect import AffectiveTransitionRow
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.initial_self import AuditEventRow
from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow, MemoryEvidenceRow
from satori.infrastructure.persistence.models.positions import (
    InclinationEvidenceRow,
    PositionEvidenceRow,
    SatoriInclinationRow,
    SatoriPositionRow,
)
from satori.infrastructure.persistence.models.reflection import (
    ReflectionAttemptRow,
    ReflectionOutcomeRow,
    ReflectionProposalRow,
    ReflectionRunRow,
    ReflectionSourceRow,
)


class SQLAlchemyReflectionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_run(self, run_id: str) -> ReflectionRun | None:
        row = self._session.get(ReflectionRunRow, run_id)
        return self._map_run(row) if row is not None else None

    def list_runs(
        self,
        *,
        identity_id: str,
        limit: int,
        purpose: ReflectionPurpose | None = None,
    ) -> tuple[ReflectionRun, ...]:
        if limit < 1:
            raise ValueError("reflection run limit must be positive")
        query = select(ReflectionRunRow).where(ReflectionRunRow.identity_id == identity_id)
        if purpose is not None:
            query = query.where(ReflectionRunRow.purpose == purpose.value)
        rows = self._session.execute(
            query.order_by(
                ReflectionRunRow.created_at.desc(), ReflectionRunRow.run_id.desc()
            ).limit(limit)
        ).scalars()
        return tuple(self._map_run(item) for item in rows)

    def list_eligible_sources(
        self,
        *,
        identity_id: str,
        limit: int,
        purpose: ReflectionPurpose = ReflectionPurpose.GENERAL,
    ) -> tuple[ReflectionSource, ...]:
        if limit < 1:
            raise ValueError("reflection source limit must be positive")
        consumed_roots = set(
            self._session.execute(
                select(ReflectionSourceRow.root_message_id)
                .join(ReflectionRunRow, ReflectionRunRow.run_id == ReflectionSourceRow.run_id)
                .where(
                    ReflectionRunRow.identity_id == identity_id,
                    ReflectionRunRow.status == ReflectionRunStatus.COMPLETED.value,
                    ReflectionRunRow.purpose == purpose.value,
                )
            ).scalars()
        )
        if purpose is ReflectionPurpose.PERSONALITY_EVOLUTION:
            consumed_roots.update(
                self._session.execute(
                    select(InclinationEvidenceRow.source_message_id)
                    .join(
                        SatoriInclinationRow,
                        SatoriInclinationRow.inclination_id
                        == InclinationEvidenceRow.inclination_id,
                    )
                    .where(SatoriInclinationRow.identity_id == identity_id)
                ).scalars()
            )
        excluded_roots = tuple(sorted(consumed_roots))
        position_query = (
            select(
                PositionEvidenceRow,
                ConversationMessageRow,
                ConversationInteractionRow,
                ConversationSessionRow,
                AffectiveTransitionRow,
            )
            .join(
                SatoriPositionRow, SatoriPositionRow.position_id == PositionEvidenceRow.position_id
            )
            .join(
                ConversationMessageRow,
                ConversationMessageRow.message_id == PositionEvidenceRow.source_message_id,
            )
            .join(
                ConversationInteractionRow,
                ConversationInteractionRow.interaction_id
                == PositionEvidenceRow.source_interaction_id,
            )
            .join(
                ConversationSessionRow,
                ConversationSessionRow.session_id == ConversationInteractionRow.session_id,
            )
            .outerjoin(
                AffectiveTransitionRow,
                and_(
                    AffectiveTransitionRow.identity_id == SatoriPositionRow.identity_id,
                    AffectiveTransitionRow.interaction_id
                    == ConversationInteractionRow.interaction_id,
                    AffectiveTransitionRow.source_message_id == ConversationMessageRow.message_id,
                ),
            )
            .where(
                SatoriPositionRow.identity_id == identity_id,
                ConversationMessageRow.role == "user",
                ConversationInteractionRow.status == "completed",
            )
            .order_by(PositionEvidenceRow.observed_at, PositionEvidenceRow.evidence_id)
        )
        if purpose is ReflectionPurpose.PERSONALITY_EVOLUTION:
            if excluded_roots:
                position_query = position_query.where(
                    ConversationMessageRow.message_id.not_in(excluded_roots)
                )
            position_query = position_query.limit(min(limit, PERSONALITY_EVIDENCE_RESERVOIR_LIMIT))
        position_rows = self._session.execute(position_query).all()
        memory_query = (
            select(
                MemoryEvidenceRow,
                EpisodicMemoryRow,
                ConversationMessageRow,
                ConversationInteractionRow,
                ConversationSessionRow,
                AffectiveTransitionRow,
            )
            .join(EpisodicMemoryRow, EpisodicMemoryRow.memory_id == MemoryEvidenceRow.memory_id)
            .join(
                ConversationMessageRow,
                ConversationMessageRow.message_id == MemoryEvidenceRow.source_message_id,
            )
            .join(
                ConversationInteractionRow,
                ConversationInteractionRow.interaction_id == ConversationMessageRow.interaction_id,
            )
            .join(
                ConversationSessionRow,
                ConversationSessionRow.session_id == ConversationInteractionRow.session_id,
            )
            .outerjoin(
                AffectiveTransitionRow,
                and_(
                    AffectiveTransitionRow.identity_id == ConversationSessionRow.identity_id,
                    AffectiveTransitionRow.interaction_id
                    == ConversationInteractionRow.interaction_id,
                    AffectiveTransitionRow.source_message_id == ConversationMessageRow.message_id,
                ),
            )
            .where(
                ConversationSessionRow.identity_id == identity_id,
                ConversationMessageRow.role == "user",
                ConversationInteractionRow.status == "completed",
                EpisodicMemoryRow.lifecycle_status == "active",
                EpisodicMemoryRow.importance >= 0.65,
            )
            .order_by(MemoryEvidenceRow.observed_at, MemoryEvidenceRow.evidence_id)
        )
        if purpose is ReflectionPurpose.PERSONALITY_EVOLUTION:
            if excluded_roots:
                memory_query = memory_query.where(
                    ConversationMessageRow.message_id.not_in(excluded_roots)
                )
            memory_query = memory_query.limit(min(limit, PERSONALITY_EVIDENCE_RESERVOIR_LIMIT))
        memory_rows = self._session.execute(memory_query).all()
        selected: dict[str, ReflectionSource] = {}
        for evidence, message, interaction, session, transition in position_rows:
            if message.message_id in consumed_roots or evidence.quote not in message.content:
                continue
            selected.setdefault(
                message.message_id,
                self._candidate_source(
                    kind=ReflectionSourceKind.POSITION_EVIDENCE,
                    edge_id=evidence.evidence_id,
                    lineage_kind=ReflectionLineageKind.POSITION,
                    lineage_id=evidence.position_id,
                    interaction=interaction,
                    message=message,
                    session=session,
                    observed_at=evidence.observed_at,
                    quote=evidence.quote,
                    transition=(transition if purpose is ReflectionPurpose.GENERAL else None),
                ),
            )
        for evidence, memory, message, interaction, session, transition in memory_rows:
            if (
                message.message_id in consumed_roots
                or message.message_id in selected
                or evidence.quote not in message.content
            ):
                continue
            selected[message.message_id] = self._candidate_source(
                kind=ReflectionSourceKind.EPISODIC_MEMORY_EVIDENCE,
                edge_id=evidence.evidence_id,
                lineage_kind=ReflectionLineageKind.EPISODIC_MEMORY,
                lineage_id=memory.memory_id,
                interaction=interaction,
                message=message,
                session=session,
                observed_at=evidence.observed_at,
                quote=evidence.quote,
                transition=(transition if purpose is ReflectionPurpose.GENERAL else None),
            )
        return tuple(
            sorted(selected.values(), key=lambda item: (item.observed_at, item.source_id))[:limit]
        )

    def load_generation_sources(self, run_id: str) -> tuple[ReflectionSource, ...]:
        run = self._session.get(ReflectionRunRow, run_id)
        if run is None:
            return ()
        records = self.list_sources(run_id)
        loaded: list[ReflectionSource] = []
        for record in records:
            if record.kind is ReflectionSourceKind.POSITION_EVIDENCE:
                position_edge = self._session.get(PositionEvidenceRow, record.evidence_edge_id)
                edge_quote = position_edge.quote if position_edge is not None else None
                edge_message_id = (
                    position_edge.source_message_id if position_edge is not None else None
                )
                edge_interaction_id = (
                    position_edge.source_interaction_id if position_edge is not None else None
                )
                edge_position = (
                    self._session.get(SatoriPositionRow, position_edge.position_id)
                    if position_edge is not None
                    else None
                )
                edge_identity_id = edge_position.identity_id if edge_position is not None else None
                edge_counterparty_id = (
                    position_edge.source_counterparty_id if position_edge is not None else None
                )
                edge_lineage_kind = ReflectionLineageKind.POSITION
                edge_lineage_id = position_edge.position_id if position_edge is not None else None
            else:
                memory_edge = self._session.get(MemoryEvidenceRow, record.evidence_edge_id)
                edge_quote = memory_edge.quote if memory_edge is not None else None
                edge_message_id = memory_edge.source_message_id if memory_edge is not None else None
                edge_memory = (
                    self._session.get(EpisodicMemoryRow, memory_edge.memory_id)
                    if memory_edge is not None
                    else None
                )
                edge_interaction_id = None
                edge_identity_id = run.identity_id if edge_memory is not None else None
                edge_counterparty_id = record.root_counterparty_id
                edge_lineage_kind = ReflectionLineageKind.EPISODIC_MEMORY
                edge_lineage_id = memory_edge.memory_id if memory_edge is not None else None
            message = self._session.get(ConversationMessageRow, record.root_message_id)
            interaction = self._session.get(ConversationInteractionRow, record.root_interaction_id)
            session = (
                self._session.get(ConversationSessionRow, interaction.session_id)
                if interaction is not None
                else None
            )
            if (
                edge_quote is None
                or message is None
                or interaction is None
                or session is None
                or message.role != "user"
                or edge_message_id != record.root_message_id
                or (
                    edge_interaction_id is not None
                    and edge_interaction_id != record.root_interaction_id
                )
                or edge_identity_id != run.identity_id
                or edge_counterparty_id != record.root_counterparty_id
                or (
                    record.upstream_lineage_kind is not None
                    and record.upstream_lineage_kind is not edge_lineage_kind
                )
                or (
                    record.upstream_lineage_id is not None
                    and record.upstream_lineage_id != edge_lineage_id
                )
                or message.interaction_id != record.root_interaction_id
                or interaction.status != "completed"
                or session.identity_id != run.identity_id
                or session.counterparty_id != record.root_counterparty_id
                or edge_quote not in message.content
                or content_hash(edge_quote) != record.content_hash
            ):
                raise ValueError("reflection source provenance or content hash mismatch")
            affective = self._load_affective_attachment(record, identity_id=run.identity_id)
            if run.schema_version == 1 and affective is not None:
                raise ValueError("Reflection V1 source cannot contain an affect attachment")
            purpose = ReflectionPurpose(run.purpose)
            if purpose is ReflectionPurpose.PERSONALITY_EVOLUTION and (
                run.schema_version != 3
                or affective is not None
                or record.upstream_lineage_kind is None
                or record.upstream_lineage_id is None
            ):
                raise ValueError("Reflection V3 source violates personality purpose isolation")
            loaded.append(
                ReflectionSource(
                    source_id=record.source_id,
                    kind=record.kind,
                    evidence_edge_id=record.evidence_edge_id,
                    evidence_edge_version=record.evidence_edge_version,
                    root_interaction_id=record.root_interaction_id,
                    root_message_id=record.root_message_id,
                    root_counterparty_id=record.root_counterparty_id,
                    observed_at=record.observed_at,
                    content_hash=record.content_hash,
                    quote=edge_quote,
                    affective=affective,
                    root_session_id=session.session_id,
                    upstream_lineage_kind=record.upstream_lineage_kind,
                    upstream_lineage_id=record.upstream_lineage_id,
                )
            )
        return tuple(loaded)

    def get_run_by_key(self, run_key: str) -> ReflectionRun | None:
        row = self._session.execute(
            select(ReflectionRunRow).where(ReflectionRunRow.run_key == run_key)
        ).scalar_one_or_none()
        return self._map_run(row) if row is not None else None

    def list_sources(self, run_id: str) -> tuple[ReflectionSourceRecord, ...]:
        rows = self._session.execute(
            select(ReflectionSourceRow)
            .where(ReflectionSourceRow.run_id == run_id)
            .order_by(ReflectionSourceRow.ordinal)
        ).scalars()
        return tuple(self._map_source(item) for item in rows)

    def list_attempts(self, run_id: str) -> tuple[ReflectionAttempt, ...]:
        rows = self._session.execute(
            select(ReflectionAttemptRow)
            .where(ReflectionAttemptRow.run_id == run_id)
            .order_by(ReflectionAttemptRow.ordinal)
        ).scalars()
        return tuple(self._map_attempt(item) for item in rows)

    def list_proposals(self, run_id: str) -> tuple[ReflectionProposal, ...]:
        rows = self._session.execute(
            select(ReflectionProposalRow)
            .where(ReflectionProposalRow.run_id == run_id)
            .order_by(ReflectionProposalRow.ordinal)
        ).scalars()
        return tuple(self._map_proposal(item) for item in rows)

    def list_outcomes(self, run_id: str) -> tuple[ReflectionOutcome, ...]:
        rows = self._session.execute(
            select(ReflectionOutcomeRow)
            .join(
                ReflectionProposalRow,
                ReflectionProposalRow.proposal_id == ReflectionOutcomeRow.proposal_id,
            )
            .where(ReflectionProposalRow.run_id == run_id)
            .order_by(ReflectionProposalRow.ordinal)
        ).scalars()
        return tuple(self._map_outcome(item) for item in rows)

    def create_run(self, run: ReflectionRun, sources: tuple[ReflectionSourceRecord, ...]) -> bool:
        if any(item.run_id != run.run_id for item in sources):
            raise ValueError("reflection source belongs to a different run")
        for source in sources:
            affective = self._load_affective_attachment(source, identity_id=run.identity_id)
            if run.schema_version == 1 and affective is not None:
                raise ValueError("Reflection V1 source cannot contain an affect attachment")
            if run.purpose is ReflectionPurpose.PERSONALITY_EVOLUTION and (
                run.schema_version != 3
                or affective is not None
                or source.upstream_lineage_kind is None
                or source.upstream_lineage_id is None
            ):
                raise ValueError("Reflection V3 source violates personality purpose isolation")
            if run.purpose is ReflectionPurpose.GENERAL and run.schema_version >= 3:
                raise ValueError("general reflection cannot use the V3 personality wire")
        statement = (
            sqlite_insert(ReflectionRunRow)
            .values(**self._run_values(run))
            .on_conflict_do_nothing(index_elements=["run_key"])
            .returning(ReflectionRunRow.run_id)
        )
        if self._session.execute(statement).scalar_one_or_none() is None:
            return False
        self._session.add_all(self._source_row(item) for item in sources)
        return True

    def record_attempt(
        self,
        run: ReflectionRun,
        attempt: ReflectionAttempt,
        proposals: tuple[ReflectionProposal, ...],
        *,
        expected_run_version: int,
    ) -> None:
        if attempt.run_id != run.run_id or any(item.run_id != run.run_id for item in proposals):
            raise ValueError("reflection attempt/proposal belongs to a different run")
        self._session.add(self._attempt_row(attempt))
        self._session.add_all(self._proposal_row(item) for item in proposals)
        self.update_run(run, expected_run_version=expected_run_version)

    def record_outcome(
        self,
        outcome: ReflectionOutcome,
        *,
        identity_id: str,
        trace_id: str,
        audit_event_id: str,
    ) -> bool:
        statement = (
            sqlite_insert(ReflectionOutcomeRow)
            .values(
                outcome_id=outcome.outcome_id,
                proposal_id=outcome.proposal_id,
                target_policy_version=outcome.target_policy_version,
                decision=outcome.decision.value,
                reason_code=outcome.reason_code,
                target_aggregate_type=outcome.target_aggregate_type,
                target_aggregate_id=outcome.target_aggregate_id,
                decided_at=outcome.decided_at,
            )
            .on_conflict_do_nothing(index_elements=["proposal_id", "target_policy_version"])
            .returning(ReflectionOutcomeRow.outcome_id)
        )
        if self._session.execute(statement).scalar_one_or_none() is None:
            return False
        self._session.add(
            AuditEventRow(
                event_id=audit_event_id,
                schema_version=1,
                event_type=f"reflection.{outcome.decision.value}",
                aggregate_type="reflection_proposal",
                aggregate_id=outcome.proposal_id,
                occurred_at=outcome.decided_at,
                trace_id=trace_id,
                details={
                    "outcome_id": outcome.outcome_id,
                    "target_policy_version": outcome.target_policy_version,
                    "reason_code": outcome.reason_code,
                    "identity_id": identity_id,
                },
            )
        )
        return True

    def update_run(self, run: ReflectionRun, *, expected_run_version: int) -> None:
        if run.aggregate_version != expected_run_version + 1:
            raise ValueError("reflection run version is not monotonic")
        values = self._run_values(run)
        values.pop("run_id")
        values.pop("run_key")
        values.pop("identity_id")
        result = self._session.execute(
            update(ReflectionRunRow)
            .where(
                ReflectionRunRow.run_id == run.run_id,
                ReflectionRunRow.aggregate_version == expected_run_version,
            )
            .values(**values)
        )
        if getattr(result, "rowcount", None) != 1:
            raise RuntimeError("reflection run was concurrently modified")

    def _load_affective_attachment(
        self,
        source: ReflectionSourceRecord,
        *,
        identity_id: str,
    ) -> InclinationAffectiveSignal | None:
        if source.affective_transition_id is None:
            return None
        transition = self._session.get(AffectiveTransitionRow, source.affective_transition_id)
        if (
            transition is None
            or transition.identity_id != identity_id
            or transition.interaction_id != source.root_interaction_id
            or transition.source_message_id != source.root_message_id
            or transition.resulting_state_version != source.affective_state_version
        ):
            raise ValueError("reflection affect attachment provenance or version mismatch")
        affective = _inclination_affective_signal(
            transition,
            source=source,
        )
        if affective.signal_hash != source.affective_signal_hash:
            raise ValueError("reflection affect attachment signal hash mismatch")
        return affective

    @staticmethod
    def _candidate_source(
        *,
        kind: ReflectionSourceKind,
        edge_id: str,
        lineage_kind: ReflectionLineageKind,
        lineage_id: str,
        interaction: ConversationInteractionRow,
        message: ConversationMessageRow,
        session: ConversationSessionRow,
        observed_at: datetime,
        quote: str,
        transition: AffectiveTransitionRow | None,
    ) -> ReflectionSource:
        source_hash = content_hash(quote)
        source_record = ReflectionSourceRecord(
            source_id=f"candidate-{kind.value}-{edge_id}",
            run_id="candidate",
            ordinal=0,
            kind=kind,
            evidence_edge_id=edge_id,
            evidence_edge_version=1,
            root_interaction_id=interaction.interaction_id,
            root_message_id=message.message_id,
            root_counterparty_id=session.counterparty_id,
            observed_at=observed_at,
            content_hash=source_hash,
        )
        affective = (
            None
            if transition is None
            else _inclination_affective_signal(
                transition,
                source=source_record,
            )
        )
        return ReflectionSource(
            source_id=source_record.source_id,
            kind=kind,
            evidence_edge_id=edge_id,
            evidence_edge_version=1,
            root_interaction_id=interaction.interaction_id,
            root_message_id=message.message_id,
            root_counterparty_id=session.counterparty_id,
            observed_at=observed_at,
            content_hash=source_hash,
            quote=quote,
            affective=affective,
            root_session_id=session.session_id,
            upstream_lineage_kind=lineage_kind,
            upstream_lineage_id=lineage_id,
        )

    @staticmethod
    def _run_values(run: ReflectionRun) -> dict[str, object]:
        return {
            "run_id": run.run_id,
            "run_key": run.run_key,
            "identity_id": run.identity_id,
            "schema_version": run.schema_version,
            "policy_version": run.policy_version,
            "purpose": run.purpose.value,
            "trigger_kind": run.trigger_kind.value,
            "source_set_hash": run.source_set_hash,
            "status": run.status.value,
            "aggregate_version": run.aggregate_version,
            "attempt_count": run.attempt_count,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "completed_at": run.completed_at,
        }

    @staticmethod
    def _source_row(source: ReflectionSourceRecord) -> ReflectionSourceRow:
        return ReflectionSourceRow(
            source_id=source.source_id,
            run_id=source.run_id,
            ordinal=source.ordinal,
            kind=source.kind.value,
            evidence_edge_id=source.evidence_edge_id,
            evidence_edge_version=source.evidence_edge_version,
            root_interaction_id=source.root_interaction_id,
            root_message_id=source.root_message_id,
            root_counterparty_id=source.root_counterparty_id,
            observed_at=source.observed_at,
            content_hash=source.content_hash,
            upstream_lineage_kind=(
                source.upstream_lineage_kind.value
                if source.upstream_lineage_kind is not None
                else None
            ),
            upstream_lineage_id=source.upstream_lineage_id,
            affective_transition_id=source.affective_transition_id,
            affective_state_version=source.affective_state_version,
            affective_signal_hash=source.affective_signal_hash,
        )

    @staticmethod
    def _attempt_row(attempt: ReflectionAttempt) -> ReflectionAttemptRow:
        return ReflectionAttemptRow(
            attempt_id=attempt.attempt_id,
            run_id=attempt.run_id,
            ordinal=attempt.ordinal,
            status=attempt.status.value,
            reason_code=attempt.reason_code,
            provider=attempt.provider,
            model=attempt.model,
            formation_method=attempt.formation_method,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            metrics=attempt.metrics,
        )

    @staticmethod
    def _proposal_row(proposal: ReflectionProposal) -> ReflectionProposalRow:
        return ReflectionProposalRow(
            proposal_id=proposal.proposal_id,
            run_id=proposal.run_id,
            ordinal=proposal.ordinal,
            target_owner=proposal.target_owner.value,
            payload=proposal.payload,
            evidence_source_ids=list(proposal.evidence_source_ids),
            created_at=proposal.created_at,
        )

    @staticmethod
    def _map_run(row: ReflectionRunRow) -> ReflectionRun:
        return ReflectionRun(
            run_id=row.run_id,
            run_key=row.run_key,
            identity_id=row.identity_id,
            schema_version=row.schema_version,
            policy_version=row.policy_version,
            trigger_kind=ReflectionTriggerKind(row.trigger_kind),
            source_set_hash=row.source_set_hash,
            status=ReflectionRunStatus(row.status),
            aggregate_version=row.aggregate_version,
            attempt_count=row.attempt_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
            purpose=ReflectionPurpose(row.purpose),
        )

    @staticmethod
    def _map_source(row: ReflectionSourceRow) -> ReflectionSourceRecord:
        return ReflectionSourceRecord(
            source_id=row.source_id,
            run_id=row.run_id,
            ordinal=row.ordinal,
            kind=ReflectionSourceKind(row.kind),
            evidence_edge_id=row.evidence_edge_id,
            evidence_edge_version=row.evidence_edge_version,
            root_interaction_id=row.root_interaction_id,
            root_message_id=row.root_message_id,
            root_counterparty_id=row.root_counterparty_id,
            observed_at=row.observed_at,
            content_hash=row.content_hash,
            affective_transition_id=row.affective_transition_id,
            affective_state_version=row.affective_state_version,
            affective_signal_hash=row.affective_signal_hash,
            upstream_lineage_kind=(
                ReflectionLineageKind(row.upstream_lineage_kind)
                if row.upstream_lineage_kind is not None
                else None
            ),
            upstream_lineage_id=row.upstream_lineage_id,
        )

    @staticmethod
    def _map_attempt(row: ReflectionAttemptRow) -> ReflectionAttempt:
        return ReflectionAttempt(
            attempt_id=row.attempt_id,
            run_id=row.run_id,
            ordinal=row.ordinal,
            status=ReflectionAttemptStatus(row.status),
            reason_code=row.reason_code,
            provider=row.provider,
            model=row.model,
            formation_method=row.formation_method,
            started_at=row.started_at,
            finished_at=row.finished_at,
            metrics=dict(row.metrics),
        )

    @staticmethod
    def _map_proposal(row: ReflectionProposalRow) -> ReflectionProposal:
        return ReflectionProposal(
            proposal_id=row.proposal_id,
            run_id=row.run_id,
            ordinal=row.ordinal,
            target_owner=ReflectionTargetOwner(row.target_owner),
            payload=dict(row.payload),
            evidence_source_ids=tuple(row.evidence_source_ids),
            created_at=row.created_at,
        )

    @staticmethod
    def _map_outcome(row: ReflectionOutcomeRow) -> ReflectionOutcome:
        return ReflectionOutcome(
            outcome_id=row.outcome_id,
            proposal_id=row.proposal_id,
            target_policy_version=row.target_policy_version,
            decision=ReflectionOutcomeDecision(row.decision),
            reason_code=row.reason_code,
            target_aggregate_type=row.target_aggregate_type,
            target_aggregate_id=row.target_aggregate_id,
            decided_at=row.decided_at,
        )


def _inclination_affective_signal(
    transition: AffectiveTransitionRow,
    *,
    source: ReflectionSourceRecord,
) -> InclinationAffectiveSignal:
    payload = dict(transition.appraisal_payload)
    signal_hash = affective_signal_hash(
        transition_id=transition.transition_id,
        identity_id=transition.identity_id,
        interaction_id=transition.interaction_id,
        source_message_id=transition.source_message_id,
        resulting_state_version=transition.resulting_state_version,
        source=source,
        appraisal_schema_version=transition.appraisal_schema_version,
        appraisal_payload=payload,
        appraisal_confidence=transition.appraisal_confidence,
        applied_delta=dict(transition.applied_delta),
    )
    return InclinationAffectiveSignal(
        transition_id=transition.transition_id,
        resulting_state_version=transition.resulting_state_version,
        signal_hash=signal_hash,
        pleasantness=_float_field(payload, "pleasantness"),
        novelty=_float_field(payload, "novelty"),
        salience=_float_field(payload, "salience"),
        curiosity_signal=_float_field(payload, "curiosity_signal"),
        interest_signal=_float_field(payload, "interest_signal"),
        concern_signal=_float_field(payload, "concern_signal"),
        frustration_signal=_float_field(payload, "frustration_signal"),
        appraisal_confidence=float(transition.appraisal_confidence),
    )


def _float_field(payload: dict[str, object], field_name: str) -> float:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"affective transition {field_name} is invalid")
    return float(value)
