"""Real SQLite owner transactions, retained evidence and reversible archival.

All sources are synthetic and all cognitive providers are fakes. Archival acceptance is
deterministic state/provenance behavior, never a judgment about sampled Character phrasing.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.engine import Connection

from satori.application.memory.lifecycle_contracts import (
    MemoryLifecycleCommand,
    MemoryLifecycleError,
    MemoryLifecyclePreview,
)
from satori.application.memory.lifecycle_use_cases import MemoryLifecycleControls
from satori.application.retrieval.contracts import RetrievalQuery, RetrievalStatus
from satori.application.retrieval.use_cases import IndexEpisodicMemory
from satori.composition import build_memory_lifecycle_services
from satori.domain.memory import EpisodicMemory, MemoryLifecycleStatus
from satori.domain.memory_scope import MemoryScope
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.repositories.memory import SQLAlchemyEpisodicMemoryRepository
from satori.infrastructure.persistence.retrieval_uow import SQLAlchemyEpisodicMemoryIndexUnitOfWork
from tests.fakes import FrozenClock
from tests.test_memory_scope_integration import RECALL, ScopeProbe, _captured, _probe, _turn
from tests.test_recovery_whole_state import recovery, stored_rows
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate, id_sequence

ACTIVE = MemoryLifecycleStatus.ACTIVE
ARCHIVED = MemoryLifecycleStatus.ARCHIVED
PRIVATE_NAME = "ARCHIVE_PRIVATE_SYNTHETIC"


def _controls(database: Database, prefix: str = "lifecycle-owner") -> MemoryLifecycleControls:
    return build_memory_lifecycle_services(
        database,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=10)),
        id_generator=id_sequence(prefix),
    )


def _fixture(database: Database) -> tuple[ScopeProbe, EpisodicMemory]:
    activate(database)
    probe = _probe(
        database,
        counterparty="alice",
        prefix="lifecycle-source",
        day=1,
    )
    _turn(probe, f"Меня зовут {PRIVATE_NAME}.", "lifecycle-source")
    return probe, probe.services.memories.execute(scope=probe.scope)[0]


def _command(preview: MemoryLifecyclePreview, request_id: str) -> MemoryLifecycleCommand:
    return MemoryLifecycleCommand(
        memory_id=preview.memory_id,
        target_status=preview.target_status,
        expected_version=preview.current_version,
        request_id=request_id,
        preview_digest=preview.preview_digest,
    )


def _rows(database: Database) -> dict[str, list[tuple[object, ...]]]:
    return stored_rows(Path(str(database.engine.url.database)))


def test_preview_is_content_free_read_only_and_binds_retention_effects(
    migrated_database: Database,
) -> None:
    probe, memory = _fixture(migrated_database)
    controls = _controls(migrated_database)
    before = _rows(migrated_database)
    captured = _captured(probe)
    first = controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope)
    controls.clock = FrozenClock(INTERACTION_TIME + timedelta(days=100))
    controls.id_generator = id_sequence("unused-preview")
    second = controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope)
    assert first == second
    assert _rows(migrated_database) == before
    assert _captured(probe) == captured
    assert PRIVATE_NAME not in first.model_dump_json()
    assert first.current_status is ACTIVE
    assert first.current_version == 1
    assert not first.effects.recall
    assert not first.effects.new_semantic
    assert not first.effects.index
    assert not first.effects.new_episode_reflection
    assert first.effects.canonical_dialogue
    assert first.effects.episode_payload_evidence
    assert first.effects.formed_semantic
    assert first.effects.global_self_relationship
    assert first.effects.compatible_vectors
    assert not first.effects.source_excluded
    assert not first.effects.physically_erased
    with pytest.raises(ValidationError, match="frozen_instance"):
        first.effects.recall = True
    with pytest.raises(ValidationError, match="frozen_instance"):
        first.current_version = 2
    active_preview = controls.preview(memory.memory_id, ACTIVE, scope=probe.scope)
    assert active_preview.preview_digest != first.preview_digest


def test_archive_noop_inverse_and_historical_replay_preserve_exact_owner_receipt(
    migrated_database: Database,
) -> None:
    probe, memory = _fixture(migrated_database)
    controls = _controls(migrated_database)
    before = _rows(migrated_database)
    captured = _captured(probe)
    command = _command(controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope), "archive-1")
    archived = controls.execute(command, scope=probe.scope, trace_id="trace-archive-1")
    assert archived.current_status is ARCHIVED
    assert archived.current_version == 2
    assert archived.receipt.changed
    assert not archived.replayed
    assert archived.receipt.approved_by == "local_operator"
    assert PRIVATE_NAME not in archived.model_dump_json()
    retained = probe.services.memories.execute(scope=probe.scope, include_archived=True)[0]
    assert retained == replace(memory, lifecycle_status=ARCHIVED, aggregate_version=2)
    assert probe.services.memories.execute(scope=probe.scope) == ()
    assert probe.services.history.execute(scope=probe.scope).sessions
    assert _rows(migrated_database)["memory_evidence"] == before["memory_evidence"]
    for name in ("conversation_messages", "episode_formation_decisions", "semantic_claims"):
        assert _rows(migrated_database)[name] == before[name]

    noop = controls.execute(
        _command(controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope), "archive-noop"),
        scope=probe.scope,
        trace_id="trace-archive-noop",
    )
    assert not noop.receipt.changed
    assert noop.receipt.reason_code == "episode_lifecycle_noop"
    assert noop.current_version == 2
    assert not noop.replayed
    reactivated = controls.execute(
        _command(controls.preview(memory.memory_id, ACTIVE, scope=probe.scope), "reactivate-1"),
        scope=probe.scope,
        trace_id="trace-reactivate-1",
    )
    assert reactivated.current_status is ACTIVE
    assert reactivated.current_version == 3
    after_inverse = _rows(migrated_database)
    replay = controls.execute(command, scope=probe.scope, trace_id="different-replay-trace")
    assert replay.replayed
    assert replay.receipt == archived.receipt
    assert replay.current_status is ACTIVE
    assert replay.current_version == 3
    assert _rows(migrated_database) == after_inverse
    assert len(after_inverse["memory_lifecycle_decisions"]) == 3
    assert len(after_inverse["audit_events"]) == len(before["audit_events"]) + 3
    with migrated_database.engine.connect() as connection:
        lifecycle_audit = connection.execute(
            text(
                "SELECT event_type, details FROM audit_events WHERE event_type IN "
                "('memory.episode_archived', 'memory.episode_reactivated', "
                "'memory.episode_lifecycle_noop')"
            )
        ).all()
    assert {row[0] for row in lifecycle_audit} == {
        "memory.episode_archived",
        "memory.episode_reactivated",
        "memory.episode_lifecycle_noop",
    }
    assert PRIVATE_NAME not in repr(lifecycle_audit)
    assert _captured(probe) == captured
    assert probe.services.memories.execute(scope=probe.scope)[0].aggregate_version == 3


@pytest.mark.parametrize("target", ["foreign", "quarantined", "unknown"])
def test_target_proof_precedes_receipt_lookup_and_every_write(
    migrated_database: Database, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    probe, memory = _fixture(migrated_database)
    controls = _controls(migrated_database)
    preview = controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope)
    command = _command(preview, "unavailable-target-request")
    controls.execute(command, scope=probe.scope, trace_id="trace-original-before-proof-denial")
    scope = probe.scope
    if target == "foreign":
        scope = MemoryScope(probe.scope.identity_id, "bob")
    elif target == "quarantined":
        with migrated_database.engine.begin() as connection:
            connection.execute(text("UPDATE conversation_sessions SET scope_verified=0"))
    else:
        command = command.model_copy(update={"memory_id": "missing-target"})

    def receipt_lookup_is_forbidden(self: object, request_id: str) -> None:
        pytest.fail("receipt was inspected before target proof")

    monkeypatch.setattr(
        SQLAlchemyEpisodicMemoryRepository,
        "get_lifecycle_decision",
        receipt_lookup_is_forbidden,
    )
    before = _rows(migrated_database)
    captured = _captured(probe)
    with pytest.raises(MemoryLifecycleError, match="memory_lifecycle_target_unavailable"):
        controls.preview(command.memory_id, ARCHIVED, scope=scope)
    with pytest.raises(MemoryLifecycleError, match="memory_lifecycle_target_unavailable"):
        controls.execute(command, scope=scope, trace_id="trace-target-denial")
    assert _rows(migrated_database) == before
    assert _captured(probe) == captured


@pytest.mark.parametrize("invalid", ["stale_version", "wrong_digest", "changed_request_payload"])
def test_stale_preview_and_changed_request_payload_cannot_mutate(
    migrated_database: Database, invalid: str
) -> None:
    probe, memory = _fixture(migrated_database)
    controls = _controls(migrated_database)
    command = _command(controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope), "binding-1")
    if invalid == "wrong_digest":
        rejected = command.model_copy(update={"preview_digest": "0" * 64})
        reason = "memory_lifecycle_preview_mismatch"
    else:
        controls.execute(command, scope=probe.scope, trace_id="trace-binding-original")
        if invalid == "stale_version":
            rejected = command.model_copy(update={"request_id": "new-stale-request"})
            reason = "memory_lifecycle_version_conflict"
        else:
            rejected = command.model_copy(update={"target_status": ACTIVE})
            reason = "memory_lifecycle_request_conflict"
    before = _rows(migrated_database)
    with pytest.raises(MemoryLifecycleError, match=reason):
        controls.execute(rejected, scope=probe.scope, trace_id="trace-binding-denial")
    assert _rows(migrated_database) == before


@pytest.mark.parametrize("failure_point", ["status_cas", "receipt", "audit", "commit"])
@pytest.mark.parametrize("noop", [False, True])
def test_lifecycle_fault_at_each_write_point_rolls_back_all_owner_state(
    migrated_database: Database, failure_point: str, noop: bool
) -> None:
    probe, memory = _fixture(migrated_database)
    controls = _controls(migrated_database)
    if noop:
        controls.execute(
            _command(
                controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope), "before-noop"
            ),
            scope=probe.scope,
            trace_id="trace-before-noop",
        )
    command = _command(controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope), "fault-1")
    before = _rows(migrated_database)
    prefixes = {
        "status_cas": "UPDATE EPISODIC_MEMORIES",
        "receipt": "INSERT INTO MEMORY_LIFECYCLE_DECISIONS",
        "audit": "INSERT INTO AUDIT_EVENTS",
    }
    observed = False

    def after_statement(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        nonlocal observed
        if statement.upper().startswith(prefixes[failure_point]):
            observed = True
            raise RuntimeError("SYNTHETIC_PRIVATE_STORAGE_FAILURE")

    def before_commit(connection: Connection) -> None:
        nonlocal observed
        observed = True
        raise RuntimeError("SYNTHETIC_PRIVATE_COMMIT_FAILURE")

    listener = before_commit if failure_point == "commit" else after_statement
    event_name = "commit" if failure_point == "commit" else "after_cursor_execute"
    event.listen(migrated_database.engine, event_name, listener)
    try:
        with pytest.raises(MemoryLifecycleError) as caught:
            controls.execute(command, scope=probe.scope, trace_id="trace-fault")
        assert caught.value.reason_code == "memory_lifecycle_commit_failed"
        assert "PRIVATE" not in str(caught.value)
    finally:
        event.remove(migrated_database.engine, event_name, listener)
    assert observed
    assert _rows(migrated_database) == before


@pytest.mark.parametrize("race", ["identical_request", "changed_payload", "lost_scope_proof"])
def test_first_writer_races_reconcile_receipts_without_partial_cas_or_unproven_replay(
    migrated_database: Database, race: str
) -> None:
    probe, memory = _fixture(migrated_database)
    controls = _controls(migrated_database, "lifecycle-losing-writer")
    winner = _controls(migrated_database, "lifecycle-winning-writer")
    command = _command(controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope), "race-1")
    winning_command = (
        _command(winner.preview(memory.memory_id, ACTIVE, scope=probe.scope), "race-1")
        if race == "changed_payload"
        else command
    )
    entered = False
    after_winner: dict[str, list[tuple[object, ...]]] | None = None

    def commit_winner_before_first_write(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        nonlocal entered, after_winner
        if entered or not statement.upper().startswith("UPDATE EPISODIC_MEMORIES"):
            return
        entered = True
        winner.execute(winning_command, scope=probe.scope, trace_id="trace-winning-writer")
        if race == "lost_scope_proof":
            with migrated_database.engine.begin() as writer:
                writer.execute(text("UPDATE conversation_sessions SET scope_verified=0"))
        after_winner = _rows(migrated_database)

    event.listen(
        migrated_database.engine, "before_cursor_execute", commit_winner_before_first_write
    )
    try:
        if race == "identical_request":
            result = controls.execute(command, scope=probe.scope, trace_id="trace-losing-writer")
            assert result.replayed
            assert result.current_status is ARCHIVED
            assert result.current_version == 2
            assert result.receipt.trace_id == "trace-winning-writer"
        else:
            reason = (
                "memory_lifecycle_request_conflict"
                if race == "changed_payload"
                else "memory_lifecycle_source_ineligible"
            )
            with pytest.raises(MemoryLifecycleError, match=reason):
                controls.execute(command, scope=probe.scope, trace_id="trace-losing-writer")
    finally:
        event.remove(
            migrated_database.engine, "before_cursor_execute", commit_winner_before_first_write
        )
    assert entered
    assert after_winner is not None
    assert _rows(migrated_database) == after_winner
    assert len(after_winner["memory_lifecycle_decisions"]) == 1
    if race == "changed_payload":
        current = probe.services.memories.execute(scope=probe.scope)[0]
        assert current.lifecycle_status is ACTIVE
        assert current.aggregate_version == 1  # Losing CAS was rolled back after request conflict.


def test_archived_episode_is_excluded_from_new_admissions_but_other_own_memory_remains_useful(
    migrated_database: Database,
) -> None:
    probe, memory = _fixture(migrated_database)
    controls = _controls(migrated_database)
    controls.execute(
        _command(controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope), "exclude-1"),
        scope=probe.scope,
        trace_id="trace-exclude",
    )
    assert probe.services.retrieve_memories is not None
    assert probe.services.index_memories is not None
    assert probe.services.process_semantic is not None
    assert probe.services.backfill_semantic is not None
    before = _rows(migrated_database)
    embedding_count = len(probe.embedding.requests)
    semantic_count = len(probe.semantic.requests) if probe.semantic is not None else 0
    query = RetrievalQuery(
        RECALL, "trace-archived-recall", INTERACTION_TIME + timedelta(days=3), None, probe.scope
    )
    recalled = asyncio.run(probe.services.retrieve_memories.execute(query))
    assert recalled.status is RetrievalStatus.NO_RELEVANT_MEMORY
    assert recalled.memory_ids == ()
    assert len(probe.embedding.requests) == embedding_count  # precheck avoids query embedding
    indexed = asyncio.run(
        probe.services.index_memories.execute(
            scope=probe.scope, trace_id="trace-archive-index", rebuild=True
        )
    )
    assert indexed.considered == 0
    assert indexed.indexed == 0
    backfill = asyncio.run(
        probe.services.backfill_semantic.execute(
            scope=probe.scope, trace_id="trace-archive-backfill", limit=10
        )
    )
    assert backfill.considered == 0
    with pytest.raises(ValueError, match="semantic_source_ineligible"):
        asyncio.run(
            probe.services.process_semantic.execute(
                memory.memory_id, scope=probe.scope, trace_id="trace-archive-formation"
            )
        )
    single = IndexEpisodicMemory(
        lambda: SQLAlchemyEpisodicMemoryIndexUnitOfWork(migrated_database.session_factory),
        probe.embedding,
        FrozenClock(INTERACTION_TIME),
    )
    with pytest.raises(ValueError, match="memory_source_ineligible"):
        asyncio.run(single.execute(memory, trace_id="trace-archived-stale-object"))
    assert len(probe.embedding.requests) == embedding_count
    assert probe.semantic is not None
    assert len(probe.semantic.requests) == semantic_count
    assert _rows(migrated_database) == before

    other = _probe(migrated_database, counterparty="alice", prefix="lifecycle-other", day=2)
    _turn(other, "Меня зовут ДругоеАктивноеИмя.", "lifecycle-other")
    assert other.services.retrieve_memories is not None
    useful = asyncio.run(other.services.retrieve_memories.execute(query))
    assert useful.status is RetrievalStatus.RETRIEVED
    assert len(useful.memory_ids) == 1
    assert memory.memory_id not in useful.memory_ids


def test_new_active_same_fact_merges_with_exact_retained_archived_support(
    migrated_database: Database,
) -> None:
    probe, memory = _fixture(migrated_database)
    original = probe.services.semantic_claims.list(scope=probe.scope)[0]
    original_inspection = probe.services.semantic_claims.inspect(
        original.claim_id, scope=probe.scope
    )
    controls = _controls(migrated_database)
    controls.execute(
        _command(controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope), "retain-1"),
        scope=probe.scope,
        trace_id="trace-retain",
    )
    assert (
        probe.services.semantic_claims.inspect(original.claim_id, scope=probe.scope)
        == original_inspection
    )
    fresh = _probe(migrated_database, counterparty="alice", prefix="lifecycle-new-proof", day=2)
    _turn(fresh, f"Меня зовут {PRIVATE_NAME}.", "lifecycle-new-proof")
    merged = fresh.services.semantic_claims.list(scope=fresh.scope)
    assert len(merged) == 1
    assert merged[0].claim_id == original.claim_id
    assert merged[0].aggregate_version == original.aggregate_version + 1
    assert merged[0].confidence > original.confidence
    assert set(original.evidence).issubset(merged[0].evidence)
    assert len(merged[0].evidence) == len(original.evidence) + 1
    assert fresh.semantic is not None
    assert fresh.semantic.requests
    assert memory.memory_id not in repr(fresh.semantic.requests)
    assert memory.source_interaction_id not in repr(fresh.semantic.requests)
    active_memory = fresh.services.memories.execute(scope=fresh.scope)[0]
    assert any(item.memory_id == active_memory.memory_id for item in merged[0].evidence)


def test_restart_and_current_recovery_preserve_archive_history_and_allow_explicit_reactivation(
    migrated_database: Database, project_root: Path, tmp_path: Path
) -> None:
    probe, memory = _fixture(migrated_database)
    controls = _controls(migrated_database)
    command = _command(
        controls.preview(memory.memory_id, ARCHIVED, scope=probe.scope), "recovery-archive"
    )
    archived = controls.execute(command, scope=probe.scope, trace_id="trace-recovery-archive")
    url = str(migrated_database.engine.url)
    baseline = _rows(migrated_database)
    migrated_database.dispose()
    restarted = create_database(url)
    try:
        restarted_controls = _controls(restarted, "lifecycle-restart")
        replay = restarted_controls.execute(
            command, scope=probe.scope, trace_id="trace-restart-replay"
        )
        assert replay.replayed
        assert replay.receipt == archived.receipt
        assert replay.current_status is ARCHIVED
        assert replay.current_version == 2
        assert _rows(restarted) == baseline
        archive = tmp_path / "private-lifecycle" / "state.satori"
        recovery_services = recovery(restarted, project_root)
        exported = recovery_services.export(archive)
        assert recovery_services.preview(archive) == exported
        destination = tmp_path / "restored-lifecycle" / "state.db"
        recovery_services.restore(archive, destination, expected_sha256=exported.archive_sha256)
        assert stored_rows(destination) == baseline
        restored = create_database(f"sqlite+pysqlite:///{destination}")
        try:
            restored_controls = _controls(restored, "lifecycle-restored")
            restored_probe = _probe(restored, counterparty="alice", prefix="restored-probe", day=4)
            assert restored_probe.services.memories.execute(scope=probe.scope) == ()
            assert (
                restored_controls.execute(
                    command, scope=probe.scope, trace_id="trace-restored-replay"
                ).receipt
                == archived.receipt
            )
            assert _rows(restored) == baseline
            vector_rows = baseline["episodic_memory_embeddings"]
            restored_controls.execute(
                _command(
                    restored_controls.preview(memory.memory_id, ACTIVE, scope=probe.scope),
                    "recovery-reactivate",
                ),
                scope=probe.scope,
                trace_id="trace-recovery-reactivate",
            )
            assert _rows(restored)["episodic_memory_embeddings"] == vector_rows
            assert restored_probe.services.retrieve_memories is not None
            result = asyncio.run(
                restored_probe.services.retrieve_memories.execute(
                    RetrievalQuery(
                        RECALL,
                        "trace-restored-recall",
                        INTERACTION_TIME + timedelta(days=5),
                        None,
                        probe.scope,
                    )
                )
            )
            assert result.memory_ids == (memory.memory_id,)
            foreign = MemoryScope(probe.scope.identity_id, "bob")
            assert (
                restored_probe.services.memories.execute(scope=foreign, include_archived=True) == ()
            )
            with pytest.raises(MemoryLifecycleError, match="memory_lifecycle_target_unavailable"):
                restored_controls.preview(memory.memory_id, ACTIVE, scope=foreign)
        finally:
            restored.dispose()
    finally:
        restarted.dispose()
