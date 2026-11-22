"""Actual synthetic localhost TLS, never LAN, Core, GUI or a real profile."""

import hashlib
import json
import socket
import ssl
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import uuid4

import pytest

from satori.desktop.mobile_gateway.auth import AuthOwner
from satori.desktop.mobile_gateway.broker import Broker
from satori.desktop.mobile_gateway.contracts import (
    BridgeCall,
    BridgeResult,
    GatewayFault,
    ScopeBinding,
)
from satori.desktop.mobile_gateway.private_store import PrivateStore
from satori.desktop.mobile_gateway.server import GatewayConfig, GatewayServer
from satori.desktop.mobile_gateway.tls import (
    TLSIdentity,
    advertised_host,
    allowed_bind,
    prepare_tls,
)


class Bridge:
    def __init__(self) -> None:
        self.calls: list[BridgeCall] = []
        self.results: dict[str, dict[str, object]] = {}
        self.hold = False
        self.pending: list[tuple[BridgeCall, Callable[[BridgeResult], None]]] = []

    def submit(self, call: BridgeCall, complete: Callable[[BridgeResult], None]) -> None:
        call.dispatch_guard()
        self.calls.append(call)
        if call.operation == "status":
            data: dict[str, object] = {
                "ready": True,
                "coreState": "ready",
                "profileID": call.scope.profile_id,
                "sessionID": "desktop-selection",
                "sourceStatuses": [],
            }
        elif call.operation == "sessions":
            data = {"activeSessionID": "desktop-selection", "sessions": [], "truncated": False}
        elif call.operation == "history":
            data = {"sessionID": call.payload["sessionID"], "messages": [], "truncated": False}
        elif call.operation == "reminders":
            data = {"items": [], "truncated": False}
        elif call.operation == "new_session":
            data = {
                "requestID": call.request_id,
                "state": "completed",
                "sessionID": "phone-session",
            }
        elif call.operation == "chat":
            data = {
                "requestID": call.request_id,
                "state": "completed",
                "kind": "conversation",
                "sessionID": call.payload["sessionID"],
                "text": "synthetic committed reply",
                "interactionID": "synthetic-interaction",
                "card": None,
            }
        else:
            assert call.operation == "reconcile"
            data = self.results[str(call.payload["requestID"])]
        if call.operation in {"chat", "new_session"}:
            self.results[str(call.request_id)] = data
        if self.hold and call.operation in {"chat", "new_session"}:
            self.pending.append((call, complete))
            return
        complete(BridgeResult(call.call_id, call.request_id, call.scope, call.device_id, data=data))


