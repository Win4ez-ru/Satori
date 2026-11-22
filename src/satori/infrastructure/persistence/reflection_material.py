"""Exact Reflection proposal material and its frozen retained parent closure."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from satori.domain.erasure_reflection import ReflectionBodyAction, ReflectionBodyTarget
from satori.domain.source_policy import SourcePolicyError

if TYPE_CHECKING:
    from satori.infrastructure.persistence.erasure_inventory import Row, Store


class ReadBudget(Protocol):
    def add(self, *, rows: int, logical_bytes: int = 0) -> None: ...


@dataclass(slots=True)
class ReflectionMaterialIndex:
    """One census index; shared parents and their retained rows are read once."""

    proposals: dict[str, Row]
    runs: dict[str, Row]
    sources: dict[str, tuple[Row, ...]]
    attempts: dict[str, tuple[Row, ...]]
    outcomes: dict[str, tuple[Row, ...]]

    @classmethod
    def build(cls, store: Store) -> ReflectionMaterialIndex:
        def index(table: str, key: str) -> dict[str, Row]:
            rows = store.get(table, ())
            result = {str(row[key]): row for row in rows}
            if len(result) != len(rows):
                raise SourcePolicyError("reflection_body_target_unproven")
            return result

        def group(table: str, key: str) -> dict[str, tuple[Row, ...]]:
            groups: dict[str, list[Row]] = {}
            for row in store.get(table, ()):
                groups.setdefault(str(row[key]), []).append(row)
            return {key: tuple(rows) for key, rows in groups.items()}

        return cls(
            index("reflection_proposals", "proposal_id"),
            index("reflection_runs", "run_id"),
            group("reflection_sources", "run_id"),
            group("reflection_attempts", "run_id"),
            group("reflection_outcomes", "proposal_id"),
        )

    def material(self, proposal_id: str, *, terminal: bool) -> dict[str, object]:
        row = self.proposals.get(proposal_id)
        if row is None or not {"body_state", "body_action_id", "payload"} <= row.keys():
            raise SourcePolicyError("reflection_body_target_unproven")
        run_id = str(row["run_id"])
        run = self.runs.get(run_id)
        if run is None or run.get("status") not in {"completed", "exhausted"}:
            raise SourcePolicyError("reflection_parent_not_terminal")
        proposal = dict(row)
        proposal["body_action_id"] = None
        if terminal:
            proposal["body_state"] = "erased"
            proposal["payload"] = None

        def ordered(rows: tuple[Row, ...], key: str) -> list[Row]:
            result = sorted(rows, key=lambda item: str(item[key]))
            if len({str(item[key]) for item in result}) != len(result):
                raise SourcePolicyError("reflection_body_target_unproven")
            return [dict(item) for item in result]

        return {
            "proposal": proposal,
            "run": dict(run),
            "sources": ordered(self.sources.get(run_id, ()), "source_id"),
            "attempts": ordered(self.attempts.get(run_id, ()), "attempt_id"),
            "outcomes": ordered(self.outcomes.get(proposal_id, ()), "outcome_id"),
        }

    def digest(self, proposal_id: str, *, terminal: bool = False) -> str:
        try:
            encoded = json.dumps(
                self.material(proposal_id, terminal=terminal),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        except (ValueError, TypeError, RecursionError):
            raise SourcePolicyError("reflection_body_target_unproven") from None
        return hashlib.sha256(encoded).hexdigest()

    def metadata_reference_count(self, proposal_id: str) -> int:
        row = self.proposals.get(proposal_id)
        if row is None or str(row["run_id"]) not in self.runs:
            raise SourcePolicyError("reflection_body_target_unproven")
        run_id = str(row["run_id"])
        return (
            2
            + len(self.sources.get(run_id, ()))
            + len(self.attempts.get(run_id, ()))
            + len(self.outcomes.get(proposal_id, ()))
        )


def reflection_target_terminal_proven(
    index: ReflectionMaterialIndex,
    action: ReflectionBodyAction,
    target: ReflectionBodyTarget,
) -> bool:
    row = index.proposals.get(target.proposal_id)
    run = index.runs.get(target.run_id)
    if (
        row is None
        or run is None
        or row.get("run_id") != target.run_id
        or row.get("body_state") != "erased"
        or row.get("payload") is not None
        or row.get("body_action_id") != action.action_id
        or run.get("aggregate_version") != target.run_version
    ):
        return False
    try:
        return (
            index.metadata_reference_count(target.proposal_id) == target.metadata_reference_count
            and index.digest(target.proposal_id) == target.terminal_digest
        )
    except (SourcePolicyError, ValueError, TypeError, KeyError):
        return False


def read_reflection_material(
    session: Session,
    proposal_ids: tuple[str, ...],
    *,
    require_terminal: bool = True,
    budget: ReadBudget | None = None,
) -> ReflectionMaterialIndex:
    """Read a bounded closure; ordinary proof checks SQL NULL before any payload read.

    ``require_terminal=False`` is reserved for the exclusive owner erasure preimage.
    It still requires an already terminal parent and never supplies live provider input.
    """

    ids = tuple(sorted(set(proposal_ids)))
    if len(ids) > 100_000:
        raise SourcePolicyError("body_application_capacity_exceeded")
    connection = session.connection()
    store: Store = {}
    total_rows = total_bytes = 0
    run_ids: set[str] = set()
    for offset in range(0, len(ids), 500):
        chunk = ids[offset : offset + 500]
        marks = ",".join("?" for _ in chunk)
        predicate = "p.proposal_id IN (" + marks + ") AND r.status IN ('completed','exhausted')"
        if require_terminal:
            predicate += (
                " AND p.body_state='erased' AND p.body_action_id IS NOT NULL AND p.payload IS NULL"
            )
        headers = connection.exec_driver_sql(
            "SELECT p.proposal_id,p.run_id FROM reflection_proposals p "
            "JOIN reflection_runs r ON r.run_id=p.run_id WHERE " + predicate,
            chunk,
        ).all()
        if len(headers) != len(chunk):
            raise SourcePolicyError("body_reconciliation_required")
        run_ids.update(str(row[1]) for row in headers)
    inspection = inspect(connection)
    for table, key, selected in (
        ("reflection_proposals", "proposal_id", ids),
        ("reflection_runs", "run_id", tuple(sorted(run_ids))),
        ("reflection_sources", "run_id", tuple(sorted(run_ids))),
        ("reflection_attempts", "run_id", tuple(sorted(run_ids))),
        ("reflection_outcomes", "proposal_id", ids),
    ):
        rows: list[Row] = []
        columns = tuple(str(c["name"]) for c in inspection.get_columns(table))
        lengths = "+".join(f'coalesce(length(CAST("{c}" AS BLOB)),0)' for c in columns)
        for offset in range(0, len(selected), 500):
            chunk = selected[offset : offset + 500]
            marks = ",".join("?" for _ in chunk)
            predicate = f'"{key}" IN ({marks})'
            if table == "reflection_proposals" and require_terminal:
                predicate += (
                    " AND body_state='erased' AND body_action_id IS NOT NULL AND payload IS NULL"
                )
            count, size = connection.exec_driver_sql(
                f'SELECT count(*),coalesce(sum({lengths}),0) FROM "{table}" WHERE {predicate}',
                chunk,
            ).one()
            total_rows += int(count)
            total_bytes += int(size)
            if budget is not None:
                budget.add(rows=int(count), logical_bytes=int(size))
            if total_rows > 100_000 or total_bytes > 64 * 1024 * 1024:
                raise SourcePolicyError("body_application_capacity_exceeded")
            rows.extend(
                dict(row)
                for row in connection.exec_driver_sql(
                    f'SELECT * FROM "{table}" WHERE {predicate}',
                    chunk,
                ).mappings()
            )
        store[table] = tuple(rows)
    return ReflectionMaterialIndex.build(store)
