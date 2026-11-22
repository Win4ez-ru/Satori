"""Bounded, read-only current-main-store erasure inventory.

The pinned schema is the admitted Alembic 0016 schema, using recovery's narrow
named-constraint ordering normalization. No preview runs a migration, repairs
an owner, initializes state, or claims that retained physical bytes are gone.
"""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK, ErasureInventory
from satori.domain.erasure_bodies import BodyAction
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_inventory import Store, assess_dependencies
from satori.infrastructure.persistence.erasure_routes import ROUTE_SPECS, build_routes
from satori.infrastructure.persistence.models import Base
from satori.infrastructure.persistence.recovery import _canonical_table_sql, _schema_digest

MAX_ERASURE_ROWS = 100_000
MAX_ERASURE_LOGICAL_BYTES = 64 * 1024 * 1024
ERASURE_SCHEMA_REVISION = "0016_source_policy"
ERASURE_SCHEMA_DIGEST = "e505b2a74b638aeb22af464992d55f36e772c57d05335df31e5155d48d83af4a"
ERASURE_PREPARATION_TABLES = frozenset({"erasure_policy_bindings", "erasure_preparations"})
ERASURE_INTENT_SCHEMA_REVISION = "0017_erasure_intent"
ERASURE_INTENT_SCHEMA_DIGEST = "941198b04cb64441061d9ec91038b4e206acb754849072b73e7ab844dd8f7d3b"
ERASURE_BODY_SCHEMA_REVISION = "0018_erasure_bodies"
ERASURE_BODY_SCHEMA_DIGEST = "115c934b77f199e2b8abadb1bcd202f1e3f5a0ca6a771bbe0d5f96f2a46ff9ed"
ERASURE_DIALOGUE_SCHEMA_REVISION = "0019_dialogue_bodies"
ERASURE_DIALOGUE_SCHEMA_DIGEST = "69c87475bb8ec301ed0d723aa0902f359c14f6d0def5b1c13c340a659f2c3611"
ERASURE_KNOWLEDGE_SCHEMA_REVISION = "0020_knowledge_bodies"
ERASURE_KNOWLEDGE_SCHEMA_DIGEST = "df549886416fd74cc4eed9d5eb7f8c255ffb1ef592e3d027d5874ef9a8b7a4ba"
ERASURE_REFLECTION_SCHEMA_REVISION = "0021_reflection_bodies"
ERASURE_REFLECTION_SCHEMA_DIGEST = (
    "c00d86726a92cfef93c2235f8a181b38666c453ea11bb30e4be913618f2e451e"
)
ERASURE_AUDIT_SCHEMA_REVISION = "0023_audit_metadata"
ERASURE_AUDIT_SCHEMA_DIGEST = "3cb0337ba3258fbd41582799f73b1269225b65a872a50c1020ca298a4da8ab08"
ERASURE_AUDIT_TABLES = frozenset({"audit_metadata_bindings"})
ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION = "0024_checkpoint_reasons"
ERASURE_CHECKPOINT_REASON_SCHEMA_DIGEST = (
    "7495f9b528c47b52245ea83c0811de53b0f1223cc7dd09a9bdabb84b73203227"
)
ERASURE_APPRAISAL_SCHEMA_REVISION = "0022_appraisal_bodies"
ERASURE_APPRAISAL_SCHEMA_DIGEST = "480b98bfce7e7fc89afe6e132f7afe697d62da02c6cedd9bf7b694192cd59100"
ERASURE_BODY_TABLES = frozenset({"erasure_body_bindings", "erasure_body_applications"})
REQUIRED_REPAIR_OWNERS = frozenset(
    {"semantic_memory", "current_models", "positions", "reflection_backlog", "relationship_backlog"}
)


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


@contextmanager
def _read_transaction(database: Database) -> Iterator[Connection]:
    if database.engine.dialect.name != "sqlite":
        raise SourcePolicyError("erasure_requires_file_sqlite")
    with database.engine.connect() as connection:
        query_only = int(connection.exec_driver_sql("PRAGMA query_only").scalar_one())
        trusted_schema = int(connection.exec_driver_sql("PRAGMA trusted_schema").scalar_one())
        try:
            connection.exec_driver_sql("PRAGMA query_only=ON")
            connection.exec_driver_sql("PRAGMA trusted_schema=OFF")
            # SQLAlchemy's logical begin does not start a physical transaction
            # for SELECT under sqlite3's legacy transaction control.
            connection.exec_driver_sql("BEGIN")
            yield connection
        finally:
            connection.rollback()
            connection.exec_driver_sql(f"PRAGMA trusted_schema={trusted_schema}")
            connection.exec_driver_sql(f"PRAGMA query_only={query_only}")
            connection.rollback()


