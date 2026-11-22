"""Reflection-only destructive selection over the shared trusted body head."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.erasure_bodies import BodyAction, body_reference_count
from satori.domain.erasure_reflection import (
    MAX_REFLECTION_TARGETS,
    ReflectionBodyPlan,
    ReflectionBodyTarget,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.canonical_provenance import (
    historical_from_approved_actions,
    historical_from_store,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import (
    ERASURE_APPRAISAL_SCHEMA_REVISION,
    ERASURE_AUDIT_SCHEMA_REVISION,
    ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
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
from satori.infrastructure.persistence.reflection_material import ReflectionMaterialIndex
from satori.infrastructure.persistence.source_policy import verify_mirror


def _support_roots(store: Store, selected: set[str], material: ReflectionMaterialIndex) -> set[str]:
    nodes: dict[Ref, tuple[tuple[str, ...], tuple[Ref, ...]]] = {}
    pending: list[Ref] = []
    for row in store["source_payload_receipts"]:
        ref = str(row["kind"]), str(row["payload_id"]), int(str(row["payload_version"]))
        roots, parents = identifiers(row["root_message_ids"]), references(row["payload_refs"])
        if roots is None or parents is None:
            continue
        nodes[ref] = roots, parents
        if ref[0] == "reflection_proposal" and ref[1] in selected:
            pending.append(ref)
    visited: set[Ref] = set()
    result: set[str] = set()
    while pending:
        ref = pending.pop()
        if ref in visited:
            continue
        visited.add(ref)
        if ref not in nodes:
            raise SourcePolicyError("reflection_body_provenance_unproven")
        roots, parents = nodes[ref]
        result.update(roots)
        pending.extend(parents)
    for run_id in {
        str(material.proposals[key]["run_id"]) for key in selected if key in material.proposals
    }:
        result.update(str(row["root_message_id"]) for row in material.sources.get(run_id, ()))
    return result


class SQLiteReflectionBodyStore:
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
    ) -> ReflectionBodyPlan:
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
                validate_review_terminal_state(connection, store, snapshot, approved_actions)
                historical = (
                    historical_from_store(store, snapshot)
                    if approved_actions is None
                    else historical_from_approved_actions(store, snapshot, actions)
                )
                owners = ProvenOwners(store, snapshot.identity_id, historical=historical)
                assessment = assess_dependencies(
                    store, snapshot.identity_id, root.message_id, historical=historical
                )
                selected = {
                    key
                    for kind, key, _version in assessment.affected
                    if kind == "reflection_proposal"
                }
                blockers: set[str] = set()
                if any(
                    kind == "reflection_proposal" for kind, _key, _version in assessment.uncertain
                ):
                    blockers.add("reflection_body_provenance_unproven")
                if len(selected) > MAX_REFLECTION_TARGETS:
                    raise SourcePolicyError("reflection_target_capacity_exceeded")
                if (
                    count * (len(selected) + body_reference_count(actions))
                    > MAX_ERASURE_ASSESSMENT_WORK
                ):
                    raise SourcePolicyError("body_application_capacity_exceeded")
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                    blockers.add("store_foreign_key_unproven")
                material = ReflectionMaterialIndex.build(store)
                for source in _support_roots(store, selected, material):
                    if not owners.canonical(source):
                        blockers.add("reflection_body_provenance_unproven")
                    elif owners.root_person(source) != root.counterparty_id:
                        blockers.add("cross_counterparty_body_policy_required")
                projected_references = sum(
                    material.metadata_reference_count(key)
                    for key in selected
                    if key in material.proposals
                )
                if (
                    count * (len(selected) + projected_references + body_reference_count(actions))
                    > MAX_ERASURE_ASSESSMENT_WORK
                ):
                    raise SourcePolicyError("body_application_capacity_exceeded")
                targets: list[ReflectionBodyTarget] = []
                erased = 0
                for key in sorted(selected):
                    row = material.proposals.get(key)
                    if row is None or not owners.reflection_proven(key):
                        blockers.add("reflection_body_provenance_unproven")
                        continue
                    if row.get("body_state") == "erased":
                        erased += 1
                        continue
                    if (
                        row.get("body_state") != "available"
                        or row.get("body_action_id") is not None
                    ):
                        blockers.add("reflection_body_provenance_unproven")
                        continue
                    run_id = str(row["run_id"])
                    targets.append(
                        ReflectionBodyTarget(
                            key,
                            run_id,
                            int(str(material.runs[run_id]["aggregate_version"])),
                            1,
                            material.digest(key, terminal=False),
                            material.digest(key, terminal=True),
                            material.metadata_reference_count(key),
                        )
                    )
                metadata = sum(target.metadata_reference_count for target in targets)
                work = body_reference_count(actions) + len(targets) + metadata
                if count * work > MAX_ERASURE_ASSESSMENT_WORK:
                    raise SourcePolicyError("body_application_capacity_exceeded")
                if len(actions) >= MAX_BODY_ACTIONS or work > MAX_BODY_REFERENCES:
                    blockers.add("policy_body_capacity_exceeded")
                ordered = tuple(targets)
                try:
                    encode_targets(ordered)
                except SourcePolicyError:
                    blockers.add("policy_body_capacity_exceeded")
                if not targets:
                    blockers.add("no_available_reflection_bodies")
                run_ids = {target.run_id for target in targets}
                return ReflectionBodyPlan(
                    root=root,
                    targets=ordered,
                    store_digest=digest,
                    schema_digest=schema,
                    scanned_rows=count,
                    reflection_proposals=len(targets),
                    parent_runs=len(run_ids),
                    source_rows=sum(len(material.sources.get(key, ())) for key in run_ids),
                    attempt_rows=sum(len(material.attempts.get(key, ())) for key in run_ids),
                    outcome_rows=sum(
                        len(material.outcomes.get(t.proposal_id, ())) for t in targets
                    ),
                    metadata_references=metadata,
                    already_erased=erased,
                    blockers=tuple(sorted(blockers)),
                )
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, ValueError, TypeError, KeyError, RecursionError):
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
