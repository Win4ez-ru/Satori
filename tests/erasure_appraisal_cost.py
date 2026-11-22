"""Warm paired-explanation control cost after a successful full Foundation job."""

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
from satori.domain.source_policy import SourceEligibilitySnapshot
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.erasure import _admit_physical_schema
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.source_policy import verify_mirror
from tests.erasure_appraisal_support import (
    appraisal_command,
    create_appraisal_fixture,
    prepare_appraisal,
)
from tests.erasure_reflection_cost import (
    CostFixtureEmbeddingProvider,
    QueryProbe,
    _foundation_jobs_finished,
    _isolated_settings,
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
        with (
            patch("tests.test_source_exclusion_production.Settings", _isolated_settings),
            patch(
                "tests.test_source_exclusion_production.FakeEmbeddingProvider",
                CostFixtureEmbeddingProvider,
            ),
        ):
            fixture = create_appraisal_fixture(database, directory / "trusted-floor")
        case = fixture.case
        calls = (
            provider_counts(case.services),
            len(fixture.provider.requests),
            tuple(
                len(getattr(service.talk.prepare_affect.provider, "requests", ()))
                for service in case.services
                if service.talk.prepare_affect is not None
            ),
        )
        manager, request = prepare_appraisal(fixture)
        preview = manager.preview(request)
        assert preview.readiness == "ready", "synthetic_appraisal_plan_blocked"
        result = manager.commit(appraisal_command(manager, request, "cost-appraisal-action"))
        assert result.main_projection_current
        assert result.selected_bodies_removed
        assert result.body_revision == 5
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
                assert snapshot.body_revision == 5

        def status() -> None:
            current = manager.status(request, request_id="cost-appraisal-action")
            assert current.main_projection_current
            assert current.selected_bodies_removed

        def resume() -> None:
            current = manager.resume(request, request_id="cost-appraisal-action")
            assert current.main_projection_current
            assert current.selected_bodies_removed

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
                ("runtime_shared_body5", admission, True),
                ("appraisal_status_body5", status, False),
                ("appraisal_noop_resume_body5", resume, False),
            ):
                phases[label] = probe.sample(operation, require_runtime_callback=require_callback)
                assert stored_state(case) == before
                assert case.runtime.ledger.database_path.read_bytes() == floor_bytes
        assert (
            provider_counts(case.services),
            len(fixture.provider.requests),
            tuple(
                len(getattr(service.talk.prepare_affect.provider, "requests", ()))
                for service in case.services
                if service.talk.prepare_affect is not None
            ),
        ) == calls
        with database.engine.connect() as connection:
            terminal = int(
                connection.exec_driver_sql(
                    "SELECT count(*) FROM affective_transitions t JOIN audit_events a "
                    "ON a.event_id=t.audit_event_id WHERE t.reason_codes IS NULL "
                    "AND t.explanation_state='erased' AND t.explanation_action_id IS NOT NULL "
                    "AND json_type(a.details,'$.appraisal_reason_codes') IS NULL"
                ).scalar_one()
            )
        assert terminal == preview.appraisal_transitions
        return {
            "schema_version": 1,
            "captured_at": datetime.now(UTC).isoformat(),
            "installed_noneditable_package_verified": True,
            "known_foundation_pytest_jobs_finished": True,
            "known_foundation_jobs_checked": known_jobs,
            "host_load_known": False,
            "host_idle_verified": False,
            "fixture": "synthetic_managed_four_root_uncited_appraisal_input",
            "synthetic_embedding_inputs_complete": True,
            "real_provider_calls": 0,
            "provider_calls_during_controls_and_measurements": 0,
            "physical_erasure": False,
            "source_generation": preview.generation,
            "erasure_revision": preview.erasure_revision,
            "body_revision": result.body_revision,
            "mixed_body_actions": 5,
            "main_rows": _row_count(database),
            "preview_scanned_rows": preview.scanned_rows,
            "selected_counts": {
                "appraisal_transitions": preview.appraisal_transitions,
                "audit_duplicates": preview.audit_duplicates,
                "input_receipts": preview.input_receipts,
                "metadata_references": preview.metadata_references,
            },
            "terminal_explanation_pairs": terminal,
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
    with tempfile.TemporaryDirectory(prefix="satori-appraisal-cost-", dir="/private/tmp") as path:
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
