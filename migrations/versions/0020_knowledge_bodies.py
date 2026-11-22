"""Add one-way derived knowledge bodies without changing witnessed evidence shapes.

Revision ID: 0020_knowledge_bodies
Revises: 0019_dialogue_bodies
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_knowledge_bodies"
down_revision: str | None = "0019_dialogue_bodies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARENTS = {
    "semantic_claims": ("claim_id", ("value", "normalized_value")),
    "user_model_claims": ("claim_id", ("value", "normalized_value")),
    "world_model_claims": (
        "claim_id",
        ("subject_label", "normalized_subject_label", "value", "normalized_value"),
    ),
    "satori_positions": ("position_id", ("proposition", "normalized_proposition")),
    "satori_inclinations": (
        "inclination_id",
        ("topic", "normalized_topic", "alternative_topic", "normalized_alternative_topic"),
    ),
}
_CHILDREN = {
    "semantic_claims": ("semantic_claim_evidence", "semantic_claim_revisions"),
    "user_model_claims": ("user_model_claim_evidence", "user_model_claim_revisions"),
    "world_model_claims": ("world_model_claim_evidence", "world_model_claim_revisions"),
    "satori_positions": ("satori_position_evidence", "satori_position_revisions"),
    "satori_inclinations": ("satori_inclination_evidence", "satori_inclination_revisions"),
}


def _body_check(fields: tuple[str, ...]) -> str:
    required = tuple(
        field
        for field in fields
        if field not in ("alternative_topic", "normalized_alternative_topic")
    )
    available = " AND ".join(f"{field} IS NOT NULL" for field in required)
    erased = " AND ".join(f"{field} IS NULL" for field in fields)
    return (
        f"(body_state = 'available' AND {available} AND body_action_id IS NULL) OR "
        f"(body_state = 'erased' AND {erased} AND body_action_id IS NOT NULL)"
    )


def _physical_transaction() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        connection = bind.connection.driver_connection
        if connection is None or not connection.in_transaction:
            bind.exec_driver_sql("BEGIN IMMEDIATE")


def _triggers() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, (key, fields) in _PARENTS.items():
        retained = tuple(
            column["name"]
            for column in inspector.get_columns(table)
            if column["name"] not in (*fields, "body_state", "body_action_id")
        )
        changed = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in retained)
        op.execute(
            f"CREATE TRIGGER {table}_body_update BEFORE UPDATE ON {table} "
            f"WHEN OLD.body_state='erased' OR (NEW.body_state='erased' AND ({changed})) "
            "BEGIN SELECT RAISE(ABORT,'knowledge_body_terminal'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_body_insert BEFORE INSERT ON {table} "
            "WHEN NEW.body_state!='available' OR NEW.body_action_id IS NOT NULL "
            "BEGIN SELECT RAISE(ABORT,'knowledge_body_terminal'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_body_delete BEFORE DELETE ON {table} "
            "WHEN OLD.body_state='erased' "
            "BEGIN SELECT RAISE(ABORT,'knowledge_body_terminal'); END"
        )
        for child in _CHILDREN[table]:
            terminal_old = (
                f"EXISTS (SELECT 1 FROM {table} p WHERE p.{key}=OLD.{key} "
                "AND p.body_state='erased')"
            )
            terminal_new = terminal_old.replace("OLD.", "NEW.")
            insertion = terminal_new
            if child == "satori_position_evidence":
                insertion += " OR NEW.quote IS NULL OR NEW.normalized_signature IS NULL"
            op.execute(
                f"CREATE TRIGGER {child}_body_insert BEFORE INSERT ON {child} "
                f"WHEN {insertion} BEGIN SELECT RAISE(ABORT,'knowledge_body_terminal'); END"
            )
            update_guard = f"{terminal_old} OR {terminal_new}"
            if child == "satori_position_evidence":
                retained_child = tuple(
                    column["name"]
                    for column in inspector.get_columns(child)
                    if column["name"] not in ("quote", "normalized_signature")
                )
                unchanged = " AND ".join(
                    f"NEW.{column} IS OLD.{column}" for column in retained_child
                )
                permitted = (
                    f"({terminal_old} AND OLD.quote IS NOT NULL "
                    "AND OLD.normalized_signature IS NOT NULL AND NEW.quote IS NULL "
                    f"AND NEW.normalized_signature IS NULL AND {unchanged})"
                )
                update_guard = (
                    f"(({update_guard}) AND NOT {permitted}) OR "
                    f"((NEW.quote IS NULL OR NEW.normalized_signature IS NULL) AND NOT {permitted})"
                )
            op.execute(
                f"CREATE TRIGGER {child}_body_update BEFORE UPDATE ON {child} "
                f"WHEN {update_guard} BEGIN SELECT RAISE(ABORT,'knowledge_body_terminal'); END"
            )
            op.execute(
                f"CREATE TRIGGER {child}_body_delete BEFORE DELETE ON {child} "
                f"WHEN {terminal_old} BEGIN SELECT RAISE(ABORT,'knowledge_body_terminal'); END"
            )


def upgrade() -> None:
    _physical_transaction()
    inspector = sa.inspect(op.get_bind())
    for table, (_, fields) in _PARENTS.items():
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        constraints = inspector.get_check_constraints(table)
        with op.batch_alter_table(table) as batch:
            if table == "semantic_claims":
                old = next(
                    item["name"]
                    for item in constraints
                    if item["sqltext"] == "length(normalized_value) > 0"
                )
                batch.drop_constraint(op.f(old), type_="check")
                batch.create_check_constraint(
                    "normalized_value_not_blank",
                    "body_state = 'erased' OR length(normalized_value) > 0",
                )
            if table == "satori_inclinations":
                old = next(item for item in constraints if "kind_shape_valid" in item["name"])
                batch.drop_constraint(op.f(old["name"]), type_="check")
                batch.create_check_constraint(
                    "kind_shape_valid", "body_state = 'erased' OR " + old["sqltext"]
                )
            for field in fields:
                batch.alter_column(field, existing_type=columns[field]["type"], nullable=True)
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
            batch.create_check_constraint("body_consistent", _body_check(fields))
    with op.batch_alter_table("satori_position_evidence") as batch:
        batch.alter_column("quote", existing_type=sa.Text(), nullable=True)
        batch.alter_column("normalized_signature", existing_type=sa.String(512), nullable=True)
        batch.create_check_constraint(
            "body_pair_consistent",
            "(quote IS NOT NULL AND normalized_signature IS NOT NULL) OR "
            "(quote IS NULL AND normalized_signature IS NULL)",
        )
    _triggers()


def downgrade() -> None:
    raise RuntimeError("knowledge_bodies_downgrade_requires_offline_owner_policy")
