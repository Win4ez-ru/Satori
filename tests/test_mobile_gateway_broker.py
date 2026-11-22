"""Synthetic broker races and replay boundaries; no Core, Qt, sockets or accounts."""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest

from satori.desktop.mobile_gateway.broker import Broker
from satori.desktop.mobile_gateway.contracts import (
    DEFAULT_CAPABILITIES,
    BridgeCall,
    BridgeResult,
    DeviceContext,
    GatewayFault,
    ScopeBinding,
)


@dataclass
class Clock:
    wall: float = 1000
    mono: float = 10

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


@dataclass
class Store:
    data: dict[str, object] | None = None
    writes: list[dict[str, object]] = field(default_factory=list)
    fail: bool = False

    def read_json(self, name: str) -> dict[str, object] | None:
        assert name == "requests.json"
        return copy.deepcopy(self.data)

    def write_json(self, name: str, value: dict[str, object]) -> None:
        assert name == "requests.json"
        if self.fail:
            raise OSError("private exception must not leave the broker")
        self.data = copy.deepcopy(value)
        self.writes.append(copy.deepcopy(value))


class Bridge:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.calls: list[BridgeCall] = []
        self.callbacks: list[Any] = []
        self.dispatched: set[int] = set()
        self.effects: list[str] = []
        self.raise_submit = False
        self.immediate = False
        self.raise_after_callback = False
        self.override_data: dict[str, object] | None = None

    def submit(self, call: BridgeCall, complete: Any) -> None:
        if call.operation in {"chat", "new_session"}:
            assert self.store.data is not None
            records = self.store.data["records"]
            assert isinstance(records, list)
            assert any(
                row["request_id"] == call.request_id and row["state"] == "pending"
                for row in records
            )
        self.calls.append(call)
        self.callbacks.append(complete)
        if self.immediate:
            self.finish(len(self.calls) - 1)
        if self.raise_submit or self.raise_after_callback:
            raise RuntimeError("sensitive native cause")

    def dispatch(self, index: int = 0) -> None:
        call = self.calls[index]
        call.dispatch_guard()
        self.dispatched.add(index)
        if call.operation in {"chat", "new_session"}:
            self.effects.append(call.operation)

    def result(
        self, index: int = 0, data: dict[str, object] | None = None, error: str | None = None
    ) -> BridgeResult:
        call = self.calls[index]
        if data is None and error is None:
            if self.override_data is not None:
                data = self.override_data
            elif call.operation == "chat":
                data = chat_reply(call.request_id or "", str(call.payload["sessionID"]))
            elif call.operation == "new_session":
                data = {
                    "requestID": call.request_id,
                    "state": "completed",
                    "sessionID": "new-private-session",
                }
            else:
                data = {"ready": True}
        return BridgeResult(call.call_id, call.request_id, call.scope, call.device_id, data, error)

    def finish(
        self, index: int = 0, data: dict[str, object] | None = None, error: str | None = None
    ) -> None:
        if index not in self.dispatched:
            self.dispatch(index)
        self.callbacks[index](self.result(index, data, error))


def chat_reply(ident: str, session: str = "explicit-session") -> dict[str, object]:
    return {
        "requestID": ident,
        "state": "completed",
        "kind": "conversation",
        "sessionID": session,
        "text": "PRIVATE REPLY SENTINEL",
        "interactionID": "interaction-opaque",
        "card": None,
    }


class Harness:
    def __init__(self, *, max_records: int = 512, wait: float = 0) -> None:
        self.clock = Clock()
        self.store = Store()
        self.bridge = Bridge(self.store)
        self.scope = ScopeBinding("principal", "profile", 1)
        self.ctx = DeviceContext(
            "device-a", self.scope, DEFAULT_CAPABILITIES, 10_000, "transport-a"
        )
        self.revoked = False
        self.broker = self.make_broker(max_records=max_records, wait=wait)

    def revalidate(self, _: DeviceContext) -> None:
        if self.revoked:
            raise GatewayFault("device_revoked", 401)

    def make_broker(self, *, max_records: int = 512, wait: float = 0) -> Broker:
        return Broker(
            self.store,
            self.bridge,
            self.revalidate,
            lambda: self.scope,
            clock=lambda: self.clock.wall,
            monotonic=lambda: self.clock.mono,
            wait_seconds=wait,
            max_records=max_records,
        )

    def submit(
        self, ident: UUID | None = None, text: str = "PRIVATE REQUEST SENTINEL"
    ) -> dict[str, object]:
        return self.broker.submit(
            self.ctx, "chat", {"sessionID": "explicit-session", "text": text}, ident or uuid4()
        )


