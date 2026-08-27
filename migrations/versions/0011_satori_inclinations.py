"""Add evidence-backed Stage 13 Satori inclinations.

Revision ID: 0011_satori_inclinations
Revises: 0010_reflection_runs
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0011_satori_inclinations"
down_revision: str | None = "0010_reflection_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only empty Stage 13 state and nullable Reflection V2 provenance."""

    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.add_column(sa.Column("inclination_context_status", sa.String(32), nullable=True))
        batch_op.add_column(
            sa.Column("inclination_context_schema_version", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("inclination_context_ids", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("inclination_curiosity_influence", sa.Float(), nullable=True))
        batch_op.create_check_constraint(
            "ck_conversation_interactions_inclination_context_status_valid",
            "inclination_context_status IS NULL OR inclination_context_status IN "
            "('not_requested', 'empty', 'available')",
        )
        batch_op.create_check_constraint(
            "ck_conversation_interactions_inclination_context_schema_version_positive",
            "inclination_context_schema_version IS NULL OR inclination_context_schema_version >= 1",
        )
        batch_op.create_check_constraint(
            "ck_conversation_interactions_inclination_curiosity_influence_valid",
            "inclination_curiosity_influence IS NULL OR "
            "(inclination_curiosity_influence >= 0.0 "
            "AND inclination_curiosity_influence <= 0.20)",
        )

    with op.batch_alter_table("reflection_sources") as batch_op:
        batch_op.add_column(sa.Column("affective_transition_id", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("affective_state_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("affective_signal_hash", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_reflection_sources_affective_transition_id_affective_transitions",
            "affective_transitions",
            ["affective_transition_id"],
            ["transition_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "ck_reflection_sources_affective_state_version_positive",
            "affective_state_version IS NULL OR affective_state_version >= 1",
        )
        batch_op.create_check_constraint(
            "ck_reflection_sources_affective_attachment_all_or_none",
            "(affective_transition_id IS NULL AND affective_state_version IS NULL "
            "AND affective_signal_hash IS NULL) OR "
            "(affective_transition_id IS NOT NULL AND affective_state_version IS NOT NULL "
            "AND affective_signal_hash IS NOT NULL)",
        )

    with op.batch_alter_table("reflection_proposals") as batch_op:
        batch_op.drop_constraint("ck_reflection_proposals_target_owner_valid", type_="check")
        batch_op.create_check_constraint(
            "ck_reflection_proposals_target_owner_valid",
            "target_owner IN ('satori_positions', 'satori_inclinations', 'personality', 'values')",
        )

    op.create_table(
        "satori_inclinations",
        sa.Column("inclination_id", sa.String(128), nullable=False),
        sa.Column("inclination_key", sa.String(64), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("normalization_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("topic", sa.String(96), nullable=False),
        sa.Column("normalized_topic", sa.String(96), nullable=False),
        sa.Column("alternative_topic", sa.String(96), nullable=True),
        sa.Column("normalized_alternative_topic", sa.String(96), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("stability", sa.Float(), nullable=False),
        sa.Column("state_as_of", UTCDateTime(), nullable=False),
        sa.Column("last_accepted_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_satori_inclinations_schema_version_positive"
        ),
        sa.CheckConstraint(
            "aggregate_version >= 1", name="ck_satori_inclinations_aggregate_version_positive"
        ),
        sa.CheckConstraint(
            "policy_version >= 1", name="ck_satori_inclinations_policy_version_positive"
        ),
        sa.CheckConstraint(
            "normalization_version >= 1",
            name="ck_satori_inclinations_normalization_version_positive",
        ),
        sa.CheckConstraint(
            "kind IN ('interest', 'preference')", name="ck_satori_inclinations_kind_valid"
        ),
        sa.CheckConstraint(
            "score >= -1.0 AND score <= 1.0", name="ck_satori_inclinations_score_valid"
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_satori_inclinations_confidence_valid",
        ),
        sa.CheckConstraint(
            "stability >= 0.0 AND stability <= 1.0",
            name="ck_satori_inclinations_stability_valid",
        ),
        sa.CheckConstraint(
            "(kind = 'interest' AND alternative_topic IS NULL "
            "AND normalized_alternative_topic IS NULL AND score >= 0.0) OR "
            "(kind = 'preference' AND alternative_topic IS NOT NULL "
            "AND normalized_alternative_topic IS NOT NULL)",
            name="ck_satori_inclinations_kind_shape_valid",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["satori_identities.identity_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("inclination_id", name="pk_satori_inclinations"),
        sa.UniqueConstraint(
            "identity_id",
            "inclination_key",
            name="uq_satori_inclinations_identity_inclination_key",
        ),
    )
    op.create_index(
        "ix_satori_inclinations_identity_kind",
        "satori_inclinations",
        ["identity_id", "kind", "updated_at"],
    )

    op.create_table(
        "satori_inclination_evidence",
        sa.Column("evidence_id", sa.String(128), nullable=False),
        sa.Column("inclination_id", sa.String(128), nullable=False),
        sa.Column("reflection_source_id", sa.String(128), nullable=False),
        sa.Column("affective_transition_id", sa.String(128), nullable=False),
        sa.Column("affective_state_version", sa.Integer(), nullable=False),
        sa.Column("affective_signal_hash", sa.String(64), nullable=False),
        sa.Column("source_message_id", sa.String(128), nullable=False),
        sa.Column("source_interaction_id", sa.String(128), nullable=False),
        sa.Column("source_session_id", sa.String(128), nullable=False),
        sa.Column("source_counterparty_id", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content_signature", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("signal", sa.Float(), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.Column("accepted_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "affective_state_version >= 1",
            name="ck_satori_inclination_evidence_affective_state_version_positive",
        ),
        sa.CheckConstraint(
            "role IN ('topic', 'option_a', 'option_b')",
            name="ck_satori_inclination_evidence_role_valid",
        ),
        sa.CheckConstraint(
            "signal >= -1.0 AND signal <= 1.0",
            name="ck_satori_inclination_evidence_signal_valid",
        ),
        sa.ForeignKeyConstraint(
            ["inclination_id"], ["satori_inclinations.inclination_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reflection_source_id"], ["reflection_sources.source_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["affective_transition_id"],
            ["affective_transitions.transition_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["conversation_messages.message_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_interaction_id"],
            ["conversation_interactions.interaction_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_session_id"], ["conversation_sessions.session_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_satori_inclination_evidence"),
        sa.UniqueConstraint(
            "inclination_id",
            "reflection_source_id",
            name="uq_satori_inclination_evidence_inclination_reflection_source",
        ),
        sa.UniqueConstraint(
            "inclination_id",
            "affective_transition_id",
            name="uq_satori_inclination_evidence_inclination_transition",
        ),
        sa.UniqueConstraint(
            "inclination_id",
            "source_message_id",
            name="uq_satori_inclination_evidence_inclination_message",
        ),
        sa.UniqueConstraint(
            "inclination_id",
            "source_interaction_id",
            name="uq_satori_inclination_evidence_inclination_interaction",
        ),
        sa.UniqueConstraint(
            "inclination_id",
            "content_signature",
            name="uq_satori_inclination_evidence_inclination_signature",
        ),
    )
    op.create_index(
        "ix_satori_inclination_evidence_inclination_observed",
        "satori_inclination_evidence",
        ["inclination_id", "observed_at"],
    )

    op.create_table(
        "satori_inclination_revisions",
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("inclination_id", sa.String(128), nullable=False),
        sa.Column("inclination_version", sa.Integer(), nullable=False),
        sa.Column("reflection_outcome_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("prior_score", sa.Float(), nullable=True),
        sa.Column("new_score", sa.Float(), nullable=False),
        sa.Column("applied_delta", sa.Float(), nullable=False),
        sa.Column("prior_confidence", sa.Float(), nullable=True),
        sa.Column("new_confidence", sa.Float(), nullable=False),
        sa.Column("prior_stability", sa.Float(), nullable=True),
        sa.Column("new_stability", sa.Float(), nullable=False),
        sa.Column("state_as_of", UTCDateTime(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("occurred_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "inclination_version >= 1",
            name="ck_satori_inclination_revisions_inclination_version_positive",
        ),
        sa.CheckConstraint(
            "kind IN ('created', 'strengthened', 'weakened')",
            name="ck_satori_inclination_revisions_kind_valid",
        ),
        sa.CheckConstraint(
            "prior_score IS NULL OR (prior_score >= -1.0 AND prior_score <= 1.0)",
            name="ck_satori_inclination_revisions_prior_score_valid",
        ),
        sa.CheckConstraint(
            "new_score >= -1.0 AND new_score <= 1.0",
            name="ck_satori_inclination_revisions_new_score_valid",
        ),
        sa.CheckConstraint(
            "applied_delta >= -1.0 AND applied_delta <= 1.0",
            name="ck_satori_inclination_revisions_applied_delta_valid",
        ),
        sa.CheckConstraint(
            "prior_confidence IS NULL OR (prior_confidence >= 0.0 AND prior_confidence <= 1.0)",
            name="ck_satori_inclination_revisions_prior_confidence_valid",
        ),
        sa.CheckConstraint(
            "new_confidence >= 0.0 AND new_confidence <= 1.0",
            name="ck_satori_inclination_revisions_new_confidence_valid",
        ),
        sa.CheckConstraint(
            "prior_stability IS NULL OR (prior_stability >= 0.0 AND prior_stability <= 1.0)",
            name="ck_satori_inclination_revisions_prior_stability_valid",
        ),
        sa.CheckConstraint(
            "new_stability >= 0.0 AND new_stability <= 1.0",
            name="ck_satori_inclination_revisions_new_stability_valid",
        ),
        sa.CheckConstraint(
            "(kind = 'created' AND inclination_version = 1 AND prior_score IS NULL "
            "AND prior_confidence IS NULL AND prior_stability IS NULL) OR "
            "(kind != 'created' AND inclination_version > 1 AND prior_score IS NOT NULL "
            "AND prior_confidence IS NOT NULL AND prior_stability IS NOT NULL)",
            name="ck_satori_inclination_revisions_prior_state_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["inclination_id"], ["satori_inclinations.inclination_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reflection_outcome_id"], ["reflection_outcomes.outcome_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("revision_id", name="pk_satori_inclination_revisions"),
        sa.UniqueConstraint(
            "inclination_id",
            "inclination_version",
            name="uq_satori_inclination_revisions_inclination_version",
        ),
        sa.UniqueConstraint(
            "reflection_outcome_id",
            name="uq_satori_inclination_revisions_reflection_outcome_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("satori_inclination_revisions")
    op.drop_index(
        "ix_satori_inclination_evidence_inclination_observed",
        table_name="satori_inclination_evidence",
    )
    op.drop_table("satori_inclination_evidence")
    op.drop_index("ix_satori_inclinations_identity_kind", table_name="satori_inclinations")
    op.drop_table("satori_inclinations")

    with op.batch_alter_table("reflection_proposals") as batch_op:
        batch_op.drop_constraint("ck_reflection_proposals_target_owner_valid", type_="check")
        batch_op.create_check_constraint(
            "ck_reflection_proposals_target_owner_valid",
            "target_owner IN ('satori_positions', 'personality', 'values')",
        )

    with op.batch_alter_table("reflection_sources") as batch_op:
        batch_op.drop_constraint(
            "ck_reflection_sources_affective_attachment_all_or_none", type_="check"
        )
        batch_op.drop_constraint(
            "ck_reflection_sources_affective_state_version_positive", type_="check"
        )
        batch_op.drop_constraint(
            "fk_reflection_sources_affective_transition_id_affective_transitions",
            type_="foreignkey",
        )
        batch_op.drop_column("affective_signal_hash")
        batch_op.drop_column("affective_state_version")
        batch_op.drop_column("affective_transition_id")

    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.drop_constraint(
            "ck_conversation_interactions_inclination_curiosity_influence_valid",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_conversation_interactions_inclination_context_schema_version_positive",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_conversation_interactions_inclination_context_status_valid",
            type_="check",
        )
        batch_op.drop_column("inclination_curiosity_influence")
        batch_op.drop_column("inclination_context_ids")
        batch_op.drop_column("inclination_context_schema_version")
        batch_op.drop_column("inclination_context_status")
