"""Add bounded emotion, slower mood, and source-linked transition history.

Revision ID: 0006_affective_state
Revises: 0005_semantic_memory
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0006_affective_state"
down_revision: str | None = "0005_semantic_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only Stage 7 schema; no provider call or emotional simulation occurs."""

    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.add_column(sa.Column("emotion_appraisal_status", sa.String(64), nullable=True))
        batch_op.add_column(
            sa.Column("emotion_context_schema_version", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("emotion_state_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("mood_state_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("emotion_state_as_of", UTCDateTime(), nullable=True))

    op.create_table(
        "affective_states",
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("mood_version", sa.Integer(), nullable=False),
        sa.Column("as_of", UTCDateTime(), nullable=False),
        sa.Column("emotion_policy_version", sa.Integer(), nullable=False),
        sa.Column("appraisal_schema_version", sa.Integer(), nullable=False),
        sa.Column("mood_policy_version", sa.Integer(), nullable=False),
        sa.Column("valence", sa.Float(), nullable=False),
        sa.Column("arousal", sa.Float(), nullable=False),
        sa.Column("tension", sa.Float(), nullable=False),
        sa.Column("curiosity", sa.Float(), nullable=False),
        sa.Column("interest", sa.Float(), nullable=False),
        sa.Column("amusement", sa.Float(), nullable=False),
        sa.Column("concern", sa.Float(), nullable=False),
        sa.Column("frustration", sa.Float(), nullable=False),
        sa.Column("situational_confidence", sa.Float(), nullable=False),
        sa.Column("mood_valence", sa.Float(), nullable=False),
        sa.Column("mood_energy", sa.Float(), nullable=False),
        sa.Column("mood_tension", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_affective_states_schema_version_positive"
        ),
        sa.CheckConstraint("state_version >= 1", name="ck_affective_states_state_version_positive"),
        sa.CheckConstraint("mood_version >= 1", name="ck_affective_states_mood_version_positive"),
        sa.CheckConstraint(
            "emotion_policy_version >= 1",
            name="ck_affective_states_emotion_policy_version_positive",
        ),
        sa.CheckConstraint(
            "appraisal_schema_version >= 1",
            name="ck_affective_states_appraisal_schema_version_positive",
        ),
        sa.CheckConstraint(
            "mood_policy_version >= 1",
            name="ck_affective_states_mood_policy_version_positive",
        ),
        sa.CheckConstraint(
            "valence >= -1.0 AND valence <= 1.0", name="ck_affective_states_valence_valid"
        ),
        *(
            sa.CheckConstraint(
                f"{column} >= 0.0 AND {column} <= 1.0",
                name=f"ck_affective_states_{column}_valid",
            )
            for column in (
                "arousal",
                "tension",
                "curiosity",
                "interest",
                "amusement",
                "concern",
                "frustration",
                "situational_confidence",
                "mood_energy",
                "mood_tension",
            )
        ),
        sa.CheckConstraint(
            "mood_valence >= -1.0 AND mood_valence <= 1.0",
            name="ck_affective_states_mood_valence_valid",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["satori_identities.identity_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("identity_id", name="pk_affective_states"),
    )

    op.create_table(
        "affective_transitions",
        sa.Column("transition_id", sa.String(128), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("interaction_id", sa.String(128), nullable=False),
        sa.Column("source_message_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("appraisal_schema_version", sa.Integer(), nullable=False),
        sa.Column("emotion_policy_version", sa.Integer(), nullable=False),
        sa.Column("mood_policy_version", sa.Integer(), nullable=False),
        sa.Column("base_state_version", sa.Integer(), nullable=False),
        sa.Column("resulting_state_version", sa.Integer(), nullable=False),
        sa.Column("base_mood_version", sa.Integer(), nullable=False),
        sa.Column("resulting_mood_version", sa.Integer(), nullable=False),
        sa.Column("appraised_at", UTCDateTime(), nullable=False),
        sa.Column("committed_at", UTCDateTime(), nullable=False),
        sa.Column("appraisal_confidence", sa.Float(), nullable=False),
        sa.Column("appraisal_payload", sa.JSON(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("applied_delta", sa.JSON(), nullable=False),
        sa.Column("mood_delta", sa.JSON(), nullable=False),
        sa.Column("state_before", sa.JSON(), nullable=False),
        sa.Column("state_after", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("appraisal_method", sa.String(128), nullable=False),
        sa.CheckConstraint(
            "appraisal_schema_version >= 1",
            name="ck_affective_transitions_appraisal_schema_version_positive",
        ),
        sa.CheckConstraint(
            "emotion_policy_version >= 1",
            name="ck_affective_transitions_emotion_policy_version_positive",
        ),
        sa.CheckConstraint(
            "mood_policy_version >= 1",
            name="ck_affective_transitions_mood_policy_version_positive",
        ),
        sa.CheckConstraint(
            "base_state_version >= 1",
            name="ck_affective_transitions_base_state_version_positive",
        ),
        sa.CheckConstraint(
            "resulting_state_version = base_state_version + 1",
            name="ck_affective_transitions_state_version_increments_once",
        ),
        sa.CheckConstraint(
            "base_mood_version >= 1",
            name="ck_affective_transitions_base_mood_version_positive",
        ),
        sa.CheckConstraint(
            "resulting_mood_version = base_mood_version + 1",
            name="ck_affective_transitions_mood_version_increments_once",
        ),
        sa.CheckConstraint(
            "appraisal_confidence >= 0.0 AND appraisal_confidence <= 1.0",
            name="ck_affective_transitions_appraisal_confidence_valid",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["satori_identities.identity_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id"],
            ["conversation_interactions.interaction_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["conversation_messages.message_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("transition_id", name="pk_affective_transitions"),
        sa.UniqueConstraint("interaction_id", name="uq_affective_transitions_interaction_id"),
    )
    op.create_index(
        "ix_affective_transitions_committed_at",
        "affective_transitions",
        ["committed_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove Stage 7 state while preserving all accepted Stage 0-6 data."""

    op.drop_index("ix_affective_transitions_committed_at", table_name="affective_transitions")
    op.drop_table("affective_transitions")
    op.drop_table("affective_states")
    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.drop_column("emotion_state_as_of")
        batch_op.drop_column("mood_state_version")
        batch_op.drop_column("emotion_state_version")
        batch_op.drop_column("emotion_context_schema_version")
        batch_op.drop_column("emotion_appraisal_status")
