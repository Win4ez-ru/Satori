"""SQLAlchemy adapter for episodic memories, evidence, decisions, and audit."""

from sqlalchemy import exists, func, literal, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.application.memory.contracts import MemoryScopeStatus
from satori.domain.audit_metadata import AuditMetadataFamily
from satori.domain.conversation_history import ConversationInteraction
from satori.domain.erasure_bodies import (
    MAX_BODY_TARGETS,
    BodyErasureAction,
    BodyErasureTarget,
    ErasedEpisodeHeader,
)
from satori.domain.memory import (
    EpisodeDecisionKind,
    EpisodeFormationDecision,
    EpisodicMemory,
    EpisodicMemoryEvidence,
    MemoryLifecycleStatus,
    MemoryProvenanceKind,
)
from satori.domain.memory_lifecycle import MemoryLifecycleDecision
from satori.domain.memory_scope import MemoryScope
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.audit_metadata_writer import bind_metadata
from satori.infrastructure.persistence.erasure_body_codec import decode_body_action
from satori.infrastructure.persistence.memory_scope import (
    admitted_episode_predicate,
    admitted_interaction_predicate,
    proven_episode_predicate,
)
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.erasure_bodies import ErasureBodyApplicationRow
from satori.infrastructure.persistence.models.initial_self import AuditEventRow
from satori.infrastructure.persistence.models.memory import (
    EpisodeFormationDecisionRow,
    EpisodicMemoryRow,
    MemoryEvidenceRow,
    MemoryLifecycleDecisionRow,
)
from satori.infrastructure.persistence.repositories.conversation import (
    SQLAlchemyConversationHistoryRepository,
)
from satori.infrastructure.persistence.source_policy import (
    copy_payload,
    eligible_payload,
    record_payload,
    require_source_inputs,
)


