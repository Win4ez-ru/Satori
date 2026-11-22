"""Actual managed six-head/CLI retention coverage with synthetic data only.

Run with the freshly rebuilt noneditable package after the Foundation gate:
uv run --no-sync python -m tests.retention_scenario --output /private/tmp/retention-report.json
The report contains counts, closed reasons and timings; no row IDs, prose or body hashes.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from functools import partial
from pathlib import Path
from time import perf_counter
from typing import cast
from unittest.mock import patch

from satori.application.erasure.retention_contracts import RetentionStatusRequest
from satori.application.erasure.retention_use_cases import RetentionPlanner
from satori.composition import ConversationServices, build_conversation_services
from satori.domain.erasure_knowledge import KnowledgeBodyAction
from satori.domain.erasure_preparation import ErasurePreparationOwner
from satori.domain.retention import RetentionReason
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure_routes import ROUTE_SPECS
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.retention_inventory import SQLiteRetentionInventory
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.checkpoint_reason_support import (
    APPROVAL_REASON,
    RESTORE_REASON,
    create_checkpoint_reason_fixture,
)
from tests.erasure_preview_scenario import CLI_ENTRY
from tests.erasure_support import provider_counts
from tests.test_erasure_appraisal_signal_continuity import retained_signals
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import DERIVE_TEXT, ROOT_TEXT, SECRET, _services, _turn
from tests.test_stage8_relationship_integration import _provider
from tests.test_stage14_personality_persistence import SOURCE_TEXTS

BOB_TEXT = "Независимый собеседник разрабатывает свой кодовый проект."
UNKNOWN_SCALAR = "SYNTHETIC_UNKNOWN_SCALAR_NOTE_731"
PROTECTED = (
    SECRET,
    ROOT_TEXT,
    DERIVE_TEXT,
    APPROVAL_REASON,
    RESTORE_REASON,
    BOB_TEXT,
    UNKNOWN_SCALAR,
    "Synthetic observation recorded.",
    *SOURCE_TEXTS,
)
TERMINAL_TABLES = (
    "episodic_memories",
    "memory_evidence",
    "conversation_messages",
    "semantic_claims",
    "reflection_proposals",
    "affective_transitions",
    "personality_checkpoint_approvals",
    "personality_restore_events",
)


def _integer(value: object) -> int:
    assert type(value) is int, "retention_scenario_noninteger_count"
    return value


def _table_map(value: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = value["table_counts"]
    assert isinstance(raw, list), "retention_scenario_invalid_table_counts"
    result: dict[str, dict[str, object]] = {}
    for item in raw:
        assert isinstance(item, dict), "retention_scenario_invalid_table_row"
        table = item["table"]
        assert isinstance(table, str)
        assert table in ROUTE_SPECS, "retention_scenario_unknown_table"
        assert table not in result, "retention_scenario_duplicate_table"
        ErasurePreparationOwner(item["owner"])
        result[table] = cast(dict[str, object], item)
    return result


def _partition(value: dict[str, object]) -> bool:
    tables = _table_map(value)
    fields = ("total_rows", "supported_retained_rows", "owned_content_rows", "unknown_rows")
    return (
        set(tables) == set(ROUTE_SPECS)
        and len({row["owner"] for row in tables.values()}) == len(ErasurePreparationOwner)
        and all(
            sum(_integer(row[field]) for field in fields[1:]) == _integer(row["total_rows"])
            for row in tables.values()
        )
        and all(
            sum(_integer(row[field]) for row in tables.values()) == _integer(value[field])
            for field in fields
        )
        and sum(_integer(value[field]) for field in fields[1:]) == _integer(value["total_rows"])
        and value["total_rows"] == value["scanned_rows"]
        and all(
            0 <= _integer(row[facet]) <= _integer(row["total_rows"])
            for row in tables.values()
            for facet in ("structural_metadata_rows", "numeric_history_rows", "terminal_body_rows")
        )
    )


def _summary(value: dict[str, object]) -> dict[str, object]:
    """Only closed public coverage fields; omit every scope/hash/private selector."""
    allowed = (
        "schema_revision",
        "generation",
        "erasure_revision",
        "body_revision",
        "scanned_rows",
        "work_units",
        "total_rows",
        "supported_retained_rows",
        "owned_content_rows",
        "unknown_rows",
        "coverage",
        "logical_readiness",
        "whole_head_current",
        "physical_readiness",
        "physical_erasure",
        "execution_authorized",
        "inventory_scope",
    )
    result = {key: value[key] for key in allowed}
    table_fields = (
        "owner",
        "table",
        "total_rows",
        "supported_retained_rows",
        "owned_content_rows",
        "unknown_rows",
        "structural_metadata_rows",
        "numeric_history_rows",
        "terminal_body_rows",
    )
    result["table_counts"] = [
        {field: row[field] for field in table_fields} for row in _table_map(value).values()
    ]
    raw_reasons = value["reason_counts"]
    assert isinstance(raw_reasons, list)
    reasons = []
    for item in raw_reasons:
        assert isinstance(item, dict)
        reasons.append(
            {"reason": RetentionReason(item["reason"]).value, "rows": _integer(item["rows"])}
        )
    result["reason_counts"] = reasons
    return result


def _bytes(path: Path, runtime: SourcePolicyRuntime) -> tuple[bytes, ...]:
    return tuple(
        candidate.read_bytes()
        for candidate in (
            path,
            runtime.ledger.database_path,
            runtime.ledger.binding_path,
            runtime.ledger.lock_path,
        )
    )


def _cognitive_counts(services: tuple[ConversationServices, ...]) -> tuple[int, ...]:
    counts = list(provider_counts(services))
    for service in services:
        candidates = (
            service.talk.prepare_affect,
            service.process_relationship,
            service.process_models,
            service.process_positions,
            service.process_reflection,
        )
        for use_case in candidates:
            provider = getattr(use_case, "provider", None)
            counts.append(len(getattr(provider, "requests", ())))
    return tuple(counts)


def _backup(database: Database, target: Path) -> None:
    source = Path(str(database.engine.url.database))
    with (
        sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as original,
        sqlite3.connect(target) as copied,
    ):
        original.backup(copied)
    os.chmod(target, 0o600)


def _corrupt_copy(
    database: Database,
    target: Path,
    *,
    table: str,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    """Disposable attack: retain the exact admitted DDL after restoring every guard.

    Production guards are tested first. A guarded legacy/corrupt state is manufactured
    only in this isolated backup; the main database, floor and product writers are untouched.
    """
    _backup(database, target)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            connection.execute(statement, parameters)
        except sqlite3.IntegrityError:
            connection.rollback()
            guards = connection.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type='trigger' AND tbl_name=?",
                (table,),
            ).fetchall()
            assert guards, "retention_scenario_missing_expected_guard"
            for name, _sql in guards:
                quoted = '"' + str(name).replace('"', '""') + '"'
                connection.execute(f"DROP TRIGGER {quoted}")
            connection.execute(statement, parameters)
            for _name, sql in guards:
                connection.execute(sql)
        connection.commit()
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def _denied_before_census(operation: Callable[[], object]) -> str:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("retention_scenario_census_before_admission")

    with patch("satori.infrastructure.persistence.retention_inventory._load_store", forbidden):
        try:
            operation()
        except SourcePolicyError as error:
            return error.reason_code
    raise AssertionError("retention_scenario_expected_admission_denial")


def run(project: Path, directory: Path) -> dict[str, object]:
    path = directory / "synthetic.db"
    url = f"sqlite+pysqlite:///{path}"
    upgrade_database(url, config_path=project / "alembic.ini")
    database = create_database(url)
    checks: dict[str, bool] = {}
    timings: list[dict[str, object]] = []
    samples: dict[str, object] = {}
    results: list[dict[str, object]] = []
    try:
        # Exercise ordinary existing relationship processing, rather than leaving
        # an undecided backlog whose unsupported withdrawal must remain UNKNOWN.
        relationship = _provider("neutral_contact")
        with patch(
            "tests.test_source_exclusion_production.build_conversation_services",
            partial(build_conversation_services, relationship_provider=relationship),
        ):
            fixture = create_checkpoint_reason_fixture(database, directory / "trusted-floor")
            case = fixture.case
            bob, _foreground, embedding = _services(
                database, case.directory, "retention-independent-bob", 128, counterparty="bob"
            )
            embedding.vectors[BOB_TEXT] = (0.0, 1.0, 0.0)
            bob_reply = _turn(bob, BOB_TEXT, "retention-independent-bob")
        controls = (
            _cognitive_counts((*case.services, bob)),
            len(fixture.reflection.provider.requests),
            len(fixture.personality_provider.requests),
        )
        environment = {
            **os.environ,
            "SATORI_DATABASE_URL": url,
            "SATORI_SOURCE_POLICY_DIRECTORY": str(case.directory),
            "SATORI_DEFAULT_COUNTERPARTY_ID": "alice",
            "SATORI_ENVIRONMENT": "test",
            "SATORI_LOG_LEVEL": "CRITICAL",
        }

        def cli(
            phase: str,
            args: list[str],
            *,
            database_url: str = url,
            expected: tuple[int, ...] = (0,),
            decode: bool = True,
            floor_directory: Path | None = None,
        ) -> dict[str, object]:
            started = perf_counter()
            command_environment = {**environment, "SATORI_DATABASE_URL": database_url}
            if floor_directory is not None:
                command_environment["SATORI_SOURCE_POLICY_DIRECTORY"] = str(floor_directory)
            response = subprocess.run(
                [sys.executable, "-c", CLI_ENTRY, *args],
                cwd=project,
                env=command_environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            timings.append(
                {
                    "phase": phase,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "exit_code": response.returncode,
                }
            )
            assert response.returncode in expected, "retention_scenario_cli_failed"
            assert all(text not in response.stdout + response.stderr for text in PROTECTED), (
                "retention_scenario_private_content_disclosed"
            )
            if not decode:
                return {}
            value = json.JSONDecoder().raw_decode(response.stdout.lstrip())[0]
            assert isinstance(value, dict), "retention_scenario_invalid_cli_json"
            return cast(dict[str, object], value)

        base = ["state", "retention", "--identity-id", case.scope.identity_id]
        reason_base = ["personality", "reason-bodies", "--identity-id", case.scope.identity_id]
        selected = list(reason_base)
        for selector in fixture.selectors:
            selected.extend(
                [
                    "--approval" if selector.kind.value == "checkpoint_approval" else "--restore",
                    selector.record_id,
                ]
            )
        cli("reason_policy_upgrade", ["personality", "reasons-policy", "--upgrade", "--reviewed"])
        old = cli("five_head_status", [*base, "--status"], expected=(0, 2))
        samples["five_head"] = _summary(old)
        results.append(old)
        before = stored_rows(path)
        with case.runtime.shared():
            actions_before = case.runtime.body_actions()
        signals_before = retained_signals(database, fixture.reflection.run.run_id)
        archive = directory / "before-reasons.state"
        cli("old_archive_export", ["state", "export", "--output", str(archive)])
        archive_bytes = archive.read_bytes()
        preview = cli("reason_preview", [*selected, "--preview"])
        apply = [
            *selected,
            "--apply",
            "--request-id",
            "retention-sixth-reason-action",
            "--expected-generation",
            str(preview["generation"]),
            "--expected-erasure-revision",
            str(preview["erasure_revision"]),
            "--expected-body-revision",
            str(preview["body_revision"]),
            "--sha256",
            str(preview["approval_digest"]),
        ]
        reason_result = cli("sixth_reason_action", apply)
        assert reason_result["main_projection_current"] is True
        current = cli("six_head_status", [*base, "--status"], expected=(0, 2))
        samples["six_head"] = _summary(current)
        results.append(current)
        tables = _table_map(current)
        with case.runtime.shared():
            actions = case.runtime.body_actions()
        knowledge_action = actions[2]
        assert isinstance(knowledge_action, KnowledgeBodyAction)
        checks["partition_covers_all_tables_and_15_owners_exactly_once"] = _partition(current)
        checks["known_six_head_is_complete_while_independent_content_remains"] = (
            current["coverage"] == "complete"
            and current["logical_readiness"] == "owned_content_remaining"
            and _integer(current["owned_content_rows"]) > 0
            and current["body_revision"] == 6
            and tuple(action.proposal.policy_version for action in actions)
            == (
                "episode_body_erasure_v1",
                "dialogue_body_erasure_v1",
                "knowledge_body_erasure_v1",
                "reflection_body_erasure_v1",
                "appraisal_explanation_erasure_v1",
                "checkpoint_reason_erasure_v1",
            )
        )
        # Knowledge action 3 exercises semantic claims. This fixture contains no
        # Position aggregates, so it makes no claim about exercising that subtype.
        checks["every_exercised_terminal_family_has_retained_terminal_facets"] = all(
            _integer(tables[table]["terminal_body_rows"]) > 0 for table in TERMINAL_TABLES
        ) and any(
            target.kind.value == "semantic_claim" for target in knowledge_action.proposal.targets
        )
        root_preview = cli(
            "selected_root_assessment",
            ["sources", "erasure", case.message_id, "--preview"],
            expected=(0, 2),
        )
        results.append(root_preview)
        root_routes = root_preview["routes"]
        assert isinstance(root_routes, list)
        checks["selected_root_routes_consume_finished_reflection_appraisal_and_reason_proofs"] = (
            all(
                any(
                    isinstance(route, dict)
                    and route["table"] == table
                    and route["disposition"]
                    in {"retain_structural_metadata", "retain_numeric_history"}
                    and _integer(route["rows"]) > 0
                    for route in root_routes
                )
                for table in (
                    "reflection_proposals",
                    "affective_transitions",
                    "personality_checkpoint_approvals",
                    "personality_restore_events",
                )
            )
        )
        after = stored_rows(path)
        checks["numerical_history_audit_bindings_and_old_material_hashes_unchanged"] = all(
            before[table] == after[table]
            for table in (
                "satori_personality_states",
                "satori_personality_traits",
                "personality_revisions",
                "personality_checkpoints",
                "personality_checkpoint_traits",
                "audit_events",
                "audit_metadata_bindings",
                "erasure_preparations",
            )
        )
        with case.runtime.shared():
            checks["five_original_actions_and_appraisal_numerical_signals_remain_exact"] = (
                case.runtime.body_actions()[:5] == actions_before
                and retained_signals(database, fixture.reflection.run.run_id) == signals_before
            )
        checks["independent_bob_content_and_scoped_provenance_survive"] = (
            any(BOB_TEXT in repr(row) for row in after["conversation_messages"])
            and before["conversation_messages"] == after["conversation_messages"]
        )
        stable = _bytes(path, case.runtime)
        checks["fresh_process_status_is_repeatable_and_byte_preserving"] = (
            cli("repeat_status", [*base, "--status"], expected=(0, 2)) == current
            and stable == _bytes(path, case.runtime)
            and stored_rows(path) == after
        )
        check_args = [
            *base,
            "--check",
            "--expected-generation",
            str(current["generation"]),
            "--expected-erasure-revision",
            str(current["erasure_revision"]),
            "--expected-body-revision",
            str(current["body_revision"]),
            "--sha256",
            str(current["review_digest"]),
        ]
        checked = cli("fresh_check", check_args, expected=(0, 2))
        results.append(checked)
        checks["fresh_review_check_is_not_execution_and_does_not_write"] = checked[
            "review_status"
        ] == "current" and stable == _bytes(path, case.runtime)
        stale_head = list(check_args)
        stale_head[stale_head.index("--expected-body-revision") + 1] = str(old["body_revision"])
        cli("stale_body_head", stale_head, expected=(2,), decode=False)
        foreign = ["state", "retention", "--identity-id", "foreign-identity", "--status"]
        cli("wrong_identity", foreign, expected=(2,), decode=False)
        bare_runtime = SourcePolicyRuntime(case.directory, case.scope.identity_id)
        planner = RetentionPlanner(bare_runtime, SQLiteRetentionInventory(database))
        request = RetentionStatusRequest(identity_id=case.scope.identity_id)
        checks["no_callback_adapter_still_proves_current_main"] = (
            planner.status(request).model_dump(mode="json") == current
        )
        _denied_before_census(lambda: planner.status(RetentionStatusRequest(identity_id="foreign")))
        checks["wrong_identity_denied_before_private_census"] = True

        legacy = directory / "missing-seal.db"
        _corrupt_copy(
            database,
            legacy,
            table="audit_metadata_bindings",
            statement="DELETE FROM audit_metadata_bindings WHERE family=?",
            parameters=("checkpoint_approval",),
        )
        legacy_url = f"sqlite+pysqlite:///{legacy}"
        legacy_before = legacy.read_bytes()
        unknown = cli(
            "legacy_missing_seal", [*base, "--status"], database_url=legacy_url, expected=(2,)
        )
        samples["legacy_missing_seal"] = _summary(unknown)
        results.append(unknown)
        checks["legacy_missing_seal_stays_unknown_without_repair_or_new_authority"] = (
            unknown["coverage"] == "partial"
            and unknown["logical_readiness"] == "blocked_unknown"
            and _integer(unknown["unknown_rows"]) > _integer(current["unknown_rows"])
            and legacy.read_bytes() == legacy_before
        )
        bad_scalar = directory / "unsupported-scalar.db"
        _corrupt_copy(
            database,
            bad_scalar,
            table="semantic_claim_revisions",
            statement=(
                "UPDATE semantic_claim_revisions SET reason_code=? WHERE claim_id IN "
                "(SELECT claim_id FROM semantic_claim_evidence WHERE root_interaction_id=?)"
            ),
            parameters=(UNKNOWN_SCALAR, bob_reply.interaction_id),
        )
        scalar_url = f"sqlite+pysqlite:///{bad_scalar}"
        scalar_before = bad_scalar.read_bytes()
        scalar = cli(
            "unsupported_scalar", [*base, "--status"], database_url=scalar_url, expected=(2,)
        )
        samples["unsupported_scalar"] = _summary(scalar)
        results.append(scalar)
        scalar_tables = _table_map(scalar)
        checks["same_head_unknown_scalar_is_not_retained_numeric_by_table_name"] = (
            _integer(scalar_tables["semantic_claim_revisions"]["unknown_rows"]) > 0
            and scalar["logical_readiness"] == "blocked_unknown"
            and scalar["body_revision"] == current["body_revision"]
            and bad_scalar.read_bytes() == scalar_before
        )
        cli(
            "same_head_stale_review",
            check_args,
            database_url=scalar_url,
            expected=(2,),
            decode=False,
        )
        checks["same_head_store_or_classification_drift_invalidates_review"] = True

        imported = cli("old_archive_preview", ["state", "import", str(archive), "--preview"])
        restored = directory / "restored.db"
        cli(
            "old_archive_restore",
            [
                "state",
                "import",
                str(archive),
                "--activate",
                "--target",
                str(restored),
                "--sha256",
                str(imported["archive_sha256"]),
            ],
        )
        restored_url = f"sqlite+pysqlite:///{restored}"
        pending_before = restored.read_bytes()
        cli(
            "pending_old_main_denied",
            [*base, "--status"],
            database_url=restored_url,
            expected=(2,),
            decode=False,
        )
        restored_database = create_database(restored_url)
        try:
            pending_planner = RetentionPlanner(
                bare_runtime, SQLiteRetentionInventory(restored_database)
            )
            _denied_before_census(lambda: pending_planner.status(request))
            checks["old_archive_pending_head_denies_even_without_runtime_callback"] = (
                restored.read_bytes() == pending_before
            )
        finally:
            restored_database.dispose()
        resumed = cli(
            "explicit_whole_head_resume",
            [
                *reason_base,
                "--resume",
                "--request-id",
                "retention-sixth-reason-action",
            ],
            database_url=restored_url,
        )
        restored_status = cli(
            "restored_current_status",
            [*base, "--status"],
            database_url=restored_url,
            expected=(0, 2),
        )
        samples["restored_current"] = _summary(restored_status)
        results.extend((resumed, restored_status))
        restored_rows = stored_rows(restored)
        checks["explicit_resume_reproves_six_head_and_restores_same_partition"] = (
            resumed["main_projection_current"] is True
            and _summary(restored_status) == _summary(current)
            and restored_status["classification_digest"] == current["classification_digest"]
            and all(
                restored_rows[table] == rows
                for table, rows in after.items()
                if table != "erasure_body_applications"
            )
        )
        # Actual re-application records a truthful new main applied_at. The full-store
        # review may differ while the trusted actions and logical partition stay exact.
        checks["restored_original_floor_actions_are_exact_despite_new_main_apply_time"] = (
            resumed == reason_result and _table_map(restored_status) == tables
        )
        restored_check = list(check_args)
        restored_check[-1] = str(restored_status["review_digest"])
        results.append(
            cli(
                "restored_fresh_check",
                restored_check,
                database_url=restored_url,
                expected=(0, 2),
            )
        )
        absent_floor = directory / "absent-floor"
        cli(
            "missing_floor_denied",
            [*base, "--status"],
            floor_directory=absent_floor,
            expected=(2,),
            decode=False,
        )
        checks["missing_floor_is_not_implicitly_created_or_enrolled"] = not absent_floor.exists()
        checks["old_archive_and_accepted_main_remain_unchanged"] = (
            archive.read_bytes() == archive_bytes
            and stable == _bytes(path, case.runtime)
            and stored_rows(path) == after
        )
        checks["controls_construct_no_cognitive_requests"] = controls == (
            _cognitive_counts((*case.services, bob)),
            len(fixture.reflection.provider.requests),
            len(fixture.personality_provider.requests),
        )
        checks["all_public_results_deny_physical_erasure_and_technical_execution"] = all(
            value["physical_erasure"] is False and value["execution_authorized"] is False
            for value in results
        )
        report: dict[str, object] = {
            "stage": "14.4.2i Whole-store Retention & Readiness Integration",
            "synthetic_only": True,
            "real_provider_calls": 0,
            "paid_cost_usd": 0,
            "physical_erasure": False,
            "execution_authorized": False,
            "accepted": all(checks.values()),
            "checks": checks,
            "samples": samples,
            "timings": timings,
        }
        serialized = json.dumps(report, ensure_ascii=False)
        assert all(text not in serialized for text in PROTECTED)
        for private_id in (
            case.message_id,
            case.derived_message_id,
            *(s.record_id for s in fixture.selectors),
        ):
            assert private_id not in serialized, "retention_scenario_private_selector_reported"
        return report
    finally:
        database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="satori-retention-", dir="/private/tmp") as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o700)
        report = run(Path(__file__).resolve().parents[1], directory)
    PrivateArtifactStore().write(args.output, json.dumps(report, indent=2).encode())
    print(json.dumps(report))
    if report["accepted"] is not True:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
