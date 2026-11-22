"""Bounded exact body plans and atomic existing-owner application proofs."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.erasure_appraisal import AppraisalBodyTarget
from satori.domain.erasure_bodies import (
    MAX_BODY_TARGETS,
    BodyAction,
    BodyErasureAction,
    BodyErasureTarget,
    BodyPlan,
    action_identity,
    action_lineage,
    body_actions_digest,
    body_reference_count,
    source_root,
)
from satori.domain.erasure_dialogue import DialogueBodyAction, DialogueBodyTarget
from satori.domain.erasure_knowledge import (
    KnowledgeBodyAction,
    KnowledgeBodyKind,
    KnowledgeBodyTarget,
)
from satori.domain.erasure_reflection import ReflectionBodyAction, ReflectionBodyTarget
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.appraisal_projection import validate_appraisal_projection
from satori.infrastructure.persistence.canonical_provenance import (
    dialogue_message_digest,
    historical_from_approved_actions,
    historical_from_store,
)
from satori.infrastructure.persistence.checkpoint_reason_material import (
    validate_checkpoint_reason_projection,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import (
    ERASURE_APPRAISAL_SCHEMA_REVISION,
    ERASURE_AUDIT_SCHEMA_REVISION,
    ERASURE_BODY_SCHEMA_REVISION,
    ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
    ERASURE_DIALOGUE_SCHEMA_REVISION,
    ERASURE_KNOWLEDGE_SCHEMA_REVISION,
    ERASURE_REFLECTION_SCHEMA_REVISION,
    SQLiteErasureInventory,
    _admit_physical_schema,
    _admit_schema,
    _load_store,
    _read_transaction,
    _root,
    _verify_policy,
)
from satori.infrastructure.persistence.erasure_body_codec import (
    decode_body_action,
    decode_body_projection_rows,
    encode_body_action,
)
from satori.infrastructure.persistence.erasure_inventory import (
    ProvenOwners,
    Row,
    Store,
    assess_dependencies,
)
from satori.infrastructure.persistence.erasure_preparation import _prove_root, _source_ready
from satori.infrastructure.persistence.knowledge_material import KnowledgeMaterialIndex
from satori.infrastructure.persistence.knowledge_projection import (
    latest_knowledge,
    validate_knowledge_projection,
)
from satori.infrastructure.persistence.models.erasure_bodies import (
    ErasureBodyApplicationRow,
    ErasureBodyBindingRow,
)
from satori.infrastructure.persistence.reflection_material import ReflectionMaterialIndex
from satori.infrastructure.persistence.reflection_projection import (
    latest_reflection,
    validate_reflection_projection,
)
from satori.infrastructure.persistence.repositories.memory import SQLAlchemyEpisodicMemoryRepository
from satori.infrastructure.persistence.repositories.retrieval import (
    SQLAlchemyEpisodicMemoryIndexRepository,
)
from satori.infrastructure.persistence.source_policy import verify_mirror

MAX_BODY_APPLICATION_TARGETS = 100_000
MAX_BODY_APPLICATIONS = 4096


@dataclass(frozen=True, slots=True)
class MaterialIndex:
    """One census index; a target never scans unrelated episode rows."""

    memories: dict[str, Row]
    evidence: dict[str, tuple[Row, ...]]
    embeddings: dict[str, tuple[Row, ...]]

    @classmethod
    def build(cls, store: Store) -> "MaterialIndex":
        edges: dict[str, list[Row]] = {}
        vectors: dict[str, list[Row]] = {}
        for row in store["memory_evidence"]:
            edges.setdefault(str(row["memory_id"]), []).append(row)
        for row in store["episodic_memory_embeddings"]:
            vectors.setdefault(str(row["memory_id"]), []).append(row)
        return cls(
            {str(row["memory_id"]): row for row in store["episodic_memories"]},
            {key: tuple(rows) for key, rows in edges.items()},
            {key: tuple(rows) for key, rows in vectors.items()},
        )


def _material(store: Store | MaterialIndex, memory_id: str, *, terminal: bool) -> dict[str, object]:
    index = store if isinstance(store, MaterialIndex) else MaterialIndex.build(store)
    if memory_id not in index.memories:
        raise StopIteration
    memory = dict(index.memories[memory_id])
    edges = [dict(row) for row in index.evidence.get(memory_id, ())]
    vectors = [dict(row) for row in index.embeddings.get(memory_id, ())]
    for row, column in ((memory, "summary"), *((edge, "quote") for edge in edges)):
        row["body_action_id"] = None
        if terminal:
            row[column] = None
            row["body_state"] = "erased"
    return {"episode": memory, "evidence": edges, "vectors": [] if terminal else vectors}


def _hash(material: object) -> str:
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _latest(
    actions: tuple[BodyAction, ...], snapshot: SourceEligibilitySnapshot
) -> dict[str, tuple[BodyErasureAction, BodyErasureTarget]]:
    if (
        len(actions) != snapshot.body_revision
        or len(actions) > MAX_BODY_APPLICATIONS
        or body_actions_digest(actions) != snapshot.body_head_digest
    ):
        raise SourcePolicyError("body_reconciliation_required")
    result: dict[str, tuple[BodyErasureAction, BodyErasureTarget]] = {}
    for revision, action in enumerate(actions, 1):
        if action.result_body_revision != revision or (
            action.floor_id,
            action.installation_id,
            action_identity(action),
            action_lineage(action),
        ) != (
            snapshot.floor_id,
            snapshot.installation_id,
            snapshot.identity_id,
            snapshot.lineage_id,
        ):
            raise SourcePolicyError("body_reconciliation_required")
        if isinstance(action, BodyErasureAction):
            for target in action.proposal.targets:
                result[target.memory_id] = action, target
    if sum(len(action.proposal.targets) for action in actions) > MAX_BODY_APPLICATION_TARGETS:
        raise SourcePolicyError("body_application_capacity_exceeded")
    if len(result) > MAX_BODY_APPLICATION_TARGETS:
        raise SourcePolicyError("body_application_capacity_exceeded")
    return result


MAX_BODY_READ_BYTES = 64 * 1024 * 1024
_BODY_QUERY_CHUNK = 500


@dataclass(slots=True)
class _ReadBudget:
    rows: int = 0
    bytes: int = 0

    def add(self, *, rows: int, logical_bytes: int = 0) -> None:
        self.rows += rows
        self.bytes += logical_bytes
        if self.rows > MAX_BODY_APPLICATION_TARGETS or self.bytes > MAX_BODY_READ_BYTES:
            raise SourcePolicyError("body_application_capacity_exceeded")


def _terminal_rows(
    connection: Connection,
    *,
    table: str,
    columns: tuple[str, ...],
    ids: tuple[str, ...],
    body_column: str,
    budget: _ReadBudget,
) -> tuple[Row, ...]:
    # Only fixed owner columns, already-terminal rows, and NULL body fields are selected.
    parameters = ",".join("?" for _ in ids)
    predicate = f"memory_id IN ({parameters}) AND body_state='erased' AND {body_column} IS NULL"
    names = ",".join(f'"{name}"' for name in columns)
    lengths = "+".join(f'coalesce(length(CAST("{name}" AS BLOB)),0)' for name in columns)
    count, size = connection.exec_driver_sql(
        f'SELECT count(*),coalesce(sum({lengths}),0) FROM "{table}" WHERE {predicate}',
        ids,
    ).one()
    budget.add(rows=int(count), logical_bytes=int(size))
    return tuple(
        dict(row)
        for row in connection.exec_driver_sql(
            f'SELECT {names} FROM "{table}" WHERE {predicate} ORDER BY '
            + ("memory_id" if table == "episodic_memories" else "evidence_id"),
            ids,
        ).mappings()
    )


def _terminal_index(
    connection: Connection,
    memory_ids: Iterable[str],
    budget: _ReadBudget,
) -> MaterialIndex:
    from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow, MemoryEvidenceRow

    ids = tuple(sorted(memory_ids))
    memories: list[Row] = []
    evidence: list[Row] = []
    for offset in range(0, len(ids), _BODY_QUERY_CHUNK):
        chunk = ids[offset : offset + _BODY_QUERY_CHUNK]
        marks = ",".join("?" for _ in chunk)
        actual_edges: dict[str, int] = {
            str(row[0]): int(row[1])
            for row in connection.exec_driver_sql(
                "SELECT memory_id,count(*) FROM memory_evidence "
                f"WHERE memory_id IN ({marks}) GROUP BY memory_id",
                chunk,
            )
        }
        if (
            budget.rows + sum(int(value) for value in actual_edges.values()) + len(chunk)
            > MAX_BODY_APPLICATION_TARGETS
        ):
            raise SourcePolicyError("body_application_capacity_exceeded")
        if (
            connection.exec_driver_sql(
                "SELECT embedding_id FROM episodic_memory_embeddings "
                f"WHERE memory_id IN ({marks}) LIMIT 1",
                chunk,
            ).first()
            is not None
        ):
            raise SourcePolicyError("body_reconciliation_required")
        chunk_memories = _terminal_rows(
            connection,
            table="episodic_memories",
            columns=tuple(EpisodicMemoryRow.__table__.columns.keys()),
            ids=chunk,
            body_column="summary",
            budget=budget,
        )
        chunk_evidence = _terminal_rows(
            connection,
            table="memory_evidence",
            columns=tuple(MemoryEvidenceRow.__table__.columns.keys()),
            ids=chunk,
            body_column="quote",
            budget=budget,
        )
        per_id: dict[str, int] = {}
        for edge in chunk_evidence:
            key = str(edge["memory_id"])
            per_id[key] = per_id.get(key, 0) + 1
        if len(chunk_memories) != len(chunk) or any(
            not per_id.get(key) or per_id[key] != actual_edges.get(key, 0) for key in chunk
        ):
            raise SourcePolicyError("body_reconciliation_required")
        memories.extend(chunk_memories)
        evidence.extend(chunk_evidence)
    return MaterialIndex.build(
        {
            "episodic_memories": tuple(memories),
            "memory_evidence": tuple(evidence),
            "episodic_memory_embeddings": (),
        }
    )


def _validate_empty_projection(session: Session, snapshot: SourceEligibilitySnapshot) -> None:
    connection = session.connection()
    inspection = inspect(connection)
    tables = set(inspection.get_table_names())
    revision = (
        connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
        if "alembic_version" in tables
        else None
    )
    body_columns = {
        table: {str(column["name"]) for column in inspection.get_columns(table)}
        for table in ("episodic_memories", "memory_evidence")
        if table in tables
    }
    body_tables = {"erasure_body_bindings", "erasure_body_applications"}
    # A complete empty create_all fixture has no physical-erasure authority.
    # Actual admitted databases and every partial new-schema remnant stay pinned.
    empty_core_schema = (
        "alembic_version" not in tables
        and body_tables <= tables
        and all("body_state" in body_columns.get(table, set()) for table in body_columns)
        and set(body_columns) == {"episodic_memories", "memory_evidence"}
    )
    has_body_schema = bool(body_tables & tables) or any(
        "body_state" in columns for columns in body_columns.values()
    )
    if revision in {
        ERASURE_BODY_SCHEMA_REVISION,
        ERASURE_DIALOGUE_SCHEMA_REVISION,
        ERASURE_KNOWLEDGE_SCHEMA_REVISION,
        ERASURE_REFLECTION_SCHEMA_REVISION,
        ERASURE_APPRAISAL_SCHEMA_REVISION,
        ERASURE_AUDIT_SCHEMA_REVISION,
        ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
    } or (has_body_schema and not empty_core_schema):
        admitted_revision, _digest = _admit_physical_schema(connection)
        if admitted_revision not in {
            ERASURE_BODY_SCHEMA_REVISION,
            ERASURE_DIALOGUE_SCHEMA_REVISION,
            ERASURE_KNOWLEDGE_SCHEMA_REVISION,
            ERASURE_REFLECTION_SCHEMA_REVISION,
            ERASURE_APPRAISAL_SCHEMA_REVISION,
            ERASURE_AUDIT_SCHEMA_REVISION,
            ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
        }:
            raise SourcePolicyError("body_reconciliation_required")
    for table in ("erasure_body_bindings", "erasure_body_applications"):
        if (
            inspection.has_table(table)
            and session.connection()
            .exec_driver_sql(
                f'SELECT 1 FROM "{table}" WHERE identity_id=? LIMIT 1',
                (snapshot.identity_id,),
            )
            .first()
            is not None
        ):
            raise SourcePolicyError("body_reconciliation_required")
    for table, body_column in (("episodic_memories", "summary"), ("memory_evidence", "quote")):
        if "body_state" not in body_columns.get(table, set()):
            continue
        memory = "t" if table == "episodic_memories" else "m"
        join = (
            ""
            if table == "episodic_memories"
            else "JOIN episodic_memories m ON m.memory_id=t.memory_id "
        )
        if (
            session.connection()
            .exec_driver_sql(
                f'SELECT 1 FROM "{table}" t {join}'
                "JOIN conversation_interactions i "
                f"ON i.interaction_id={memory}.source_interaction_id "
                "JOIN conversation_sessions s ON s.session_id=i.session_id "
                "WHERE s.identity_id=? AND (t.body_state='erased' "
                f"OR t.body_action_id IS NOT NULL OR t.{body_column} IS NULL) LIMIT 1",
                (snapshot.identity_id,),
            )
            .first()
            is not None
        ):
            raise SourcePolicyError("body_reconciliation_required")

    if "body_state" in {str(c["name"]) for c in inspection.get_columns("conversation_messages")}:
        invalid = connection.exec_driver_sql(
            "SELECT 1 FROM conversation_messages m JOIN "
            "conversation_sessions s ON s.session_id=m.session_id "
            "WHERE s.identity_id=? AND (m.body_state!='available' "
            "OR m.content IS NULL OR m.body_action_id IS NOT NULL) LIMIT 1",
            (snapshot.identity_id,),
        ).first()
        if invalid is not None:
            raise SourcePolicyError("body_reconciliation_required")

    budget = _ReadBudget()
    validate_checkpoint_reason_projection(connection, snapshot, (), budget)
    validate_appraisal_projection(connection, snapshot, (), budget)
    validate_knowledge_projection(connection, snapshot, (), budget)
    validate_reflection_projection(connection, snapshot, (), budget)


def _validate_terminal_census(
    connection: Connection,
    snapshot: SourceEligibilitySnapshot,
    memory_ids: Iterable[str],
    budget: _ReadBudget,
    *,
    require_complete: bool = True,
) -> frozenset[str]:
    """Only the trusted head may authorize a terminal episode in this identity."""
    identities = (
        "JOIN conversation_interactions i ON i.interaction_id=m.source_interaction_id "
        "JOIN conversation_sessions s ON s.session_id=i.session_id "
    )
    terminal = (
        "s.identity_id=? AND (m.body_state='erased' OR m.body_action_id IS NOT NULL "
        "OR m.summary IS NULL)"
    )
    count, size = connection.exec_driver_sql(
        "SELECT count(*),coalesce(sum(length(CAST(m.memory_id AS BLOB))),0) "
        f"FROM episodic_memories m {identities}WHERE {terminal}",
        (snapshot.identity_id,),
    ).one()
    expected = frozenset(memory_ids)
    if int(count) > len(expected) or (require_complete and int(count) != len(expected)):
        raise SourcePolicyError("body_reconciliation_required")
    budget.add(rows=int(count), logical_bytes=int(size))
    actual = frozenset(
        str(row[0])
        for row in connection.exec_driver_sql(
            f"SELECT m.memory_id FROM episodic_memories m {identities}WHERE {terminal}",
            (snapshot.identity_id,),
        )
    )
    if not actual <= expected or (require_complete and actual != expected):
        raise SourcePolicyError("body_reconciliation_required")
    invalid_quote = connection.exec_driver_sql(
        "SELECT 1 FROM memory_evidence e "
        "JOIN episodic_memories m ON m.memory_id=e.memory_id "
        f"{identities}WHERE s.identity_id=? "
        "AND (e.body_state='erased' OR e.body_action_id IS NOT NULL OR e.quote IS NULL) "
        "AND (m.body_state!='erased' OR m.summary IS NOT NULL "
        "OR m.body_action_id IS NULL OR e.body_state!='erased' OR e.quote IS NOT NULL "
        "OR e.body_action_id IS NULL OR e.body_action_id!=m.body_action_id) LIMIT 1",
        (snapshot.identity_id,),
    ).first()
    if invalid_quote is not None:
        raise SourcePolicyError("body_reconciliation_required")
    return actual


def _validate_episode_targets(
    index: MaterialIndex,
    latest: dict[str, tuple[BodyErasureAction, BodyErasureTarget]],
    actions: tuple[BodyAction, ...],
    memory_ids: Iterable[str],
) -> None:
    authorized = {
        (action.action_id, target.memory_id, target.terminal_digest)
        for action in actions
        if isinstance(action, BodyErasureAction)
        for target in action.proposal.targets
    }
    for memory_id in memory_ids:
        _action, target = latest[memory_id]
        if _hash(_material(index, memory_id, terminal=False)) != target.terminal_digest:
            raise SourcePolicyError("body_reconciliation_required")
        for terminal_row in (index.memories[memory_id], *index.evidence.get(memory_id, ())):
            if (
                terminal_row["body_action_id"] != index.memories[memory_id]["body_action_id"]
                or (str(terminal_row["body_action_id"]), memory_id, target.terminal_digest)
                not in authorized
            ):
                raise SourcePolicyError("body_reconciliation_required")


def validate_review_terminal_state(
    connection: Connection,
    store: Store,
    snapshot: SourceEligibilitySnapshot,
    approved_actions: tuple[BodyAction, ...] | None,
) -> None:
    """A pending target may be live; every existing terminal body still needs exact proof."""
    from satori.infrastructure.persistence.knowledge_material import KNOWLEDGE_OWNER_SPECS

    body_fields = (
        ("episodic_memories", "summary"),
        ("memory_evidence", "quote"),
        ("conversation_messages", "content"),
        *(
            (spec.table, column)
            for spec in KNOWLEDGE_OWNER_SPECS.values()
            for column in spec.content_columns
            if column not in {"alternative_topic", "normalized_alternative_topic"}
        ),
        ("satori_position_evidence", "quote"),
        ("satori_position_evidence", "normalized_signature"),
        ("reflection_proposals", "payload"),
    )
    actions = (
        decode_body_projection_rows(store["erasure_body_applications"])
        if approved_actions is None
        else approved_actions
    )
    budget = _ReadBudget()
    validate_checkpoint_reason_projection(
        connection, snapshot, actions, budget, require_complete=False
    )
    validate_appraisal_projection(connection, snapshot, actions, budget, require_complete=False)
    if not any(
        row.get("body_state", "available") != "available"
        or row.get("body_action_id") is not None
        or row.get(column) is None
        for table, column in body_fields
        for row in store.get(table, ())
    ):
        return
    latest = _latest(actions, snapshot)
    actual = _validate_terminal_census(connection, snapshot, latest, budget, require_complete=False)
    index = _terminal_index(connection, actual, budget)
    _validate_episode_targets(index, latest, actions, actual)
    _validate_dialogue_projection(connection, snapshot, actions, budget, require_complete=False)
    validate_knowledge_projection(connection, snapshot, actions, budget, require_complete=False)
    validate_reflection_projection(connection, snapshot, actions, budget, require_complete=False)


def validate_body_projection(session: Session, snapshot: SourceEligibilitySnapshot) -> None:
    if not snapshot.body_revision:
        _validate_empty_projection(session, snapshot)
        return
    admitted_revision, _digest = _admit_physical_schema(session.connection())
    if admitted_revision not in {
        ERASURE_BODY_SCHEMA_REVISION,
        ERASURE_DIALOGUE_SCHEMA_REVISION,
        ERASURE_KNOWLEDGE_SCHEMA_REVISION,
        ERASURE_REFLECTION_SCHEMA_REVISION,
        ERASURE_APPRAISAL_SCHEMA_REVISION,
        ERASURE_AUDIT_SCHEMA_REVISION,
        ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
    }:
        raise SourcePolicyError("body_reconciliation_required")
    binding = session.get(ErasureBodyBindingRow, snapshot.identity_id)
    if binding is None or (binding.floor_id, binding.lineage_id, binding.body_revision) != (
        snapshot.floor_id,
        snapshot.lineage_id,
        snapshot.body_revision,
    ):
        raise SourcePolicyError("body_reconciliation_required")
    budget = _ReadBudget()
    receipt_lengths = "+".join(
        f'coalesce(length(CAST("{name}" AS BLOB)),0)'
        for name in tuple(ErasureBodyApplicationRow.__table__.columns.keys())
    )
    receipt_count, receipt_bytes = (
        session.connection()
        .exec_driver_sql(
            f"SELECT count(*),coalesce(sum({receipt_lengths}),0) "
            "FROM erasure_body_applications WHERE identity_id=?",
            (snapshot.identity_id,),
        )
        .one()
    )
    if int(receipt_count) > MAX_BODY_APPLICATIONS:
        raise SourcePolicyError("body_application_capacity_exceeded")
    budget.add(rows=int(receipt_count), logical_bytes=int(receipt_bytes))
    rows = tuple(
        session.scalars(
            select(ErasureBodyApplicationRow)
            .where(ErasureBodyApplicationRow.identity_id == snapshot.identity_id)
            .order_by(ErasureBodyApplicationRow.body_revision)
            .limit(MAX_BODY_APPLICATIONS + 1)
        )
    )
    actions = tuple(decode_body_action(row.action) for row in rows)
    latest = _latest(actions, snapshot)
    for row, action in zip(rows, actions, strict=True):
        root = source_root(action)
        if (
            row.action_id,
            row.request_id,
            row.root_message_id,
            row.floor_id,
            row.lineage_id,
            row.body_revision,
        ) != (
            action.action_id,
            action.proposal.request_id,
            root.message_id if root is not None else None,
            action.floor_id,
            action_lineage(action),
            action.result_body_revision,
        ):
            raise SourcePolicyError("body_reconciliation_required")
        if root is not None:
            _prove_root(session, root, snapshot)
    # Prove terminal owner content before historical witnesses can load retained
    # rows. Copied receipts/ACKs must not make restored position quotes readable.
    validate_checkpoint_reason_projection(session.connection(), snapshot, actions, budget)
    validate_appraisal_projection(session.connection(), snapshot, actions, budget)
    validate_knowledge_projection(session.connection(), snapshot, actions, budget)
    validate_reflection_projection(session.connection(), snapshot, actions, budget)
    _validate_dialogue_projection(session.connection(), snapshot, actions, budget)
    _validate_terminal_census(session.connection(), snapshot, latest, budget)
    index = _terminal_index(session.connection(), latest, budget)
    _validate_episode_targets(index, latest, actions, latest)


class SQLiteBodyErasureStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def resolve_root(
        self,
        message_id: str,
        *,
        counterparty_id: str,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> CanonicalRootRef:
        return SQLiteErasureInventory(self.database).resolve_root(
            message_id,
            counterparty_id=counterparty_id,
            snapshot=snapshot,
            approved_actions=approved_actions,
        )

    @staticmethod
    def _schema(connection: Connection) -> str:
        revision, digest = _admit_schema(connection)
        if revision not in {
            ERASURE_BODY_SCHEMA_REVISION,
            ERASURE_DIALOGUE_SCHEMA_REVISION,
            ERASURE_KNOWLEDGE_SCHEMA_REVISION,
            ERASURE_REFLECTION_SCHEMA_REVISION,
            ERASURE_APPRAISAL_SCHEMA_REVISION,
            ERASURE_AUDIT_SCHEMA_REVISION,
            ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
        }:
            raise SourcePolicyError("body_application_schema_required")
        return digest

    def preview(
        self,
        root: CanonicalRootRef,
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> BodyPlan:
        try:
            with _read_transaction(self.database) as connection:
                schema_digest = self._schema(connection)
                if (
                    _root(
                        connection,
                        root.message_id,
                        root.counterparty_id,
                        snapshot,
                        approved_actions=approved_actions,
                    )
                    != root
                ):
                    raise SourcePolicyError("canonical_source_unavailable_in_scope")
                _verify_policy(connection, snapshot)
                with Session(bind=connection) as session:
                    verify_mirror(session, snapshot, include_body=False)
                store, count, digest, _keys, _retention = _load_store(connection, schema_digest)
                material = MaterialIndex.build(store)
                from satori.infrastructure.persistence.canonical_provenance import (
                    historical_from_approved_actions,
                )

                historical = (
                    historical_from_store(store, snapshot)
                    if approved_actions is None
                    else historical_from_approved_actions(store, snapshot, approved_actions)
                )
                validate_review_terminal_state(connection, store, snapshot, approved_actions)
                owners = ProvenOwners(store, snapshot.identity_id, historical=historical)
                assessment = assess_dependencies(
                    store, snapshot.identity_id, root.message_id, historical=historical
                )
                selected = {
                    payload_id
                    for kind, payload_id, _version in assessment.affected
                    if kind == "episode"
                }
                selected.update(
                    str(row["memory_id"])
                    for row in store["episodic_memories"]
                    if row["source_interaction_id"] == root.interaction_id
                )
                selected.update(
                    str(row["memory_id"])
                    for row in store["memory_evidence"]
                    if row["source_message_id"] == root.message_id
                )
                blockers = set()
                if any(kind == "episode" for kind, _id, _version in assessment.uncertain):
                    blockers.add("episode_body_provenance_unproven")
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                    blockers.add("store_foreign_key_unproven")
                if len(selected) > MAX_BODY_TARGETS:
                    blockers.add("body_target_capacity_exceeded")
                if count * len(selected) > MAX_ERASURE_ASSESSMENT_WORK:
                    raise SourcePolicyError("body_application_capacity_exceeded")
                targets: list[BodyErasureTarget] = []
                quotes = vectors = erased = 0
                for row in store["episodic_memories"]:
                    memory_id = str(row["memory_id"])
                    if memory_id not in selected:
                        continue
                    version = int(str(row["aggregate_version"]))
                    if not owners.admits(("episode", memory_id, version)):
                        blockers.add("episode_body_provenance_unproven")
                    source = owners.interactions.get(str(row["source_interaction_id"]))
                    scoped = owners.sessions.get(str(source["session_id"])) if source else None
                    if scoped is None or scoped["counterparty_id"] != root.counterparty_id:
                        blockers.add("cross_counterparty_body_policy_required")
                    if row["body_state"] == "erased":
                        erased += 1
                        continue
                    if row["body_state"] != "available":
                        blockers.add("episode_body_provenance_unproven")
                    targets.append(
                        BodyErasureTarget(
                            memory_id,
                            version,
                            _hash(_material(material, memory_id, terminal=False)),
                            _hash(_material(material, memory_id, terminal=True)),
                        )
                    )
                    quotes += len(material.evidence.get(memory_id, ()))
                    vectors += len(material.embeddings.get(memory_id, ()))
                from satori.infrastructure.source_policy.body_policy import (
                    MAX_BODY_ACTIONS,
                    MAX_BODY_REFERENCES,
                    encode_targets,
                )

                actions = (
                    decode_body_projection_rows(store["erasure_body_applications"])
                    if approved_actions is None
                    else approved_actions
                )
                if (
                    len(actions) >= MAX_BODY_ACTIONS
                    or body_reference_count(actions) + len(targets) > MAX_BODY_REFERENCES
                ):
                    blockers.add("policy_body_capacity_exceeded")
                try:
                    encode_targets(tuple(targets))
                except SourcePolicyError:
                    blockers.add("policy_body_capacity_exceeded")
                if not targets:
                    blockers.add("no_available_episode_bodies")
                return BodyPlan(
                    root,
                    tuple(sorted(targets, key=lambda target: target.memory_id)),
                    digest,
                    schema_digest,
                    count,
                    quotes,
                    vectors,
                    erased,
                    tuple(sorted(blockers)),
                )
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, ValueError, TypeError, KeyError):
            raise SourcePolicyError("body_inventory_unavailable") from None

    def current(
        self, actions: tuple[BodyAction, ...], *, snapshot: SourceEligibilitySnapshot
    ) -> bool:
        try:
            with (
                _read_transaction(self.database) as connection,
                Session(bind=connection) as session,
            ):
                self._schema(connection)
                verify_mirror(session, snapshot)
                _latest(actions, snapshot)
            return True
        except (SourcePolicyError, SQLAlchemyError, ValueError, TypeError, KeyError):
            return False

    def apply(
        self,
        actions: tuple[BodyAction, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
        reviewed_store_digest: str | None = None,
    ) -> None:
        try:
            with self.database.session_factory() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                schema_digest = self._schema(session.connection())
                _source_ready(session, snapshot)
                verify_mirror(session, snapshot, include_body=False)
                latest = _latest(actions, snapshot)
                for action in actions:
                    root = source_root(action)
                    if root is not None:
                        _prove_root(session, root, snapshot)
                store, count, digest, _keys, _retention = _load_store(
                    session.connection(), schema_digest
                )
                if count * body_reference_count(actions) > MAX_ERASURE_ASSESSMENT_WORK:
                    raise SourcePolicyError("body_application_capacity_exceeded")
                if reviewed_store_digest is not None and digest != reviewed_store_digest:
                    raise SourcePolicyError("body_erasure_review_stale")
                # Validate every target before recording receipts or writing any body.
                pending: dict[str, list[BodyErasureTarget]] = {}
                terminal_episodes: list[str] = []
                material = MaterialIndex.build(store)
                historical = historical_from_approved_actions(store, snapshot, actions)
                owners = ProvenOwners(store, snapshot.identity_id, historical=historical)
                for memory_id, (action, target) in latest.items():
                    try:
                        actual = _hash(_material(material, memory_id, terminal=False))
                    except StopIteration:
                        raise SourcePolicyError("body_target_changed_review_required") from None
                    if actual == target.terminal_digest:
                        terminal_episodes.append(memory_id)
                        continue
                    if actual != target.body_digest or not owners.admits(
                        ("episode", memory_id, target.aggregate_version)
                    ):
                        raise SourcePolicyError("body_target_changed_review_required")
                    pending.setdefault(action.action_id, []).append(target)
                _validate_episode_targets(material, latest, actions, terminal_episodes)
                dialogue_pending = _dialogue_pending(store, actions, snapshot)
                knowledge_pending = _knowledge_pending(store, actions, snapshot, owners)
                reflection_pending = _reflection_pending(store, actions, snapshot, owners)
                from satori.infrastructure.persistence.erasure_appraisal import (
                    preflight_appraisal_pending,
                )

                appraisal_pending: dict[str, list[AppraisalBodyTarget]] = (
                    preflight_appraisal_pending(store, actions, snapshot, historical=historical)
                )
                from satori.infrastructure.persistence.erasure_personality_reasons import (
                    checkpoint_reasons_pending,
                )

                reason_pending = checkpoint_reasons_pending(store, snapshot, actions)
                for action in actions:
                    root = source_root(action)
                    existing = session.get(ErasureBodyApplicationRow, action.action_id)
                    if existing is None:
                        session.add(
                            ErasureBodyApplicationRow(
                                action_id=action.action_id,
                                request_id=action.proposal.request_id,
                                identity_id=snapshot.identity_id,
                                root_message_id=root.message_id if root is not None else None,
                                floor_id=action.floor_id,
                                lineage_id=snapshot.lineage_id,
                                body_revision=action.result_body_revision,
                                action=encode_body_action(action),
                                applied_at=datetime.now(UTC),
                            )
                        )
                    elif decode_body_action(existing.action) != action:
                        raise SourcePolicyError("body_reconciliation_required")
                session.flush()
                for action_id, targets in pending.items():
                    SQLAlchemyEpisodicMemoryIndexRepository(session).erase_bodies(
                        tuple(target.memory_id for target in targets)
                    )
                    SQLAlchemyEpisodicMemoryRepository(session).erase_bodies(
                        tuple(targets), action_id=action_id
                    )
                from satori.infrastructure.persistence.repositories.conversation import (
                    SQLAlchemyConversationHistoryRepository,
                )

                for action_id, dialogue_targets in dialogue_pending.items():
                    SQLAlchemyConversationHistoryRepository(session).erase_bodies(
                        tuple(dialogue_targets), action_id=action_id
                    )
                _apply_knowledge_pending(session, knowledge_pending)
                from satori.infrastructure.persistence.repositories.reflection import (
                    SQLAlchemyReflectionRepository,
                )

                for action_id, reflection_targets in reflection_pending.items():
                    SQLAlchemyReflectionRepository(session).erase_payload_bodies(
                        tuple(reflection_targets), action_id=action_id
                    )
                from satori.infrastructure.persistence.repositories.affect import (
                    SQLAlchemyAffectiveStateRepository,
                )

                for action_id, appraisal_targets in appraisal_pending.items():
                    SQLAlchemyAffectiveStateRepository(session).erase_explanation_bodies(
                        tuple(appraisal_targets), action_id=action_id
                    )
                from satori.infrastructure.persistence.repositories.personality import (
                    SQLAlchemyPersonalityRepository,
                )

                for action_id, reason_targets in reason_pending.items():
                    SQLAlchemyPersonalityRepository(session).erase_reason_bodies(
                        tuple(reason_targets), action_id=action_id
                    )
                binding = session.get(ErasureBodyBindingRow, snapshot.identity_id)
                if binding is None:
                    session.add(
                        ErasureBodyBindingRow(
                            identity_id=snapshot.identity_id,
                            floor_id=snapshot.floor_id,
                            lineage_id=snapshot.lineage_id,
                            body_revision=snapshot.body_revision,
                        )
                    )
                else:
                    binding.floor_id, binding.lineage_id, binding.body_revision = (
                        snapshot.floor_id,
                        snapshot.lineage_id,
                        snapshot.body_revision,
                    )
                session.flush()
                validate_body_projection(session, snapshot)
                session.commit()
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, ValueError, TypeError, KeyError):
            raise SourcePolicyError("body_application_unavailable") from None


def _latest_dialogue(
    actions: tuple[BodyAction, ...],
) -> dict[str, tuple[DialogueBodyAction, DialogueBodyTarget]]:
    result: dict[str, tuple[DialogueBodyAction, DialogueBodyTarget]] = {}
    for action in actions:
        if isinstance(action, DialogueBodyAction):
            for target in action.proposal.targets:
                result[target.message_id] = action, target
    return result


def _validate_dialogue_projection(
    connection: Connection,
    snapshot: SourceEligibilitySnapshot,
    actions: tuple[BodyAction, ...],
    budget: _ReadBudget,
    *,
    require_complete: bool = True,
) -> None:
    from satori.infrastructure.persistence.canonical_provenance import validate_historical_witnesses
    from satori.infrastructure.persistence.models.conversation import ConversationMessageRow

    latest = _latest_dialogue(actions)
    columns = {
        str(row[1])
        for row in connection.exec_driver_sql("PRAGMA table_info(conversation_messages)")
    }
    if "body_state" not in columns:
        if latest:
            raise SourcePolicyError("body_application_schema_required")
        return
    count, size = connection.exec_driver_sql(
        "SELECT count(*),coalesce(sum(length(CAST(m.message_id AS BLOB))),0) "
        "FROM conversation_messages m JOIN conversation_sessions s ON s.session_id=m.session_id "
        "WHERE s.identity_id=? AND (m.body_state!='available' OR "
        "m.content IS NULL OR m.body_action_id IS NOT NULL)",
        (snapshot.identity_id,),
    ).one()
    if int(count) > len(latest) or (require_complete and int(count) != len(latest)):
        raise SourcePolicyError("body_reconciliation_required")
    budget.add(rows=int(count), logical_bytes=int(size))
    actual = {
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT m.message_id FROM conversation_messages m "
            "JOIN conversation_sessions s ON s.session_id=m.session_id "
            "WHERE s.identity_id=? AND (m.body_state!='available' "
            "OR m.content IS NULL OR m.body_action_id IS NOT NULL)",
            (snapshot.identity_id,),
        )
    }
    if not actual <= set(latest) or (require_complete and actual != set(latest)):
        raise SourcePolicyError("body_reconciliation_required")
    authorized = {
        (a.action_id, t.message_id, t.terminal_digest)
        for a in actions
        if isinstance(a, DialogueBodyAction)
        for t in a.proposal.targets
    }
    names = tuple(ConversationMessageRow.__table__.columns.keys())
    selection = ",".join(f'"{name}"' for name in names)
    lengths = "+".join(f'coalesce(length(CAST("{name}" AS BLOB)),0)' for name in names)
    ordered_ids = tuple(sorted(actual))
    for offset in range(0, len(ordered_ids), _BODY_QUERY_CHUNK):
        ids = ordered_ids[offset : offset + _BODY_QUERY_CHUNK]
        marks = ",".join("?" for _ in ids)
        predicate = f"message_id IN ({marks}) AND body_state='erased' AND content IS NULL"
        n, b = connection.exec_driver_sql(
            f"SELECT count(*),coalesce(sum({lengths}),0) FROM "
            f"conversation_messages WHERE {predicate}",
            ids,
        ).one()
        if int(n) != len(ids):
            raise SourcePolicyError("body_reconciliation_required")
        budget.add(rows=int(n), logical_bytes=int(b))
        for row in connection.exec_driver_sql(
            f"SELECT {selection} FROM conversation_messages WHERE {predicate}", ids
        ).mappings():
            key = str(row["message_id"])
            _action, target = latest[key]
            scope = connection.exec_driver_sql(
                "SELECT s.identity_id,s.counterparty_id,s.scope_verified,i.status "
                "FROM conversation_interactions i JOIN "
                "conversation_sessions s ON s.session_id=i.session_id "
                "WHERE i.interaction_id=? AND i.session_id=?",
                (row["interaction_id"], row["session_id"]),
            ).first()
            if (
                scope is None
                or tuple(scope)
                != (snapshot.identity_id, _action.proposal.root.counterparty_id, 1, "completed")
                or (row["role"] != "assistant" and key != _action.proposal.root.message_id)
            ):
                raise SourcePolicyError("body_reconciliation_required")
            if (
                dialogue_message_digest(dict(row), terminal=False) != target.terminal_digest
                or (str(row["body_action_id"]), key, target.terminal_digest) not in authorized
            ):
                raise SourcePolicyError("body_reconciliation_required")
    validate_historical_witnesses(
        connection,
        actions,
        snapshot=snapshot,
        budget=budget,
        message_ids=None if require_complete else frozenset(actual),
    )


def _dialogue_pending(
    store: Store, actions: tuple[BodyAction, ...], snapshot: SourceEligibilitySnapshot
) -> dict[str, list[DialogueBodyTarget]]:
    owners = ProvenOwners(store, snapshot.identity_id)
    result: dict[str, list[DialogueBodyTarget]] = {}
    approved = {
        (action.action_id, target.message_id, target.terminal_digest)
        for action in actions
        if isinstance(action, DialogueBodyAction)
        for target in action.proposal.targets
    }
    for message_id, (action, target) in _latest_dialogue(actions).items():
        row = owners.messages.get(message_id)
        if row is None:
            raise SourcePolicyError("body_target_changed_review_required")
        interaction = owners.interactions.get(str(row["interaction_id"]))
        scope = owners.sessions.get(str(interaction["session_id"])) if interaction else None
        if (
            scope is None
            or not scope["scope_verified"]
            or scope["identity_id"] != snapshot.identity_id
            or scope["counterparty_id"] != action.proposal.root.counterparty_id
            or interaction is None
            or interaction["status"] != "completed"
        ):
            raise SourcePolicyError("body_target_changed_review_required")
        if row["role"] != "assistant" and message_id != action.proposal.root.message_id:
            raise SourcePolicyError("body_target_changed_review_required")
        actual = dialogue_message_digest(row, terminal=False)
        if actual == target.terminal_digest:
            if (str(row.get("body_action_id")), message_id, target.terminal_digest) not in approved:
                raise SourcePolicyError("body_reconciliation_required")
            continue
        if actual != target.body_digest:
            raise SourcePolicyError("body_target_changed_review_required")
        result.setdefault(action.action_id, []).append(target)
    return result


def _knowledge_pending(
    store: Store,
    actions: tuple[BodyAction, ...],
    snapshot: SourceEligibilitySnapshot,
    owners: ProvenOwners,
) -> dict[str, list[KnowledgeBodyTarget]]:
    """Preflight the whole mixed head before any owner executes its mutation."""
    material = KnowledgeMaterialIndex.build(store)
    approved = {
        (a.action_id, t.kind, t.aggregate_id, t.terminal_digest)
        for a in actions
        if isinstance(a, KnowledgeBodyAction)
        for t in a.proposal.targets
    }
    result: dict[str, list[KnowledgeBodyTarget]] = {}
    for key, (action, target) in latest_knowledge(actions).items():
        row = material.aggregates.get(key)
        if row is None or ("identity_id" in row and row["identity_id"] != snapshot.identity_id):
            raise SourcePolicyError("body_target_changed_review_required")
        actual = material.digest(*key, terminal=False)
        if actual == target.terminal_digest:
            if (str(row.get("body_action_id")), *key, target.terminal_digest) not in approved:
                raise SourcePolicyError("body_reconciliation_required")
            continue
        if (
            actual != target.body_digest
            or row.get("body_state") != "available"
            or row.get("body_action_id") is not None
            or material.digest(*key, terminal=True) != target.terminal_digest
            or not owners.admits((target.kind.value, target.aggregate_id, target.aggregate_version))
        ):
            raise SourcePolicyError("body_target_changed_review_required")
        result.setdefault(action.action_id, []).append(target)
    return result


def _reflection_pending(
    store: Store,
    actions: tuple[BodyAction, ...],
    snapshot: SourceEligibilitySnapshot,
    owners: ProvenOwners,
) -> dict[str, list[ReflectionBodyTarget]]:
    """Preflight frozen parent metadata and payloads before any mixed owner writes."""
    latest = latest_reflection(actions)
    if not latest:
        return {}
    material = ReflectionMaterialIndex.build(store)
    authorized = {
        (action.action_id, target.proposal_id, target.terminal_digest)
        for action in actions
        if isinstance(action, ReflectionBodyAction)
        for target in action.proposal.targets
    }
    pending: dict[str, list[ReflectionBodyTarget]] = {}
    for proposal_id, (action, target) in latest.items():
        row = material.proposals.get(proposal_id)
        run = material.runs.get(target.run_id)
        if (
            row is None
            or run is None
            or row["run_id"] != target.run_id
            or run["identity_id"] != snapshot.identity_id
            or run["aggregate_version"] != target.run_version
            or run["status"] not in {"completed", "exhausted"}
            or material.metadata_reference_count(proposal_id) != target.metadata_reference_count
        ):
            raise SourcePolicyError("body_target_changed_review_required")
        actual = material.digest(proposal_id, terminal=False)
        if actual == target.terminal_digest:
            if (
                str(row.get("body_action_id")),
                proposal_id,
                target.terminal_digest,
            ) not in authorized:
                raise SourcePolicyError("body_reconciliation_required")
            continue
        if (
            actual != target.body_digest
            or row.get("body_state") != "available"
            or row.get("body_action_id") is not None
            or material.digest(proposal_id, terminal=True) != target.terminal_digest
            or not owners.reflection_proven(proposal_id)
        ):
            raise SourcePolicyError("body_target_changed_review_required")
        pending.setdefault(action.action_id, []).append(target)
    return pending


def _apply_knowledge_pending(
    session: Session, pending: dict[str, list[KnowledgeBodyTarget]]
) -> None:
    from satori.infrastructure.persistence.repositories.models import (
        SQLAlchemyCurrentModelsRepository,
    )
    from satori.infrastructure.persistence.repositories.positions import (
        SQLAlchemyPositionsRepository,
    )
    from satori.infrastructure.persistence.repositories.semantic import (
        SQLAlchemySemanticMemoryRepository,
    )

    for action_id, targets in pending.items():
        for repository, kinds in (
            (SQLAlchemySemanticMemoryRepository(session), {KnowledgeBodyKind.SEMANTIC_CLAIM}),
            (
                SQLAlchemyCurrentModelsRepository(session),
                {KnowledgeBodyKind.USER_MODEL_CLAIM, KnowledgeBodyKind.WORLD_MODEL_CLAIM},
            ),
            (
                SQLAlchemyPositionsRepository(session),
                {KnowledgeBodyKind.POSITION, KnowledgeBodyKind.INCLINATION},
            ),
        ):
            owned = tuple(target for target in targets if target.kind in kinds)
            if owned:
                repository.erase_knowledge_bodies(owned, action_id=action_id)
