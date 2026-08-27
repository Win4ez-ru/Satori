"""SQLAlchemy owner repository for bounded Stage 14 personality evolution."""

import hashlib
import json
from datetime import datetime
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.application.personality.ports import (
    PersonalityCheckpointApprovalRecord,
    PersonalityCheckpointRecord,
    PersonalityEvidenceRecord,
    PersonalityEvolutionWrite,
    PersonalityRestoreEventRecord,
    PersonalityRestoreWrite,
    PersonalityRevisionRecord,
    PersonalityTraitDiff,
    ResolvedPersonalitySource,
)
from satori.core.personality import (
    CANONICAL_TRAIT_KEYS,
    PersonalityChangeProposal,
    PersonalityCitationRole,
    PersonalityTraitKey,
)
from satori.core.reflection import (
    ReflectionLineageKind,
    ReflectionPurpose,
    ReflectionSourceKind,
    ReflectionTargetOwner,
)
from satori.domain.errors import CorruptSatoriState
from satori.domain.personality import Personality, PersonalityTrait
from satori.domain.personality_evolution import (
    PERSONALITY_CHECKPOINT_HASH_SCHEMA_VERSION,
    PERSONALITY_EVOLUTION_POLICY_VERSION,
    PersonalityCheckpointKind,
    PersonalityCheckpointSnapshot,
    PersonalityDecisionKind,
    PersonalityEvidenceSource,
    PersonalityEvolutionRecord,
    checkpoint_hash,
    personality_content_signature,
    personality_drift_metrics,
    trait_distance,
)
from satori.domain.reflection import (
    REFLECTION_POLICY_VERSION_V3,
    REFLECTION_SCHEMA_VERSION_V3,
    ReflectionOutcome,
    ReflectionOutcomeDecision,
    ReflectionSourceRecord,
    source_set_hash,
)
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.initial_self import (
    AuditEventRow,
    PersonalityStateRow,
    PersonalityTraitRow,
)
from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow, MemoryEvidenceRow
from satori.infrastructure.persistence.models.personality import (
    PersonalityCheckpointApprovalRow,
    PersonalityCheckpointRow,
    PersonalityCheckpointTraitRow,
    PersonalityEvidenceRow,
    PersonalityRestoreEventRow,
    PersonalityRevisionRow,
)
from satori.infrastructure.persistence.models.positions import (
    InclinationEvidenceRow,
    PositionEvidenceRow,
    SatoriPositionRow,
)
from satori.infrastructure.persistence.models.reflection import (
    ReflectionOutcomeRow,
    ReflectionProposalRow,
    ReflectionRunRow,
    ReflectionSourceRow,
)


