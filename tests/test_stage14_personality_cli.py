"""Stage 14 explicit local personality processing and recovery CLI contracts."""

import json
from pathlib import Path

import pytest

from satori.__main__ import build_parser, main
from satori.application.personality.use_cases import GetPersonalityEvolution
from satori.config import Environment, LogLevel, Settings
from satori.infrastructure.persistence.database import Database
from tests.fakes import FrozenClock
from tests.test_stage12_reflection_integration import FakeReflectionProvider
from tests.test_stage14_personality_persistence import (
    NOW,
    SOURCE_TEXTS,
    _seed_personality_run,
    _uow,
)


def _settings(database: Database) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=str(database.engine.url),
        log_level=LogLevel.WARNING,
    )


def _run_cli(
    database: Database,
    project_root: Path,
    arguments: list[str],
    *,
    reflection_provider: FakeReflectionProvider | None = None,
) -> int:
    return main(
        arguments,
        settings=_settings(database),
        alembic_config=project_root / "alembic.ini",
        reflection_generation_provider=reflection_provider,
    )


def test_parser_exposes_typed_personality_commands_without_force_or_implicit_hash() -> None:
    parser = build_parser()
    checkpoint_hash = "a" * 64

    assert parser.parse_args(["personality", "inspect"]).personality_action == "inspect"
    assert (
        parser.parse_args(["personality", "compare", "checkpoint-1"]).checkpoint_id
        == "checkpoint-1"
    )
    assert parser.parse_args(["personality", "export", "--output", "state.json"]).output == Path(
        "state.json"
    )
    processed = parser.parse_args(["personality", "process"])
    assert processed.personality_action == "process"
    assert not hasattr(processed, "force")
    for action in ("approve", "restore"):
        proposal = parser.parse_args(
            [
                "personality",
                action,
                "checkpoint-1",
                "--hash",
                checkpoint_hash,
                "--expected-version",
                "7",
                "--reason",
                "local anchor review",
            ]
        )
        assert proposal.checkpoint_hash == checkpoint_hash
        assert proposal.expected_version == 7
        assert proposal.reason == "local anchor review"

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "personality",
                "restore",
                "checkpoint-1",
                "--hash",
                "A" * 64,
                "--expected-version",
                "7",
                "--reason",
                "invalid uppercase hash",
            ]
        )


def test_process_resumes_persisted_v3_run_routes_owner_and_never_calls_provider_twice(
    migrated_database: Database,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _seed_personality_run(migrated_database, trait_key="optimism")
    provider = FakeReflectionProvider()
    monkeypatch.setattr("satori.composition.SystemClock", lambda: FrozenClock(NOW))

    assert (
        _run_cli(
            migrated_database,
            project_root,
            ["personality", "process"],
            reflection_provider=provider,
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "purpose=personality_evolution" in captured.out
    assert f"run={fixture.run_id}" in captured.out
    assert "status=completed" in captured.out
    assert "reason=nonterminal_run_requires_routing" in captured.out
    assert "provider_called=false" in captured.out
    assert provider.requests == []


def test_inspect_compare_export_approve_and_restore_are_explicit_and_quote_free(
    migrated_database: Database,
    project_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _seed_personality_run(migrated_database, trait_key="optimism")
    provider = FakeReflectionProvider()
    monkeypatch.setattr("satori.composition.SystemClock", lambda: FrozenClock(NOW))
    assert (
        _run_cli(
            migrated_database,
            project_root,
            ["personality", "process"],
            reflection_provider=provider,
        )
        == 0
    )
    capsys.readouterr()
    evolution = GetPersonalityEvolution(
        unit_of_work_factory=lambda: _uow(migrated_database),
        clock=FrozenClock(NOW),
    )
    state = evolution.inspect(fixture.identity_id)
    assert state is not None
    activation = state.activation_checkpoint.snapshot
    evolved = next(
        item.snapshot
        for item in state.checkpoints
        if item.snapshot.checkpoint_kind.value == "evolution"
    )

    assert _run_cli(migrated_database, project_root, ["personality", "inspect"]) == 0
    inspected = capsys.readouterr()
    assert inspected.err == ""
    assert "aggregate_version=2" in inspected.out
    assert "trait[optimism] value=0.625000 baseline=0.620000 delta=+0.005000" in inspected.out
    assert f"checkpoint[{evolved.checkpoint_id}]" in inspected.out
    assert "quote=" not in inspected.out
    assert all(source_text not in inspected.out for source_text in SOURCE_TEXTS)

    assert (
        _run_cli(
            migrated_database,
            project_root,
            ["personality", "compare", activation.checkpoint_id],
        )
        == 0
    )
    compared = capsys.readouterr()
    assert compared.err == ""
    assert "distance_linf=0.005000 distance_l1=0.005000" in compared.out
    assert "trait[optimism] checkpoint=0.620000 current=0.625000 delta=+0.005000" in compared.out

    output_path = tmp_path / "nested" / "personality.json"
    assert (
        _run_cli(
            migrated_database,
            project_root,
            ["personality", "export", "--output", str(output_path)],
        )
        == 0
    )
    exported = capsys.readouterr()
    assert exported.err == ""
    payload_text = output_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    assert payload["personality"]["aggregate_version"] == 2
    assert "quote" not in payload_text
    assert all(source_text not in payload_text for source_text in SOURCE_TEXTS)

    assert (
        _run_cli(
            migrated_database,
            project_root,
            [
                "personality",
                "approve",
                evolved.checkpoint_id,
                "--hash",
                evolved.checkpoint_hash,
                "--expected-version",
                "2",
                "--reason",
                "local anchor review",
            ],
        )
        == 0
    )
    approved = capsys.readouterr()
    assert approved.err == ""
    assert f"approved checkpoint={evolved.checkpoint_id}" in approved.out

    assert (
        _run_cli(
            migrated_database,
            project_root,
            [
                "personality",
                "restore",
                activation.checkpoint_id,
                "--hash",
                activation.checkpoint_hash,
                "--expected-version",
                "2",
                "--reason",
                "restore reviewed activation anchor",
            ],
        )
        == 0
    )
    restored = capsys.readouterr()
    assert restored.err == ""
    assert "restored=true" in restored.out
    assert "reason=personality_checkpoint_restored" in restored.out
    assert "aggregate_version=3" in restored.out
