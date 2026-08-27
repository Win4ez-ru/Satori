"""Add evidence-typed counterparty user and world models.

Revision ID: 0008_user_world_models
Revises: 0007_relationship_state
Create Date: 2026-08-22
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0008_user_world_models"
down_revision: str | None = "0007_relationship_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _claim_common_columns() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("claim_id", sa.String(128), nullable=False),
        sa.Column("claim_key", sa.String(64), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("counterparty_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("formation_version", sa.Integer(), nullable=False),
        sa.Column("normalization_version", sa.Integer(), nullable=False),
    )


def _claim_value_columns() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("predicate", sa.String(64), nullable=False),
        sa.Column("value_kind", sa.String(16), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("normalized_value", sa.String(160), nullable=False),
        sa.Column("epistemic_kind", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("valid_from", UTCDateTime(), nullable=False),
        sa.Column("valid_until", UTCDateTime(), nullable=True),
        sa.Column("last_observed_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=True),
        sa.Column("superseded_by_claim_id", sa.String(128), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
    )


def _claim_checks(prefix: str) -> tuple[sa.CheckConstraint, ...]:
    return (
        sa.CheckConstraint("schema_version >= 1", name=f"ck_{prefix}_schema_version_positive"),
        sa.CheckConstraint(
            "aggregate_version >= 1", name=f"ck_{prefix}_aggregate_version_positive"
        ),
        sa.CheckConstraint("policy_version >= 1", name=f"ck_{prefix}_policy_version_positive"),
        sa.CheckConstraint(
            "formation_version >= 1", name=f"ck_{prefix}_formation_version_positive"
        ),
        sa.CheckConstraint(
            "normalization_version >= 1", name=f"ck_{prefix}_normalization_version_positive"
        ),
        sa.CheckConstraint(
            "epistemic_kind IN ('explicit_fact', 'inference', 'hypothesis')",
            name=f"ck_{prefix}_epistemic_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN ('current', 'superseded', 'disputed', 'retracted', 'expired')",
            name=f"ck_{prefix}_status_valid",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0", name=f"ck_{prefix}_confidence_valid"
        ),
        sa.CheckConstraint(
            "(status = 'superseded' AND superseded_by_claim_id IS NOT NULL) OR "
            "(status != 'superseded' AND superseded_by_claim_id IS NULL)",
            name=f"ck_{prefix}_supersession_consistent",
        ),
    )


def _create_evidence(table: str, claim_table: str) -> None:
    op.create_table(
        table,
        sa.Column("evidence_id", sa.String(128), nullable=False),
        sa.Column("claim_id", sa.String(128), nullable=False),
        sa.Column("source_message_id", sa.String(128), nullable=False),
        sa.Column("source_interaction_id", sa.String(128), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], [f"{claim_table}.claim_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["conversation_messages.message_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_interaction_id"],
            ["conversation_interactions.interaction_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("evidence_id", name=f"pk_{table}"),
        sa.UniqueConstraint("claim_id", "source_message_id", name=f"uq_{table}_claim_message"),
    )


def _create_revisions(table: str, claim_table: str) -> None:
    op.create_table(
        table,
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("claim_id", sa.String(128), nullable=False),
        sa.Column("claim_version", sa.Integer(), nullable=False),
        sa.Column("decision_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("prior_status", sa.String(16), nullable=True),
        sa.Column("new_status", sa.String(16), nullable=False),
        sa.Column("prior_confidence", sa.Float(), nullable=True),
        sa.Column("new_confidence", sa.Float(), nullable=False),
        sa.Column("prior_expires_at", UTCDateTime(), nullable=True),
        sa.Column("new_expires_at", UTCDateTime(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("occurred_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint("claim_version >= 1", name=f"ck_{table}_claim_version_positive"),
        sa.ForeignKeyConstraint(["claim_id"], [f"{claim_table}.claim_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["model_formation_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("revision_id", name=f"pk_{table}"),
        sa.UniqueConstraint("claim_id", "claim_version", name=f"uq_{table}_claim_version"),
    )


def upgrade() -> None:
    """Create Stage 9 storage without profiling historical dialogue."""

    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "model_processing_required", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.add_column(sa.Column("model_context_status", sa.String(32), nullable=True))
        batch_op.add_column(
            sa.Column("user_model_context_schema_version", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("user_model_context_claim_ids", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("world_model_context_schema_version", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("world_model_context_claim_ids", sa.JSON(), nullable=True))

    op.create_table(
        "user_model_claims",
        *_claim_common_columns(),
        *_claim_value_columns(),
        sa.CheckConstraint(
            "value_kind IN ('text', 'number', 'boolean')",
            name="ck_user_model_claims_value_kind_valid",
        ),
        *_claim_checks("user_model_claims"),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["satori_identities.identity_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_claim_id"], ["user_model_claims.claim_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("claim_id", name="pk_user_model_claims"),
    )
    op.create_index(
        "uq_user_model_claims_current_key",
        "user_model_claims",
        ["claim_key"],
        unique=True,
        sqlite_where=sa.text("status = 'current'"),
    )
    op.create_index(
        "ix_user_model_claims_partition_current",
        "user_model_claims",
        ["identity_id", "counterparty_id", "status", "predicate"],
        unique=False,
    )

    op.create_table(
        "world_model_claims",
        *_claim_common_columns(),
        sa.Column("subject_kind", sa.String(32), nullable=False),
        sa.Column("subject_label", sa.String(120), nullable=False),
        sa.Column("normalized_subject_label", sa.String(120), nullable=False),
        *_claim_value_columns(),
        sa.CheckConstraint(
            "subject_kind IN ('project', 'situation', 'commitment', 'outcome')",
            name="ck_world_model_claims_subject_kind_valid",
        ),
        sa.CheckConstraint("predicate = 'status'", name="ck_world_model_claims_predicate_valid"),
        sa.CheckConstraint("value_kind = 'text'", name="ck_world_model_claims_value_kind_valid"),
        *_claim_checks("world_model_claims"),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["satori_identities.identity_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_claim_id"], ["world_model_claims.claim_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("claim_id", name="pk_world_model_claims"),
    )
    op.create_index(
        "uq_world_model_claims_current_key",
        "world_model_claims",
        ["claim_key"],
        unique=True,
        sqlite_where=sa.text("status = 'current'"),
    )
    op.create_index(
        "ix_world_model_claims_partition_current",
        "world_model_claims",
        ["identity_id", "counterparty_id", "status", "subject_kind"],
        unique=False,
    )

    _create_evidence("user_model_claim_evidence", "user_model_claims")
    _create_evidence("world_model_claim_evidence", "world_model_claims")

    op.create_table(
        "model_formation_decisions",
        sa.Column("decision_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("source_interaction_id", sa.String(128), nullable=False),
        sa.Column("source_message_id", sa.String(128), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("counterparty_id", sa.String(128), nullable=False),
        sa.Column("formation_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        *(
            sa.Column(f"{owner}_{count}_count", sa.Integer(), nullable=False)
            for owner in ("user", "world")
            for count in ("created", "merged", "superseded", "disputed", "rejected")
        ),
        sa.Column("user_claim_ids", sa.JSON(), nullable=False),
        sa.Column("world_claim_ids", sa.JSON(), nullable=False),
        sa.Column("decided_at", UTCDateTime(), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("formation_method", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.CheckConstraint(
            "formation_version >= 1", name="ck_model_formation_decisions_formation_version_positive"
        ),
        sa.CheckConstraint(
            "policy_version >= 1", name="ck_model_formation_decisions_policy_version_positive"
        ),
        sa.CheckConstraint(
            "kind IN ('applied', 'skipped', 'rejected')",
            name="ck_model_formation_decisions_kind_valid",
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
        sa.PrimaryKeyConstraint("decision_id", name="pk_model_formation_decisions"),
        sa.UniqueConstraint("idempotency_key", name="uq_model_formation_decisions_idempotency_key"),
        sa.UniqueConstraint(
            "source_interaction_id",
            "formation_version",
            name="uq_model_formation_decisions_source_version",
        ),
    )
    _create_revisions("user_model_claim_revisions", "user_model_claims")
    _create_revisions("world_model_claim_revisions", "world_model_claims")


def downgrade() -> None:
    op.drop_table("world_model_claim_revisions")
    op.drop_table("user_model_claim_revisions")
    op.drop_table("model_formation_decisions")
    op.drop_table("world_model_claim_evidence")
    op.drop_table("user_model_claim_evidence")
    op.drop_index("ix_world_model_claims_partition_current", table_name="world_model_claims")
    op.drop_index("uq_world_model_claims_current_key", table_name="world_model_claims")
    op.drop_table("world_model_claims")
    op.drop_index("ix_user_model_claims_partition_current", table_name="user_model_claims")
    op.drop_index("uq_user_model_claims_current_key", table_name="user_model_claims")
    op.drop_table("user_model_claims")
    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.drop_column("world_model_context_claim_ids")
        batch_op.drop_column("world_model_context_schema_version")
        batch_op.drop_column("user_model_context_claim_ids")
        batch_op.drop_column("user_model_context_schema_version")
        batch_op.drop_column("model_context_status")
        batch_op.drop_column("model_processing_required")
