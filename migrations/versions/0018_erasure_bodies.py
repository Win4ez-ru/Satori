"""Add approved one-way episode body representations; preserve all attribution rows.

Revision ID: 0018_erasure_bodies
Revises: 0017_erasure_intent
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_erasure_bodies"
down_revision: str | None = "0017_erasure_intent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BODY_CHECKS = {
    "episodic_memories": (
        "summary",
        "(body_state = 'available' AND summary IS NOT NULL AND length(summary) > 0 "
        "AND body_action_id IS NULL) OR (body_state = 'erased' AND summary IS NULL "
        "AND body_action_id IS NOT NULL)",
    ),
    "memory_evidence": (
        "quote",
        "(body_state = 'available' AND quote IS NOT NULL AND length(quote) > 0 "
        "AND body_action_id IS NULL) OR (body_state = 'erased' AND quote IS NULL "
        "AND body_action_id IS NOT NULL)",
    ),
}


def _physical_transaction() -> None:
    """Include DDL in a real SQLite transaction under legacy sqlite3 transaction control."""

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        connection = bind.connection.driver_connection
        if connection is None or not connection.in_transaction:
            bind.exec_driver_sql("BEGIN IMMEDIATE")


def _triggers() -> None:
    immutable = {
        "episodic_memories": (
            "memory_id",
            "schema_version",
            "source_interaction_id",
            "occurred_at",
            "importance",
            "confidence",
            "created_at",
            "formation_method",
            "formation_version",
            "lifecycle_status",
            "aggregate_version",
        ),
        "memory_evidence": (
            "evidence_id",
            "memory_id",
            "source_message_id",
            "provenance_kind",
            "observed_at",
        ),
    }
    for table, prefix in (("episodic_memories", "episode"), ("memory_evidence", "evidence")):
        changed = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in immutable[table])
        extra = ""
        if table == "memory_evidence":
            extra = (
                " OR (NEW.body_state='erased' AND NOT EXISTS (SELECT 1 FROM episodic_memories "
                "m WHERE m.memory_id=NEW.memory_id AND m.body_state='erased' "
                "AND m.body_action_id=NEW.body_action_id))"
                " OR (NEW.body_state='available' AND EXISTS (SELECT 1 FROM episodic_memories "
                "m WHERE m.memory_id=NEW.memory_id AND m.body_state='erased'))"
            )
        op.execute(
            f"CREATE TRIGGER {prefix}_body_terminal BEFORE UPDATE ON {table} "
            f"WHEN OLD.body_state='erased' OR (NEW.body_state='erased' AND ({changed})){extra} "
            f"BEGIN SELECT RAISE(ABORT,'{prefix}_body_terminal'); END"
        )
        op.execute(
            f"CREATE TRIGGER {prefix}_body_delete BEFORE DELETE ON {table} "
            f"WHEN OLD.body_state='erased' BEGIN SELECT RAISE(ABORT,'{prefix}_body_terminal'); END"
        )
        insertion = "NEW.body_state!='available' OR NEW.body_action_id IS NOT NULL"
        if table == "memory_evidence":
            insertion += (
                " OR EXISTS (SELECT 1 FROM episodic_memories m "
                "WHERE m.memory_id=NEW.memory_id AND m.body_state='erased')"
            )
        op.execute(
            f"CREATE TRIGGER {prefix}_body_insert BEFORE INSERT ON {table} WHEN {insertion} "
            f"BEGIN SELECT RAISE(ABORT,'{prefix}_body_terminal'); END"
        )
    for operation in ("INSERT", "UPDATE"):
        op.execute(
            f"CREATE TRIGGER episode_vector_{operation.lower()} BEFORE {operation} "
            "ON episodic_memory_embeddings WHEN EXISTS (SELECT 1 FROM episodic_memories m "
            "WHERE m.memory_id=NEW.memory_id AND m.body_state='erased') "
            "BEGIN SELECT RAISE(ABORT,'episode_body_terminal'); END"
        )


def upgrade() -> None:
    _physical_transaction()
    op.create_table(
        "erasure_body_applications",
        sa.Column("action_id", sa.String(128), primary_key=True),
        sa.Column("request_id", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "identity_id",
            sa.String(128),
            sa.ForeignKey("satori_identities.identity_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "root_message_id",
            sa.String(128),
            sa.ForeignKey("conversation_messages.message_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("floor_id", sa.String(128), nullable=False),
        sa.Column("lineage_id", sa.String(128), nullable=False),
        sa.Column("body_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.JSON(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("identity_id", "body_revision"),
        sa.CheckConstraint("body_revision >= 1", name="revision_positive"),
    )
    op.create_table(
        "erasure_body_bindings",
        sa.Column("identity_id", sa.String(128), primary_key=True),
        sa.Column("floor_id", sa.String(128), nullable=False),
        sa.Column("lineage_id", sa.String(128), nullable=False),
        sa.Column("body_revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("body_revision >= 1", name="revision_positive"),
    )
    for table, (column, condition) in _BODY_CHECKS.items():
        constraints = sa.inspect(op.get_bind()).get_check_constraints(table)
        old_constraint = next(
            item["name"] for item in constraints if item["sqltext"] == f"length({column}) > 0"
        )
        if not isinstance(old_constraint, str):
            raise RuntimeError("erasure_bodies_legacy_schema_invalid")
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(op.f(old_constraint), type_="check")
            batch.alter_column(column, existing_type=sa.String(500), nullable=True)
            batch.add_column(
                sa.Column("body_state", sa.String(16), nullable=False, server_default="available")
            )
            batch.add_column(sa.Column("body_action_id", sa.String(128), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_body_action_id_erasure_body_applications",
                "erasure_body_applications",
                ["body_action_id"],
                ["action_id"],
                ondelete="RESTRICT",
            )
            batch.create_check_constraint("body_consistent", condition)
    _triggers()


def downgrade() -> None:
    """A schema rollback cannot erase terminal body proof or reconstruct removed content."""

    raise RuntimeError("erasure_bodies_downgrade_requires_offline_owner_policy")
