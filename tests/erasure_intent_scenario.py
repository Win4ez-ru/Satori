"""Synthetic floor-first intent, fresh-process preparation and old-backup recovery drill.

Only metadata and timings are emitted. Stored bodies are deliberately retained.
Run: uv run --no-sync python -m tests.erasure_intent_scenario --output /private/tmp/report.json
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

from sqlalchemy import select

from satori.application.erasure.intent_contracts import (
    ErasureIntentCommand,
    ErasureIntentRequest,
    ErasureIntentResult,
)
from satori.application.erasure.intent_use_cases import ErasureIntentManager
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.erasure import SQLiteErasureInventory
from satori.infrastructure.persistence.erasure_preparation import SQLiteErasurePreparationStore
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.models import (
    ConversationMessageRow,
    EpisodicMemoryRow,
    MemoryEvidenceRow,
    SemanticClaimRow,
)
from tests.erasure_preview_scenario import CLI_ENTRY
from tests.erasure_support import create_case, provider_counts
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import DERIVE_TEXT, ROOT_TEXT, SECRET


def run(project_root: Path, directory: Path) -> dict[str, object]:
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
                protected.update(str(value) for value in session.scalars(select(column)))
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
            if not require_json:
                return {}
            result: object = json.JSONDecoder().raw_decode(completed.stdout.lstrip())[0]
            assert isinstance(result, dict), "scenario_metadata_shape"
            return result

        archive = directory / "before-intent.state"
        exported = cli(["state", "export", "--output", str(archive)])
        archive_bytes = archive.read_bytes()
        original_rows = stored_rows(case.path)
        request = ErasureIntentRequest(message_id=case.message_id, counterparty_id="alice")
        store = SQLiteErasurePreparationStore(database)
        manager = ErasureIntentManager(case.runtime, SQLiteErasureInventory(database), store)
        preview = manager.preview(request)
        assert preview.inventory.assessment_status == "blocked"
        command = ErasureIntentCommand(
            message_id=case.message_id,
            counterparty_id="alice",
            expected_generation=preview.inventory.generation,
            expected_erasure_revision=preview.erasure_revision,
            approval_digest=preview.approval_digest,
            request_id="scenario-erasure",
        )
        persist = store.persist

        def fail_projection(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("synthetic_storage_failure")

        store.persist = fail_projection  # type: ignore[method-assign]
        pending = phase(
            "floor_committed_before_projection_failure", lambda: manager.commit(command)
        )
        store.persist = persist  # type: ignore[method-assign]
        assert isinstance(pending, ErasureIntentResult)
        assert not pending.main_projection_current
        assert stored_rows(case.path) == original_rows
        args = ["sources", "erasure-intent", case.message_id]
        status_args = [*args, "--status", "--request-id", command.request_id]
        phase("fresh_process_pending_status", lambda: cli(status_args, expected_exit=2))
        phase(
            "pending_preview_admission_denied",
            lambda: cli([*args, "--preview"], expected_exit=2, require_json=False),
        )
        prepared = phase(
            "fresh_process_resumable_owner_preparation",
            lambda: cli([*args, "--resume", "--request-id", command.request_id]),
        )
        assert isinstance(prepared, dict)
        assert prepared["preparation_current"] is True
        assert prepared["phase"] == "intent_committed_preparation_blocked"
        assert prepared["physical_erasure"] is False
        assert prepared["execution_authorized"] is False
        stable_rows = stored_rows(case.path)
        phase(
            "fresh_process_original_commit_replay_noop",
            lambda: cli(
                [
                    *args,
                    "--commit",
                    "--expected-generation",
                    str(command.expected_generation),
                    "--expected-erasure-revision",
                    str(command.expected_erasure_revision),
                    "--sha256",
                    command.approval_digest,
                    "--request-id",
                    command.request_id,
                ]
            ),
        )
        assert stored_rows(case.path) == stable_rows
        main_bytes = case.path.read_bytes()
        floor_bytes = case.runtime.ledger.database_path.read_bytes()
        current = cli(status_args)
        assert current["preparation_current"] is True
        assert case.path.read_bytes() == main_bytes
        assert case.runtime.ledger.database_path.read_bytes() == floor_bytes
        phase(
            "foreign_scope_status_denied",
            lambda: cli(status_args, counterparty="bob", expected_exit=2, require_json=False),
        )

        restored = directory / "restored-old.db"
        phase(
            "actual_cli_offline_old_main_restore",
            lambda: cli(
                [
                    "state",
                    "import",
                    str(archive),
                    "--activate",
                    "--target",
                    str(restored),
                    "--sha256",
                    str(exported["archive_sha256"]),
                ]
            ),
        )
        old_url = f"sqlite+pysqlite:///{restored}"
        phase(
            "old_main_erasure_head_lag_detected",
            lambda: cli(status_args, database_url=old_url, expected_exit=2),
        )
        with case.runtime.exclusive(), case.runtime.ledger._connection() as connection:
            exclusion_id = connection.execute(
                "SELECT request_id FROM policy_receipts LIMIT 1"
            ).fetchone()[0]
        phase(
            "ordinary_source_repair_cannot_ack_erasure",
            lambda: cli(
                ["sources", "repair", "--request-id", str(exclusion_id)], database_url=old_url
            ),
        )
        cli(status_args, database_url=old_url, expected_exit=2)
        phase(
            "old_main_explicit_all_owner_resume",
            lambda: cli(
                [*args, "--resume", "--request-id", command.request_id], database_url=old_url
            ),
        )
        restored_database = create_database(old_url)
        with restored_database.session_factory() as session:
            assert (
                session.scalar(
                    select(ConversationMessageRow.content).where(
                        ConversationMessageRow.message_id == case.message_id
                    )
                )
                == ROOT_TEXT
            )
        assert archive.read_bytes() == archive_bytes
        original_floor = case.runtime.ledger.database_path
        held = case.directory / "synthetic-held-floor"
        original_floor.rename(held)
        try:
            phase(
                "missing_floor_denied_without_replacement",
                lambda: cli(status_args, database_url=old_url, expected_exit=2, require_json=False),
            )
            assert not original_floor.exists()
        finally:
            held.rename(original_floor)
        assert provider_counts(case.services) == case.initial_provider_calls
        preparation = prepared["preparation"]
        assert isinstance(preparation, dict)
        assert isinstance(preparation["assessments"], list)
        return {
            "scenario": "durable-erasure-intent-owner-preparation-v1",
            "checks": {
                "floor_survives_failed_main_projection": True,
                "pending_main_denies_normal_preview": True,
                "fresh_process_resume_prepares_complete_head": True,
                "replayed_commit_has_no_duplicate_preparation": True,
                "status_preserves_main_and_floor_bytes": True,
                "foreign_scope_denied": True,
                "old_backup_cannot_rollback_floor_intent": True,
                "source_repair_cannot_ack_erasure_head": True,
                "explicit_old_main_resume_is_recoverable": True,
                "raw_body_and_old_archive_deliberately_retained": True,
                "missing_floor_never_recreated": True,
                "public_output_contains_no_source_or_descendant_content": True,
            },
            "prepared_owner_count": len(preparation["assessments"]),
            "source_generation": command.expected_generation,
            "erasure_revision": 1,
            "control_provider_calls": 0,
            "paid_provider_calls": 0,
            "physical_erasure": False,
            "execution_authorized": False,
            "timings": timings,
        }
    finally:
        if restored_database is not None:
            restored_database.dispose()
        database.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="satori-erasure-intent-scenario-") as temporary:
        report = run(Path(__file__).resolve().parents[1], Path(temporary))
    payload = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    if arguments.output is None:
        print(payload.decode(), end="")
    else:
        PrivateArtifactStore().write(arguments.output, payload)
        print(json.dumps({"report_written": True, "physical_erasure": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
