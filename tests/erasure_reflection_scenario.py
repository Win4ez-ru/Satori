"""Actual private CLI, Reflection interruption, mixed-head recovery and restart drill.

Only synthetic managed fixtures are selected. Reports contain counts, boolean checks and
timings; no stored payload, quotation, body fingerprint or descendant ID is emitted.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.engine import Connection

from satori.application.erasure.body_contracts import BodyErasureCommand, BodyErasureRequest
from satori.application.erasure.reflection_use_cases import ReflectionBodyManager
from satori.application.retrieval.contracts import RetrievalQuery
from satori.domain.erasure_bodies import body_actions_digest
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure_reflection import SQLiteReflectionBodyStore
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.repositories.reflection import SQLAlchemyReflectionRepository
from satori.infrastructure.source_policy.installation import bind_installation
from tests.erasure_preview_scenario import CLI_ENTRY
from tests.erasure_reflection_support import create_reflection_fixture, reflection_applier
from tests.erasure_support import provider_counts
from tests.test_erasure_intent_integration import _command as intent_command
from tests.test_erasure_intent_integration import _manager as intent_manager
from tests.test_erasure_intent_integration import _request as intent_request
from tests.test_erasure_knowledge_integration import knowledge_command, prepare_knowledge
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import (
    RECALL,
    ROOT_TEXT,
    SECRET,
    _exclude,
    _services,
    _turn,
)


def run(project: Path, directory: Path) -> dict[str, object]:
    url = f"sqlite+pysqlite:///{directory / 'synthetic.db'}"
    upgrade_database(url, config_path=project / "alembic.ini")
    database = create_database(url)
    restored = restarted = None
    checks: dict[str, bool] = {}
    timings: list[dict[str, object]] = []
    try:
        fixture = create_reflection_fixture(database, directory / "trusted-floor")
        case = fixture.case
        _exclude(database, case.runtime, case.message_id)
        knowledge, request = prepare_knowledge(case)
        assert knowledge.commit(knowledge_command(knowledge, request)).main_projection_current
        baseline_calls = provider_counts(case.services)
        baseline_reflection_calls = len(fixture.provider.requests)
        with case.runtime.shared() as snapshot:
            old_actions = case.runtime.body_actions()
            old_head = snapshot.body_head_digest
        assert len(old_actions) == 3
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
            expected_exit: int = 0,
            counterparty: str = "alice",
            decode: bool = True,
            expected_reason: str | None = None,
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
            if result.returncode != expected_exit:
                raise AssertionError("scenario_cli_exit_mismatch")
            combined = result.stdout + result.stderr
            assert SECRET not in combined
            assert ROOT_TEXT not in combined
            assert all(key not in combined for key in fixture.proposal_ids)
            if counterparty != "alice":
                assert case.message_id not in combined
            if expected_reason is not None:
                assert result.stdout.strip() == ""
                assert result.stderr.strip() == f"Erasure intent rejected: {expected_reason}"
            if not decode:
                return {}
            value = json.JSONDecoder().raw_decode(result.stdout.lstrip())[0]
            assert isinstance(value, dict)
            return value

        cli(["sources", "erasure-reflection-policy", "--upgrade", "--reviewed"])
        with case.runtime.shared() as snapshot:
            checks["explicit_upgrade_preserves_old_head"] = (
                snapshot.body_revision == 3
                and snapshot.body_head_digest == old_head
                and case.runtime.body_actions() == old_actions
            )
        archive = directory / "before-reflection.state"
        exported = cli(["state", "export", "--output", str(archive)])
        archive_bytes = archive.read_bytes()
        base_args = [
            "sources",
            "erasure-reflection",
            case.message_id,
            "--intent-request-id",
            request.intent_request_id,
        ]
        preview = cli([*base_args, "--preview"])
        checks["uncited_root_selects_both_payloads_and_whole_parent"] = (
            preview["reflection_proposals"] == 2
            and preview["parent_runs"] == 1
            and preview["source_rows"] == 4
            and preview["attempt_rows"] == 1
            and preview["outcome_rows"] == 2
            and preview["metadata_references"] == 16
        )
        command = BodyErasureCommand(
            **request.model_dump(),
            request_id="reflection-cli",
            expected_generation=int(str(preview["generation"])),
            expected_erasure_revision=int(str(preview["erasure_revision"])),
            expected_body_revision=int(str(preview["body_revision"])),
            approval_digest=str(preview["approval_digest"]),
        )
        before = stored_rows(case.path)
        null_updates = observed = 0

        def fail(
            connection: Connection,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            many: bool,
        ) -> None:
            nonlocal null_updates, observed
            if statement.startswith("UPDATE reflection_proposals SET payload=NULL"):
                null_updates += 1
                if null_updates == 2:
                    observed = int(
                        connection.exec_driver_sql(
                            "SELECT count(*) FROM reflection_proposals WHERE payload IS NULL"
                        ).scalar_one()
                    )
                    raise RuntimeError("synthetic_second_reflection_write_failure")

        event.listen(database.engine, "before_cursor_execute", fail)
        started = perf_counter()
        try:
            pending = ReflectionBodyManager(
                case.runtime, SQLiteReflectionBodyStore(database)
            ).commit(command)
        finally:
            event.remove(database.engine, "before_cursor_execute", fail)
        timings.append(
            {
                "phase": "faulted_floor_first_application",
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            }
        )
        checks["second_null_write_fault_rolls_back_all_main_changes"] = (
            null_updates == 2
            and observed == 1
            and not pending.main_projection_current
            and stored_rows(case.path) == before
        )
        receipt_args = [*base_args, "--request-id", command.request_id]
        assert cli([*receipt_args, "--status"], expected_exit=2)["main_projection_current"] is False

        def denied_bind(opened: Database) -> None:
            try:
                bind_installation(opened, case.directory)
            except SourcePolicyError as error:
                if error.reason_code != "body_reconciliation_required":
                    raise AssertionError("scenario_unexpected_body_fence") from None
            else:
                raise AssertionError("scenario_pending_body_head_admitted")

        denied_bind(database)
        checks["floor_first_pending_head_fences_startup"] = True
        assert cli([*receipt_args, "--resume"])["main_projection_current"] is True
        checks["fresh_cli_resumes_whole_mixed_head"] = True
        after = stored_rows(case.path)
        mutable = {"reflection_proposals", "erasure_body_applications", "erasure_body_bindings"}
        checks["all_other_rows_including_numeric_history_remain_exact"] = {
            table: rows for table, rows in before.items() if table not in mutable
        } == {table: rows for table, rows in after.items() if table not in mutable}
        with database.engine.connect() as connection:
            proposal_columns = tuple(
                str(row[1])
                for row in connection.exec_driver_sql("PRAGMA table_info(reflection_proposals)")
            )
            kept = tuple(
                index
                for index, name in enumerate(proposal_columns)
                if name not in {"payload", "body_state", "body_action_id"}
            )
            checks["proposal_headers_retained_exactly"] = {
                tuple(row[index] for index in kept) for row in before["reflection_proposals"]
            } == {tuple(row[index] for index in kept) for row in after["reflection_proposals"]}
            checks["both_payloads_actual_sql_null"] = (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM reflection_proposals WHERE payload IS NULL "
                    "AND body_state='erased' AND body_action_id IS NOT NULL"
                ).scalar_one()
                == 2
            )
            checks["foreign_keys_and_integrity"] = (
                connection.exec_driver_sql("PRAGMA foreign_key_check").first() is None
                and connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
            )
        with (
            patch.object(
                SQLAlchemyReflectionRepository,
                "list_proposals",
                side_effect=AssertionError("scenario_terminal_body_read"),
            ),
            patch.object(
                SQLAlchemyReflectionRepository,
                "load_generation_sources",
                side_effect=AssertionError("scenario_terminal_quote_read"),
            ),
        ):
            replay = reflection_applier(database).execute(
                fixture.run.run_id, trace_id="terminal-reflection-replay"
            )
        checks["completed_apply_replay_uses_only_retained_run"] = replay == fixture.run

        def byte_state() -> tuple[bytes, bytes, bytes]:
            return (
                case.path.read_bytes(),
                case.runtime.ledger.database_path.read_bytes(),
                case.runtime.ledger.binding_path.read_bytes(),
            )

        stable = byte_state()
        assert cli([*receipt_args, "--status"])["main_projection_current"] is True
        assert cli([*receipt_args, "--resume"])["main_projection_current"] is True
        assert (
            cli(
                [
                    *base_args,
                    "--apply",
                    "--request-id",
                    command.request_id,
                    "--expected-generation",
                    str(command.expected_generation),
                    "--expected-erasure-revision",
                    str(command.expected_erasure_revision),
                    "--expected-body-revision",
                    str(command.expected_body_revision),
                    "--sha256",
                    command.approval_digest,
                ]
            )["main_projection_current"]
            is True
        )
        checks["readonly_status_noop_resume_and_original_retry_preserve_bytes"] = (
            byte_state() == stable
        )
        cli([*receipt_args, "--status"], expected_exit=2, counterparty="bob", decode=False)
        checks["foreign_counterparty_denied_without_proposal_ids"] = True
        checks["controls_invoke_zero_providers"] = (
            provider_counts(case.services) == baseline_calls
            and len(fixture.provider.requests) == baseline_reflection_calls
        )

        imported = cli(["state", "import", str(archive), "--preview"])
        assert imported["archive_sha256"] == exported["archive_sha256"]
        target = directory / "old-restored.db"
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
        restored = create_database(restored_url)
        denied_bind(restored)
        checks["actual_old_archive_restore_is_fenced"] = True
        restored_before_repair = stored_rows(target)
        body_before_repair = {
            table: restored_before_repair[table]
            for table in ("erasure_body_applications", "erasure_body_bindings")
        }
        cli(
            ["sources", "repair", "--request-id", f"exclude-{case.message_id}"],
            database_url=restored_url,
        )
        cli(
            [
                "sources",
                "erasure-intent",
                case.message_id,
                "--resume",
                "--request-id",
                request.intent_request_id,
            ],
            database_url=restored_url,
            expected_exit=2,
            decode=False,
            expected_reason="dialogue_provenance_unproven",
        )
        denied_bind(restored)
        restored_after_repair = stored_rows(target)
        checks["source_and_intent_repair_never_ack_body_head"] = body_before_repair == {
            table: restored_after_repair[table] for table in body_before_repair
        }
        assert (
            cli([*receipt_args, "--resume"], database_url=restored_url)["main_projection_current"]
            is True
        )
        bind_installation(restored, case.directory)
        checks["explicit_restore_resume_proves_actual_null_and_archive_unchanged"] = (
            archive.read_bytes() == archive_bytes
        )

        restarted = create_database(url)
        services, provider, _embedding = _services(
            restarted, case.directory, "after-reflection", 7, form=False
        )
        assert services.retrieve_memories is not None
        retrieved = asyncio.run(
            services.retrieve_memories.execute(
                RetrievalQuery(
                    text=RECALL,
                    trace_id="reflection-retrieval",
                    cutoff=datetime.now(UTC),
                    current_interaction_id=None,
                    scope=case.scope,
                    operator_query=True,
                )
            )
        )
        checks["restart_retrieval_contains_no_excluded_content"] = SECRET not in repr(retrieved)
        reply = _turn(services, RECALL, "after-reflection")
        checks["restart_foreground_projection_and_reply_clean"] = (
            len(provider.requests) == 1
            and SECRET not in repr(provider.requests[0])
            and SECRET not in str(reply)
        )

        _exclude(database, case.runtime, case.derived_message_id)
        intent = intent_manager(case)
        second_request = intent_request(case, message_id=case.derived_message_id)
        second_command = intent_command(intent, second_request, request_id="later-intent")
        assert intent.commit(second_command).main_projection_current
        later_request = BodyErasureRequest(
            message_id=case.derived_message_id,
            counterparty_id="alice",
            intent_request_id=second_command.request_id,
        )
        later_args = [
            "sources",
            "erasure-dialogue",
            later_request.message_id,
            "--intent-request-id",
            later_request.intent_request_id,
        ]
        later = cli([*later_args, "--preview"])
        result = cli(
            [
                *later_args,
                "--apply",
                "--request-id",
                "later-dialogue-cli",
                "--expected-generation",
                str(later["generation"]),
                "--expected-erasure-revision",
                str(later["erasure_revision"]),
                "--expected-body-revision",
                str(later["body_revision"]),
                "--sha256",
                str(later["approval_digest"]),
            ]
        )
        checks["later_dialogue_preserves_historical_terminal_upstream_proof"] = (
            later["user_bodies"] == 1 and result["main_projection_current"] is True
        )
        rebound = bind_installation(database, case.directory)
        with rebound.shared() as snapshot:
            actions = rebound.body_actions()
            checks["old_head_prefix_and_final_mixed_head_proven_after_restart"] = (
                snapshot.body_revision == 5
                and actions[:3] == old_actions
                and body_actions_digest(actions[:3]) == old_head
                and body_actions_digest(actions) == snapshot.body_head_digest
            )
        assert all(checks.values()), "scenario_check_failed"
        return {
            "stage": "14.4.2h",
            "synthetic_only": True,
            "real_provider_calls": 0,
            "paid_cost_usd": 0,
            "fake_reflection_calls_for_setup": baseline_reflection_calls,
            "fake_foreground_calls_after_restart": 1,
            "selected_reflection_payloads_removed": 2,
            "checks": checks,
            "timings": timings,
            "physical_erasure": False,
        }
    finally:
        for opened in (restored, restarted, database):
            if opened is not None:
                opened.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="satori-reflection-scenario-", dir="/private/tmp"
    ) as directory:
        report = run(Path(__file__).resolve().parents[1], Path(directory))
    arguments.output.write_text(json.dumps(report, indent=2) + "\n")
    checks = report["checks"]
    assert isinstance(checks, dict)
    print(json.dumps({"passed": len(checks), "physical_erasure": False, "real_provider_calls": 0}))


if __name__ == "__main__":
    main()
