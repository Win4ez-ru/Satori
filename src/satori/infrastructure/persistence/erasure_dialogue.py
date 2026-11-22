"""Bounded InteractionLog selection; shared coordinator owns transaction orchestration."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.erasure_bodies import BodyAction, BodyErasureAction, body_reference_count
from satori.domain.erasure_dialogue import (
    MAX_DIALOGUE_TARGETS,
    DialogueBodyPlan,
    DialogueBodyTarget,
    HistoricalEvidenceWitness,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.canonical_provenance import (
    capture_historical_witnesses,
    dialogue_message_digest,
    historical_from_store,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import (
    ERASURE_APPRAISAL_SCHEMA_REVISION,
    ERASURE_AUDIT_SCHEMA_REVISION,
    ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
    ERASURE_DIALOGUE_SCHEMA_REVISION,
    ERASURE_KNOWLEDGE_SCHEMA_REVISION,
    ERASURE_REFLECTION_SCHEMA_REVISION,
    SQLiteErasureInventory,
    _admit_schema,
    _load_store,
    _read_transaction,
    _root,
    _verify_policy,
)
from satori.infrastructure.persistence.erasure_inventory import ProvenOwners, assess_dependencies
from satori.infrastructure.persistence.source_policy import verify_mirror


class SQLiteDialogueBodyStore:
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
    ) -> DialogueBodyPlan:
        try:
            with _read_transaction(self.database) as connection:
                revision, schema = _admit_schema(connection)
                if revision not in {
                    ERASURE_DIALOGUE_SCHEMA_REVISION,
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
                from satori.infrastructure.persistence.canonical_provenance import (
                    historical_from_approved_actions,
                )

                historical = (
                    historical_from_store(store, snapshot)
                    if approved_actions is None
                    else historical_from_approved_actions(store, snapshot, approved_actions)
                )
                from satori.infrastructure.persistence.erasure_bodies import (
                    validate_review_terminal_state,
                )

                validate_review_terminal_state(connection, store, snapshot, approved_actions)
                owners = ProvenOwners(store, snapshot.identity_id, historical=historical)
                assessment = assess_dependencies(
                    store, snapshot.identity_id, root.message_id, historical=historical
                )
                blockers: set[str] = set()
                if any(kind == "assistant_reply" for kind, _id, _version in assessment.uncertain):
                    blockers.add("dialogue_body_provenance_unproven")
                if any(kind == "episode" for kind, _id, _version in assessment.uncertain):
                    blockers.add("episode_body_provenance_unproven")
                episodes = {key for kind, key, _version in assessment.affected if kind == "episode"}
                for row in store["episodic_memories"]:
                    if str(row["memory_id"]) in episodes and row["body_state"] != "erased":
                        blockers.add("episode_body_prerequisite_required")
                selected_interactions = {root.interaction_id} | {
                    key for kind, key, _version in assessment.affected if kind == "assistant_reply"
                }
                pairs: dict[str, list[dict[str, object]]] = {}
                for message in store["conversation_messages"]:
                    pairs.setdefault(str(message["interaction_id"]), []).append(message)
                if any(
                    sorted((str(m["role"]), int(str(m["sequence"]))) for m in pairs.get(key, ()))
                    != [("assistant", 2), ("user", 1)]
                    or any(
                        m["session_id"] != owners.interactions[key]["session_id"]
                        for m in pairs.get(key, ())
                    )
                    for key in selected_interactions
                ):
                    blockers.add("dialogue_body_provenance_unproven")
                selected = [
                    row
                    for row in store["conversation_messages"]
                    if row["message_id"] == root.message_id
                    or (
                        row["role"] == "assistant"
                        and str(row["interaction_id"]) in selected_interactions
                    )
                ]
                if len(selected) > MAX_DIALOGUE_TARGETS:
                    blockers.add("dialogue_target_capacity_exceeded")
                if (
                    count
                    * (
                        len(selected)
                        + body_reference_count(
                            self._actions(store) if approved_actions is None else approved_actions
                        )
                    )
                    > MAX_ERASURE_ASSESSMENT_WORK
                ):
                    raise SourcePolicyError("body_application_capacity_exceeded")
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                    blockers.add("store_foreign_key_unproven")
                actions = self._actions(store) if approved_actions is None else approved_actions
                witnesses: tuple[HistoricalEvidenceWitness, ...] = ()
                if owners.messages[root.message_id]["body_state"] == "available":
                    try:
                        witnesses = capture_historical_witnesses(
                            store,
                            root,
                            approved_episode_actions=tuple(
                                a for a in actions if isinstance(a, BodyErasureAction)
                            ),
                            approved_actions=actions,
                            snapshot=snapshot,
                        )
                    except SourcePolicyError as error:
                        blockers.add(error.reason_code)
                if count * (len(selected) + len(witnesses)) > MAX_ERASURE_ASSESSMENT_WORK:
                    raise SourcePolicyError("body_application_capacity_exceeded")
                targets: list[DialogueBodyTarget] = []
                users = assistants = erased = 0
                for row in selected:
                    interaction = owners.interactions.get(str(row["interaction_id"]))
                    scope = (
                        owners.sessions.get(str(interaction["session_id"])) if interaction else None
                    )
                    if (
                        scope is None
                        or scope["identity_id"] != root.identity_id
                        or scope["counterparty_id"] != root.counterparty_id
                        or not scope["scope_verified"]
                    ):
                        blockers.add("cross_counterparty_body_policy_required")
                    if row["role"] == "assistant" and not owners.admits(
                        ("assistant_reply", str(row["interaction_id"]), 1)
                    ):
                        blockers.add("dialogue_body_provenance_unproven")
                    if row["body_state"] == "erased":
                        erased += 1
                        continue
                    if (
                        row["body_state"] != "available"
                        or not isinstance(row["content"], str)
                        or not row["content"].strip()
                    ):
                        blockers.add("dialogue_body_provenance_unproven")
                        continue
                    targets.append(
                        DialogueBodyTarget(
                            str(row["message_id"]),
                            int(str(row["schema_version"])),
                            dialogue_message_digest(row, terminal=False),
                            dialogue_message_digest(row, terminal=True),
                            witnesses if row["message_id"] == root.message_id else (),
                        )
                    )
                    if row["role"] == "user":
                        users += 1
                    else:
                        assistants += 1
                from satori.infrastructure.source_policy.body_policy import (
                    MAX_BODY_ACTIONS,
                    MAX_BODY_REFERENCES,
                    encode_targets,
                )

                if (
                    len(actions) >= MAX_BODY_ACTIONS
                    or body_reference_count(actions)
                    + len(targets)
                    + sum(len(t.witnesses) for t in targets)
                    > MAX_BODY_REFERENCES
                ):
                    blockers.add("policy_body_capacity_exceeded")
                try:
                    encode_targets(tuple(targets))
                except SourcePolicyError:
                    blockers.add("policy_body_capacity_exceeded")
                if not targets:
                    blockers.add("no_available_dialogue_bodies")
                return DialogueBodyPlan(
                    root,
                    tuple(sorted(targets, key=lambda t: t.message_id)),
                    digest,
                    schema,
                    count,
                    users,
                    assistants,
                    erased,
                    tuple(sorted(blockers)),
                )
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, ValueError, TypeError, KeyError):
            raise SourcePolicyError("body_inventory_unavailable") from None

    @staticmethod
    def _actions(store: dict[str, tuple[dict[str, object], ...]]) -> tuple[BodyAction, ...]:
        from satori.infrastructure.persistence.erasure_body_codec import decode_body_projection_rows

        return decode_body_projection_rows(store["erasure_body_applications"])

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
