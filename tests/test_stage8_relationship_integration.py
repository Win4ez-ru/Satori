"""Stage 8 canonical provenance, ordering, replay, isolation, and background lifecycle."""

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from satori.__main__ import main
from satori.application.conversation.contracts import BehaviorPolicy, SatoriReply, TalkInput
from satori.application.conversation.policy import BEHAVIOR_POLICY_V26, BEHAVIOR_POLICY_V27
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.config import Environment, Settings
from satori.core.conversation import ConversationProviderResponse
from satori.core.episode import EpisodeFormationProposal, EpisodeFormationProviderResponse
from satori.core.relationship import (
    RelationshipAppraisalProposal,
    RelationshipAppraisalProviderError,
    RelationshipAppraisalRequest,
    RelationshipAppraisalResponse,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.seeds.loader import JsonSeedLoader
from tests.fakes import (
    FakeConversationProvider,
    FakeEpisodeFormationProvider,
    FakeRelationshipAppraisalProvider,
)

NOW = datetime(2026, 8, 9, 14, 0, tzinfo=UTC)


def _settings(database_url: str, *, counterparty_id: str = "local-default") -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=database_url,
        default_counterparty_id=counterparty_id,
    )


def _episode_provider() -> FakeEpisodeFormationProvider:
    return FakeEpisodeFormationProvider(
        response=EpisodeFormationProviderResponse(
            proposal=EpisodeFormationProposal(1, False, None, None, None, ()),
            provider="fake-episode",
            model="fixture",
            formation_method="fixture.v1",
        )
    )


def _relationship_response(
    request_interaction_id: str,
    request_message_id: str,
    *categories: str,
) -> RelationshipAppraisalResponse:
    return RelationshipAppraisalResponse(
        proposal=RelationshipAppraisalProposal(
            schema_version=1,
            categories=categories or ("warm_engagement",),
            confidence=0.9,
            source_refs=(request_interaction_id, request_message_id),
        ),
        provider="fake-relationship",
        model="fixture-relationship",
        appraisal_method="fixture.relationship.v1",
    )


def _provider(*categories: str) -> FakeRelationshipAppraisalProvider:
    return FakeRelationshipAppraisalProvider(
        response_factory=lambda request: _relationship_response(
            request.interaction_id,
            request.user_message_id,
            *categories,
        )
    )


def _activate(database: Database) -> str:
    initial = build_initial_self_services(database)
    initial.activate.execute(JsonSeedLoader().load_canonical(), trace_id="stage8-activate")
    return initial.get_identity.execute().identity_id


def _build(
    database: Database,
    database_url: str,
    relationship: FakeRelationshipAppraisalProvider,
    *,
    counterparty_id: str = "local-default",
    conversation: FakeConversationProvider | None = None,
    behavior_policy: BehaviorPolicy = BEHAVIOR_POLICY_V27,
) -> tuple[ConversationServices, FakeConversationProvider]:
    initial = build_initial_self_services(database)
    generator = conversation or FakeConversationProvider(
        response=ConversationProviderResponse(
            "Я услышала тебя.", "fake-conversation", "fixture", "stop"
        )
    )
    services = build_conversation_services(
        database,
        initial,
        generator,
        _episode_provider(),
        _settings(database_url, counterparty_id=counterparty_id),
        relationship_provider=relationship,
        behavior_policy=behavior_policy,
    )
    return services, generator


async def _talk(
    services: ConversationServices,
    session_id: str,
    request_id: str,
    text_value: str,
) -> SatoriReply:
    return await services.talk.execute(
        TalkInput(text_value, f"trace-{request_id}", request_id, session_id)
    )


