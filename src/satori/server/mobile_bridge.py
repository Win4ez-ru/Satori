"""Thread-safe mobile submission into one existing non-Qt Core event loop."""

import asyncio
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from satori.application.assistant.intents import parse_assistant_intent
from satori.desktop.mobile_gateway.contracts import (
    BridgeCall,
    BridgeResult,
    GatewayFault,
    ScopeBinding,
)
from satori.desktop.mobile_gateway.wire import ChatData
from satori.server.mobile_projection import identifier, project
from satori.server.runtime import HeadlessCommand, HeadlessError, HeadlessRuntime


class HeadlessMobileBridge:
    """One bounded lane; callbacks cannot cancel or replay an admitted Core operation.

    Construction does not open Core or a listener. The trusted host supplies the already
    running owner, its event loop and qualified scope; this is no profile-binding authority.
    """

    def __init__(
        self,
        runtime: HeadlessRuntime,
        scope_provider: Callable[[], ScopeBinding],
        *,
        timezone: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        ZoneInfo(timezone)
        self._runtime = runtime
        self._scope = scope_provider
        self._timezone = timezone
        self._clock = clock
        self._loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._reserved = False
        self._closed = False
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(self, call: BridgeCall, complete: Callable[[BridgeResult], None]) -> None:
        with self._lock:
            if self._closed:
                raise GatewayFault("core_unavailable", 503)
            if self._reserved:
                raise GatewayFault("busy", 409)
            if not isinstance(call, BridgeCall) or not callable(complete):
                raise GatewayFault("invalid_request")
            self._reserved = True
        try:
            self._loop.call_soon_threadsafe(self._schedule, call, complete)
        except RuntimeError:
            with self._lock:
                self._reserved = False
            raise GatewayFault("core_unavailable", 503) from None

    def _schedule(self, call: BridgeCall, complete: Callable[[BridgeResult], None]) -> None:
        task = self._loop.create_task(self._run(call, complete))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _guard(self, call: BridgeCall) -> None:
        with self._lock:
            if self._closed:
                raise GatewayFault("core_unavailable", 503)
        if self._scope() != call.scope:
            raise GatewayFault("profile_changed", 403)
        if self._loop.time() >= call.deadline_monotonic:
            raise GatewayFault("deadline_exceeded", 408)
        call.dispatch_guard()

    async def _run(self, call: BridgeCall, complete: Callable[[BridgeResult], None]) -> None:
        data: dict[str, object] | None = None
        error: str | None = None
        try:
            command = self._command(call)
            if command is None:
                self._guard(call)
                data = self._unavailable(call)
            else:
                receipt = await self._runtime.execute(
                    command, dispatch_guard=lambda: self._guard(call)
                )
                data = project(call, receipt)
        except GatewayFault as fault:
            error = fault.code
        except HeadlessError as fault:
            error = "busy" if fault.code == "busy" else "core_unavailable"
        except Exception:
            error = "operation_failed"
        finally:
            with self._lock:
                self._reserved = False
            # The broker owns reconciliation. A failed callback never replays a command.
            with suppress(Exception):
                complete(
                    BridgeResult(
                        call.call_id, call.request_id, call.scope, call.device_id, data, error
                    )
                )

    def _command(self, call: BridgeCall) -> HeadlessCommand | None:
        operation = call.operation
        if operation in {"status", "sessions"}:
            if call.payload or call.request_id is not None:
                raise GatewayFault("invalid_request")
            return HeadlessCommand(operation="status" if operation == "status" else "sessions")
        if operation == "history":
            if set(call.payload) != {"sessionID"} or call.request_id is not None:
                raise GatewayFault("invalid_request")
            return HeadlessCommand(
                operation="history", session_id=identifier(call.payload.get("sessionID"))
            )
        request_id = str(UUID(identifier(call.request_id)))
        if operation == "new_session":
            if call.payload:
                raise GatewayFault("invalid_request")
            return HeadlessCommand(operation="new_session", request_id=request_id)
        if operation == "reconcile":
            if (
                set(call.payload) != {"requestID", "sessionID"}
                or call.payload["requestID"] != request_id
            ):
                raise GatewayFault("invalid_request")
            if call.payload["sessionID"] is None:
                raise GatewayFault("request_unknown", 404)
            return HeadlessCommand(
                operation="reconcile",
                session_id=identifier(call.payload["sessionID"]),
                request_id=request_id,
            )
        if operation != "chat" or set(call.payload) != {"sessionID", "text"}:
            raise GatewayFault("not_supported")
        text = call.payload["text"]
        if type(text) is not str or not text.strip() or len(text) > 8000 or "\x00" in text:
            raise GatewayFault("invalid_request")
        session = identifier(call.payload["sessionID"])
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise GatewayFault("operation_failed", 503)
        intent = parse_assistant_intent(
            text, now=now, timezone=self._timezone, request_id=UUID(request_id)
        )
        if intent is not None:
            return None
        return HeadlessCommand(
            operation="send", session_id=session, request_id=request_id, text=text
        )

    def _unavailable(self, call: BridgeCall) -> dict[str, object]:
        # All recognized task routes are separate from canonical conversation. This
        # slice has no source/reminder owner and never pretends the Mac is available.
        return ChatData.model_validate(
            {
                "requestID": call.request_id,
                "state": "completed",
                "kind": "assistant",
                "sessionID": call.payload.get("sessionID"),
                "text": "",
                "interactionID": None,
                "card": {
                    "type": "connection_required",
                    "title": "Источник или действие пока недоступны",
                    "body": (
                        "Этот серверный режим пока поддерживает разговор. "
                        "Подключение источников и действий выполняется отдельно; "
                        "данные не запрашивались."
                    ),
                    "source": None,
                    "observedAt": None,
                    "expiresAt": None,
                    "status": "permission_required",
                },
            }
        ).model_dump()

    async def close(self) -> None:
        """Stop admission and wait for dispatched owner work; never cancel it."""
        if asyncio.get_running_loop() is not self._loop:
            raise GatewayFault("core_unavailable", 503)
        with self._lock:
            self._closed = True
        # A thread may have reserved a call whose scheduling callback is not run yet.
        await asyncio.sleep(0)
        if self._tasks:
            await asyncio.shield(asyncio.gather(*tuple(self._tasks)))