def fault(code: str) -> Any:
    return pytest.raises(GatewayFault, match=f"^{code}$")


def test_durable_intent_precedes_dispatch_and_payload_is_immutable() -> None:
    h = Harness()
    ident = uuid4()
    payload: dict[str, object] = {"sessionID": "explicit-session", "text": "original"}
    result = h.broker.submit(h.ctx, "chat", payload, ident)
    assert result["state"] == "pending"
    payload["text"] = "changed"
    assert h.bridge.calls[0].payload["text"] == "original"
    with pytest.raises(TypeError):
        h.bridge.calls[0].payload["text"] = "mutated"  # type: ignore[index]
    assert h.bridge.calls[0].capabilities == h.ctx.capabilities
    assert h.bridge.calls[0].payload["sessionID"] == "explicit-session"
    assert not h.bridge.effects
    h.bridge.finish()
    assert h.bridge.effects == ["chat"]
    assert h.store.data is not None
    assert h.store.data["records"][0]["state"] == "completed"  # type: ignore[index]


def test_no_request_reply_or_card_text_is_persistent() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    data = chat_reply(str(ident))
    data.update({"kind": "assistant", "card": {"body": "PRIVATE CARD SENTINEL"}})
    h.bridge.finish(data=data)
    persistent = json.dumps(h.store.writes)
    for secret in (
        "PRIVATE REQUEST SENTINEL",
        "PRIVATE REPLY SENTINEL",
        "PRIVATE CARD SENTINEL",
        "interaction-opaque",
    ):
        assert secret not in persistent
    assert "digest" in persistent
    assert "explicit-session" in persistent


def test_same_id_same_payload_reuses_result_without_post_replay() -> None:
    h = Harness()
    ident = uuid4()
    first = h.submit(ident)
    assert h.submit(ident) == first
    h.bridge.finish()
    result = h.submit(ident)
    assert result["state"] == "completed"
    result["text"] = "caller-mutated"
    assert h.submit(ident)["text"] == "PRIVATE REPLY SENTINEL"
    assert len(h.bridge.calls) == 1


def test_same_id_changed_payload_or_operation_is_conflict() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    with fault("request_id_conflict"):
        h.submit(ident, "different")
    with fault("request_id_conflict"):
        h.broker.submit(h.ctx, "new_session", {}, ident)
    assert len(h.bridge.calls) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("device_id", "other-device"),
        ("transport_id", "other-transport"),
        ("capabilities", frozenset({"chat"})),
        ("expires_at", 20_000),
    ],
)
def test_foreign_grant_cannot_obtain_existing_id(field: str, value: object) -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.bridge.finish()
    if field == "device_id":
        assert isinstance(value, str)
        other = replace(h.ctx, device_id=value)
    elif field == "transport_id":
        assert isinstance(value, str)
        other = replace(h.ctx, transport_id=value)
    elif field == "capabilities":
        assert isinstance(value, frozenset)
        other = replace(h.ctx, capabilities=value)
    else:
        assert isinstance(value, int | float)
        other = replace(h.ctx, expires_at=float(value))
    with fault("forbidden"):
        h.broker.reconcile(other, ident)
    with fault("forbidden"):
        h.broker.submit(
            other,
            "chat",
            {"sessionID": "explicit-session", "text": "PRIVATE REQUEST SENTINEL"},
            ident,
        )


def test_scope_change_prevents_cached_or_pending_publication() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.bridge.dispatch()
    h.scope = replace(h.scope, generation=2)
    h.bridge.finish()
    with fault("profile_changed"):
        h.broker.reconcile(h.ctx, ident)
    new_context = replace(h.ctx, scope=h.scope)
    with fault("forbidden"):
        h.broker.reconcile(new_context, ident)
    with fault("busy"):
        h.broker.submit(new_context, "new_session", {}, uuid4())


