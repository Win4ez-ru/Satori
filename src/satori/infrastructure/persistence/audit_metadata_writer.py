"""Prospective audit sidecars appended by existing owners in their transaction."""

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from satori.domain.audit_metadata import (
    AUDIT_METADATA_FIELDS_BY_FAMILY,
    AuditBindingRef,
    AuditMetadataBinding,
    AuditMetadataFamily,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.audit_metadata_schema import (
    event_digest,
    owner_record_field,
    validate_event_shape,
)
from satori.infrastructure.persistence.models.audit_metadata import AuditMetadataBindingRow
from satori.infrastructure.persistence.models.initial_self import AuditEventRow

_PRE_POLICY_REVISIONS = frozenset(
    {
        "0002_initial_self",
        "0003_conversation_memory",
        "0004_episodic_retrieval",
        "0005_semantic_memory",
        "0006_affective_state",
        "0007_relationship_state",
        "0008_user_world_models",
        "0009_satori_positions",
        "0010_reflection_runs",
        "0011_satori_inclinations",
        "0012_personality_evolution",
        "0013_conversation_failure_reason",
        "0014_memory_scope",
        "0015_memory_lifecycle",
    }
)
_POLICY_TABLES = frozenset(
    {
        "audit_metadata_bindings",
        "source_policy_bindings",
        "source_excluded_roots",
        "source_inputs",
        "source_payload_receipts",
        "owner_source_withdrawals",
        "source_owner_repairs",
        "erasure_policy_bindings",
        "erasure_preparations",
        "erasure_body_bindings",
        "erasure_body_applications",
    }
)


def bind_metadata(
    session: Session,
    event: AuditEventRow,
    *,
    family: AuditMetadataFamily,
    identity_id: str,
    owner_record_id: str,
    source_input_id: str | None = None,
) -> AuditEventRow:
    """Keep the original event; unsupported metadata receives no certification.

    This function performs no flush, commit, legacy backfill or owner mutation.
    Admission independently proves the referenced owner, scope and exact event; a
    sidecar alone never certifies retention or grants protected-content access.
    """
    event_state = inspect(event)
    if not event_state.transient and not event_state.pending:
        # Existing or detached historical events never become prospective seals.
        # Inspect state before accessing even expired audit attributes.
        return event
    session.add(event)
    row = {column.name: getattr(event, column.name) for column in AuditEventRow.__table__.c}
    try:
        reference = AuditBindingRef(family, owner_record_id)
        details = validate_event_shape(row, family)
        field = owner_record_field(family)
        actual_owner_id = row["aggregate_id"] if field is None else details[field]
        if actual_owner_id != reference.owner_record_id:
            return event
        if family is AuditMetadataFamily.ACTIVATION and actual_owner_id != identity_id:
            return event
        binding = AuditMetadataBinding(
            event_id=event.event_id,
            identity_id=identity_id,
            reference=reference,
            event_digest=event_digest(row),
            source_input_id=source_input_id,
        )
    except (ValueError, TypeError, KeyError, RecursionError):
        return event
    connection = session.connection()
    inspection = inspect(connection)
    if not inspection.has_table("audit_metadata_bindings"):
        tables = frozenset(inspection.get_table_names())
        if (
            session.info.get("source_snapshot") is None
            and not tables.intersection(_POLICY_TABLES)
            and "alembic_version" in tables
        ):
            revisions = tuple(
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars()
            )
            if len(revisions) == 1 and revisions[0] in _PRE_POLICY_REVISIONS:
                # Historical pure core preserves its original event, not a seal.
                # A declared old revision never excuses surviving policy tables.
                return event
        from satori.infrastructure.persistence.erasure import _admit_physical_schema

        revision, _digest = _admit_physical_schema(connection)
        if revision not in {
            "0016_source_policy",
            "0017_erasure_intent",
            "0018_erasure_bodies",
            "0019_dialogue_bodies",
            "0020_knowledge_bodies",
            "0021_reflection_bodies",
            "0022_appraisal_bodies",
        }:
            raise SourcePolicyError("audit_metadata_schema_required")
        # Exact historical operator transport keeps the event UNKNOWN. There is
        # no missing-table fallback for a damaged current schema or implicit migration.
        return event
    values: dict[str, object] = {
        "event_id": binding.event_id,
        "identity_id": binding.identity_id,
        "family": binding.reference.family.value,
        "contract_version": binding.contract_version,
        "event_digest": binding.event_digest,
        "source_input_id": binding.source_input_id,
    }
    slot = AUDIT_METADATA_FIELDS_BY_FAMILY[binding.reference.family]
    if slot is not None:
        values[slot] = binding.reference.owner_record_id
    binding_row = AuditMetadataBindingRow(**values)
    binding_row.audit_event = event
    # Foreign keys alone do not order separate ORM mappers. Link only exact
    # already-pending parents, using their closed primary keys and no body reads.
    pending = tuple(session.new)
    for relationship in inspect(AuditMetadataBindingRow).relationships:
        if relationship.key == "audit_event":
            continue
        local = next(iter(relationship.local_columns))
        local_key = local.key
        if local_key is None:
            continue
        value = getattr(binding_row, local_key)
        if value is None:
            continue
        parent_type = relationship.mapper.class_
        parent_key = relationship.mapper.primary_key[0].key
        if parent_key is None:
            continue
        matches = [
            candidate
            for candidate in pending
            if isinstance(candidate, parent_type) and getattr(candidate, parent_key) == value
        ]
        if len(matches) == 1:
            setattr(binding_row, relationship.key, matches[0])
    session.add(binding_row)
    return event
