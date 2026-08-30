"""Stage 6 semantic formation, evidence, correction, recall, and failure evals."""

import asyncio
import json
from collections.abc import Callable
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import text

from satori.__main__ import main
from satori.application.conversation.contracts import TalkInput
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.config import LogLevel
from satori.core.conversation import ConversationPastClaim, ConversationProviderResponse
from satori.core.episode import (
    EpisodeEvidenceProposal,
    EpisodeFormationProposal,
    EpisodeFormationProviderResponse,
    EpisodeFormationRequest,
)
from satori.core.semantic import (
    SemanticClaimKind,
    SemanticClaimProposal,
    SemanticFormationProposal,
    SemanticFormationProviderResponse,
    SemanticFormationRequest,
    SemanticValueKind,
)
from satori.domain.semantic_memory import (
    SemanticClaimStatus,
    SemanticEvidenceSourceKind,
    SemanticFormationDecision,
)
from satori.infrastructure.persistence.database import Database
from satori.observability.logging import configure_logging
from tests.fakes import (
    FakeConversationProvider,
    FakeEmbeddingProvider,
    FakeEpisodeFormationProvider,
    FakeSemanticFormationProvider,
    FrozenClock,
)
from tests.test_stage4_conversation_memory import (
    INTERACTION_TIME,
    activate,
    conversation_provider,
    id_sequence,
    settings,
    skip_episode_provider,
)


def episode_from_user() -> FakeEpisodeFormationProvider:
    """Create a source episode whose evidence is the complete current user statement."""

    def respond(request: EpisodeFormationRequest) -> EpisodeFormationProviderResponse:
        user = next(message for message in request.messages if message.role.value == "user")
        return EpisodeFormationProviderResponse(
            proposal=EpisodeFormationProposal(
                1,
                True,
                user.content,
                0.8,
                0.95,
                (EpisodeEvidenceProposal(user.message_id, user.content),),
            ),
            provider="fake-episode",
            model="fixture",
            formation_method="fixture.episode.v1",
        )

    return FakeEpisodeFormationProvider(response_factory=respond)


def semantic_provider(
    factory: Callable[[SemanticFormationRequest], SemanticFormationProposal],
) -> FakeSemanticFormationProvider:
    def respond(request: SemanticFormationRequest) -> SemanticFormationProviderResponse:
        return SemanticFormationProviderResponse(
            proposal=factory(request),
            provider="fake-semantic",
            model="fixture",
            formation_method="fixture.semantic.v1",
        )

    return FakeSemanticFormationProvider(response_factory=respond)


class ConcurrentSemanticProvider:
    """Release two formation attempts only after both reached the provider boundary."""

    def __init__(self) -> None:
        self.requests: list[SemanticFormationRequest] = []
        self._both_started = asyncio.Event()

    async def generate_structured(
        self, request: SemanticFormationRequest, /
    ) -> SemanticFormationProviderResponse:
        self.requests.append(request)
        if len(self.requests) == 2:
            self._both_started.set()
        await self._both_started.wait()
        return SemanticFormationProviderResponse(
            proposal=one_claim(request, predicate="name", value="Алексей"),
            provider="fake-semantic",
            model="concurrent",
            formation_method="fixture.semantic.v1",
        )


def one_claim(
    request: SemanticFormationRequest,
    *,
    predicate: str,
    value: str | float | bool,
    value_kind: SemanticValueKind = SemanticValueKind.TEXT,
    claim_kind: SemanticClaimKind = SemanticClaimKind.EXPLICIT_FACT,
    evidence_memory_ids: tuple[str, ...] | None = None,
    polarity: bool = True,
    corrects_claim_id: str | None = None,
) -> SemanticFormationProposal:
    return SemanticFormationProposal(
        1,
        (
            SemanticClaimProposal(
                subject="user",
                predicate=predicate,
                value_kind=value_kind,
                value=value,
                polarity=polarity,
                claim_kind=claim_kind,
                confidence=0.99,
                evidence_memory_ids=evidence_memory_ids or (request.source_memory_id,),
                corrects_claim_id=corrects_claim_id,
            ),
        ),
    )


