"""Explicit local lifecycle controls work with every cognitive capability offline."""

import json
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

import satori.__main__ as cli
from satori.config import Environment, LogLevel, Settings
from satori.infrastructure.persistence.database import Database
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import activate
from tests.test_stage6_semantic_memory import MEMORY_SCOPE, services, talk


def _settings(database: Database, *, counterparty: str = "local-default") -> Settings:
    from satori.infrastructure.source_policy.installation import enroll_installation
    from tests.source_policy_support import policy_directory

    directory = policy_directory(str(database.engine.url))
    if not database.session_factory.kw.get("info", {}).get("source_runtime"):
        enroll_installation(
            database, directory, reviewed=True, new_installation=not directory.exists()
        )
    return Settings(  # type: ignore[call-arg]  # BaseSettings supports a per-call env-file override.
        _env_file=None,
        environment=Environment.TEST,
        database_url=str(database.engine.url),
        source_policy_directory=directory,
        default_counterparty_id=counterparty,
        log_level=LogLevel.CRITICAL,
    )


def _forbid_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("deterministic lifecycle constructed a provider")

    for name in (
        "_configured_conversation_provider",
        "_configured_episode_provider",
        "_configured_embedding_provider",
        "_configured_semantic_provider",
        "_configured_affective_provider",
        "_configured_relationship_provider",
        "_configured_model_provider",
        "_configured_position_provider",
        "_configured_reflection_provider",
    ):
        monkeypatch.setattr(cli, name, forbidden)


def test_cli_review_apply_replay_and_reactivate_have_no_provider_dependency(
    migrated_database: Database,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activate(migrated_database)
    active = services(migrated_database, semantic=None, prefix="lifecycle-cli")
    talk(active, "Меня зовут ПриватныйТест.", "lifecycle-cli")
    memory = active.memories.execute(scope=MEMORY_SCOPE)[0]
    database_path = Path(str(migrated_database.engine.url.database))
    _forbid_providers(monkeypatch)
    settings = _settings(migrated_database)
    before = stored_rows(database_path)
    config = project_root / "alembic.ini"

    assert (
        cli.main(
            ["memories", "archive", memory.memory_id, "--preview"],
            settings=settings,
            alembic_config=config,
        )
        == 0
    )
    preview_text = capsys.readouterr().out
    preview = json.loads(preview_text)
    assert "ПриватныйТест" not in preview_text
    assert stored_rows(database_path) == before
    command = [
        "memories",
        "archive",
        memory.memory_id,
        "--apply",
        "--expected-version",
        str(preview["current_version"]),
        "--sha256",
        preview["preview_digest"],
        "--request-id",
        "cli-archive-1",
    ]
    assert cli.main(command, settings=settings, alembic_config=config) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["current_status"] == "archived"
    assert result["current_version"] == 2
    archived = stored_rows(database_path)
    assert cli.main(command, settings=settings, alembic_config=config) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["replayed"] is True
    assert stored_rows(database_path) == archived
    assert active.memories.execute(scope=MEMORY_SCOPE) == ()
    retained = active.memories.execute(scope=MEMORY_SCOPE, include_archived=True)
    assert len(retained) == 1
    assert retained[0].summary == memory.summary
    assert retained[0].evidence == memory.evidence

    assert (
        cli.main(
            ["memories", "reactivate", memory.memory_id, "--preview"],
            settings=settings,
            alembic_config=config,
        )
        == 0
    )
    reactivate = json.loads(capsys.readouterr().out)
    assert (
        cli.main(
            [
                "memories",
                "reactivate",
                memory.memory_id,
                "--apply",
                "--expected-version",
                "2",
                "--sha256",
                reactivate["preview_digest"],
                "--request-id",
                "cli-reactivate-1",
            ],
            settings=settings,
            alembic_config=config,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["current_version"] == 3
    assert active.memories.execute(scope=MEMORY_SCOPE)[0].aggregate_version == 3
    assert cli.main(command, settings=settings, alembic_config=config) == 0
    historical_replay = json.loads(capsys.readouterr().out)
    assert historical_replay["replayed"] is True
    assert historical_replay["current_status"] == "active"
    assert historical_replay["receipt"] == result["receipt"]


@pytest.mark.parametrize("action", ["archive", "reactivate"])
def test_cli_denials_are_metadata_only_and_do_not_write(
    action: str,
    migrated_database: Database,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activate(migrated_database)
    active = services(migrated_database, semantic=None, prefix="denied-lifecycle-cli")
    talk(active, "Меня зовут ПриватныйТест.", "denied-lifecycle-cli")
    memory = active.memories.execute(scope=MEMORY_SCOPE)[0]
    _forbid_providers(monkeypatch)
    settings = _settings(migrated_database, counterparty="foreign")
    before = stored_rows(Path(str(migrated_database.engine.url.database)))
    config = project_root / "alembic.ini"
    assert (
        cli.main(
            ["memories", action, memory.memory_id, "--preview"],
            settings=settings,
            alembic_config=config,
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Memory lifecycle rejected:" in captured.err
    assert "ПриватныйТест" not in captured.err
    assert "Traceback" not in captured.err
    assert stored_rows(Path(str(migrated_database.engine.url.database))) == before
    assert (
        cli.main(
            ["memories", action, memory.memory_id, "--apply"],
            settings=settings,
            alembic_config=config,
        )
        == 2
    )
    assert "apply_requires_version_hash_and_request_id" in capsys.readouterr().err


def test_cli_storage_failure_has_safe_error_and_preserves_source(
    migrated_database: Database,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activate(migrated_database)
    settings = _settings(migrated_database)
    before = stored_rows(Path(str(migrated_database.engine.url.database)))
    _forbid_providers(monkeypatch)

    class FailingControls:
        def preview(self, *args: object, **kwargs: object) -> None:
            raise OperationalError("SQL_PRIVATE_TEXT", None, RuntimeError("PRIVATE_DATA"))

    monkeypatch.setattr(cli, "build_memory_lifecycle_services", lambda *args: FailingControls())
    assert (
        cli.main(
            ["memories", "archive", "memory-id", "--preview"],
            settings=settings,
            alembic_config=project_root / "alembic.ini",
        )
        == 1
    )
    assert capsys.readouterr().err == "Memory lifecycle unavailable: storage_failure\n"
    assert stored_rows(Path(str(migrated_database.engine.url.database))) == before
