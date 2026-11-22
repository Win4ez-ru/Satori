"""Read-only real stored inventory, opaque freshness, scope and trusted recovery gates."""

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, null, select, update

import satori.__main__ as cli
import satori.infrastructure.persistence.erasure as erasure_adapter
from satori.application.erasure.contracts import (
    ErasureCheckRequest,
    ErasurePreviewRequest,
)
from satori.application.erasure.use_cases import ErasurePlanner
from satori.application.recovery.use_cases import WholeStateRecovery
from satori.config import Environment, Settings
from satori.domain.erasure import ErasureDisposition, ErasureInventory, ErasureRoute
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure import SQLiteErasureInventory
from satori.infrastructure.persistence.models import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
    SemanticClaimRow,
    SourceOwnerRepairRow,
    SourcePayloadRow,
)
from satori.infrastructure.persistence.recovery import SQLiteStoredSnapshot
from satori.infrastructure.source_policy.installation import (
    bind_installation,
    canonical_root,
    repair_installation,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.erasure_support import ErasureCase, create_case, provider_counts, stored_state
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import ROOT_TEXT, SECRET, _exclude, _services


@pytest.fixture
def erasure_case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "erasure-policy")


def _check_request(case: ErasureCase) -> ErasureCheckRequest:
    preview = case.planner().preview(case.request)
    return ErasureCheckRequest(
        message_id=case.message_id,
        counterparty_id=case.scope.counterparty_id,
        expected_generation=preview.generation,
        preview_digest=preview.preview_digest,
    )


def test_preview_routes_actual_derivatives_without_disclosing_payloads_or_writing(
    erasure_case: ErasureCase,
) -> None:
    case = erasure_case
    before = stored_state(case)
    main_bytes = case.path.read_bytes()
    floor_bytes = case.runtime.ledger.database_path.read_bytes()
    planner = case.planner()
    preview = planner.preview(case.request)
    repeated = planner.preview(case.request)
    checked = planner.check(_check_request(case))
    assert preview == repeated
    assert checked.review_status == "current"
    assert checked.preview_digest == preview.preview_digest
    # Production appraisal/audit can retain opaque wording without a dependency receipt.
    # A complete census must report that uncertainty, rather than certifying physical readiness.
    assert preview.assessment_status == "blocked"
    assert preview.assessment_blockers
    assert any(
        route.disposition is ErasureDisposition.REVIEW_UNCERTAIN and route.rows > 0
        for route in preview.routes
    )
    assert preview.affected_payload_versions >= 4
    assert preview.physical_readiness == "blocked"
    assert "physical_erasure_executor_not_implemented" in preview.execution_blockers
    assert "durable_erasure_intent_not_committed" in preview.execution_blockers
    assert preview.external_retention == "not_assessed"
    assert not preview.physical_erasure
    assert not preview.execution_authorized
    assert not preview.automatic_processing
    assert preview.historical_numeric_state_retained
    assert preview.unassessed_surfaces
    assert any(
        route.table == "conversation_messages"
        and route.disposition is ErasureDisposition.REDACT_CONTENT
        and route.rows >= 2
        for route in preview.routes
    )
    assert any(route.table == "episodic_memories" and route.rows >= 2 for route in preview.routes)
    assert any(route.table == "semantic_claims" and route.rows >= 2 for route in preview.routes)
    assert any(
        route.disposition is ErasureDisposition.REMOVE_INDEX and route.rows > 0
        for route in preview.routes
    )
    serialized = preview.model_dump_json()
    assert SECRET not in serialized
    assert ROOT_TEXT not in serialized
    for descendant in (
        case.derived_message_id,
        case.derived_interaction_id,
        *case.episode_ids,
        *case.claim_ids,
    ):
        assert descendant not in serialized
    assert stored_state(case) == before
    assert case.path.read_bytes() == main_bytes
    assert case.runtime.ledger.database_path.read_bytes() == floor_bytes
    assert provider_counts(case.services) == case.initial_provider_calls


def test_public_plan_and_routes_are_deeply_immutable(erasure_case: ErasureCase) -> None:
    preview = erasure_case.planner().preview(erasure_case.request)
    with pytest.raises(ValidationError, match="frozen_instance"):
        preview.execution_authorized = True  # type: ignore[assignment]
    assert preview.routes
    with pytest.raises(FrozenInstanceError):
        preview.routes[0].rows = 0  # type: ignore[misc]
    assert preview.execution_authorized is False