def services(
    database: Database,
    *,
    semantic: FakeSemanticFormationProvider | None,
    prefix: str,
    day: int = 0,
    episode: FakeEpisodeFormationProvider | None = None,
    conversation: FakeConversationProvider | None = None,
    embedding: FakeEmbeddingProvider | None = None,
) -> ConversationServices:
    return build_conversation_services(
        database,
        build_initial_self_services(database),
        conversation or conversation_provider(),
        episode or episode_from_user(),
        settings(),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=day)),
        id_generator=id_sequence(prefix),
        embedding_provider=embedding,
        semantic_provider=semantic,
    )


def talk(active: ConversationServices, user_text: str, request_id: str) -> None:
    async def execute() -> None:
        reply = await active.talk.execute(TalkInput(user_text, f"trace-{request_id}", request_id))
        if not reply.replayed:
            await active.post_response.execute(
                reply.interaction_id,
                trace_id=f"trace-{request_id}",
            )

    asyncio.run(execute())


def test_explicit_fact_has_typed_identity_confidence_and_full_lineage(
    migrated_database: Database,
) -> None:
    """Golden explicit fact is capped and traces to episode, user message, and interaction."""

    activate(migrated_database)
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    active = services(migrated_database, semantic=provider, prefix="explicit")
    log_stream = StringIO()
    configure_logging(LogLevel.INFO, stream=log_stream)

    talk(active, "Меня зовут Алексей.", "explicit")

    claim = active.semantic_claims.list()[0]
    memory = active.memories.execute()[0]
    assert claim.subject == "user"
    assert claim.predicate == "name"
    assert claim.value == "Алексей"
    assert claim.normalized_value == "алексей"
    assert claim.claim_kind is SemanticClaimKind.EXPLICIT_FACT
    assert claim.confidence == 0.90
    assert claim.status is SemanticClaimStatus.ACTIVE
    assert len(claim.evidence) == 1
    evidence = claim.evidence[0]
    assert evidence.memory_id == memory.memory_id
    assert evidence.memory_evidence_id == memory.evidence[0].evidence_id
    assert evidence.root_message_id == memory.evidence[0].source_message_id
    assert evidence.root_interaction_id == memory.source_interaction_id
    assert evidence.source_kind is SemanticEvidenceSourceKind.EXPLICIT_USER_STATEMENT
    logs = log_stream.getvalue()
    assert "semantic_formation_decided" in logs
    assert "Алексей" not in logs
    assert "Меня зовут" not in logs


def test_retry_and_duplicate_derivation_do_not_inflate_confidence(
    migrated_database: Database,
) -> None:
    """The source/version decision is terminal and does not call the provider twice."""

    activate(migrated_database)
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    active = services(migrated_database, semantic=provider, prefix="retry")
    talk(active, "Меня зовут Алексей.", "retry")
    memory_id = active.memories.execute()[0].memory_id
    before = active.semantic_claims.list()[0]

    assert active.process_semantic is not None
    replay = asyncio.run(active.process_semantic.execute(memory_id, trace_id="trace-replay"))
    after = active.semantic_claims.list()[0]

    assert len(provider.requests) == 1
    assert replay.source_memory_id == memory_id
    assert after.claim_id == before.claim_id
    assert after.aggregate_version == before.aggregate_version
    assert after.confidence == before.confidence
    assert len(after.evidence) == 1


