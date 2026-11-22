"""Derived physical body application proof; independent floor remains authoritative."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class ErasureBodyApplicationRow(Base):
    __tablename__ = "erasure_body_applications"
    action_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_identities.identity_id", ondelete="RESTRICT"),
        nullable=False,
    )
    root_message_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("conversation_messages.message_id", ondelete="RESTRICT"),
        nullable=True,
    )
    floor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lineage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    body_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    __table_args__ = (
        UniqueConstraint("identity_id", "body_revision"),
        CheckConstraint("body_revision >= 1", name="revision_positive"),
        CheckConstraint(
            "json_valid(action) AND coalesce((json_extract(action,'$.proposal.policy_version')="
            "'checkpoint_reason_erasure_v1' "
            "AND root_message_id IS NULL "
            "AND json_extract(action,'$.proposal.scope.identity_id')=identity_id "
            "AND json_extract(action,'$.proposal.scope.lineage_id')=lineage_id "
            "AND json_type(action,'$.proposal.root') IS NULL "
            "AND json_type(action,'$.proposal.intent_request_id') IS NULL) OR "
            "(json_extract(action,'$.proposal.policy_version') IN "
            "('episode_body_erasure_v1','dialogue_body_erasure_v1','knowledge_body_erasure_v1',"
            "'reflection_body_erasure_v1','appraisal_explanation_erasure_v1') "
            "AND root_message_id IS NOT NULL "
            "AND json_extract(action,'$.proposal.root.message_id')=root_message_id "
            "AND json_extract(action,'$.proposal.root.identity_id')=identity_id "
            "AND json_extract(action,'$.proposal.root.lineage_id')=lineage_id),0)=1",
            name="closed_scope_binding",
        ),
    )


class ErasureBodyBindingRow(Base):
    __tablename__ = "erasure_body_bindings"
    identity_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    floor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lineage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    body_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (CheckConstraint("body_revision >= 1", name="revision_positive"),)