def test_revoke_before_queued_dispatch_causes_zero_effects() -> None:
    h = Harness()
    h.submit()
    h.revoked = True
    with fault("device_revoked"):
        h.bridge.dispatch()
    assert h.bridge.effects == []


def test_profile_change_before_queued_dispatch_causes_zero_effects() -> None:
    h = Harness()
    h.submit()
    h.scope = replace(h.scope, profile_id="new-profile")
    with fault("profile_changed"):
        h.bridge.dispatch()
    assert h.bridge.effects == []


def test_expiry_before_queued_dispatch_causes_zero_effects() -> None:
    h = Harness()
    h.submit()
    h.clock.advance(10_000)
    with fault("device_expired"):
        h.bridge.dispatch()
    assert h.bridge.effects == []


def test_dispatch_guard_is_one_use() -> None:
    h = Harness()
    h.submit()
    h.bridge.dispatch()
    with fault("request_id_conflict"):
        h.bridge.dispatch()
    assert h.bridge.effects == ["chat"]


def test_preflight_is_repeatable_without_consuming_effect_guard() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    call = h.bridge.calls[0]
    assert call.preflight_guard is not None
    deadline = call.deadline_monotonic
    call.preflight_guard()
    call.preflight_guard()
    assert not h.bridge.effects
    assert h.store.data is not None
    assert h.store.data["records"][0]["state"] == "pending"  # type: ignore[index]
    assert call.deadline_monotonic == deadline
    h.bridge.dispatch()
    with fault("request_id_conflict"):
        call.preflight_guard()
    with fault("request_id_conflict"):
        h.bridge.dispatch()
    h.bridge.finish()
    assert h.bridge.effects == ["chat"]
    assert h.broker.reconcile(h.ctx, ident)["state"] == "completed"


def test_preflight_alone_cannot_authorize_a_completion_callback() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    call = h.bridge.calls[0]
    assert call.preflight_guard is not None
    call.preflight_guard()
    call.preflight_guard()
    h.bridge.callbacks[0](h.bridge.result())
    assert h.submit(ident)["state"] == "unknown"
    assert not h.bridge.effects
    with fault("request_id_conflict"):
        call.preflight_guard()
    with fault("request_id_conflict"):
        h.bridge.dispatch()


@pytest.mark.parametrize("phase", ["before_preflight", "before_effect"])
@pytest.mark.parametrize("change", ["revoked", "expired", "profile", "transport"])
def test_preflight_and_effect_each_revalidate_current_authority(phase: str, change: str) -> None:
    h = Harness()
    current_transport = [h.ctx.transport_id]

    def revalidate(context: DeviceContext) -> None:
        h.revalidate(context)
        if context.transport_id != current_transport[0]:
            raise GatewayFault("device_revoked", 401)

    h.broker._revalidate = revalidate
    h.submit()
    call = h.bridge.calls[0]
    assert call.preflight_guard is not None
    if phase == "before_effect":
        call.preflight_guard()
        call.preflight_guard()
    if change == "revoked":
        h.revoked = True
        code = "device_revoked"
    elif change == "expired":
        h.clock.wall = h.ctx.expires_at
        code = "device_expired"
    elif change == "profile":
        h.scope = replace(h.scope, generation=h.scope.generation + 1)
        code = "profile_changed"
    else:
        current_transport[0] = "replacement-transport"
        code = "device_revoked"
    with fault(code):
        if phase == "before_preflight":
            call.preflight_guard()
        else:
            h.bridge.dispatch()
    assert not h.bridge.effects


def test_repeated_preflight_never_extends_the_original_deadline() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    call = h.bridge.calls[0]
    assert call.preflight_guard is not None
    deadline = call.deadline_monotonic
    h.clock.advance(119)
    call.preflight_guard()
    assert call.deadline_monotonic == deadline
    h.clock.advance(1)
    with fault("deadline_exceeded"):
        call.preflight_guard()
    with fault("deadline_exceeded"):
        h.bridge.dispatch()
    assert call.deadline_monotonic == deadline
    assert h.broker.reconcile(h.ctx, ident)["state"] == "unknown"
    assert not h.bridge.effects
    with fault("busy"):
        h.submit()


