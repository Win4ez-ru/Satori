"""Add owner-controlled episodic archival with durable local approval receipts.

Revision ID: 0015_memory_lifecycle
Revises: 0014_memory_scope
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0015_memory_lifecycle"
down_revision: str | None = "0014_memory_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preserve every source and formation record; old active episodes start at version one."""

    with op.batch_alter_table("episodic_memories") as batch:
        batch.add_column(
            sa.Column("aggregate_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.create_check_constraint("aggregate_version_positive", "aggregate_version >= 1")
        batch.drop_constraint(
            op.f("ck_episodic_memories_ck_episodic_memories_lifecycle_status_valid"), type_="check"
        )
        batch.create_check_constraint(
            "lifecycle_status_valid", "lifecycle_status IN ('active', 'archived')"
        )
    op.create_table(
        "memory_lifecycle_decisions",
        sa.Column("decision_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("memory_id", sa.String(128), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("counterparty_id", sa.String(128), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=False),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("decided_at", UTCDateTime(), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=False),
        sa.Column("preview_digest", sa.String(64), nullable=False),
        sa.CheckConstraint("length(trim(request_id)) > 0", name="request_id_not_blank"),
        sa.CheckConstraint("length(trim(counterparty_id)) > 0", name="counterparty_id_not_blank"),
        sa.CheckConstraint("from_status IN ('active', 'archived')", name="from_status_valid"),
        sa.CheckConstraint("to_status IN ('active', 'archived')", name="to_status_valid"),
        sa.CheckConstraint("expected_version >= 1", name="expected_version_positive"),
        sa.CheckConstraint("result_version >= 1", name="result_version_positive"),
        sa.CheckConstraint(
            "(from_status = to_status AND result_version = expected_version) OR "
            "(from_status != to_status AND result_version = expected_version + 1)",
            name="transition_version_consistent",
        ),
        sa.CheckConstraint("policy_version = 1", name="policy_version_valid"),
        sa.CheckConstraint("approved_by = 'local_operator'", name="approval_valid"),
        sa.CheckConstraint(
            "(from_status = to_status AND reason_code = 'episode_lifecycle_noop') OR "
            "(from_status != to_status AND to_status = 'archived' "
            "AND reason_code = 'episode_archived') OR "
            "(from_status != to_status AND to_status = 'active' "
            "AND reason_code = 'episode_reactivated')",
            name="reason_consistent",
        ),
        sa.CheckConstraint(
            "length(preview_digest) = 64 AND preview_digest NOT GLOB '*[^0-9a-f]*'",
            name="preview_digest_valid",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"], ["episodic_memories.memory_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["satori_identities.identity_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.UniqueConstraint("request_id"),
    )


def downgrade() -> None:
    """Refuse to erase lifecycle availability or receipts before restoring the old schema."""

    if op.get_context().as_sql:
        raise RuntimeError("0015_memory_lifecycle requires online downgrade validation")
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM memory_lifecycle_decisions LIMIT 1")).first():
        raise RuntimeError("memory lifecycle downgrade would discard owner decisions")
    if bind.execute(
        sa.text("SELECT 1 FROM episodic_memories WHERE lifecycle_status != 'active' LIMIT 1")
    ).first():
        raise RuntimeError("memory lifecycle downgrade would discard archived state")
    if bind.execute(
        sa.text("SELECT 1 FROM episodic_memories WHERE aggregate_version != 1 LIMIT 1")
    ).first():
        raise RuntimeError("memory lifecycle downgrade would discard aggregate versions")
    # SQLite batch rename quotes the table name in sqlite_schema. Exact historical
    # recovery admission requires the original unquoted CREATE prefix and fixed DDL.
    columns = (
        "memory_id,schema_version,source_interaction_id,occurred_at,summary,importance,"
        "confidence,created_at,formation_method,formation_version,lifecycle_status"
    )
    op.execute(
        f"CREATE TEMP TABLE _satori_lifecycle_legacy_rows AS "
        f"SELECT {columns} FROM episodic_memories WHERE 0"
    )
    # DML starts the SQLite transaction before destructive DDL, even with no rows.
    op.execute(
        f"INSERT INTO temp._satori_lifecycle_legacy_rows SELECT {columns} FROM episodic_memories"
    )
    op.drop_table("memory_lifecycle_decisions")
    op.drop_table("episodic_memories")
    op.execute(
        "CREATE TABLE episodic_memories ("
        "memory_id VARCHAR(128) NOT NULL, schema_version INTEGER NOT NULL, "
        "source_interaction_id VARCHAR(128) NOT NULL, occurred_at DATETIME NOT NULL, "
        "summary VARCHAR(500) NOT NULL, importance FLOAT NOT NULL, confidence FLOAT NOT NULL, "
        "created_at DATETIME NOT NULL, formation_method VARCHAR(128) NOT NULL, "
        "formation_version INTEGER NOT NULL, lifecycle_status VARCHAR(32) NOT NULL, "
        "CONSTRAINT pk_episodic_memories PRIMARY KEY (memory_id), "
        "CONSTRAINT ck_episodic_memories_ck_episodic_memories_schema_version_positive "
        "CHECK (schema_version >= 1), "
        "CONSTRAINT ck_episodic_memories_ck_episodic_memories_importance_unit_interval "
        "CHECK (importance >= 0.0 AND importance <= 1.0), "
        "CONSTRAINT ck_episodic_memories_ck_episodic_memories_confidence_unit_interval "
        "CHECK (confidence >= 0.0 AND confidence <= 1.0), "
        "CONSTRAINT ck_episodic_memories_ck_episodic_memories_formation_version_positive "
        "CHECK (formation_version >= 1), "
        "CONSTRAINT ck_episodic_memories_ck_episodic_memories_lifecycle_status_valid "
        "CHECK (lifecycle_status = 'active'), "
        "CONSTRAINT ck_episodic_memories_ck_episodic_memories_summary_not_blank "
        "CHECK (length(summary) > 0), "
        "CONSTRAINT fk_episodic_memories_source_interaction_id_conversation_interactions "
        "FOREIGN KEY(source_interaction_id) REFERENCES conversation_interactions "
        "(interaction_id) ON DELETE RESTRICT, "
        "CONSTRAINT uq_episodic_memories_source_interaction_id "
        "UNIQUE (source_interaction_id, formation_version))"
    )
    op.execute(
        f"INSERT INTO episodic_memories ({columns}) "
        f"SELECT {columns} FROM temp._satori_lifecycle_legacy_rows"
    )
    op.execute("DROP TABLE temp._satori_lifecycle_legacy_rows")
