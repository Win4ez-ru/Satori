"""Synthetic fresh-process body handling, atomic failure and old-archive recovery drill.

Only metadata and timings are emitted. No existing installation or real provider is selected.
Run: uv run --no-sync python -m tests.erasure_bodies_scenario --output /private/tmp/report.json
"""

import argparse
import asyncio
import json
import math
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter

from sqlalchemy import event, select
from sqlalchemy.engine import Connection

from satori.application.erasure.body_contracts import BodyErasureRequest, BodyErasureResult
from satori.application.erasure.body_use_cases import BodyErasureManager
from satori.application.retrieval.contracts import RetrievalQuery, RetrievalStatus
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.erasure_bodies import SQLiteBodyErasureStore
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.models import (
    ConversationMessageRow,
    EpisodicMemoryRow,
    MemoryEvidenceRow,
    SemanticClaimRow,
)
from satori.infrastructure.source_policy.installation import bind_installation
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.erasure_preview_scenario import CLI_ENTRY
from tests.erasure_support import create_case, provider_counts
from tests.test_erasure_bodies_integration import command_for
from tests.test_erasure_intent_integration import _command as intent_command
from tests.test_erasure_intent_integration import _manager as intent_manager
from tests.test_erasure_intent_integration import _request as intent_request
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import (
    DERIVE_TEXT,
    RECALL,
    ROOT_TEXT,
    SECRET,
    _services,
    _turn,
)


