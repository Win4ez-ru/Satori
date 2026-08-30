"""Add privacy-safe foreground provider failure diagnostics.

Revision ID: 0013_conversation_failure_reason
Revises: 0012_personality_evolution
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_conversation_failure_reason"
down_revision: str | None = "0012_personality_evolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FAILURE_REASONS = (
    "transport_unavailable",
    "temporarily_unavailable",
    "rate_or_quota_limited",
    "credentials_rejected",
    "resource_not_found",
    "request_rejected",
    "output_token_limit",
    "incomplete_unknown",
    "generation_failed",
    "generation_cancelled",
    "response_refused",
    "response_too_large",
    "response_malformed",
    "missing_assistant_text",
    "usage_metadata_invalid",
    "visible_output_limit_exceeded",
    "response_character_limit_exceeded",
    "adapter_contract_violation",
)


def _quoted_failure_reasons() -> str:
    return ", ".join(f"'{value}'" for value in _FAILURE_REASONS)


def upgrade() -> None:
    """Persist only a closed reason plus already-safe provider/model identifiers."""

    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.drop_constraint(
            "ck_conversation_interactions_completion_metadata_consistent",
            type_="check",
        )
        batch_op.add_column(sa.Column("failure_reason", sa.String(length=64), nullable=True))
        batch_op.create_check_constraint(
            "ck_conversation_interactions_failure_reason_valid",
            f"failure_reason IS NULL OR failure_reason IN ({_quoted_failure_reasons()})",
        )
        batch_op.create_check_constraint(
            "ck_conversation_interactions_completion_metadata_consistent",
            "(status = 'completed' AND completed_at IS NOT NULL AND provider IS NOT NULL "
            "AND model IS NOT NULL AND finish_status IS NOT NULL "
            "AND context_schema_version IS NOT NULL "
            "AND context_manifest_schema_version IS NOT NULL "
            "AND policy_id IS NOT NULL AND policy_schema_version IS NOT NULL "
            "AND failure_kind IS NULL AND failure_reason IS NULL) OR "
            "(status = 'pending' AND completed_at IS NULL AND provider IS NULL "
            "AND model IS NULL AND finish_status IS NULL "
            "AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND context_schema_version IS NULL "
            "AND context_manifest_schema_version IS NULL "
            "AND policy_id IS NULL AND policy_schema_version IS NULL "
            "AND failure_kind IS NULL AND failure_reason IS NULL) OR "
            "(status = 'failed' AND completed_at IS NULL AND finish_status IS NULL "
            "AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND context_schema_version IS NULL "
            "AND context_manifest_schema_version IS NULL "
            "AND policy_id IS NULL AND policy_schema_version IS NULL "
            "AND failure_kind IS NOT NULL AND "
            "((failure_reason IS NULL AND provider IS NULL AND model IS NULL) OR "
            "(failure_reason IS NOT NULL AND provider IS NOT NULL AND model IS NOT NULL)))",
        )


def downgrade() -> None:
    """Discard only optional diagnostics before restoring the Stage 14 constraint."""

    op.get_bind().execute(
        sa.text(
            "UPDATE conversation_interactions "
            "SET failure_reason = NULL, provider = NULL, model = NULL "
            "WHERE status = 'failed'"
        )
    )
    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.drop_constraint(
            "ck_conversation_interactions_completion_metadata_consistent",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_conversation_interactions_failure_reason_valid",
            type_="check",
        )
        batch_op.drop_column("failure_reason")
        batch_op.create_check_constraint(
            "ck_conversation_interactions_completion_metadata_consistent",
            "(status = 'completed' AND completed_at IS NOT NULL AND provider IS NOT NULL "
            "AND model IS NOT NULL AND finish_status IS NOT NULL "
            "AND context_schema_version IS NOT NULL "
            "AND context_manifest_schema_version IS NOT NULL "
            "AND policy_id IS NOT NULL AND policy_schema_version IS NOT NULL "
            "AND failure_kind IS NULL) OR "
            "(status != 'completed' AND completed_at IS NULL AND provider IS NULL "
            "AND model IS NULL AND finish_status IS NULL "
            "AND context_schema_version IS NULL "
            "AND context_manifest_schema_version IS NULL "
            "AND policy_id IS NULL AND policy_schema_version IS NULL)",
        )
