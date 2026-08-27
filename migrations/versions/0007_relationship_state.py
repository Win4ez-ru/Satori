"""Add counterparty-specific persistent relationship state.

Revision ID: 0007_relationship_state
Revises: 0006_affective_state
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0007_relationship_state"
down_revision: str | None = "0006_affective_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create Stage 8 storage without inferring relationship from old dialogue."""

    with op.batch_alter_table("conversation_sessions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "counterparty_id", sa.String(128), nullable=False, server_default="local-default"
            )
        )
    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "relationship_processing_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("relationship_context_schema_version", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("relationship_state_version", sa.Integer(), nullable=True))

    vectors = ("familiarity", "trust", "comfort", "closeness", "intellectual_respect", "affection")
    counters = (
        "processed_interaction_count",
        "qualified_interaction_count",
        "distinct_session_count",
        "positive_evidence_count",
        "negative_evidence_count",
    )
    op.create_table(
        "relationship_states",
        sa.Column("relationship_id", sa.String(128), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("counterparty_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        *(sa.Column(name, sa.Float(), nullable=False) for name in vectors),
        *(sa.Column(name, sa.Integer(), nullable=False) for name in counters),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_relationship_states_schema_version_positive"
        ),
        sa.CheckConstraint(
            "state_version >= 1", name="ck_relationship_states_state_version_positive"
        ),
        sa.CheckConstraint(
            "policy_version >= 1", name="ck_relationship_states_policy_version_positive"
        ),
        *(
            sa.CheckConstraint(
                f"{name} >= 0.0 AND {name} <= 1.0", name=f"ck_relationship_states_{name}_valid"
            )
            for name in vectors
        ),
        *(
            sa.CheckConstraint(f"{name} >= 0", name=f"ck_relationship_states_{name}_non_negative")
            for name in counters
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["satori_identities.identity_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("relationship_id", name="pk_relationship_states"),
        sa.UniqueConstraint(
            "identity_id", "counterparty_id", name="uq_relationship_states_identity_counterparty"
        ),
    )

    op.create_table(
        "relationship_decisions",
        sa.Column("decision_id", sa.String(128), nullable=False),
        sa.Column("relationship_id", sa.String(128), nullable=False),
        sa.Column("interaction_id", sa.String(128), nullable=False),
        sa.Column("source_user_message_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("appraisal_method", sa.String(128), nullable=False),
        sa.Column("appraisal_schema_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("decided_at", UTCDateTime(), nullable=False),
        sa.Column("transition_id", sa.String(128), nullable=True),
        sa.CheckConstraint(
            "kind IN ('applied', 'skipped', 'rejected')",
            name="ck_relationship_decisions_kind_valid",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_relationship_decisions_confidence_valid",
        ),
        sa.CheckConstraint(
            "appraisal_schema_version >= 1",
            name="ck_relationship_decisions_appraisal_schema_version_positive",
        ),
        sa.CheckConstraint(
            "policy_version >= 1", name="ck_relationship_decisions_policy_version_positive"
        ),
        sa.CheckConstraint(
            "(kind = 'applied' AND transition_id IS NOT NULL) OR "
            "(kind != 'applied' AND transition_id IS NULL)",
            name="ck_relationship_decisions_transition_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id"], ["relationship_states.relationship_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id"], ["conversation_interactions.interaction_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_user_message_id"], ["conversation_messages.message_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("decision_id", name="pk_relationship_decisions"),
        sa.UniqueConstraint("interaction_id", name="uq_relationship_decisions_interaction_id"),
    )
    op.create_table(
        "relationship_transitions",
        sa.Column("transition_id", sa.String(128), nullable=False),
        sa.Column("relationship_id", sa.String(128), nullable=False),
        sa.Column("interaction_id", sa.String(128), nullable=False),
        sa.Column("source_user_message_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("base_state_version", sa.Integer(), nullable=False),
        sa.Column("resulting_state_version", sa.Integer(), nullable=False),
        sa.Column("state_before", sa.JSON(), nullable=False),
        sa.Column("applied_delta", sa.JSON(), nullable=False),
        sa.Column("state_after", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("appraisal_method", sa.String(128), nullable=False),
        sa.Column("appraisal_schema_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("committed_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_relationship_transitions_confidence_valid",
        ),
        sa.CheckConstraint(
            "base_state_version >= 1",
            name="ck_relationship_transitions_base_state_version_positive",
        ),
        sa.CheckConstraint(
            "resulting_state_version = base_state_version + 1",
            name="ck_relationship_transitions_version_increments_once",
        ),
        sa.CheckConstraint(
            "appraisal_schema_version >= 1",
            name="ck_relationship_transitions_appraisal_schema_version_positive",
        ),
        sa.CheckConstraint(
            "policy_version >= 1", name="ck_relationship_transitions_policy_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id"], ["relationship_states.relationship_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id"], ["conversation_interactions.interaction_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_user_message_id"], ["conversation_messages.message_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("transition_id", name="pk_relationship_transitions"),
        sa.UniqueConstraint("interaction_id", name="uq_relationship_transitions_interaction_id"),
    )
    op.create_index(
        "ix_relationship_transitions_relationship_committed",
        "relationship_transitions",
        ["relationship_id", "committed_at"],
        unique=False,
    )
    op.create_index(
        "ix_relationship_decisions_relationship_session",
        "relationship_decisions",
        ["relationship_id", "session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_relationship_decisions_relationship_session", table_name="relationship_decisions"
    )
    op.drop_index(
        "ix_relationship_transitions_relationship_committed", table_name="relationship_transitions"
    )
    op.drop_table("relationship_transitions")
    op.drop_table("relationship_decisions")
    op.drop_table("relationship_states")
    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.drop_column("relationship_state_version")
        batch_op.drop_column("relationship_context_schema_version")
        batch_op.drop_column("relationship_processing_required")
    with op.batch_alter_table("conversation_sessions") as batch_op:
        batch_op.drop_column("counterparty_id")
