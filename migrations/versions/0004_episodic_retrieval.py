"""Add the rebuildable episodic-memory embedding index.

Revision ID: 0004_episodic_retrieval
Revises: 0003_conversation_memory
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0004_episodic_retrieval"
down_revision: str | None = "0003_conversation_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add only derived, disposable Stage 5 vector state."""

    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.add_column(sa.Column("retrieval_status", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("retrieved_memory_ids", sa.JSON(), nullable=True))

    op.create_table(
        "episodic_memory_embeddings",
        sa.Column("embedding_id", sa.String(length=64), nullable=False),
        sa.Column("memory_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("input_schema_version", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("indexed_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "dimensions >= 1",
            name="ck_episodic_memory_embeddings_dimensions_positive",
        ),
        sa.CheckConstraint(
            "input_schema_version >= 1",
            name="ck_episodic_memory_embeddings_input_schema_version_positive",
        ),
        sa.CheckConstraint(
            "length(provider) > 0",
            name="ck_episodic_memory_embeddings_provider_not_blank",
        ),
        sa.CheckConstraint(
            "length(model) > 0",
            name="ck_episodic_memory_embeddings_model_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["episodic_memories.memory_id"],
            name="fk_episodic_memory_embeddings_memory_id_episodic_memories",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "embedding_id",
            name="pk_episodic_memory_embeddings",
        ),
        sa.UniqueConstraint(
            "memory_id",
            "provider",
            "model",
            "dimensions",
            "input_schema_version",
            name="uq_episodic_memory_embeddings_memory_id",
        ),
    )
    op.create_index(
        "ix_episodic_memory_embeddings_space",
        "episodic_memory_embeddings",
        ["provider", "model", "dimensions", "input_schema_version"],
        unique=False,
    )


def downgrade() -> None:
    """Drop only the rebuildable index; canonical Stage 4 episodes remain."""

    op.drop_index(
        "ix_episodic_memory_embeddings_space",
        table_name="episodic_memory_embeddings",
    )
    op.drop_table("episodic_memory_embeddings")
    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.drop_column("retrieved_memory_ids")
        batch_op.drop_column("retrieval_status")
