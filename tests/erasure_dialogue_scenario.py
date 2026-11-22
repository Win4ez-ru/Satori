"""Synthetic actual CLI/restart/recovery drill. Reports contain metadata only."""

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

from sqlalchemy import event, select
from sqlalchemy.engine import Connection

from satori.application.erasure.body_contracts import BodyErasureCommand
from satori.application.erasure.dialogue_use_cases import DialogueBodyManager
from satori.application.retrieval.contracts import RetrievalQuery, RetrievalStatus
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.erasure_dialogue import SQLiteDialogueBodyStore
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.models import ConversationMessageRow
from satori.infrastructure.source_policy.installation import bind_installation
from tests.erasure_preview_scenario import CLI_ENTRY
from tests.erasure_support import create_case, provider_counts
from tests.test_erasure_bodies_integration import command_for, prepare
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import (
    DERIVE_TEXT,
    RECALL,
    ROOT_TEXT,
    SECRET,
    _services,
    _turn,
)


def run(project: Path, directory: Path) -> dict[str, object]:
    url = f"sqlite+pysqlite:///{directory / 'synthetic.db'}"
    upgrade_database(url, config_path=project / "alembic.ini")
    database = create_database(url)
    restored = None
    timings: list[dict[str, object]] = []
    checks: dict[str, bool] = {}
    try:
        case = create_case(database, directory / "trusted-floor")
        body, request = prepare(case)
        assert body.commit(command_for(body, request)).main_projection_current
        baseline_calls = provider_counts(case.services)
        with database.session_factory() as session:
            texts = {
                str(value)
                for value in session.scalars(select(ConversationMessageRow.content))
                if value
            }
        protected = texts | {
            SECRET,
            ROOT_TEXT,
            DERIVE_TEXT,
            case.derived_message_id,
            case.derived_interaction_id,
            *case.episode_ids,
            *case.claim_ids,
        }
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
        ) -> dict[str, object]:
            start = perf_counter()
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
                timeout=30,
            )
            timings.append(
                {
                    "phase": args[1] if len(args) > 1 else args[0],
                    "duration_ms": round((perf_counter() - start) * 1000, 3),
                    "exit_code": result.returncode,
                }
            )
            assert result.returncode == expected_exit, "scenario_cli_exit_mismatch"
            output = result.stdout + result.stderr
            assert all(value not in output for value in protected), "scenario_content_disclosure"
            if counterparty != "alice":
                assert all(
                    value not in output
                    for value in (case.message_id, case.interaction_id, case.scope.identity_id)
                ), "scenario_foreign_scope_disclosure"
            if not decode:
                return {}
            decoded = json.JSONDecoder().raw_decode(result.stdout.lstrip())[0]
            assert isinstance(decoded, dict)
            return decoded

        cli(["sources", "erasure-dialogue-policy", "--upgrade", "--reviewed"])
        archive = directory / "before-dialogue.zip"
        cli(["state", "export", "--output", str(archive)])
        preview = cli(
            [
                "sources",
                "erasure-dialogue",
                case.message_id,
                "--intent-request-id",
                request.intent_request_id,
                "--preview",
            ]
        )
        checks["complete_selection"] = (preview["user_bodies"], preview["assistant_bodies"]) == (
            1,
            2,
        )
        assert checks["complete_selection"]
        apply_args = [
            "sources",
            "erasure-dialogue",
            case.message_id,
            "--intent-request-id",
            request.intent_request_id,
            "--apply",
            "--request-id",
            "dialogue-cli",
            "--expected-generation",
            str(preview["generation"]),
            "--expected-erasure-revision",
            str(preview["erasure_revision"]),
            "--expected-body-revision",
            str(preview["body_revision"]),
            "--sha256",
            str(preview["approval_digest"]),
        ]
        before = stored_rows(case.path)
        observed = 0
        writes = 0

        def fail(
            connection: Connection,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            many: bool,
        ) -> None:
            nonlocal writes, observed
            if statement.startswith("UPDATE conversation_messages"):
                writes += 1
                if writes == 3:
                    observed = int(
                        connection.exec_driver_sql(
                            "SELECT count(*) FROM conversation_messages WHERE content IS NULL"
                        ).scalar_one()
                    )
                    raise RuntimeError("synthetic_mid_dialogue_owner_failure")

        event.listen(database.engine, "before_cursor_execute", fail)
        try:
            pending = DialogueBodyManager(case.runtime, SQLiteDialogueBodyStore(database)).commit(
                BodyErasureCommand(
                    **request.model_dump(),
                    request_id="dialogue-cli",
                    expected_generation=int(str(preview["generation"])),
                    expected_erasure_revision=int(str(preview["erasure_revision"])),
                    expected_body_revision=int(str(preview["body_revision"])),
                    approval_digest=str(preview["approval_digest"]),
                )
            )
        finally:
            event.remove(database.engine, "before_cursor_execute", fail)
        checks["mid_owner_failure_atomic_rollback"] = (
            observed == 2
            and not pending.main_projection_current
            and stored_rows(case.path) == before
        )
        receipt_args = [
            "sources",
            "erasure-dialogue",
            case.message_id,
            "--intent-request-id",
            request.intent_request_id,
            "--request-id",
            "dialogue-cli",
        ]
        assert cli([*receipt_args, "--status"], expected_exit=2)["main_projection_current"] is False
        checks["floor_survives_and_fresh_process_withholds"] = True
        assert cli([*receipt_args, "--resume"])["main_projection_current"] is True
        checks["fresh_process_resume_after_interruption"] = True
        result = cli(apply_args)
        assert result["main_projection_current"] is True
        assert result["physical_erasure"] is False
        checks["terminal_actual_bodies"] = False
        with database.session_factory() as session:
            messages = tuple(session.scalars(select(ConversationMessageRow)))
            checks["terminal_actual_bodies"] = sum(row.content is None for row in messages) == 3
            checks["independent_user_retained"] = (
                next(row for row in messages if row.message_id == case.derived_message_id).content
                is not None
            )
        checks["controls_zero_provider_calls"] = provider_counts(case.services) == baseline_calls
        receipt_args = [
            "sources",
            "erasure-dialogue",
            case.message_id,
            "--intent-request-id",
            request.intent_request_id,
            "--request-id",
            "dialogue-cli",
        ]
        assert cli([*receipt_args, "--status"])["main_projection_current"] is True
        assert cli([*receipt_args, "--resume"])["main_projection_current"] is True
        checks["fresh_process_status_resume"] = True
        assert cli(apply_args)["main_projection_current"] is True
        checks["exact_request_replay"] = True
        cli([*receipt_args, "--status"], expected_exit=2, counterparty="bob", decode=False)
        checks["counterparty_denied"] = True
        services, provider, _embedding = _services(
            database, case.directory, "after-null", 1, form=False
        )
        assert services.retrieve_memories is not None
        retrieval = asyncio.run(
            services.retrieve_memories.execute(
                RetrievalQuery(
                    text=RECALL,
                    trace_id="dialogue-retrieval",
                    cutoff=datetime.now(UTC),
                    current_interaction_id=None,
                    scope=case.scope,
                    operator_query=True,
                )
            )
        )
        checks["retrieval_after_restart_empty"] = (
            retrieval.status is RetrievalStatus.NO_RELEVANT_MEMORY
        )
        _turn(services, RECALL, "after-null")
        checks["provider_projection_after_restart_clean"] = len(
            provider.requests
        ) == 1 and SECRET not in str(provider.requests[0])
        import_preview = cli(["state", "import", str(archive), "--preview"])
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
                str(import_preview["archive_sha256"]),
            ]
        )
        restored_url = f"sqlite+pysqlite:///{target}"
        cli([*receipt_args, "--status"], database_url=restored_url, expected_exit=2)
        restored = create_database(restored_url)
        try:
            bind_installation(restored, case.directory)
        except SourcePolicyError as error:
            if error.reason_code != "body_reconciliation_required":
                raise AssertionError("unexpected_restore_fence") from error
            checks["old_archive_body_fence"] = True
        else:
            raise AssertionError("old_archive_admitted")
        assert (
            cli([*receipt_args, "--resume"], database_url=restored_url)["main_projection_current"]
            is True
        )
        rebound = bind_installation(restored, case.directory)
        with rebound.shared() as snapshot:
            checks["old_archive_explicit_resume"] = snapshot.body_revision == 2
        cli(["sources", "erasure", case.message_id, "--preview"], expected_exit=2)
        checks["historical_operator_inventory_works"] = True
        assert all(checks.values()), "scenario_check_failed"
        return {
            "stage": "14.4.2f",
            "synthetic_only": True,
            "real_provider_calls": 0,
            "paid_cost_usd": 0,
            "fake_foreground_calls_after_restart": 1,
            "checks": checks,
            "timings": timings,
            "selected_user_bodies_removed": 1,
            "selected_assistant_bodies_removed": 2,
            "historical_witnesses": preview["historical_witnesses"],
            "physical_erasure": False,
        }
    finally:
        if restored is not None:
            restored.dispose()
        database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(
        prefix="satori-dialogue-scenario-", dir="/private/tmp"
    ) as directory:
        report = run(project, Path(directory))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    checks = report["checks"]
    assert isinstance(checks, dict)
    print(json.dumps({"passed": len(checks), "physical_erasure": False, "real_provider_calls": 0}))


if __name__ == "__main__":
    main()
