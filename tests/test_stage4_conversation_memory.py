"""Stage 4 golden, failure, idempotency, and provenance scenarios."""

import asyncio
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import text

from satori.application.conversation.contracts import SatoriReply, TalkInput
from satori.application.conversation.errors import (
    ConversationSessionClosed,
    UnsupportedPastClaim,
)
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.config import Environment, Settings
from satori.core.conversation import (
    ConversationPastClaim,
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ProviderUnavailable,
)
from satori.core.episode import (
    EpisodeEvidenceProposal,
    EpisodeFormationProposal,
    EpisodeFormationProviderResponse,
    EpisodeFormationRequest,
)
from satori.domain.conversation_history import (
    InteractionFailureMetadata,
    InteractionStatus,
    SessionStatus,
)
from satori.domain.initial_self import InitialSelfSnapshot
from satori.domain.memory import EpisodeDecisionKind
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.repositories.conversation import (
    SQLAlchemyConversationHistoryRepository,
)
from satori.infrastructure.persistence.repositories.memory import (
    SQLAlchemyEpisodicMemoryRepository,
)
from satori.infrastructure.seeds.loader import JsonSeedLoader
from satori.observability.logging import bind_trace_id, configure_logging
from tests.fakes import (
    FakeConversationProvider,
    FakeEpisodeFormationProvider,
    FrozenClock,
    SequenceIdGenerator,
)

ACTIVATION_TIME = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
INTERACTION_TIME = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def settings(database_url: str = "sqlite+pysqlite:///:memory:") -> Settings:
    return Settings(environment=Environment.TEST, database_url=database_url)


def activate(database: Database) -> InitialSelfSnapshot:
    services = build_initial_self_services(
        database,
        clock=FrozenClock(ACTIVATION_TIME),
        id_generator=SequenceIdGenerator("identity-stage4", "audit-activation"),
    )
    return services.activate.execute(
        JsonSeedLoader().load_canonical(),
        trace_id="trace-activation",
    )


def id_sequence(prefix: str = "record") -> SequenceIdGenerator:
    return SequenceIdGenerator(*(f"{prefix}-{index}" for index in range(1, 50)))


def conversation_provider(
    text_value: str = "Это важный рубеж. Ты довёл проект до первого запуска.",
    *,
    claims: tuple[ConversationPastClaim, ...] = (),
) -> FakeConversationProvider:
    return FakeConversationProvider(
        response=ConversationProviderResponse(
            text=text_value,
            provider="fake-conversation",
            model="fixture-conversation",
            finish_status="stop",
            declared_past_claims=claims,
        )
    )


class ConcurrentConversationProvider(FakeConversationProvider):
    """Release two pending retries together with deliberately different drafts."""

    def __init__(self) -> None:
        super().__init__(response=ConversationProviderResponse("unused", "fake", "model", "stop"))
        self._both_started = asyncio.Event()

    async def generate(
        self,
        request: ConversationProviderRequest,
        /,
    ) -> ConversationProviderResponse:
        self.requests.append(request)
        request_number = len(self.requests)
        if request_number == 2:
            self._both_started.set()
        await self._both_started.wait()
        return ConversationProviderResponse(
            text=f"Concurrent reply {request_number}",
            provider="fake-conversation",
            model="fixture-conversation",
            finish_status="stop",
        )


def skip_episode_provider() -> FakeEpisodeFormationProvider:
    return FakeEpisodeFormationProvider(
        response=EpisodeFormationProviderResponse(
            proposal=EpisodeFormationProposal(1, False, None, None, None, ()),
            provider="fake-episode",
            model="fixture-episode",
            formation_method="fixture.v1",
        )
    )


def meaningful_episode_provider(
    *,
    quote: str = "Я сегодня впервые запустил свой проект и очень рад.",
) -> FakeEpisodeFormationProvider:
    def respond(request: EpisodeFormationRequest) -> EpisodeFormationProviderResponse:
        user_message = next(message for message in request.messages if message.role.value == "user")
        return EpisodeFormationProviderResponse(
            proposal=EpisodeFormationProposal(
                schema_version=1,
                should_create=True,
                summary="Пользователь впервые запустил свой проект и был рад этому.",
                importance=0.82,
                confidence=0.95,
                evidence=(
                    EpisodeEvidenceProposal(
                        message_id=user_message.message_id,
                        quote=quote,
                    ),
                ),
            ),
            provider="fake-episode",
            model="fixture-episode",
            formation_method="fixture.v1",
        )

    return FakeEpisodeFormationProvider(response_factory=respond)


