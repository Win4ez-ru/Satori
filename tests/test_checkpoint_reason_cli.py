"""Actual identity controls bypass cognitive composition and preserve read-only bytes."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import satori.__main__ as cli
from satori.config import Environment, LogLevel, Settings
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.repositories.personality import (
    SQLAlchemyPersonalityRepository,
)
from tests.source_policy_support import enroll_for_cli
from tests.test_checkpoint_reason_owner import create_reason_records
from tests.test_recovery_whole_state import stored_rows


@pytest.fixture
def reason_cli(
    migrated_database: Database,
) -> tuple[Database, Settings, list[str], Path]:
    scope, _approval, _restore = create_reason_records(migrated_database)
    directory = enroll_for_cli(migrated_database)
    with migrated_database.session_factory() as session:
        owner = SQLAlchemyPersonalityRepository(session)
        approvals = owner.list_checkpoint_approval_headers(scope.identity_id)
        restores = owner.list_restore_headers(scope.identity_id)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment=Environment.TEST,
        database_url=str(migrated_database.engine.url),
        source_policy_directory=directory,
        log_level=LogLevel.CRITICAL,
    )
    selected = [
        "personality",
        "reason-bodies",
        "--identity-id",
        scope.identity_id,
        "--approval",
        approvals[0].approval_id,
        "--restore",
        restores[0].restore_id,
    ]
    return migrated_database, settings, selected, directory


def _run(settings: Settings, args: list[str]) -> int:
    with (
        patch.object(
            cli, "_open_services", side_effect=AssertionError("cognitive_services_opened")
        ),
        patch.object(
            cli, "upgrade_database", side_effect=AssertionError("implicit_main_migration")
        ),
    ):
        return cli.main(args, settings=settings)


def _upgrade(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(settings, ["personality", "reasons-policy", "--upgrade", "--reviewed"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["physical_erasure"] is False
    assert result["execution_authorized"] is False


def test_actual_cli_preview_and_foreign_identity_preserve_all_bytes(
    reason_cli: tuple[Database, Settings, list[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    database, settings, selected, directory = reason_cli
    capsys.readouterr()
    _upgrade(settings, capsys)
    path = Path(str(database.engine.url.database))
    before = stored_rows(path)
    files = {p: p.read_bytes() for p in (path, *directory.iterdir()) if p.is_file()}
    assert _run(settings, [*selected, "--preview"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["approval_reasons"] == 1
    assert result["restore_reasons"] == 1
    assert result["metadata_references"] == 51
    assert "SYNTHETIC_PRIVATE" not in captured.out + captured.err
    foreign = list(selected)
    foreign[3] = "foreign-identity"
    assert _run(settings, [*foreign, "--preview"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "checkpoint_reason_scope_unavailable" in captured.err
    assert selected[5] not in captured.err
    assert selected[7] not in captured.err
    assert stored_rows(path) == before
    assert all(p.read_bytes() == data for p, data in files.items())


def test_actual_cli_apply_replay_and_status_remove_bodies_without_new_owner_operation(
    reason_cli: tuple[Database, Settings, list[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    database, settings, selected, _directory = reason_cli
    capsys.readouterr()
    _upgrade(settings, capsys)
    assert _run(settings, [*selected, "--preview"]) == 0
    preview = json.loads(capsys.readouterr().out)
    command = [
        *selected,
        "--apply",
        "--request-id",
        "cli-checkpoint-reasons",
        "--expected-generation",
        str(preview["generation"]),
        "--expected-erasure-revision",
        str(preview["erasure_revision"]),
        "--expected-body-revision",
        str(preview["body_revision"]),
        "--sha256",
        str(preview["approval_digest"]),
    ]
    path = Path(str(database.engine.url.database))
    before = stored_rows(path)
    assert _run(settings, command) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["selected_bodies_removed"] is True
    assert applied["physical_erasure"] is False
    assert applied["execution_authorized"] is False
    after = stored_rows(path)
    for table in (
        "satori_personality_states",
        "satori_personality_traits",
        "personality_checkpoints",
        "personality_checkpoint_traits",
        "personality_revisions",
        "audit_events",
        "audit_metadata_bindings",
    ):
        assert after[table] == before[table]
    with database.engine.connect() as connection:
        for table in ("personality_checkpoint_approvals", "personality_restore_events"):
            assert [
                tuple(row)
                for row in connection.exec_driver_sql(f"SELECT reason,reason_state FROM {table}")
            ] == [(None, "erased")]
    assert _run(settings, command) == 0
    assert json.loads(capsys.readouterr().out) == applied
    assert (
        _run(settings, [*selected[:4], "--status", "--request-id", "cli-checkpoint-reasons"]) == 0
    )
    assert json.loads(capsys.readouterr().out) == applied
    assert stored_rows(path) == after


def test_cli_duplicates_and_missing_selection_do_not_open_services_or_mutate_floor(
    reason_cli: tuple[Database, Settings, list[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    database, settings, selected, directory = reason_cli
    capsys.readouterr()
    _upgrade(settings, capsys)
    path = Path(str(database.engine.url.database))
    before = stored_rows(path)
    files = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    assert _run(settings, [*selected, "--approval", selected[5], "--preview"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid_command" in captured.err
    assert _run(settings, [*selected[:4], "--preview"]) == 2
    assert "checkpoint_reason_selectors_required" in capsys.readouterr().err
    assert stored_rows(path) == before
    assert all(p.read_bytes() == data for p, data in files.items())
