"""One bounded, durable-intent lane into the existing owned Core bridge.

Only metadata survives restart. A timeout or a missing callback never opens a
second lane or authorizes replay. Source text and reply bodies remain RAM-only.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar, Literal, Protocol, cast
from uuid import UUID, uuid4

from .contracts import (
    ERROR_CODES,
    BridgeCall,
    BridgeResult,
    CoreBridge,
    DeviceContext,
    GatewayFault,
    Operation,
    ScopeBinding,
)

type WriteOperation = Literal["chat", "new_session"]
type IntentState = Literal["pending", "completed", "unknown", "failed"]
_MAX_RESPONSE_BYTES = 1_048_576


class JournalStore(Protocol):
    def read_json(self, name: str) -> dict[str, object] | None: ...

    def write_json(self, name: str, value: dict[str, object]) -> None: ...


@dataclass(slots=True)
class _Intent:
    request_id: str
    call_id: str
    operation: WriteOperation
    digest: str
    ctx: DeviceContext
    session_id: str | None
    state: IntentState
    created_at: float
    updated_at: float


@dataclass(slots=True)
class _Flight:
    call: BridgeCall
    ctx: DeviceContext
    intent: _Intent | None
    event: threading.Event = field(default_factory=threading.Event)
    data: dict[str, object] | None = field(default=None, repr=False)
    fault: str | None = None
    dispatched: bool = False
    timed_out: bool = False
    callback_seen: bool = False


class Broker:
    """Synchronous HTTP facade; CoreBridge owns asynchronous Qt dispatch.

    ``dispatch_guard`` is mandatory at the adapter's actual worker-write point.
    Metadata-full journals reject new effects rather than forgetting old IDs.
    """

    _journal_name = "requests.json"
    _read_caps: ClassVar[Mapping[str, str]] = MappingProxyType(
        {
            "status": "status",
            "sessions": "history",
            "history": "history",
            "reminders": "reminders",
        }
    )

    def __init__(
        self,
        store: JournalStore,
        bridge: CoreBridge,
        revalidate: Callable[[DeviceContext], None],
        scope_provider: Callable[[], ScopeBinding],
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        wait_seconds: float = 2,
        max_poll: float = 120,
        max_records: int = 512,
    ) -> None:
        if not 0 <= wait_seconds <= 2 or not 0 < max_poll <= 120 or not 1 <= max_records <= 512:
            raise ValueError("invalid broker bounds")
        self._store = store
        self._bridge = bridge
        self._revalidate = revalidate
        self._scope_provider = scope_provider
        self._clock = clock
        self._monotonic = monotonic
        self._wait = wait_seconds
        self._max_poll = max_poll
        self._max_records = max_records
        self._lock = threading.RLock()
        self._closed = False
        self._storage_failed = False
        self._flight: _Flight | None = None
        self._records: dict[str, _Intent] = {}
        self._cache: OrderedDict[str, dict[str, object]] = OrderedDict()
        self._rates: dict[str, deque[float]] = {}
        self._global_rate: deque[float] = deque()
        self._load()

    def read(
        self, ctx: DeviceContext, operation: Operation, payload: Mapping[str, object]
    ) -> dict[str, object]:
        if operation not in self._read_caps:
            raise GatewayFault("not_supported", 400)
        value, _ = _snapshot(payload, 65_536)
        if operation == "history":
            _session(value.get("sessionID"))
        with self._lock:
            self._admit(ctx, self._read_caps[operation])
            self._expire_lane()
            if self._flight is not None:
                raise GatewayFault("busy", 409)
            flight = self._reserve(ctx, operation, value, None)
        self._dispatch(flight)
        flight.event.wait(self._wait)
        with self._lock:
            self._authorize(ctx, self._read_caps[operation])
            self._expire_lane()
            if flight.data is not None:
                return _snapshot(flight.data, _MAX_RESPONSE_BYTES)[0]
            if flight.fault:
                raise GatewayFault(flight.fault, 504 if flight.timed_out else 503)
            raise GatewayFault("request_pending", 202)

    def submit(
        self,
        ctx: DeviceContext,
        operation: WriteOperation,
        payload: Mapping[str, object],
        request_id: UUID | str,
    ) -> dict[str, object]:
        if operation not in {"chat", "new_session"}:
            raise GatewayFault("not_supported", 400)
        ident = _request_id(request_id)
        value, canonical = _snapshot(payload, 65_536)
        if operation == "chat":
            if (
                set(value) != {"sessionID", "text"}
                or not isinstance(value.get("text"), str)
                or not value["text"]
            ):
                raise GatewayFault("invalid_request", 400)
            session = _session(value.get("sessionID"))
        else:
            if value:
                raise GatewayFault("invalid_request", 400)
            session = None
        digest = hashlib.sha256(operation.encode() + b"\n" + canonical).hexdigest()
        with self._lock:
            self._admit(ctx, "chat")
            self._expire_lane()
            previous = self._records.get(ident)
            if previous is not None:
                self._owner(previous, ctx)
                if previous.operation != operation or previous.digest != digest:
                    raise GatewayFault("request_id_conflict", 409, ident)
                return self._receipt(previous)
            if self._flight is not None or len(self._records) >= self._max_records:
                raise GatewayFault("busy", 409, ident)
            now = self._wall()
            intent = _Intent(
                ident, str(uuid4()), operation, digest, ctx, session, "pending", now, now
            )
            _journal_bounds(self._document(intent))
            self._records[ident] = intent
            # Private atomic intent acknowledgement precedes all bridge submissions.
            self._persist()
            flight = self._reserve(ctx, operation, value, intent, call_id=intent.call_id)
        self._dispatch(flight)
        flight.event.wait(self._wait)
        with self._lock:
            self._authorize(ctx, "chat")
            self._owner(intent, ctx)
            self._expire_lane()
            return self._receipt(intent)

    def reconcile(self, ctx: DeviceContext, request_id: UUID | str) -> dict[str, object]:
        ident = _request_id(request_id)
        with self._lock:
            self._admit(ctx, "chat")
            self._expire_lane()
            intent = self._records.get(ident)
            if intent is None:
                raise GatewayFault("request_unknown", 404, ident)
            self._owner(intent, ctx)
            if ident in self._cache:
                return self._receipt(intent)
            if self._flight is not None:
                if self._flight.intent is intent:
                    return self._receipt(intent)
                raise GatewayFault("busy", 409, ident)
            flight = self._reserve(
                ctx, "reconcile", {"requestID": ident, "sessionID": intent.session_id}, intent
            )
        self._dispatch(flight)
        flight.event.wait(self._wait)
        with self._lock:
            self._authorize(ctx, "chat")
            self._owner(intent, ctx)
            self._expire_lane()
            return self._receipt(intent)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            flight = self._flight
            if flight is not None:
                if flight.intent is not None:
                    flight.intent.state = "unknown"
                    # A previous pending intent remains durable if this final write fails.
                    with suppress(GatewayFault):
                        self._persist()
                flight.fault = "core_unavailable"
                flight.event.set()
            self._cache.clear()
            self._flight = None

    def _admit(self, ctx: DeviceContext, capability: str) -> None:
        self._authorize(ctx, capability)
        now = self._mono()
        for device in tuple(self._rates):
            while self._rates[device] and self._rates[device][0] <= now - 60:
                self._rates[device].popleft()
            if not self._rates[device]:
                del self._rates[device]
        while self._global_rate and self._global_rate[0] <= now - 60:
            self._global_rate.popleft()
        if len(self._rates) >= 512 and ctx.device_id not in self._rates:
            raise GatewayFault("busy", 429)
        rate = self._rates.setdefault(ctx.device_id, deque())
        if len(rate) >= 120 or len(self._global_rate) >= 256:
            raise GatewayFault("busy", 429)
        rate.append(now)
        self._global_rate.append(now)

    def _authorize(self, ctx: DeviceContext, capability: str) -> None:
        if self._closed or self._storage_failed:
            raise GatewayFault("core_unavailable", 503)
        _context(ctx)
        try:
            if ctx.expires_at <= self._wall():
                raise GatewayFault("device_expired", 401)
            if capability not in ctx.capabilities:
                raise GatewayFault("forbidden", 403)
            self._revalidate(ctx)
            if self._scope_provider() != ctx.scope:
                raise GatewayFault("profile_changed", 403)
        except Exception as exc:
            for ident, intent in self._records.items():
                if intent.ctx.device_id == ctx.device_id:
                    self._cache.pop(ident, None)
            if isinstance(exc, GatewayFault):
                raise GatewayFault(exc.code, exc.status) from None
            raise GatewayFault("unauthorized", 401) from None

    @staticmethod
    def _owner(intent: _Intent, ctx: DeviceContext) -> None:
        if intent.ctx != ctx:
            raise GatewayFault("forbidden", 403, intent.request_id)

    def _reserve(
        self,
        ctx: DeviceContext,
        operation: Operation,
        payload: dict[str, object],
        intent: _Intent | None,
        *,
        call_id: str | None = None,
    ) -> _Flight:
        ident = call_id or str(uuid4())

        def validate_pending() -> None:
            if self._flight is not flight or flight.callback_seen or flight.dispatched:
                raise GatewayFault("request_id_conflict", 409)
            self._authorize(ctx, self._read_caps.get(operation, "chat"))
            self._expire_lane()
            if flight.timed_out:
                raise GatewayFault("deadline_exceeded", 504)

        def preflight_guard() -> None:
            with self._lock:
                validate_pending()

        def guard() -> None:
            with self._lock:
                validate_pending()
                flight.dispatched = True

        call = BridgeCall(
            ident,
            intent.request_id if intent else None,
            operation,
            ctx.scope,
            ctx.device_id,
            ctx.capabilities,
            cast(Mapping[str, object], _freeze(payload)),
            self._mono() + self._max_poll,
            guard,
            preflight_guard=preflight_guard,
        )
        flight = _Flight(call, ctx, intent)
        self._flight = flight
        return flight

    def _dispatch(self, flight: _Flight) -> None:
        try:
            self._bridge.submit(flight.call, lambda result: self._complete(flight, result))
        except Exception:
            # Queueing/native effects may precede an exception; absence is never inferred.
            with self._lock:
                if self._flight is flight and not flight.callback_seen:
                    flight.fault = "operation_failed"
                    if flight.intent is not None:
                        flight.intent.state = "unknown"
                        self._persist_quietly()
                    flight.event.set()

    def _complete(self, flight: _Flight, result: BridgeResult) -> None:
        with self._lock:
            if (
                self._closed
                or self._flight is not flight
                or flight.callback_seen
                or not isinstance(result, BridgeResult)
            ):
                return
            call = flight.call
            if (
                (result.call_id, result.request_id, result.scope, result.device_id)
                != (call.call_id, call.request_id, call.scope, call.device_id)
                or not isinstance(result.scope, ScopeBinding)
                or type(result.scope.generation) is not int
            ):
                return  # Foreign callbacks cannot publish OR release the owned lane.
            flight.callback_seen = True
            try:
                self._expire_lane()
            except GatewayFault:
                flight.fault = "operation_failed"
                flight.event.set()
                return
            try:
                self._authorize(flight.ctx, self._read_caps.get(call.operation, "chat"))
            except GatewayFault:
                if flight.intent is not None:
                    flight.intent.state = "unknown"
                    self._persist_quietly()
                flight.fault = "unauthorized"
                flight.event.set()
                return  # Quarantined until explicit close; this result has lost its grant.
            if flight.timed_out:
                # A terminal callback releases the known call, not its expired response lease.
                # The effect stays unknown until a fresh authorized read-only reconciliation.
                self._flight = None
                flight.event.set()
                return
            try:
                if result.error_code is not None or result.data is None or not flight.dispatched:
                    raise GatewayFault(result.error_code or "operation_failed", 503)
                projected = (
                    _project_read(call.operation, result.data)
                    if flight.intent is None
                    else result.data
                )
                value, _ = _snapshot(projected, _MAX_RESPONSE_BYTES)
                if call.operation == "history" and value.get("sessionID") != call.payload.get(
                    "sessionID"
                ):
                    raise GatewayFault("operation_failed", 503)
                if flight.intent is not None:
                    value = self._write_result(flight.intent, value)
                    state = cast(IntentState, value["state"])
                    flight.intent.state = state
                    session = value.get("sessionID")
                    if isinstance(session, str):
                        flight.intent.session_id = session
                    self._persist()
                    if state in {"completed", "failed"}:
                        self._cache[flight.intent.request_id] = value
                        self._cache.move_to_end(flight.intent.request_id)
                        while len(self._cache) > min(self._max_records, 32):
                            self._cache.popitem(last=False)
                flight.data = value
            except Exception as exc:
                flight.fault = (
                    exc.code
                    if isinstance(exc, GatewayFault) and exc.code in ERROR_CODES
                    else "operation_failed"
                )
                if flight.intent is not None:
                    flight.intent.state = "unknown"
                    self._persist_quietly()
            self._flight = None  # Only this exact, authorized terminal callback releases the lane.
            flight.event.set()

    @staticmethod
    def _write_result(intent: _Intent, value: dict[str, object]) -> dict[str, object]:
        if value.get("requestID") != intent.request_id or value.get("state") not in {
            "pending",
            "completed",
            "unknown",
            "failed",
        }:
            raise GatewayFault("operation_failed", 503)
        session = value.get("sessionID")
        if intent.operation == "chat":
            if session != intent.session_id or value.get("kind") not in {
                "conversation",
                "assistant",
            }:
                raise GatewayFault("operation_failed", 503)
            if not isinstance(value.get("text"), str) or (
                value.get("interactionID") is not None
                and not isinstance(value["interactionID"], str)
            ):
                raise GatewayFault("operation_failed", 503)
            if value.get("card") is not None and not isinstance(value["card"], dict):
                raise GatewayFault("operation_failed", 503)
            if len(cast(str, value["text"])) > 64_000:
                raise GatewayFault("too_large", 413)
            card = value.get("card")
            if isinstance(card, dict) and (
                not isinstance(card.get("body"), str) or len(card["body"]) > 24_000
            ):
                raise GatewayFault("too_large", 413)
            keys = {"requestID", "state", "kind", "sessionID", "text", "interactionID", "card"}
        else:
            if intent.session_id is not None and session != intent.session_id:
                raise GatewayFault("operation_failed", 503)
            if value["state"] == "completed" or session is not None:
                _session(session)
            keys = {"requestID", "state", "sessionID"}
        if set(value) != keys:
            raise GatewayFault("operation_failed", 503)
        return value

    def _receipt(self, intent: _Intent) -> dict[str, object]:
        cached = self._cache.get(intent.request_id)
        if cached is not None:
            return _snapshot(cached, _MAX_RESPONSE_BYTES)[0]
        state = intent.state if intent.state in {"pending", "unknown"} else "unknown"
        value: dict[str, object] = {
            "requestID": intent.request_id,
            "state": state,
            "sessionID": intent.session_id,
        }
        if intent.operation == "chat":
            value.update({"kind": "conversation", "text": "", "interactionID": None, "card": None})
        return value

    def _expire_lane(self) -> None:
        flight = self._flight
        if flight is None or flight.timed_out or self._mono() < flight.call.deadline_monotonic:
            return
        flight.timed_out = True
        flight.fault = "deadline_exceeded"
        if flight.intent is not None:
            flight.intent.state = "unknown"
            self._persist()
        flight.event.set()

    def _wall(self) -> float:
        value = self._clock()
        if not math.isfinite(value):
            raise GatewayFault("operation_failed", 503)
        return value

    def _mono(self) -> float:
        value = self._monotonic()
        if not math.isfinite(value):
            raise GatewayFault("operation_failed", 503)
        return value

    def _persist_quietly(self) -> None:
        with suppress(GatewayFault):
            self._persist()

    def _persist(self) -> None:
        for intent in self._records.values():
            intent.updated_at = max(intent.updated_at, self._wall())
        document = self._document()
        try:
            _journal_bounds(document)
            self._store.write_json(self._journal_name, document)
        except Exception:
            self._storage_failed = True
            self._cache.clear()
            raise GatewayFault("operation_failed", 503) from None

    def _document(self, extra: _Intent | None = None) -> dict[str, object]:
        rows: list[dict[str, object]] = []
        records = list(self._records.values())
        if extra is not None:
            records.append(extra)
        for intent in records:
            rows.append(
                {
                    "request_id": intent.request_id,
                    "call_id": intent.call_id,
                    "operation": intent.operation,
                    "digest": intent.digest,
                    "device_id": intent.ctx.device_id,
                    "scope": {
                        "principal_id": intent.ctx.scope.principal_id,
                        "profile_id": intent.ctx.scope.profile_id,
                        "generation": intent.ctx.scope.generation,
                    },
                    "capabilities": sorted(intent.ctx.capabilities),
                    "transport_id": intent.ctx.transport_id,
                    "expires_at": intent.ctx.expires_at,
                    "session_id": intent.session_id,
                    "state": intent.state,
                    "created_at": intent.created_at,
                    "updated_at": intent.updated_at,
                }
            )
        return {"version": 1, "records": rows}

    def _load(self) -> None:
        try:
            value = self._store.read_json(self._journal_name)
            if value is None:
                return
            if (
                set(value) != {"version", "records"}
                or type(value["version"]) is not int
                or value["version"] != 1
            ):
                raise ValueError
            rows = value["records"]
            if not isinstance(rows, list) or len(rows) > self._max_records:
                raise ValueError
            changed = False
            for row in rows:
                intent = _decode_intent(row)
                if intent.request_id in self._records:
                    raise ValueError
                if intent.state == "pending":
                    intent.state = "unknown"
                    changed = True
                self._records[intent.request_id] = intent
            if changed:
                self._persist()
        except Exception:
            self._storage_failed = True
            raise GatewayFault("operation_failed", 503) from None


def _request_id(value: UUID | str) -> str:
    try:
        if not isinstance(value, UUID | str):
            raise ValueError
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise GatewayFault("invalid_request", 400) from None


def _context(ctx: DeviceContext) -> None:
    if not isinstance(ctx, DeviceContext) or not isinstance(ctx.scope, ScopeBinding):
        raise GatewayFault("unauthorized", 401)
    if type(ctx.scope.generation) is not int or ctx.scope.generation < 0:
        raise GatewayFault("unauthorized", 401)
    for value in (ctx.device_id, ctx.transport_id, ctx.scope.principal_id, ctx.scope.profile_id):
        if not isinstance(value, str) or not value or len(value) > 200:
            raise GatewayFault("unauthorized", 401)
    if not isinstance(ctx.capabilities, frozenset) or len(ctx.capabilities) > 32:
        raise GatewayFault("unauthorized", 401)
    if any(
        not isinstance(value, str) or not value or len(value) > 64 for value in ctx.capabilities
    ):
        raise GatewayFault("unauthorized", 401)
    if type(ctx.expires_at) not in (int, float) or not math.isfinite(ctx.expires_at):
        raise GatewayFault("unauthorized", 401)


def _session(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 200
        or any(ord(char) < 32 for char in value)
    ):
        raise GatewayFault("invalid_request", 400)
    return value


def _snapshot(value: Mapping[str, object], maximum: int) -> tuple[dict[str, object], bytes]:
    nodes = 0

    def check(node: object, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 8 or nodes > 16_384:
            raise GatewayFault("too_large", 413)
        if isinstance(node, dict):
            if len(node) > 64 or any(not isinstance(key, str) or len(key) > 128 for key in node):
                raise GatewayFault("too_large", 413)
            for item in node.values():
                check(item, depth + 1)
        elif isinstance(node, list):
            if len(node) > 1000:
                raise GatewayFault("too_large", 413)
            for item in node:
                check(item, depth + 1)
        elif isinstance(node, str) and len(node) > maximum:
            raise GatewayFault("too_large", 413)
        elif node is not None and not isinstance(node, str | bool | int | float):
            raise GatewayFault("invalid_request", 400)

    try:
        copied = dict(value)
        check(copied)
        raw = json.dumps(
            copied, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(raw) > maximum:
            raise GatewayFault("too_large", 413)
        return cast(dict[str, object], json.loads(raw)), raw
    except GatewayFault:
        raise
    except Exception:
        raise GatewayFault("invalid_request", 400) from None


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _project_read(operation: Operation, data: Mapping[str, object]) -> Mapping[str, object]:
    """Keep whole records under the final wire budget without mutating bridge data."""
    collection = {
        "history": ("messages", 64),
        "sessions": ("sessions", 100),
        "reminders": ("items", 100),
    }.get(operation)
    if collection is None:
        return data
    field_name, maximum = collection
    value = dict(data)
    items = value.get(field_name)
    if not isinstance(items, list):
        raise GatewayFault("operation_failed", 503)
    if len(items) > 10_000:
        raise GatewayFault("too_large", 413)
    selected = items[-maximum:] if operation == "history" else items[:maximum]
    changed = len(selected) != len(items)
    value[field_name] = []
    base, _ = _snapshot(value, _MAX_RESPONSE_BYTES)
    budget = len(json.dumps({"ok": True, "data": base}, ensure_ascii=True, separators=(",", ":")))
    retained: list[dict[str, object]] = []
    ordered = reversed(selected) if operation == "history" else iter(selected)
    for item in ordered:
        if not isinstance(item, dict):
            raise GatewayFault("operation_failed", 503)
        row, _ = _snapshot(item, _MAX_RESPONSE_BYTES)
        cost = len(json.dumps(row, ensure_ascii=True, separators=(",", ":"))) + bool(retained)
        if budget + cost > _MAX_RESPONSE_BYTES - 64:
            changed = True
            break
        budget += cost
        retained.append(row)
    if operation == "history":
        retained.reverse()
    base[field_name] = retained
    if changed:
        base["truncated"] = True
    return base


def _journal_bounds(document: dict[str, object]) -> None:
    """Leave room for final states and newly allocated session IDs before admitting an effect."""
    nodes = 0
    cost = 0

    def visit(value: object) -> None:
        nonlocal nodes, cost
        nodes += 1
        cost += 8
        if isinstance(value, str):
            cost += len(value.encode("utf-8"))
        elif isinstance(value, dict):
            for key, child in value.items():
                visit(key)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    records = cast(list[dict[str, object]], document["records"])
    reserve = sum(
        808 if row["operation"] == "new_session" and row["session_id"] is None else 8
        for row in records
    )
    encoded = json.dumps(
        document, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if nodes > 65_280 or cost + reserve > 1_044_480 or len(encoded) + reserve > 1_044_480:
        raise GatewayFault("busy", 409)


def _decode_intent(value: object) -> _Intent:
    keys = {
        "request_id",
        "call_id",
        "operation",
        "digest",
        "device_id",
        "scope",
        "capabilities",
        "transport_id",
        "expires_at",
        "session_id",
        "state",
        "created_at",
        "updated_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or value["operation"] not in {"chat", "new_session"}
    ):
        raise ValueError
    for key in ("request_id", "call_id"):
        if not isinstance(value[key], str) or _request_id(value[key]) != value[key]:
            raise ValueError
    scope = value["scope"]
    if not isinstance(scope, dict) or set(scope) != {"principal_id", "profile_id", "generation"}:
        raise ValueError
    if type(scope["generation"]) is not int or scope["generation"] < 0:
        raise ValueError
    for item in (
        value["device_id"],
        value["transport_id"],
        scope["principal_id"],
        scope["profile_id"],
    ):
        if not isinstance(item, str) or not item or len(item) > 200:
            raise ValueError
    caps = value["capabilities"]
    if (
        not isinstance(caps, list)
        or len(caps) > 32
        or any(not isinstance(cap, str) or len(cap) > 64 for cap in caps)
    ):
        raise ValueError
    if len(caps) != len(set(caps)) or value["state"] not in {
        "pending",
        "completed",
        "unknown",
        "failed",
    }:
        raise ValueError
    for key in ("created_at", "updated_at", "expires_at"):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] < 0:
            raise ValueError
    digest = value["digest"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError
    session = value["session_id"]
    if session is not None:
        _session(session)
    if value["operation"] == "chat" and session is None:
        raise ValueError
    ctx = DeviceContext(
        value["device_id"],
        ScopeBinding(scope["principal_id"], scope["profile_id"], scope["generation"]),
        frozenset(caps),
        value["expires_at"],
        value["transport_id"],
    )
    return _Intent(
        value["request_id"],
        value["call_id"],
        value["operation"],
        digest,
        ctx,
        session,
        value["state"],
        value["created_at"],
        value["updated_at"],
    )
