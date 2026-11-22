"""Derived preparation acknowledgement; no content executor or floor authority."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class ErasurePolicyBindingRow(Base):
    __tablename__ = "erasure_policy_bindings"
    identity_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    floor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lineage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    erasure_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (CheckConstraint("erasure_revision >= 1", name="revision_positive"),)


class ErasurePreparationRow(Base):
    __tablename__ = "erasure_preparations"
    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    preparation_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_identities.identity_id", ondelete="RESTRICT"),
        nullable=False,
    )
    counterparty_id: Mapped[str] = mapped_column(String(128), nullable=False)
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
    root_session_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    floor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    installation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lineage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    retention_policy: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_source_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_erasure_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    result_erasure_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_preview_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_schema_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    intent_schema_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_store_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_routing_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_assessment_status: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    store_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    routing_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    retention_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    preparation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_status: Mapped[str] = mapped_column(String(16), nullable=False)
    body_readiness: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    prepared_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    __table_args__ = (
        CheckConstraint("preparation_revision >= 1", name="preparation_revision_positive"),
        CheckConstraint("contract_version = 1", name="contract_version_valid"),
        CheckConstraint(
            "expected_erasure_revision >= 0 AND result_erasure_revision = "
            "expected_erasure_revision + 1",
            name="erasure_transition_valid",
        ),
        CheckConstraint(
            "source_generation >= expected_source_generation AND expected_source_generation >= 0",
            name="source_generation_valid",
        ),
        CheckConstraint(
            "assessment_status IN ('complete','blocked') AND intent_assessment_status IN "
            "('complete','blocked')",
            name="assessment_valid",
        ),
        CheckConstraint(
            "body_readiness IN "
            "('retain_only','blocked_executor','blocked_owner_policy',"
            "'blocked_provenance')",
            name="body_readiness_valid",
        ),
        CheckConstraint(
            "owner IN "
            "('interaction_log','memory_manager','retrieval_index','semantic_memory',"
            "'current_models','positions','reflection','affect','relationship',"
            "'personality','audit','initial_self','initial_values','source_policy',"
            "'schema_registry')",
            name="owner_valid",
        ),
    )
