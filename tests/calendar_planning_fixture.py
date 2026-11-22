"""Public fixtures around the actual Core, SQLite, source grants and action owner."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionCatalog,
    CalendarActionScope,
)
from satori.application.assistant.calendar_planning_contracts import (
    CalendarObservedEvent,
    CalendarPlanningError,
    CalendarPlanningRequest,
    CalendarPlanningSnapshot,
)
from satori.application.assistant.calendar_reasoning import CoreCalendarReasoner
from satori.application.assistant.calendar_service import CalendarAssistantService
from satori.application.conversation.contracts import TalkInput
from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.core.conversation import ConversationProviderRequest, ConversationProviderResponse
from satori.domain.memory_scope import MemoryScope
from satori.infrastructure.assistant.calendar_actions_store import SQLiteCalendarActionStore
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.seeds.loader import JsonSeedLoader
from satori.infrastructure.source_policy.installation import enroll_installation
from tests.calendar_actions_fixture import FixtureCalendarActionProvider
from tests.fakes import FakeConversationProvider, FakeEmbeddingProvider
from tests.test_conversation import conversation_settings, skip_episode_provider, success_response
from tests.test_stage5_retrieval import episode_provider_with_summary

NOW = datetime(2026, 11, 3, 9, tzinfo=UTC)
PREFERENCE = "PUBLIC_PREFERENCE_AFTER_16: мне удобны встречи после 16 часов UTC."
SUMMARY = "PUBLIC_PREFERENCE_AFTER_16: пользователь предпочитает встречи после 16 часов UTC."
RECENT = "PUBLIC_RECENT_CONTEXT: завтра нужен отдых после работы."
QUERY = "Помнишь мои предпочтения? Подбери время для публичной тестовой встречи завтра."


class PlanningClock:
    instant = NOW

    def now(self) -> datetime:
        return self.instant

    def __call__(self) -> datetime:
        return self.instant


class PlanningConversation(FakeConversationProvider):
    """Fake model interpreting actual supplied fixture context, not quality evidence."""

    def __init__(self) -> None:
        super().__init__(response=success_response("PUBLIC canonical acknowledgement."))
        self.memory_ids: tuple[str, ...] = ()
        self.before_response: Callable[[], None] | None = None
        self.invalid_choice = False
        self.wire_sends = 0
        self.source_packets: list[dict[str, Any]] = []

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        admit_provider_dispatch()
        packet = None
        for message in request.messages:
            if message.content.startswith("{"):
                candidate = json.loads(message.content)
                if candidate.get("kind") == "calendar_observation":
                    packet = candidate
        if packet is None:
            return await super().generate(request)
        self.requests.append(request)
        self.source_packets.append(packet)
        body = "\n".join(message.content for message in request.messages)
        assert SUMMARY in body
        assert RECENT in body
        assert all(memory_id in body for memory_id in self.memory_ids)
        assert request.messages[-1].content == QUERY
        selected = next(
            slot
            for slot in packet["candidate_slots"]
            if datetime.fromisoformat(slot["starts_at"].replace("Z", "+00:00")).hour >= 16
        )
        if self.before_response is not None:
            self.before_response()
        admit_provider_dispatch()
        self.wire_sends += 1
        return success_response(
            json.dumps(
                {
                    "status": "suggested",
                    "slot_id": "f" * 24 if self.invalid_choice else selected["slot_id"],
                    "explanation": (
                        "Публичный fixture: учтено предпочтение времени "
                        "и занятость выбранного календаря."
                    ),
                    "used_memory_ids": self.memory_ids,
                    "used_event_refs": [event["event_ref"] for event in packet["events"]],
                },
                ensure_ascii=False,
            )
        )


class PlanningSource:
    def __init__(self, scope: CalendarActionScope, clock: PlanningClock) -> None:
        self.scope, self.clock = scope, clock
        self.read_grant_id, self.catalog_revision = uuid4(), uuid4()
        self.active, self.complete = True, True
        self.events: tuple[CalendarObservedEvent, ...] = ()
        self.effect_provider: FixtureCalendarActionProvider | None = None

    def require(self, scope: CalendarActionScope, *, read_grant_id: UUID | None = None) -> None:
        if (
            not self.active
            or scope != self.scope
            or (read_grant_id is not None and read_grant_id != self.read_grant_id)
        ):
            raise CalendarPlanningError("fixture_calendar_scope_revoked")

    async def fetch(self, request: CalendarPlanningRequest) -> CalendarPlanningSnapshot:
        self.require(request.scope)
        events = list(self.events)
        if self.effect_provider is not None:
            events.extend(
                CalendarObservedEvent(
                    event_ref=event.event_id,
                    calendar_id=self.scope.calendar_id,
                    title=event.command.title,
                    starts_at=event.command.starts_at,
                    ends_at=event.command.ends_at,
                    status="confirmed",
                )
                for event in self.effect_provider.events.values()
            )
        return CalendarPlanningSnapshot(
            scope=self.scope,
            read_grant_id=self.read_grant_id,
            selected_calendar_ids=(self.scope.calendar_id,),
            observed_at=self.clock(),
            expires_at=self.clock() + timedelta(minutes=2),
            window_start=request.window_start,
            window_end=request.window_end,
            complete=self.complete,
            events=tuple(events),
        )

    def catalog(self, scope: CalendarActionScope) -> CalendarActionCatalog:
        self.require(scope)
        return CalendarActionCatalog(
            scope=scope,
            revision=self.catalog_revision,
            observed_at=self.clock(),
            full_access=True,
            writable=True,
            supports_events=True,
            calendar_title="PUBLIC WORK",
            account_title="PUBLIC ACCOUNT",
        )


async def seeded_core(
    database: Database, directory: Path
) -> tuple[
    Database, ConversationServices, PlanningConversation, PlanningClock, CalendarActionScope
]:
    clock = PlanningClock()
    settings = conversation_settings(source_policy_directory=directory / "source-policy")
    initial = build_initial_self_services(database, clock=clock)
    identity = initial.activate.execute(
        JsonSeedLoader().load_canonical(), trace_id="public-calendar-seed"
    ).identity.identity_id
    enroll_installation(
        database, settings.source_policy_directory, reviewed=True, new_installation=True
    )
    embedding = FakeEmbeddingProvider(
        {
            PREFERENCE: (1.0, 0.0, 0.0),
            SUMMARY: (1.0, 0.0, 0.0),
            RECENT: (0.0, 1.0, 0.0),
            QUERY: (0.99, 0.01, 0.0),
        }
    )
    first = build_conversation_services(
        database,
        initial,
        FakeConversationProvider(response=success_response("PUBLIC acknowledgement.")),
        episode_provider_with_summary(SUMMARY),
        settings,
        clock=clock,
        embedding_provider=embedding,
    )
    session = first.start_session.execute()
    reply = await first.talk.execute(
        TalkInput(PREFERENCE, "public-before-restart", "public-memory-request", session.session_id)
    )
    processed = await first.post_response.execute(
        reply.interaction_id, trace_id="public-memory-process"
    )
    assert processed.succeeded
    memory_scope = MemoryScope(identity, "local-default")
    memory_ids = tuple(memory.memory_id for memory in first.memories.execute(scope=memory_scope))
    assert len(memory_ids) == 1
    url = str(database.engine.url)
    database.dispose()
    reopened = create_database(url)
    clock.instant += timedelta(hours=1)
    model = PlanningConversation()
    model.memory_ids = memory_ids
    second = build_conversation_services(
        reopened,
        build_initial_self_services(reopened, clock=clock),
        model,
        skip_episode_provider(),
        settings,
        clock=clock,
        embedding_provider=embedding,
    )
    new_session = second.start_session.execute()
    await second.talk.execute(
        TalkInput(RECENT, "public-recent", "public-recent-request", new_session.session_id)
    )
    scope = CalendarActionScope(
        identity_id=UUID(identity),
        profile_id=uuid4(),
        session_id=UUID(new_session.session_id),
        generation=uuid4(),
        provider="synthetic_fixture",
        account_id="public-account",
        calendar_id="public-work",
        timezone="Europe/Moscow",
    )
    return reopened, second, model, clock, scope


def make_request(scope: CalendarActionScope) -> CalendarPlanningRequest:
    return CalendarPlanningRequest(
        request_id=uuid4(),
        scope=scope,
        user_text=QUERY,
        title="PUBLIC MEETING",
        window_start=NOW + timedelta(days=1),
        window_end=NOW + timedelta(days=1, hours=10),
        duration_minutes=60,
    )


def make_service(
    core: ConversationServices, scope: CalendarActionScope, clock: PlanningClock, tmp_path: Path
) -> tuple[CalendarAssistantService, PlanningSource, FixtureCalendarActionProvider]:
    provider = FixtureCalendarActionProvider(clock)
    source = PlanningSource(scope, clock)
    source.effect_provider = provider
    source.events = (
        CalendarObservedEvent(
            event_ref="public-busy",
            calendar_id=scope.calendar_id,
            title="PUBLIC_BUSY_TITLE",
            starts_at=NOW + timedelta(days=1),
            ends_at=NOW + timedelta(days=1, hours=3),
            status="tentative",
        ),
    )
    owner = CalendarActionOwner(
        SQLiteCalendarActionStore(tmp_path / "private-calendar" / "actions.sqlite3"),
        provider,
        clock=clock,
    )
    service = CalendarAssistantService(
        reasoner=CoreCalendarReasoner(core.talk, core.emotion_status, "fake-a:fixture-a", clock),
        source=source,
        actions=owner,
        clock=clock,
        live_scope=lambda: source.scope,
    )
    return service, source, provider