def run(
    project_root: Path, directory: Path, *, cost_report: dict[str, object] | None = None
) -> dict[str, object]:
    timings: list[dict[str, object]] = []
    url = f"sqlite+pysqlite:///{directory / 'synthetic.db'}"
    upgrade_database(url, config_path=project_root / "alembic.ini")
    database = create_database(url)
    restored_database = None
    try:
        case = create_case(database, directory / "trusted-policy")
        protected = {SECRET, ROOT_TEXT, DERIVE_TEXT}
        with database.session_factory() as session:
            for column in (
                ConversationMessageRow.content,
                EpisodicMemoryRow.summary,
                MemoryEvidenceRow.quote,
                SemanticClaimRow.value,
            ):
                protected.update(
                    str(value) for value in session.scalars(select(column)) if value is not None
                )
        descendants = (
            case.derived_message_id,
            case.derived_interaction_id,
            *case.episode_ids,
            *case.claim_ids,
        )
        environment = {
            **os.environ,
            "SATORI_DATABASE_URL": url,
            "SATORI_SOURCE_POLICY_DIRECTORY": str(case.directory),
            "SATORI_DEFAULT_COUNTERPARTY_ID": "alice",
            "SATORI_ENVIRONMENT": "test",
            "SATORI_LOG_LEVEL": "CRITICAL",
        }

        def phase(name: str, operation: Callable[[], object]) -> object:
            started = perf_counter()
            result = operation()
            timings.append(
                {"phase": name, "elapsed_ms": round((perf_counter() - started) * 1000, 3)}
            )
            return result

        def measure(name: str, operation: Callable[[], object]) -> None:
            if cost_report is None:
                return
            samples = []
            for _ in range(50):
                started = perf_counter()
                operation()
                samples.append((perf_counter() - started) * 1000)
            ordered = sorted(samples)
            cost_report[name] = {
                "sample_count": len(samples),
                "samples_ms": [round(sample, 3) for sample in samples],
                "median_ms": round(median(samples), 3),
                "p95_ms": round(ordered[math.ceil(len(samples) * 0.95) - 1], 3),
            }

        def cli(
            arguments: list[str],
            *,
            expected_exit: int = 0,
            database_url: str = url,
            require_json: bool = True,
            counterparty: str = "alice",
        ) -> dict[str, object]:
            completed = subprocess.run(
                [sys.executable, "-c", CLI_ENTRY, *arguments],
                cwd=project_root,
                env={
                    **environment,
                    "SATORI_DATABASE_URL": database_url,
                    "SATORI_DEFAULT_COUNTERPARTY_ID": counterparty,
                },
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert completed.returncode == expected_exit, "scenario_cli_exit_mismatch"
            output = completed.stdout + completed.stderr
            assert all(value not in output for value in protected), "scenario_content_leak"
            assert all(value not in output for value in descendants), "scenario_descendant_leak"
            if counterparty != "alice":
                assert all(
                    value not in output
                    for value in (case.message_id, case.interaction_id, case.scope.identity_id)
                ), "scenario_foreign_root_leak"
            if not require_json:
                return {}
            decoded: object = json.JSONDecoder().raw_decode(completed.stdout.lstrip())[0]
            assert isinstance(decoded, dict), "scenario_metadata_shape"
            return decoded

        manager = intent_manager(case)
        intent = intent_command(manager, intent_request(case), request_id="scenario-body-intent")
        prepared = phase("durable_intent_and_owner_preparation", lambda: manager.commit(intent))
        assert getattr(prepared, "main_projection_current", False), "scenario_intent_pending"
        with case.runtime.exclusive(), case.runtime.ledger._connection() as floor_connection:
            assert (
                floor_connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                == "policy_0002"
            )
        phase(
            "fresh_cli_explicit_body_floor_upgrade",
            lambda: cli(["sources", "erasure-body-policy", "--upgrade", "--reviewed"]),
        )
        with case.runtime.exclusive(), case.runtime.ledger._connection() as floor_connection:
            assert (
                floor_connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                == "policy_0003"
            )
        request = BodyErasureRequest(
            message_id=case.message_id,
            counterparty_id="alice",
            intent_request_id=intent.request_id,
        )
        body = BodyErasureManager(case.runtime, SQLiteBodyErasureStore(database))
        command = command_for(body, request, "scenario-body-action")
        args = [
            "sources",
            "erasure-bodies",
            case.message_id,
            "--intent-request-id",
            intent.request_id,
        ]
        preview = cli([*args, "--preview"])
        assert (preview["summaries"], preview["quotes"], preview["vectors"]) == (2, 2, 2)
        assert preview["readiness"] == "ready"
        assert preview["approval_digest"] == command.approval_digest
        archive = directory / "after-intent-before-body.state"
        exported = phase(
            "actual_cli_after_intent_before_body_archive",
            lambda: cli(["state", "export", "--output", str(archive)]),
        )
        assert isinstance(exported, dict)
        archive_bytes = archive.read_bytes()
        before = stored_rows(case.path)
        measured_runtime = bind_installation(database, case.directory)

        def admission_before() -> None:
            with measured_runtime.shared() as snapshot:
                assert snapshot.body_revision == 0

        measure("shared_admission_body_revision_0", admission_before)
        observed = {
            "vectors_absent": False,
            "summary_transition_started": False,
            "quotes_present": False,
        }

        def fail_quote(
            connection: Connection,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _many: bool,
        ) -> None:
            if statement.startswith("UPDATE memory_evidence SET"):
                observed["vectors_absent"] = (
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM episodic_memory_embeddings"
                    ).scalar_one()
                    == 0
                )
                observed["summary_transition_started"] = (
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM episodic_memories WHERE summary IS NULL"
                    ).scalar_one()
                    == 1
                )
                observed["quotes_present"] = (
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM memory_evidence WHERE quote IS NOT NULL"
                    ).scalar_one()
                    == 2
                )
                raise RuntimeError("synthetic_mid_owner_failure")

        event.listen(database.engine, "before_cursor_execute", fail_quote)
        try:
            pending = phase("floor_committed_mid_owner_failure", lambda: body.commit(command))
        finally:
            event.remove(database.engine, "before_cursor_execute", fail_quote)
        assert isinstance(pending, BodyErasureResult)
        assert pending.phase == "body_application_pending"
        assert not pending.main_projection_current
        assert all(observed.values()), "scenario_failure_not_at_target_boundary"
        assert stored_rows(case.path) == before, "scenario_partial_main_commit"
        with case.runtime.exclusive() as snapshot:
            assert snapshot.generation == command.expected_generation
            assert snapshot.erasure_revision == command.expected_erasure_revision
            assert snapshot.body_revision == 1
            assert case.runtime.erasure_intent(intent.request_id) is not None
            assert case.runtime.body_action(command.request_id) is not None
        try:
            bind_installation(database, case.directory)
        except SourcePolicyError as error:
            if error.reason_code != "body_reconciliation_required":
                raise AssertionError("scenario_unexpected_pending_admission_reason") from None
        else:
            raise AssertionError("scenario_pending_body_admitted")
        status_args = [*args, "--status", "--request-id", command.request_id]
        fresh_pending = phase("fresh_cli_pending_status", lambda: cli(status_args, expected_exit=2))
        assert isinstance(fresh_pending, dict)
        assert fresh_pending["phase"] == "body_application_pending"
        done = phase(
            "fresh_cli_resume_removes_selected_bodies",
            lambda: cli([*args, "--resume", "--request-id", command.request_id]),
        )
        assert isinstance(done, dict)
        assert done["main_projection_current"] is True
        assert done["selected_bodies_removed"] is True
        assert done["physical_erasure"] is False
        after = stored_rows(case.path)
        with database.engine.connect() as main_connection:
            assert (
                main_connection.exec_driver_sql(
                    "SELECT count(*) FROM episodic_memories "
                    "WHERE summary IS NULL AND body_state='erased'"
                ).scalar_one()
                == 2
            )
            assert (
                main_connection.exec_driver_sql(
                    "SELECT count(*) FROM memory_evidence "
                    "WHERE quote IS NULL AND body_state='erased'"
                ).scalar_one()
                == 2
            )
            assert (
                main_connection.exec_driver_sql(
                    "SELECT count(*) FROM episodic_memory_embeddings"
                ).scalar_one()
                == 0
            )
            assert main_connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert main_connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        changed = {
            "episodic_memories",
            "memory_evidence",
            "episodic_memory_embeddings",
            "erasure_body_applications",
            "erasure_body_bindings",
        }
        assert {name: rows for name, rows in after.items() if name not in changed} == {
            name: rows for name, rows in before.items() if name not in changed
        }, "scenario_unrelated_owner_changed"
        for table in ("episodic_memories", "memory_evidence"):
            assert [row[:4] + row[5:-2] for row in after[table]] == [
                row[:4] + row[5:-2] for row in before[table]
            ], "scenario_retained_owner_header_changed"
        phase(
            "fresh_cli_original_apply_replay_noop",
            lambda: cli(
                [
                    *args,
                    "--apply",
                    "--expected-generation",
                    str(command.expected_generation),
                    "--expected-erasure-revision",
                    str(command.expected_erasure_revision),
                    "--expected-body-revision",
                    str(command.expected_body_revision),
                    "--sha256",
                    command.approval_digest,
                    "--request-id",
                    command.request_id,
                ]
            ),
        )
        assert stored_rows(case.path) == after
        byte_state = (case.path.read_bytes(), case.runtime.ledger.database_path.read_bytes())
        current = cli(status_args)
        assert current["main_projection_current"] is True
        assert byte_state == (
            case.path.read_bytes(),
            case.runtime.ledger.database_path.read_bytes(),
        )
        measured_runtime = bind_installation(database, case.directory)
        measured_manager = BodyErasureManager(measured_runtime, SQLiteBodyErasureStore(database))

        def admission_after() -> None:
            with measured_runtime.shared() as snapshot:
                assert snapshot.body_revision == 1

        measure("shared_admission_body_revision_1", admission_after)
        measure(
            "scoped_status_current",
            lambda: measured_manager.status(request, request_id=command.request_id),
        )
        measure(
            "no_op_resume_current",
            lambda: measured_manager.resume(request, request_id=command.request_id),
        )
        assert stored_rows(case.path) == after, "scenario_cost_probe_mutated_main"
        assert byte_state == (
            case.path.read_bytes(),
            case.runtime.ledger.database_path.read_bytes(),
        )
        if cost_report is not None:
            first_application = next(
                item
                for item in timings
                if item["phase"] == "fresh_cli_resume_removes_selected_bodies"
            )
            cost_report.update(
                {
                    "measurement": "small_synthetic_episode_body_owner_fixture",
                    "schema_revision": str(before["alembic_version"][0][0]),
                    "floor_schema_revision": "policy_0003",
                    "selected_episodes": 2,
                    "selected_quotes": 2,
                    "selected_vectors": 2,
                    "scanned_rows": preview["scanned_rows"],
                    "first_successful_application_fresh_cli_ms": first_application["elapsed_ms"],
                    "first_application_includes_process_startup": True,
                    "control_provider_calls": 0,
                    "paid_provider_calls": 0,
                    "paid_cost_usd": 0,
                    "foreground_latency_measured": False,
                    "large_store_scalability_measured": False,
                }
            )
        phase(
            "foreign_scope_status_denied_without_identifiers",
            lambda: cli(status_args, expected_exit=2, require_json=False, counterparty="bob"),
        )

        restored = directory / "restored-old.db"
        import_preview = phase(
            "actual_cli_old_archive_import_preview",
            lambda: cli(["state", "import", str(archive), "--preview"]),
        )
        assert isinstance(import_preview, dict)
        assert import_preview["archive_sha256"] == exported["archive_sha256"]
        phase(
            "actual_cli_old_archive_restore",
            lambda: cli(
                [
                    "state",
                    "import",
                    str(archive),
                    "--activate",
                    "--target",
                    str(restored),
                    "--sha256",
                    str(import_preview["archive_sha256"]),
                ]
            ),
        )
        old_url = f"sqlite+pysqlite:///{restored}"
        restored_handle = create_database(old_url)
        restored_database = restored_handle

        def denied_bind() -> None:
            try:
                bind_installation(restored_handle, case.directory)
            except SourcePolicyError as error:
                if error.reason_code != "body_reconciliation_required":
                    raise AssertionError("scenario_unexpected_restore_admission_reason") from None
            else:
                raise AssertionError("scenario_old_body_main_admitted")

        phase("old_main_runtime_body_head_fence", denied_bind)
        assert (
            cli(status_args, expected_exit=2, database_url=old_url)["main_projection_current"]
            is False
        )
        phase(
            "ordinary_source_repair_cannot_ack_body",
            lambda: cli(
                ["sources", "repair", "--request-id", f"exclude-{case.message_id}"],
                database_url=old_url,
            ),
        )
        intent_pending = phase(
            "intent_resume_cannot_ack_body",
            lambda: cli(
                [
                    "sources",
                    "erasure-intent",
                    case.message_id,
                    "--resume",
                    "--request-id",
                    intent.request_id,
                ],
                database_url=old_url,
                expected_exit=2,
            ),
        )
        assert isinstance(intent_pending, dict)
        assert intent_pending["phase"] == "intent_committed_preparation_pending"
        assert intent_pending["preparation_current"] is False
        assert intent_pending["main_projection_current"] is False
        assert intent_pending["failure_reason"] == "body_reconciliation_required"
        assert intent_pending["physical_erasure"] is False
        denied_bind()
        assert (
            cli(status_args, expected_exit=2, database_url=old_url)["main_projection_current"]
            is False
        )
        phase(
            "fresh_cli_restored_main_body_resume",
            lambda: cli(
                [*args, "--resume", "--request-id", command.request_id], database_url=old_url
            ),
        )
        bind_installation(restored_database, case.directory)
        assert archive.read_bytes() == archive_bytes
        assert provider_counts(case.services) == case.initial_provider_calls
        fresh_runtime = SourcePolicyRuntime(case.directory, case.scope.identity_id)
        with fresh_runtime.shared() as snapshot:
            assert snapshot.body_revision == 1
            assert snapshot.erasure_revision == 1
        restarted, provider, embedding = _services(
            restored_database, case.directory, "body-drill-restart", 12, form=False
        )
        reply = phase(
            "restart_fake_foreground_without_erased_memory",
            lambda: _turn(restarted, RECALL, "body-drill-restart"),
        )
        assert SECRET not in str(getattr(reply, "text", ""))
        assert SECRET not in repr(provider.requests)
        assert SECRET not in repr(embedding.requests)
        assert restarted.retrieve_memories is not None
        retrieved = asyncio.run(
            restarted.retrieve_memories.execute(
                RetrievalQuery(
                    text=RECALL,
                    trace_id="body-drill-retrieval",
                    cutoff=datetime.now(UTC),
                    current_interaction_id=None,
                    scope=case.scope,
                    operator_query=True,
                )
            )
        )
        assert retrieved.status is RetrievalStatus.NO_RELEVANT_MEMORY
        assert SECRET not in repr(retrieved)
        residuals = done["residual_surfaces"]
        assert isinstance(residuals, list)
        report: dict[str, object] = {
            "scenario": "terminal-episodic-bodies-restart-recovery-v1",
            "schema_revision": str(before["alembic_version"][0][0]),
            "floor_schema_revision": "policy_0003",
            "checks": {
                "real_mid_owner_failure_and_exact_rollback": True,
                "intent_and_action_survive_main_failure": True,
                "fresh_process_pending_then_resumed": True,
                "summaries_quotes_removed_vectors_absent": True,
                "other_owners_numeric_trajectory_retained": True,
                "original_apply_replay_is_noop": True,
                "read_only_status_preserves_main_and_floor_bytes": True,
                "foreign_scope_rejected_without_identifiers": True,
                "old_archive_runtime_denied_by_body_head": True,
                "source_and_intent_resume_cannot_ack_body": True,
                "explicit_restored_main_body_resume_is_recoverable": True,
                "restart_reply_and_retrieval_do_not_use_erased_content": True,
                "old_archive_retained_byte_for_byte": True,
                "public_output_has_no_body_or_descendant_identifiers": True,
            },
            "removed": {"summaries": 2, "quotes": 2, "vectors": 2},
            "phase": "body_application_complete",
            "main_projection_current": True,
            "selected_bodies_removed": True,
            "source_generation": command.expected_generation,
            "erasure_revision": 1,
            "body_revision": 1,
            "coverage": "partial",
            "residual_surfaces": residuals,
            "control_provider_calls": 0,
            "restart_fake_foreground_calls": len(provider.requests),
            "paid_provider_calls": 0,
            "physical_erasure": False,
            "timings": timings,
        }
        serialized = json.dumps(report, sort_keys=True)
        assert all(value not in serialized for value in protected)
        assert all(
            value not in serialized
            for value in (*descendants, case.message_id, case.interaction_id)
        )
        return report
    finally:
        if restored_database is not None:
            restored_database.dispose()
        database.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cost-output", type=Path)
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="satori-erasure-bodies-scenario-") as temporary:
        cost_report: dict[str, object] | None = {} if arguments.cost_output is not None else None
        report = run(Path(__file__).resolve().parents[1], Path(temporary), cost_report=cost_report)
    payload = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    if arguments.output is None:
        print(payload.decode(), end="")
    else:
        PrivateArtifactStore().write(arguments.output, payload)
        print(json.dumps({"report_written": True, "physical_erasure": False}))
    if arguments.cost_output is not None and cost_report is not None:
        PrivateArtifactStore().write(
            arguments.cost_output,
            (json.dumps(cost_report, sort_keys=True, indent=2) + "\n").encode(),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
