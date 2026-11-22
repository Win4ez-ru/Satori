"""Real owners, trusted action floor and restore/replay; synthetic data only."""

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.engine import Connection

from satori.application.erasure.body_contracts import BodyErasureCommand, BodyErasureRequest
from satori.application.erasure.body_use_cases import BodyErasureManager
from satori.application.memory.lifecycle_contracts import MemoryLifecycleError
from satori.application.retrieval.contracts import RetrievalQuery, RetrievalStatus
from satori.composition import build_memory_lifecycle_services
from satori.domain.erasure_bodies import BodyErasureTarget, BodyPlan
from satori.domain.memory import MemoryLifecycleStatus
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure_bodies import SQLiteBodyErasureStore
from satori.infrastructure.persistence.models import (
    EpisodicMemoryRow,
    MemoryEvidenceRow,
    SourcePayloadRow,
)
from satori.infrastructure.persistence.repositories.memory import SQLAlchemyEpisodicMemoryRepository
from satori.infrastructure.source_policy.installation import bind_installation, repair_installation
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.erasure_support import ErasureCase, create_case, provider_counts
from tests.test_erasure_intent_integration import _command as intent_command
from tests.test_erasure_intent_integration import _manager as intent_manager
from tests.test_erasure_intent_integration import _request as intent_request
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import RECALL, SECRET, _exclude, _services, _turn


def prepare(case: ErasureCase) -> tuple[BodyErasureManager, BodyErasureRequest]:
    manager = intent_manager(case)
    command = intent_command(manager, intent_request(case))
    assert manager.commit(command).main_projection_current
    case.runtime.upgrade_body_policy()
    return BodyErasureManager(
        case.runtime, SQLiteBodyErasureStore(case.database)
    ), BodyErasureRequest(
        message_id=case.message_id,
        counterparty_id=case.scope.counterparty_id,
        intent_request_id=command.request_id,
    )


def command_for(
    manager: BodyErasureManager, request: BodyErasureRequest, request_id: str = "body-action"
) -> BodyErasureCommand:
    preview = manager.preview(request)
    assert preview.readiness == "ready", preview.blockers
    return BodyErasureCommand(
        **request.model_dump(),
        request_id=request_id,
        expected_generation=preview.generation,
        expected_erasure_revision=preview.erasure_revision,
        expected_body_revision=preview.body_revision,
        approval_digest=preview.approval_digest,
    )


@pytest.fixture
def case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "body-floor")


def test_real_derived_bodies_removed_numeric_and_other_owners_retained(case: ErasureCase) -> None:
    manager, request = prepare(case)
    before = stored_rows(case.path)
    preview = manager.preview(request)
    assert preview.summaries == 2
    assert preview.quotes == 2
    assert preview.vectors == 2
    assert stored_rows(case.path) == before
    command = command_for(manager, request)
    result = manager.commit(command)
    assert result.main_projection_current
    assert result.selected_bodies_removed
    assert result.physical_erasure is False
    with case.database.session_factory() as session:
        for memory in session.scalars(select(EpisodicMemoryRow)):
            assert memory.summary is None
            assert memory.body_state == "erased"
            assert memory.aggregate_version == 1
            header = SQLAlchemyEpisodicMemoryRepository(session).get_erased_header(memory.memory_id)
            assert header is not None
            assert header.aggregate_version == 1
        assert all(
            edge.quote is None and edge.body_state == "erased"
            for edge in session.scalars(select(MemoryEvidenceRow))
        )
    after = stored_rows(case.path)
    assert after["episodic_memory_embeddings"] == []
    changed = {
        "episodic_memories",
        "memory_evidence",
        "episodic_memory_embeddings",
        "erasure_body_applications",
        "erasure_body_bindings",
    }
    assert {name: rows for name, rows in after.items() if name not in changed} == {
        name: rows for name, rows in before.items() if name not in changed
    }
    assert manager.commit(command) == result
    assert manager.status(request, request_id=command.request_id) == result
    assert stored_rows(case.path) == after
    public = result.model_dump_json() + preview.model_dump_json()
    assert SECRET not in public
    assert all(memory_id not in public for memory_id in case.episode_ids)
    assert all(claim_id not in public for claim_id in case.claim_ids)
    assert provider_counts(case.services) == case.initial_provider_calls


