"""Metadata-first census and exact terminal proof of Reflection payload bodies."""

from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from satori.domain.erasure_bodies import BodyAction
from satori.domain.erasure_reflection import ReflectionBodyAction, ReflectionBodyTarget
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.reflection_material import (
    ReadBudget,
    read_reflection_material,
    reflection_target_terminal_proven,
)


def latest_reflection(
    actions: tuple[BodyAction, ...],
) -> dict[str, tuple[ReflectionBodyAction, ReflectionBodyTarget]]:
    return {
        target.proposal_id: (action, target)
        for action in actions
        if isinstance(action, ReflectionBodyAction)
        for target in action.proposal.targets
    }


def validate_reflection_projection(
    connection: Connection,
    snapshot: SourceEligibilitySnapshot,
    actions: tuple[BodyAction, ...],
    budget: ReadBudget,
    *,
    require_complete: bool = True,
) -> None:
    """No original/restored payload is read before exact terminal metadata admission."""
    latest = latest_reflection(actions)
    columns = {str(c["name"]) for c in inspect(connection).get_columns("reflection_proposals")}
    markers = {"body_state", "body_action_id"}
    if not markers.intersection(columns):
        if latest:
            raise SourcePolicyError("body_application_schema_required")
        return
    if not markers <= columns:
        raise SourcePolicyError("body_reconciliation_required")
    predicate = (
        "r.identity_id=? AND (p.body_state!='available' OR "
        "p.body_action_id IS NOT NULL OR p.payload IS NULL)"
    )
    count, size = connection.exec_driver_sql(
        "SELECT count(*),coalesce(sum(length(CAST(p.proposal_id AS BLOB))),0) "
        "FROM reflection_proposals p JOIN reflection_runs r ON r.run_id=p.run_id WHERE "
        + predicate,
        (snapshot.identity_id,),
    ).one()
    budget.add(rows=int(count), logical_bytes=int(size))
    actual = frozenset(
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT p.proposal_id FROM reflection_proposals p "
            "JOIN reflection_runs r ON r.run_id=p.run_id WHERE " + predicate,
            (snapshot.identity_id,),
        )
    )
    if not actual <= latest.keys() or (require_complete and actual != latest.keys()):
        raise SourcePolicyError("body_reconciliation_required")
    if not actual:
        return
    with Session(bind=connection) as session:
        index = read_reflection_material(session, tuple(sorted(actual)), budget=budget)
    approved = {
        (action.action_id, target.proposal_id, target.terminal_digest): (action, target)
        for action in actions
        if isinstance(action, ReflectionBodyAction)
        for target in action.proposal.targets
    }
    for proposal_id in actual:
        _latest_action, target = latest[proposal_id]
        row = index.proposals.get(proposal_id)
        bound = approved.get(
            (str(row.get("body_action_id")) if row else "", proposal_id, target.terminal_digest)
        )
        if bound is None or not reflection_target_terminal_proven(index, *bound):
            raise SourcePolicyError("body_reconciliation_required")
