"""Prospective closed audit metadata bindings without historical inference.

Revision ID: 0023_audit_metadata
Revises: 0022_appraisal_bodies
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_audit_metadata"
down_revision: str | None = "0022_appraisal_bodies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen migration contract; future domain registries cannot change this DDL.
_FAMILIES = {
    "activation": None,
    "episode_formation": "episode_decision_id",
    "memory_lifecycle": "lifecycle_decision_id",
    "semantic_formation": "semantic_decision_id",
    "user_model_formation": "model_decision_id",
    "world_model_formation": "model_decision_id",
    "position_formation": "position_decision_id",
    "reflection_position": "reflection_outcome_id",
    "reflection_inclination": "reflection_outcome_id",
    "reflection_personality": "reflection_outcome_id",
    "reflection_disabled": "reflection_outcome_id",
    "relationship_transition": "relationship_decision_id",
    "checkpoint_approval": "checkpoint_approval_id",
    "personality_restore": "restore_event_id",
    "source_withdrawal": "withdrawal_receipt_id",
}
_SLOTS = {
    "checkpoint_approval_id": ("personality_checkpoint_approvals", "approval_id"),
    "episode_decision_id": ("episode_formation_decisions", "decision_id"),
    "lifecycle_decision_id": ("memory_lifecycle_decisions", "decision_id"),
    "model_decision_id": ("model_formation_decisions", "decision_id"),
    "position_decision_id": ("position_formation_decisions", "decision_id"),
    "reflection_outcome_id": ("reflection_outcomes", "outcome_id"),
    "relationship_decision_id": ("relationship_decisions", "decision_id"),
    "restore_event_id": ("personality_restore_events", "restore_id"),
    "semantic_decision_id": ("semantic_formation_decisions", "decision_id"),
    "withdrawal_receipt_id": ("owner_source_withdrawals", "receipt_id"),
}


def _guard(name: str, event: str, table: str, condition: str) -> None:
    op.execute(
        f"CREATE TRIGGER {name} BEFORE {event} ON {table} WHEN {condition} "
        "BEGIN SELECT RAISE(ABORT,'audit_metadata_immutable'); END"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        connection = bind.connection.driver_connection
        if connection is None or not connection.in_transaction:
            bind.exec_driver_sql("BEGIN IMMEDIATE")
    slot_shape = " OR ".join(
        "(family='"
        + family
        + "' AND "
        + " AND ".join(
            f"{candidate} IS {'NOT NULL' if candidate == slot else 'NULL'}" for candidate in _SLOTS
        )
        + ")"
        for family, slot in _FAMILIES.items()
    )
    op.create_table(
        "audit_metadata_bindings",
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("family", sa.String(32), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False),
        sa.Column("source_input_id", sa.String(128), nullable=True),
        *(sa.Column(slot, sa.String(128), nullable=True) for slot in _SLOTS),
        sa.PrimaryKeyConstraint("event_id", name="pk_audit_metadata_bindings"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["audit_events.event_id"],
            ondelete="RESTRICT",
            name="fk_audit_metadata_bindings_event_id_audit_events",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["satori_identities.identity_id"],
            ondelete="RESTRICT",
            name="fk_audit_metadata_bindings_identity_id_satori_identities",
        ),
        sa.ForeignKeyConstraint(
            ["source_input_id"],
            ["source_inputs.input_id"],
            ondelete="RESTRICT",
            name="fk_audit_metadata_bindings_source_input_id_source_inputs",
        ),
        *(
            sa.ForeignKeyConstraint(
                [slot],
                [f"{table}.{key}"],
                ondelete="RESTRICT",
                name=f"fk_audit_metadata_bindings_{slot}_{table}",
            )
            for slot, (table, key) in _SLOTS.items()
        ),
        sa.CheckConstraint("contract_version=1", name="contract_version_valid"),
        sa.CheckConstraint(
            "family IN (" + ",".join(repr(family) for family in _FAMILIES) + ")",
            name="family_valid",
        ),
        sa.CheckConstraint(slot_shape, name="owner_slot_consistent"),
        sa.CheckConstraint(
            "length(event_id) BETWEEN 1 AND 128 AND length(identity_id) BETWEEN 1 AND 128",
            name="headers_bounded",
        ),
        sa.CheckConstraint(
            "length(event_digest)=64 AND event_digest NOT GLOB '*[^0-9a-f]*'", name="digest_valid"
        ),
        *(
            sa.UniqueConstraint("family", slot, name=f"uq_audit_metadata_bindings_family_{slot}")
            for slot in _SLOTS
        ),
    )
    op.create_index(
        "uq_audit_metadata_bindings_activation_identity",
        "audit_metadata_bindings",
        ["identity_id"],
        unique=True,
        sqlite_where=sa.text("family='activation'"),
    )
    logical_conflict = " OR ".join(
        f"(NEW.{slot} IS NOT NULL AND existing.{slot}=NEW.{slot})" for slot in _SLOTS
    )
    _guard(
        "audit_metadata_bindings_insert",
        "INSERT",
        "audit_metadata_bindings",
        "NOT EXISTS (SELECT 1 FROM audit_events event WHERE event.event_id=NEW.event_id) OR "
        "EXISTS (SELECT 1 FROM audit_events event WHERE event.event_id=NEW.event_id "
        "AND event.event_type='emotion.transition_applied') OR "
        "EXISTS (SELECT 1 FROM audit_metadata_bindings existing WHERE "
        "existing.event_id=NEW.event_id OR (existing.family=NEW.family AND ("
        "(NEW.family='activation' AND existing.identity_id=NEW.identity_id) OR "
        + logical_conflict
        + ")))",
    )
    _guard("audit_metadata_bindings_update", "UPDATE", "audit_metadata_bindings", "1")
    _guard("audit_metadata_bindings_delete", "DELETE", "audit_metadata_bindings", "1")
    bound_old = (
        "EXISTS (SELECT 1 FROM audit_metadata_bindings binding WHERE binding.event_id=OLD.event_id)"
    )
    bound_new = (
        "EXISTS (SELECT 1 FROM audit_metadata_bindings binding WHERE binding.event_id=NEW.event_id)"
    )
    _guard("audit_events_metadata_insert", "INSERT", "audit_events", bound_new)
    _guard("audit_events_metadata_update", "UPDATE", "audit_events", bound_old + " OR " + bound_new)
    _guard("audit_events_metadata_delete", "DELETE", "audit_events", bound_old)


def downgrade() -> None:
    raise RuntimeError("audit_metadata_downgrade_requires_offline_owner_policy")
