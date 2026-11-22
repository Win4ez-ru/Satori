"""Queued paired-client adapter into the existing worker and reminder owner only."""

# ruff: noqa: RUF001 - bounded Russian action-card presentation

import json
import math
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, QProcess, Qt, Signal, Slot

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.intents import parse_assistant_intent
from satori.application.assistant.reminder_contracts import ReminderRecord
from satori.desktop.mobile_gateway.contracts import (
    ERROR_CODES,
    BridgeCall,
    BridgeResult,
    GatewayFault,
    ScopeBinding,
)
from satori.desktop.mobile_gateway.wire import (
    ChatData,
    HistoryData,
    NewSessionData,
    RemindersData,
    SessionsData,
    StatusData,
)
from satori.desktop.reminder_controller import ReminderController


@dataclass
class _Job:
    call: BridgeCall = field(repr=False)
    complete: Callable[[BridgeResult], None] = field(repr=False)
    epoch: int
    payload: dict[str, object] = field(repr=False)
    wire_id: str = field(default_factory=lambda: "mobile:" + str(uuid4()))
    command: str | None = None
    dispatched: bool = False
    admitted: bool = False
    effect_started: bool = False
    staged_intent: AssistantIntent | None = field(default=None, repr=False)
    finished: bool = False


def _string(value: object, limit: int = 128) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > limit
        or value != value.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise GatewayFault("invalid_request")
    return value


