"""Prove legacy memory partitions without assigning the anonymous local default.

Revision ID: 0014_memory_scope
Revises: 0013_conversation_failure_reason
Create Date: 2026-10-03
"""

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

from satori.infrastructure.persistence.types import UTCDateTime

revision: str = "0014_memory_scope"
down_revision: str | None = "0013_conversation_failure_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scoped_key(identity_id: str, counterparty_id: str, base_key: str) -> str:
    """Frozen V1 namespace codec, independent of later runtime implementations."""

    material = json.dumps([identity_id, counterparty_id, base_key], separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _provable_claim_scopes() -> dict[str, tuple[str, str]]:
    bind = op.get_bind()
    memory_scopes: dict[str, tuple[str, str] | None] = {}
    rows = bind.execute(
        sa.text(
            "SELECT m.memory_id, m.source_interaction_id, m.lifecycle_status, "
            "e.evidence_id, e.provenance_kind, e.quote, msg.role, msg.content, "
            "msg.interaction_id, msg.session_id, i.interaction_id, i.session_id, i.status, "
            "s.identity_id, s.counterparty_id "
            "FROM episodic_memories m "
            "LEFT JOIN memory_evidence e ON e.memory_id=m.memory_id "
            "LEFT JOIN conversation_messages msg ON msg.message_id=e.source_message_id "
            "LEFT JOIN conversation_interactions i ON i.interaction_id=m.source_interaction_id "
            "LEFT JOIN conversation_sessions s ON s.session_id=i.session_id"
        )
    ).all()
    for row in rows:
        memory_id = str(row[0])
        valid = (
            row[2] == "active"
            and row[3] is not None
            and row[4] == "explicit_user_statement"
            and isinstance(row[5], str)
            and bool(row[5])
            and row[6] == "user"
            and isinstance(row[7], str)
            and row[5] in row[7]
            and row[1] == row[8] == row[10]
            and row[9] == row[11]
            and row[12] == "completed"
            and isinstance(row[13], str)
            and bool(row[13].strip())
            and isinstance(row[14], str)
            and bool(row[14].strip())
            and row[14] != "local-default"
        )
        scope = (str(row[13]), str(row[14])) if valid else None
        if memory_id not in memory_scopes:
            memory_scopes[memory_id] = scope
        elif memory_scopes[memory_id] != scope:
            memory_scopes[memory_id] = None
    claim_scopes: dict[str, tuple[str, str] | None] = {}
    rows = bind.execute(
        sa.text(
            "SELECT c.claim_id, ce.memory_id, ce.memory_evidence_id, ce.root_message_id, "
            "ce.root_interaction_id, e.evidence_id, e.memory_id, e.source_message_id, "
            "m.source_interaction_id FROM semantic_claims c "
            "LEFT JOIN semantic_claim_evidence ce ON ce.claim_id=c.claim_id "
            "LEFT JOIN memory_evidence e ON e.evidence_id=ce.memory_evidence_id "
            "LEFT JOIN episodic_memories m ON m.memory_id=ce.memory_id"
        )
    ).all()
    for row in rows:
        claim_id = str(row[0])
        scope = memory_scopes.get(str(row[1]))
        if (
            row[1] is None
            or row[2] != row[5]
            or row[1] != row[6]
            or row[3] != row[7]
            or row[4] != row[8]
        ):
            scope = None
        if claim_id not in claim_scopes:
            claim_scopes[claim_id] = scope
        elif claim_scopes[claim_id] != scope:
            claim_scopes[claim_id] = None
    return {claim_id: scope for claim_id, scope in claim_scopes.items() if scope is not None}


def _audit(rekeyed: int, quarantined: int) -> None:
    audit = sa.table(
        "audit_events",
        sa.column("event_id", sa.String(128)),
        sa.column("schema_version", sa.Integer()),
        sa.column("event_type", sa.String(128)),
        sa.column("aggregate_type", sa.String(128)),
        sa.column("aggregate_id", sa.String(128)),
        sa.column("occurred_at", UTCDateTime()),
        sa.column("trace_id", sa.String(128)),
        sa.column("details", sa.JSON()),
    )
    op.get_bind().execute(
        audit.insert().values(
            event_id=f"migration-memory-scope-{uuid4()}",
            schema_version=1,
            event_type="memory_scope_migrated",
            aggregate_type="storage_migration",
            aggregate_id=revision,
            occurred_at=datetime.now(UTC),
            trace_id=revision,
            details={
                "migration_revision": revision,
                "policy_version": 1,
                "rekeyed_claim_count": rekeyed,
                "quarantined_claim_count": quarantined,
            },
        )
    )


def upgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("0014_memory_scope requires online canonical provenance validation")
    bind = op.get_bind()
    scopes = _provable_claim_scopes()
    claims = bind.execute(sa.text("SELECT claim_id, claim_key, status FROM semantic_claims")).all()
    plan = {
        str(claim_id): _scoped_key(*scopes[str(claim_id)], str(base_key))
        for claim_id, base_key, _status in claims
        if str(claim_id) in scopes
    }
    active_keys = [
        plan.get(str(claim_id), str(key)) for claim_id, key, status in claims if status == "active"
    ]
    if len(active_keys) != len(set(active_keys)):
        raise RuntimeError("memory scope migration would collide active claim identities")
    with op.batch_alter_table("conversation_sessions") as batch:
        batch.add_column(
            sa.Column("scope_verified", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
        batch.create_check_constraint("scope_verified_valid", "scope_verified IN (0, 1)")
    bind.execute(
        sa.text(
            "UPDATE conversation_sessions SET scope_verified=1 "
            "WHERE counterparty_id != 'local-default' AND length(trim(counterparty_id))>0 "
            "AND length(trim(identity_id))>0"
        )
    )
    # Temporary keys make updates order-independent even for adversarial old keys.
    for claim_id in plan:
        bind.execute(
            sa.text("UPDATE semantic_claims SET claim_key=:key WHERE claim_id=:claim_id"),
            {"key": f"scope-migration-{uuid4().hex}", "claim_id": claim_id},
        )
    for claim_id, key in plan.items():
        bind.execute(
            sa.text("UPDATE semantic_claims SET claim_key=:key WHERE claim_id=:claim_id"),
            {"key": key, "claim_id": claim_id},
        )
    if claims or bind.execute(sa.text("SELECT 1 FROM conversation_sessions LIMIT 1")).first():
        _audit(len(plan), len(claims) - len(plan))


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("0014_memory_scope requires online downgrade validation")
    bind = op.get_bind()
    claims = bind.execute(
        sa.text(
            "SELECT claim_id, claim_kind, normalization_version, polarity, predicate, "
            "subject, normalized_value, value_kind, status FROM semantic_claims"
        )
    ).all()
    keys: dict[str, str] = {}
    active_keys: dict[str, list[str]] = defaultdict(list)
    for row in claims:
        material = {
            "claim_kind": row[1],
            "normalization_version": row[2],
            "polarity": bool(row[3]),
            "predicate": row[4],
            "subject": row[5],
            "value": row[6],
            "value_kind": row[7],
        }
        key = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        keys[str(row[0])] = key
        if row[8] == "active":
            active_keys[key].append(str(row[0]))
    if any(len(claim_ids) > 1 for claim_ids in active_keys.values()):
        raise RuntimeError("memory scope downgrade would collapse distinct active partitions")
    for claim_id in keys:
        bind.execute(
            sa.text("UPDATE semantic_claims SET claim_key=:key WHERE claim_id=:claim_id"),
            {"key": f"scope-downgrade-{uuid4().hex}", "claim_id": claim_id},
        )
    for claim_id, key in keys.items():
        bind.execute(
            sa.text("UPDATE semantic_claims SET claim_key=:key WHERE claim_id=:claim_id"),
            {"key": key, "claim_id": claim_id},
        )
    with op.batch_alter_table("conversation_sessions") as batch:
        batch.drop_constraint(op.f("ck_conversation_sessions_scope_verified_valid"), type_="check")
        batch.drop_column("scope_verified")
