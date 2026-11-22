"""Finite assistant actions beside conversation; source results never enter Core."""

# ruff: noqa: RUF001 - intentional Russian user-facing text

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, Signal

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.dialogue_contracts import CalendarReadProposal, SourceInterval
from satori.application.assistant.intents import parse_assistant_intent
from satori.application.assistant.reminder_contracts import ReminderRecord
from satori.application.assistant.schedule_contracts import local_instant
from satori.desktop.calendar_controller import CalendarController
from satori.desktop.calendar_dialogue_ui import CalendarDialoguePanel
from satori.desktop.reminder_controller import ReminderController


@dataclass(frozen=True)
class TaskCard:
    request_id: UUID
    title: str
    body: str
    status: str
    source: str
    action: str | None = None


class AssistantController(QObject):
    """One transient result surface using the existing source and reminder owners."""

    changed = Signal()

    def __init__(
        self,
        calendar: CalendarController,
        reminders: ReminderController,
        parent: QObject | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        telegram_reply_handoff: Callable[[AssistantIntent], bool] | None = None,
        mail_lookup_handoff: Callable[[AssistantIntent], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.calendar = calendar
        self.reminders = reminders
        self._clock = clock or (lambda: datetime.now(UTC))
        self._telegram_reply_handoff = telegram_reply_handoff
        self._mail_lookup_handoff = mail_lookup_handoff
        self.card: TaskCard | None = None
        self._calendar_intent: AssistantIntent | None = None
        self.calendar.dialogue_changed.connect(self._calendar_changed)
        self.calendar.changed.connect(self._calendar_changed)

    def parse(self, text: str, *, timezone: str) -> AssistantIntent | None:
        return parse_assistant_intent(text, now=self._clock(), timezone=timezone)

    def clear(self) -> None:
        self._calendar_intent = None
        self.calendar.clear_dialogue("cancelled")
        self.card = None
        self.changed.emit()

    def clear_calendar_read(self) -> None:
        """Retire this conversation's transient Calendar read on view loss."""
        if self._calendar_intent is not None:
            self.clear()

    def show_due(self, record: ReminderRecord) -> None:
        """Show only a due record currently held by this profile's reminder owner."""
        if record.state != "due" or record not in self.reminders.records:
            return
        self.clear()
        self.card = TaskCard(
            record.request_id,
            "Пора вспомнить",
            record.title,
            "due",
            "Твоё напоминание",
            "reminders",
        )
        self.changed.emit()

    def _show(
        self,
        intent: AssistantIntent,
        title: str,
        body: str,
        status: str,
        source: str,
        action: str | None = None,
    ) -> None:
        self.card = TaskCard(intent.request_id, title, body, status, source, action)
        self.changed.emit()

    def dispatch(
        self, intent: AssistantIntent, *, capabilities: frozenset[str] | None = None
    ) -> None:
        """A mobile capability is checked before any source-owner property or call."""
        self._calendar_intent = None
        source = (
            "calendar"
            if intent.kind.startswith("calendar_")
            else intent.source
            if intent.kind in {"selected_lookup", "mailbox_search"}
            else None
        )
        required = (
            "source_analysis"
            if intent.kind == "source_answer"
            else "telegram"
            if intent.kind == "telegram_reply"
            else source
        ) or ("reminders" if intent.kind.startswith("reminder_") else None)
        if capabilities is not None and required is not None and required not in capabilities:
            self._show(
                intent,
                "Нужен доступ",
                "Это подключение не разрешено этому устройству.",
                "source_grant_required",
                "Разрешения",
            )
            return
        if intent.kind == "clarification":
            self._show(
                intent,
                "Уточним задачу",
                intent.clarification or "Уточни запрос.",
                "clarification",
                "Твой запрос",
            )
        elif intent.kind == "source_answer":
            self._show(
                intent,
                "Проверка сообщений",
                "Этот запрос обрабатывается в разговоре Сатори. Подключения доступны в настройках.",
                "conversation_task_required",
                "Твой запрос",
            )
        elif intent.kind == "telegram_reply":
            # Desktop-only text staging. Mobile must never drive a local UI callback.
            accepted = (
                capabilities is None
                and self._telegram_reply_handoff is not None
                and self._telegram_reply_handoff(intent)
            )
            self._show(
                intent,
                "Ответ в Telegram",
                "Точный текст перенесён в Telegram. Выбери там актуальное сообщение, "
                "проверь ответ и отдельно разреши отправку. Текст живёт не дольше двух минут."
                if accepted
                else "Открой Telegram в текущем разговоре и подготовь ответ заново. "
                "Отправки не было.",
                "selection_required" if accepted else "context_changed",
                "Твой текст · без модели",
                "telegram",
            )
        elif intent.kind.startswith("calendar_"):
            self._read_calendar(intent)
        elif intent.kind == "reminder_create":
            assert intent.title is not None
            assert intent.due_at is not None
            receipt = self.reminders.create(
                intent.title, intent.due_at, intent.timezone, intent.request_id
            )
            if receipt is None:
                uncertain = (self.reminders.diagnostic or "").startswith("reminder_store_")
                self._show(
                    intent,
                    "Сохранение не подтверждено",
                    "Напоминание могло сохраниться. Сначала проверь список; "
                    "повтор автоматически не выполняется."
                    if uncertain
                    else "Проверь время и раздел «Напоминания».",
                    "unknown" if uncertain else "error",
                    "Локальные напоминания",
                    "reminders",
                )
                return
            record = receipt.record
            local = record.due_at.astimezone(ZoneInfo(record.timezone))
            self._show(
                intent,
                "Напоминание сохранено",
                f"{record.title}\n{local:%d.%m.%Y %H:%M} "
                f"· {record.timezone}\n\nДоставка OS-уведомления: "
                f"{record.delivery_state}. Сохранение и доставка проверяются отдельно.",
                "saved",
                "Локальные напоминания",
                "reminders",
            )
        elif intent.kind == "reminder_list":
            self.reminders.refresh()
            if self.reminders.status == "error" or self.reminders.diagnostic is not None:
                self._show(
                    intent,
                    "Напоминания недоступны",
                    "Не удалось проверить локальный список. "
                    "Открой раздел «Напоминания» для проверки состояния.",
                    "error",
                    "Локальные напоминания",
                    "reminders",
                )
                return
            active = [r for r in self.reminders.records if r.state != "cancelled"]
            rows = [
                f"{r.due_at.astimezone(ZoneInfo(r.timezone)):%d.%m %H:%M} · {r.title}"
                f"\n{r.state} · {r.reminder_id}"
                for r in active[:50]
            ]
            if len(active) > 50:
                rows.append("Показаны первые 50 напоминаний. Полный список — в настройках.")
            self._show(
                intent,
                "Твои напоминания",
                "\n\n".join(rows) or "Активных напоминаний нет.",
                "ready",
                "Локальные напоминания",
                "reminders",
            )
        elif intent.kind == "reminder_cancel":
            assert intent.reminder_id is not None
            receipt = self.reminders.cancel(intent.reminder_id, intent.request_id)
            uncertain = receipt is None and (self.reminders.diagnostic or "").startswith(
                "reminder_store_"
            )
            self._show(
                intent,
                "Отмена напоминания",
                "Локальное напоминание отменено. "
                "Состояние OS-доставки показано в разделе «Напоминания»."
                if receipt is not None
                else "Отмена не подтверждена. Проверь выбранное напоминание.",
                "cancelled" if receipt is not None else "unknown" if uncertain else "error",
                "Локальные напоминания",
                "reminders",
            )
        elif intent.kind in {"selected_lookup", "mailbox_search"}:
            mailbox = intent.kind == "mailbox_search"
            if (
                intent.source == "mail"
                and capabilities is None
                and self._mail_lookup_handoff is not None
            ):
                accepted = self._mail_lookup_handoff(intent)
                self._show(
                    intent,
                    "Поиск по всей почте Gmail" if mailbox else "Поиск в выбранных письмах",
                    (
                        "Запрос открыт в разделе «Почта». При действующем входе поиск выполняется "
                        "по всему Gmail, включая спам и корзину. Если входа нет, подключи Gmail."
                        if mailbox
                        else "Запрос открыт в разделе «Почта». Результаты показаны там; "
                        "новое чтение писем выполняется только твоей отдельной кнопкой."
                    )
                    if accepted
                    else "Контекст разговора изменился. Запрос сохранён в поле ввода; "
                    "поиск и чтение не выполнялись.",
                    "source_local" if accepted else "context_changed",
                    "Gmail" if mailbox else "Выбранные письма",
                    "mail",
                )
                return
            target = intent.source or "connections"
            self._show(
                intent,
                "Поиск по всей почте Gmail" if mailbox else "Поиск в выбранном источнике",
                "Поиск из этого интерфейса недоступен; открой почту на Mac."
                if mailbox
                else "Открой источник и выбери данные. Поиск работает только по текущему "
                "разрешённому тексту; весь аккаунт не просматривается.",
                "selection_required",
                "Выбранные данные",
                target,
            )

    def _read_calendar(self, intent: AssistantIntent) -> None:
        projection = self.calendar.status_projection
        if not projection.can_query_dialogue:
            self._show(
                intent,
                "Проверить календарь сейчас не получилось",
                projection.status_text,
                "connection_required",
                "Календарь",
                "calendar",
            )
            return
        assert intent.day is not None
        chat_id = self.calendar.dialogue_chat_id
        if chat_id is None:
            self._show(
                intent,
                "Календарь недоступен",
                "Вернись в текущий разговор и повтори запрос.",
                "context_changed",
                "Календарь",
            )
            return
        try:
            start = local_instant(intent.day, time.min, intent.timezone)
            end = local_instant(intent.day + timedelta(days=1), time.min, intent.timezone)
            proposal = CalendarReadProposal(
                proposal_id=intent.request_id,
                principal=self.calendar.principal,
                chat_id=chat_id,
                turn_id=intent.request_id,
                kind="agenda" if intent.kind == "calendar_agenda" else "unoccupied_intervals",
                day=intent.day,
                timezone=intent.timezone,
                window=SourceInterval(starts_at=start.astimezone(UTC), ends_at=end.astimezone(UTC)),
                minimum_minutes=30,
            )
        except (ValueError, OverflowError):
            self._show(
                intent,
                "Уточни время",
                "Не удалось однозначно определить границы дня.",
                "clarification",
                "Календарь",
            )
            return
        self._calendar_intent = intent
        self._show(
            intent,
            "Проверяю календарь",
            "Читаю только разрешённые выбранные календари…",
            "reading",
            "Календарь",
        )
        self.calendar.submit_dialogue(proposal)

    def _calendar_changed(self) -> None:
        intent = self._calendar_intent
        if intent is None:
            return
        answer = self.calendar.dialogue_answer
        if answer is not None and answer.proposal.proposal_id == intent.request_id:
            agenda = intent.kind == "calendar_agenda"
            self._show(
                intent,
                f"Календарь на {answer.proposal.day:%d.%m.%Y}"
                if agenda
                else "Интервалы без событий",
                CalendarDialoguePanel._answer_text(answer, agenda_first=agenda),
                answer.receipt.status,
                "Выбранные календари · EventKit",
                "calendar",
            )
        elif self.calendar.dialogue_status == "reading":
            self._show(
                intent, "Проверяю календарь", "Читаю выбранные календари…", "reading", "Календарь"
            )
        else:
            self._show(
                intent,
                "Календарная карточка",
                "Временные данные убраны или не получены. "
                "Повтори запрос, чтобы проверить актуальный календарь.",
                self.calendar.dialogue_status,
                "Календарь",
                "calendar",
            )