def test_concurrent_same_source_processing_commits_one_terminal_decision(
    migrated_database: Database,
) -> None:
    """A source/version race stores one claim, one evidence root, and one decision."""

    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="race-source")
    talk(upstream, "Меня зовут Алексей.", "race-source")
    memory_id = upstream.memories.execute()[0].memory_id
    provider = ConcurrentSemanticProvider()
    active = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        conversation_provider(),
        skip_episode_provider(),
        settings(),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=1)),
        id_generator=id_sequence("race"),
        semantic_provider=provider,
    )
    processor = active.process_semantic
    assert processor is not None

    async def race() -> tuple[SemanticFormationDecision, SemanticFormationDecision]:
        decisions = await asyncio.gather(
            processor.execute(memory_id, trace_id="race-a"),
            processor.execute(memory_id, trace_id="race-b"),
        )
        return decisions[0], decisions[1]

    first, second = asyncio.run(race())
    assert first.decision_id == second.decision_id
    assert len(provider.requests) == 2
    assert len(active.semantic_claims.list()) == 1
    with migrated_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM semantic_formation_decisions")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(text("SELECT count(*) FROM semantic_claim_evidence")).scalar_one()
            == 1
        )


def test_overgeneralization_assistant_hallucination_and_skip_are_conservative(
    migrated_database: Database,
) -> None:
    """One anecdote cannot infer a fact, and values absent from user evidence are rejected."""

    activate(migrated_database)
    inferred = semantic_provider(
        lambda request: one_claim(
            request,
            predicate="likes",
            value="кофе",
            claim_kind=SemanticClaimKind.INFERRED_FACT,
        )
    )
    first = services(migrated_database, semantic=inferred, prefix="overgeneralize")
    talk(first, "Сегодня я выпил кофе.", "overgeneralize")
    assert first.semantic_claims.list() == ()

    hallucinated = semantic_provider(
        lambda request: one_claim(request, predicate="residence_city", value="Москва")
    )
    second = services(migrated_database, semantic=hallucinated, prefix="hallucinated", day=1)
    talk(second, "Привет.", "hallucinated")
    source_request = hallucinated.requests[0]
    source_memory = next(
        memory
        for memory in source_request.memories
        if memory.memory_id == source_request.source_memory_id
    )
    assert source_memory.evidence[0].quote == "Привет."
    assert second.semantic_claims.list() == ()

    skipped = semantic_provider(lambda request: SemanticFormationProposal(1, ()))
    third = services(migrated_database, semantic=skipped, prefix="skip", day=2)
    talk(third, "Сегодня идёт дождь.", "skip")
    assert third.semantic_claims.list() == ()

    with migrated_database.engine.connect() as connection:
        kinds = connection.execute(
            text("SELECT kind FROM semantic_formation_decisions ORDER BY decided_at")
        ).scalars()
        assert list(kinds) == ["rejected", "rejected", "skipped"]


def test_unknown_predicate_is_rejected_and_negation_remains_explicit(
    migrated_database: Database,
) -> None:
    """The v1 registry is closed and negative attributed statements keep polarity."""

    activate(migrated_database)
    unknown = semantic_provider(
        lambda request: one_claim(request, predicate="favorite_color", value="синий")
    )
    first = services(migrated_database, semantic=unknown, prefix="unknown")
    talk(first, "Мой любимый цвет синий.", "unknown")
    assert first.semantic_claims.list() == ()

    negative = semantic_provider(
        lambda request: one_claim(
            request,
            predicate="likes",
            value="кофе",
            claim_kind=SemanticClaimKind.ATTRIBUTED_STATEMENT,
            polarity=False,
        )
    )
    second = services(migrated_database, semantic=negative, prefix="negative", day=1)
    talk(second, "Мне не нравится кофе.", "negative")
    claim = second.semantic_claims.list()[0]
    assert claim.polarity is False
    assert claim.claim_kind is SemanticClaimKind.ATTRIBUTED_STATEMENT
    assert claim.confidence == 0.85


def test_independent_explicit_evidence_merges_once_and_strengthens(
    migrated_database: Database,
) -> None:
    """A second root interaction merges into one identity and raises the v1 cap to 0.92."""

    activate(migrated_database)
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="works_on_project", value="SATORI")
    )
    first = services(migrated_database, semantic=provider, prefix="merge-a")
    talk(first, "Я работаю над проектом SATORI.", "merge-a")
    second = services(migrated_database, semantic=provider, prefix="merge-b", day=1)
    talk(second, "Мой текущий проект — SATORI.", "merge-b")

    claims = second.semantic_claims.list()
    assert len(claims) == 1
    assert claims[0].aggregate_version == 2
    assert claims[0].confidence == 0.92
    assert len(claims[0].evidence) == 2
    assert len({item.root_interaction_id for item in claims[0].evidence}) == 2