def test_old_preflight_cannot_validate_a_new_foreign_flight() -> None:
    h = Harness()
    h.submit()
    old = h.bridge.calls[0]
    assert old.preflight_guard is not None
    old.preflight_guard()
    h.bridge.finish()
    other = replace(h.ctx, device_id="device-b")
    h.broker.submit(other, "new_session", {}, uuid4())
    current = h.bridge.calls[1]
    assert current.preflight_guard is not None
    with fault("request_id_conflict"):
        old.preflight_guard()
    current.preflight_guard()
    assert h.bridge.effects == ["chat"]


def test_foreign_callback_cannot_advance_the_preflight_phase() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    call = h.bridge.calls[0]
    assert call.preflight_guard is not None
    call.preflight_guard()
    foreign = replace(h.bridge.result(), call_id=str(uuid4()))
    h.bridge.callbacks[0](foreign)
    call.preflight_guard()
    assert not h.bridge.effects
    h.bridge.finish()
    assert h.broker.reconcile(h.ctx, ident)["state"] == "completed"
    with fault("request_id_conflict"):
        call.preflight_guard()


def test_callback_revoke_drops_data_and_keeps_lane_quarantined() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.bridge.dispatch()
    h.revoked = True
    h.bridge.finish()
    with fault("device_revoked"):
        h.broker.reconcile(h.ctx, ident)
    h.revoked = False
    h.bridge.callbacks[0](h.bridge.result())
    assert h.broker.reconcile(h.ctx, ident)["state"] == "unknown"
    with fault("busy"):
        h.submit()


@pytest.mark.parametrize("field", ["call_id", "request_id", "device_id", "scope"])
def test_foreign_callback_cannot_publish_or_release_lane(field: str) -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.bridge.dispatch()
    original = h.bridge.result()
    if field == "scope":
        bad = replace(original, scope=replace(h.scope, generation=9))
    elif field == "call_id":
        bad = replace(original, call_id=str(uuid4()))
    elif field == "request_id":
        bad = replace(original, request_id=str(uuid4()))
    else:
        bad = replace(original, device_id="other-device")
    h.bridge.callbacks[0](bad)
    assert h.broker.reconcile(h.ctx, ident)["state"] == "pending"
    with fault("busy"):
        h.submit()
    h.bridge.finish()
    assert h.broker.reconcile(h.ctx, ident)["state"] == "completed"


def test_duplicate_callback_cannot_overwrite_first_result_or_new_flight() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.bridge.finish()
    h.broker.submit(h.ctx, "new_session", {}, uuid4())
    late = chat_reply(str(ident))
    late["text"] = "late duplicate"
    h.bridge.callbacks[0](h.bridge.result(data=late))
    assert h.broker.reconcile(h.ctx, ident)["text"] == "PRIVATE REPLY SENTINEL"
    with fault("busy"):
        h.submit()


def test_timeout_is_unknown_and_never_opens_parallel_lane() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.bridge.dispatch()
    h.clock.advance(121)
    assert h.broker.reconcile(h.ctx, ident)["state"] == "unknown"
    assert h.submit(ident)["state"] == "unknown"
    with fault("busy"):
        h.broker.read(h.ctx, "status", {})
    with fault("busy"):
        h.submit()
    assert len(h.bridge.calls) == 1
    h.bridge.finish()
    assert h.broker.reconcile(h.ctx, ident)["state"] == "unknown"
    assert h.bridge.calls[-1].operation == "reconcile"
    h.bridge.finish(1, data=chat_reply(str(ident)))
    assert h.broker.reconcile(h.ctx, ident)["state"] == "completed"
    assert h.submit()["state"] == "pending"


def test_deadline_guard_blocks_effect_while_retaining_lane() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.clock.advance(121)
    with fault("deadline_exceeded"):
        h.bridge.dispatch()
    assert h.bridge.effects == []
    with fault("busy"):
        h.submit()
    assert h.broker.reconcile(h.ctx, ident)["state"] == "unknown"


def test_native_exception_after_submit_is_unknown_not_failed_or_replayed() -> None:
    h = Harness()
    h.bridge.raise_submit = True
    ident = uuid4()
    assert h.submit(ident)["state"] == "unknown"
    assert h.submit(ident)["state"] == "unknown"
    with fault("busy"):
        h.submit()
    assert len(h.bridge.calls) == 1