class SQLAlchemyPersonalityRepository:
    """Keep every post-activation personality write behind one transaction boundary."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_current(self, identity_id: str) -> Personality | None:
        state = self._session.get(PersonalityStateRow, identity_id)
        if state is None:
            return None
        rows = tuple(
            self._session.execute(
                select(PersonalityTraitRow)
                .where(PersonalityTraitRow.identity_id == identity_id)
                .order_by(PersonalityTraitRow.trait_key)
            ).scalars()
        )
        if tuple(item.trait_key for item in rows) != CANONICAL_TRAIT_KEYS:
            raise CorruptSatoriState("personality current trait vector is incomplete")
        try:
            return Personality(
                schema_version=state.schema_version,
                aggregate_version=state.aggregate_version,
                traits=tuple(
                    PersonalityTrait(
                        key=item.trait_key,
                        value=item.value,
                        baseline_value=item.baseline_value,
                    )
                    for item in rows
                ),
            )
        except (TypeError, ValueError) as error:
            raise CorruptSatoriState("personality current state violates invariants") from error

    def get_checkpoint(self, checkpoint_id: str) -> PersonalityCheckpointRecord | None:
        row = self._session.get(PersonalityCheckpointRow, checkpoint_id)
        return self._map_checkpoint(row) if row is not None else None

    def get_checkpoint_for_version(
        self, identity_id: str, aggregate_version: int
    ) -> PersonalityCheckpointRecord | None:
        row = (
            self._session.execute(
                select(PersonalityCheckpointRow)
                .where(
                    PersonalityCheckpointRow.identity_id == identity_id,
                    PersonalityCheckpointRow.source_aggregate_version == aggregate_version,
                )
                .order_by(
                    PersonalityCheckpointRow.created_at, PersonalityCheckpointRow.checkpoint_id
                )
            )
            .scalars()
            .first()
        )
        return self._map_checkpoint(row) if row is not None else None

    def get_activation_checkpoint(self, identity_id: str) -> PersonalityCheckpointRecord | None:
        rows = tuple(
            self._session.execute(
                select(PersonalityCheckpointRow).where(
                    PersonalityCheckpointRow.identity_id == identity_id,
                    PersonalityCheckpointRow.checkpoint_kind
                    == PersonalityCheckpointKind.ACTIVATION.value,
                )
            ).scalars()
        )
        if len(rows) > 1:
            raise CorruptSatoriState("personality has multiple activation checkpoints")
        return self._map_checkpoint(rows[0]) if rows else None

    def get_approved_checkpoint(self, identity_id: str) -> PersonalityCheckpointRecord | None:
        approval = (
            self._session.execute(
                select(PersonalityCheckpointApprovalRow)
                .where(PersonalityCheckpointApprovalRow.identity_id == identity_id)
                .order_by(
                    PersonalityCheckpointApprovalRow.approved_at.desc(),
                    PersonalityCheckpointApprovalRow.expected_aggregate_version.desc(),
                    PersonalityCheckpointApprovalRow.approval_id.desc(),
                )
            )
            .scalars()
            .first()
        )
        if approval is None:
            return self.get_activation_checkpoint(identity_id)
        checkpoint = self.get_checkpoint(approval.checkpoint_id)
        if (
            checkpoint is None
            or checkpoint.snapshot.identity_id != identity_id
            or checkpoint.snapshot.checkpoint_hash != approval.checkpoint_hash
            or checkpoint.snapshot.source_aggregate_version != approval.expected_aggregate_version
        ):
            raise CorruptSatoriState("approved personality checkpoint is missing or changed")
        return checkpoint

    def list_checkpoints(self, identity_id: str) -> tuple[PersonalityCheckpointRecord, ...]:
        rows = self._session.execute(
            select(PersonalityCheckpointRow)
            .where(PersonalityCheckpointRow.identity_id == identity_id)
            .order_by(
                PersonalityCheckpointRow.source_aggregate_version,
                PersonalityCheckpointRow.created_at,
                PersonalityCheckpointRow.checkpoint_id,
            )
        ).scalars()
        return tuple(self._map_checkpoint(item) for item in rows)

    def list_revisions(self, identity_id: str) -> tuple[PersonalityRevisionRecord, ...]:
        rows = self._session.execute(
            select(PersonalityRevisionRow)
            .where(PersonalityRevisionRow.identity_id == identity_id)
            .order_by(
                PersonalityRevisionRow.after_aggregate_version,
                PersonalityRevisionRow.revision_id,
            )
        ).scalars()
        return tuple(self._map_revision(item) for item in rows)

    def list_evidence(self, identity_id: str) -> tuple[PersonalityEvidenceRecord, ...]:
        rows = self._session.execute(
            select(PersonalityEvidenceRow)
            .where(PersonalityEvidenceRow.identity_id == identity_id)
            .order_by(PersonalityEvidenceRow.accepted_at, PersonalityEvidenceRow.evidence_id)
        ).scalars()
        return tuple(self._map_evidence(item) for item in rows)

    def list_checkpoint_approvals(
        self, identity_id: str
    ) -> tuple[PersonalityCheckpointApprovalRecord, ...]:
        rows = self._session.execute(
            select(PersonalityCheckpointApprovalRow)
            .where(PersonalityCheckpointApprovalRow.identity_id == identity_id)
            .order_by(
                PersonalityCheckpointApprovalRow.approved_at,
                PersonalityCheckpointApprovalRow.approval_id,
            )
        ).scalars()
        return tuple(self._map_approval(item) for item in rows)

    def list_restore_events(self, identity_id: str) -> tuple[PersonalityRestoreEventRecord, ...]:
        rows = self._session.execute(
            select(PersonalityRestoreEventRow)
            .where(PersonalityRestoreEventRow.identity_id == identity_id)
            .order_by(
                PersonalityRestoreEventRow.after_aggregate_version,
                PersonalityRestoreEventRow.restore_id,
            )
        ).scalars()
        return tuple(self._map_restore(item) for item in rows)

    def list_evolution_records(self, identity_id: str) -> tuple[PersonalityEvolutionRecord, ...]:
        rows = self._session.execute(
            select(PersonalityRevisionRow)
            .where(
                PersonalityRevisionRow.identity_id == identity_id,
                PersonalityRevisionRow.revision_kind == "evolution",
            )
            .order_by(PersonalityRevisionRow.occurred_at, PersonalityRevisionRow.revision_id)
        ).scalars()
        return tuple(
            PersonalityEvolutionRecord(
                identity_id=item.identity_id,
                trait_key=cast(PersonalityTraitKey, item.trait_key),
                applied_delta=cast(float, item.applied_delta),
                occurred_at=item.occurred_at,
                policy_version=item.policy_version,
            )
            for item in rows
        )

    def list_used_root_message_ids(self, identity_id: str) -> frozenset[str]:
        return frozenset(
            self._session.execute(
                select(PersonalityEvidenceRow.root_message_id).where(
                    PersonalityEvidenceRow.identity_id == identity_id
                )
            ).scalars()
        )

    def get_reflection_outcome(
        self, reflection_proposal_id: str, target_policy_version: int
    ) -> ReflectionOutcome | None:
        row = self._session.execute(
            select(ReflectionOutcomeRow).where(
                ReflectionOutcomeRow.proposal_id == reflection_proposal_id,
                ReflectionOutcomeRow.target_policy_version == target_policy_version,
            )
        ).scalar_one_or_none()
        return self._map_outcome(row) if row is not None else None

    def resolve_reflection_sources(
        self,
        *,
        identity_id: str,
        reflection_run_id: str,
        reflection_proposal_id: str,
        proposal: PersonalityChangeProposal,
    ) -> tuple[ResolvedPersonalitySource, ...]:
        """Re-resolve V3 provenance independently of generation-context assembly."""

        run = self._session.get(ReflectionRunRow, reflection_run_id)
        proposal_row = self._session.get(ReflectionProposalRow, reflection_proposal_id)
        if (
            run is None
            or run.identity_id != identity_id
            or run.schema_version != REFLECTION_SCHEMA_VERSION_V3
            or run.policy_version != REFLECTION_POLICY_VERSION_V3
            or run.purpose != ReflectionPurpose.PERSONALITY_EVOLUTION.value
            or run.status != "applying"
        ):
            raise ValueError("personality owner requires an exact V3 personality run")
        if (
            proposal_row is None
            or proposal_row.run_id != run.run_id
            or proposal_row.target_owner != ReflectionTargetOwner.PERSONALITY.value
        ):
            raise ValueError("personality proposal is absent from the target V3 run")
        stored_payload = dict(proposal_row.payload)
        stored_target = stored_payload.pop("target_owner", None)
        expected_payload = proposal.model_dump(mode="json")
        if (
            stored_target != ReflectionTargetOwner.PERSONALITY.value
            or stored_payload != expected_payload
            or tuple(proposal_row.evidence_source_ids)
            != tuple(item.source_id for item in proposal.citations)
        ):
            raise ValueError("typed personality proposal differs from the persisted V3 payload")
        rows = tuple(
            self._session.execute(
                select(ReflectionSourceRow)
                .where(ReflectionSourceRow.run_id == run.run_id)
                .order_by(ReflectionSourceRow.ordinal)
            ).scalars()
        )
        if not rows or tuple(item.ordinal for item in rows) != tuple(range(len(rows))):
            raise ValueError("personality run fixed source set is incomplete")
        persisted_records = tuple(
            ReflectionSourceRecord(
                source_id=item.source_id,
                run_id=item.run_id,
                ordinal=item.ordinal,
                kind=ReflectionSourceKind(item.kind),
                evidence_edge_id=item.evidence_edge_id,
                evidence_edge_version=item.evidence_edge_version,
                root_interaction_id=item.root_interaction_id,
                root_message_id=item.root_message_id,
                root_counterparty_id=item.root_counterparty_id,
                observed_at=item.observed_at,
                content_hash=item.content_hash,
                affective_transition_id=item.affective_transition_id,
                affective_state_version=item.affective_state_version,
                affective_signal_hash=item.affective_signal_hash,
                upstream_lineage_kind=(
                    ReflectionLineageKind(item.upstream_lineage_kind)
                    if item.upstream_lineage_kind is not None
                    else None
                ),
                upstream_lineage_id=item.upstream_lineage_id,
            )
            for item in rows
        )
        if (
            source_set_hash(
                persisted_records,
                schema_version=REFLECTION_SCHEMA_VERSION_V3,
                purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
            )
            != run.source_set_hash
        ):
            raise ValueError("personality run source-set hash no longer matches its fixed sources")
        source_ids = {item.source_id for item in rows}
        if not set(proposal_row.evidence_source_ids).issubset(source_ids):
            raise ValueError("personality proposal escaped its fixed source set")
        return tuple(self._resolve_source(run, item) for item in rows)

    def record_reflection_decision(
        self,
        outcome: ReflectionOutcome,
        evolution: PersonalityEvolutionWrite | None,
        *,
        identity_id: str,
        trace_id: str,
        audit_event_id: str,
    ) -> bool:
        """Atomically store the terminal outcome and optional owner-approved evolution."""

        accepted = outcome.decision is ReflectionOutcomeDecision.ACCEPTED
        if accepted != (evolution is not None):
            raise ValueError("personality outcome and evolution write disagree")
        if outcome.target_policy_version != PERSONALITY_EVOLUTION_POLICY_VERSION:
            raise ValueError("personality outcome targets an unsupported policy")
        if accepted:
            assert evolution is not None
            self._validate_evolution(outcome, evolution, identity_id=identity_id)
        elif outcome.target_aggregate_type is not None or outcome.target_aggregate_id is not None:
            raise ValueError("rejected personality outcome cannot target aggregate state")

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

        revision_id: str | None = None
        evidence_ids: list[str] = []
        aggregate_version: int | None = None
        if evolution is not None:
            revision_id, evidence_ids, aggregate_version = self._apply_evolution(
                outcome,
                evolution,
                identity_id=identity_id,
            )
        self._session.add(
            AuditEventRow(
                event_id=audit_event_id,
                schema_version=1,
                event_type=f"reflection.personality_{outcome.decision.value}",
                aggregate_type="personality",
                aggregate_id=identity_id,
                occurred_at=outcome.decided_at,
                trace_id=trace_id,
                details={
                    "outcome_id": outcome.outcome_id,
                    "proposal_id": outcome.proposal_id,
                    "target_policy_version": outcome.target_policy_version,
                    "reason_code": outcome.reason_code,
                    "revision_id": revision_id,
                    "aggregate_version": aggregate_version,
                    "evidence_ids": evidence_ids,
                    "evidence_count": len(evidence_ids),
                },
            )
        )
        return True

    def record_checkpoint_approval(
        self,
        approval: PersonalityCheckpointApprovalRecord,
        *,
        trace_id: str,
        audit_event_id: str,
    ) -> bool:
        current = self.get_current(approval.identity_id)
        checkpoint = self.get_checkpoint(approval.checkpoint_id)
        if (
            current is None
            or current.aggregate_version != approval.expected_aggregate_version
            or checkpoint is None
            or checkpoint.snapshot.identity_id != approval.identity_id
            or checkpoint.snapshot.checkpoint_hash != approval.checkpoint_hash
            or checkpoint.snapshot.source_aggregate_version != current.aggregate_version
            or checkpoint.snapshot.checkpoint_kind is PersonalityCheckpointKind.ACTIVATION
        ):
            raise RuntimeError("personality checkpoint approval target is stale or invalid")
        statement = (
            sqlite_insert(PersonalityCheckpointApprovalRow)
            .values(
                approval_id=approval.approval_id,
                identity_id=approval.identity_id,
                checkpoint_id=approval.checkpoint_id,
                checkpoint_hash=approval.checkpoint_hash,
                expected_aggregate_version=approval.expected_aggregate_version,
                reason=approval.reason,
                approved_at=approval.approved_at,
            )
            .on_conflict_do_nothing(index_elements=["checkpoint_id"])
            .returning(PersonalityCheckpointApprovalRow.approval_id)
        )
        if self._session.execute(statement).scalar_one_or_none() is None:
            return False
        self._session.add(
            AuditEventRow(
                event_id=audit_event_id,
                schema_version=1,
                event_type="personality.checkpoint_approved",
                aggregate_type="personality",
                aggregate_id=approval.identity_id,
                occurred_at=approval.approved_at,
                trace_id=trace_id,
                details={
                    "approval_id": approval.approval_id,
                    "checkpoint_id": approval.checkpoint_id,
                    "checkpoint_hash": approval.checkpoint_hash,
                    "aggregate_version": approval.expected_aggregate_version,
                },
            )
        )
        return True

    def record_restore(
        self,
        restore: PersonalityRestoreWrite,
        *,
        reason: str,
        restored_at: datetime,
        trace_id: str,
        audit_event_id: str,
    ) -> None:
        evaluation = restore.evaluation
        plan = evaluation.plan
        if evaluation.kind is not PersonalityDecisionKind.APPLIED or plan is None:
            raise ValueError("only an applied personality restore can be committed")
        before = restore.before_personality
        after = plan.personality
        identity_id = restore.source_checkpoint.identity_id
        if (
            restore.source_checkpoint.checkpoint_id != plan.checkpoint_id
            or restore.resulting_checkpoint.identity_id != identity_id
            or restore.resulting_checkpoint.checkpoint_kind is not PersonalityCheckpointKind.RESTORE
            or restore.resulting_checkpoint.source_aggregate_version != after.aggregate_version
            or restore.resulting_checkpoint.traits != after.traits
            or restore.approved_checkpoint.identity_id != identity_id
        ):
            raise ValueError("personality restore checkpoint lineage is inconsistent")
        self._validate_checkpoint(restore.source_checkpoint)
        self._validate_checkpoint(restore.resulting_checkpoint)
        persisted_source = self.get_checkpoint(restore.source_checkpoint.checkpoint_id)
        persisted_activation = self.get_activation_checkpoint(identity_id)
        persisted_approved = self.get_approved_checkpoint(identity_id)
        if (
            persisted_source is None
            or persisted_source.snapshot != restore.source_checkpoint
            or persisted_activation is None
            or persisted_approved is None
            or persisted_approved.snapshot != restore.approved_checkpoint
            or restore.prior_evolution != self.list_evolution_records(identity_id)
        ):
            raise RuntimeError("personality restore inputs changed before commit")
        verified_metrics = personality_drift_metrics(
            after,
            approved_checkpoint=restore.approved_checkpoint,
            history=restore.prior_evolution,
            target_trait=CANONICAL_TRAIT_KEYS[0],
            now=restored_at,
        )
        persisted_activation_distance = trait_distance(
            after,
            self._checkpoint_personality(persisted_activation.snapshot),
        )
        if (
            persisted_activation_distance != verified_metrics.activation
            or restore.activation_distance != persisted_activation_distance
            or restore.approved_checkpoint_distance != verified_metrics.approved_checkpoint
            or restore.rolling_total_path != verified_metrics.rolling_global_path
            or restore.lifetime_total_path != verified_metrics.lifetime_global_path
        ):
            raise ValueError("personality restore drift or path metrics were altered")
        current = self.get_current(identity_id)
        if current != before:
            raise RuntimeError("personality restore target was concurrently modified")
        self._ensure_prior_checkpoint(identity_id, before, created_at=restored_at)
        self._insert_checkpoint(restore.resulting_checkpoint, created_at=restored_at)
        self._optimistic_replace(identity_id, before, after)

        diffs = self._trait_diff_values(before, after)
        revision = PersonalityRevisionRow(
            revision_id=restore.revision_id,
            identity_id=identity_id,
            revision_kind="restore",
            before_aggregate_version=before.aggregate_version,
            after_aggregate_version=after.aggregate_version,
            trait_key=None,
            direction=None,
            before_value=None,
            after_value=None,
            applied_delta=None,
            decision_confidence=None,
            policy_version=PERSONALITY_EVOLUTION_POLICY_VERSION,
            reason_code=evaluation.reason_code,
            source_checkpoint_id=restore.source_checkpoint.checkpoint_id,
            resulting_checkpoint_id=restore.resulting_checkpoint.checkpoint_id,
            reflection_outcome_id=None,
            trait_diffs=self._trait_diff_payload(diffs),
            activation_distance_linf=restore.activation_distance.linf,
            activation_distance_l1=restore.activation_distance.l1,
            approved_checkpoint_distance_linf=restore.approved_checkpoint_distance.linf,
            approved_checkpoint_distance_l1=restore.approved_checkpoint_distance.l1,
            rolling_trait_path=None,
            rolling_total_path=restore.rolling_total_path,
            lifetime_trait_path=None,
            lifetime_total_path=restore.lifetime_total_path,
            occurred_at=restored_at,
        )
        self._session.add(revision)
        self._session.flush()
        self._session.add(
            PersonalityRestoreEventRow(
                restore_id=restore.restore_id,
                revision_id=restore.revision_id,
                identity_id=identity_id,
                source_checkpoint_id=restore.source_checkpoint.checkpoint_id,
                source_checkpoint_hash=restore.source_checkpoint.checkpoint_hash,
                resulting_checkpoint_id=restore.resulting_checkpoint.checkpoint_id,
                before_aggregate_version=before.aggregate_version,
                after_aggregate_version=after.aggregate_version,
                trait_diffs=self._trait_diff_payload(diffs),
                reason=reason,
                restored_at=restored_at,
            )
        )
        self._session.add(
            AuditEventRow(
                event_id=audit_event_id,
                schema_version=1,
                event_type="personality.checkpoint_restored",
                aggregate_type="personality",
                aggregate_id=identity_id,
                occurred_at=restored_at,
                trace_id=trace_id,
                details={
                    "restore_id": restore.restore_id,
                    "revision_id": restore.revision_id,
                    "source_checkpoint_id": restore.source_checkpoint.checkpoint_id,
                    "source_checkpoint_hash": restore.source_checkpoint.checkpoint_hash,
                    "resulting_checkpoint_id": restore.resulting_checkpoint.checkpoint_id,
                    "before_aggregate_version": before.aggregate_version,
                    "after_aggregate_version": after.aggregate_version,
                    "changed_trait_keys": [item.trait_key for item in diffs],
                },
            )
        )

    def _resolve_source(
        self, run: ReflectionRunRow, record: ReflectionSourceRow
    ) -> ResolvedPersonalitySource:
        if (
            record.affective_transition_id is not None
            or record.affective_state_version is not None
            or record.affective_signal_hash is not None
            or record.upstream_lineage_kind is None
            or record.upstream_lineage_id is None
        ):
            raise ValueError("personality source has invalid V3 lineage or affect attachment")
        message = self._session.get(ConversationMessageRow, record.root_message_id)
        interaction = self._session.get(ConversationInteractionRow, record.root_interaction_id)
        conversation_session = (
            self._session.get(ConversationSessionRow, interaction.session_id)
            if interaction is not None
            else None
        )
        if (
            message is None
            or interaction is None
            or conversation_session is None
            or message.role != "user"
            or message.interaction_id != interaction.interaction_id
            or interaction.status != "completed"
            or conversation_session.identity_id != run.identity_id
            or conversation_session.counterparty_id != record.root_counterparty_id
        ):
            raise ValueError("personality source canonical message lineage is invalid")

        if record.kind == ReflectionSourceKind.POSITION_EVIDENCE.value:
            edge = self._session.get(PositionEvidenceRow, record.evidence_edge_id)
            position = (
                self._session.get(SatoriPositionRow, edge.position_id) if edge is not None else None
            )
            if (
                edge is None
                or position is None
                or position.identity_id != run.identity_id
                or edge.source_message_id != message.message_id
                or edge.source_interaction_id != interaction.interaction_id
                or edge.source_counterparty_id != conversation_session.counterparty_id
                or record.upstream_lineage_kind != ReflectionLineageKind.POSITION.value
                or record.upstream_lineage_id != edge.position_id
            ):
                raise ValueError("personality position evidence lineage is invalid")
            quote = edge.quote
            observed_at = edge.observed_at
            lineage_kind = ReflectionLineageKind.POSITION
        elif record.kind == ReflectionSourceKind.EPISODIC_MEMORY_EVIDENCE.value:
            memory_edge = self._session.get(MemoryEvidenceRow, record.evidence_edge_id)
            memory = (
                self._session.get(EpisodicMemoryRow, memory_edge.memory_id)
                if memory_edge is not None
                else None
            )
            if (
                memory_edge is None
                or memory is None
                or memory_edge.source_message_id != message.message_id
                or memory.source_interaction_id != interaction.interaction_id
                or memory.lifecycle_status != "active"
                or memory.importance < 0.65
                or record.upstream_lineage_kind != ReflectionLineageKind.EPISODIC_MEMORY.value
                or record.upstream_lineage_id != memory.memory_id
            ):
                raise ValueError("personality episodic evidence lineage is invalid")
            quote = memory_edge.quote
            observed_at = memory_edge.observed_at
            lineage_kind = ReflectionLineageKind.EPISODIC_MEMORY
        else:
            raise ValueError("personality reflection source kind is unsupported")
        if (
            record.evidence_edge_version != 1
            or record.observed_at != observed_at
            or quote not in message.content
            or hashlib.sha256(quote.encode("utf-8")).hexdigest() != record.content_hash
        ):
            raise ValueError("personality source edge version, time, or content hash changed")
        inclination_used = (
            self._session.execute(
                select(InclinationEvidenceRow.evidence_id).where(
                    InclinationEvidenceRow.source_message_id == message.message_id
                )
            ).first()
            is not None
        )
        return ResolvedPersonalitySource(
            source=PersonalityEvidenceSource(
                source_id=record.source_id,
                identity_id=run.identity_id,
                evidence_edge_id=record.evidence_edge_id,
                root_message_id=message.message_id,
                root_interaction_id=interaction.interaction_id,
                root_session_id=conversation_session.session_id,
                root_counterparty_id=conversation_session.counterparty_id,
                lineage_id=record.upstream_lineage_id,
                observed_at=observed_at,
                quote=quote,
                content_hash=record.content_hash,
                canonical_user_message=True,
                interaction_completed=True,
                accepted_as_inclination_evidence=inclination_used,
            ),
            evidence_edge_version=record.evidence_edge_version,
            upstream_lineage_kind=lineage_kind,
        )

    def _validate_evolution(
        self,
        outcome: ReflectionOutcome,
        evolution: PersonalityEvolutionWrite,
        *,
        identity_id: str,
    ) -> None:
        evaluation = evolution.evaluation
        plan = evaluation.plan
        if evaluation.kind is not PersonalityDecisionKind.APPLIED or plan is None:
            raise ValueError("accepted outcome requires an applied personality evaluation")
        if outcome.reason_code != evaluation.reason_code:
            raise ValueError("personality outcome and owner reason disagree")
        if (
            outcome.target_aggregate_type != "personality"
            or outcome.target_aggregate_id != identity_id
            or evolution.reflection_proposal_id != outcome.proposal_id
            or evolution.source_checkpoint.identity_id != identity_id
            or evolution.resulting_checkpoint.identity_id != identity_id
            or evolution.resulting_checkpoint.checkpoint_kind
            is not PersonalityCheckpointKind.EVOLUTION
            or evolution.resulting_checkpoint.traits != plan.personality.traits
            or evolution.resulting_checkpoint.source_aggregate_version
            != plan.personality.aggregate_version
            or plan.personality.aggregate_version
            != evolution.before_personality.aggregate_version + 1
            or evolution.source_checkpoint.source_aggregate_version
            != evolution.before_personality.aggregate_version
            or evolution.source_checkpoint.traits != evolution.before_personality.traits
        ):
            raise ValueError("personality evolution lineage is inconsistent")
        self._validate_checkpoint(evolution.source_checkpoint)
        self._validate_checkpoint(evolution.resulting_checkpoint)
        accepted_ids = {item.source_id for item in plan.accepted_sources}
        proposal_row = self._session.get(ReflectionProposalRow, evolution.reflection_proposal_id)
        if proposal_row is None or proposal_row.run_id != evolution.reflection_run_id:
            raise ValueError("persisted personality proposal lineage is invalid")
        persisted_payload = dict(proposal_row.payload)
        persisted_payload.pop("target_owner", None)
        persisted_proposal = PersonalityChangeProposal.model_validate_json(
            json.dumps(persisted_payload, ensure_ascii=False, separators=(",", ":"))
        )
        canonical_sources = self.resolve_reflection_sources(
            identity_id=identity_id,
            reflection_run_id=evolution.reflection_run_id,
            reflection_proposal_id=evolution.reflection_proposal_id,
            proposal=persisted_proposal,
        )
        canonical_by_id = {item.source.source_id: item for item in canonical_sources}
        raw_citations = proposal_row.payload.get("citations")
        if not isinstance(raw_citations, list):
            raise ValueError("persisted personality citations are invalid")
        citation_roles: dict[str, PersonalityCitationRole] = {}
        for raw in raw_citations:
            if not isinstance(raw, dict):
                raise ValueError("persisted personality citation is invalid")
            source_id = raw.get("source_id")
            role = raw.get("role")
            if not isinstance(source_id, str) or not isinstance(role, str):
                raise ValueError("persisted personality citation fields are invalid")
            citation_roles[source_id] = PersonalityCitationRole(role)
        if (
            len(accepted_ids) != len(plan.accepted_sources)
            or {item.source.source.source_id for item in evolution.evidence} != accepted_ids
            or any(
                item.source.source
                != next(
                    source
                    for source in plan.accepted_sources
                    if source.source_id == item.source.source.source_id
                )
                for item in evolution.evidence
            )
            or any(
                canonical_by_id.get(item.source.source.source_id) != item.source
                for item in evolution.evidence
            )
        ):
            raise ValueError("personality evidence write differs from the owner plan")
        if set(citation_roles) != accepted_ids or any(
            item.citation_role is not citation_roles.get(item.source.source.source_id)
            or item.normalized_signature != personality_content_signature(item.source.source.quote)
            for item in evolution.evidence
        ):
            raise ValueError("personality evidence role or normalized signature was altered")

    def _apply_evolution(
        self,
        outcome: ReflectionOutcome,
        evolution: PersonalityEvolutionWrite,
        *,
        identity_id: str,
    ) -> tuple[str, list[str], int]:
        plan = evolution.evaluation.plan
        assert plan is not None
        current = self.get_current(identity_id)
        if current != evolution.before_personality:
            raise RuntimeError("personality evolution target was concurrently modified")
        self._insert_checkpoint(evolution.source_checkpoint, created_at=outcome.decided_at)
        self._insert_checkpoint(evolution.resulting_checkpoint, created_at=outcome.decided_at)
        self._optimistic_replace(identity_id, evolution.before_personality, plan.personality)

        before_value = evolution.before_personality.trait(plan.trait_key).value
        after_value = plan.personality.trait(plan.trait_key).value
        diffs = (PersonalityTraitDiff(plan.trait_key, before_value, after_value),)
        self._session.add(
            PersonalityRevisionRow(
                revision_id=evolution.revision_id,
                identity_id=identity_id,
                revision_kind="evolution",
                before_aggregate_version=evolution.before_personality.aggregate_version,
                after_aggregate_version=plan.personality.aggregate_version,
                trait_key=plan.trait_key,
                direction=plan.direction.value,
                before_value=before_value,
                after_value=after_value,
                applied_delta=plan.applied_delta,
                decision_confidence=plan.decision_confidence,
                policy_version=PERSONALITY_EVOLUTION_POLICY_VERSION,
                reason_code=evolution.evaluation.reason_code,
                source_checkpoint_id=evolution.source_checkpoint.checkpoint_id,
                resulting_checkpoint_id=evolution.resulting_checkpoint.checkpoint_id,
                reflection_outcome_id=outcome.outcome_id,
                trait_diffs=self._trait_diff_payload(diffs),
                activation_distance_linf=plan.after_metrics.activation.linf,
                activation_distance_l1=plan.after_metrics.activation.l1,
                approved_checkpoint_distance_linf=plan.after_metrics.approved_checkpoint.linf,
                approved_checkpoint_distance_l1=plan.after_metrics.approved_checkpoint.l1,
                rolling_trait_path=plan.after_metrics.rolling_trait_path,
                rolling_total_path=plan.after_metrics.rolling_global_path,
                lifetime_trait_path=plan.after_metrics.lifetime_trait_path,
                lifetime_total_path=plan.after_metrics.lifetime_global_path,
                occurred_at=outcome.decided_at,
            )
        )
        self._session.flush()
        rows = [
            PersonalityEvidenceRow(
                evidence_id=item.evidence_id,
                revision_id=evolution.revision_id,
                identity_id=identity_id,
                trait_key=plan.trait_key,
                direction=plan.direction.value,
                reflection_run_id=evolution.reflection_run_id,
                reflection_proposal_id=evolution.reflection_proposal_id,
                reflection_source_id=item.source.source.source_id,
                evidence_edge_id=item.source.source.evidence_edge_id,
                evidence_edge_version=item.source.evidence_edge_version,
                root_interaction_id=item.source.source.root_interaction_id,
                root_message_id=item.source.source.root_message_id,
                root_session_id=item.source.source.root_session_id,
                root_counterparty_id=item.source.source.root_counterparty_id,
                upstream_lineage_kind=item.source.upstream_lineage_kind.value,
                upstream_lineage_id=item.source.source.lineage_id,
                content_hash=item.source.source.content_hash,
                normalized_signature=item.normalized_signature,
                citation_role=item.citation_role.value,
                observed_at=item.source.source.observed_at,
                accepted_at=outcome.decided_at,
            )
            for item in evolution.evidence
        ]
        self._session.add_all(rows)
        return (
            evolution.revision_id,
            [item.evidence_id for item in rows],
            plan.personality.aggregate_version,
        )

    def _ensure_prior_checkpoint(
        self, identity_id: str, personality: Personality, *, created_at: datetime
    ) -> None:
        if self.get_checkpoint_for_version(identity_id, personality.aggregate_version) is not None:
            return
        digest = checkpoint_hash(
            identity_id=identity_id,
            checkpoint_kind=PersonalityCheckpointKind.MANUAL,
            personality=personality,
        )
        self._insert_checkpoint(
            PersonalityCheckpointSnapshot(
                checkpoint_id=f"personality-checkpoint-{digest}",
                checkpoint_kind=PersonalityCheckpointKind.MANUAL,
                identity_id=identity_id,
                source_aggregate_version=personality.aggregate_version,
                personality_schema_version=personality.schema_version,
                hash_schema_version=PERSONALITY_CHECKPOINT_HASH_SCHEMA_VERSION,
                checkpoint_hash=digest,
                traits=personality.traits,
            ),
            created_at=created_at,
        )

    def _optimistic_replace(
        self, identity_id: str, before: Personality, after: Personality
    ) -> None:
        if after.aggregate_version != before.aggregate_version + 1:
            raise ValueError("personality aggregate version must advance exactly once")
        state_result = self._session.execute(
            update(PersonalityStateRow)
            .where(
                PersonalityStateRow.identity_id == identity_id,
                PersonalityStateRow.aggregate_version == before.aggregate_version,
                PersonalityStateRow.schema_version == before.schema_version,
            )
            .values(aggregate_version=after.aggregate_version)
        )
        if getattr(state_result, "rowcount", None) != 1:
            raise RuntimeError("personality aggregate was concurrently modified")
        before_by_key = {item.key: item for item in before.traits}
        after_by_key = {item.key: item for item in after.traits}
        changed = tuple(
            key for key in CANONICAL_TRAIT_KEYS if before_by_key[key] != after_by_key[key]
        )
        if not changed:
            raise ValueError("personality owner write must change at least one trait")
        for key in changed:
            prior = before_by_key[key]
            current = after_by_key[key]
            if prior.baseline_value != current.baseline_value:
                raise ValueError("personality activation baseline is immutable")
            result = self._session.execute(
                update(PersonalityTraitRow)
                .where(
                    PersonalityTraitRow.identity_id == identity_id,
                    PersonalityTraitRow.trait_key == key,
                    PersonalityTraitRow.value == prior.value,
                    PersonalityTraitRow.baseline_value == prior.baseline_value,
                )
                .values(value=current.value)
            )
            if getattr(result, "rowcount", None) != 1:
                raise RuntimeError("personality trait was concurrently modified")

    def _insert_checkpoint(
        self, checkpoint: PersonalityCheckpointSnapshot, *, created_at: datetime
    ) -> None:
        existing = self.get_checkpoint(checkpoint.checkpoint_id)
        if existing is not None:
            if existing.snapshot != checkpoint:
                raise CorruptSatoriState("immutable personality checkpoint changed")
            return
        self._session.add(
            PersonalityCheckpointRow(
                checkpoint_id=checkpoint.checkpoint_id,
                identity_id=checkpoint.identity_id,
                personality_schema_version=checkpoint.personality_schema_version,
                source_aggregate_version=checkpoint.source_aggregate_version,
                checkpoint_kind=checkpoint.checkpoint_kind.value,
                hash_schema_version=checkpoint.hash_schema_version,
                checkpoint_hash=checkpoint.checkpoint_hash,
                created_at=created_at,
            )
        )
        self._session.flush()
        self._session.add_all(
            PersonalityCheckpointTraitRow(
                checkpoint_id=checkpoint.checkpoint_id,
                trait_key=item.key,
                value=item.value,
                baseline_value=item.baseline_value,
            )
            for item in checkpoint.traits
        )
        self._session.flush()

    @staticmethod
    def _validate_checkpoint(checkpoint: PersonalityCheckpointSnapshot) -> None:
        personality = SQLAlchemyPersonalityRepository._checkpoint_personality(checkpoint)
        if (
            checkpoint_hash(
                identity_id=checkpoint.identity_id,
                checkpoint_kind=checkpoint.checkpoint_kind,
                personality=personality,
            )
            != checkpoint.checkpoint_hash
        ):
            raise ValueError("personality checkpoint hash is invalid")

    @staticmethod
    def _checkpoint_personality(checkpoint: PersonalityCheckpointSnapshot) -> Personality:
        return Personality(
            schema_version=checkpoint.personality_schema_version,
            aggregate_version=checkpoint.source_aggregate_version,
            traits=checkpoint.traits,
        )

    def _map_checkpoint(self, row: PersonalityCheckpointRow) -> PersonalityCheckpointRecord:
        traits = tuple(
            self._session.execute(
                select(PersonalityCheckpointTraitRow)
                .where(PersonalityCheckpointTraitRow.checkpoint_id == row.checkpoint_id)
                .order_by(PersonalityCheckpointTraitRow.trait_key)
            ).scalars()
        )
        try:
            snapshot = PersonalityCheckpointSnapshot(
                checkpoint_id=row.checkpoint_id,
                checkpoint_kind=PersonalityCheckpointKind(row.checkpoint_kind),
                identity_id=row.identity_id,
                source_aggregate_version=row.source_aggregate_version,
                personality_schema_version=row.personality_schema_version,
                hash_schema_version=row.hash_schema_version,
                checkpoint_hash=row.checkpoint_hash,
                traits=tuple(
                    PersonalityTrait(item.trait_key, item.value, item.baseline_value)
                    for item in traits
                ),
            )
            self._validate_checkpoint(snapshot)
        except (TypeError, ValueError) as error:
            raise CorruptSatoriState("personality checkpoint violates invariants") from error
        return PersonalityCheckpointRecord(snapshot=snapshot, created_at=row.created_at)

    @staticmethod
    def _map_revision(row: PersonalityRevisionRow) -> PersonalityRevisionRecord:
        return PersonalityRevisionRecord(
            revision_id=row.revision_id,
            identity_id=row.identity_id,
            revision_kind=row.revision_kind,
            before_aggregate_version=row.before_aggregate_version,
            after_aggregate_version=row.after_aggregate_version,
            trait_key=cast(PersonalityTraitKey | None, row.trait_key),
            direction=row.direction,
            before_value=row.before_value,
            after_value=row.after_value,
            applied_delta=row.applied_delta,
            decision_confidence=row.decision_confidence,
            policy_version=row.policy_version,
            reason_code=row.reason_code,
            source_checkpoint_id=row.source_checkpoint_id,
            resulting_checkpoint_id=row.resulting_checkpoint_id,
            reflection_outcome_id=row.reflection_outcome_id,
            trait_diffs=SQLAlchemyPersonalityRepository._map_trait_diffs(row.trait_diffs),
            activation_distance_linf=row.activation_distance_linf,
            activation_distance_l1=row.activation_distance_l1,
            approved_checkpoint_distance_linf=row.approved_checkpoint_distance_linf,
            approved_checkpoint_distance_l1=row.approved_checkpoint_distance_l1,
            rolling_trait_path=row.rolling_trait_path,
            rolling_total_path=row.rolling_total_path,
            lifetime_trait_path=row.lifetime_trait_path,
            lifetime_total_path=row.lifetime_total_path,
            occurred_at=row.occurred_at,
        )

    @staticmethod
    def _map_evidence(row: PersonalityEvidenceRow) -> PersonalityEvidenceRecord:
        return PersonalityEvidenceRecord(
            evidence_id=row.evidence_id,
            revision_id=row.revision_id,
            identity_id=row.identity_id,
            trait_key=cast(PersonalityTraitKey, row.trait_key),
            direction=row.direction,
            reflection_run_id=row.reflection_run_id,
            reflection_proposal_id=row.reflection_proposal_id,
            reflection_source_id=row.reflection_source_id,
            evidence_edge_id=row.evidence_edge_id,
            evidence_edge_version=row.evidence_edge_version,
            root_interaction_id=row.root_interaction_id,
            root_message_id=row.root_message_id,
            root_session_id=row.root_session_id,
            root_counterparty_id=row.root_counterparty_id,
            upstream_lineage_kind=ReflectionLineageKind(row.upstream_lineage_kind),
            upstream_lineage_id=row.upstream_lineage_id,
            content_hash=row.content_hash,
            normalized_signature=row.normalized_signature,
            citation_role=PersonalityCitationRole(row.citation_role),
            observed_at=row.observed_at,
            accepted_at=row.accepted_at,
        )

    @staticmethod
    def _map_approval(
        row: PersonalityCheckpointApprovalRow,
    ) -> PersonalityCheckpointApprovalRecord:
        return PersonalityCheckpointApprovalRecord(
            approval_id=row.approval_id,
            identity_id=row.identity_id,
            checkpoint_id=row.checkpoint_id,
            checkpoint_hash=row.checkpoint_hash,
            expected_aggregate_version=row.expected_aggregate_version,
            reason=row.reason,
            approved_at=row.approved_at,
        )

    @staticmethod
    def _map_restore(row: PersonalityRestoreEventRow) -> PersonalityRestoreEventRecord:
        return PersonalityRestoreEventRecord(
            restore_id=row.restore_id,
            revision_id=row.revision_id,
            identity_id=row.identity_id,
            source_checkpoint_id=row.source_checkpoint_id,
            source_checkpoint_hash=row.source_checkpoint_hash,
            resulting_checkpoint_id=row.resulting_checkpoint_id,
            before_aggregate_version=row.before_aggregate_version,
            after_aggregate_version=row.after_aggregate_version,
            trait_diffs=SQLAlchemyPersonalityRepository._map_trait_diffs(row.trait_diffs),
            reason=row.reason,
            restored_at=row.restored_at,
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

    @staticmethod
    def _trait_diff_values(
        before: Personality, after: Personality
    ) -> tuple[PersonalityTraitDiff, ...]:
        before_values = {item.key: item.value for item in before.traits}
        after_values = {item.key: item.value for item in after.traits}
        return tuple(
            PersonalityTraitDiff(key, before_values[key], after_values[key])
            for key in CANONICAL_TRAIT_KEYS
            if before_values[key] != after_values[key]
        )

    @staticmethod
    def _trait_diff_payload(
        diffs: tuple[PersonalityTraitDiff, ...],
    ) -> list[dict[str, object]]:
        return [
            {
                "trait_key": item.trait_key,
                "before_value": item.before_value,
                "after_value": item.after_value,
            }
            for item in diffs
        ]

    @staticmethod
    def _map_trait_diffs(payload: list[dict[str, object]]) -> tuple[PersonalityTraitDiff, ...]:
        result: list[PersonalityTraitDiff] = []
        for item in payload:
            trait_key = item.get("trait_key")
            before = item.get("before_value")
            after = item.get("after_value")
            if (
                not isinstance(trait_key, str)
                or trait_key not in CANONICAL_TRAIT_KEYS
                or isinstance(before, bool)
                or not isinstance(before, int | float)
                or isinstance(after, bool)
                or not isinstance(after, int | float)
            ):
                raise CorruptSatoriState("personality revision trait diff is invalid")
            result.append(PersonalityTraitDiff(trait_key, float(before), float(after)))
        return tuple(result)
