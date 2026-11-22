"""Actual private CLI, mixed-owner interruption and restart/recovery adversary."""

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

from sqlalchemy import event
from sqlalchemy.engine import Connection

from satori.application.erasure.body_contracts import BodyErasureCommand, BodyErasureRequest
from satori.application.erasure.knowledge_use_cases import KnowledgeBodyManager
from satori.application.retrieval.contracts import RetrievalQuery, RetrievalStatus
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.erasure_knowledge import SQLiteKnowledgeBodyStore
from satori.infrastructure.persistence.knowledge_material import KNOWLEDGE_OWNER_SPECS
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.source_policy.installation import bind_installation
from tests.erasure_knowledge_support import add_knowledge
from tests.erasure_preview_scenario import CLI_ENTRY
from tests.erasure_support import create_case, provider_counts
from tests.test_erasure_dialogue_integration import dialogue_command, prepare_dialogue
from tests.test_erasure_intent_integration import _command as intent_command
from tests.test_erasure_intent_integration import _manager as intent_manager
from tests.test_erasure_intent_integration import _request as intent_request
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
        case = create_case(database, directory / "trusted-floor", excluded=False)
        add_knowledge(case)
        _exclude(database, case.runtime, case.message_id)
        dialogue, request = prepare_dialogue(case)
        assert dialogue.commit(dialogue_command(dialogue, request)).main_projection_current
        baseline_calls = provider_counts(case.services)
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
            assert result.returncode == expected_exit, "scenario_cli_exit_mismatch"
            assert SECRET not in result.stdout + result.stderr, "scenario_content_disclosure"
            assert ROOT_TEXT not in result.stdout + result.stderr, "scenario_content_disclosure"
            if counterparty != "alice":
                assert case.message_id not in result.stdout + result.stderr
            if not decode:
                return {}
            value = json.JSONDecoder().raw_decode(result.stdout.lstrip())[0]
            assert isinstance(value, dict)
            return value

        cli(["sources", "erasure-knowledge-policy", "--upgrade", "--reviewed"])
        archive = directory / "before-knowledge.state"
        cli(["state", "export", "--output", str(archive)])
        base_args = [
            "sources",
            "erasure-knowledge",
            case.message_id,
            "--intent-request-id",
            request.intent_request_id,
        ]
        preview = cli([*base_args, "--preview"])
        checks["five_owner_kinds_selected"] = tuple(
            preview[name]
            for name in (
                "semantic_claims",
                "user_model_claims",
                "world_model_claims",
                "positions",
                "inclinations",
            )
        ) == (4, 1, 1, 1, 1)
        checks["old_null_and_live_position_witnesses_selected"] = (
            preview["witness_transitions"] == 2
        )
        command = BodyErasureCommand(
            **request.model_dump(),
            request_id="knowledge-cli",
            expected_generation=int(str(preview["generation"])),
            expected_erasure_revision=int(str(preview["erasure_revision"])),
            expected_body_revision=int(str(preview["body_revision"])),
            approval_digest=str(preview["approval_digest"]),
        )
        before = stored_rows(case.path)
        observed = 0

        def fail(
            connection: Connection,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            many: bool,
        ) -> None:
            nonlocal observed
            if statement.startswith("UPDATE world_model_claims SET"):
                observed = int(
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM semantic_claims WHERE value IS NULL"
                    ).scalar_one()
                ) + int(
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM user_model_claims WHERE value IS NULL"
                    ).scalar_one()
                )
                raise RuntimeError("synthetic_mid_knowledge_owner_failure")

        event.listen(database.engine, "before_cursor_execute", fail)
        try:
            pending = KnowledgeBodyManager(case.runtime, SQLiteKnowledgeBodyStore(database)).commit(
                command
            )
        finally:
            event.remove(database.engine, "before_cursor_execute", fail)
        checks["cross_owner_failure_atomic_rollback"] = (
            observed == 5
            and not pending.main_projection_current
            and stored_rows(case.path) == before
        )
        receipt_args = [*base_args, "--request-id", command.request_id]
        assert cli([*receipt_args, "--status"], expected_exit=2)["main_projection_current"] is False
        try:
            bind_installation(database, case.directory)
        except SourcePolicyError as error:
            if error.reason_code != "body_reconciliation_required":
                raise AssertionError("unexpected_body_fence") from error
        else:
            raise AssertionError("pending_head_admitted")
        checks["floor_first_pending_startup_fenced"] = True
        assert cli([*receipt_args, "--resume"])["main_projection_current"] is True
        checks["fresh_process_resumes_whole_head"] = True
        with database.engine.connect() as connection:
            checks["all_selected_content_sql_null"] = all(
                connection.exec_driver_sql(
                    f"SELECT count(*) FROM \"{spec.table}\" WHERE body_state!='erased' OR "
                    + " OR ".join(f'"{column}" IS NOT NULL' for column in spec.content_columns)
                ).scalar_one()
                == 0
                for spec in KNOWLEDGE_OWNER_SPECS.values()
            )
            checks["position_quote_and_signature_sql_null"] = (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM satori_position_evidence "
                    "WHERE quote IS NOT NULL OR normalized_signature IS NOT NULL"
                ).scalar_one()
                == 0
            )
            checks["independent_user_retained"] = (
                connection.exec_driver_sql(
                    "SELECT content IS NOT NULL FROM conversation_messages WHERE message_id=?",
                    (case.derived_message_id,),
                ).scalar_one()
                == 1
            )
            checks["foreign_keys_and_integrity"] = (
                connection.exec_driver_sql("PRAGMA foreign_key_check").first() is None
                and connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
            )
        after = stored_rows(case.path)
        assert cli([*receipt_args, "--status"])["main_projection_current"] is True
        assert cli([*receipt_args, "--resume"])["main_projection_current"] is True
        checks["read_only_status_noop_resume_stable"] = stored_rows(case.path) == after
        cli([*receipt_args, "--status"], expected_exit=2, counterparty="bob", decode=False)
        checks["foreign_counterparty_denied"] = True
        checks["controls_zero_provider_calls"] = provider_counts(case.services) == baseline_calls

        imported = cli(["state", "import", str(archive), "--preview"])
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
        try:
            bind_installation(restored, case.directory)
        except SourcePolicyError as error:
            if error.reason_code != "body_reconciliation_required":
                raise AssertionError("unexpected_body_fence") from error
        else:
            raise AssertionError("old_archive_admitted")
        checks["old_archive_restored_content_fenced"] = True
        assert (
            cli([*receipt_args, "--resume"], database_url=restored_url)["main_projection_current"]
            is True
        )
        bind_installation(restored, case.directory)
        checks["old_archive_explicit_resume_proven"] = True

        restarted = create_database(url)
        services, provider, _embedding = _services(
            restarted, case.directory, "after-knowledge", 1, form=False
        )
        assert services.retrieve_memories is not None
        retrieved = asyncio.run(
            services.retrieve_memories.execute(
                RetrievalQuery(
                    text=RECALL,
                    trace_id="knowledge-retrieval",
                    cutoff=datetime.now(UTC),
                    current_interaction_id=None,
                    scope=case.scope,
                    operator_query=True,
                )
            )
        )
        checks["restart_retrieval_empty"] = retrieved.status is RetrievalStatus.NO_RELEVANT_MEMORY
        reply = _turn(services, RECALL, "after-knowledge")
        checks["restart_provider_context_and_reply_clean"] = (
            len(provider.requests) == 1
            and SECRET not in str(provider.requests[0])
            and SECRET not in str(reply)
        )

        _exclude(database, case.runtime, case.derived_message_id)
        intent = intent_manager(case)
        second_request = intent_request(case, message_id=case.derived_message_id)
        second_command = intent_command(intent, second_request, request_id="second-intent")
        assert intent.commit(second_command).main_projection_current
        second_body = BodyErasureRequest(
            message_id=case.derived_message_id,
            counterparty_id="alice",
            intent_request_id=second_command.request_id,
        )
        second_args = [
            "sources",
            "erasure-dialogue",
            second_body.message_id,
            "--intent-request-id",
            second_body.intent_request_id,
        ]
        later = cli([*second_args, "--preview"])
        assert later["user_bodies"] == 1
        result = cli(
            [
                *second_args,
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
        checks["later_dialogue_accepts_approved_terminal_evidence"] = (
            result["main_projection_current"] is True
        )
        rebound = bind_installation(database, case.directory)
        with rebound.shared() as snapshot:
            checks["later_mixed_head_restart_proven"] = snapshot.body_revision == 4
        assert all(checks.values()), "scenario_check_failed"
        return {
            "stage": "14.4.2g",
            "synthetic_only": True,
            "real_provider_calls": 0,
            "paid_cost_usd": 0,
            "fake_foreground_calls_after_restart": 1,
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
        prefix="satori-knowledge-scenario-", dir="/private/tmp"
    ) as directory:
        report = run(Path(__file__).resolve().parents[1], Path(directory))
    arguments.output.write_text(json.dumps(report, indent=2) + "\n")
    checks = report["checks"]
    assert isinstance(checks, dict)
    print(json.dumps({"passed": len(checks), "physical_erasure": False, "real_provider_calls": 0}))


if __name__ == "__main__":
    main()