def test_immediate_callback_and_raise_after_callback_preserve_known_result() -> None:
    h = Harness(wait=0.01)
    h.bridge.immediate = True
    h.bridge.raise_after_callback = True
    assert h.submit()["state"] == "completed"
    assert h.bridge.effects == ["chat"]


def test_pending_restart_is_unknown_without_dispatch_and_reconcile_is_read_only() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.broker = h.make_broker()
    assert h.submit(ident)["state"] == "unknown"
    assert len(h.bridge.calls) == 1
    result = h.broker.reconcile(h.ctx, ident)
    assert result["state"] == "unknown"
    assert h.bridge.calls[-1].operation == "reconcile"
    assert dict(h.bridge.calls[-1].payload) == {
        "requestID": str(ident),
        "sessionID": "explicit-session",
    }
    h.bridge.finish(1, data=chat_reply(str(ident)))
    assert h.broker.reconcile(h.ctx, ident)["state"] == "completed"
    assert h.bridge.effects == []


def test_completed_restart_keeps_no_reply_body_and_never_resubmits_chat() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.bridge.finish()
    h.broker = h.make_broker()
    value = h.submit(ident)
    assert value["state"] == "unknown"
    assert value["text"] == ""
    assert len(h.bridge.calls) == 1
    h.broker.reconcile(h.ctx, ident)
    assert h.bridge.calls[-1].operation == "reconcile"


def test_new_session_has_durable_intent_and_never_replays_after_restart() -> None:
    h = Harness()
    ident = uuid4()
    assert h.broker.submit(h.ctx, "new_session", {}, ident) == {
        "requestID": str(ident),
        "state": "pending",
        "sessionID": None,
    }
    h.broker = h.make_broker()
    assert h.broker.submit(h.ctx, "new_session", {}, ident)["state"] == "unknown"
    h.broker.reconcile(h.ctx, ident)
    assert h.bridge.calls[-1].operation == "reconcile"
    assert dict(h.bridge.calls[-1].payload) == {"requestID": str(ident), "sessionID": None}


def test_reads_share_lane_and_have_pending_then_deadline_closed_faults() -> None:
    h = Harness()
    with fault("request_pending"):
        h.broker.read(h.ctx, "history", {"sessionID": "explicit-session", "limit": 5})
    h.bridge.dispatch()
    with fault("busy"):
        h.submit()
    h.clock.advance(121)
    with fault("busy"):
        h.broker.read(h.ctx, "status", {})
    h.bridge.finish(data={"history": []})
    h.bridge.immediate = True
    assert h.broker.read(h.ctx, "status", {}) == {"ready": True}


def test_read_and_reconcile_dispatch_guards_revalidate_before_effect() -> None:
    h = Harness()
    with fault("request_pending"):
        h.broker.read(h.ctx, "status", {})
    h.revoked = True
    with fault("device_revoked"):
        h.bridge.dispatch()
    other = Harness()
    ident = uuid4()
    other.submit(ident)
    other.broker = other.make_broker()
    other.broker.reconcile(other.ctx, ident)
    other.revoked = True
    with fault("device_revoked"):
        other.bridge.dispatch(1)
    assert other.bridge.effects == []


def test_concurrent_http_submissions_have_exactly_one_owned_bridge_call() -> None:
    h = Harness()
    barrier = Barrier(2)

    def run() -> str:
        barrier.wait(timeout=2)
        try:
            return str(h.submit()["state"])
        except GatewayFault as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: run(), range(2)))
    assert sorted(values) == ["busy", "pending"]
    assert len(h.bridge.calls) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requestID", str(UUID(int=5))),
        ("sessionID", "foreign-session"),
        ("state", "arbitrary"),
        ("text", ["not text"]),
        ("extra", "unexpected"),
    ],
)
def test_malformed_result_cannot_be_published(field: str, value: object) -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    data = chat_reply(str(ident))
    data[field] = value
    h.bridge.finish(data=data)
    result = h.broker.reconcile(h.ctx, ident)
    assert result["state"] == "unknown"
    assert result["text"] == ""


def test_storage_failure_prevents_any_submit_and_private_error_is_closed() -> None:
    h = Harness()
    h.store.fail = True
    with fault("operation_failed"):
        h.submit()
    assert h.bridge.calls == []


