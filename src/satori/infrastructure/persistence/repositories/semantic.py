"""SQLAlchemy adapter for canonical evidence-grounded semantic memory."""

from datetime import datetime

from sqlalchemy import and_, exists, literal, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from satori.core.semantic import SemanticClaimKind, SemanticScalar, SemanticValueKind
from satori.domain.audit_metadata import AuditMetadataFamily
from satori.domain.erasure_knowledge import (
    ErasedKnowledgeHeader,
    KnowledgeBodyKind,
    KnowledgeBodyTarget,
)
from satori.domain.memory import (
    EpisodicMemory,
    EpisodicMemoryEvidence,
    MemoryLifecycleStatus,
    MemoryProvenanceKind,
)
from satori.domain.memory_scope import MemoryScope
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
    SemanticMemoryManager,
    SemanticRevisionKind,
    normalize_semantic_value,
    semantic_record_key,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.audit_metadata_writer import bind_metadata
from satori.infrastructure.persistence.memory_scope import (
    admitted_episode_predicate,
    admitted_semantic_claim_predicate,
    resolve_memory_scope,
    resolve_proven_memory_scope,
)
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.initial_self import AuditEventRow
from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow, MemoryEvidenceRow
from satori.infrastructure.persistence.models.semantic import (
    SemanticClaimEvidenceRow,
    SemanticClaimRevisionRow,
    SemanticClaimRow,
    SemanticFormationDecisionRow,
)
from satori.infrastructure.persistence.models.source_policy import SourcePayloadRow
from satori.infrastructure.persistence.repositories.knowledge_bodies import (
    available_owner_predicate,
    current_owner_row,
    erase_owned_knowledge_bodies,
    erased_knowledge_header,
    repair_owner_rows,
    require_available_knowledge_target,
)
from satori.infrastructure.persistence.source_policy import (
    copy_payload,
    payload_admission_predicate,
    policy_snapshot,
    record_owner_withdrawal,
    record_payload,
    require_owner_reconciled,
    require_source_inputs,
)


def _require_scope(scope: MemoryScope) -> None:
    if not isinstance(scope, MemoryScope):
        raise ValueError("memory_scope_required")