@dataclass
class Runtime:
    root: Path
    store: PrivateStore
    auth: AuthOwner
    broker: Broker
    bridge: Bridge
    server: GatewayServer
    identity: TLSIdentity
    scope: list[ScopeBinding]

    def connection(self, *, host: str = "127.0.0.1", pin: str | None = None) -> ssl.SSLSocket:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cafile=str(self.identity.certificate))
        raw = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        try:
            secure = context.wrap_socket(raw, server_hostname=host)
            certificate = secure.getpeercert(binary_form=True)
            assert certificate is not None
            actual = hashlib.sha256(certificate).hexdigest()
            if actual != (pin or self.identity.sha256):
                secure.close()
                raise ssl.SSLError("synthetic pin mismatch")
            return secure
        except Exception:
            raw.close()
            raise

    def raw(self, request: bytes) -> tuple[int, dict[str, Any]]:
        with self.connection() as connection:
            connection.sendall(request)
            result = bytearray()
            while chunk := connection.recv(65536):
                result.extend(chunk)
        head, body = bytes(result).split(b"\r\n\r\n", 1)
        return int(head.split(b" ", 2)[1]), json.loads(body)

    def request(
        self, method: str, path: str, data: object = None, token: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        body = b"" if data is None else json.dumps(data).encode()
        headers = [
            f"{method} {path} HTTP/1.1",
            f"Host: 127.0.0.1:{self.server.port}",
            "Connection: close",
        ]
        if token:
            headers.append("Authorization: Bearer " + token)
        if method == "POST":
            headers.extend(["Content-Type: application/json", f"Content-Length: {len(body)}"])
        return self.raw(("\r\n".join(headers) + "\r\n\r\n").encode() + body)

    def pair(self, capabilities: set[str] | None = None) -> dict[str, Any]:
        code = self.auth.begin_pairing(
            capabilities if capabilities is not None else {"chat", "history", "reminders"}
        )
        status, value = self.request(
            "POST", "/v1/pair", {"code": code, "deviceName": "Synthetic phone"}
        )
        assert status == 200
        return cast(dict[str, Any], value["data"])


def make_runtime(
    root: Path,
    *,
    port: int = 0,
    bridge: Bridge | None = None,
    scope: list[ScopeBinding] | None = None,
) -> Runtime:
    store = PrivateStore(root)
    binding = scope or [ScopeBinding("synthetic-principal", "synthetic-profile", 1)]
    identity = prepare_tls(store, "127.0.0.1")
    auth = AuthOwner(store, lambda: binding[0], identity.transport_id(port or 8766))
    fake = bridge or Bridge()
    broker = Broker(store, fake, auth.revalidate, lambda: binding[0], wait_seconds=0.01)
    server = GatewayServer(GatewayConfig(enabled=True, port=port), auth, broker, identity)
    assert server.start()
    return Runtime(root, store, auth, broker, fake, server, identity, binding)


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[Runtime]:
    item = make_runtime(tmp_path / "satori-mobile-v1")
    try:
        yield item
    finally:
        item.server.stop()
        item.store.close()


def test_default_off_constructs_no_listener_or_tls_material(tmp_path: Path) -> None:
    store = PrivateStore(tmp_path / "off")
    scope = ScopeBinding("principal", "profile", 1)
    auth = AuthOwner(store, lambda: scope, "unbound")
    broker = Broker(store, Bridge(), auth.revalidate, lambda: scope)
    server = GatewayServer(GatewayConfig(), auth, broker)
    try:
        assert not server.start()
        assert server.port is None
        assert not store.path("server-key.pem").exists()
    finally:
        server.stop()
        store.close()


def test_tls_pair_auth_routes_idempotency_revoke(runtime: Runtime) -> None:
    pair = runtime.pair()
    token = pair["token"]
    assert set(pair) == {"deviceID", "token", "expiresAt", "profileID", "capabilities"}
    assert "status" in pair["capabilities"]
    for path in (
        "/v1/status",
        "/v1/sessions",
        "/v1/history?session_id=phone-session",
        "/v1/reminders",
    ):
        status, value = runtime.request("GET", path, token=token)
        assert status == 200
        assert value["ok"]
    new_id = str(uuid4())
    status, value = runtime.request("POST", "/v1/sessions", {"requestID": new_id}, token)
    assert status == 200
    assert value["data"]["sessionID"] == "phone-session"
    identifier = str(uuid4())
    body = {"requestID": identifier, "sessionID": "phone-session", "text": "synthetic private text"}
    first = runtime.request("POST", "/v1/chat", body, token)
    assert first[0] == 200
    assert first[1]["data"]["state"] == "completed"
    assert runtime.request("POST", "/v1/chat", body, token) == first
    assert runtime.request("GET", "/v1/requests/" + identifier, token=token) == first
    body["text"] = "different synthetic text"
    status, value = runtime.request("POST", "/v1/chat", body, token)
    assert status == 409
    assert value["error"]["code"] == "request_id_conflict"
    assert sum(call.operation == "chat" for call in runtime.bridge.calls) == 1
    assert runtime.request("POST", "/v1/revoke", {}, token)[1]["data"] == {"revoked": True}
    assert runtime.request("GET", "/v1/status", token=token)[0] == 401


def test_pin_and_hostname_checked_before_credentials(runtime: Runtime) -> None:
    with pytest.raises(ssl.SSLError):
        runtime.connection(pin="0" * 64)
    with pytest.raises(ssl.SSLCertVerificationError):
        runtime.connection(host="wrong.synthetic.invalid")
    assert runtime.server.metrics.get("request", 0) == 0
    assert runtime.bridge.calls == []


def test_repair_replaces_prior_grant_not_device_name(runtime: Runtime) -> None:
    first = runtime.pair()
    second = runtime.pair()
    assert first["deviceID"] != second["deviceID"]
    assert runtime.request("GET", "/v1/status", token=first["token"])[0] == 401
    assert runtime.request("GET", "/v1/status", token=second["token"])[0] == 200


def test_explicit_capability_selection_does_not_inherit_chat(runtime: Runtime) -> None:
    pair = runtime.pair(set())
    assert pair["capabilities"] == ["status"]
    assert runtime.request("GET", "/v1/status", token=pair["token"])[0] == 200
    status, value = runtime.request(
        "POST",
        "/v1/chat",
        {"requestID": str(uuid4()), "sessionID": "s", "text": "hello"},
        pair["token"],
    )
    assert status == 403
    assert value["error"]["code"] == "forbidden"
    assert all(call.operation != "chat" for call in runtime.bridge.calls)


def test_profile_change_revokes_authority(runtime: Runtime) -> None:
    pair = runtime.pair()
    runtime.scope[0] = ScopeBinding("synthetic-principal", "other-profile", 2)
    assert runtime.request("GET", "/v1/status", token=pair["token"])[0] != 200
    runtime.scope[0] = ScopeBinding("synthetic-principal", "synthetic-profile", 1)
    assert runtime.request("GET", "/v1/status", token=pair["token"])[0] != 200


@pytest.mark.parametrize(
    "mutation",
    [
        b"Host: attacker.invalid\r\n",
        b"Host: x\r\nHost: y\r\n",
        b"Content-Length: 0\r\nContent-Length: 0\r\n",
        b"Transfer-Encoding: chunked\r\n",
        b"Expect: 100-continue\r\n",
        b" folded: bad\r\n",
        b"Authorization: Bearer A\r\nAuthorization: Bearer B\r\n",
        b"Content-Length: 65537\r\n",
    ],
)
def test_http_smuggling_bounds_reject_before_core(runtime: Runtime, mutation: bytes) -> None:
    request = (
        f"GET /v1/status HTTP/1.1\r\nHost: 127.0.0.1:{runtime.server.port}\r\n".encode()
        + mutation
        + b"\r\n"
    )
    status, value = runtime.raw(request)
    assert status in {400, 413}
    assert not value["ok"]
    assert runtime.bridge.calls == []


@pytest.mark.parametrize(
    "body",
    [
        b'{"code":"a","code":"b","deviceName":"x"}',
        b'{"code":true,"deviceName":"x"}',
        b'{"code":"x","deviceName":"\\ud800"}',
    ],
)
def test_malformed_pair_bodies_closed_and_budgeted(runtime: Runtime, body: bytes) -> None:
    runtime.auth.begin_pairing({"chat"})
    raw = (
        f"POST /v1/pair HTTP/1.1\r\nHost: 127.0.0.1:{runtime.server.port}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    for _ in range(4):
        assert runtime.raw(raw)[0] == 400
    assert runtime.raw(raw)[1]["error"]["code"] == "pairing_rate_limited"
    assert runtime.bridge.calls == []


def test_unknown_request_survives_restart_reconcile_without_replay(tmp_path: Path) -> None:
    first = make_runtime(tmp_path / "restart")
    pair = first.pair()
    first.bridge.hold = True
    identifier = str(uuid4())
    body = {
        "requestID": identifier,
        "sessionID": "phone-session",
        "text": "synthetic request after restart",
    }
    assert first.request("POST", "/v1/chat", body, pair["token"])[1]["data"]["state"] == "pending"
    port = first.server.port
    assert port is not None
    first.server.stop()
    first.store.close()
    second = make_runtime(first.root, port=port, bridge=first.bridge, scope=first.scope)
    try:
        assert (
            second.request("POST", "/v1/chat", body, pair["token"])[1]["data"]["state"] == "unknown"
        )
        status, value = second.request("GET", "/v1/requests/" + identifier, token=pair["token"])
        assert status == 200
        assert value["data"]["state"] == "completed"
        assert sum(call.operation == "chat" for call in first.bridge.calls) == 1
        assert sum(call.operation == "reconcile" for call in first.bridge.calls) == 1
    finally:
        second.server.stop()
        second.store.close()


def test_unknown_request_id_invalid_uuid_is_400(runtime: Runtime) -> None:
    pair = runtime.pair()
    status, value = runtime.request("GET", "/v1/requests/not-a-uuid", token=pair["token"])
    assert status == 400
    assert value["error"]["code"] == "invalid_request"


def test_stop_closes_accepted_tls_pairing_connection(runtime: Runtime) -> None:
    code = runtime.auth.begin_pairing({"chat"})
    connection = runtime.connection()
    accepted = Event()
    # A header byte ensures the handshake finished and the handler owns this TLS socket.
    connection.sendall(b"P")
    accepted.wait(0.02)
    runtime.server.stop()
    body = json.dumps({"code": code, "deviceName": "late phone"}).encode()
    try:
        connection.sendall(
            (
                "OST /v1/pair HTTP/1.1\r\nHost: 127.0.0.1:1\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
            ).encode()
            + body
        )
        assert b'"token"' not in connection.recv(4096)
    except OSError:
        pass
    finally:
        connection.close()
    with pytest.raises(GatewayFault):
        runtime.auth.pair(code, "late phone")


def test_unauthenticated_tls_connections_are_bounded(runtime: Runtime) -> None:
    connections: list[ssl.SSLSocket] = []
    try:
        for _ in range(8):
            connection = runtime.connection()
            connection.sendall(b"G")
            connections.append(connection)
        with pytest.raises((OSError, ssl.SSLError)):
            runtime.connection()
        assert runtime.server.metrics.get("connection_limit", 0) >= 1
        assert runtime.bridge.calls == []
    finally:
        for connection in connections:
            connection.close()


def test_header_has_absolute_deadline_without_auth_or_body(runtime: Runtime) -> None:
    with runtime.connection() as connection:
        connection.settimeout(7)
        started = time.monotonic()
        connection.sendall(b"GET /v1/status HTTP/1.1\r\nH")
        response = connection.recv(4096)
        assert b"408 Result" in response
        assert b"deadline_exceeded" in response
        assert time.monotonic() - started < 7
    assert runtime.bridge.calls == []


def test_no_secrets_or_bodies_in_private_storage_or_logs(
    runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    pair = runtime.pair()
    secret_body = "SYNTHETIC_BODY_NOT_A_LOG_" + uuid4().hex
    runtime.request(
        "POST",
        "/v1/chat",
        {"requestID": str(uuid4()), "sessionID": "s", "text": secret_body},
        pair["token"],
    )
    for path in runtime.root.glob("*.json"):
        content = path.read_text()
        assert pair["token"] not in content
        assert secret_body not in content
    output = capsys.readouterr()
    assert output.out == output.err == ""
    assert set(runtime.server.metrics) <= {
        "request",
        "rejected",
        "connection_error",
        "connection_limit",
        "closed_error",
    }


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "[::1]",
        "https://localhost",
        "localhost:8766",
        "x\nDNS:other",
        "*.local",
        "2130706433",
        "999.1.2.3",
        "UPPER.local",
    ],
)
def test_advertised_host_injection_and_ambiguous_forms_reject(host: str) -> None:
    with pytest.raises(GatewayFault):
        advertised_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "169.254.169.254",
        "8.8.8.8",
        "100.64.0.1",
        "224.0.0.1",
        "::",
        "fe80::1",
        "192.0.0.1",
    ],
)
def test_even_explicit_lan_rejects_public_wildcard_metadata_and_reserved(host: str) -> None:
    with pytest.raises(GatewayFault):
        allowed_bind(host, lan_enabled=True)


def test_lan_is_only_configuration_admission_no_actual_lan_listener() -> None:
    assert allowed_bind("127.0.0.1", lan_enabled=False) == "127.0.0.1"
    for address in ("10.0.0.1", "192.168.1.2", "172.16.1.3", "fd00::1"):
        with pytest.raises(GatewayFault):
            allowed_bind(address, lan_enabled=False)
        assert allowed_bind(address, lan_enabled=True) == address


def test_tls_identity_reload_and_host_change_requires_explicit_rotation(tmp_path: Path) -> None:
    store = PrivateStore(tmp_path / "identity")
    try:
        first = prepare_tls(store, "127.0.0.1")
        assert prepare_tls(store, "127.0.0.1").sha256 == first.sha256
        with pytest.raises(GatewayFault):
            prepare_tls(store, "localhost")
        changed = prepare_tls(store, "localhost", rotate=True)
        assert changed.sha256 != first.sha256
        assert changed.host == "localhost"
        assert first.private_key.stat().st_mode & 0o777 == 0o600
    finally:
        store.close()
