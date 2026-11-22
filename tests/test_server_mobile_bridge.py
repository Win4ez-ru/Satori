"""Real synthetic Core plus mobile journal races; no API/account/private profile."""

# ruff: noqa: RUF001 - public synthetic Russian task commands

import asyncio
import copy
import http.client
import json
import ssl
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from satori.desktop.backend import DesktopProviders
from satori.desktop.mobile_gateway.broker import Broker
from satori.desktop.mobile_gateway.contracts import DeviceContext, GatewayFault, ScopeBinding
from satori.desktop.mobile_gateway.private_store import PrivateStore
from satori.server.mobile_bridge import HeadlessMobileBridge
from satori.server.mobile_gateway import LoopbackMobileGateway
from satori.server.runtime import HeadlessCommand, HeadlessError, HeadlessRuntime
from tests.fakes import FakeConversationProvider
from tests.test_conversation import skip_episode_provider
from tests.test_server_runtime import REPOSITORY, WaitingProvider, prepared

SCOPE = ScopeBinding("synthetic-principal", "synthetic-profile", 0)
EXPIRY = 0.0


@pytest.fixture(autouse=True)
def fresh_device_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep one device binding within a test, independent of collection/queue time.
    monkeypatch.setattr(sys.modules[__name__], "EXPIRY", time.time() + 1000)


@dataclass
class Store:
    data: dict[str, object] | None = None

    def read_json(self, name: str) -> dict[str, object] | None:
        assert name == "requests.json"
        return copy.deepcopy(self.data)

    def write_json(self, name: str, value: dict[str, object]) -> None:
        assert name == "requests.json"
        self.data = copy.deepcopy(value)


def context(device: str = "phone-one") -> DeviceContext:
    return DeviceContext(
        device,
        SCOPE,
        frozenset({"status", "history", "chat"}),
        EXPIRY,
        "synthetic-transport",
    )


async def wait_idle(bridge: HeadlessMobileBridge) -> None:
    for _ in range(1000):
        await asyncio.sleep(0.001)
        if not bridge._reserved:
            return
    raise AssertionError("synthetic bridge did not drain")


def broker(
    bridge: HeadlessMobileBridge,
    store: Any,
    authorize: Callable[[DeviceContext], None] = lambda _: None,
    *,
    max_poll: float = 120,
) -> Broker:
    return Broker(store, bridge, authorize, lambda: SCOPE, wait_seconds=0, max_poll=max_poll)


def test_real_send_history_sessions_and_two_device_ownership(tmp_path: Path) -> None:
    root, providers, first, second = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        bridge = HeadlessMobileBridge(runtime, lambda: SCOPE, timezone="Europe/Moscow")
        owner = broker(bridge, Store())
        ident = str(uuid4())
        assert (
            owner.submit(
                context(), "chat", {"sessionID": first, "text": "Synthetic greeting."}, ident
            )["state"]
            == "pending"
        )
        await wait_idle(bridge)
        assert owner.reconcile(context(), ident)["state"] == "completed"
        with pytest.raises(GatewayFault, match="forbidden"):
            owner.reconcile(context("phone-two"), ident)
        second_ident = str(uuid4())
        owner.submit(
            context("phone-two"),
            "chat",
            {"sessionID": second, "text": "Synthetic second session."},
            second_ident,
        )
        await wait_idle(bridge)
        assert owner.reconcile(context("phone-two"), second_ident)["sessionID"] == second
        with pytest.raises(GatewayFault, match="request_pending"):
            owner.read(context(), "history", {"sessionID": first})
        await wait_idle(bridge)
        # Reads have no retained outbox. Inspect the owner directly for isolation.
        history = await runtime.execute(HeadlessCommand(operation="history", session_id=first))
        other = await runtime.execute(HeadlessCommand(operation="history", session_id=second))
        assert isinstance(history["interactions"], list)
        assert isinstance(other["interactions"], list)
        assert len(history["interactions"]) == len(other["interactions"]) == 1
        assert history["interactions"][0]["session_id"] == first
        assert other["interactions"][0]["session_id"] == second
        assert isinstance(providers.conversation, FakeConversationProvider)
        assert len(providers.conversation.requests) == 2
        owner.close()
        await bridge.close()
        await runtime.close()

    asyncio.run(scenario())