class SQLAlchemySemanticMemoryRepository:
    """Keep semantic policy persistence behind the application-owned port."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def erase_knowledge_bodies(
        self, targets: tuple[KnowledgeBodyTarget, ...], *, action_id: str
    ) -> None:
        erase_owned_knowledge_bodies(
            self._session,
            targets,
            action_id=action_id,
            allowed_kinds=frozenset((KnowledgeBodyKind.SEMANTIC_CLAIM,)),
        )

    def get_erased_knowledge_header(
        self, kind: KnowledgeBodyKind, aggregate_id: str
    ) -> ErasedKnowledgeHeader | None:
        return erased_knowledge_header(
            self._session,
            kind,
            aggregate_id,
            allowed_kinds=frozenset((KnowledgeBodyKind.SEMANTIC_CLAIM,)),
        )

    def get_decision(self, idempotency_key: str) -> SemanticFormationDecision | None:
        row = self._session.execute(
            select(SemanticFormationDecisionRow).where(
                SemanticFormationDecisionRow.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        return self._map_decision(row) if row is not None else None

    def resolve_source_scope(self, source_memory_id: str) -> MemoryScope | None:
        scope = resolve_memory_scope(self._session, source_memory_id)
        if scope is not None:
            require_owner_reconciled(self._session, "semantic_memory")
        return scope

    def get_source_memories(
        self, source_memory_id: str, *, scope: MemoryScope, limit: int
    ) -> tuple[EpisodicMemory, ...]:
        _require_scope(scope)
        if limit < 1:
            raise ValueError("semantic source memory limit must be positive")
        source = self._session.get(EpisodicMemoryRow, source_memory_id)
        if source is None or resolve_memory_scope(self._session, source_memory_id) != scope:
            return ()
        rows = tuple(
            self._session.execute(
                select(EpisodicMemoryRow)
                .where(
                    admitted_episode_predicate(self._session, scope),
                    EpisodicMemoryRow.occurred_at <= source.occurred_at,
                )
                .order_by(EpisodicMemoryRow.occurred_at.desc(), EpisodicMemoryRow.memory_id)
                .limit(limit)
            ).scalars()
        )
        ordered = (source, *(row for row in rows if row.memory_id != source_memory_id))[:limit]
        return tuple(self._map_memory(row) for row in ordered)

    def list_claims(
        self, *, scope: MemoryScope, active_only: bool = False, predicate: str | None = None
    ) -> tuple[SemanticClaim, ...]:
        _require_scope(scope)
        query = select(SemanticClaimRow).where(
            SemanticClaimRow.body_state == "available",
            SemanticClaimRow.body_action_id.is_(None),
            admitted_semantic_claim_predicate(self._session, scope),
        )
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
        mapped = tuple(self._map_claim(row) for row in rows)
        return tuple(claim for claim in mapped if self._has_scoped_key(claim, scope))

    def get_claim(self, claim_id: str, *, scope: MemoryScope) -> SemanticClaim | None:
        _require_scope(scope)
        row = self._session.execute(
            select(SemanticClaimRow).where(
                SemanticClaimRow.body_state == "available",
                SemanticClaimRow.body_action_id.is_(None),
                SemanticClaimRow.claim_id == claim_id,
                admitted_semantic_claim_predicate(self._session, scope),
            )
        ).scalar_one_or_none()
        claim = self._map_claim(row) if row is not None else None
        return claim if claim is not None and self._has_scoped_key(claim, scope) else None

    @staticmethod
    def _has_scoped_key(claim: SemanticClaim, scope: MemoryScope) -> bool:
        return claim.normalized_value == normalize_semantic_value(
            claim.value_kind, claim.value
        ) and claim.claim_key == semantic_record_key(claim, scope=scope)

    def list_revisions(self, claim_id: str) -> tuple[SemanticClaimRevision, ...]:
        rows = tuple(
            self._session.execute(
                select(SemanticClaimRevisionRow)
                .where(SemanticClaimRevisionRow.claim_id == claim_id)
                .order_by(SemanticClaimRevisionRow.claim_version)
            ).scalars()
        )
        return tuple(self._map_revision(row) for row in rows)

    def list_unprocessed_memory_ids(self, *, scope: MemoryScope, limit: int) -> tuple[str, ...]:
        _require_scope(scope)
        processed = exists().where(
            SemanticFormationDecisionRow.source_memory_id == EpisodicMemoryRow.memory_id,
            SemanticFormationDecisionRow.formation_version == SEMANTIC_FORMATION_VERSION,
        )
        return tuple(
            self._session.execute(
                select(EpisodicMemoryRow.memory_id)
                .where(admitted_episode_predicate(self._session, scope), ~processed)
                .order_by(EpisodicMemoryRow.occurred_at, EpisodicMemoryRow.memory_id)
                .limit(limit)
            ).scalars()
        )

    def record_decision(
        self,
        decision: SemanticFormationDecision,
        plan: SemanticFormationPlan,
        *,
        scope: MemoryScope,
        source_memories: tuple[EpisodicMemory, ...],
        audit_event_id: str,
        input_id: str | None = None,
    ) -> bool:
        require_owner_reconciled(self._session, "semantic_memory")
        require_source_inputs(self._session, input_id)
        source_ids = frozenset(memory.memory_id for memory in source_memories)
        plan_guards = self._validate_plan(decision.source_memory_id, plan, scope, source_ids)
        if decision.source_memory_id not in source_ids:
            raise ValueError("semantic_sources_changed")
        source_guards = tuple(
            exists(
                select(EpisodicMemoryRow.memory_id).where(
                    EpisodicMemoryRow.memory_id == memory.memory_id,
                    EpisodicMemoryRow.aggregate_version == memory.aggregate_version,
                    admitted_episode_predicate(self._session, scope),
                )
            )
            for memory in source_memories
        )
        values = {
            "decision_id": decision.decision_id,
            "idempotency_key": decision.idempotency_key,
            "source_memory_id": decision.source_memory_id,
            "formation_version": decision.formation_version,
            "policy_version": decision.policy_version,
            "kind": decision.kind.value,
            "reason_code": decision.reason_code,
            "created_count": decision.created_count,
            "merged_count": decision.merged_count,
            "superseded_count": decision.superseded_count,
            "disputed_count": decision.disputed_count,
            "rejected_count": decision.rejected_count,
            "claim_ids": list(decision.claim_ids),
            "decided_at": decision.decided_at,
            "trace_id": decision.trace_id,
            "formation_method": decision.formation_method,
            "provider": decision.provider,
            "model": decision.model,
        }
        table = SemanticFormationDecisionRow.__table__
        admitted = select(
            *(literal(value, type_=table.c[name].type) for name, value in values.items())
        ).where(*source_guards, *plan_guards)
        statement = (
            sqlite_insert(SemanticFormationDecisionRow)
            .from_select(list(values), admitted)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(SemanticFormationDecisionRow.decision_id)
        )
        if self._session.execute(statement).scalar_one_or_none() is None:
            if not self._session.scalar(select(and_(*source_guards))):
                raise ValueError("semantic_sources_changed")
            if self.get_decision(decision.idempotency_key) is not None:
                return False
            raise ValueError("semantic_sources_changed")

        new_claims: list[SemanticClaim] = []
        changed_claims: list[tuple[SemanticClaim, int]] = []
        for claim in plan.claims:
            require_available_knowledge_target(
                self._session, KnowledgeBodyKind.SEMANTIC_CLAIM, claim.claim_id
            )
            current = current_owner_row(self._session, SemanticClaimRow, claim.claim_id)
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
                    available_owner_predicate(self._session, SemanticClaimRow),
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
            bind_metadata(
                self._session,
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
                ),
                family=AuditMetadataFamily.SEMANTIC_FORMATION,
                identity_id=self._session.scalar(
                    select(ConversationSessionRow.identity_id)
                    .join(
                        ConversationInteractionRow,
                        ConversationInteractionRow.session_id == ConversationSessionRow.session_id,
                    )
                    .join(
                        EpisodicMemoryRow,
                        EpisodicMemoryRow.source_interaction_id
                        == ConversationInteractionRow.interaction_id,
                    )
                    .where(EpisodicMemoryRow.memory_id == decision.source_memory_id)
                )
                or "",
                owner_record_id=decision.decision_id,
                source_input_id=input_id,
            )
        )
        for claim in plan.claims:
            record_payload(
                self._session, "semantic_claim", claim.claim_id, claim.aggregate_version, input_id
            )
        return True

    def withdraw_source_support(
        self,
        *,
        excluded_message_ids: frozenset[str],
        request_id: str,
        policy_generation: int,
        now: datetime,
    ) -> int:
        """Repair semantic keys through their owner; retain exact historical payloads.

        This internal repair path runs under the policy owner's exclusive lease.
        It deliberately does not map these unsafe claims into ordinary read models.
        The complete actual-input dependency closure governs selection, including
        legacy unknown claims and roots omitted from an LLM's evidence proposal.
        """

        affected_root = exists(
            select(SemanticClaimEvidenceRow.semantic_evidence_id).where(
                SemanticClaimEvidenceRow.claim_id == SemanticClaimRow.claim_id,
                SemanticClaimEvidenceRow.root_message_id.in_(excluded_message_ids),
            )
        )
        snapshot = policy_snapshot(self._session)
        if snapshot is None:
            raise ValueError("semantic_withdrawal_requires_source_policy")
        evidence_owner = (
            select(SemanticClaimEvidenceRow.semantic_evidence_id)
            .join(
                ConversationInteractionRow,
                ConversationInteractionRow.interaction_id
                == SemanticClaimEvidenceRow.root_interaction_id,
            )
            .join(
                ConversationSessionRow,
                ConversationSessionRow.session_id == ConversationInteractionRow.session_id,
            )
            .where(
                SemanticClaimEvidenceRow.claim_id == SemanticClaimRow.claim_id,
            )
        )
        receipt_owner = select(SourcePayloadRow.payload_id).where(
            SourcePayloadRow.kind == "semantic_claim",
            SourcePayloadRow.payload_id == SemanticClaimRow.claim_id,
        )
        owns_claim = exists(
            evidence_owner.where(ConversationSessionRow.identity_id == snapshot.identity_id)
        ) | exists(receipt_owner.where(SourcePayloadRow.identity_id == snapshot.identity_id))
        unsafe = (
            ~payload_admission_predicate(
                self._session,
                "semantic_claim",
                SemanticClaimRow.claim_id,
                SemanticClaimRow.aggregate_version,
            )
            | affected_root
        )
        current_status = SemanticClaimRow.status.in_(
            (SemanticClaimStatus.ACTIVE.value, SemanticClaimStatus.DISPUTED.value)
        )
        if self._session.scalar(
            select(
                exists(
                    select(SemanticClaimRow.claim_id).where(
                        current_status, unsafe, ~exists(evidence_owner), ~exists(receipt_owner)
                    )
                )
            )
        ):
            raise SourcePolicyError("source_owner_attribution_unproven")
        rows = repair_owner_rows(
            self._session, SemanticClaimRow, current_status, owns_claim, unsafe
        )
        changed = 0
        manager = SemanticMemoryManager()
        for row in rows:
            prior = self._map_claim(row)
            plan = manager.withdraw_support(
                prior, request_id=request_id, policy_generation=policy_generation, now=now
            )
            if plan is None:
                continue
            current = plan.current_claim
            result = self._session.execute(
                update(SemanticClaimRow)
                .where(
                    SemanticClaimRow.claim_id == prior.claim_id,
                    SemanticClaimRow.aggregate_version == prior.aggregate_version,
                    SemanticClaimRow.status == prior.status.value,
                )
                .values(
                    aggregate_version=current.aggregate_version,
                    status=current.status.value,
                    valid_until=current.valid_until,
                    superseded_by_claim_id=None,
                    updated_at=current.updated_at,
                )
                .execution_options(synchronize_session=False)
            )
            if getattr(result, "rowcount", None) != 1:
                raise ValueError("semantic_withdrawal_version_conflict")
            copy_payload(
                self._session,
                "semantic_claim",
                prior.claim_id,
                prior.aggregate_version,
                current.aggregate_version,
                allow_withheld=True,
            )
            record_owner_withdrawal(
                self._session,
                owner="semantic_memory",
                aggregate_id=prior.claim_id,
                request_id=request_id,
                policy_generation=policy_generation,
                before_status=prior.status.value,
                after_status=current.status.value,
                before_version=prior.aggregate_version,
                after_version=current.aggregate_version,
                root_message_ids=tuple(
                    sorted({edge.root_message_id for edge in prior.evidence} & excluded_message_ids)
                ),
                now=now,
            )
            changed += 1
        self._session.expire_all()
        return changed

    def _validate_plan(
        self,
        source_memory_id: str,
        plan: SemanticFormationPlan,
        scope: MemoryScope,
        selected_source_ids: frozenset[str],
    ) -> tuple[ColumnElement[bool], ...]:
        """Revalidate namespace and every canonical evidence edge at the write boundary."""

        _require_scope(scope)
        if resolve_memory_scope(self._session, source_memory_id) != scope:
            raise ValueError("semantic_source_ineligible")
        guards: list[ColumnElement[bool]] = []
        for claim in plan.claims:
            if not claim.evidence or not self._has_scoped_key(claim, scope):
                raise ValueError("semantic_claim_scope_invalid")
            require_available_knowledge_target(
                self._session, KnowledgeBodyKind.SEMANTIC_CLAIM, claim.claim_id
            )
            current = current_owner_row(self._session, SemanticClaimRow, claim.claim_id)
            retained_edges: tuple[SemanticEvidence, ...] = ()
            if current is not None:
                admitted = self.get_claim(claim.claim_id, scope=scope)
                if admitted is None or admitted.claim_key != claim.claim_key:
                    raise ValueError("semantic_target_scope_invalid")
                retained_edges = admitted.evidence
                guards.append(
                    exists(
                        select(SemanticClaimRow.claim_id).where(
                            SemanticClaimRow.claim_id == claim.claim_id,
                            SemanticClaimRow.claim_key == claim.claim_key,
                            SemanticClaimRow.body_state == "available",
                            SemanticClaimRow.body_action_id.is_(None),
                            SemanticClaimRow.aggregate_version == current.aggregate_version,
                            admitted_semantic_claim_predicate(self._session, scope),
                        )
                    )
                )
            else:
                guards.append(
                    ~exists(
                        select(SemanticClaimRow.claim_id).where(
                            SemanticClaimRow.claim_id == claim.claim_id
                        )
                    )
                )
            for evidence in claim.evidence:
                if evidence not in retained_edges and evidence.memory_id not in selected_source_ids:
                    raise ValueError("semantic_evidence_not_selected")
                source_scope = (
                    resolve_proven_memory_scope(self._session, evidence.memory_id)
                    if evidence in retained_edges
                    else resolve_memory_scope(self._session, evidence.memory_id)
                )
                if source_scope != scope:
                    raise ValueError("semantic_evidence_scope_invalid")
                memory = self._session.get(EpisodicMemoryRow, evidence.memory_id)
                edge = self._session.get(MemoryEvidenceRow, evidence.memory_evidence_id)
                if (
                    memory is None
                    or edge is None
                    or edge.memory_id != memory.memory_id
                    or edge.source_message_id != evidence.root_message_id
                    or memory.source_interaction_id != evidence.root_interaction_id
                    or evidence.observed_at != memory.occurred_at
                    or evidence.claim_id != claim.claim_id
                ):
                    raise ValueError("semantic_evidence_lineage_invalid")
                guards.append(
                    self._evidence_guard(evidence, scope, retained=evidence in retained_edges)
                )
        return tuple(guards)

    def _evidence_guard(
        self, evidence: SemanticEvidence, scope: MemoryScope, *, retained: bool
    ) -> ColumnElement[bool]:
        """Bind late admission to the exact root, including retained edge ownership."""

        canonical = exists(
            select(MemoryEvidenceRow.evidence_id)
            .join(EpisodicMemoryRow, EpisodicMemoryRow.memory_id == MemoryEvidenceRow.memory_id)
            .where(
                MemoryEvidenceRow.evidence_id == evidence.memory_evidence_id,
                MemoryEvidenceRow.memory_id == evidence.memory_id,
                MemoryEvidenceRow.source_message_id == evidence.root_message_id,
                EpisodicMemoryRow.source_interaction_id == evidence.root_interaction_id,
                EpisodicMemoryRow.occurred_at == evidence.observed_at,
                admitted_episode_predicate(self._session, scope, include_archived=retained),
            )
        )
        if not retained:
            return canonical
        owned = exists(
            select(SemanticClaimEvidenceRow.semantic_evidence_id).where(
                SemanticClaimEvidenceRow.semantic_evidence_id == evidence.semantic_evidence_id,
                SemanticClaimEvidenceRow.claim_id == evidence.claim_id,
                SemanticClaimEvidenceRow.memory_id == evidence.memory_id,
                SemanticClaimEvidenceRow.memory_evidence_id == evidence.memory_evidence_id,
                SemanticClaimEvidenceRow.root_message_id == evidence.root_message_id,
                SemanticClaimEvidenceRow.root_interaction_id == evidence.root_interaction_id,
                SemanticClaimEvidenceRow.source_kind == evidence.source_kind.value,
                SemanticClaimEvidenceRow.observed_at == evidence.observed_at,
            )
        )
        return and_(canonical, owned)

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
        if (
            row.body_state != "available"
            or row.body_action_id is not None
            or row.normalized_value is None
        ):
            raise SourcePolicyError("knowledge_body_unavailable")
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
        if row.body_state != "available" or row.summary is None:
            raise SourcePolicyError("episode_body_unavailable")
        evidence_rows = tuple(
            self._session.execute(
                select(MemoryEvidenceRow)
                .where(MemoryEvidenceRow.memory_id == row.memory_id)
                .order_by(MemoryEvidenceRow.evidence_id)
            ).scalars()
        )
        available_evidence = []
        for evidence in evidence_rows:
            if evidence.body_state != "available" or evidence.quote is None:
                raise SourcePolicyError("episode_body_unavailable")
            available_evidence.append(
                EpisodicMemoryEvidence(
                    evidence_id=evidence.evidence_id,
                    memory_id=evidence.memory_id,
                    source_message_id=evidence.source_message_id,
                    provenance_kind=MemoryProvenanceKind(evidence.provenance_kind),
                    quote=evidence.quote,
                    observed_at=evidence.observed_at,
                )
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
            aggregate_version=row.aggregate_version,
            evidence=tuple(available_evidence),
        )
