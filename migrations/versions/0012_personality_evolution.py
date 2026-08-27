"""Add bounded Stage 14 personality evolution history and checkpoints.

Revision ID: 0012_personality_evolution
Revises: 0011_satori_inclinations
Create Date: 2026-08-23
"""

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0012_personality_evolution"
down_revision: str | None = "0011_satori_inclinations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECKPOINT_HASH_SCHEMA = "satori.personality-checkpoint.v1"
_CHECKPOINT_HASH_SCHEMA_VERSION = 1
_CANONICAL_V1_TRAIT_KEYS = (
    "analytical_thinking",
    "assertiveness",
    "curiosity",
    "emotional_sensitivity",
    "empathy",
    "humor",
    "impulsivity",
    "independence",
    "irony",
    "openness",
    "optimism",
    "patience",
    "playfulness",
    "self_confidence",
    "warmth",
)


@dataclass(frozen=True, slots=True)
class _ActivationSnapshot:
    identity_id: str
    personality_schema_version: int
    source_aggregate_version: int
    created_at: datetime
    traits: list[dict[str, str | float]]


def _checkpoint_hash(
    *,
    identity_id: str,
    personality_schema_version: int,
    source_aggregate_version: int,
    checkpoint_kind: str,
    traits: list[dict[str, str | float]],
) -> str:
    payload = {
        "checkpoint_kind": checkpoint_kind,
        "hash_schema": _CHECKPOINT_HASH_SCHEMA,
        "identity_id": identity_id,
        "personality_schema_version": personality_schema_version,
        "source_aggregate_version": source_aggregate_version,
        "traits": [
            {
                "key": str(item["key"]),
                "value": round(float(item["value"]), 6),
                "baseline_value": round(float(item["baseline_value"]), 6),
            }
            for item in traits
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _load_activation_snapshots() -> list[_ActivationSnapshot]:
    """Validate and load existing state before any non-transactional SQLite DDL."""

    if op.get_context().as_sql:
        raise RuntimeError(
            "0012_personality_evolution requires an online migration to hash existing state"
        )

    bind = op.get_bind()
    state_source = sa.table(
        "satori_personality_states",
        sa.column("identity_id", sa.String(128)),
        sa.column("schema_version", sa.Integer()),
        sa.column("aggregate_version", sa.Integer()),
        sa.column("created_at", UTCDateTime()),
    )
    trait_source = sa.table(
        "satori_personality_traits",
        sa.column("identity_id", sa.String(128)),
        sa.column("trait_key", sa.String(64)),
        sa.column("value", sa.Float()),
        sa.column("baseline_value", sa.Float()),
    )
    states = (
        bind.execute(
            sa.select(
                state_source.c.identity_id,
                state_source.c.schema_version,
                state_source.c.aggregate_version,
                state_source.c.created_at,
            ).order_by(state_source.c.identity_id)
        )
        .mappings()
        .all()
    )
    traits_by_identity: dict[str, list[dict[str, str | float]]] = defaultdict(list)
    trait_rows = (
        bind.execute(
            sa.select(
                trait_source.c.identity_id,
                trait_source.c.trait_key,
                trait_source.c.value,
                trait_source.c.baseline_value,
            ).order_by(trait_source.c.identity_id, trait_source.c.trait_key)
        )
        .mappings()
        .all()
    )
    for row in trait_rows:
        traits_by_identity[str(row["identity_id"])].append(
            {
                "key": str(row["trait_key"]),
                "value": float(row["value"]),
                "baseline_value": float(row["baseline_value"]),
            }
        )

    state_identity_ids: set[str] = set()
    snapshots: list[_ActivationSnapshot] = []
    for state in states:
        identity_id = str(state["identity_id"])
        state_identity_ids.add(identity_id)
        traits = traits_by_identity.get(identity_id, [])
        trait_keys = tuple(str(item["key"]) for item in traits)
        if trait_keys != _CANONICAL_V1_TRAIT_KEYS:
            raise RuntimeError(
                "cannot create a complete activation checkpoint for personality "
                f"{identity_id!r}: canonical trait vector is incomplete or invalid"
            )
        aggregate_version = int(state["aggregate_version"])
        if aggregate_version != 1:
            raise RuntimeError(
                "cannot create an activation checkpoint for personality "
                f"{identity_id!r}: pre-Stage-14 aggregate version is not 1; "
                "export/recovery is required"
            )
        if any(float(item["value"]) != float(item["baseline_value"]) for item in traits):
            raise RuntimeError(
                "cannot create an activation checkpoint for personality "
                f"{identity_id!r}: pre-Stage-14 current traits differ from their "
                "activation baselines; export/recovery is required"
            )
        created_at = state["created_at"]
        if not isinstance(created_at, datetime):
            raise RuntimeError("personality created_at could not be loaded as a datetime")
        snapshots.append(
            _ActivationSnapshot(
                identity_id=identity_id,
                personality_schema_version=int(state["schema_version"]),
                source_aggregate_version=aggregate_version,
                created_at=created_at,
                traits=traits,
            )
        )

    orphan_trait_identities = set(traits_by_identity) - state_identity_ids
    if orphan_trait_identities:
        raise RuntimeError("orphan personality traits prevent activation checkpoint backfill")
    return snapshots


def _backfill_activation_checkpoints(
    checkpoint_table: sa.Table,
    checkpoint_trait_table: sa.Table,
    snapshots: list[_ActivationSnapshot],
) -> None:
    """Persist prevalidated authoritative snapshots without mutating source rows."""

    bind = op.get_bind()
    for snapshot in snapshots:
        checkpoint_hash = _checkpoint_hash(
            identity_id=snapshot.identity_id,
            personality_schema_version=snapshot.personality_schema_version,
            source_aggregate_version=snapshot.source_aggregate_version,
            checkpoint_kind="activation",
            traits=snapshot.traits,
        )
        checkpoint_id = f"personality-checkpoint-{checkpoint_hash}"
        bind.execute(
            checkpoint_table.insert().values(
                checkpoint_id=checkpoint_id,
                identity_id=snapshot.identity_id,
                personality_schema_version=snapshot.personality_schema_version,
                source_aggregate_version=snapshot.source_aggregate_version,
                checkpoint_kind="activation",
                hash_schema_version=_CHECKPOINT_HASH_SCHEMA_VERSION,
                checkpoint_hash=checkpoint_hash,
                created_at=snapshot.created_at,
            )
        )
        bind.execute(
            checkpoint_trait_table.insert(),
            [
                {
                    "checkpoint_id": checkpoint_id,
                    "trait_key": item["key"],
                    "value": item["value"],
                    "baseline_value": item["baseline_value"],
                }
                for item in snapshot.traits
            ],
        )


def _assert_downgrade_safe() -> None:
    """Refuse to discard Stage 14 provenance after the owner has been used."""

    if op.get_context().as_sql:
        raise RuntimeError(
            "0012_personality_evolution downgrade requires an online provenance check"
        )

    bind = op.get_bind()
    guarded_queries = (
        (
            "Reflection V3/personality run",
            "SELECT 1 FROM reflection_runs "
            "WHERE schema_version >= 3 OR purpose = 'personality_evolution' LIMIT 1",
        ),
        ("personality evidence", "SELECT 1 FROM personality_evidence LIMIT 1"),
        ("personality revision", "SELECT 1 FROM personality_revisions LIMIT 1"),
        (
            "checkpoint approval",
            "SELECT 1 FROM personality_checkpoint_approvals LIMIT 1",
        ),
        ("personality restore event", "SELECT 1 FROM personality_restore_events LIMIT 1"),
        (
            "non-activation personality checkpoint",
            "SELECT 1 FROM personality_checkpoints WHERE checkpoint_kind != 'activation' LIMIT 1",
        ),
        (
            "personality owner audit",
            "SELECT 1 FROM audit_events "
            "WHERE aggregate_type = 'personality' "
            "OR event_type LIKE 'personality.%' "
            "OR event_type LIKE 'reflection.personality_%' LIMIT 1",
        ),
    )
    for record_kind, query in guarded_queries:
        if bind.execute(sa.text(query)).first() is not None:
            raise RuntimeError(
                "cannot downgrade 0012_personality_evolution after a "
                f"{record_kind}; export/recovery is required"
            )


def upgrade() -> None:
    """Create Stage 14 owner history and nullable Reflection/context provenance."""

    activation_snapshots = _load_activation_snapshots()

    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.add_column(sa.Column("personality_aggregate_version", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("personality_expression_schema_version", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("personality_expression_cues", sa.JSON(), nullable=True))
        batch_op.create_check_constraint(
            "ck_conversation_interactions_personality_aggregate_version_positive",
            "personality_aggregate_version IS NULL OR personality_aggregate_version >= 1",
        )
        batch_op.create_check_constraint(
            "ck_conversation_interactions_personality_expression_schema_version_positive",
            "personality_expression_schema_version IS NULL OR "
            "personality_expression_schema_version >= 1",
        )
        batch_op.create_check_constraint(
            "ck_conversation_interactions_personality_manifest_consistent",
            "((context_manifest_schema_version IS NULL OR context_manifest_schema_version < 16) "
            "AND personality_aggregate_version IS NULL "
            "AND personality_expression_schema_version IS NULL "
            "AND personality_expression_cues IS NULL) OR "
            "(context_manifest_schema_version >= 16 AND context_schema_version >= 16 "
            "AND personality_aggregate_version IS NOT NULL "
            "AND personality_expression_schema_version = 2 "
            "AND personality_expression_cues IS NOT NULL)",
        )

    with op.batch_alter_table("reflection_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "purpose",
                sa.String(32),
                nullable=False,
                server_default=sa.text("'general'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_reflection_runs_purpose_valid",
            "purpose IN ('general', 'personality_evolution')",
        )
        batch_op.create_check_constraint(
            "ck_reflection_runs_purpose_schema_consistent",
            "(purpose = 'general' AND schema_version IN (1, 2)) OR "
            "(purpose = 'personality_evolution' AND schema_version = 3)",
        )
    with op.batch_alter_table("reflection_runs") as batch_op:
        batch_op.alter_column(
            "purpose",
            existing_type=sa.String(32),
            nullable=False,
            server_default=None,
        )

    with op.batch_alter_table("reflection_sources") as batch_op:
        batch_op.add_column(sa.Column("upstream_lineage_kind", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("upstream_lineage_id", sa.String(128), nullable=True))
        batch_op.create_check_constraint(
            "ck_reflection_sources_upstream_lineage_all_or_none",
            "(upstream_lineage_kind IS NULL AND upstream_lineage_id IS NULL) OR "
            "(upstream_lineage_kind IS NOT NULL AND upstream_lineage_id IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            "ck_reflection_sources_upstream_lineage_kind_valid",
            "upstream_lineage_kind IS NULL OR "
            "upstream_lineage_kind IN ('position', 'episodic_memory')",
        )

    checkpoint_table = op.create_table(
        "personality_checkpoints",
        sa.Column("checkpoint_id", sa.String(128), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("personality_schema_version", sa.Integer(), nullable=False),
        sa.Column("source_aggregate_version", sa.Integer(), nullable=False),
        sa.Column("checkpoint_kind", sa.String(16), nullable=False),
        sa.Column("hash_schema_version", sa.Integer(), nullable=False),
        sa.Column("checkpoint_hash", sa.String(64), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "personality_schema_version >= 1",
            name="ck_personality_checkpoints_personality_schema_positive",
        ),
        sa.CheckConstraint(
            "source_aggregate_version >= 1",
            name="ck_personality_checkpoints_source_aggregate_positive",
        ),
        sa.CheckConstraint(
            "hash_schema_version >= 1",
            name="ck_personality_checkpoints_hash_schema_positive",
        ),
        sa.CheckConstraint(
            "checkpoint_kind IN ('activation', 'evolution', 'restore', 'manual')",
            name="ck_personality_checkpoints_checkpoint_kind_valid",
        ),
        sa.CheckConstraint(
            "length(checkpoint_hash) = 64",
            name="ck_personality_checkpoints_checkpoint_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["satori_personality_states.identity_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("checkpoint_id", name="pk_personality_checkpoints"),
        sa.UniqueConstraint(
            "identity_id",
            "source_aggregate_version",
            "checkpoint_kind",
            name="uq_personality_checkpoints_identity_version_kind",
        ),
        sa.UniqueConstraint(
            "identity_id",
            "checkpoint_hash",
            name="uq_personality_checkpoints_identity_hash",
        ),
    )
    op.create_index(
        "ix_personality_checkpoints_identity_version",
        "personality_checkpoints",
        ["identity_id", "source_aggregate_version"],
    )

    checkpoint_trait_table = op.create_table(
        "personality_checkpoint_traits",
        sa.Column("checkpoint_id", sa.String(128), nullable=False),
        sa.Column("trait_key", sa.String(64), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("baseline_value", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "length(trait_key) > 0",
            name="ck_personality_checkpoint_traits_trait_key_not_blank",
        ),
        sa.CheckConstraint(
            "value >= 0.0 AND value <= 1.0",
            name="ck_personality_checkpoint_traits_value_unit_interval",
        ),
        sa.CheckConstraint(
            "baseline_value >= 0.0 AND baseline_value <= 1.0",
            name="ck_personality_checkpoint_traits_baseline_value_unit_interval",
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["personality_checkpoints.checkpoint_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "checkpoint_id",
            "trait_key",
            name="pk_personality_checkpoint_traits",
        ),
    )

    op.create_table(
        "personality_revisions",
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("revision_kind", sa.String(16), nullable=False),
        sa.Column("before_aggregate_version", sa.Integer(), nullable=False),
        sa.Column("after_aggregate_version", sa.Integer(), nullable=False),
        sa.Column("trait_key", sa.String(64), nullable=True),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("before_value", sa.Float(), nullable=True),
        sa.Column("after_value", sa.Float(), nullable=True),
        sa.Column("applied_delta", sa.Float(), nullable=True),
        sa.Column("decision_confidence", sa.Float(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("source_checkpoint_id", sa.String(128), nullable=False),
        sa.Column("resulting_checkpoint_id", sa.String(128), nullable=False),
        sa.Column("reflection_outcome_id", sa.String(128), nullable=True),
        sa.Column("trait_diffs", sa.JSON(), nullable=False),
        sa.Column("activation_distance_linf", sa.Float(), nullable=False),
        sa.Column("activation_distance_l1", sa.Float(), nullable=False),
        sa.Column("approved_checkpoint_distance_linf", sa.Float(), nullable=False),
        sa.Column("approved_checkpoint_distance_l1", sa.Float(), nullable=False),
        sa.Column("rolling_trait_path", sa.Float(), nullable=True),
        sa.Column("rolling_total_path", sa.Float(), nullable=False),
        sa.Column("lifetime_trait_path", sa.Float(), nullable=True),
        sa.Column("lifetime_total_path", sa.Float(), nullable=False),
        sa.Column("occurred_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "revision_kind IN ('evolution', 'restore')",
            name="ck_personality_revisions_revision_kind_valid",
        ),
        sa.CheckConstraint(
            "before_aggregate_version >= 1",
            name="ck_personality_revisions_before_aggregate_positive",
        ),
        sa.CheckConstraint(
            "after_aggregate_version >= 2",
            name="ck_personality_revisions_after_aggregate_positive",
        ),
        sa.CheckConstraint(
            "after_aggregate_version = before_aggregate_version + 1",
            name="ck_personality_revisions_aggregate_versions_consecutive",
        ),
        sa.CheckConstraint(
            "source_checkpoint_id != resulting_checkpoint_id",
            name="ck_personality_revisions_checkpoint_lineage_distinct",
        ),
        sa.CheckConstraint(
            "policy_version >= 1",
            name="ck_personality_revisions_policy_version_positive",
        ),
        sa.CheckConstraint(
            "length(reason_code) > 0",
            name="ck_personality_revisions_reason_code_not_blank",
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('increase', 'decrease')",
            name="ck_personality_revisions_direction_valid",
        ),
        sa.CheckConstraint(
            "before_value IS NULL OR (before_value >= 0.0 AND before_value <= 1.0)",
            name="ck_personality_revisions_before_value_valid",
        ),
        sa.CheckConstraint(
            "after_value IS NULL OR (after_value >= 0.0 AND after_value <= 1.0)",
            name="ck_personality_revisions_after_value_valid",
        ),
        sa.CheckConstraint(
            "applied_delta IS NULL OR applied_delta IN (-0.005, 0.005)",
            name="ck_personality_revisions_applied_delta_exact",
        ),
        sa.CheckConstraint(
            "decision_confidence IS NULL OR "
            "(decision_confidence >= 0.0 AND decision_confidence <= 1.0)",
            name="ck_personality_revisions_decision_confidence_valid",
        ),
        sa.CheckConstraint(
            "activation_distance_linf >= 0.0 AND activation_distance_l1 >= 0.0 "
            "AND approved_checkpoint_distance_linf >= 0.0 "
            "AND approved_checkpoint_distance_l1 >= 0.0 "
            "AND rolling_total_path >= 0.0 AND lifetime_total_path >= 0.0",
            name="ck_personality_revisions_aggregate_metrics_non_negative",
        ),
        sa.CheckConstraint(
            "rolling_trait_path IS NULL OR rolling_trait_path >= 0.0",
            name="ck_personality_revisions_rolling_trait_path_non_negative",
        ),
        sa.CheckConstraint(
            "lifetime_trait_path IS NULL OR lifetime_trait_path >= 0.0",
            name="ck_personality_revisions_lifetime_trait_path_non_negative",
        ),
        sa.CheckConstraint(
            "(revision_kind = 'evolution' AND trait_key IS NOT NULL "
            "AND direction IS NOT NULL AND before_value IS NOT NULL "
            "AND after_value IS NOT NULL AND applied_delta IS NOT NULL "
            "AND decision_confidence IS NOT NULL AND rolling_trait_path IS NOT NULL "
            "AND lifetime_trait_path IS NOT NULL AND reflection_outcome_id IS NOT NULL) OR "
            "(revision_kind = 'restore' AND trait_key IS NULL AND direction IS NULL "
            "AND before_value IS NULL AND after_value IS NULL AND applied_delta IS NULL "
            "AND decision_confidence IS NULL AND rolling_trait_path IS NULL "
            "AND lifetime_trait_path IS NULL AND reflection_outcome_id IS NULL)",
            name="ck_personality_revisions_revision_shape_valid",
        ),
        sa.CheckConstraint(
            "(direction = 'increase' AND applied_delta = 0.005) OR "
            "(direction = 'decrease' AND applied_delta = -0.005) OR "
            "(direction IS NULL AND applied_delta IS NULL)",
            name="ck_personality_revisions_direction_delta_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["satori_personality_states.identity_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_checkpoint_id"],
            ["personality_checkpoints.checkpoint_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resulting_checkpoint_id"],
            ["personality_checkpoints.checkpoint_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reflection_outcome_id"],
            ["reflection_outcomes.outcome_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("revision_id", name="pk_personality_revisions"),
        sa.UniqueConstraint(
            "identity_id",
            "after_aggregate_version",
            name="uq_personality_revisions_identity_id",
        ),
        sa.UniqueConstraint(
            "reflection_outcome_id",
            name="uq_personality_revisions_reflection_outcome_id",
        ),
        sa.UniqueConstraint(
            "resulting_checkpoint_id",
            name="uq_personality_revisions_resulting_checkpoint_id",
        ),
    )
    op.create_index(
        "ix_personality_revisions_identity_occurred",
        "personality_revisions",
        ["identity_id", "occurred_at"],
    )

    op.create_table(
        "personality_evidence",
        sa.Column("evidence_id", sa.String(128), nullable=False),
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("trait_key", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("reflection_run_id", sa.String(128), nullable=False),
        sa.Column("reflection_proposal_id", sa.String(128), nullable=False),
        sa.Column("reflection_source_id", sa.String(128), nullable=False),
        sa.Column("evidence_edge_id", sa.String(128), nullable=False),
        sa.Column("evidence_edge_version", sa.Integer(), nullable=False),
        sa.Column("root_interaction_id", sa.String(128), nullable=False),
        sa.Column("root_message_id", sa.String(128), nullable=False),
        sa.Column("root_session_id", sa.String(128), nullable=False),
        sa.Column("root_counterparty_id", sa.String(128), nullable=False),
        sa.Column("upstream_lineage_kind", sa.String(32), nullable=False),
        sa.Column("upstream_lineage_id", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("normalized_signature", sa.String(64), nullable=False),
        sa.Column("citation_role", sa.String(16), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.Column("accepted_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "length(trait_key) > 0",
            name="ck_personality_evidence_trait_key_not_blank",
        ),
        sa.CheckConstraint(
            "direction IN ('increase', 'decrease')",
            name="ck_personality_evidence_direction_valid",
        ),
        sa.CheckConstraint(
            "citation_role IN ('support', 'counterevidence')",
            name="ck_personality_evidence_citation_role_valid",
        ),
        sa.CheckConstraint(
            "evidence_edge_version >= 1",
            name="ck_personality_evidence_evidence_edge_version_positive",
        ),
        sa.CheckConstraint(
            "upstream_lineage_kind IN ('position', 'episodic_memory')",
            name="ck_personality_evidence_upstream_lineage_kind_valid",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name="ck_personality_evidence_content_hash_length",
        ),
        sa.CheckConstraint(
            "length(normalized_signature) = 64",
            name="ck_personality_evidence_normalized_signature_length",
        ),
        sa.CheckConstraint(
            "accepted_at >= observed_at",
            name="ck_personality_evidence_accepted_after_observed",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["personality_revisions.revision_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["satori_personality_states.identity_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reflection_run_id"], ["reflection_runs.run_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reflection_proposal_id"],
            ["reflection_proposals.proposal_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reflection_source_id"],
            ["reflection_sources.source_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["root_interaction_id"],
            ["conversation_interactions.interaction_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["root_message_id"],
            ["conversation_messages.message_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["root_session_id"],
            ["conversation_sessions.session_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_personality_evidence"),
        sa.UniqueConstraint(
            "root_message_id",
            name="uq_personality_evidence_root_message_id",
        ),
        sa.UniqueConstraint(
            "reflection_source_id",
            name="uq_personality_evidence_reflection_source_id",
        ),
    )
    op.create_index(
        "ix_personality_evidence_identity_observed",
        "personality_evidence",
        ["identity_id", "observed_at"],
    )

    op.create_table(
        "personality_checkpoint_approvals",
        sa.Column("approval_id", sa.String(128), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("checkpoint_id", sa.String(128), nullable=False),
        sa.Column("checkpoint_hash", sa.String(64), nullable=False),
        sa.Column("expected_aggregate_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("approved_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "length(checkpoint_hash) = 64",
            name="ck_personality_checkpoint_approvals_checkpoint_hash_length",
        ),
        sa.CheckConstraint(
            "expected_aggregate_version >= 1",
            name="ck_personality_checkpoint_approvals_expected_aggregate_positive",
        ),
        sa.CheckConstraint(
            "length(reason) > 0",
            name="ck_personality_checkpoint_approvals_reason_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["satori_personality_states.identity_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["personality_checkpoints.checkpoint_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "approval_id",
            name="pk_personality_checkpoint_approvals",
        ),
        sa.UniqueConstraint(
            "checkpoint_id",
            name="uq_personality_checkpoint_approvals_checkpoint_id",
        ),
    )
    op.create_index(
        "ix_personality_checkpoint_approvals_identity_approved",
        "personality_checkpoint_approvals",
        ["identity_id", "approved_at"],
    )

    op.create_table(
        "personality_restore_events",
        sa.Column("restore_id", sa.String(128), nullable=False),
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("identity_id", sa.String(128), nullable=False),
        sa.Column("source_checkpoint_id", sa.String(128), nullable=False),
        sa.Column("source_checkpoint_hash", sa.String(64), nullable=False),
        sa.Column("resulting_checkpoint_id", sa.String(128), nullable=False),
        sa.Column("before_aggregate_version", sa.Integer(), nullable=False),
        sa.Column("after_aggregate_version", sa.Integer(), nullable=False),
        sa.Column("trait_diffs", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("restored_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "length(source_checkpoint_hash) = 64",
            name="ck_personality_restore_events_source_hash_length",
        ),
        sa.CheckConstraint(
            "before_aggregate_version >= 1",
            name="ck_personality_restore_events_before_aggregate_positive",
        ),
        sa.CheckConstraint(
            "after_aggregate_version >= 2",
            name="ck_personality_restore_events_after_aggregate_positive",
        ),
        sa.CheckConstraint(
            "after_aggregate_version = before_aggregate_version + 1",
            name="ck_personality_restore_events_aggregate_versions_consecutive",
        ),
        sa.CheckConstraint(
            "source_checkpoint_id != resulting_checkpoint_id",
            name="ck_personality_restore_events_checkpoint_lineage_distinct",
        ),
        sa.CheckConstraint(
            "length(reason) > 0",
            name="ck_personality_restore_events_reason_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["personality_revisions.revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["satori_personality_states.identity_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_checkpoint_id"],
            ["personality_checkpoints.checkpoint_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resulting_checkpoint_id"],
            ["personality_checkpoints.checkpoint_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("restore_id", name="pk_personality_restore_events"),
        sa.UniqueConstraint(
            "revision_id",
            name="uq_personality_restore_events_revision_id",
        ),
        sa.UniqueConstraint(
            "identity_id",
            "after_aggregate_version",
            name="uq_personality_restore_events_identity_id",
        ),
        sa.UniqueConstraint(
            "resulting_checkpoint_id",
            name="uq_personality_restore_events_resulting_checkpoint_id",
        ),
    )
    op.create_index(
        "ix_personality_restore_events_identity_restored",
        "personality_restore_events",
        ["identity_id", "restored_at"],
    )

    _backfill_activation_checkpoints(
        checkpoint_table,
        checkpoint_trait_table,
        activation_snapshots,
    )


def downgrade() -> None:
    """Remove unused Stage 14 schema, but never discard live owner provenance."""

    _assert_downgrade_safe()

    op.drop_index(
        "ix_personality_restore_events_identity_restored",
        table_name="personality_restore_events",
    )
    op.drop_table("personality_restore_events")
    op.drop_index(
        "ix_personality_checkpoint_approvals_identity_approved",
        table_name="personality_checkpoint_approvals",
    )
    op.drop_table("personality_checkpoint_approvals")
    op.drop_index(
        "ix_personality_evidence_identity_observed",
        table_name="personality_evidence",
    )
    op.drop_table("personality_evidence")
    op.drop_index(
        "ix_personality_revisions_identity_occurred",
        table_name="personality_revisions",
    )
    op.drop_table("personality_revisions")
    op.drop_table("personality_checkpoint_traits")
    op.drop_index(
        "ix_personality_checkpoints_identity_version",
        table_name="personality_checkpoints",
    )
    op.drop_table("personality_checkpoints")

    with op.batch_alter_table("reflection_sources") as batch_op:
        batch_op.drop_constraint(
            "ck_reflection_sources_upstream_lineage_kind_valid",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_reflection_sources_upstream_lineage_all_or_none",
            type_="check",
        )
        batch_op.drop_column("upstream_lineage_id")
        batch_op.drop_column("upstream_lineage_kind")

    with op.batch_alter_table("reflection_runs") as batch_op:
        batch_op.drop_constraint(
            "ck_reflection_runs_purpose_schema_consistent",
            type_="check",
        )
        batch_op.drop_constraint("ck_reflection_runs_purpose_valid", type_="check")
        batch_op.drop_column("purpose")

    with op.batch_alter_table("conversation_interactions") as batch_op:
        batch_op.drop_constraint(
            "ck_conversation_interactions_personality_manifest_consistent",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_conversation_interactions_personality_expression_schema_version_positive",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_conversation_interactions_personality_aggregate_version_positive",
            type_="check",
        )
        batch_op.drop_column("personality_expression_cues")
        batch_op.drop_column("personality_expression_schema_version")
        batch_op.drop_column("personality_aggregate_version")
