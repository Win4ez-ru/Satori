"""SQLAlchemy adapter for the Stage 4 InteractionLog owner."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.core.conversation import ConversationProviderFailureReason
from satori.domain.conversation_history import (
    ConversationHistorySnapshot,
    ConversationInteraction,
    ConversationSession,
    HistoricalMessage,
    HistoricalMessageRole,
    InteractionFailureMetadata,
    InteractionProviderMetadata,
    InteractionStatus,
    SessionKind,
    SessionStatus,
)
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)


class SQLAlchemyConversationHistoryRepository:
    """Map history read models while keeping ORM write capability in infrastructure."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_session(self, session_id: str) -> ConversationSession | None:
        row = self._session.get(ConversationSessionRow, session_id)
        return self._map_session(row) if row is not None else None

    def add_session(self, session: ConversationSession) -> bool:
        statement = (
            sqlite_insert(ConversationSessionRow)
            .values(
                session_id=session.session_id,
                identity_id=session.identity_id,
                counterparty_id=session.counterparty_id,
                schema_version=session.schema_version,
                kind=session.kind.value,
                status=session.status.value,
                started_at=session.started_at,
                ended_at=session.ended_at,
            )
            .on_conflict_do_nothing(index_elements=["session_id"])
            .returning(ConversationSessionRow.session_id)
        )
        return self._session.execute(statement).scalar_one_or_none() is not None

    def close_session(self, session_id: str, *, ended_at: datetime) -> ConversationSession | None:
        self._session.execute(
            update(ConversationSessionRow)
            .where(
                ConversationSessionRow.session_id == session_id,
                ConversationSessionRow.status == SessionStatus.OPEN.value,
            )
            .values(status=SessionStatus.CLOSED.value, ended_at=ended_at)
        )
        row = self._session.get(ConversationSessionRow, session_id)
        if row is None:
            return None
        self._session.refresh(row)
        return self._map_session(row)

    def get_by_client_request_id(self, client_request_id: str) -> ConversationInteraction | None:
        row = self._session.execute(
            select(ConversationInteractionRow).where(
                ConversationInteractionRow.client_request_id == client_request_id
            )
        ).scalar_one_or_none()
        return self._map_interaction(row) if row is not None else None

    def get_interaction(self, interaction_id: str) -> ConversationInteraction | None:
        row = self._session.get(ConversationInteractionRow, interaction_id)
        return self._map_interaction(row) if row is not None else None

    def add_interaction(self, interaction: ConversationInteraction) -> bool:
        if interaction.status is not InteractionStatus.PENDING:
            raise ValueError("new interaction must be pending")
        statement = (
            sqlite_insert(ConversationInteractionRow)
            .values(
                interaction_id=interaction.interaction_id,
                session_id=interaction.session_id,
                client_request_id=interaction.client_request_id,
                trace_id=interaction.trace_id,
                schema_version=interaction.schema_version,
                status=interaction.status.value,
                started_at=interaction.started_at,
                relationship_processing_required=interaction.relationship_processing_required,
                model_processing_required=interaction.model_processing_required,
                position_processing_required=interaction.position_processing_required,
            )
            .on_conflict_do_nothing(index_elements=["client_request_id"])
            .returning(ConversationInteractionRow.interaction_id)
        )
        if self._session.execute(statement).scalar_one_or_none() is None:
            return False
        self._session.add(self._message_row(interaction.user_message))
        return True

    def mark_failed(self, interaction_id: str, *, failure: InteractionFailureMetadata) -> None:
        self._session.execute(
            update(ConversationInteractionRow)
            .where(
                ConversationInteractionRow.interaction_id == interaction_id,
                ConversationInteractionRow.status != InteractionStatus.COMPLETED.value,
            )
            .values(
                status=InteractionStatus.FAILED.value,
                failure_kind=failure.kind,
                failure_reason=(failure.reason.value if failure.reason is not None else None),
                provider=failure.provider,
                model=failure.model,
            )
        )
        current = self._session.get(ConversationInteractionRow, interaction_id)
        if current is None:
            raise RuntimeError("interaction is missing while marking failure")

    def complete_interaction(
        self,
        interaction_id: str,
        *,
        assistant_message: HistoricalMessage,
        completed_at: datetime,
        provider_metadata: InteractionProviderMetadata,
        close_session: bool,
    ) -> ConversationInteraction:
        row = self._session.get(ConversationInteractionRow, interaction_id)
        if row is None:
            raise RuntimeError("interaction is missing during finalize")
        if row.status == InteractionStatus.COMPLETED.value:
            return self._map_interaction(row)
        self._session.add(self._message_row(assistant_message))
        self._session.flush()
        row.status = InteractionStatus.COMPLETED.value
        row.completed_at = completed_at
        row.provider = provider_metadata.provider
        row.model = provider_metadata.model
        row.finish_status = provider_metadata.finish_status
        row.input_tokens = provider_metadata.input_tokens
        row.output_tokens = provider_metadata.output_tokens
        row.context_schema_version = provider_metadata.context_schema_version
        row.context_manifest_schema_version = provider_metadata.context_manifest_schema_version
        row.policy_id = provider_metadata.policy_id
        row.policy_schema_version = provider_metadata.policy_schema_version
        row.retrieval_status = provider_metadata.retrieval_status
        row.retrieved_memory_ids = list(provider_metadata.retrieved_memory_ids)
        row.semantic_retrieval_status = provider_metadata.semantic_retrieval_status
        row.retrieved_semantic_claim_ids = list(provider_metadata.retrieved_semantic_claim_ids)
        row.model_context_status = provider_metadata.model_context_status
        row.user_model_context_schema_version = provider_metadata.user_model_context_schema_version
        row.user_model_context_claim_ids = list(provider_metadata.user_model_context_claim_ids)
        row.world_model_context_schema_version = (
            provider_metadata.world_model_context_schema_version
        )
        row.world_model_context_claim_ids = list(provider_metadata.world_model_context_claim_ids)
        row.position_context_status = provider_metadata.position_context_status
        row.position_context_schema_version = provider_metadata.position_context_schema_version
        row.position_context_ids = list(provider_metadata.position_context_ids)
        row.inclination_context_status = provider_metadata.inclination_context_status
        row.inclination_context_schema_version = (
            provider_metadata.inclination_context_schema_version
        )
        row.inclination_context_ids = list(provider_metadata.inclination_context_ids)
        row.inclination_curiosity_influence = provider_metadata.inclination_curiosity_influence
        row.personality_aggregate_version = provider_metadata.personality_aggregate_version
        row.personality_expression_schema_version = (
            provider_metadata.personality_expression_schema_version
        )
        row.personality_expression_cues = (
            list(provider_metadata.personality_expression_cues)
            if provider_metadata.context_manifest_schema_version >= 16
            else None
        )
        row.emotion_appraisal_status = provider_metadata.emotion_appraisal_status
        row.emotion_context_schema_version = provider_metadata.emotion_context_schema_version
        row.emotion_state_version = provider_metadata.emotion_state_version
        row.mood_state_version = provider_metadata.mood_state_version
        row.emotion_state_as_of = provider_metadata.emotion_state_as_of
        row.relationship_context_schema_version = (
            provider_metadata.relationship_context_schema_version
        )
        row.relationship_state_version = provider_metadata.relationship_state_version
        row.failure_kind = None
        row.failure_reason = None
        if close_session:
            self._session.execute(
                update(ConversationSessionRow)
                .where(
                    ConversationSessionRow.session_id == row.session_id,
                    ConversationSessionRow.status == SessionStatus.OPEN.value,
                )
                .values(status=SessionStatus.CLOSED.value, ended_at=completed_at)
            )
        self._session.flush()
        return self._map_interaction(row)

    def get_history(self, *, session_id: str | None = None) -> ConversationHistorySnapshot:
        session_query = select(ConversationSessionRow)
        interaction_query = select(ConversationInteractionRow)
        if session_id is not None:
            session_query = session_query.where(ConversationSessionRow.session_id == session_id)
            interaction_query = interaction_query.where(
                ConversationInteractionRow.session_id == session_id
            )
        session_rows = tuple(
            self._session.execute(
                session_query.order_by(
                    ConversationSessionRow.started_at, ConversationSessionRow.session_id
                )
            ).scalars()
        )
        interaction_rows = tuple(
            self._session.execute(
                interaction_query.order_by(
                    ConversationInteractionRow.started_at,
                    ConversationInteractionRow.interaction_id,
                )
            ).scalars()
        )
        return ConversationHistorySnapshot(
            sessions=tuple(self._map_session(row) for row in session_rows),
            interactions=tuple(self._map_interaction(row) for row in interaction_rows),
        )

    def list_recent_completed(
        self,
        *,
        session_id: str,
        excluded_interaction_id: str,
        limit: int,
    ) -> tuple[ConversationInteraction, ...]:
        if limit < 1:
            raise ValueError("recent completed interaction limit must be positive")
        rows = tuple(
            self._session.execute(
                select(ConversationInteractionRow)
                .where(
                    ConversationInteractionRow.session_id == session_id,
                    ConversationInteractionRow.status == InteractionStatus.COMPLETED.value,
                    ConversationInteractionRow.interaction_id != excluded_interaction_id,
                )
                .order_by(
                    ConversationInteractionRow.started_at.desc(),
                    ConversationInteractionRow.interaction_id.desc(),
                )
                .limit(limit)
            ).scalars()
        )
        return tuple(self._map_interaction(row) for row in reversed(rows))

    def _map_interaction(self, row: ConversationInteractionRow) -> ConversationInteraction:
        message_rows = tuple(
            self._session.execute(
                select(ConversationMessageRow)
                .where(ConversationMessageRow.interaction_id == row.interaction_id)
                .order_by(ConversationMessageRow.sequence)
            ).scalars()
        )
        user_messages = tuple(message for message in message_rows if message.role == "user")
        assistant_messages = tuple(
            message for message in message_rows if message.role == "assistant"
        )
        if len(user_messages) != 1 or len(assistant_messages) > 1:
            raise RuntimeError("persistent interaction has invalid raw-message cardinality")
        metadata = None
        failure = None
        if row.status == InteractionStatus.COMPLETED.value:
            if (
                row.provider is None
                or row.model is None
                or row.finish_status is None
                or row.context_schema_version is None
                or row.context_manifest_schema_version is None
                or row.policy_id is None
                or row.policy_schema_version is None
            ):
                raise RuntimeError("completed interaction has incomplete provider metadata")
            metadata = InteractionProviderMetadata(
                provider=row.provider,
                model=row.model,
                finish_status=row.finish_status,
                context_schema_version=row.context_schema_version,
                context_manifest_schema_version=row.context_manifest_schema_version,
                policy_id=row.policy_id,
                policy_schema_version=row.policy_schema_version,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                retrieval_status=row.retrieval_status or "not_requested",
                retrieved_memory_ids=tuple(row.retrieved_memory_ids or ()),
                semantic_retrieval_status=(row.semantic_retrieval_status or "not_requested"),
                retrieved_semantic_claim_ids=tuple(row.retrieved_semantic_claim_ids or ()),
                model_context_status=row.model_context_status or "not_requested",
                user_model_context_schema_version=row.user_model_context_schema_version,
                user_model_context_claim_ids=tuple(row.user_model_context_claim_ids or ()),
                world_model_context_schema_version=row.world_model_context_schema_version,
                world_model_context_claim_ids=tuple(row.world_model_context_claim_ids or ()),
                position_context_status=row.position_context_status or "not_requested",
                position_context_schema_version=row.position_context_schema_version,
                position_context_ids=tuple(row.position_context_ids or ()),
                inclination_context_status=(row.inclination_context_status or "not_requested"),
                inclination_context_schema_version=(row.inclination_context_schema_version),
                inclination_context_ids=tuple(row.inclination_context_ids or ()),
                inclination_curiosity_influence=(row.inclination_curiosity_influence or 0.0),
                personality_aggregate_version=row.personality_aggregate_version,
                personality_expression_schema_version=(row.personality_expression_schema_version),
                personality_expression_cues=tuple(row.personality_expression_cues or ()),
                emotion_appraisal_status=(row.emotion_appraisal_status or "not_requested"),
                emotion_context_schema_version=row.emotion_context_schema_version,
                emotion_state_version=row.emotion_state_version,
                mood_state_version=row.mood_state_version,
                emotion_state_as_of=row.emotion_state_as_of,
                relationship_context_schema_version=row.relationship_context_schema_version,
                relationship_state_version=row.relationship_state_version,
            )
        elif row.status == InteractionStatus.FAILED.value:
            if row.failure_kind is None:
                raise RuntimeError("failed interaction has no failure kind")
            failure_reason = (
                ConversationProviderFailureReason(row.failure_reason)
                if row.failure_reason is not None
                else None
            )
            failure = InteractionFailureMetadata(
                kind=row.failure_kind,
                reason=failure_reason,
                provider=row.provider,
                model=row.model,
            )
        return ConversationInteraction(
            interaction_id=row.interaction_id,
            session_id=row.session_id,
            client_request_id=row.client_request_id,
            trace_id=row.trace_id,
            schema_version=row.schema_version,
            status=InteractionStatus(row.status),
            started_at=row.started_at,
            completed_at=row.completed_at,
            user_message=self._map_message(user_messages[0]),
            assistant_message=(
                self._map_message(assistant_messages[0]) if assistant_messages else None
            ),
            provider_metadata=metadata,
            failure=failure,
            relationship_processing_required=row.relationship_processing_required,
            model_processing_required=row.model_processing_required,
            position_processing_required=row.position_processing_required,
        )

    @staticmethod
    def _map_session(row: ConversationSessionRow) -> ConversationSession:
        return ConversationSession(
            session_id=row.session_id,
            identity_id=row.identity_id,
            schema_version=row.schema_version,
            kind=SessionKind(row.kind),
            status=SessionStatus(row.status),
            started_at=row.started_at,
            ended_at=row.ended_at,
            counterparty_id=row.counterparty_id,
        )

    @staticmethod
    def _map_message(row: ConversationMessageRow) -> HistoricalMessage:
        return HistoricalMessage(
            message_id=row.message_id,
            session_id=row.session_id,
            interaction_id=row.interaction_id,
            schema_version=row.schema_version,
            role=HistoricalMessageRole(row.role),
            content=row.content,
            created_at=row.created_at,
            sequence=row.sequence,
        )

    @staticmethod
    def _message_row(message: HistoricalMessage) -> ConversationMessageRow:
        return ConversationMessageRow(
            message_id=message.message_id,
            session_id=message.session_id,
            interaction_id=message.interaction_id,
            schema_version=message.schema_version,
            role=message.role.value,
            content=message.content,
            created_at=message.created_at,
            sequence=message.sequence,
        )
