"""Synthetic paired-client tests against the one existing canonical Core owner."""

import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from satori.core.conversation import (
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ProviderUnavailable,
)
from satori.desktop.backend import DesktopBackend, DesktopBackendError, DesktopProviders
from satori.desktop.worker import WorkerRequest, dispatch, run_worker, safe_error
from tests.fakes import FakeConversationProvider
from tests.test_conversation import skip_episode_provider, success_response

REPOSITORY = Path(__file__).resolve().parents[1]


class BlockingFirstProvider(FakeConversationProvider):
    """Hold the first provider call while another client/caller changes state."""

    def __init__(self) -> None:
        super().__init__(response=success_response("Готово."))
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        return await super().generate(request)


def activated_backend(
    directory: Path, provider: FakeConversationProvider | None = None
) -> tuple[DesktopBackend, FakeConversationProvider]:
    provider = provider or FakeConversationProvider(response=success_response("Готово."))
    backend = DesktopBackend(
        directory,
        REPOSITORY,
        providers=DesktopProviders(provider, skip_episode_provider()),
    )
    backend.open()
    backend.activate()
    return backend, provider


def test_desktop_and_phone_keep_exact_sessions_under_concurrent_turns(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider = BlockingFirstProvider()
        backend, _ = activated_backend(tmp_path, provider)
        desktop_a = str(backend.new_session()["session_id"])
        mobile_b = str((await backend.new_mobile_session())["session_id"])
        assert backend.status()["session_id"] == desktop_a
        try:
            desktop = asyncio.create_task(backend.send("Вопрос на Mac", "desktop-a"))
            await asyncio.wait_for(provider.started.wait(), timeout=10)
            phone = asyncio.create_task(
                backend.send_for_session("Телефонный вопрос", "phone-b", mobile_b)
            )
            await asyncio.sleep(0)
            desktop_c = str(backend.new_session()["session_id"])
            assert provider.calls == 1
            provider.release.set()
            first, second = await asyncio.gather(desktop, phone)
            assert first["session_id"] == desktop_a
            assert second["session_id"] == mobile_b
            assert backend.status()["session_id"] == desktop_c
            a = backend.history(desktop_a)["interactions"]
            b = backend.history(mobile_b)["interactions"]
            assert isinstance(a, list)
            assert isinstance(b, list)
            assert [item["client_request_id"] for item in a] == ["desktop-a"]
            assert [item["client_request_id"] for item in b] == ["phone-b"]
            assert backend.history(desktop_c)["interactions"] == []
            assert provider.calls == 2
        finally:
            provider.release.set()
            await backend.close()

    asyncio.run(scenario())


def test_mobile_replay_conflicts_and_read_only_reconciliation(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend, provider = activated_backend(tmp_path)
        session = str((await backend.new_mobile_session())["session_id"])
        other = str((await backend.new_mobile_session())["session_id"])
        assert backend.status()["session_id"] is None
        try:
            unknown = backend.request_status(session, "never-dispatched")
            assert unknown == {
                "state": "unknown",
                "session_id": session,
                "client_request_id": "never-dispatched",
                "interaction_id": None,
                "text": None,
            }
            assert not provider.requests
            reply = await backend.send_for_session("Привет", "mobile-replay", session)
            replay = await backend.send_for_session("Привет", "mobile-replay", session)
            assert reply["interaction_id"] == replay["interaction_id"]
            assert replay["replayed"] is True
            assert replay["voice_delivery"] is None
            assert backend.request_status(session, "mobile-replay") == {
                "state": "completed",
                "session_id": session,
                "client_request_id": "mobile-replay",
                "interaction_id": reply["interaction_id"],
                "text": "Готово.",
            }
            assert backend.request_status(other, "mobile-replay")["state"] == "unknown"
            with pytest.raises(DesktopBackendError, match="request_id_conflict"):
                await backend.send_for_session("Изменённый запрос", "mobile-replay", session)
            with pytest.raises(DesktopBackendError, match="request_id_conflict"):
                await backend.send_for_session("Привет", "mobile-replay", other)
            assert len(provider.requests) == 1
            assert backend.status()["session_id"] is None
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_concurrent_initial_desktop_turns_keep_one_default_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend, provider = activated_backend(tmp_path)
        try:
            first, second = await asyncio.gather(
                backend.send("Первый вопрос", "initial-desktop-a"),
                backend.send("Второй вопрос", "initial-desktop-b"),
            )
            assert first["session_id"] == second["session_id"]
            assert backend.status()["session_id"] == first["session_id"]
            assert len(backend.history()["sessions"]) == 1  # type: ignore[arg-type]
            assert len(provider.requests) == 2
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_status_uses_owner_history_after_restart_not_ui_delivery_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        backend, _ = activated_backend(tmp_path)
        session = str((await backend.new_mobile_session())["session_id"])
        reply = await backend.send_for_session("Привет", "durable-mobile-request", session)
        await backend.close()
        restarted, provider = activated_backend(tmp_path)
        monkeypatch.setattr("satori.desktop.backend.MAX_READ_CHARS", 0)
        try:
            assert restarted.history(session)["interactions"] == []
            assert restarted.history(session)["truncated"] is True
            status = restarted.request_status(session, "durable-mobile-request")
            assert status["state"] == "completed"
            assert status["interaction_id"] == reply["interaction_id"]
            assert status["text"] == "Готово."
            assert not provider.requests
            assert restarted.status()["session_id"] is None
        finally:
            await restarted.close()

    asyncio.run(scenario())


def test_missing_closed_and_foreign_profile_sessions_do_not_dispatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend, provider = activated_backend(tmp_path / "profile-a")
        foreign, foreign_provider = activated_backend(tmp_path / "profile-b")
        session = str((await backend.new_mobile_session())["session_id"])
        try:
            await backend.send_for_session("Привет", "closed-request", session)
            # The fixture uses the existing application owner, not direct SQL.
            backend._conversation().close_session.execute(session)
            assert backend.request_status(session, "closed-request")["state"] == "completed"
            with pytest.raises(DesktopBackendError, match="session_closed"):
                await backend.send_for_session("Привет", "closed-request", session)
            for runtime in (backend, foreign):
                with pytest.raises(DesktopBackendError, match="session_missing"):
                    await runtime.send_for_session("Привет", "missing", "missing-session")
                with pytest.raises(DesktopBackendError, match="session_missing"):
                    runtime.request_status("missing-session", "missing")
            with pytest.raises(DesktopBackendError, match="session_missing"):
                await foreign.send_for_session("Привет", "closed-request", session)
            with pytest.raises(DesktopBackendError, match="session_missing"):
                foreign.request_status(session, "closed-request")
            assert len(provider.requests) == 1
            assert not foreign_provider.requests
        finally:
            await backend.close()
            await foreign.close()

    asyncio.run(scenario())


def test_cancelled_caller_does_not_release_owner_or_imply_uncommitted(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider = BlockingFirstProvider()
        backend, _ = activated_backend(tmp_path, provider)
        session = str((await backend.new_mobile_session())["session_id"])
        try:
            caller = asyncio.create_task(
                backend.send_for_session("Первый запрос", "cancelled-caller", session)
            )
            await asyncio.wait_for(provider.started.wait(), timeout=10)
            assert backend.request_status(session, "cancelled-caller")["state"] == "pending"
            assert backend.request_status(session, "cancelled-caller")["text"] is None
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            following = asyncio.create_task(
                backend.send_for_session("Следующий запрос", "following-request", session)
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert provider.calls == 1
            assert not following.done()
            provider.release.set()
            await following
            assert backend.request_status(session, "cancelled-caller")["state"] == "completed"
            assert backend.request_status(session, "following-request")["state"] == "completed"
            assert provider.calls == 2
        finally:
            provider.release.set()
            await backend.close()

    asyncio.run(scenario())


def test_close_drains_active_turn_and_fences_queued_work_even_if_close_caller_cancels(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        provider = BlockingFirstProvider()
        backend, _ = activated_backend(tmp_path, provider)
        desktop = str(backend.new_session()["session_id"])
        active = asyncio.create_task(backend.send("Текущий запрос", "active-request"))
        await asyncio.wait_for(provider.started.wait(), timeout=10)
        queued = asyncio.create_task(
            backend.send_for_session("Очередной запрос", "queued-request", desktop)
        )
        queued_session = asyncio.create_task(backend.new_mobile_session())
        await asyncio.sleep(0)
        closing = asyncio.create_task(backend.close())
        await asyncio.sleep(0)
        assert not closing.done()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        provider.release.set()
        assert (await active)["session_id"] == desktop
        for operation in (queued, queued_session):
            with pytest.raises(DesktopBackendError, match="runtime_closed"):
                await operation
        assert (await backend.close())["closed"] is True
        assert provider.calls == 1
        with pytest.raises(DesktopBackendError, match="runtime_closed"):
            await backend.new_mobile_session()
        with pytest.raises(DesktopBackendError, match="runtime_closed"):
            await backend.send_for_session("Поздний", "late-request", desktop)
        restarted, restarted_provider = activated_backend(tmp_path)
        try:
            assert restarted.request_status(desktop, "active-request")["state"] == "completed"
            assert restarted.request_status(desktop, "queued-request")["state"] == "unknown"
            assert len(restarted.history()["sessions"]) == 1  # type: ignore[arg-type]
            assert not restarted_provider.requests
        finally:
            await restarted.close()

    asyncio.run(scenario())


def test_failed_request_status_contains_no_uncommitted_reply_or_exception(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend, provider = activated_backend(tmp_path)
        session = str((await backend.new_mobile_session())["session_id"])
        provider.error = ProviderUnavailable(
            "fake",
            "fixture",
            "private-provider-detail",
            reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
        )
        try:
            with pytest.raises(ProviderUnavailable):
                await backend.send_for_session("Привет", "failed-mobile", session)
            status = backend.request_status(session, "failed-mobile")
            assert status["state"] == "failed"
            assert status["text"] is None
            assert "private-provider-detail" not in json.dumps(status)
            assert "private-provider-detail" not in safe_error(provider.error).model_dump_json()
            assert len(provider.requests) == 1
        finally:
            await backend.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("text", "request_id", "session_id"),
    [
        (" ", "request", "session"),
        ("x" * 8001, "request", "session"),
        ("Привет", "", "session"),
        ("Привет", " request", "session"),
        ("Привет", "x" * 129, "session"),
        ("Привет", "request", "session "),
        ("Привет", "request", "x" * 129),
    ],
)
def test_mobile_boundary_rejects_unbound_or_oversized_input(
    tmp_path: Path, text: str, request_id: str, session_id: str
) -> None:
    async def scenario() -> None:
        backend, provider = activated_backend(tmp_path)
        try:
            with pytest.raises(DesktopBackendError, match="invalid_request"):
                await backend.send_for_session(text, request_id, session_id)
            assert backend.history()["sessions"] == []
            assert not provider.requests
        finally:
            await backend.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("command", "data"),
    [
        ("send_for_session", {"text": "private-draft", "request_id": "r"}),
        ("send_for_session", {"session_id": "s", "request_id": "r"}),
        ("send_for_session", {"session_id": "s", "text": "private-draft"}),
        ("request_status", {"session_id": "s"}),
        ("request_status", {"request_id": "r"}),
        ("request_status", {"session_id": "s", "request_id": "r", "text": "private-draft"}),
        ("new_mobile_session", {"session_id": "s"}),
        ("new_mobile_session", {"request_id": "x" * 129}),
        ("send", {"session_id": "s", "text": "private-draft", "request_id": "r"}),
    ],
)
def test_worker_mobile_fields_are_strict_and_errors_are_redacted(
    command: str, data: dict[str, str]
) -> None:
    with pytest.raises(ValidationError) as caught:
        WorkerRequest.model_validate({"id": "wire", "command": command, "data": data})
    assert safe_error(caught.value).code == "command_rejected"
    assert "private-draft" not in safe_error(caught.value).model_dump_json()


def test_owned_worker_dispatches_mobile_commands_without_desktop_switch(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend, provider = activated_backend(tmp_path)
        desktop = str(backend.new_session()["session_id"])
        created = await dispatch(
            backend,
            WorkerRequest.model_validate(
                {
                    "id": "new-mobile",
                    "command": "new_mobile_session",
                    "data": {"request_id": "gateway-owned-create"},
                }
            ),
        )
        mobile = str(created["session_id"])
        commands = [
            {
                "id": "mobile-send",
                "command": "send_for_session",
                "data": {"session_id": mobile, "request_id": "wire-r", "text": "Привет"},
            },
            {
                "id": "mobile-status",
                "command": "request_status",
                "data": {"session_id": mobile, "request_id": "wire-r"},
            },
            {"id": "desktop-status", "command": "status"},
            {"id": "stop", "command": "shutdown"},
        ]
        output = StringIO()
        assert (
            await run_worker(
                backend,
                StringIO("".join(json.dumps(item) + "\n" for item in commands)),
                output,
            )
            == 0
        )
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        assert all(item["event"] == "result" for item in responses)
        assert responses[0]["data"]["session_id"] == mobile
        assert responses[1]["data"]["state"] == "completed"
        assert responses[2]["data"]["session_id"] == desktop
        assert len(provider.requests) == 1

    asyncio.run(scenario())
