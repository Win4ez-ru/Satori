"""Real write-free CLI controls never compose cognition or implicitly enroll/migrate."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import satori.__main__ as cli
from satori.config import Environment, LogLevel, Settings
from satori.infrastructure.persistence.database import Database
from tests.source_policy_support import enroll_for_cli
from tests.test_checkpoint_reason_owner import create_reason_records
from tests.test_recovery_whole_state import stored_rows


@pytest.fixture
def retention_cli(migrated_database: Database) -> tuple[Settings, list[str], Path]:
    scope, _approval, _restore = create_reason_records(migrated_database)
    directory = enroll_for_cli(migrated_database)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment=Environment.TEST,
        database_url=str(migrated_database.engine.url),
        source_policy_directory=directory,
        log_level=LogLevel.CRITICAL,
    )
    return settings, ["state", "retention", "--identity-id", scope.identity_id], directory


def run(settings: Settings, arguments: list[str]) -> int:
    with (
        patch.object(cli, "_open_services", side_effect=AssertionError("cognition_opened")),
        patch.object(cli, "upgrade_database", side_effect=AssertionError("implicit_migration")),
        patch.object(
            cli, "_whole_state_command", side_effect=AssertionError("wrong_state_command")
        ),
    ):
        return cli.main(arguments, settings=settings)


def test_real_status_and_fresh_blocked_check_keep_partition_and_every_byte(
    retention_cli: tuple[Settings, list[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    settings, selected, directory = retention_cli
    path = Path(settings.database_url.split("///", 1)[1])
    before_rows = stored_rows(path)
    before_bytes = {p: p.read_bytes() for p in (path, *directory.iterdir()) if p.is_file()}
    capsys.readouterr()
    first_code = run(settings, [*selected, "--status"])
    assert first_code in {0, 2}
    captured = capsys.readouterr()
    status = json.loads(captured.out)
    assert "SYNTHETIC_PRIVATE" not in captured.out + captured.err
    assert status["total_rows"] == sum(
        status[key] for key in ("supported_retained_rows", "owned_content_rows", "unknown_rows")
    )
    assert len(status["table_counts"]) == 60
    assert len({item["owner"] for item in status["table_counts"]}) == 15
    assert status["owned_content_rows"] > 0
    assert status["physical_erasure"] is False
    assert status["execution_authorized"] is False
    assert status["whole_head_current"] is True
    assert run(settings, [*selected, "--status"]) == first_code
    assert json.loads(capsys.readouterr().out) == status
    check = [
        *selected,
        "--check",
        "--expected-generation",
        str(status["generation"]),
        "--expected-erasure-revision",
        str(status["erasure_revision"]),
        "--expected-body-revision",
        str(status["body_revision"]),
        "--sha256",
        status["review_digest"],
    ]
    assert run(settings, check) == first_code
    checked = json.loads(capsys.readouterr().out)
    assert checked["review_status"] == "current"
    assert checked["coverage"] == status["coverage"]
    assert checked["execution_authorized"] is False
    assert stored_rows(path) == before_rows
    assert all(p.read_bytes() == data for p, data in before_bytes.items())


def test_foreign_identity_and_incomplete_review_reveal_no_counts_or_prose(
    retention_cli: tuple[Settings, list[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    settings, selected, _directory = retention_cli
    capsys.readouterr()
    foreign = [*selected[:3], "foreign"]
    assert run(settings, [*foreign, "--status"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "retention_identity_unavailable" in captured.err
    assert selected[3] not in captured.err
    assert run(settings, [*selected, "--check"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "retention_check_requires_review" in captured.err
    assert run(settings, [*selected, "--status", "--expected-generation", "0"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "retention_status_does_not_approve" in captured.err


def test_missing_database_and_floor_are_not_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "absent.db"
    floor = tmp_path / "absent-floor"
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment=Environment.TEST,
        database_url=f"sqlite+pysqlite:///{path}",
        source_policy_directory=floor,
        log_level=LogLevel.CRITICAL,
    )
    assert run(settings, ["state", "retention", "--identity-id", "identity", "--status"]) == 2
    assert capsys.readouterr().out == ""
    assert not path.exists()
    assert not floor.exists()
