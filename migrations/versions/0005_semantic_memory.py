"""Add evidence-grounded semantic memory and terminal formation state.

Revision ID: 0005_semantic_memory
Revises: 0004_episodic_retrieval
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0005_semantic_memory"
down_revision: str | None = "0004_episodic_retrieval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create canonical Stage 6 claims, evidence, decisions, and history."""

    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.add_column(
            sa.Column("semantic_retrieval_status", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("retrieved_semantic_claim_ids", sa.JSON(), nullable=True))

    op.create_table(
        "semantic_claims",
        sa.Column("claim_id", sa.String(length=128), nullable=False),
        sa.Column("claim_key", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("predicate", sa.String(length=64), nullable=False),
        sa.Column("value_kind", sa.String(length=16), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("normalized_value", sa.String(length=500), nullable=False),
        sa.Column("polarity", sa.Boolean(), nullable=False),
        sa.Column("claim_kind", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("valid_from", UTCDateTime(), nullable=False),
        sa.Column("valid_until", UTCDateTime(), nullable=True),
        sa.Column("superseded_by_claim_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("formation_method", sa.String(length=128), nullable=False),
        sa.Column("formation_version", sa.Integer(), nullable=False),
        sa.Column("normalization_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_semantic_claims_schema_version_positive"
        ),
        sa.CheckConstraint(
            "aggregate_version >= 1", name="ck_semantic_claims_aggregate_version_positive"
        ),
        sa.CheckConstraint("subject = 'user'", name="ck_semantic_claims_subject_valid"),
        sa.CheckConstraint(
            "value_kind IN ('text', 'number', 'boolean')",
            name="ck_semantic_claims_value_kind_valid",
        ),
        sa.CheckConstraint(
            "claim_kind IN "
            "('explicit_fact', 'inferred_fact', 'hypothesis', 'attributed_statement')",
            name="ck_semantic_claims_claim_kind_valid",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_semantic_claims_confidence_valid",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'disputed', 'retracted')",
            name="ck_semantic_claims_status_valid",
        ),
        sa.CheckConstraint(
            "formation_version >= 1", name="ck_semantic_claims_formation_version_positive"
        ),
        sa.CheckConstraint(
            "normalization_version >= 1",
            name="ck_semantic_claims_normalization_version_positive",
        ),
        sa.CheckConstraint("length(predicate) > 0", name="ck_semantic_claims_predicate_not_blank"),
        sa.CheckConstraint(
            "length(normalized_value) > 0",
            name="ck_semantic_claims_normalized_value_not_blank",
        ),
        sa.CheckConstraint(
            "(status = 'superseded' AND superseded_by_claim_id IS NOT NULL) OR "
            "(status != 'superseded' AND superseded_by_claim_id IS NULL)",
            name="ck_semantic_claims_supersession_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_claim_id"],
            ["semantic_claims.claim_id"],
            name="fk_semantic_claims_superseded_by_claim_id_semantic_claims",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("claim_id", name="pk_semantic_claims"),
    )
    op.create_index(
        "uq_semantic_claims_active_key",
        "semantic_claims",
        ["claim_key"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_semantic_claims_active_predicate",
        "semantic_claims",
        ["status", "subject", "predicate"],
        unique=False,
    )
    op.create_table(
        "semantic_claim_evidence",
        sa.Column("semantic_evidence_id", sa.String(length=128), nullable=False),
        sa.Column("claim_id", sa.String(length=128), nullable=False),
        sa.Column("memory_id", sa.String(length=128), nullable=False),
        sa.Column("memory_evidence_id", sa.String(length=128), nullable=False),
        sa.Column("root_message_id", sa.String(length=128), nullable=False),
        sa.Column("root_interaction_id", sa.String(length=128), nullable=False),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('explicit_user_statement', 'episode_inference')",
            name="ck_semantic_claim_evidence_source_kind_valid",
        ),
        sa.ForeignKeyConstraint(["claim_id"], ["semantic_claims.claim_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["memory_id"], ["episodic_memories.memory_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["memory_evidence_id"], ["memory_evidence.evidence_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["root_message_id"], ["conversation_messages.message_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["root_interaction_id"],
            ["conversation_interactions.interaction_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("semantic_evidence_id", name="pk_semantic_claim_evidence"),
        sa.UniqueConstraint(
            "claim_id", "root_message_id", name="uq_semantic_claim_evidence_claim_id"
        ),
    )
    op.create_table(
        "semantic_formation_decisions",
        sa.Column("decision_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("source_memory_id", sa.String(length=128), nullable=False),
        sa.Column("formation_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("merged_count", sa.Integer(), nullable=False),
        sa.Column("superseded_count", sa.Integer(), nullable=False),
        sa.Column("disputed_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("claim_ids", sa.JSON(), nullable=False),
        sa.Column("decided_at", UTCDateTime(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("formation_method", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.CheckConstraint(
            "formation_version >= 1",
            name="ck_semantic_formation_decisions_formation_version_positive",
        ),
        sa.CheckConstraint(
            "policy_version >= 1",
            name="ck_semantic_formation_decisions_policy_version_positive",
        ),
        sa.CheckConstraint(
            "kind IN ('applied', 'skipped', 'rejected')",
            name="ck_semantic_formation_decisions_kind_valid",
        ),
        sa.CheckConstraint(
            "created_count >= 0 AND merged_count >= 0 AND superseded_count >= 0 "
            "AND disputed_count >= 0 AND rejected_count >= 0",
            name="ck_semantic_formation_decisions_counts_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["source_memory_id"], ["episodic_memories.memory_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("decision_id", name="pk_semantic_formation_decisions"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_semantic_formation_decisions_idempotency_key"
        ),
        sa.UniqueConstraint(
            "source_memory_id",
            "formation_version",
            name="uq_semantic_formation_decisions_source_memory_id",
        ),
    )
    op.create_table(
        "semantic_claim_revisions",
        sa.Column("revision_id", sa.String(length=128), nullable=False),
        sa.Column("claim_id", sa.String(length=128), nullable=False),
        sa.Column("claim_version", sa.Integer(), nullable=False),
        sa.Column("decision_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("prior_status", sa.String(length=16), nullable=True),
        sa.Column("new_status", sa.String(length=16), nullable=False),
        sa.Column("prior_confidence", sa.Float(), nullable=True),
        sa.Column("new_confidence", sa.Float(), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "claim_version >= 1",
            name="ck_semantic_claim_revisions_claim_version_positive",
        ),
        sa.CheckConstraint(
            "kind IN ('created', 'strengthened', 'superseded', 'disputed', 'retracted')",
            name="ck_semantic_claim_revisions_kind_valid",
        ),
        sa.ForeignKeyConstraint(["claim_id"], ["semantic_claims.claim_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["semantic_formation_decisions.decision_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("revision_id", name="pk_semantic_claim_revisions"),
        sa.UniqueConstraint(
            "claim_id",
            "claim_version",
            name="uq_semantic_claim_revisions_claim_id",
        ),
    )


def downgrade() -> None:
    """Remove Stage 6 while preserving canonical Stage 4 and Stage 5 state."""

    op.drop_table("semantic_claim_revisions")
    op.drop_table("semantic_formation_decisions")
    op.drop_table("semantic_claim_evidence")
    op.drop_index("ix_semantic_claims_active_predicate", table_name="semantic_claims")
    op.drop_index("uq_semantic_claims_active_key", table_name="semantic_claims")
    op.drop_table("semantic_claims")
    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.drop_column("retrieved_semantic_claim_ids")
        batch_op.drop_column("semantic_retrieval_status")
