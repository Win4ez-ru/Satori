"""Shared floor/main atomicity and restart admission on synthetic production state."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection

from satori.application.erasure.body_contracts import BodyErasureCommand, BodyErasureRequest
from satori.application.erasure.knowledge_use_cases import KnowledgeBodyManager
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure_knowledge import SQLiteKnowledgeBodyStore
from satori.infrastructure.source_policy.installation import bind_installation
from tests.erasure_support import ErasureCase, create_case, provider_counts
from tests.test_erasure_dialogue_integration import dialogue_command, prepare_dialogue
from tests.test_recovery_whole_state import stored_rows


def prepare_knowledge(case: ErasureCase) -> tuple[KnowledgeBodyManager, BodyErasureRequest]:
    dialogue, request = prepare_dialogue(case)
    assert dialogue.commit(dialogue_command(dialogue, request)).main_projection_current
    case.runtime.upgrade_knowledge_policy()
    return KnowledgeBodyManager(case.runtime, SQLiteKnowledgeBodyStore(case.database)), request


def knowledge_command(
    manager: KnowledgeBodyManager, request: BodyErasureRequest, request_id: str = "knowledge-action"
) -> BodyErasureCommand:
    preview = manager.preview(request)
    assert preview.readiness == "ready", preview.blockers
    return BodyErasureCommand(
        **request.model_dump(),
        request_id=request_id,
        expected_generation=preview.generation,
        expected_erasure_revision=preview.erasure_revision,
        expected_body_revision=preview.body_revision,
        approval_digest=preview.approval_digest,
    )


@pytest.fixture
def case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "knowledge-floor")


def test_derived_semantics_null_with_exact_old_actions_and_restart(case: ErasureCase) -> None:
    manager, request = prepare_knowledge(case)
    with case.runtime.exclusive():
        old_actions = case.runtime.body_actions()
    before = stored_rows(case.path)
    with case.database.engine.connect() as connection:
        versions = tuple(
            connection.exec_driver_sql(
                "SELECT claim_id,aggregate_version FROM semantic_claims ORDER BY claim_id"
            )
        )
    calls = provider_counts(case.services)
    preview = manager.preview(request)
    assert preview.semantic_claims == 2
    assert preview.positions == preview.witness_transitions == 0
    assert stored_rows(case.path) == before
    command = knowledge_command(manager, request)
    result = manager.commit(command)
    assert result.main_projection_current
    assert result.selected_bodies_removed
    assert result.physical_erasure is False
    with case.runtime.exclusive():
        assert case.runtime.body_actions()[:2] == old_actions
    after = stored_rows(case.path)
    changed = {"semantic_claims", "erasure_body_applications", "erasure_body_bindings"}
    assert {name: rows for name, rows in before.items() if name not in changed} == {
        name: rows for name, rows in after.items() if name not in changed
    }
    with case.database.engine.connect() as connection:
        rows = tuple(
            connection.exec_driver_sql(
                "SELECT value,normalized_value,body_state,aggregate_version FROM semantic_claims"
            )
        )
        assert rows
        assert all(tuple(row[:3]) == (None, None, "erased") for row in rows)
        assert (
            tuple(
                connection.exec_driver_sql(
                    "SELECT claim_id,aggregate_version FROM semantic_claims ORDER BY claim_id"
                )
            )
            == versions
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").first() is None
    assert manager.commit(command).main_projection_current
    assert provider_counts(case.services) == calls
    restarted = create_database(str(case.database.engine.url))
    try:
        rebound = bind_installation(restarted, case.directory)
        with rebound.shared() as snapshot:
            assert snapshot.body_revision == 3
        assert (
            KnowledgeBodyManager(rebound, SQLiteKnowledgeBodyStore(restarted))
            .status(request, request_id=command.request_id)
            .main_projection_current
        )
    finally:
        restarted.dispose()


def test_mid_owner_failure_rolls_back_main_and_resumes_after_restart(case: ErasureCase) -> None:
    manager, request = prepare_knowledge(case)
    command = knowledge_command(manager, request)
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
        if statement.startswith("UPDATE semantic_claims SET"):
            writes += 1
            if writes == 2:
                assert (
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM semantic_claims WHERE value IS NULL"
                    ).scalar_one()
                    == 1
                )
                raise RuntimeError("synthetic_knowledge_owner_fault")

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
        fresh = KnowledgeBodyManager(case.runtime, SQLiteKnowledgeBodyStore(restarted))
        assert fresh.resume(request, request_id=command.request_id).main_projection_current
        bind_installation(restarted, case.directory)
    finally:
        restarted.dispose()


def test_pre_knowledge_archive_cannot_regain_body_authority(
    case: ErasureCase, tmp_path: Path
) -> None:
    manager, request = prepare_knowledge(case)
    backup = tmp_path / "before-knowledge.db"
    with sqlite3.connect(case.path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    command = knowledge_command(manager, request)
    assert manager.commit(command).main_projection_current
    restored = create_database(f"sqlite+pysqlite:///{backup}")
    try:
        with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
            bind_installation(restored, case.directory)
        fresh = KnowledgeBodyManager(case.runtime, SQLiteKnowledgeBodyStore(restored))
        assert not fresh.status(request, request_id=command.request_id).main_projection_current
        assert fresh.resume(request, request_id=command.request_id).main_projection_current
        assert fresh.status(request, request_id=command.request_id).main_projection_current
        bind_installation(restored, case.directory)
    finally:
        restored.dispose()