def _admitted_tables(revision: str) -> set[str]:
    expected = set(Base.metadata.tables) | {"alembic_version"}
    if revision not in {
        ERASURE_BODY_SCHEMA_REVISION,
        ERASURE_DIALOGUE_SCHEMA_REVISION,
        ERASURE_KNOWLEDGE_SCHEMA_REVISION,
        ERASURE_REFLECTION_SCHEMA_REVISION,
        ERASURE_APPRAISAL_SCHEMA_REVISION,
        ERASURE_AUDIT_SCHEMA_REVISION,
        ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
    }:
        expected -= ERASURE_BODY_TABLES
    if revision not in {ERASURE_AUDIT_SCHEMA_REVISION, ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION}:
        expected -= ERASURE_AUDIT_TABLES
    if revision == ERASURE_SCHEMA_REVISION:
        expected -= ERASURE_PREPARATION_TABLES
    return expected


def _admit_physical_schema(connection: Connection) -> tuple[str, str]:
    """Pin exact current DDL and its complete owner-table set without inventory reads."""
    rows = tuple(
        (
            str(kind),
            str(name),
            str(table),
            _canonical_table_sql(sql) if kind == "table" and isinstance(sql, str) else sql,
        )
        for kind, name, table, sql in connection.exec_driver_sql(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        )
    )
    schema_digest = _schema_digest(rows)
    revisions = tuple(
        str(row[0]) for row in connection.exec_driver_sql("SELECT version_num FROM alembic_version")
    )
    supported = {
        ERASURE_SCHEMA_REVISION: ERASURE_SCHEMA_DIGEST,
        ERASURE_INTENT_SCHEMA_REVISION: ERASURE_INTENT_SCHEMA_DIGEST,
        ERASURE_BODY_SCHEMA_REVISION: ERASURE_BODY_SCHEMA_DIGEST,
        ERASURE_DIALOGUE_SCHEMA_REVISION: ERASURE_DIALOGUE_SCHEMA_DIGEST,
        ERASURE_KNOWLEDGE_SCHEMA_REVISION: ERASURE_KNOWLEDGE_SCHEMA_DIGEST,
        ERASURE_REFLECTION_SCHEMA_REVISION: ERASURE_REFLECTION_SCHEMA_DIGEST,
        ERASURE_APPRAISAL_SCHEMA_REVISION: ERASURE_APPRAISAL_SCHEMA_DIGEST,
        ERASURE_AUDIT_SCHEMA_REVISION: ERASURE_AUDIT_SCHEMA_DIGEST,
        ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION: ERASURE_CHECKPOINT_REASON_SCHEMA_DIGEST,
    }
    if len(revisions) != 1 or supported.get(revisions[0]) != schema_digest:
        raise SourcePolicyError("erasure_schema_unsupported")
    tables = {name for kind, name, _table, _sql in rows if kind == "table"}
    expected = _admitted_tables(revisions[0])
    if tables != expected or not tables <= set(ROUTE_SPECS):
        raise SourcePolicyError("erasure_owner_registry_incomplete")
    return revisions[0], schema_digest


def _admit_schema(connection: Connection) -> tuple[str, str]:
    """Inventory admission additionally proves each registered content/key column."""
    revision, schema_digest = _admit_physical_schema(connection)
    tables = _admitted_tables(revision)
    for table, spec in ROUTE_SPECS.items():
        if table not in tables:
            continue
        names = {
            str(row[1])
            for row in connection.exec_driver_sql(f"PRAGMA table_info({_identifier(table)})")
        }
        if not set(spec.content_columns) <= names or (
            spec.key is not None and spec.key not in names
        ):
            raise SourcePolicyError("erasure_owner_registry_incomplete")
    return revision, schema_digest


def _root(
    connection: Connection,
    message_id: str,
    counterparty_id: str,
    snapshot: SourceEligibilitySnapshot,
    *,
    approved_actions: tuple[BodyAction, ...] | None = None,
) -> CanonicalRootRef:
    # Scope proof precedes every store count and content-bearing SELECT.
    row = connection.exec_driver_sql(
        "SELECT m.message_id,m.interaction_id,m.session_id,s.identity_id,s.counterparty_id "
        "FROM conversation_messages AS m "
        "JOIN conversation_interactions AS i ON i.interaction_id=m.interaction_id "
        "AND i.session_id=m.session_id "
        "JOIN conversation_sessions AS s ON s.session_id=i.session_id "
        "WHERE m.message_id=? AND m.role='user' AND m.sequence=1 "
        "AND i.status='completed' AND s.scope_verified=1 "
        "AND s.identity_id=? AND s.counterparty_id=?",
        (message_id, snapshot.identity_id, counterparty_id),
    ).first()
    if row is None:
        raise SourcePolicyError("canonical_source_unavailable_in_scope")
    if message_id not in snapshot.excluded_message_ids:
        raise SourcePolicyError("erasure_requires_source_exclusion")
    from satori.infrastructure.persistence.canonical_provenance import canonical_root_available

    if not canonical_root_available(
        connection, message_id, snapshot, approved_actions=approved_actions
    ):
        raise SourcePolicyError("canonical_source_unavailable_in_scope")
    return CanonicalRootRef(
        snapshot.lineage_id, str(row[3]), str(row[4]), str(row[2]), str(row[1]), str(row[0])
    )