def build_services(
    database: Database,
    conversation: FakeConversationProvider,
    episode: FakeEpisodeFormationProvider,
    *,
    ids: SequenceIdGenerator | None = None,
) -> ConversationServices:
    return build_conversation_services(
        database,
        build_initial_self_services(database),
        conversation,
        episode,
        settings(),
        clock=FrozenClock(INTERACTION_TIME),
        id_generator=ids or id_sequence(),
    )


def run_talk(
    services: ConversationServices,
    *,
    text_value: str = "Я сегодня впервые запустил свой проект и очень рад.",
    request_id: str = "request-stage4",
    session_id: str | None = None,
) -> SatoriReply:
    async def execute() -> SatoriReply:
        reply = await services.talk.execute(
            TalkInput(
                user_text=text_value,
                trace_id="trace-stage4",
                client_request_id=request_id,
                session_id=session_id,
            )
        )
        if not reply.replayed:
            await services.post_response.execute(reply.interaction_id, trace_id="trace-stage4")
        return reply

    return asyncio.run(execute())


def test_golden_history_and_episode_survive_full_restart(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Canonical Stage 4 scenario preserves raw dialogue and source-grounded episode."""

    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini")
    first_database = create_database(sqlite_url)
    before = activate(first_database)
    conversation = conversation_provider()
    episode = meaningful_episode_provider()
    first_services = build_services(first_database, conversation, episode)

    reply = run_talk(first_services)
    first_history = first_services.history.execute()
    first_memories = first_services.memories.execute()
    after = build_initial_self_services(first_database).get_self.execute()
    first_database.dispose()

    assert after == before
    assert reply.text == "Это важный рубеж. Ты довёл проект до первого запуска."
    assert len(first_history.sessions) == 1
    assert first_history.sessions[0].status is SessionStatus.CLOSED
    assert len(first_history.interactions) == 1
    interaction = first_history.interactions[0]
    assert interaction.status is InteractionStatus.COMPLETED
    assert interaction.user_message.content == "Я сегодня впервые запустил свой проект и очень рад."
    assert interaction.assistant_message is not None
    assert interaction.assistant_message.content == reply.text
    assert len(first_memories) == 1
    assert first_memories[0].source_interaction_id == interaction.interaction_id
    assert first_memories[0].evidence[0].source_message_id == interaction.user_message.message_id
    assert first_memories[0].evidence[0].quote == interaction.user_message.content
    request_messages = conversation.requests[0].messages
    assert request_messages[0].role.value == "system"
    assert request_messages[-1].role.value == "user"
    assert request_messages[-1].content == interaction.user_message.content
    assert (
        sum(
            "Trusted current-turn presence Сатори" in message.content
            for message in request_messages
        )
        == 1
    )
    assert all(
        interaction.assistant_message.content not in message.content for message in request_messages
    )

    second_database = create_database(sqlite_url)
    try:
        second_services = build_services(
            second_database,
            conversation_provider("unused"),
            skip_episode_provider(),
            ids=id_sequence("restart"),
        )
        assert second_services.history.execute() == first_history
        assert second_services.memories.execute() == first_memories
        assert build_initial_self_services(second_database).get_self.execute() == before
    finally:
        second_database.dispose()


def test_trivial_interaction_persists_but_creates_no_episode(
    migrated_database: Database,
) -> None:
    """History and memory remain distinct when formation selects skip."""

    activate(migrated_database)
    services = build_services(
        migrated_database,
        conversation_provider("Пожалуйста."),
        skip_episode_provider(),
    )

    run_talk(services, text_value="Спасибо")

    assert len(services.history.execute().interactions) == 1
    assert services.memories.execute() == ()
    with migrated_database.engine.connect() as connection:
        kind = connection.execute(text("SELECT kind FROM episode_formation_decisions")).scalar_one()
    assert kind == EpisodeDecisionKind.SKIPPED.value


def test_episode_failure_keeps_history_and_explicit_retry_retries_only_projection(
    migrated_database: Database,
) -> None:
    """A failed derived projection never erases or regenerates canonical dialogue."""

    activate(migrated_database)
    first_conversation = conversation_provider()
    failing_episode = FakeEpisodeFormationProvider(error=RuntimeError("extractor offline"))
    first_services = build_services(migrated_database, first_conversation, failing_episode)

    first_reply = run_talk(first_services)

    assert first_reply.text
    assert first_services.history.execute().interactions[0].status is InteractionStatus.COMPLETED
    assert first_services.memories.execute() == ()

    replay_conversation = conversation_provider("must not be used")
    retry_episode = meaningful_episode_provider()
    retry_services = build_services(
        migrated_database,
        replay_conversation,
        retry_episode,
        ids=id_sequence("retry"),
    )
    retry_report = asyncio.run(
        retry_services.post_response.execute(
            first_reply.interaction_id,
            trace_id="trace-stage4-retry",
        )
    )
    replay_reply = run_talk(retry_services)

    assert replay_reply.text == first_reply.text
    assert replay_reply.interaction_id == first_reply.interaction_id
    assert replay_conversation.requests == []
    assert retry_report.succeeded
    assert len(retry_episode.requests) == 1
    assert len(retry_services.memories.execute()) == 1


def test_replaying_completed_interaction_creates_no_duplicate_history_or_memory(
    migrated_database: Database,
) -> None:
    """Both canonical finalize and derived formation reuse their stable decisions."""

    activate(migrated_database)
    conversation = conversation_provider()
    episode = meaningful_episode_provider()
    services = build_services(migrated_database, conversation, episode)

    first = run_talk(services)
    second = run_talk(services)

    assert second == first
    assert len(conversation.requests) == 1
    assert len(episode.requests) == 1
    assert len(services.history.execute().interactions) == 1
    assert len(services.memories.execute()) == 1


def test_concurrent_pending_replays_return_the_same_canonical_reply(
    migrated_database: Database,
) -> None:
    """A losing concurrent generation cannot escape after another retry commits first."""

    activate(migrated_database)
    conversation = ConcurrentConversationProvider()
    services = build_services(
        migrated_database,
        conversation,
        skip_episode_provider(),
    )
    command = TalkInput(
        user_text="Один idempotent turn",
        trace_id="trace-concurrent-retry",
        client_request_id="request-concurrent-retry",
    )

    async def run_concurrently() -> tuple[SatoriReply, SatoriReply]:
        first, second = await asyncio.gather(
            services.talk.execute(command),
            services.talk.execute(command),
        )
        return first, second

    first, second = asyncio.run(run_concurrently())
    interaction = services.history.execute().interactions[0]

    assert len(conversation.requests) == 2
    assert first == second
    assert interaction.assistant_message is not None
    assert first.text == interaction.assistant_message.content
    assert len(services.history.execute().interactions) == 1


def test_completed_replay_does_not_depend_on_current_context_budget(
    migrated_database: Database,
) -> None:
    """Recovery returns stored reply before rebuilding a now-incompatible provider context."""

    activate(migrated_database)
    first_services = build_services(
        migrated_database,
        conversation_provider("Committed reply."),
        skip_episode_provider(),
    )
    first = run_talk(first_services, text_value="Stable input", request_id="stable-request")
    replay_conversation = conversation_provider("must not run")
    replay_episode = skip_episode_provider()
    constrained_settings = Settings(
        environment=Environment.TEST,
        database_url="sqlite+pysqlite:///:memory:",
        conversation_max_context_chars=1000,
    )
    replay_services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        replay_conversation,
        replay_episode,
        constrained_settings,
        clock=FrozenClock(INTERACTION_TIME),
        id_generator=id_sequence("budget-replay"),
    )

    second = run_talk(
        replay_services,
        text_value="Stable input",
        request_id="stable-request",
    )

    assert second == first
    assert replay_conversation.requests == []
    assert replay_episode.requests == []


def test_unsupported_episode_summary_is_rejected_but_history_is_valid(
    migrated_database: Database,
) -> None:
    """A nonexistent source quote cannot ground a durable episodic claim."""

    activate(migrated_database)
    episode = meaningful_episode_provider(quote="этого текста в сообщении нет")
    services = build_services(migrated_database, conversation_provider(), episode)

    run_talk(services)

    assert services.history.execute().interactions[0].status is InteractionStatus.COMPLETED
    assert services.memories.execute() == ()
    with migrated_database.engine.connect() as connection:
        decision = (
            connection.execute(text("SELECT kind, reason_code FROM episode_formation_decisions"))
            .mappings()
            .one()
        )
    assert dict(decision) == {
        "kind": EpisodeDecisionKind.REJECTED.value,
        "reason_code": "evidence_quote_not_grounded",
    }


def test_generated_assistant_output_cannot_be_episode_evidence(
    migrated_database: Database,
) -> None:
    """A model cannot make its own conversation draft self-confirming evidence."""

    activate(migrated_database)

    def respond(request: EpisodeFormationRequest) -> EpisodeFormationProviderResponse:
        assistant = next(
            message for message in request.messages if message.role.value == "assistant"
        )
        return EpisodeFormationProviderResponse(
            proposal=EpisodeFormationProposal(
                schema_version=1,
                should_create=True,
                summary="Сатори объявила вымышленный внешний факт.",
                importance=0.9,
                confidence=0.9,
                evidence=(
                    EpisodeEvidenceProposal(
                        message_id=assistant.message_id,
                        quote=assistant.content,
                    ),
                ),
            ),
            provider="hostile-episode",
            model="fixture",
            formation_method="hostile.v1",
        )

    episode = FakeEpisodeFormationProvider(response_factory=respond)
    services = build_services(migrated_database, conversation_provider(), episode)

    run_talk(services)

    assert services.memories.execute() == ()
    with migrated_database.engine.connect() as connection:
        reason = connection.execute(
            text("SELECT reason_code FROM episode_formation_decisions")
        ).scalar_one()
    assert reason == "assistant_output_not_evidence"


def test_declared_past_claim_without_retrieved_evidence_is_not_committed(
    migrated_database: Database,
) -> None:
    """Stage 4 storage does not silently enable Stage 5 recall."""

    activate(migrated_database)
    conversation = conversation_provider(
        "Я помню это.",
        claims=(ConversationPastClaim(("memory-not-in-context",)),),
    )
    services = build_services(migrated_database, conversation, skip_episode_provider())

    with pytest.raises(UnsupportedPastClaim):
        run_talk(services)

    interaction = services.history.execute().interactions[0]
    assert interaction.status is InteractionStatus.FAILED
    assert interaction.assistant_message is None
    assert services.memories.execute() == ()


def test_provider_failure_diagnostic_is_durable_and_explicit_retry_clears_it(
    migrated_database: Database,
) -> None:
    """A failed call is not auto-retried; an explicit replay reuses and completes its row."""

    activate(migrated_database)
    failed_provider = FakeConversationProvider(
        error=ProviderUnavailable(
            "fixture-provider",
            "fixture-model",
            "private transport detail",
            reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
        )
    )
    failed_services = build_services(
        migrated_database,
        failed_provider,
        skip_episode_provider(),
    )

    with pytest.raises(ProviderUnavailable):
        run_talk(failed_services, text_value="Stable input", request_id="retryable-request")

    failed = failed_services.history.execute().interactions[0]
    assert len(failed_provider.requests) == 1
    assert failed.status is InteractionStatus.FAILED
    assert failed.failure == InteractionFailureMetadata(
        kind="ProviderUnavailable",
        reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
        provider="fixture-provider",
        model="fixture-model",
    )

    recovered_provider = conversation_provider("Recovered reply.")
    recovered_services = build_services(
        migrated_database,
        recovered_provider,
        skip_episode_provider(),
        ids=id_sequence("explicit-retry"),
    )
    reply = run_talk(
        recovered_services,
        text_value="Stable input",
        request_id="retryable-request",
    )

    completed = recovered_services.history.execute().interactions[0]
    assert len(recovered_provider.requests) == 1
    assert reply.interaction_id == failed.interaction_id
    assert completed.status is InteractionStatus.COMPLETED
    assert completed.failure is None
    assert completed.provider_metadata is not None
    assert completed.provider_metadata.provider == "fake-conversation"


def test_atomic_finalize_failure_leaves_no_completed_half_pair(
    migrated_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reply staging and completed status roll back together before delivery."""

    activate(migrated_database)
    original = SQLAlchemyConversationHistoryRepository.complete_interaction

    def complete_then_fail(
        repository: SQLAlchemyConversationHistoryRepository,
        *args: object,
        **kwargs: object,
    ) -> object:
        original(repository, *args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("simulated finalize failure")

    monkeypatch.setattr(
        SQLAlchemyConversationHistoryRepository,
        "complete_interaction",
        complete_then_fail,
    )
    episode = skip_episode_provider()
    services = build_services(migrated_database, conversation_provider(), episode)

    with pytest.raises(RuntimeError, match="simulated finalize failure"):
        run_talk(services)

    interaction = services.history.execute().interactions[0]
    assert interaction.status is InteractionStatus.PENDING
    assert interaction.assistant_message is None
    assert episode.requests == []


def test_memory_commit_failure_rolls_back_projection_but_not_history(
    migrated_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Memory, evidence, decision, and audit are one secondary transaction."""

    activate(migrated_database)
    original = SQLAlchemyEpisodicMemoryRepository.record_decision

    def record_then_fail(
        repository: SQLAlchemyEpisodicMemoryRepository,
        *args: object,
        **kwargs: object,
    ) -> bool:
        assert original(repository, *args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("simulated memory commit failure")

    monkeypatch.setattr(
        SQLAlchemyEpisodicMemoryRepository,
        "record_decision",
        record_then_fail,
    )
    services = build_services(
        migrated_database,
        conversation_provider(),
        meaningful_episode_provider(),
    )

    reply = run_talk(services)

    assert reply.text
    assert services.history.execute().interactions[0].status is InteractionStatus.COMPLETED
    assert services.memories.execute() == ()
    with migrated_database.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM memory_evidence")).scalar_one() == 0
        assert (
            connection.execute(
                text("SELECT count(*) FROM episode_formation_decisions")
            ).scalar_one()
            == 0
        )


def test_explicit_session_orders_turns_and_close_blocks_new_interactions(
    migrated_database: Database,
) -> None:
    """Explicit sessions span turns and expose only bounded canonical recent pairs."""

    activate(migrated_database)
    conversation = conversation_provider("Ответ.")
    episode = skip_episode_provider()
    services = build_services(migrated_database, conversation, episode)
    session = services.start_session.execute()

    run_talk(
        services, text_value="Первый", request_id="request-first", session_id=session.session_id
    )
    second = run_talk(
        services, text_value="Второй", request_id="request-second", session_id=session.session_id
    )
    replayed_second = run_talk(
        services, text_value="Второй", request_id="request-second", session_id=session.session_id
    )
    closed = services.close_session.execute(session.session_id)
    history = services.history.execute(session_id=session.session_id)

    assert closed.status is SessionStatus.CLOSED
    assert replayed_second == second
    assert replayed_second.replayed
    assert [item.user_message.content for item in history.interactions] == ["Первый", "Второй"]
    assert len(conversation.requests) == 2
    first_messages = conversation.requests[0].messages
    second_messages = conversation.requests[1].messages
    assert first_messages[-1].role.value == "user"
    assert first_messages[-1].content == "Первый"
    assert not any(message.role.value == "assistant" for message in first_messages)
    previous_user_index = next(
        index
        for index, message in enumerate(second_messages)
        if message.role.value == "user" and message.content == "Первый"
    )
    assert second_messages[previous_user_index + 1].role.value == "assistant"
    assert second_messages[previous_user_index + 1].content == "Ответ."
    assert second_messages[-1].role.value == "user"
    assert second_messages[-1].content == "Второй"
    assert not any(
        "Trusted transient dialogue-coherence signals" in message.content
        for message in second_messages
    )

    with pytest.raises(ConversationSessionClosed):
        run_talk(
            services,
            text_value="Третий",
            request_id="request-third",
            session_id=session.session_id,
        )
    assert len(conversation.requests) == 2


def test_stage4_logs_exclude_raw_history_episode_summary_and_evidence(
    migrated_database: Database,
) -> None:
    """Durable sensitive content is not duplicated into normal structured logs."""

    activate(migrated_database)
    stream = StringIO()
    configure_logging("INFO", stream=stream)
    private_input = "Я сегодня впервые запустил свой проект и очень рад."
    private_reply = "Это важный рубеж. Ты довёл проект до первого запуска."
    private_summary = "Пользователь впервые запустил свой проект и был рад этому."
    services = build_services(
        migrated_database,
        conversation_provider(private_reply),
        meaningful_episode_provider(quote=private_input),
    )

    with bind_trace_id("trace-stage4-private"):
        run_talk(services, text_value=private_input, request_id="request-private")

    rendered = stream.getvalue()
    assert private_input not in rendered
    assert private_reply not in rendered
    assert private_summary not in rendered
    assert "interaction_persisted" in rendered
    assert "episode_created" in rendered
