"""Late semantic routes cannot outlive their real lease, Core scope or Window context."""

import asyncio
import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from PySide6.QtCore import QProcess

from satori.application.assistant.routing import AssistantReadRoute
from satori.application.assistant.source_answer_contracts import (
    SourceAnswerError,
    SourceTaskLease,
    SourceTaskRequest,
    SourceTaskScope,
)
from satori.application.assistant.source_answer_reasoning import CoreSourceAnswerReasoner
from satori.application.assistant.source_answer_service import SourceAnswerService
from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.composition import build_initial_self_services
from satori.core.conversation import ConversationProviderRequest, ConversationProviderResponse
from satori.desktop.backend import DesktopBackend
from satori.desktop.source_answer_bridge import DesktopSourceAnswerOperations
from satori.desktop.worker import WorkerRequest, dispatch
from satori.infrastructure.assistant.source_answer_lease import (
    SourceTaskLeaseOwner,
    SourceTaskLeaseReader,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.providers.openai_http import OpenAIHttpClient
from tests.calendar_planning_fixture import seeded_core
from tests.test_conversation import success_response
from tests.test_desktop_openai_window import context
from tests.test_telegram_chat_handoff import app as app
from tests.test_telegram_chat_handoff import window as window


@pytest.fixture(autouse=True)
def forbid_real_api_or_child(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("router_lifecycle_fixture_crossed_external_boundary")

    monkeypatch.setattr(OpenAIHttpClient, "post_json", forbidden)
    monkeypatch.setattr(QProcess, "start", forbidden)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("revoke", "source_task_lease_unavailable"),
        ("session", "source_core_scope_mismatch"),
        ("provider", "public_provider_changed"),
        ("response_provider", "assistant_route_invalid"),
    ],
)
def test_route_rechecks_real_lease_and_core_after_provider_await(
    migrated_database: Database, tmp_path: Path, mutation: str, reason: str
) -> None:
    async def scenario() -> None:
        database, core, _, clock, scope = await seeded_core(migrated_database, tmp_path)
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
        provider = ["fake-a:fixture-a"]
        lease = SourceTaskLease(
            lease_id=uuid4(),
            request_id=request.request_id,
            scope=request.scope,
            request_digest=request.digest,
            sources=request.sources,
            provider_id=provider[0],
            disclosure_authorized=True,
            issued_at=clock(),
            expires_at=request.expires_at,
            monotonic_deadline=220,
            ui_pid=os.getpid(),
            os_uid=os.getuid(),
        )
        owner = SourceTaskLeaseOwner(tmp_path, clock=clock, monotonic=lambda: 100)
        owner.issue(lease)
        reader = SourceTaskLeaseReader(
            tmp_path, lease, clock=clock, monotonic=lambda: 100, parent_pid=os.getpid
        )

        def require_live() -> None:
            reader.require()
            if provider[0] != lease.provider_id:
                raise SourceAnswerError("public_provider_changed")

        calls: list[ConversationProviderRequest] = []

        class Model:
            async def generate(
                self, generation: ConversationProviderRequest, /
            ) -> ConversationProviderResponse:
                admit_provider_dispatch()
                calls.append(generation)
                await asyncio.sleep(0)
                if mutation == "revoke":
                    owner.revoke()
                elif mutation == "session":
                    core.close_session.execute(str(scope.session_id))
                elif mutation == "provider":
                    provider[0] = "fake-b:fixture-b"
                return replace(
                    success_response('{"action":"source_answer","sources":["telegram"]}'),
                    provider="fake-b" if mutation == "response_provider" else "fake-a",
                    model="fixture-b" if mutation == "response_provider" else "fixture-a",
                )

        core.talk.provider = Model()
        service = SourceAnswerService(
            request=request,
            reasoner=CoreSourceAnswerReasoner(
                core.talk, core.emotion_status, "fake-a:fixture-a", clock
            ),
            require_live=require_live,
            clock=clock,
        )
        before = core.history.execute().interactions
        try:
            with pytest.raises(SourceAnswerError, match=reason):
                await service.route()
            assert len(calls) == 1
            assert core.history.execute().interactions == before
            with pytest.raises(SourceAnswerError):
                await service.route()
            assert len(calls) == 1
        finally:
            owner.revoke()
            database.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mutation", ["profile", "session", "identity", "provider", "wall_expiry", "tick_expiry"]
)
def test_late_route_cannot_read_or_send_after_window_context_changes(
    window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str
) -> None:
    w, commands = window
    context(w)
    w.supports_source_answers = True
    now, tick = [datetime.now(UTC)], [time.monotonic()]
    controller = w.source_answer
    controller._clock = lambda: now[0]
    controller._monotonic = lambda: tick[0]
    w.editor.setPlainText("Здорово, что мне Алексей писал в тг?")
    w.send()
    request = controller._request
    assert request is not None
    assert controller._pending == "route"
    assert controller._current(request)
    sent = list(commands)
    received: list[object] = []
    controller.route_ready.connect(lambda *args: received.append(args))
    dispatched: list[object] = []
    monkeypatch.setattr(w.assistant, "dispatch", dispatched.append)
    if mutation == "profile":
        w.calendar_panel.controller.set_profile(tmp_path / "different-public-profile")
    elif mutation == "session":
        w.session_id = str(uuid4())
    elif mutation == "identity":
        w._calendar_identity = str(uuid4())
    elif mutation == "provider":
        w._calendar_provider = "public-different:provider"
    elif mutation == "wall_expiry":
        now[0] = request.expires_at
    else:
        assert controller._lease is not None
        tick[0] = controller._lease.monotonic_deadline
    controller.accept_result(
        str(request.request_id),
        "route",
        {
            "status": "routed",
            "request_id": str(request.request_id),
            "route": {"action": "source_answer", "sources": ["telegram"]},
        },
    )
    assert received == []
    assert dispatched == []
    assert commands == sent
    assert controller._request is None
    assert controller._lease is None


