"""Versioned knowledge actions share the immutable existing approved-body stream."""

from alembic import op

revision = "policy_0005"
down_revision = "policy_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A new CHECK needs a table replacement; copy every old value without reserialization.
    op.execute(
        "CREATE TABLE policy_body_actions_next (request_id TEXT PRIMARY KEY, "
        "action_id TEXT NOT NULL UNIQUE, identity_id TEXT NOT NULL, lineage_id TEXT NOT NULL, "
        "counterparty_id TEXT NOT NULL, session_id TEXT NOT NULL, interaction_id TEXT NOT NULL, "
        "message_id TEXT NOT NULL, intent_request_id TEXT NOT NULL, "
        "expected_source_generation INTEGER NOT NULL CHECK(expected_source_generation>=0), "
        "expected_erasure_revision INTEGER NOT NULL CHECK(expected_erasure_revision>=0), "
        "expected_body_revision INTEGER NOT NULL CHECK(expected_body_revision>=0), "
        "result_body_revision INTEGER NOT NULL "
        "CHECK(result_body_revision=expected_body_revision+1), "
        "approval_digest TEXT NOT NULL CHECK(length(approval_digest)=64), "
        "store_digest TEXT NOT NULL CHECK(length(store_digest)=64), "
        "schema_digest TEXT NOT NULL CHECK(length(schema_digest)=64), "
        "targets TEXT NOT NULL CHECK(length(targets)>0 AND length(targets)<=131072), "
        "policy_version TEXT NOT NULL CHECK(policy_version IN "
        "('episode_body_erasure_v1','dialogue_body_erasure_v1','knowledge_body_erasure_v1')), "
        "decided_at TEXT NOT NULL, trace_id TEXT NOT NULL, "
        "UNIQUE(identity_id,result_body_revision), "
        "FOREIGN KEY(identity_id,message_id) REFERENCES policy_exclusions(identity_id,message_id), "
        "FOREIGN KEY(intent_request_id) REFERENCES policy_erasure_intents(request_id))"
    )
    op.execute("INSERT INTO policy_body_actions_next SELECT * FROM policy_body_actions")
    op.execute("DROP TABLE policy_body_actions")
    op.execute("ALTER TABLE policy_body_actions_next RENAME TO policy_body_actions")


def downgrade() -> None:
    raise RuntimeError("policy_floor_downgrade_forbidden")
