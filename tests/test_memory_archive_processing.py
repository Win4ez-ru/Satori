"""Late provider commits, exact retained support and lifecycle-aware postresponse replay."""

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import event, text

from satori.application.conversation.history import InteractionLog
from satori.application.memory.lifecycle_contracts import MemoryLifecycleCommand
from satori.application.memory.lifecycle_use_cases import MemoryLifecycleControls
from satori.application.retrieval.use_cases import IndexEpisodicMemory
from satori.core.embedding import EmbeddingRequest, EmbeddingResponse
from satori.core.semantic import SemanticFormationProviderResponse, SemanticFormationRequest
from satori.domain.conversation_history import ConversationSession
from satori.domain.memory import EpisodicMemory, MemoryLifecycleStatus, MemoryManager
from satori.domain.memory_scope import MemoryScope
from satori.domain.semantic_memory import SemanticFormationDecision, SemanticFormationPlan
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.memory_uow import SQLAlchemyEpisodicMemoryUnitOfWork
from satori.infrastructure.persistence.repositories.memory import SQLAlchemyEpisodicMemoryRepository
from satori.infrastructure.persistence.repositories.semantic import (
    SQLAlchemySemanticMemoryRepository,
)
from satori.infrastructure.persistence.retrieval_uow import SQLAlchemyEpisodicMemoryIndexUnitOfWork
from tests.fakes import FakeEmbeddingProvider, FrozenClock
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate, id_sequence
from tests.test_stage6_semantic_memory import (
    MEMORY_SCOPE,
    one_claim,
    semantic_provider,
    services,
    talk,
)


def _transition(database: Database, memory_id: str, *, archived: bool, prefix: str) -> None:
    controls = MemoryLifecycleControls(
        unit_of_work_factory=lambda: SQLAlchemyEpisodicMemoryUnitOfWork(database.session_factory),
        manager=MemoryManager(),
        clock=FrozenClock(INTERACTION_TIME),
        id_generator=id_sequence(prefix),
    )
    status = MemoryLifecycleStatus.ARCHIVED if archived else MemoryLifecycleStatus.ACTIVE
    preview = controls.preview(memory_id, status, scope=MEMORY_SCOPE)
    controls.execute(
        MemoryLifecycleCommand(
            memory_id=memory_id,
            target_status=status,
            expected_version=preview.current_version,
            request_id=prefix,
            preview_digest=preview.preview_digest,
        ),
        scope=MEMORY_SCOPE,
        trace_id=prefix,
    )


def _assert_no_semantic_rows(database: Database) -> None:
    with database.engine.connect() as connection:
        for table in (
            "semantic_claims",
            "semantic_claim_evidence",
            "semantic_claim_revisions",
            "semantic_formation_decisions",
        ):
            assert connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one() == 0


class _ChangingEmbedding(FakeEmbeddingProvider):
    def __init__(self, database: Database, memory: EpisodicMemory, *, aba: bool) -> None:
        super().__init__({memory.summary: (1.0, 0.0, 0.0)})
        self.database = database
        self.memory_id = memory.memory_id
        self.aba = aba

    async def embed(self, request: EmbeddingRequest, /) -> EmbeddingResponse:
        response = await super().embed(request)
        _transition(self.database, self.memory_id, archived=True, prefix="embedding-archive")
        if self.aba:
            _transition(
                self.database, self.memory_id, archived=False, prefix="embedding-reactivate"
            )
        return response


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("aba", [False, True])
def test_indexing_rejects_archive_and_archive_reactivate_during_provider(
    migrated_database: Database, batch: bool, aba: bool
) -> None:
    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="index-original")
    talk(upstream, "Меня зовут Алексей.", "index-original")
    memory = upstream.memories.execute(scope=MEMORY_SCOPE)[0]
    embedding = _ChangingEmbedding(migrated_database, memory, aba=aba)
    if batch:
        index = services(
            migrated_database, semantic=None, prefix="index-batch", embedding=embedding
        ).index_memories
        assert index is not None
        report = asyncio.run(index.execute(scope=MEMORY_SCOPE, trace_id="index-batch"))
        assert report.indexed == 0
        assert report.failed == 1
    else:
        single = IndexEpisodicMemory(
            unit_of_work_factory=lambda: SQLAlchemyEpisodicMemoryIndexUnitOfWork(
                migrated_database.session_factory
            ),
            provider=embedding,
            clock=FrozenClock(INTERACTION_TIME),
        )
        with pytest.raises(ValueError, match=r"^memory_source_changed$"):
            asyncio.run(single.execute(memory, trace_id="index-single"))
    with migrated_database.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM episodic_memory_embeddings"
            ).scalar_one()
            == 0
        )
    changed = upstream.memories.execute(scope=MEMORY_SCOPE, include_archived=True)[0]
    assert changed.aggregate_version == (3 if aba else 2)


