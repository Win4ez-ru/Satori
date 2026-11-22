"""Independent immutable erasure intent; exclusion epochs stay unchanged."""

from alembic import op

revision = "policy_0002"
down_revision = "policy_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE policy_lineages ADD COLUMN erasure_revision "
        "INTEGER NOT NULL DEFAULT 0 CHECK(erasure_revision>=0)"
    )
    op.execute(
        "CREATE TABLE policy_erasure_intents (request_id TEXT PRIMARY KEY, "
        "decision_id TEXT NOT NULL UNIQUE, identity_id TEXT NOT NULL, lineage_id TEXT NOT NULL, "
        "counterparty_id TEXT NOT NULL, session_id TEXT NOT NULL, interaction_id TEXT NOT NULL, "
        "message_id TEXT NOT NULL, expected_source_generation "
        "INTEGER NOT NULL CHECK(expected_source_generation>=0), "
        "expected_erasure_revision INTEGER NOT NULL CHECK(expected_erasure_revision>=0), "
        "result_erasure_revision INTEGER NOT NULL "
        "CHECK(result_erasure_revision=expected_erasure_revision+1), "
        "reviewed_preview_digest TEXT NOT NULL CHECK(length(reviewed_preview_digest)=64), "
        "approval_digest TEXT NOT NULL CHECK(length(approval_digest)=64), "
        "schema_revision TEXT NOT NULL, "
        "schema_digest TEXT NOT NULL CHECK(length(schema_digest)=64), "
        "store_digest TEXT NOT NULL CHECK(length(store_digest)=64), "
        "routing_digest TEXT NOT NULL CHECK(length(routing_digest)=64), "
        "assessment_status TEXT NOT NULL CHECK(assessment_status IN ('complete','blocked')), "
        "retention_policy TEXT NOT NULL "
        "CHECK(retention_policy='retain_numeric_and_structural_history_v1'), "
        "decided_at TEXT NOT NULL, trace_id TEXT NOT NULL, "
        "UNIQUE(identity_id,message_id), UNIQUE(identity_id,result_erasure_revision), "
        "FOREIGN KEY(identity_id,message_id) REFERENCES policy_exclusions(identity_id,message_id))"
    )


def downgrade() -> None:
    raise RuntimeError("policy_floor_downgrade_forbidden")
