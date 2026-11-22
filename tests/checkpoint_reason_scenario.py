"""Actual synthetic mixed-head reason controls, restart and old-archive drill."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from satori.application.personality.ports import PersonalityCheckpointApprovalHeader
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from tests.checkpoint_reason_support import (
    APPROVAL_REASON,
    RESTORE_REASON,
    create_checkpoint_reason_fixture,
)
from tests.erasure_preview_scenario import CLI_ENTRY
from tests.erasure_support import provider_counts
from tests.test_erasure_appraisal_signal_continuity import retained_signals
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import RECALL, ROOT_TEXT, SECRET, _services, _turn


def run(project: Path, directory: Path) -> dict[str, object]:
    path = directory / "synthetic.db"
    url = f"sqlite+pysqlite:///{path}"
    upgrade_database(url, config_path=project / "alembic.ini")
    database = create_database(url)
    checks: dict[str, bool] = {}
    timings: list[dict[str, object]] = []
    try:
        fixture = create_checkpoint_reason_fixture(database, directory / "trusted-floor")
        case = fixture.case
        owner = case.services[-1].personality
        controls = (
            provider_counts(case.services),
            len(fixture.reflection.provider.requests),
            len(fixture.personality_provider.requests),
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
            decode: bool = True,
            expected: tuple[int, ...] = (0,),
        ) -> dict[str, object]:
            started = perf_counter()
            result = subprocess.run(
                [sys.executable, "-c", CLI_ENTRY, *args],
                cwd=project,
                env={**environment, "SATORI_DATABASE_URL": database_url},
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
            assert result.returncode in expected, "checkpoint_reason_scenario_cli_failed"
            for protected in (SECRET, ROOT_TEXT, APPROVAL_REASON, RESTORE_REASON):
                assert protected not in result.stdout + result.stderr
            if not decode:
                return {}
            value = json.JSONDecoder().raw_decode(result.stdout.lstrip())[0]
            assert isinstance(value, dict)
            return value

        base = ["personality", "reason-bodies", "--identity-id", case.scope.identity_id]
        selected = list(base)
        for selector in fixture.selectors:
            selected.extend(
                [
                    "--approval" if selector.kind.value == "checkpoint_approval" else "--restore",
                    selector.record_id,
                ]
            )
        status = [*base, "--status", "--request-id", "scenario-checkpoint-reasons"]
        cli(["personality", "reasons-policy", "--upgrade", "--reviewed"])
        with case.runtime.shared():
            old_actions = case.runtime.body_actions()
        checks["five_actual_source_action_families_precede_identity_action"] = (
            len(old_actions) == 5 and len(fixture.personality_provider.requests) == 1
        )
        hashes = retained_signals(database, fixture.reflection.run.run_id)
        before_rows = stored_rows(path)
        archive = directory / "before-checkpoint-reasons.state"
        cli(["state", "export", "--output", str(archive)])
        archive_bytes = archive.read_bytes()
        preview = cli([*selected, "--preview"])
        checks["review_binds_two_real_owner_records_and_fixed_51_references"] = (
            preview["approval_reasons"] == 1
            and preview["restore_reasons"] == 1
            and preview["metadata_references"] == 51
        )
        command = [
            *selected,
            "--apply",
            "--request-id",
            "scenario-checkpoint-reasons",
            "--expected-generation",
            str(preview["generation"]),
            "--expected-erasure-revision",
            str(preview["erasure_revision"]),
            "--expected-body-revision",
            str(preview["body_revision"]),
            "--sha256",
            str(preview["approval_digest"]),
        ]
        result = cli(command)
        checks["sixth_identity_action_materializes_same_whole_head"] = (
            result["main_projection_current"] is True and result["body_revision"] == 6
        )
        checks["fresh_process_exact_command_replay_is_idempotent"] = cli(command) == result
        after_rows = stored_rows(path)
        checks["audit_binding_numeric_checkpoint_trait_revision_bytes_unchanged"] = all(
            before_rows[table] == after_rows[table]
            for table in (
                "audit_events",
                "audit_metadata_bindings",
                "satori_personality_states",
                "satori_personality_traits",
                "personality_checkpoints",
                "personality_checkpoint_traits",
                "personality_revisions",
            )
        )
        with database.engine.connect() as connection:
            checks["both_actual_reason_values_are_sql_null"] = all(
                [
                    tuple(row)
                    for row in connection.exec_driver_sql(
                        f"SELECT reason,reason_state FROM {table}"
                    )
                ]
                == [(None, "erased")]
                for table in ("personality_checkpoint_approvals", "personality_restore_events")
            )
        current = owner.evolution.inspect(case.scope.identity_id)
        assert current is not None
        numeric = fixture.numeric_before
        checks["inspection_preserves_personality_budgets_anchor_and_history"] = all(
            (
                current.personality == numeric.personality,
                current.budgets == numeric.budgets,
                current.approved_checkpoint == numeric.approved_checkpoint,
                current.checkpoints == numeric.checkpoints,
                current.revisions == numeric.revisions,
            )
        )
        replay = owner.approve_checkpoint.execute(
            case.scope.identity_id,
            fixture.approval_proposal.model_copy(update={"reason": "DO_NOT_REPOPULATE_REASON"}),
            trace_id="scenario-terminal-approval-replay",
        )
        checks["approval_replay_returns_terminal_header_without_new_reason_or_audit"] = (
            isinstance(replay, PersonalityCheckpointApprovalHeader)
            and replay.reason_state == "erased"
            and "reason" not in asdict(replay)
            and stored_rows(path) == after_rows
        )
        restore = owner.restore_checkpoint.execute(
            case.scope.identity_id,
            fixture.restore_proposal,
            trace_id="scenario-stale-restore",
        )
        checks["stale_restore_preserves_original_version_conflict_without_second_restore"] = (
            not restore.restored
            and restore.evaluation.reason_code == "personality_target_version_conflict"
            and stored_rows(path) == after_rows
        )
        with case.runtime.shared():
            checks["old_five_envelopes_and_appraisal_signal_hashes_unchanged"] = (
                case.runtime.body_actions()[:5] == old_actions
                and retained_signals(database, fixture.reflection.run.run_id) == hashes
            )
        stable_bytes = (
            path.read_bytes(),
            case.runtime.ledger.database_path.read_bytes(),
            case.runtime.ledger.binding_path.read_bytes(),
        )
        checks["fresh_process_status_proves_actual_head_and_preserves_bytes"] = (
            cli(status) == result
            and stored_rows(path) == after_rows
            and stable_bytes
            == (
                path.read_bytes(),
                case.runtime.ledger.database_path.read_bytes(),
                case.runtime.ledger.binding_path.read_bytes(),
            )
        )
        foreign = list(selected)
        foreign[3] = "foreign-identity"
        cli([*foreign, "--preview"], decode=False, expected=(2,))
        checks["foreign_identity_denied_without_private_prose_or_counts"] = True
        imported = cli(["state", "import", str(archive), "--preview"])
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
        pending = cli(status, database_url=restored_url, expected=(2,))
        checks["old_archive_status_reports_pending_in_new_process"] = (
            pending["selected_bodies_removed"] is False
            and pending["main_projection_current"] is False
        )
        cli(["personality", "inspect"], database_url=restored_url, decode=False, expected=(2,))
        checks["old_archive_ordinary_owner_use_fenced_before_reason_read"] = True
        resumed = cli(
            [*base, "--resume", "--request-id", "scenario-checkpoint-reasons"],
            database_url=restored_url,
        )
        checks["explicit_resume_reapplies_whole_head_and_reproves_terminal_material"] = (
            resumed == result and cli(status, database_url=restored_url) == result
        )
        restored_rows = stored_rows(target)
        checks["restored_numeric_audit_and_binding_bytes_match_accepted_main"] = all(
            restored_rows[table] == after_rows[table]
            for table in (
                "personality_checkpoint_approvals",
                "personality_restore_events",
                "audit_events",
                "audit_metadata_bindings",
                "satori_personality_states",
                "personality_revisions",
                "erasure_body_bindings",
            )
        )
        checks["original_archive_bytes_remain_unchanged"] = archive.read_bytes() == archive_bytes
        restarted = create_database(restored_url)
        try:
            probe, foreground, _embedding = _services(
                restarted, case.directory, "checkpoint-restarted-probe", 129, form=False
            )
            reply = _turn(probe, RECALL, "checkpoint-restarted-probe")
            provider_input = repr(foreground.requests)
            checks[
                "restarted_foreground_input_and_reply_exclude_both_reason_bodies_and_source_secret"
            ] = all(
                protected not in provider_input + reply.text
                for protected in (APPROVAL_REASON, RESTORE_REASON, SECRET)
            )
        finally:
            restarted.dispose()
        checks["controls_use_zero_cognitive_ports"] = controls == (
            provider_counts(case.services),
            len(fixture.reflection.provider.requests),
            len(fixture.personality_provider.requests),
        )
        checks["all_controls_deny_physical_erasure_and_execution"] = all(
            value["physical_erasure"] is False and value["execution_authorized"] is False
            for value in (preview, result, pending, resumed)
        )
        assert all(checks.values()), checks
        return {
            "stage": "14.4.2h identity-owned checkpoint reasons",
            "synthetic_only": True,
            "physical_erasure": False,
            "execution_authorized": False,
            "real_provider_calls": 0,
            "paid_cost_usd": 0,
            "checks": checks,
            "timings": timings,
        }
    finally:
        database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="satori-checkpoint-reasons-", dir="/private/tmp"
    ) as path:
        directory = Path(path)
        os.chmod(directory, 0o700)
        report = run(Path(__file__).resolve().parents[1], directory)
    PrivateArtifactStore().write(args.output, json.dumps(report, indent=2).encode())
    print(json.dumps(report))


if __name__ == "__main__":
    main()