def _verify_policy(connection: Connection, snapshot: SourceEligibilitySnapshot) -> None:
    row = connection.exec_driver_sql(
        "SELECT installation_id,floor_id,lineage_id,policy_generation "
        "FROM source_policy_bindings WHERE identity_id=?",
        (snapshot.identity_id,),
    ).first()
    if row is None or tuple(row[:3]) != (
        snapshot.installation_id,
        snapshot.floor_id,
        snapshot.lineage_id,
    ):
        raise SourcePolicyError("source_installation_binding_mismatch")
    if row[3] != snapshot.generation:
        raise SourcePolicyError("source_policy_reconciliation_required")
    excluded = connection.exec_driver_sql(
        "SELECT message_id FROM source_excluded_roots WHERE identity_id=? LIMIT ?",
        (snapshot.identity_id, MAX_ERASURE_ROWS + 1),
    ).all()
    if len(excluded) > MAX_ERASURE_ROWS:
        raise SourcePolicyError("erasure_inventory_capacity_exceeded")
    if frozenset(str(item[0]) for item in excluded) != snapshot.excluded_message_ids:
        raise SourcePolicyError("source_policy_mirror_mismatch")
    owners = {
        str(owner): generation
        for owner, generation in connection.exec_driver_sql(
            "SELECT owner,policy_generation FROM source_owner_repairs WHERE identity_id=?",
            (snapshot.identity_id,),
        )
    }
    if any(owners.get(owner) != snapshot.generation for owner in REQUIRED_REPAIR_OWNERS):
        raise SourcePolicyError("source_owner_reconciliation_required")


def _load_store(
    connection: Connection, schema_digest: str
) -> tuple[Store, int, str, dict[str, tuple[str, ...]], str]:
    columns: dict[str, tuple[str, ...]] = {}
    ordering: dict[str, tuple[str, ...]] = {}
    row_count = byte_count = 0
    tables = sorted(
        str(row[0])
        for row in connection.exec_driver_sql("SELECT name FROM sqlite_schema WHERE type='table'")
    )
    for table in tables:
        header = connection.exec_driver_sql(f"PRAGMA table_info({_identifier(table)})").all()
        columns[table] = tuple(str(row[1]) for row in header)
        ordering[table] = tuple(
            str(row[1]) for row in sorted(header, key=lambda row: row[5]) if row[5]
        )
        if not ordering[table]:
            raise SourcePolicyError("erasure_schema_unsupported")
        count = int(
            connection.exec_driver_sql(f"SELECT COUNT(*) FROM {_identifier(table)}").scalar_one()
        )
        row_count += count
        if row_count > MAX_ERASURE_ROWS:
            raise SourcePolicyError("erasure_inventory_capacity_exceeded")
        lengths = "+".join(
            f"COALESCE(length(CAST({_identifier(column)} AS BLOB)),0)" for column in columns[table]
        )
        size = int(
            connection.exec_driver_sql(
                f"SELECT COALESCE(SUM({lengths}),0) FROM {_identifier(table)}"
            ).scalar_one()
        )
        byte_count += size
        if byte_count > MAX_ERASURE_LOGICAL_BYTES:
            raise SourcePolicyError("erasure_inventory_capacity_exceeded")
    # Every value and its SQLite scalar type participates. No public artifact
    # contains these bytes; ordering uses the exact admitted primary keys.
    digest = hashlib.sha256(schema_digest.encode("ascii"))
    retention_digest = hashlib.sha256(schema_digest.encode("ascii"))
    encoded_bytes = 0

    def absorb(value: object, *, retention: bool) -> None:
        nonlocal encoded_bytes
        payload = json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        encoded_bytes += len(payload)
        if encoded_bytes > MAX_ERASURE_LOGICAL_BYTES:
            raise SourcePolicyError("erasure_inventory_capacity_exceeded")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        if retention:
            retention_digest.update(len(payload).to_bytes(8, "big"))
            retention_digest.update(payload)

    store: Store = {}
    for table, names in columns.items():
        retention = table not in (ERASURE_PREPARATION_TABLES | ERASURE_BODY_TABLES)
        absorb([table, list(names)], retention=retention)
        selected = ",".join(map(_identifier, names))
        order = ",".join(map(_identifier, ordering[table]))
        raw_rows = connection.exec_driver_sql(
            f"SELECT {selected} FROM {_identifier(table)} ORDER BY {order}"
        )
        mapped = []
        for row in raw_rows:
            values = tuple(row)
            if any(type(value) not in {str, int, float, type(None)} for value in values):
                raise SourcePolicyError("erasure_store_value_unproven")
            absorb(values, retention=retention)
            mapped.append(dict(zip(names, values, strict=True)))
        store[table] = tuple(mapped)
    return store, row_count, digest.hexdigest(), ordering, retention_digest.hexdigest()