def test_single_value_contradiction_is_superseded_by_new_explicit_correction(
    migrated_database: Database,
) -> None:
    """A later explicit single-valued fact closes old validity without deleting history."""

    activate(migrated_database)
    correction_target: list[str] = []

    def factory(request: SemanticFormationRequest) -> SemanticFormationProposal:
        source = next(
            item for item in request.memories if item.memory_id == request.source_memory_id
        )
        value = "Санкт-Петербург" if "Санкт-Петербург" in source.evidence[0].quote else "Москва"
        return one_claim(
            request,
            predicate="residence_city",
            value=value,
            corrects_claim_id=correction_target[0] if correction_target else None,
        )

    provider = semantic_provider(factory)
    first = services(migrated_database, semantic=provider, prefix="city-a")
    talk(first, "Мой город — Москва.", "city-a")
    old_id = first.semantic_claims.list()[0].claim_id
    correction_target.append(old_id)
    second = services(migrated_database, semantic=provider, prefix="city-b", day=1)
    talk(second, "Исправление: я живу в Санкт-Петербург.", "city-b")

    active = second.semantic_claims.list()
    historical = second.semantic_claims.list(active_only=False)
    old = next(claim for claim in historical if claim.claim_id == old_id)
    assert len(active) == 1
    assert active[0].value == "Санкт-Петербург"
    assert old.status is SemanticClaimStatus.SUPERSEDED
    assert old.superseded_by_claim_id == active[0].claim_id
    assert old.valid_until == active[0].valid_from
    inspected = second.semantic_claims.inspect(old.claim_id)
    assert inspected is not None
    assert inspected[1][-1].kind.value == "superseded"
    assert inspected[1][-1].reason_code == "explicit_correction"


def test_hypothesis_and_inference_keep_labels_and_yield_to_stronger_evidence(
    migrated_database: Database,
) -> None:
    """Two-source hypothesis/inference transitions preserve labels before explicit support."""

    activate(migrated_database)

    def factory(request: SemanticFormationRequest) -> SemanticFormationProposal:
        matching = tuple(
            memory.memory_id for memory in request.memories if "Python" in memory.evidence[0].quote
        )
        source_quote = (
            next(
                memory
                for memory in request.memories
                if memory.memory_id == request.source_memory_id
            )
            .evidence[0]
            .quote
        )
        if "точно" in source_quote:
            return one_claim(request, predicate="studies_topic", value="Python")
        if "уверенно" in source_quote:
            return one_claim(
                request,
                predicate="studies_topic",
                value="Python",
                claim_kind=SemanticClaimKind.INFERRED_FACT,
                evidence_memory_ids=matching[:2],
            )
        if len(matching) >= 2:
            return one_claim(
                request,
                predicate="studies_topic",
                value="Python",
                claim_kind=SemanticClaimKind.HYPOTHESIS,
                evidence_memory_ids=matching[:2],
            )
        return SemanticFormationProposal(1, ())

    provider = semantic_provider(factory)
    talk(
        services(migrated_database, semantic=provider, prefix="infer-a"),
        "Изучаю Python.",
        "infer-a",
    )
    second = services(migrated_database, semantic=provider, prefix="infer-b", day=1)
    talk(second, "Снова занимаюсь Python.", "infer-b")
    hypothesis = second.semantic_claims.list()[0]
    assert hypothesis.claim_kind is SemanticClaimKind.HYPOTHESIS
    assert hypothesis.confidence == 0.50

    third = services(migrated_database, semantic=provider, prefix="infer-c", day=2)
    talk(third, "Я уверенно изучаю Python.", "infer-c")
    inferred = third.semantic_claims.list()[0]
    assert inferred.claim_kind is SemanticClaimKind.INFERRED_FACT
    assert inferred.confidence == 0.65
    assert len({item.root_interaction_id for item in inferred.evidence}) == 2
    all_after_inference = third.semantic_claims.list(active_only=False)
    preserved_hypothesis = next(
        claim for claim in all_after_inference if claim.claim_id == hypothesis.claim_id
    )
    assert preserved_hypothesis.claim_kind is SemanticClaimKind.HYPOTHESIS
    assert preserved_hypothesis.status is SemanticClaimStatus.SUPERSEDED

    fourth = services(migrated_database, semantic=provider, prefix="infer-d", day=3)
    talk(fourth, "Я точно изучаю Python.", "infer-d")
    active = fourth.semantic_claims.list()
    all_claims = fourth.semantic_claims.list(active_only=False)
    assert len(active) == 1
    assert active[0].claim_kind is SemanticClaimKind.EXPLICIT_FACT
    preserved = next(claim for claim in all_claims if claim.claim_id == inferred.claim_id)
    assert preserved.claim_kind is SemanticClaimKind.INFERRED_FACT
    assert preserved.status is SemanticClaimStatus.SUPERSEDED


