"""Finite gateway/owned-Qt-bridge contracts. No Core or GUI construction."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

type Operation = Literal[
    "status", "sessions", "history", "reminders", "chat", "new_session", "reconcile"
]

ERROR_CODES = frozenset(
    {
        "invalid_request",
        "unauthorized",
        "forbidden",
        "pairing_closed",
        "pairing_expired",
        "pairing_rate_limited",
        "device_revoked",
        "device_expired",
        "profile_changed",
        "session_closed",
        "session_missing",
        "request_id_conflict",
        "request_unknown",
        "request_pending",
        "core_unavailable",
        "busy",
        "too_large",
        "deadline_exceeded",
        "operation_failed",
        "not_supported",
        "source_grant_required",
    }
)
DEFAULT_CAPABILITIES = frozenset({"status", "chat", "history", "reminders"})


class GatewayFault(Exception):
    """Closed diagnostics only; never include a request, credential or cause text."""

    def __init__(self, code: str, status: int = 400, request_id: str | None = None) -> None:
        self.code = code if code in ERROR_CODES else "operation_failed"
        self.status = status
        self.request_id = request_id
        super().__init__(self.code)


@dataclass(frozen=True)
class ScopeBinding:
    principal_id: str
    profile_id: str
    generation: int


@dataclass(frozen=True)
class DeviceContext:
    device_id: str
    scope: ScopeBinding
    capabilities: frozenset[str]
    expires_at: float
    transport_id: str


@dataclass(frozen=True)
class BridgeCall:
    call_id: str
    request_id: str | None
    operation: Operation
    scope: ScopeBinding
    device_id: str
    capabilities: frozenset[str]
    payload: Mapping[str, object] = field(repr=False)
    deadline_monotonic: float
    dispatch_guard: Callable[[], None] = field(repr=False, compare=False)
    preflight_guard: Callable[[], None] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class BridgeResult:
    call_id: str
    request_id: str | None
    scope: ScopeBinding
    device_id: str
    data: Mapping[str, object] | None = field(default=None, repr=False)
    error_code: str | None = None


class CoreBridge(Protocol):
    """Thread-safe queued submission into the existing canonical Core owner.

    The desktop adapter marshals to Qt and shares the existing worker queue. The
    ADR-0103 loopback adapter schedules into one injected HeadlessRuntime's loop;
    its read guard runs inside the runtime lane; mutation guards run after the
    backend's turn lock, directly before the owner operation.
    It must not construct DesktopBackend, open another Core database, change desktop
    selection, or implement explicit-session chat as resume followed by ordinary Send.
    Immediately before writing to the owned worker, including after any Qt queue wait,
    the adapter must call dispatch_guard. A callback is correlated, terminal and at
    most once. Reconcile is read-only.

    A staged read-only open-session preflight may use the repeatable preflight_guard
    without consuming the one-use effect permit. Missing preflight_guard denies that
    staged path. The final local mutation still requires dispatch_guard immediately
    before its effect, with fresh device/profile/expiry/deadline validation.
    """

    def submit(self, call: BridgeCall, complete: Callable[[BridgeResult], None]) -> None: ...