class SQLiteErasureInventory:
    def __init__(self, database: Database) -> None:
        self._database = database

    def resolve_root(
        self,
        message_id: str,
        *,
        counterparty_id: str,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> CanonicalRootRef:
        """Scope proof precedes receipt lookup; content and owner scans are unnecessary."""
        try:
            with _read_transaction(self._database) as connection:
                _admit_schema(connection)
                return _root(
                    connection,
                    message_id,
                    counterparty_id,
                    snapshot,
                    approved_actions=approved_actions,
                )
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, OSError, ValueError, TypeError, KeyError):
            raise SourcePolicyError("erasure_root_unavailable") from None

    def inspect(
        self,
        message_id: str,
        *,
        counterparty_id: str,
        snapshot: SourceEligibilitySnapshot,
    ) -> ErasureInventory:
        return self._inspect(((message_id, counterparty_id, None),), snapshot=snapshot)[0]

    def inspect_many(
        self,
        roots: tuple[CanonicalRootRef, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> tuple[ErasureInventory, ...]:
        """Private approved-head assessment; prove every root before one content census."""
        if not roots or len(set(roots)) != len(roots):
            raise SourcePolicyError("erasure_inventory_targets_invalid")
        return self._inspect(
            tuple((root.message_id, root.counterparty_id, root) for root in roots),
            snapshot=snapshot,
            approved_actions=approved_actions,
        )

    def _inspect(
        self,
        targets: tuple[tuple[str, str, CanonicalRootRef | None], ...],
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> tuple[ErasureInventory, ...]:
        try:
            with _read_transaction(self._database) as connection:
                schema_revision, schema_digest = _admit_schema(connection)
                roots = tuple(
                    _root(
                        connection, message_id, person, snapshot, approved_actions=approved_actions
                    )
                    for message_id, person, _expected in targets
                )
                if any(
                    expected is not None and expected != actual
                    for actual, (_message, _person, expected) in zip(roots, targets, strict=True)
                ):
                    raise SourcePolicyError("erasure_inventory_target_mismatch")
                _verify_policy(connection, snapshot)
                store, rows, digest, primary_keys, retention_digest = _load_store(
                    connection, schema_digest
                )
                if len(roots) * rows > MAX_ERASURE_ASSESSMENT_WORK:
                    raise SourcePolicyError("erasure_assessment_capacity_exceeded")
                integrity_blockers = set()
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                    integrity_blockers.add("store_foreign_key_unproven")
                from satori.infrastructure.persistence.retention_context import RetentionContext

                context = RetentionContext.for_owner_assessment(
                    store,
                    snapshot=snapshot,
                    primary_keys=primary_keys,
                    schema_digest=schema_digest,
                    store_digest=digest,
                    approved_actions=approved_actions,
                )
                context.assert_bound(store, snapshot, schema_digest, digest)
                historical = context.historical
                result = []
                for root in roots:
                    assessment = assess_dependencies(
                        store,
                        snapshot.identity_id,
                        root.message_id,
                        historical=historical,
                    )
                    routes, route_blockers, routing_digest = build_routes(
                        store, assessment, root.message_id, primary_keys, retention=context
                    )
                    result.append(
                        ErasureInventory(
                            root,
                            schema_revision,
                            schema_digest,
                            digest,
                            routing_digest,
                            routes,
                            rows,
                            len(assessment.affected),
                            len(assessment.uncertain),
                            tuple(
                                sorted(assessment.blockers | route_blockers | integrity_blockers)
                            ),
                            retention_digest,
                        )
                    )
                return tuple(result)
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, OSError, ValueError, TypeError, KeyError):
            raise SourcePolicyError("erasure_inventory_unavailable") from None
