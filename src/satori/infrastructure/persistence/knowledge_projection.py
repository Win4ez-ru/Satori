"""Bounded terminal knowledge census and exact materialized-head proof."""

from typing import Protocol

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from satori.domain.erasure_bodies import BodyAction
from satori.domain.erasure_knowledge import (
    KnowledgeBodyAction,
    KnowledgeBodyKind,
    KnowledgeBodyTarget,
)
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.erasure_inventory import Row, Store
from satori.infrastructure.persistence.knowledge_material import (
    KNOWLEDGE_OWNER_SPECS,
    KnowledgeMaterialIndex,
    knowledge_target_terminal_proven,
)


class ReadBudget(Protocol):
    def add(self, *, rows: int, logical_bytes: int = 0) -> None: ...


def latest_knowledge(
    actions: tuple[BodyAction, ...],
) -> dict[tuple[KnowledgeBodyKind, str], tuple[KnowledgeBodyAction, KnowledgeBodyTarget]]:
    return {
        (target.kind, target.aggregate_id): (action, target)
        for action in actions
        if isinstance(action, KnowledgeBodyAction)
        for target in action.proposal.targets
    }


def terminal_knowledge_keys(
    connection: Connection,
    snapshot: SourceEligibilitySnapshot,
    actions: tuple[BodyAction, ...],
    budget: ReadBudget,
    *,
    require_complete: bool,
) -> frozenset[tuple[KnowledgeBodyKind, str]]:
    """Detect every actual NULL/terminal remnant, including unauthorized child NULLs."""
    expected = set(latest_knowledge(actions))
    actual: set[tuple[KnowledgeBodyKind, str]] = set()
    inspection = inspect(connection)
    for kind, spec in KNOWLEDGE_OWNER_SPECS.items():
        columns = {str(c["name"]) for c in inspection.get_columns(spec.table)}
        if "body_state" not in columns:
            if any(k == kind for k, _ in expected):
                raise SourcePolicyError("body_application_schema_required")
            continue
        required = tuple(
            c
            for c in spec.content_columns
            if c not in {"alternative_topic", "normalized_alternative_topic"}
        )
        parameters: tuple[str, ...] = (snapshot.identity_id,)
        scope = "identity_id=?"
        if kind is KnowledgeBodyKind.SEMANTIC_CLAIM:
            scope = (
                "EXISTS (SELECT 1 FROM semantic_claim_evidence e "
                "JOIN conversation_messages m ON m.message_id=e.root_message_id "
                "JOIN conversation_sessions s ON s.session_id=m.session_id "
                "WHERE e.claim_id=semantic_claims.claim_id AND s.identity_id=?) OR "
                "EXISTS (SELECT 1 FROM erasure_body_applications a "
                "WHERE a.action_id=semantic_claims.body_action_id AND a.identity_id=?) OR "
                "NOT EXISTS (SELECT 1 FROM semantic_claim_evidence e "
                "WHERE e.claim_id=semantic_claims.claim_id)"
            )
            parameters = (snapshot.identity_id, snapshot.identity_id)
        predicate = (
            "(" + scope + ")" + " AND (body_state!='available' OR body_action_id IS NOT NULL OR "
        )
        predicate += " OR ".join(f'"{column}" IS NULL' for column in required) + ")"
        count, size = connection.exec_driver_sql(
            f'SELECT count(*),coalesce(sum(length(CAST("{spec.key}" AS BLOB))),0) '
            f'FROM "{spec.table}" WHERE {predicate}',
            parameters,
        ).one()
        budget.add(rows=int(count), logical_bytes=int(size))
        actual.update(
            (kind, str(row[0]))
            for row in connection.exec_driver_sql(
                f'SELECT "{spec.key}" FROM "{spec.table}" WHERE {predicate}',
                parameters,
            )
        )
    if not actual <= expected or (require_complete and actual != expected):
        raise SourcePolicyError("body_reconciliation_required")
    if "body_state" in {str(c["name"]) for c in inspection.get_columns("satori_positions")}:
        invalid = connection.exec_driver_sql(
            "SELECT 1 FROM satori_position_evidence e JOIN satori_positions p "
            "ON p.position_id=e.position_id WHERE p.identity_id=? AND "
            "((p.body_state='erased' AND "
            "(e.quote IS NOT NULL OR e.normalized_signature IS NOT NULL)) "
            "OR (p.body_state!='erased' AND (e.quote IS NULL OR e.normalized_signature IS NULL))) "
            "LIMIT 1",
            (snapshot.identity_id,),
        ).first()
        if invalid is not None:
            raise SourcePolicyError("body_reconciliation_required")
    return frozenset(actual)