def test_competing_inferences_become_disputed_and_leave_active_recall(
    migrated_database: Database,
) -> None:
    """Contradictory single-valued inferences are retained but neither stays active."""

    activate(migrated_database)

    def factory(request: SemanticFormationRequest) -> SemanticFormationProposal:
        source = next(
            item for item in request.memories if item.memory_id == request.source_memory_id
        )
        value = "дизайнер" if "дизайнер" in source.evidence[0].quote else "инженер"
        matching = tuple(
            memory.memory_id
            for memory in request.memories
            if value in memory.evidence[0].quote.casefold()
        )
        if len(matching) < 2:
            return SemanticFormationProposal(1, ())
        return one_claim(
            request,
            predicate="occupation",
            value=value,
            claim_kind=SemanticClaimKind.INFERRED_FACT,
            evidence_memory_ids=matching,
        )

    provider = semantic_provider(factory)
    utterances = (
        "Я работаю как инженер.",
        "Моя работа — инженер.",
        "Иногда я работаю как дизайнер.",
        "Моя работа сейчас дизайнер.",
    )
    latest: ConversationServices | None = None
    for day, utterance in enumerate(utterances):
        latest = services(
            migrated_database,
            semantic=provider,
            prefix=f"dispute-{day}",
            day=day,
        )
        talk(latest, utterance, f"dispute-{day}")
    assert latest is not None
    assert latest.semantic_claims.list() == ()
    all_claims = latest.semantic_claims.list(active_only=False)
    assert len(all_claims) == 2
    assert {claim.status for claim in all_claims} == {SemanticClaimStatus.DISPUTED}


def test_backfill_is_restartable_and_partial_failure_preserves_upstream_state(
    migrated_database: Database,
) -> None:
    """Missing decisions retry; semantic failure never rolls back raw history or episodes."""

    activate(migrated_database)
    upstream = services(migrated_database, semantic=None, prefix="upstream")
    talk(upstream, "Меня зовут Алексей.", "upstream")
    assert len(upstream.history.execute().interactions) == 1
    assert len(upstream.memories.execute()) == 1

    failure = FakeSemanticFormationProvider(error=RuntimeError("semantic offline"))
    failed = services(
        migrated_database,
        semantic=failure,
        prefix="failed",
        day=1,
        episode=skip_episode_provider(),
    )
    assert failed.backfill_semantic is not None
    first_report = asyncio.run(failed.backfill_semantic.execute(trace_id="failure", limit=10))
    assert first_report.failed == 1
    assert len(failed.history.execute().interactions) == 1
    assert len(failed.memories.execute()) == 1

    healthy_provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    healthy = services(
        migrated_database,
        semantic=healthy_provider,
        prefix="healthy",
        day=2,
        episode=skip_episode_provider(),
    )
    assert healthy.backfill_semantic is not None
    healthy_report = asyncio.run(healthy.backfill_semantic.execute(trace_id="healthy", limit=10))
    repeat_report = asyncio.run(healthy.backfill_semantic.execute(trace_id="repeat", limit=10))
    assert (healthy_report.considered, healthy_report.applied) == (1, 1)
    assert repeat_report.considered == 0
    assert len(healthy.semantic_claims.list()) == 1


