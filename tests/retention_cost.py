"""Warm whole-store retention controls on a small private synthetic six-action store.

Run only after the recorded unfiltered Foundation gate has passed. Timings and SQL
counts describe this local fixture, not foreground latency or supported-limit scale.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
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
from satori.application.erasure.retention_contracts import (
    RetentionCheckRequest,
    RetentionStatusRequest,
)
from satori.application.erasure.retention_use_cases import RetentionPlanner
from satori.domain.source_policy import SourceEligibilitySnapshot
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.erasure import _admit_physical_schema, _load_store
from satori.infrastructure.persistence.erasure_bodies import validate_body_projection
from satori.infrastructure.persistence.erasure_inventory import Store
from satori.infrastructure.persistence.erasure_personality_reasons import (
    SQLiteCheckpointReasonBodyStore,
)
from satori.infrastructure.persistence.erasure_routes import ROUTE_SPECS
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.retention_context import RetentionContext
from satori.infrastructure.persistence.retention_contracts import RetentionWorkBudget
from satori.infrastructure.persistence.retention_inventory import SQLiteRetentionInventory
from satori.infrastructure.persistence.source_policy import verify_mirror
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.checkpoint_reason_support import create_checkpoint_reason_fixture
from tests.erasure_reflection_cost import (
    SAMPLES,
    WARMUPS,
    QueryProbe,
    _foundation_jobs_finished,
    _isolated_settings,
    _row_count,
)
from tests.erasure_support import stored_state
from tests.retention_scenario import PROTECTED, _cognitive_counts

_ROW_SELECT = re.compile(r'\bFROM "([a-z_]+)" ORDER BY ', re.IGNORECASE)
_WRITE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|VACUUM|REINDEX|ATTACH|DETACH)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RetentionProbe(QueryProbe):
    phase: str = ""
    mirror_source: str = ""
    inventory_mirror_proofs: int = 0
    runtime_head_proofs: int = 0
    inventory_head_proofs: int = 0
    census_calls: int = 0
    context_builds: int = 0
    census_active: bool = False
    census_table_selects: Counter[str] = field(default_factory=Counter)
    writes: int = 0
    inventory_connection: Connection | None = None

    def observe(
        self,
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        QueryProbe.observe(self, connection, cursor, statement, parameters, context, many)
        if _WRITE.match(statement):
            self.writes += 1
        if self.census_active and (match := _ROW_SELECT.search(statement)) is not None:
            self.census_table_selects[match.group(1)] += 1

    def extra_counts(self) -> dict[str, int]:
        return {
            "explicit_inventory_mirror_proofs": self.inventory_mirror_proofs,
            "runtime_current_body_projection_proofs": self.runtime_head_proofs,
            "inventory_current_body_projection_proofs": self.inventory_head_proofs,
            "full_store_censuses": self.census_calls,
            "fresh_raw_retention_contexts": self.context_builds,
            "census_table_row_selects": sum(self.census_table_selects.values()),
            "main_dml_or_ddl_statements": self.writes,
        }

    def measure(
        self, label: str, operation: Callable[[], None], *, inventory: bool
    ) -> dict[str, object]:
        self.phase = label
        extra: list[dict[str, int]] = []

        def checked() -> None:
            before = self.extra_counts()
            tables = self.census_table_selects.copy()
            operation()
            delta = {key: value - before[key] for key, value in self.extra_counts().items()}
            if inventory:
                assert delta["explicit_inventory_mirror_proofs"] == 1
                assert delta["inventory_current_body_projection_proofs"] == 1
                assert delta["runtime_current_body_projection_proofs"] == 0
                assert delta["full_store_censuses"] == delta["fresh_raw_retention_contexts"] == 1
                assert delta["census_table_row_selects"] == 60
                assert self.census_table_selects - tables == Counter(dict.fromkeys(ROUTE_SPECS, 1))
            else:
                assert delta["runtime_current_body_projection_proofs"] == 1
                assert delta["explicit_inventory_mirror_proofs"] == 0
                assert delta["inventory_current_body_projection_proofs"] == 0
                assert delta["full_store_censuses"] == delta["fresh_raw_retention_contexts"] == 0
                assert delta["census_table_row_selects"] == 0
            assert delta["main_dml_or_ddl_statements"] == 0
            extra.append(delta)

        result = QueryProbe.sample(self, checked, require_runtime_callback=not inventory)
        measured = extra[WARMUPS:]
        assert len(measured) == SAMPLES
        for key in measured[0]:
            values = [sample[key] for sample in measured]
            result[key + "_samples"] = values
            result[key + "_per_sample"] = {"min": min(values), "max": max(values)}
        self.phase = ""
        return result


def run(project: Path, directory: Path, *, known_jobs: int) -> dict[str, object]:
    assert known_jobs > 0
    assert Path(str(satori.__file__)).resolve().is_relative_to(Path(sys.prefix).resolve())
    url = f"sqlite+pysqlite:///{directory / 'synthetic.db'}"
    upgrade_database(url, config_path=project / "alembic.ini")
    database = create_database(url)
    probe = RetentionProbe()
    observed = False
    try:
        with patch("tests.test_source_exclusion_production.Settings", _isolated_settings):
            fixture = create_checkpoint_reason_fixture(database, directory / "trusted-floor")
        case = fixture.case
        case.runtime.upgrade_checkpoint_reason_policy()
        reasons = CheckpointReasonManager(case.runtime, SQLiteCheckpointReasonBodyStore(database))
        selected = CheckpointReasonRequest(
            identity_id=case.scope.identity_id, selectors=fixture.selectors
        )
        preview = reasons.preview(selected)
        assert preview.readiness == "ready", "retention_cost_fixture_plan_blocked"
        applied = reasons.commit(
            CheckpointReasonCommand(
                identity_id=selected.identity_id,
                selectors=selected.selectors,
                request_id="cost-retention-sixth-action",
                expected_generation=preview.generation,
                expected_erasure_revision=preview.erasure_revision,
                expected_body_revision=preview.body_revision,
                approval_digest=preview.approval_digest,
            )
        )
        assert applied.main_projection_current
        assert applied.body_revision == 6
        calls = (
            _cognitive_counts(case.services),
            len(fixture.reflection.provider.requests),
            len(fixture.personality_provider.requests),
        )
        rows = _row_count(database)
        standalone = SourcePolicyRuntime(case.directory, case.scope.identity_id)
        assert standalone.verify_mirror is None
        planner = RetentionPlanner(standalone, SQLiteRetentionInventory(database))
        request = RetentionStatusRequest(identity_id=case.scope.identity_id)
        current = planner.status(request)
        assert len(current.table_counts) == 60
        assert current.total_rows == rows
        check = RetentionCheckRequest(
            identity_id=request.identity_id,
            expected_generation=current.generation,
            expected_erasure_revision=current.erasure_revision,
            expected_body_revision=current.body_revision,
            review_digest=current.review_digest,
        )
        original_callback = case.runtime.verify_mirror
        assert original_callback is not None

        def runtime_mirror(session: Session, snapshot: SourceEligibilitySnapshot) -> None:
            probe.mirror_source = "runtime"
            try:
                verify_mirror(session, snapshot)
                probe.runtime_mirror_proofs += 1
            finally:
                probe.mirror_source = ""

        def inventory_mirror(session: Session, snapshot: SourceEligibilitySnapshot) -> None:
            probe.mirror_source = "inventory"
            try:
                verify_mirror(session, snapshot)
                probe.inventory_connection = session.connection()
                probe.inventory_mirror_proofs += 1
            finally:
                probe.mirror_source = ""

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

        def body_head(session: Session, snapshot: SourceEligibilitySnapshot) -> None:
            validate_body_projection(session, snapshot)
            if probe.mirror_source == "runtime":
                probe.runtime_head_proofs += 1
            elif probe.mirror_source == "inventory":
                probe.inventory_head_proofs += 1
            else:
                raise AssertionError("retention_cost_unclassified_body_projection_proof")

        def census(
            connection: Connection, schema_digest: str
        ) -> tuple[Store, int, str, dict[str, tuple[str, ...]], str]:
            assert probe.mirror_source == ""
            assert probe.inventory_connection is connection
            assert probe.inventory_head_proofs == probe.inventory_mirror_proofs
            assert connection.exec_driver_sql("PRAGMA query_only").scalar_one() == 1
            probe.census_calls += 1
            probe.census_active = True
            try:
                value = _load_store(connection, schema_digest)
                assert set(value[0]) == set(ROUTE_SPECS)
                assert len(value[0]) == 60
                return value
            finally:
                probe.census_active = False

        build = RetentionContext.build

        def context(
            store: Store,
            *,
            snapshot: SourceEligibilitySnapshot,
            primary_keys: dict[str, tuple[str, ...]],
            schema_digest: str,
            store_digest: str,
            budget: RetentionWorkBudget | None = None,
        ) -> RetentionContext:
            value = build(
                store,
                snapshot=snapshot,
                primary_keys=primary_keys,
                schema_digest=schema_digest,
                store_digest=store_digest,
                budget=budget,
            )
            probe.context_builds += 1
            return value

        def admission() -> None:
            with case.runtime.shared() as snapshot:
                assert snapshot.body_revision == 6

        def status() -> None:
            value = planner.status(request)
            assert value == current
            assert value.whole_head_current
            assert value.physical_erasure is False
            assert value.execution_authorized is False

        def reviewed_check() -> None:
            value = planner.check(check)
            assert value.review_status == "current"
            assert value.body_revision == 6
            assert value.physical_erasure is False
            assert value.execution_authorized is False

        case.runtime.verify_mirror = callback
        before = stored_state(case)
        main_bytes = case.path.read_bytes()
        floor_bytes = case.runtime.ledger.database_path.read_bytes()
        event.listen(database.engine, "before_cursor_execute", probe.observe)
        observed = True
        phases: dict[str, object] = {}
        with (
            patch("satori.infrastructure.source_policy.installation.verify_mirror", runtime_mirror),
            patch(
                "satori.infrastructure.persistence.retention_inventory.verify_mirror",
                inventory_mirror,
            ),
            patch("satori.infrastructure.persistence.erasure._admit_physical_schema", physical),
            patch(
                "satori.infrastructure.persistence.erasure_bodies._admit_physical_schema", physical
            ),
            patch(
                "satori.infrastructure.persistence.erasure_bodies.validate_body_projection",
                body_head,
            ),
            patch("satori.infrastructure.persistence.retention_inventory._load_store", census),
            patch.object(RetentionContext, "build", context),
        ):
            for label, operation, inventory in (
                ("runtime_shared_body6", admission, False),
                ("whole_store_status_body6_without_runtime_callback", status, True),
                ("whole_store_check_body6_without_runtime_callback", reviewed_check, True),
            ):
                phases[label] = probe.measure(label, operation, inventory=inventory)
                assert stored_state(case) == before
                assert case.path.read_bytes() == main_bytes
                assert case.runtime.ledger.database_path.read_bytes() == floor_bytes
        assert standalone.verify_mirror is None
        assert (
            _cognitive_counts(case.services),
            len(fixture.reflection.provider.requests),
            len(fixture.personality_provider.requests),
        ) == calls
        return {
            "schema_version": 1,
            "captured_at": datetime.now(UTC).isoformat(),
            "installed_noneditable_package_verified": True,
            "known_foundation_pytest_jobs_finished": True,
            "known_foundation_jobs_checked": known_jobs,
            "host_load_known": False,
            "host_idle_verified": False,
            "fixture": "small_synthetic_managed_six_action_head_with_retained_initial_content",
            "real_provider_calls": 0,
            "provider_calls_during_controls_and_measurements": 0,
            "paid_provider_calls": 0,
            "cost_usd": 0,
            "physical_erasure": False,
            "execution_authorized": False,
            "source_generation": current.generation,
            "erasure_revision": current.erasure_revision,
            "body_revision": current.body_revision,
            "mixed_body_actions": 6,
            "main_rows": rows,
            "classified_tables": len(current.table_counts),
            "registered_owners": len({item.owner for item in current.table_counts}),
            "coverage": current.coverage,
            "row_partition": {
                "supported_retained": current.supported_retained_rows,
                "owned_content": current.owned_content_rows,
                "unknown": current.unknown_rows,
            },
            "classifier_work_units": current.work_units,
            "inventory_runtime_callback_absent": True,
            "explicit_inventory_admission_before_census_verified": True,
            "census_tables_per_status_or_check": 60,
            "contexts_per_status_or_check": 1,
            "phases": phases,
            "noop_main_rows_and_bytes_unchanged": True,
            "noop_floor_rows_and_bytes_unchanged": True,
            "noop_binding_and_lock_bytes_unchanged": True,
            "limitations": [
                "Small synthetic warm store; no foreground or Character latency benchmark.",
                "No 100000-row/64MiB/one-million-work capacity latency claim.",
                "Only supplied full Foundation jobs checked; other host load unknown.",
                "Nearest-rank p95 over 50 samples; all nanosecond samples retained.",
                "SQL counts cover main SQLAlchemy engine, not independent-floor SQLite.",
                "Runtime projection, explicit inventory projection and raw context proofs "
                "are distinct counters.",
                "One SQL census does not claim one-pass CPU work or physical storage cleanup.",
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
    with tempfile.TemporaryDirectory(prefix="satori-retention-cost-", dir="/private/tmp") as path:
        directory = Path(path)
        os.chmod(directory, 0o700)
        report = run(project, directory, known_jobs=jobs)
    payload = json.dumps(report, sort_keys=True, indent=2)
    assert not any(text in payload for text in PROTECTED), "retention_cost_protected_content"
    assert not any(
        key in payload
        for key in (
            "identity_id",
            "message_id",
            "interaction_id",
            "transition_id",
            "source_input_id",
            "action_id",
            "approval_digest",
            "review_digest",
            "store_digest",
            "body_digest",
            "terminal_digest",
        )
    ), "retention_cost_private_selector"
    PrivateArtifactStore().write(arguments.output, payload.encode())
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