@pytest.mark.parametrize("aba", [False, True])
def test_semantic_formation_rejects_archive_reactivate_source_snapshot(
    migrated_database: Database, aba: bool
) -> None:
    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="semantic-original")
    talk(upstream, "Меня зовут Алексей.", "semantic-original")
    memory = upstream.memories.execute(scope=MEMORY_SCOPE)[0]

    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    original = provider.response_factory
    assert original is not None

    def change_source(request: SemanticFormationRequest) -> SemanticFormationProviderResponse:
        _transition(migrated_database, memory.memory_id, archived=True, prefix="semantic-archive")
        if aba:
            _transition(
                migrated_database, memory.memory_id, archived=False, prefix="semantic-reactivate"
            )
        return original(request)

    provider.response_factory = change_source
    processor = services(
        migrated_database, semantic=provider, prefix="semantic-race"
    ).process_semantic
    assert processor is not None
    with pytest.raises(ValueError, match=r"^semantic_sources?_changed$"):
        asyncio.run(
            processor.execute(memory.memory_id, scope=MEMORY_SCOPE, trace_id="semantic-race")
        )
    _assert_no_semantic_rows(migrated_database)


@pytest.mark.parametrize("stage", ["index", "semantic"])
@pytest.mark.parametrize("tamper", ["archive", "quarantine"])
def test_first_writer_rechecks_source_when_eligibility_changes_after_application_read(
    migrated_database: Database, stage: str, tamper: str
) -> None:
    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="late-source")
    talk(upstream, "Меня зовут Алексей.", "late-source")
    memory = upstream.memories.execute(scope=MEMORY_SCOPE)[0]
    triggered = False
    prefix = (
        "INSERT INTO episodic_memory_embeddings"
        if stage == "index"
        else "INSERT INTO semantic_formation_decisions"
    )

    def before_write(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal triggered
        if triggered or not statement.startswith(prefix):
            return
        triggered = True
        if tamper == "archive":
            _transition(migrated_database, memory.memory_id, archived=True, prefix="late-archive")
        else:
            with migrated_database.engine.begin() as connection:
                connection.execute(text("UPDATE conversation_sessions SET scope_verified=0"))

    event.listen(migrated_database.engine, "before_cursor_execute", before_write)
    try:
        if stage == "index":
            index = IndexEpisodicMemory(
                unit_of_work_factory=lambda: SQLAlchemyEpisodicMemoryIndexUnitOfWork(
                    migrated_database.session_factory
                ),
                provider=FakeEmbeddingProvider({memory.summary: (1.0, 0.0, 0.0)}),
                clock=FrozenClock(INTERACTION_TIME),
            )
            with pytest.raises(ValueError, match=r"^memory_source_changed$"):
                asyncio.run(index.execute(memory, trace_id="late-index"))
        else:
            provider = semantic_provider(
                lambda request: one_claim(request, predicate="name", value="Алексей")
            )
            processor = services(
                migrated_database, semantic=provider, prefix="late-semantic"
            ).process_semantic
            assert processor is not None
            with pytest.raises(ValueError, match=r"^semantic_sources_changed$"):
                asyncio.run(
                    processor.execute(
                        memory.memory_id, scope=MEMORY_SCOPE, trace_id="late-semantic"
                    )
                )
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", before_write)
    assert triggered
    _assert_no_semantic_rows(migrated_database)
    with migrated_database.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM episodic_memory_embeddings"
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("archived", [False, True])
def test_semantic_writer_rejects_unselected_or_newly_archived_nonowned_evidence(
    migrated_database: Database, monkeypatch: pytest.MonkeyPatch, archived: bool
) -> None:
    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="outside-window")
    talk(upstream, "Меня зовут Алексей.", "outside-window")
    outside = upstream.memories.execute(scope=MEMORY_SCOPE)[0]
    if archived:
        _transition(migrated_database, outside.memory_id, archived=True, prefix="outside-archive")
        outside = upstream.memories.execute(scope=MEMORY_SCOPE, include_archived=True)[0]
    fresh = services(migrated_database, semantic=None, prefix="selected-window", day=1)
    talk(fresh, "Меня зовут Алексей.", "selected-window")
    memory = next(
        item
        for item in fresh.memories.execute(scope=MEMORY_SCOPE)
        if item.memory_id != outside.memory_id
    )
    original = SQLAlchemySemanticMemoryRepository.record_decision

    def malformed(
        repository: SQLAlchemySemanticMemoryRepository,
        decision: SemanticFormationDecision,
        plan: SemanticFormationPlan,
        *,
        scope: MemoryScope,
        source_memories: tuple[EpisodicMemory, ...],
        audit_event_id: str,
        input_id: str | None = None,
    ) -> bool:
        claim = plan.claims[0]
        root = outside.evidence[0]
        injected = replace(
            claim.evidence[0],
            semantic_evidence_id="malformed-extra-semantic-edge",
            memory_id=outside.memory_id,
            memory_evidence_id=root.evidence_id,
            root_message_id=root.source_message_id,
            root_interaction_id=outside.source_interaction_id,
            observed_at=outside.occurred_at,
        )
        return original(
            repository,
            decision,
            replace(plan, claims=(replace(claim, evidence=(*claim.evidence, injected)),)),
            scope=scope,
            source_memories=(*source_memories, outside) if archived else source_memories,
            audit_event_id=audit_event_id,
            input_id=input_id,
        )

    monkeypatch.setattr(SQLAlchemySemanticMemoryRepository, "record_decision", malformed)
    processor = services(
        migrated_database,
        semantic=semantic_provider(
            lambda request: one_claim(request, predicate="name", value="Алексей")
        ),
        prefix="malformed-plan",
        day=1,
    ).process_semantic
    assert processor is not None
    processor.max_source_memories = 1
    reason = "semantic_evidence_scope_invalid" if archived else "semantic_evidence_not_selected"
    with pytest.raises(ValueError, match=f"^{reason}$"):
        asyncio.run(
            processor.execute(memory.memory_id, scope=MEMORY_SCOPE, trace_id="malformed-plan")
        )
    _assert_no_semantic_rows(migrated_database)