def terminal_knowledge_index(
    connection: Connection,
    keys: frozenset[tuple[KnowledgeBodyKind, str]],
    budget: ReadBudget,
) -> KnowledgeMaterialIndex:
    """Read only selected owner bodies and all their exact evidence/revision rows."""
    store: Store = {}
    inspection = inspect(connection)
    for kind, spec in KNOWLEDGE_OWNER_SPECS.items():
        ids = tuple(sorted(key for owner_kind, key in keys if owner_kind == kind))
        for table in (spec.table, spec.evidence_table, spec.revision_table):
            rows: list[Row] = []
            if ids:
                columns = tuple(str(c["name"]) for c in inspection.get_columns(table))
                lengths = "+".join(f'coalesce(length(CAST("{c}" AS BLOB)),0)' for c in columns)
                for offset in range(0, len(ids), 500):
                    chunk = ids[offset : offset + 500]
                    marks = ",".join("?" for _ in chunk)
                    predicate = f'"{spec.key}" IN ({marks})'
                    if table == spec.table:
                        predicate += " AND body_state='erased' AND body_action_id IS NOT NULL"
                        predicate += "".join(
                            f' AND "{column}" IS NULL' for column in spec.content_columns
                        )
                    elif kind is KnowledgeBodyKind.POSITION and table == spec.evidence_table:
                        predicate += " AND quote IS NULL AND normalized_signature IS NULL"
                    count, size = connection.exec_driver_sql(
                        f'SELECT count(*),coalesce(sum({lengths}),0) FROM "{table}" '
                        f"WHERE {predicate}",
                        chunk,
                    ).one()
                    if table == spec.table and int(count) != len(chunk):
                        raise SourcePolicyError("body_reconciliation_required")
                    budget.add(rows=int(count), logical_bytes=int(size))
                    rows.extend(
                        dict(row)
                        for row in connection.exec_driver_sql(
                            f'SELECT * FROM "{table}" WHERE {predicate}', chunk
                        ).mappings()
                    )
            store[table] = tuple(rows)
    return KnowledgeMaterialIndex.build(store)


def validate_knowledge_projection(
    connection: Connection,
    snapshot: SourceEligibilitySnapshot,
    actions: tuple[BodyAction, ...],
    budget: ReadBudget,
    *,
    require_complete: bool = True,
) -> None:
    keys = terminal_knowledge_keys(
        connection, snapshot, actions, budget, require_complete=require_complete
    )
    if not keys:
        return
    index = terminal_knowledge_index(connection, keys, budget)
    latest = latest_knowledge(actions)
    # Re-reviewed pending actions may retain a previously approved identical terminal owner.
    approved = {
        (a.action_id, t.kind, t.aggregate_id, t.terminal_digest): (a, t)
        for a in actions
        if isinstance(a, KnowledgeBodyAction)
        for t in a.proposal.targets
    }
    for key in keys:
        _action, target = latest[key]
        row = index.aggregates.get(key)
        candidate = approved.get(
            (str(row.get("body_action_id")) if row else "", *key, target.terminal_digest)
        )
        if candidate is None or not knowledge_target_terminal_proven({}, *candidate, index=index):
            raise SourcePolicyError("body_reconciliation_required")
