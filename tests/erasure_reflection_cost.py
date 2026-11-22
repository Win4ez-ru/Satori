"""Warm local Reflection-control cost on one private synthetic managed store.

Run only after the successful recorded full Foundation pytest job has finished:
uv run --no-sync python -m tests.erasure_reflection_cost --output /private/tmp/cost.json
    --foundation-results /private/tmp/foundation/results.json
The report contains counts and timings, never protected selectors or body material.
"""

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns
from typing import Any
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

import satori
from satori.config import Settings
from satori.core.embedding import EmbeddingSpace
from satori.domain.source_policy import SourceEligibilitySnapshot
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure import _admit_physical_schema
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.source_policy import verify_mirror
from tests.erasure_reflection_support import (
    create_reflection_fixture,
    prepare_reflection,
    reflection_command,
)
from tests.erasure_support import provider_counts, stored_state
from tests.fakes import FakeEmbeddingProvider

SAMPLES = 50
WARMUPS = 2


@dataclass(slots=True)
class QueryProbe:
    statements: int = 0
    schema_queries: int = 0
    physical_admissions: int = 0
    runtime_callbacks: int = 0
    runtime_mirror_proofs: int = 0

    def observe(
        self,
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        self.statements += 1
        lowered = statement.lower()
        if "sqlite_schema" in lowered or "sqlite_master" in lowered:
            self.schema_queries += 1

    def snapshot(self) -> tuple[int, int, int, int, int]:
        return (
            self.statements,
            self.schema_queries,
            self.physical_admissions,
            self.runtime_callbacks,
            self.runtime_mirror_proofs,
        )

    def sample(
        self,
        operation: Callable[[], None],
        *,
        require_runtime_callback: bool = False,
    ) -> dict[str, object]:
        for _ in range(WARMUPS):
            operation()
        durations: list[int] = []
        counts: list[tuple[int, ...]] = []
        for _ in range(SAMPLES):
            before = self.snapshot()
            started = perf_counter_ns()
            operation()
            durations.append(perf_counter_ns() - started)
            delta = tuple(end - start for start, end in zip(before, self.snapshot(), strict=True))
            counts.append(delta)
            if delta[1] < 1 or delta[2] < 1:
                raise RuntimeError("cost_missing_fresh_main_schema_admission")
            if delta[3] != delta[4] or (require_runtime_callback and delta[3] < 1):
                raise RuntimeError("cost_runtime_callback_without_mirror_proof")
        ordered = sorted(durations)
        labels = (
            "main_sql_statements",
            "fresh_main_schema_queries",
            "physical_schema_admissions",
            "runtime_verify_mirror_callbacks",
            "runtime_verify_mirror_proofs",
        )
        result: dict[str, object] = {
            "samples": SAMPLES,
            "warmups_excluded": WARMUPS,
            "samples_ns": durations,
            "samples_ms": [round(value / 1_000_000, 3) for value in durations],
            "median_ms": round(statistics.median(ordered) / 1_000_000, 3),
            "p95_ms": round(ordered[math.ceil(0.95 * SAMPLES) - 1] / 1_000_000, 3),
            "min_ms": round(ordered[0] / 1_000_000, 3),
            "max_ms": round(ordered[-1] / 1_000_000, 3),
        }
        for index, label in enumerate(labels):
            values = [sample[index] for sample in counts]
            result[label + "_samples"] = values
            result[label + "_per_sample"] = {"min": min(values), "max": max(values)}
        return result


def _row_count(database: Database) -> int:
    with database.engine.connect() as connection:
        tables = tuple(
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        return sum(
            int(connection.exec_driver_sql(f'SELECT count(*) FROM "{table}"').scalar_one())
            for table in tables
        )


def _foundation_jobs_finished(paths: tuple[Path, ...]) -> int:
    """Accept only the exact seven-command successful Foundation gate."""
    if not paths:
        raise RuntimeError("cost_requires_successful_foundation_results")
    commands = (
        (
            "uv",
            "sync",
            "--frozen",
            "--all-groups",
            "--no-editable",
            "--reinstall-package",
            "satori-core",
        ),
        ("uv", "run", "--no-sync", "ruff", "format", "--check", "."),
        ("uv", "run", "--no-sync", "ruff", "check", "."),
        ("uv", "run", "--no-sync", "mypy", "src", "tests"),
        ("uv", "run", "--no-sync", "pytest"),
        ("uv", "run", "--no-sync", "alembic", "upgrade", "head"),
        ("uv", "run", "--no-sync", "satori", "bootstrap"),
    )
    for path in paths:
        value = json.loads(PrivateArtifactStore().read(path, max_bytes=1_048_576))
        if not isinstance(value, list) or len(value) != len(commands):
            raise RuntimeError("known_foundation_gate_not_finished_successfully")
        for item, command in zip(value, commands, strict=True):
            if (
                not isinstance(item, dict)
                or item.get("command") != list(command)
                or type(item.get("exit_code")) is not int
                or item["exit_code"] != 0
            ):
                raise RuntimeError("known_foundation_gate_not_finished_successfully")
    return len(paths)


def _isolated_settings(**kwargs: Any) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


class CostFixtureEmbeddingProvider(FakeEmbeddingProvider):
    """Cover the two independent synthetic queries without a provider fallback."""

    def __init__(
        self,
        vectors: dict[str, tuple[float, ...]],
        *,
        space: EmbeddingSpace | None = None,
        error: Exception | None = None,
    ) -> None:
        complete = dict(vectors)
        for day in (3, 4):
            complete[f"Проверяемость оснований решения полезна, наблюдение {day}."] = (
                0.0,
                1.0,
                0.0,
            )
        super().__init__(complete, space=space, error=error)


def run(project: Path, directory: Path, *, known_foundation_jobs: int) -> dict[str, object]:
    if known_foundation_jobs < 1:
        raise RuntimeError("cost_requires_successful_foundation_results")
    package = Path(str(satori.__file__)).resolve()
    if not package.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError("cost_requires_installed_noneditable_package")
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
            fixture = create_reflection_fixture(database, directory / "trusted-floor")
        case = fixture.case
        calls = provider_counts(case.services), len(fixture.provider.requests)
        manager, request = prepare_reflection(fixture)
        preview = manager.preview(request)
        assert preview.readiness == "ready", "synthetic_reflection_plan_blocked"
        assert preview.reflection_proposals == 2
        assert preview.parent_runs == 1
        assert preview.source_rows == 4
        result = manager.commit(reflection_command(manager, request, "cost-reflection-action"))
        assert result.main_projection_current
        assert result.selected_bodies_removed
        assert result.body_revision == 4
        assert result.physical_erasure is False
        with case.runtime.exclusive():
            assert len(case.runtime.body_actions()) == 4
        assert (provider_counts(case.services), len(fixture.provider.requests)) == calls

        original_callback = case.runtime.verify_mirror
        assert original_callback is not None

        def verified_mirror(session: Session, snapshot: SourceEligibilitySnapshot) -> None:
            verify_mirror(session, snapshot)
            probe.runtime_mirror_proofs += 1

        def callback(snapshot: SourceEligibilitySnapshot) -> None:
            before = probe.runtime_mirror_proofs
            probe.runtime_callbacks += 1
            original_callback(snapshot)
            if probe.runtime_mirror_proofs - before != 1:
                raise RuntimeError("cost_runtime_callback_without_mirror_proof")

        def physical_admission(connection: Connection) -> tuple[str, str]:
            assert connection.engine is database.engine, "cost_schema_proof_on_unobserved_engine"
            result = _admit_physical_schema(connection)
            probe.physical_admissions += 1
            return result

        def admission() -> None:
            with case.runtime.shared() as snapshot:
                assert snapshot.body_revision == 4

        def status() -> None:
            current = manager.status(request, request_id="cost-reflection-action")
            assert current.main_projection_current
            assert current.selected_bodies_removed
            assert current.physical_erasure is False

        def resume() -> None:
            current = manager.resume(request, request_id="cost-reflection-action")
            assert current.main_projection_current
            assert current.selected_bodies_removed
            assert current.physical_erasure is False

        case.runtime.verify_mirror = callback
        event.listen(database.engine, "before_cursor_execute", probe.observe)
        observed = True
        before = stored_state(case)
        floor_bytes = case.runtime.ledger.database_path.read_bytes()
        phases: dict[str, object] = {}
        with (
            patch(
                "satori.infrastructure.source_policy.installation.verify_mirror", verified_mirror
            ),
            patch(
                "satori.infrastructure.persistence.erasure._admit_physical_schema",
                physical_admission,
            ),
            patch(
                "satori.infrastructure.persistence.erasure_bodies._admit_physical_schema",
                physical_admission,
            ),
        ):
            for label, operation, require_callback in (
                ("runtime_shared_body4", admission, True),
                ("reflection_status_body4", status, False),
                ("reflection_noop_resume_body4", resume, False),
            ):
                phases[label] = probe.sample(operation, require_runtime_callback=require_callback)
                assert stored_state(case) == before, "cost_controls_changed_persistent_state"
                assert case.runtime.ledger.database_path.read_bytes() == floor_bytes
        assert (provider_counts(case.services), len(fixture.provider.requests)) == calls
        with database.engine.connect() as connection:
            terminal = int(
                connection.exec_driver_sql(
                    "SELECT count(*) FROM reflection_proposals WHERE body_state='erased' "
                    "AND body_action_id IS NOT NULL AND payload IS NULL"
                ).scalar_one()
            )
        assert terminal == 2
        return {
            "schema_version": 1,
            "captured_at": datetime.now(UTC).isoformat(),
            "installed_noneditable_package_verified": True,
            "known_foundation_pytest_jobs_finished": True,
            "known_foundation_jobs_checked": known_foundation_jobs,
            "host_load_known": False,
            "host_idle_verified": False,
            "fixture": "synthetic_managed_four_root_reflection_uncited_actual_input",
            "synthetic_embedding_inputs_complete": True,
            "real_provider_calls": 0,
            "provider_calls_during_controls_and_measurements": 0,
            "physical_erasure": False,
            "source_generation": preview.generation,
            "erasure_revision": preview.erasure_revision,
            "body_revision": result.body_revision,
            "mixed_body_actions": 4,
            "main_rows": _row_count(database),
            "preview_scanned_rows": preview.scanned_rows,
            "selected_counts": {
                "reflection_proposals": preview.reflection_proposals,
                "parent_runs": preview.parent_runs,
                "source_rows": preview.source_rows,
                "attempt_rows": preview.attempt_rows,
                "outcome_rows": preview.outcome_rows,
                "metadata_references": preview.metadata_references,
            },
            "terminal_reflection_payloads": terminal,
            "phases": phases,
            "noop_main_rows_unchanged": True,
            "noop_floor_rows_and_bytes_unchanged": True,
            "noop_binding_and_lock_bytes_unchanged": True,
            "limitations": (
                "Small synthetic warm store; no foreground or Character benchmark.",
                "Only supplied full Foundation jobs are checked; other host load is unknown.",
                "p95 uses nearest-rank over 50 samples; all nanosecond samples are retained.",
                "SQL counts cover the main SQLAlchemy engine, not independent-floor SQLite.",
                "Operator status/resume use their direct current-state proof; only shared runtime "
                "admission requires the runtime mirror callback.",
                "Selected payloads are actual SQL NULL; no forensic storage certificate.",
            ),
        }
    finally:
        if observed:
            event.remove(database.engine, "before_cursor_execute", probe.observe)
        database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--foundation-results", type=Path, action="append", required=True)
    options = parser.parse_args()
    completed = _foundation_jobs_finished(tuple(options.foundation_results))
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="satori-reflection-cost-", dir="/private/tmp") as path:
        directory = Path(path)
        os.chmod(directory, 0o700)
        report = run(project, directory, known_foundation_jobs=completed)
    payload = json.dumps(report, sort_keys=True, indent=2)
    forbidden = (
        "message_id",
        "interaction_id",
        "memory_id",
        "evidence_id",
        "aggregate_id",
        "proposal_id",
        "run_id",
        "floor_id",
        "lineage_id",
        "body_digest",
        "terminal_digest",
        "approval_digest",
        "store_digest",
    )
    assert not any(name in payload for name in forbidden), "cost_report_private_selector"
    PrivateArtifactStore().write(options.output, payload.encode())
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
