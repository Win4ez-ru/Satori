"""Offscreen queue/owner regressions: no worker, profiles, accounts or provider calls."""

# ruff: noqa: RUF001 - synthetic Russian intent inputs

import json
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtWidgets import QApplication

from satori.application.assistant.reminder_contracts import (
    ReminderActionReceipt,
    ReminderRecord,
    reminder_digest,
)
from satori.desktop.mobile_bridge import MobileBridge
from satori.desktop.mobile_gateway.broker import Broker
from satori.desktop.mobile_gateway.contracts import (
    DEFAULT_CAPABILITIES,
    BridgeCall,
    BridgeResult,
    DeviceContext,
    GatewayFault,
    Operation,
    ScopeBinding,
)
from satori.desktop.reminder_controller import ReminderController

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
STAMP = "2026-10-31T12:00:00Z"


class Process(QObject):
    finished = Signal(int, object)

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[dict[str, object]] = []
        self.write_threads: list[int] = []
        self.running = True
        self.partial = False
        self.before_write: Callable[[], None] | None = None
        self.mac_selection = "desktop-selected"

    def state(self) -> QProcess.ProcessState:
        return QProcess.ProcessState.Running if self.running else QProcess.ProcessState.NotRunning

    def write(self, data: bytes) -> int:
        if self.before_write is not None:
            self.before_write()
        self.frames.append(json.loads(data))
        self.write_threads.append(threading.get_ident())
        return len(data) - 1 if self.partial else len(data)


class Reminders:
    def __init__(self) -> None:
        self.status = "ready"
        self.diagnostic: str | None = None
        self._records: list[ReminderRecord] = []
        self.creates: list[tuple[str, datetime, str, UUID | None]] = []
        self.cancels: list[tuple[UUID, UUID | None]] = []
        self.refreshes = 0
        self.fail = False
        self.unknown_after_commit = False
        self.receipts: dict[UUID, ReminderActionReceipt] = {}
        self.reconciles: list[UUID] = []

    @property
    def records(self) -> tuple[ReminderRecord, ...]:
        return tuple(self._records)

    def refresh(self) -> None:
        self.refreshes += 1

    def create(
        self, title: str, due: datetime, zone: str, request_id: UUID | None = None
    ) -> ReminderActionReceipt | None:
        self.creates.append((title, due, zone, request_id))
        if self.fail:
            return None
        assert request_id is not None
        record = next((item for item in self._records if item.request_id == request_id), None)
        existing = record is not None
        if record is None:
            ident = uuid4()
            record = ReminderRecord(
                reminder_id=ident,
                request_id=request_id,
                title=title,
                due_at=due,
                timezone=zone,
                created_at=NOW,
                updated_at=NOW,
                payload_digest=reminder_digest(title, due, zone),
                notification_identifier=f"satori.reminder.{ident}",
            )
            self._records.append(record)
        receipt = ReminderActionReceipt(
            request_id=request_id,
            operation="create",
            status="existing" if existing else "created",
            record=record,
            observed_at=NOW,
        )
        self.receipts[request_id] = receipt
        return None if self.unknown_after_commit else receipt

    def cancel(
        self, reminder_id: UUID, request_id: UUID | None = None
    ) -> ReminderActionReceipt | None:
        self.cancels.append((reminder_id, request_id))
        if self.fail:
            return None
        assert request_id is not None
        record = next(item for item in self._records if item.reminder_id == reminder_id)
        record = record.model_copy(update={"state": "cancelled"})
        self._records = [
            record if item.reminder_id == reminder_id else item for item in self._records
        ]
        receipt = ReminderActionReceipt(
            request_id=request_id,
            operation="cancel",
            status="cancelled",
            record=record,
            observed_at=NOW,
        )
        self.receipts[request_id] = receipt
        return None if self.unknown_after_commit else receipt

    def reconcile_action(self, request_id: UUID) -> ReminderActionReceipt | None:
        self.reconciles.append(request_id)
        return self.receipts.get(request_id)


class ForbiddenReminders:
    def __getattribute__(self, name: str) -> object:
        raise AssertionError("An unauthorized request accessed a reminder/source owner")