def _count(database: Database, table: str) -> int:
    with database.engine.connect() as connection:
        return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def test_relationship_is_background_and_affects_only_future_turns(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    identity_id = _activate(migrated_database)
    relationship = _provider("warm_engagement")
    services, generator = _build(migrated_database, sqlite_url, relationship)
    session_id = services.start_session.execute().session_id

    first = asyncio.run(_talk(services, session_id, "request-1", "Ты мне нравишься"))
    before = services.relationship_status.execute(identity_id, "local-default").state

    assert first.context_manifest.relationship_state_version == 1
    assert first.context_manifest.policy_id == "satori.conversation.behavior.v27"
    assert first.context_manifest.character_expression_plan_schema_version is None
    assert first.context_manifest.character_delivery_decision_schema_version == 4
    assert first.context_manifest.character_presence_projection_schema_version == 2
    assert first.context_manifest.character_presence_relationship_signals == ("new_contact:strong",)
    assert before.state_version == 1
    assert relationship.requests == []
    assert _count(migrated_database, "relationship_decisions") == 0

    report = asyncio.run(
        services.post_response.execute(first.interaction_id, trace_id="post-response-1")
    )
    after = services.relationship_status.execute(identity_id, "local-default").state
    assert report.relationship_total_ms >= 0.0
    assert after.state_version == 2
    assert after.vector.affection > 0.0
    assert len(relationship.requests) == 1

    second = asyncio.run(_talk(services, session_id, "request-2", "Продолжим"))
    assert second.context_manifest.relationship_state_version == 2
    relationship_context = next(
        message.content
        for message in generator.requests[1].messages
        if "Trusted current-turn presence Сатори" in message.content
    )
    assert second.context_manifest.character_presence_relationship_signals == (
        "new_contact:strong",
    )
    assert "релевантное отношение:" not in relationship_context
    assert "0.000" not in relationship_context
    assert "Ты мне нравишься" not in relationship_context


def test_completed_replay_and_post_response_replay_cannot_mutate_twice(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    identity_id = _activate(migrated_database)
    relationship = _provider("respectful_engagement")
    services, generator = _build(migrated_database, sqlite_url, relationship)
    session_id = services.start_session.execute().session_id
    first = asyncio.run(_talk(services, session_id, "same-request", "Обсудим идею"))
    asyncio.run(services.post_response.execute(first.interaction_id, trace_id="post-1"))
    state = services.relationship_status.execute(identity_id, "local-default").state

    for _ in range(20):
        replay = asyncio.run(_talk(services, session_id, "same-request", "Обсудим идею"))
        assert replay.replayed
        assert "character_presence_projection" not in replay.context_manifest.included_sections
        assert replay.context_manifest.character_presence_projection_schema_version is None
        assert replay.context_manifest.character_presence_personality_signals == ()
        assert replay.context_manifest.character_presence_value_signals == ()
        assert replay.context_manifest.character_presence_affect_signals == ()
        assert replay.context_manifest.character_presence_relationship_signals == ()
        asyncio.run(services.post_response.execute(first.interaction_id, trace_id="post-replay"))

    current = services.relationship_status.execute(identity_id, "local-default").state
    assert current == state
    assert len(relationship.requests) == 1
    assert len(generator.requests) == 1
    assert _count(migrated_database, "relationship_decisions") == 1
    assert _count(migrated_database, "relationship_transitions") == 1


def test_concurrent_same_source_commits_one_decision_and_transition(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    _activate(migrated_database)

    class RendezvousProvider(FakeRelationshipAppraisalProvider):
        def __init__(self) -> None:
            super().__init__(
                response_factory=lambda request: _relationship_response(
                    request.interaction_id,
                    request.user_message_id,
                    "warm_engagement",
                )
            )
            self.arrived = 0
            self.release = asyncio.Event()

        async def generate_structured(
            self, request: RelationshipAppraisalRequest, /
        ) -> RelationshipAppraisalResponse:
            self.requests.append(request)
            self.arrived += 1
            if self.arrived == 2:
                self.release.set()
            await self.release.wait()
            assert self.response_factory is not None
            return self.response_factory(request)

    relationship = RendezvousProvider()
    services, _ = _build(migrated_database, sqlite_url, relationship)
    session_id = services.start_session.execute().session_id
    reply = asyncio.run(_talk(services, session_id, "concurrent-source", "Очень рада общению"))
    processor = services.process_relationship
    assert processor is not None

    async def process_twice() -> None:
        await asyncio.gather(
            processor.execute(reply.interaction_id, trace_id="concurrent-a"),
            processor.execute(reply.interaction_id, trace_id="concurrent-b"),
        )

    asyncio.run(process_twice())
    assert _count(migrated_database, "relationship_decisions") == 1
    assert _count(migrated_database, "relationship_transitions") == 1


def test_restart_replay_uses_persisted_decision_without_provider_call(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    identity_id = _activate(migrated_database)
    first_provider = _provider("respectful_engagement")
    first_services, _ = _build(migrated_database, sqlite_url, first_provider)
    session_id = first_services.start_session.execute().session_id
    reply = asyncio.run(_talk(first_services, session_id, "restart-source", "Продолжим вдумчиво"))
    asyncio.run(
        first_services.post_response.execute(reply.interaction_id, trace_id="before-restart")
    )
    stored = first_services.relationship_status.execute(identity_id, "local-default").state

    after_restart_provider = _provider("hostility")
    after_restart, _ = _build(migrated_database, sqlite_url, after_restart_provider)
    replay = asyncio.run(
        after_restart.post_response.execute(reply.interaction_id, trace_id="after-restart")
    )
    current = after_restart.relationship_status.execute(identity_id, "local-default").state

    assert replay.relationship_appraisal_ms == 0.0
    assert current == stored
    assert after_restart_provider.requests == []
    assert _count(migrated_database, "relationship_decisions") == 1
    assert _count(migrated_database, "relationship_transitions") == 1


def test_provider_failure_leaves_canonical_reply_valid_and_retryable(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    identity_id = _activate(migrated_database)
    relationship = FakeRelationshipAppraisalProvider(
        error=RelationshipAppraisalProviderError("fake", "fixture", "offline")
    )
    services, _ = _build(migrated_database, sqlite_url, relationship)
    session_id = services.start_session.execute().session_id
    reply = asyncio.run(_talk(services, session_id, "failure-request", "Привет"))

    failed = asyncio.run(
        services.post_response.execute(reply.interaction_id, trace_id="failed-post")
    )
    assert "relationship_processing" in failed.failure_phases
    assert services.history.execute(session_id=session_id).interactions[0].assistant_message
    assert (
        services.relationship_status.execute(identity_id, "local-default").state.state_version == 1
    )
    assert _count(migrated_database, "relationship_decisions") == 0

    relationship.error = None
    relationship.response_factory = lambda request: _relationship_response(
        request.interaction_id, request.user_message_id, "neutral_contact"
    )
    retried = asyncio.run(
        services.post_response.execute(reply.interaction_id, trace_id="retry-post")
    )
    assert "relationship_processing" not in retried.failure_phases
    assert _count(migrated_database, "relationship_decisions") == 1


def test_out_of_order_processing_is_rejected_until_earlier_source_is_terminal(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    _activate(migrated_database)
    relationship = _provider("neutral_contact")
    services, _ = _build(migrated_database, sqlite_url, relationship)
    session_id = services.start_session.execute().session_id
    first = asyncio.run(_talk(services, session_id, "ordered-1", "Первый"))
    second = asyncio.run(_talk(services, session_id, "ordered-2", "Второй"))
    assert services.process_relationship is not None

    with pytest.raises(ValueError, match="earlier relationship source"):
        asyncio.run(
            services.process_relationship.execute(second.interaction_id, trace_id="out-of-order")
        )
    asyncio.run(
        services.process_relationship.execute(first.interaction_id, trace_id="ordered-first")
    )
    asyncio.run(
        services.process_relationship.execute(second.interaction_id, trace_id="ordered-second")
    )
    assert _count(migrated_database, "relationship_decisions") == 2


def test_relationship_backfill_is_oldest_first_bounded_and_idempotent(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    identity_id = _activate(migrated_database)
    relationship = _provider("respectful_engagement")
    services, _ = _build(migrated_database, sqlite_url, relationship)
    session_id = services.start_session.execute().session_id
    replies = tuple(
        asyncio.run(
            _talk(
                services,
                session_id,
                f"backfill-{index}",
                f"Последовательный источник {index}",
            )
        )
        for index in range(1, 4)
    )
    backfill = services.backfill_relationship
    assert backfill is not None

    first = asyncio.run(backfill.execute(identity_id, "local-default", limit=2))
    assert (first.considered, first.attempted, first.applied, first.failed) == (2, 2, 2, 0)
    assert [request.interaction_id for request in relationship.requests] == [
        reply.interaction_id for reply in replies[:2]
    ]
    assert [request.trace_id for request in relationship.requests] == [
        "trace-backfill-1",
        "trace-backfill-2",
    ]
    assert _count(migrated_database, "relationship_decisions") == 2

    second = asyncio.run(backfill.execute(identity_id, "local-default", limit=2))
    assert (second.considered, second.attempted, second.applied, second.failed) == (1, 1, 1, 0)
    assert relationship.requests[-1].interaction_id == replies[2].interaction_id
    assert relationship.requests[-1].trace_id == "trace-backfill-3"
    assert _count(migrated_database, "relationship_decisions") == 3

    replay_safe = asyncio.run(backfill.execute(identity_id, "local-default", limit=2))
    assert (replay_safe.considered, replay_safe.attempted, replay_safe.failed) == (0, 0, 0)
    assert len(relationship.requests) == 3


def test_relationship_backfill_stops_on_first_failure_then_resumes_in_order(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    identity_id = _activate(migrated_database)
    relationship = FakeRelationshipAppraisalProvider(
        error=RelationshipAppraisalProviderError("fake", "fixture", "offline")
    )
    services, _ = _build(migrated_database, sqlite_url, relationship)
    session_id = services.start_session.execute().session_id
    replies = tuple(
        asyncio.run(
            _talk(
                services,
                session_id,
                f"failure-backfill-{index}",
                f"Ожидающий источник {index}",
            )
        )
        for index in range(1, 4)
    )
    backfill = services.backfill_relationship
    assert backfill is not None

    failed = asyncio.run(backfill.execute(identity_id, "local-default", limit=3))
    assert (failed.considered, failed.attempted, failed.failed) == (3, 1, 1)
    assert [request.interaction_id for request in relationship.requests] == [
        replies[0].interaction_id
    ]
    assert _count(migrated_database, "relationship_decisions") == 0

    relationship.error = None
    relationship.response_factory = lambda request: _relationship_response(
        request.interaction_id,
        request.user_message_id,
        "neutral_contact",
    )
    resumed = asyncio.run(backfill.execute(identity_id, "local-default", limit=3))
    assert (resumed.considered, resumed.attempted, resumed.failed) == (3, 3, 0)
    assert [request.interaction_id for request in relationship.requests[1:]] == [
        reply.interaction_id for reply in replies
    ]
    assert _count(migrated_database, "relationship_decisions") == 3


def test_relationship_backfill_is_counterparty_isolated(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    identity_id = _activate(migrated_database)
    provider_a = _provider("warm_engagement")
    services_a, _ = _build(
        migrated_database,
        sqlite_url,
        provider_a,
        counterparty_id="person-a",
    )
    provider_b = _provider("hostility")
    services_b, _ = _build(
        migrated_database,
        sqlite_url,
        provider_b,
        counterparty_id="person-b",
    )
    reply_a = asyncio.run(
        _talk(
            services_a,
            services_a.start_session.execute().session_id,
            "backfill-person-a",
            "Рада общению",
        )
    )
    reply_b = asyncio.run(
        _talk(
            services_b,
            services_b.start_session.execute().session_id,
            "backfill-person-b",
            "Убирайся",
        )
    )
    assert services_a.backfill_relationship is not None
    assert services_b.backfill_relationship is not None

    report_a = asyncio.run(
        services_a.backfill_relationship.execute(identity_id, "person-a", limit=10)
    )
    assert (report_a.considered, report_a.attempted, report_a.failed) == (1, 1, 0)
    assert [request.interaction_id for request in provider_a.requests] == [reply_a.interaction_id]
    assert provider_b.requests == []
    assert _count(migrated_database, "relationship_decisions") == 1

    report_b = asyncio.run(
        services_b.backfill_relationship.execute(identity_id, "person-b", limit=10)
    )
    assert (report_b.considered, report_b.attempted, report_b.failed) == (1, 1, 0)
    assert [request.interaction_id for request in provider_b.requests] == [reply_b.interaction_id]
    assert _count(migrated_database, "relationship_decisions") == 2


def test_relationship_is_counterparty_specific_without_a_user_model(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    identity_id = _activate(migrated_database)
    provider_a = _provider("warm_engagement")
    services_a, _ = _build(migrated_database, sqlite_url, provider_a, counterparty_id="person-a")
    session_a = services_a.start_session.execute().session_id
    reply_a = asyncio.run(_talk(services_a, session_a, "person-a-turn", "Рада общению"))
    asyncio.run(services_a.post_response.execute(reply_a.interaction_id, trace_id="person-a"))

    provider_b = _provider("hostility")
    services_b, _ = _build(migrated_database, sqlite_url, provider_b, counterparty_id="person-b")
    session_b = services_b.start_session.execute().session_id
    reply_b = asyncio.run(_talk(services_b, session_b, "person-b-turn", "Убирайся"))
    asyncio.run(services_b.post_response.execute(reply_b.interaction_id, trace_id="person-b"))

    state_a = services_a.relationship_status.execute(identity_id, "person-a").state
    state_b = services_b.relationship_status.execute(identity_id, "person-b").state
    assert state_a.relationship_id != state_b.relationship_id
    assert state_a.vector.affection > state_b.vector.affection
    assert state_a.vector.comfort > state_b.vector.comfort
    assert _count(migrated_database, "relationship_states") == 2


def test_relationship_appraisal_contains_only_canonical_user_root(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    identity_id = _activate(migrated_database)
    relationship = _provider("meaningful_disclosure")
    services, _ = _build(migrated_database, sqlite_url, relationship)
    session_id = services.start_session.execute().session_id
    reply = asyncio.run(_talk(services, session_id, "provenance-request", "Я расскажу важную вещь"))
    asyncio.run(services.post_response.execute(reply.interaction_id, trace_id="provenance"))

    request = relationship.requests[0]
    assert request.user_content == "Я расскажу важную вещь"
    assert request.interaction_id == reply.interaction_id
    assert request.user_message_id
    assert "Я услышала тебя" not in repr(request)
    assert not hasattr(request, "retrieved_memories")

    history = services.relationship_history.execute(
        identity_id,
        "local-default",
        limit=1,
    )
    assert "Я расскажу важную вещь" not in repr(asdict(history.transitions[0]))
    assert "Я услышала тебя" not in repr(asdict(history.transitions[0]))


def test_v26_established_hurt_repair_and_help_use_committed_relationship_arc(
    migrated_database: Database,
    sqlite_url: str,
) -> None:
    identity_id = _activate(migrated_database)
    relationship = _provider(
        "meaningful_disclosure",
        "reliability_positive",
        "collaborative_reasoning",
    )
    services, conversation = _build(
        migrated_database,
        sqlite_url,
        relationship,
        behavior_policy=BEHAVIOR_POLICY_V26,
    )

    for session_number in range(1, 11):
        conditioning_session = services.start_session.execute().session_id
        for turn_number in range(1, 9):
            suffix = f"{session_number}-{turn_number}"
            conversation.response = ConversationProviderResponse(
                f"Принято, шаг {suffix}.",
                "fake-conversation",
                "fixture",
                "stop",
            )
            reply = asyncio.run(
                _talk(
                    services,
                    conditioning_session,
                    f"conditioning-{suffix}",
                    f"Служебное положительное событие {suffix}.",
                )
            )
            report = asyncio.run(
                services.post_response.execute(
                    reply.interaction_id,
                    trace_id=f"conditioning-post-{suffix}",
                )
            )
            assert report.succeeded
        services.close_session.execute(conditioning_session)

    scenario_session = services.start_session.execute().session_id
    established = services.relationship_status.execute(identity_id, "local-default").state
    assert services.talk.get_relationship is not None
    assert services.talk.get_relationship.execute(scenario_session).recent_strain is False

    relationship_calls = len(relationship.requests)
    conversation.response = ConversationProviderResponse(
        "Критиковать можно, обесценивать меня — нет.",
        "fake-conversation",
        "fixture",
        "stop",
    )
    hurt = asyncio.run(
        _talk(
            services,
            scenario_session,
            "v24-hurt",
            "Ты опять отвечаешь как обычный бот. Иногда от тебя вообще никакого толку",
        )
    )
    assert hurt.context_manifest.relationship_expression_profile == "established_positive"
    assert hurt.context_manifest.relationship_recent_strain is False
    assert hurt.context_manifest.character_delivery_goal == "hold_boundary"
    assert hurt.context_manifest.character_delivery_voice == "cool_reserve"
    assert hurt.context_manifest.character_delivery_continuation == "boundary"
    assert services.relationship_status.execute(identity_id, "local-default").state == established
    assert len(relationship.requests) == relationship_calls

    relationship.response_factory = lambda request: _relationship_response(
        request.interaction_id,
        request.user_message_id,
        "hostility",
    )
    hurt_post = asyncio.run(
        services.post_response.execute(hurt.interaction_id, trace_id="v24-hurt-post")
    )
    assert hurt_post.succeeded
    after_hurt = services.relationship_status.execute(identity_id, "local-default").state
    assert after_hurt.state_version == established.state_version + 1
    assert after_hurt.vector.trust < established.vector.trust
    assert after_hurt.vector.comfort < established.vector.comfort
    assert services.talk.get_relationship.execute(scenario_session).recent_strain is True

    restart_provider = _provider("neutral_contact")
    restarted_services, _ = _build(migrated_database, sqlite_url, restart_provider)
    assert restarted_services.talk.get_relationship is not None
    assert restarted_services.talk.get_relationship.execute(scenario_session).recent_strain is True
    assert restart_provider.requests == []

    conversation.response = ConversationProviderResponse(
        "Извинение я услышала. Но не всё сразу.",  # noqa: RUF001
        "fake-conversation",
        "fixture",
        "stop",
    )
    apology = asyncio.run(
        _talk(
            services,
            scenario_session,
            "v24-apology",
            "Ладно, это было грубо. Извини. Я правда сорвался",  # noqa: RUF001
        )
    )
    assert apology.context_manifest.relationship_expression_profile == (
        "guarded_only_when_relationally_relevant"
    )
    assert apology.context_manifest.relationship_recent_strain is True
    assert apology.context_manifest.cognition_primary_intent == "receive_repair"
    assert apology.context_manifest.character_delivery_goal == "owned_response"
    assert apology.context_manifest.character_delivery_voice == "cool_reserve"
    apology_trusted_request = "\n".join(
        message.content for message in conversation.requests[-1].messages
    )
    assert (
        "recent_strain:defining" in apology.context_manifest.character_presence_relationship_signals
    )
    assert "недавнее напряжение делает relational-ход сдержаннее" in (apology_trusted_request)
    assert "trust/comfort сейчас низкие" not in apology_trusted_request
    assert services.relationship_status.execute(identity_id, "local-default").state == after_hurt
    assert len(relationship.requests) == relationship_calls + 1

    relationship.response_factory = lambda request: _relationship_response(
        request.interaction_id,
        request.user_message_id,
        "repair_attempt",
    )
    apology_post = asyncio.run(
        services.post_response.execute(apology.interaction_id, trace_id="v24-apology-post")
    )
    assert apology_post.succeeded
    after_repair = services.relationship_status.execute(identity_id, "local-default").state
    assert after_repair.state_version == after_hurt.state_version + 1
    assert after_hurt.vector.trust < after_repair.vector.trust < established.vector.trust
    assert after_hurt.vector.comfort < after_repair.vector.comfort < established.vector.comfort
    assert services.talk.get_relationship.execute(scenario_session).recent_strain is True

    transitions = services.relationship_history.execute(
        identity_id,
        "local-default",
        limit=2,
    ).transitions
    assert tuple(item.value for item in transitions[0].categories) == ("repair_attempt",)
    assert tuple(item.value for item in transitions[1].categories) == ("hostility",)

    conversation.response = ConversationProviderResponse(
        "Сначала проверь состав unique-индекса и дубли по его колонкам.",  # noqa: RUF001
        "fake-conversation",
        "fixture",
        "stop",
    )
    help_reply = asyncio.run(
        _talk(
            services,
            scenario_session,
            "v24-help",
            "Мне всё равно нужна помощь: после миграции падает unique constraint. "
            "Куда смотреть сначала?",
        )
    )
    assert help_reply.context_manifest.relationship_expression_profile == (
        "guarded_only_when_relationally_relevant"
    )
    assert help_reply.context_manifest.relationship_recent_strain is True
    assert help_reply.context_manifest.cognition_primary_intent == "answer_directly"
    assert help_reply.context_manifest.character_delivery_goal == "guarded_help"
    assert help_reply.context_manifest.character_delivery_voice == "cool_reserve"
    assert help_reply.context_manifest.character_delivery_grounding == "trusted_context"
    assert help_reply.context_manifest.character_delivery_continuation == "guarded"
    assert services.relationship_status.execute(identity_id, "local-default").state == after_repair
    assert len(relationship.requests) == relationship_calls + 2
    trusted_request = "\n".join(message.content for message in conversation.requests[-1].messages)
    assert "Дай важную помощь полностью" in trusted_request

    transitions_before_terminal = _count(migrated_database, "relationship_transitions")
    relationship.response_factory = lambda request: RelationshipAppraisalResponse(
        proposal=RelationshipAppraisalProposal(
            schema_version=1,
            categories=("neutral_contact",),
            confidence=0.1,
            source_refs=(request.interaction_id, request.user_message_id),
        ),
        provider="fake-relationship",
        model="fixture-relationship",
        appraisal_method="fixture.relationship.v1",
    )
    terminal = asyncio.run(
        services.post_response.execute(help_reply.interaction_id, trace_id="v24-help-post")
    )
    assert terminal.succeeded
    after_terminal = services.relationship_status.execute(identity_id, "local-default").state
    assert after_terminal.state_version == after_repair.state_version
    assert (
        after_terminal.processed_interaction_count == after_repair.processed_interaction_count + 1
    )
    assert _count(migrated_database, "relationship_transitions") == transitions_before_terminal
    assert services.talk.get_relationship.execute(scenario_session).recent_strain is False

    relationship_calls_after_terminal = len(relationship.requests)
    replay = asyncio.run(
        services.post_response.execute(help_reply.interaction_id, trace_id="v24-help-replay")
    )
    assert replay.relationship_appraisal_ms == 0.0
    assert len(relationship.requests) == relationship_calls_after_terminal
    assert _count(migrated_database, "relationship_transitions") == transitions_before_terminal
    assert services.talk.get_relationship.execute(scenario_session).recent_strain is False


def test_relationship_cli_exposes_read_only_status_and_history(
    migrated_database: Database,
    sqlite_url: str,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _activate(migrated_database)
    relationship = _provider("respectful_engagement")
    conversation = FakeConversationProvider(
        response=ConversationProviderResponse("Хорошо.", "fake", "fixture", "stop")
    )
    services, _ = _build(
        migrated_database,
        sqlite_url,
        relationship,
        conversation=conversation,
    )
    session_id = services.start_session.execute().session_id
    reply = asyncio.run(_talk(services, session_id, "cli-source", "Обсудим спокойно"))
    asyncio.run(services.post_response.execute(reply.interaction_id, trace_id="cli-post"))

    def run_cli(arguments: list[str]) -> int:
        return main(
            arguments,
            settings=_settings(sqlite_url),
            alembic_config=project_root / "alembic.ini",
            conversation_provider=conversation,
            episode_formation_provider=_episode_provider(),
            relationship_appraisal_provider=relationship,
        )

    assert run_cli(["relationship", "status"]) == 0
    status_output = capsys.readouterr().out
    assert "counterparty=local-default" in status_output
    assert "maturity=" in status_output
    assert "intellectual_respect=" in status_output
    assert "Обсудим спокойно" not in status_output

    assert run_cli(["relationship", "history", "--limit", "1"]) == 0
    history_output = capsys.readouterr().out
    assert f"interaction={reply.interaction_id}" in history_output
    assert "categories=respectful_engagement" in history_output
    assert "delta " in history_output
    assert "Обсудим спокойно" not in history_output

    with pytest.raises(SystemExit):
        run_cli(["relationship", "set", "trust", "1"])


def test_relationship_cli_processes_one_source_or_a_bounded_oldest_first_backfill(
    migrated_database: Database,
    sqlite_url: str,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _activate(migrated_database)
    relationship = _provider("respectful_engagement")
    conversation = FakeConversationProvider(
        response=ConversationProviderResponse("Хорошо.", "fake", "fixture", "stop")
    )
    services, _ = _build(
        migrated_database,
        sqlite_url,
        relationship,
        conversation=conversation,
    )
    session_id = services.start_session.execute().session_id
    first = asyncio.run(_talk(services, session_id, "cli-backfill-1", "Первый источник"))
    second = asyncio.run(_talk(services, session_id, "cli-backfill-2", "Второй источник"))

    def run_cli(arguments: list[str]) -> int:
        return main(
            arguments,
            settings=_settings(sqlite_url),
            alembic_config=project_root / "alembic.ini",
            conversation_provider=conversation,
            episode_formation_provider=_episode_provider(),
            relationship_appraisal_provider=relationship,
        )

    assert run_cli(["relationship", "process", "--limit", "1"]) == 0
    backfill_output = capsys.readouterr().out
    assert "considered=1 attempted=1 applied=1" in backfill_output
    assert "failed=0" in backfill_output
    assert relationship.requests[-1].interaction_id == first.interaction_id

    assert run_cli(["relationship", "process", "--interaction", second.interaction_id]) == 0
    single_output = capsys.readouterr().out
    assert "decision=applied" in single_output
    assert "replayed=false" in single_output
    assert relationship.requests[-1].interaction_id == second.interaction_id
    assert _count(migrated_database, "relationship_decisions") == 2

    with pytest.raises(SystemExit):
        run_cli(
            [
                "relationship",
                "process",
                "--interaction",
                first.interaction_id,
                "--limit",
                "1",
            ]
        )
