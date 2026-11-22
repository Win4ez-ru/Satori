"""Warm local owner-control cost on synthetic state, never a Character benchmark.

Run after the recorded Foundation jobs have finished, using their installed package:
uv run --no-sync python -m tests.erasure_knowledge_cost --output /private/tmp/cost.json
    --foundation-results /private/tmp/known-foundation/results.json
Only metadata is saved. The helper never selects an existing Satori installation.
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

import satori
from satori.config import Settings
from satori.domain.source_policy import SourceEligibilitySnapshot
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.knowledge_material import KNOWLEDGE_OWNER_SPECS
from satori.infrastructure.persistence.migrations import upgrade_database
from tests.erasure_knowledge_support import add_knowledge
from tests.erasure_support import create_case, provider_counts
from tests.test_erasure_knowledge_integration import knowledge_command, prepare_knowledge
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import _exclude

SAMPLES = 50
WARMUPS = 2


@dataclass(slots=True)
class QueryProbe:
    statements: int = 0
    schemas: int = 0
    mirror_callbacks: int = 0

    def observe(
        self,
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        self.statements += 1
        # Count fresh schema reads rather than assuming a callback is a proof.
        lowered = statement.lower()
        if "sqlite_schema" in lowered or "sqlite_master" in lowered:
            self.schemas += 1

    def sample(self, operation: Callable[[], None]) -> dict[str, object]:
        for _ in range(WARMUPS):
            operation()
        durations: list[float] = []
        statements: list[int] = []
        schemas: list[int] = []
        callbacks: list[int] = []
        for _ in range(SAMPLES):
            before = self.statements, self.schemas, self.mirror_callbacks
            started = perf_counter_ns()
            operation()
            durations.append((perf_counter_ns() - started) / 1_000_000)
            statements.append(self.statements - before[0])
            schemas.append(self.schemas - before[1])
            callbacks.append(self.mirror_callbacks - before[2])
            if not schemas[-1]:
                raise RuntimeError("cost_proof_skipped_fresh_schema_read")
        ordered = sorted(durations)
        return {
            "samples": SAMPLES,
            "warmups_excluded": WARMUPS,
            "samples_ms": [round(value, 3) for value in durations],
            "median_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[math.ceil(0.95 * SAMPLES) - 1], 3),
            "min_ms": round(ordered[0], 3),
            "max_ms": round(ordered[-1], 3),
            "main_sql_statements_per_sample": {"min": min(statements), "max": max(statements)},
            "fresh_main_schema_queries_per_sample": {"min": min(schemas), "max": max(schemas)},
            "runtime_verify_mirror_callbacks_per_sample": {
                "min": min(callbacks),
                "max": max(callbacks),
            },
        }


def _row_count(database: Database) -> int:
    with database.engine.connect() as connection:
        tables = tuple(
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        return sum(
            int(connection.exec_driver_sql(f'SELECT count(*) FROM "{name}"').scalar_one())
            for name in tables
        )


def _foundation_jobs_finished(paths: tuple[Path, ...]) -> int:
    """Verify only the supplied recorded jobs; this never claims the host is idle."""
    for path in paths:
        value = json.loads(PrivateArtifactStore().read(path, max_bytes=1_048_576))
        if not isinstance(value, list) or not any(
            isinstance(item, dict)
            and isinstance(item.get("command"), list)
            and "pytest" in item["command"]
            and item.get("exit_code") == 0
            for item in value
        ):
            raise RuntimeError("known_foundation_pytest_not_finished_successfully")
    return len(paths)


def _isolated_settings(**kwargs: Any) -> Settings:
    # BaseSettings accepts _env_file; the generated model signature omits it.
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def run(project: Path, directory: Path, *, known_foundation_jobs: int) -> dict[str, object]:
    package = Path(str(satori.__file__)).resolve()
    if not package.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError("cost_requires_installed_noneditable_package")
    url = f"sqlite+pysqlite:///{directory / 'synthetic.db'}"
    upgrade_database(url, config_path=project / "alembic.ini")
    database = create_database(url)
    probe = QueryProbe()
    observed = False
    try:
        # The shared factory keeps every fake port; only developer .env loading is disabled.
        with patch("tests.test_source_exclusion_production.Settings", _isolated_settings):
            case = create_case(database, directory / "trusted-floor", excluded=False)
        add_knowledge(case)
        _exclude(database, case.runtime, case.message_id)
        calls = provider_counts(case.services)
        manager, request = prepare_knowledge(case)
        preview = manager.preview(request)
        assert preview.readiness == "ready", "synthetic_knowledge_plan_blocked"
        selected = {
            "semantic_claims": preview.semantic_claims,
            "user_model_claims": preview.user_model_claims,
            "world_model_claims": preview.world_model_claims,
            "positions": preview.positions,
            "inclinations": preview.inclinations,
            "position_quotes": preview.position_quotes,
            "witness_transitions": preview.witness_transitions,
        }
        assert all(
            selected[name] > 0
            for name in (
                "semantic_claims",
                "user_model_claims",
                "world_model_claims",
                "positions",
                "inclinations",
            )
        )
        result = manager.commit(knowledge_command(manager, request, "cost-knowledge-action"))
        assert result.main_projection_current
        assert result.body_revision == 3
        assert result.physical_erasure is False
        original_verify = case.runtime.verify_mirror
        assert original_verify is not None

        def verify(snapshot: SourceEligibilitySnapshot) -> None:
            probe.mirror_callbacks += 1
            original_verify(snapshot)

        case.runtime.verify_mirror = verify
        event.listen(database.engine, "before_cursor_execute", probe.observe)
        observed = True
        main_before = stored_rows(case.path)
        floor_before = case.runtime.ledger.database_path.read_bytes()
        binding_before = case.runtime.ledger.binding_path.read_bytes()

        def admission() -> None:
            with case.runtime.shared() as snapshot:
                assert snapshot.body_revision == 3

        def status() -> None:
            assert manager.status(
                request, request_id="cost-knowledge-action"
            ).main_projection_current

        def resume() -> None:
            current = manager.resume(request, request_id="cost-knowledge-action")
            assert current.main_projection_current
            assert current.selected_bodies_removed

        phases = {
            "runtime_shared_body3": probe.sample(admission),
            "knowledge_status_body3": probe.sample(status),
            "knowledge_noop_resume_body3": probe.sample(resume),
        }
        assert stored_rows(case.path) == main_before
        assert case.runtime.ledger.database_path.read_bytes() == floor_before
        assert case.runtime.ledger.binding_path.read_bytes() == binding_before
        assert provider_counts(case.services) == calls
        terminal_counts: dict[str, int] = {}
        with database.engine.connect() as connection:
            for kind, spec in KNOWLEDGE_OWNER_SPECS.items():
                predicate = " AND ".join(f'"{column}" IS NULL' for column in spec.content_columns)
                terminal_counts[kind.value] = int(
                    connection.exec_driver_sql(
                        f'SELECT count(*) FROM "{spec.table}" '
                        f"WHERE body_state='erased' AND {predicate}"
                    ).scalar_one()
                )
            terminal_counts["position_quotes"] = int(
                connection.exec_driver_sql(
                    "SELECT count(*) FROM satori_position_evidence "
                    "WHERE quote IS NULL AND normalized_signature IS NULL"
                ).scalar_one()
            )
        assert sum(terminal_counts.values()) >= 7
        return {
            "schema_version": 1,
            "captured_at": datetime.now(UTC).isoformat(),
            "installed_noneditable_package_verified": True,
            "known_foundation_pytest_jobs_finished": known_foundation_jobs > 0,
            "known_foundation_jobs_checked": known_foundation_jobs,
            "host_idle_verified": False,
            "fixture": "synthetic_retained_five_kind_knowledge_with_mixed_position_support",
            "real_provider_calls": 0,
            "provider_calls_during_controls_and_measurements": 0,
            "source_generation": preview.generation,
            "erasure_revision": preview.erasure_revision,
            "body_revision": result.body_revision,
            "main_rows": _row_count(database),
            "knowledge_preview_scanned_rows": preview.scanned_rows,
            "selected_counts": selected,
            "terminal_counts": terminal_counts,
            "phases": phases,
            "noop_main_rows_unchanged": True,
            "noop_floor_bytes_unchanged": True,
            "noop_binding_bytes_unchanged": True,
            "limitations": (
                "Synthetic small store and warm direct controls; "
                "no foreground or Character benchmark.",
                "Only supplied Foundation result files are checked; "
                "other host workloads are unknown.",
                "p95 uses nearest-rank over 50 samples; every timing outlier is retained.",
                "SQL counts cover the main SQLAlchemy engine, not independent-floor SQLite.",
                "Real SQL NULL removal in selected owners; no forensic storage certificate.",
            ),
        }
    finally:
        if observed:
            event.remove(database.engine, "before_cursor_execute", probe.observe)
        database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--foundation-results", type=Path, action="append", default=[])
    options = parser.parse_args()
    completed = _foundation_jobs_finished(tuple(options.foundation_results))
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="satori-knowledge-cost-", dir="/private/tmp") as path:
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
