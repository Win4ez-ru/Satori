"""Paired explanation disposition through complete actual appraisal input closure."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.erasure_appraisal import (
    MAX_APPRAISAL_TARGETS,
    AppraisalBodyAction,
    AppraisalBodyPlan,
    AppraisalBodyTarget,
)
from satori.domain.erasure_bodies import BodyAction, body_reference_count
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.appraisal_material import (
    AppraisalMaterialIndex,
    appraisal_input_dependencies,
    appraisal_target_terminal_proven,
)
from satori.infrastructure.persistence.appraisal_projection import latest_appraisal
from satori.infrastructure.persistence.canonical_provenance import (
    HistoricalProvenance,
    historical_from_approved_actions,
    historical_from_store,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import (
    ERASURE_APPRAISAL_SCHEMA_REVISION,
    ERASURE_AUDIT_SCHEMA_REVISION,
    ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
    SQLiteErasureInventory,
    _admit_schema,
    _load_store,
    _read_transaction,
    _root,
    _verify_policy,
)
from satori.infrastructure.persistence.erasure_body_codec import decode_body_projection_rows
from satori.infrastructure.persistence.erasure_inventory import ProvenOwners, Ref, Row, Store
from satori.infrastructure.persistence.source_policy import verify_mirror


@dataclass(slots=True)
class AppraisalInputClosure:
    """One bounded owner graph; provider citations never stand in for actual inputs."""

    owners: ProvenOwners
    inputs: dict[str, Row]
    parents: dict[Ref, tuple[Ref, ...]]
    children: dict[Ref, set[Ref]]
    roots: dict[Ref, tuple[str, ...]]
    valid: dict[Ref, bool]
    people: dict[Ref, frozenset[str]]
    affected_cache: dict[str, frozenset[Ref]]

    @classmethod
    def build(
        cls, store: Store, identity_id: str, historical: HistoricalProvenance
    ) -> AppraisalInputClosure:
        owners = ProvenOwners(store, identity_id, historical=historical)
        result = cls(owners, {}, {}, {}, {}, {}, {}, {})
        for row in store["source_inputs"]:
            key = str(row["input_id"])
            if key in result.inputs:
                raise SourcePolicyError("appraisal_body_provenance_unproven")
            result.inputs[key] = row
        for row in store["source_payload_receipts"]:
            kind, payload_id, version = (
                row.get("kind"),
                row.get("payload_id"),
                row.get("payload_version"),
            )
            if (
                type(kind) is not str
                or type(payload_id) is not str
                or type(version) is not int
                or version < 1
            ):
                raise SourcePolicyError("appraisal_body_provenance_unproven")
            ref = kind, payload_id, version
            if ref in result.parents:
                raise SourcePolicyError("appraisal_body_provenance_unproven")
            try:
                roots, parents = appraisal_input_dependencies(row)
            except SourcePolicyError:
                roots, parents = (), ()
                decoded = False
            else:
                decoded = True
            proven = (
                row.get("identity_id") == identity_id
                and row.get("proof_status") == "complete"
                and row.get("legacy") in (False, 0)
                and decoded
            )
            try:
                proven = proven and owners.admits(ref)
            except (ValueError, TypeError, KeyError, RecursionError):
                proven = False
            result.roots[ref] = roots or ()
            result.parents[ref] = parents or ()
            result.valid[ref] = proven
            result.people[ref] = frozenset()
            for parent in set(parents or ()):
                result.children.setdefault(parent, set()).add(ref)
        remaining = {ref: len(set(parents)) for ref, parents in result.parents.items()}
        queue = deque(ref for ref, count in remaining.items() if count == 0)
        visited: set[Ref] = set()
        while queue:
            ref = queue.popleft()
            visited.add(ref)
            persons: set[str] = set()
            for root in result.roots[ref]:
                if not result.canonical(root):
                    result.valid[ref] = False
                else:
                    persons.add(str(owners.root_person(root)))
            for parent in set(result.parents[ref]):
                if parent not in visited or not result.valid.get(parent, False):
                    result.valid[ref] = False
                persons.update(result.people.get(parent, ()))
            result.people[ref] = frozenset(persons)
            if len(persons) != 1:
                result.valid[ref] = False
            for child in result.children.get(ref, ()):
                remaining[child] -= 1
                if remaining[child] == 0:
                    queue.append(child)
        for ref in result.parents.keys() - visited:
            result.valid[ref] = False
        return result

    def canonical(self, message_id: str) -> bool:
        if not self.owners.canonical(message_id):
            return False
        message = self.owners.messages[message_id]
        interaction = self.owners.interactions[str(message["interaction_id"])]
        session = self.owners.sessions[str(message["session_id"])]
        return all(row.get("schema_version") == 1 for row in (message, interaction, session))

    def affected(self, message_id: str) -> frozenset[Ref]:
        if message_id not in self.affected_cache:
            reached = {ref for ref, roots in self.roots.items() if message_id in roots}
            queue = deque(reached)
            while queue:
                for child in self.children.get(queue.popleft(), ()):
                    if child not in reached:
                        reached.add(child)
                        queue.append(child)
            self.affected_cache[message_id] = frozenset(reached)
        return self.affected_cache[message_id]

    def prove(self, transition: Row, root: CanonicalRootRef) -> tuple[bool, bool, frozenset[str]]:
        source = self.inputs.get(str(transition.get("source_input_id")))
        if (
            source is None
            or source.get("identity_id") != root.identity_id
            or (source.get("proof_status") != "complete" or source.get("legacy") not in (False, 0))
        ):
            return False, False, frozenset()
        try:
            roots, refs = appraisal_input_dependencies(source)
        except SourcePolicyError:
            return False, False, frozenset()
        if transition.get("source_message_id") not in roots:
            return False, False, frozenset()
        current = str(transition.get("source_message_id"))
        if not self.canonical(current) or self.owners.messages[current].get(
            "interaction_id"
        ) != transition.get("interaction_id"):
            return False, False, frozenset()
        valid = True
        people: set[str] = set()
        for message_id in roots:
            if not self.canonical(message_id):
                valid = False
            else:
                people.add(str(self.owners.root_person(message_id)))
        for ref in refs:
            valid = valid and self.valid.get(ref, False)
            people.update(self.people.get(ref, ()))
        reached = root.message_id in roots or any(
            ref in self.affected(root.message_id) for ref in refs
        )
        return valid and len(people) == 1, reached, frozenset(people)


def preflight_appraisal_pending(
    store: Store,
    actions: tuple[BodyAction, ...],
    snapshot: SourceEligibilitySnapshot,
    *,
    historical: HistoricalProvenance,
) -> dict[str, list[AppraisalBodyTarget]]:
    """Privileged exact before/terminal proof, completed before any owner mutation."""
    latest = latest_appraisal(actions)
    if not latest:
        return {}
    if (
        sum(len(rows) for rows in store.values()) * body_reference_count(actions)
        > MAX_ERASURE_ASSESSMENT_WORK
    ):
        raise SourcePolicyError("body_application_capacity_exceeded")
    material = AppraisalMaterialIndex.build(store)
    closure = AppraisalInputClosure.build(store, snapshot.identity_id, historical)
    approved = {
        (action.action_id, target.transition_id, target.terminal_digest): (action, target)
        for action in actions
        if isinstance(action, AppraisalBodyAction)
        for target in action.proposal.targets
    }
    pending: dict[str, list[AppraisalBodyTarget]] = {}
    for transition_id, (action, target) in latest.items():
        row = material.transitions.get(transition_id)
        if row is None or (
            row.get("identity_id") != snapshot.identity_id
            or row.get("source_input_id") != target.source_input_id
            or row.get("audit_event_id") != target.audit_event_id
            or target.payload_version != 1
            or material.metadata_reference_count(transition_id) != target.metadata_reference_count
        ):
            raise SourcePolicyError("appraisal_body_target_changed")
        proven, affected, people = closure.prove(row, action.proposal.root)
        if not proven or not affected or people != {action.proposal.root.counterparty_id}:
            raise SourcePolicyError("appraisal_body_provenance_unproven")
        if material.input_closure_digest(transition_id) != target.input_closure_digest:
            raise SourcePolicyError("appraisal_body_target_changed")
        if row.get("explanation_state") == "erased":
            bound = approved.get(
                (str(row.get("explanation_action_id")), transition_id, target.terminal_digest)
            )
            if bound is None or not appraisal_target_terminal_proven(material, *bound):
                raise SourcePolicyError("appraisal_body_target_changed")
            continue
        if (
            row.get("explanation_state") != "available"
            or row.get("explanation_action_id") is not None
            or material.digest(transition_id) != target.body_digest
            or material.digest(transition_id, terminal=True) != target.terminal_digest
        ):
            raise SourcePolicyError("appraisal_body_target_changed")
        pending.setdefault(action.action_id, []).append(target)
    return pending


class SQLiteAppraisalBodyStore:
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
    ) -> AppraisalBodyPlan:
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
                material = AppraisalMaterialIndex.build(store)
                closure = AppraisalInputClosure.build(store, snapshot.identity_id, historical)
                selected: list[str] = []
                blockers: set[str] = set()
                for key, row in material.transitions.items():
                    if row.get("identity_id") != snapshot.identity_id:
                        continue
                    valid, affected, people = closure.prove(row, root)
                    if not valid:
                        blockers.add("appraisal_body_provenance_unproven")
                    elif affected:
                        if people != {root.counterparty_id}:
                            blockers.add("cross_counterparty_body_policy_required")
                        else:
                            selected.append(key)
                if len(selected) > MAX_APPRAISAL_TARGETS:
                    raise SourcePolicyError("appraisal_target_capacity_exceeded")
                work = body_reference_count(actions) + 4 * len(selected)
                if count * work > MAX_ERASURE_ASSESSMENT_WORK:
                    raise SourcePolicyError("body_application_capacity_exceeded")
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                    blockers.add("store_foreign_key_unproven")
                targets: list[AppraisalBodyTarget] = []
                erased = 0
                for key in sorted(selected):
                    row = material.transitions[key]
                    try:
                        material.material(key, terminal=False)
                        if row.get("explanation_state") == "erased":
                            erased += 1
                            continue
                        targets.append(
                            AppraisalBodyTarget(
                                key,
                                str(row["source_input_id"]),
                                str(row["audit_event_id"]),
                                1,
                                material.digest(key),
                                material.digest(key, terminal=True),
                                material.metadata_reference_count(key),
                                material.input_closure_digest(key),
                            )
                        )
                    except SourcePolicyError as error:
                        blockers.add(error.reason_code)
                if len(actions) >= MAX_BODY_ACTIONS or work > MAX_BODY_REFERENCES:
                    blockers.add("policy_body_capacity_exceeded")
                ordered = tuple(targets)
                try:
                    encode_targets(ordered)
                except SourcePolicyError:
                    blockers.add("policy_body_capacity_exceeded")
                if not targets:
                    blockers.add("no_available_appraisal_explanations")
                return AppraisalBodyPlan(
                    root=root,
                    targets=ordered,
                    store_digest=digest,
                    schema_digest=schema,
                    scanned_rows=count,
                    appraisal_transitions=len(targets),
                    audit_duplicates=len(targets),
                    input_receipts=len({target.source_input_id for target in targets}),
                    metadata_references=sum(target.metadata_reference_count for target in targets),
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
