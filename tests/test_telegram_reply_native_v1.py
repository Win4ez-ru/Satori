"""Public fixture native lane only; no TDLib client, credentials or account."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from satori.application.assistant.contracts import LocalPrincipal
from satori.desktop.telegram_port import WireRejected
from satori.desktop.telegram_reply_native import (
    LEGACY_WORKER_SHA,
    build_reply_engine,
    legacy_definitions,
)
from satori.desktop.telegram_reply_wire import parse_reply_frame
from satori.desktop.telegram_reply_worker import PROTOCOL_SHA256, verified_reply_protocol

REPO = Path(__file__).resolve().parents[1]
RESOURCES = REPO / "src/satori/resources"


@dataclass
class Clock:
    mono: float = 100.0
    wall: float = 1800000000.0


class NativeFixture:
    def __init__(self) -> None:
        self.queue: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.effects = 0
        self.paid = 0
        self.regular = True
        self.before_effect: Any = None
        self.pending: dict[str, Any] | None = None
        self.lookup: dict[str, Any] | None = None

    def send(self, query: dict[str, Any]) -> None:
        self.calls.append(deepcopy(query))
        kind = query["@type"]
        if kind == "getChat":
            response = {
                "@type": "chat",
                "id": 10,
                "title": "PUBLIC CHAT",
                "type": {"@type": "chatTypePrivate", "user_id": 20},
                "has_protected_content": False,
                "message_auto_delete_time": 0,
                "paid_message_star_count": self.paid,
            }
        elif kind == "getUser":
            response = {
                "@type": "user",
                "id": 20,
                "type": {"@type": "userTypeRegular" if self.regular else "userTypeBot"},
            }
        elif kind == "sendMessage":
            if self.before_effect is not None:
                self.before_effect()
            self.effects += 1
            self.pending = self.message(query["options"]["sending_id"], pending=True)
            response = self.pending
        elif kind == "getMessage":
            response = self.lookup or {"@type": "error", "code": 404, "message": "PUBLIC"}
        elif kind in {"setOption", "openChat", "closeChat", "setMessageViewingMetrics"}:
            response = {"@type": "ok"}
        else:
            raise AssertionError("unexpected fixture RPC")
        self.queue.append({**response, "@extra": query["@extra"]})

    def receive(self, _timeout: float) -> dict[str, Any] | None:
        return self.queue.pop(0) if self.queue else None

    @staticmethod
    def message(sending_id: int, *, pending: bool) -> dict[str, Any]:
        return {
            "@type": "message",
            "id": -101 if pending else 200,
            "chat_id": 10,
            "is_outgoing": True,
            "paid_message_star_count": 0,
            "sending_state": {"@type": "messageSendingStatePending", "sending_id": sending_id}
            if pending
            else None,
            "scheduling_state": None,
            "content": {"@type": "messageText", "text": {"text": "PUBLIC REPLY", "entities": []}},
            "reply_to": {"@type": "messageReplyToMessage", "chat_id": 10, "message_id": 22},
        }


class SDKBoundaryFixture(NativeFixture):
    """Exercises the production native.send override without loading a library."""

    def __init__(self, *_args: Any) -> None:
        super().__init__()
        self.client_id = 1
        self.lib = self

    def td_send(self, _client_id: int, data: bytes) -> None:
        NativeFixture.send(self, json.loads(data))


def make_engine(
    tmp_path: Path, *, sdk_boundary: bool = False
) -> tuple[Any, NativeFixture, Clock, list[dict[str, Any]]]:
    legacy = legacy_definitions(RESOURCES / "telegram/backend/worker.py.source")
    if sdk_boundary:
        legacy["NativeClient"] = SDKBoundaryFixture
    clock, native = Clock(), NativeFixture()
    events: list[dict[str, Any]] = []
    principal = LocalPrincipal(os_uid=os.getuid(), profile_id=uuid4(), session_id=uuid4())
    engine = build_reply_engine(
        legacy,
        Path("/public/not-loaded.dylib"),
        "0" * 64,
        tmp_path / "temporary",
        events.append,
        principal=principal,
        generation=1,
        journal_path=tmp_path / "reply-journal" / "attempts.sqlite3",
        native_factory=None if sdk_boundary else lambda *_args: native,
        clock=lambda: clock.mono,
        wall=lambda: clock.wall,
    )
    engine.native = engine.native_factory(Path("/public/not-loaded.dylib"), "0" * 64)
    if sdk_boundary:
        native = cast(NativeFixture, engine.native)
    engine.account_confirmed = True
    engine.auth_state = "authorizationStateReady"
    engine.account_id = 1
    engine.selected_token, engine.page_id = uuid4().hex, uuid4().hex
    engine.selected_chat = {"id": 10, "user_id": 20, "title": "PUBLIC CHAT"}
    engine.session_until, engine.grant_until, engine.page_until = 1000.0, 900.0, 400.0
    engine.last_presence_active, engine.last_presence_sample = True, clock.mono
    handle = uuid4().hex
    engine.messages = {
        handle: legacy["Message"](22, 20, handle, "PUBLIC ORIGINAL", int(clock.wall))
    }
    return engine, native, clock, events


class LoginNativeFixture(NativeFixture):
    """Full synthetic authorization handshake through the production Engine RPC lane."""

    def __init__(self) -> None:
        super().__init__()
        self.auth_state = "authorizationStateWaitTdlibParameters"

    def send(self, query: dict[str, Any]) -> None:
        kind = query["@type"]
        if kind in {
            "getAuthorizationState",
            "setTdlibParameters",
            "setAuthenticationPhoneNumber",
            "checkAuthenticationCode",
            "getMe",
            "close",
        }:
            self.calls.append(deepcopy(query))
            response: dict[str, Any]
            if kind == "getAuthorizationState":
                response = {"@type": self.auth_state}
            elif kind == "getMe":
                response = {
                    "@type": "user",
                    "id": 1,
                    "first_name": "PUBLIC",
                    "type": {"@type": "userTypeRegular"},
                }
            elif kind == "close":
                self.queue.append(
                    {
                        "@type": "updateAuthorizationState",
                        "authorization_state": {"@type": "authorizationStateClosed"},
                    }
                )
                return
            else:
                self.auth_state = {
                    "setTdlibParameters": "authorizationStateWaitPhoneNumber",
                    "setAuthenticationPhoneNumber": "authorizationStateWaitCode",
                    "checkAuthenticationCode": "authorizationStateReady",
                }[kind]
                response = {"@type": "ok"}
            self.queue.append({**response, "@extra": query["@extra"]})
        else:
            super().send(query)


def test_native_confirmed_login_survives_old_limit_and_expires_at_original_workday(
    tmp_path: Path,
) -> None:
    legacy = legacy_definitions(RESOURCES / "telegram/backend/worker.py.source")
    clock, native = Clock(), LoginNativeFixture()
    events: list[dict[str, Any]] = []
    engine = legacy["Engine"](
        Path("/public/not-loaded.dylib"),
        "0" * 64,
        tmp_path / "temporary",
        events.append,
        native_factory=lambda *_args: native,
        clock=lambda: clock.mono,
        wall=lambda: clock.wall,
    )
    engine.command(
        {
            "v": 1,
            "request_id": "PUBLIC_CONNECT",
            "command": "connect",
            "session_only": True,
            "api_id": 1,
            "api_hash": "0" * 32,
        }
    )
    assert engine.auth_until == 400
    for number, kind, value in ((1, "PHONE", "+10000000000"), (2, "CODE", "00000")):
        assert engine.challenge_kind == kind
        engine.command(
            {
                "v": 1,
                "request_id": f"PUBLIC_AUTH_{number}",
                "command": "auth_submit",
                "challenge_id": engine.challenge_id,
                "kind": kind,
                "value": value,
            }
        )
    assert events[-1]["type"] == "account"
    assert events[-1]["session_seconds"] == 86400
    engine.command(
        {
            "v": 1,
            "request_id": "PUBLIC_CONFIRM",
            "command": "confirm_account",
            "account_token": engine.account_token,
        }
    )
    original_deadline, owned_path = engine.session_until, engine.session.path
    authentication_calls = [
        q
        for q in native.calls
        if q["@type"]
        in {
            "setTdlibParameters",
            "setAuthenticationPhoneNumber",
            "checkAuthenticationCode",
        }
    ]
    for now in (1001.0, 43200.0, original_deadline - 1):
        clock.mono = now
        engine.drain()
        assert engine.account_confirmed
        assert not engine.stopped
        assert engine.session_until == original_deadline == 86500
    assert [
        q
        for q in native.calls
        if q["@type"]
        in {
            "setTdlibParameters",
            "setAuthenticationPhoneNumber",
            "checkAuthenticationCode",
        }
    ] == authentication_calls
    clock.mono = original_deadline
    with pytest.raises(legacy["Rejected"]) as caught:
        engine.drain()
    assert caught.value.reason == "SESSION_EXPIRED"
    assert not engine.pause_expired_view(caught.value)
    engine.close(caught.value.reason, caught.value)
    assert engine.stopped
    assert not owned_path.exists()
    assert engine.session.key == ""
    assert events[-1]["cleanup"] == "CONFIRMED"


@pytest.mark.parametrize("source", ["page", "grant"])
def test_native_source_expiry_clears_view_without_ending_confirmed_login(
    tmp_path: Path,
    source: str,
) -> None:
    engine, native, clock, events = make_engine(tmp_path)
    legacy = engine.reply_extension.legacy
    engine.session_until = 86500
    engine.opened_chat = 10
    if source == "grant":
        engine.page_id, engine.page_until = None, 0
        clock.mono = engine.grant_until
    else:
        clock.mono = engine.page_until
    reason = "PAGE_EXPIRED" if source == "page" else "GRANT_EXPIRED"
    with pytest.raises(legacy["Rejected"]) as caught:
        engine.drain()
    assert caught.value.reason == reason
    assert engine.pause_expired_view(caught.value)
    assert engine.account_confirmed
    assert engine.phase == "READY"
    assert not engine.stopped
    assert engine.session_until == 86500
    assert engine.native is not None
    assert not engine.messages
    assert engine.selected_chat is None
    assert engine.page_id is None
    assert engine.opened_chat is None
    assert [q["@type"] for q in native.calls] == ["closeChat"]
    assert any(e["type"] == "cleared" and e["reason"] == reason for e in events)
    clock.mono = 1001
    engine.drain()
    assert engine.account_confirmed
    assert not engine.stopped


def test_native_expiry_racing_queued_conceal_keeps_account_and_goes_offline(
    tmp_path: Path,
) -> None:
    engine, native, clock, _events = make_engine(tmp_path)
    legacy = engine.reply_extension.legacy
    engine.session_until, engine.online, engine.opened_chat = 86500, True, 10
    engine.interrupted.set()
    clock.mono = engine.page_until
    with pytest.raises(legacy["Rejected"]) as caught:
        engine.drain()
    assert engine.pause_expired_view(caught.value)
    assert engine.account_confirmed
    assert engine.online
    assert [q["@type"] for q in native.calls] == ["closeChat"]
    engine.command({"v": 1, "request_id": "PUBLIC_CONCEAL", "command": "conceal"})
    assert engine.account_confirmed
    assert not engine.online
    assert not engine.interrupted.is_set()
    assert not engine.stopped
    assert [q["@type"] for q in native.calls] == ["closeChat", "setOption"]
    assert native.calls[-1]["name"] == "online"
    assert native.calls[-1]["value"]["value"] is False


def test_native_expired_view_rpc_retires_late_answer_without_reauthentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, native, clock, _events = make_engine(tmp_path)
    legacy = engine.reply_extension.legacy
    engine.session_until = 86500
    receive = native.receive
    engine.page_until = 101

    def expire_before_answer(timeout: float) -> dict[str, Any] | None:
        if clock.mono < 101:
            clock.mono = 101
            return None
        return receive(timeout)

    monkeypatch.setattr(native, "receive", expire_before_answer)
    with pytest.raises(legacy["Rejected"]) as caught:
        engine.rpc({"@type": "getChat", "chat_id": 10})
    token = native.calls[-1]["@extra"]
    assert caught.value.reason == "PAGE_EXPIRED"
    assert token in engine.retired
    assert engine.pause_expired_view(caught.value)
    engine.drain()
    assert not native.queue
    assert engine.account_confirmed
    assert engine.phase == "READY"
    assert not engine.stopped
    assert [q["@type"] for q in native.calls] == ["getChat"]


def command(engine: Any, operation: str, **fields: Any) -> None:
    engine.command(
        {
            "v": 2,
            "capability": "reviewed_reply_v1",
            "request_id": uuid4().hex,
            "command": operation,
            **fields,
        }
    )


def test_worker_conceal_retires_interrupted_open_and_closes_chat_without_logout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, native, _clock, events = make_engine(tmp_path)
    deadline = engine.session_until
    receive = native.receive

    def interrupt_open(timeout: float) -> dict[str, Any] | None:
        if native.calls[-1]["@type"] == "openChat":
            engine.interrupted.set()
            return None
        return receive(timeout)

    monkeypatch.setattr(native, "receive", interrupt_open)
    legacy = engine.reply_extension.legacy
    with pytest.raises(legacy["Rejected"]) as caught:
        engine.command({"v": 1, "request_id": "read-1", "command": "read"})
    assert engine.pause_interrupted_view(caught.value)
    token = native.calls[-1]["@extra"]
    assert token in engine.retired
    assert engine.opened_chat == 10
    assert not engine.read_inflight
    engine.command({"v": 1, "request_id": "hide-1", "command": "conceal"})
    assert [x["@type"] for x in native.calls] == ["getChat", "openChat", "closeChat"]
    assert not native.queue
    assert engine.opened_chat is None
    assert engine.account_confirmed
    assert engine.session_until == deadline
    assert not engine.selected_chat
    assert not engine.page_id
    assert not engine.messages
    assert not engine.stopped
    assert not engine.interrupted.is_set()
    assert events[-1]["operation"] == "CONCEAL"
    assert events[-1]["step"] == "NONE"
    assert events[-1]["request_id"] == "hide-1"


def test_worker_focus_interrupt_does_not_mask_auth_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, native, _clock, _events = make_engine(tmp_path)

    def auth_loss(_timeout: float) -> dict[str, Any]:
        engine.interrupted.set()
        return {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateClosing"},
        }

    monkeypatch.setattr(native, "receive", auth_loss)
    legacy = engine.reply_extension.legacy
    with pytest.raises(legacy["Rejected"]) as caught:
        engine.command({"v": 1, "request_id": "read-1", "command": "read"})
    assert caught.value.reason == "AUTH_LOST"
    assert not engine.pause_interrupted_view(caught.value)


def test_worker_conceal_wins_over_concurrent_new_message_invalidation(tmp_path: Path) -> None:
    engine, native, _clock, events = make_engine(tmp_path)
    legacy = engine.reply_extension.legacy
    stale = legacy["SnapshotStale"](engine.page_id, "read-1", from_read=True)
    before = list(events)
    engine.current_command = "read"
    engine.current_request = "read-1"
    engine.interrupted.set()
    engine.stale_page(stale)
    assert engine.current_request is None
    assert events == before
    assert native.calls == []

    engine.command({"v": 1, "request_id": "hide-1", "command": "conceal"})
    assert engine.account_confirmed
    assert engine.page_id is None
    assert not engine.messages
    assert not engine.stopped
    assert events[-1]["request_id"] == "hide-1"


def preview(engine: Any, events: list[dict[str, Any]]) -> dict[str, Any]:
    command(
        engine,
        "reply_preview",
        page_id=engine.page_id,
        message_handle=next(iter(engine.messages)),
        text="PUBLIC REPLY",
        action_id=str(uuid4()),
    )
    assert events[-1]["status"] == "preview"
    return events[-1]


def approved(engine: Any, events: list[dict[str, Any]]) -> dict[str, Any]:
    p = preview(engine, events)
    command(
        engine,
        "reply_approve",
        action_id=p["action_id"],
        payload_digest=p["payload_digest"],
        confirmed=True,
    )
    assert events[-1]["status"] == "approved"
    return events[-1]


def test_real_owner_pending_terminal_update_then_explicit_getmessage(tmp_path: Path) -> None:
    engine, native, _, events = make_engine(tmp_path)
    try:
        a = approved(engine, events)
        command(engine, "reply_send", action_id=a["action_id"], approval_id=a["approval_id"])
        assert events[-1]["status"] == "unknown"
        assert native.effects == 1
        prior = engine.reply_extension.owner.receipt(engine.reply_extension.draft.request_id)
        final = native.message(prior.sending_id, pending=False)
        engine._update(
            {"@type": "updateMessageSendSucceeded", "old_message_id": -101, "message": final}
        )
        assert "content" not in engine.reply_extension._terminal_updates[0]["message"]
        assert events[-1]["status"] == "unknown"  # Update alone is not SENT.
        native.lookup = final
        command(engine, "reply_reconcile", action_id=a["action_id"])
        assert events[-1]["status"] == "sent"
        assert native.effects == 1
        assert [q["@type"] for q in native.calls].count("getMessage") == 1
        command(engine, "reply_send", action_id=a["action_id"], approval_id=a["approval_id"])
        assert native.effects == 1
        assert events[-1]["status"] == "unknown"
        assert all("chat_id" not in row and "message_id" not in row for row in events)
    finally:
        engine.reply_extension.close()


@pytest.mark.parametrize(
    "change",
    [
        "paid",
        "bot",
        "wrong_handle",
        "own_message",
        "wrong_page",
        "not_foreground",
        "unconfirmed",
        "expired_page",
    ],
)
def test_preview_rejects_ineligible_or_stale_scope(tmp_path: Path, change: str) -> None:
    engine, native, clock, events = make_engine(tmp_path)
    handle, page = next(iter(engine.messages)), engine.page_id
    if change == "paid":
        native.paid = 1
    elif change == "bot":
        native.regular = False
    elif change == "wrong_handle":
        handle = uuid4().hex
    elif change == "own_message":
        engine.messages[handle].sender_id = engine.account_id
    elif change == "wrong_page":
        page = uuid4().hex
    elif change == "not_foreground":
        engine.last_presence_active = False
    elif change == "unconfirmed":
        engine.account_confirmed = False
    else:
        engine.page_until = clock.mono
    try:
        command(
            engine,
            "reply_preview",
            page_id=page,
            message_handle=handle,
            text="PUBLIC REPLY",
            action_id=str(uuid4()),
        )
        assert events[-1]["status"] == "unavailable"
        assert native.effects == 0
    finally:
        engine.reply_extension.close()


@pytest.mark.parametrize(
    "change", ["digest", "not_confirmed", "text_edit", "page", "expiry", "interrupted", "paid"]
)
def test_exact_review_expiry_and_last_native_guard(tmp_path: Path, change: str) -> None:
    engine, native, clock, events = make_engine(tmp_path)
    try:
        p = preview(engine, events)
        command(
            engine,
            "reply_approve",
            action_id=p["action_id"],
            payload_digest="f" * 64 if change == "digest" else p["payload_digest"],
            confirmed=change != "not_confirmed",
        )
        if change in {"digest", "not_confirmed"}:
            assert events[-1]["status"] == "unavailable"
            assert native.effects == 0
            return
        a = events[-1]
        if change == "text_edit":
            command(engine, "reply_discard")
        elif change == "page":
            engine.page_id = uuid4().hex
        elif change == "expiry":
            clock.mono += 61
            clock.wall += 61
            engine.last_presence_sample = clock.mono
        elif change == "interrupted":
            engine.interrupted.set()
        elif change == "paid":
            native.paid = 1
        command(engine, "reply_send", action_id=a["action_id"], approval_id=a["approval_id"])
        assert native.effects == 0
        assert events[-1]["status"] == "unknown"
    finally:
        engine.reply_extension.close()


def test_native_guard_runs_after_rpc_queueing_before_send(tmp_path: Path) -> None:
    engine, native, _, events = make_engine(tmp_path)
    try:
        a = approved(engine, events)
        original = engine.rpc

        def queued(query: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            if query["@type"] == "sendMessage":
                engine.interrupted.set()
            return cast(dict[str, Any], original(query, **kwargs))

        engine.rpc = queued
        command(engine, "reply_send", action_id=a["action_id"], approval_id=a["approval_id"])
        assert native.effects == 0
        assert events[-1]["status"] == "unknown"
        assert (
            engine.reply_extension.owner.receipt(engine.reply_extension.draft.request_id).state
            == "unknown"
        )
    finally:
        engine.reply_extension.close()


def test_actual_native_send_override_rechecks_after_rpc_preflight(tmp_path: Path) -> None:
    engine, native, _, events = make_engine(tmp_path, sdk_boundary=True)
    try:
        a = approved(engine, events)
        original = engine.native.send

        def delayed(query: dict[str, Any]) -> None:
            if query["@type"] == "sendMessage":
                engine.interrupted.set()
            original(query)

        engine.native.send = delayed
        command(engine, "reply_send", action_id=a["action_id"], approval_id=a["approval_id"])
        assert native.effects == 0
        assert events[-1]["status"] == "unknown"
        assert (
            engine.reply_extension.owner.receipt(engine.reply_extension.draft.request_id).state
            == "unknown"
        )
    finally:
        engine.reply_extension.close()


def test_durable_unknown_precedes_the_native_effect_and_survives_reopen(tmp_path: Path) -> None:
    engine, native, _, events = make_engine(tmp_path)
    a = approved(engine, events)
    path = tmp_path / "reply-journal/attempts.sqlite3"
    observations: list[str] = []

    def observe_claim() -> None:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            receipt = json.loads(db.execute("SELECT receipt FROM reply_attempts").fetchone()[0])
        assert native.effects == 0
        assert receipt["state"] == "unknown"
        observations.append(receipt["code"])

    native.before_effect = observe_claim
    command(engine, "reply_send", action_id=a["action_id"], approval_id=a["approval_id"])
    assert observations == ["dispatch_claimed"]
    assert native.effects == 1
    engine.reply_extension.close()
    reopened, other_native, _, other_events = make_engine(tmp_path)
    try:
        command(
            reopened,
            "reply_preview",
            page_id=reopened.page_id,
            message_handle=next(iter(reopened.messages)),
            text="PUBLIC REPLY",
            action_id=a["action_id"],
        )
        assert other_events[-1]["status"] == "unavailable"
        assert other_native.effects == 0
    finally:
        reopened.reply_extension.close()


@pytest.mark.parametrize("field", ["paid", "text", "target", "chat"])
def test_invalid_getmessage_cannot_promote_pending_to_sent(tmp_path: Path, field: str) -> None:
    engine, native, _, events = make_engine(tmp_path)
    try:
        a = approved(engine, events)
        command(engine, "reply_send", action_id=a["action_id"], approval_id=a["approval_id"])
        prior = engine.reply_extension.owner.receipt(engine.reply_extension.draft.request_id)
        final = native.message(prior.sending_id, pending=False)
        engine._update(
            {"@type": "updateMessageSendSucceeded", "old_message_id": -101, "message": final}
        )
        native.lookup = deepcopy(final)
        if field == "paid":
            native.lookup["paid_message_star_count"] = 1
        elif field == "text":
            native.lookup["content"]["text"]["text"] = "PUBLIC OTHER"
        elif field == "target":
            native.lookup["reply_to"]["message_id"] = 23
        else:
            native.lookup["chat_id"] = 11
        command(engine, "reply_reconcile", action_id=a["action_id"])
        assert events[-1]["status"] == "unknown"
        assert native.effects == 1
    finally:
        engine.reply_extension.close()


def test_hide_after_effect_does_not_publish_sent_or_resend(tmp_path: Path) -> None:
    engine, native, _, events = make_engine(tmp_path)
    try:
        a = approved(engine, events)
        native.before_effect = engine.interrupted.set
        command(engine, "reply_send", action_id=a["action_id"], approval_id=a["approval_id"])
        assert native.effects == 1
        assert events[-1]["status"] == "unknown"
        assert set(events[-1]) <= {
            "v",
            "capability",
            "event_seq",
            "type",
            "request_id",
            "status",
            "action_id",
            "code",
        }
    finally:
        engine.reply_extension.close()


def test_404_getmessage_retains_unknown(tmp_path: Path) -> None:
    engine, native, _, events = make_engine(tmp_path)
    try:
        a = approved(engine, events)
        command(engine, "reply_send", action_id=a["action_id"], approval_id=a["approval_id"])
        command(engine, "reply_reconcile", action_id=a["action_id"])
        assert events[-1]["status"] == "unknown"
        assert native.effects == 1
    finally:
        engine.reply_extension.close()


def test_new_capability_protocol_pins_and_legacy_not_widened() -> None:
    verified_reply_protocol(RESOURCES)
    worker = RESOURCES / "telegram/backend/worker.py.source"
    assert hashlib.sha256(worker.read_bytes()).hexdigest() == LEGACY_WORKER_SHA
    namespace = legacy_definitions(worker)
    assert "sendMessage" not in namespace["ALLOWED_NATIVE"]
    assert "getMessage" not in namespace["ALLOWED_NATIVE"]
    assert (
        hashlib.sha256((RESOURCES / "telegram/reply_v1/protocol.json").read_bytes()).hexdigest()
        == PROTOCOL_SHA256
    )


def test_wire_closed_shapes_and_no_native_ids(tmp_path: Path) -> None:
    engine, _, _, events = make_engine(tmp_path)
    try:
        p = preview(engine, events)
        view = parse_reply_frame(p, sequence=p["event_seq"] - 1)
        assert view.text == "PUBLIC REPLY"
        assert view.state == "preview"
        for key, value in (
            ("chat_id", 10),
            ("message_id", 22),
            ("capability", "read"),
            ("event_seq", 0),
        ):
            bad = {**p, key: value}
            with pytest.raises(WireRejected):
                parse_reply_frame(bad, sequence=p["event_seq"] - 1)
    finally:
        engine.reply_extension.close()