@dataclass
class Harness:
    bridge: MobileBridge
    process: Process
    reminders: Reminders
    results: list[BridgeResult]
    scope: ScopeBinding
    mono: float = 10.0
    guard_calls: int = 0
    preflight_calls: int = 0
    revoked: bool = False

    def guard(self) -> None:
        self.guard_calls += 1
        if self.revoked:
            raise GatewayFault("device_revoked", 401)

    def preflight_guard(self) -> None:
        self.preflight_calls += 1
        if self.revoked:
            raise GatewayFault("device_revoked", 401)

    def call(
        self,
        operation: Operation = "chat",
        payload: dict[str, object] | None = None,
        *,
        request_id: str | None = None,
        capabilities: frozenset[str] = DEFAULT_CAPABILITIES,
        preflight: bool = True,
    ) -> BridgeCall:
        return BridgeCall(
            str(uuid4()),
            request_id
            or (str(uuid4()) if operation in {"chat", "new_session", "reconcile"} else None),
            operation,
            self.scope,
            "synthetic-device",
            capabilities,
            {"sessionID": "phone-session", "text": "Обычная беседа"}
            if payload is None
            else payload,
            self.mono + 30,
            self.guard,
            preflight_guard=self.preflight_guard if preflight else None,
        )

    def submit(self, call: BridgeCall) -> None:
        self.bridge.submit(call, self.results.append)

    def reply(
        self, data: dict[str, object], *, command: str | None = None, ident: str | None = None
    ) -> bool:
        value: dict[str, object] = {
            "protocol_version": 1,
            "id": ident or self.process.frames[-1]["id"],
            "event": "result",
            "data": data,
        }
        if command is not None:
            value["command"] = command
        return self.bridge.receive(value)

    def open_session(self) -> None:
        assert self.process.frames[-1]["command"] == "history"
        session = obj(self.process.frames[-1]["data"])["session_id"]
        assert self.reply(
            {
                "sessions": [{"session_id": session, "status": "open", "started_at": STAMP}],
                "interactions": [],
                "truncated": False,
            }
        )


@pytest.fixture(scope="module")
def app() -> QApplication:
    current = QApplication.instance() or QApplication([])
    assert isinstance(current, QApplication)
    return current


@pytest.fixture
def h(app: QApplication) -> Harness:
    del app
    process, reminders = Process(), Reminders()
    harness = Harness.__new__(Harness)
    harness.process = process
    harness.reminders = reminders
    harness.results = []
    harness.scope = ScopeBinding("synthetic-principal", "synthetic-profile", 1)
    harness.bridge = MobileBridge(
        cast(QProcess, process),
        lambda: harness.scope,
        cast(ReminderController, reminders),
        timezone_provider=lambda: "Europe/Moscow",
        clock=lambda: NOW,
        monotonic=lambda: harness.mono,
    )
    return harness


def committed(call: BridgeCall) -> dict[str, object]:
    return {
        "session_id": "phone-session",
        "client_request_id": call.request_id,
        "interaction_id": "committed-interaction",
        "text": "COMMITTED SYNTHETIC REPLY",
    }


