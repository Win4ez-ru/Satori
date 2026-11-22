"""Actual read-only scoped CLI census without providers, migrations or raw output."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import satori.__main__ as cli
from satori.config import Environment, LogLevel, Settings
from satori.infrastructure.persistence.database import Database
from tests.erasure_support import ErasureCase, create_case, provider_counts, stored_state
from tests.test_source_exclusion_production import ROOT_TEXT, SECRET


@pytest.fixture
def audit_case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "audit-cli-floor")


def _settings(case: ErasureCase, *, counterparty: str = "alice") -> Settings:
    # BaseSettings supports this runtime keyword; the Pydantic mypy-generated
    # Settings signature lists declared model fields only.
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment=Environment.TEST,
        database_url=str(case.database.engine.url),
        source_policy_directory=case.directory,
        default_counterparty_id=counterparty,
        log_level=LogLevel.CRITICAL,
    )


def _status(case: ErasureCase, *, counterparty: str = "alice") -> int:
    with (
        patch.object(
            cli, "_open_services", side_effect=AssertionError("cognitive_services_opened")
        ),
        patch.object(cli, "upgrade_database", side_effect=AssertionError("implicit_migration")),
    ):
        return cli.main(
            ["sources", "audit-metadata", case.message_id, "--status"],
            settings=_settings(case, counterparty=counterparty),
        )


def test_actual_cli_status_counts_are_read_only_and_never_execution_authority(
    audit_case: ErasureCase, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    state = stored_state(audit_case)
    main_bytes = audit_case.path.read_bytes()
    floor_bytes = audit_case.runtime.ledger.database_path.read_bytes()
    calls = provider_counts(audit_case.services)
    exit_code = _status(audit_case)
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert exit_code == (0 if result["coverage"] == "complete" else 2)
    assert result["total_events"] == (
        result["certified_metadata_events"]
        + result["owned_content_events"]
        + result["unknown_events"]
    )
    assert result["certified_metadata_events"] > 0
    assert result["unknown_binding_rows"] == 0
    assert result["physical_erasure"] is False
    assert result["execution_authorized"] is False
    assert result["automatic_processing"] is False
    assert audit_case.message_id not in captured.out
    assert SECRET not in captured.out + captured.err
    assert ROOT_TEXT not in captured.out + captured.err
    assert stored_state(audit_case) == state
    assert audit_case.path.read_bytes() == main_bytes
    assert audit_case.runtime.ledger.database_path.read_bytes() == floor_bytes
    assert provider_counts(audit_case.services) == calls


def test_foreign_counterparty_is_denied_before_private_counts_or_identifiers(
    audit_case: ErasureCase, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    state = stored_state(audit_case)
    assert _status(audit_case, counterparty="bob") == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "canonical_source_unavailable_in_scope" in captured.err
    assert audit_case.message_id not in captured.out + captured.err
    assert SECRET not in captured.out + captured.err
    assert ROOT_TEXT not in captured.out + captured.err
    assert stored_state(audit_case) == state


def test_unknown_prose_remains_stored_and_only_safe_unknown_counts_are_returned(
    audit_case: ErasureCase, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    _status(audit_case)
    before = json.loads(capsys.readouterr().out)
    with audit_case.database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO audit_events(event_id,schema_version,event_type,aggregate_type,"
            "aggregate_id,occurred_at,trace_id,details) VALUES(?,1,?,?,?,?,?,?)",
            (
                "unknown-audit-private",
                "untrusted." + SECRET,
                "untrusted",
                "untrusted-record",
                "2026-10-13 00:00:00.000000",
                "untrusted-trace",
                json.dumps({"prose": SECRET}),
            ),
        )
    state = stored_state(audit_case)
    assert _status(audit_case) == 2
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["coverage"] == "partial"
    assert result["unknown_events"] == before["unknown_events"] + 1
    assert result["total_events"] == before["total_events"] + 1
    assert result["certified_metadata_events"] == before["certified_metadata_events"]
    assert result["owned_content_events"] == before["owned_content_events"]
    assert "unknown-audit-private" not in captured.out
    assert SECRET not in captured.out + captured.err
    assert stored_state(audit_case) == state


def test_cli_has_no_audit_execution_or_provider_path(
    audit_case: ErasureCase, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    state = stored_state(audit_case)
    with (
        patch.object(
            cli, "_open_services", side_effect=AssertionError("cognitive_services_opened")
        ),
        pytest.raises(SystemExit) as rejected,
    ):
        cli.main(
            ["sources", "audit-metadata", audit_case.message_id, "--status", "--apply"],
            settings=_settings(audit_case),
        )
    assert rejected.value.code == 2
    assert stored_state(audit_case) == state
