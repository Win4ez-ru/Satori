"""Stage 13 local inclination parser, inspection, and export contracts."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from satori.__main__ import build_parser, main
from satori.config import Environment, LogLevel, Settings
from satori.domain.inclinations import INCLINATION_POLICY_VERSION, SatoriInclination
from satori.domain.reflection import ReflectionOutcome, ReflectionOutcomeDecision
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from tests.test_stage4_conversation_memory import activate
from tests.test_stage13_inclinations_persistence import (
    _applied_inclination,
    _canonical_position_source,
    _create_reflection_source,
)

MATERIALIZED_AT = datetime(2026, 9, 4, 12, tzinfo=UTC)
MATERIALIZED_AT_TEXT = MATERIALIZED_AT.isoformat()


def _settings(database: Database) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=str(database.engine.url),
        log_level=LogLevel.WARNING,
    )


def _run_cli(database: Database, project_root: Path, arguments: list[str]) -> int:
    return main(
        arguments,
        settings=_settings(database),
        alembic_config=project_root / "alembic.ini",
    )


def _seed_inclination(database: Database) -> tuple[SatoriInclination, str]:
    snapshot = activate(database)
    _, _, source_session_id, raw_quote = _canonical_position_source(
        database,
        identity_id=snapshot.identity.identity_id,
    )
    _, source, proposal_id = _create_reflection_source(
        database,
        identity_id=snapshot.identity.identity_id,
    )
    outcome_id = "inclination-cli-outcome"
    inclination, evaluation = _applied_inclination(
        identity_id=snapshot.identity.identity_id,
        source=source,
        source_session_id=source_session_id,
        outcome_id=outcome_id,
    )
    outcome = ReflectionOutcome(
        outcome_id=outcome_id,
        proposal_id=proposal_id,
        target_policy_version=INCLINATION_POLICY_VERSION,
        decision=ReflectionOutcomeDecision.ACCEPTED,
        reason_code="eligible_inclination_created",
        target_aggregate_type="satori_inclinations",
        target_aggregate_id=inclination.inclination_id,
        decided_at=inclination.updated_at,
    )
    with SQLAlchemyPositionsUnitOfWork(database.session_factory) as unit:
        assert unit.positions.record_inclination_reflection_decision(
            outcome,
            evaluation,
            identity_id=snapshot.identity.identity_id,
            trace_id="trace-inclination-cli-seed",
            audit_event_id="audit-inclination-cli-seed",
        )
        unit.commit()
    return inclination, raw_quote


def test_parser_exposes_exact_inclination_commands_and_aware_as_of() -> None:
    parser = build_parser()

    listed = parser.parse_args(
        ["positions", "inclinations-list", "--as-of", "2026-09-04T12:00:00Z"]
    )
    assert listed.command == "positions"
    assert listed.positions_action == "inclinations-list"
    assert listed.as_of == MATERIALIZED_AT

    inspected = parser.parse_args(
        [
            "positions",
            "inclination-inspect",
            "inclination-1",
            "--as-of",
            "2026-09-04T14:00:00+02:00",
        ]
    )
    assert inspected.positions_action == "inclination-inspect"
    assert inspected.inclination_id == "inclination-1"
    assert inspected.as_of.utcoffset() == timedelta(hours=2)
    assert inspected.as_of.astimezone(UTC) == MATERIALIZED_AT

    exported = parser.parse_args(
        [
            "positions",
            "inclination-export",
            "--output",
            "inclinations.json",
            "--as-of",
            MATERIALIZED_AT_TEXT,
        ]
    )
    assert exported.positions_action == "inclination-export"
    assert exported.output == Path("inclinations.json")
    assert exported.as_of == MATERIALIZED_AT


@pytest.mark.parametrize(
    "action",
    [
        ["inclinations-list"],
        ["inclination-inspect", "inclination-1"],
        ["inclination-export"],
    ],
)
def test_parser_rejects_naive_as_of_for_every_inclination_command(
    action: list[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["positions", *action, "--as-of", "2026-09-04T12:00:00"])

    assert error.value.code == 2


def test_empty_list_export_and_missing_inspect_have_controlled_outputs(
    migrated_database: Database,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = activate(migrated_database)

    assert (
        _run_cli(
            migrated_database,
            project_root,
            ["positions", "inclinations-list", "--as-of", MATERIALIZED_AT_TEXT],
        )
        == 0
    )
    listed = capsys.readouterr()
    assert listed.out == f"materialized_at={MATERIALIZED_AT_TEXT}\n"
    assert listed.err == ""

    assert (
        _run_cli(
            migrated_database,
            project_root,
            ["positions", "inclination-export", "--as-of", MATERIALIZED_AT_TEXT],
        )
        == 0
    )
    exported = capsys.readouterr()
    assert exported.err == ""
    assert json.loads(exported.out) == {
        "schema_version": 1,
        "identity_id": snapshot.identity.identity_id,
        "inclination_policy_version": INCLINATION_POLICY_VERSION,
        "materialized_at": MATERIALIZED_AT_TEXT,
        "inclinations": [],
    }

    assert (
        _run_cli(
            migrated_database,
            project_root,
            [
                "positions",
                "inclination-inspect",
                "missing-inclination",
                "--as-of",
                MATERIALIZED_AT_TEXT,
            ],
        )
        == 2
    )
    missing = capsys.readouterr()
    assert missing.out == ""
    assert missing.err == "Satori inclination not found.\n"


def test_seeded_list_inspect_and_export_show_anchor_decay_and_safe_provenance(
    migrated_database: Database,
    project_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inclination, raw_quote = _seed_inclination(migrated_database)
    evidence = inclination.evidence[0]
    revision = inclination.revisions[0]

    assert (
        _run_cli(
            migrated_database,
            project_root,
            ["positions", "inclinations-list", "--as-of", MATERIALIZED_AT_TEXT],
        )
        == 0
    )
    listed = capsys.readouterr()
    assert listed.err == ""
    assert listed.out.splitlines()[0] == f"materialized_at={MATERIALIZED_AT_TEXT}"
    assert f"[{inclination.inclination_id}] interest" in listed.out
    assert "topic='Архитектура'" in listed.out
    assert "score=0.120" in listed.out
    assert "effective_score=0.078" in listed.out
    assert "confidence=0.700" in listed.out
    assert "stability=0.200" in listed.out
    assert f"state_as_of={inclination.state_as_of.isoformat()}" in listed.out
    assert f"materialized_at={MATERIALIZED_AT_TEXT}" in listed.out
    assert raw_quote not in listed.out

    assert (
        _run_cli(
            migrated_database,
            project_root,
            [
                "positions",
                "inclination-inspect",
                inclination.inclination_id,
                "--as-of",
                MATERIALIZED_AT_TEXT,
            ],
        )
        == 0
    )
    inspected = capsys.readouterr()
    assert inspected.err == ""
    assert f"evidence={evidence.evidence_id} role=topic" in inspected.out
    assert f"reflection_source={evidence.reflection_source_id}" in inspected.out
    assert f"affective_transition={evidence.affective_transition_id}" in inspected.out
    assert f"affective_state_version={evidence.affective_state_version}" in inspected.out
    assert f"message={evidence.source_message_id}" in inspected.out
    assert f"interaction={evidence.source_interaction_id}" in inspected.out
    assert f"session={evidence.source_session_id}" in inspected.out
    assert f"revision={revision.revision_id} v=1 kind=created" in inspected.out
    assert "delta=0.120 reason=eligible_inclination_created" in inspected.out
    assert "quote=" not in inspected.out
    assert raw_quote not in inspected.out

    assert (
        _run_cli(
            migrated_database,
            project_root,
            ["positions", "inclination-export", "--as-of", MATERIALIZED_AT_TEXT],
        )
        == 0
    )
    exported = capsys.readouterr()
    assert exported.err == ""
    payload = json.loads(exported.out)
    assert payload["materialized_at"] == MATERIALIZED_AT_TEXT
    assert len(payload["inclinations"]) == 1
    item = payload["inclinations"][0]
    assert item["inclination_id"] == inclination.inclination_id
    assert item["score_at_state_as_of"] == pytest.approx(0.12)
    assert item["state_as_of"] == inclination.state_as_of.isoformat()
    assert item["effective_score"] == pytest.approx(0.07781)
    assert item["confidence"] == pytest.approx(0.7)
    assert item["stability"] == pytest.approx(0.2)
    assert item["evidence"][0]["reflection_source_id"] == evidence.reflection_source_id
    assert item["evidence"][0]["affective_transition_id"] == evidence.affective_transition_id
    assert item["evidence"][0]["source_message_id"] == evidence.source_message_id
    assert item["revisions"][0]["reflection_outcome_id"] == revision.reflection_outcome_id
    assert item["revisions"][0]["reason_code"] == "eligible_inclination_created"
    assert "quote" not in item["evidence"][0]
    assert raw_quote not in exported.out

    output_path = tmp_path / "nested" / "inclinations.json"
    assert (
        _run_cli(
            migrated_database,
            project_root,
            [
                "positions",
                "inclination-export",
                "--output",
                str(output_path),
                "--as-of",
                MATERIALIZED_AT_TEXT,
            ],
        )
        == 0
    )
    file_result = capsys.readouterr()
    assert file_result.err == ""
    assert file_result.out == f"Inclinations export written: {output_path}\n"
    serialized_file = output_path.read_text(encoding="utf-8")
    assert serialized_file.endswith("\n")
    assert json.loads(serialized_file) == payload
    assert raw_quote not in serialized_file


def test_as_of_before_canonical_state_returns_controlled_code_two(
    migrated_database: Database,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inclination, _ = _seed_inclination(migrated_database)
    before = (inclination.state_as_of - timedelta(microseconds=1)).isoformat()
    cases = (
        (
            ["positions", "inclinations-list", "--as-of", before],
            "Inclination materialization time precedes canonical state.",
        ),
        (
            [
                "positions",
                "inclination-inspect",
                inclination.inclination_id,
                "--as-of",
                before,
            ],
            "Inclination materialization time precedes canonical state.",
        ),
        (
            ["positions", "inclination-export", "--as-of", before],
            "Inclination export rejected: inclination cannot be materialized backwards in time",
        ),
    )

    for arguments, expected_error in cases:
        assert _run_cli(migrated_database, project_root, arguments) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == f"{expected_error}\n"
