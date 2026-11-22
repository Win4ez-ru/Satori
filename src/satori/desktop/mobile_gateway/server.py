"""Bounded default-off HTTPS transport, without a second Core/backend owner."""

import re
import socket
import socketserver
import ssl
import threading
import time
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from .auth import AuthOwner
from .broker import Broker
from .contracts import DeviceContext, GatewayFault, Operation
from .tls import TLSIdentity, advertised_host, allowed_bind, authority
from .wire import (
    MAX_REQUEST_BYTES,
    ChatRequest,
    EmptyRequest,
    PairRequest,
    RequestID,
    json_bytes,
    request_body,
    response_data,
)


@dataclass(frozen=True)
class GatewayConfig:
    enabled: bool = False
    bind_host: str = "127.0.0.1"
    advertised_host: str = "127.0.0.1"
    port: int = 8766
    lan_enabled: bool = False


@dataclass(frozen=True)
class _Request:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes


class _RateLimit:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, int]] = {}

    def check(self, key: str, maximum: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._buckets = {k: v for k, v in self._buckets.items() if now - v[0] < 60}
            if len(self._buckets) >= 256 and key not in self._buckets:
                raise GatewayFault("busy", 429)
            started, count = self._buckets.get(key, (now, 0))
            if count >= maximum:
                raise GatewayFault("busy", 429)
            self._buckets[key] = (started, count + 1)


class _BoundedTCPServer(socketserver.TCPServer):
    allow_reuse_address = True
    request_queue_size = 8

    def __init__(self, owner: "GatewayServer", host: str, port: int) -> None:
        self.owner = owner
        self._slots = threading.BoundedSemaphore(8)
        self._active_lock = threading.Lock()
        self._active: set[socket.socket] = set()
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        super().__init__((host, port), socketserver.BaseRequestHandler)

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: object
    ) -> None:
        if not isinstance(request, socket.socket):
            self.owner._count("connection_error")
            return
        if not self._slots.acquire(blocking=False):
            request.close()
            self.owner._count("connection_limit")
            return
        with self._active_lock:
            self._active.add(request)
        thread = threading.Thread(
            target=self._connection, args=(request, client_address), daemon=True
        )
        thread.start()

    def _connection(self, request: socket.socket, peer: object) -> None:
        secure: ssl.SSLSocket | None = None
        try:
            request.settimeout(3)
            context = self.owner._context
            if context is None:
                return
            secure = context.wrap_socket(request, server_side=True)
            secure.settimeout(5)
            with self._active_lock:
                if self.owner._stopped:
                    return
                self._active.add(secure)
            self.owner._handle(secure, peer)
        except Exception:
            self.owner._count("connection_error")
        finally:
            if secure is not None:
                secure.close()
            request.close()
            with self._active_lock:
                self._active.discard(request)
                if secure is not None:
                    self._active.discard(secure)
            self._slots.release()

    def handle_error(self, request: object, client_address: object) -> None:
        # socketserver's default prints tracebacks that may contain untrusted values.
        self.owner._count("connection_error")

    def close_connections(self) -> None:
        with self._active_lock:
            for request in self._active:
                with suppress(OSError):
                    request.shutdown(socket.SHUT_RDWR)


