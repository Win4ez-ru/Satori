"""Stage 12-14 reflection lifecycle ORM models."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class ReflectionRunRow(Base):
    __tablename__ = "reflection_runs"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint("attempt_count >= 0 AND attempt_count <= 2", name="attempt_count_valid"),
        CheckConstraint(
            "trigger_kind IN ('automatic', 'explicit_local')", name="trigger_kind_valid"
        ),
        CheckConstraint("purpose IN ('general', 'personality_evolution')", name="purpose_valid"),
        CheckConstraint(
            "(purpose = 'general' AND schema_version IN (1, 2)) OR "
            "(purpose = 'personality_evolution' AND schema_version = 3)",
            name="purpose_schema_consistent",
        ),
        CheckConstraint(
            "status IN ('pending_generation', 'proposals_ready', 'applying', "
            "'completed', 'retryable_failure', 'exhausted')",
            name="status_valid",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    identity_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("satori_identities.identity_id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ReflectionSourceRow(Base):
    __tablename__ = "reflection_sources"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal"),
        UniqueConstraint("run_id", "root_message_id"),
        CheckConstraint("ordinal >= 0 AND ordinal < 12", name="ordinal_valid"),
        CheckConstraint("evidence_edge_version >= 1", name="edge_version_positive"),
        CheckConstraint(
            "kind IN ('position_evidence', 'episodic_memory_evidence')", name="kind_valid"
        ),
        CheckConstraint(
            "affective_state_version IS NULL OR affective_state_version >= 1",
            name="affective_state_version_positive",
        ),
        CheckConstraint(
            "(affective_transition_id IS NULL AND affective_state_version IS NULL "
            "AND affective_signal_hash IS NULL) OR "
            "(affective_transition_id IS NOT NULL AND affective_state_version IS NOT NULL "
            "AND affective_signal_hash IS NOT NULL)",
            name="affective_attachment_all_or_none",
        ),
        CheckConstraint(
            "(upstream_lineage_kind IS NULL AND upstream_lineage_id IS NULL) OR "
            "(upstream_lineage_kind IS NOT NULL AND upstream_lineage_id IS NOT NULL)",
            name="upstream_lineage_all_or_none",
        ),
        CheckConstraint(
            "upstream_lineage_kind IS NULL OR "
            "upstream_lineage_kind IN ('position', 'episodic_memory')",
            name="upstream_lineage_kind_valid",
        ),
    )

    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("reflection_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
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
    root_counterparty_id: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    upstream_lineage_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    upstream_lineage_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    affective_transition_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("affective_transitions.transition_id", ondelete="RESTRICT"),
        nullable=True,
    )
    affective_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    affective_signal_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ReflectionAttemptRow(Base):
    __tablename__ = "reflection_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal"),
        CheckConstraint("ordinal >= 1 AND ordinal <= 2", name="ordinal_valid"),
        CheckConstraint("status IN ('succeeded', 'failed')", name="status_valid"),
    )

    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("reflection_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    formation_method: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    metrics: Mapped[dict[str, int | float | None]] = mapped_column(JSON, nullable=False)


class ReflectionProposalRow(Base):
    __tablename__ = "reflection_proposals"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal"),
        CheckConstraint("ordinal >= 0 AND ordinal < 3", name="ordinal_valid"),
        CheckConstraint(
            "target_owner IN ('satori_positions', 'satori_inclinations', 'personality', 'values')",
            name="target_owner_valid",
        ),
    )

    proposal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("reflection_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    target_owner: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    evidence_source_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ReflectionOutcomeRow(Base):
    __tablename__ = "reflection_outcomes"
    __table_args__ = (
        UniqueConstraint("proposal_id", "target_policy_version"),
        CheckConstraint("target_policy_version >= 1", name="target_policy_version_positive"),
        CheckConstraint("decision IN ('accepted', 'rejected')", name="decision_valid"),
    )

    outcome_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("reflection_proposals.proposal_id", ondelete="CASCADE"),
        nullable=False,
    )
    target_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    target_aggregate_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_aggregate_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
