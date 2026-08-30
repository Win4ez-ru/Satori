"""Stage 4 append-only conversation-history ORM models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from satori.core.conversation import ConversationProviderFailureReason
from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime

_FAILURE_REASON_VALUES_SQL = ", ".join(
    f"'{reason.value}'" for reason in ConversationProviderFailureReason
)


class ConversationSessionRow(Base):
    """Persistent conversational container, distinct from memory."""

    __tablename__ = "conversation_sessions"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("kind IN ('explicit', 'implicit')", name="kind_valid"),
        CheckConstraint("status IN ('open', 'closed')", name="status_valid"),
        CheckConstraint(
            "(status = 'open' AND ended_at IS NULL) OR "
            "(status = 'closed' AND ended_at IS NOT NULL)",
            name="status_end_consistent",
        ),
    )

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_identities.identity_id", ondelete="RESTRICT"),
        nullable=False,
    )
    counterparty_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ConversationInteractionRow(Base):
    """One idempotent non-streaming user-to-Satori turn."""

    __tablename__ = "conversation_interactions"
    __table_args__ = (
        UniqueConstraint("client_request_id"),
        UniqueConstraint("interaction_id", "session_id"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("status IN ('pending', 'failed', 'completed')", name="status_valid"),
        CheckConstraint(
            "inclination_context_status IS NULL OR inclination_context_status IN "
            "('not_requested', 'empty', 'available')",
            name="inclination_context_status_valid",
        ),
        CheckConstraint(
            "inclination_context_schema_version IS NULL OR inclination_context_schema_version >= 1",
            name="inclination_context_schema_version_positive",
        ),
        CheckConstraint(
            "inclination_curiosity_influence IS NULL OR "
            "(inclination_curiosity_influence >= 0.0 "
            "AND inclination_curiosity_influence <= 0.20)",
            name="inclination_curiosity_influence_valid",
        ),
        CheckConstraint(
            "personality_aggregate_version IS NULL OR personality_aggregate_version >= 1",
            name="personality_aggregate_version_positive",
        ),
        CheckConstraint(
            "personality_expression_schema_version IS NULL OR "
            "personality_expression_schema_version >= 1",
            name="personality_expression_schema_version_positive",
        ),
        CheckConstraint(
            "((context_manifest_schema_version IS NULL OR "
            "context_manifest_schema_version < 16) AND "
            "personality_aggregate_version IS NULL AND "
            "personality_expression_schema_version IS NULL AND "
            "personality_expression_cues IS NULL) OR "
            "(context_manifest_schema_version >= 16 AND context_schema_version >= 16 AND "
            "personality_aggregate_version IS NOT NULL AND "
            "personality_expression_schema_version = 2 AND "
            "personality_expression_cues IS NOT NULL)",
            name="personality_manifest_consistent",
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL AND provider IS NOT NULL "
            "AND model IS NOT NULL AND finish_status IS NOT NULL "
            "AND context_schema_version IS NOT NULL "
            "AND context_manifest_schema_version IS NOT NULL "
            "AND policy_id IS NOT NULL AND policy_schema_version IS NOT NULL "
            "AND failure_kind IS NULL AND failure_reason IS NULL) OR "
            "(status = 'pending' AND completed_at IS NULL AND provider IS NULL "
            "AND model IS NULL AND finish_status IS NULL "
            "AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND context_schema_version IS NULL "
            "AND context_manifest_schema_version IS NULL "
            "AND policy_id IS NULL AND policy_schema_version IS NULL "
            "AND failure_kind IS NULL AND failure_reason IS NULL) OR "
            "(status = 'failed' AND completed_at IS NULL AND finish_status IS NULL "
            "AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND context_schema_version IS NULL "
            "AND context_manifest_schema_version IS NULL "
            "AND policy_id IS NULL AND policy_schema_version IS NULL "
            "AND failure_kind IS NOT NULL AND "
            "((failure_reason IS NULL AND provider IS NULL AND model IS NULL) OR "
            "(failure_reason IS NOT NULL AND provider IS NOT NULL AND model IS NOT NULL)))",
            name="completion_metadata_consistent",
        ),
        CheckConstraint(
            f"failure_reason IS NULL OR failure_reason IN ({_FAILURE_REASON_VALUES_SQL})",
            name="failure_reason_valid",
        ),
    )

    interaction_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    finish_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_manifest_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retrieved_memory_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    semantic_retrieval_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retrieved_semantic_claim_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    emotion_appraisal_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    emotion_context_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    emotion_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mood_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    emotion_state_as_of: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    relationship_context_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relationship_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_kind: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relationship_processing_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    model_processing_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    position_processing_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    model_context_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_model_context_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_model_context_claim_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    world_model_context_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    world_model_context_claim_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    position_context_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    position_context_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_context_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    inclination_context_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    inclination_context_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inclination_context_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    inclination_curiosity_influence: Mapped[float | None] = mapped_column(Float, nullable=True)
    personality_aggregate_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    personality_expression_schema_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    personality_expression_cues: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)


class ConversationMessageRow(Base):
    """Exact immutable user or assistant text; hidden prompts are never inserted."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["interaction_id", "session_id"],
            ["conversation_interactions.interaction_id", "conversation_interactions.session_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("interaction_id", "role"),
        UniqueConstraint("interaction_id", "sequence"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("role IN ('user', 'assistant')", name="role_valid"),
        CheckConstraint("sequence IN (1, 2)", name="sequence_valid"),
        CheckConstraint("length(content) > 0", name="content_not_blank"),
    )

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    interaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