def test_complete_known_inventory_port_is_reviewable_but_never_execution_permission(
    erasure_case: ErasureCase,
) -> None:
    """Pure application-port contract; no claim that production opaque rows are COMPLETE."""
    case = erasure_case
    root = canonical_root(case.database, case.runtime, case.message_id, counterparty_id="alice")
    fixed = ErasureInventory(
        root=root,
        schema_revision="0016_source_policy",
        schema_digest="a" * 64,
        store_digest="b" * 64,
        routing_digest="c" * 64,
        routes=(
            ErasureRoute(
                "interaction_log",
                "conversation_messages",
                ErasureDisposition.REDACT_CONTENT,
                1,
                ("content",),
            ),
        ),
        scanned_rows=1,
        affected_payload_versions=0,
        uncertain_payload_versions=0,
    )

    class KnownInventory:
        def __init__(self, inventory: ErasureInventory) -> None:
            self.inventory = inventory

        def inspect(
            self,
            message_id: str,
            *,
            counterparty_id: str,
            snapshot: SourceEligibilitySnapshot,
        ) -> ErasureInventory:
            assert message_id == root.message_id
            assert counterparty_id == root.counterparty_id
            assert message_id in snapshot.excluded_message_ids
            return self.inventory

    port = KnownInventory(fixed)
    planner = ErasurePlanner(case.runtime, port)
    preview = planner.preview(case.request)
    assert preview.assessment_status == "complete"
    assert preview.assessment_blockers == ()
    request = ErasureCheckRequest(
        message_id=root.message_id,
        counterparty_id=root.counterparty_id,
        expected_generation=preview.generation,
        preview_digest=preview.preview_digest,
    )
    checked = planner.check(request)
    assert checked.assessment_status == "complete"
    assert checked.execution_authorized is False
    assert checked.physical_erasure is False
    # An internal row-to-owner routing change binds review even if all public counts agree.
    port.inventory = replace(fixed, routing_digest="d" * 64)
    changed = planner.preview(case.request)
    assert changed.store_digest == preview.store_digest
    assert changed.routes == preview.routes
    assert changed.generation == preview.generation
    assert changed.scanned_rows == preview.scanned_rows
    assert changed.preview_digest != preview.preview_digest
    with pytest.raises(SourcePolicyError, match="erasure_preview_stale"):
        planner.check(request)


@pytest.mark.parametrize("generation", [True, -1, "1"])
def test_check_request_requires_strict_review_binding(generation: object) -> None:
    with pytest.raises(ValidationError):
        ErasureCheckRequest.model_validate(
            {
                "message_id": "synthetic-root",
                "counterparty_id": "alice",
                "expected_generation": generation,
                "preview_digest": "a" * 64,
            }
        )


def test_preview_requires_exclusion_and_never_excludes_on_its_own(
    migrated_database: Database, tmp_path: Path
) -> None:
    case = create_case(migrated_database, tmp_path / "not-excluded-policy", excluded=False)
    before = stored_state(case)
    with pytest.raises(SourcePolicyError, match="erasure_requires_source_exclusion"):
        case.planner().preview(case.request)
    assert stored_state(case) == before
    assert provider_counts(case.services) == case.initial_provider_calls


@pytest.mark.parametrize("counterparty", ["bob", "local-default"])
def test_foreign_target_denied_before_inventory_or_descendant_disclosure(
    erasure_case: ErasureCase, counterparty: str
) -> None:
    case = erasure_case
    before = stored_state(case)
    with pytest.raises(SourcePolicyError, match="canonical_source_unavailable_in_scope"):
        case.planner().preview(
            ErasurePreviewRequest(message_id=case.message_id, counterparty_id=counterparty)
        )
    assert stored_state(case) == before


