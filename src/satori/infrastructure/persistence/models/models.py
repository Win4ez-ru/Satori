"""Stage 9 counterparty-scoped user/world claims and provenance."""

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime

CLAIM_STATUS_CHECK = "status IN ('current', 'superseded', 'disputed', 'retracted', 'expired')"
EPISTEMIC_KIND_CHECK = "epistemic_kind IN ('explicit_fact', 'inference', 'hypothesis')"


class UserModelClaimRow(Base):
    __tablename__ = "user_model_claims"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("formation_version >= 1", name="formation_version_positive"),
        CheckConstraint("normalization_version >= 1", name="normalization_version_positive"),
        CheckConstraint("value_kind IN ('text', 'number', 'boolean')", name="value_kind_valid"),
        CheckConstraint(EPISTEMIC_KIND_CHECK, name="epistemic_kind_valid"),
        CheckConstraint(CLAIM_STATUS_CHECK, name="status_valid"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_valid"),
        CheckConstraint(
            "(status = 'superseded' AND superseded_by_claim_id IS NOT NULL) OR "
            "(status != 'superseded' AND superseded_by_claim_id IS NULL)",
            name="supersession_consistent",
        ),
        Index(
            "uq_user_model_claims_current_key",
            "claim_key",
            unique=True,
            sqlite_where=text("status = 'current'"),
        ),
        Index(
            "ix_user_model_claims_partition_current",
            "identity_id",
            "counterparty_id",
            "status",
            "predicate",
        ),
    )

    claim_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_key: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("satori_identities.identity_id", ondelete="CASCADE"), nullable=False
    )
    counterparty_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    formation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    normalization_version: Mapped[int] = mapped_column(Integer, nullable=False)
    predicate: Mapped[str] = mapped_column(String(64), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[object] = mapped_column(JSON, nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(160), nullable=False)
    epistemic_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    superseded_by_claim_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("user_model_claims.claim_id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WorldModelClaimRow(Base):
    __tablename__ = "world_model_claims"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("formation_version >= 1", name="formation_version_positive"),
        CheckConstraint("normalization_version >= 1", name="normalization_version_positive"),
        CheckConstraint(
            "subject_kind IN ('project', 'situation', 'commitment', 'outcome')",
            name="subject_kind_valid",
        ),
        CheckConstraint("predicate = 'status'", name="predicate_valid"),
        CheckConstraint("value_kind = 'text'", name="value_kind_valid"),
        CheckConstraint(EPISTEMIC_KIND_CHECK, name="epistemic_kind_valid"),
        CheckConstraint(CLAIM_STATUS_CHECK, name="status_valid"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_valid"),
        CheckConstraint(
            "(status = 'superseded' AND superseded_by_claim_id IS NOT NULL) OR "
            "(status != 'superseded' AND superseded_by_claim_id IS NULL)",
            name="supersession_consistent",
        ),
        Index(
            "uq_world_model_claims_current_key",
            "claim_key",
            unique=True,
            sqlite_where=text("status = 'current'"),
        ),
        Index(
            "ix_world_model_claims_partition_current",
            "identity_id",
            "counterparty_id",
            "status",
            "subject_kind",
        ),
    )

    claim_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_key: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("satori_identities.identity_id", ondelete="CASCADE"), nullable=False
    )
    counterparty_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    formation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    normalization_version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_label: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_subject_label: Mapped[str] = mapped_column(String(120), nullable=False)
    predicate: Mapped[str] = mapped_column(String(64), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[object] = mapped_column(JSON, nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(160), nullable=False)
    epistemic_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    superseded_by_claim_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("world_model_claims.claim_id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class UserModelClaimEvidenceRow(Base):
    __tablename__ = "user_model_claim_evidence"
    __table_args__ = (UniqueConstraint("claim_id", "source_message_id"),)

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("user_model_claims.claim_id", ondelete="CASCADE"), nullable=False
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
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WorldModelClaimEvidenceRow(Base):
    __tablename__ = "world_model_claim_evidence"
    __table_args__ = (UniqueConstraint("claim_id", "source_message_id"),)

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("world_model_claims.claim_id", ondelete="CASCADE"), nullable=False
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
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ModelFormationDecisionRow(Base):
    __tablename__ = "model_formation_decisions"
    __table_args__ = (
        UniqueConstraint("source_interaction_id", "formation_version"),
        CheckConstraint("formation_version >= 1", name="formation_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("kind IN ('applied', 'skipped', 'rejected')", name="kind_valid"),
    )

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
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
    counterparty_id: Mapped[str] = mapped_column(String(128), nullable=False)
    formation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    user_created_count: Mapped[int] = mapped_column(Integer, nullable=False)
    user_merged_count: Mapped[int] = mapped_column(Integer, nullable=False)
    user_superseded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    user_disputed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    user_rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    world_created_count: Mapped[int] = mapped_column(Integer, nullable=False)
    world_merged_count: Mapped[int] = mapped_column(Integer, nullable=False)
    world_superseded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    world_disputed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    world_rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    user_claim_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    world_claim_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    formation_method: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)


class UserModelClaimRevisionRow(Base):
    __tablename__ = "user_model_claim_revisions"
    __table_args__ = (
        UniqueConstraint("claim_id", "claim_version"),
        CheckConstraint("claim_version >= 1", name="claim_version_positive"),
    )

    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("user_model_claims.claim_id", ondelete="CASCADE"), nullable=False
    )
    claim_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("model_formation_decisions.decision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    prior_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    new_status: Mapped[str] = mapped_column(String(16), nullable=False)
    prior_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    prior_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    new_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WorldModelClaimRevisionRow(Base):
    __tablename__ = "world_model_claim_revisions"
    __table_args__ = (
        UniqueConstraint("claim_id", "claim_version"),
        CheckConstraint("claim_version >= 1", name="claim_version_positive"),
    )

    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("world_model_claims.claim_id", ondelete="CASCADE"), nullable=False
    )
    claim_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("model_formation_decisions.decision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    prior_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    new_status: Mapped[str] = mapped_column(String(16), nullable=False)
    prior_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    prior_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    new_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