def obj(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def array(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


class MemoryJournal:
    value: dict[str, object] | None = None

    def read_json(self, name: str) -> dict[str, object] | None:
        return self.value

    def write_json(self, name: str, value: dict[str, object]) -> None:
        self.value = obj(json.loads(json.dumps(value)))


def real_broker(h: Harness) -> tuple[Broker, DeviceContext]:
    context = DeviceContext(
        "synthetic-device", h.scope, DEFAULT_CAPABILITIES, NOW.timestamp() + 3600, "synthetic-tls"
    )

    def revalidate(value: DeviceContext) -> None:
        if h.revoked or value != context:
            raise GatewayFault("device_revoked", 401)

    return Broker(
        MemoryJournal(),
        h.bridge,
        revalidate,
        lambda: h.scope,
        wait_seconds=0,
        clock=lambda: NOW.timestamp(),
        monotonic=lambda: h.mono,
    ), context


def test_constructor_and_cross_thread_submit_do_not_write_until_qt(
    h: Harness, app: QApplication
) -> None:
    call = h.call()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(h.submit, call).result(timeout=2)
    assert not h.process.frames
    assert h.guard_calls == 0
    app.processEvents()
    assert h.process.write_threads == [threading.get_ident()]
    assert h.guard_calls == 1


def test_queue_guard_revocation_stops_write(h: Harness, app: QApplication) -> None:
    h.submit(h.call())
    h.revoked = True
    app.processEvents()
    assert not h.process.frames
    assert h.results[0].error_code == "device_revoked"


def test_epoch_is_captured_before_qt_delivery(h: Harness, app: QApplication) -> None:
    h.submit(h.call())
    h.bridge.invalidate_scope()
    app.processEvents()
    assert not h.process.frames
    assert len(h.results) == 1
    assert h.results[0].error_code == "profile_changed"


def test_guard_is_immediately_before_write_and_never_resumes_mac_selection(
    h: Harness, app: QApplication
) -> None:
    call = h.call()
    h.process.before_write = lambda: pytest.fail("guard missing") if h.guard_calls != 1 else None
    h.submit(call)
    app.processEvents()
    assert h.process.frames[0]["command"] == "send_for_session"
    assert h.process.frames[0]["data"] == {
        "session_id": "phone-session",
        "request_id": call.request_id,
        "text": "Обычная беседа",
    }
    assert h.process.mac_selection == "desktop-selected"
    assert h.reply(committed(call))
    assert h.results[0].data is not None
    assert h.results[0].data["kind"] == "conversation"
    assert h.results[0].data["interactionID"] == "committed-interaction"


def test_foreign_response_or_wrong_command_does_not_release_owned_lane(
    h: Harness, app: QApplication
) -> None:
    call = h.call()
    h.submit(call)
    app.processEvents()
    assert not h.reply(committed(call), ident="desktop-owned-id")
    assert not h.reply(committed(call), command="resume_session")
    assert not h.results
    with pytest.raises(GatewayFault, match="busy"):
        h.submit(h.call())
    assert h.reply(committed(call))
    assert h.reply(committed(call))
    assert len(h.results) == 1


def test_expired_dispatched_call_waits_for_terminal_response_without_replay(
    h: Harness, app: QApplication
) -> None:
    call = h.call()
    h.submit(call)
    app.processEvents()
    h.mono += 31
    with pytest.raises(GatewayFault, match="busy"):
        h.submit(h.call())
    assert not h.results
    assert h.reply(committed(call))
    assert h.results[0].error_code == "deadline_exceeded"
    assert h.results[0].data is None
    assert len(h.process.frames) == 1


def test_scope_change_discards_late_payload_but_consumes_known_id(
    h: Harness, app: QApplication
) -> None:
    call = h.call()
    h.submit(call)
    app.processEvents()
    h.scope = ScopeBinding("synthetic-principal", "other-profile", 2)
    h.bridge.invalidate_scope()
    assert h.reply(committed(call))
    assert h.results[0].data is None
    assert h.results[0].error_code == "profile_changed"


def test_shutdown_closes_queued_work_and_consumes_late_known_response(
    h: Harness, app: QApplication
) -> None:
    call = h.call()
    h.submit(call)
    app.processEvents()
    h.bridge.shutdown()
    assert h.reply(committed(call))
    assert len(h.results) == 1
    assert h.results[0].error_code == "core_unavailable"
    with pytest.raises(GatewayFault, match="core_unavailable"):
        h.submit(h.call())


def test_process_end_is_unknown_not_success(h: Harness, app: QApplication) -> None:
    h.submit(h.call())
    app.processEvents()
    h.process.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert h.results[0].error_code == "core_unavailable"
    assert h.results[0].data is None


def test_partial_write_closes_bridge_until_process_end(h: Harness, app: QApplication) -> None:
    h.process.partial = True
    h.submit(h.call())
    app.processEvents()
    assert h.results[0].error_code == "operation_failed"
    with pytest.raises(GatewayFault, match="core_unavailable"):
        h.submit(h.call())
    assert len(h.process.frames) == 1


def test_status_strips_private_worker_configuration(h: Harness, app: QApplication) -> None:
    h.submit(h.call("status", {}))
    app.processEvents()
    h.reply(
        {
            "activated": True,
            "session_id": "desktop-selected",
            "data_directory": "/SYNTHETIC/PRIVATE",
            "model": "private-model",
            "provider": "private-provider",
        }
    )
    result = h.results[0].data
    assert result is not None
    assert set(result) == {"ready", "coreState", "profileID", "sessionID", "sourceStatuses"}
    assert "PRIVATE" not in json.dumps(dict(result))
    assert all(obj(item)["connection"] == "unknown" for item in array(result["sourceStatuses"]))


def test_sessions_are_bounded_without_changing_active_mac_selection(
    h: Harness, app: QApplication
) -> None:
    h.submit(h.call("sessions", {}))
    app.processEvents()
    h.reply(
        {
            "sessions": [
                {"session_id": str(i), "started_at": STAMP, "status": "open"} for i in range(101)
            ],
            "truncated": False,
        }
    )
    data = h.results[0].data
    assert data is not None
    assert len(array(data["sessions"])) == 100
    assert data["truncated"] is True
    assert data["activeSessionID"] is None
    assert h.process.mac_selection == "desktop-selected"


def test_history_only_committed_assistant_text(h: Harness, app: QApplication) -> None:
    h.submit(h.call("history", {"sessionID": "phone-session"}))
    app.processEvents()
    h.reply(
        {
            "truncated": False,
            "interactions": [
                {
                    "interaction_id": "one",
                    "session_id": "phone-session",
                    "status": "completed",
                    "user_text": "User",
                    "assistant_text": "COMMITTED",
                    "started_at": STAMP,
                },
                {
                    "interaction_id": "two",
                    "session_id": "phone-session",
                    "status": "pending",
                    "user_text": "Pending user",
                    "assistant_text": "UNCOMMITTED CANDIDATE",
                    "started_at": STAMP,
                },
            ],
        }
    )
    data = h.results[0].data
    assert data is not None
    assert "COMMITTED" in json.dumps(dict(data))
    assert "UNCOMMITTED" not in json.dumps(dict(data))


def test_history_rejects_foreign_session(h: Harness, app: QApplication) -> None:
    h.submit(h.call("history", {"sessionID": "phone-session"}))
    app.processEvents()
    h.reply(
        {"truncated": False, "interactions": [{"session_id": "foreign", "user_text": "PRIVATE"}]}
    )
    assert h.results[0].data is None
    assert h.results[0].error_code == "operation_failed"


def test_reminder_create_is_same_owner_stable_request_and_typed_card(
    h: Harness, app: QApplication
) -> None:
    call = h.call(
        payload={"sessionID": "phone-session", "text": "Напомни через 10 минут SYNTHETIC TASK"}
    )
    h.submit(call)
    app.processEvents()
    assert not h.reminders.creates
    assert h.guard_calls == 0
    h.open_session()
    assert [frame["command"] for frame in h.process.frames] == ["history"]
    assert h.reminders.creates[0][3] == UUID(cast(str, call.request_id))
    data = h.results[0].data
    assert data is not None
    assert data["kind"] == "assistant"
    assert data["text"] == ""
    assert data["interactionID"] is None
    assert set(obj(data["card"])) == {
        "type",
        "title",
        "body",
        "source",
        "observedAt",
        "expiresAt",
        "status",
    }
    assert obj(data["card"])["type"] == "reminder"
    assert h.process.mac_selection == "desktop-selected"


@pytest.mark.parametrize("operation", ["create", "cancel"])
@pytest.mark.parametrize("session_state", ["open", "closed", "missing"])
def test_real_broker_requires_open_session_before_local_mutation(
    h: Harness, app: QApplication, operation: str, session_state: str
) -> None:
    if operation == "cancel":
        h.reminders.create("SYNTHETIC", NOW + timedelta(hours=1), "Europe/Moscow", uuid4())
        text = f"Отмени напоминание {h.reminders.records[0].reminder_id}"
    else:
        text = "Напомни через 10 минут SYNTHETIC"
    before = len(h.reminders.creates), len(h.reminders.cancels)
    broker, context = real_broker(h)
    request = uuid4()
    payload = {"sessionID": "phone-session", "text": text}
    try:
        assert broker.submit(context, "chat", payload, request)["state"] == "pending"
        app.processEvents()
        assert len(h.process.frames) == 1
        assert h.process.frames[0]["command"] == "history"
        assert h.process.frames[0]["data"] == {"session_id": "phone-session"}
        assert (len(h.reminders.creates), len(h.reminders.cancels)) == before
        sessions = (
            []
            if session_state == "missing"
            else [
                {"session_id": "phone-session", "status": session_state, "started_at": STAMP},
            ]
        )
        h.reply({"sessions": sessions, "interactions": [], "truncated": False})
        result = broker.reconcile(context, request)
        if session_state == "open":
            assert result["state"] == "completed"
            assert (len(h.reminders.creates), len(h.reminders.cancels)) == (
                before[0] + (operation == "create"),
                before[1] + (operation == "cancel"),
            )
        else:
            assert result["state"] == "failed"
            assert result["kind"] == "assistant"
            assert obj(result["card"])["type"] == "error"
            assert (len(h.reminders.creates), len(h.reminders.cancels)) == before
        assert broker.submit(context, "chat", payload, request) == result
        assert len(h.process.frames) == 1
    finally:
        broker.close()
        h.bridge.shutdown()


def test_real_broker_revocation_during_preflight_never_mutates_or_publishes(
    h: Harness, app: QApplication
) -> None:
    broker, context = real_broker(h)
    request = uuid4()
    try:
        broker.submit(
            context,
            "chat",
            {
                "sessionID": "phone-session",
                "text": "Напомни через 10 минут SYNTHETIC",
            },
            request,
        )
        app.processEvents()
        assert h.process.frames[0]["command"] == "history"
        h.revoked = True
        h.open_session()
        assert not h.reminders.creates
        assert not h.reminders.cancels
        with pytest.raises(GatewayFault, match="device_revoked"):
            broker.reconcile(context, request)
        assert len(h.process.frames) == 1
    finally:
        broker.close()
        h.bridge.shutdown()


@pytest.mark.parametrize("missing", ["guard", "history_capability"])
def test_local_mutation_without_preflight_authority_is_terminal_denial(
    h: Harness, app: QApplication, missing: str
) -> None:
    h.bridge._reminders = cast(ReminderController, ForbiddenReminders())
    capabilities = (
        DEFAULT_CAPABILITIES if missing == "guard" else frozenset({"status", "chat", "reminders"})
    )
    h.submit(
        h.call(
            payload={"sessionID": "phone-session", "text": "Напомни через 10 минут SYNTHETIC"},
            capabilities=capabilities,
            preflight=missing != "guard",
        )
    )
    app.processEvents()
    assert not h.process.frames
    assert h.results[0].data is not None
    assert h.results[0].data["state"] == "failed"
    assert obj(h.results[0].data["card"])["type"] == "error"
    assert h.guard_calls == 1


def test_unconfirmed_reminder_is_unknown_not_canonical_reply(h: Harness, app: QApplication) -> None:
    h.reminders.fail = True
    h.submit(
        h.call(
            payload={"sessionID": "phone-session", "text": "Напомни через 10 минут SYNTHETIC TASK"}
        )
    )
    app.processEvents()
    h.open_session()
    data = h.results[0].data
    assert data is not None
    assert data["kind"] == "assistant"
    assert obj(data["card"])["type"] == "error"
    assert data["state"] == "unknown"
    assert obj(data["card"])["status"] == "unknown"
    assert [frame["command"] for frame in h.process.frames] == ["history"]


def test_create_postcommit_unknown_is_reconciled_read_only(h: Harness, app: QApplication) -> None:
    h.reminders.unknown_after_commit = True
    call = h.call(
        payload={"sessionID": "phone-session", "text": "Напомни через 10 минут SYNTHETIC"}
    )
    h.submit(call)
    app.processEvents()
    h.open_session()
    assert len(h.reminders.creates) == 1
    assert len(h.reminders.records) == 1
    assert h.results[0].data is not None
    assert h.results[0].data["state"] == "unknown"
    request = cast(str, call.request_id)
    h.submit(
        h.call(
            "reconcile", {"requestID": request, "sessionID": "phone-session"}, request_id=request
        )
    )
    app.processEvents()
    h.reply(
        {
            "session_id": "phone-session",
            "client_request_id": request,
            "state": "unknown",
            "text": None,
            "interaction_id": None,
        }
    )
    assert h.results[-1].data is not None
    assert h.results[-1].data["state"] == "completed"
    assert h.results[-1].data["kind"] == "assistant"
    assert len(h.reminders.creates) == 1


@pytest.mark.parametrize("receipt_available", [True, False])
def test_cancel_postcommit_unknown_never_resubmits_cancel(
    h: Harness, app: QApplication, receipt_available: bool
) -> None:
    h.reminders.create("SYNTHETIC", NOW + timedelta(hours=1), "Europe/Moscow", uuid4())
    reminder = h.reminders.records[0]
    h.reminders.unknown_after_commit = True
    call = h.call(
        payload={"sessionID": "phone-session", "text": f"Отмени напоминание {reminder.reminder_id}"}
    )
    h.submit(call)
    app.processEvents()
    h.open_session()
    assert h.reminders.records[0].state == "cancelled"
    assert h.results[0].data is not None
    assert h.results[0].data["state"] == "unknown"
    if not receipt_available:
        h.reminders.receipts.clear()
    request = cast(str, call.request_id)
    h.submit(
        h.call(
            "reconcile", {"requestID": request, "sessionID": "phone-session"}, request_id=request
        )
    )
    app.processEvents()
    h.reply(
        {
            "session_id": "phone-session",
            "client_request_id": request,
            "state": "unknown",
            "text": None,
            "interaction_id": None,
        }
    )
    assert h.results[-1].data is not None
    assert h.results[-1].data["state"] == ("completed" if receipt_available else "unknown")
    assert len(h.reminders.cancels) == 1


@pytest.mark.parametrize("operation", ["create", "cancel"])
@pytest.mark.parametrize("code", ["session_closed", "session_missing"])
@pytest.mark.parametrize("receipt_available", [True, False])
def test_unknown_reminder_reconciliation_survives_lost_conversation(
    h: Harness, app: QApplication, operation: str, code: str, receipt_available: bool
) -> None:
    if operation == "cancel":
        h.reminders.create("SYNTHETIC", NOW + timedelta(hours=1), "Europe/Moscow", uuid4())
        text = f"Отмени напоминание {h.reminders.records[0].reminder_id}"
    else:
        text = "Напомни через 10 минут SYNTHETIC"
    h.reminders.unknown_after_commit = True
    original = h.call(payload={"sessionID": "phone-session", "text": text})
    h.submit(original)
    app.processEvents()
    h.open_session()
    assert h.results[0].data is not None
    assert h.results[0].data["state"] == "unknown"
    before = len(h.reminders.creates), len(h.reminders.cancels)
    if not receipt_available:
        h.reminders.receipts.clear()
    request = cast(str, original.request_id)
    h.submit(
        h.call(
            "reconcile",
            {
                "requestID": request,
                "sessionID": "phone-session",
            },
            request_id=request,
        )
    )
    app.processEvents()
    assert h.process.frames[-1]["command"] == "request_status"
    assert h.bridge.receive(
        {
            "id": h.process.frames[-1]["id"],
            "event": "error",
            "error": {"code": code},
        }
    )
    result = h.results[-1].data
    assert result is not None
    assert result["state"] == ("completed" if receipt_available else "unknown")
    assert result["text"] == ""
    if receipt_available:
        assert result["kind"] == "assistant"
        assert obj(result["card"])["type"] == "reminder"
    assert h.reminders.reconciles == [UUID(request)]
    assert (len(h.reminders.creates), len(h.reminders.cancels)) == before


def test_clarification_never_creates_reminder_or_core_message(
    h: Harness, app: QApplication
) -> None:
    h.bridge._reminders = cast(ReminderController, ForbiddenReminders())
    h.submit(
        h.call(
            payload={"sessionID": "phone-session", "text": "Напомни завтра вечером SYNTHETIC TASK"}
        )
    )
    app.processEvents()
    assert not h.process.frames
    assert h.results[0].data is not None
    assert obj(h.results[0].data["card"])["type"] == "clarification"


@pytest.mark.parametrize(
    "text", ["Что у меня завтра", "Найди в почте SYNTHETIC", "Найди в телеграме SYNTHETIC"]
)
def test_external_sources_require_grant_without_any_owner_or_worker_access(
    h: Harness, app: QApplication, text: str
) -> None:
    h.bridge._reminders = cast(ReminderController, ForbiddenReminders())
    h.submit(h.call(payload={"sessionID": "phone-session", "text": text}))
    app.processEvents()
    assert not h.process.frames
    data = h.results[0].data
    assert data is not None
    assert data["state"] == "completed"
    assert data["kind"] == "assistant"
    assert obj(data["card"])["type"] == "connection_required"
    assert obj(data["card"])["status"] == "permission_required"
    assert h.guard_calls == 1


def test_real_broker_source_denial_completes_instead_of_locking_phone_send(
    h: Harness, app: QApplication
) -> None:
    class Journal:
        value: dict[str, object] | None = None

        def read_json(self, name: str) -> dict[str, object] | None:
            return self.value

        def write_json(self, name: str, value: dict[str, object]) -> None:
            self.value = obj(json.loads(json.dumps(value)))

    journal = Journal()
    context = DeviceContext(
        "synthetic-device",
        h.scope,
        DEFAULT_CAPABILITIES,
        NOW.timestamp() + 3600,
        "synthetic-tls",
    )
    broker = Broker(
        journal,
        h.bridge,
        lambda _: None,
        lambda: h.scope,
        wait_seconds=0,
        clock=lambda: NOW.timestamp(),
        monotonic=lambda: h.mono,
    )
    h.bridge._reminders = cast(ReminderController, ForbiddenReminders())
    request = uuid4()
    try:
        first = broker.submit(
            context,
            "chat",
            {
                "sessionID": "phone-session",
                "text": "Что у меня завтра",
            },
            request,
        )
        assert first["state"] == "pending"
        app.processEvents()
        result = broker.reconcile(context, request)
        assert result["state"] == "completed"
        assert result["kind"] == "assistant"
        assert obj(result["card"])["type"] == "connection_required"
        assert obj(result["card"])["status"] == "permission_required"
        assert journal.value is not None
        assert obj(array(journal.value["records"])[0])["state"] == "completed"
        assert not h.process.frames
        following = broker.submit(
            context,
            "chat",
            {
                "sessionID": "phone-session",
                "text": "Обычная беседа",
            },
            uuid4(),
        )
        assert following["state"] == "pending"
        app.processEvents()
        assert len(h.process.frames) == 1
        assert h.process.frames[0]["command"] == "send_for_session"
    finally:
        broker.close()
        h.bridge.shutdown()


def test_ungranted_reminder_chat_is_terminal_card_before_owner_access(
    h: Harness, app: QApplication
) -> None:
    h.bridge._reminders = cast(ReminderController, ForbiddenReminders())
    h.submit(
        h.call(
            payload={"sessionID": "phone-session", "text": "Напомни через 10 минут SYNTHETIC"},
            capabilities=frozenset({"status", "chat"}),
        )
    )
    app.processEvents()
    assert not h.process.frames
    assert h.results[0].data is not None
    assert h.results[0].data["state"] == "failed"
    assert obj(h.results[0].data["card"])["type"] == "error"


@pytest.mark.parametrize("code", ["session_missing", "session_closed"])
def test_proved_precommit_session_error_is_terminal_failure(
    h: Harness, app: QApplication, code: str
) -> None:
    call = h.call()
    h.submit(call)
    app.processEvents()
    assert h.bridge.receive(
        {"id": h.process.frames[-1]["id"], "event": "error", "error": {"code": code}}
    )
    data = h.results[0].data
    assert data is not None
    assert data["state"] == "failed"
    assert data["text"] == ""
    assert data["interactionID"] is None


def test_reminder_capability_is_checked_before_owner_access(h: Harness, app: QApplication) -> None:
    h.bridge._reminders = cast(ReminderController, ForbiddenReminders())
    h.submit(h.call("reminders", {}, capabilities=frozenset({"status"})))
    app.processEvents()
    assert h.results[0].error_code == "source_grant_required"


def test_get_reminders_reads_existing_owner_only(h: Harness, app: QApplication) -> None:
    h.reminders.create("SYNTHETIC", NOW + timedelta(hours=1), "Europe/Moscow", uuid4())
    h.submit(h.call("reminders", {}))
    app.processEvents()
    assert h.reminders.refreshes == 1
    assert not h.process.frames
    assert h.results[0].data is not None
    assert obj(array(h.results[0].data["items"])[0])["notificationState"] == "not_requested"


def test_new_session_and_lost_session_reconcile_never_resume_or_resubmit(
    h: Harness, app: QApplication
) -> None:
    call = h.call("new_session", {})
    h.submit(call)
    app.processEvents()
    assert h.process.frames[0]["command"] == "new_mobile_session"
    h.reply({"session_id": "new-phone-session"})
    assert h.results[0].data == {
        "requestID": call.request_id,
        "state": "completed",
        "sessionID": "new-phone-session",
    }
    h.submit(
        h.call(
            "reconcile",
            {"requestID": call.request_id, "sessionID": None},
            request_id=call.request_id,
        )
    )
    app.processEvents()
    assert len(h.process.frames) == 1
    assert h.results[-1].data == {
        "requestID": call.request_id,
        "state": "unknown",
        "sessionID": None,
    }


def test_reconcile_reminder_creation_is_read_only(h: Harness, app: QApplication) -> None:
    ident = uuid4()
    h.reminders.create("SYNTHETIC", NOW + timedelta(hours=1), "Europe/Moscow", ident)
    call = h.call(
        "reconcile", {"requestID": str(ident), "sessionID": "phone-session"}, request_id=str(ident)
    )
    h.submit(call)
    app.processEvents()
    assert h.process.frames[0]["command"] == "request_status"
    h.reply(
        {
            "session_id": "phone-session",
            "client_request_id": str(ident),
            "state": "unknown",
            "text": None,
            "interaction_id": None,
        }
    )
    assert len(h.reminders.creates) == 1
    assert not h.reminders.cancels
    assert h.results[0].data is not None
    assert h.results[0].data["kind"] == "assistant"


def test_payload_is_snapshotted_and_oversized_text_never_writes(
    h: Harness, app: QApplication
) -> None:
    payload: dict[str, object] = {"sessionID": "phone-session", "text": "ORIGINAL"}
    h.submit(h.call(payload=payload))
    payload["text"] = "MUTATED"
    app.processEvents()
    assert obj(h.process.frames[0]["data"])["text"] == "ORIGINAL"
    h.bridge.shutdown()


def test_queued_deadline_and_unavailable_process_fail_without_write(
    h: Harness, app: QApplication
) -> None:
    h.submit(h.call())
    h.mono += 31
    app.processEvents()
    assert not h.process.frames
    assert h.results[0].error_code == "deadline_exceeded"
    h.process.running = False
    h.submit(h.call())
    app.processEvents()
    assert h.results[-1].error_code == "core_unavailable"


def test_no_private_text_is_printed(
    h: Harness, app: QApplication, capsys: pytest.CaptureFixture[str]
) -> None:
    call = h.call()
    h.submit(call)
    app.processEvents()
    h.reply(committed(call))
    captured = capsys.readouterr()
    assert "SYNTHETIC REPLY" not in captured.out + captured.err
