"""Knowledge-specific selection over the existing shared body transaction."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.erasure_bodies import BodyAction, body_reference_count
from satori.domain.erasure_knowledge import (
    MAX_KNOWLEDGE_TARGETS,
    KnowledgeBodyKind,
    KnowledgeBodyPlan,
    KnowledgeBodyTarget,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.canonical_provenance import (
    capture_knowledge_transitions,
    historical_from_approved_actions,
    historical_from_store,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import (
    ERASURE_APPRAISAL_SCHEMA_REVISION,
    ERASURE_AUDIT_SCHEMA_REVISION,
    ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
    ERASURE_KNOWLEDGE_SCHEMA_REVISION,
    ERASURE_REFLECTION_SCHEMA_REVISION,
    SQLiteErasureInventory,
    _admit_schema,
    _load_store,
    _read_transaction,
    _root,
    _verify_policy,
)
from satori.infrastructure.persistence.erasure_body_codec import decode_body_projection_rows
from satori.infrastructure.persistence.erasure_inventory import (
    ProvenOwners,
    Ref,
    Store,
    assess_dependencies,
    identifiers,
    references,
)
from satori.infrastructure.persistence.knowledge_material import (
    KNOWLEDGE_OWNER_SPECS,
    KnowledgeMaterialIndex,
)
from satori.infrastructure.persistence.source_policy import verify_mirror


def _support_roots(store: Store, selected: set[tuple[KnowledgeBodyKind, str]]) -> set[str]:
    """All historical actual-input ancestors and retained physical evidence count."""
    nodes: dict[Ref, tuple[tuple[str, ...], tuple[Ref, ...]]] = {}
    pending: list[Ref] = []
    selected_keys = {(kind.value, key) for kind, key in selected}
    for row in store["source_payload_receipts"]:
        ref = str(row["kind"]), str(row["payload_id"]), int(str(row["payload_version"]))
        roots, dependencies = identifiers(row["root_message_ids"]), references(row["payload_refs"])
        if roots is None or dependencies is None:
            continue  # Assessment rejects every selected uncertain dependency.
        nodes[ref] = roots, dependencies
        if ref[:2] in selected_keys:
            pending.append(ref)
    result: set[str] = set()
    visited: set[Ref] = set()
    while pending:
        ref = pending.pop()
        if ref in visited:
            continue
        visited.add(ref)
        if ref not in nodes:
            raise SourcePolicyError("knowledge_body_provenance_unproven")
        roots, dependencies = nodes[ref]
        result.update(roots)
        pending.extend(dependencies)
    for kind, spec in KNOWLEDGE_OWNER_SPECS.items():
        root_key = (
            "root_message_id" if kind is KnowledgeBodyKind.SEMANTIC_CLAIM else "source_message_id"
        )
        result.update(
            str(row[root_key])
            for row in store[spec.evidence_table]
            if (kind, str(row[spec.key])) in selected
        )
    return result


class SQLiteKnowledgeBodyStore:
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

    def preview(
        self,
        root: CanonicalRootRef,
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> KnowledgeBodyPlan:
        from satori.infrastructure.persistence.erasure_bodies import validate_review_terminal_state
        from satori.infrastructure.source_policy.body_policy import (
            MAX_BODY_ACTIONS,
            MAX_BODY_REFERENCES,
            encode_targets,
        )

        try:
            with _read_transaction(self.database) as connection:
                revision, schema = _admit_schema(connection)
                if revision not in {
                    ERASURE_KNOWLEDGE_SCHEMA_REVISION,
                    ERASURE_REFLECTION_SCHEMA_REVISION,
                    ERASURE_APPRAISAL_SCHEMA_REVISION,
                    ERASURE_AUDIT_SCHEMA_REVISION,
                    ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
                }:
                    raise SourcePolicyError("body_application_schema_required")
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
                store, count, digest, _keys, _retention = _load_store(connection, schema)
                actions = (
                    decode_body_projection_rows(store["erasure_body_applications"])
                    if approved_actions is None
                    else approved_actions
                )
                historical = (
                    historical_from_store(store, snapshot)
                    if approved_actions is None
                    else historical_from_approved_actions(store, snapshot, actions)
                )
                validate_review_terminal_state(connection, store, snapshot, approved_actions)
                owners = ProvenOwners(store, snapshot.identity_id, historical=historical)
                assessment = assess_dependencies(
                    store, snapshot.identity_id, root.message_id, historical=historical
                )
                kinds = {kind.value: kind for kind in KnowledgeBodyKind}
                selected = {
                    (kinds[kind], key)
                    for kind, key, _version in assessment.affected
                    if kind in kinds
                }
                blockers: set[str] = set()
                if any(kind in kinds for kind, _key, _version in assessment.uncertain):
                    blockers.add("knowledge_body_provenance_unproven")
                if len(selected) > MAX_KNOWLEDGE_TARGETS:
                    blockers.add("knowledge_target_capacity_exceeded")
                if (
                    count * (len(selected) + body_reference_count(actions))
                    > MAX_ERASURE_ASSESSMENT_WORK
                ):
                    raise SourcePolicyError("body_application_capacity_exceeded")
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                    blockers.add("store_foreign_key_unproven")
                for source in _support_roots(store, selected):
                    if not owners.canonical(source):
                        blockers.add("knowledge_body_provenance_unproven")
                    elif owners.root_person(source) != root.counterparty_id:
                        blockers.add("cross_counterparty_body_policy_required")
                material = KnowledgeMaterialIndex.build(store)
                targets: list[KnowledgeBodyTarget] = []
                counters = {kind: 0 for kind in KnowledgeBodyKind}
                erased = quotes = 0
                available: set[tuple[KnowledgeBodyKind, str]] = set()
                for kind, key in selected:
                    row = material.aggregates.get((kind, key))
                    if row is None or not owners.admits(
                        (kind.value, key, int(str(row["aggregate_version"])))
                    ):
                        blockers.add("knowledge_body_provenance_unproven")
                        continue
                    if row["body_state"] == "erased":
                        erased += 1
                    elif row["body_state"] == "available" and row["body_action_id"] is None:
                        available.add((kind, key))
                    else:
                        blockers.add("knowledge_body_provenance_unproven")
                transitions = (
                    capture_knowledge_transitions(
                        store,
                        root,
                        tuple(sorted(available, key=lambda key: (key[0].value, key[1]))),
                        approved_actions=actions,
                        snapshot=snapshot,
                    )
                    if not blockers
                    else ()
                )
                evidence_parent = {
                    str(row["evidence_id"]): str(row["position_id"])
                    for row in store["satori_position_evidence"]
                }
                for kind, key in available:
                    row = material.aggregates[kind, key]
                    witnesses = tuple(
                        sorted(
                            (
                                w
                                for w in transitions
                                if kind is KnowledgeBodyKind.POSITION
                                and evidence_parent.get(w.evidence_id) == key
                            ),
                            key=lambda w: (w.table.value, w.evidence_id),
                        )
                    )
                    targets.append(
                        KnowledgeBodyTarget(
                            kind,
                            key,
                            int(str(row["aggregate_version"])),
                            material.digest(kind, key, terminal=False),
                            material.digest(kind, key, terminal=True),
                            witnesses,
                        )
                    )
                    counters[kind] += 1
                    if kind is KnowledgeBodyKind.POSITION:
                        quotes += len(material.evidence.get((kind, key), ()))
                if (
                    len(actions) >= MAX_BODY_ACTIONS
                    or body_reference_count(actions) + len(targets) + len(transitions)
                    > MAX_BODY_REFERENCES
                ):
                    blockers.add("policy_body_capacity_exceeded")
                ordered_targets: tuple[KnowledgeBodyTarget, ...] = tuple(
                    sorted(targets, key=lambda t: (t.kind.value, t.aggregate_id))
                )
                try:
                    encode_targets(ordered_targets)
                except SourcePolicyError:
                    blockers.add("policy_body_capacity_exceeded")
                if not targets:
                    blockers.add("no_available_knowledge_bodies")
                return KnowledgeBodyPlan(
                    root=root,
                    targets=ordered_targets,
                    store_digest=digest,
                    schema_digest=schema,
                    scanned_rows=count,
                    semantic_claims=counters[KnowledgeBodyKind.SEMANTIC_CLAIM],
                    user_model_claims=counters[KnowledgeBodyKind.USER_MODEL_CLAIM],
                    world_model_claims=counters[KnowledgeBodyKind.WORLD_MODEL_CLAIM],
                    positions=counters[KnowledgeBodyKind.POSITION],
                    inclinations=counters[KnowledgeBodyKind.INCLINATION],
                    position_quotes=quotes,
                    witness_transitions=len(transitions),
                    already_erased=erased,
                    blockers=tuple(sorted(blockers)),
                )
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, ValueError, TypeError, KeyError):
            raise SourcePolicyError("body_inventory_unavailable") from None

    def apply(
        self,
        actions: tuple[BodyAction, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
        reviewed_store_digest: str | None = None,
    ) -> None:
        from satori.infrastructure.persistence.erasure_bodies import SQLiteBodyErasureStore

        SQLiteBodyErasureStore(self.database).apply(
            actions, snapshot=snapshot, reviewed_store_digest=reviewed_store_digest
        )

    def current(
        self, actions: tuple[BodyAction, ...], *, snapshot: SourceEligibilitySnapshot
    ) -> bool:
        from satori.infrastructure.persistence.erasure_bodies import SQLiteBodyErasureStore

        return SQLiteBodyErasureStore(self.database).current(actions, snapshot=snapshot)
