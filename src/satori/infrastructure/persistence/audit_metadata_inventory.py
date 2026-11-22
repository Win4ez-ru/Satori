"""Scoped current-main audit census with private structural proof and safe counts."""

from sqlalchemy.exc import SQLAlchemyError

from satori.application.erasure.audit_contracts import (
    AuditFamilyCoverage,
    AuditMetadataStatus,
    AuditMetadataStatusRequest,
)
from satori.domain.audit_metadata import AuditMetadataCoverageStatus, AuditMetadataFamily
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.audit_metadata import AuditMetadataIndex
from satori.infrastructure.persistence.canonical_provenance import historical_from_store
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import (
    _admit_schema,
    _load_store,
    _read_transaction,
    _root,
    _verify_policy,
)


class SQLiteAuditMetadataInventory:
    def __init__(self, database: Database) -> None:
        self._database = database

    def status(
        self, request: AuditMetadataStatusRequest, snapshot: SourceEligibilitySnapshot
    ) -> AuditMetadataStatus:
        try:
            with _read_transaction(self._database) as connection:
                revision, digest = _admit_schema(connection)
                _root(connection, request.message_id, request.counterparty_id, snapshot)
                _verify_policy(connection, snapshot)
                store, rows, _store_digest, _keys, _retention = _load_store(connection, digest)
                index = AuditMetadataIndex.build(
                    store, historical=historical_from_store(store, snapshot)
                )
                family_counts: dict[AuditMetadataFamily, list[int]] = {}
                status_slots = {
                    AuditMetadataCoverageStatus.CERTIFIED_METADATA: 0,
                    AuditMetadataCoverageStatus.OWNED_CONTENT: 1,
                    AuditMetadataCoverageStatus.UNKNOWN: 2,
                }
                for item in index.classifications:
                    if item.family is not None:
                        family_counts.setdefault(item.family, [0, 0, 0])[
                            status_slots[item.status]
                        ] += 1
                counts = tuple(
                    AuditFamilyCoverage(
                        family=family,
                        certified_metadata_events=values[0],
                        owned_content_events=values[1],
                        unknown_events=values[2],
                    )
                    for family, values in sorted(family_counts.items())
                )
                coverage = index.coverage
                return AuditMetadataStatus(
                    schema_revision=revision,
                    generation=snapshot.generation,
                    erasure_revision=snapshot.erasure_revision,
                    body_revision=snapshot.body_revision,
                    scanned_rows=rows,
                    total_events=coverage.total_events,
                    total_bindings=index.total_binding_rows,
                    unknown_binding_rows=index.unknown_binding_rows,
                    certified_metadata_events=coverage.certified_metadata_events,
                    owned_content_events=coverage.owned_content_events,
                    unknown_events=coverage.unknown_events,
                    coverage=(
                        "complete"
                        if coverage.unknown_events == 0 and index.unknown_binding_rows == 0
                        else "partial"
                    ),
                    counts_by_family=counts,
                    safe_reason_codes=tuple(
                        sorted(
                            {
                                item.reason
                                for item in index.classifications
                                if item.reason is not None
                            }
                            | set(index.binding_unknown_reasons)
                        )
                    ),
                )
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, OSError, ValueError, TypeError, KeyError, RecursionError):
            raise SourcePolicyError("audit_metadata_inventory_unavailable") from None