def test_revoked_while_queued_never_enters_core(tmp_path: Path) -> None:
    root, providers, first, _ = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        bridge = HeadlessMobileBridge(runtime, lambda: SCOPE, timezone="Europe/Moscow")
        revoked = False

        def authorize(_: DeviceContext) -> None:
            if revoked:
                raise GatewayFault("device_revoked", 401)

        owner = broker(bridge, Store(), authorize)
        owner.submit(
            context(), "chat", {"sessionID": first, "text": "Synthetic text."}, str(uuid4())
        )
        revoked = True
        await wait_idle(bridge)
        assert not (await runtime.execute(HeadlessCommand(operation="history", session_id=first)))[
            "interactions"
        ]
        assert isinstance(providers.conversation, FakeConversationProvider)
        assert not providers.conversation.requests
        owner.close()
        await bridge.close()
        await runtime.close()

    asyncio.run(scenario())


def test_runtime_guard_runs_after_async_task_queue_wait(tmp_path: Path) -> None:
    root, providers, first, _ = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        allowed = True
        checked = []

        def guard() -> None:
            checked.append(allowed)
            if not allowed:
                raise GatewayFault("device_revoked", 401)

        task = asyncio.create_task(
            runtime.execute(
                HeadlessCommand(
                    operation="send",
                    session_id=first,
                    request_id="synthetic-guard",
                    text="Synthetic text.",
                ),
                dispatch_guard=guard,
            )
        )
        await asyncio.sleep(0)
        allowed = False
        with pytest.raises(HeadlessError, match="command_failed"):
            await task
        assert checked == [False]
        assert not (await runtime.execute(HeadlessCommand(operation="history", session_id=first)))[
            "interactions"
        ]
        await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["chat", "new_session"])
@pytest.mark.parametrize("denial", ["device_revoked", "device_expired"])
def test_guard_after_blocked_backend_lane_prevents_effect(
    tmp_path: Path, operation: str, denial: str
) -> None:
    root, providers, first, _ = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        before = await runtime.execute(HeadlessCommand(operation="sessions"))
        assert isinstance(before["sessions"], list)
        bridge = HeadlessMobileBridge(runtime, lambda: SCOPE, timezone="Europe/Moscow")
        denied = False

        def authorize(_: DeviceContext) -> None:
            if denied:
                raise GatewayFault(denial, 401)

        owner = broker(bridge, Store(), authorize)
        backend = runtime._backend
        assert backend is not None
        await backend._turn_lock.acquire()
        try:
            if operation == "chat":
                owner.submit(
                    context(), "chat", {"sessionID": first, "text": "Synthetic text."}, str(uuid4())
                )
            else:
                owner.submit(context(), "new_session", {}, str(uuid4()))
            await asyncio.sleep(0.01)
            assert runtime.metadata()["command_pending"]
            denied = True
        finally:
            backend._turn_lock.release()
        await wait_idle(bridge)
        after = await runtime.execute(HeadlessCommand(operation="sessions"))
        assert isinstance(after["sessions"], list)
        assert len(before["sessions"]) == len(after["sessions"])
        assert not (await runtime.execute(HeadlessCommand(operation="history", session_id=first)))[
            "interactions"
        ]
        assert isinstance(providers.conversation, FakeConversationProvider)
        assert not providers.conversation.requests
        owner.close()
        await bridge.close()
        await runtime.close()

    asyncio.run(scenario())


def test_timeout_reconcile_and_restart_do_not_duplicate_send(tmp_path: Path) -> None:
    root, _, first, _ = prepared(tmp_path)

    async def scenario() -> None:
        provider = WaitingProvider()
        runtime = HeadlessRuntime(
            root, REPOSITORY, providers=DesktopProviders(provider, skip_episode_provider())
        )
        await runtime.start()
        bridge = HeadlessMobileBridge(runtime, lambda: SCOPE, timezone="Europe/Moscow")
        store = PrivateStore(tmp_path.resolve() / "gateway")
        owner = broker(bridge, store, max_poll=0.02)
        ident = str(uuid4())
        payload = {"sessionID": first, "text": "Synthetic text."}
        owner.submit(context(), "chat", payload, ident)
        await asyncio.wait_for(provider.entered.wait(), 5)
        await asyncio.sleep(0.03)
        assert owner.reconcile(context(), ident)["state"] == "unknown"
        assert owner.submit(context(), "chat", payload, ident)["state"] == "unknown"
        provider.release.set()
        await wait_idle(bridge)
        owner.close()
        # Reload the exact durable journal, without replaying its unknown intent.
        restarted = broker(bridge, store)
        assert restarted.submit(context(), "chat", payload, ident)["state"] == "unknown"
        restarted.reconcile(context(), ident)
        await wait_idle(bridge)
        assert restarted.reconcile(context(), ident)["state"] == "completed"
        assert len(provider.requests) == 1
        restarted.close()
        await bridge.close()
        await runtime.close()
        store.close()

    asyncio.run(scenario())


