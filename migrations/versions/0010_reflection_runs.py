"""Add bounded Stage 12 reflection lifecycle records.

Revision ID: 0010_reflection_runs
Revises: 0009_satori_positions
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0010_reflection_runs"
down_revision: str | None = "0009_satori_positions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reflection_runs",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("run_key", sa.String(64), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("trigger_kind", sa.String(32), nullable=False),
        sa.Column("source_set_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("completed_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_reflection_runs_schema_version_positive"
        ),
        sa.CheckConstraint(
            "policy_version >= 1", name="ck_reflection_runs_policy_version_positive"
        ),
        sa.CheckConstraint(
            "aggregate_version >= 1", name="ck_reflection_runs_aggregate_version_positive"
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 2",
            name="ck_reflection_runs_attempt_count_valid",
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('automatic', 'explicit_local')",
            name="ck_reflection_runs_trigger_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN ('pending_generation', 'proposals_ready', 'applying', 'completed', "
            "'retryable_failure', 'exhausted')",
            name="ck_reflection_runs_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["satori_identities.identity_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_reflection_runs"),
        sa.UniqueConstraint("run_key", name="uq_reflection_runs_run_key"),
    )
    op.create_index(
        "ix_reflection_runs_identity_status",
        "reflection_runs",
        ["identity_id", "status", "created_at"],
    )
    op.create_table(
        "reflection_sources",
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("evidence_edge_id", sa.String(128), nullable=False),
        sa.Column("evidence_edge_version", sa.Integer(), nullable=False),
        sa.Column("root_interaction_id", sa.String(128), nullable=False),
        sa.Column("root_message_id", sa.String(128), nullable=False),
        sa.Column("root_counterparty_id", sa.String(128), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 0 AND ordinal < 12", name="ck_reflection_sources_ordinal_valid"
        ),
        sa.CheckConstraint(
            "evidence_edge_version >= 1", name="ck_reflection_sources_edge_version_positive"
        ),
        sa.CheckConstraint(
            "kind IN ('position_evidence', 'episodic_memory_evidence')",
            name="ck_reflection_sources_kind_valid",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["reflection_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["root_interaction_id"],
            ["conversation_interactions.interaction_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["root_message_id"], ["conversation_messages.message_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("source_id", name="pk_reflection_sources"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_reflection_sources_run_ordinal"),
        sa.UniqueConstraint(
            "run_id", "root_message_id", name="uq_reflection_sources_run_root_message"
        ),
    )
    op.create_table(
        "reflection_attempts",
        sa.Column("attempt_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("formation_method", sa.String(128), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("finished_at", UTCDateTime(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 1 AND ordinal <= 2", name="ck_reflection_attempts_ordinal_valid"
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')", name="ck_reflection_attempts_status_valid"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["reflection_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("attempt_id", name="pk_reflection_attempts"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_reflection_attempts_run_ordinal"),
    )
    op.create_table(
        "reflection_proposals",
        sa.Column("proposal_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("target_owner", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("evidence_source_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 0 AND ordinal < 3", name="ck_reflection_proposals_ordinal_valid"
        ),
        sa.CheckConstraint(
            "target_owner IN ('satori_positions', 'personality', 'values')",
            name="ck_reflection_proposals_target_owner_valid",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["reflection_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("proposal_id", name="pk_reflection_proposals"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_reflection_proposals_run_ordinal"),
    )
    op.create_table(
        "reflection_outcomes",
        sa.Column("outcome_id", sa.String(128), nullable=False),
        sa.Column("proposal_id", sa.String(128), nullable=False),
        sa.Column("target_policy_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("target_aggregate_type", sa.String(64), nullable=True),
        sa.Column("target_aggregate_id", sa.String(128), nullable=True),
        sa.Column("decided_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "target_policy_version >= 1",
            name="ck_reflection_outcomes_target_policy_version_positive",
        ),
        sa.CheckConstraint(
            "decision IN ('accepted', 'rejected')", name="ck_reflection_outcomes_decision_valid"
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["reflection_proposals.proposal_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("outcome_id", name="pk_reflection_outcomes"),
        sa.UniqueConstraint(
            "proposal_id", "target_policy_version", name="uq_reflection_outcomes_proposal_policy"
        ),
    )
    with op.batch_alter_table("satori_position_revisions") as batch_op:
        batch_op.alter_column("decision_id", existing_type=sa.String(128), nullable=True)
        batch_op.add_column(sa.Column("reflection_outcome_id", sa.String(128), nullable=True))
        batch_op.create_foreign_key(
            "fk_satori_position_revisions_reflection_outcome_id_reflection_outcomes",
            "reflection_outcomes",
            ["reflection_outcome_id"],
            ["outcome_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "ck_satori_position_revisions_origin_reference_valid",
            "(decision_id IS NOT NULL AND reflection_outcome_id IS NULL) OR "
            "(decision_id IS NULL AND reflection_outcome_id IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("satori_position_revisions") as batch_op:
        batch_op.drop_constraint(
            "ck_satori_position_revisions_origin_reference_valid", type_="check"
        )
        batch_op.drop_constraint(
            "fk_satori_position_revisions_reflection_outcome_id_reflection_outcomes",
            type_="foreignkey",
        )
        batch_op.drop_column("reflection_outcome_id")
        batch_op.alter_column("decision_id", existing_type=sa.String(128), nullable=False)
    op.drop_table("reflection_outcomes")
    op.drop_table("reflection_proposals")
    op.drop_table("reflection_attempts")
    op.drop_table("reflection_sources")
    op.drop_index("ix_reflection_runs_identity_status", table_name="reflection_runs")
    op.drop_table("reflection_runs")
