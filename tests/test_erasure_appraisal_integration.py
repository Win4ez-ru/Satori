"""Actual appraisal input/owner/floor continuity with synthetic transports only."""

import json
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Connection

from satori.application.erasure.appraisal_use_cases import AppraisalBodyManager
from satori.core.affect import AffectiveAppraisalRequest
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.affect_uow import SQLAlchemyAffectiveStateUnitOfWork
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure_appraisal import SQLiteAppraisalBodyStore
from satori.infrastructure.persistence.models.affect import AffectiveTransitionRow
from satori.infrastructure.persistence.models.source_policy import SourceInputRow
from satori.infrastructure.source_policy.installation import bind_installation
from tests.erasure_appraisal_support import (
    appraisal_command,
    create_appraisal_fixture,
    prepare_appraisal,
)
from tests.erasure_reflection_support import ReflectionFixture
from tests.erasure_support import stored_state
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import SECRET
from tests.test_stage4_conversation_memory import INTERACTION_TIME


def _appraisal_calls(fixture: ReflectionFixture) -> tuple[int, ...]:
    return tuple(
        len(getattr(service.talk.prepare_affect.provider, "requests", ()))
        for service in fixture.case.services
        if service.talk.prepare_affect is not None
    )


@pytest.fixture
def fixture(migrated_database: Database, tmp_path: Path) -> ReflectionFixture:
    return create_appraisal_fixture(migrated_database, tmp_path / "appraisal-floor")


def test_uncited_actual_appraisal_input_links_transition_audit_and_receipt(
    fixture: ReflectionFixture,
) -> None:
    case = fixture.case
    second_provider = case.services[1].talk.prepare_affect
    assert second_provider is not None
    requests = cast(
        list[AffectiveAppraisalRequest], getattr(second_provider.provider, "requests", [])
    )
    assert len(requests) == 1
    assert SECRET in repr(requests[0])
    with case.database.session_factory() as session:
        transition = session.scalar(
            select(AffectiveTransitionRow).where(
                AffectiveTransitionRow.interaction_id == case.derived_interaction_id
            )
        )
        assert transition is not None
        assert transition.source_refs == [case.derived_interaction_id]
        assert SECRET in repr(transition.reason_codes)
        assert transition.source_input_id is not None
        assert transition.audit_event_id is not None
        source = session.get(SourceInputRow, transition.source_input_id)
        assert source is not None
        assert source.proof_status == "complete"
        assert source.legacy is False
        assert source.payload_refs
        assert source.identity_id == transition.identity_id
    manager, request = prepare_appraisal(fixture)
    preview = manager.preview(request)
    assert preview.readiness == "ready", preview.blockers
    assert preview.appraisal_transitions == 2
    assert preview.audit_duplicates == 2
    assert preview.input_receipts == 2


def test_actual_pair_removal_retains_numeric_history_and_contentful_exclusion(
    fixture: ReflectionFixture,
) -> None:
    case = fixture.case
    manager, request = prepare_appraisal(fixture)
    history_before = case.services[1].emotion_history.execute()
    calls_before = _appraisal_calls(fixture)
    with SQLAlchemyAffectiveStateUnitOfWork(case.database.session_factory) as unit:
        # B's uncited input closure contains excluded A, so available explanations
        # are already withheld before their physical SQL representation is changed.
        assert (
            unit.affective_state.get_transition_for_interaction(case.derived_interaction_id) is None
        )
    command = appraisal_command(manager, request)
    result = manager.commit(command)
    assert result.main_projection_current
    assert result.selected_bodies_removed
    assert result.physical_erasure is False
    with case.database.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM affective_transitions WHERE reason_codes IS NULL "
                "AND explanation_state='erased'"
            ).scalar_one()
            == 2
        )
        duplicates = (
            connection.exec_driver_sql(
                "SELECT details FROM audit_events WHERE event_type='emotion.transition_applied'"
            )
            .scalars()
            .all()
        )
        assert sum("appraisal_reason_codes" not in json.loads(item) for item in duplicates) == 2
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    after = case.services[1].emotion_history.execute()
    assert len(after) == len(history_before) == 4
    before_by_id = {item.transition_id: item for item in history_before}
    for item in after:
        prior = before_by_id[item.transition_id]
        assert item.before == prior.before
        assert item.after == prior.after
        assert item.applied_delta == prior.applied_delta
        assert item.mood_delta == prior.mood_delta
        assert item.appraisal_scores == prior.appraisal_scores
        assert item.source_refs == prior.source_refs
        assert not hasattr(item, "proposal")
    status = case.services[-1].emotion_status.execute(case.scope.identity_id)
    assert status.last_transition_id == after[0].transition_id
    assert status.state.as_of == INTERACTION_TIME + timedelta(days=4)
    assert _appraisal_calls(fixture) == calls_before
    state = stored_state(case)
    assert manager.commit(command) == result
    assert manager.status(request, request_id=command.request_id) == result
    assert stored_state(case) == state
    assert SECRET not in result.model_dump_json()


def test_mid_pair_failure_floor_survives_atomic_rollback_restart_and_resume(
    fixture: ReflectionFixture,
) -> None:
    case = fixture.case
    manager, request = prepare_appraisal(fixture)
    command = appraisal_command(manager, request)
    before = stored_rows(case.path)
    writes = 0

    def fail(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        nonlocal writes
        if statement.startswith("UPDATE affective_transitions SET reason_codes"):
            writes += 1
            if writes == 2:
                raise RuntimeError("private_pair_failure")

    event.listen(case.database.engine, "before_cursor_execute", fail)
    try:
        result = manager.commit(command)
    finally:
        event.remove(case.database.engine, "before_cursor_execute", fail)
    assert writes == 2
    assert not result.main_projection_current
    assert result.phase == "body_application_pending"
    assert stored_rows(case.path) == before
    with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
        bind_installation(case.database, case.directory)
    restarted = create_database(str(case.database.engine.url))
    try:
        fresh = AppraisalBodyManager(case.runtime, SQLiteAppraisalBodyStore(restarted))
        assert fresh.resume(request, request_id=command.request_id).main_projection_current
        bind_installation(restarted, case.directory)
    finally:
        restarted.dispose()


def test_pre_appraisal_backup_remains_blocked_until_explicit_approved_resume(
    fixture: ReflectionFixture, tmp_path: Path
) -> None:
    case = fixture.case
    manager, request = prepare_appraisal(fixture)
    backup = tmp_path / "before-appraisal.db"
    with case.database.engine.connect() as connection:
        driver = connection.connection.driver_connection
        assert driver is not None
        import sqlite3

        with sqlite3.connect(backup) as destination:
            driver.backup(destination)
    result = manager.commit(appraisal_command(manager, request))
    assert result.main_projection_current
    restored = create_database(f"sqlite+pysqlite:///{backup}")
    try:
        with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
            bind_installation(restored, case.directory)
        fresh = AppraisalBodyManager(case.runtime, SQLiteAppraisalBodyStore(restored))
        status = fresh.status(request, request_id="appraisal-action")
        assert not status.main_projection_current
        assert fresh.resume(request, request_id="appraisal-action").main_projection_current
        bound = bind_installation(restored, case.directory)
        with bound.shared() as snapshot:
            assert snapshot.body_revision == result.body_revision
    finally:
        restored.dispose()