def test_postresponse_archived_replay_skips_derived_providers_without_degradation(
    migrated_database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    activate(migrated_database)
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    summary = "Меня зовут Алексей."
    embedding = FakeEmbeddingProvider({summary: (1.0, 0.0, 0.0)})
    active = services(
        migrated_database, semantic=provider, prefix="replay-original", embedding=embedding
    )
    talk(active, summary, "replay-original")
    memory = active.memories.execute(scope=MEMORY_SCOPE)[0]
    counts = len(embedding.requests), len(provider.requests)
    _transition(migrated_database, memory.memory_id, archived=True, prefix="replay-archive")
    with caplog.at_level("INFO", logger="satori.post_response"):
        report = asyncio.run(
            active.post_response.execute(memory.source_interaction_id, trace_id="replay")
        )
    assert report.succeeded
    assert (len(embedding.requests), len(provider.requests)) == counts
    assert any(
        record.message == "post_response_memory_archived_skipped" for record in caplog.records
    )
    assert not any(
        record.message == "post_response_processing_degraded" for record in caplog.records
    )


@pytest.mark.parametrize("aba", [False, True])
def test_postresponse_late_archive_is_intentional_but_aba_remains_a_failed_commit(
    migrated_database: Database, aba: bool
) -> None:
    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="post-late")
    talk(upstream, "Меня зовут Алексей.", "post-late")
    memory = upstream.memories.execute(scope=MEMORY_SCOPE)[0]
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    active = services(
        migrated_database,
        semantic=provider,
        prefix="post-processor",
        embedding=_ChangingEmbedding(migrated_database, memory, aba=aba),
    )
    report = asyncio.run(
        active.post_response.execute(memory.source_interaction_id, trace_id="post-late")
    )
    assert ("episode_embedding" in report.failure_phases) is aba
    if not aba:
        assert report.succeeded
        assert provider.requests == []
        _assert_no_semantic_rows(migrated_database)


