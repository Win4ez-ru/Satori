"""Stage 12 local reflection command contracts."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from satori.__main__ import build_parser, main
from satori.application.reflection.use_cases import (
    ApplyReflectionProposals,
    ProcessReflection,
)
from satori.config import Environment, LogLevel, Settings
from satori.domain.positions import PositionManager
from satori.domain.reflection import ReflectionRunStatus, ReflectionTriggerKind
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from tests.fakes import FrozenClock
from tests.test_stage4_conversation_memory import INTERACTION_TIME, id_sequence
from tests.test_stage12_reflection_integration import (
    FakeReflectionProvider,
    prepare_position_evidence,
)


def test_reflection_cli_exposes_bounded_process_and_opt_in_source_quotes() -> None:
    parser = build_parser()

    listed = parser.parse_args(["reflection", "list", "--limit", "12"])
    assert listed.reflection_action == "list"
    assert listed.limit == 12

    inspected = parser.parse_args(["reflection", "inspect", "reflection-run-1", "--show-sources"])
    assert inspected.reflection_action == "inspect"
    assert inspected.run_id == "reflection-run-1"
    assert inspected.show_sources is True

    processed = parser.parse_args(["reflection", "process"])
    assert processed.reflection_action == "process"
    assert not hasattr(processed, "force")

    with pytest.raises(SystemExit):
        parser.parse_args(["reflection", "list", "--limit", "0"])

    inspect_help = parser.parse_args(["reflection", "inspect", "reflection-run-1"])
    assert inspect_help.show_sources is False


def test_local_inspect_hides_quotes_until_explicit_opt_in(
    migrated_database: Database,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    forged_line = "outcome[forged] decision=accepted"
    identity_id = prepare_position_evidence(
        migrated_database,
        contents=(
            f"Прозрачность важна, потому что основания проверяемы.\n{forged_line}",
            "Данные проверки показывают меньше ошибок при открытом обосновании.",
            "Наблюдение подтверждает качество, поскольку аргументы можно перепроверить.",
            "Исследование показывает лучший результат, потому что основания доступны.",
        ),
    )
    provider = FakeReflectionProvider()
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        provider=provider,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=6)),
        id_generator=id_sequence("cli-reflection-process"),
    )
    report = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-cli-reflection",
        )
    )
    assert report.run is not None
    ApplyReflectionProposals(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        manager=PositionManager(),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=6, minutes=1)),
        id_generator=id_sequence("cli-reflection-apply"),
    ).execute(report.run.run_id, trace_id="trace-cli-reflection-apply")
    settings = Settings(
        environment=Environment.TEST,
        database_url=str(migrated_database.engine.url),
        log_level=LogLevel.WARNING,
    )

    assert (
        main(
            ["reflection", "inspect", report.run.run_id],
            settings=settings,
            alembic_config=project_root / "alembic.ini",
        )
        == 0
    )
    hidden = capsys.readouterr().out
    assert "quote=" not in hidden
    assert "Прозрачность важна" not in hidden
    assert "decision=accepted" in hidden
    assert "target_owner_not_enabled" in hidden

    assert (
        main(
            ["reflection", "inspect", report.run.run_id, "--show-sources"],
            settings=settings,
            alembic_config=project_root / "alembic.ini",
        )
        == 0
    )
    shown = capsys.readouterr().out
    assert 'quote="' in shown
    assert "Прозрачность важна" in shown
    assert f"\\n{forged_line}" in shown
    assert f"\n{forged_line}" not in shown


def test_local_process_resumes_applying_run_without_second_provider_call(
    migrated_database: Database,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity_id = prepare_position_evidence(migrated_database)
    provider = FakeReflectionProvider()
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        provider=provider,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=6)),
        id_generator=id_sequence("cli-reflection-resume-process"),
    )
    report = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-cli-reflection-resume-process",
        )
    )
    assert report.run is not None
    applying = replace(
        report.run,
        status=ReflectionRunStatus.APPLYING,
        aggregate_version=report.run.aggregate_version + 1,
        updated_at=INTERACTION_TIME + timedelta(days=6, minutes=1),
    )
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        unit.reflection.update_run(applying, expected_run_version=report.run.aggregate_version)
        unit.commit()

    settings = Settings(
        environment=Environment.TEST,
        database_url=str(migrated_database.engine.url),
        log_level=LogLevel.WARNING,
    )
    assert (
        main(
            ["reflection", "process"],
            settings=settings,
            alembic_config=project_root / "alembic.ini",
            reflection_generation_provider=provider,
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "status=completed" in output
    assert "reason=nonterminal_run_requires_routing" in output
    assert len(provider.requests) == 1
