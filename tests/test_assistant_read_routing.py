"""Semantic read proposals through the real Core and actual Window, without accounts."""

# ruff: noqa: RUF001 - public Russian owner requests.

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from satori.application.assistant.routing import AssistantReadRoute, route_intent
from satori.application.assistant.source_answer_contracts import (
    SourceAnswerError,
    SourceTaskRequest,
    SourceTaskScope,
)
from satori.application.assistant.source_answer_reasoning import CoreSourceAnswerReasoner
from satori.application.assistant.source_answer_service import SourceAnswerService
from satori.application.assistant.source_task_reads import (
    SOURCE_TASK_REFINEMENT,
    source_task_window,
)
from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.core.conversation import ConversationProviderRequest, ConversationProviderResponse
from satori.infrastructure.persistence.database import Database
from tests.calendar_planning_fixture import seeded_core
from tests.test_conversation import success_response
from tests.test_desktop_openai_window import context
from tests.test_source_answer_contracts import public_request
from tests.test_source_answer_desktop import Harness
from tests.test_telegram_chat_handoff import app as app
from tests.test_telegram_chat_handoff import window as window


def test_host_computes_calendar_date_and_rejects_invented_or_conflicting_date() -> None:
    request = public_request().model_copy(
        update={
            "user_text": "а в воскресенье стоит что-то?",
            "created_at": datetime(2026, 11, 18, 10, tzinfo=UTC),
            "expires_at": datetime(2026, 11, 18, 10, 2, tzinfo=UTC),
        }
    )
    route = AssistantReadRoute(action="calendar_agenda", day_literal="в воскресенье")
    intent = route_intent(request, route)
    assert intent is not None
    assert str(intent.day) == "2026-11-22"
    with pytest.raises(ValueError, match="not_literal"):
        route_intent(request, route.model_copy(update={"day_literal": "завтра"}))
    with pytest.raises(ValueError, match="date_mismatch"):
        route_intent(request.model_copy(update={"user_text": "Завтра или в воскресенье?"}), route)
    past = request.model_copy(update={"user_text": "А в прошлое воскресенье что было?"})
    intent = route_intent(past, route.model_copy(update={"day_literal": "воскресенье"}))
    assert intent is not None
    assert str(intent.day) == "2026-11-15"
    later = request.model_copy(update={"user_text": "А через неделю в воскресенье что будет?"})
    intent = route_intent(later, route)
    assert intent is not None
    assert intent.kind == "clarification"