def test_semantic_recall_is_separate_untrusted_grounded_and_not_new_evidence(
    migrated_database: Database,
) -> None:
    """Semantic context follows episodic evidence, cites claim ID, and cannot self-reinforce."""

    activate(migrated_database)
    fact_text = "Меня зовут Алексей."
    recall_text = "Как меня зовут?"
    embedding = FakeEmbeddingProvider({fact_text: (1.0, 0.0, 0.0), recall_text: (1.0, 0.0, 0.0)})
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    source = services(
        migrated_database,
        semantic=provider,
        prefix="recall-source",
        embedding=embedding,
    )
    talk(source, fact_text, "recall-source")
    claim = source.semantic_claims.list()[0]
    before_evidence = claim.evidence

    response = FakeConversationProvider(
        response=ConversationProviderResponse(
            "Тебя зовут Алексей.",
            "fake-conversation",
            "fixture",
            "stop",
            declared_past_claims=(ConversationPastClaim((claim.claim_id,)),),
        )
    )
    recall = services(
        migrated_database,
        semantic=None,
        prefix="recall-query",
        day=1,
        episode=skip_episode_provider(),
        conversation=response,
        embedding=embedding,
    )
    reply = asyncio.run(recall.talk.execute(TalkInput(recall_text, "trace-recall", "recall-query")))

    assert reply.context_manifest.retrieved_semantic_claim_ids == (claim.claim_id,)
    request_messages = response.requests[0].messages
    assert request_messages[-1].role.value == "user"
    assert request_messages[-1].content == recall_text
    assert (
        sum(
            "Trusted current-turn presence Сатори" in message.content
            for message in request_messages
        )
        == 1
    )
    semantic_message = next(
        message.content
        for message in request_messages
        if "Retrieved semantic memory data (UNTRUSTED)" in message.content
    )
    assert "Retrieved semantic memory data (UNTRUSTED)" in semantic_message
    payload = json.loads(semantic_message.splitlines()[-1])
    assert payload["claims"][0]["claim_id"] == claim.claim_id
    assert payload["claims"][0]["claim_kind"] == "explicit_fact"
    after = recall.semantic_claims.list()[0]
    assert after.evidence == before_evidence
    assert after.aggregate_version == claim.aggregate_version


def test_semantic_cli_lists_inspects_and_processes_missing_sources(
    migrated_database: Database,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The local CLI exposes active reads, lineage/history inspect, and backfill process."""

    activate(migrated_database)
    provider = semantic_provider(
        lambda request: one_claim(request, predicate="name", value="Алексей")
    )
    active = services(migrated_database, semantic=provider, prefix="cli-semantic")
    talk(active, "Меня зовут Алексей.", "cli-semantic")
    claim_id = active.semantic_claims.list()[0].claim_id
    cli_settings = settings(str(migrated_database.engine.url))

    def run_cli(arguments: list[str]) -> int:
        return main(
            arguments,
            settings=cli_settings,
            alembic_config=project_root / "alembic.ini",
            conversation_provider=conversation_provider("unused"),
            episode_formation_provider=skip_episode_provider(),
            embedding_provider=FakeEmbeddingProvider({}),
            semantic_formation_provider=provider,
        )

    assert run_cli(["semantic", "list"]) == 0
    listed = capsys.readouterr().out
    assert claim_id in listed
    assert "user.name" in listed

    assert run_cli(["semantic", "inspect", claim_id]) == 0
    inspected = capsys.readouterr().out
    assert "root_message=" in inspected
    assert "revision=" in inspected

    assert run_cli(["semantic", "process"]) == 0
    processed = capsys.readouterr().out
    assert "considered=0" in processed