def test_new_session_journal_is_durable_and_never_replayed(tmp_path: Path) -> None:
    root, providers, _, _ = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        bridge = HeadlessMobileBridge(runtime, lambda: SCOPE, timezone="Europe/Moscow")
        store = PrivateStore(tmp_path.resolve() / "gateway")
        owner = broker(bridge, store)
        before = await runtime.execute(HeadlessCommand(operation="sessions"))
        ident = str(uuid4())
        owner.submit(context(), "new_session", {}, ident)
        await wait_idle(bridge)
        complete = owner.submit(context(), "new_session", {}, ident)
        assert complete["state"] == "completed"
        assert isinstance(complete["sessionID"], str)
        owner.close()
        restarted = broker(bridge, store)
        receipt = restarted.submit(context(), "new_session", {}, ident)
        assert receipt["state"] == "unknown"  # Reply bodies are RAM-only; do not invent recovery.
        assert receipt["sessionID"] == complete["sessionID"]
        after = await runtime.execute(HeadlessCommand(operation="sessions"))
        assert isinstance(before["sessions"], list)
        assert isinstance(after["sessions"], list)
        assert len(after["sessions"]) == len(before["sessions"]) + 1
        with pytest.raises(HeadlessError, match="invalid_command"):
            await runtime.execute(HeadlessCommand(operation="new_session", request_id=str(uuid4())))
        restarted.close()
        await bridge.close()
        await runtime.close()
        store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "text",
    [
        "что у меня сегодня?",
        "Что у меня запланировано на завтра?",
        "Что у меня завтра запланировано?",
        "Есть ли у меня свободное время завтра?",
        "Есть ли у меня завтра свободные окна?",
        "Есть ли у меня сегодня свободное время завтра?",
        "когда сегодня свободные окна?",
        "найди в почте проверка",
        "найди в телеграме проверка",
        "мои напоминания",
        "напомни завтра вечером",
        "отмени напоминание",
    ],
)
def test_task_requests_never_reach_canonical_or_model(tmp_path: Path, text: str) -> None:
    root, providers, first, _ = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        bridge = HeadlessMobileBridge(runtime, lambda: SCOPE, timezone="Europe/Moscow")
        store = Store()
        owner = broker(bridge, store)
        ident = str(uuid4())
        owner.submit(context(), "chat", {"sessionID": first, "text": text}, ident)
        await wait_idle(bridge)
        result = owner.reconcile(context(), ident)
        assert result["kind"] == "assistant"
        assert isinstance(result["card"], dict)
        assert result["card"]["status"] == "permission_required"
        assert result["text"] == ""
        assert result["interactionID"] is None
        assert not (await runtime.execute(HeadlessCommand(operation="history", session_id=first)))[
            "interactions"
        ]
        assert isinstance(providers.conversation, FakeConversationProvider)
        assert not providers.conversation.requests
        assert text not in json.dumps(store.data, ensure_ascii=False)
        owner.close()
        await bridge.close()
        await runtime.close()

    asyncio.run(scenario())


def test_shutdown_waits_for_active_send_and_retains_core_lease(tmp_path: Path) -> None:
    root, _, first, _ = prepared(tmp_path)

    async def scenario() -> None:
        provider = WaitingProvider()
        runtime = HeadlessRuntime(
            root, REPOSITORY, providers=DesktopProviders(provider, skip_episode_provider())
        )
        await runtime.start()
        bridge = HeadlessMobileBridge(runtime, lambda: SCOPE, timezone="Europe/Moscow")
        owner = broker(bridge, Store())
        owner.submit(
            context(), "chat", {"sessionID": first, "text": "Synthetic text."}, str(uuid4())
        )
        await asyncio.wait_for(provider.entered.wait(), 5)
        closing = asyncio.create_task(bridge.close())
        await asyncio.sleep(0)
        assert not closing.done()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        other = HeadlessRuntime(
            root, REPOSITORY, providers=DesktopProviders(provider, skip_episode_provider())
        )
        with pytest.raises(HeadlessError, match="owner_busy"):
            await other.start()
        provider.release.set()
        await bridge.close()
        assert len(provider.requests) == 1
        owner.close()
        await runtime.close()
        await other.start()
        await other.close()

    asyncio.run(scenario())


