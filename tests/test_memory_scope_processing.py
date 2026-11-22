"""Provider intake and commit-time eligibility checks for derived memory processing."""

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import text

from satori.application.retrieval.contracts import RetrievalQuery, RetrievalStatus
from satori.application.retrieval.use_cases import IndexEpisodicMemory
from satori.core.embedding import EmbeddingRequest, EmbeddingResponse
from satori.core.semantic import SemanticFormationProposal, SemanticFormationRequest
from satori.domain.memory_scope import MemoryScope
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.retrieval_uow import SQLAlchemyEpisodicMemoryIndexUnitOfWork
from tests.fakes import FakeEmbeddingProvider, FrozenClock, SequenceIdGenerator
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate
from tests.test_stage6_semantic_memory import (
    MEMORY_SCOPE,
    one_claim,
    semantic_provider,
    services,
    talk,
)


def _quarantine(database: Database) -> None:
    with database.engine.begin() as connection:
        connection.execute(text("UPDATE conversation_sessions SET scope_verified=0"))


class QuarantiningEmbeddingProvider(FakeEmbeddingProvider):
    """Change eligibility while the provider runs outside a database transaction."""

    def __init__(self, database: Database, summary: str) -> None:
        super().__init__({summary: (1.0, 0.0, 0.0)})
        self._database = database

    async def embed(self, request: EmbeddingRequest, /) -> EmbeddingResponse:
        response = await super().embed(request)
        _quarantine(self._database)
        return response


def test_single_index_uses_canonical_summary_and_refuses_quarantine_before_provider(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="canonical-index")
    talk(upstream, "Меня зовут Алексей.", "canonical-index")
    memory = upstream.memories.execute(scope=MEMORY_SCOPE)[0]
    embedding = FakeEmbeddingProvider({memory.summary: (1.0, 0.0, 0.0)})
    index = IndexEpisodicMemory(
        unit_of_work_factory=lambda: SQLAlchemyEpisodicMemoryIndexUnitOfWork(
            migrated_database.session_factory
        ),
        provider=embedding,
        clock=FrozenClock(INTERACTION_TIME),
    )

    asyncio.run(index.execute(replace(memory, summary="forged private summary"), trace_id="reload"))

    assert embedding.requests[0].texts == (memory.summary,)
    _quarantine(migrated_database)
    with pytest.raises(ValueError, match=r"^memory_source_ineligible$"):
        asyncio.run(index.execute(memory, trace_id="denied"))
    assert len(embedding.requests) == 1


@pytest.mark.parametrize("batch", [False, True])
def test_indexing_revalidates_source_after_provider_without_writing_vector(
    batch: bool, migrated_database: Database
) -> None:
    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="index-race")
    talk(upstream, "Меня зовут Алексей.", "index-race")
    memory = upstream.memories.execute(scope=MEMORY_SCOPE)[0]
    embedding = QuarantiningEmbeddingProvider(migrated_database, memory.summary)
    active = services(migrated_database, semantic=None, prefix="index-run", embedding=embedding)
    if batch:
        assert active.index_memories is not None
        report = asyncio.run(
            active.index_memories.execute(scope=MEMORY_SCOPE, trace_id="index-race")
        )
        assert (report.considered, report.indexed, report.failed) == (1, 0, 1)
    else:
        index = IndexEpisodicMemory(
            unit_of_work_factory=lambda: SQLAlchemyEpisodicMemoryIndexUnitOfWork(
                migrated_database.session_factory
            ),
            provider=embedding,
            clock=FrozenClock(INTERACTION_TIME),
        )
        with pytest.raises(ValueError, match=r"^memory_source_changed$"):
            asyncio.run(index.execute(memory, trace_id="index-race"))
    assert len(embedding.requests) == 1
    with migrated_database.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM episodic_memory_embeddings")).scalar_one()
            == 0
        )


def test_quarantined_semantic_source_is_never_disclosed_or_backfilled(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="semantic-denied")
    talk(upstream, "Меня зовут Алексей.", "semantic-denied")
    memory = upstream.memories.execute(scope=MEMORY_SCOPE)[0]
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    active = services(migrated_database, semantic=provider, prefix="denied-processor")
    _quarantine(migrated_database)
    assert active.process_semantic is not None
    with pytest.raises(ValueError, match=r"^semantic_source_ineligible$"):
        asyncio.run(active.process_semantic.execute(memory.memory_id, trace_id="denied"))
    assert active.backfill_semantic is not None
    report = asyncio.run(
        active.backfill_semantic.execute(scope=MEMORY_SCOPE, trace_id="backfill-denied", limit=10)
    )
    assert report.considered == 0
    assert provider.requests == []
    assert active.semantic_claims.list(scope=MEMORY_SCOPE) == ()


