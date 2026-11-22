"""Add append-only owner preparation projection; retain every existing content body.

Revision ID: 0017_erasure_intent
Revises: 0016_source_policy
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_erasure_intent"
down_revision: str | None = "0016_source_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "erasure_policy_bindings",
        sa.Column("identity_id", sa.String(128), primary_key=True),
        sa.Column("floor_id", sa.String(128), nullable=False),
        sa.Column("lineage_id", sa.String(128), nullable=False),
        sa.Column("erasure_revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("erasure_revision >= 1", name="revision_positive"),
    )
    foreign = {
        "identity_id": "satori_identities.identity_id",
        "root_message_id": "conversation_messages.message_id",
        "root_interaction_id": "conversation_interactions.interaction_id",
        "root_session_id": "conversation_sessions.session_id",
    }
    # Keep the exact column order of the frozen 0017 persistence contract.
    fields = (
        ("request_id", "string128"),
        ("preparation_revision", "integer"),
        ("owner", "string64"),
        ("decision_id", "string128"),
        ("identity_id", "string128"),
        ("counterparty_id", "string128"),
        ("root_message_id", "string128"),
        ("root_interaction_id", "string128"),
        ("root_session_id", "string128"),
        ("floor_id", "string128"),
        ("installation_id", "string128"),
        ("lineage_id", "string128"),
        ("policy_version", "string128"),
        ("retention_policy", "string128"),
        ("expected_source_generation", "integer"),
        ("expected_erasure_revision", "integer"),
        ("result_erasure_revision", "integer"),
        ("source_generation", "integer"),
        ("reviewed_preview_digest", "string64"),
        ("approval_digest", "string64"),
        ("intent_schema_revision", "string128"),
        ("intent_schema_digest", "string64"),
        ("intent_store_digest", "string64"),
        ("intent_routing_digest", "string64"),
        ("intent_assessment_status", "string16"),
        ("decided_at", "datetime"),
        ("trace_id", "string128"),
        ("contract_version", "integer"),
        ("schema_revision", "string128"),
        ("schema_digest", "string64"),
        ("store_digest", "string64"),
        ("routing_digest", "string64"),
        ("retention_digest", "string64"),
        ("preparation_digest", "string64"),
        ("assessment_status", "string16"),
        ("body_readiness", "string32"),
        ("reason_codes", "json"),
        ("counts", "json"),
        ("prepared_at", "datetime"),
    )
    columns: list[sa.Column[object]] = []
    for name, kind in fields:
        column_type: sa.String | sa.Integer | sa.DateTime | sa.JSON
        if kind.startswith("string"):
            column_type = sa.String(int(kind.removeprefix("string")))
        elif kind == "integer":
            column_type = sa.Integer()
        elif kind == "datetime":
            column_type = sa.DateTime(timezone=True)
        else:
            column_type = sa.JSON()
        if name in foreign:
            column = sa.Column(
                name, column_type, sa.ForeignKey(foreign[name], ondelete="RESTRICT"), nullable=False
            )
        else:
            column = sa.Column(
                name,
                column_type,
                primary_key=name in {"request_id", "preparation_revision", "owner"},
                nullable=False,
            )
        columns.append(column)
    op.create_table(
        "erasure_preparations",
        *columns,
        sa.CheckConstraint("preparation_revision >= 1", name="preparation_revision_positive"),
        sa.CheckConstraint("contract_version = 1", name="contract_version_valid"),
        sa.CheckConstraint(
            "expected_erasure_revision >= 0 AND result_erasure_revision = "
            "expected_erasure_revision + 1",
            name="erasure_transition_valid",
        ),
        sa.CheckConstraint(
            "source_generation >= expected_source_generation AND expected_source_generation >= 0",
            name="source_generation_valid",
        ),
        sa.CheckConstraint(
            "assessment_status IN ('complete','blocked') AND intent_assessment_status IN "
            "('complete','blocked')",
            name="assessment_valid",
        ),
        sa.CheckConstraint(
            "body_readiness IN "
            "('retain_only','blocked_executor','blocked_owner_policy',"
            "'blocked_provenance')",
            name="body_readiness_valid",
        ),
        sa.CheckConstraint(
            "owner IN "
            "('interaction_log','memory_manager','retrieval_index','semantic_memory',"
            "'current_models','positions','reflection','affect','relationship',"
            "'personality','audit','initial_self','initial_values','source_policy',"
            "'schema_registry')",
            name="owner_valid",
        ),
    )


def downgrade() -> None:
    for table in ("erasure_preparations", "erasure_policy_bindings"):
        if op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one():
            raise RuntimeError("erasure_intent_downgrade_would_lose_preparation_history")
    op.drop_table("erasure_preparations")
    op.drop_table("erasure_policy_bindings")