class SQLAlchemyEpisodicMemoryRepository:
    """Keep MemoryManager persistence and ORM details behind one application port."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_source_interaction(self, interaction_id: str) -> ConversationInteraction | None:
        admitted = self._session.execute(
            select(ConversationInteractionRow.interaction_id).where(
                ConversationInteractionRow.interaction_id == interaction_id,
                admitted_interaction_predicate(self._session, None),
            )
        ).scalar_one_or_none()
        if admitted is None:
            return None
        return SQLAlchemyConversationHistoryRepository(self._session).get_interaction(admitted)

    def scope_status(self, scope: MemoryScope) -> MemoryScopeStatus:
        verified_sessions = self._session.scalar(
            select(func.count())
            .select_from(ConversationSessionRow)
            .where(
                ConversationSessionRow.identity_id == scope.identity_id,
                ConversationSessionRow.counterparty_id == scope.counterparty_id,
                ConversationSessionRow.scope_verified.is_(True),
            )
        )
        unverified_sessions = self._session.scalar(
            select(func.count())
            .select_from(ConversationSessionRow)
            .where(
                ConversationSessionRow.identity_id == scope.identity_id,
                ConversationSessionRow.scope_verified.is_(False),
            )
        )
        episode_counts = (
            select(func.count())
            .select_from(EpisodicMemoryRow)
            .join(
                ConversationInteractionRow,
                ConversationInteractionRow.interaction_id
                == EpisodicMemoryRow.source_interaction_id,
            )
            .join(ConversationSessionRow)
            .where(ConversationSessionRow.identity_id == scope.identity_id)
        )
        eligible = self._session.scalar(
            episode_counts.where(admitted_episode_predicate(self._session, scope))
        )
        quarantined = self._session.scalar(episode_counts.where(~proven_episode_predicate(None)))
        return MemoryScopeStatus(
            identity_id=scope.identity_id,
            counterparty_id=scope.counterparty_id,
            verified_sessions_in_scope=int(verified_sessions or 0),
            unverified_sessions_for_identity=int(unverified_sessions or 0),
            eligible_episodes_in_scope=int(eligible or 0),
            quarantined_episodes_for_identity=int(quarantined or 0),
        )

    def get_decision(self, idempotency_key: str) -> EpisodeFormationDecision | None:
        row = self._session.execute(
            select(EpisodeFormationDecisionRow).where(
                EpisodeFormationDecisionRow.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        if row is None or self.get_source_interaction(row.source_interaction_id) is None:
            return None
        if row.memory_id is not None:
            memory = self._session.get(EpisodicMemoryRow, row.memory_id)
            if (
                memory is None
                or memory.body_state != "available"
                or not eligible_payload(
                    self._session, "episode", memory.memory_id, memory.aggregate_version
                )
            ):
                return None
        return self._map_decision(row)

    def get_erased_header(self, memory_id: str) -> ErasedEpisodeHeader | None:
        """Return terminal owner attribution without constructing an available body."""

        row = self._session.get(EpisodicMemoryRow, memory_id)
        if row is None or row.body_state != "erased":
            return None
        evidence = tuple(
            self._session.execute(
                select(MemoryEvidenceRow)
                .where(MemoryEvidenceRow.memory_id == memory_id)
                .order_by(MemoryEvidenceRow.evidence_id)
            ).scalars()
        )
        if (
            row.summary is not None
            or row.body_action_id is None
            or not evidence
            or any(
                edge.body_state != "erased"
                or edge.quote is not None
                or edge.body_action_id != row.body_action_id
                for edge in evidence
            )
            or self._session.get(ErasureBodyApplicationRow, row.body_action_id) is None
        ):
            raise SourcePolicyError("body_terminal_proof_invalid")
        return ErasedEpisodeHeader(
            memory_id=row.memory_id,
            aggregate_version=row.aggregate_version,
            action_id=row.body_action_id,
            evidence_ids=tuple(edge.evidence_id for edge in evidence),
        )

    def erase_bodies(self, targets: tuple[BodyErasureTarget, ...], *, action_id: str) -> None:
        """Apply an approved body transition; the caller owns the encompassing transaction."""

        if (
            not 1 <= len(targets) <= MAX_BODY_TARGETS
            or any(not isinstance(target, BodyErasureTarget) for target in targets)
            or len({target.memory_id for target in targets}) != len(targets)
        ):
            raise SourcePolicyError("body_target_invalid")
        action = self._session.get(ErasureBodyApplicationRow, action_id)
        if action is None:
            raise SourcePolicyError("body_action_missing")
        approved = self._require_action_targets(action, targets)
        for target in targets:
            row = self._session.get(EpisodicMemoryRow, target.memory_id)
            if (
                row is None
                or row.aggregate_version != target.aggregate_version
                or row.body_state != "available"
                or row.summary is None
                or row.body_action_id is not None
            ):
                raise SourcePolicyError("body_owner_state_changed")
            partition = self._session.execute(
                select(ConversationSessionRow.identity_id, ConversationSessionRow.counterparty_id)
                .join(
                    ConversationInteractionRow,
                    ConversationInteractionRow.session_id == ConversationSessionRow.session_id,
                )
                .where(
                    ConversationInteractionRow.interaction_id == row.source_interaction_id,
                    ConversationInteractionRow.status == "completed",
                    ConversationSessionRow.scope_verified.is_(True),
                )
            ).one_or_none()
            if partition is None or tuple(partition) != (
                approved.proposal.root.identity_id,
                approved.proposal.root.counterparty_id,
            ):
                raise SourcePolicyError("body_action_target_mismatch")
            evidence = tuple(
                self._session.execute(
                    select(MemoryEvidenceRow).where(MemoryEvidenceRow.memory_id == target.memory_id)
                ).scalars()
            )
            if not evidence or any(
                edge.body_state != "available"
                or edge.quote is None
                or edge.body_action_id is not None
                for edge in evidence
            ):
                raise SourcePolicyError("body_owner_state_changed")
        for target in targets:
            changed = self._session.execute(
                update(EpisodicMemoryRow)
                .where(
                    EpisodicMemoryRow.memory_id == target.memory_id,
                    EpisodicMemoryRow.aggregate_version == target.aggregate_version,
                    EpisodicMemoryRow.body_state == "available",
                    EpisodicMemoryRow.body_action_id.is_(None),
                )
                .values(summary=None, body_state="erased", body_action_id=action_id)
                .returning(EpisodicMemoryRow.memory_id)
            ).scalar_one_or_none()
            if changed is None:
                raise SourcePolicyError("body_owner_state_changed")
            self._session.execute(
                update(MemoryEvidenceRow)
                .where(MemoryEvidenceRow.memory_id == target.memory_id)
                .values(quote=None, body_state="erased", body_action_id=action_id)
            )
        self._session.expire_all()

    @staticmethod
    def _require_action_targets(
        action: ErasureBodyApplicationRow, targets: tuple[BodyErasureTarget, ...]
    ) -> BodyErasureAction:
        """Validate the persisted approved selectors before the first body write."""

        document = action.action
        if not isinstance(document, dict):
            raise SourcePolicyError("body_action_target_mismatch")
        try:
            approved = decode_body_action(document)
        except (ValueError, TypeError, KeyError):
            raise SourcePolicyError("body_action_target_mismatch") from None
        if (
            not isinstance(approved, BodyErasureAction)
            or approved.action_id != action.action_id
            or approved.floor_id != action.floor_id
            or approved.proposal.root.identity_id != action.identity_id
            or approved.proposal.root.message_id != action.root_message_id
            or approved.proposal.root.lineage_id != action.lineage_id
            or approved.proposal.request_id != action.request_id
            or approved.result_body_revision != action.body_revision
            or any(target not in approved.proposal.targets for target in targets)
        ):
            raise SourcePolicyError("body_action_target_mismatch")
        return approved

    def record_decision(
        self,
        decision: EpisodeFormationDecision,
        *,
        audit_event_id: str,
        input_id: str | None = None,
    ) -> bool:
        require_source_inputs(self._session, input_id)
        memory = decision.memory
        source_guard = exists(
            select(ConversationInteractionRow.interaction_id).where(
                ConversationInteractionRow.interaction_id == decision.source_interaction_id,
                admitted_interaction_predicate(self._session, None),
            )
        )
        if memory is not None:
            values = {
                "memory_id": memory.memory_id,
                "schema_version": memory.schema_version,
                "source_interaction_id": memory.source_interaction_id,
                "occurred_at": memory.occurred_at,
                "summary": memory.summary,
                "importance": memory.importance,
                "confidence": memory.confidence,
                "created_at": memory.created_at,
                "formation_method": memory.formation_method,
                "formation_version": memory.formation_version,
                "lifecycle_status": memory.lifecycle_status.value,
                "aggregate_version": memory.aggregate_version,
            }
            table = EpisodicMemoryRow.__table__
            inserted = self._session.execute(
                sqlite_insert(EpisodicMemoryRow)
                .from_select(
                    list(values),
                    select(
                        *(
                            literal(value, type_=table.c[name].type)
                            for name, value in values.items()
                        )
                    ).where(source_guard),
                )
                .returning(EpisodicMemoryRow.memory_id)
            )
            if inserted.scalar_one_or_none() is None:
                raise ValueError("episode_source_changed")
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

        values = {
            "decision_id": decision.decision_id,
            "idempotency_key": decision.idempotency_key,
            "source_interaction_id": decision.source_interaction_id,
            "formation_version": decision.formation_version,
            "policy_version": decision.policy_version,
            "kind": decision.kind.value,
            "reason_code": decision.reason_code,
            "decided_at": decision.decided_at,
            "trace_id": decision.trace_id,
            "formation_method": decision.formation_method,
            "provider": decision.provider,
            "model": decision.model,
            "memory_id": memory.memory_id if memory is not None else None,
        }
        table = EpisodeFormationDecisionRow.__table__
        statement = (
            sqlite_insert(EpisodeFormationDecisionRow)
            .from_select(
                list(values),
                select(
                    *(literal(value, type_=table.c[name].type) for name, value in values.items())
                ).where(source_guard),
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(EpisodeFormationDecisionRow.decision_id)
        )
        if self._session.execute(statement).scalar_one_or_none() is None:
            if not self._session.scalar(select(source_guard)):
                raise ValueError("episode_source_changed")
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
            bind_metadata(
                self._session,
                AuditEventRow(
                    event_id=audit_event_id,
                    schema_version=1,
                    event_type=event_type,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    occurred_at=decision.decided_at,
                    trace_id=decision.trace_id,
                    details=details,
                ),
                family=AuditMetadataFamily.EPISODE_FORMATION,
                identity_id=self._session.scalar(
                    select(ConversationSessionRow.identity_id)
                    .join(
                        ConversationInteractionRow,
                        ConversationInteractionRow.session_id == ConversationSessionRow.session_id,
                    )
                    .where(
                        ConversationInteractionRow.interaction_id == decision.source_interaction_id
                    )
                )
                or "",
                owner_record_id=decision.decision_id,
                source_input_id=input_id,
            )
        )
        if memory is not None:
            record_payload(
                self._session, "episode", memory.memory_id, memory.aggregate_version, input_id
            )
        return True

    def list_memories(
        self,
        *,
        scope: MemoryScope,
        interaction_id: str | None = None,
        include_archived: bool = False,
    ) -> tuple[EpisodicMemory, ...]:
        if not isinstance(scope, MemoryScope):
            raise ValueError("memory_scope_required")
        query = select(EpisodicMemoryRow).where(
            admitted_episode_predicate(self._session, scope, include_archived=include_archived)
        )
        if interaction_id is not None:
            query = query.where(EpisodicMemoryRow.source_interaction_id == interaction_id)
        rows = tuple(
            self._session.execute(
                query.order_by(EpisodicMemoryRow.occurred_at, EpisodicMemoryRow.memory_id)
            ).scalars()
        )
        return tuple(self._map_memory(row) for row in rows)

    def get_memory(
        self, memory_id: str, *, scope: MemoryScope, include_archived: bool = False
    ) -> EpisodicMemory | None:
        if not isinstance(scope, MemoryScope):
            raise ValueError("memory_scope_required")
        row = self._session.execute(
            select(EpisodicMemoryRow).where(
                EpisodicMemoryRow.memory_id == memory_id,
                admitted_episode_predicate(self._session, scope, include_archived=include_archived),
            )
        ).scalar_one_or_none()
        return self._map_memory(row) if row is not None else None

    def get_lifecycle_decision(self, request_id: str) -> MemoryLifecycleDecision | None:
        row = self._session.execute(
            select(MemoryLifecycleDecisionRow).where(
                MemoryLifecycleDecisionRow.request_id == request_id
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return MemoryLifecycleDecision(
            decision_id=row.decision_id,
            request_id=row.request_id,
            scope=MemoryScope(row.identity_id, row.counterparty_id),
            memory_id=row.memory_id,
            from_status=MemoryLifecycleStatus(row.from_status),
            to_status=MemoryLifecycleStatus(row.to_status),
            expected_version=row.expected_version,
            result_version=row.result_version,
            policy_version=row.policy_version,
            reason_code=row.reason_code,
            decided_at=row.decided_at,
            trace_id=row.trace_id,
            approved_by=row.approved_by,
            preview_digest=row.preview_digest,
        )

    def record_lifecycle_decision(
        self, decision: MemoryLifecycleDecision, *, audit_event_id: str
    ) -> bool:
        """Acquire the writer through one canonical scope/status/version guarded update."""

        changed = self._session.execute(
            update(EpisodicMemoryRow)
            .where(
                EpisodicMemoryRow.memory_id == decision.memory_id,
                EpisodicMemoryRow.aggregate_version == decision.expected_version,
                EpisodicMemoryRow.lifecycle_status == decision.from_status.value,
                admitted_episode_predicate(self._session, decision.scope, include_archived=True),
            )
            .values(
                lifecycle_status=decision.to_status.value,
                aggregate_version=decision.result_version,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(changed, "rowcount", None) != 1:
            self._session.rollback()
            if (
                self.get_memory(decision.memory_id, scope=decision.scope, include_archived=True)
                is None
            ):
                raise ValueError("memory_lifecycle_source_ineligible")
            if self.get_lifecycle_decision(decision.request_id) is not None:
                return False
            raise ValueError("memory_lifecycle_version_conflict")
        receipt = (
            sqlite_insert(MemoryLifecycleDecisionRow)
            .values(
                decision_id=decision.decision_id,
                request_id=decision.request_id,
                memory_id=decision.memory_id,
                identity_id=decision.scope.identity_id,
                counterparty_id=decision.scope.counterparty_id,
                from_status=decision.from_status.value,
                to_status=decision.to_status.value,
                expected_version=decision.expected_version,
                result_version=decision.result_version,
                policy_version=decision.policy_version,
                reason_code=decision.reason_code,
                decided_at=decision.decided_at,
                trace_id=decision.trace_id,
                approved_by=decision.approved_by,
                preview_digest=decision.preview_digest,
            )
            .on_conflict_do_nothing(index_elements=["request_id"])
            .returning(MemoryLifecycleDecisionRow.decision_id)
        )
        if self._session.execute(receipt).scalar_one_or_none() is None:
            self._session.rollback()
            return False
        self._session.add(
            bind_metadata(
                self._session,
                AuditEventRow(
                    event_id=audit_event_id,
                    schema_version=1,
                    event_type=f"memory.{decision.reason_code}",
                    aggregate_type="memory",
                    aggregate_id=decision.memory_id,
                    occurred_at=decision.decided_at,
                    trace_id=decision.trace_id,
                    details={
                        "decision_id": decision.decision_id,
                        "request_id": decision.request_id,
                        "identity_id": decision.scope.identity_id,
                        "counterparty_id": decision.scope.counterparty_id,
                        "from_status": decision.from_status.value,
                        "to_status": decision.to_status.value,
                        "expected_version": decision.expected_version,
                        "result_version": decision.result_version,
                        "policy_version": decision.policy_version,
                        "reason_code": decision.reason_code,
                        "approved_by": decision.approved_by,
                        "preview_digest": decision.preview_digest,
                    },
                ),
                family=AuditMetadataFamily.MEMORY_LIFECYCLE,
                identity_id=decision.scope.identity_id,
                owner_record_id=decision.decision_id,
            )
        )
        if decision.changed:
            copy_payload(
                self._session,
                "episode",
                decision.memory_id,
                decision.expected_version,
                decision.result_version,
            )
        self._session.expire_all()
        return True

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
        if row.body_state != "available" or row.summary is None:
            raise SourcePolicyError("episode_body_unavailable")
        evidence_rows = tuple(
            self._session.execute(
                select(MemoryEvidenceRow)
                .where(MemoryEvidenceRow.memory_id == row.memory_id)
                .order_by(MemoryEvidenceRow.evidence_id)
            ).scalars()
        )
        if any(edge.body_state != "available" or edge.quote is None for edge in evidence_rows):
            raise SourcePolicyError("episode_body_unavailable")
        evidence_items = []
        for evidence in evidence_rows:
            quote = evidence.quote
            if quote is None:
                raise SourcePolicyError("episode_body_unavailable")
            evidence_items.append(
                EpisodicMemoryEvidence(
                    evidence_id=evidence.evidence_id,
                    memory_id=evidence.memory_id,
                    source_message_id=evidence.source_message_id,
                    provenance_kind=MemoryProvenanceKind(evidence.provenance_kind),
                    quote=quote,
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
            evidence=tuple(evidence_items),
        )
