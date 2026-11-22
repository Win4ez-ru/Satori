"""Managed full-input lineage, mixed owner atomicity and restart/recovery barriers."""

from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection

from satori.application.erasure.reflection_use_cases import ReflectionBodyManager
from satori.application.reflection.use_cases import GetReflections
from satori.domain.reflection import ReflectionRunStatus
from satori.domain.source_policy import PayloadRef, SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure_reflection import SQLiteReflectionBodyStore
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from satori.infrastructure.persistence.source_policy import require_current_payloads
from satori.infrastructure.source_policy.installation import (
    bind_installation,
    installation_identity,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.erasure_reflection_support import (
    ReflectionFixture,
    create_reflection_fixture,
    prepare_reflection,
    reflection_applier,
    reflection_command,
)
from tests.erasure_support import provider_counts
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import RECALL, SECRET, _services, _turn


@pytest.fixture
def reflection_fixture(migrated_database: Database, tmp_path: Path) -> ReflectionFixture:
    return create_reflection_fixture(migrated_database, tmp_path / "reflection-floor")


def test_full_actual_input_targets_uncited_source_and_preserves_metadata_and_old_head(
    reflection_fixture: ReflectionFixture,
) -> None:
    fixture, case = reflection_fixture, reflection_fixture.case
    manager, request = prepare_reflection(fixture)
    with case.runtime.exclusive():
        prior_actions = case.runtime.body_actions()
    before = stored_rows(case.path)
    calls = provider_counts(case.services), len(fixture.provider.requests)
    preview = manager.preview(request)
    assert preview.reflection_proposals == 2
    assert preview.parent_runs == 1
    assert preview.source_rows == 4
    assert stored_rows(case.path) == before
    result = manager.commit(reflection_command(manager, request))
    assert result.main_projection_current
    assert result.selected_bodies_removed
    assert result.physical_erasure is False
    assert result.body_revision == 4
    after = stored_rows(case.path)
    changed = {"reflection_proposals", "erasure_body_applications", "erasure_body_bindings"}
    assert {k: v for k, v in before.items() if k not in changed} == {
        k: v for k, v in after.items() if k not in changed
    }
    with case.database.engine.connect() as connection:
        names = tuple(
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(reflection_proposals)")
        )
        for old, terminal in zip(
            before["reflection_proposals"], after["reflection_proposals"], strict=True
        ):
            left, right = (
                dict(zip(names, old, strict=True)),
                dict(zip(names, terminal, strict=True)),
            )
            assert right["payload"] is None
            assert right["body_state"] == "erased"
            assert {
                k: v
                for k, v in left.items()
                if k not in {"payload", "body_state", "body_action_id"}
            } == {
                k: v
                for k, v in right.items()
                if k not in {"payload", "body_state", "body_action_id"}
            }
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").first() is None
    with case.runtime.exclusive():
        assert case.runtime.body_actions()[:3] == prior_actions
    assert (provider_counts(case.services), len(fixture.provider.requests)) == calls


def test_terminal_replay_and_inspection_do_not_load_payload_or_live_generation_sources(
    reflection_fixture: ReflectionFixture,
) -> None:
    fixture, case = reflection_fixture, reflection_fixture.case
    manager, request = prepare_reflection(fixture)
    manager.commit(reflection_command(manager, request))
    seen: list[str] = []

    def capture(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        if statement.lstrip().startswith("SELECT"):
            seen.append(statement)

    event.listen(case.database.engine, "before_cursor_execute", capture)
    try:
        assert (
            reflection_applier(case.database).execute(
                fixture.run.run_id, trace_id="terminal-replay"
            )
            == fixture.run
        )
        inspected = GetReflections(
            lambda: SQLAlchemyReflectionUnitOfWork(case.database.session_factory)
        ).inspect(fixture.run.run_id, identity_id=case.scope.identity_id)
        assert inspected is not None
        assert inspected.run.status is ReflectionRunStatus.COMPLETED
        assert inspected.proposals == ()
        assert {header.proposal_id for header in inspected.unavailable_proposals} == set(
            fixture.proposal_ids
        )
    finally:
        event.remove(case.database.engine, "before_cursor_execute", capture)
    # Admission may prove NULL. Application never retrieves it as an available body.
    assert not any(
        "reflection_proposals.payload" in sql and "body_state" not in sql for sql in seen
    )
    assert len(fixture.provider.requests) == 1


def test_second_owner_write_fault_rolls_back_then_fresh_database_resumes_whole_head(
    reflection_fixture: ReflectionFixture,
) -> None:
    fixture, case = reflection_fixture, reflection_fixture.case
    manager, request = prepare_reflection(fixture)
    command = reflection_command(manager, request)
    before = stored_rows(case.path)
    writes = 0

    def fail(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        nonlocal writes
        if statement.startswith("UPDATE reflection_proposals SET"):
            writes += 1
            if writes == 2:
                assert (
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM reflection_proposals WHERE payload IS NULL"
                    ).scalar_one()
                    == 1
                )
                raise RuntimeError("synthetic_reflection_owner_fault")

    event.listen(case.database.engine, "before_cursor_execute", fail)
    try:
        result = manager.commit(command)
        assert not result.main_projection_current
        assert writes == 2
        assert stored_rows(case.path) == before
    finally:
        event.remove(case.database.engine, "before_cursor_execute", fail)
    restarted = create_database(str(case.database.engine.url))
    try:
        with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
            bind_installation(restarted, case.directory)
        runtime = SourcePolicyRuntime(case.directory, installation_identity(restarted))
        resumed = ReflectionBodyManager(runtime, SQLiteReflectionBodyStore(restarted)).resume(
            request, request_id=command.request_id
        )
        assert resumed.main_projection_current
        runtime = bind_installation(restarted, case.directory)
        with runtime.shared() as snapshot:
            assert snapshot.body_revision == 4
    finally:
        restarted.dispose()


def test_old_payload_version_cannot_pass_dispatch_or_commit_after_terminal_removal(
    reflection_fixture: ReflectionFixture,
) -> None:
    fixture, case = reflection_fixture, reflection_fixture.case
    manager, request = prepare_reflection(fixture)
    manager.commit(reflection_command(manager, request))
    with case.runtime.shared() as snapshot, case.database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        for proposal_id in fixture.proposal_ids:
            with pytest.raises(SourcePolicyError):
                require_current_payloads(
                    session, (PayloadRef("reflection_proposal", proposal_id, 1),)
                )


def test_restart_retrieval_and_actual_generation_receive_no_forbidden_knowledge(
    reflection_fixture: ReflectionFixture,
) -> None:
    fixture, case = reflection_fixture, reflection_fixture.case
    manager, request = prepare_reflection(fixture)
    manager.commit(reflection_command(manager, request))
    restarted = create_database(str(case.database.engine.url))
    try:
        services, provider, _ = _services(
            restarted, case.directory, "retained-restart-probe", 8, form=False
        )
        reply = _turn(services, RECALL, "retained-restart-probe")
        assert SECRET not in reply.text
        assert len(provider.requests) == 1
        assert SECRET not in repr(provider.requests[0].messages)
        assert (
            reflection_applier(restarted).execute(
                fixture.run.run_id, trace_id="restart-terminal-replay"
            )
            == fixture.run
        )
    finally:
        restarted.dispose()


def test_pending_resume_reproves_full_upstream_content_hash_before_any_owner_write(
    reflection_fixture: ReflectionFixture,
) -> None:
    fixture, case = reflection_fixture, reflection_fixture.case
    manager, request = prepare_reflection(fixture)
    command = reflection_command(manager, request)

    def interrupt(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        if statement.startswith("UPDATE reflection_proposals SET"):
            raise RuntimeError("synthetic_floor_committed_before_main")

    event.listen(case.database.engine, "before_cursor_execute", interrupt)
    try:
        assert not manager.commit(command).main_projection_current
    finally:
        event.remove(case.database.engine, "before_cursor_execute", interrupt)
    # Keep proposal, parent and canonical root exact, but alter a still available
    # upstream quote. Its old Reflection descriptor must no longer prove admission.
    with case.database.engine.begin() as connection:
        evidence_id = connection.exec_driver_sql(
            "SELECT e.evidence_id FROM memory_evidence e "
            "JOIN reflection_sources s ON s.evidence_edge_id=e.evidence_id "
            "WHERE s.run_id=? AND e.body_state='available' ORDER BY e.evidence_id LIMIT 1",
            (fixture.run.run_id,),
        ).scalar_one()
        connection.exec_driver_sql(
            "UPDATE memory_evidence SET quote=substr(quote,1,length(quote)-1) WHERE evidence_id=?",
            (evidence_id,),
        )
    before = stored_rows(case.path)
    assert not manager.resume(request, request_id=command.request_id).main_projection_current
    assert stored_rows(case.path) == before
