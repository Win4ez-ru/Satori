"""Add derived source admission metadata; independent policy ledger is not in this DB.

Revision ID: 0016_source_policy
Revises: 0015_memory_lifecycle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_source_policy"
down_revision: str | None = "0015_memory_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_policy_bindings",
        sa.Column("identity_id", sa.String(128), primary_key=True),
        sa.Column("installation_id", sa.String(128), nullable=False),
        sa.Column("floor_id", sa.String(128), nullable=False),
        sa.Column("lineage_id", sa.String(128), nullable=False),
        sa.Column("policy_generation", sa.Integer(), nullable=False),
        sa.CheckConstraint("policy_generation >= 0", name="generation_nonnegative"),
    )
    op.create_table(
        "source_excluded_roots",
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("policy_generation", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_source_excluded_roots_identity_id", "source_excluded_roots", ["identity_id"]
    )
    for table, primary in (
        ("source_inputs", [sa.Column("input_id", sa.String(128), primary_key=True)]),
        (
            "source_payload_receipts",
            [
                sa.Column("kind", sa.String(64), primary_key=True),
                sa.Column("payload_id", sa.String(128), primary_key=True),
                sa.Column("payload_version", sa.Integer(), primary_key=True),
            ],
        ),
    ):
        extra = (
            [
                sa.Column("eligible", sa.Boolean(), nullable=False),
                sa.CheckConstraint("payload_version >= 1", name="version_positive"),
            ]
            if table == "source_payload_receipts"
            else []
        )
        op.create_table(
            table,
            *primary,
            sa.Column("identity_id", sa.String(128), nullable=False),
            sa.Column("policy_generation", sa.Integer(), nullable=False),
            sa.Column("proof_status", sa.String(16), nullable=False),
            sa.Column("legacy", sa.Boolean(), nullable=False),
            sa.Column("root_message_ids", sa.JSON(), nullable=False),
            sa.Column("payload_refs", sa.JSON(), nullable=False),
            sa.CheckConstraint(
                "proof_status IN ('complete','unknown','overflow')", name="proof_valid"
            ),
            *extra,
        )
        op.create_index(f"ix_{table}_identity_id", table, ["identity_id"])
    op.create_table(
        "owner_source_withdrawals",
        sa.Column("receipt_id", sa.String(128), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("policy_generation", sa.Integer(), nullable=False),
        sa.Column("before_status", sa.String(32), nullable=False),
        sa.Column("after_status", sa.String(32), nullable=False),
        sa.Column("before_version", sa.Integer(), nullable=False),
        sa.Column("after_version", sa.Integer(), nullable=False),
        sa.Column("root_message_ids", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner", "aggregate_id", "request_id"),
        sa.CheckConstraint("after_version >= before_version", name="version_nondecreasing"),
    )
    op.create_table(
        "source_owner_repairs",
        sa.Column("owner", sa.String(64), primary_key=True),
        sa.Column("identity_id", sa.String(128), primary_key=True),
        sa.Column("policy_generation", sa.Integer(), nullable=False),
        sa.CheckConstraint("policy_generation >= 0", name="generation_nonnegative"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = (
        "source_owner_repairs",
        "owner_source_withdrawals",
        "source_payload_receipts",
        "source_inputs",
        "source_excluded_roots",
        "source_policy_bindings",
    )
    for table in tables:
        if bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one():
            raise RuntimeError("source_policy_downgrade_would_lose_admission_history")
    for table in tables:
        op.drop_table(table)
