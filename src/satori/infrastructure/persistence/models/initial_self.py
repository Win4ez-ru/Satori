"""Normalized Stage 2 persistence schema models."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class SatoriIdentityRow(Base):
    """One stable primary identity per installation database."""

    __tablename__ = "satori_identities"
    __table_args__ = (
        UniqueConstraint("installation_slot"),
        CheckConstraint("installation_slot = 1", name="primary_slot_is_one"),
        CheckConstraint("length(name) > 0", name="name_not_blank"),
        CheckConstraint("identity_version >= 1", name="identity_version_positive"),
        CheckConstraint("seed_schema_version >= 1", name="seed_schema_version_positive"),
        CheckConstraint("length(seed_content_hash) = 64", name="seed_hash_length"),
    )

    identity_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    installation_slot: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    activation_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    identity_version: Mapped[int] = mapped_column(Integer, nullable=False)
    seed_id: Mapped[str] = mapped_column(String(128), nullable=False)
    seed_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    seed_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class PersonalityStateRow(Base):
    """Version metadata for the current personality projection."""

    __tablename__ = "satori_personality_states"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
    )

    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_identities.identity_id", ondelete="CASCADE"),
        primary_key=True,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PersonalityTraitRow(Base):
    """One current trait projection with its activation baseline."""

    __tablename__ = "satori_personality_traits"
    __table_args__ = (
        CheckConstraint("length(trait_key) > 0", name="trait_key_not_blank"),
        CheckConstraint("value >= 0.0 AND value <= 1.0", name="value_unit_interval"),
        CheckConstraint(
            "baseline_value >= 0.0 AND baseline_value <= 1.0",
            name="baseline_value_unit_interval",
        ),
    )

    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_personality_states.identity_id", ondelete="CASCADE"),
        primary_key=True,
    )
    trait_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_value: Mapped[float] = mapped_column(Float, nullable=False)


class ValueSetRow(Base):
    """Version metadata for the current values projection."""

    __tablename__ = "satori_value_sets"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
    )

    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_identities.identity_id", ondelete="CASCADE"),
        primary_key=True,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ValueRow(Base):
    """One persisted initial value."""

    __tablename__ = "satori_values"
    __table_args__ = (
        CheckConstraint("length(value_key) > 0", name="value_key_not_blank"),
        CheckConstraint("strength >= 0.0 AND strength <= 1.0", name="strength_unit_interval"),
        CheckConstraint("length(description) > 0", name="description_not_blank"),
    )

    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_value_sets.identity_id", ondelete="CASCADE"),
        primary_key=True,
    )
    value_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    strength: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)


class AuditEventRow(Base):
    """Minimal append-only audit record; Stage 2 writes activation only."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("length(event_type) > 0", name="event_type_not_blank"),
        CheckConstraint("length(aggregate_type) > 0", name="aggregate_type_not_blank"),
        CheckConstraint("length(aggregate_id) > 0", name="aggregate_id_not_blank"),
        CheckConstraint("length(trace_id) > 0", name="trace_id_not_blank"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
