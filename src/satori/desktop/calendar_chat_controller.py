"""GUI-owned transient Calendar conversation workflow over the existing Core worker."""

# ruff: noqa: RUF001 - intentional Russian user-facing text.

import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionRecord,
    CalendarActionScope,
    CalendarCreateProposal,
)
from satori.application.assistant.calendar_chat_fields import (
    CalendarChatFields,
    anchor_calendar_fields,
    calendar_chat_fields,
    calendar_directive,
    calendar_fields_clarification,
    is_direct_calendar_create_request,
    normalize_calendar_title,
)
from satori.application.assistant.calendar_chat_followup import (
    CalendarPendingClarification,
    calendar_chat_followup,
    calendar_followup_is_date_correction,
    calendar_followup_reference,
)
from satori.application.assistant.calendar_chat_intake import (
    UNSUPPORTED_EXISTING_EVENT,
    calendar_chat_intake,
    is_calendar_chat_request,
    is_existing_calendar_mutation,
)
from satori.application.assistant.calendar_host_lease import (
    MAX_HOST_LEASE_LIFETIME,
    CalendarHostLease,
)
from satori.application.assistant.calendar_planning_contracts import (
    CalendarObservedEvent,
    CalendarPlanningRequest,
    CalendarPlanningSnapshot,
    context_digest,
)
from satori.application.assistant.calendar_text import (
    CalendarTextClarification,
)
from satori.application.assistant.contracts import (
    CalendarDescriptor,
    CalendarSnapshot,
    CalendarSourceRef,
)
from satori.application.assistant.dialogue_contracts import _unambiguous_midnight
from satori.desktop.calendar_controller import CalendarController
from satori.desktop.calendar_conversation import (
    CALENDAR_TASK_SECONDS,
    CalendarConversationAuthority,
    CalendarConversationTask,
)
from satori.infrastructure.assistant.calendar_lease import CalendarHostLeaseOwner
from satori.server.calendar_backend import CalendarBackendCommand


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CalendarChatCard:
    status: str
    message: str
    draft: str = ""
    proposal: CalendarCreateProposal | None = None
    provider: str = ""


@dataclass
class _HostRead:
    call_id: str
    request: CalendarPlanningRequest
    native_id: str | None = None


