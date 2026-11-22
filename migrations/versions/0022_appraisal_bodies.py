"""Terminal paired appraisal explanations with exact retained provenance.

Revision ID: 0022_appraisal_bodies
Revises: 0021_reflection_bodies
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_appraisal_bodies"
down_revision: str | None = "0021_reflection_bodies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _guard(name: str, event: str, table: str, condition: str) -> None:
    op.execute(
        f"CREATE TRIGGER {name} BEFORE {event} ON {table} WHEN {condition} "
        "BEGIN SELECT RAISE(ABORT,'appraisal_explanation_terminal'); END"
    )


def _frozen_audit(event: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM affective_transitions t WHERE "
        + f"t.audit_event_id={event} AND t.explanation_state='erased')"
    )


def _frozen_claim(details: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM affective_transitions t WHERE "
        "t.explanation_state='erased' AND ("
        + f"t.transition_id=json_extract({details},'$.transition_id') OR "
        f"t.interaction_id=json_extract({details},'$.interaction_id') OR "
        f"t.source_message_id=json_extract({details},'$.source_message_id')))"
    )


def _approved(transition: str, action: str | None = None) -> str:
    selector = f"a.action_id={action} AND " if action else ""
    return (
        "EXISTS (SELECT 1 FROM erasure_body_applications "
        "a,json_each(a.action,'$.proposal.targets') target WHERE "
        + selector
        + "json_extract(a.action,'$.proposal.policy_version')='appraisal_explanat"
        "ion_erasure_v1' AND "
        + f"json_extract(target.value,'$.transition_id')={transition}.transition_id AND "
        + f"json_extract(target.value,'$.source_input_id')={transition}.source_input_id AND "
        + f"json_extract(target.value,'$.audit_event_id')={transition}.audit_event_id AND "
        + f"a.identity_id={transition}.identity_id)"
    )


def _triggers() -> None:
    retained = [
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("affective_transitions")
        if column["name"] not in {"reason_codes", "explanation_state", "explanation_action_id"}
    ]
    unchanged = " AND ".join(f"NEW.{name} IS OLD.{name}" for name in retained)
    terminal = (
        "OLD.explanation_state='available' AND NEW.explanation_state='erased' AND "
        "OLD.explanation_action_id IS NULL AND NEW.explanation_action_id IS NOT "
        "NULL AND NEW.reason_codes IS NULL AND "
        + unchanged
        + " AND "
        + _approved("NEW", "NEW.explanation_action_id")
        + " AND EXISTS (SELECT 1 FROM audit_events e WHERE e.event_id=NEW.audit_event_id "
        "AND json_type(e.details,'$.appraisal_reason_codes') IS NULL)"
    )
    _guard(
        "affective_transitions_explanation_update",
        "UPDATE",
        "affective_transitions",
        f"NOT ({terminal})",
    )
    _guard(
        "affective_transitions_explanation_insert",
        "INSERT",
        "affective_transitions",
        "NEW.explanation_state!='available' OR NEW.explanation_action_id IS NOT NULL OR "
        "EXISTS (SELECT 1 FROM affective_transitions existing WHERE "
        "existing.explanation_state='erased' AND "
        "(existing.transition_id=NEW.transition_id OR "
        "existing.interaction_id=NEW.interaction_id OR "
        "existing.audit_event_id=NEW.audit_event_id))",
    )
    _guard(
        "affective_transitions_explanation_delete",
        "DELETE",
        "affective_transitions",
        "OLD.explanation_state='erased'",
    )
    linked = "EXISTS (SELECT 1 FROM affective_transitions t WHERE t.audit_event_id=OLD.event_id)"
    audit_headers = tuple(
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("audit_events")
        if column["name"] != "details"
    )
    audit_unchanged = " AND ".join(f"NEW.{name} IS OLD.{name}" for name in audit_headers)
    removal = (
        audit_unchanged + " AND OLD.event_type='emotion.transition_applied' AND "
        "json_type(OLD.details,'$.appraisal_reason_codes')='array' AND "
        "json_type(NEW.details,'$.appraisal_reason_codes') IS NULL AND "
        "json(NEW.details)=json(json_remove(OLD.details,'$.appraisal_reason_codes')) AND "
        "EXISTS (SELECT 1 FROM affective_transitions t WHERE t.audit_event_id=OLD.event_id "
        "AND t.explanation_state='available' AND " + _approved("t") + ")"
    )
    _guard(
        "audit_events_appraisal_update",
        "UPDATE",
        "audit_events",
        _frozen_audit("OLD.event_id") + " OR (" + linked + " AND NOT (" + removal + ")) OR "
        "(NEW.event_type='emotion.transition_applied' AND " + _frozen_claim("NEW.details") + ")",
    )
    _guard(
        "audit_events_appraisal_insert",
        "INSERT",
        "audit_events",
        _frozen_audit("NEW.event_id")
        + " OR (NEW.event_type='emotion.transition_applied' AND "
        + _frozen_claim("NEW.details")
        + ")",
    )
    _guard("audit_events_appraisal_delete", "DELETE", "audit_events", _frozen_audit("OLD.event_id"))
    for event in ("INSERT", "UPDATE", "DELETE"):
        row = "NEW" if event == "INSERT" else "OLD"
        condition = (
            "EXISTS (SELECT 1 FROM affective_transitions t WHERE "
            "t.explanation_state='erased' AND " + f"t.source_input_id={row}.input_id)"
        )
        if event == "UPDATE":
            condition += (
                " OR EXISTS (SELECT 1 FROM affective_transitions t "
                "WHERE t.explanation_state='erased' AND t.source_input_id=NEW.input_id)"
            )
        _guard(f"source_inputs_appraisal_{event.lower()}", event, "source_inputs", condition)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        connection = bind.connection.driver_connection
        if connection is None or not connection.in_transaction:
            bind.exec_driver_sql("BEGIN IMMEDIATE")
    with op.batch_alter_table("affective_transitions") as batch:
        batch.alter_column("reason_codes", existing_type=sa.JSON(), nullable=True)
        batch.add_column(
            sa.Column(
                "explanation_state", sa.String(16), nullable=False, server_default="available"
            )
        )
        for name in ("explanation_action_id", "source_input_id", "audit_event_id"):
            batch.add_column(sa.Column(name, sa.String(128), nullable=True))
        for name, table, key in (
            ("explanation_action_id", "erasure_body_applications", "action_id"),
            ("source_input_id", "source_inputs", "input_id"),
            ("audit_event_id", "audit_events", "event_id"),
        ):
            batch.create_foreign_key(
                f"fk_affective_transitions_{name}_{table}",
                table,
                [name],
                [key],
                ondelete="RESTRICT",
            )
        batch.create_unique_constraint(
            "uq_affective_transitions_audit_event_id", ["audit_event_id"]
        )
        batch.create_check_constraint(
            "explanation_consistent",
            "(explanation_state='available' AND reason_codes IS NOT NULL AND "
            "json_type(reason_codes)='array' "
            "AND json_array_length(reason_codes)>=1 AND explanation_action_id IS NULL) OR "
            "(explanation_state='erased' AND reason_codes IS NULL AND "
            "explanation_action_id IS NOT NULL "
            "AND source_input_id IS NOT NULL AND audit_event_id IS NOT NULL)",
        )
    _triggers()


def downgrade() -> None:
    raise RuntimeError("appraisal_bodies_downgrade_requires_offline_owner_policy")
