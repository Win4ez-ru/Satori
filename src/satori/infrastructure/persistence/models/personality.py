"""Stage 14 append-only personality evolution ORM models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class PersonalityCheckpointRow(Base):
    """Immutable complete personality vector at one aggregate version."""

    __tablename__ = "personality_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "identity_id",
            "source_aggregate_version",
            "checkpoint_kind",
            name="uq_personality_checkpoints_identity_version_kind",
        ),
        UniqueConstraint(
            "identity_id",
            "checkpoint_hash",
            name="uq_personality_checkpoints_identity_hash",
        ),
        CheckConstraint("personality_schema_version >= 1", name="personality_schema_positive"),
        CheckConstraint("source_aggregate_version >= 1", name="source_aggregate_positive"),
        CheckConstraint("hash_schema_version >= 1", name="hash_schema_positive"),
        CheckConstraint(
            "checkpoint_kind IN ('activation', 'evolution', 'restore', 'manual')",
            name="checkpoint_kind_valid",
        ),
        CheckConstraint("length(checkpoint_hash) = 64", name="checkpoint_hash_length"),
        Index(
            "ix_personality_checkpoints_identity_version",
            "identity_id",
            "source_aggregate_version",
        ),
    )

    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_personality_states.identity_id", ondelete="CASCADE"),
        nullable=False,
    )
    personality_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    hash_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PersonalityCheckpointTraitRow(Base):
    """One immutable member of a checkpoint's complete trait vector."""

    __tablename__ = "personality_checkpoint_traits"
    __table_args__ = (
        CheckConstraint("length(trait_key) > 0", name="trait_key_not_blank"),
        CheckConstraint("value >= 0.0 AND value <= 1.0", name="value_unit_interval"),
        CheckConstraint(
            "baseline_value >= 0.0 AND baseline_value <= 1.0",
            name="baseline_value_unit_interval",
        ),
    )

    checkpoint_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("personality_checkpoints.checkpoint_id", ondelete="CASCADE"),
        primary_key=True,
    )
    trait_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_value: Mapped[float] = mapped_column(Float, nullable=False)