def test_imports_are_headless_and_do_not_construct_a_backend() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import sys;import satori.server.mobile_gateway;"
                "assert not any(x.startswith(('PySide6','PyQt')) for x in sys.modules);"
                "print('headless')"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "headless\n"


def test_gateway_before_start_is_inert_and_invalid_ports_reject(tmp_path: Path) -> None:
    root, providers, _, _ = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        namespace = tmp_path.resolve() / "gateway"
        gateway = LoopbackMobileGateway(
            runtime, lambda: SCOPE, namespace, timezone="Europe/Moscow", port=0
        )
        assert gateway.port is None
        assert not namespace.exists()
        with pytest.raises(GatewayFault):
            gateway.begin_pairing({"chat"})
        assert (await gateway.close())["state"] == "ready"
        assert runtime.metadata()["state"] == "ready"
        with pytest.raises(GatewayFault, match="core_unavailable"):
            gateway.start()
        assert not namespace.exists()
        with pytest.raises(GatewayFault):
            LoopbackMobileGateway(
                runtime, lambda: SCOPE, namespace, timezone="Europe/Moscow", port=True
            )
        await runtime.close()

    asyncio.run(scenario())


def test_actual_loopback_tls_pair_chat_revoke_and_owner_close(tmp_path: Path) -> None:
    root, providers, first, _ = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        namespace = tmp_path.resolve() / "gateway"
        gateway = LoopbackMobileGateway(
            runtime, lambda: SCOPE, namespace, timezone="Europe/Moscow", port=0
        )
        assert gateway.start()
        assert gateway.port is not None
        with pytest.raises(GatewayFault, match="forbidden"):
            gateway.begin_pairing({"reminders"})
        code = gateway.begin_pairing({"chat", "history"})

        def request(
            method: str,
            path: str,
            body: dict[str, object] | None = None,
            token: str | None = None,
        ) -> tuple[int, dict[str, Any]]:
            trust = ssl.create_default_context(cafile=str(namespace / "server-cert.pem"))
            connection = http.client.HTTPSConnection(
                "127.0.0.1", gateway.port, context=trust, timeout=5
            )
            headers = {"Accept": "application/json"}
            if token is not None:
                headers["Authorization"] = "Bearer " + token
            content = None if body is None else json.dumps(body).encode()
            if content is not None:
                headers["Content-Type"] = "application/json"
            try:
                connection.request(method, path, body=content, headers=headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        status, pair = await asyncio.to_thread(
            request, "POST", "/v1/pair", {"code": code, "deviceName": "Synthetic phone"}
        )
        assert status == 200
        token = pair["data"]["token"]
        status, result = await asyncio.to_thread(request, "GET", "/v1/status", None, token)
        assert status == 200
        assert result["data"]["ready"]
        assert all(row["connection"] == "disconnected" for row in result["data"]["sourceStatuses"])
        ident = str(uuid4())
        body: dict[str, object] = {
            "requestID": ident,
            "sessionID": first,
            "text": "Synthetic greeting.",
        }
        status, result = await asyncio.to_thread(request, "POST", "/v1/chat", body, token)
        assert status == 200
        # Completion is asynchronous even with a fake model: a loaded host may
        # return the durable pending receipt before the Core commits the reply.
        async with asyncio.timeout(30):
            while result["data"]["state"] == "pending":
                await asyncio.sleep(0.5)
                status, result = await asyncio.to_thread(
                    request, "GET", "/v1/requests/" + ident, None, token
                )
                assert status == 200
        assert result["data"]["state"] == "completed"
        repeated = await asyncio.to_thread(request, "POST", "/v1/chat", body, token)
        assert repeated == (status, result)
        status, history = await asyncio.to_thread(
            request, "GET", "/v1/history?session_id=" + first, None, token
        )
        assert status == 200
        assert len(history["data"]["messages"]) == 2
        gateway.revoke_all()
        status, _ = await asyncio.to_thread(request, "GET", "/v1/status", None, token)
        assert status in {401, 403}
        assert (await gateway.close())["state"] == "closed"
        assert isinstance(providers.conversation, FakeConversationProvider)
        assert len(providers.conversation.requests) == 1

    asyncio.run(scenario())
