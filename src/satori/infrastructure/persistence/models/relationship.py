"""Stage 8 counterparty-specific relationship projection and audit records."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class RelationshipStateRow(Base):
    __tablename__ = "relationship_states"
    __table_args__ = (
        UniqueConstraint("identity_id", "counterparty_id"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("state_version >= 1", name="state_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        *(
            CheckConstraint(f"{name} >= 0.0 AND {name} <= 1.0", name=f"{name}_valid")
            for name in (
                "familiarity",
                "trust",
                "comfort",
                "closeness",
                "intellectual_respect",
                "affection",
            )
        ),
        *(
            CheckConstraint(f"{name} >= 0", name=f"{name}_non_negative")
            for name in (
                "processed_interaction_count",
                "qualified_interaction_count",
                "distinct_session_count",
                "positive_evidence_count",
                "negative_evidence_count",
            )
        ),
    )

    relationship_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    identity_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("satori_identities.identity_id", ondelete="CASCADE"), nullable=False
    )
    counterparty_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    familiarity: Mapped[float] = mapped_column(Float, nullable=False)
    trust: Mapped[float] = mapped_column(Float, nullable=False)
    comfort: Mapped[float] = mapped_column(Float, nullable=False)
    closeness: Mapped[float] = mapped_column(Float, nullable=False)
    intellectual_respect: Mapped[float] = mapped_column(Float, nullable=False)
    affection: Mapped[float] = mapped_column(Float, nullable=False)
    processed_interaction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    qualified_interaction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    distinct_session_count: Mapped[int] = mapped_column(Integer, nullable=False)
    positive_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    negative_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class RelationshipDecisionRow(Base):
    __tablename__ = "relationship_decisions"
    __table_args__ = (
        UniqueConstraint("interaction_id"),
        CheckConstraint("kind IN ('applied', 'skipped', 'rejected')", name="kind_valid"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_valid"),
        CheckConstraint("appraisal_schema_version >= 1", name="appraisal_schema_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint(
            "(kind = 'applied' AND transition_id IS NOT NULL) OR "
            "(kind != 'applied' AND transition_id IS NULL)",
            name="transition_consistent",
        ),
    )

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    relationship_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("relationship_states.relationship_id", ondelete="RESTRICT"),
        nullable=False,
    )
    interaction_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_interactions.interaction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_user_message_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_messages.message_id", ondelete="RESTRICT"),
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    categories: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    appraisal_method: Mapped[str] = mapped_column(String(128), nullable=False)
    appraisal_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    transition_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class RelationshipTransitionRow(Base):
    __tablename__ = "relationship_transitions"
    __table_args__ = (
        UniqueConstraint("interaction_id"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_valid"),
        CheckConstraint("base_state_version >= 1", name="base_state_version_positive"),
        CheckConstraint(
            "resulting_state_version = base_state_version + 1", name="version_increments_once"
        ),
        CheckConstraint("appraisal_schema_version >= 1", name="appraisal_schema_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
    )

    transition_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    relationship_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("relationship_states.relationship_id", ondelete="RESTRICT"),
        nullable=False,
    )
    interaction_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_interactions.interaction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_user_message_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_messages.message_id", ondelete="RESTRICT"),
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    categories: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    base_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state_before: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    applied_delta: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    state_after: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    appraisal_method: Mapped[str] = mapped_column(String(128), nullable=False)
    appraisal_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
