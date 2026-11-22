"""Identity-owned reasons join the same immutable body stream without source attribution."""

from alembic import op

revision = "policy_0008"
down_revision = "policy_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE policy_body_actions_next (request_id TEXT PRIMARY KEY, "
        "action_id TEXT NOT NULL UNIQUE, identity_id TEXT NOT NULL, lineage_id TEXT NOT NULL, "
        "counterparty_id TEXT, session_id TEXT, interaction_id TEXT, message_id TEXT, "
        "intent_request_id TEXT, "
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
        "('episode_body_erasure_v1','dialogue_body_erasure_v1',"
        "'knowledge_body_erasure_v1','reflection_body_erasure_v1',"
        "'appraisal_explanation_erasure_v1','checkpoint_reason_erasure_v1')), "
        "decided_at TEXT NOT NULL, trace_id TEXT NOT NULL, "
        "CHECK((policy_version='checkpoint_reason_erasure_v1' AND counterparty_id IS NULL "
        "AND session_id IS NULL AND interaction_id IS NULL AND message_id IS NULL "
        "AND intent_request_id IS NULL) OR (policy_version!='checkpoint_reason_erasure_v1' "
        "AND counterparty_id IS NOT NULL AND session_id IS NOT NULL "
        "AND interaction_id IS NOT NULL AND message_id IS NOT NULL "
        "AND intent_request_id IS NOT NULL)), "
        "UNIQUE(identity_id,result_body_revision), "
        "FOREIGN KEY(identity_id,message_id) REFERENCES policy_exclusions(identity_id,message_id), "
        "FOREIGN KEY(intent_request_id) REFERENCES policy_erasure_intents(request_id))"
    )
    columns = (
        "request_id,action_id,identity_id,lineage_id,counterparty_id,session_id,interaction_id,"
        "message_id,intent_request_id,expected_source_generation,expected_erasure_revision,"
        "expected_body_revision,result_body_revision,approval_digest,store_digest,schema_digest,"
        "targets,policy_version,decided_at,trace_id"
    )
    op.execute(
        f"INSERT INTO policy_body_actions_next ({columns}) "
        f"SELECT {columns} FROM policy_body_actions"
    )
    op.execute("DROP TABLE policy_body_actions")
    op.execute("ALTER TABLE policy_body_actions_next RENAME TO policy_body_actions")


def downgrade() -> None:
    raise RuntimeError("policy_floor_downgrade_forbidden")