def test_real_worker_bridge_routes_once_without_canonical_send_or_external_reads(
    migrated_database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only runtime qualification is a fixture; worker, lease, backend and Core route are real."""

    async def scenario() -> None:
        database, core, _, clock, scope = await seeded_core(migrated_database, tmp_path)
        backend = DesktopBackend(tmp_path, Path(__file__).parents[1], model="PUBLIC_ROUTER")
        backend._database = database
        backend._initial = build_initial_self_services(database, clock=clock)
        backend._services = core
        backend._session_id = str(scope.session_id)
        monkeypatch.setattr(DesktopBackend, "supports_source_answers", property(lambda self: True))

        def no_send(*args: object, **kwargs: object) -> object:
            raise AssertionError("route_dispatched_canonical_send")

        monkeypatch.setattr(backend, "send", no_send)
        calls: list[ConversationProviderRequest] = []

        class Model:
            async def generate(
                self, generation: ConversationProviderRequest, /
            ) -> ConversationProviderResponse:
                admit_provider_dispatch()
                calls.append(generation)
                return replace(
                    success_response('{"action":"source_answer","sources":["telegram"]}'),
                    provider="ollama",
                    model="PUBLIC_ROUTER",
                )

        core.talk.provider = Model()
        request = SourceTaskRequest(
            request_id=uuid4(),
            scope=SourceTaskScope(
                identity_id=scope.identity_id,
                profile_id=uuid5(NAMESPACE_URL, str(backend.data_directory)),
                session_id=scope.session_id,
                generation=uuid4(),
                timezone=scope.timezone,
            ),
            user_text="Здорово, что мне Алексей писал в тг?",
            sources=("mail", "telegram", "calls"),
            created_at=clock(),
            expires_at=clock() + timedelta(seconds=120),
        )
        lease = SourceTaskLease(
            lease_id=uuid4(),
            request_id=request.request_id,
            scope=request.scope,
            request_digest=request.digest,
            sources=request.sources,
            provider_id="ollama:PUBLIC_ROUTER",
            disclosure_authorized=True,
            issued_at=clock(),
            expires_at=request.expires_at,
            monotonic_deadline=220,
            ui_pid=os.getpid(),
            os_uid=os.getuid(),
        )
        owner = SourceTaskLeaseOwner(tmp_path, clock=clock, monotonic=lambda: 100)
        owner.issue(lease)
        bridge = DesktopSourceAnswerOperations(
            backend, clock=clock, monotonic=lambda: 100, parent_pid=os.getpid
        )
        wire = WorkerRequest.model_validate_json(
            json.dumps(
                {
                    "id": "PUBLIC_ROUTE_WIRE",
                    "command": "source_answer",
                    "data": {
                        "session_id": str(scope.session_id),
                        "request_id": str(request.request_id),
                        "source_answer": {
                            "operation": "route",
                            "request": request.model_dump(mode="json"),
                        },
                        "source_answer_lease": lease.model_dump(mode="json"),
                    },
                }
            )
        )
        before = core.history.execute().interactions
        try:
            result = await dispatch(backend, wire, source_answer=bridge)
            assert result["status"] == "routed"
            assert result["request_id"] == str(request.request_id)
            route = AssistantReadRoute.model_validate_json(json.dumps(result["route"]))
            assert route.action == "source_answer"
            assert route.sources == ("telegram",)
            assert len(calls) == 1
            assert core.history.execute().interactions == before
            replay = await dispatch(backend, wire, source_answer=bridge)
            assert replay["status"] == "unavailable"
            assert len(calls) == 1
            assert core.history.execute().interactions == before
        finally:
            bridge.close()
            owner.revoke()
            database.dispose()

    asyncio.run(scenario())