class GatewayServer:
    def __init__(
        self,
        config: GatewayConfig,
        auth: AuthOwner,
        broker: Broker,
        identity: TLSIdentity | None = None,
    ) -> None:
        self.config = config
        self.auth = auth
        self.broker = broker
        self.identity = identity
        self._server: _BoundedTCPServer | None = None
        self._thread: threading.Thread | None = None
        self._context: ssl.SSLContext | None = None
        self._expected_host = ""
        self._metrics: Counter[str] = Counter()
        self._metrics_lock = threading.Lock()
        self._rate = _RateLimit()
        self._stopped = False

    def _count(self, code: str) -> None:
        with self._metrics_lock:
            self._metrics[code] += 1

    @property
    def metrics(self) -> dict[str, int]:
        with self._metrics_lock:
            return dict(self._metrics)

    @property
    def port(self) -> int | None:
        return None if self._server is None else int(self._server.server_address[1])

    def start(self) -> bool:
        if not self.config.enabled:
            return False
        if self._server is not None:
            raise GatewayFault("busy", 409)
        if self._stopped:
            raise GatewayFault("core_unavailable", 503)
        host = allowed_bind(self.config.bind_host, lan_enabled=self.config.lan_enabled)
        endpoint = advertised_host(self.config.advertised_host)
        if type(self.config.port) is not int or not 0 <= self.config.port <= 65535:
            raise GatewayFault("invalid_request")
        if self.identity is None or self.identity.host != endpoint:
            raise GatewayFault("profile_changed", 409)
        self._context = self.identity.server_context()
        server: _BoundedTCPServer | None = None
        try:
            server = _BoundedTCPServer(self, host, self.config.port)
            port = int(server.server_address[1])
            self._expected_host = authority(endpoint, port)
            self.auth.set_transport(self.identity.transport_id(port))
            self._server = server
            self._thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
            )
            self._thread.start()
            return True
        except Exception:
            if server is not None:
                server.server_close()
            self._context = None
            raise GatewayFault("operation_failed", 503) from None

    def stop(self) -> None:
        self._stopped = True
        self.auth.cancel_pairing()
        server, self._server = self._server, None
        if server is not None:
            self.broker.close()
            server.shutdown()
            server.close_connections()
            server.server_close()
            if self._thread is not None:
                self._thread.join(timeout=2)
            self._thread = None
        self._context = None

    @staticmethod
    def _receive(connection: ssl.SSLSocket, count: int, deadline: float) -> bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GatewayFault("deadline_exceeded", 408)
        connection.settimeout(min(remaining, 5))
        chunk = connection.recv(count)
        if not chunk:
            raise GatewayFault("invalid_request")
        return chunk

    def _line(self, connection: ssl.SSLSocket, limit: int, deadline: float) -> bytes:
        line = bytearray()
        while len(line) <= limit:
            line.extend(self._receive(connection, 1, deadline))
            if line.endswith(b"\n"):
                if not line.endswith(b"\r\n"):
                    raise GatewayFault("invalid_request")
                return bytes(line[:-2])
        raise GatewayFault("too_large", 413)

    def _parse(self, connection: ssl.SSLSocket) -> _Request:
        deadline = time.monotonic() + 5
        first = self._line(connection, 2048, deadline)
        match = re.fullmatch(rb"(GET|POST) (/[^\x00-\x20\x7f]*) HTTP/1\.1", first)
        if match is None:
            raise GatewayFault("invalid_request")
        try:
            method, target = (part.decode("ascii") for part in match.groups())
            headers: dict[str, str] = {}
            total = 0
            while True:
                line = self._line(connection, 4096, deadline)
                total += len(line) + 2
                if total > 16384 or len(headers) > 32:
                    raise GatewayFault("too_large", 413)
                if not line:
                    break
                field = re.fullmatch(
                    rb"([!#$%&'*+.^_`|~0-9A-Za-z-]+):[ \t]*([^\x00-\x08\x0a-\x1f\x7f]*)", line
                )
                if field is None:
                    raise GatewayFault("invalid_request")
                name = field.group(1).decode("ascii").lower()
                value = field.group(2).decode("ascii").strip()
                if name in headers:
                    raise GatewayFault("invalid_request")
                headers[name] = value
        except UnicodeError:
            raise GatewayFault("invalid_request") from None
        if headers.get("host") != self._expected_host and not (
            self._expected_host.endswith(":443") and headers.get("host") == self._expected_host[:-4]
        ):
            raise GatewayFault("invalid_request")
        if any(name in headers for name in ("transfer-encoding", "expect", "trailer")):
            raise GatewayFault("invalid_request")
        raw_length = headers.get("content-length", "0")
        if not re.fullmatch(r"0|[1-9][0-9]{0,8}", raw_length):
            raise GatewayFault("invalid_request")
        length = int(raw_length)
        if length > MAX_REQUEST_BYTES:
            raise GatewayFault("too_large", 413)
        if method == "GET" and length:
            raise GatewayFault("invalid_request")
        if method == "POST" and (
            not length
            or headers.get("content-type", "").lower()
            not in (
                "application/json",
                "application/json; charset=utf-8",
            )
        ):
            raise GatewayFault("invalid_request")
        body = bytearray()
        while len(body) < length:
            body.extend(self._receive(connection, min(length - len(body), 16384), deadline))
        return _Request(method, target, headers, bytes(body))

    def _dispatch(self, request: _Request) -> tuple[dict[str, object], DeviceContext | None]:
        if self._stopped or self._server is None:
            raise GatewayFault("core_unavailable", 503)
        try:
            parsed = urlsplit(request.target)
            if parsed.netloc or parsed.scheme or parsed.fragment or request.target.startswith("//"):
                raise GatewayFault("invalid_request")
            query = parse_qs(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
            )
        except (ValueError, UnicodeError):
            raise GatewayFault("invalid_request") from None
        path = parsed.path
        if request.method == "POST" and path == "/v1/pair":
            if query:
                raise GatewayFault("invalid_request")
            try:
                pair = request_body(request.body, PairRequest)
            except GatewayFault:
                self.auth.reject_pairing_attempt()
                raise
            assert isinstance(pair, PairRequest)
            return self.auth.pair(pair.code, pair.deviceName), None
        header = request.headers.get("authorization", "")
        if not re.fullmatch(r"Bearer [A-Za-z0-9_-]{43}", header):
            raise GatewayFault("unauthorized", 401)
        context = self.auth.authenticate(header[7:])
        self._rate.check("device:" + context.device_id, 120)
        capability = {
            "/v1/status": "status",
            "/v1/sessions": "history" if request.method == "GET" else "chat",
            "/v1/history": "history",
            "/v1/chat": "chat",
            "/v1/reminders": "reminders",
            "/v1/revoke": "status",
        }.get(path)
        if capability and capability not in context.capabilities:
            raise GatewayFault("forbidden", 403)
        if request.method == "POST":
            if query:
                raise GatewayFault("invalid_request")
            if path == "/v1/chat":
                chat = request_body(request.body, ChatRequest)
                assert isinstance(chat, ChatRequest)
                result = self.broker.submit(
                    context,
                    "chat",
                    {"sessionID": chat.sessionID, "text": chat.text},
                    chat.requestID,
                )
                return response_data("chat", result, context), context
            if path == "/v1/sessions":
                item = request_body(request.body, RequestID)
                assert isinstance(item, RequestID)
                result = self.broker.submit(context, "new_session", {}, item.requestID)
                return response_data("new_session", result, context), context
            if path == "/v1/revoke":
                request_body(request.body, EmptyRequest)
                self.auth.revoke(context.device_id)
                return {"revoked": True}, None
            raise GatewayFault("not_supported", 404)
        if path == "/v1/history":
            if set(query) != {"session_id"} or len(query["session_id"]) != 1:
                raise GatewayFault("invalid_request")
            session = query["session_id"][0]
            if (
                not session
                or len(session) > 200
                or any(ord(c) < 32 or ord(c) == 127 for c in session)
            ):
                raise GatewayFault("invalid_request")
            value = self.broker.read(context, "history", {"sessionID": session})
            result = response_data("history", value, context)
            if result.get("sessionID") != session:
                raise GatewayFault("operation_failed", 502)
            return result, context
        if query:
            raise GatewayFault("invalid_request")
        if path.startswith("/v1/requests/"):
            request_id = RequestID(requestID=path.removeprefix("/v1/requests/")).requestID
            value = self.broker.reconcile(context, request_id)
            return response_data(
                "chat" if "kind" in value else "new_session", value, context
            ), context
        operations: dict[str, Operation] = {
            "/v1/status": "status",
            "/v1/sessions": "sessions",
            "/v1/reminders": "reminders",
        }
        operation = operations.get(path)
        if operation is None:
            raise GatewayFault("not_supported", 404)
        return response_data(operation, self.broker.read(context, operation, {}), context), context

    def _handle(self, connection: ssl.SSLSocket, peer: object) -> None:
        status = 200
        try:
            peer_ip = str(peer[0]) if isinstance(peer, tuple) else "unknown"
            self._rate.check("peer:" + peer_ip, 180)
            request = self._parse(connection)
            self._count("request")
            result, context = self._dispatch(request)
            data = json_bytes({"ok": True, "data": result})
            if context is not None:
                self.auth.revalidate(context)
            if self._stopped or self._server is None:
                raise GatewayFault("core_unavailable", 503)
        except GatewayFault as fault:
            self._count("rejected")
            status = fault.status
            data = json_bytes(
                {
                    "ok": False,
                    "error": {
                        "code": fault.code,
                        "retryable": False,
                        "requestID": fault.request_id,
                    },
                }
            )
        except (TimeoutError, OSError):
            status = 408
            data = json_bytes(
                {
                    "ok": False,
                    "error": {"code": "deadline_exceeded", "retryable": False, "requestID": None},
                }
            )
        except ValidationError:
            status = 400
            data = json_bytes(
                {
                    "ok": False,
                    "error": {"code": "invalid_request", "retryable": False, "requestID": None},
                }
            )
        except Exception:
            self._count("closed_error")
            status = 503
            data = json_bytes(
                {
                    "ok": False,
                    "error": {"code": "operation_failed", "retryable": False, "requestID": None},
                }
            )
        response = (
            f"HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\nCache-Control: no-store\r\n"
            "X-Content-Type-Options: nosniff\r\n\r\n"
        ).encode("ascii") + data
        connection.settimeout(5)
        connection.sendall(response)
