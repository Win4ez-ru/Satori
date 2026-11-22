"""Offline launch-boundary checks; no paid or local model transport is permitted."""

import json
from pathlib import Path
from typing import Any

import pytest

from satori.config import Environment
from tests.character_closeout_api_test import (
    EvaluationSettings,
    _paid_worker_authority,
    execution_jobs,
    prepare,
    run_campaign,
    verify_installed_package,
)
from tests.character_closeout_plan import (
    CloseoutGuardError,
    build_execution_plan,
    file_digest,
)

CORPUS = Path(__file__).parents[1] / "docs/evaluation/character-closeout-scenarios.v1.json"


def test_literal_evaluation_settings_ignore_environment_and_dotenv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SATORI_CONVERSATION_CURRENT_POSITION_ENABLED", "true")
    monkeypatch.setenv("SATORI_DATABASE_URL", "invalid-and-never-opened")
    monkeypatch.setenv("SATORI_OPENAI_API_KEY", "never-read-secret-fixture")
    settings = EvaluationSettings(environment=Environment.TEST)
    assert settings.conversation_current_position_enabled is False
    assert settings.database_url == "sqlite+pysqlite:///./var/satori.db"
    assert settings.openai_api_key is None


def test_jobs_cover_exact_schedule_and_declared_restart_boundaries() -> None:
    plan = build_execution_plan(CORPUS, source_hashes={"corpus.json": file_digest(CORPUS)})
    jobs = execution_jobs(plan)
    assert [t for j in jobs for t in j["turn_ids"]] == [t["turn_id"] for t in plan["turns"]]
    by_name = {job["name"]: job for job in jobs}
    assert by_name["independent-12-0"]["activate"] is False
    assert by_name["independent-12-0"]["new_session"] is False
    assert by_name["independent-12-0"]["floor_store"] == "independent-01"
    assert by_name["independent-17-0"]["store"] == "independent-01"
    assert by_name["independent-17-0"]["activate"] is False
    assert [len(by_name[f"continuous-{i}"]["turn_ids"]) for i in range(3)] == [18, 9, 3]
    assert by_name["independent-20-0"]["turn_ids"][-1] == "independent:20:3"
    assert by_name["independent-20-1"]["turn_ids"][0] == "independent:20:4"
    assert by_name["relationship_strained_A"]["floor_store"] == "relationship-profiles"
    assert by_name["relationship_fresh_B"]["store"] == "relationship-strained"


def test_prepare_never_constructs_http_or_opens_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("preparation attempted HTTP or credentials")

    monkeypatch.setattr("tests.character_closeout_api_test.OpenAIHttpClient", forbidden)
    monkeypatch.setattr("tests.character_closeout_api_test.os.environ.get", forbidden)
    plan = prepare(tmp_path / "preparation")
    assert plan["calls_executed"] == 0
    assert plan["preparation_directory"] == str((tmp_path / "preparation").resolve())
    grant = json.loads((tmp_path / "preparation/grant-template.json").read_text())
    assert grant["status"] == "NOT_GRANTED"
    assert grant["authorized_invocations"] == 0


def test_paid_launch_without_grant_stops_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "preparation"
    prepare(directory)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("ungranted launch attempted a child or transport")

    monkeypatch.setattr("tests.character_closeout_api_test.subprocess.Popen", forbidden)
    monkeypatch.setattr("tests.character_closeout_api_test.OpenAIHttpClient", forbidden)
    with pytest.raises(CloseoutGuardError, match="human grant"):
        run_campaign(directory, tmp_path / "denied", synthetic=False)


def test_worker_rejects_noncanonical_claim_before_credentials(tmp_path: Path) -> None:
    with pytest.raises(CloseoutGuardError, match="canonical"):
        _paid_worker_authority(
            tmp_path, {"paid_claim_path": str(tmp_path / "invented-claim.json")}, {}
        )


def test_noneditable_source_and_installed_package_match() -> None:
    proof = verify_installed_package()
    assert proof["status"] == "PASS_EXACT_PACKAGE_PARITY"
    assert proof["package_files"] > 250
