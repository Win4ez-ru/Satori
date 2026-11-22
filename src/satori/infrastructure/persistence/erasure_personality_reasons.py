"""Exact identity-owned checkpoint reason selection over the shared body head."""

from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.erasure_bodies import BodyAction, body_reference_count
from satori.domain.erasure_personality_reasons import (
    MAX_CHECKPOINT_REASON_TARGETS,
    CheckpointReasonAction,
    CheckpointReasonKind,
    CheckpointReasonPlan,
    CheckpointReasonScope,
    CheckpointReasonSelector,
    CheckpointReasonTarget,
)
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.checkpoint_reason_material import (
    CheckpointReasonMaterialIndex,
    checkpoint_reason_target_terminal_proven,
    latest_checkpoint_reasons,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import (
    ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
    _admit_schema,
    _load_store,
    _read_transaction,
    _verify_policy,
)
from satori.infrastructure.persistence.erasure_body_codec import decode_body_projection_rows
from satori.infrastructure.persistence.erasure_inventory import Store
from satori.infrastructure.persistence.source_policy import verify_mirror


def _scope(
    connection: Connection, identity_id: str, snapshot: SourceEligibilitySnapshot
) -> CheckpointReasonScope:
    if identity_id != snapshot.identity_id:
        raise SourcePolicyError("checkpoint_reason_scope_unavailable")
    present = connection.exec_driver_sql(
        "SELECT i.identity_id FROM satori_identities i JOIN satori_personality_states p "
        "ON p.identity_id=i.identity_id WHERE i.identity_id=?",
        (identity_id,),
    ).all()
    if len(present) != 1:
        raise SourcePolicyError("checkpoint_reason_scope_unavailable")
    _verify_policy(connection, snapshot)
    return CheckpointReasonScope(identity_id, snapshot.lineage_id)


def checkpoint_reasons_pending(
    store: Store,
    snapshot: SourceEligibilitySnapshot,
    actions: tuple[BodyAction, ...],
) -> dict[str, list[CheckpointReasonTarget]]:
    """Prove every effective target before the coordinator writes any owner row."""
    latest = latest_checkpoint_reasons(actions)
    if not latest:
        return {}
    scope = CheckpointReasonScope(snapshot.identity_id, snapshot.lineage_id)
    index = CheckpointReasonMaterialIndex.build(store)
    approved = {
        (action.action_id, target.selector.key, target.terminal_digest): (action, target)
        for action in actions
        if isinstance(action, CheckpointReasonAction)
        for target in action.proposal.targets
    }
    result: dict[str, list[CheckpointReasonTarget]] = {}
    for key, (action, target) in latest.items():
        if action.proposal.scope != scope:
            raise SourcePolicyError("body_action_unavailable_in_scope")
        index.check_scope(target.selector, scope)
        if index.metadata_reference_count(target.selector) != target.metadata_reference_count:
            raise SourcePolicyError("body_reconciliation_required")
        row = index.rows[key]
        if row.get("reason_state") == "erased":
            effective = approved.get(
                (str(row.get("reason_action_id")), key, target.terminal_digest)
            )
            if effective is None or not checkpoint_reason_target_terminal_proven(index, *effective):
                raise SourcePolicyError("body_reconciliation_required")
        elif row.get("reason_state") == "available" and row.get("reason_action_id") is None:
            if (
                index.digest(target.selector) != target.body_digest
                or index.digest(target.selector, terminal=True) != target.terminal_digest
            ):
                raise SourcePolicyError("body_target_changed_review_required")
            result.setdefault(action.action_id, []).append(target)
        else:
            raise SourcePolicyError("body_reconciliation_required")
    return result


class SQLiteCheckpointReasonBodyStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def resolve_scope(
        self,
        identity_id: str,
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> CheckpointReasonScope:
        del approved_actions  # Scope is not reconstructed from a USER action.
        try:
            with _read_transaction(self.database) as connection:
                _admit_schema(connection)
                return _scope(connection, identity_id, snapshot)
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, ValueError, TypeError, KeyError, RecursionError):
            raise SourcePolicyError("body_inventory_unavailable") from None

    def preview(
        self,
        scope: CheckpointReasonScope,
        selectors: tuple[CheckpointReasonSelector, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> CheckpointReasonPlan:
        from satori.infrastructure.persistence.erasure_bodies import validate_review_terminal_state
        from satori.infrastructure.source_policy.body_policy import (
            MAX_BODY_ACTIONS,
            MAX_BODY_REFERENCES,
            encode_targets,
        )

        keys = tuple(item.key for item in selectors)
        if not 1 <= len(selectors) <= MAX_CHECKPOINT_REASON_TARGETS or keys != tuple(
            sorted(set(keys))
        ):
            raise SourcePolicyError("checkpoint_reason_selectors_invalid")
        try:
            with _read_transaction(self.database) as connection:
                revision, schema = _admit_schema(connection)
                if revision != ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION:
                    raise SourcePolicyError("body_application_schema_required")
                if _scope(connection, scope.identity_id, snapshot) != scope:
                    raise SourcePolicyError("checkpoint_reason_scope_unavailable")
                # Prove selected identity metadata before any reason census. The
                # consistent read snapshot prevents changing this proof between reads.
                for kind in CheckpointReasonKind:
                    table, column = (
                        ("personality_checkpoint_approvals", "approval_id")
                        if kind is CheckpointReasonKind.CHECKPOINT_APPROVAL
                        else ("personality_restore_events", "restore_id")
                    )
                    ids = tuple(item.record_id for item in selectors if item.kind is kind)
                    if not ids:
                        continue
                    placeholders = ",".join("?" for _ in ids)
                    headers = connection.exec_driver_sql(
                        f'SELECT "{column}",identity_id FROM "{table}" '
                        f'WHERE "{column}" IN ({placeholders})',
                        ids,
                    ).all()
                    if len(headers) != len(ids) or any(
                        header[1] != scope.identity_id for header in headers
                    ):
                        raise SourcePolicyError("checkpoint_reason_selector_unavailable")
                with Session(bind=connection) as session:
                    verify_mirror(session, snapshot, include_body=False)
                store, count, digest, _keys, _retention = _load_store(connection, schema)
                actions = (
                    decode_body_projection_rows(store["erasure_body_applications"])
                    if approved_actions is None
                    else approved_actions
                )
                validate_review_terminal_state(connection, store, snapshot, approved_actions)
                index = CheckpointReasonMaterialIndex.build(store)
                latest = latest_checkpoint_reasons(actions)
                approved = {
                    (action.action_id, target.selector.key, target.terminal_digest): (
                        action,
                        target,
                    )
                    for action in actions
                    if isinstance(action, CheckpointReasonAction)
                    for target in action.proposal.targets
                }
                blockers: set[str] = set()
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                    blockers.add("store_foreign_key_unproven")
                targets: list[CheckpointReasonTarget] = []
                erased = 0
                newly_available: list[CheckpointReasonTarget] = []
                for selector in selectors:
                    index.check_scope(selector, scope)
                    selected_owner = index.rows[selector.key]
                    if selected_owner.get("reason_state") == "erased":
                        latest_pair = latest.get(selector.key)
                        effective = (
                            approved.get(
                                (
                                    str(selected_owner.get("reason_action_id")),
                                    selector.key,
                                    latest_pair[1].terminal_digest,
                                )
                            )
                            if latest_pair
                            else None
                        )
                        if effective is None or not checkpoint_reason_target_terminal_proven(
                            index, *effective
                        ):
                            raise SourcePolicyError("body_reconciliation_required")
                        erased += 1
                        terminal_digest = index.digest(selector)
                        targets.append(
                            CheckpointReasonTarget(
                                selector,
                                1,
                                terminal_digest,
                                terminal_digest,
                                index.metadata_reference_count(selector),
                            )
                        )
                        continue
                    try:
                        target = CheckpointReasonTarget(
                            selector,
                            1,
                            index.digest(selector),
                            index.digest(selector, terminal=True),
                            index.metadata_reference_count(selector),
                        )
                        targets.append(target)
                        newly_available.append(target)
                    except (SourcePolicyError, ValueError, TypeError, KeyError, RecursionError):
                        blockers.add("checkpoint_reason_material_unproven")
                ordered = tuple(targets)
                metadata = sum(target.metadata_reference_count for target in ordered)
                work = body_reference_count(actions) + len(ordered) + metadata
                if count * work > MAX_ERASURE_ASSESSMENT_WORK:
                    raise SourcePolicyError("body_application_capacity_exceeded")
                if len(actions) >= MAX_BODY_ACTIONS or work > MAX_BODY_REFERENCES:
                    blockers.add("policy_body_capacity_exceeded")
                try:
                    if ordered:
                        encode_targets(ordered)
                except SourcePolicyError:
                    blockers.add("policy_body_capacity_exceeded")
                if not newly_available:
                    blockers.add("no_available_checkpoint_reason_bodies")
                return CheckpointReasonPlan(
                    scope,
                    ordered,
                    digest,
                    schema,
                    count,
                    sum(
                        t.selector.kind is CheckpointReasonKind.CHECKPOINT_APPROVAL
                        for t in newly_available
                    ),
                    sum(
                        t.selector.kind is CheckpointReasonKind.PERSONALITY_RESTORE
                        for t in newly_available
                    ),
                    metadata,
                    erased,
                    tuple(sorted(blockers)),
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
