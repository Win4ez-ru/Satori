"""Add conversation history and selective episodic memory.

Revision ID: 0003_conversation_memory
Revises: 0002_initial_self
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0003_conversation_memory"
down_revision: str | None = "0002_initial_self"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only Stage 4 canonical history and episodic projection records."""

    op.create_table(
        "conversation_sessions",
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("identity_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("ended_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_conversation_sessions_schema_version_positive"
        ),
        sa.CheckConstraint(
            "kind IN ('explicit', 'implicit')", name="ck_conversation_sessions_kind_valid"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'closed')", name="ck_conversation_sessions_status_valid"
        ),
        sa.CheckConstraint(
            "(status = 'open' AND ended_at IS NULL) OR "
            "(status = 'closed' AND ended_at IS NOT NULL)",
            name="ck_conversation_sessions_status_end_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["satori_identities.identity_id"],
            name="fk_conversation_sessions_identity_id_satori_identities",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("session_id", name="pk_conversation_sessions"),
    )
    op.create_table(
        "conversation_interactions",
        sa.Column("interaction_id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("client_request_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("completed_at", UTCDateTime(), nullable=True),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("finish_status", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("context_schema_version", sa.Integer(), nullable=True),
        sa.Column("context_manifest_schema_version", sa.Integer(), nullable=True),
        sa.Column("policy_id", sa.String(length=128), nullable=True),
        sa.Column("policy_schema_version", sa.Integer(), nullable=True),
        sa.Column("failure_kind", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "schema_version >= 1",
            name="ck_conversation_interactions_schema_version_positive",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'failed', 'completed')",
            name="ck_conversation_interactions_status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL AND provider IS NOT NULL "
            "AND model IS NOT NULL AND finish_status IS NOT NULL "
            "AND context_schema_version IS NOT NULL "
            "AND context_manifest_schema_version IS NOT NULL "
            "AND policy_id IS NOT NULL AND policy_schema_version IS NOT NULL "
            "AND failure_kind IS NULL) OR "
            "(status != 'completed' AND completed_at IS NULL AND provider IS NULL "
            "AND model IS NULL AND finish_status IS NULL "
            "AND context_schema_version IS NULL "
            "AND context_manifest_schema_version IS NULL "
            "AND policy_id IS NULL AND policy_schema_version IS NULL)",
            name="ck_conversation_interactions_completion_metadata_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["conversation_sessions.session_id"],
            name="fk_conversation_interactions_session_id_conversation_sessions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("interaction_id", name="pk_conversation_interactions"),
        sa.UniqueConstraint(
            "client_request_id",
            name="uq_conversation_interactions_client_request_id",
        ),
        sa.UniqueConstraint(
            "interaction_id",
            "session_id",
            name="uq_conversation_interactions_interaction_id",
        ),
    )
    op.create_table(
        "conversation_messages",
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("interaction_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_conversation_messages_schema_version_positive"
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_conversation_messages_role_valid"
        ),
        sa.CheckConstraint("sequence IN (1, 2)", name="ck_conversation_messages_sequence_valid"),
        sa.CheckConstraint(
            "length(content) > 0", name="ck_conversation_messages_content_not_blank"
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id", "session_id"],
            [
                "conversation_interactions.interaction_id",
                "conversation_interactions.session_id",
            ],
            name="fk_conversation_messages_interaction_id_conversation_interactions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("message_id", name="pk_conversation_messages"),
        sa.UniqueConstraint(
            "interaction_id",
            "role",
            name="uq_conversation_messages_interaction_id",
        ),
        sa.UniqueConstraint(
            "interaction_id",
            "sequence",
            name="uq_conversation_messages_interaction_sequence",
        ),
    )
    op.create_table(
        "episodic_memories",
        sa.Column("memory_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("source_interaction_id", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", UTCDateTime(), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("formation_method", sa.String(length=128), nullable=False),
        sa.Column("formation_version", sa.Integer(), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_episodic_memories_schema_version_positive"
        ),
        sa.CheckConstraint(
            "importance >= 0.0 AND importance <= 1.0",
            name="ck_episodic_memories_importance_unit_interval",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_episodic_memories_confidence_unit_interval",
        ),
        sa.CheckConstraint(
            "formation_version >= 1",
            name="ck_episodic_memories_formation_version_positive",
        ),
        sa.CheckConstraint(
            "lifecycle_status = 'active'",
            name="ck_episodic_memories_lifecycle_status_valid",
        ),
        sa.CheckConstraint("length(summary) > 0", name="ck_episodic_memories_summary_not_blank"),
        sa.ForeignKeyConstraint(
            ["source_interaction_id"],
            ["conversation_interactions.interaction_id"],
            name="fk_episodic_memories_source_interaction_id_conversation_interactions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("memory_id", name="pk_episodic_memories"),
        sa.UniqueConstraint(
            "source_interaction_id",
            "formation_version",
            name="uq_episodic_memories_source_interaction_id",
        ),
    )
    op.create_table(
        "memory_evidence",
        sa.Column("evidence_id", sa.String(length=128), nullable=False),
        sa.Column("memory_id", sa.String(length=128), nullable=False),
        sa.Column("source_message_id", sa.String(length=128), nullable=False),
        sa.Column("provenance_kind", sa.String(length=64), nullable=False),
        sa.Column("quote", sa.String(length=500), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "provenance_kind = 'explicit_user_statement'",
            name="ck_memory_evidence_provenance_kind_valid",
        ),
        sa.CheckConstraint("length(quote) > 0", name="ck_memory_evidence_quote_not_blank"),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["episodic_memories.memory_id"],
            name="fk_memory_evidence_memory_id_episodic_memories",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["conversation_messages.message_id"],
            name="fk_memory_evidence_source_message_id_conversation_messages",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_memory_evidence"),
        sa.UniqueConstraint(
            "memory_id",
            "source_message_id",
            "quote",
            name="uq_memory_evidence_memory_id",
        ),
    )
    op.create_table(
        "episode_formation_decisions",
        sa.Column("decision_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("source_interaction_id", sa.String(length=128), nullable=False),
        sa.Column("formation_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("decided_at", UTCDateTime(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("formation_method", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("memory_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "formation_version >= 1",
            name="ck_episode_formation_decisions_formation_version_positive",
        ),
        sa.CheckConstraint(
            "policy_version >= 1",
            name="ck_episode_formation_decisions_policy_version_positive",
        ),
        sa.CheckConstraint(
            "kind IN ('created', 'skipped', 'rejected')",
            name="ck_episode_formation_decisions_kind_valid",
        ),
        sa.CheckConstraint(
            "(kind = 'created' AND memory_id IS NOT NULL) OR "
            "(kind != 'created' AND memory_id IS NULL)",
            name="ck_episode_formation_decisions_kind_memory_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["source_interaction_id"],
            ["conversation_interactions.interaction_id"],
            name=("fk_episode_formation_decisions_source_interaction_id_conversation_interactions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["episodic_memories.memory_id"],
            name="fk_episode_formation_decisions_memory_id_episodic_memories",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("decision_id", name="pk_episode_formation_decisions"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_episode_formation_decisions_idempotency_key",
        ),
        sa.UniqueConstraint(
            "source_interaction_id",
            "formation_version",
            name="uq_episode_formation_decisions_source_interaction_id",
        ),
    )


def downgrade() -> None:
    """Remove Stage 4 data while preserving the accepted Stage 2 schema."""

    op.drop_table("episode_formation_decisions")
    op.drop_table("memory_evidence")
    op.drop_table("episodic_memories")
    op.drop_table("conversation_messages")
    op.drop_table("conversation_interactions")
    op.drop_table("conversation_sessions")