def test_floor_first_main_failure_restart_and_read_only_status(
    case: ErasureCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, request = prepare(case)
    command = command_for(manager, request)
    before = stored_rows(case.path)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic private exception")

    monkeypatch.setattr(manager.store, "apply", fail)
    pending = manager.commit(command)
    assert pending.phase == "body_application_pending"
    assert stored_rows(case.path) == before
    with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
        bind_installation(case.database, case.directory)
    runtime = SourcePolicyRuntime(case.directory, case.scope.identity_id)
    fresh = BodyErasureManager(runtime, SQLiteBodyErasureStore(case.database))
    before_status = (case.path.read_bytes(), runtime.ledger.database_path.read_bytes())
    assert not fresh.status(request, request_id=command.request_id).main_projection_current
    assert before_status == (case.path.read_bytes(), runtime.ledger.database_path.read_bytes())
    assert fresh.resume(request, request_id=command.request_id).main_projection_current
    bind_installation(case.database, case.directory)


def test_after_intent_before_body_archive_is_fenced_then_explicit_resume(
    case: ErasureCase, tmp_path: Path
) -> None:
    manager, request = prepare(case)
    old_path = tmp_path / "after-intent-before-body.db"
    with sqlite3.connect(case.path) as source, sqlite3.connect(old_path) as target:
        source.backup(target)
    command = command_for(manager, request)
    assert manager.commit(command).main_projection_current
    restored = create_database(f"sqlite+pysqlite:///{old_path}")
    try:
        with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
            bind_installation(restored, case.directory)
        runtime = SourcePolicyRuntime(case.directory, case.scope.identity_id)
        with runtime.exclusive() as snapshot:
            receipt = runtime.receipt(f"exclude-{case.message_id}")
            assert receipt is not None
            repair_installation(restored, runtime, snapshot, receipt)
        with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
            bind_installation(restored, case.directory)
        runtime = SourcePolicyRuntime(case.directory, case.scope.identity_id)
        fresh = BodyErasureManager(runtime, SQLiteBodyErasureStore(restored))
        assert not fresh.status(request, request_id=command.request_id).main_projection_current
        assert fresh.resume(request, request_id=command.request_id).main_projection_current
        bind_installation(restored, case.directory)
        restarted, provider, embedding = _services(
            restored, case.directory, "after-body-restore", 12, form=False
        )
        assert SECRET not in _turn(restarted, RECALL, "after-body-restore").text
        assert SECRET not in repr(provider.requests)
        assert SECRET not in repr(embedding.requests)
        assert restarted.retrieve_memories is not None
        assert (
            asyncio.run(
                restarted.retrieve_memories.execute(
                    RetrievalQuery(
                        text=RECALL,
                        trace_id="body-recall",
                        cutoff=datetime.now(UTC),
                        current_interaction_id=None,
                        scope=case.scope,
                        operator_query=True,
                    )
                )
            ).status
            is RetrievalStatus.NO_RELEVANT_MEMORY
        )
    finally:
        restored.dispose()


def test_stale_review_denies_before_floor_and_new_approval_repairs_changed_pending_body(
    case: ErasureCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, request = prepare(case)
    old = command_for(manager, request)
    with case.database.session_factory() as session:
        session.execute(
            update(EpisodicMemoryRow)
            .where(EpisodicMemoryRow.memory_id == case.episode_ids[0])
            .values(summary="Changed synthetic summary")
        )
        session.commit()
    with pytest.raises(SourcePolicyError, match="body_erasure_review_stale"):
        manager.commit(old)
    with case.runtime.exclusive() as snapshot:
        assert snapshot.body_revision == 0
    current = command_for(manager, request)
    original = manager.store.apply

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("failure before bodies")

    monkeypatch.setattr(manager.store, "apply", fail)
    assert not manager.commit(current).main_projection_current
    monkeypatch.setattr(manager.store, "apply", original)
    with case.database.session_factory() as session:
        session.execute(
            update(EpisodicMemoryRow)
            .where(EpisodicMemoryRow.memory_id == case.episode_ids[0])
            .values(summary="Changed again synthetic summary")
        )
        session.commit()
    pending = manager.resume(request, request_id=current.request_id)
    assert pending.failure_reason == "body_target_changed_review_required"
    approved = command_for(manager, request, "body-review-revision-two")
    done = manager.commit(approved)
    assert done.main_projection_current
    assert done.materialized_body_revision == 2
    old_result = manager.resume(request, request_id=current.request_id)
    assert old_result.body_revision == 1
    assert old_result.materialized_body_revision == 2
    assert old_result.effective_body_head_digest == done.effective_body_head_digest


def test_mid_body_failure_rolls_back_both_owners_but_retains_floor_action(
    case: ErasureCase,
) -> None:
    manager, request = prepare(case)
    command = command_for(manager, request)
    before = stored_rows(case.path)

    def fail(
        _conn: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        if statement.startswith("UPDATE memory_evidence"):
            raise RuntimeError("synthetic_failure_after_vector_and_summary_mutations")

    event.listen(case.database.engine, "before_cursor_execute", fail)
    try:
        result = manager.commit(command)
    finally:
        event.remove(case.database.engine, "before_cursor_execute", fail)
    assert not result.main_projection_current
    assert stored_rows(case.path) == before
    with case.runtime.exclusive() as snapshot:
        assert snapshot.body_revision == 1
    assert manager.resume(request, request_id=command.request_id).main_projection_current


@pytest.mark.parametrize("legacy_unknown", [False, True])
def test_proven_unrelated_bob_body_retained_and_unknown_ancestry_blocks(
    case: ErasureCase, legacy_unknown: bool
) -> None:
    bob, _provider, _embedding = _services(
        case.database, case.directory, "unrelated-bob", 4, counterparty="bob"
    )
    reply = _turn(bob, "Синтетическая независимая запись Боба.", "unrelated-bob")
    with case.database.session_factory() as session:
        memory_id = session.scalar(
            select(EpisodicMemoryRow.memory_id).where(
                EpisodicMemoryRow.source_interaction_id == reply.interaction_id
            )
        )
        assert memory_id is not None
        if legacy_unknown:
            session.execute(
                update(SourcePayloadRow)
                .where(SourcePayloadRow.kind == "episode", SourcePayloadRow.payload_id == memory_id)
                .values(proof_status="unknown", eligible=False)
            )
            session.commit()
    manager, request = prepare(case)
    before = stored_rows(case.path)
    preview = manager.preview(request)
    if legacy_unknown:
        assert preview.readiness == "blocked"
        assert "episode_body_provenance_unproven" in preview.blockers
        rejected = BodyErasureCommand(
            **request.model_dump(),
            request_id="unknown-body-deny",
            expected_generation=preview.generation,
            expected_erasure_revision=preview.erasure_revision,
            expected_body_revision=preview.body_revision,
            approval_digest=preview.approval_digest,
        )
        with pytest.raises(SourcePolicyError, match="body_erasure_plan_blocked"):
            manager.commit(rejected)
        assert stored_rows(case.path) == before
    else:
        assert manager.commit(command_for(manager, request)).main_projection_current
        after = stored_rows(case.path)
        for table in ("episodic_memories", "memory_evidence", "episodic_memory_embeddings"):
            assert [row for row in after[table] if memory_id in row] == [
                row for row in before[table] if memory_id in row
            ]


def test_archive_terminal_body_keeps_versions_and_cannot_reactivate(
    migrated_database: Database, tmp_path: Path
) -> None:
    from tests.test_memory_lifecycle_integration import _command as lifecycle_command

    case = create_case(migrated_database, tmp_path / "archived-body-floor", excluded=False)
    controls = build_memory_lifecycle_services(case.database)
    memory_id = case.episode_ids[0]
    reviewed = controls.preview(memory_id, MemoryLifecycleStatus.ARCHIVED, scope=case.scope)
    controls.execute(
        lifecycle_command(reviewed, "archive-before-erasure"),
        scope=case.scope,
        trace_id="archive-body",
    )
    _exclude(case.database, case.runtime, case.message_id)
    manager, request = prepare(case)
    assert manager.commit(command_for(manager, request)).main_projection_current
    with case.database.session_factory() as session:
        row = session.get(EpisodicMemoryRow, memory_id)
        assert row is not None
        assert row.lifecycle_status == "archived"
        assert row.aggregate_version == 2
        assert row.summary is None
    with pytest.raises(MemoryLifecycleError):
        controls.preview(memory_id, MemoryLifecycleStatus.ACTIVE, scope=case.scope)


def test_erased_episode_cannot_reactivate_reindex_or_map_as_normal_memory(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    case = create_case(migrated_database, tmp_path / "cached-body-floor", excluded=False)
    cached = case.services[0].memories.execute(scope=case.scope, include_archived=True)
    _exclude(case.database, case.runtime, case.message_id)
    manager, request = prepare(case)
    assert manager.commit(command_for(manager, request)).main_projection_current
    controls = build_memory_lifecycle_services(case.database)
    for memory_id in case.episode_ids:
        with pytest.raises(MemoryLifecycleError):
            controls.preview(memory_id, MemoryLifecycleStatus.ACTIVE, scope=case.scope)
    fresh, _provider, _embedding = _services(case.database, case.directory, "erased-reader", 11)
    assert fresh.memories.execute(scope=case.scope, include_archived=True) == ()
    assert fresh.process_semantic is not None
    assert fresh.post_response.index_memory is not None
    for memory in cached:
        with pytest.raises(ValueError, match="memory_source_ineligible"):
            asyncio.run(
                fresh.post_response.index_memory.execute(memory, trace_id="stale-cache-index")
            )


@pytest.mark.parametrize("operation", ["preview", "commit", "resume", "status"])
def test_foreign_scope_never_discloses_target_or_receipt(case: ErasureCase, operation: str) -> None:
    manager, request = prepare(case)
    command = command_for(manager, request)
    manager.commit(command)
    foreign = BodyErasureRequest(**{**request.model_dump(), "counterparty_id": "bob"})
    operations: dict[str, Callable[[], object]] = {
        "preview": lambda: manager.preview(foreign),
        "commit": lambda: manager.commit(
            BodyErasureCommand(**{**command.model_dump(), "counterparty_id": "bob"})
        ),
        "resume": lambda: manager.resume(foreign, request_id=command.request_id),
        "status": lambda: manager.status(foreign, request_id=command.request_id),
    }
    with pytest.raises(SourcePolicyError, match="canonical_source_unavailable_in_scope"):
        operations[operation]()


def test_pending_head_capacity_is_consistent_in_preview_and_commit(
    case: ErasureCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, request = prepare(case)
    original = command_for(manager, request)
    apply = manager.store.apply

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic pending action")

    monkeypatch.setattr(manager.store, "apply", fail)
    manager.commit(original)
    monkeypatch.setattr(manager.store, "apply", apply)
    with case.runtime.exclusive() as snapshot:
        root = manager.store.resolve_root(
            request.message_id, counterparty_id=request.counterparty_id, snapshot=snapshot
        )
        actual = manager.store.preview(root, snapshot=snapshot)
    large = replace(
        actual,
        scanned_rows=100_000,
        targets=tuple(
            BodyErasureTarget(f"next-{index}", 1, "a" * 64, "b" * 64) for index in range(9)
        ),
    )

    def preview(*args: object, **kwargs: object) -> BodyPlan:
        return large

    monkeypatch.setattr(manager.store, "preview", preview)
    proposed = manager.preview(request)
    assert proposed.readiness == "blocked"
    assert "body_application_capacity_exceeded" in proposed.blockers
    rejected = BodyErasureCommand(
        **request.model_dump(),
        request_id="over-budget-body",
        expected_generation=proposed.generation,
        expected_erasure_revision=proposed.erasure_revision,
        expected_body_revision=proposed.body_revision,
        approval_digest=proposed.approval_digest,
    )
    before = case.runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="body_erasure_plan_blocked"):
        manager.commit(rejected)
    assert case.runtime.ledger.database_path.read_bytes() == before


def test_preview_requires_explicit_body_policy_upgrade_without_side_effect(
    case: ErasureCase,
) -> None:
    intents = intent_manager(case)
    reviewed = intent_command(intents, intent_request(case))
    intents.commit(reviewed)
    manager = BodyErasureManager(case.runtime, SQLiteBodyErasureStore(case.database))
    request = BodyErasureRequest(
        message_id=case.message_id,
        counterparty_id=case.scope.counterparty_id,
        intent_request_id=reviewed.request_id,
    )
    before = (case.path.read_bytes(), case.runtime.ledger.database_path.read_bytes())
    preview = manager.preview(request)
    assert preview.readiness == "blocked"
    assert "policy_body_schema_upgrade_required" in preview.blockers
    assert before == (case.path.read_bytes(), case.runtime.ledger.database_path.read_bytes())
