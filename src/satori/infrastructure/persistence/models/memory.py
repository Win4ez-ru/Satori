"""Stage 4 episodic-memory, evidence, and formation-decision ORM models."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class EpisodicMemoryRow(Base):
    """Selective derived event representation with a rebuildable source seam."""

    __tablename__ = "episodic_memories"
    __table_args__ = (
        UniqueConstraint("source_interaction_id", "formation_version"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint("importance >= 0.0 AND importance <= 1.0", name="importance_unit_interval"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_unit_interval"),
        CheckConstraint("formation_version >= 1", name="formation_version_positive"),
        CheckConstraint(
            "lifecycle_status IN ('active', 'archived')", name="lifecycle_status_valid"
        ),
        CheckConstraint(
            "(body_state = 'available' AND summary IS NOT NULL AND length(summary) > 0 "
            "AND body_action_id IS NULL) OR (body_state = 'erased' AND summary IS NULL "
            "AND body_action_id IS NOT NULL)",
            name="body_consistent",
        ),
    )

    memory_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_interaction_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_interactions.interaction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    importance: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    formation_method: Mapped[str] = mapped_column(String(128), nullable=False)
    formation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    body_state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="available")
    body_action_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("erasure_body_applications.action_id", ondelete="RESTRICT")
    )


class MemoryLifecycleDecisionRow(Base):
    """One append-only local archival approval or accepted repeated-target no-op."""

    __tablename__ = "memory_lifecycle_decisions"
    __table_args__ = (
        UniqueConstraint("request_id"),
        CheckConstraint("length(trim(request_id)) > 0", name="request_id_not_blank"),
        CheckConstraint("length(trim(counterparty_id)) > 0", name="counterparty_id_not_blank"),
        CheckConstraint("from_status IN ('active', 'archived')", name="from_status_valid"),
        CheckConstraint("to_status IN ('active', 'archived')", name="to_status_valid"),
        CheckConstraint("expected_version >= 1", name="expected_version_positive"),
        CheckConstraint("result_version >= 1", name="result_version_positive"),
        CheckConstraint(
            "(from_status = to_status AND result_version = expected_version) OR "
            "(from_status != to_status AND result_version = expected_version + 1)",
            name="transition_version_consistent",
        ),
        CheckConstraint("policy_version = 1", name="policy_version_valid"),
        CheckConstraint("approved_by = 'local_operator'", name="approval_valid"),
        CheckConstraint(
            "(from_status = to_status AND reason_code = 'episode_lifecycle_noop') OR "
            "(from_status != to_status AND to_status = 'archived' "
            "AND reason_code = 'episode_archived') OR "
            "(from_status != to_status AND to_status = 'active' "
            "AND reason_code = 'episode_reactivated')",
            name="reason_consistent",
        ),
        CheckConstraint(
            "length(preview_digest) = 64 AND preview_digest NOT GLOB '*[^0-9a-f]*'",
            name="preview_digest_valid",
        ),
    )

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    memory_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("episodic_memories.memory_id", ondelete="RESTRICT"), nullable=False
    )
    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_identities.identity_id", ondelete="RESTRICT"),
        nullable=False,
    )
    counterparty_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class MemoryEvidenceRow(Base):
    """Exact source-message span supporting one episode."""

    __tablename__ = "memory_evidence"
    __table_args__ = (
        UniqueConstraint("memory_id", "source_message_id", "quote"),
        CheckConstraint(
            "provenance_kind = 'explicit_user_statement'",
            name="provenance_kind_valid",
        ),
        CheckConstraint(
            "(body_state = 'available' AND quote IS NOT NULL AND length(quote) > 0 "
            "AND body_action_id IS NULL) OR (body_state = 'erased' AND quote IS NULL "
            "AND body_action_id IS NOT NULL)",
            name="body_consistent",
        ),
    )

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("episodic_memories.memory_id", ondelete="CASCADE"),
        nullable=False,
    )
    source_message_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_messages.message_id", ondelete="RESTRICT"),
        nullable=False,
    )
    provenance_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    quote: Mapped[str | None] = mapped_column(String(500), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    body_state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="available")
    body_action_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("erasure_body_applications.action_id", ondelete="RESTRICT")
    )


class EpisodeFormationDecisionRow(Base):
    """Terminal idempotent create/skip/reject owner decision."""

    __tablename__ = "episode_formation_decisions"
    __table_args__ = (
        UniqueConstraint("source_interaction_id", "formation_version"),
        CheckConstraint("formation_version >= 1", name="formation_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("kind IN ('created', 'skipped', 'rejected')", name="kind_valid"),
        CheckConstraint(
            "(kind = 'created' AND memory_id IS NOT NULL) OR "
            "(kind != 'created' AND memory_id IS NULL)",
            name="kind_memory_consistent",
        ),
    )

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    source_interaction_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_interactions.interaction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    formation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    formation_method: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    memory_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("episodic_memories.memory_id", ondelete="RESTRICT"),
        nullable=True,
    )