@pytest.mark.parametrize("lookup", ["session", "memory"])
@pytest.mark.parametrize("archived", [False, True])
def test_postresponse_inspection_fault_returns_report_and_preserves_canonical_admission(
    migrated_database: Database,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    lookup: str,
    archived: bool,
) -> None:
    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="inspection-original")
    talk(upstream, "Меня зовут Алексей.", "inspection-original")
    memory = upstream.memories.execute(scope=MEMORY_SCOPE)[0]
    if archived:
        _transition(migrated_database, memory.memory_id, archived=True, prefix="inspection-archive")
    original = upstream.post_response.interaction_log.get(memory.source_interaction_id)
    assert original is not None
    assert original.assistant_message is not None
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    embedding = FakeEmbeddingProvider({memory.summary: (1.0, 0.0, 0.0)})
    active = services(
        migrated_database,
        semantic=provider,
        prefix="inspection-processor",
        embedding=embedding,
    )
    private_detail = "private-lifecycle-lookup-detail"

    def fail_session(_log: InteractionLog, _session_id: str) -> ConversationSession | None:
        raise RuntimeError(private_detail)

    def fail_memory(
        _repository: SQLAlchemyEpisodicMemoryRepository,
        _memory_id: str,
        *,
        scope: MemoryScope,
        include_archived: bool = False,
    ) -> EpisodicMemory | None:
        raise RuntimeError(private_detail)

    if lookup == "session":
        monkeypatch.setattr(InteractionLog, "get_session", fail_session)
    else:
        monkeypatch.setattr(SQLAlchemyEpisodicMemoryRepository, "get_memory", fail_memory)
    with caplog.at_level("INFO", logger="satori.post_response"):
        report = asyncio.run(
            active.post_response.execute(memory.source_interaction_id, trace_id="inspection-fault")
        )
    assert report.failure_phases.count("memory_lifecycle_inspection") == 1
    assert upstream.post_response.interaction_log.get(memory.source_interaction_id) == original
    assert len(embedding.requests) == (0 if archived else 1)
    assert len(provider.requests) == (0 if archived else 1)
    inspection_logs = [
        record
        for record in caplog.records
        if getattr(record, "satori_fields", {}).get("phase") == "memory_lifecycle_inspection"
    ]
    assert len(inspection_logs) == 1
    assert getattr(inspection_logs[0], "satori_fields", {})["error_type"] == "RuntimeError"
    assert private_detail not in caplog.text
    assert all(private_detail not in repr(record.__dict__) for record in caplog.records)
    if archived:
        _assert_no_semantic_rows(migrated_database)
