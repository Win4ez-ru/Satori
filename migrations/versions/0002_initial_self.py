"""Create activation, identity, personality, values, and minimal audit state.

Revision ID: 0002_initial_self
Revises: 0001_foundation
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0002_initial_self"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the normalized persistent state required by Stage 2."""

    op.create_table(
        "satori_identities",
        sa.Column("identity_id", sa.String(length=128), nullable=False),
        sa.Column("installation_slot", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("activation_time", UTCDateTime(), nullable=False),
        sa.Column("identity_version", sa.Integer(), nullable=False),
        sa.Column("seed_id", sa.String(length=128), nullable=False),
        sa.Column("seed_schema_version", sa.Integer(), nullable=False),
        sa.Column("seed_content_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "identity_version >= 1", name="ck_satori_identities_identity_version_positive"
        ),
        sa.CheckConstraint(
            "installation_slot = 1", name="ck_satori_identities_primary_slot_is_one"
        ),
        sa.CheckConstraint("length(name) > 0", name="ck_satori_identities_name_not_blank"),
        sa.CheckConstraint(
            "seed_schema_version >= 1",
            name="ck_satori_identities_seed_schema_version_positive",
        ),
        sa.CheckConstraint(
            "length(seed_content_hash) = 64",
            name="ck_satori_identities_seed_hash_length",
        ),
        sa.PrimaryKeyConstraint("identity_id", name="pk_satori_identities"),
        sa.UniqueConstraint(
            "installation_slot",
            name="uq_satori_identities_installation_slot",
        ),
    )
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", UTCDateTime(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "length(aggregate_id) > 0", name="ck_audit_events_aggregate_id_not_blank"
        ),
        sa.CheckConstraint(
            "length(aggregate_type) > 0", name="ck_audit_events_aggregate_type_not_blank"
        ),
        sa.CheckConstraint("length(event_type) > 0", name="ck_audit_events_event_type_not_blank"),
        sa.CheckConstraint("schema_version >= 1", name="ck_audit_events_schema_version_positive"),
        sa.CheckConstraint("length(trace_id) > 0", name="ck_audit_events_trace_id_not_blank"),
        sa.PrimaryKeyConstraint("event_id", name="pk_audit_events"),
    )
    op.create_table(
        "satori_personality_states",
        sa.Column("identity_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "aggregate_version >= 1",
            name="ck_satori_personality_states_aggregate_version_positive",
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name="ck_satori_personality_states_schema_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["satori_identities.identity_id"],
            name="fk_satori_personality_states_identity_id_satori_identities",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("identity_id", name="pk_satori_personality_states"),
    )
    op.create_table(
        "satori_value_sets",
        sa.Column("identity_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "aggregate_version >= 1",
            name="ck_satori_value_sets_aggregate_version_positive",
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name="ck_satori_value_sets_schema_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["satori_identities.identity_id"],
            name="fk_satori_value_sets_identity_id_satori_identities",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("identity_id", name="pk_satori_value_sets"),
    )
    op.create_table(
        "satori_personality_traits",
        sa.Column("identity_id", sa.String(length=128), nullable=False),
        sa.Column("trait_key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("baseline_value", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "baseline_value >= 0.0 AND baseline_value <= 1.0",
            name="ck_satori_personality_traits_baseline_value_unit_interval",
        ),
        sa.CheckConstraint(
            "length(trait_key) > 0",
            name="ck_satori_personality_traits_trait_key_not_blank",
        ),
        sa.CheckConstraint(
            "value >= 0.0 AND value <= 1.0",
            name="ck_satori_personality_traits_value_unit_interval",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["satori_personality_states.identity_id"],
            name="fk_satori_personality_traits_identity_id_satori_personality_states",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "identity_id",
            "trait_key",
            name="pk_satori_personality_traits",
        ),
    )
    op.create_table(
        "satori_values",
        sa.Column("identity_id", sa.String(length=128), nullable=False),
        sa.Column("value_key", sa.String(length=64), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "length(description) > 0",
            name="ck_satori_values_description_not_blank",
        ),
        sa.CheckConstraint(
            "strength >= 0.0 AND strength <= 1.0",
            name="ck_satori_values_strength_unit_interval",
        ),
        sa.CheckConstraint("length(value_key) > 0", name="ck_satori_values_value_key_not_blank"),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["satori_value_sets.identity_id"],
            name="fk_satori_values_identity_id_satori_value_sets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("identity_id", "value_key", name="pk_satori_values"),
    )


def downgrade() -> None:
    """Remove Stage 2 state while preserving the Stage 1 baseline."""

    op.drop_table("satori_values")
    op.drop_table("satori_personality_traits")
    op.drop_table("satori_value_sets")
    op.drop_table("satori_personality_states")
    op.drop_table("audit_events")
    op.drop_table("satori_identities")