@pytest.mark.parametrize("proof", ["quarantined", "pending", "non_user"])
def test_unproven_or_incomplete_target_cannot_receive_completed_source_inventory(
    erasure_case: ErasureCase, proof: str
) -> None:
    case = erasure_case
    request = case.request
    with case.database.engine.begin() as connection:
        if proof == "quarantined":
            connection.execute(
                update(ConversationSessionRow)
                .where(
                    ConversationSessionRow.session_id
                    == select(ConversationInteractionRow.session_id)
                    .where(ConversationInteractionRow.interaction_id == case.interaction_id)
                    .scalar_subquery()
                )
                .values(scope_verified=False)
            )
        elif proof == "pending":
            connection.execute(
                update(ConversationInteractionRow)
                .where(ConversationInteractionRow.interaction_id == case.interaction_id)
                .values(
                    status="pending",
                    completed_at=None,
                    provider=None,
                    model=None,
                    finish_status=None,
                    input_tokens=None,
                    output_tokens=None,
                    context_schema_version=None,
                    context_manifest_schema_version=None,
                    policy_id=None,
                    policy_schema_version=None,
                    personality_aggregate_version=None,
                    personality_expression_schema_version=None,
                    personality_expression_cues=null(),
                )
            )
        else:
            assistant_id = connection.scalar(
                select(ConversationMessageRow.message_id).where(
                    ConversationMessageRow.interaction_id == case.interaction_id,
                    ConversationMessageRow.role == "assistant",
                )
            )
            assert assistant_id is not None
            request = ErasurePreviewRequest(message_id=assistant_id, counterparty_id="alice")
    before = stored_state(case)
    with pytest.raises(SourcePolicyError, match="canonical_source_unavailable_in_scope"):
        case.planner().preview(request)
    assert stored_state(case) == before


def test_digest_repeats_after_runtime_and_database_restart(erasure_case: ErasureCase) -> None:
    case = erasure_case
    preview = case.planner().preview(case.request)
    request = _check_request(case)
    case.database.dispose()
    restarted = create_database(str(case.database.engine.url))
    try:
        runtime = bind_installation(restarted, case.directory)
        planner = ErasurePlanner(runtime, SQLiteErasureInventory(restarted))
        assert planner.preview(case.request) == preview
        assert planner.check(request).preview_digest == preview.preview_digest
    finally:
        restarted.dispose()


@pytest.mark.parametrize("mutation", ["owner_version", "opaque_content"])
def test_fresh_check_rejects_same_generation_owner_or_content_drift(
    erasure_case: ErasureCase, mutation: str
) -> None:
    case = erasure_case
    planner = case.planner()
    request = _check_request(case)
    with case.database.engine.begin() as connection:
        if mutation == "owner_version":
            connection.execute(
                update(SemanticClaimRow)
                .where(SemanticClaimRow.claim_id == case.claim_ids[0])
                .values(aggregate_version=SemanticClaimRow.aggregate_version + 1)
            )
        else:
            connection.execute(
                update(ConversationMessageRow)
                .where(
                    ConversationMessageRow.interaction_id == case.derived_interaction_id,
                    ConversationMessageRow.role == "assistant",
                )
                .values(content=f"{SECRET}-OPAQUE-SYNTHETIC-DRIFT")
            )
    mutated = stored_state(case)
    fresh = planner.preview(case.request)
    assert fresh.generation == request.expected_generation
    assert fresh.preview_digest != request.preview_digest
    with pytest.raises(SourcePolicyError, match="erasure_preview_stale"):
        planner.check(request)
    assert stored_state(case) == mutated
    assert provider_counts(case.services) == case.initial_provider_calls


def test_floor_advance_invalidates_previous_check(erasure_case: ErasureCase) -> None:
    case = erasure_case
    request = _check_request(case)
    _exclude(case.database, case.runtime, case.derived_message_id)
    with pytest.raises(SourcePolicyError, match="erasure_generation_conflict"):
        case.planner().check(request)


@pytest.mark.parametrize("problem", ["unknown", "missing", "cycle", "overflow"])
def test_incomplete_dependency_inventory_never_becomes_complete_empty_plan(
    erasure_case: ErasureCase, problem: str
) -> None:
    case = erasure_case
    with case.database.session_factory() as session:
        row = session.get(SourcePayloadRow, ("episode", case.episode_ids[-1], 1))
        assert row is not None
        if problem == "unknown":
            row.proof_status, row.legacy = "unknown", True
        elif problem == "missing":
            session.delete(row)
        elif problem == "cycle":
            row.payload_refs = [{"kind": "episode", "payload_id": row.payload_id, "version": 1}]
        else:
            row.proof_status = "overflow"
        session.commit()
    before = stored_state(case)
    preview = case.planner().preview(case.request)
    assert preview.assessment_status == "blocked"
    assert preview.assessment_blockers
    assert preview.uncertain_payload_versions > 0
    assert preview.physical_erasure is False
    assert stored_state(case) == before


