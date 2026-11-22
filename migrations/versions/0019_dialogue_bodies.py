"""Retain immutable dialogue headers after approved terminal body handling.

Revision ID: 0019_dialogue_bodies
Revises: 0018_erasure_bodies
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_dialogue_bodies"
down_revision: str | None = "0018_erasure_bodies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BODY_CHECK = (
    "(body_state = 'available' AND content IS NOT NULL AND length(content) > 0 "
    "AND body_action_id IS NULL) OR (body_state = 'erased' AND content IS NULL "
    "AND body_action_id IS NOT NULL)"
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        connection = bind.connection.driver_connection
        if connection is None or not connection.in_transaction:
            bind.exec_driver_sql("BEGIN IMMEDIATE")
    constraints = sa.inspect(bind).get_check_constraints("conversation_messages")
    old_constraint = next(
        item["name"] for item in constraints if item["sqltext"] == "length(content) > 0"
    )
    if not isinstance(old_constraint, str):
        raise RuntimeError("dialogue_bodies_legacy_schema_invalid")
    with op.batch_alter_table("conversation_messages") as batch:
        batch.drop_constraint(op.f(old_constraint), type_="check")
        batch.alter_column("content", existing_type=sa.Text(), nullable=True)
        batch.add_column(
            sa.Column("body_state", sa.String(16), nullable=False, server_default="available")
        )
        batch.add_column(sa.Column("body_action_id", sa.String(128), nullable=True))
        batch.create_foreign_key(
            "fk_conversation_messages_body_action_id_erasure_body_applications",
            "erasure_body_applications",
            ["body_action_id"],
            ["action_id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint("body_consistent", _BODY_CHECK)
    immutable = (
        "message_id",
        "session_id",
        "interaction_id",
        "schema_version",
        "role",
        "created_at",
        "sequence",
    )
    changed = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in immutable)
    op.execute(
        "CREATE TRIGGER dialogue_body_terminal BEFORE UPDATE ON conversation_messages "
        f"WHEN OLD.body_state='erased' OR (NEW.body_state='erased' AND ({changed})) "
        "OR (NEW.body_state='erased' AND NOT EXISTS (SELECT 1 "
        "FROM conversation_interactions i JOIN conversation_sessions s "
        "ON s.session_id=i.session_id WHERE i.interaction_id=NEW.interaction_id "
        "AND i.session_id=NEW.session_id AND i.status='completed' AND s.scope_verified=1)) "
        "OR (NEW.body_state='erased' AND ((SELECT count(*) FROM conversation_messages m "
        "WHERE m.interaction_id=NEW.interaction_id)!=2 "
        "OR NOT EXISTS (SELECT 1 FROM conversation_messages m "
        "WHERE m.interaction_id=NEW.interaction_id AND m.session_id=NEW.session_id "
        "AND m.role='user' AND m.sequence=1) "
        "OR NOT EXISTS (SELECT 1 FROM conversation_messages m "
        "WHERE m.interaction_id=NEW.interaction_id AND m.session_id=NEW.session_id "
        "AND m.role='assistant' AND m.sequence=2))) "
        "BEGIN SELECT RAISE(ABORT,'dialogue_body_terminal'); END"
    )
    op.execute(
        "CREATE TRIGGER dialogue_body_delete BEFORE DELETE ON conversation_messages "
        "WHEN OLD.body_state='erased' "
        "BEGIN SELECT RAISE(ABORT,'dialogue_body_terminal'); END"
    )
    op.execute(
        "CREATE TRIGGER dialogue_body_insert BEFORE INSERT ON conversation_messages "
        "WHEN NEW.body_state!='available' OR NEW.body_action_id IS NOT NULL "
        "OR EXISTS (SELECT 1 FROM conversation_messages m "
        "WHERE m.interaction_id=NEW.interaction_id AND m.body_state='erased') "
        "BEGIN SELECT RAISE(ABORT,'dialogue_body_terminal'); END"
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER dialogue_interaction_{operation.lower()} BEFORE {operation} "
            "ON conversation_interactions WHEN EXISTS (SELECT 1 FROM conversation_messages m "
            "WHERE m.interaction_id=OLD.interaction_id AND m.body_state='erased') "
            "BEGIN SELECT RAISE(ABORT,'dialogue_body_terminal'); END"
        )


def downgrade() -> None:
    """Never remove retained action proof or fabricate the former message body."""

    raise RuntimeError("dialogue_bodies_downgrade_requires_offline_owner_policy")
