"""Add terminal Reflection payload bodies and freeze their retained provenance.

Revision ID: 0021_reflection_bodies
Revises: 0020_knowledge_bodies
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_reflection_bodies"
down_revision: str | None = "0020_knowledge_bodies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _frozen(run: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM reflection_proposals frozen_child WHERE "
        f"frozen_child.run_id={run} AND frozen_child.body_state='erased')"
    )


def _guard(name: str, event: str, table: str, condition: str) -> None:
    op.execute(
        f"CREATE TRIGGER {name} BEFORE {event} ON {table} WHEN {condition} "
        "BEGIN SELECT RAISE(ABORT,'reflection_body_terminal'); END"
    )


def _triggers() -> None:
    retained = tuple(
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("reflection_proposals")
        if column["name"] not in ("payload", "body_state", "body_action_id")
    )
    unchanged = " AND ".join(f"NEW.{column} IS OLD.{column}" for column in retained)
    terminal_parent = (
        "EXISTS (SELECT 1 FROM reflection_runs r WHERE r.run_id=NEW.run_id "
        "AND r.status IN ('completed','exhausted'))"
    )
    transition = (
        "OLD.body_state='available' AND OLD.body_action_id IS NULL AND "
        "NEW.body_state='erased' AND NEW.payload IS NULL AND "
        f"NEW.body_action_id IS NOT NULL AND {unchanged} AND {terminal_parent}"
    )
    _guard(
        "reflection_proposals_body_update",
        "UPDATE",
        "reflection_proposals",
        "OLD.body_state='erased' OR "
        f"(NEW.body_state='erased' AND NOT ({transition})) OR "
        f"(({_frozen('OLD.run_id')} OR {_frozen('NEW.run_id')}) AND NOT ({transition}))",
    )
    _guard(
        "reflection_proposals_body_insert",
        "INSERT",
        "reflection_proposals",
        "NEW.body_state!='available' OR NEW.body_action_id IS NOT NULL OR "
        f"{_frozen('NEW.run_id')} OR EXISTS (SELECT 1 FROM reflection_proposals existing "
        f"WHERE existing.proposal_id=NEW.proposal_id AND {_frozen('existing.run_id')})",
    )
    _guard(
        "reflection_proposals_body_delete",
        "DELETE",
        "reflection_proposals",
        _frozen("OLD.run_id"),
    )
    for event in ("INSERT", "UPDATE", "DELETE"):
        condition = _frozen("NEW.run_id" if event == "INSERT" else "OLD.run_id")
        if event == "INSERT":
            condition += (
                " OR EXISTS (SELECT 1 FROM reflection_runs existing WHERE "
                "(existing.run_id=NEW.run_id OR existing.run_key=NEW.run_key) AND "
                + _frozen("existing.run_id")
                + ")"
            )
        if event == "UPDATE":
            condition += " OR " + _frozen("NEW.run_id")
        _guard(f"reflection_runs_body_{event.lower()}", event, "reflection_runs", condition)
    for table in ("reflection_sources", "reflection_attempts"):
        for event in ("INSERT", "UPDATE", "DELETE"):
            row = "NEW" if event == "INSERT" else "OLD"
            condition = _frozen(f"{row}.run_id")
            if event == "INSERT":
                key = "source_id" if table == "reflection_sources" else "attempt_id"
                condition += (
                    f" OR EXISTS (SELECT 1 FROM {table} existing WHERE "
                    f"existing.{key}=NEW.{key} AND {_frozen('existing.run_id')})"
                )
            if event == "UPDATE":
                condition += " OR " + _frozen("NEW.run_id")
            _guard(f"{table}_body_{event.lower()}", event, table, condition)
    for event in ("INSERT", "UPDATE", "DELETE"):
        row = "NEW" if event == "INSERT" else "OLD"
        run = f"(SELECT run_id FROM reflection_proposals WHERE proposal_id={row}.proposal_id)"
        condition = _frozen(run)
        if event == "INSERT":
            old_run = (
                "(SELECT run_id FROM reflection_proposals WHERE proposal_id=existing.proposal_id)"
            )
            condition += (
                " OR EXISTS (SELECT 1 FROM reflection_outcomes existing WHERE "
                f"existing.outcome_id=NEW.outcome_id AND {_frozen(old_run)})"
            )
        if event == "UPDATE":
            condition += " OR " + _frozen(
                "(SELECT run_id FROM reflection_proposals WHERE proposal_id=NEW.proposal_id)"
            )
        _guard(f"reflection_outcomes_body_{event.lower()}", event, "reflection_outcomes", condition)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        connection = bind.connection.driver_connection
        if connection is None or not connection.in_transaction:
            bind.exec_driver_sql("BEGIN IMMEDIATE")
    with op.batch_alter_table("reflection_proposals") as batch:
        batch.alter_column("payload", existing_type=sa.JSON(), nullable=True)
        batch.add_column(
            sa.Column("body_state", sa.String(16), nullable=False, server_default="available")
        )
        batch.add_column(sa.Column("body_action_id", sa.String(128), nullable=True))
        batch.create_foreign_key(
            "fk_reflection_proposals_body_action_id_erasure_body_applications",
            "erasure_body_applications",
            ["body_action_id"],
            ["action_id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "body_consistent",
            "(body_state = 'available' AND payload IS NOT NULL "
            "AND json_type(payload) = 'object' AND body_action_id IS NULL) OR "
            "(body_state = 'erased' AND payload IS NULL AND body_action_id IS NOT NULL)",
        )
    _triggers()


def downgrade() -> None:
    raise RuntimeError("reflection_bodies_downgrade_requires_offline_owner_policy")