def _date(value: object) -> str:
    text = _string(value, 64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except ValueError:
        raise GatewayFault("operation_failed", 502) from None


class MobileBridge(QObject):
    """No process/database construction, desktop selection, source adapter or visible card.

    One admitted call owns a slot including its Qt queue wait. Worker response IDs
    bind the exact stored command: the current private worker does not echo command.
    A timed-out dispatched call keeps its slot until an actual matching terminal
    response or process end; it is never replayed or replaced by a synthetic timeout.
    """

    _submitted = Signal(object, object)

    def __init__(
        self,
        process: QProcess,
        scope_provider: Callable[[], ScopeBinding],
        reminders: ReminderController,
        parent: QObject | None = None,
        *,
        timezone_provider: Callable[[], str],
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        self._process = process
        self._scope_provider = scope_provider
        self._reminders = reminders
        self._timezone_provider = timezone_provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._lock = RLock()
        self._epoch = 0
        self._closed = False
        self._write_failed = False
        self._active: _Job | None = None
        self._retired: OrderedDict[str, str | None] = OrderedDict()
        self._submitted.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)
        process.finished.connect(self._process_ended)

    def submit(self, call: BridgeCall, complete: Callable[[BridgeResult], None]) -> None:
        """Called from HTTP threads; only Qt's queued slot touches owners/process I/O."""
        if not isinstance(call, BridgeCall) or not callable(complete):
            raise GatewayFault("invalid_request")
        _string(call.call_id)
        _string(call.device_id)
        if not isinstance(call.scope, ScopeBinding) or type(call.scope.generation) is not int:
            raise GatewayFault("invalid_request")
        if not isinstance(call.capabilities, frozenset) or not callable(call.dispatch_guard):
            raise GatewayFault("invalid_request")
        if type(call.deadline_monotonic) not in {int, float} or not math.isfinite(
            call.deadline_monotonic
        ):
            raise GatewayFault("invalid_request")
        payload = dict(call.payload)
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise GatewayFault("invalid_request") from None
        if len(encoded) > 65_536:
            raise GatewayFault("too_large", 413)
        # Snapshot caller-owned containers before the cross-thread signal.
        payload = cast(dict[str, object], json.loads(encoded))
        with self._lock:
            if self._closed or self._write_failed:
                raise GatewayFault("core_unavailable", 503)
            if self._active is not None:
                raise GatewayFault("busy", 409)
            job = _Job(call, complete, self._epoch, payload)
            self._active = job
        try:
            self._submitted.emit(job, complete)
        except RuntimeError:
            self._finish(job, error="core_unavailable")

    def _fence(self, job: _Job) -> None:
        if self._closed:
            raise GatewayFault("core_unavailable", 503)
        if job.epoch != self._epoch or self._scope_provider() != job.call.scope:
            raise GatewayFault("profile_changed", 409)
        now = self._monotonic()
        if not math.isfinite(now) or now >= job.call.deadline_monotonic:
            raise GatewayFault("deadline_exceeded", 504)

    def _guard(self, job: _Job, capability: str) -> None:
        self._fence(job)
        if capability not in job.call.capabilities:
            raise GatewayFault("source_grant_required", 403)
        job.call.dispatch_guard()
        job.admitted = True
        self._fence(job)

    def _preflight_guard(self, job: _Job) -> None:
        self._fence(job)
        guard = job.call.preflight_guard
        if "history" not in job.call.capabilities or guard is None or not callable(guard):
            raise GatewayFault("source_grant_required", 403)
        guard()
        self._fence(job)

    @Slot(object, object)
    def _dispatch(self, value: object, complete: object) -> None:
        if not isinstance(value, _Job) or complete is not value.complete:
            return
        job = value
        with self._lock:
            if job.finished or self._active is not job:
                return
            try:
                self._fence(job)
                operation = job.call.operation
                if operation == "chat":
                    request_id = self._request_id(job)
                    if set(job.payload) != {"sessionID", "text"}:
                        raise GatewayFault("invalid_request")
                    _string(job.payload.get("sessionID"))
                    text = job.payload.get("text")
                    if (
                        type(text) is not str
                        or not text.strip()
                        or len(text) > 8000
                        or "\x00" in text
                    ):
                        raise GatewayFault("invalid_request")
                    intent = parse_assistant_intent(
                        text,
                        now=self._now(),
                        timezone=self._timezone_provider(),
                        request_id=UUID(request_id),
                    )
                    if intent is not None:
                        self._assistant(job, intent)
                        return
                    self._write(
                        job,
                        "send_for_session",
                        {
                            "text": text,
                            "request_id": request_id,
                            "session_id": job.payload["sessionID"],
                        },
                        "chat",
                    )
                elif operation == "new_session":
                    if job.payload:
                        raise GatewayFault("invalid_request")
                    self._write(
                        job, "new_mobile_session", {"request_id": self._request_id(job)}, "chat"
                    )
                elif operation == "reconcile":
                    request_id = self._request_id(job)
                    if (
                        set(job.payload) != {"requestID", "sessionID"}
                        or job.payload["requestID"] != request_id
                    ):
                        raise GatewayFault("invalid_request")
                    session = job.payload.get("sessionID")
                    if session is None:
                        self._guard(job, "chat")
                        self._finish(
                            job,
                            data={"requestID": request_id, "state": "unknown", "sessionID": None},
                        )
                    else:
                        self._write(
                            job,
                            "request_status",
                            {
                                "request_id": request_id,
                                "session_id": _string(session),
                            },
                            "chat",
                        )
                elif operation in {"status", "sessions", "history"}:
                    if job.call.request_id is not None:
                        raise GatewayFault("invalid_request")
                    if operation == "history":
                        if set(job.payload) != {"sessionID"}:
                            raise GatewayFault("invalid_request")
                        data = {"session_id": _string(job.payload.get("sessionID"))}
                    else:
                        if job.payload:
                            raise GatewayFault("invalid_request")
                        data = {}
                    self._write(
                        job,
                        "status" if operation == "status" else "history",
                        data,
                        "status" if operation == "status" else "history",
                    )
                elif operation == "reminders":
                    if job.payload or job.call.request_id is not None:
                        raise GatewayFault("invalid_request")
                    self._guard(job, "reminders")
                    self._reminders.refresh()
                    if self._reminders.status == "error" or self._reminders.diagnostic is not None:
                        raise GatewayFault("operation_failed", 503)
                    records = self._reminders.records
                    items = [self._reminder(record) for record in records[:100]]
                    result = RemindersData.model_validate(
                        {
                            "items": items,
                            "truncated": len(records) > 100,
                        }
                    ).model_dump()
                    self._finish(job, data=result)
                else:
                    raise GatewayFault("not_supported")
            except GatewayFault as fault:
                if (
                    job.call.operation in {"chat", "new_session"}
                    and not job.dispatched
                    and not job.effect_started
                    and fault.code in {"invalid_request", "too_large", "not_supported"}
                ):
                    try:
                        if not job.admitted:
                            self._guard(job, "chat")
                        self._finish(job, data=self._terminal_failure(job))
                    except GatewayFault as rejected:
                        self._finish(job, error=rejected.code)
                else:
                    self._finish(job, error=fault.code)
            except Exception:
                self._finish(job, error="operation_failed")

    def _request_id(self, job: _Job) -> str:
        value = job.call.request_id
        if type(value) is not str:
            raise GatewayFault("invalid_request")
        try:
            if str(UUID(value)) != value:
                raise ValueError
        except ValueError:
            raise GatewayFault("invalid_request") from None
        return value

    def _write(
        self,
        job: _Job,
        command: str,
        data: Mapping[str, object],
        capability: str,
        *,
        preflight: bool = False,
    ) -> None:
        if self._process.state() != QProcess.ProcessState.Running:
            raise GatewayFault("core_unavailable", 503)
        frame = (
            json.dumps(
                {"protocol_version": 1, "id": job.wire_id, "command": command, "data": data},
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        encoded = frame.encode("utf-8")
        if len(frame) > 64_000 or len(encoded) > 65_536:
            raise GatewayFault("too_large", 413)
        job.command = command
        # This is the last admission step after the Qt queue wait and before the native write.
        if preflight:
            self._preflight_guard(job)
        else:
            self._guard(job, capability)
        job.dispatched = True
        try:
            accepted = self._process.write(encoded)
        except Exception:
            self._write_failed = True
            raise GatewayFault("operation_failed", 503) from None
        if accepted != len(encoded):
            # An accepted prefix is not proof that the effect failed; never resend it.
            self._write_failed = True
            raise GatewayFault("operation_failed", 503)

    def receive(self, message: Mapping[str, object]) -> bool:
        """The root output router calls this before its ordinary desktop receive handler."""
        ident = message.get("id")
        if type(ident) is not str:
            return False
        with self._lock:
            job = self._active
            if ident in self._retired:
                return message.get("command", self._retired[ident]) == self._retired[ident]
            if job is None or not job.dispatched or ident != job.wire_id:
                return False
            if message.get("command", job.command) != job.command:
                return False
            try:
                self._fence(job)
                if (
                    message.get("protocol_version", 1) != 1
                    or type(message.get("protocol_version", 1)) is not int
                ):
                    raise GatewayFault("operation_failed", 502)
                if job.staged_intent is not None:
                    self._accept_preflight(job, message)
                    return True
                if message.get("event") == "error":
                    error = message.get("error")
                    code = error.get("code") if isinstance(error, dict) else None
                    if (
                        code in {"session_missing", "session_closed"}
                        and job.call.operation == "chat"
                    ):
                        self._finish(job, data=self._terminal_failure(job))
                        return True
                    if (
                        code in {"session_missing", "session_closed"}
                        and job.call.operation == "reconcile"
                    ):
                        self._finish(job, data=self._reconcile_unknown(job))
                        return True
                    raise GatewayFault(
                        code
                        if isinstance(code, str) and code in ERROR_CODES
                        else "operation_failed",
                        503,
                    )
                if message.get("event") != "result" or not isinstance(message.get("data"), dict):
                    raise GatewayFault("operation_failed", 502)
                data = cast(dict[str, object], message["data"])
                self._finish(job, data=self._project(job, data))
            except GatewayFault as fault:
                self._finish(job, error=fault.code)
            except Exception:
                self._finish(job, error="operation_failed")
            return True

    def _project(self, job: _Job, data: dict[str, object]) -> dict[str, object]:
        operation = job.call.operation
        if operation == "status":
            activated = data.get("activated")
            if type(activated) is not bool:
                raise GatewayFault("operation_failed", 502)
            session = data.get("session_id")
            return StatusData.model_validate(
                {
                    "ready": activated,
                    "coreState": "ready" if activated else "stopped",
                    "profileID": job.call.scope.profile_id,
                    "sessionID": None if session is None else _string(session),
                    "sourceStatuses": [
                        {"id": source, "connection": "unknown", "capabilities": []}
                        for source in ("calendar", "gmail", "telegram")
                    ],
                }
            ).model_dump()
        if operation in {"sessions", "history"}:
            truncated = data.get("truncated")
            if type(truncated) is not bool:
                raise GatewayFault("operation_failed", 502)
            if operation == "sessions":
                sessions = data.get("sessions")
                if not isinstance(sessions, list) or len(sessions) > 1000:
                    raise GatewayFault("operation_failed", 502)
                rows = []
                for item in sessions[-100:]:
                    if not isinstance(item, dict):
                        raise GatewayFault("operation_failed", 502)
                    rows.append(
                        {
                            "id": _string(item.get("session_id")),
                            "startedAt": _date(item.get("started_at")),
                            "status": item.get("status"),
                        }
                    )
                return SessionsData.model_validate(
                    {
                        "activeSessionID": None,
                        "sessions": rows,
                        "truncated": truncated or len(sessions) > 100,
                    }
                ).model_dump()
            interactions = data.get("interactions")
            if not isinstance(interactions, list) or len(interactions) > 200:
                raise GatewayFault("operation_failed", 502)
            messages: list[dict[str, object]] = []
            for item in interactions:
                if not isinstance(item, dict) or item.get("session_id") != job.payload["sessionID"]:
                    raise GatewayFault("operation_failed", 502)
                ident = _string(item.get("interaction_id"))
                status = item.get("status")
                if (
                    status not in {"completed", "pending", "failed"}
                    or type(item.get("user_text")) is not str
                ):
                    raise GatewayFault("operation_failed", 502)
                # The existing projection provides the interaction's owner timestamp for both roles.
                created = _date(item.get("started_at"))
                messages.append(
                    {
                        "id": ident + ":user",
                        "role": "user",
                        "text": item["user_text"],
                        "createdAt": created,
                        "status": status,
                    }
                )
                if status == "completed":
                    if type(item.get("assistant_text")) is not str:
                        raise GatewayFault("operation_failed", 502)
                    messages.append(
                        {
                            "id": ident + ":assistant",
                            "role": "assistant",
                            "text": item["assistant_text"],
                            "createdAt": created,
                            "status": "completed",
                        }
                    )
            return HistoryData.model_validate(
                {
                    "sessionID": job.payload["sessionID"],
                    "messages": messages[-64:],
                    "truncated": truncated or len(messages) > 64,
                }
            ).model_dump()
        if operation == "new_session":
            return NewSessionData(
                requestID=self._request_id(job),
                state="completed",
                sessionID=_string(data.get("session_id")),
            ).model_dump()
        session = _string(job.payload.get("sessionID"))
        if (
            data.get("session_id") != session
            or data.get("client_request_id") != job.call.request_id
        ):
            raise GatewayFault("operation_failed", 502)
        state = data.get("state", "completed") if operation == "chat" else data.get("state")
        if state not in {"completed", "pending", "failed", "unknown"}:
            raise GatewayFault("operation_failed", 502)
        text = data.get("text") if state == "completed" else ""
        interaction = data.get("interaction_id") if state == "completed" else None
        if state == "completed":
            _string(interaction)
            if type(text) is not str:
                raise GatewayFault("operation_failed", 502)
        elif operation == "reconcile" and state == "unknown":
            return self._reconcile_unknown(job)
        return ChatData.model_validate(
            {
                "requestID": self._request_id(job),
                "state": state,
                "kind": "conversation",
                "sessionID": session,
                "text": text,
                "interactionID": interaction,
                "card": None,
            }
        ).model_dump()

    def _reconcile_unknown(self, job: _Job) -> dict[str, object]:
        """Session loss says nothing about an already committed independent reminder action."""
        if "reminders" in job.call.capabilities:
            receipt = self._reminders.reconcile_action(UUID(self._request_id(job)))
            if receipt is not None and str(receipt.request_id) == job.call.request_id:
                return self._card(
                    job,
                    "reminder",
                    "Отмена подтверждена"
                    if receipt.operation == "cancel"
                    else "Напоминание найдено",
                    self._record_body(receipt.record),
                    "completed",
                    receipt.observed_at,
                )
        return ChatData.model_validate(
            {
                "requestID": self._request_id(job),
                "state": "unknown",
                "kind": "conversation",
                "sessionID": _string(job.payload.get("sessionID")),
                "text": "",
                "interactionID": None,
                "card": None,
            }
        ).model_dump()

    def _assistant(
        self, job: _Job, intent: AssistantIntent, *, session_proved: bool = False
    ) -> None:
        if intent.kind in {"reminder_create", "reminder_cancel"} and not session_proved:
            self._begin_preflight(job, intent)
            return
        self._guard(job, "chat")
        if intent.kind.startswith("calendar_") or intent.kind in {
            "selected_lookup",
            "mailbox_search",
            "source_answer",
            "telegram_reply",
        }:
            # No source owner is injected or accessed, even for a forged extra capability.
            self._finish(
                job,
                data=self._card(
                    job,
                    "connection_required",
                    "Нужен доступ к источнику",
                    "Этот источник не разрешён телефону. "
                    "Подключение и выбор данных выполняются отдельно на Mac.",
                    "permission_required",
                    self._now(),
                ),
            )
            return
        if intent.kind.startswith("reminder_") and "reminders" not in job.call.capabilities:
            self._finish(
                job,
                data=self._card(
                    job,
                    "connection_required",
                    "Нужен доступ к напоминаниям",
                    "Разреши напоминания этому телефону при привязке. Запись не выполнялась.",
                    "permission_required",
                    self._now(),
                ),
            )
            return
        if intent.kind == "clarification":
            result = self._card(
                job,
                "clarification",
                "Уточни задачу",
                intent.clarification or "Уточни запрос.",
                "pending",
                self._now(),
            )
        elif intent.kind == "reminder_create":
            assert intent.title is not None
            assert intent.due_at is not None
            job.effect_started = True
            receipt = self._reminders.create(
                intent.title, intent.due_at, intent.timezone, UUID(self._request_id(job))
            )
            result = (
                self._card(
                    job,
                    "error",
                    "Сохранение не подтверждено",
                    "Напоминание не подтверждено владельцем. Проверь его состояние отдельно.",
                    "unknown",
                    self._now(),
                    state="unknown",
                )
                if receipt is None
                else self._card(
                    job,
                    "reminder",
                    "Напоминание сохранено",
                    self._record_body(receipt.record),
                    "completed",
                    receipt.observed_at,
                )
            )
        elif intent.kind == "reminder_cancel":
            assert intent.reminder_id is not None
            job.effect_started = True
            receipt = self._reminders.cancel(intent.reminder_id, UUID(self._request_id(job)))
            result = (
                self._card(
                    job,
                    "error",
                    "Отмена не подтверждена",
                    "Проверь состояние напоминания; повтор автоматически не выполняется.",
                    "unknown",
                    self._now(),
                    state="unknown",
                )
                if receipt is None
                else self._card(
                    job,
                    "reminder",
                    "Напоминание отменено",
                    "Локальная отмена сохранена. Состояние OS-доставки проверяется отдельно.",
                    "completed",
                    receipt.observed_at,
                )
            )
        else:
            self._reminders.refresh()
            if self._reminders.status == "error" or self._reminders.diagnostic is not None:
                result = self._card(
                    job,
                    "error",
                    "Список недоступен",
                    "Не удалось проверить локальные напоминания.",
                    "failed",
                    self._now(),
                )
            else:
                active = [
                    record for record in self._reminders.records if record.state != "cancelled"
                ]
                body = "\n\n".join(self._record_body(record) for record in active[:20])
                if len(active) > 20:
                    body += "\n\nПоказаны первые 20 напоминаний."
                result = self._card(
                    job,
                    "reminder",
                    "Локальные напоминания",
                    body or "Активных напоминаний нет.",
                    "completed" if active else "empty",
                    self._now(),
                )
        self._finish(job, data=result)

    def _begin_preflight(self, job: _Job, intent: AssistantIntent) -> None:
        if (
            not {"chat", "history", "reminders"} <= job.call.capabilities
            or job.call.preflight_guard is None
        ):
            self._refuse_preflight(
                job,
                "Необходимы доступ к истории и напоминаниям "
                "и проверка открытой сессии. Запись не выполнялась.",
            )
            return
        job.staged_intent = intent
        self._write(
            job,
            "history",
            {"session_id": _string(job.payload.get("sessionID"))},
            "history",
            preflight=True,
        )

    def _accept_preflight(self, job: _Job, message: Mapping[str, object]) -> None:
        intent = job.staged_intent
        assert intent is not None
        self._preflight_guard(job)
        data = message.get("data")
        session = _string(job.payload.get("sessionID"))
        proved = False
        if message.get("event") == "result" and isinstance(data, dict):
            rows = data.get("sessions")
            interactions = data.get("interactions")
            if (
                isinstance(rows, list)
                and len(rows) <= 100
                and isinstance(interactions, list)
                and len(interactions) <= 200
                and ("session_id" not in data or data["session_id"] == session)
                and all(
                    isinstance(item, dict) and item.get("session_id") == session
                    for item in interactions
                )
            ):
                selected = [
                    item
                    for item in rows
                    if isinstance(item, dict) and item.get("session_id") == session
                ]
                proved = len(selected) == 1 and selected[0].get("status") == "open"
        # History bodies never enter an action card, canonical input or a new owner.
        job.staged_intent = None
        if not proved:
            self._refuse_preflight(
                job,
                "Не подтверждена открытая выбранная сессия. "
                "Выбери открытый разговор и повтори задачу. Запись не выполнялась.",
            )
            return
        self._assistant(job, intent, session_proved=True)

    def _refuse_preflight(self, job: _Job, body: str) -> None:
        self._guard(job, "chat")
        self._finish(
            job,
            data=self._card(
                job,
                "error",
                "Сессия не подтверждена",
                body,
                "failed",
                self._now(),
                state="failed",
            ),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise GatewayFault("operation_failed", 503)
        return value.astimezone(UTC)

    def _terminal_failure(self, job: _Job) -> dict[str, object]:
        if job.call.operation == "new_session":
            return NewSessionData(
                requestID=self._request_id(job), state="failed", sessionID=None
            ).model_dump()
        return ChatData.model_validate(
            {
                "requestID": self._request_id(job),
                "state": "failed",
                "kind": "conversation",
                "sessionID": _string(job.payload.get("sessionID"), 200),
                "text": "",
                "interactionID": None,
                "card": None,
            }
        ).model_dump()

    @staticmethod
    def _record_body(record: ReminderRecord) -> str:
        due = record.due_at.astimezone(ZoneInfo(record.timezone))
        return (
            f"{record.title}\n{due:%d.%m.%Y %H:%M} · {record.timezone}\n"
            f"OS-доставка: {record.delivery_state}. "
            "Сохранение и доставка — отдельные состояния."
        )

    def _card(
        self,
        job: _Job,
        kind: str,
        title: str,
        body: str,
        status: str,
        observed: datetime,
        *,
        state: str = "completed",
    ) -> dict[str, object]:
        return ChatData.model_validate(
            {
                "requestID": self._request_id(job),
                "state": state,
                "kind": "assistant",
                "sessionID": job.payload["sessionID"],
                "text": "",
                "interactionID": None,
                "card": {
                    "type": kind,
                    "title": title,
                    "body": body,
                    "source": "local_reminder_store" if kind == "reminder" else "user_request",
                    "observedAt": observed.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    "expiresAt": None,
                    "status": status,
                },
            }
        ).model_dump()

    @staticmethod
    def _reminder(record: ReminderRecord) -> dict[str, object]:
        delivery = {
            "not_requested": "not_requested",
            "scheduled": "scheduled",
            "delivered": "delivered",
            "unavailable": "failed",
            "absent": "failed",
            "cancelled": "not_requested",
        }.get(record.delivery_state, "unknown")
        return {
            "id": str(record.reminder_id),
            "title": record.title,
            "dueAt": record.due_at.isoformat().replace("+00:00", "Z"),
            "timezone": record.timezone,
            "state": record.state,
            "notificationState": delivery,
        }

    def _finish(
        self, job: _Job, *, data: dict[str, object] | None = None, error: str | None = None
    ) -> None:
        with self._lock:
            if job.finished:
                return
            try:
                self._fence(job)
            except GatewayFault as fault:
                data, error = None, error or fault.code
            job.finished = True
            if self._active is job:
                self._active = None
            if job.dispatched:
                self._retired[job.wire_id] = job.command
                while len(self._retired) > 128:
                    self._retired.popitem(last=False)
        result = BridgeResult(
            job.call.call_id,
            job.call.request_id,
            job.call.scope,
            job.call.device_id,
            data=data,
            error_code=error,
        )
        # A failed callback cannot reopen a call, retry an effect or log private data.
        with suppress(Exception):
            job.complete(result)

    def invalidate_scope(self) -> None:
        """Synchronous epoch fence, including calls not yet delivered by Qt."""
        with self._lock:
            self._epoch += 1
            job = self._active
            if job is not None and not job.dispatched:
                self._finish(job, error="profile_changed")

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._epoch += 1
            if self._active is not None:
                self._finish(self._active, error="core_unavailable")

    @Slot()
    def _process_ended(self) -> None:
        with self._lock:
            self._write_failed = False
            self._epoch += 1
            if self._active is not None:
                self._finish(self._active, error="core_unavailable")
