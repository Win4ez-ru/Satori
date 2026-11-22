"""Warm identity reason control cost after a successful full Foundation gate."""

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

import satori
from satori.application.erasure.personality_reason_contracts import (
    CheckpointReasonCommand,
    CheckpointReasonRequest,
)
from satori.application.erasure.personality_reason_use_cases import CheckpointReasonManager
from satori.domain.source_policy import SourceEligibilitySnapshot
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.erasure import _admit_physical_schema
from satori.infrastructure.persistence.erasure_personality_reasons import (
    SQLiteCheckpointReasonBodyStore,
)
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.source_policy import verify_mirror
from tests.checkpoint_reason_support import create_checkpoint_reason_fixture
from tests.erasure_reflection_cost import (
    QueryProbe,
    _foundation_jobs_finished,
    _row_count,
)
from tests.erasure_support import provider_counts, stored_state


def run(project: Path, directory: Path, *, known_jobs: int) -> dict[str, object]:
    assert known_jobs > 0
    assert Path(str(satori.__file__)).resolve().is_relative_to(Path(sys.prefix).resolve())
    url = f"sqlite+pysqlite:///{directory / 'synthetic.db'}"
    upgrade_database(url, config_path=project / "alembic.ini")
    database = create_database(url)
    probe = QueryProbe()
    observed = False
    try:
        fixture = create_checkpoint_reason_fixture(database, directory / "trusted-floor")
        case = fixture.case
        calls = (
            provider_counts(case.services),
            len(fixture.reflection.provider.requests),
            len(fixture.personality_provider.requests),
        )
        case.runtime.upgrade_checkpoint_reason_policy()
        manager = CheckpointReasonManager(case.runtime, SQLiteCheckpointReasonBodyStore(database))
        request = CheckpointReasonRequest(
            identity_id=case.scope.identity_id, selectors=fixture.selectors
        )
        preview = manager.preview(request)
        assert preview.readiness == "ready", "synthetic_checkpoint_reason_plan_blocked"
        command = CheckpointReasonCommand(
            identity_id=request.identity_id,
            selectors=request.selectors,
            request_id="cost-checkpoint-reasons",
            expected_generation=preview.generation,
            expected_erasure_revision=preview.erasure_revision,
            expected_body_revision=preview.body_revision,
            approval_digest=preview.approval_digest,
        )
        result = manager.commit(command)
        assert result.main_projection_current
        assert result.selected_bodies_removed
        assert result.body_revision == 6
        assert result.physical_erasure is False
        original_callback = case.runtime.verify_mirror
        assert original_callback is not None

        def mirror(session: Session, snapshot: SourceEligibilitySnapshot) -> None:
            verify_mirror(session, snapshot)
            probe.runtime_mirror_proofs += 1

        def callback(snapshot: SourceEligibilitySnapshot) -> None:
            before = probe.runtime_mirror_proofs
            probe.runtime_callbacks += 1
            original_callback(snapshot)
            assert probe.runtime_mirror_proofs - before == 1

        def physical(connection: Connection) -> tuple[str, str]:
            assert connection.engine is database.engine
            value = _admit_physical_schema(connection)
            probe.physical_admissions += 1
            return value

        def admission() -> None:
            with case.runtime.shared() as snapshot:
                assert snapshot.body_revision == 6

        status_request = CheckpointReasonRequest(identity_id=case.scope.identity_id)

        def status() -> None:
            current = manager.status(status_request, request_id=command.request_id)
            assert current.main_projection_current
            assert current.physical_erasure is False
            assert current.execution_authorized is False

        def terminal_preview() -> None:
            current = manager.preview(request)
            assert current.already_erased == 2
            assert current.readiness == "blocked"
            assert current.blockers == ("no_available_checkpoint_reason_bodies",)
            assert current.physical_erasure is False
            assert current.execution_authorized is False

        case.runtime.verify_mirror = callback
        event.listen(database.engine, "before_cursor_execute", probe.observe)
        observed = True
        before = stored_state(case)
        floor_bytes = case.runtime.ledger.database_path.read_bytes()
        phases: dict[str, object] = {}
        with (
            patch("satori.infrastructure.source_policy.installation.verify_mirror", mirror),
            patch("satori.infrastructure.persistence.erasure._admit_physical_schema", physical),
            patch(
                "satori.infrastructure.persistence.erasure_bodies._admit_physical_schema", physical
            ),
        ):
            for label, operation, require_callback in (
                ("runtime_shared_body6", admission, True),
                ("identity_reason_status_body6", status, False),
                ("terminal_reason_preview_body6", terminal_preview, False),
            ):
                phases[label] = probe.sample(operation, require_runtime_callback=require_callback)
                assert stored_state(case) == before
                assert case.runtime.ledger.database_path.read_bytes() == floor_bytes
        assert (
            provider_counts(case.services),
            len(fixture.reflection.provider.requests),
            len(fixture.personality_provider.requests),
        ) == calls
        with database.engine.connect() as connection:
            terminal = sum(
                int(
                    connection.exec_driver_sql(
                        f"SELECT count(*) FROM {table} WHERE reason IS NULL "
                        "AND reason_state='erased' AND reason_action_id IS NOT NULL"
                    ).scalar_one()
                )
                for table in ("personality_checkpoint_approvals", "personality_restore_events")
            )
        assert terminal == 2
        return {
            "schema_version": 1,
            "captured_at": datetime.now(UTC).isoformat(),
            "installed_noneditable_package_verified": True,
            "known_foundation_pytest_jobs_finished": True,
            "known_foundation_jobs_checked": known_jobs,
            "host_load_known": False,
            "host_idle_verified": False,
            "fixture": "synthetic_managed_longitudinal_two_owner_reasons_six_action_head",
            "synthetic_embedding_inputs_complete": True,
            "real_provider_calls": 0,
            "provider_calls_during_controls_and_measurements": 0,
            "physical_erasure": False,
            "source_generation": preview.generation,
            "erasure_revision": preview.erasure_revision,
            "body_revision": result.body_revision,
            "mixed_body_actions": 6,
            "main_rows": _row_count(database),
            "preview_scanned_rows": preview.scanned_rows,
            "selected_counts": {
                "approval_reasons": preview.approval_reasons,
                "restore_reasons": preview.restore_reasons,
                "metadata_references": preview.metadata_references,
            },
            "terminal_reason_bodies": terminal,
            "phases": phases,
            "noop_main_rows_unchanged": True,
            "noop_floor_rows_and_bytes_unchanged": True,
            "noop_binding_and_lock_bytes_unchanged": True,
            "limitations": [
                "Small synthetic warm store; no Character/foreground benchmark.",
                "Only supplied full Foundation jobs checked; host load unknown.",
                "Nearest-rank p95 over 50 samples, all nanosecond samples retained.",
                "SQL counts cover main SQLAlchemy engine, not floor SQLite.",
                "Main row equality does not certify file/page physical erasure.",
            ],
        }
    finally:
        if observed:
            event.remove(database.engine, "before_cursor_execute", probe.observe)
        database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--foundation-results", type=Path, action="append", required=True)
    arguments = parser.parse_args()
    jobs = _foundation_jobs_finished(tuple(arguments.foundation_results))
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(
        prefix="satori-checkpoint-reason-cost-", dir="/private/tmp"
    ) as path:
        directory = Path(path)
        os.chmod(directory, 0o700)
        report = run(project, directory, known_jobs=jobs)
    payload = json.dumps(report, sort_keys=True, indent=2)
    assert not any(
        name in payload
        for name in (
            "message_id",
            "interaction_id",
            "transition_id",
            "audit_event_id",
            "source_input_id",
            "body_digest",
            "terminal_digest",
            "input_closure_digest",
            "approval_digest",
            "store_digest",
        )
    ), "cost_report_private_selector"
    PrivateArtifactStore().write(arguments.output, payload.encode())
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
