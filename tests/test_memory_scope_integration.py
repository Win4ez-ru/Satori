"""Two-counterparty privacy through production composition, restart and recovery.

All content is synthetic. Equal vectors deliberately make a foreign episode eligible by
similarity; a useful own recall must still succeed. No generated phrasing is acceptance evidence.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from satori.application.conversation.contracts import SatoriReply, TalkInput
from satori.application.conversation.errors import (
    ConversationSessionNotFound,
    InteractionIdempotencyConflict,
)
from satori.application.recovery.use_cases import WholeStateRecovery
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.core.affect import (
    AffectiveAppraisalProposal,
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
)
from satori.core.conversation import ConversationProviderResponse
from satori.core.semantic import SemanticFormationProposal, SemanticFormationRequest
from satori.domain.memory_scope import MemoryScope
from satori.domain.reflection import ReflectionOutcome, ReflectionOutcomeDecision
from satori.domain.semantic_memory import SemanticClaim
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from tests.fakes import (
    FakeAffectiveAppraisalProvider,
    FakeConversationProvider,
    FakeEmbeddingProvider,
    FakeEpisodeFormationProvider,
    FakeSemanticFormationProvider,
    FrozenClock,
)
from tests.test_recovery_whole_state import recovery, stored_rows
from tests.test_stage4_conversation_memory import (
    INTERACTION_TIME,
    activate,
    conversation_provider,
    id_sequence,
    settings,
    skip_episode_provider,
)
from tests.test_stage6_semantic_memory import episode_from_user, one_claim, semantic_provider
from tests.test_stage11_positions_integration import FakePositionProvider, former
from tests.test_stage13_inclinations_persistence import (
    _applied_inclination,
    _canonical_position_source,
    _create_reflection_source,
)

ALICE_MARKER = "ALICE_PRIVATE_SCOPE"
BOB_MARKER = "BOB_PRIVATE_SCOPE"
RECALL = "Напомни, как меня зовут, по нашей прежней беседе."


@dataclass(frozen=True, slots=True)
class ScopeProbe:
    services: ConversationServices
    scope: MemoryScope
    conversation: FakeConversationProvider
    episode: FakeEpisodeFormationProvider
    semantic: FakeSemanticFormationProvider | None
    appraisal: FakeAffectiveAppraisalProvider
    embedding: FakeEmbeddingProvider


def _neutral_appraisal(request: AffectiveAppraisalRequest) -> AffectiveAppraisalProviderResponse:
    return AffectiveAppraisalProviderResponse(
        proposal=AffectiveAppraisalProposal(
            schema_version=1,
            pleasantness=0.0,
            activation=0.0,
            novelty=0.0,
            salience=0.0,
            uncertainty=0.0,
            curiosity_signal=0.0,
            interest_signal=0.0,
            humor_signal=0.0,
            concern_signal=0.0,
            frustration_signal=0.0,
            confidence_signal=0.0,
            appraisal_confidence=0.9,
            source_refs=(request.interaction_id,),
            reason_codes=("neutral_event",),
        ),
        provider="fake-appraisal",
        model="scope-fixture",
        appraisal_method="fixture.scope.v1",
    )


def _name_proposal(request: SemanticFormationRequest) -> SemanticFormationProposal:
    source = next(item for item in request.memories if item.memory_id == request.source_memory_id)
    value = source.summary.removeprefix("Меня зовут ").removesuffix(".")
    return one_claim(request, predicate="name", value=value)


def _probe(
    database: Database,
    *,
    counterparty: str,
    prefix: str,
    day: int,
    create_episode: bool = True,
    semantic_factory: Callable[[SemanticFormationRequest], SemanticFormationProposal] | None = (
        _name_proposal
    ),
) -> ScopeProbe:
    initial = build_initial_self_services(database)
    scope = MemoryScope(initial.get_self.execute().identity.identity_id, counterparty)
    foreground = conversation_provider("Приняла.")
    episode = episode_from_user() if create_episode else skip_episode_provider()
    semantic = semantic_provider(semantic_factory) if semantic_factory is not None else None
    appraisal = FakeAffectiveAppraisalProvider(response_factory=_neutral_appraisal)
    # Every corpus input receives this exact vector, so privacy cannot pass by low similarity.
    embedding = FakeEmbeddingProvider({RECALL: (1.0, 0.0, 0.0)})
    active = build_conversation_services(
        database,
        initial,
        foreground,
        episode,
        settings().model_copy(update={"default_counterparty_id": counterparty}),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=day)),
        id_generator=id_sequence(prefix),
        embedding_provider=embedding,
        semantic_provider=semantic,
        appraisal_provider=appraisal,
        source_policy_required=False,
    )
    return ScopeProbe(active, scope, foreground, episode, semantic, appraisal, embedding)


def _turn(
    probe: ScopeProbe, user_text: str, request_id: str, *, session_id: str | None = None
) -> SatoriReply:
    probe.embedding.vectors[user_text] = (1.0, 0.0, 0.0)

    async def run() -> SatoriReply:
        reply = await probe.services.talk.execute(
            TalkInput(user_text, f"trace-{request_id}", request_id, session_id=session_id)
        )
        if not reply.replayed:
            await probe.services.post_response.execute(
                reply.interaction_id, trace_id=f"trace-post-{request_id}"
            )
        return reply

    return asyncio.run(run())


def _captured(probe: ScopeProbe) -> str:
    """Inspect complete typed requests, including nested text and provenance handles."""
    return repr(
        (
            probe.conversation.requests,
            probe.episode.requests,
            probe.semantic.requests if probe.semantic is not None else (),
            probe.appraisal.requests,
            probe.embedding.requests,
        )
    )


def _private_handles(database: Database, counterparty: str) -> tuple[str, ...]:
    """Read synthetic fixture handles without adding a global production read interface."""
    queries = (
        "SELECT session_id FROM conversation_sessions WHERE counterparty_id=:party",
        "SELECT i.interaction_id FROM conversation_interactions i "
        "JOIN conversation_sessions s ON s.session_id=i.session_id WHERE s.counterparty_id=:party",
        "SELECT m.message_id FROM conversation_messages m "
        "JOIN conversation_sessions s ON s.session_id=m.session_id WHERE s.counterparty_id=:party",
        "SELECT e.memory_id FROM episodic_memories e "
        "JOIN conversation_interactions i ON i.interaction_id=e.source_interaction_id "
        "JOIN conversation_sessions s ON s.session_id=i.session_id WHERE s.counterparty_id=:party",
        "SELECT e.evidence_id FROM memory_evidence e "
        "JOIN episodic_memories m ON m.memory_id=e.memory_id "
        "JOIN conversation_interactions i ON i.interaction_id=m.source_interaction_id "
        "JOIN conversation_sessions s ON s.session_id=i.session_id WHERE s.counterparty_id=:party",
        "SELECT DISTINCT e.claim_id FROM semantic_claim_evidence e "
        "JOIN conversation_interactions i ON i.interaction_id=e.root_interaction_id "
        "JOIN conversation_sessions s ON s.session_id=i.session_id WHERE s.counterparty_id=:party",
        "SELECT e.semantic_evidence_id FROM semantic_claim_evidence e "
        "JOIN conversation_interactions i ON i.interaction_id=e.root_interaction_id "
        "JOIN conversation_sessions s ON s.session_id=i.session_id WHERE s.counterparty_id=:party",
    )
    with database.engine.connect() as connection:
        return tuple(
            value
            for query in queries
            for value in connection.execute(text(query), {"party": counterparty}).scalars()
        )


def _no_foreign_request_data(probe: ScopeProbe, marker: str, handles: tuple[str, ...]) -> None:
    captured = _captured(probe)
    assert marker not in captured
    assert all(handle not in captured for handle in handles)


def _assert_own_recall(database: Database, *, prefix: str, day: int) -> ScopeProbe:
    bob = _probe(database, counterparty="bob", prefix=prefix, day=day, create_episode=False)
    foreign_handles = _private_handles(database, "alice")
    own_memories = bob.services.memories.execute(scope=bob.scope)
    own_claims = bob.services.semantic_claims.list(scope=bob.scope)
    assert own_memories
    assert own_claims
    assert all(BOB_MARKER in memory.summary for memory in own_memories)
    assert all(BOB_MARKER in str(claim.value) for claim in own_claims)
    reply = _turn(bob, RECALL, f"request-{prefix}")
    assert reply.context_manifest.retrieved_memory_ids == tuple(
        memory.memory_id for memory in own_memories
    )
    assert reply.context_manifest.retrieved_semantic_claim_ids == tuple(
        claim.claim_id for claim in own_claims
    )
    assert BOB_MARKER in _captured(bob)
    _no_foreign_request_data(bob, ALICE_MARKER, foreign_handles)
    history = bob.services.history.execute(scope=bob.scope)
    assert history.sessions
    assert all(session.counterparty_id == "bob" for session in history.sessions)
    assert ALICE_MARKER not in repr(history)
    return bob


def test_full_context_isolation_survives_restart_and_whole_state_restore(
    migrated_database: Database, project_root: Path, tmp_path: Path
) -> None:
    activate(migrated_database)
    alice = _probe(migrated_database, counterparty="alice", prefix="scope-a", day=1)
    _turn(alice, f"Меня зовут {ALICE_MARKER}_01.", "scope-source-a")
    bob = _probe(migrated_database, counterparty="bob", prefix="scope-b", day=2)
    foreign_handles = _private_handles(migrated_database, "alice")
    _turn(bob, f"Меня зовут {BOB_MARKER}_01.", "scope-source-b")
    assert bob.semantic is not None
    assert bob.semantic.requests
    _no_foreign_request_data(bob, ALICE_MARKER, foreign_handles)
    newest_alice = _probe(migrated_database, counterparty="alice", prefix="scope-a-newest", day=3)
    _turn(newest_alice, f"Меня зовут {ALICE_MARKER}_02.", "scope-source-a-newest")
    _no_foreign_request_data(newest_alice, BOB_MARKER, _private_handles(migrated_database, "bob"))
    _assert_own_recall(migrated_database, prefix="scope-recall-before", day=4)

    url = str(migrated_database.engine.url)
    migrated_database.dispose()
    restarted = create_database(url)
    try:
        _assert_own_recall(restarted, prefix="scope-recall-restart", day=5)
        use_cases = recovery(restarted, project_root)
        archive = tmp_path / "private" / "scope.state"
        exported = use_cases.export(archive)
        assert use_cases.preview(archive) == exported
        baseline = stored_rows(Path(restarted.engine.url.database or ""))
        target = tmp_path / "restored" / "scope.db"
        use_cases.restore(archive, target, expected_sha256=exported.archive_sha256)
        assert stored_rows(target) == baseline
        restored = create_database(f"sqlite+pysqlite:///{target}")
        try:
            _assert_own_recall(restored, prefix="scope-recall-restored", day=6)
        finally:
            restored.dispose()
    finally:
        restarted.dispose()


@pytest.mark.parametrize("access", ["session", "replay"])
def test_foreign_history_access_fails_before_any_write_or_provider_call(
    migrated_database: Database, access: str
) -> None:
    activate(migrated_database)
    alice = _probe(
        migrated_database,
        counterparty="alice",
        prefix="foreign-a",
        day=1,
        create_episode=False,
        semantic_factory=None,
    )
    session = alice.services.start_session.execute()
    alice.conversation.response = ConversationProviderResponse(
        f"{ALICE_MARKER}_REPLY", "fake-conversation", "fixture", "stop"
    )
    _turn(alice, "Привет.", "shared-replay-id", session_id=session.session_id)
    bob = _probe(
        migrated_database,
        counterparty="bob",
        prefix="foreign-b",
        day=2,
        create_episode=False,
        semantic_factory=None,
    )
    source = Path(migrated_database.engine.url.database or "")
    before = stored_rows(source)
    with pytest.raises((ConversationSessionNotFound, InteractionIdempotencyConflict)):
        _turn(
            bob,
            "Привет.",
            "new-foreign-session-id" if access == "session" else "shared-replay-id",
            session_id=session.session_id if access == "session" else None,
        )
    assert stored_rows(source) == before
    assert not bob.conversation.requests
    assert not bob.appraisal.requests
    assert not bob.episode.requests
    with pytest.raises(ConversationSessionNotFound):
        bob.services.history.execute(scope=bob.scope, session_id=session.session_id)


def test_equal_facts_do_not_merge_people_and_correction_cannot_change_other_person(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    alice = _probe(migrated_database, counterparty="alice", prefix="equal-a", day=1)
    bob = _probe(migrated_database, counterparty="bob", prefix="equal-b", day=2)
    _turn(alice, "Меня зовут СовпадающееИмя.", "equal-a-name")
    _turn(bob, "Меня зовут СовпадающееИмя.", "equal-b-name")
    a_claim = alice.services.semantic_claims.list(scope=alice.scope)[0]
    b_claim = bob.services.semantic_claims.list(scope=bob.scope)[0]
    assert a_claim.claim_id != b_claim.claim_id
    assert a_claim.confidence == b_claim.confidence == 0.90
    assert a_claim.aggregate_version == b_claim.aggregate_version == 1
    assert {edge.root_message_id for edge in a_claim.evidence}.isdisjoint(
        edge.root_message_id for edge in b_claim.evidence
    )

    city_a = _probe(
        migrated_database,
        counterparty="alice",
        prefix="city-a",
        day=3,
        semantic_factory=lambda request: one_claim(
            request, predicate="residence_city", value="ГородА"
        ),
    )
    _turn(city_a, "Я живу в ГородА.", "city-a-source")
    city_b = _probe(
        migrated_database,
        counterparty="bob",
        prefix="city-b",
        day=4,
        semantic_factory=lambda request: one_claim(
            request, predicate="residence_city", value="ГородБ"
        ),
    )
    _turn(city_b, "Я живу в ГородБ.", "city-b-source")
    a_before = city_a.services.semantic_claims.list(scope=city_a.scope, active_only=False)
    a_revisions = _revisions(migrated_database, a_before)
    b_city = next(
        claim
        for claim in city_b.services.semantic_claims.list(scope=city_b.scope)
        if claim.predicate == "residence_city"
    )
    corrected = _probe(
        migrated_database,
        counterparty="bob",
        prefix="city-b-corrected",
        day=5,
        semantic_factory=lambda request: one_claim(
            request, predicate="residence_city", value="ГородВ", corrects_claim_id=b_city.claim_id
        ),
    )
    _turn(corrected, "Исправление: теперь я живу в ГородВ.", "city-b-correction")
    assert city_a.services.semantic_claims.list(scope=city_a.scope, active_only=False) == a_before
    assert _revisions(migrated_database, a_before) == a_revisions
    b_current = corrected.services.semantic_claims.list(scope=corrected.scope)
    assert [claim.value for claim in b_current if claim.predicate == "residence_city"] == ["ГородВ"]


def _revisions(database: Database, claims: tuple[SemanticClaim, ...]) -> tuple[object, ...]:
    with database.engine.connect() as connection:
        return tuple(
            tuple(row)
            for claim in claims
            for row in connection.execute(
                text(
                    "SELECT * FROM semantic_claim_revisions WHERE claim_id=:claim "
                    "ORDER BY claim_version"
                ),
                {"claim": claim.claim_id},
            )
        )


@pytest.mark.parametrize("attack", ["evidence", "correction"])
def test_malicious_foreign_semantic_reference_cannot_write_or_disclose_foreign_state(
    migrated_database: Database, attack: str
) -> None:
    activate(migrated_database)
    alice = _probe(
        migrated_database,
        counterparty="alice",
        prefix="attack-a",
        day=1,
        semantic_factory=lambda request: one_claim(
            request, predicate="residence_city", value=f"{ALICE_MARKER}_CITY"
        ),
    )
    _turn(alice, f"Я живу в {ALICE_MARKER}_CITY.", "attack-a-source")
    a_claims = alice.services.semantic_claims.list(scope=alice.scope, active_only=False)
    a_memory = alice.services.memories.execute(scope=alice.scope)[0]
    a_revisions = _revisions(migrated_database, a_claims)
    bob = _probe(
        migrated_database,
        counterparty="bob",
        prefix="attack-b",
        day=2,
        semantic_factory=None,
    )
    _turn(bob, "Я живу в ГородБ.", "attack-b-source")
    b_memory = bob.services.memories.execute(scope=bob.scope)[0]
    attacker = _probe(
        migrated_database,
        counterparty="bob",
        prefix="attack-former",
        day=3,
        semantic_factory=lambda request: one_claim(
            request,
            predicate="residence_city",
            value="ГородБ",
            evidence_memory_ids=(request.source_memory_id, a_memory.memory_id)
            if attack == "evidence"
            else None,
            corrects_claim_id=a_claims[0].claim_id if attack == "correction" else None,
        ),
    )
    assert attacker.services.process_semantic is not None
    decision = asyncio.run(
        attacker.services.process_semantic.execute(
            b_memory.memory_id, trace_id="trace-attack", scope=attacker.scope
        )
    )
    assert decision.kind.value == "rejected"
    assert attacker.semantic is not None
    assert len(attacker.semantic.requests) == 1
    _no_foreign_request_data(attacker, ALICE_MARKER, _private_handles(migrated_database, "alice"))
    assert alice.services.semantic_claims.list(scope=alice.scope, active_only=False) == a_claims
    assert _revisions(migrated_database, a_claims) == a_revisions
    assert not attacker.services.semantic_claims.list(scope=attacker.scope)


def test_unverified_roots_stay_excluded_from_processing_restart_and_restored_recall(
    migrated_database: Database, project_root: Path, tmp_path: Path
) -> None:
    activate(migrated_database)
    alice = _probe(migrated_database, counterparty="alice", prefix="quarantine-a", day=1)
    _turn(alice, f"Меня зовут {ALICE_MARKER}_UNKNOWN.", "quarantine-a-source")
    memory_id = alice.services.memories.execute(scope=alice.scope)[0].memory_id
    with migrated_database.engine.begin() as connection:
        connection.execute(
            text("UPDATE conversation_sessions SET scope_verified=0 WHERE counterparty_id=:party"),
            {"party": "alice"},
        )
    url = str(migrated_database.engine.url)
    migrated_database.dispose()
    restarted = create_database(url)
    try:
        _assert_quarantine(restarted, memory_id=memory_id, prefix="quarantine-restart", day=2)
        use_cases: WholeStateRecovery = recovery(restarted, project_root)
        archive = tmp_path / "quarantine.state"
        exported = use_cases.export(archive)
        target = tmp_path / "restored-quarantine.db"
        use_cases.restore(archive, target, expected_sha256=exported.archive_sha256)
        restored = create_database(f"sqlite+pysqlite:///{target}")
        try:
            _assert_quarantine(restored, memory_id=memory_id, prefix="quarantine-restored", day=3)
        finally:
            restored.dispose()
    finally:
        restarted.dispose()


def _assert_quarantine(database: Database, *, memory_id: str, prefix: str, day: int) -> ScopeProbe:
    alice = _probe(database, counterparty="alice", prefix=prefix, day=day, create_episode=False)
    assert not alice.services.memories.execute(scope=alice.scope)
    assert not alice.services.semantic_claims.list(scope=alice.scope)
    assert alice.services.index_memories is not None
    indexed = asyncio.run(
        alice.services.index_memories.execute(scope=alice.scope, trace_id=f"trace-index-{prefix}")
    )
    assert indexed.indexed == 0
    assert not alice.embedding.requests
    assert alice.services.backfill_semantic is not None
    backfill = asyncio.run(
        alice.services.backfill_semantic.execute(
            scope=alice.scope, trace_id=f"trace-backfill-{prefix}", limit=20
        )
    )
    assert backfill.considered == 0
    assert alice.services.process_semantic is not None
    with pytest.raises(ValueError, match="semantic_source_ineligible"):
        asyncio.run(
            alice.services.process_semantic.execute(
                memory_id, trace_id="trace-quarantined", scope=alice.scope
            )
        )
    assert alice.semantic is not None
    assert not alice.semantic.requests
    reply = _turn(alice, RECALL, f"request-{prefix}")
    assert not reply.context_manifest.retrieved_memory_ids
    assert not reply.context_manifest.retrieved_semantic_claim_ids
    _no_foreign_request_data(alice, ALICE_MARKER, (memory_id,))
    return alice


def test_own_global_position_survives_partitioning_without_private_source_quotes(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    alice = _probe(
        migrated_database,
        counterparty="alice",
        prefix="own-position-a",
        day=1,
        create_episode=False,
        semantic_factory=None,
    )
    a_reply = _turn(
        alice,
        f"Прозрачность важна, потому что позволяет проверить основания решения. {ALICE_MARKER}.",
        "own-position-a-source",
    )
    bob = _probe(
        migrated_database,
        counterparty="bob",
        prefix="own-position-b",
        day=2,
        create_episode=False,
        semantic_factory=None,
    )
    b_reply = _turn(
        bob,
        "Данные ретроспективы показывают меньше ошибок при открытом обосновании.",
        "own-position-b-source",
    )
    owner_provider = FakePositionProvider()
    owner = former(migrated_database, owner_provider)
    asyncio.run(owner.execute(a_reply.interaction_id, trace_id="trace-own-position-a"))
    decision = asyncio.run(owner.execute(b_reply.interaction_id, trace_id="trace-own-position-b"))
    assert decision.kind.value == "applied"
    position = bob.services.positions.list(identity_id=bob.scope.identity_id)[0]
    assert {edge.source_counterparty_id for edge in position.evidence} == {"alice", "bob"}
    for party in ("alice", "bob"):
        reader = _probe(
            migrated_database,
            counterparty=party,
            prefix=f"own-position-{party}-read",
            day=4,
            create_episode=False,
            semantic_factory=None,
        )
        reply = _turn(
            reader, "Что ты думаешь про прозрачные основания решений?", f"own-read-{party}"
        )
        assert reply.context_manifest.position_context_ids == (position.position_id,)
        section = next(
            message.content
            for message in reader.conversation.requests[0].messages
            if "Canonical Satori epistemic positions" in message.content
        )
        assert position.proposition in section
        assert ALICE_MARKER not in section
        assert all(edge.quote not in section for edge in position.evidence)


def test_own_global_inclination_projection_keeps_topic_and_omits_source_history(
    migrated_database: Database,
) -> None:
    identity = activate(migrated_database).identity.identity_id
    _, _, source_session, _ = _canonical_position_source(migrated_database, identity_id=identity)
    _, source, proposal_id = _create_reflection_source(migrated_database, identity_id=identity)
    outcome_id = "scope-inclination-outcome"
    inclination, evaluation = _applied_inclination(
        identity_id=identity, source=source, source_session_id=source_session, outcome_id=outcome_id
    )
    outcome = ReflectionOutcome(
        outcome_id=outcome_id,
        proposal_id=proposal_id,
        target_policy_version=inclination.policy_version,
        decision=ReflectionOutcomeDecision.ACCEPTED,
        reason_code=evaluation.reason_code,
        target_aggregate_type="satori_inclinations",
        target_aggregate_id=inclination.inclination_id,
        decided_at=inclination.updated_at,
    )
    with SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory) as unit:
        assert unit.positions.record_inclination_reflection_decision(
            outcome,
            evaluation,
            identity_id=identity,
            trace_id="trace-scope-inclination",
            audit_event_id="audit-scope-inclination",
        )
        unit.commit()
    for party in ("alice", "bob"):
        reader = _probe(
            migrated_database,
            counterparty=party,
            prefix=f"own-interest-{party}",
            day=9,
            create_episode=False,
            semantic_factory=None,
        )
        reply = _turn(reader, "Что тебе интересно в архитектуре?", f"own-interest-{party}-read")
        assert reply.context_manifest.inclination_context_ids == (inclination.inclination_id,)
        section = next(
            message.content
            for message in reader.conversation.requests[0].messages
            if "Canonical Satori preference/interest STATE DATA" in message.content
        )
        assert inclination.topic in section
        for forbidden in (
            "source_message_id",
            "source_counterparty_id",
            '"evidence"',
            '"revisions"',
        ):
            assert forbidden not in section
