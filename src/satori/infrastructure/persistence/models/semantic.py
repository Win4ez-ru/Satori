"""Stage 6 canonical semantic claims, provenance, decisions, and revisions."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class SemanticClaimRow(Base):
    """One canonical typed claim aggregate; old lifecycle states are retained."""

    __tablename__ = "semantic_claims"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint("subject = 'user'", name="subject_valid"),
        CheckConstraint("value_kind IN ('text', 'number', 'boolean')", name="value_kind_valid"),
        CheckConstraint(
            "claim_kind IN "
            "('explicit_fact', 'inferred_fact', 'hypothesis', 'attributed_statement')",
            name="claim_kind_valid",
        ),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_valid"),
        CheckConstraint(
            "status IN ('active', 'superseded', 'disputed', 'retracted')",
            name="status_valid",
        ),
        CheckConstraint("formation_version >= 1", name="formation_version_positive"),
        CheckConstraint("normalization_version >= 1", name="normalization_version_positive"),
        CheckConstraint("length(predicate) > 0", name="predicate_not_blank"),
        CheckConstraint("length(normalized_value) > 0", name="normalized_value_not_blank"),
        CheckConstraint(
            "(status = 'superseded' AND superseded_by_claim_id IS NOT NULL) OR "
            "(status != 'superseded' AND superseded_by_claim_id IS NULL)",
            name="supersession_consistent",
        ),
        Index(
            "uq_semantic_claims_active_key",
            "claim_key",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
        Index("ix_semantic_claims_active_predicate", "status", "subject", "predicate"),
    )

    claim_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_key: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    predicate: Mapped[str] = mapped_column(String(64), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[object] = mapped_column(JSON, nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(500), nullable=False)
    polarity: Mapped[bool] = mapped_column(Boolean, nullable=False)
    claim_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    superseded_by_claim_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("semantic_claims.claim_id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    formation_method: Mapped[str] = mapped_column(String(128), nullable=False)
    formation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    normalization_version: Mapped[int] = mapped_column(Integer, nullable=False)


class SemanticClaimEvidenceRow(Base):
    """Evidence lineage down to one root user message and interaction."""

    __tablename__ = "semantic_claim_evidence"
    __table_args__ = (
        UniqueConstraint("claim_id", "root_message_id"),
        CheckConstraint(
            "source_kind IN ('explicit_user_statement', 'episode_inference')",
            name="source_kind_valid",
        ),
    )

    semantic_evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("semantic_claims.claim_id", ondelete="CASCADE"), nullable=False
    )
    memory_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("episodic_memories.memory_id", ondelete="RESTRICT"), nullable=False
    )
    memory_evidence_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("memory_evidence.evidence_id", ondelete="RESTRICT"), nullable=False
    )
    root_message_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_messages.message_id", ondelete="RESTRICT"),
        nullable=False,
    )
    root_interaction_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_interactions.interaction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SemanticFormationDecisionRow(Base):
    """Terminal processing state for retry/backfill/restart safety."""

    __tablename__ = "semantic_formation_decisions"
    __table_args__ = (
        UniqueConstraint("source_memory_id", "formation_version"),
        CheckConstraint("formation_version >= 1", name="formation_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("kind IN ('applied', 'skipped', 'rejected')", name="kind_valid"),
        CheckConstraint(
            "created_count >= 0 AND merged_count >= 0 AND superseded_count >= 0 "
            "AND disputed_count >= 0 AND rejected_count >= 0",
            name="counts_non_negative",
        ),
    )

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    source_memory_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("episodic_memories.memory_id", ondelete="RESTRICT"), nullable=False
    )
    formation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False)
    merged_count: Mapped[int] = mapped_column(Integer, nullable=False)
    superseded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    disputed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    formation_method: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)


class SemanticClaimRevisionRow(Base):
    """Append-only claim transition used by history/inspect and audit reconstruction."""

    __tablename__ = "semantic_claim_revisions"
    __table_args__ = (
        UniqueConstraint("claim_id", "claim_version"),
        CheckConstraint("claim_version >= 1", name="claim_version_positive"),
        CheckConstraint(
            "kind IN ('created', 'strengthened', 'superseded', 'disputed', 'retracted')",
            name="kind_valid",
        ),
    )

    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("semantic_claims.claim_id", ondelete="CASCADE"), nullable=False
    )
    claim_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("semantic_formation_decisions.decision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    prior_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    new_status: Mapped[str] = mapped_column(String(16), nullable=False)
    prior_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
