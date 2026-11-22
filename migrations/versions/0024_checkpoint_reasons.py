"""Terminal identity-owned checkpoint reasons without changing numerical history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_checkpoint_reasons"
down_revision: str | None = "0023_audit_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = {
    "personality_checkpoint_approvals": ("approval_id", "checkpoint_approval"),
    "personality_restore_events": ("restore_id", "personality_restore"),
}


def _guard(name: str, event: str, table: str, condition: str) -> None:
    op.execute(
        f"CREATE TRIGGER {name} BEFORE {event} ON {table} WHEN {condition} "
        "BEGIN SELECT RAISE(ABORT,'checkpoint_reason_terminal'); END"
    )


def _approved(row: str, key: str, kind: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM erasure_body_applications a,"
        "json_each(a.action,'$.proposal.targets') target WHERE "
        f"a.action_id={row}.reason_action_id AND a.root_message_id IS NULL AND "
        f"a.identity_id={row}.identity_id AND "
        "json_extract(a.action,'$.proposal.policy_version')='checkpoint_reason_erasure_v1' AND "
        f"json_extract(a.action,'$.proposal.scope.identity_id')={row}.identity_id AND "
        "json_extract(a.action,'$.proposal.scope.lineage_id')=a.lineage_id AND "
        f"json_extract(target.value,'$.selector.kind')='{kind}' AND "
        f"json_extract(target.value,'$.selector.record_id')={row}.{key})"
    )


def _alias(table: str, key: str) -> str:
    matches = [f"existing.{key}=NEW.{key}"]
    if table == "personality_checkpoint_approvals":
        matches.append("existing.checkpoint_id=NEW.checkpoint_id")
    else:
        matches += [
            "existing.revision_id=NEW.revision_id",
            "(existing.identity_id=NEW.identity_id AND "
            "existing.after_aggregate_version=NEW.after_aggregate_version)",
            "existing.resulting_checkpoint_id=NEW.resulting_checkpoint_id",
        ]
    return (
        f"EXISTS (SELECT 1 FROM {table} existing WHERE existing.reason_state='erased' AND ("
        + " OR ".join(matches)
        + "))"
    )


def _frozen_checkpoint(value: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM personality_checkpoint_approvals p "
        f"WHERE p.reason_state='erased' AND p.checkpoint_id={value}) OR "
        "EXISTS (SELECT 1 FROM personality_restore_events r WHERE r.reason_state='erased' "
        f"AND (r.source_checkpoint_id={value} OR r.resulting_checkpoint_id={value}))"
    )


def _triggers() -> None:
    for table, (key, kind) in _TABLES.items():
        retained = [
            column["name"]
            for column in sa.inspect(op.get_bind()).get_columns(table)
            if column["name"] not in {"reason", "reason_state", "reason_action_id"}
        ]
        unchanged = " AND ".join(f"NEW.{name} IS OLD.{name}" for name in retained)
        transition = (
            "OLD.reason_state='available' AND NEW.reason_state='erased' AND "
            "OLD.reason_action_id IS NULL AND NEW.reason IS NULL AND "
            "NEW.reason_action_id IS NOT NULL AND "
            + unchanged
            + " AND "
            + _approved("NEW", key, kind)
        )
        _guard(
            f"{table}_reason_update",
            "UPDATE",
            table,
            "OLD.reason_state='erased' OR "
            + _alias(table, key)
            + " OR (NEW.reason_state='erased' AND NOT ("
            + transition
            + "))",
        )
        _guard(f"{table}_reason_delete", "DELETE", table, "OLD.reason_state='erased'")
        _guard(
            f"{table}_reason_insert",
            "INSERT",
            table,
            "NEW.reason_state!='available' OR NEW.reason_action_id IS NOT NULL OR "
            + _alias(table, key),
        )
    for operation in ("INSERT", "UPDATE", "DELETE"):
        row = "NEW" if operation == "INSERT" else "OLD"
        checkpoint = _frozen_checkpoint(f"{row}.checkpoint_id")
        if operation == "UPDATE":
            checkpoint += " OR " + _frozen_checkpoint("NEW.checkpoint_id")
        if operation == "INSERT":
            checkpoint = (
                "EXISTS (SELECT 1 FROM personality_checkpoints existing WHERE ("
                "existing.checkpoint_id=NEW.checkpoint_id OR "
                "(existing.identity_id=NEW.identity_id AND "
                "existing.checkpoint_hash=NEW.checkpoint_hash) "
                "OR (existing.identity_id=NEW.identity_id AND "
                "existing.source_aggregate_version=NEW.source_aggregate_version AND "
                "existing.checkpoint_kind=NEW.checkpoint_kind)) AND ("
                + _frozen_checkpoint("existing.checkpoint_id")
                + "))"
            )
        _guard(
            f"personality_checkpoints_reason_{operation.lower()}",
            operation,
            "personality_checkpoints",
            checkpoint,
        )
        traits = _frozen_checkpoint(f"{row}.checkpoint_id")
        if operation == "UPDATE":
            traits += " OR " + _frozen_checkpoint("NEW.checkpoint_id")
        _guard(
            f"personality_checkpoint_traits_reason_{operation.lower()}",
            operation,
            "personality_checkpoint_traits",
            traits,
        )
        revision = (
            "EXISTS (SELECT 1 FROM personality_restore_events r WHERE "
            f"r.reason_state='erased' AND r.revision_id={row}.revision_id)"
        )
        if operation in {"INSERT", "UPDATE"}:
            revision += (
                " OR EXISTS (SELECT 1 FROM personality_revisions existing JOIN "
                "personality_restore_events r ON r.revision_id=existing.revision_id "
                "WHERE r.reason_state='erased' AND (existing.revision_id=NEW.revision_id OR "
                "existing.resulting_checkpoint_id=NEW.resulting_checkpoint_id OR "
                "(existing.identity_id=NEW.identity_id AND "
                "existing.after_aggregate_version=NEW.after_aggregate_version) OR "
                "existing.reflection_outcome_id=NEW.reflection_outcome_id))"
            )
        _guard(
            f"personality_revisions_reason_{operation.lower()}",
            operation,
            "personality_revisions",
            revision,
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        connection = bind.connection.driver_connection
        if connection is None or not connection.in_transaction:
            bind.exec_driver_sql("BEGIN IMMEDIATE")
    # SQLite validates external trigger bodies during batch-table rename. Suspend only
    # those referencing rebuilt tables, inside the same real DDL transaction, then
    # restore their exact definitions before adding the new owner guards.
    rebuilt = {*_TABLES, "erasure_body_applications"}
    existing_triggers = tuple(
        (str(name), str(sql))
        for name, sql in bind.exec_driver_sql(
            "SELECT name,sql FROM sqlite_schema WHERE type='trigger' ORDER BY name"
        )
        if sql is not None and any(table in str(sql).lower() for table in rebuilt)
    )
    for name, _sql in existing_triggers:
        quoted = name.replace('"', '""')
        bind.exec_driver_sql(f'DROP TRIGGER "{quoted}"')
    for table in _TABLES:
        old_checks = sa.inspect(bind).get_check_constraints(table)
        reason_check = next(
            item["name"] for item in old_checks if str(item["name"]).endswith("reason_not_blank")
        )
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(op.f(reason_check), type_="check")
            batch.alter_column("reason", existing_type=sa.String(500), nullable=True)
            batch.add_column(
                sa.Column("reason_state", sa.String(16), nullable=False, server_default="available")
            )
            batch.add_column(sa.Column("reason_action_id", sa.String(128), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_reason_action_id_erasure_body_applications",
                "erasure_body_applications",
                ["reason_action_id"],
                ["action_id"],
                ondelete="RESTRICT",
            )
            batch.create_check_constraint(
                "reason_body_consistent",
                "(reason_state='available' AND reason IS NOT NULL AND length(reason)>0 "
                "AND reason_action_id IS NULL) OR (reason_state='erased' AND reason IS NULL "
                "AND reason_action_id IS NOT NULL)",
            )
    with op.batch_alter_table("erasure_body_applications") as batch:
        batch.alter_column("root_message_id", existing_type=sa.String(128), nullable=True)
        batch.create_check_constraint(
            "closed_scope_binding",
            "json_valid(action) AND coalesce((json_extract(action,'$.proposal.policy_ver"
            "sion')='checkpoint_reason_erasure_v1' AND root_message_id IS NULL AND json_"
            "extract(action,'$.proposal.scope.identity_id')=identity_id AND json_extract"
            "(action,'$.proposal.scope.lineage_id')=lineage_id AND json_type(action,'$.p"
            "roposal.root') IS NULL AND json_type(action,'$.proposal.intent_request_id')"
            " IS NULL) OR (json_extract(action,'$.proposal.policy_version') IN ('episode"
            "_body_erasure_v1','dialogue_body_erasure_v1','knowledge_body_erasure_v1','r"
            "eflection_body_erasure_v1','appraisal_explanation_erasure_v1') AND root_mes"
            "sage_id IS NOT NULL AND json_extract(action,'$.proposal.root.message_id')=r"
            "oot_message_id AND json_extract(action,'$.proposal.root.identity_id')=ident"
            "ity_id AND json_extract(action,'$.proposal.root.lineage_id')=lineage_id),0)"
            "=1",
        )
    for _name, sql in existing_triggers:
        bind.exec_driver_sql(sql)
    _triggers()


def downgrade() -> None:
    raise RuntimeError("checkpoint_reasons_downgrade_requires_offline_owner_policy")
