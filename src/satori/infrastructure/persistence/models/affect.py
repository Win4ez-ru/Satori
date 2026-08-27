"""Stage 7 authoritative affective projection and append-only transition rows."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class AffectiveStateRow(Base):
    """One current emotion+mood projection per persistent identity."""

    __tablename__ = "affective_states"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("state_version >= 1", name="state_version_positive"),
        CheckConstraint("mood_version >= 1", name="mood_version_positive"),
        CheckConstraint("emotion_policy_version >= 1", name="emotion_policy_version_positive"),
        CheckConstraint("appraisal_schema_version >= 1", name="appraisal_schema_version_positive"),
        CheckConstraint("mood_policy_version >= 1", name="mood_policy_version_positive"),
        CheckConstraint("valence >= -1.0 AND valence <= 1.0", name="valence_valid"),
        CheckConstraint("arousal >= 0.0 AND arousal <= 1.0", name="arousal_valid"),
        CheckConstraint("tension >= 0.0 AND tension <= 1.0", name="tension_valid"),
        CheckConstraint("curiosity >= 0.0 AND curiosity <= 1.0", name="curiosity_valid"),
        CheckConstraint("interest >= 0.0 AND interest <= 1.0", name="interest_valid"),
        CheckConstraint("amusement >= 0.0 AND amusement <= 1.0", name="amusement_valid"),
        CheckConstraint("concern >= 0.0 AND concern <= 1.0", name="concern_valid"),
        CheckConstraint("frustration >= 0.0 AND frustration <= 1.0", name="frustration_valid"),
        CheckConstraint(
            "situational_confidence >= 0.0 AND situational_confidence <= 1.0",
            name="situational_confidence_valid",
        ),
        CheckConstraint("mood_valence >= -1.0 AND mood_valence <= 1.0", name="mood_valence_valid"),
        CheckConstraint("mood_energy >= 0.0 AND mood_energy <= 1.0", name="mood_energy_valid"),
        CheckConstraint("mood_tension >= 0.0 AND mood_tension <= 1.0", name="mood_tension_valid"),
    )

    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_identities.identity_id", ondelete="CASCADE"),
        primary_key=True,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mood_version: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    emotion_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    appraisal_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mood_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    valence: Mapped[float] = mapped_column(Float, nullable=False)
    arousal: Mapped[float] = mapped_column(Float, nullable=False)
    tension: Mapped[float] = mapped_column(Float, nullable=False)
    curiosity: Mapped[float] = mapped_column(Float, nullable=False)
    interest: Mapped[float] = mapped_column(Float, nullable=False)
    amusement: Mapped[float] = mapped_column(Float, nullable=False)
    concern: Mapped[float] = mapped_column(Float, nullable=False)
    frustration: Mapped[float] = mapped_column(Float, nullable=False)
    situational_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    mood_valence: Mapped[float] = mapped_column(Float, nullable=False)
    mood_energy: Mapped[float] = mapped_column(Float, nullable=False)
    mood_tension: Mapped[float] = mapped_column(Float, nullable=False)


class AffectiveTransitionRow(Base):
    """Source-linked audit/replay trail without duplicated conversation content."""

    __tablename__ = "affective_transitions"
    __table_args__ = (
        UniqueConstraint("interaction_id"),
        CheckConstraint("appraisal_schema_version >= 1", name="appraisal_schema_version_positive"),
        CheckConstraint("emotion_policy_version >= 1", name="emotion_policy_version_positive"),
        CheckConstraint("mood_policy_version >= 1", name="mood_policy_version_positive"),
        CheckConstraint("base_state_version >= 1", name="base_state_version_positive"),
        CheckConstraint(
            "resulting_state_version = base_state_version + 1",
            name="state_version_increments_once",
        ),
        CheckConstraint("base_mood_version >= 1", name="base_mood_version_positive"),
        CheckConstraint(
            "resulting_mood_version = base_mood_version + 1",
            name="mood_version_increments_once",
        ),
        CheckConstraint(
            "appraisal_confidence >= 0.0 AND appraisal_confidence <= 1.0",
            name="appraisal_confidence_valid",
        ),
    )

    transition_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_identities.identity_id", ondelete="RESTRICT"),
        nullable=False,
    )
    interaction_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_interactions.interaction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_message_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_messages.message_id", ondelete="RESTRICT"),
        nullable=False,
    )
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    appraisal_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    emotion_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mood_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    base_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    base_mood_version: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_mood_version: Mapped[int] = mapped_column(Integer, nullable=False)
    appraised_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    appraisal_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    appraisal_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    source_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    applied_delta: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    mood_delta: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    state_before: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    state_after: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    appraisal_method: Mapped[str] = mapped_column(String(128), nullable=False)
