"""Source-floor adapter and owner repair regressions with a controlled synthetic DAG.

The canonical fixtures use fake providers. This module installs explicit dependency receipts
so that repository filtering, actual episode extraction inputs, and owner retirement can be
checked independently of the installation ledger/barrier tests. It is not Character evidence.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from satori.application.conversation.contracts import TalkInput
from satori.application.memory.lifecycle_contracts import (
    MemoryLifecycleCommand,
    MemoryLifecycleError,
)
from satori.application.memory.lifecycle_use_cases import MemoryLifecycleControls
from satori.application.memory.use_cases import FormEpisodeForInteraction
from satori.application.retrieval.contracts import RetrievalQuery, RetrievalStatus
from satori.application.retrieval.use_cases import IndexEpisodicMemory
from satori.application.semantic.use_cases import FormSemanticMemory
from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.core.embedding import EmbeddingRequest, EmbeddingResponse
from satori.core.semantic import SemanticFormationProviderResponse, SemanticFormationRequest
from satori.domain.memory import EpisodicMemory, MemoryLifecycleStatus, MemoryManager
from satori.domain.semantic_memory import SemanticClaimStatus, SemanticMemoryManager
from satori.domain.source_policy import PayloadRef, SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.memory_uow import SQLAlchemyEpisodicMemoryUnitOfWork
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
)
from satori.infrastructure.persistence.models.memory import (
    EpisodeFormationDecisionRow,
    EpisodicMemoryRow,
)
from satori.infrastructure.persistence.models.retrieval import EpisodicMemoryEmbeddingRow
from satori.infrastructure.persistence.models.semantic import (
    SemanticClaimEvidenceRow,
    SemanticClaimRow,
    SemanticFormationDecisionRow,
)
from satori.infrastructure.persistence.models.source_policy import (
    OwnerSourceWithdrawalRow,
    SourcePayloadRow,
)
from satori.infrastructure.persistence.repositories.memory import SQLAlchemyEpisodicMemoryRepository
from satori.infrastructure.persistence.repositories.retrieval import (
    SQLAlchemyEpisodicMemoryIndexRepository,
)
from satori.infrastructure.persistence.repositories.semantic import (
    SQLAlchemySemanticMemoryRepository,
)
from satori.infrastructure.persistence.retrieval_uow import SQLAlchemyEpisodicMemoryIndexUnitOfWork
from satori.infrastructure.persistence.semantic_uow import SQLAlchemySemanticMemoryUnitOfWork
from satori.infrastructure.persistence.source_policy import (
    SessionSourceAdmission,
    mark_owner_reconciled,
    materialize_mirror,
    require_owner_reconciled,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.fakes import FakeEmbeddingProvider, FakeSemanticFormationProvider, FrozenClock
from tests.test_memory_scope_integration import ScopeProbe, _probe, _turn
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate, id_sequence
from tests.test_stage6_semantic_memory import episode_from_user, one_claim, semantic_provider


def _snapshot(identity: str, roots: frozenset[str] = frozenset()) -> SourceEligibilitySnapshot:
    return SourceEligibilitySnapshot(
        "memory-floor", "memory-installation", "memory-lineage", identity, int(bool(roots)), roots
    )


def _ref(ref: PayloadRef) -> dict[str, object]:
    return {"kind": ref.kind, "payload_id": ref.payload_id, "version": ref.version}


@dataclass(slots=True)
class FakeSourceRuntime:
    """Controlled projection admission only; ledger/locking are tested separately.

    A real operational factory always carries an installation SourcePolicyRuntime.
    This explicit test port supplies a mutable snapshot to exercise adapter/owner
    guards without claiming independent-floor or cross-process lock acceptance.
    """

    snapshot: SourceEligibilitySnapshot

    @contextmanager
    def shared(self) -> Iterator[SourceEligibilitySnapshot]:
        yield self.snapshot


def controlled_session_factory(
    database: Database, runtime: FakeSourceRuntime
) -> sessionmaker[Session]:
    return sessionmaker(
        bind=database.engine,
        autoflush=False,
        expire_on_commit=False,
        info={"source_runtime": cast(SourcePolicyRuntime, runtime)},
    )


def bind_controlled_runtime(
    database: Database, snapshot: SourceEligibilitySnapshot
) -> FakeSourceRuntime:
    runtime = FakeSourceRuntime(snapshot)
    database.session_factory.configure(info={"source_runtime": cast(SourcePolicyRuntime, runtime)})
    return runtime


def _receipt(
    session: Session,
    snapshot: SourceEligibilitySnapshot,
    ref: PayloadRef,
    *,
    roots: tuple[str, ...] = (),
    refs: tuple[PayloadRef, ...] = (),
    unknown: bool = False,
) -> None:
    session.add(
        SourcePayloadRow(
            kind=ref.kind,
            payload_id=ref.payload_id,
            payload_version=ref.version,
            identity_id=snapshot.identity_id,
            policy_generation=snapshot.generation,
            proof_status="unknown" if unknown else "complete",
            legacy=unknown,
            root_message_ids=list(roots),
            payload_refs=[_ref(item) for item in refs],
            eligible=True,
        )
    )


def _seed_graph(database: Database, snapshot: SourceEligibilitySnapshot) -> None:
    """Controlled receipt graph: reply→current root; episode→reply; claim→episodes."""
    with database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        for interaction_id, message_id in session.execute(
            select(ConversationInteractionRow.interaction_id, ConversationMessageRow.message_id)
            .join(
                ConversationMessageRow,
                ConversationMessageRow.interaction_id == ConversationInteractionRow.interaction_id,
            )
            .where(ConversationMessageRow.role == "user")
        ):
            _receipt(
                session,
                snapshot,
                PayloadRef("assistant_reply", interaction_id),
                roots=(message_id,),
            )
        for memory in session.scalars(select(EpisodicMemoryRow)):
            _receipt(
                session,
                snapshot,
                PayloadRef("episode", memory.memory_id, memory.aggregate_version),
                refs=(PayloadRef("assistant_reply", memory.source_interaction_id),),
            )
        for claim in session.scalars(select(SemanticClaimRow)):
            memory_ids = tuple(
                session.scalars(
                    select(SemanticClaimEvidenceRow.memory_id).where(
                        SemanticClaimEvidenceRow.claim_id == claim.claim_id
                    )
                )
            )
            _receipt(
                session,
                snapshot,
                PayloadRef("semantic_claim", claim.claim_id, claim.aggregate_version),
                refs=tuple(
                    PayloadRef("episode", memory_id) for memory_id in dict.fromkeys(memory_ids)
                ),
            )
        session.flush()
        materialize_mirror(session, snapshot)
        # This deliberately clean synthetic fixture has no legacy owner repair backlog.
        # The real installation path marks owners only after their individual repair commits.
        for owner in ("semantic_memory", "current_models", "positions"):
            mark_owner_reconciled(session, owner)
        session.commit()
    bind_controlled_runtime(database, snapshot)


def _exclude(
    database: Database, snapshot: SourceEligibilitySnapshot, root: str
) -> SourceEligibilitySnapshot:
    changed = _snapshot(snapshot.identity_id, frozenset({root}))
    with database.session_factory() as session:
        session.info["source_snapshot"] = changed
        materialize_mirror(session, changed)
        session.commit()
    bind_controlled_runtime(database, changed)
    return changed


def _session(database: Database, snapshot: SourceEligibilitySnapshot) -> Session:
    session = database.session_factory()
    session.info["source_snapshot"] = snapshot
    return session


class _MemoryUnit(SQLAlchemyEpisodicMemoryUnitOfWork):
    def __init__(self, database: Database, snapshot: SourceEligibilitySnapshot) -> None:
        super().__init__(controlled_session_factory(database, FakeSourceRuntime(snapshot)))
        self.snapshot = snapshot

    def __enter__(self) -> _MemoryUnit:
        super().__enter__()
        self.session.info["source_snapshot"] = self.snapshot
        return self

    @property
    def source_admission(self) -> SessionSourceAdmission:
        return SessionSourceAdmission(self.session)


class _IndexUnit(SQLAlchemyEpisodicMemoryIndexUnitOfWork):
    def __init__(self, database: Database, snapshot: SourceEligibilitySnapshot) -> None:
        super().__init__(controlled_session_factory(database, FakeSourceRuntime(snapshot)))
        self.snapshot = snapshot

    def __enter__(self) -> _IndexUnit:
        super().__enter__()
        self.session.info["source_snapshot"] = self.snapshot
        return self

    @property
    def source_admission(self) -> SessionSourceAdmission:
        return SessionSourceAdmission(self.session)


class _SemanticUnit(SQLAlchemySemanticMemoryUnitOfWork):
    def __init__(self, database: Database, snapshot: SourceEligibilitySnapshot) -> None:
        super().__init__(controlled_session_factory(database, FakeSourceRuntime(snapshot)))
        self.snapshot = snapshot

    def __enter__(self) -> _SemanticUnit:
        super().__enter__()
        self.session.info["source_snapshot"] = self.snapshot
        return self

    @property
    def source_admission(self) -> SessionSourceAdmission:
        return SessionSourceAdmission(self.session)


def _two_sources(
    database: Database,
) -> tuple[
    ScopeProbe, ScopeProbe, tuple[EpisodicMemory, EpisodicMemory], SourceEligibilitySnapshot
]:
    activate(database)
    alice = _probe(database, counterparty="alice", prefix="excluded-source", day=1)
    _turn(alice, "Меня зовут SOURCE_EXCLUDED_MARKER.", "excluded-source")
    bob = _probe(database, counterparty="bob", prefix="safe-source", day=2)
    _turn(bob, "Меня зовут SOURCE_SAFE_MARKER.", "safe-source")
    memories = (
        alice.services.memories.execute(scope=alice.scope)[0],
        bob.services.memories.execute(scope=bob.scope)[0],
    )
    snapshot = _snapshot(alice.scope.identity_id)
    _seed_graph(database, snapshot)
    return alice, bob, memories, snapshot


def test_exclusion_filters_episode_semantic_inspection_vectors_and_backfill_before_selection(
    migrated_database: Database,
) -> None:
    alice, bob, (excluded, safe), snapshot = _two_sources(migrated_database)
    changed = _exclude(migrated_database, snapshot, excluded.evidence[0].source_message_id)
    with _session(migrated_database, changed) as session:
        memories = SQLAlchemyEpisodicMemoryRepository(session)
        semantic = SQLAlchemySemanticMemoryRepository(session)
        index = SQLAlchemyEpisodicMemoryIndexRepository(session)
        assert memories.list_memories(scope=alice.scope, include_archived=True) == ()
        assert (
            memories.get_memory(excluded.memory_id, scope=alice.scope, include_archived=True)
            is None
        )
        assert semantic.resolve_source_scope(excluded.memory_id) is None
        assert semantic.list_claims(scope=alice.scope) == ()
        assert semantic.list_unprocessed_memory_ids(scope=alice.scope, limit=1) == ()
        assert index.list_unindexed(alice.embedding.space, scope=alice.scope, rebuild=True) == ()
        assert not index.has_candidates(
            alice.embedding.space,
            scope=alice.scope,
            cutoff=INTERACTION_TIME + timedelta(days=10),
            excluded_interaction_id=None,
        )
        assert memories.list_memories(scope=bob.scope) == (safe,)
        assert len(semantic.list_claims(scope=bob.scope)) == 1
        assert index.has_candidates(
            bob.embedding.space,
            scope=bob.scope,
            cutoff=INTERACTION_TIME + timedelta(days=10),
            excluded_interaction_id=None,
        )


def test_episode_formation_requires_paired_assistant_dependency_even_when_quotes_are_safe(
    migrated_database: Database,
) -> None:
    _alice, bob, (excluded, safe), snapshot = _two_sources(migrated_database)
    interaction = bob.services.history.execute(scope=bob.scope).interactions[0]
    with migrated_database.session_factory() as session:
        row = session.get(SourcePayloadRow, ("assistant_reply", safe.source_interaction_id, 1))
        assert row is not None
        row.payload_refs = [_ref(PayloadRef("episode", excluded.memory_id))]
        session.commit()
    changed = _exclude(migrated_database, snapshot, excluded.evidence[0].source_message_id)
    with _session(migrated_database, changed) as session:
        assert (
            SQLAlchemyEpisodicMemoryRepository(session).get_source_interaction(
                safe.source_interaction_id
            )
            is None
        )
    assert bob.services.history.execute(scope=bob.scope).interactions == ()
    provider = episode_from_user()
    former = FormEpisodeForInteraction(
        lambda: _MemoryUnit(migrated_database, changed),
        provider,
        MemoryManager(),
        FrozenClock(INTERACTION_TIME),
        id_sequence("paired-denied"),
    )
    with pytest.raises(ValueError, match="episode_source_scope_unverified"):
        asyncio.run(former.execute(interaction, trace_id="paired-denied"))
    assert provider.requests == []


def test_semantic_owner_withdrawal_preserves_history_frees_key_and_never_calls_provider(
    migrated_database: Database,
) -> None:
    alice, bob, (excluded, _safe), snapshot = _two_sources(migrated_database)
    prior = alice.services.semantic_claims.list(scope=alice.scope)[0]
    unrelated = bob.services.semantic_claims.list(scope=bob.scope)[0]
    changed = _exclude(migrated_database, snapshot, excluded.evidence[0].source_message_id)
    with _session(migrated_database, changed) as session:
        repository = SQLAlchemySemanticMemoryRepository(session)
        assert (
            repository.withdraw_source_support(
                excluded_message_ids=changed.excluded_message_ids,
                request_id="exclude-owner",
                policy_generation=changed.generation,
                now=INTERACTION_TIME + timedelta(days=10),
            )
            == 1
        )
        session.commit()
        current = session.get(SemanticClaimRow, prior.claim_id)
        other = session.get(SemanticClaimRow, unrelated.claim_id)
        assert current is not None
        assert other is not None
        assert current.status == "retracted"
        assert current.aggregate_version == prior.aggregate_version + 1
        assert current.confidence == prior.confidence
        assert current.value == prior.value
        assert other.aggregate_version == unrelated.aggregate_version
        assert other.status == "active"
        assert repository._map_claim(current).evidence == prior.evidence
        assert repository.list_claims(scope=alice.scope, active_only=False) == ()
        assert (
            repository.withdraw_source_support(
                excluded_message_ids=changed.excluded_message_ids,
                request_id="exclude-owner",
                policy_generation=changed.generation,
                now=INTERACTION_TIME + timedelta(days=11),
            )
            == 0
        )
        assert len(tuple(session.scalars(select(OwnerSourceWithdrawalRow)))) == 1
        assert len(repository.list_revisions(prior.claim_id)) == 1
        assert not tuple(
            session.scalars(
                select(SemanticClaimRow).where(
                    SemanticClaimRow.claim_key == prior.claim_key,
                    SemanticClaimRow.status == "active",
                )
            )
        )
    assert alice.semantic is not None
    assert len(alice.semantic.requests) == 1


def test_omitted_actual_input_root_retires_claim_whose_accepted_evidence_is_safe(
    migrated_database: Database,
) -> None:
    _alice, bob, (excluded, safe), snapshot = _two_sources(migrated_database)
    safe_claim = bob.services.semantic_claims.list(scope=bob.scope)[0]
    with migrated_database.session_factory() as session:
        row = session.get(SourcePayloadRow, ("semantic_claim", safe_claim.claim_id, 1))
        assert row is not None
        row.payload_refs = [
            _ref(PayloadRef("episode", safe.memory_id)),
            _ref(PayloadRef("episode", excluded.memory_id)),
        ]
        session.commit()
    changed = _exclude(migrated_database, snapshot, excluded.evidence[0].source_message_id)
    with _session(migrated_database, changed) as session:
        repository = SQLAlchemySemanticMemoryRepository(session)
        assert (
            repository.withdraw_source_support(
                excluded_message_ids=changed.excluded_message_ids,
                request_id="actual-input-owner",
                policy_generation=changed.generation,
                now=INTERACTION_TIME + timedelta(days=10),
            )
            == 2
        )
        session.commit()
        withdrawn = session.get(SemanticClaimRow, safe_claim.claim_id)
        assert withdrawn is not None
        assert withdrawn.status == "retracted"
        assert repository._map_claim(withdrawn).evidence == safe_claim.evidence
        assert withdrawn.confidence == safe_claim.confidence


def test_unknown_legacy_semantic_is_withheld_and_retired_after_any_lineage_exclusion(
    migrated_database: Database,
) -> None:
    _alice, bob, (excluded, _safe), snapshot = _two_sources(migrated_database)
    claim = bob.services.semantic_claims.list(scope=bob.scope)[0]
    with migrated_database.session_factory() as session:
        row = session.get(SourcePayloadRow, ("semantic_claim", claim.claim_id, 1))
        assert row is not None
        row.proof_status, row.legacy = "unknown", True
        session.commit()
    changed = _exclude(migrated_database, snapshot, excluded.evidence[0].source_message_id)
    with _session(migrated_database, changed) as session:
        repository = SQLAlchemySemanticMemoryRepository(session)
        assert repository.list_claims(scope=bob.scope) == ()
        assert (
            repository.withdraw_source_support(
                excluded_message_ids=changed.excluded_message_ids,
                request_id="legacy-owner",
                policy_generation=changed.generation,
                now=INTERACTION_TIME + timedelta(days=10),
            )
            == 2
        )
        session.commit()
        withdrawn = session.get(SemanticClaimRow, claim.claim_id)
        assert withdrawn is not None
        assert withdrawn.status == "retracted"


def test_excluded_replay_and_embedding_dispatch_fail_before_provider(
    migrated_database: Database,
) -> None:
    alice, _bob, (excluded, _safe), snapshot = _two_sources(migrated_database)
    changed = _exclude(migrated_database, snapshot, excluded.evidence[0].source_message_id)
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="SOURCE_EXCLUDED_MARKER")
    )
    former = FormSemanticMemory(
        lambda: _SemanticUnit(migrated_database, changed),
        provider,
        SemanticMemoryManager(),
        FrozenClock(INTERACTION_TIME),
        id_sequence("semantic-denied"),
        4,
        4,
    )
    with pytest.raises(ValueError, match="semantic_source_ineligible"):
        asyncio.run(former.execute(excluded.memory_id, scope=alice.scope, trace_id="denied"))
    assert provider.requests == []
    embedding = FakeEmbeddingProvider({excluded.summary: (1.0, 0.0, 0.0)})
    indexer = IndexEpisodicMemory(
        lambda: _IndexUnit(migrated_database, changed), embedding, FrozenClock(INTERACTION_TIME)
    )
    with pytest.raises(ValueError, match="memory_source_ineligible"):
        asyncio.run(indexer.execute(excluded, trace_id="denied"))
    assert embedding.requests == []


def test_withdrawal_audit_failure_rolls_back_claim_and_receipt_but_preserves_floor(
    migrated_database: Database,
) -> None:
    alice, _bob, (excluded, _safe), snapshot = _two_sources(migrated_database)
    claim = alice.services.semantic_claims.list(scope=alice.scope)[0]
    changed = _exclude(migrated_database, snapshot, excluded.evidence[0].source_message_id)

    def fail_audit(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        if statement.startswith("INSERT INTO audit_events"):
            raise RuntimeError("synthetic_audit_failure")

    event.listen(migrated_database.engine, "before_cursor_execute", fail_audit)
    try:
        with _session(migrated_database, changed) as session:
            SQLAlchemySemanticMemoryRepository(session).withdraw_source_support(
                excluded_message_ids=changed.excluded_message_ids,
                request_id="fault-owner",
                policy_generation=changed.generation,
                now=INTERACTION_TIME + timedelta(days=10),
            )
            with pytest.raises(RuntimeError, match="synthetic_audit_failure"):
                session.commit()
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", fail_audit)
    with _session(migrated_database, changed) as session:
        row = session.get(SemanticClaimRow, claim.claim_id)
        assert row is not None
        assert row.aggregate_version == claim.aggregate_version
        assert row.status == "active"
        assert not tuple(session.scalars(select(OwnerSourceWithdrawalRow)))
        assert (
            SQLAlchemyEpisodicMemoryRepository(session).get_memory(
                excluded.memory_id, scope=alice.scope, include_archived=True
            )
            is None
        )


def test_pure_semantic_retirement_keeps_evidence_and_confidence_without_rewording(
    migrated_database: Database,
) -> None:
    alice, _bob, _memories, _snapshot_value = _two_sources(migrated_database)
    prior = alice.services.semantic_claims.list(scope=alice.scope)[0]
    manager = SemanticMemoryManager()
    plan = manager.withdraw_support(
        prior,
        request_id="pure-withdrawal",
        policy_generation=1,
        now=INTERACTION_TIME + timedelta(days=10),
    )
    assert plan is not None
    assert plan.current_claim.status is SemanticClaimStatus.RETRACTED
    assert plan.current_claim.aggregate_version == prior.aggregate_version + 1
    assert plan.current_claim.evidence == prior.evidence
    assert plan.current_claim.confidence == prior.confidence
    assert plan.current_claim.value == prior.value
    assert (
        manager.withdraw_support(
            plan.current_claim,
            request_id="repeat",
            policy_generation=1,
            now=INTERACTION_TIME + timedelta(days=11),
        )
        is None
    )
    with pytest.raises(ValueError, match="semantic_withdrawal_request_invalid"):
        manager.withdraw_support(prior, request_id="", policy_generation=1, now=INTERACTION_TIME)


def test_semantic_window_excludes_unsafe_rows_before_limit_without_starving_safe_input(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    probes = [
        _probe(
            migrated_database,
            counterparty="alice",
            prefix=f"window-{day}",
            day=day,
            semantic_factory=None,
        )
        for day in (1, 2, 3)
    ]
    for day, probe in enumerate(probes, 1):
        _turn(probe, f"Меня зовут WINDOW_{day}.", f"window-{day}")
    scope = probes[-1].scope
    memories = probes[-1].services.memories.execute(scope=scope)
    snapshot = _snapshot(scope.identity_id)
    _seed_graph(migrated_database, snapshot)
    changed = _exclude(migrated_database, snapshot, memories[1].evidence[0].source_message_id)
    with _session(migrated_database, changed) as session:
        repository = SQLAlchemySemanticMemoryRepository(session)
        sources = repository.get_source_memories(memories[-1].memory_id, scope=scope, limit=2)
        assert tuple(item.memory_id for item in sources) == (
            memories[2].memory_id,
            memories[0].memory_id,
        )
        assert repository.list_unprocessed_memory_ids(scope=scope, limit=2) == (
            memories[0].memory_id,
            memories[2].memory_id,
        )


def test_new_independent_root_same_fact_creates_fresh_claim_without_retired_confidence(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    probes = [
        _probe(
            migrated_database,
            counterparty="alice",
            prefix=f"replacement-{day}",
            day=day,
            semantic_factory=None
            if day == 3
            else lambda request: one_claim(request, predicate="name", value="SAME_FACT"),
        )
        for day in (1, 2, 3)
    ]
    for day, probe in enumerate(probes, 1):
        _turn(probe, "Меня зовут SAME_FACT.", f"replacement-{day}")
    scope = probes[-1].scope
    prior = probes[-1].services.semantic_claims.list(scope=scope)[0]
    assert prior.confidence == 0.92
    assert len(prior.evidence) == 2
    memories = probes[-1].services.memories.execute(scope=scope)
    snapshot = _snapshot(scope.identity_id)
    _seed_graph(migrated_database, snapshot)
    changed = _exclude(migrated_database, snapshot, memories[0].evidence[0].source_message_id)
    with _session(migrated_database, changed) as session:
        assert (
            SQLAlchemySemanticMemoryRepository(session).withdraw_source_support(
                excluded_message_ids=changed.excluded_message_ids,
                request_id="replacement-owner",
                policy_generation=changed.generation,
                now=INTERACTION_TIME + timedelta(days=10),
            )
            == 1
        )
        mark_owner_reconciled(session, "semantic_memory")
        session.commit()
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="SAME_FACT")
    )
    former = FormSemanticMemory(
        lambda: _SemanticUnit(migrated_database, changed),
        provider,
        SemanticMemoryManager(),
        FrozenClock(INTERACTION_TIME + timedelta(days=11)),
        id_sequence("replacement-fresh"),
        4,
        4,
    )
    decision = asyncio.run(
        former.execute(memories[-1].memory_id, scope=scope, trace_id="replacement-fresh")
    )
    assert decision.created_count == 1
    assert decision.merged_count == 0
    assert len(provider.requests) == 1
    assert memories[0].memory_id not in repr(provider.requests)
    with _session(migrated_database, changed) as session:
        claims = SQLAlchemySemanticMemoryRepository(session).list_claims(
            scope=scope, active_only=True
        )
        assert len(claims) == 1
        current = claims[0]
        assert current.claim_id != prior.claim_id
        assert current.value == prior.value
        assert current.confidence == 0.90
        assert len(current.evidence) == 1
        assert current.evidence[0].root_message_id == memories[-1].evidence[0].source_message_id
        old = session.get(SemanticClaimRow, prior.claim_id)
        assert old is not None
        assert old.confidence == 0.92
        assert old.status == "retracted"


def test_archived_support_is_retained_but_source_exclusion_denies_reactivation(
    migrated_database: Database,
) -> None:
    alice, _bob, (episode, _safe), snapshot = _two_sources(migrated_database)
    with _session(migrated_database, snapshot) as session:
        row = session.get(EpisodicMemoryRow, episode.memory_id)
        assert row is not None
        row.lifecycle_status = "archived"
        # This controlled fixture retains a version-1 archive to isolate provenance from CAS.
        session.commit()
        assert SQLAlchemySemanticMemoryRepository(session).list_claims(scope=alice.scope)
        assert (
            SQLAlchemyEpisodicMemoryRepository(session).get_memory(
                episode.memory_id, scope=alice.scope, include_archived=True
            )
            is not None
        )
    changed = _exclude(migrated_database, snapshot, episode.evidence[0].source_message_id)

    controls = MemoryLifecycleControls(
        lambda: _MemoryUnit(migrated_database, changed),
        MemoryManager(),
        FrozenClock(INTERACTION_TIME),
        id_sequence("reactivate-denied"),
    )
    with pytest.raises(MemoryLifecycleError, match="memory_lifecycle_target_unavailable"):
        controls.preview(episode.memory_id, MemoryLifecycleStatus.ACTIVE, scope=alice.scope)


def test_owner_repair_lag_allows_safe_reads_but_blocks_semantic_dispatch_and_replay(
    migrated_database: Database,
) -> None:
    alice, bob, (excluded, safe), snapshot = _two_sources(migrated_database)
    changed = _exclude(migrated_database, snapshot, excluded.evidence[0].source_message_id)
    with _session(migrated_database, changed) as session:
        assert SQLAlchemyEpisodicMemoryRepository(session).list_memories(scope=bob.scope) == (safe,)
        assert SQLAlchemySemanticMemoryRepository(session).list_claims(scope=bob.scope)
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="SOURCE_SAFE_MARKER")
    )
    former = FormSemanticMemory(
        lambda: _SemanticUnit(migrated_database, changed),
        provider,
        SemanticMemoryManager(),
        FrozenClock(INTERACTION_TIME),
        id_sequence("pending-owner"),
        4,
        4,
    )
    with pytest.raises(SourcePolicyError, match="owner_reconciliation_required"):
        asyncio.run(former.execute(safe.memory_id, scope=bob.scope, trace_id="pending-owner"))
    assert provider.requests == []
    with _session(migrated_database, changed) as session:
        repository = SQLAlchemySemanticMemoryRepository(session)
        assert (
            repository.withdraw_source_support(
                excluded_message_ids=changed.excluded_message_ids,
                request_id="complete-owner",
                policy_generation=changed.generation,
                now=INTERACTION_TIME + timedelta(days=10),
            )
            == 1
        )
        mark_owner_reconciled(session, "semantic_memory")
        session.commit()
    replay = asyncio.run(former.execute(safe.memory_id, scope=bob.scope, trace_id="safe-replay"))
    assert replay.source_memory_id == safe.memory_id
    assert provider.requests == []
    assert alice.services.semantic_claims is not None


@pytest.mark.parametrize("operation", ["index", "semantic"])
def test_first_sql_write_rechecks_source_receipt_and_rolls_back_on_late_ineligibility(
    migrated_database: Database, operation: str
) -> None:
    activate(migrated_database)
    probe = _probe(
        migrated_database,
        counterparty="alice",
        prefix="first-write-source",
        day=1,
        semantic_factory=None,
    )
    _turn(probe, "Меня зовут FIRST_WRITE_MARKER.", "first-write-source")
    memory = probe.services.memories.execute(scope=probe.scope)[0]
    snapshot = _snapshot(probe.scope.identity_id)
    _seed_graph(migrated_database, snapshot)
    target = (
        "INSERT INTO episodic_memory_embeddings"
        if operation == "index"
        else "INSERT INTO semantic_formation_decisions"
    )
    fired = False

    def change_admission(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        nonlocal fired
        if not fired and statement.startswith(target):
            fired = True
            connection.execute(
                update(SourcePayloadRow)
                .where(
                    SourcePayloadRow.kind == "episode",
                    SourcePayloadRow.payload_id == memory.memory_id,
                    SourcePayloadRow.payload_version == 1,
                )
                .values(eligible=False)
            )

    with migrated_database.engine.connect() as connection:
        vectors_before = tuple(connection.execute(select(EpisodicMemoryEmbeddingRow.vector)))
    event.listen(migrated_database.engine, "before_cursor_execute", change_admission)
    try:
        if operation == "index":
            embedding = FakeEmbeddingProvider({memory.summary: (0.0, 1.0, 0.0)})
            indexer = IndexEpisodicMemory(
                lambda: _IndexUnit(migrated_database, snapshot),
                embedding,
                FrozenClock(INTERACTION_TIME),
            )
            with pytest.raises(ValueError, match="memory_source_changed"):
                asyncio.run(indexer.execute(memory, trace_id="first-write-index"))
            assert len(embedding.requests) == 1
        else:
            provider = semantic_provider(
                lambda request: one_claim(request, predicate="name", value="FIRST_WRITE_MARKER")
            )
            former = FormSemanticMemory(
                lambda: _SemanticUnit(migrated_database, snapshot),
                provider,
                SemanticMemoryManager(),
                FrozenClock(INTERACTION_TIME),
                id_sequence("first-write-semantic"),
                4,
                4,
            )
            with pytest.raises(ValueError, match="semantic_sources_changed"):
                asyncio.run(
                    former.execute(
                        memory.memory_id, scope=probe.scope, trace_id="first-write-semantic"
                    )
                )
            assert len(provider.requests) == 1
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", change_admission)
    assert fired
    with migrated_database.engine.connect() as connection:
        assert (
            tuple(connection.execute(select(EpisodicMemoryEmbeddingRow.vector))) == vectors_before
        )
        assert (
            connection.scalar(select(func.count()).select_from(SemanticFormationDecisionRow)) == 0
        )
        assert connection.scalar(select(func.count()).select_from(SemanticClaimRow)) == 0
    with _session(migrated_database, snapshot) as session:
        receipt = session.get(SourcePayloadRow, ("episode", memory.memory_id, 1))
        assert receipt is not None
        assert receipt.eligible


def test_episode_first_write_binds_paired_reply_even_if_root_quote_still_eligible(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    probe = _probe(
        migrated_database,
        counterparty="alice",
        prefix="episode-write",
        day=1,
        create_episode=False,
        semantic_factory=None,
    )
    user_text = "Меня зовут EPISODE_WRITE_MARKER."
    probe.embedding.vectors[user_text] = (1.0, 0.0, 0.0)
    # Commit only the real canonical pair. Post-response processing would already
    # create a terminal skip receipt, so do not run it for a first-write fixture.
    asyncio.run(
        probe.services.talk.execute(TalkInput(user_text, "trace-episode-write", "episode-write"))
    )
    interaction = probe.services.history.execute(scope=probe.scope).interactions[0]
    assert probe.episode.requests == []
    with migrated_database.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(EpisodicMemoryRow)) == 0
        assert connection.scalar(select(func.count()).select_from(EpisodeFormationDecisionRow)) == 0
    snapshot = _snapshot(probe.scope.identity_id)
    _seed_graph(migrated_database, snapshot)
    fired = False

    def change_reply(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        nonlocal fired
        if not fired and statement.startswith("INSERT INTO episodic_memories"):
            fired = True
            connection.execute(
                update(SourcePayloadRow)
                .where(
                    SourcePayloadRow.kind == "assistant_reply",
                    SourcePayloadRow.payload_id == interaction.interaction_id,
                )
                .values(eligible=False)
            )

    provider = episode_from_user()
    former = FormEpisodeForInteraction(
        lambda: _MemoryUnit(migrated_database, snapshot),
        provider,
        MemoryManager(),
        FrozenClock(INTERACTION_TIME),
        id_sequence("episode-first-write"),
    )
    event.listen(migrated_database.engine, "before_cursor_execute", change_reply)
    try:
        with pytest.raises(ValueError, match="episode_source_changed"):
            asyncio.run(former.execute(interaction, trace_id="episode-first-write"))
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", change_reply)
    assert fired
    assert len(provider.requests) == 1
    with migrated_database.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(EpisodicMemoryRow)) == 0
        assert connection.scalar(select(func.count()).select_from(EpisodeFormationDecisionRow)) == 0


def test_operator_search_is_fresh_query_only_and_does_not_fabricate_canonical_evidence(
    migrated_database: Database,
) -> None:
    _alice, bob, (excluded, safe), snapshot = _two_sources(migrated_database)
    changed = _exclude(migrated_database, snapshot, excluded.evidence[0].source_message_id)
    retriever = bob.services.retrieve_memories
    assert retriever is not None
    query_text = "FRESH_LOCAL_OPERATOR_QUERY"
    bob.embedding.vectors[query_text] = (1.0, 0.0, 0.0)
    calls_before = len(bob.embedding.requests)
    with migrated_database.engine.connect() as connection:
        roots_before = connection.scalar(select(func.count()).select_from(ConversationMessageRow))
        evidence_before = connection.scalar(
            select(func.count()).select_from(SemanticClaimEvidenceRow)
        )
    query = RetrievalQuery(
        query_text, "operator-query", INTERACTION_TIME + timedelta(days=10), None, bob.scope
    )
    denied = asyncio.run(retriever.execute(query))
    assert denied.status is RetrievalStatus.UNAVAILABLE
    assert len(bob.embedding.requests) == calls_before
    allowed = asyncio.run(retriever.execute(replace(query, operator_query=True)))
    assert allowed.status is RetrievalStatus.RETRIEVED
    assert allowed.memory_ids == (safe.memory_id,)
    assert allowed.source_payload_refs == (
        PayloadRef("episode", safe.memory_id, safe.aggregate_version),
    )
    assert len(bob.embedding.requests) == calls_before + 1
    with migrated_database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(ConversationMessageRow))
            == roots_before
        )
        assert (
            connection.scalar(select(func.count()).select_from(SemanticClaimEvidenceRow))
            == evidence_before
        )
    assert changed.generation == 1


def test_missing_current_payload_receipt_with_prior_owner_proof_can_retire_safely(
    migrated_database: Database,
) -> None:
    alice, _bob, (excluded, _safe), snapshot = _two_sources(migrated_database)
    prior = alice.services.semantic_claims.list(scope=alice.scope)[0]
    with migrated_database.session_factory() as session:
        row = session.get(SemanticClaimRow, prior.claim_id)
        assert row is not None
        row.aggregate_version += 1
        session.commit()
    changed = _exclude(migrated_database, snapshot, excluded.evidence[0].source_message_id)
    with _session(migrated_database, changed) as session:
        repository = SQLAlchemySemanticMemoryRepository(session)
        assert (
            repository.withdraw_source_support(
                excluded_message_ids=changed.excluded_message_ids,
                request_id="missing-version-owner",
                policy_generation=changed.generation,
                now=INTERACTION_TIME + timedelta(days=10),
            )
            == 1
        )
        session.commit()
        row = session.get(SemanticClaimRow, prior.claim_id)
        assert row is not None
        assert row.aggregate_version == prior.aggregate_version + 2
        assert row.status == "retracted"
        receipt = session.get(
            SourcePayloadRow, ("semantic_claim", prior.claim_id, row.aggregate_version)
        )
        assert receipt is not None
        assert not receipt.eligible
        assert receipt.proof_status == "unknown"
        assert repository.list_claims(scope=alice.scope, active_only=False) == ()


def test_unattributed_unsafe_active_claim_keeps_owner_repair_pending(
    migrated_database: Database,
) -> None:
    alice, _bob, (excluded, _safe), snapshot = _two_sources(migrated_database)
    prior = alice.services.semantic_claims.list(scope=alice.scope)[0]
    with migrated_database.engine.begin() as connection:
        connection.execute(
            delete(SemanticClaimEvidenceRow).where(
                SemanticClaimEvidenceRow.claim_id == prior.claim_id
            )
        )
        connection.execute(
            delete(SourcePayloadRow).where(
                SourcePayloadRow.kind == "semantic_claim",
                SourcePayloadRow.payload_id == prior.claim_id,
            )
        )
    changed = _exclude(migrated_database, snapshot, excluded.evidence[0].source_message_id)
    with _session(migrated_database, changed) as session:
        repository = SQLAlchemySemanticMemoryRepository(session)
        with pytest.raises(SourcePolicyError, match="source_owner_attribution_unproven"):
            repository.withdraw_source_support(
                excluded_message_ids=changed.excluded_message_ids,
                request_id="unproven-owner",
                policy_generation=changed.generation,
                now=INTERACTION_TIME + timedelta(days=10),
            )
        row = session.get(SemanticClaimRow, prior.claim_id)
        assert row is not None
        assert row.status == "active"
        with pytest.raises(SourcePolicyError, match="source_owner_reconciliation_required"):
            require_owner_reconciled(session, "semantic_memory")
        assert not tuple(session.scalars(select(OwnerSourceWithdrawalRow)))


@pytest.mark.parametrize("operation", ["index", "semantic"])
@pytest.mark.parametrize("reactivate", [False, True])
def test_queued_new_processing_reproves_active_exact_episode_version_before_dispatch(
    migrated_database: Database, operation: str, reactivate: bool
) -> None:
    activate(migrated_database)
    probe = _probe(
        migrated_database,
        counterparty="alice",
        prefix="queued-source",
        day=1,
        semantic_factory=None,
    )
    _turn(probe, "Меня зовут QUEUED_SOURCE_MARKER.", "queued-source")
    memory = probe.services.memories.execute(scope=probe.scope)[0]
    snapshot = _snapshot(probe.scope.identity_id)
    _seed_graph(migrated_database, snapshot)
    controls = MemoryLifecycleControls(
        lambda: _MemoryUnit(migrated_database, snapshot),
        MemoryManager(),
        FrozenClock(INTERACTION_TIME + timedelta(days=10)),
        id_sequence("queued-lifecycle"),
    )

    def change_lifecycle() -> None:
        preview = controls.preview(
            memory.memory_id, MemoryLifecycleStatus.ARCHIVED, scope=probe.scope
        )
        controls.execute(
            MemoryLifecycleCommand(
                memory_id=memory.memory_id,
                target_status=MemoryLifecycleStatus.ARCHIVED,
                expected_version=preview.current_version,
                request_id="queued-archive",
                preview_digest=preview.preview_digest,
            ),
            scope=probe.scope,
            trace_id="queued-archive",
        )
        if reactivate:
            preview = controls.preview(
                memory.memory_id, MemoryLifecycleStatus.ACTIVE, scope=probe.scope
            )
            controls.execute(
                MemoryLifecycleCommand(
                    memory_id=memory.memory_id,
                    target_status=MemoryLifecycleStatus.ACTIVE,
                    expected_version=preview.current_version,
                    request_id="queued-reactivate",
                    preview_digest=preview.preview_digest,
                ),
                scope=probe.scope,
                trace_id="queued-reactivate",
            )

    class QueuedEmbedding(FakeEmbeddingProvider):
        produced = 0

        async def embed(self, request: EmbeddingRequest, /) -> EmbeddingResponse:
            self.produced += 1
            change_lifecycle()
            admit_provider_dispatch()
            return await super().embed(request)

    class QueuedSemantic(FakeSemanticFormationProvider):
        produced = 0

        async def generate_structured(
            self, request: SemanticFormationRequest, /
        ) -> SemanticFormationProviderResponse:
            self.produced += 1
            change_lifecycle()
            admit_provider_dispatch()
            return await super().generate_structured(request)

    if operation == "index":
        embedding = QueuedEmbedding({memory.summary: (1.0, 0.0, 0.0)})
        indexer = IndexEpisodicMemory(
            lambda: _IndexUnit(migrated_database, snapshot),
            embedding,
            FrozenClock(INTERACTION_TIME),
        )
        with pytest.raises(SourcePolicyError):
            asyncio.run(indexer.execute(memory, trace_id="queued-index"))
        assert embedding.produced == 1
        assert embedding.requests == []
    else:
        provider = QueuedSemantic(
            response_factory=lambda request: SemanticFormationProviderResponse(
                proposal=one_claim(request, predicate="name", value="QUEUED_SOURCE_MARKER"),
                provider="fake",
                model="queued",
                formation_method="fixture.queued",
            )
        )
        former = FormSemanticMemory(
            lambda: _SemanticUnit(migrated_database, snapshot),
            provider,
            SemanticMemoryManager(),
            FrozenClock(INTERACTION_TIME),
            id_sequence("queued-semantic"),
            4,
            4,
        )
        with pytest.raises(SourcePolicyError):
            asyncio.run(
                former.execute(memory.memory_id, scope=probe.scope, trace_id="queued-semantic")
            )
        assert provider.produced == 1
        assert provider.requests == []
    with _session(migrated_database, snapshot) as session:
        current = session.get(EpisodicMemoryRow, memory.memory_id)
        assert current is not None
        assert current.aggregate_version == (3 if reactivate else 2)
        assert not tuple(session.scalars(select(SemanticFormationDecisionRow)))
        assert not tuple(session.scalars(select(SemanticClaimRow)))