def test_journal_capacity_never_discards_request_ids() -> None:
    h = Harness(max_records=2)
    h.bridge.immediate = True
    first = uuid4()
    h.submit(first)
    h.submit()
    with fault("busy"):
        h.submit()
    assert h.submit(first)["state"] == "completed"
    assert len(h.bridge.calls) == 2


def test_rate_admission_is_finite_even_for_cached_post_requests() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    for _ in range(119):
        h.submit(ident)
    with fault("busy"):
        h.submit(ident)
    assert len(h.bridge.calls) == 1


def test_closed_broker_drops_callback_and_never_reopens() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.bridge.dispatch()
    h.broker.close()
    h.bridge.finish()
    with fault("core_unavailable"):
        h.submit(ident)
    assert h.store.data is not None
    assert h.store.data["records"][0]["state"] == "unknown"  # type: ignore[index]


def test_permissions_are_per_operation_and_source_caps_are_never_invented() -> None:
    h = Harness()
    limited = replace(h.ctx, capabilities=frozenset({"status"}))
    with fault("forbidden"):
        h.broker.submit(limited, "new_session", {}, uuid4())
    with fault("not_supported"):
        h.broker.read(h.ctx, "chat", {})
    with fault("forbidden"):
        h.broker.read(limited, "history", {"sessionID": "opaque"})
    h.submit()
    assert h.bridge.calls[0].capabilities == DEFAULT_CAPABILITIES
    assert "calendar" not in h.bridge.calls[0].capabilities


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"sessionID": "", "text": "hello"},
        {"sessionID": "opaque", "text": ""},
        {"sessionID": "opaque", "text": "hello", "resume": True},
    ],
)
def test_ambiguous_or_extra_chat_payload_is_rejected(payload: dict[str, object]) -> None:
    h = Harness()
    with fault("invalid_request"):
        h.broker.submit(h.ctx, "chat", payload, uuid4())
    assert h.bridge.calls == []


def test_large_nonfinite_or_nonjson_payload_never_enters_bridge() -> None:
    h = Harness()
    with fault("too_large"):
        h.submit(text="x" * 65_537)
    for payload in ({"bad": float("nan")}, {"bad": object()}):
        with fault("invalid_request"):
            h.broker.read(h.ctx, "status", payload)
    assert h.bridge.calls == []


def test_corrupt_or_extra_body_bearing_journal_is_not_overwritten() -> None:
    h = Harness()
    h.submit()
    assert h.store.data is not None
    h.store.data["request_text"] = "not allowed"
    before = copy.deepcopy(h.store.data)
    with fault("operation_failed"):
        h.make_broker()
    assert h.store.data == before


def test_callback_after_deadline_without_any_poll_never_publishes_its_body() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.bridge.dispatch()
    h.clock.advance(121)
    h.bridge.finish()
    value = h.submit(ident)
    assert value["state"] == "unknown"
    assert value["text"] == ""
    assert len(h.bridge.calls) == 1
    h.broker.reconcile(h.ctx, ident)
    assert h.bridge.calls[-1].operation == "reconcile"


@pytest.mark.parametrize(("field", "size"), [("text", 64_001), ("card", 24_001)])
def test_reply_and_card_body_limits_remain_unknown(field: str, size: int) -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    data = chat_reply(str(ident))
    if field == "card":
        data.update({"kind": "assistant", "card": {"body": "x" * size}})
    else:
        data["text"] = "x" * size
    h.bridge.finish(data=data)
    value = h.submit(ident)
    assert value["state"] == "unknown"
    assert value["text"] == ""


def test_512_minimal_metadata_records_fit_actual_private_store(tmp_path: Path) -> None:
    from satori.desktop.mobile_gateway.private_store import PrivateStore

    h = Harness()
    h.submit()
    assert h.store.data is not None
    initial = h.store.data["records"]
    assert isinstance(initial, list)
    prototype = initial[0]
    rows = []
    for index in range(512):
        row = copy.deepcopy(prototype)
        row["request_id"] = str(UUID(int=index + 1))
        row["call_id"] = str(UUID(int=index + 1000))
        rows.append(row)
    store = PrivateStore(tmp_path / "private-gateway")
    try:
        store.write_json("requests.json", {"version": 1, "records": rows})
        bridge = Bridge(h.store)
        bridge.immediate = True
        broker = Broker(
            store,
            bridge,
            h.revalidate,
            lambda: h.scope,
            clock=lambda: h.clock.wall,
            monotonic=lambda: h.clock.mono,
            wait_seconds=0,
        )
        assert bridge.calls == []
        assert broker.read(h.ctx, "status", {}) == {"ready": True}
        with fault("busy"):
            broker.submit(h.ctx, "new_session", {}, uuid4())
        assert len(bridge.calls) == 1
    finally:
        store.close()


