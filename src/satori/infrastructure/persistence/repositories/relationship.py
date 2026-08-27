"""SQLAlchemy adapter for the sole Stage 8 RelationshipManager owner."""

from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.application.relationship.ports import RelationshipSource
from satori.domain.relationship import (
    RelationshipDecision,
    RelationshipDecisionKind,
    RelationshipDelta,
    RelationshipEventCategory,
    RelationshipState,
    RelationshipTransition,
    RelationshipVector,
)
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.initial_self import AuditEventRow
from satori.infrastructure.persistence.models.relationship import (
    RelationshipDecisionRow,
    RelationshipStateRow,
    RelationshipTransitionRow,
)


class SQLAlchemyRelationshipRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_state(self, identity_id: str, counterparty_id: str) -> RelationshipState | None:
        row = self._session.execute(
            select(RelationshipStateRow).where(
                RelationshipStateRow.identity_id == identity_id,
                RelationshipStateRow.counterparty_id == counterparty_id,
            )
        ).scalar_one_or_none()
        return self._map_state(row) if row is not None else None

    def add_initial_state(self, state: RelationshipState) -> bool:
        statement = (
            sqlite_insert(RelationshipStateRow)
            .values(**self._state_values(state))
            .on_conflict_do_nothing(index_elements=["identity_id", "counterparty_id"])
            .returning(RelationshipStateRow.relationship_id)
        )
        return self._session.execute(statement).scalar_one_or_none() is not None

    def get_source(self, interaction_id: str) -> RelationshipSource | None:
        row = self._session.execute(
            select(ConversationInteractionRow, ConversationSessionRow, ConversationMessageRow)
            .join(
                ConversationSessionRow,
                ConversationSessionRow.session_id == ConversationInteractionRow.session_id,
            )
            .join(
                ConversationMessageRow,
                and_(
                    ConversationMessageRow.interaction_id
                    == ConversationInteractionRow.interaction_id,
                    ConversationMessageRow.role == "user",
                ),
            )
            .where(
                ConversationInteractionRow.interaction_id == interaction_id,
                ConversationInteractionRow.status == "completed",
            )
        ).one_or_none()
        if row is None:
            return None
        interaction, session, message = row
        if interaction.completed_at is None:
            raise RuntimeError("completed relationship source has no completed_at")
        return RelationshipSource(
            interaction_id=interaction.interaction_id,
            user_message_id=message.message_id,
            user_content=message.content,
            session_id=interaction.session_id,
            identity_id=session.identity_id,
            counterparty_id=session.counterparty_id,
            trace_id=interaction.trace_id,
            started_at=interaction.started_at,
            completed_at=interaction.completed_at,
            processing_required=interaction.relationship_processing_required,
        )

    def get_counterparty_for_session(self, session_id: str) -> tuple[str, str] | None:
        row = self._session.get(ConversationSessionRow, session_id)
        return (row.identity_id, row.counterparty_id) if row is not None else None

    def get_decision(self, interaction_id: str) -> RelationshipDecision | None:
        row = self._session.execute(
            select(RelationshipDecisionRow).where(
                RelationshipDecisionRow.interaction_id == interaction_id
            )
        ).scalar_one_or_none()
        return self._map_decision(row) if row is not None else None

    def has_earlier_undecided_source(self, source: RelationshipSource) -> bool:
        decision_exists = (
            select(RelationshipDecisionRow.decision_id)
            .where(
                RelationshipDecisionRow.interaction_id == ConversationInteractionRow.interaction_id
            )
            .exists()
        )
        row = self._session.execute(
            select(ConversationInteractionRow.interaction_id)
            .join(
                ConversationSessionRow,
                ConversationSessionRow.session_id == ConversationInteractionRow.session_id,
            )
            .where(
                ConversationSessionRow.identity_id == source.identity_id,
                ConversationSessionRow.counterparty_id == source.counterparty_id,
                ConversationInteractionRow.status == "completed",
                ConversationInteractionRow.relationship_processing_required.is_(True),
                ~decision_exists,
                or_(
                    ConversationInteractionRow.started_at < source.started_at,
                    and_(
                        ConversationInteractionRow.started_at == source.started_at,
                        ConversationInteractionRow.interaction_id < source.interaction_id,
                    ),
                ),
            )
            .limit(1)
        ).scalar_one_or_none()
        return row is not None

    def session_delta(self, relationship_id: str, session_id: str) -> RelationshipDelta:
        rows = tuple(
            self._session.execute(
                select(RelationshipTransitionRow.applied_delta).where(
                    RelationshipTransitionRow.relationship_id == relationship_id,
                    RelationshipTransitionRow.session_id == session_id,
                )
            ).scalars()
        )
        values = {key: 0.0 for key in RelationshipVector.field_names()}
        for payload in rows:
            for key in values:
                values[key] += float(payload[key])
        return RelationshipDelta.from_mapping(values)

    def session_has_qualified_evidence(self, relationship_id: str, session_id: str) -> bool:
        rows = tuple(
            self._session.execute(
                select(RelationshipDecisionRow.categories).where(
                    RelationshipDecisionRow.relationship_id == relationship_id,
                    RelationshipDecisionRow.session_id == session_id,
                )
            ).scalars()
        )
        return any(
            any(item != RelationshipEventCategory.NEUTRAL_CONTACT.value for item in row)
            for row in rows
        )

    def record(
        self,
        *,
        decision: RelationshipDecision,
        before: RelationshipState,
        after: RelationshipState,
        transition: RelationshipTransition | None,
        audit_event_id: str,
    ) -> bool:
        if self.get_decision(decision.interaction_id) is not None:
            return False
        values = self._state_values(after)
        values.pop("relationship_id")
        updated = self._session.execute(
            update(RelationshipStateRow)
            .where(
                RelationshipStateRow.relationship_id == before.relationship_id,
                RelationshipStateRow.state_version == before.state_version,
                RelationshipStateRow.processed_interaction_count
                == before.processed_interaction_count,
            )
            .values(**values)
            .returning(RelationshipStateRow.relationship_id)
        ).scalar_one_or_none()
        if updated is None:
            return False
        if transition is not None:
            self._session.add(self._transition_row(transition))
        self._session.add(self._decision_row(decision))
        self._session.add(
            AuditEventRow(
                event_id=audit_event_id,
                schema_version=1,
                event_type=f"relationship.decision_{decision.kind.value}",
                aggregate_type="relationship",
                aggregate_id=decision.relationship_id,
                occurred_at=decision.decided_at,
                trace_id=decision.trace_id,
                details={
                    "decision_id": decision.decision_id,
                    "transition_id": decision.transition_id,
                    "interaction_id": decision.interaction_id,
                    "source_user_message_id": decision.source_user_message_id,
                    "session_id": decision.session_id,
                    "categories": [item.value for item in decision.categories],
                    "reason_code": decision.reason_code,
                    "policy_version": decision.policy_version,
                    "appraisal_schema_version": decision.appraisal_schema_version,
                },
            )
        )
        self._session.flush()
        return True

    def list_transitions(
        self, relationship_id: str, *, limit: int | None = None
    ) -> Sequence[RelationshipTransition]:
        query = (
            select(RelationshipTransitionRow)
            .where(RelationshipTransitionRow.relationship_id == relationship_id)
            .order_by(
                RelationshipTransitionRow.committed_at.desc(),
                RelationshipTransitionRow.transition_id.desc(),
            )
        )
        if limit is not None:
            query = query.limit(limit)
        return tuple(self._map_transition(row) for row in self._session.execute(query).scalars())

    def list_unprocessed_source_ids(
        self, identity_id: str, counterparty_id: str, *, limit: int
    ) -> Sequence[str]:
        decision_exists = (
            select(RelationshipDecisionRow.decision_id)
            .where(
                RelationshipDecisionRow.interaction_id == ConversationInteractionRow.interaction_id
            )
            .exists()
        )
        query = (
            select(ConversationInteractionRow.interaction_id)
            .join(
                ConversationSessionRow,
                ConversationSessionRow.session_id == ConversationInteractionRow.session_id,
            )
            .where(
                ConversationSessionRow.identity_id == identity_id,
                ConversationSessionRow.counterparty_id == counterparty_id,
                ConversationInteractionRow.status == "completed",
                ConversationInteractionRow.relationship_processing_required.is_(True),
                ~decision_exists,
            )
            .order_by(
                ConversationInteractionRow.started_at, ConversationInteractionRow.interaction_id
            )
            .limit(limit)
        )
        return tuple(self._session.execute(query).scalars())

    @staticmethod
    def _state_values(state: RelationshipState) -> dict[str, object]:
        return {
            "relationship_id": state.relationship_id,
            "identity_id": state.identity_id,
            "counterparty_id": state.counterparty_id,
            "schema_version": state.schema_version,
            "state_version": state.state_version,
            "policy_version": state.policy_version,
            **state.vector.as_mapping(),
            "processed_interaction_count": state.processed_interaction_count,
            "qualified_interaction_count": state.qualified_interaction_count,
            "distinct_session_count": state.distinct_session_count,
            "positive_evidence_count": state.positive_evidence_count,
            "negative_evidence_count": state.negative_evidence_count,
            "updated_at": state.updated_at,
        }

    @staticmethod
    def _map_state(row: RelationshipStateRow) -> RelationshipState:
        return RelationshipState(
            relationship_id=row.relationship_id,
            identity_id=row.identity_id,
            counterparty_id=row.counterparty_id,
            schema_version=row.schema_version,
            state_version=row.state_version,
            policy_version=row.policy_version,
            vector=RelationshipVector(
                row.familiarity,
                row.trust,
                row.comfort,
                row.closeness,
                row.intellectual_respect,
                row.affection,
            ),
            processed_interaction_count=row.processed_interaction_count,
            qualified_interaction_count=row.qualified_interaction_count,
            distinct_session_count=row.distinct_session_count,
            positive_evidence_count=row.positive_evidence_count,
            negative_evidence_count=row.negative_evidence_count,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _decision_row(item: RelationshipDecision) -> RelationshipDecisionRow:
        return RelationshipDecisionRow(
            decision_id=item.decision_id,
            relationship_id=item.relationship_id,
            interaction_id=item.interaction_id,
            source_user_message_id=item.source_user_message_id,
            session_id=item.session_id,
            trace_id=item.trace_id,
            kind=item.kind.value,
            reason_code=item.reason_code,
            categories=[value.value for value in item.categories],
            confidence=item.confidence,
            provider=item.provider,
            model=item.model,
            appraisal_method=item.appraisal_method,
            appraisal_schema_version=item.appraisal_schema_version,
            policy_version=item.policy_version,
            decided_at=item.decided_at,
            transition_id=item.transition_id,
        )

    @staticmethod
    def _map_decision(row: RelationshipDecisionRow) -> RelationshipDecision:
        return RelationshipDecision(
            decision_id=row.decision_id,
            relationship_id=row.relationship_id,
            interaction_id=row.interaction_id,
            source_user_message_id=row.source_user_message_id,
            session_id=row.session_id,
            trace_id=row.trace_id,
            kind=RelationshipDecisionKind(row.kind),
            reason_code=row.reason_code,
            categories=tuple(RelationshipEventCategory(item) for item in row.categories),
            confidence=row.confidence,
            provider=row.provider,
            model=row.model,
            appraisal_method=row.appraisal_method,
            appraisal_schema_version=row.appraisal_schema_version,
            policy_version=row.policy_version,
            decided_at=row.decided_at,
            transition_id=row.transition_id,
        )

    @classmethod
    def _transition_row(cls, item: RelationshipTransition) -> RelationshipTransitionRow:
        return RelationshipTransitionRow(
            transition_id=item.transition_id,
            relationship_id=item.relationship_id,
            interaction_id=item.interaction_id,
            source_user_message_id=item.source_user_message_id,
            session_id=item.session_id,
            trace_id=item.trace_id,
            categories=[value.value for value in item.categories],
            confidence=item.confidence,
            base_state_version=item.before.state_version,
            resulting_state_version=item.after.state_version,
            state_before=cls._snapshot(item.before),
            applied_delta=item.delta.as_mapping(),
            state_after=cls._snapshot(item.after),
            provider=item.provider,
            model=item.model,
            appraisal_method=item.appraisal_method,
            appraisal_schema_version=item.appraisal_schema_version,
            policy_version=item.policy_version,
            committed_at=item.committed_at,
        )

    @classmethod
    def _map_transition(cls, row: RelationshipTransitionRow) -> RelationshipTransition:
        return RelationshipTransition(
            transition_id=row.transition_id,
            relationship_id=row.relationship_id,
            interaction_id=row.interaction_id,
            source_user_message_id=row.source_user_message_id,
            session_id=row.session_id,
            trace_id=row.trace_id,
            categories=tuple(RelationshipEventCategory(item) for item in row.categories),
            confidence=row.confidence,
            before=cls._snapshot_from_payload(row.state_before),
            delta=RelationshipDelta.from_mapping(
                {key: float(value) for key, value in row.applied_delta.items()}
            ),
            after=cls._snapshot_from_payload(row.state_after),
            provider=row.provider,
            model=row.model,
            appraisal_method=row.appraisal_method,
            appraisal_schema_version=row.appraisal_schema_version,
            policy_version=row.policy_version,
            committed_at=row.committed_at,
        )

    @staticmethod
    def _snapshot(state: RelationshipState) -> dict[str, object]:
        return {
            "relationship_id": state.relationship_id,
            "identity_id": state.identity_id,
            "counterparty_id": state.counterparty_id,
            "schema_version": state.schema_version,
            "state_version": state.state_version,
            "policy_version": state.policy_version,
            "vector": state.vector.as_mapping(),
            "processed_interaction_count": state.processed_interaction_count,
            "qualified_interaction_count": state.qualified_interaction_count,
            "distinct_session_count": state.distinct_session_count,
            "positive_evidence_count": state.positive_evidence_count,
            "negative_evidence_count": state.negative_evidence_count,
            "updated_at": state.updated_at.isoformat(),
        }

    @staticmethod
    def _snapshot_from_payload(payload: dict[str, object]) -> RelationshipState:
        vector = cast(dict[str, object], payload["vector"])
        return RelationshipState(
            relationship_id=str(payload["relationship_id"]),
            identity_id=str(payload["identity_id"]),
            counterparty_id=str(payload["counterparty_id"]),
            schema_version=int(cast(int, payload["schema_version"])),
            state_version=int(cast(int, payload["state_version"])),
            policy_version=int(cast(int, payload["policy_version"])),
            vector=RelationshipVector.from_mapping(
                {key: float(cast(int | float, value)) for key, value in vector.items()}
            ),
            processed_interaction_count=int(cast(int, payload["processed_interaction_count"])),
            qualified_interaction_count=int(cast(int, payload["qualified_interaction_count"])),
            distinct_session_count=int(cast(int, payload["distinct_session_count"])),
            positive_evidence_count=int(cast(int, payload["positive_evidence_count"])),
            negative_evidence_count=int(cast(int, payload["negative_evidence_count"])),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        )
