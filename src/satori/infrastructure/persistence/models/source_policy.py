"""Derived policy/provenance and owner withdrawal metadata; no stored source text."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class SourcePolicyBindingRow(Base):
    __tablename__ = "source_policy_bindings"
    identity_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    floor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lineage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (CheckConstraint("policy_generation >= 0", name="generation_nonnegative"),)


class SourceExcludedRootRow(Base):
    __tablename__ = "source_excluded_roots"
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    identity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_generation: Mapped[int] = mapped_column(Integer, nullable=False)


class SourceInputRow(Base):
    __tablename__ = "source_inputs"
    input_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    identity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    proof_status: Mapped[str] = mapped_column(String(16), nullable=False)
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    root_message_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    payload_refs: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        CheckConstraint("proof_status IN ('complete','unknown','overflow')", name="proof_valid"),
    )


class SourcePayloadRow(Base):
    __tablename__ = "source_payload_receipts"
    kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    proof_status: Mapped[str] = mapped_column(String(16), nullable=False)
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    root_message_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    payload_refs: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    __table_args__ = (
        CheckConstraint("payload_version >= 1", name="version_positive"),
        CheckConstraint("proof_status IN ('complete','unknown','overflow')", name="proof_valid"),
    )


class OwnerSourceWithdrawalRow(Base):
    __tablename__ = "owner_source_withdrawals"
    receipt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    before_status: Mapped[str] = mapped_column(String(32), nullable=False)
    after_status: Mapped[str] = mapped_column(String(32), nullable=False)
    before_version: Mapped[int] = mapped_column(Integer, nullable=False)
    after_version: Mapped[int] = mapped_column(Integer, nullable=False)
    root_message_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    __table_args__ = (
        UniqueConstraint("owner", "aggregate_id", "request_id"),
        CheckConstraint("after_version >= before_version", name="version_nondecreasing"),
    )


class SourceOwnerRepairRow(Base):
    """Owner reconciliation generation; independent from the readable mirror head."""

    __tablename__ = "source_owner_repairs"
    owner: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    policy_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (CheckConstraint("policy_generation >= 0", name="generation_nonnegative"),)