def test_metadata_byte_budget_rejects_before_effect_without_disabling_reads() -> None:
    h = Harness()
    capabilities = DEFAULT_CAPABILITIES | frozenset(str(index) + "я" * 60 for index in range(28))
    h.ctx = replace(h.ctx, capabilities=capabilities)
    h.bridge.immediate = True
    rejection = None
    for _ in range(512):
        h.clock.advance(1)
        try:
            h.submit()
        except GatewayFault as exc:
            rejection = exc.code
            break
    else:
        pytest.fail("Metadata admission must enforce the byte budget before record-count capacity.")
    assert rejection == "busy"
    effects = len(h.bridge.effects)
    assert effects < 512
    assert h.store.data is not None
    assert len(h.store.data["records"]) == effects  # type: ignore[arg-type]
    assert h.broker.read(h.ctx, "status", {}) == {"ready": True}


def test_callback_journal_failure_is_private_and_keeps_original_intent() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    h.bridge.dispatch()
    original = copy.deepcopy(h.store.data)
    h.store.fail = True
    h.clock.advance(121)
    h.bridge.finish()  # No exception escapes the callback into its Qt/process owner.
    assert h.store.data == original
    with fault("core_unavailable"):
        h.submit(ident)


def test_history_response_cannot_publish_another_explicit_session() -> None:
    h = Harness()
    h.bridge.immediate = True
    with fault("operation_failed"):
        h.broker.read(h.ctx, "history", {"sessionID": "explicit-session"})


def test_bridge_reprs_never_include_request_or_reply_text() -> None:
    h = Harness()
    ident = uuid4()
    h.submit(ident)
    assert "PRIVATE REQUEST SENTINEL" not in repr(h.bridge.calls[0])
    assert "PRIVATE REPLY SENTINEL" not in repr(h.bridge.result())


def history_data(count: int, text_size: int) -> dict[str, object]:
    return {
        "sessionID": "explicit-session",
        "truncated": False,
        "messages": [
            {
                "id": str(index),
                "role": "assistant",
                "text": "x" * text_size,
                "createdAt": "2026-10-31T00:00:00Z",
                "status": "completed",
            }
            for index in range(count)
        ],
    }


def test_valid_history_above_old_half_megabyte_limit_is_not_rejected() -> None:
    h = Harness()
    h.bridge.immediate = True
    h.bridge.override_data = history_data(10, 64_000)
    result = h.broker.read(h.ctx, "history", {"sessionID": "explicit-session"})
    assert len(result["messages"]) == 10  # type: ignore[arg-type]
    assert result["truncated"] is False


def test_history_over_wire_budget_retains_latest_whole_messages_and_marks_partial() -> None:
    h = Harness()
    h.bridge.immediate = True
    original = history_data(40, 64_000)
    h.bridge.override_data = original
    result = h.broker.read(h.ctx, "history", {"sessionID": "explicit-session"})
    messages = result["messages"]
    assert isinstance(messages, list)
    assert 0 < len(messages) < 40
    assert messages[-1]["id"] == "39"
    assert all(len(item["text"]) == 64_000 for item in messages)
    assert result["truncated"] is True
    assert (
        len(json.dumps({"ok": True, "data": result}, ensure_ascii=True, separators=(",", ":")))
        <= 1_048_576
    )
    assert len(original["messages"]) == 40  # type: ignore[arg-type]
    assert original["truncated"] is False


def test_history_count_bound_keeps_newest_64_without_mutating_raw_data() -> None:
    h = Harness()
    h.bridge.immediate = True
    h.bridge.override_data = history_data(65, 1)
    result = h.broker.read(h.ctx, "history", {"sessionID": "explicit-session"})
    messages = result["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 64
    assert messages[0]["id"] == "1"
    assert messages[-1]["id"] == "64"
    assert result["truncated"] is True
