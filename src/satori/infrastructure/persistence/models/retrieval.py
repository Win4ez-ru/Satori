"""Rebuildable Stage 5 episodic-memory embedding index rows."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.types import UTCDateTime


class EpisodicMemoryEmbeddingRow(Base):
    """One derived vector in one exact provider/model/dimension/input space."""

    __tablename__ = "episodic_memory_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "memory_id",
            "provider",
            "model",
            "dimensions",
            "input_schema_version",
        ),
        CheckConstraint("dimensions >= 1", name="dimensions_positive"),
        CheckConstraint("input_schema_version >= 1", name="input_schema_version_positive"),
        CheckConstraint("length(provider) > 0", name="provider_not_blank"),
        CheckConstraint("length(model) > 0", name="model_not_blank"),
    )

    embedding_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("episodic_memories.memory_id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    input_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