def test_owner_repair_lag_rejects_inventory_without_automatic_repair(
    erasure_case: ErasureCase,
) -> None:
    case = erasure_case
    with case.database.engine.begin() as connection:
        connection.execute(
            delete(SourceOwnerRepairRow).where(SourceOwnerRepairRow.owner == "semantic_memory")
        )
    before = stored_state(case)
    with pytest.raises(SourcePolicyError, match="source_owner_reconciliation_required"):
        case.planner().preview(case.request)
    assert stored_state(case) == before


@pytest.mark.parametrize("damage", ["extra_table", "missing_table", "revision"])
def test_schema_inventory_requires_exact_supported_schema_without_migration(
    erasure_case: ErasureCase, damage: str
) -> None:
    case = erasure_case
    with case.database.engine.begin() as connection:
        if damage == "extra_table":
            connection.exec_driver_sql("CREATE TABLE unregistered_content (text TEXT)")
        elif damage == "missing_table":
            connection.exec_driver_sql("DROP TABLE satori_positions")
        else:
            connection.exec_driver_sql("UPDATE alembic_version SET version_num='untrusted-future'")
    before = stored_state(case)
    with pytest.raises(SourcePolicyError, match="erasure_schema_unsupported"):
        case.planner().preview(case.request)
    assert stored_state(case) == before


