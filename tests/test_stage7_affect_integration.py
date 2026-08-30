"""Stage 7 appraisal, persistence, conversation lifecycle, and developer CLI."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from satori.__main__ import main
from satori.application.affect.contracts import PreparedAffectiveContext
from satori.application.conversation.contracts import SatoriReply, TalkInput
from satori.composition import (
    ConversationServices,
    InitialSelfServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.config import Environment, LogLevel, Settings
from satori.core.affect import (
    AffectiveAppraisalProposal,
    AffectiveAppraisalProviderError,
    AffectiveAppraisalProviderResponse,
)
from satori.core.conversation import (
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ProviderUnavailable,
)
from satori.core.episode import EpisodeFormationProposal, EpisodeFormationProviderResponse
from satori.core.ids import Uuid4Generator
from satori.domain.affect import AffectiveTransition, materialize_affective_state
from satori.domain.conversation_history import (
    InteractionFailureMetadata,
    InteractionProviderMetadata,
    InteractionStatus,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.repositories.affect import (
    SQLAlchemyAffectiveStateRepository,
)
from satori.infrastructure.seeds.loader import JsonSeedLoader
from tests.fakes import (
    FakeAffectiveAppraisalProvider,
    FakeConversationProvider,
    FakeEmbeddingProvider,
    FakeEpisodeFormationProvider,
    FrozenClock,
    SequenceIdGenerator,
)

NOW = datetime(2026, 7, 30, 14, 0, tzinfo=UTC)


class ConcurrentConversationProvider(FakeConversationProvider):
    """Release two same-request generations only after both reached the provider."""

    def __init__(self) -> None:
        super().__init__(response=ConversationProviderResponse("unused", "fake", "fixture", "stop"))
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
            f"Concurrent affect reply {request_number}",
            "fake-conversation",
            "fixture-conversation",
            "stop",
        )


def settings(sqlite_url: str = "sqlite+pysqlite:///:memory:") -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=sqlite_url,
        log_level=LogLevel.WARNING,
    )


def episode_provider() -> FakeEpisodeFormationProvider:
    return FakeEpisodeFormationProvider(
        response=EpisodeFormationProviderResponse(
            proposal=EpisodeFormationProposal(1, False, None, None, None, ()),
            provider="fake-episode",
            model="fixture",
            formation_method="fixture.v1",
        )
    )


def activate(database: Database) -> None:
    services = build_initial_self_services(
        database,
        clock=FrozenClock(NOW),
        id_generator=SequenceIdGenerator("stage7-identity", "stage7-activation-audit"),
    )
    services.activate.execute(JsonSeedLoader().load_canonical(), trace_id="stage7-activation")


def conversation_provider(*, error: Exception | None = None) -> FakeConversationProvider:
    if error is not None:
        return FakeConversationProvider(error=error)
    return FakeConversationProvider(
        response=ConversationProviderResponse(
            "Я услышала тебя.",
            "fake-conversation",
            "fixture-conversation",
            "stop",
        )
    )


def proposal_for(
    interaction_id: str,
    *,
    source_refs: tuple[str, ...] | None = None,
    salience: float = 0.85,
) -> AffectiveAppraisalProviderResponse:
    return AffectiveAppraisalProviderResponse(
        proposal=AffectiveAppraisalProposal(
            schema_version=1,
            pleasantness=0.7,
            activation=0.45,
            novelty=0.3,
            salience=salience,
            uncertainty=0.05,
            curiosity_signal=0.35,
            interest_signal=0.8,
            humor_signal=0.15,
            concern_signal=0.05,
            frustration_signal=0.0,
            confidence_signal=0.5,
            appraisal_confidence=0.9,
            source_refs=source_refs or (interaction_id,),
            reason_codes=("positive_engagement",),
        ),
        provider="fake-appraisal",
        model="fixture-appraisal",
        appraisal_method="fixture.appraisal.v1",
    )


def build(
    database: Database,
    appraisal: FakeAffectiveAppraisalProvider,
    *,
    clock: FrozenClock | None = None,
    conversation: FakeConversationProvider | None = None,
) -> tuple[ConversationServices, FakeConversationProvider, InitialSelfServices]:
    initial_self = build_initial_self_services(database)
    active_conversation = conversation or conversation_provider()
    services = build_conversation_services(
        database,
        initial_self,
        active_conversation,
        episode_provider(),
        settings(),
        clock=clock or FrozenClock(NOW),
        id_generator=Uuid4Generator(),
        appraisal_provider=appraisal,
    )
    return services, active_conversation, initial_self


def run_talk(
    services: ConversationServices,
    *,
    request_id: str = "stage7-request",
    text_value: str = "Спасибо",
) -> SatoriReply:
    return asyncio.run(
        services.talk.execute(
            TalkInput(
                user_text=text_value,
                trace_id="stage7-trace",
                client_request_id=request_id,
            )
        )
    )


def scalar(database: Database, statement: str) -> int:
    with database.engine.connect() as connection:
        return int(connection.execute(text(statement)).scalar_one())


def test_golden_appraisal_is_tentative_then_committed_with_canonical_reply(
    migrated_database: Database,
) -> None:
    """Only the manager-owned accepted proposal changes durable state and mood."""

    activate(migrated_database)
    initial_self = build_initial_self_services(migrated_database).get_self.execute()
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    services, generator, _ = build(migrated_database, appraisal)

    reply = run_talk(services, text_value="Это очень хорошая новость")

    status = services.emotion_status.execute(initial_self.identity.identity_id)
    transitions = services.emotion_history.execute()
    assert reply.context_manifest.emotion_appraisal_status == "applied"
    assert reply.context_manifest.emotion_state_version == 2
    assert reply.context_manifest.mood_state_version == 2
    assert reply.context_manifest.affect_expression_profile == "calm_even"
    assert status.state.state_version == 2
    assert status.state.mood_version == 2
    assert status.state.fast.valence > 0.0
    assert status.state.mood.valence > 0.0
    assert len(transitions) == 1
    assert transitions[0].interaction_id == reply.interaction_id
    assert transitions[0].source_message_id
    assert transitions[0].proposal.source_refs == (reply.interaction_id,)
    assert scalar(migrated_database, "SELECT count(*) FROM affective_transitions") == 1
    assert (
        scalar(
            migrated_database,
            "SELECT count(*) FROM audit_events WHERE event_type = 'emotion.transition_applied'",
        )
        == 1
    )
    assert build_initial_self_services(migrated_database).get_self.execute() == initial_self

    assert len(appraisal.requests) == 1
    assert appraisal.requests[0].user_content == "Это очень хорошая новость"
    assert "Я услышала тебя." not in repr(appraisal.requests[0])
    presence_message = next(
        message.content
        for message in generator.requests[0].messages
        if "Trusted current-turn presence Сатори" in message.content
    )
    assert reply.context_manifest.character_presence_projection_schema_version == 2
    assert "engaged_curiosity:defining" in (
        reply.context_manifest.character_presence_affect_signals
    )
    assert "живое любопытство" in presence_message
    assert "operational move v2" in presence_message
    assert "state_version" not in presence_message
    assert generator.requests[0].messages[-1].content == "Это очень хорошая новость"


def test_same_logical_retry_replays_reply_without_double_appraisal_or_transition(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    services, generator, initial_self = build(migrated_database, appraisal)

    first = run_talk(services, request_id="same-request")
    for _ in range(99):
        assert run_talk(services, request_id="same-request") == first

    identity_id = initial_self.get_self.execute().identity.identity_id
    assert services.emotion_status.execute(identity_id).state.state_version == 2
    assert len(appraisal.requests) == 1
    assert len(generator.requests) == 1
    assert scalar(migrated_database, "SELECT count(*) FROM affective_transitions") == 1
    assert scalar(migrated_database, "SELECT count(*) FROM conversation_messages") == 2


def test_same_phrase_after_prior_event_changes_only_affective_expression_layer(
    migrated_database: Database,
) -> None:
    """State can change expression context without rewriting character or current user data."""

    activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    services, generator, _ = build(migrated_database, appraisal)

    first_reply = run_talk(services, request_id="same-phrase-1", text_value="Расскажи ещё")
    second_reply = run_talk(services, request_id="same-phrase-2", text_value="Расскажи ещё")

    first, second = generator.requests
    assert first.messages[0] == second.messages[0]
    assert first.messages[-1] == second.messages[-1]
    first_presence = next(
        message.content
        for message in first.messages
        if "Trusted current-turn presence Сатори" in message.content
    )
    second_presence = next(
        message.content
        for message in second.messages
        if "Trusted current-turn presence Сатори" in message.content
    )
    assert first_presence != second_presence
    assert first_reply.context_manifest.emotion_state_version == 2
    assert second_reply.context_manifest.emotion_state_version == 3
    assert first_reply.context_manifest.affect_expression_profile == "calm_even"
    assert second_reply.context_manifest.affect_expression_profile == "positive_light"
    assert first_reply.context_manifest.character_presence_affect_signals != (
        second_reply.context_manifest.character_presence_affect_signals
    )
    assert first_reply.context_manifest.character_presence_personality_signals == (
        second_reply.context_manifest.character_presence_personality_signals
    )
    assert first_reply.context_manifest.character_presence_value_signals == (
        second_reply.context_manifest.character_presence_value_signals
    )
    assert first_reply.context_manifest.character_presence_relationship_signals == (
        second_reply.context_manifest.character_presence_relationship_signals
    )
    assert "state_version" not in first_presence
    assert "state_version" not in second_presence


def test_concurrent_same_logical_request_commits_one_affective_transition(
    migrated_database: Database,
) -> None:
    """Both retries may infer, but only the canonical reply can authorize one mutation."""

    activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    generator = ConcurrentConversationProvider()
    services, _, initial_self = build(
        migrated_database,
        appraisal,
        conversation=generator,
    )
    command = TalkInput("One logical affect event", "concurrent-trace", "concurrent-request")

    async def run_concurrently() -> tuple[SatoriReply, SatoriReply]:
        first, second = await asyncio.gather(
            services.talk.execute(command),
            services.talk.execute(command),
        )
        return first, second

    first, second = asyncio.run(run_concurrently())
    identity_id = initial_self.get_self.execute().identity.identity_id

    assert first == second
    assert len(appraisal.requests) == 2
    assert len(generator.requests) == 2
    assert len(services.history.execute().interactions) == 1
    assert len(services.emotion_history.execute()) == 1
    assert services.emotion_status.execute(identity_id).state.state_version == 2


def test_generation_failure_discards_tentative_affect(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    failed_generator = conversation_provider(
        error=ProviderUnavailable(
            "fake-conversation",
            "fixture",
            "offline",
            reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
        )
    )
    services, _, initial_self = build(
        migrated_database,
        appraisal,
        conversation=failed_generator,
    )

    with pytest.raises(ProviderUnavailable):
        run_talk(services)

    identity_id = initial_self.get_self.execute().identity.identity_id
    assert services.emotion_status.execute(identity_id).state.state_version == 1
    assert services.emotion_history.execute() == ()
    interaction = services.history.execute().interactions[0]
    assert interaction.status is InteractionStatus.FAILED
    assert interaction.assistant_message is None


def test_appraisal_failure_and_invalid_provenance_degrade_without_state_mutation(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    unavailable = FakeAffectiveAppraisalProvider(
        error=AffectiveAppraisalProviderError("fake-appraisal", "fixture", "offline")
    )
    services, _, initial_self = build(migrated_database, unavailable)

    reply = run_talk(services, request_id="appraisal-offline")

    identity_id = initial_self.get_self.execute().identity.identity_id
    assert reply.context_manifest.emotion_appraisal_status == "unavailable"
    assert services.emotion_status.execute(identity_id).state.state_version == 1
    assert services.emotion_history.execute() == ()

    invalid = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(
            request.interaction_id,
            source_refs=(request.interaction_id, "invented-source"),
        )
    )
    invalid_services, _, _ = build(migrated_database, invalid)
    invalid_reply = run_talk(invalid_services, request_id="invalid-provenance")
    assert invalid_reply.context_manifest.emotion_appraisal_status == "rejected"
    assert invalid_services.emotion_status.execute(identity_id).state.state_version == 1
    assert invalid_services.emotion_history.execute() == ()


def test_restart_materializes_decay_without_read_side_effect(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    services, _, initial_self = build(migrated_database, appraisal)
    run_talk(services)
    identity_id = initial_self.get_self.execute().identity.identity_id
    transition = services.emotion_history.execute()[0]

    later = NOW + timedelta(hours=6)
    restarted, _, _ = build(
        migrated_database,
        FakeAffectiveAppraisalProvider(
            response_factory=lambda request: proposal_for(request.interaction_id)
        ),
        clock=FrozenClock(later),
    )
    expected = materialize_affective_state(transition.after, at=later)

    first_read = restarted.emotion_status.execute(identity_id).state
    second_read = restarted.emotion_status.execute(identity_id).state
    assert first_read == expected
    assert second_read == expected
    assert first_read.state_version == 2
    with migrated_database.engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT state_version, mood_version, as_of "
                "FROM affective_states WHERE identity_id = :identity_id"
            ),
            {"identity_id": identity_id},
        ).one()
    assert stored.state_version == 2
    assert stored.mood_version == 2
    assert str(stored.as_of).startswith("2026-07-30 14:00:00")


def test_atomic_finalize_rolls_back_state_transition_audit_and_assistant_message(
    migrated_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    services, _, initial_self = build(migrated_database, appraisal)
    original = SQLAlchemyAffectiveStateRepository.apply_transition

    def fail_after_writes(
        repository: SQLAlchemyAffectiveStateRepository,
        transition: AffectiveTransition,
        *,
        audit_event_id: str,
    ) -> bool:
        original(repository, transition, audit_event_id=audit_event_id)
        raise RuntimeError("controlled atomic finalize failure")

    monkeypatch.setattr(SQLAlchemyAffectiveStateRepository, "apply_transition", fail_after_writes)

    with pytest.raises(RuntimeError, match="controlled atomic finalize failure"):
        run_talk(services)

    identity_id = initial_self.get_self.execute().identity.identity_id
    assert services.emotion_status.execute(identity_id).state.state_version == 1
    assert scalar(migrated_database, "SELECT count(*) FROM affective_transitions") == 0
    assert (
        scalar(
            migrated_database,
            "SELECT count(*) FROM audit_events WHERE event_type = 'emotion.transition_applied'",
        )
        == 0
    )
    assert scalar(migrated_database, "SELECT count(*) FROM conversation_messages") == 1
    interaction = services.history.execute().interactions[0]
    assert interaction.status is InteractionStatus.PENDING


def test_two_stale_preparations_conflict_and_retry_from_latest_version(
    migrated_database: Database,
) -> None:
    """Different interactions cannot silently overwrite a shared base projection."""

    activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    services, _, initial_self = build(migrated_database, appraisal)
    snapshot = initial_self.get_self.execute()
    first_command = TalkInput("Первое событие", "trace-1", "concurrent-1")
    second_command = TalkInput("Второе событие", "trace-2", "concurrent-2")
    first = services.talk.interaction_log.begin(
        first_command,
        identity_id=snapshot.identity.identity_id,
    )
    second = services.talk.interaction_log.begin(
        second_command,
        identity_id=snapshot.identity.identity_id,
    )
    prepare = services.talk.prepare_affect
    finalize = services.talk.finalize_affect
    assert prepare is not None
    assert finalize is not None
    prepared_first = asyncio.run(
        prepare.execute(
            snapshot,
            first,
            user_text=first_command.user_text,
            trace_id=first_command.trace_id,
            memory_context=None,
            semantic_context=None,
        )
    )
    prepared_second = asyncio.run(
        prepare.execute(
            snapshot,
            second,
            user_text=second_command.user_text,
            trace_id=second_command.trace_id,
            memory_context=None,
            semantic_context=None,
        )
    )
    assert prepared_first.transition is not None
    assert prepared_second.transition is not None
    assert prepared_first.transition.before.state_version == 1
    assert prepared_second.transition.before.state_version == 1

    metadata_first = _provider_metadata(prepared_first)
    metadata_second = _provider_metadata(prepared_second)
    finalize.execute(
        first,
        assistant_text="Первый ответ",
        provider_metadata=metadata_first,
        prepared=prepared_first,
    )
    from satori.domain.affect import AffectiveStateConflict

    with pytest.raises(AffectiveStateConflict):
        finalize.execute(
            second,
            assistant_text="Устаревший ответ",
            provider_metadata=metadata_second,
            prepared=prepared_second,
        )
    services.talk.interaction_log.mark_failed(
        second.interaction_id,
        failure=InteractionFailureMetadata(kind="AffectiveStateConflict"),
    )
    prepared_retry = asyncio.run(
        prepare.execute(
            snapshot,
            second,
            user_text=second_command.user_text,
            trace_id=second_command.trace_id,
            memory_context=None,
            semantic_context=None,
        )
    )
    assert prepared_retry.transition is not None
    assert prepared_retry.transition.before.state_version == 2
    finalize.execute(
        second,
        assistant_text="Повторный ответ",
        provider_metadata=_provider_metadata(prepared_retry),
        prepared=prepared_retry,
    )

    assert services.emotion_status.execute(snapshot.identity.identity_id).state.state_version == 3
    assert len(services.emotion_history.execute()) == 2
    history = services.history.execute()
    assert all(item.status is InteractionStatus.COMPLETED for item in history.interactions)


def _provider_metadata(prepared: PreparedAffectiveContext) -> InteractionProviderMetadata:
    return InteractionProviderMetadata(
        provider="fake-conversation",
        model="fixture-conversation",
        finish_status="stop",
        context_schema_version=4,
        context_manifest_schema_version=4,
        policy_id="satori.conversation.behavior.v3",
        policy_schema_version=3,
        emotion_appraisal_status=prepared.appraisal_status.value,
        emotion_context_schema_version=prepared.expression.schema_version,
        emotion_state_version=prepared.expression.state_version,
        mood_state_version=prepared.expression.mood_version,
        emotion_state_as_of=prepared.expression.as_of,
    )


def test_emotion_cli_status_and_history_are_structured_and_do_not_dump_raw_text(
    sqlite_url: str,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    active_settings = settings(sqlite_url)
    assert (
        main(["activate"], settings=active_settings, alembic_config=project_root / "alembic.ini")
        == 0
    )
    capsys.readouterr()
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    generator = conversation_provider()
    private_text = "Мой приватный положительный момент"
    assert (
        main(
            ["talk", private_text, "--request-id", "emotion-cli-request"],
            settings=active_settings,
            alembic_config=project_root / "alembic.ini",
            conversation_provider=generator,
            episode_formation_provider=episode_provider(),
            embedding_provider=FakeEmbeddingProvider({private_text: (1.0, 0.0, 0.0)}),
            affective_appraisal_provider=appraisal,
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            ["emotion", "status"],
            settings=active_settings,
            alembic_config=project_root / "alembic.ini",
            conversation_provider=generator,
            episode_formation_provider=episode_provider(),
            affective_appraisal_provider=appraisal,
        )
        == 0
    )
    status_output = capsys.readouterr().out
    assert "state_version=2 mood_version=2" in status_output
    assert "fast valence=" in status_output
    assert "mood valence=" in status_output

    assert (
        main(
            ["emotion", "history", "--limit", "1"],
            settings=active_settings,
            alembic_config=project_root / "alembic.ini",
            conversation_provider=generator,
            episode_formation_provider=episode_provider(),
            affective_appraisal_provider=appraisal,
        )
        == 0
    )
    history_output = capsys.readouterr().out
    assert "state=1->2 policy=1" in history_output
    assert "delta valence=" in history_output
    assert private_text not in history_output
    assert "reasoning" not in history_output


def test_persistent_transition_contains_structured_evidence_but_no_raw_content(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    services, _, _ = build(migrated_database, appraisal)
    private_text = "Секретный текст пользователя для проверки границы"
    reply = run_talk(services, text_value=private_text)

    with migrated_database.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT appraisal_payload, source_refs, reason_codes, state_before, state_after "
                "FROM affective_transitions WHERE interaction_id = :interaction_id"
            ),
            {"interaction_id": reply.interaction_id},
        ).one()
    serialized = json.dumps(tuple(row), ensure_ascii=False)
    assert private_text not in serialized
    assert "positive_engagement" in serialized
    assert reply.interaction_id in serialized
