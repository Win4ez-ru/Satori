"""Private synthetic audit coverage, restart and old-archive adversarial drill."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter

from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from tests.erasure_appraisal_support import (
    appraisal_command,
    create_appraisal_fixture,
    prepare_appraisal,
)
from tests.erasure_preview_scenario import CLI_ENTRY
from tests.erasure_support import provider_counts
from tests.test_erasure_appraisal_signal_continuity import retained_signals
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import ROOT_TEXT, SECRET


def run(project: Path, directory: Path) -> dict[str, object]:
    path = directory / "synthetic.db"
    url = f"sqlite+pysqlite:///{path}"
    upgrade_database(url, config_path=project / "alembic.ini")
    database = create_database(url)
    checks: dict[str, bool] = {}
    timings: list[dict[str, object]] = []
    try:
        fixture = create_appraisal_fixture(database, directory / "trusted-floor")
        case = fixture.case
        manager, request = prepare_appraisal(fixture)
        controls = (
            provider_counts(case.services),
            len(fixture.provider.requests),
            tuple(
                len(getattr(service.talk.prepare_affect.provider, "requests", ()))
                for service in case.services
                if service.talk.prepare_affect is not None
            ),
        )
        environment = {
            **os.environ,
            "SATORI_DATABASE_URL": url,
            "SATORI_SOURCE_POLICY_DIRECTORY": str(case.directory),
            "SATORI_DEFAULT_COUNTERPARTY_ID": "alice",
            "SATORI_ENVIRONMENT": "test",
            "SATORI_LOG_LEVEL": "CRITICAL",
        }

        def cli(
            args: list[str],
            *,
            database_url: str = url,
            counterparty: str = "alice",
            decode: bool = True,
            expected: tuple[int, ...] = (0,),
        ) -> dict[str, object]:
            started = perf_counter()
            result = subprocess.run(
                [sys.executable, "-c", CLI_ENTRY, *args],
                cwd=project,
                env={
                    **environment,
                    "SATORI_DATABASE_URL": database_url,
                    "SATORI_DEFAULT_COUNTERPARTY_ID": counterparty,
                },
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            timings.append(
                {
                    "phase": args[1],
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "exit_code": result.returncode,
                }
            )
            assert result.returncode in expected, "audit_scenario_cli_failed"
            assert SECRET not in result.stdout + result.stderr
            assert ROOT_TEXT not in result.stdout + result.stderr
            if counterparty != "alice":
                assert case.message_id not in result.stdout + result.stderr
            if not decode:
                return {}
            value = json.JSONDecoder().raw_decode(result.stdout.lstrip())[0]
            assert isinstance(value, dict)
            return value

        status = ["sources", "audit-metadata", case.message_id, "--status"]
        before = cli(status, expected=(0, 2))
        checks["prospective_metadata_has_real_owner_certificates"] = (
            int(str(before["certified_metadata_events"])) > 0
        )
        checks["available_appraisal_is_content_not_metadata"] = (
            int(str(before["owned_content_events"])) >= 2
        )
        archive = directory / "before-appraisal.state"
        exported = cli(["state", "export", "--output", str(archive)])
        archive_bytes = archive.read_bytes()
        with case.runtime.shared():
            old_actions = case.runtime.body_actions()
        hashes = retained_signals(database, fixture.run.run_id)
        command = appraisal_command(manager, request, "audit-scenario-appraisal")
        assert manager.commit(command).main_projection_current
        after = cli(status, expected=(0, 2))
        checks["approved_terminal_appraisal_receives_metadata_only_after_pair_proof"] = int(
            str(after["owned_content_events"])
        ) < int(str(before["owned_content_events"])) and int(
            str(after["certified_metadata_events"])
        ) > int(str(before["certified_metadata_events"]))
        with case.runtime.shared():
            checks["old_four_action_bytes_and_numerical_hashes_unchanged"] = (
                case.runtime.body_actions()[:4] == old_actions
                and retained_signals(database, fixture.run.run_id) == hashes
            )
        stable_rows = stored_rows(path)
        stable_bytes = (
            path.read_bytes(),
            case.runtime.ledger.database_path.read_bytes(),
            case.runtime.ledger.binding_path.read_bytes(),
        )
        assert cli(status, expected=(0, 2)) == after
        checks["status_preserves_all_rows_and_main_floor_binding_bytes"] = stored_rows(
            path
        ) == stable_rows and stable_bytes == (
            path.read_bytes(),
            case.runtime.ledger.database_path.read_bytes(),
            case.runtime.ledger.binding_path.read_bytes(),
        )
        cli(status, counterparty="bob", decode=False, expected=(2,))
        checks["foreign_counterparty_denied_without_private_output"] = True
        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO audit_events(event_id,schema_version,event_type,aggregate_type,"
                "aggregate_id,occurred_at,trace_id,details) VALUES(?,1,?,?,?, ?,?,?)",
                (
                    "audit-unsupported",
                    "untrusted." + SECRET,
                    "untrusted",
                    "untrusted-record",
                    "2026-10-13 00:00:00.000000",
                    "untrusted-trace",
                    json.dumps({"prose": SECRET}),
                ),
            )
        unknown = cli(status, expected=(2,))
        checks["unbound_prose_and_unknown_event_name_remain_unknown"] = (
            int(str(unknown["unknown_events"])) == int(str(after["unknown_events"])) + 1
            and unknown["coverage"] == "partial"
        )
        imported = cli(["state", "import", str(archive), "--preview"])
        assert imported["archive_sha256"] == exported["archive_sha256"]
        target = directory / "restored.db"
        cli(
            [
                "state",
                "import",
                str(archive),
                "--activate",
                "--target",
                str(target),
                "--sha256",
                str(imported["archive_sha256"]),
            ]
        )
        restored_url = f"sqlite+pysqlite:///{target}"
        cli(status, database_url=restored_url, expected=(2,), decode=False)
        checks["old_archive_cannot_bypass_pending_body_head_with_metadata_status"] = True
        cli(
            [
                "sources",
                "erasure-appraisal",
                case.message_id,
                "--intent-request-id",
                request.intent_request_id,
                "--resume",
                "--request-id",
                command.request_id,
            ],
            database_url=restored_url,
        )
        resumed = cli(status, database_url=restored_url, expected=(0, 2))
        checks["explicit_restore_resume_revalidates_terminal_metadata"] = (
            resumed["certified_metadata_events"] == after["certified_metadata_events"]
            and resumed["owned_content_events"] == after["owned_content_events"]
            and archive.read_bytes() == archive_bytes
        )
        checks["fresh_process_retains_unknown_without_guessing_or_raw_disclosure"] = (
            cli(status, expected=(2,)) == unknown
        )
        checks["controls_use_zero_cognitive_ports"] = controls == (
            provider_counts(case.services),
            len(fixture.provider.requests),
            tuple(
                len(getattr(service.talk.prepare_affect.provider, "requests", ()))
                for service in case.services
                if service.talk.prepare_affect is not None
            ),
        )
        checks["all_results_deny_physical_erasure_and_execution"] = all(
            value["physical_erasure"] is False and value["execution_authorized"] is False
            for value in (before, after, unknown, resumed)
        )
        assert all(checks.values()), checks
        return {
            "stage": "14.4.2h retained audit metadata",
            "synthetic_only": True,
            "physical_erasure": False,
            "real_provider_calls": 0,
            "paid_cost_usd": 0,
            "checks": checks,
            "timings": timings,
            "certified_events_before": before["certified_metadata_events"],
            "certified_events_after": after["certified_metadata_events"],
            "unknown_events_after_injection": unknown["unknown_events"],
        }
    finally:
        database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="satori-retained-audit-", dir="/private/tmp") as path:
        directory = Path(path)
        os.chmod(directory, 0o700)
        report = run(Path(__file__).resolve().parents[1], directory)
    PrivateArtifactStore().write(args.output, json.dumps(report, indent=2).encode())
    print(json.dumps(report))


if __name__ == "__main__":
    main()