class CalendarChatController(QObject):
    """One revocable RAM card. It never performs canonical Send or grants access."""

    changed = Signal()
    command_requested = Signal(dict)
    host_read_result = Signal(dict)
    revocation_failed = Signal()

    def __init__(
        self,
        calendar: CalendarController,
        data_directory: Path,
        parent: QObject | None = None,
        *,
        clock: Callable[[], datetime] = _now,
        monotonic: Callable[[], float] = time.monotonic,
        lease_owner: CalendarHostLeaseOwner | None = None,
        conversational: bool = False,
        foreground: Callable[[], bool] | None = None,
        timezone: str = "Europe/Moscow",
    ) -> None:
        super().__init__(parent)
        self.calendar = calendar
        self._clock, self._monotonic = clock, monotonic
        self._owner = lease_owner or CalendarHostLeaseOwner(
            data_directory, clock=clock, monotonic=monotonic
        )
        self._context: tuple[str, str, str] | None = None
        self._card: CalendarChatCard | None = None
        self._fields = CalendarChatFields()
        self._fields_edited = False
        self._manual_target: CalendarSourceRef | None = None
        self._lease: CalendarHostLease | None = None
        self._request: CalendarPlanningRequest | None = None
        self._pending: str | None = None
        self._host_read: _HostRead | None = None
        self._clearing = False
        self._closed = False
        self.conversational = conversational
        self._foreground = foreground or (lambda: False)
        self._foreground_enabled = True
        self.timezone = timezone
        self._task: CalendarConversationTask | None = None
        self._clarification: CalendarPendingClarification | None = None
        self._conversation_authority: CalendarConversationAuthority | None = None
        self._catalog_waiting = False
        self._catalog_checked_revision: UUID | None = None
        self._advancing = False
        self._continuation_epoch = 0
        self._resume_required = False
        self._topic_reference_required = False
        self._effect_dispatched = False
        self._auto_approval_sent = False
        self._task_grant_id: UUID | None = None
        calendar.changed.connect(self._calendar_changed)
        calendar.planning_read_ready.connect(self._read_ready)
        calendar.planning_read_failed.connect(self._read_failed)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.tick)
        self._timer.start()

    @property
    def card(self) -> CalendarChatCard | None:
        return self._card

    @property
    def busy(self) -> bool:
        return self._pending is not None or self._catalog_waiting

    @property
    def conversation_authority(self) -> CalendarConversationAuthority | None:
        return self._conversation_authority

    @property
    def pending_clarification(self) -> CalendarPendingClarification | None:
        return self._clarification

    def _foreground_current(self) -> bool:
        return self._foreground_enabled and self._foreground()

    def _task_current(self) -> bool:
        task, now = self._task, self._monotonic()
        return bool(
            task is not None
            and math.isfinite(now)
            and task.issued_monotonic <= now < task.expires_monotonic
            and self._context == task.context
            and task.profile_id == self.calendar.principal.profile_id
        )

    def set_foreground(self, active: bool) -> None:
        """Activation alone never restarts a suspended user task."""
        if self.conversational and not active:
            self.suspend()
        self._foreground_enabled = active

    def _queue_conversation(self, callback: Callable[[], None]) -> None:
        task = self._task
        if task is None:
            return
        epoch = self._continuation_epoch

        def continue_current_task() -> None:
            current = self._task
            if (
                self._closed
                or self._resume_required
                or self._continuation_epoch != epoch
                or current is None
                or current.task_id != task.task_id
                or current.revision != task.revision
                or current.context != task.context
                or current.profile_id != task.profile_id
            ):
                return
            if not self._task_current():
                self.clear()
                return
            if not self._foreground_current():
                self.suspend()
                return
            callback()

        QTimer.singleShot(0, continue_current_task)

    def suspend(self) -> None:
        if not self.conversational:
            self.clear()
            return
        if self._card is None or self._task is None:
            self._continuation_epoch += 1
            self._resume_required = True
            return
        text, fields, task = self._card.draft, self._fields, self._task
        topic_reference_required = self._topic_reference_required
        previous_status, previous_message = self._card.status, self._card.message
        dispatched = self._effect_dispatched
        self.clear()
        self._fields, self._task, self._effect_dispatched = fields, task, dispatched
        self._fields_edited = True
        self._resume_required = True
        self._topic_reference_required = topic_reference_required
        terminal = previous_status in {
            "created",
            "reconciled_present",
            "reconciled_absent",
            "rejected",
            "conflict",
        }
        self._card = CalendarChatCard(
            previous_status if terminal else "unknown" if dispatched else "paused",
            previous_message
            if terminal
            else "Не могу подтвердить результат. Повторно создавать событие не буду."
            if dispatched
            else "Поручение сохранено. Напиши ответ или «продолжи», когда вернёшься.",
            text,
        )
        self.changed.emit()

    def pause_for_chat(self) -> None:
        """Keep an authored draft without treating a later bare answer as permission."""
        if (
            self.conversational
            and self._task is not None
            and self._card is not None
            and not self._effect_dispatched
        ):
            self.suspend()
            self._topic_reference_required = True

    def _ask(self, waiting_for: str, message: str) -> None:
        task = self._task
        if task is None or self._card is None:
            return
        self._clarification = CalendarPendingClarification(
            task_id=task.task_id,
            session_id=task.context[1],
            fields=self._fields,
            waiting_for="end" if waiting_for == "duration" else waiting_for,
            expires_at_monotonic=task.expires_monotonic,
            timezone=self.timezone,
            anchor_date=task.anchor_date,
        )
        self._card = CalendarChatCard("clarification", message, self._card.draft)
        self.changed.emit()

    def answer(self, text: str) -> bool:
        """Only a real foreground input can complete or resume this exact RAM task."""
        task, card = self._task, self._card
        if (
            not self.conversational
            or task is None
            or card is None
            or not self._foreground_current()
        ):
            return False
        answer, references_task = calendar_followup_reference(text)
        if self._effect_dispatched:
            if (
                text.strip().casefold() in {"продолжи", "продолжить", "повтори", "да"}
                or references_task
                or calendar_followup_is_date_correction(text)
            ):
                if card.status not in {
                    "created",
                    "reconciled_present",
                    "reconciled_absent",
                    "rejected",
                    "conflict",
                }:
                    self._card = CalendarChatCard(
                        "unknown",
                        "Повторно создавать событие не буду: "
                        "сначала нужно проверить прежний результат.",
                        card.draft,
                    )
                self.changed.emit()
                return True
            return False
        if not self._task_current():
            self.clear()
            return False
        if self._topic_reference_required and not references_task:
            if calendar_followup_is_date_correction(text):
                self._card = CalendarChatCard(
                    "clarification",
                    "Ты про прежнее событие? Напиши «к этому событию:» и уточнение "
                    "или «продолжи», чтобы вернуться к нему.",
                    card.draft,
                )
                self.changed.emit()
                return True
            return False
        if text.strip().casefold() in {"продолжи", "продолжить"}:
            self.suspend()
            self._continuation_epoch += 1
            self._resume_required = False
            self._topic_reference_required = False
            self._task = replace(task, revision=uuid4())
            self._clarification = None
            self._advance_conversation()
            return True
        if self._clarification is None:
            issue = calendar_fields_clarification(self._fields)
            waiting = (
                issue.missing[0]
                if issue
                else "calendar"
                if self.requested_calendar_title is None
                else None
            )
            if waiting is None:
                if not references_task and not calendar_followup_is_date_correction(text):
                    return False
                waiting = "detail"
            self._clarification = CalendarPendingClarification(
                task_id=task.task_id,
                session_id=task.context[1],
                fields=self._fields,
                waiting_for="end" if waiting == "duration" else waiting,
                expires_at_monotonic=task.expires_monotonic,
                timezone=self.timezone,
                anchor_date=task.anchor_date,
            )
        result = calendar_chat_followup(
            answer,
            pending=self._clarification,
            task_id=task.task_id,
            session_id=task.context[1],
            monotonic_now=self._monotonic(),
            related=references_task,
        )
        if result is None:
            if references_task or calendar_followup_is_date_correction(text):
                self.suspend()
                self._fields = self._fields.model_copy(
                    update={"unsupported_semantics": True, "needs_clarification": True}
                )
                self._ask(
                    "detail",
                    "Не получилось однозначно понять изменение. "
                    "Напиши новую просьбу об одном событии целиком.",
                )
                return True
            return False
        if result.status in {"cancelled", "expired"}:
            self.clear()
            return True
        if result.status == "clarification":
            pending = self._clarification
            if result.fields != self._fields:
                self.suspend()
                self._task = replace(task, revision=uuid4())
            self._fields = result.fields
            self._card = CalendarChatCard("clarification", result.message, card.draft)
            issue = calendar_fields_clarification(result.fields)
            waiting = issue.missing[0] if issue else pending.waiting_for
            self._clarification = replace(
                pending,
                fields=result.fields,
                waiting_for="end" if waiting == "duration" else waiting,
            )
            self.changed.emit()
            return True
        if self._clarification.waiting_for == "calendar":
            # An ordinary unrelated utterance must not become an invented calendar
            # name merely because a previous task happened to ask for a target.
            explicit = (
                references_task
                or "календар" in text.casefold()
                or text.strip().startswith(("«", '"'))
            )
            hint = result.fields.requested_calendar_title
            if not explicit and hint is not None:
                labels = {
                    normalize_calendar_title(value)
                    for c in self.calendar.catalog
                    for value in (c.title, f"{c.account_title} / {c.title}")
                }
                if normalize_calendar_title(hint) not in labels:
                    return False
        self.suspend()
        self._fields, self._fields_edited = result.fields, True
        self._continuation_epoch += 1
        self._resume_required = False
        self._topic_reference_required = False
        self._task = replace(task, revision=uuid4())
        self._clarification = None
        self._card = CalendarChatCard("draft", "Детали события уточнены.", card.draft)
        self._advance_conversation()
        return True

    def _submit_conversation(self, text: str) -> bool:
        if (
            self._context is None
            or not is_calendar_chat_request(text)
            or not math.isfinite(self._monotonic())
        ):
            return False
        self._fields = anchor_calendar_fields(
            calendar_chat_fields(text), now=self._clock(), timezone=self.timezone
        )
        self._fields_edited = True
        self._task = CalendarConversationTask(
            task_id=uuid4(),
            revision=uuid4(),
            context=self._context,
            profile_id=self.calendar.principal.profile_id,
            anchor_date=self._clock().astimezone(ZoneInfo(self.timezone)).date(),
            issued_monotonic=self._monotonic(),
            expires_monotonic=self._monotonic() + CALENDAR_TASK_SECONDS,
            direct_create=is_direct_calendar_create_request(text),
        )
        self._card = CalendarChatCard("draft", "Разбираю поручение…", text)
        self._advance_conversation()
        return True

    def _advance_conversation(self) -> None:
        task = self._task
        if (
            self._advancing
            or self._resume_required
            or task is None
            or self._card is None
            or self._effect_dispatched
        ):
            return
        if not self._foreground_current():
            self.suspend()
            return
        if not self._task_current():
            self.clear()
            return
        self._advancing = True
        try:
            issue = calendar_fields_clarification(self._fields)
            if issue is not None:
                questions = {
                    "title": "Как назвать событие?",
                    "day": "На какой день его поставить?",
                    "exact_time": "Во сколько начать? Уточни нужное время словами.",
                    "duration": "На сколько поставить событие — на час или до скольки?",
                }
                self._ask(
                    issue.missing[0],
                    "Пока могу поставить одно обычное событие. Напиши его точные детали."
                    if self._fields.unsupported_semantics
                    or (self._fields.needs_clarification and not self._fields.unresolved_fields)
                    else questions.get(issue.missing[0], "Уточни детали события."),
                )
                return
            if self._catalog_checked_revision != task.revision:
                if (
                    self.calendar.catalog_diagnostics.state == "fresh"
                    and self.calendar.permission_status == "full_access"
                ):
                    self._catalog_checked_revision = task.revision
                else:
                    self._catalog_waiting = True
                    self._card = CalendarChatCard(
                        "preparing", "Проверяю доступ к календарю…", self._card.draft
                    )
                    self.changed.emit()
                    self.calendar.refresh_catalog()
                    self._queue_conversation(self._calendar_changed)
                    return
            if self.calendar.permission_status != "full_access":
                self._card = CalendarChatCard(
                    "connection_required",
                    "Нужно подключить календарь в настройках Сатори и разрешить доступ macOS.",
                    self._card.draft,
                )
                self.changed.emit()
                return
            if self.requested_calendar_title is None:
                self._ask("calendar", "В какой календарь добавить событие?")
                return
            hint = normalize_calendar_title(self.requested_calendar_title)
            matches = [
                c
                for c in self.calendar.catalog
                if self._writable(c)
                and hint
                in {
                    normalize_calendar_title(c.title),
                    normalize_calendar_title(f"{c.account_title} / {c.title}"),
                }
            ]
            if len(matches) != 1:
                self._ask(
                    "calendar",
                    "Название календаря неоднозначно. "
                    "Уточни аккаунт и название, например «iCloud / События»."
                    if len(matches) > 1
                    else "Не нашла доступный календарь с таким названием. "
                    "Уточни название или подключи его в настройках.",
                )
                return
            target = matches[0].source
            grant = self.calendar.grant
            reuse = (
                grant is not None
                and target in grant.resources
                and all(c.account_id == target.account_id for c in grant.resources)
            )
            if not reuse and not self.calendar.use_calendars((target,)):
                self._card = CalendarChatCard(
                    "unavailable",
                    "Не получилось получить доступ к этому календарю. "
                    "Проверь подключение в настройках.",
                    self._card.draft,
                )
                self.changed.emit()
                return
            if not reuse and self.calendar.grant is not None:
                self._task_grant_id = self.calendar.grant.grant_id
            self._manual_target = target
            self._card = CalendarChatCard(
                "draft", "Проверяю время и добавляю событие…", self._card.draft
            )
            self._clarification = None
            self.request_preview(target, self.timezone)
        finally:
            self._advancing = False

    @staticmethod
    def _writable(calendar: CalendarDescriptor) -> bool:
        return (
            calendar.writable is True
            and calendar.allows_modification is True
            and calendar.supports_events is True
            and calendar.source_kind not in {"subscribed", "birthdays", "unknown"}
        )

    @property
    def active_request_id(self) -> str | None:
        return str(self._request.request_id) if self._request is not None else None

    @property
    def writable_calendars(self) -> tuple[CalendarDescriptor, ...]:
        return tuple(
            c
            for c in self.calendar.selected_calendars
            if c.writable is True
            and c.allows_modification is True
            and c.supports_events is True
            and c.source_kind not in {"subscribed", "birthdays", "unknown"}
        )

    @property
    def fields(self) -> CalendarChatFields:
        return self._fields

    @property
    def requested_calendar_title(self) -> str | None:
        return self._fields.requested_calendar_title

    @property
    def preferred_calendar(self) -> CalendarSourceRef | None:
        eligible = self.writable_calendars
        if self._manual_target is not None:
            return next((c.source for c in eligible if c.source == self._manual_target), None)
        hint = self.requested_calendar_title
        if hint is not None:
            matches = [
                c.source
                for c in eligible
                if normalize_calendar_title(c.title) == normalize_calendar_title(hint)
            ]
            return matches[0] if len(matches) == 1 else None
        return next(
            (c.source for c in eligible if c.title == "Работа"),
            eligible[0].source if eligible else None,
        )

    def set_fields(self, fields: CalendarChatFields, *, replace_unsupported: bool = False) -> None:
        """An explicit form submission changes RAM fields and retires prior authority."""
        if (
            self.busy
            or self._card is None
            or self._card.status not in {"draft", "clarification", "unavailable"}
        ):
            return
        checked = CalendarChatFields.model_validate(fields.model_dump())
        # This form does not edit the original destination hint. A different source
        # requires the independent deliberate calendar chooser below.
        checked = checked.model_copy(
            update={"requested_calendar_title": self.requested_calendar_title}
        )
        if self._fields.needs_clarification:
            checked = checked.model_copy(
                update={
                    "needs_clarification": not replace_unsupported,
                    "mode": "exact" if replace_unsupported else checked.mode,
                }
            )
        text, target = self._card.draft, self._manual_target
        self.clear()
        self._fields, self._fields_edited, self._manual_target = checked, True, target
        issue = calendar_fields_clarification(checked)
        self._card = CalendarChatCard(
            "clarification" if issue else "draft",
            issue.message
            if issue
            else "Поля уточнены. Проверь календарь и прочитай выбранные календари.",
            text,
        )
        self.changed.emit()

    def choose_calendar(self, target: CalendarSourceRef) -> bool:
        """Only the explicit UI activation may override a missing/ambiguous name hint."""
        if (
            self.busy
            or self._card is None
            or self._card.status not in {"draft", "clarification", "unavailable"}
        ):
            return False
        if target not in {c.source for c in self.writable_calendars}:
            return False
        text, fields, edited = self._card.draft, self._fields, self._fields_edited
        self.clear()
        self._fields, self._fields_edited, self._manual_target = fields, edited, target
        self._card = CalendarChatCard(
            "draft", "Календарь выбран явно. Проверь поля и прочитай выбранные календари.", text
        )
        self.changed.emit()
        return True

    def reject_fields(self) -> None:
        """Invalid form input cannot retain a preceding lease or old valid values."""
        if (
            self.busy
            or self._card is None
            or self._card.status not in {"draft", "clarification", "unavailable"}
        ):
            return
        text, target, hint = self._card.draft, self._manual_target, self.requested_calendar_title
        unsupported = self._fields.needs_clarification
        self.clear()
        self._fields, self._fields_edited, self._manual_target = (
            CalendarChatFields(requested_calendar_title=hint, needs_clarification=unsupported),
            True,
            target,
        )
        self._card = CalendarChatCard(
            "clarification",
            "Проверь поля: время HH:MM, длительность от 5 до 720 целых минут. "
            "Изменения ещё не применены.",
            text,
        )
        self.changed.emit()

    def bind_context(self, identity_id: str, session_id: str, provider_id: str) -> None:
        value = (identity_id, session_id, provider_id)
        if value != self._context:
            self.clear()
            self._context = value

    def set_profile(self, data_directory: Path) -> None:
        self.clear()
        self._context = None
        self._owner = CalendarHostLeaseOwner(
            data_directory, clock=self._clock, monotonic=self._monotonic
        )

    def submit(self, text: str) -> bool:
        """Display a draft after ordinary Send routing; no file/read/model I/O."""
        if (
            self.conversational
            and self._effect_dispatched
            and self._card is not None
            and self._card.status not in {"created", "reconciled_present"}
        ):
            self._card = CalendarChatCard(
                "unknown",
                "Прежний результат ещё не подтверждён. Повторно создавать событие не буду.",
                self._card.draft,
                self._card.proposal,
            )
            self.changed.emit()
            return True
        self.clear()
        if self._closed or not isinstance(text, str) or not 0 < len(text.strip()) <= 8000:
            return False
        if is_existing_calendar_mutation(text):
            self._card = CalendarChatCard("unsupported", UNSUPPORTED_EXISTING_EVENT, "")
            self.changed.emit()
            return True
        if self.conversational:
            return self._submit_conversation(text)
        self._fields = calendar_chat_fields(text)
        directive = calendar_directive(text)
        issue = (
            calendar_fields_clarification(self._fields)
            if directive and directive.extended
            else None
        )
        if (
            issue is None
            and self.requested_calendar_title is not None
            and self.preferred_calendar is None
        ):
            issue = CalendarTextClarification(
                missing=("calendar",),
                message="Запрошенный календарь недоступен или имя неоднозначно. "
                "Выбери разрешённый календарь назначения явно.",
            )
        self._card = CalendarChatCard(
            "clarification" if issue else "draft",
            issue.message
            if issue
            else "Проверь календарь назначения и часовой пояс, затем прочитай выбранные календари.",
            text,
        )
        self.changed.emit()
        return True

    def _require_current(self) -> CalendarHostLease:
        if self._closed or QThread.currentThread() != self.thread():
            raise ValueError("calendar_chat_unavailable")
        lease, context, request = self._lease, self._context, self._request
        grant = self.calendar.grant
        if (
            lease is None
            or request is None
            or context is None
            or grant != lease.read_grant
            or self.calendar.generation != lease.controller_generation
            or self.calendar.permission_status != "full_access"
            or context[:2] != (str(lease.scope.identity_id), str(lease.scope.session_id))
            or not lease.issued_at <= self._clock() < lease.expires_at
            or self._monotonic() >= lease.monotonic_deadline
            or (self.conversational and not self._foreground_current())
        ):
            raise ValueError("calendar_chat_context_changed")
        return lease

    def request_preview(self, target: CalendarSourceRef, timezone: str) -> None:
        if self._card is None or self._card.status not in {"draft", "clarification", "unavailable"}:
            return
        text = self._card.draft
        try:
            directive = calendar_directive(text)
            if self._fields_edited or (directive and directive.extended):
                issue = calendar_fields_clarification(self._fields)
                if issue is not None:
                    self._card = CalendarChatCard("clarification", issue.message, text)
                    self.changed.emit()
                    return
            if self.requested_calendar_title is not None and self.preferred_calendar != target:
                self._card = CalendarChatCard(
                    "clarification",
                    "Запрошенный календарь не выбран однозначно. "
                    "Выбери доступный календарь назначения явно.",
                    text,
                )
                self.changed.emit()
                return
            if self._context is None or self.calendar.permission_status != "full_access":
                raise ValueError("calendar_chat_grant_required")
            grant = self.calendar.grant
            if grant is None or target not in grant.resources:
                raise ValueError("calendar_chat_grant_required")
            if target not in {c.source for c in self.writable_calendars}:
                raise ValueError("calendar_chat_grant_required")
            now = self._clock()
            scope = CalendarActionScope(
                identity_id=UUID(self._context[0]),
                profile_id=grant.principal.profile_id,
                session_id=UUID(self._context[1]),
                generation=uuid5(NAMESPACE_URL, f"{grant.grant_id}:{self.calendar.generation}"),
                provider="macos_eventkit",
                account_id=target.account_id,
                calendar_id=target.calendar_id,
                timezone=timezone,
            )
            request_id = uuid4()
            parsed = calendar_chat_intake(
                text,
                request_id=request_id,
                scope=scope,
                now=now,
                fields=self._fields if self._fields_edited else None,
            )
            if isinstance(parsed, CalendarTextClarification):
                self._card = CalendarChatCard("clarification", parsed.message, text)
                self.changed.emit()
                return
            revision, observed_at, selected = self.calendar.planning_catalog()
            by_source = {item.source: item for item in selected}
            ordered = tuple(by_source[resource] for resource in grant.resources)
            expires = min(grant.expires_at, now + MAX_HOST_LEASE_LIFETIME)
            lease = CalendarHostLease(
                full_access=True,
                lease_id=uuid4(),
                request_id=request_id,
                scope=scope,
                read_grant=grant,
                controller_generation=self.calendar.generation,
                calendars=ordered,
                catalog_revision=revision,
                catalog_observed_at=observed_at,
                issued_at=now,
                expires_at=expires,
                monotonic_deadline=self._monotonic() + (expires - now).total_seconds(),
                ui_pid=os.getpid(),
            )
            self._owner.issue(lease)
            self._lease, self._request = lease, parsed.request
            task = self._task
            if (
                self.conversational
                and task is not None
                and task.direct_create
                and parsed.mode == "exact"
            ):
                self._conversation_authority = CalendarConversationAuthority(
                    task_id=task.task_id,
                    revision=task.revision,
                    context=task.context,
                    request=parsed.request,
                    fields_digest=context_digest(self._fields.model_dump(mode="json")),
                    expires_monotonic=min(task.expires_monotonic, lease.monotonic_deadline),
                )
            self._send(
                "exact_preview" if parsed.mode == "exact" else "preview", request=parsed.request
            )
        except (ValueError, KeyError, TypeError):
            fields, edited, target_choice = self._fields, self._fields_edited, self._manual_target
            self.clear()
            self._fields, self._fields_edited, self._manual_target = fields, edited, target_choice
            self._card = CalendarChatCard(
                "unavailable",
                "Нужны действующее разрешение, свежий каталог и доступный для записи календарь. "
                "Проверь подключение календарей в настройках.",
                text,
            )
            self.changed.emit()

    def _send(self, operation: str, **fields: object) -> None:
        lease = self._require_current()
        if self._pending is not None:
            raise ValueError("calendar_chat_busy")
        command = CalendarBackendCommand.model_validate({"operation": operation, **fields})
        payload: dict[str, object] = {
            "session_id": str(lease.scope.session_id),
            "request_id": str(lease.request_id),
            "calendar": command.model_dump(mode="json", exclude_none=True),
        }
        if operation in {"preview", "exact_preview"}:
            payload["calendar_lease"] = lease.model_dump(mode="json")
        if operation == "create" and self.conversational:
            # Mark possible effect before queuing the wire command. Hide/crash/late
            # results may retire publication, but can never rearm this RAM intent.
            self._effect_dispatched = True
            self._clarification = None
        self._pending = operation
        self.changed.emit()
        self.command_requested.emit(payload)

    def disclose(self) -> None:
        if self._card is not None and self._card.status == "disclosure_review":
            try:
                self._send("disclose", explicit_disclosure=True)
            except ValueError:
                self.clear()

    def create_reviewed(self) -> None:
        if self.busy:
            return
        if self._card is not None and self._card.status == "proposed" and self._card.proposal:
            try:
                if self.conversational and self._task is not None and self._task.direct_create:
                    if not self._conversation_admits(self._card.proposal):
                        return
                    self._auto_approval_sent = True
                self._send("approve", review_digest=self._card.proposal.review_digest)
            except ValueError:
                if self.conversational:
                    self.suspend()
                else:
                    self.clear()

    def _conversation_admits(self, proposal: CalendarCreateProposal) -> bool:
        authority = self._conversation_authority
        return bool(
            self._foreground_current()
            and authority is not None
            and authority.admits(
                self._task, self._fields, self._context, self._request, proposal, self._monotonic()
            )
        )

    def reconcile(self) -> None:
        if self._card is not None and self._card.status in {
            "unknown",
            "created",
            "reconciled_present",
        }:
            try:
                self._send("reconcile")
            except ValueError:
                if self.conversational:
                    self.suspend()
                else:
                    self.clear()

    def accept_result(self, request_id: str, operation: str, result: dict[str, object]) -> None:
        if request_id != self.active_request_id or operation != self._pending:
            return
        self._pending = None
        try:
            lease = self._require_current()
            assert self._request is not None
            assert self._card is not None
            draft, proposal = self._card.draft, self._card.proposal
            if result.get("status") in {"unavailable", "unknown"}:
                status = "unknown" if operation in {"create", "reconcile"} else "unavailable"
                self._card = CalendarChatCard(status, self._outcome_text(status), draft, proposal)
            elif operation == "preview":
                if (
                    result.get("status") != "disclosure_review_required"
                    or result.get("request_id") != request_id
                    or self._context is None
                    or result.get("provider") != self._context[2]
                ):
                    raise ValueError("calendar_chat_preview_invalid")
                echoed = CalendarPlanningRequest.model_validate_json(
                    json.dumps(result.get("request"))
                )
                if echoed != self._request:
                    raise ValueError("calendar_chat_request_mismatch")
                self._card = CalendarChatCard(
                    "disclosure_review",
                    "Разрешить этой модели один раз получить названия и время выбранных событий, "
                    "контекст разговора, подходящую память и типизированные проекции Сатори?",
                    draft,
                    provider=self._context[2],
                )
            elif operation == "disclose":
                if result.get("status") != "disclosure_approved":
                    raise ValueError("calendar_chat_disclosure_invalid")
                self._send("plan")
            elif operation in {"plan", "exact_preview"}:
                if result.get("status") != "proposed":
                    self._card = CalendarChatCard(
                        "clarification",
                        "Нужно уточнить запрос или выбрать другое окно поиска.",
                        draft,
                    )
                else:
                    proposal = CalendarCreateProposal.model_validate_json(
                        json.dumps(result.get("proposal"))
                    )
                    target = next(
                        c
                        for c in lease.calendars
                        if c.source.calendar_id == lease.scope.calendar_id
                    )
                    if (
                        proposal.scope != lease.scope
                        or proposal.command.request_id != lease.request_id
                        or proposal.command.title != self._request.title
                        or proposal.calendar_title != target.title
                        or proposal.account_title != target.account_title
                        or not self._request.window_start
                        <= proposal.command.starts_at
                        < proposal.command.ends_at
                        <= self._request.window_end
                        or proposal.command.ends_at - proposal.command.starts_at
                        != timedelta(minutes=self._request.duration_minutes)
                        or (
                            operation == "exact_preview"
                            and (
                                result.get("request_id") != request_id
                                or result.get("mode") != "user_exact_interval"
                                or result.get("model_called") is not False
                                or (proposal.command.starts_at, proposal.command.ends_at)
                                != (self._request.window_start, self._request.window_end)
                            )
                        )
                    ):
                        raise ValueError("calendar_chat_proposal_mismatch")
                    self._card = CalendarChatCard(
                        "proposed",
                        "Проверь точные детали. Создание требует отдельного подтверждения.",
                        draft,
                        proposal,
                    )
                    if (
                        self.conversational
                        and operation == "exact_preview"
                        and self._task is not None
                        and self._task.direct_create
                    ):
                        if not self._conversation_admits(proposal):
                            raise ValueError("calendar_user_authority_changed")
                        self.create_reviewed()
            else:
                record = CalendarActionRecord.model_validate_json(json.dumps(result))
                if record.proposal != proposal or record.proposal.scope != lease.scope:
                    raise ValueError("calendar_chat_record_mismatch")
                if operation == "approve":
                    if record.state != "approved" or record.approval is None:
                        raise ValueError("calendar_chat_approval_invalid")
                    if (
                        self.conversational
                        and self._task is not None
                        and self._task.direct_create
                        and (
                            not self._auto_approval_sent
                            or not self._conversation_admits(record.proposal)
                        )
                    ):
                        raise ValueError("calendar_user_authority_changed")
                    self._send("create", approval_id=record.approval.approval_id)
                else:
                    self._card = CalendarChatCard(
                        record.state,
                        self._conversation_outcome(record.state, record.proposal)
                        if self.conversational
                        else self._outcome_text(record.state),
                        draft,
                        proposal,
                    )
                    self._clarification = None
                    self._conversation_authority = None
                    if self.conversational and record.state in {
                        "created",
                        "reconciled_present",
                        "reconciled_absent",
                        "rejected",
                        "conflict",
                    }:
                        self.suspend()
            self.changed.emit()
        except (ValueError, TypeError, AssertionError):
            if self.conversational:
                self.suspend()
            else:
                self.clear()

    def _conversation_outcome(self, status: str, proposal: CalendarCreateProposal) -> str:
        if status in {"created", "reconciled_present"}:
            zone = ZoneInfo(proposal.command.timezone)
            start, end = (
                proposal.command.starts_at.astimezone(zone),
                proposal.command.ends_at.astimezone(zone),
            )
            return (
                f"Добавила «{proposal.command.title}» в «{proposal.calendar_title}»: "
                f"{start:%d.%m.%Y}, {start:%H:%M}–{end:%H:%M} "
                + (
                    "по Москве."
                    if proposal.command.timezone == "Europe/Moscow"
                    else f"({proposal.command.timezone})."
                )
            )
        return self._outcome_text(status)

    @staticmethod
    def _outcome_text(status: str) -> str:
        return {
            "created": (
                "Событие создано и проверено в локальном календаре. "
                "Доставка через iCloud на iPhone отдельно не подтверждена."
            ),
            "reconciled_present": (
                "Проверка нашла созданное событие с точными деталями. Новое событие не создавалось."
            ),
            "reconciled_absent": (
                "Проверка не нашла событие. Повторное создание автоматически не разрешается."
            ),
            "unknown": (
                "Неизвестно, создано ли событие. Повторной отправки нет; "
                "можно только проверить результат."
            ),
            "rejected": "Создание отклонено. Событие не подтверждено.",
            "unavailable": "Календарная операция сейчас недоступна. Создание не подтверждено.",
        }.get(status, "Результат операции требует проверки.")

    def accept_error(self, request_id: str, operation: str, _code: str) -> None:
        if request_id != self.active_request_id or operation != self._pending:
            return
        self._pending = None
        try:
            self._require_current()
        except ValueError:
            if self.conversational:
                self.suspend()
            else:
                self.clear()
            return
        if self._card is not None:
            status = "unknown" if operation in {"create", "reconcile"} else "unavailable"
            self._card = CalendarChatCard(
                status, self._outcome_text(status), self._card.draft, self._card.proposal
            )
            self.changed.emit()

    def handle_host_read(self, data: dict[str, object]) -> None:
        value = data.get("call_id")
        if not isinstance(value, str) or len(value) != 36:
            return
        try:
            identifier = UUID(value)
            if not identifier.int or str(identifier) != value:
                return
        except ValueError:
            return
        call_id = value
        try:
            if set(data) != {"call_id", "request"}:
                raise ValueError("calendar_host_request_invalid")
            self._require_current()
            request = CalendarPlanningRequest.model_validate_json(json.dumps(data.get("request")))
            if request != self._request or self._host_read is not None or self._pending is None:
                raise ValueError("calendar_host_request_mismatch")
            pending = _HostRead(call_id, request)
            self._host_read = pending
            native_id = self.calendar.request_planning_read(
                request.window_start, request.window_end, request.scope.timezone
            )
            if self._host_read is pending:
                pending.native_id = str(native_id)
        except (ValueError, TypeError):
            self._reply_host(call_id, None)

    def _reply_host(self, call_id: str, snapshot: CalendarPlanningSnapshot | None) -> None:
        self.host_read_result.emit(
            {
                "protocol_version": 1,
                "kind": "calendar_host_result",
                "call_id": call_id,
                "snapshot": snapshot.model_dump(mode="json") if snapshot is not None else None,
                "error": None if snapshot is not None else "calendar_host_read_unavailable",
            }
        )

    def _read_ready(self, native_id: str, value: object) -> None:
        pending = self._host_read
        if pending is None or (pending.native_id is not None and pending.native_id != native_id):
            return
        self._host_read = None
        try:
            lease = self._require_current()
            snapshot = CalendarSnapshot.model_validate(value)
            read, request = snapshot.request, pending.request
            if (
                read.grant_id != lease.read_grant.grant_id
                or read.principal != lease.read_grant.principal
                or read.generation != lease.read_grant.generation
                or read.resources != lease.read_grant.resources
                or (read.starts_at, read.ends_at, read.timezone)
                != (request.window_start, request.window_end, request.scope.timezone)
            ):
                raise ValueError("calendar_host_snapshot_mismatch")
            events = []
            for event in snapshot.occurrences:
                start, end = event.starts_at, event.ends_at
                if start is None or end is None:
                    assert event.all_day_start is not None
                    assert event.all_day_end is not None
                    zone = ZoneInfo(event.timezone)
                    start = _unambiguous_midnight(event.all_day_start, zone)
                    end = _unambiguous_midnight(event.all_day_end, zone)
                events.append(
                    CalendarObservedEvent(
                        event_ref=context_digest(
                            [
                                event.source.calendar_id,
                                event.event_id,
                                event.occurrence_id,
                                event.revision,
                            ]
                        )[:32],
                        calendar_id=event.source.calendar_id,
                        title=event.title,
                        starts_at=start,
                        ends_at=end,
                        status=event.status,
                    )
                )
            admitted = CalendarPlanningSnapshot(
                scope=request.scope,
                read_grant_id=read.grant_id,
                selected_calendar_ids=tuple(resource.calendar_id for resource in read.resources),
                observed_at=snapshot.observed_at,
                expires_at=min(snapshot.expires_at, lease.expires_at),
                window_start=read.starts_at,
                window_end=read.ends_at,
                complete=snapshot.status == "ok",
                events=tuple(events),
            )
            admitted.require_for(request, self._clock())
            self._reply_host(pending.call_id, admitted)
        except (ValueError, TypeError, AssertionError):
            self._reply_host(pending.call_id, None)

    def _read_failed(self, native_id: str, _code: str) -> None:
        pending = self._host_read
        if pending is not None and (pending.native_id is None or pending.native_id == native_id):
            self._host_read = None
            self._reply_host(pending.call_id, None)

    def _calendar_changed(self) -> None:
        if self._clearing or self._advancing:
            return
        if self.conversational and self._task is not None and not self._task_current():
            self.clear()
            return
        if self.conversational and self._resume_required:
            return
        if self.conversational and self._catalog_waiting:
            if not self._foreground_current():
                self.suspend()
                return
            if self.calendar.busy:
                return
            self._catalog_waiting = False
            if (
                self._task is not None
                and self.calendar.catalog_diagnostics.state == "fresh"
                and self.calendar.permission_status == "full_access"
            ):
                self._catalog_checked_revision = self._task.revision
                self._queue_conversation(self._advance_conversation)
            elif self._card is not None:
                access_needed = self.calendar.permission_status != "full_access"
                self._card = CalendarChatCard(
                    "connection_required" if access_needed else "unavailable",
                    "Нужно подключить календарь в настройках Сатори и разрешить доступ macOS."
                    if access_needed
                    else "Не получилось проверить список календарей. "
                    "Попробуй продолжить поручение позже.",
                    self._card.draft,
                )
            self.changed.emit()
            return
        if self._lease is not None:
            try:
                self._require_current()
            except ValueError:
                if self.conversational:
                    self.suspend()
                else:
                    self.clear()
        self.changed.emit()

    def tick(self) -> None:
        if self.conversational and self._task is not None and not self._task_current():
            self.clear()
            return
        self._calendar_changed()

    def clear(self) -> None:
        if self._clearing:
            return
        self._continuation_epoch += 1
        self._clearing = True
        try:
            task_grant_id, self._task_grant_id = self._task_grant_id, None
            self._lease, self._request, self._pending, self._card = None, None, None, None
            self._fields, self._fields_edited, self._manual_target = (
                CalendarChatFields(),
                False,
                None,
            )
            self._task, self._clarification, self._conversation_authority = None, None, None
            self._catalog_waiting, self._catalog_checked_revision = False, None
            self._effect_dispatched, self._auto_approval_sent = False, False
            self._resume_required = False
            self._topic_reference_required = False
            pending, self._host_read = self._host_read, None
            try:
                self._owner.revoke()
            except ValueError:
                self.revocation_failed.emit()
            self.calendar.cancel_planning_read()
            if (
                task_grant_id is not None
                and self.calendar.grant is not None
                and self.calendar.grant.grant_id == task_grant_id
            ):
                self.calendar.selection_changed()
            if pending is not None:
                self._reply_host(pending.call_id, None)
        finally:
            self._clearing = False
        self.changed.emit()

    def close(self) -> None:
        self.clear()
        self._closed = True
        self._timer.stop()
