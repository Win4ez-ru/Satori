"""SQLAlchemy adapter for canonical evidence-grounded semantic memory."""

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.core.semantic import SemanticClaimKind, SemanticScalar, SemanticValueKind
from satori.domain.memory import (
    EpisodicMemory,
    EpisodicMemoryEvidence,
    MemoryLifecycleStatus,
    MemoryProvenanceKind,
)
from satori.domain.semantic_memory import (
    SEMANTIC_FORMATION_VERSION,
    SemanticClaim,
    SemanticClaimRevision,
    SemanticClaimStatus,
    SemanticDecisionKind,
    SemanticEvidence,
    SemanticEvidenceSourceKind,
    SemanticFormationDecision,
    SemanticFormationPlan,
    SemanticRevisionKind,
)
from satori.infrastructure.persistence.models.initial_self import AuditEventRow
from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow, MemoryEvidenceRow
from satori.infrastructure.persistence.models.semantic import (
    SemanticClaimEvidenceRow,
    SemanticClaimRevisionRow,
    SemanticClaimRow,
    SemanticFormationDecisionRow,
)


class SQLAlchemySemanticMemoryRepository:
    """Keep semantic policy persistence behind the application-owned port."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_decision(self, idempotency_key: str) -> SemanticFormationDecision | None:
        row = self._session.execute(
            select(SemanticFormationDecisionRow).where(
                SemanticFormationDecisionRow.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        return self._map_decision(row) if row is not None else None

    def get_source_memories(
        self, source_memory_id: str, *, limit: int
    ) -> tuple[EpisodicMemory, ...]:
        if limit < 1:
            raise ValueError("semantic source memory limit must be positive")
        source = self._session.get(EpisodicMemoryRow, source_memory_id)
        if source is None or source.lifecycle_status != "active":
            return ()
        rows = tuple(
            self._session.execute(
                select(EpisodicMemoryRow)
                .where(
                    EpisodicMemoryRow.lifecycle_status == "active",
                    EpisodicMemoryRow.occurred_at <= source.occurred_at,
                )
                .order_by(EpisodicMemoryRow.occurred_at.desc(), EpisodicMemoryRow.memory_id)
                .limit(limit)
            ).scalars()
        )
        ordered = (source, *(row for row in rows if row.memory_id != source_memory_id))[:limit]
        return tuple(self._map_memory(row) for row in ordered)

    def list_claims(
        self, *, active_only: bool = False, predicate: str | None = None
    ) -> tuple[SemanticClaim, ...]:
        query = select(SemanticClaimRow)
        if active_only:
            query = query.where(SemanticClaimRow.status == SemanticClaimStatus.ACTIVE.value)
        if predicate is not None:
            query = query.where(SemanticClaimRow.predicate == predicate)
        rows = tuple(
            self._session.execute(
                query.order_by(
                    SemanticClaimRow.predicate,
                    SemanticClaimRow.valid_from,
                    SemanticClaimRow.claim_id,
                )
            ).scalars()
        )
        return tuple(self._map_claim(row) for row in rows)

    def get_claim(self, claim_id: str) -> SemanticClaim | None:
        row = self._session.get(SemanticClaimRow, claim_id)
        return self._map_claim(row) if row is not None else None

    def list_revisions(self, claim_id: str) -> tuple[SemanticClaimRevision, ...]:
        rows = tuple(
            self._session.execute(
                select(SemanticClaimRevisionRow)
                .where(SemanticClaimRevisionRow.claim_id == claim_id)
                .order_by(SemanticClaimRevisionRow.claim_version)
            ).scalars()
        )
        return tuple(self._map_revision(row) for row in rows)

    def list_unprocessed_memory_ids(self, *, limit: int) -> tuple[str, ...]:
        processed = exists().where(
            SemanticFormationDecisionRow.source_memory_id == EpisodicMemoryRow.memory_id,
            SemanticFormationDecisionRow.formation_version == SEMANTIC_FORMATION_VERSION,
        )
        return tuple(
            self._session.execute(
                select(EpisodicMemoryRow.memory_id)
                .where(EpisodicMemoryRow.lifecycle_status == "active", ~processed)
                .order_by(EpisodicMemoryRow.occurred_at, EpisodicMemoryRow.memory_id)
                .limit(limit)
            ).scalars()
        )

    def record_decision(
        self,
        decision: SemanticFormationDecision,
        plan: SemanticFormationPlan,
        *,
        audit_event_id: str,
    ) -> bool:
        statement = (
            sqlite_insert(SemanticFormationDecisionRow)
            .values(
                decision_id=decision.decision_id,
                idempotency_key=decision.idempotency_key,
                source_memory_id=decision.source_memory_id,
                formation_version=decision.formation_version,
                policy_version=decision.policy_version,
                kind=decision.kind.value,
                reason_code=decision.reason_code,
                created_count=decision.created_count,
                merged_count=decision.merged_count,
                superseded_count=decision.superseded_count,
                disputed_count=decision.disputed_count,
                rejected_count=decision.rejected_count,
                claim_ids=list(decision.claim_ids),
                decided_at=decision.decided_at,
                trace_id=decision.trace_id,
                formation_method=decision.formation_method,
                provider=decision.provider,
                model=decision.model,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(SemanticFormationDecisionRow.decision_id)
        )
        if self._session.execute(statement).scalar_one_or_none() is None:
            return False

        new_claims: list[SemanticClaim] = []
        changed_claims: list[tuple[SemanticClaim, int]] = []
        for claim in plan.claims:
            current = self._session.get(SemanticClaimRow, claim.claim_id)
            if current is None:
                new_claims.append(claim)
            else:
                changed_claims.append((claim, current.aggregate_version))

        for claim in new_claims:
            self._session.add(self._claim_row(claim))
        self._session.flush()
        for claim, expected_version in changed_claims:
            if claim.aggregate_version != expected_version + 1:
                raise RuntimeError("semantic aggregate version is not monotonic")
            result = self._session.execute(
                update(SemanticClaimRow)
                .where(
                    SemanticClaimRow.claim_id == claim.claim_id,
                    SemanticClaimRow.aggregate_version == expected_version,
                )
                .values(
                    aggregate_version=claim.aggregate_version,
                    confidence=claim.confidence,
                    status=claim.status.value,
                    valid_until=claim.valid_until,
                    superseded_by_claim_id=claim.superseded_by_claim_id,
                    updated_at=claim.updated_at,
                )
            )
            if getattr(result, "rowcount", None) != 1:
                raise RuntimeError("semantic aggregate was concurrently modified")
        self._session.flush()

        for claim in plan.claims:
            known = set(
                self._session.execute(
                    select(SemanticClaimEvidenceRow.root_message_id).where(
                        SemanticClaimEvidenceRow.claim_id == claim.claim_id
                    )
                ).scalars()
            )
            self._session.add_all(
                self._evidence_row(evidence)
                for evidence in claim.evidence
                if evidence.root_message_id not in known
            )
        self._session.add_all(self._revision_row(revision) for revision in plan.revisions)
        self._session.add(
            AuditEventRow(
                event_id=audit_event_id,
                schema_version=1,
                event_type=f"memory.semantic_{decision.kind.value}",
                aggregate_type="semantic_memory",
                aggregate_id=decision.source_memory_id,
                occurred_at=decision.decided_at,
                trace_id=decision.trace_id,
                details={
                    "decision_id": decision.decision_id,
                    "source_memory_id": decision.source_memory_id,
                    "formation_version": decision.formation_version,
                    "policy_version": decision.policy_version,
                    "reason_code": decision.reason_code,
                    "claim_ids": list(decision.claim_ids),
                    "created_count": decision.created_count,
                    "merged_count": decision.merged_count,
                    "superseded_count": decision.superseded_count,
                    "disputed_count": decision.disputed_count,
                    "rejected_count": decision.rejected_count,
                },
            )
        )
        return True

    @staticmethod
    def _claim_row(claim: SemanticClaim) -> SemanticClaimRow:
        return SemanticClaimRow(
            claim_id=claim.claim_id,
            claim_key=claim.claim_key,
            schema_version=claim.schema_version,
            aggregate_version=claim.aggregate_version,
            subject=claim.subject,
            predicate=claim.predicate,
            value_kind=claim.value_kind.value,
            value=claim.value,
            normalized_value=claim.normalized_value,
            polarity=claim.polarity,
            claim_kind=claim.claim_kind.value,
            confidence=claim.confidence,
            status=claim.status.value,
            valid_from=claim.valid_from,
            valid_until=claim.valid_until,
            superseded_by_claim_id=claim.superseded_by_claim_id,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
            formation_method=claim.formation_method,
            formation_version=claim.formation_version,
            normalization_version=claim.normalization_version,
        )

    @staticmethod
    def _evidence_row(evidence: SemanticEvidence) -> SemanticClaimEvidenceRow:
        return SemanticClaimEvidenceRow(
            semantic_evidence_id=evidence.semantic_evidence_id,
            claim_id=evidence.claim_id,
            memory_id=evidence.memory_id,
            memory_evidence_id=evidence.memory_evidence_id,
            root_message_id=evidence.root_message_id,
            root_interaction_id=evidence.root_interaction_id,
            source_kind=evidence.source_kind.value,
            observed_at=evidence.observed_at,
        )

    @staticmethod
    def _revision_row(revision: SemanticClaimRevision) -> SemanticClaimRevisionRow:
        return SemanticClaimRevisionRow(
            revision_id=revision.revision_id,
            claim_id=revision.claim_id,
            claim_version=revision.claim_version,
            decision_id=revision.decision_id,
            kind=revision.kind.value,
            prior_status=revision.prior_status.value if revision.prior_status else None,
            new_status=revision.new_status.value,
            prior_confidence=revision.prior_confidence,
            new_confidence=revision.new_confidence,
            reason_code=revision.reason_code,
            occurred_at=revision.occurred_at,
        )

    def _map_claim(self, row: SemanticClaimRow) -> SemanticClaim:
        evidence_rows = tuple(
            self._session.execute(
                select(SemanticClaimEvidenceRow)
                .where(SemanticClaimEvidenceRow.claim_id == row.claim_id)
                .order_by(
                    SemanticClaimEvidenceRow.observed_at,
                    SemanticClaimEvidenceRow.semantic_evidence_id,
                )
            ).scalars()
        )
        value = row.value
        if not isinstance(value, (str, int, float, bool)):
            raise RuntimeError("persisted semantic value has an unsupported type")
        semantic_value: SemanticScalar = value
        return SemanticClaim(
            claim_id=row.claim_id,
            claim_key=row.claim_key,
            schema_version=row.schema_version,
            aggregate_version=row.aggregate_version,
            subject=row.subject,
            predicate=row.predicate,
            value_kind=SemanticValueKind(row.value_kind),
            value=semantic_value,
            normalized_value=row.normalized_value,
            polarity=row.polarity,
            claim_kind=SemanticClaimKind(row.claim_kind),
            confidence=row.confidence,
            status=SemanticClaimStatus(row.status),
            valid_from=row.valid_from,
            valid_until=row.valid_until,
            superseded_by_claim_id=row.superseded_by_claim_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            formation_method=row.formation_method,
            formation_version=row.formation_version,
            normalization_version=row.normalization_version,
            evidence=tuple(
                SemanticEvidence(
                    semantic_evidence_id=evidence.semantic_evidence_id,
                    claim_id=evidence.claim_id,
                    memory_id=evidence.memory_id,
                    memory_evidence_id=evidence.memory_evidence_id,
                    root_message_id=evidence.root_message_id,
                    root_interaction_id=evidence.root_interaction_id,
                    source_kind=SemanticEvidenceSourceKind(evidence.source_kind),
                    observed_at=evidence.observed_at,
                )
                for evidence in evidence_rows
            ),
        )

    @staticmethod
    def _map_revision(row: SemanticClaimRevisionRow) -> SemanticClaimRevision:
        return SemanticClaimRevision(
            revision_id=row.revision_id,
            claim_id=row.claim_id,
            claim_version=row.claim_version,
            decision_id=row.decision_id,
            kind=SemanticRevisionKind(row.kind),
            prior_status=SemanticClaimStatus(row.prior_status) if row.prior_status else None,
            new_status=SemanticClaimStatus(row.new_status),
            prior_confidence=row.prior_confidence,
            new_confidence=row.new_confidence,
            reason_code=row.reason_code,
            occurred_at=row.occurred_at,
        )

    @staticmethod
    def _map_decision(row: SemanticFormationDecisionRow) -> SemanticFormationDecision:
        return SemanticFormationDecision(
            decision_id=row.decision_id,
            idempotency_key=row.idempotency_key,
            source_memory_id=row.source_memory_id,
            formation_version=row.formation_version,
            policy_version=row.policy_version,
            kind=SemanticDecisionKind(row.kind),
            reason_code=row.reason_code,
            created_count=row.created_count,
            merged_count=row.merged_count,
            superseded_count=row.superseded_count,
            disputed_count=row.disputed_count,
            rejected_count=row.rejected_count,
            claim_ids=tuple(row.claim_ids),
            decided_at=row.decided_at,
            trace_id=row.trace_id,
            formation_method=row.formation_method,
            provider=row.provider,
            model=row.model,
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
