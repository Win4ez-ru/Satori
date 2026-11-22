"""Private actual CLI/restart/archive drill for paired appraisal explanations.

Only synthetic managed data and fake cognitive ports are used. Reports contain
counts, assertions and timings, never body material or private selectors.
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

from sqlalchemy import event
from sqlalchemy.engine import Connection

from satori.application.erasure.body_contracts import BodyErasureCommand
from satori.application.retrieval.contracts import RetrievalQuery
from satori.domain.erasure_bodies import body_actions_digest
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.repositories.affect import SQLAlchemyAffectiveStateRepository
from satori.infrastructure.source_policy.installation import bind_installation
from tests.erasure_appraisal_support import create_appraisal_fixture, prepare_appraisal
from tests.erasure_preview_scenario import CLI_ENTRY
from tests.erasure_support import provider_counts
from tests.test_erasure_appraisal_signal_continuity import retained_signals
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import RECALL, ROOT_TEXT, SECRET, _services, _turn


def run(project: Path, directory: Path) -> dict[str, object]:
    url = f"sqlite+pysqlite:///{directory / 'synthetic.db'}"
    upgrade_database(url, config_path=project / "alembic.ini")
    database = create_database(url)
    restored = restarted = None
    checks: dict[str, bool] = {}
    timings: list[dict[str, object]] = []
    try:
        fixture = create_appraisal_fixture(database, directory / "trusted-floor")
        case = fixture.case
        manager, request = prepare_appraisal(fixture)
        calls = (
            provider_counts(case.services),
            len(fixture.provider.requests),
            tuple(
                len(getattr(service.talk.prepare_affect.provider, "requests", ()))
                for service in case.services
                if service.talk.prepare_affect is not None
            ),
        )
        with case.runtime.shared() as snapshot:
            old_actions, old_head = case.runtime.body_actions(), snapshot.body_head_digest
        assert len(old_actions) == 4
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
            completed = subprocess.run(
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
                    "exit_code": completed.returncode,
                }
            )
            assert completed.returncode == expected_exit, "scenario_cli_exit_mismatch"
            combined = completed.stdout + completed.stderr
            assert SECRET not in combined
            assert ROOT_TEXT not in combined
            if counterparty != "alice":
                assert case.message_id not in combined
            if not decode:
                return {}
            value = json.JSONDecoder().raw_decode(completed.stdout.lstrip())[0]
            assert isinstance(value, dict)
            return value

        cli(["sources", "erasure-appraisal-policy", "--upgrade", "--reviewed"])
        with case.runtime.shared() as snapshot:
            checks["explicit_upgrade_preserves_four_action_head"] = (
                snapshot.body_revision == 4
                and snapshot.body_head_digest == old_head
                and case.runtime.body_actions() == old_actions
            )
        archive = directory / "before-appraisal.state"
        exported = cli(["state", "export", "--output", str(archive)])
        archive_bytes = archive.read_bytes()
        base = [
            "sources",
            "erasure-appraisal",
            case.message_id,
            "--intent-request-id",
            request.intent_request_id,
        ]
        preview = cli([*base, "--preview"])
        selected = int(str(preview["appraisal_transitions"]))
        checks["uncited_actual_root_selects_complete_explanation_pairs"] = (
            selected >= 2
            and preview["audit_duplicates"] == selected
            and preview["input_receipts"] == selected
            and preview["metadata_references"] == selected * 3
        )
        command = BodyErasureCommand(
            **request.model_dump(),
            request_id="appraisal-cli",
            expected_generation=int(str(preview["generation"])),
            expected_erasure_revision=int(str(preview["erasure_revision"])),
            expected_body_revision=int(str(preview["body_revision"])),
            approval_digest=str(preview["approval_digest"]),
        )
        before = stored_rows(case.path)
        signal_hashes = retained_signals(database, fixture.run.run_id)
        assert len(signal_hashes) == 4
        writes = observed = 0

        def fail(
            connection: Connection,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _many: bool,
        ) -> None:
            nonlocal writes, observed
            if statement.startswith("UPDATE affective_transitions SET reason_codes=NULL"):
                writes += 1
                if writes == 2:
                    observed = int(
                        connection.exec_driver_sql(
                            "SELECT count(*) FROM affective_transitions WHERE reason_codes IS NULL"
                        ).scalar_one()
                    )
                    raise RuntimeError("synthetic_second_pair_write_failure")

        event.listen(database.engine, "before_cursor_execute", fail)
        try:
            pending = manager.commit(command)
        finally:
            event.remove(database.engine, "before_cursor_execute", fail)
        checks["second_pair_fault_rolls_back_both_copies_and_all_main_rows"] = (
            writes == 2
            and observed == 1
            and not pending.main_projection_current
            and stored_rows(case.path) == before
        )
        receipt = [*base, "--request-id", command.request_id]
        assert cli([*receipt, "--status"], expected_exit=2)["main_projection_current"] is False

        def fenced(opened: Database) -> None:
            try:
                bind_installation(opened, case.directory)
            except SourcePolicyError as error:
                if error.reason_code != "body_reconciliation_required":
                    raise AssertionError("scenario_unexpected_body_fence") from None
            else:
                raise AssertionError("scenario_pending_head_admitted")

        fenced(database)
        checks["floor_first_pending_action_fences_startup"] = True
        assert cli([*receipt, "--resume"])["main_projection_current"] is True
        checks["fresh_cli_resumes_whole_five_action_head"] = True
        after = stored_rows(case.path)
        checks["exact_existing_affective_signal_hashes_retained"] = (
            retained_signals(database, fixture.run.run_id) == signal_hashes
        )
        mutable = {
            "affective_transitions",
            "audit_events",
            "erasure_body_applications",
            "erasure_body_bindings",
        }
        checks["all_other_rows_remain_exact"] = {
            t: r for t, r in before.items() if t not in mutable
        } == {t: r for t, r in after.items() if t not in mutable}
        with database.engine.connect() as connection:
            columns = tuple(
                str(row[1])
                for row in connection.exec_driver_sql("PRAGMA table_info(affective_transitions)")
            )
            kept = tuple(
                i
                for i, name in enumerate(columns)
                if name not in {"reason_codes", "explanation_state", "explanation_action_id"}
            )
            checks["all_numerical_transition_metadata_retained_exactly"] = {
                tuple(row[i] for i in kept) for row in before["affective_transitions"]
            } == {tuple(row[i] for i in kept) for row in after["affective_transitions"]}
            checks["actual_sql_null_and_audit_key_absence"] = (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM affective_transitions t JOIN audit_events a "
                    "ON a.event_id=t.audit_event_id WHERE t.reason_codes IS NULL "
                    "AND t.explanation_state='erased' AND t.explanation_action_id IS NOT NULL "
                    "AND json_type(a.details,'$.appraisal_reason_codes') IS NULL"
                ).scalar_one()
                == selected
            )
            checks["foreign_keys_and_integrity"] = (
                connection.exec_driver_sql("PRAGMA foreign_key_check").first() is None
                and connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
            )
        with database.session_factory() as session:
            repository = SQLAlchemyAffectiveStateRepository(session)
            history = repository.list_numerical_history()
            checks["numerical_history_retains_terminal_transitions_without_reasons"] = (
                sum(item.explanation_state == "erased" for item in history) == selected
                and SECRET not in repr(history)
                and all(not hasattr(item, "proposal") for item in history)
            )

        def bytes_state() -> tuple[bytes, bytes, bytes]:
            return (
                case.path.read_bytes(),
                case.runtime.ledger.database_path.read_bytes(),
                case.runtime.ledger.binding_path.read_bytes(),
            )

        stable = bytes_state()
        assert cli([*receipt, "--status"])["main_projection_current"] is True
        assert cli([*receipt, "--resume"])["main_projection_current"] is True
        assert (
            cli(
                [
                    *base,
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
        checks["status_noop_resume_and_original_retry_preserve_bytes"] = bytes_state() == stable
        cli([*receipt, "--status"], expected_exit=2, counterparty="bob", decode=False)
        checks["foreign_counterparty_denied_before_private_receipt"] = True
        checks["controls_invoke_zero_cognitive_ports"] = (
            provider_counts(case.services),
            len(fixture.provider.requests),
            tuple(
                len(getattr(service.talk.prepare_affect.provider, "requests", ()))
                for service in case.services
                if service.talk.prepare_affect is not None
            ),
        ) == calls

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
        fenced(restored)
        checks["actual_archive_before_explanation_disposition_is_fenced"] = True
        assert (
            cli([*receipt, "--resume"], database_url=restored_url)["main_projection_current"]
            is True
        )
        bind_installation(restored, case.directory)
        checks["explicit_restore_resume_proves_pair_without_mutating_archive"] = (
            archive.read_bytes() == archive_bytes
        )

        restarted = create_database(url)
        services, provider, _embedding = _services(
            restarted, case.directory, "after-appraisal", 7, form=False
        )
        cli(["emotion", "history"], decode=False)
        cli(["emotion", "status"], decode=False)
        checks["fresh_cli_history_and_status_have_no_explanation_text"] = True
        assert services.retrieve_memories is not None
        retrieved = asyncio.run(
            services.retrieve_memories.execute(
                RetrievalQuery(
                    text=RECALL,
                    trace_id="appraisal-retrieval",
                    cutoff=datetime.now(UTC),
                    current_interaction_id=None,
                    scope=case.scope,
                    operator_query=True,
                )
            )
        )
        checks["restart_retrieval_has_no_excluded_content"] = SECRET not in repr(retrieved)
        reply = _turn(services, RECALL, "after-appraisal")
        checks["restart_actual_foreground_input_and_reply_clean"] = (
            len(provider.requests) == 1
            and SECRET not in repr(provider.requests[0])
            and SECRET not in str(reply)
        )
        rebound = bind_installation(database, case.directory)
        with rebound.shared() as snapshot:
            actions = rebound.body_actions()
            checks["old_prefix_and_final_mixed_head_proven_after_restart"] = (
                snapshot.body_revision == 5
                and actions[:4] == old_actions
                and body_actions_digest(actions[:4]) == old_head
                and body_actions_digest(actions) == snapshot.body_head_digest
            )
        assert all(checks.values()), "scenario_check_failed"
        return {
            "stage": "14.4.2h",
            "synthetic_only": True,
            "real_provider_calls": 0,
            "paid_cost_usd": 0,
            "physical_erasure": False,
            "selected_explanation_pairs": selected,
            "checks": checks,
            "timings": timings,
            "limits": (
                "Logical explanation disposition; numerical influence, other audit text, "
                "storage remnants, old archives and key/access work remain."
            ),
        }
    finally:
        for opened in (database, restored, restarted):
            if opened is not None:
                opened.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(
        prefix="satori-appraisal-scenario-", dir="/private/tmp"
    ) as path:
        directory = Path(path)
        os.chmod(directory, 0o700)
        report = run(project, directory)
    PrivateArtifactStore().write(arguments.output, json.dumps(report, indent=2).encode())
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
