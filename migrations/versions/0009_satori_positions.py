"""Add identity-global evidence-linked Satori positions.

Revision ID: 0009_satori_positions
Revises: 0008_user_world_models
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0009_satori_positions"
down_revision: str | None = "0008_user_world_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create Stage 11 state without profiling pre-Stage-11 conversations."""

    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "position_processing_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("position_context_status", sa.String(32), nullable=True))
        batch_op.add_column(
            sa.Column("position_context_schema_version", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("position_context_ids", sa.JSON(), nullable=True))

    op.create_table(
        "satori_positions",
        sa.Column("position_id", sa.String(128), nullable=False),
        sa.Column("position_key", sa.String(64), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("formation_version", sa.Integer(), nullable=False),
        sa.Column("normalization_version", sa.Integer(), nullable=False),
        sa.Column("proposition", sa.String(240), nullable=False),
        sa.Column("normalized_proposition", sa.String(240), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("stance", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("value_key", sa.String(64), nullable=True),
        sa.Column("competing_with_position_id", sa.String(128), nullable=True),
        sa.Column("superseded_by_position_id", sa.String(128), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_satori_positions_schema_version_positive"
        ),
        sa.CheckConstraint(
            "aggregate_version >= 1", name="ck_satori_positions_aggregate_version_positive"
        ),
        sa.CheckConstraint(
            "policy_version >= 1", name="ck_satori_positions_policy_version_positive"
        ),
        sa.CheckConstraint(
            "formation_version >= 1", name="ck_satori_positions_formation_version_positive"
        ),
        sa.CheckConstraint(
            "normalization_version >= 1", name="ck_satori_positions_normalization_version_positive"
        ),
        sa.CheckConstraint(
            "kind IN ('fact', 'belief', 'opinion', 'hypothesis')",
            name="ck_satori_positions_kind_valid",
        ),
        sa.CheckConstraint(
            "stance IN ('support', 'oppose', 'uncertain')", name="ck_satori_positions_stance_valid"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'competing', 'superseded', 'retracted')",
            name="ck_satori_positions_status_valid",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0", name="ck_satori_positions_confidence_valid"
        ),
        sa.CheckConstraint(
            "(kind = 'opinion' AND value_key IS NOT NULL) OR "
            "(kind != 'opinion' AND value_key IS NULL)",
            name="ck_satori_positions_value_reference_consistent",
        ),
        sa.CheckConstraint(
            "(status = 'competing' AND competing_with_position_id IS NOT NULL) OR "
            "(status != 'competing' AND competing_with_position_id IS NULL)",
            name="ck_satori_positions_competition_consistent",
        ),
        sa.CheckConstraint(
            "(status = 'superseded' AND superseded_by_position_id IS NOT NULL) OR "
            "(status != 'superseded' AND superseded_by_position_id IS NULL)",
            name="ck_satori_positions_supersession_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["satori_identities.identity_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["competing_with_position_id"], ["satori_positions.position_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_position_id"], ["satori_positions.position_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("position_id", name="pk_satori_positions"),
    )
    op.create_index(
        "uq_satori_positions_current_key",
        "satori_positions",
        ["identity_id", "position_key"],
        unique=True,
        sqlite_where=sa.text("status IN ('active', 'competing')"),
    )
    op.create_index(
        "ix_satori_positions_identity_status",
        "satori_positions",
        ["identity_id", "status", "kind"],
        unique=False,
    )
    op.create_table(
        "satori_position_evidence",
        sa.Column("evidence_id", sa.String(128), nullable=False),
        sa.Column("position_id", sa.String(128), nullable=False),
        sa.Column("source_message_id", sa.String(128), nullable=False),
        sa.Column("source_interaction_id", sa.String(128), nullable=False),
        sa.Column("source_counterparty_id", sa.String(128), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("normalized_signature", sa.String(512), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "role IN ('argument', 'observation', 'counterexample', 'verified_record')",
            name="ck_satori_position_evidence_role_valid",
        ),
        sa.ForeignKeyConstraint(
            ["position_id"], ["satori_positions.position_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["conversation_messages.message_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_interaction_id"],
            ["conversation_interactions.interaction_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_satori_position_evidence"),
        sa.UniqueConstraint(
            "position_id", "source_message_id", name="uq_satori_position_evidence_position_message"
        ),
        sa.UniqueConstraint(
            "position_id",
            "normalized_signature",
            name="uq_satori_position_evidence_position_signature",
        ),
    )
    op.create_table(
        "position_formation_decisions",
        sa.Column("decision_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("source_interaction_id", sa.String(128), nullable=False),
        sa.Column("source_message_id", sa.String(128), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("formation_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("merged_count", sa.Integer(), nullable=False),
        sa.Column("superseded_count", sa.Integer(), nullable=False),
        sa.Column("competing_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("position_ids", sa.JSON(), nullable=False),
        sa.Column("decided_at", UTCDateTime(), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("formation_method", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.CheckConstraint(
            "formation_version >= 1",
            name="ck_position_formation_decisions_formation_version_positive",
        ),
        sa.CheckConstraint(
            "policy_version >= 1", name="ck_position_formation_decisions_policy_version_positive"
        ),
        sa.CheckConstraint(
            "kind IN ('applied', 'skipped', 'rejected')",
            name="ck_position_formation_decisions_kind_valid",
        ),
        sa.CheckConstraint(
            "created_count >= 0 AND merged_count >= 0 AND superseded_count >= 0 "
            "AND competing_count >= 0 AND rejected_count >= 0",
            name="ck_position_formation_decisions_counts_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["source_interaction_id"],
            ["conversation_interactions.interaction_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["conversation_messages.message_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["satori_identities.identity_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("decision_id", name="pk_position_formation_decisions"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_position_formation_decisions_idempotency_key"
        ),
        sa.UniqueConstraint(
            "source_interaction_id",
            "formation_version",
            name="uq_position_formation_decisions_source_version",
        ),
    )
    op.create_table(
        "satori_position_revisions",
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("position_id", sa.String(128), nullable=False),
        sa.Column("position_version", sa.Integer(), nullable=False),
        sa.Column("decision_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("prior_status", sa.String(16), nullable=True),
        sa.Column("new_status", sa.String(16), nullable=False),
        sa.Column("prior_confidence", sa.Float(), nullable=True),
        sa.Column("new_confidence", sa.Float(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("occurred_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "position_version >= 1", name="ck_satori_position_revisions_position_version_positive"
        ),
        sa.CheckConstraint(
            "kind IN "
            "('created', 'strengthened', 'weakened', 'competing', 'superseded', 'retracted')",
            name="ck_satori_position_revisions_kind_valid",
        ),
        sa.ForeignKeyConstraint(
            ["position_id"], ["satori_positions.position_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["position_formation_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("revision_id", name="pk_satori_position_revisions"),
        sa.UniqueConstraint(
            "position_id", "position_version", name="uq_satori_position_revisions_position_version"
        ),
    )


def downgrade() -> None:
    op.drop_table("satori_position_revisions")
    op.drop_table("position_formation_decisions")
    op.drop_table("satori_position_evidence")
    op.drop_index("ix_satori_positions_identity_status", table_name="satori_positions")
    op.drop_index("uq_satori_positions_current_key", table_name="satori_positions")
    op.drop_table("satori_positions")
    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.drop_column("position_context_ids")
        batch_op.drop_column("position_context_schema_version")
        batch_op.drop_column("position_context_status")
        batch_op.drop_column("position_processing_required")