@pytest.mark.parametrize("limit", ["MAX_ERASURE_ROWS", "MAX_ERASURE_LOGICAL_BYTES"])
def test_inventory_capacity_is_fail_closed_and_never_truncates_routes(
    erasure_case: ErasureCase, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    case = erasure_case
    before = stored_state(case)
    monkeypatch.setattr(erasure_adapter, limit, 1)
    with pytest.raises(SourcePolicyError, match="erasure_inventory_capacity_exceeded"):
        case.planner().preview(case.request)
    assert stored_state(case) == before


@pytest.mark.parametrize("file_name", ["policy.sqlite", "installation.json", "policy.lock"])
def test_missing_ledger_components_are_not_recreated_by_preview(
    erasure_case: ErasureCase, file_name: str
) -> None:
    case = erasure_case
    path = case.directory / file_name
    path.unlink()
    before = stored_rows(case.path)
    with pytest.raises(SourcePolicyError):
        case.planner().preview(case.request)
    assert not path.exists()
    assert stored_rows(case.path) == before


def test_actual_old_backup_restore_requires_trusted_repair_then_new_fresh_review(
    migrated_database: Database, project_root: Path, tmp_path: Path
) -> None:
    case = create_case(migrated_database, tmp_path / "backup-policy", excluded=False)
    recovery = WholeStateRecovery(
        SQLiteStoredSnapshot(
            str(migrated_database.engine.url), alembic_config=project_root / "alembic.ini"
        ),
        PrivateArtifactStore(),
        reserved_paths=case.runtime.reserved_paths,
    )
    old_archive = tmp_path / "before-exclusion.state"
    old_export = recovery.export(old_archive)
    archive_bytes = old_archive.read_bytes()
    exclusion = _exclude(case.database, case.runtime, case.message_id)
    original_preview = case.planner().preview(case.request)
    # A legitimate independent owner write changes inventory at the same floor generation.
    current, _, _ = _services(
        case.database, case.directory, "post-exclusion-independent", 3, form=False
    )
    current.start_session.execute()
    current_preview = case.planner().preview(case.request)
    assert current_preview.generation == original_preview.generation
    assert current_preview.preview_digest != original_preview.preview_digest
    assert recovery.preview(old_archive) == old_export
    restored_path = tmp_path / "older-restored.db"
    recovery.restore(old_archive, restored_path, expected_sha256=old_export.archive_sha256)
    older = create_database(f"sqlite+pysqlite:///{restored_path}")
    try:
        with pytest.raises(SourcePolicyError, match="source_policy_reconciliation_required"):
            bind_installation(older, case.directory)
        runtime = SourcePolicyRuntime(case.directory, case.scope.identity_id)
        with runtime.exclusive() as snapshot:
            repair_installation(older, runtime, snapshot, exclusion)
        active = bind_installation(older, case.directory)
        planner = ErasurePlanner(active, SQLiteErasureInventory(older))
        restored_preview = planner.preview(case.request)
        assert restored_preview.generation == current_preview.generation
        assert restored_preview.preview_digest != current_preview.preview_digest
        with pytest.raises(SourcePolicyError, match="erasure_preview_stale"):
            planner.check(
                ErasureCheckRequest(
                    message_id=case.message_id,
                    counterparty_id=case.scope.counterparty_id,
                    expected_generation=current_preview.generation,
                    preview_digest=current_preview.preview_digest,
                )
            )
        assert restored_preview.physical_erasure is False
        root = canonical_root(older, active, case.message_id, counterparty_id="alice")
        with active.shared(), pytest.raises(SourcePolicyError, match="canonical_source_excluded"):
            active.check_root(root)
        assert old_archive.read_bytes() == archive_bytes
    finally:
        older.dispose()


def test_metadata_cli_preview_check_cannot_create_providers_or_run_mutations(
    erasure_case: ErasureCase,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = erasure_case

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("read-only erasure control tried provider/bootstrap/enrollment/repair")

    for name in (
        "_open_services",
        "upgrade_database",
        "enroll_installation",
        "repair_installation",
        "_configured_conversation_provider",
        "_configured_episode_provider",
        "_configured_embedding_provider",
        "_configured_semantic_provider",
        "_configured_affective_provider",
        "_configured_relationship_provider",
        "_configured_model_provider",
        "_configured_position_provider",
        "_configured_reflection_provider",
        "OllamaHttpClient",
        "OpenAIHttpClient",
        "YandexAIStudioHttpClient",
    ):
        monkeypatch.setattr(cli, name, forbidden)
    settings = Settings(
        environment=Environment.TEST,
        database_url=str(case.database.engine.url),
        source_policy_directory=case.directory,
        default_counterparty_id="alice",
    )
    before = stored_state(case)
    config = project_root / "alembic.ini"
    assert (
        cli.main(
            ["sources", "erasure", case.message_id, "--preview"],
            settings=settings,
            alembic_config=config,
        )
        == 2
    )
    raw_preview = capsys.readouterr()
    assert SECRET not in raw_preview.out + raw_preview.err
    preview = json.loads(raw_preview.out)
    assert preview["assessment_status"] == "blocked"
    assert preview["physical_erasure"] is False
    assert (
        cli.main(
            [
                "sources",
                "erasure",
                case.message_id,
                "--check",
                "--expected-generation",
                str(preview["generation"]),
                "--sha256",
                preview["preview_digest"],
            ],
            settings=settings,
            alembic_config=config,
        )
        == 2
    )
    checked = json.loads(capsys.readouterr().out)
    assert checked["review_status"] == "current"
    assert checked["execution_authorized"] is False
    assert stored_state(case) == before
    assert provider_counts(case.services) == case.initial_provider_calls


def test_cli_missing_database_preview_never_creates_database_or_policy(
    tmp_path: Path, project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "absent.db"
    policy = tmp_path / "absent-policy"
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+pysqlite:///{target}",
        source_policy_directory=policy,
        default_counterparty_id="alice",
    )
    assert (
        cli.main(
            ["sources", "erasure", "absent-root", "--preview"],
            settings=settings,
            alembic_config=project_root / "alembic.ini",
        )
        == 2
    )
    assert not target.exists()
    assert not policy.exists()
    assert SECRET not in capsys.readouterr().err


def test_cli_exposes_no_apply_or_erase_operation(
    erasure_case: ErasureCase, capsys: pytest.CaptureFixture[str]
) -> None:
    before = stored_state(erasure_case)
    with pytest.raises(SystemExit) as rejected:
        cli.main(["sources", "erasure", erasure_case.message_id, "--apply"])
    assert rejected.value.code == 2
    assert SECRET not in capsys.readouterr().err
    assert stored_state(erasure_case) == before