class PersonalityRevisionRow(Base):
    """Append-only accepted evolution or restore trajectory record."""

    __tablename__ = "personality_revisions"
    __table_args__ = (
        UniqueConstraint("identity_id", "after_aggregate_version"),
        UniqueConstraint("reflection_outcome_id"),
        UniqueConstraint("resulting_checkpoint_id"),
        CheckConstraint("revision_kind IN ('evolution', 'restore')", name="revision_kind_valid"),
        CheckConstraint("before_aggregate_version >= 1", name="before_aggregate_positive"),
        CheckConstraint("after_aggregate_version >= 2", name="after_aggregate_positive"),
        CheckConstraint(
            "after_aggregate_version = before_aggregate_version + 1",
            name="aggregate_versions_consecutive",
        ),
        CheckConstraint(
            "source_checkpoint_id != resulting_checkpoint_id",
            name="checkpoint_lineage_distinct",
        ),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("length(reason_code) > 0", name="reason_code_not_blank"),
        CheckConstraint(
            "direction IS NULL OR direction IN ('increase', 'decrease')",
            name="direction_valid",
        ),
        CheckConstraint(
            "before_value IS NULL OR (before_value >= 0.0 AND before_value <= 1.0)",
            name="before_value_valid",
        ),
        CheckConstraint(
            "after_value IS NULL OR (after_value >= 0.0 AND after_value <= 1.0)",
            name="after_value_valid",
        ),
        CheckConstraint(
            "applied_delta IS NULL OR applied_delta IN (-0.005, 0.005)",
            name="applied_delta_exact",
        ),
        CheckConstraint(
            "decision_confidence IS NULL OR "
            "(decision_confidence >= 0.0 AND decision_confidence <= 1.0)",
            name="decision_confidence_valid",
        ),
        CheckConstraint(
            "activation_distance_linf >= 0.0 AND activation_distance_l1 >= 0.0 "
            "AND approved_checkpoint_distance_linf >= 0.0 "
            "AND approved_checkpoint_distance_l1 >= 0.0 "
            "AND rolling_total_path >= 0.0 AND lifetime_total_path >= 0.0",
            name="aggregate_metrics_non_negative",
        ),
        CheckConstraint(
            "rolling_trait_path IS NULL OR rolling_trait_path >= 0.0",
            name="rolling_trait_path_non_negative",
        ),
        CheckConstraint(
            "lifetime_trait_path IS NULL OR lifetime_trait_path >= 0.0",
            name="lifetime_trait_path_non_negative",
        ),
        CheckConstraint(
            "(revision_kind = 'evolution' AND trait_key IS NOT NULL "
            "AND direction IS NOT NULL AND before_value IS NOT NULL "
            "AND after_value IS NOT NULL AND applied_delta IS NOT NULL "
            "AND decision_confidence IS NOT NULL AND rolling_trait_path IS NOT NULL "
            "AND lifetime_trait_path IS NOT NULL AND reflection_outcome_id IS NOT NULL) OR "
            "(revision_kind = 'restore' AND trait_key IS NULL AND direction IS NULL "
            "AND before_value IS NULL AND after_value IS NULL AND applied_delta IS NULL "
            "AND decision_confidence IS NULL AND rolling_trait_path IS NULL "
            "AND lifetime_trait_path IS NULL AND reflection_outcome_id IS NULL)",
            name="revision_shape_valid",
        ),
        CheckConstraint(
            "(direction = 'increase' AND applied_delta = 0.005) OR "
            "(direction = 'decrease' AND applied_delta = -0.005) OR "
            "(direction IS NULL AND applied_delta IS NULL)",
            name="direction_delta_consistent",
        ),
        Index(
            "ix_personality_revisions_identity_occurred",
            "identity_id",
            "occurred_at",
        ),
    )

    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_personality_states.identity_id", ondelete="CASCADE"),
        nullable=False,
    )
    revision_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    before_aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    after_aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    trait_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    before_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    after_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    applied_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_checkpoint_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("personality_checkpoints.checkpoint_id", ondelete="RESTRICT"),
        nullable=False,
    )
    resulting_checkpoint_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("personality_checkpoints.checkpoint_id", ondelete="RESTRICT"),
        nullable=False,
    )
    reflection_outcome_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("reflection_outcomes.outcome_id", ondelete="RESTRICT"),
        nullable=True,
    )
    trait_diffs: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    activation_distance_linf: Mapped[float] = mapped_column(Float, nullable=False)
    activation_distance_l1: Mapped[float] = mapped_column(Float, nullable=False)
    approved_checkpoint_distance_linf: Mapped[float] = mapped_column(Float, nullable=False)
    approved_checkpoint_distance_l1: Mapped[float] = mapped_column(Float, nullable=False)
    rolling_trait_path: Mapped[float | None] = mapped_column(Float, nullable=True)
    rolling_total_path: Mapped[float] = mapped_column(Float, nullable=False)
    lifetime_trait_path: Mapped[float | None] = mapped_column(Float, nullable=True)
    lifetime_total_path: Mapped[float] = mapped_column(Float, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PersonalityEvidenceRow(Base):
    """Canonical accepted source reference; raw source text is never duplicated."""

    __tablename__ = "personality_evidence"
    __table_args__ = (
        UniqueConstraint("root_message_id"),
        UniqueConstraint("reflection_source_id"),
        CheckConstraint("length(trait_key) > 0", name="trait_key_not_blank"),
        CheckConstraint("direction IN ('increase', 'decrease')", name="direction_valid"),
        CheckConstraint(
            "citation_role IN ('support', 'counterevidence')",
            name="citation_role_valid",
        ),
        CheckConstraint("evidence_edge_version >= 1", name="evidence_edge_version_positive"),
        CheckConstraint(
            "upstream_lineage_kind IN ('position', 'episodic_memory')",
            name="upstream_lineage_kind_valid",
        ),
        CheckConstraint("length(content_hash) = 64", name="content_hash_length"),
        CheckConstraint(
            "length(normalized_signature) = 64",
            name="normalized_signature_length",
        ),
        CheckConstraint("accepted_at >= observed_at", name="accepted_after_observed"),
        Index(
            "ix_personality_evidence_identity_observed",
            "identity_id",
            "observed_at",
        ),
    )

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("personality_revisions.revision_id", ondelete="CASCADE"),
        nullable=False,
    )
    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_personality_states.identity_id", ondelete="CASCADE"),
        nullable=False,
    )
    trait_key: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    reflection_run_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("reflection_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    reflection_proposal_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("reflection_proposals.proposal_id", ondelete="RESTRICT"),
        nullable=False,
    )
    reflection_source_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("reflection_sources.source_id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_edge_id: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_edge_version: Mapped[int] = mapped_column(Integer, nullable=False)
    root_interaction_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_interactions.interaction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    root_message_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_messages.message_id", ondelete="RESTRICT"),
        nullable=False,
    )
    root_session_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    root_counterparty_id: Mapped[str] = mapped_column(String(128), nullable=False)
    upstream_lineage_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    upstream_lineage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    citation_role: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PersonalityCheckpointApprovalRow(Base):
    """Explicit local selection of a reviewed checkpoint as budget origin."""

    __tablename__ = "personality_checkpoint_approvals"
    __table_args__ = (
        UniqueConstraint("checkpoint_id"),
        CheckConstraint("length(checkpoint_hash) = 64", name="checkpoint_hash_length"),
        CheckConstraint("expected_aggregate_version >= 1", name="expected_aggregate_positive"),
        CheckConstraint("length(reason) > 0", name="reason_not_blank"),
        Index(
            "ix_personality_checkpoint_approvals_identity_approved",
            "identity_id",
            "approved_at",
        ),
    )

    approval_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_personality_states.identity_id", ondelete="CASCADE"),
        nullable=False,
    )
    checkpoint_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("personality_checkpoints.checkpoint_id", ondelete="RESTRICT"),
        nullable=False,
    )
    checkpoint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PersonalityRestoreEventRow(Base):
    """Append-only explicit checkpoint restore with typed JSON trait diffs."""

    __tablename__ = "personality_restore_events"
    __table_args__ = (
        UniqueConstraint("revision_id"),
        UniqueConstraint("identity_id", "after_aggregate_version"),
        UniqueConstraint("resulting_checkpoint_id"),
        CheckConstraint("length(source_checkpoint_hash) = 64", name="source_hash_length"),
        CheckConstraint("before_aggregate_version >= 1", name="before_aggregate_positive"),
        CheckConstraint("after_aggregate_version >= 2", name="after_aggregate_positive"),
        CheckConstraint(
            "after_aggregate_version = before_aggregate_version + 1",
            name="aggregate_versions_consecutive",
        ),
        CheckConstraint(
            "source_checkpoint_id != resulting_checkpoint_id",
            name="checkpoint_lineage_distinct",
        ),
        CheckConstraint("length(reason) > 0", name="reason_not_blank"),
        Index(
            "ix_personality_restore_events_identity_restored",
            "identity_id",
            "restored_at",
        ),
    )

    restore_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("personality_revisions.revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_personality_states.identity_id", ondelete="CASCADE"),
        nullable=False,
    )
    source_checkpoint_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("personality_checkpoints.checkpoint_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_checkpoint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resulting_checkpoint_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("personality_checkpoints.checkpoint_id", ondelete="RESTRICT"),
        nullable=False,
    )
    before_aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    after_aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    trait_diffs: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    restored_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
