"""Identity-proved current-main census; admission precedes every protected census."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from satori.application.erasure.retention_contracts import RetentionStatusRequest
from satori.domain.retention import RetentionInventory
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import (
    ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
    _admit_schema,
    _load_store,
    _read_transaction,
    _verify_policy,
)
from satori.infrastructure.persistence.retention_census import partition_store
from satori.infrastructure.persistence.retention_context import RetentionContext
from satori.infrastructure.persistence.source_policy import verify_mirror


class SQLiteRetentionInventory:
    def __init__(self, database: Database) -> None:
        self._database = database

    def inspect(
        self, request: RetentionStatusRequest, snapshot: SourceEligibilitySnapshot
    ) -> RetentionInventory:
        try:
            with _read_transaction(self._database) as connection:
                revision, schema_digest = _admit_schema(connection)
                if revision != ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION:
                    raise SourcePolicyError("retention_schema_unsupported")
                if (
                    request.identity_id != snapshot.identity_id
                    or connection.exec_driver_sql(
                        "SELECT identity_id FROM satori_identities WHERE identity_id=?",
                        (request.identity_id,),
                    ).first()
                    is None
                ):
                    raise SourcePolicyError("retention_identity_unavailable")
                _verify_policy(connection, snapshot)
                # Keep this joined session alive through the same physical read transaction.
                # A missing runtime callback is never a protected-census bypass.
                with Session(bind=connection, autoflush=False) as session:
                    session.info["source_snapshot"] = snapshot
                    verify_mirror(session, snapshot)
                    if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                        raise SourcePolicyError("retention_store_integrity_unproven")
                    store, rows, digest, primary_keys, _retention_digest = _load_store(
                        connection, schema_digest
                    )
                    context = RetentionContext.build(
                        store,
                        snapshot=snapshot,
                        primary_keys=primary_keys,
                        schema_digest=schema_digest,
                        store_digest=digest,
                    )
                    context.assert_bound(store, snapshot, schema_digest, digest)
                    tables, reasons, classification = partition_store(store, primary_keys, context)
                    return RetentionInventory(
                        identity_id=snapshot.identity_id,
                        lineage_id=snapshot.lineage_id,
                        source_generation=snapshot.generation,
                        erasure_revision=snapshot.erasure_revision,
                        body_revision=snapshot.body_revision,
                        body_head_digest=snapshot.body_head_digest,
                        schema_revision=revision,
                        schema_digest=schema_digest,
                        store_digest=digest,
                        classification_digest=classification,
                        table_counts=tables,
                        reason_counts=reasons,
                        scanned_rows=rows,
                        work_units=context.budget.work,
                    )
        except SourcePolicyError:
            raise
        except (
            SQLAlchemyError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            RecursionError,
        ):
            raise SourcePolicyError("retention_inventory_unavailable") from None
