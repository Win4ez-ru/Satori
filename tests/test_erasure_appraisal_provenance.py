"""Exact paired preimages/terminal skeletons and latest effective action admission."""

import copy
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.appraisal_material import read_appraisal_material
from satori.infrastructure.persistence.appraisal_projection import validate_appraisal_projection
from satori.infrastructure.persistence.erasure_appraisal import preflight_appraisal_pending
from satori.infrastructure.persistence.erasure_bodies import _ReadBudget
from tests.appraisal_material_support import (
    add_exchange,
    add_reply_receipt,
    appraisal_action,
    appraisal_snapshot,
    appraisal_store,
    erase_pair,
    historical,
    set_actual_input,
)
from tests.test_erasure_appraisal_material import _Database, _write_rows, database

# Make the reader fixture visible to pytest without granting operational floor/schema authority.
__all__ = ["database"]


def test_pending_proof_binds_the_whole_original_pair_and_does_not_mutate() -> None:
    store = appraisal_store()
    action = appraisal_action(store)
    before = copy.deepcopy(store)
    result = preflight_appraisal_pending(
        store, (action,), appraisal_snapshot(), historical=historical(store)
    )
    assert result == {action.action_id: list(action.proposal.targets)}
    assert store == before
    erase_pair(store)
    assert (
        preflight_appraisal_pending(
            store, (action,), appraisal_snapshot(), historical=historical(store)
        )
        == {}
    )


@pytest.mark.parametrize(
    "change", ["reason", "input", "numeric", "missing-audit", "wrong-action", "partial-pair"]
)
def test_pending_action_never_accepts_changed_or_partial_preimage(change: str) -> None:
    store = appraisal_store()
    action = appraisal_action(store)
    row = store["affective_transitions"][0]
    if change == "reason":
        row["reason_codes"] = '["CHANGED_REASON"]'
    elif change == "input":
        store["source_inputs"][0]["policy_generation"] = 1
    elif change == "numeric":
        row["appraisal_confidence"] = 0.5
    elif change == "missing-audit":
        store["audit_events"] = ()
    elif change == "wrong-action":
        erase_pair(store, action_id="unapproved-action")
    else:
        row.update(
            explanation_state="erased", explanation_action_id=action.action_id, reason_codes=None
        )
    with pytest.raises(SourcePolicyError):
        preflight_appraisal_pending(
            store, (action,), appraisal_snapshot(), historical=historical(store)
        )


def test_latest_reapproval_accepts_only_same_exact_earlier_terminal_skeleton(
    database: _Database,
) -> None:
    action = appraisal_action(appraisal_store())
    successor = replace(
        action,
        action_id="successor",
        result_body_revision=2,
        proposal=replace(action.proposal, request_id="next-request", expected_body_revision=1),
    )
    validate_appraisal_projection(
        database.connection, appraisal_snapshot(), (action, successor), _ReadBudget()
    )
    database.connection.exec_driver_sql("UPDATE affective_transitions SET appraisal_confidence=0.5")
    database.connection.commit()
    with pytest.raises(SourcePolicyError):
        validate_appraisal_projection(
            database.connection, appraisal_snapshot(), (action, successor), _ReadBudget()
        )


def test_changed_valid_ancestry_requires_review_even_with_same_direct_pair_and_scope() -> None:
    store = appraisal_store()
    add_exchange(store)
    add_reply_receipt(store, root_ids=("message", "message-b"))
    set_actual_input(store, roots=("message",), refs=(("assistant_reply", "interaction-b", 1),))
    action = appraisal_action(store)
    store["source_payload_receipts"][0]["root_message_ids"] = '["message"]'
    with pytest.raises(SourcePolicyError, match="appraisal_body_target_changed"):
        preflight_appraisal_pending(
            store, (action,), appraisal_snapshot(), historical=historical(store)
        )


def test_mutable_eligibility_mirror_flags_do_not_stale_immutable_ancestry_review() -> None:
    store = appraisal_store()
    add_exchange(store)
    add_reply_receipt(store, root_ids=("message", "message-b"))
    set_actual_input(store, roots=("message",), refs=(("assistant_reply", "interaction-b", 1),))
    action = appraisal_action(store)
    store["source_payload_receipts"][0].update(eligible=0, policy_generation=9)
    assert preflight_appraisal_pending(
        store, (action,), appraisal_snapshot(), historical=historical(store)
    ) == {action.action_id: list(action.proposal.targets)}


def test_sql_immutable_ancestry_mutation_is_denied_before_protected_pair_read() -> None:
    store = appraisal_store()
    add_exchange(store)
    add_reply_receipt(store, root_ids=("message", "message-b"))
    set_actual_input(store, roots=("message",), refs=(("assistant_reply", "interaction-b", 1),))
    action = appraisal_action(store)
    erase_pair(store)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        _write_rows(connection, store)
        connection.commit()
        statements: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def capture(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            statements.append(statement)

        expected = {"transition": action.proposal.targets[0].input_closure_digest}
        with Session(bind=connection) as session:
            read_appraisal_material(
                session, ("transition",), expected_input_closure_digests=expected
            )
        connection.exec_driver_sql(
            "UPDATE source_payload_receipts SET root_message_ids='[\"message\"]'"
        )
        connection.commit()
        statements.clear()
        with Session(bind=connection) as session, pytest.raises(SourcePolicyError):
            read_appraisal_material(
                session, ("transition",), expected_input_closure_digests=expected
            )
        assert not any(statement.upper().startswith("SELECT *") for statement in statements)
    engine.dispose()


def test_unapproved_terminal_or_copied_ack_cannot_authorize_reader(database: _Database) -> None:
    action = appraisal_action(appraisal_store())
    database.connection.exec_driver_sql(
        "UPDATE affective_transitions SET explanation_action_id='copied-unapproved-action'"
    )
    database.connection.commit()
    database.statements.clear()
    with pytest.raises(SourcePolicyError):
        validate_appraisal_projection(
            database.connection, appraisal_snapshot(), (action,), _ReadBudget()
        )
    assert not any(statement.upper().startswith("SELECT *") for statement in database.statements)


def test_no_head_rejects_terminal_remnant_without_materializing_payload(
    database: _Database,
) -> None:
    database.statements.clear()
    with pytest.raises(SourcePolicyError):
        validate_appraisal_projection(database.connection, appraisal_snapshot(), (), _ReadBudget())
    assert not any(statement.upper().startswith("SELECT *") for statement in database.statements)
