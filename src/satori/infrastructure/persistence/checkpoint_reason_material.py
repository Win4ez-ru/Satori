"""Fixed checkpoint/restore reason proof, independent of future Personality state."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from satori.core.personality import CANONICAL_TRAIT_KEYS
from satori.domain.erasure_bodies import BodyAction
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonAction,
    CheckpointReasonKind,
    CheckpointReasonScope,
    CheckpointReasonSelector,
    CheckpointReasonTarget,
)
from satori.domain.personality import Personality, PersonalityTrait
from satori.domain.personality_evolution import PersonalityCheckpointKind, checkpoint_hash
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.audit_metadata_schema import (
    document,
    integer,
    text,
    utc_time,
)

if TYPE_CHECKING:
    from satori.infrastructure.persistence.erasure_inventory import Row, Store
    from satori.infrastructure.persistence.reflection_material import ReadBudget

REASON_TABLES = {
    CheckpointReasonKind.CHECKPOINT_APPROVAL: ("personality_checkpoint_approvals", "approval_id"),
    CheckpointReasonKind.PERSONALITY_RESTORE: ("personality_restore_events", "restore_id"),
}


def _unproven() -> SourcePolicyError:
    return SourcePolicyError("checkpoint_reason_material_unproven")


def _number(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(cast(float, value)):
        raise _unproven()
    result = float(cast(float, value))
    if not 0 <= result <= 1:
        raise _unproven()
    return result


@dataclass(slots=True)
class CheckpointReasonMaterialIndex:
    rows: dict[tuple[str, str], Row]
    checkpoints: dict[str, Row]
    traits: dict[str, tuple[Row, ...]]
    revisions: dict[str, Row]

    @classmethod
    def build(cls, store: Store) -> CheckpointReasonMaterialIndex:
        def indexed(table: str, key: str) -> dict[str, Row]:
            rows = store.get(table, ())
            result = {str(row[key]): row for row in rows}
            if len(result) != len(rows):
                raise _unproven()
            return result

        owner_rows = {
            (kind.value, key): row
            for kind, (table, column) in REASON_TABLES.items()
            for key, row in indexed(table, column).items()
        }
        traits: dict[str, list[Row]] = {}
        for row in store.get("personality_checkpoint_traits", ()):
            traits.setdefault(str(row["checkpoint_id"]), []).append(row)
        return cls(
            owner_rows,
            indexed("personality_checkpoints", "checkpoint_id"),
            {key: tuple(rows) for key, rows in traits.items()},
            indexed("personality_revisions", "revision_id"),
        )

    def check_scope(self, selector: CheckpointReasonSelector, scope: CheckpointReasonScope) -> None:
        row = self.rows.get(selector.key)
        if row is None or row.get("identity_id") != scope.identity_id:
            raise SourcePolicyError("checkpoint_reason_selector_unavailable")

    def _checkpoint(self, key: str, identity: str) -> tuple[Row, tuple[Row, ...]]:
        row = self.checkpoints.get(key)
        if row is None or row.get("identity_id") != identity:
            raise _unproven()
        if (
            integer(row.get("personality_schema_version"), minimum=1) != 1
            or integer(row.get("hash_schema_version"), minimum=1) != 1
        ):
            raise _unproven()
        integer(row.get("source_aggregate_version"), minimum=1)
        utc_time(row.get("created_at"))
        traits = tuple(sorted(self.traits.get(key, ()), key=lambda item: str(item["trait_key"])))
        if tuple(item.get("trait_key") for item in traits) != CANONICAL_TRAIT_KEYS:
            raise _unproven()
        personality = Personality(
            1,
            integer(row["source_aggregate_version"], minimum=1),
            tuple(
                PersonalityTrait(
                    str(item["trait_key"]), _number(item["value"]), _number(item["baseline_value"])
                )
                for item in traits
            ),
        )
        if checkpoint_hash(
            identity_id=identity,
            checkpoint_kind=PersonalityCheckpointKind(str(row["checkpoint_kind"])),
            personality=personality,
        ) != row.get("checkpoint_hash"):
            raise _unproven()
        return row, traits

    def material(
        self, selector: CheckpointReasonSelector, *, terminal: bool = False
    ) -> dict[str, object]:
        row = self.rows.get(selector.key)
        if row is None:
            raise _unproven()
        identity = text(row.get("identity_id"))
        state, action_id, reason = (
            row.get("reason_state"),
            row.get("reason_action_id"),
            row.get("reason"),
        )
        if state == "available":
            if action_id is not None:
                raise _unproven()
            text(reason, maximum=500)
        elif state == "erased":
            if reason is not None:
                raise _unproven()
            text(action_id)
        else:
            raise _unproven()
        record = dict(row)
        record["reason_action_id"] = None
        if terminal:
            record["reason_state"], record["reason"] = "erased", None
        if selector.kind is CheckpointReasonKind.CHECKPOINT_APPROVAL:
            checkpoint, traits = self._checkpoint(text(row.get("checkpoint_id")), identity)
            if (
                checkpoint["checkpoint_hash"] != row.get("checkpoint_hash")
                or checkpoint["source_aggregate_version"]
                != integer(row.get("expected_aggregate_version"), minimum=1)
                or checkpoint["checkpoint_kind"] == "activation"
            ):
                raise _unproven()
            utc_time(row.get("approved_at"))
            return {
                "kind": selector.kind.value,
                "record": record,
                "checkpoint": dict(checkpoint),
                "traits": [dict(item) for item in traits],
            }
        before = integer(row.get("before_aggregate_version"), minimum=1)
        after = integer(row.get("after_aggregate_version"), minimum=2)
        if after != before + 1 or row.get("source_checkpoint_id") == row.get(
            "resulting_checkpoint_id"
        ):
            raise _unproven()
        source, source_traits = self._checkpoint(text(row.get("source_checkpoint_id")), identity)
        result, result_traits = self._checkpoint(text(row.get("resulting_checkpoint_id")), identity)
        revision = self.revisions.get(text(row.get("revision_id")))
        if (
            revision is None
            or revision.get("identity_id") != identity
            or revision.get("revision_kind") != "restore"
            or integer(revision.get("policy_version"), minimum=1) != 1
            or revision.get("before_aggregate_version") != before
            or revision.get("after_aggregate_version") != after
            or revision.get("source_checkpoint_id") != row["source_checkpoint_id"]
            or revision.get("resulting_checkpoint_id") != row["resulting_checkpoint_id"]
            or integer(source["source_aggregate_version"], minimum=1) > before
            or source["checkpoint_hash"] != row.get("source_checkpoint_hash")
            or result["source_aggregate_version"] != after
            or result["checkpoint_kind"] != "restore"
            or utc_time(revision.get("occurred_at")) != utc_time(row.get("restored_at"))
        ):
            raise _unproven()
        for field in (
            "trait_key",
            "direction",
            "before_value",
            "after_value",
            "applied_delta",
            "decision_confidence",
            "rolling_trait_path",
            "lifetime_trait_path",
            "reflection_outcome_id",
        ):
            if revision.get(field) is not None:
                raise _unproven()
        for field in (
            "activation_distance_linf",
            "activation_distance_l1",
            "approved_checkpoint_distance_linf",
            "approved_checkpoint_distance_l1",
            "rolling_total_path",
            "lifetime_total_path",
        ):
            value = revision.get(field)
            if (
                type(value) not in (int, float)
                or not math.isfinite(cast(float, value))
                or cast(float, value) < 0
            ):
                raise _unproven()
        text(revision.get("reason_code"), maximum=64)
        diffs, revision_diffs = (
            document(row.get("trait_diffs")),
            document(revision.get("trait_diffs")),
        )
        if not isinstance(diffs, list) or diffs != revision_diffs or not 1 <= len(diffs) <= 15:
            raise _unproven()
        keys = []
        result_by_key = {str(item["trait_key"]): item for item in result_traits}
        for diff in diffs:
            if not isinstance(diff, dict) or set(diff) != {
                "trait_key",
                "before_value",
                "after_value",
            }:
                raise _unproven()
            key = text(diff.get("trait_key"))
            keys.append(key)
            if (
                key not in result_by_key
                or _number(diff["after_value"]) != result_by_key[key]["value"]
                or _number(diff["before_value"]) == _number(diff["after_value"])
            ):
                raise _unproven()
        if keys != sorted(set(keys)) or any(
            left["value"] != right["value"] or left["baseline_value"] != right["baseline_value"]
            for left, right in zip(source_traits, result_traits, strict=True)
        ):
            raise _unproven()
        return {
            "kind": selector.kind.value,
            "record": record,
            "revision": dict(revision),
            "checkpoints": [dict(source), dict(result)],
            "traits": [
                [dict(item) for item in source_traits],
                [dict(item) for item in result_traits],
            ],
        }

    def digest(self, selector: CheckpointReasonSelector, *, terminal: bool = False) -> str:
        try:
            material = self.material(selector, terminal=terminal)
            encoded = json.dumps(
                material, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        except (ValueError, TypeError, KeyError, RecursionError):
            raise _unproven() from None
        return hashlib.sha256(encoded).hexdigest()

    def metadata_reference_count(self, selector: CheckpointReasonSelector) -> int:
        self.material(selector)
        # A complete checkpoint has exactly fifteen canonical trait rows. Restore
        # retains one event/revision and two different full checkpoints.
        return 17 if selector.kind is CheckpointReasonKind.CHECKPOINT_APPROVAL else 34


def read_checkpoint_reason_material(
    session: Session,
    selectors: tuple[CheckpointReasonSelector, ...],
    *,
    require_terminal: bool = True,
    scope: CheckpointReasonScope | None = None,
    budget: ReadBudget | None = None,
) -> CheckpointReasonMaterialIndex:
    """Scope and SQL NULL are proved from headers before protected reason values."""
    if scope is None:
        from satori.infrastructure.persistence.source_policy import policy_snapshot

        snapshot = policy_snapshot(session)
        if snapshot is None:
            raise SourcePolicyError("checkpoint_reason_scope_required")
        scope = CheckpointReasonScope(snapshot.identity_id, snapshot.lineage_id)
    if len(selectors) > 100_000 or len({item.key for item in selectors}) != len(selectors):
        raise SourcePolicyError("body_application_capacity_exceeded")
    connection = session.connection()
    store: Store = {}
    checkpoint_ids: set[str] = set()
    revision_ids: set[str] = set()
    total_rows = total_bytes = 0
    inspection = inspect(connection)
    selected_owner_ids: dict[CheckpointReasonKind, tuple[str, ...]] = {}

    def read(
        table: str, key: str, ids: tuple[str, ...], extra: str = "", params: tuple[object, ...] = ()
    ) -> tuple[Row, ...]:
        nonlocal total_rows, total_bytes
        columns = tuple(str(c["name"]) for c in inspection.get_columns(table))
        lengths = "+".join(f'coalesce(length(CAST("{c}" AS BLOB)),0)' for c in columns)
        result: list[Row] = []
        for offset in range(0, len(ids), 500):
            chunk = ids[offset : offset + 500]
            predicate = f'"{key}" IN (' + ",".join("?" for _ in chunk) + ")" + extra
            values = (*chunk, *params)
            count, size = connection.exec_driver_sql(
                f'SELECT count(*),coalesce(sum({lengths}),0) FROM "{table}" WHERE {predicate}',
                values,
            ).one()
            total_rows += int(count)
            total_bytes += int(size)
            if budget is not None:
                budget.add(rows=int(count), logical_bytes=int(size))
            if total_rows > 100_000 or total_bytes > 64 * 1024 * 1024:
                raise SourcePolicyError("body_application_capacity_exceeded")
            result.extend(
                dict(row)
                for row in connection.exec_driver_sql(
                    f'SELECT * FROM "{table}" WHERE {predicate}', values
                ).mappings()
            )
        return tuple(result)

    for kind, (table, key) in REASON_TABLES.items():
        ids = tuple(sorted(item.record_id for item in selectors if item.kind is kind))
        selected_owner_ids[kind] = ids
        columns = {str(c["name"]) for c in inspection.get_columns(table)}
        if not {"reason_state", "reason_action_id"} <= columns:
            raise SourcePolicyError("body_application_schema_required")
        headers: list[Row] = []
        needed = (
            "checkpoint_id"
            if kind is CheckpointReasonKind.CHECKPOINT_APPROVAL
            else "revision_id,source_checkpoint_id,resulting_checkpoint_id"
        )
        for offset in range(0, len(ids), 500):
            chunk = ids[offset : offset + 500]
            predicate = f'"{key}" IN (' + ",".join("?" for _ in chunk) + ") AND identity_id=?"
            if require_terminal:
                predicate += (
                    " AND reason_state='erased' AND reason_action_id IS NOT NULL AND reason IS NULL"
                )
            batch = tuple(
                dict(row)
                for row in connection.exec_driver_sql(
                    f'SELECT "{key}",{needed} FROM "{table}" WHERE {predicate}',
                    (*chunk, scope.identity_id),
                ).mappings()
            )
            if len(batch) != len(chunk):
                raise SourcePolicyError("checkpoint_reason_selector_unavailable")
            headers.extend(batch)
        for header in headers:
            if kind is CheckpointReasonKind.CHECKPOINT_APPROVAL:
                checkpoint_ids.add(str(header["checkpoint_id"]))
            else:
                checkpoint_ids.update(
                    (str(header["source_checkpoint_id"]), str(header["resulting_checkpoint_id"]))
                )
                revision_ids.add(str(header["revision_id"]))
    selected_checkpoints = tuple(sorted(checkpoint_ids))
    store["personality_checkpoints"] = read(
        "personality_checkpoints",
        "checkpoint_id",
        selected_checkpoints,
        " AND identity_id=?",
        (scope.identity_id,),
    )
    if len(store["personality_checkpoints"]) != len(selected_checkpoints):
        raise SourcePolicyError("checkpoint_reason_selector_unavailable")
    store["personality_revisions"] = read(
        "personality_revisions",
        "revision_id",
        tuple(sorted(revision_ids)),
        " AND identity_id=?",
        (scope.identity_id,),
    )
    if len(store["personality_revisions"]) != len(revision_ids):
        raise SourcePolicyError("checkpoint_reason_selector_unavailable")
    store["personality_checkpoint_traits"] = read(
        "personality_checkpoint_traits", "checkpoint_id", selected_checkpoints
    )
    # Canonical FK ownership is also proved before reading selected reason prose.
    # A forged header identity cannot conceal a foreign checkpoint or revision.
    fixed = CheckpointReasonMaterialIndex.build(store)
    for checkpoint_id in checkpoint_ids:
        fixed._checkpoint(checkpoint_id, scope.identity_id)
    if any(row.get("identity_id") != scope.identity_id for row in fixed.revisions.values()):
        raise SourcePolicyError("checkpoint_reason_selector_unavailable")
    for kind, (table, key) in REASON_TABLES.items():
        extra = " AND identity_id=?" + (
            " AND reason_state='erased' AND reason_action_id IS NOT NULL AND reason IS NULL"
            if require_terminal
            else ""
        )
        store[table] = read(table, key, selected_owner_ids[kind], extra, (scope.identity_id,))
    index = CheckpointReasonMaterialIndex.build(store)
    for selector in selectors:
        index.check_scope(selector, scope)
        index.material(selector)
    return index


def latest_checkpoint_reasons(
    actions: tuple[BodyAction, ...],
) -> dict[tuple[str, str], tuple[CheckpointReasonAction, CheckpointReasonTarget]]:
    return {
        target.selector.key: (action, target)
        for action in actions
        if isinstance(action, CheckpointReasonAction)
        for target in action.proposal.targets
    }


def checkpoint_reason_target_terminal_proven(
    index: CheckpointReasonMaterialIndex,
    action: CheckpointReasonAction,
    target: CheckpointReasonTarget,
) -> bool:
    row = index.rows.get(target.selector.key)
    if (
        row is None
        or row.get("identity_id") != action.proposal.scope.identity_id
        or row.get("reason_state") != "erased"
        or row.get("reason") is not None
        or row.get("reason_action_id") != action.action_id
    ):
        return False
    try:
        return (
            index.digest(target.selector) == target.terminal_digest
            and index.metadata_reference_count(target.selector) == target.metadata_reference_count
        )
    except (ValueError, TypeError, KeyError, SourcePolicyError):
        return False


def validate_checkpoint_reason_projection(
    connection: Connection,
    snapshot: SourceEligibilitySnapshot,
    actions: tuple[BodyAction, ...],
    budget: ReadBudget,
    *,
    require_complete: bool = True,
) -> None:
    latest = latest_checkpoint_reasons(actions)
    scope = CheckpointReasonScope(snapshot.identity_id, snapshot.lineage_id)
    if any(action.proposal.scope != scope for action, _target in latest.values()):
        raise SourcePolicyError("body_reconciliation_required")
    actual: set[tuple[str, str]] = set()
    for kind, (table, key) in REASON_TABLES.items():
        columns = {str(c["name"]) for c in inspect(connection).get_columns(table)}
        markers = {"reason_state", "reason_action_id"}
        if not markers.intersection(columns):
            if latest:
                raise SourcePolicyError("body_application_schema_required")
            continue
        if not markers <= columns:
            raise SourcePolicyError("body_reconciliation_required")
        predicate = (
            "identity_id=? AND (reason_state!='available' OR "
            "reason_action_id IS NOT NULL OR reason IS NULL)"
        )
        count, size = connection.exec_driver_sql(
            f'SELECT count(*),coalesce(sum(length(CAST("{key}" AS BLOB))),0) '
            f'FROM "{table}" WHERE {predicate}',
            (snapshot.identity_id,),
        ).one()
        budget.add(rows=int(count), logical_bytes=int(size))
        actual.update(
            (kind.value, str(row[0]))
            for row in connection.exec_driver_sql(
                f'SELECT "{key}" FROM "{table}" WHERE {predicate}', (snapshot.identity_id,)
            )
        )
    if not actual <= latest.keys() or (require_complete and actual != latest.keys()):
        raise SourcePolicyError("body_reconciliation_required")
    if not actual:
        return
    selectors = tuple(
        CheckpointReasonSelector(CheckpointReasonKind(kind), key) for kind, key in sorted(actual)
    )
    with Session(bind=connection) as session:
        index = read_checkpoint_reason_material(session, selectors, scope=scope, budget=budget)
    approved = {
        (action.action_id, target.selector.key, target.terminal_digest): (action, target)
        for action in actions
        if isinstance(action, CheckpointReasonAction)
        for target in action.proposal.targets
    }
    for selector_key in actual:
        target = latest[selector_key][1]
        row = index.rows[selector_key]
        bound = approved.get(
            (str(row.get("reason_action_id")), selector_key, target.terminal_digest)
        )
        if bound is None or not checkpoint_reason_target_terminal_proven(index, *bound):
            raise SourcePolicyError("body_reconciliation_required")
