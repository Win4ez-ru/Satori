"""Stage 11 identity-global Satori positions, evidence, decisions and revisions."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class SatoriPositionRow(Base):
    __tablename__ = "satori_positions"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("formation_version >= 1", name="formation_version_positive"),
        CheckConstraint("normalization_version >= 1", name="normalization_version_positive"),
        CheckConstraint("kind IN ('fact', 'belief', 'opinion', 'hypothesis')", name="kind_valid"),
        CheckConstraint("stance IN ('support', 'oppose', 'uncertain')", name="stance_valid"),
        CheckConstraint(
            "status IN ('active', 'competing', 'superseded', 'retracted')", name="status_valid"
        ),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_valid"),
        CheckConstraint(
            "(kind = 'opinion' AND value_key IS NOT NULL) OR "
            "(kind != 'opinion' AND value_key IS NULL)",
            name="value_reference_consistent",
        ),
        CheckConstraint(
            "(status = 'competing' AND competing_with_position_id IS NOT NULL) OR "
            "(status != 'competing' AND competing_with_position_id IS NULL)",
            name="competition_consistent",
        ),
        CheckConstraint(
            "(status = 'superseded' AND superseded_by_position_id IS NOT NULL) OR "
            "(status != 'superseded' AND superseded_by_position_id IS NULL)",
            name="supersession_consistent",
        ),
        Index(
            "uq_satori_positions_current_key",
            "identity_id",
            "position_key",
            unique=True,
            sqlite_where=text("status IN ('active', 'competing')"),
        ),
        Index("ix_satori_positions_identity_status", "identity_id", "status", "kind"),
    )

    position_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    position_key: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("satori_identities.identity_id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    formation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    normalization_version: Mapped[int] = mapped_column(Integer, nullable=False)
    proposition: Mapped[str] = mapped_column(String(240), nullable=False)
    normalized_proposition: Mapped[str] = mapped_column(String(240), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    stance: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    value_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    competing_with_position_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("satori_positions.position_id", ondelete="RESTRICT"), nullable=True
    )
    superseded_by_position_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("satori_positions.position_id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PositionEvidenceRow(Base):
    __tablename__ = "satori_position_evidence"
    __table_args__ = (
        UniqueConstraint("position_id", "source_message_id"),
        UniqueConstraint("position_id", "normalized_signature"),
        CheckConstraint(
            "role IN ('argument', 'observation', 'counterexample', 'verified_record')",
            name="role_valid",
        ),
    )

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    position_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("satori_positions.position_id", ondelete="CASCADE"), nullable=False
    )
    source_message_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_messages.message_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_interaction_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_interactions.interaction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_counterparty_id: Mapped[str] = mapped_column(String(128), nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_signature: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PositionFormationDecisionRow(Base):
    __tablename__ = "position_formation_decisions"
    __table_args__ = (
        UniqueConstraint("source_interaction_id", "formation_version"),
        CheckConstraint("formation_version >= 1", name="formation_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("kind IN ('applied', 'skipped', 'rejected')", name="kind_valid"),
        CheckConstraint(
            "created_count >= 0 AND merged_count >= 0 AND superseded_count >= 0 "
            "AND competing_count >= 0 AND rejected_count >= 0",
            name="counts_non_negative",
        ),
    )

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    source_interaction_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_interactions.interaction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_message_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_messages.message_id", ondelete="RESTRICT"),
        nullable=False,
    )
    identity_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("satori_identities.identity_id", ondelete="CASCADE"), nullable=False
    )
    formation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False)
    merged_count: Mapped[int] = mapped_column(Integer, nullable=False)
    superseded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    competing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    position_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    formation_method: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)


class PositionRevisionRow(Base):
    __tablename__ = "satori_position_revisions"
    __table_args__ = (
        UniqueConstraint("position_id", "position_version"),
        CheckConstraint(
            "(decision_id IS NOT NULL AND reflection_outcome_id IS NULL) OR "
            "(decision_id IS NULL AND reflection_outcome_id IS NOT NULL)",
            name="origin_reference_valid",
        ),
        CheckConstraint("position_version >= 1", name="position_version_positive"),
        CheckConstraint(
            "kind IN "
            "('created', 'strengthened', 'weakened', 'competing', 'superseded', 'retracted')",
            name="kind_valid",
        ),
    )

    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    position_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("satori_positions.position_id", ondelete="CASCADE"), nullable=False
    )
    position_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("position_formation_decisions.decision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    reflection_outcome_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("reflection_outcomes.outcome_id", ondelete="RESTRICT"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    prior_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    new_status: Mapped[str] = mapped_column(String(16), nullable=False)
    prior_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SatoriInclinationRow(Base):
    """Identity-global Stage 13 inclination aggregate, separate from epistemic positions."""

    __tablename__ = "satori_inclinations"
    __table_args__ = (
        UniqueConstraint("identity_id", "inclination_key"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("normalization_version >= 1", name="normalization_version_positive"),
        CheckConstraint("kind IN ('interest', 'preference')", name="kind_valid"),
        CheckConstraint("score >= -1.0 AND score <= 1.0", name="score_valid"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_valid"),
        CheckConstraint("stability >= 0.0 AND stability <= 1.0", name="stability_valid"),
        CheckConstraint(
            "(kind = 'interest' AND alternative_topic IS NULL "
            "AND normalized_alternative_topic IS NULL AND score >= 0.0) OR "
            "(kind = 'preference' AND alternative_topic IS NOT NULL "
            "AND normalized_alternative_topic IS NOT NULL)",
            name="kind_shape_valid",
        ),
        Index(
            "ix_satori_inclinations_identity_kind",
            "identity_id",
            "kind",
            "updated_at",
        ),
    )

    inclination_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    inclination_key: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("satori_identities.identity_id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    normalization_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    topic: Mapped[str] = mapped_column(String(96), nullable=False)
    normalized_topic: Mapped[str] = mapped_column(String(96), nullable=False)
    alternative_topic: Mapped[str | None] = mapped_column(String(96), nullable=True)
    normalized_alternative_topic: Mapped[str | None] = mapped_column(String(96), nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    stability: Mapped[float] = mapped_column(Float, nullable=False)
    state_as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_accepted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class InclinationEvidenceRow(Base):
    """Accepted immutable evidence edge for exactly one inclination aggregate."""

    __tablename__ = "satori_inclination_evidence"
    __table_args__ = (
        UniqueConstraint("inclination_id", "reflection_source_id"),
        UniqueConstraint("inclination_id", "affective_transition_id"),
        UniqueConstraint("inclination_id", "source_message_id"),
        UniqueConstraint("inclination_id", "source_interaction_id"),
        UniqueConstraint("inclination_id", "content_signature"),
        CheckConstraint("affective_state_version >= 1", name="affective_state_version_positive"),
        CheckConstraint("role IN ('topic', 'option_a', 'option_b')", name="role_valid"),
        CheckConstraint("signal >= -1.0 AND signal <= 1.0", name="signal_valid"),
        Index(
            "ix_satori_inclination_evidence_inclination_observed",
            "inclination_id",
            "observed_at",
        ),
    )

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    inclination_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_inclinations.inclination_id", ondelete="CASCADE"),
        nullable=False,
    )
    reflection_source_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("reflection_sources.source_id", ondelete="RESTRICT"), nullable=False
    )
    affective_transition_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("affective_transitions.transition_id", ondelete="RESTRICT"),
        nullable=False,
    )
    affective_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    affective_signal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_message_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_messages.message_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_interaction_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_interactions.interaction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_session_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_counterparty_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    signal: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class InclinationRevisionRow(Base):
    """Append-only before/after inclination trajectory."""

    __tablename__ = "satori_inclination_revisions"
    __table_args__ = (
        UniqueConstraint("inclination_id", "inclination_version"),
        UniqueConstraint("reflection_outcome_id"),
        CheckConstraint("inclination_version >= 1", name="inclination_version_positive"),
        CheckConstraint("kind IN ('created', 'strengthened', 'weakened')", name="kind_valid"),
        CheckConstraint(
            "prior_score IS NULL OR (prior_score >= -1.0 AND prior_score <= 1.0)",
            name="prior_score_valid",
        ),
        CheckConstraint("new_score >= -1.0 AND new_score <= 1.0", name="new_score_valid"),
        CheckConstraint(
            "applied_delta >= -1.0 AND applied_delta <= 1.0", name="applied_delta_valid"
        ),
        CheckConstraint(
            "prior_confidence IS NULL OR (prior_confidence >= 0.0 AND prior_confidence <= 1.0)",
            name="prior_confidence_valid",
        ),
        CheckConstraint(
            "new_confidence >= 0.0 AND new_confidence <= 1.0", name="new_confidence_valid"
        ),
        CheckConstraint(
            "prior_stability IS NULL OR (prior_stability >= 0.0 AND prior_stability <= 1.0)",
            name="prior_stability_valid",
        ),
        CheckConstraint(
            "new_stability >= 0.0 AND new_stability <= 1.0", name="new_stability_valid"
        ),
        CheckConstraint(
            "(kind = 'created' AND inclination_version = 1 AND prior_score IS NULL "
            "AND prior_confidence IS NULL AND prior_stability IS NULL) OR "
            "(kind != 'created' AND inclination_version > 1 AND prior_score IS NOT NULL "
            "AND prior_confidence IS NOT NULL AND prior_stability IS NOT NULL)",
            name="prior_state_consistent",
        ),
    )

    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    inclination_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_inclinations.inclination_id", ondelete="CASCADE"),
        nullable=False,
    )
    inclination_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reflection_outcome_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("reflection_outcomes.outcome_id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    prior_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_score: Mapped[float] = mapped_column(Float, nullable=False)
    applied_delta: Mapped[float] = mapped_column(Float, nullable=False)
    prior_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    prior_stability: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_stability: Mapped[float] = mapped_column(Float, nullable=False)
    state_as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
