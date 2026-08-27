"""SQLAlchemy adapter for episodic memories, evidence, decisions, and audit."""

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.domain.memory import (
    EpisodeDecisionKind,
    EpisodeFormationDecision,
    EpisodicMemory,
    EpisodicMemoryEvidence,
    MemoryLifecycleStatus,
    MemoryProvenanceKind,
)
from satori.infrastructure.persistence.models.initial_self import AuditEventRow
from satori.infrastructure.persistence.models.memory import (
    EpisodeFormationDecisionRow,
    EpisodicMemoryRow,
    MemoryEvidenceRow,
)


class SQLAlchemyEpisodicMemoryRepository:
    """Keep MemoryManager persistence and ORM details behind one application port."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_decision(self, idempotency_key: str) -> EpisodeFormationDecision | None:
        row = self._session.execute(
            select(EpisodeFormationDecisionRow).where(
                EpisodeFormationDecisionRow.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        return self._map_decision(row) if row is not None else None

    def record_decision(
        self,
        decision: EpisodeFormationDecision,
        *,
        audit_event_id: str,
    ) -> bool:
        memory = decision.memory
        if memory is not None:
            self._session.add(
                EpisodicMemoryRow(
                    memory_id=memory.memory_id,
                    schema_version=memory.schema_version,
                    source_interaction_id=memory.source_interaction_id,
                    occurred_at=memory.occurred_at,
                    summary=memory.summary,
                    importance=memory.importance,
                    confidence=memory.confidence,
                    created_at=memory.created_at,
                    formation_method=memory.formation_method,
                    formation_version=memory.formation_version,
                    lifecycle_status=memory.lifecycle_status.value,
                )
            )
            self._session.flush()
            self._session.add_all(
                MemoryEvidenceRow(
                    evidence_id=evidence.evidence_id,
                    memory_id=evidence.memory_id,
                    source_message_id=evidence.source_message_id,
                    provenance_kind=evidence.provenance_kind.value,
                    quote=evidence.quote,
                    observed_at=evidence.observed_at,
                )
                for evidence in memory.evidence
            )
            self._session.flush()

        statement = (
            sqlite_insert(EpisodeFormationDecisionRow)
            .values(
                decision_id=decision.decision_id,
                idempotency_key=decision.idempotency_key,
                source_interaction_id=decision.source_interaction_id,
                formation_version=decision.formation_version,
                policy_version=decision.policy_version,
                kind=decision.kind.value,
                reason_code=decision.reason_code,
                decided_at=decision.decided_at,
                trace_id=decision.trace_id,
                formation_method=decision.formation_method,
                provider=decision.provider,
                model=decision.model,
                memory_id=memory.memory_id if memory is not None else None,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(EpisodeFormationDecisionRow.decision_id)
        )
        if self._session.execute(statement).scalar_one_or_none() is None:
            return False

        aggregate_type = "memory" if memory is not None else "interaction"
        aggregate_id = memory.memory_id if memory is not None else decision.source_interaction_id
        event_type = f"memory.episode_{decision.kind.value}"
        details: dict[str, object] = {
            "source_interaction_id": decision.source_interaction_id,
            "formation_version": decision.formation_version,
            "policy_version": decision.policy_version,
            "decision_id": decision.decision_id,
            "reason_code": decision.reason_code,
        }
        if memory is not None:
            details["evidence_ids"] = [item.evidence_id for item in memory.evidence]
            details["source_message_ids"] = [item.source_message_id for item in memory.evidence]
        self._session.add(
            AuditEventRow(
                event_id=audit_event_id,
                schema_version=1,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                occurred_at=decision.decided_at,
                trace_id=decision.trace_id,
                details=details,
            )
        )
        return True

    def list_memories(self, *, interaction_id: str | None = None) -> tuple[EpisodicMemory, ...]:
        query = select(EpisodicMemoryRow)
        if interaction_id is not None:
            query = query.where(EpisodicMemoryRow.source_interaction_id == interaction_id)
        rows = tuple(
            self._session.execute(
                query.order_by(EpisodicMemoryRow.occurred_at, EpisodicMemoryRow.memory_id)
            ).scalars()
        )
        return tuple(self._map_memory(row) for row in rows)

    def _map_decision(self, row: EpisodeFormationDecisionRow) -> EpisodeFormationDecision:
        memory = None
        if row.memory_id is not None:
            memory_row = self._session.get(EpisodicMemoryRow, row.memory_id)
            if memory_row is None:
                raise RuntimeError("episode decision references missing memory")
            memory = self._map_memory(memory_row)
        return EpisodeFormationDecision(
            decision_id=row.decision_id,
            idempotency_key=row.idempotency_key,
            source_interaction_id=row.source_interaction_id,
            formation_version=row.formation_version,
            policy_version=row.policy_version,
            kind=EpisodeDecisionKind(row.kind),
            reason_code=row.reason_code,
            decided_at=row.decided_at,
            trace_id=row.trace_id,
            formation_method=row.formation_method,
            provider=row.provider,
            model=row.model,
            memory=memory,
        )

    def _map_memory(self, row: EpisodicMemoryRow) -> EpisodicMemory:
        evidence_rows = tuple(
            self._session.execute(
                select(MemoryEvidenceRow)
                .where(MemoryEvidenceRow.memory_id == row.memory_id)
                .order_by(MemoryEvidenceRow.evidence_id)
            ).scalars()
        )
        return EpisodicMemory(
            memory_id=row.memory_id,
            schema_version=row.schema_version,
            source_interaction_id=row.source_interaction_id,
            occurred_at=row.occurred_at,
            summary=row.summary,
            importance=row.importance,
            confidence=row.confidence,
            created_at=row.created_at,
            formation_method=row.formation_method,
            formation_version=row.formation_version,
            lifecycle_status=MemoryLifecycleStatus(row.lifecycle_status),
            evidence=tuple(
                EpisodicMemoryEvidence(
                    evidence_id=evidence.evidence_id,
                    memory_id=evidence.memory_id,
                    source_message_id=evidence.source_message_id,
                    provenance_kind=MemoryProvenanceKind(evidence.provenance_kind),
                    quote=evidence.quote,
                    observed_at=evidence.observed_at,
                )
                for evidence in evidence_rows
            ),
        )