@pytest.mark.parametrize(
    "proposal",
    [
        {"action": "calendar_create", "day_literal": "завтра"},
        {"action": "chat", "sources": ["mail"]},
        {"action": "source_answer", "sources": ["telegram", "telegram"]},
        {"action": "source_answer", "sources": ["mail"], "send": True},
    ],
)
def test_router_cannot_invent_write_or_permission_fields(proposal: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        AssistantReadRoute.model_validate(proposal)


def test_route_uses_same_core_provider_without_reading_or_learning_source_context(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database, core, _, clock, scope = await seeded_core(migrated_database, tmp_path)
        requests: list[ConversationProviderRequest] = []

        class Model:
            async def generate(
                self, request: ConversationProviderRequest, /
            ) -> ConversationProviderResponse:
                admit_provider_dispatch()
                requests.append(request)
                return replace(
                    success_response(
                        '{"action":"source_answer","sources":["telegram"],"sender_literal":"Алексей"}'
                    ),
                    provider="fake-a",
                    model="fixture-a",
                )

        core.talk.provider = Model()
        request = SourceTaskRequest(
            request_id=uuid4(),
            scope=SourceTaskScope(
                identity_id=scope.identity_id,
                profile_id=scope.profile_id,
                session_id=scope.session_id,
                generation=scope.generation,
                timezone=scope.timezone,
            ),
            user_text="Здорово, что мне Алексей писал в тг?",
            sources=("mail", "telegram", "calls"),
            created_at=clock(),
            expires_at=clock() + timedelta(seconds=120),
        )
        reasoner = CoreSourceAnswerReasoner(
            core.talk, core.emotion_status, "fake-a:fixture-a", clock
        )
        history = core.history.execute()
        try:
            route = await reasoner.route(request, lambda: None)
            assert route.sources == ("telegram",)
            assert core.history.execute() == history
            assert len(requests) == 1
            assert requests[0].parameters.max_output_tokens == 400
            packet = json.loads(requests[0].messages[-1].content)
            assert set(packet) == {"owner_request", "local_today", "timezone"}
            assert "PUBLIC_PREFERENCE" not in requests[0].messages[0].content
            execution = request.model_copy(
                update={
                    "request_id": uuid4(),
                    "sender_literal": route.sender_literal,
                    "sources": route.sources,
                }
            )
            service = SourceAnswerService(
                request=execution, reasoner=reasoner, require_live=lambda: None, clock=clock
            )
            plan = await service.plan()
            assert plan.telegram_match == "peer"
            assert plan.telegram_query == "Алексей"
            assert len(requests) == 1
            assert core.history.execute() == history

            def revoked() -> None:
                raise SourceAnswerError("revoked")

            with pytest.raises(SourceAnswerError, match="revoked"):
                await reasoner.route(request, revoked)
            assert len(requests) == 1
        finally:
            database.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("text", "proposal", "expected"),
    [
        (
            "а в воскресенье стоит что-то?",
            {"action": "calendar_agenda", "day_literal": "в воскресенье"},
            "calendar_agenda",
        ),
        (
            "Здорово, что мне Алексей писал в тг?",
            {"action": "source_answer", "sources": ["telegram"], "sender_literal": "Алексей"},
            "plan",
        ),
        ("Расскажи что-нибудь интересное про океан", {"action": "chat"}, "send"),
    ],
)
def test_real_window_routes_unmatched_request_before_canonical_send(
    window: Any,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    proposal: dict[str, Any],
    expected: str,
) -> None:
    w, requests = window
    context(w)
    monkeypatch.setattr(w.process, "write", lambda raw: len(raw))
    w.supports_source_answers = True
    dispatched: list[Any] = []
    monkeypatch.setattr(w.assistant, "dispatch", dispatched.append)
    w.editor.setPlainText(text)
    w.send()
    assert len(requests) == 1
    assert requests[0][0] == "source_answer"
    command = requests[0][1]["source_answer"]
    assert command["operation"] == "route"
    request = SourceTaskRequest.model_validate_json(json.dumps(command["request"]))
    w.source_answer.accept_result(
        str(request.request_id),
        "route",
        {
            "status": "routed",
            "request_id": str(request.request_id),
            "route": proposal,
        },
    )
    if expected == "calendar_agenda":
        assert len(dispatched) == 1
        assert dispatched[0].kind == expected
        assert len(requests) == 1
    elif expected == "send":
        assert requests[-1] == ("send", {"text": text, "request_id": str(request.request_id)})
    else:
        execution = requests[-1][1]["source_answer"]
        assert execution["operation"] == "plan"
        assert execution["request"]["sources"] == ["telegram"]
        assert execution["request"]["request_id"] != str(request.request_id)
        assert execution["request"]["expires_at"] == command["request"]["expires_at"]
        assert execution["request"]["sender_literal"] == "Алексей"


def test_new_draft_cancels_route_instead_of_sending_old_text(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, requests = window
    context(w)
    monkeypatch.setattr(w.process, "write", lambda raw: len(raw))
    w.supports_source_answers = True
    w.editor.setPlainText("а в воскресенье стоит что-то?")
    w.send()
    request = w.source_answer._request
    assert request is not None
    w.editor.setPlainText("PUBLIC newer draft")
    w.source_answer.accept_result(
        str(request.request_id),
        "route",
        {
            "status": "routed",
            "request_id": str(request.request_id),
            "route": {"action": "chat"},
        },
    )
    assert len(requests) == 1
    assert w.editor.toPlainText() == "PUBLIC newer draft"


def test_route_clarification_keeps_question_and_deadline_through_real_composer(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, requests = window
    context(w)
    monkeypatch.setattr(w.process, "write", lambda raw: len(raw))
    w.supports_source_answers = True
    w.editor.setPlainText("Когда я могу отдохнуть?")
    w.send()
    first = w.source_answer._request
    assert first is not None
    w.source_answer.accept_result(
        str(first.request_id),
        "route",
        {
            "status": "routed",
            "request_id": str(first.request_id),
            "route": {"action": "clarify", "clarification": "На какой день посмотреть планы?"},
        },
    )
    assert w.source_answer.pending_route_clarification
    w.editor.setPlainText("завтра")
    w.send()
    second = w.source_answer._request
    assert second is not None
    assert second.request_id != first.request_id
    assert second.user_text == first.user_text + SOURCE_TASK_REFINEMENT + "завтра"
    assert second.created_at == first.created_at
    assert second.expires_at == first.expires_at
    assert requests[-1][1]["source_answer"]["operation"] == "route"


def test_source_execution_after_midnight_uses_original_semantic_day(
    app: Any,
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.now = datetime(2026, 11, 18, 20, 59, 59, tzinfo=UTC)
    assert h.controller.try_route("Что мне сегодня Алексей писал в тг?")
    original = h.request
    window = source_task_window(original)
    lease = h.controller._lease
    assert lease is not None
    deadline = lease.monotonic_deadline
    h.now += timedelta(seconds=2)
    h.tick += 2
    route = AssistantReadRoute(
        action="source_answer", sources=("telegram",), sender_literal="Алексей"
    )
    assert h.controller.try_dispatch_routed(original, route, deadline)
    assert source_task_window(h.request) == window
    assert h.request.created_at == original.created_at
    lease = h.controller._lease
    assert lease is not None
    assert lease.monotonic_deadline == deadline
    h.controller.clear()
