"""Independent installation floor, lineages, monotonic exclusions and durable receipts."""

from alembic import op

revision = "policy_0001"
down_revision = None
branch_labels = None
depends_on = None

_DDL = (
    "CREATE TABLE policy_installation (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
    "installation_id TEXT NOT NULL, floor_id TEXT NOT NULL, policy_version TEXT NOT NULL)",
    "CREATE TABLE policy_lineages (identity_id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL UNIQUE, "
    "generation INTEGER NOT NULL CHECK(generation>=0), enrolled_at TEXT NOT NULL)",
    "CREATE TABLE policy_exclusions (message_id TEXT NOT NULL, identity_id TEXT NOT NULL, "
    "counterparty_id TEXT NOT NULL, session_id TEXT NOT NULL, interaction_id TEXT NOT NULL, "
    "generation INTEGER NOT NULL CHECK(generation>0), request_id TEXT NOT NULL, "
    "PRIMARY KEY(identity_id,message_id), "
    "FOREIGN KEY(identity_id) REFERENCES policy_lineages(identity_id))",
    "CREATE TABLE policy_receipts (request_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL UNIQUE, "
    "identity_id TEXT NOT NULL, lineage_id TEXT NOT NULL, counterparty_id TEXT NOT NULL, "
    "session_id TEXT NOT NULL, interaction_id TEXT NOT NULL, message_id TEXT NOT NULL, "
    "expected_generation INTEGER NOT NULL CHECK(expected_generation>=0), "
    "result_generation INTEGER NOT NULL CHECK(result_generation>=expected_generation), "
    "preview_digest TEXT NOT NULL CHECK(length(preview_digest)=64), decided_at TEXT NOT NULL, "
    "trace_id TEXT NOT NULL, phase TEXT NOT NULL "
    "CHECK(phase IN ('exclusion_committed_repair_pending','complete')), "
    "FOREIGN KEY(identity_id,message_id) REFERENCES policy_exclusions(identity_id,message_id))",
    "CREATE INDEX ix_policy_exclusions_identity_generation "
    "ON policy_exclusions(identity_id,generation)",
)


def upgrade() -> None:
    for statement in _DDL:
        op.execute(statement)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.exec_driver_sql("SELECT count(*) FROM policy_lineages").scalar_one():
        raise RuntimeError("policy_floor_downgrade_forbidden")
    for table in ("policy_receipts", "policy_exclusions", "policy_lineages", "policy_installation"):
        op.drop_table(table)
