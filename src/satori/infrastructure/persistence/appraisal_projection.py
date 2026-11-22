"""Metadata-first census of paired terminal appraisal explanations."""

from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from satori.domain.erasure_appraisal import AppraisalBodyAction, AppraisalBodyTarget
from satori.domain.erasure_bodies import BodyAction
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.appraisal_material import (
    appraisal_target_terminal_proven,
    read_appraisal_material,
)
from satori.infrastructure.persistence.reflection_material import ReadBudget


def latest_appraisal(
    actions: tuple[BodyAction, ...],
) -> dict[str, tuple[AppraisalBodyAction, AppraisalBodyTarget]]:
    return {
        target.transition_id: (action, target)
        for action in actions
        if isinstance(action, AppraisalBodyAction)
        for target in action.proposal.targets
    }


def validate_appraisal_projection(
    connection: Connection,
    snapshot: SourceEligibilitySnapshot,
    actions: tuple[BodyAction, ...],
    budget: ReadBudget,
    *,
    require_complete: bool = True,
) -> None:
    """An ACK or FK never replaces actual NULL and exact retained pair proof."""
    latest = latest_appraisal(actions)
    columns = {
        str(column["name"]) for column in inspect(connection).get_columns("affective_transitions")
    }
    markers = {"explanation_state", "explanation_action_id", "source_input_id", "audit_event_id"}
    if not markers.intersection(columns):
        if latest:
            raise SourcePolicyError("body_application_schema_required")
        return
    if not markers <= columns:
        raise SourcePolicyError("body_reconciliation_required")
    predicate = (
        "identity_id=? AND (explanation_state!='available' OR "
        "explanation_action_id IS NOT NULL OR reason_codes IS NULL)"
    )
    count, size = connection.exec_driver_sql(
        "SELECT count(*),coalesce(sum(length(CAST(transition_id AS BLOB))),0) "
        "FROM affective_transitions WHERE " + predicate,
        (snapshot.identity_id,),
    ).one()
    budget.add(rows=int(count), logical_bytes=int(size))
    headers = tuple(
        connection.exec_driver_sql(
            "SELECT transition_id,source_input_id,audit_event_id,explanation_action_id "
            "FROM affective_transitions WHERE " + predicate,
            (snapshot.identity_id,),
        )
    )
    actual = frozenset(str(row[0]) for row in headers)
    if not actual <= latest.keys() or (require_complete and actual != latest.keys()):
        raise SourcePolicyError("body_reconciliation_required")
    if not actual:
        return
    approved = {
        (action.action_id, target.transition_id, target.terminal_digest): (action, target)
        for action in actions
        if isinstance(action, AppraisalBodyAction)
        for target in action.proposal.targets
    }
    for transition_id, input_id, audit_id, action_id in headers:
        target = latest[str(transition_id)][1]
        if (
            input_id != target.source_input_id
            or audit_id != target.audit_event_id
            or (str(action_id), str(transition_id), target.terminal_digest) not in approved
        ):
            raise SourcePolicyError("body_reconciliation_required")
    with Session(bind=connection) as session:
        index = read_appraisal_material(
            session,
            tuple(sorted(actual)),
            budget=budget,
            expected_input_closure_digests={
                key: latest[key][1].input_closure_digest for key in actual
            },
        )
    for transition_id in actual:
        _action, target = latest[transition_id]
        row = index.transitions.get(transition_id)
        bound = approved.get(
            (
                str(row.get("explanation_action_id")) if row else "",
                transition_id,
                target.terminal_digest,
            )
        )
        if bound is None or not appraisal_target_terminal_proven(index, *bound):
            raise SourcePolicyError("body_reconciliation_required")