def test_semantic_source_change_during_provider_cannot_commit_claim_or_decision(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="semantic-race")
    talk(upstream, "Меня зовут Алексей.", "semantic-race")
    memory = upstream.memories.execute(scope=MEMORY_SCOPE)[0]

    def respond(request: SemanticFormationRequest) -> SemanticFormationProposal:
        _quarantine(migrated_database)
        return one_claim(request, predicate="name", value="Алексей")

    provider = semantic_provider(respond)
    active = services(migrated_database, semantic=provider, prefix="race-processor")
    assert active.process_semantic is not None
    with pytest.raises(ValueError, match=r"^semantic_source_changed$"):
        asyncio.run(active.process_semantic.execute(memory.memory_id, trace_id="race"))
    assert len(provider.requests) == 1
    with migrated_database.engine.connect() as connection:
        for table in ("semantic_claims", "semantic_formation_decisions", "semantic_claim_evidence"):
            assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_namespace_mismatch_is_quarantined_before_semantic_recall_or_inspection(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    active = services(migrated_database, semantic=provider, prefix="namespace")
    talk(active, "Меня зовут Алексей.", "namespace")
    claim = active.semantic_claims.list(scope=MEMORY_SCOPE)[0]
    memory = active.memories.execute(scope=MEMORY_SCOPE)[0]
    with migrated_database.engine.begin() as connection:
        connection.execute(text("UPDATE semantic_claims SET claim_key=:key"), {"key": "a" * 64})
    assert active.semantic_claims.list(scope=MEMORY_SCOPE) == ()
    assert active.semantic_claims.inspect(claim.claim_id, scope=MEMORY_SCOPE) is None
    assert active.talk.retrieve_semantic is not None
    recalled = active.talk.retrieve_semantic.execute((memory.memory_id,), scope=MEMORY_SCOPE)
    assert recalled.claims == ()
    with migrated_database.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM semantic_claims")).scalar_one() == 1


def test_wrong_scope_precheck_skips_query_embedding(migrated_database: Database) -> None:
    activate(migrated_database)
    embedding = FakeEmbeddingProvider({"Меня зовут Алексей.": (1.0, 0.0, 0.0)})
    active = services(
        migrated_database, semantic=None, prefix="scope-precheck", embedding=embedding
    )
    talk(active, "Меня зовут Алексей.", "scope-precheck")
    before = len(embedding.requests)
    assert active.retrieve_memories is not None
    recalled = asyncio.run(
        active.retrieve_memories.execute(
            RetrievalQuery(
                "unavailable private query",
                "wrong-scope",
                INTERACTION_TIME,
                None,
                MemoryScope(MEMORY_SCOPE.identity_id, "other-counterparty"),
            )
        )
    )
    assert recalled.status is RetrievalStatus.NO_RELEVANT_MEMORY
    assert recalled.candidate_count == 0
    assert len(embedding.requests) == before


def test_explicit_semantic_source_scope_is_checked_before_replay_or_provider(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    active = services(migrated_database, semantic=provider, prefix="explicit-scope")
    talk(active, "Меня зовут Алексей.", "explicit-scope")
    memory = active.memories.execute(scope=MEMORY_SCOPE)[0]
    before = len(provider.requests)
    assert active.process_semantic is not None
    with pytest.raises(ValueError, match=r"^semantic_source_scope_mismatch$"):
        asyncio.run(
            active.process_semantic.execute(
                memory.memory_id,
                trace_id="foreign-replay",
                scope=MemoryScope(MEMORY_SCOPE.identity_id, "other-counterparty"),
            )
        )
    assert len(provider.requests) == before


def test_generated_claim_id_collision_cannot_mutate_foreign_aggregate(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    alice = services(
        migrated_database,
        semantic=semantic_provider(
            lambda request: one_claim(request, predicate="name", value="Алексей")
        ),
        prefix="collision-a",
    )
    talk(alice, "Меня зовут Алексей.", "collision-a")
    claim = alice.semantic_claims.list(scope=MEMORY_SCOPE)[0]
    before = alice.semantic_claims.inspect(claim.claim_id, scope=MEMORY_SCOPE)
    bob_scope = MemoryScope(MEMORY_SCOPE.identity_id, "bob")
    bob = services(migrated_database, semantic=None, prefix="collision-b", counterparty_id="bob")
    talk(bob, "Меня зовут Борис.", "collision-b")
    memory = bob.memories.execute(scope=bob_scope)[0]
    processor = services(
        migrated_database,
        semantic=semantic_provider(
            lambda request: one_claim(request, predicate="name", value="Борис")
        ),
        prefix="collision-processor",
        counterparty_id="bob",
    ).process_semantic
    assert processor is not None
    processor.id_generator = SequenceIdGenerator(
        "collision-decision",
        "collision-evidence",
        claim.claim_id,
        "collision-revision",
        "collision-audit",
    )
    with pytest.raises(ValueError, match=r"^semantic_target_scope_invalid$"):
        asyncio.run(processor.execute(memory.memory_id, scope=bob_scope, trace_id="collision"))
    assert alice.semantic_claims.inspect(claim.claim_id, scope=MEMORY_SCOPE) == before
    assert bob.semantic_claims.list(scope=bob_scope) == ()
    with migrated_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM semantic_formation_decisions")
            ).scalar_one()
            == 1
        )


def test_invalid_correction_reference_cannot_bypass_policy_via_exact_merge(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    first = services(
        migrated_database,
        semantic=semantic_provider(
            lambda request: one_claim(request, predicate="name", value="Алексей")
        ),
        prefix="correction-original",
    )
    talk(first, "Меня зовут Алексей.", "correction-original")
    original = first.semantic_claims.list(scope=MEMORY_SCOPE)[0]
    second = services(
        migrated_database,
        semantic=semantic_provider(
            lambda request: one_claim(
                request, predicate="name", value="Алексей", corrects_claim_id="foreign-reference"
            )
        ),
        prefix="correction-repeated",
        day=1,
    )
    talk(second, "Меня по-прежнему зовут Алексей.", "correction-repeated")
    assert second.semantic_claims.list(scope=MEMORY_SCOPE) == (original,)
    with migrated_database.engine.connect() as connection:
        assert set(
            connection.execute(text("SELECT kind FROM semantic_formation_decisions")).scalars()
        ) == {"applied", "rejected"}
