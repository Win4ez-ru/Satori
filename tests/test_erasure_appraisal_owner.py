"""EmotionManager removes only paired explanations, retaining exact numerical history."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Connection

from satori.domain.affect import AffectiveTransition, EmotionManager
from satori.domain.affect_history import validate_affective_numeric_record
from satori.domain.erasure_appraisal import (
    AppraisalBodyAction,
    AppraisalBodyProposal,
    AppraisalBodyTarget,
)
from satori.domain.source_policy import CanonicalRootRef, SourcePolicyError
from satori.infrastructure.persistence.appraisal_material import read_appraisal_material
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_body_codec import encode_body_action
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
)
from satori.infrastructure.persistence.models.erasure_bodies import ErasureBodyApplicationRow
from satori.infrastructure.persistence.models.source_policy import SourceInputRow
from satori.infrastructure.persistence.repositories.affect import SQLAlchemyAffectiveStateRepository
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate
from tests.test_stage7_affect_integration import proposal_for
from tests.test_stage11_positions_integration import create_interaction


def _appraisal_fixture(database: Database) -> tuple[AppraisalBodyAction, AffectiveTransition]:
    snapshot = activate(database)
    interaction_id = create_interaction(
        database,
        counterparty_id="alice",
        content="SYNTHETIC_APPRAISAL_INPUT",
        prefix="appraisal-owner",
        day=1,
    )
    with database.session_factory() as session:
        interaction = session.get(ConversationInteractionRow, interaction_id)
        assert interaction is not None
        message_id = session.scalar(
            select(ConversationMessageRow.message_id).where(
                ConversationMessageRow.interaction_id == interaction_id,
                ConversationMessageRow.role == "user",
            )
        )
        assert message_id is not None
        owner = SQLAlchemyAffectiveStateRepository(session)
        state = owner.get_state(snapshot.identity.identity_id)
        assert state is not None
        response = proposal_for(interaction_id)
        proposal = replace(response.proposal, reason_codes=("SYNTHETIC_PRIVATE_EXPLANATION",))
        draft = (
            EmotionManager()
            .evaluate(
                proposal,
                state,
                snapshot.personality,
                interaction_id=interaction_id,
                allowed_source_refs=(interaction_id,),
                event_time=INTERACTION_TIME + timedelta(days=1),
            )
            .transition
        )
        assert draft is not None
        transition = AffectiveTransition(
            "owner-transition",
            snapshot.identity.identity_id,
            interaction_id,
            message_id,
            "owner-transition-trace",
            draft.proposal,
            draft.before,
            draft.after,
            draft.applied_delta,
            draft.mood_delta,
            response.provider,
            response.model,
            response.appraisal_method,
            draft.after.as_of,
        )
        session.add(
            SourceInputRow(
                input_id="owner-input",
                identity_id=snapshot.identity.identity_id,
                policy_generation=0,
                proof_status="complete",
                legacy=False,
                root_message_ids=[message_id],
                payload_refs=[],
            )
        )
        session.flush()
        assert owner.apply_transition(
            transition, source_input_id="owner-input", audit_event_id="owner-audit"
        )
        session.commit()
        material = read_appraisal_material(
            session, (transition.transition_id,), require_terminal=False
        )
        target = AppraisalBodyTarget(
            transition.transition_id,
            "owner-input",
            "owner-audit",
            1,
            material.digest(transition.transition_id),
            material.digest(transition.transition_id, terminal=True),
            3,
            material.input_closure_digest(transition.transition_id),
        )
        root = CanonicalRootRef(
            identity_id=transition.identity_id,
            lineage_id="owner-lineage",
            counterparty_id="alice",
            session_id=interaction.session_id,
            interaction_id=interaction_id,
            message_id=message_id,
        )
    proposal_body = AppraisalBodyProposal(
        root, "owner-intent", "owner-request", 1, 1, 0, "a" * 64, "b" * 64, "c" * 64, (target,)
    )
    return AppraisalBodyAction(
        proposal_body,
        "owner-action",
        "owner-floor",
        "owner-installation",
        1,
        INTERACTION_TIME + timedelta(days=1),
        "owner-body-trace",
    ), transition


def _receipt(action: AppraisalBodyAction) -> ErasureBodyApplicationRow:
    return ErasureBodyApplicationRow(
        action_id=action.action_id,
        request_id=action.proposal.request_id,
        identity_id=action.proposal.root.identity_id,
        root_message_id=action.proposal.root.message_id,
        floor_id=action.floor_id,
        lineage_id=action.proposal.root.lineage_id,
        body_revision=action.result_body_revision,
        action=encode_body_action(action),
        applied_at=action.decided_at,
    )


def _apply(database: Database, action: AppraisalBodyAction) -> None:
    with database.session_factory() as session:
        session.add(_receipt(action))
        session.flush()
        SQLAlchemyAffectiveStateRepository(session).erase_explanation_bodies(
            action.proposal.targets, action_id=action.action_id
        )
        session.commit()


def test_pair_erased_actual_null_key_absent_without_numeric_rollback(
    migrated_database: Database,
) -> None:
    action, _ = _appraisal_fixture(migrated_database)
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)
    with migrated_database.session_factory() as session:
        history_before = SQLAlchemyAffectiveStateRepository(session).list_numerical_history()
    _apply(migrated_database, action)
    after = stored_rows(path)
    for table in before.keys() - {
        "affective_transitions",
        "audit_events",
        "erasure_body_applications",
    }:
        assert after[table] == before[table]
    with migrated_database.session_factory() as session:
        owner = SQLAlchemyAffectiveStateRepository(session)
        assert owner.list_transitions() == ()
        history_after = owner.list_numerical_history()
        assert len(history_after) == 1
        assert (
            replace(history_after[0], explanation_state="available", explanation_action_id=None)
            == history_before[0]
        )
        assert not hasattr(history_after[0], "proposal")
        assert owner.list_transition_headers()[0].explanation_state == "erased"
        row = (
            session.connection()
            .exec_driver_sql("SELECT reason_codes,typeof(reason_codes) FROM affective_transitions")
            .one()
        )
        assert tuple(row) == (None, "null")
        details = json.loads(
            session.connection()
            .exec_driver_sql("SELECT details FROM audit_events WHERE event_id='owner-audit'")
            .scalar_one()
        )
        assert "appraisal_reason_codes" not in details
        assert details["decision_reason_code"] == "bounded_appraisal_applied"
        assert session.connection().exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_owner_failure_between_pair_writes_rolls_back_everything(
    migrated_database: Database,
) -> None:
    action, _ = _appraisal_fixture(migrated_database)
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)

    def fail(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        if statement.startswith("UPDATE affective_transitions SET reason_codes"):
            raise RuntimeError("injected_pair_failure")

    event.listen(migrated_database.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected_pair_failure"):
            _apply(migrated_database, action)
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", fail)
    assert stored_rows(path) == before


@pytest.mark.parametrize("damage", ["body", "terminal", "scope", "input"])
def test_owner_checks_preimage_scope_and_exact_pair_before_any_write(
    migrated_database: Database, damage: str
) -> None:
    action, _ = _appraisal_fixture(migrated_database)
    target = action.proposal.targets[0]
    if damage == "body":
        target = replace(target, body_digest="d" * 64)
    elif damage == "terminal":
        target = replace(target, terminal_digest="d" * 64)
    elif damage == "input":
        target = replace(target, source_input_id="other-input")
    proposal = replace(action.proposal, targets=(target,))
    if damage == "scope":
        proposal = replace(proposal, root=replace(proposal.root, counterparty_id="bob"))
    action = replace(action, proposal=proposal)
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)
    with pytest.raises(SourcePolicyError, match="body_owner_state_changed"):
        _apply(migrated_database, action)
    assert stored_rows(path) == before


def test_replay_and_numerical_history_do_not_select_reason_body(
    migrated_database: Database,
) -> None:
    action, transition = _appraisal_fixture(migrated_database)
    _apply(migrated_database, action)
    statements: list[str] = []

    def observe(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        statements.append(statement)

    event.listen(migrated_database.engine, "before_cursor_execute", observe)
    try:
        with migrated_database.session_factory() as session:
            owner = SQLAlchemyAffectiveStateRepository(session)
            assert not owner.apply_transition(transition, audit_event_id="replayed-audit")
            assert owner.get_transition_for_interaction(transition.interaction_id) is None
            assert owner.list_numerical_history()[0].source_refs == transition.proposal.source_refs
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", observe)
    assert not any("reason_codes" in statement for statement in statements)


def test_managed_legacy_reason_is_withheld_without_loading_body(
    migrated_database: Database,
) -> None:
    action, transition = _appraisal_fixture(migrated_database)
    # No guess: a missing prospective input pointer remains unavailable in a managed read.
    with migrated_database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER affective_transitions_explanation_update")
        connection.exec_driver_sql("UPDATE affective_transitions SET source_input_id=NULL")
    statements: list[str] = []

    def observe(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        statements.append(statement)

    event.listen(migrated_database.engine, "before_cursor_execute", observe)
    try:
        with (
            patch(
                "satori.infrastructure.persistence.repositories.affect.policy_snapshot",
                return_value=object(),
            ),
            migrated_database.session_factory() as session,
        ):
            owner = SQLAlchemyAffectiveStateRepository(session)
            assert owner.get_transition_for_interaction(transition.interaction_id) is None
            assert len(owner.list_numerical_history()) == 1
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", observe)
    assert not any("reason_codes" in statement for statement in statements)
    assert action.proposal.targets[0].source_input_id == "owner-input"


@pytest.mark.parametrize(
    "damage",
    [
        "score_bool",
        "extra_score",
        "version_float",
        "duplicate_json",
        "delta_bool",
        "unknown_policy",
        "before_policy",
    ],
)
def test_retained_numerical_json_rejects_coercion_or_unclassified_fields(
    migrated_database: Database, damage: str
) -> None:
    action, _ = _appraisal_fixture(migrated_database)
    with migrated_database.session_factory() as session:
        row = dict(
            read_appraisal_material(
                session, (action.proposal.targets[0].transition_id,), require_terminal=False
            ).transitions[action.proposal.targets[0].transition_id]
        )
    if damage == "score_bool":
        scores = json.loads(str(row["appraisal_payload"]))
        scores["pleasantness"] = True
        row["appraisal_payload"] = scores
    elif damage == "extra_score":
        scores = json.loads(str(row["appraisal_payload"]))
        scores["private_explanation"] = "SYNTHETIC_UNCLASSIFIED_COPY"
        row["appraisal_payload"] = scores
    elif damage == "version_float":
        row["base_state_version"] = 1.0
    elif damage == "duplicate_json":
        row["appraisal_payload"] = '{"pleasantness":0,"pleasantness":1}'
    elif damage in {"unknown_policy", "before_policy"}:
        snapshot = json.loads(
            str(row["state_after" if damage == "unknown_policy" else "state_before"])
        )
        snapshot["emotion_policy_version"] = 2
        row["state_after" if damage == "unknown_policy" else "state_before"] = snapshot
    else:
        delta = json.loads(str(row["applied_delta"]))
        delta["interest"] = True
        row["applied_delta"] = delta
    with pytest.raises(ValueError, match="affective_history_invalid"):
        validate_affective_numeric_record(row)


@pytest.mark.parametrize(
    "attack",
    [
        "transition_replace_audit",
        "audit_duplicate",
        "audit_replace",
        "audit_delete",
        "reason_restore",
        "input_replace",
    ],
)
def test_raw_sql_cannot_replace_resurrect_or_remove_retained_pair(
    migrated_database: Database, attack: str
) -> None:
    action, _ = _appraisal_fixture(migrated_database)
    _apply(migrated_database, action)
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA recursive_triggers=OFF")
        if attack == "transition_replace_audit":
            columns = [
                row[1] for row in connection.execute("PRAGMA table_info(affective_transitions)")
            ]
            data = dict(
                zip(
                    columns,
                    connection.execute("SELECT * FROM affective_transitions").fetchone(),
                    strict=True,
                )
            )
            data.update(
                transition_id="forged-new-transition",
                interaction_id="forged-new-interaction",
                explanation_state="available",
                explanation_action_id=None,
                reason_codes='["FORGED"]',
            )
            statement = (
                "INSERT OR REPLACE INTO affective_transitions("
                + ",".join(columns)
                + ") VALUES("
                + ",".join("?" for _ in columns)
                + ")"
            )
            parameters = tuple(data[name] for name in columns)
        elif attack in {"audit_duplicate", "audit_replace"}:
            audit_data = list(
                connection.execute(
                    "SELECT * FROM audit_events WHERE event_id='owner-audit'"
                ).fetchone()
            )
            if attack == "audit_duplicate":
                audit_data[0] = "forged-duplicate-audit"
            statement = (
                "INSERT OR REPLACE INTO audit_events VALUES("
                + ",".join("?" for _ in audit_data)
                + ")"
            )
            parameters = tuple(audit_data)
        elif attack == "audit_delete":
            statement, parameters = "DELETE FROM audit_events WHERE event_id=?", ("owner-audit",)
        elif attack == "reason_restore":
            statement, parameters = (
                "UPDATE audit_events SET details=json_set(details,'$.appraisal_reason_c"
                "odes',json('[\"FORGED\"]')) WHERE event_id=?",
                ("owner-audit",),
            )
        else:
            source_data = list(
                connection.execute(
                    "SELECT * FROM source_inputs WHERE input_id='owner-input'"
                ).fetchone()
            )
            statement = (
                "INSERT OR REPLACE INTO source_inputs VALUES("
                + ",".join("?" for _ in source_data)
                + ")"
            )
            parameters = tuple(source_data)
        with pytest.raises(sqlite3.IntegrityError, match="appraisal_explanation_terminal"):
            connection.execute(statement, parameters)
        connection.rollback()
    assert stored_rows(path) == before
