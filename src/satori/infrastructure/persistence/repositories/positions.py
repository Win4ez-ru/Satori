"""SQLAlchemy adapter for identity-global Stage 11 Satori positions."""

from datetime import datetime

from sqlalchemy import exists, literal, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.core.inclinations import InclinationKind, InclinationStateReference
from satori.core.positions import (
    PositionEvidenceRole,
    PositionKind,
    PositionSourceMessage,
    PositionStance,
    PositionValueReference,
)
from satori.domain.audit_metadata import AuditMetadataFamily
from satori.domain.erasure_knowledge import (
    ErasedKnowledgeHeader,
    KnowledgeBodyKind,
    KnowledgeBodyTarget,
)
from satori.domain.inclinations import (
    INCLINATION_POLICY_VERSION,
    InclinationDecisionKind,
    InclinationEvaluation,
    InclinationEvidence,
    InclinationEvidenceRole,
    InclinationRevision,
    InclinationRevisionKind,
    SatoriInclination,
)
from satori.domain.positions import (
    POSITION_FORMATION_VERSION,
    PositionDecisionKind,
    PositionEvidence,
    PositionFormationDecision,
    PositionFormationPlan,
    PositionManager,
    PositionRevision,
    PositionRevisionKind,
    PositionStatus,
    SatoriPosition,
)
from satori.domain.reflection import ReflectionOutcome, ReflectionOutcomeDecision
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.audit_metadata_writer import bind_metadata
from satori.infrastructure.persistence.memory_scope import require_live_message_content
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.initial_self import AuditEventRow, ValueRow
from satori.infrastructure.persistence.models.positions import (
    InclinationEvidenceRow,
    InclinationRevisionRow,
    PositionEvidenceRow,
    PositionFormationDecisionRow,
    PositionRevisionRow,
    SatoriInclinationRow,
    SatoriPositionRow,
)
from satori.infrastructure.persistence.models.reflection import ReflectionOutcomeRow
from satori.infrastructure.persistence.repositories.knowledge_bodies import (
    available_owner_predicate,
    current_owner_row,
    erase_owned_knowledge_bodies,
    erased_knowledge_header,
    repair_owner_rows,
    require_available_knowledge_target,
)
from satori.infrastructure.persistence.source_policy import (
    eligible_payload,
    payload_admission_predicate,
    policy_snapshot,
    record_owner_withdrawal,
    record_payload,
    require_evidence_roots,
    require_owner_reconciled,
    require_source_inputs,
    root_admission_predicate,
)


class SQLAlchemyPositionsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def erase_knowledge_bodies(
        self, targets: tuple[KnowledgeBodyTarget, ...], *, action_id: str
    ) -> None:
        erase_owned_knowledge_bodies(
            self._session,
            targets,
            action_id=action_id,
            allowed_kinds=frozenset(
                (
                    KnowledgeBodyKind.POSITION,
                    KnowledgeBodyKind.INCLINATION,
                )
            ),
        )

    def get_erased_knowledge_header(
        self, kind: KnowledgeBodyKind, aggregate_id: str
    ) -> ErasedKnowledgeHeader | None:
        return erased_knowledge_header(
            self._session,
            kind,
            aggregate_id,
            allowed_kinds=frozenset(
                (
                    KnowledgeBodyKind.POSITION,
                    KnowledgeBodyKind.INCLINATION,
                )
            ),
        )

    def withdraw_source_support(
        self,
        *,
        excluded_message_ids: frozenset[str],
        request_id: str,
        policy_generation: int,
        now: datetime,
    ) -> None:
        """Retire position text; record inclination holds without changing its trajectory."""

        snapshot = policy_snapshot(self._session)
        if snapshot is None:
            return
        identity_id = snapshot.identity_id
        positions = tuple(
            self._map_position(row)
            for row in repair_owner_rows(
                self._session,
                SatoriPositionRow,
                SatoriPositionRow.identity_id == identity_id,
                SatoriPositionRow.status.in_(("active", "competing")),
            )
        )
        plan = PositionManager().withdraw_support(
            positions,
            affected_position_ids=frozenset(
                position.position_id
                for position in positions
                if not eligible_payload(
                    self._session, "position", position.position_id, position.aggregate_version
                )
            ),
            now=now,
        )
        before_positions = {position.position_id: position for position in positions}
        self._apply_plan(plan)
        for position in plan.positions:
            before = before_positions[position.position_id]
            record_owner_withdrawal(
                self._session,
                owner="position",
                aggregate_id=position.position_id,
                request_id=request_id,
                policy_generation=policy_generation,
                before_status=before.status.value,
                after_status=position.status.value,
                before_version=before.aggregate_version,
                after_version=position.aggregate_version,
                root_message_ids=tuple(
                    sorted(
                        edge.source_message_id
                        for edge in position.evidence
                        if edge.source_message_id in excluded_message_ids
                    )
                ),
                now=now,
            )
        for row in repair_owner_rows(
            self._session, SatoriInclinationRow, SatoriInclinationRow.identity_id == identity_id
        ):
            if eligible_payload(
                self._session, "inclination", row.inclination_id, row.aggregate_version
            ):
                continue
            roots = tuple(
                self._session.scalars(
                    select(InclinationEvidenceRow.source_message_id).where(
                        InclinationEvidenceRow.inclination_id == row.inclination_id,
                        InclinationEvidenceRow.source_message_id.in_(excluded_message_ids),
                    )
                )
            )
            record_owner_withdrawal(
                self._session,
                owner="inclination",
                aggregate_id=row.inclination_id,
                request_id=request_id,
                policy_generation=policy_generation,
                before_status="retained",
                after_status="disclosure_held",
                before_version=row.aggregate_version,
                after_version=row.aggregate_version,
                root_message_ids=roots,
                now=now,
            )

    def get_decision(self, idempotency_key: str) -> PositionFormationDecision | None:
        row = self._session.execute(
            select(PositionFormationDecisionRow).where(
                PositionFormationDecisionRow.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        return self._map_decision(row) if row is not None else None

    def get_source_messages(
        self, source_interaction_id: str, *, limit: int
    ) -> tuple[PositionSourceMessage, ...]:
        require_owner_reconciled(self._session, "positions")
        if limit < 1:
            raise ValueError("position source-message limit must be positive")
        interaction = self._session.get(ConversationInteractionRow, source_interaction_id)
        if (
            interaction is None
            or interaction.status != "completed"
            or not interaction.position_processing_required
        ):
            return ()
        source_session = self._session.get(ConversationSessionRow, interaction.session_id)
        if source_session is None:
            raise RuntimeError("position source session is missing")
        rows = tuple(
            self._session.execute(
                select(ConversationMessageRow, ConversationInteractionRow, ConversationSessionRow)
                .join(
                    ConversationInteractionRow,
                    ConversationInteractionRow.interaction_id
                    == ConversationMessageRow.interaction_id,
                )
                .join(
                    ConversationSessionRow,
                    ConversationSessionRow.session_id == ConversationInteractionRow.session_id,
                )
                .where(
                    ConversationMessageRow.role == "user",
                    ConversationMessageRow.session_id == ConversationInteractionRow.session_id,
                    ConversationSessionRow.scope_verified.is_(True)
                    if policy_snapshot(self._session) is not None
                    else literal(True),
                    ConversationInteractionRow.status == "completed",
                    ConversationInteractionRow.position_processing_required.is_(True),
                    ConversationInteractionRow.started_at <= interaction.started_at,
                    ConversationSessionRow.identity_id == source_session.identity_id,
                    root_admission_predicate(self._session, ConversationMessageRow.message_id),
                )
                .order_by(
                    ConversationInteractionRow.started_at.desc(),
                    ConversationInteractionRow.interaction_id.desc(),
                )
                .limit(limit)
            ).all()
        )
        mapped = tuple(
            PositionSourceMessage(
                message_id=message.message_id,
                interaction_id=row_interaction.interaction_id,
                identity_id=session.identity_id,
                counterparty_id=session.counterparty_id,
                observed_at=message.created_at,
                content=require_live_message_content(message),
            )
            for message, row_interaction, session in rows
        )
        source = next(
            (item for item in mapped if item.interaction_id == source_interaction_id), None
        )
        return (
            ()
            if source is None
            else (source, *(item for item in mapped if item.message_id != source.message_id))
        )

    def get_value_references(self, identity_id: str) -> tuple[PositionValueReference, ...]:
        rows = tuple(
            self._session.execute(
                select(ValueRow)
                .where(ValueRow.identity_id == identity_id)
                .order_by(ValueRow.value_key)
            ).scalars()
        )
        return tuple(
            PositionValueReference(key=item.value_key, description=item.description)
            for item in rows
        )

    def list_positions(
        self, *, identity_id: str, current_only: bool = False
    ) -> tuple[SatoriPosition, ...]:
        query = select(SatoriPositionRow).where(
            SatoriPositionRow.body_state == "available",
            SatoriPositionRow.body_action_id.is_(None),
            SatoriPositionRow.identity_id == identity_id,
            payload_admission_predicate(
                self._session,
                "position",
                SatoriPositionRow.position_id,
                SatoriPositionRow.aggregate_version,
            ),
        )
        if current_only:
            query = query.where(
                SatoriPositionRow.status.in_(
                    (PositionStatus.ACTIVE.value, PositionStatus.COMPETING.value)
                )
            )
        rows = tuple(
            self._session.execute(
                query.order_by(
                    SatoriPositionRow.status,
                    SatoriPositionRow.kind,
                    SatoriPositionRow.updated_at.desc(),
                    SatoriPositionRow.position_id,
                )
            ).scalars()
        )
        return tuple(self._map_position(row) for row in rows)

    def get_position(self, position_id: str) -> SatoriPosition | None:
        row = self._session.get(SatoriPositionRow, position_id)
        return (
            self._map_position(row)
            if row is not None
            and row.body_state == "available"
            and row.body_action_id is None
            and eligible_payload(self._session, "position", row.position_id, row.aggregate_version)
            else None
        )

    def list_held_inclination_keys(self, *, identity_id: str) -> frozenset[str]:
        """Return only opaque reserved identities, never withheld labels or evidence."""
        return frozenset(
            self._session.scalars(
                select(SatoriInclinationRow.inclination_key).where(
                    SatoriInclinationRow.identity_id == identity_id,
                    or_(
                        SatoriInclinationRow.body_state == "erased",
                        ~payload_admission_predicate(
                            self._session,
                            "inclination",
                            SatoriInclinationRow.inclination_id,
                            SatoriInclinationRow.aggregate_version,
                        ),
                    ),
                )
            )
        )

    def list_inclination_references(
        self, *, identity_id: str
    ) -> tuple[InclinationStateReference, ...]:
        """Return compact anchor-only projections without loading evidence or revisions."""

        rows = self._session.execute(
            select(SatoriInclinationRow)
            .where(
                SatoriInclinationRow.body_state == "available",
                SatoriInclinationRow.body_action_id.is_(None),
                SatoriInclinationRow.identity_id == identity_id,
                payload_admission_predicate(
                    self._session,
                    "inclination",
                    SatoriInclinationRow.inclination_id,
                    SatoriInclinationRow.aggregate_version,
                ),
            )
            .order_by(
                SatoriInclinationRow.kind,
                SatoriInclinationRow.updated_at.desc(),
                SatoriInclinationRow.inclination_id,
            )
        ).scalars()
        return tuple(self._map_inclination_reference(item) for item in rows)

    def list_inclinations(self, *, identity_id: str) -> tuple[SatoriInclination, ...]:
        rows = self._session.execute(
            select(SatoriInclinationRow)
            .where(
                SatoriInclinationRow.body_state == "available",
                SatoriInclinationRow.body_action_id.is_(None),
                SatoriInclinationRow.identity_id == identity_id,
                payload_admission_predicate(
                    self._session,
                    "inclination",
                    SatoriInclinationRow.inclination_id,
                    SatoriInclinationRow.aggregate_version,
                ),
            )
            .order_by(
                SatoriInclinationRow.kind,
                SatoriInclinationRow.updated_at.desc(),
                SatoriInclinationRow.inclination_id,
            )
        ).scalars()
        return tuple(self._map_inclination(item) for item in rows)

    def get_inclination(self, inclination_id: str) -> SatoriInclination | None:
        row = self._session.get(SatoriInclinationRow, inclination_id)
        return (
            self._map_inclination(row)
            if row is not None
            and row.body_state == "available"
            and row.body_action_id is None
            and eligible_payload(
                self._session, "inclination", row.inclination_id, row.aggregate_version
            )
            else None
        )

    def list_revisions(self, position_id: str) -> tuple[PositionRevision, ...]:
        rows = tuple(
            self._session.execute(
                select(PositionRevisionRow)
                .where(PositionRevisionRow.position_id == position_id)
                .order_by(PositionRevisionRow.position_version)
            ).scalars()
        )
        return tuple(self._map_revision(row) for row in rows)

    def list_unprocessed_interaction_ids(self, *, limit: int) -> tuple[str, ...]:
        if limit < 1:
            raise ValueError("position backfill limit must be positive")
        processed = exists().where(
            PositionFormationDecisionRow.source_interaction_id
            == ConversationInteractionRow.interaction_id,
            PositionFormationDecisionRow.formation_version == POSITION_FORMATION_VERSION,
        )
        return tuple(
            self._session.execute(
                select(ConversationInteractionRow.interaction_id)
                .where(
                    ConversationInteractionRow.status == "completed",
                    ConversationInteractionRow.position_processing_required.is_(True),
                    ~processed,
                    exists(
                        select(ConversationMessageRow.message_id).where(
                            ConversationMessageRow.interaction_id
                            == ConversationInteractionRow.interaction_id,
                            ConversationMessageRow.role == "user",
                            ConversationMessageRow.session_id
                            == ConversationInteractionRow.session_id,
                            exists(
                                select(ConversationSessionRow.session_id).where(
                                    ConversationSessionRow.session_id
                                    == ConversationInteractionRow.session_id,
                                    ConversationSessionRow.scope_verified.is_(True)
                                    if policy_snapshot(self._session) is not None
                                    else literal(True),
                                )
                            ),
                            root_admission_predicate(
                                self._session, ConversationMessageRow.message_id
                            ),
                        )
                    ),
                )
                .order_by(
                    ConversationInteractionRow.started_at,
                    ConversationInteractionRow.interaction_id,
                )
                .limit(limit)
            ).scalars()
        )

    def record_decision(
        self,
        decision: PositionFormationDecision,
        plan: PositionFormationPlan,
        *,
        audit_event_id: str,
        source_input_id: str | None = None,
    ) -> bool:
        require_owner_reconciled(self._session, "positions")
        require_source_inputs(self._session, source_input_id)
        for position in plan.positions:
            require_available_knowledge_target(
                self._session, KnowledgeBodyKind.POSITION, position.position_id
            )
        self._validate_plan_sources(plan, source_input_id)
        statement = (
            sqlite_insert(PositionFormationDecisionRow)
            .values(
                decision_id=decision.decision_id,
                idempotency_key=decision.idempotency_key,
                source_interaction_id=decision.source_interaction_id,
                source_message_id=decision.source_message_id,
                identity_id=decision.identity_id,
                formation_version=decision.formation_version,
                policy_version=decision.policy_version,
                kind=decision.kind.value,
                reason_code=decision.reason_code,
                created_count=decision.created_count,
                merged_count=decision.merged_count,
                superseded_count=decision.superseded_count,
                competing_count=decision.competing_count,
                rejected_count=decision.rejected_count,
                position_ids=list(decision.position_ids),
                decided_at=decision.decided_at,
                trace_id=decision.trace_id,
                formation_method=decision.formation_method,
                provider=decision.provider,
                model=decision.model,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(PositionFormationDecisionRow.decision_id)
        )
        if self._session.execute(statement).scalar_one_or_none() is None:
            return False

        self._apply_plan(plan)
        for position in plan.positions:
            record_payload(
                self._session,
                "position",
                position.position_id,
                position.aggregate_version,
                source_input_id,
            )
        self._session.add(
            bind_metadata(
                self._session,
                AuditEventRow(
                    event_id=audit_event_id,
                    schema_version=1,
                    event_type=f"positions.{decision.kind.value}",
                    aggregate_type="satori_positions",
                    aggregate_id=decision.identity_id,
                    occurred_at=decision.decided_at,
                    trace_id=decision.trace_id,
                    details={
                        "decision_id": decision.decision_id,
                        "source_interaction_id": decision.source_interaction_id,
                        "formation_version": decision.formation_version,
                        "policy_version": decision.policy_version,
                        "reason_code": decision.reason_code,
                        "position_ids": list(decision.position_ids),
                        "created_count": decision.created_count,
                        "merged_count": decision.merged_count,
                        "superseded_count": decision.superseded_count,
                        "competing_count": decision.competing_count,
                        "rejected_count": decision.rejected_count,
                    },
                ),
                family=AuditMetadataFamily.POSITION_FORMATION,
                identity_id=decision.identity_id,
                owner_record_id=decision.decision_id,
                source_input_id=source_input_id,
            )
        )
        return True

    def record_reflection_decision(
        self,
        outcome: ReflectionOutcome,
        plan: PositionFormationPlan,
        *,
        identity_id: str,
        trace_id: str,
        audit_event_id: str,
        source_input_id: str | None = None,
    ) -> bool:
        require_owner_reconciled(self._session, "positions")
        require_source_inputs(self._session, source_input_id)
        for position in plan.positions:
            require_available_knowledge_target(
                self._session, KnowledgeBodyKind.POSITION, position.position_id
            )
        self._validate_plan_sources(plan, source_input_id)
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
        self._apply_plan(plan)
        for position in plan.positions:
            record_payload(
                self._session,
                "position",
                position.position_id,
                position.aggregate_version,
                source_input_id,
            )
        self._session.add(
            bind_metadata(
                self._session,
                AuditEventRow(
                    event_id=audit_event_id,
                    schema_version=1,
                    event_type=f"reflection.position_{outcome.decision.value}",
                    aggregate_type="satori_positions",
                    aggregate_id=identity_id,
                    occurred_at=outcome.decided_at,
                    trace_id=trace_id,
                    details={
                        "outcome_id": outcome.outcome_id,
                        "proposal_id": outcome.proposal_id,
                        "target_policy_version": outcome.target_policy_version,
                        "reason_code": outcome.reason_code,
                        "position_ids": [item.position_id for item in plan.positions],
                    },
                ),
                family=AuditMetadataFamily.REFLECTION_POSITION,
                identity_id=identity_id,
                owner_record_id=outcome.outcome_id,
                source_input_id=source_input_id,
            )
        )
        return True

    def record_inclination_reflection_decision(
        self,
        outcome: ReflectionOutcome,
        evaluation: InclinationEvaluation,
        *,
        identity_id: str,
        trace_id: str,
        audit_event_id: str,
        source_input_id: str | None = None,
    ) -> bool:
        """Atomically persist one terminal outcome and its owner-authorized mutation."""

        require_owner_reconciled(self._session, "positions")
        require_source_inputs(self._session, source_input_id)
        applied = evaluation.kind is InclinationDecisionKind.APPLIED
        if applied != (outcome.decision is ReflectionOutcomeDecision.ACCEPTED):
            raise ValueError("inclination evaluation and reflection outcome disagree")
        if outcome.reason_code != evaluation.reason_code:
            raise ValueError("inclination evaluation and reflection outcome reasons disagree")
        if outcome.target_policy_version != INCLINATION_POLICY_VERSION:
            raise ValueError("reflection outcome targets an unsupported inclination policy")
        inclination = evaluation.inclination
        revision = evaluation.revision
        if applied:
            if inclination is None or revision is None:
                raise ValueError("applied inclination evaluation is incomplete")
            retained = self._session.execute(
                select(SatoriInclinationRow).where(
                    SatoriInclinationRow.identity_id == identity_id,
                    SatoriInclinationRow.inclination_key == inclination.inclination_key,
                )
            ).scalar_one_or_none()
            if retained is not None and (
                retained.body_state != "available" or retained.body_action_id is not None
            ):
                raise SourcePolicyError("knowledge_body_unavailable")
            if retained is not None and not eligible_payload(
                self._session, "inclination", retained.inclination_id, retained.aggregate_version
            ):
                raise ValueError("owner_reconciliation_required")
            if outcome.target_aggregate_type != "satori_inclinations":
                raise ValueError("accepted reflection outcome targets a different owner")
            if inclination.identity_id != identity_id:
                raise ValueError("inclination belongs to a different identity")
            if outcome.target_aggregate_id != inclination.inclination_id:
                raise ValueError("reflection outcome targets a different inclination")
            if (
                revision.reflection_outcome_id != outcome.outcome_id
                or revision.inclination_id != inclination.inclination_id
                or revision.inclination_version != inclination.aggregate_version
                or revision.reason_code != evaluation.reason_code
                or revision.new_score != inclination.score
                or revision.new_confidence != inclination.confidence
                or revision.new_stability != inclination.stability
                or revision.state_as_of != inclination.state_as_of
            ):
                raise ValueError("inclination revision does not match committed aggregate state")
            if any(
                edge.inclination_id != inclination.inclination_id
                for edge in evaluation.new_evidence
            ):
                raise ValueError("inclination evidence belongs to a different aggregate")
            evidence_by_id = {item.evidence_id: item for item in inclination.evidence}
            if len(evidence_by_id) != len(inclination.evidence) or any(
                evidence_by_id.get(item.evidence_id) != item for item in evaluation.new_evidence
            ):
                raise ValueError("new inclination evidence is absent from committed aggregate")
        elif outcome.target_aggregate_type is not None or outcome.target_aggregate_id is not None:
            raise ValueError("rejected inclination outcome cannot target aggregate state")

        if inclination is not None:
            require_available_knowledge_target(
                self._session, KnowledgeBodyKind.INCLINATION, inclination.inclination_id
            )
        if inclination is not None and policy_snapshot(self._session) is not None:
            current = current_owner_row(
                self._session, SatoriInclinationRow, inclination.inclination_id
            )
            if current is not None and not eligible_payload(
                self._session, "inclination", current.inclination_id, current.aggregate_version
            ):
                raise SourcePolicyError("source_payload_unavailable")
            require_evidence_roots(
                self._session,
                source_input_id,
                tuple(edge.source_message_id for edge in evaluation.new_evidence),
            )
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

        if inclination is not None and revision is not None:
            self._apply_inclination(inclination)
            record_payload(
                self._session,
                "inclination",
                inclination.inclination_id,
                inclination.aggregate_version,
                source_input_id,
            )
            self._session.add_all(
                self._inclination_evidence_row(item) for item in evaluation.new_evidence
            )
            self._session.add(self._inclination_revision_row(revision))

        self._session.add(
            bind_metadata(
                self._session,
                AuditEventRow(
                    event_id=audit_event_id,
                    schema_version=1,
                    event_type=f"reflection.inclination_{outcome.decision.value}",
                    aggregate_type="satori_inclinations",
                    aggregate_id=(
                        inclination.inclination_id if inclination is not None else identity_id
                    ),
                    occurred_at=outcome.decided_at,
                    trace_id=trace_id,
                    details={
                        "outcome_id": outcome.outcome_id,
                        "proposal_id": outcome.proposal_id,
                        "target_policy_version": outcome.target_policy_version,
                        "reason_code": outcome.reason_code,
                        "inclination_id": (
                            inclination.inclination_id if inclination is not None else None
                        ),
                        "inclination_kind": (
                            inclination.kind.value if inclination is not None else None
                        ),
                        "aggregate_version": (
                            inclination.aggregate_version if inclination is not None else None
                        ),
                        "revision_id": revision.revision_id if revision is not None else None,
                        "evidence_ids": [item.evidence_id for item in evaluation.new_evidence],
                        "new_evidence_count": len(evaluation.new_evidence),
                        "prior_score": revision.prior_score if revision is not None else None,
                        "new_score": revision.new_score if revision is not None else None,
                        "applied_delta": (revision.applied_delta if revision is not None else None),
                        "prior_confidence": (
                            revision.prior_confidence if revision is not None else None
                        ),
                        "new_confidence": (
                            revision.new_confidence if revision is not None else None
                        ),
                        "prior_stability": (
                            revision.prior_stability if revision is not None else None
                        ),
                        "new_stability": (revision.new_stability if revision is not None else None),
                    },
                ),
                family=AuditMetadataFamily.REFLECTION_INCLINATION,
                identity_id=identity_id,
                owner_record_id=outcome.outcome_id,
                source_input_id=source_input_id,
            )
        )
        return True

    def _apply_inclination(self, inclination: SatoriInclination) -> None:
        require_available_knowledge_target(
            self._session, KnowledgeBodyKind.INCLINATION, inclination.inclination_id
        )
        current = current_owner_row(self._session, SatoriInclinationRow, inclination.inclination_id)
        if current is None:
            if inclination.aggregate_version != 1:
                raise RuntimeError("new inclination must start at aggregate version one")
            self._session.add(self._inclination_row(inclination))
            self._session.flush()
            return
        if (
            current.identity_id != inclination.identity_id
            or current.inclination_key != inclination.inclination_key
            or current.kind != inclination.kind.value
            or current.normalized_topic != inclination.normalized_topic
            or current.normalized_alternative_topic != inclination.normalized_alternative_topic
        ):
            raise RuntimeError("inclination immutable identity changed")
        expected_version = inclination.aggregate_version - 1
        if expected_version < 1:
            raise RuntimeError("inclination aggregate version is not monotonic")
        result = self._session.execute(
            update(SatoriInclinationRow)
            .where(
                SatoriInclinationRow.inclination_id == inclination.inclination_id,
                SatoriInclinationRow.aggregate_version == expected_version,
                available_owner_predicate(self._session, SatoriInclinationRow),
            )
            .values(
                schema_version=inclination.schema_version,
                aggregate_version=inclination.aggregate_version,
                policy_version=inclination.policy_version,
                normalization_version=inclination.normalization_version,
                topic=inclination.topic,
                alternative_topic=inclination.alternative_topic,
                score=inclination.score,
                confidence=inclination.confidence,
                stability=inclination.stability,
                state_as_of=inclination.state_as_of,
                last_accepted_at=inclination.last_accepted_at,
                updated_at=inclination.updated_at,
            )
        )
        if getattr(result, "rowcount", None) != 1:
            raise RuntimeError("inclination aggregate was concurrently modified")
        self._session.flush()

    def _validate_plan_sources(self, plan: PositionFormationPlan, input_id: str | None) -> None:
        if policy_snapshot(self._session) is None:
            return
        for position in plan.positions:
            row = self._session.get(SatoriPositionRow, position.position_id)
            existing: tuple[PositionEvidence, ...] = ()
            if row is not None:
                if not eligible_payload(
                    self._session, "position", row.position_id, row.aggregate_version
                ):
                    raise SourcePolicyError("source_payload_unavailable")
                if (
                    row.identity_id != position.identity_id
                    or row.position_key != position.position_key
                    or position.aggregate_version != row.aggregate_version + 1
                ):
                    raise ValueError("position_aggregate_target_invalid")
                existing = self._map_position(row).evidence
            elif position.aggregate_version != 1:
                raise ValueError("position_aggregate_target_invalid")
            require_evidence_roots(
                self._session,
                input_id,
                tuple(edge.source_message_id for edge in position.evidence if edge not in existing),
            )

    def _apply_plan(self, plan: PositionFormationPlan) -> None:
        new_positions: list[SatoriPosition] = []
        changed_positions: list[tuple[SatoriPosition, int]] = []
        for position in plan.positions:
            require_available_knowledge_target(
                self._session, KnowledgeBodyKind.POSITION, position.position_id
            )
            current = current_owner_row(self._session, SatoriPositionRow, position.position_id)
            if current is None:
                new_positions.append(position)
            else:
                changed_positions.append((position, current.aggregate_version))
        self._session.add_all(self._position_row(item) for item in new_positions)
        self._session.flush()
        for position, expected_version in changed_positions:
            if position.aggregate_version != expected_version + 1:
                raise RuntimeError("position aggregate version is not monotonic")
            result = self._session.execute(
                update(SatoriPositionRow)
                .where(
                    SatoriPositionRow.position_id == position.position_id,
                    SatoriPositionRow.aggregate_version == expected_version,
                    available_owner_predicate(self._session, SatoriPositionRow),
                )
                .values(
                    aggregate_version=position.aggregate_version,
                    confidence=position.confidence,
                    status=position.status.value,
                    competing_with_position_id=position.competing_with_position_id,
                    superseded_by_position_id=position.superseded_by_position_id,
                    updated_at=position.updated_at,
                )
            )
            if getattr(result, "rowcount", None) != 1:
                raise RuntimeError("position aggregate was concurrently modified")
        self._session.flush()

        for position in plan.positions:
            known_messages = set(
                self._session.execute(
                    select(PositionEvidenceRow.source_message_id).where(
                        PositionEvidenceRow.position_id == position.position_id
                    )
                ).scalars()
            )
            known_signatures = set(
                self._session.execute(
                    select(PositionEvidenceRow.normalized_signature).where(
                        PositionEvidenceRow.position_id == position.position_id
                    )
                ).scalars()
            )
            self._session.add_all(
                self._evidence_row(item)
                for item in position.evidence
                if item.source_message_id not in known_messages
                and item.normalized_signature not in known_signatures
            )
        self._session.add_all(self._revision_row(item) for item in plan.revisions)

    @staticmethod
    def _inclination_row(inclination: SatoriInclination) -> SatoriInclinationRow:
        return SatoriInclinationRow(
            inclination_id=inclination.inclination_id,
            inclination_key=inclination.inclination_key,
            identity_id=inclination.identity_id,
            schema_version=inclination.schema_version,
            aggregate_version=inclination.aggregate_version,
            policy_version=inclination.policy_version,
            normalization_version=inclination.normalization_version,
            kind=inclination.kind.value,
            topic=inclination.topic,
            normalized_topic=inclination.normalized_topic,
            alternative_topic=inclination.alternative_topic,
            normalized_alternative_topic=inclination.normalized_alternative_topic,
            score=inclination.score,
            confidence=inclination.confidence,
            stability=inclination.stability,
            state_as_of=inclination.state_as_of,
            last_accepted_at=inclination.last_accepted_at,
            created_at=inclination.created_at,
            updated_at=inclination.updated_at,
        )

    @staticmethod
    def _inclination_evidence_row(evidence: InclinationEvidence) -> InclinationEvidenceRow:
        return InclinationEvidenceRow(
            evidence_id=evidence.evidence_id,
            inclination_id=evidence.inclination_id,
            reflection_source_id=evidence.reflection_source_id,
            affective_transition_id=evidence.affective_transition_id,
            affective_state_version=evidence.affective_state_version,
            affective_signal_hash=evidence.affective_signal_hash,
            source_message_id=evidence.source_message_id,
            source_interaction_id=evidence.source_interaction_id,
            source_session_id=evidence.source_session_id,
            source_counterparty_id=evidence.source_counterparty_id,
            content_hash=evidence.content_hash,
            content_signature=evidence.content_signature,
            role=evidence.role.value,
            signal=evidence.signal,
            observed_at=evidence.observed_at,
            accepted_at=evidence.accepted_at,
        )

    @staticmethod
    def _inclination_revision_row(revision: InclinationRevision) -> InclinationRevisionRow:
        return InclinationRevisionRow(
            revision_id=revision.revision_id,
            inclination_id=revision.inclination_id,
            inclination_version=revision.inclination_version,
            reflection_outcome_id=revision.reflection_outcome_id,
            kind=revision.kind.value,
            prior_score=revision.prior_score,
            new_score=revision.new_score,
            applied_delta=revision.applied_delta,
            prior_confidence=revision.prior_confidence,
            new_confidence=revision.new_confidence,
            prior_stability=revision.prior_stability,
            new_stability=revision.new_stability,
            state_as_of=revision.state_as_of,
            reason_code=revision.reason_code,
            occurred_at=revision.occurred_at,
        )

    @staticmethod
    def _position_row(position: SatoriPosition) -> SatoriPositionRow:
        return SatoriPositionRow(
            position_id=position.position_id,
            position_key=position.position_key,
            identity_id=position.identity_id,
            schema_version=position.schema_version,
            aggregate_version=position.aggregate_version,
            policy_version=position.policy_version,
            formation_version=position.formation_version,
            normalization_version=position.normalization_version,
            proposition=position.proposition,
            normalized_proposition=position.normalized_proposition,
            kind=position.kind.value,
            stance=position.stance.value,
            confidence=position.confidence,
            status=position.status.value,
            value_key=position.value_key,
            competing_with_position_id=position.competing_with_position_id,
            superseded_by_position_id=position.superseded_by_position_id,
            created_at=position.created_at,
            updated_at=position.updated_at,
        )

    @staticmethod
    def _evidence_row(evidence: PositionEvidence) -> PositionEvidenceRow:
        return PositionEvidenceRow(
            evidence_id=evidence.evidence_id,
            position_id=evidence.position_id,
            source_message_id=evidence.source_message_id,
            source_interaction_id=evidence.source_interaction_id,
            source_counterparty_id=evidence.source_counterparty_id,
            quote=evidence.quote,
            normalized_signature=evidence.normalized_signature,
            role=evidence.role.value,
            observed_at=evidence.observed_at,
        )

    @staticmethod
    def _revision_row(revision: PositionRevision) -> PositionRevisionRow:
        return PositionRevisionRow(
            revision_id=revision.revision_id,
            position_id=revision.position_id,
            position_version=revision.position_version,
            decision_id=revision.decision_id,
            reflection_outcome_id=revision.reflection_outcome_id,
            kind=revision.kind.value,
            prior_status=revision.prior_status.value if revision.prior_status else None,
            new_status=revision.new_status.value,
            prior_confidence=revision.prior_confidence,
            new_confidence=revision.new_confidence,
            reason_code=revision.reason_code,
            occurred_at=revision.occurred_at,
        )

    @staticmethod
    def _map_inclination_reference(row: SatoriInclinationRow) -> InclinationStateReference:
        if row.body_state != "available" or row.body_action_id is not None or row.topic is None:
            raise SourcePolicyError("knowledge_body_unavailable")
        return InclinationStateReference(
            inclination_id=row.inclination_id,
            aggregate_version=row.aggregate_version,
            kind=InclinationKind(row.kind),
            topic=row.topic,
            alternative_topic=row.alternative_topic,
            score=row.score,
            confidence=row.confidence,
            stability=row.stability,
            state_as_of=row.state_as_of,
        )

    def _map_inclination(self, row: SatoriInclinationRow) -> SatoriInclination:
        if (
            row.body_state != "available"
            or row.body_action_id is not None
            or row.topic is None
            or row.normalized_topic is None
        ):
            raise SourcePolicyError("knowledge_body_unavailable")
        evidence_rows = self._session.execute(
            select(InclinationEvidenceRow)
            .where(InclinationEvidenceRow.inclination_id == row.inclination_id)
            .order_by(InclinationEvidenceRow.observed_at, InclinationEvidenceRow.evidence_id)
        ).scalars()
        revision_rows = self._session.execute(
            select(InclinationRevisionRow)
            .where(InclinationRevisionRow.inclination_id == row.inclination_id)
            .order_by(InclinationRevisionRow.inclination_version)
        ).scalars()
        return SatoriInclination(
            inclination_id=row.inclination_id,
            inclination_key=row.inclination_key,
            identity_id=row.identity_id,
            schema_version=row.schema_version,
            aggregate_version=row.aggregate_version,
            policy_version=row.policy_version,
            normalization_version=row.normalization_version,
            kind=InclinationKind(row.kind),
            topic=row.topic,
            normalized_topic=row.normalized_topic,
            alternative_topic=row.alternative_topic,
            normalized_alternative_topic=row.normalized_alternative_topic,
            score=row.score,
            confidence=row.confidence,
            stability=row.stability,
            state_as_of=row.state_as_of,
            last_accepted_at=row.last_accepted_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
            evidence=tuple(self._map_inclination_evidence(item) for item in evidence_rows),
            revisions=tuple(self._map_inclination_revision(item) for item in revision_rows),
        )

    @staticmethod
    def _map_inclination_evidence(row: InclinationEvidenceRow) -> InclinationEvidence:
        return InclinationEvidence(
            evidence_id=row.evidence_id,
            inclination_id=row.inclination_id,
            reflection_source_id=row.reflection_source_id,
            affective_transition_id=row.affective_transition_id,
            affective_state_version=row.affective_state_version,
            affective_signal_hash=row.affective_signal_hash,
            source_message_id=row.source_message_id,
            source_interaction_id=row.source_interaction_id,
            source_session_id=row.source_session_id,
            source_counterparty_id=row.source_counterparty_id,
            content_hash=row.content_hash,
            content_signature=row.content_signature,
            role=InclinationEvidenceRole(row.role),
            signal=row.signal,
            observed_at=row.observed_at,
            accepted_at=row.accepted_at,
        )

    @staticmethod
    def _map_inclination_revision(row: InclinationRevisionRow) -> InclinationRevision:
        return InclinationRevision(
            revision_id=row.revision_id,
            inclination_id=row.inclination_id,
            inclination_version=row.inclination_version,
            reflection_outcome_id=row.reflection_outcome_id,
            kind=InclinationRevisionKind(row.kind),
            prior_score=row.prior_score,
            new_score=row.new_score,
            applied_delta=row.applied_delta,
            prior_confidence=row.prior_confidence,
            new_confidence=row.new_confidence,
            prior_stability=row.prior_stability,
            new_stability=row.new_stability,
            state_as_of=row.state_as_of,
            reason_code=row.reason_code,
            occurred_at=row.occurred_at,
        )

    def _map_position(self, row: SatoriPositionRow) -> SatoriPosition:
        if (
            row.body_state != "available"
            or row.body_action_id is not None
            or row.proposition is None
            or row.normalized_proposition is None
        ):
            raise SourcePolicyError("knowledge_body_unavailable")
        evidence_rows = tuple(
            self._session.execute(
                select(PositionEvidenceRow)
                .where(PositionEvidenceRow.position_id == row.position_id)
                .order_by(PositionEvidenceRow.observed_at, PositionEvidenceRow.evidence_id)
            ).scalars()
        )
        if any(item.quote is None or item.normalized_signature is None for item in evidence_rows):
            raise SourcePolicyError("knowledge_body_unavailable")
        return SatoriPosition(
            position_id=row.position_id,
            position_key=row.position_key,
            identity_id=row.identity_id,
            schema_version=row.schema_version,
            aggregate_version=row.aggregate_version,
            policy_version=row.policy_version,
            formation_version=row.formation_version,
            normalization_version=row.normalization_version,
            proposition=row.proposition,
            normalized_proposition=row.normalized_proposition,
            kind=PositionKind(row.kind),
            stance=PositionStance(row.stance),
            confidence=row.confidence,
            status=PositionStatus(row.status),
            value_key=row.value_key,
            competing_with_position_id=row.competing_with_position_id,
            superseded_by_position_id=row.superseded_by_position_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            evidence=tuple(
                PositionEvidence(
                    evidence_id=item.evidence_id,
                    position_id=item.position_id,
                    source_message_id=item.source_message_id,
                    source_interaction_id=item.source_interaction_id,
                    source_counterparty_id=item.source_counterparty_id,
                    quote=self._live_position_body(item.quote),
                    normalized_signature=self._live_position_body(item.normalized_signature),
                    role=PositionEvidenceRole(item.role),
                    observed_at=item.observed_at,
                )
                for item in evidence_rows
            ),
        )

    @staticmethod
    def _live_position_body(value: str | None) -> str:
        if value is None or not value.strip():
            raise SourcePolicyError("knowledge_body_unavailable")
        return value

    @staticmethod
    def _map_revision(row: PositionRevisionRow) -> PositionRevision:
        return PositionRevision(
            revision_id=row.revision_id,
            position_id=row.position_id,
            position_version=row.position_version,
            decision_id=row.decision_id,
            reflection_outcome_id=row.reflection_outcome_id,
            kind=PositionRevisionKind(row.kind),
            prior_status=PositionStatus(row.prior_status) if row.prior_status else None,
            new_status=PositionStatus(row.new_status),
            prior_confidence=row.prior_confidence,
            new_confidence=row.new_confidence,
            reason_code=row.reason_code,
            occurred_at=row.occurred_at,
        )

    @staticmethod
    def _map_decision(row: PositionFormationDecisionRow) -> PositionFormationDecision:
        return PositionFormationDecision(
            decision_id=row.decision_id,
            idempotency_key=row.idempotency_key,
            source_interaction_id=row.source_interaction_id,
            source_message_id=row.source_message_id,
            identity_id=row.identity_id,
            formation_version=row.formation_version,
            policy_version=row.policy_version,
            kind=PositionDecisionKind(row.kind),
            reason_code=row.reason_code,
            created_count=row.created_count,
            merged_count=row.merged_count,
            superseded_count=row.superseded_count,
            competing_count=row.competing_count,
            rejected_count=row.rejected_count,
            position_ids=tuple(row.position_ids),
            decided_at=row.decided_at,
            trace_id=row.trace_id,
            formation_method=row.formation_method,
            provider=row.provider,
            model=row.model,
        )
