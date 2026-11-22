"""Explicit local calendar action; transient plain-text cards never enter conversation Core."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from satori.application.assistant.contracts import (
    CalendarOccurrence,
    CalendarSourceRef,
    require_timezone,
)
from satori.application.assistant.dialogue_contracts import (
    CalendarReadProposal,
    GroundedCalendarAnswer,
    SourceInterval,
)
from satori.desktop.calendar_controller import CalendarController
from satori.desktop.calendar_ui import _plain_label, _system_timezone

_STATUSES = {
    "idle": "Выбери запрос и выполни проверку перед чтением.",
    "cancelled": "Календарный запрос отменён. Временные данные убраны.",
    "reading": "Читаю выбранные календари для этого запроса.",
    "ready": "Временный ответ по выбранным календарям получен.",
    "ok": "Временный ответ по выбранным календарям получен.",
    "empty": "Полный снимок выбранных календарей не содержит событий в этом окне.",
    "partial": "Ответ неполный. Интервалы без событий по нему не определяются.",
    "denied": "Чтение не разрешено. Отсутствие событий не установлено.",
    "unavailable": "Календарный источник недоступен. Отсутствие событий не установлено.",
    "error": "Календарный ответ не получен. Отсутствие событий не установлено.",
    "grant_required": "Сначала выбери и разреши календари во вкладке «Мой день».",
    "grant_expired": "Разрешение на календари истекло. Выбери их во вкладке «Мой день» заново.",
    "snapshot_expired": "Срок ответа истёк. Для чтения нужны новая проверка запроса и согласие.",
    "stale": "Срок ответа истёк. Для чтения нужны новая проверка запроса и согласие.",
    "request_expired": "Время чтения истекло. Для повтора нужны новая проверка запроса и согласие.",
    "context_changed": "Чат изменён. Проверь календарный запрос заново.",
    "draft_changed": "Текст сообщения изменён. Проверь календарный запрос заново.",
    "disconnected": "Календарь отключён. Временные данные убраны.",
    "selection_changed": "Выбор календарей изменён. Проверь календарный запрос заново.",
    "superseded": "Предыдущий календарный запрос заменён новым.",
    "permission_denied": "macOS не разрешила чтение календарей.",
    "permission_restricted": "macOS ограничивает доступ к календарям.",
    "permission_not_determined": "Подключи календарь явно во вкладке «Мой день».",
    "provider_unavailable": "Календарный компонент недоступен.",
    "provider_error": "Чтение календаря не удалось.",
}
_INVALID_REQUEST = (
    "Проверь дату, часовой пояс IANA и окно внутри одного дня. "
    "Неоднозначное или несуществующее местное время не допускается."
)


def _local_boundary(day: date, value: time, zone: ZoneInfo) -> datetime:
    """Reject civil-time gaps and both folds instead of silently choosing one offset."""

    local = datetime.combine(day, value, zone)
    utc = local.astimezone(UTC)
    if (
        utc.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None)
        or local.replace(fold=1).utcoffset() != local.utcoffset()
    ):
        raise ValueError("ambiguous_or_nonexistent_window_boundary")
    return utc


class CalendarDialoguePanel(QWidget):
    """One explicit preview and one-use display consent, sharing My day's read owner."""

    def __init__(self, controller: CalendarController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._proposal: CalendarReadProposal | None = None
        self._preview_context: tuple[object, ...] | None = None
        self._context: tuple[object, ...] | None = None
        layout = QVBoxLayout(self)
        self.disclosure = _plain_label(
            "Календарный ответ — отдельная временная карточка в этом чате. "
            "Данные не передаются модели, не сохраняются в истории или памяти и не озвучиваются. "
            "Доступные источники выбираются во вкладке «Мой день». "
            "Интервал без событий не подтверждает твою личную доступность."
        )
        layout.addWidget(self.disclosure)
        self.connection_summary = _plain_label()
        self.connection_summary.setAccessibleName("Состояние подключения календаря")
        layout.addWidget(self.connection_summary)
        self.intent = QComboBox()
        self.intent.addItem("Что в календаре за день", "agenda")
        self.intent.addItem("Интервалы без событий", "unoccupied_intervals")
        self.intent.setAccessibleName("Тип календарного запроса")
        layout.addWidget(self.intent)
        day_row = QHBoxLayout()
        self.today_button = QPushButton("Сегодня")
        self.tomorrow_button = QPushButton("Завтра")
        for button in (self.today_button, self.tomorrow_button):
            button.setToolTip("Подготовить список событий за день; чтение требует нового согласия")
        self.day = QDateEdit(QDate.currentDate())
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("dd.MM.yyyy")
        self.day.setAccessibleName("Точная дата календарного запроса")
        for widget in (self.today_button, self.tomorrow_button, self.day):
            day_row.addWidget(widget)
        layout.addLayout(day_row)
        zone_row = QHBoxLayout()
        zone_row.addWidget(_plain_label("Часовой пояс IANA:"))
        self.timezone = QLineEdit(_system_timezone())
        self.timezone.setMaxLength(128)
        self.timezone.setAccessibleName("Часовой пояс календарного запроса IANA")
        zone_row.addWidget(self.timezone)
        layout.addLayout(zone_row)
        window_row = QHBoxLayout()
        window_row.addWidget(_plain_label("Начало:"))
        self.window_start = QTimeEdit(QTime(9, 0))
        self.window_start.setDisplayFormat("HH:mm")
        self.window_start.setAccessibleName("Начало интервала без событий")
        window_row.addWidget(self.window_start)
        window_row.addWidget(_plain_label("до:"))
        self.window_end = QTimeEdit(QTime(18, 0))
        self.window_end.setDisplayFormat("HH:mm")
        self.window_end.setAccessibleName("Конец интервала без событий")
        window_row.addWidget(self.window_end)
        window_row.addWidget(_plain_label("Минимум минут:"))
        self.minimum_gap = QSpinBox()
        self.minimum_gap.setRange(1, 1440)
        self.minimum_gap.setValue(30)
        self.minimum_gap.setAccessibleName("Минимальная длительность интервала без событий")
        window_row.addWidget(self.minimum_gap)
        layout.addLayout(window_row)
        self.window_description = _plain_label()
        self.sources = _plain_label()
        layout.addWidget(self.window_description)
        layout.addWidget(self.sources)
        self.preview_button = QPushButton("Проверить запрос")
        layout.addWidget(self.preview_button)
        self.preview = _plain_label()
        self.preview.setAccessibleName("Точный preview календарного запроса")
        layout.addWidget(self.preview)
        self.consent = QCheckBox(
            "Разрешаю один раз прочитать этот запрос и временно показать ответ здесь.\n"
            "Без модели, истории, памяти и озвучки."
        )
        layout.addWidget(self.consent)
        action_row = QHBoxLayout()
        self.run_button = QPushButton("Прочитать и показать")
        self.cancel_button = QPushButton("Убрать / отменить")
        self.refresh_card_button = QPushButton("Обновить карточку…")
        self.refresh_card_button.setToolTip(
            "Подготовить запрос заново. Затем проверь запрос и разреши одно чтение."
        )
        action_row.addWidget(self.run_button)
        action_row.addWidget(self.refresh_card_button)
        action_row.addWidget(self.cancel_button)
        layout.addLayout(action_row)
        self.status = _plain_label()
        layout.addWidget(self.status)
        self.answer = QPlainTextEdit()
        self.answer.setReadOnly(True)
        self.answer.setAccessibleName("Временный календарный ответ вне истории чата")
        self.answer.setMinimumHeight(120)
        layout.addWidget(self.answer)
        self.intent.currentIndexChanged.connect(self._edited)
        self.day.dateChanged.connect(self._edited)
        self.timezone.textChanged.connect(self._edited)
        self.window_start.timeChanged.connect(self._edited)
        self.window_end.timeChanged.connect(self._edited)
        self.minimum_gap.valueChanged.connect(self._edited)
        self.today_button.clicked.connect(lambda: self._navigate(0))
        self.tomorrow_button.clicked.connect(lambda: self._navigate(1))
        self.preview_button.clicked.connect(self._prepare_preview)
        self.consent.toggled.connect(self._update_actions)
        self.run_button.clicked.connect(self._submit)
        self.refresh_card_button.clicked.connect(self._prepare_preview)
        self.cancel_button.clicked.connect(self._cancel)
        self.controller.changed.connect(self._render)
        self.controller.dialogue_changed.connect(self._dialogue_changed)
        self._render()

    def _context_key(self) -> tuple[object, ...]:
        grant = self.controller.grant
        return (
            self.controller.principal,
            self.controller.dialogue_chat_id,
            grant,
            self.controller.selected_calendars,
        )

    def _clear_preview(self) -> None:
        self._proposal = None
        self._preview_context = None
        self.preview.clear()
        self.consent.setChecked(False)

    def _edited(self) -> None:
        self._clear_preview()
        self.controller.clear_dialogue()
        self._render()

    def _dialogue_changed(self) -> None:
        # Includes normal-editor/lifecycle invalidation signalled by DesktopWindow's owner.
        self._clear_preview()
        self._render()

    def _navigate(self, offset: int) -> None:
        try:
            zone = ZoneInfo(require_timezone(self.timezone.text()))
            chosen = datetime.now(UTC).astimezone(zone).date() + timedelta(days=offset)
        except (ValueError, ZoneInfoNotFoundError, OverflowError):
            self._edited()
            self.status.setText(_INVALID_REQUEST)
            return
        # Clicking today's already-selected date must still invalidate a previous consent.
        self._edited()
        self.intent.setCurrentIndex(self.intent.findData("agenda"))
        self.day.setDate(QDate(chosen.year, chosen.month, chosen.day))
        self._prepare_preview()

    def _make_proposal(self) -> CalendarReadProposal:
        chat_id = self.controller.dialogue_chat_id
        if chat_id is None or self.controller.grant is None:
            raise ValueError("calendar_context_required")
        chosen = self.day.date()
        day = date(chosen.year(), chosen.month(), chosen.day())
        timezone = require_timezone(self.timezone.text())
        zone = ZoneInfo(timezone)
        kind: Literal["agenda", "unoccupied_intervals"] = (
            "agenda" if self.intent.currentData() == "agenda" else "unoccupied_intervals"
        )
        if kind == "agenda":
            start = _local_boundary(day, time.min, zone)
            end = _local_boundary(day + timedelta(days=1), time.min, zone)
        else:
            start_value, end_value = self.window_start.time(), self.window_end.time()
            start = _local_boundary(day, time(start_value.hour(), start_value.minute()), zone)
            end = _local_boundary(day, time(end_value.hour(), end_value.minute()), zone)
        return CalendarReadProposal(
            proposal_id=uuid4(),
            principal=self.controller.principal,
            chat_id=chat_id,
            turn_id=uuid4(),
            kind=kind,
            day=day,
            timezone=timezone,
            window=SourceInterval(starts_at=start, ends_at=end),
            minimum_minutes=self.minimum_gap.value(),
        )

    def _source_text(self) -> str:
        grant = self.controller.grant
        if grant is None:
            return "Разрешённых источников нет. Выбери календари во вкладке «Мой день»."
        by_source = {item.source: item for item in self.controller.selected_calendars}
        if any(source not in by_source for source in grant.resources):
            return "Выбранные источники недоступны. Обнови выбор во вкладке «Мой день»."
        return "Выбранные календари: " + "; ".join(
            f"{by_source[source].account_title} / {by_source[source].title}"
            for source in grant.resources
        )

    def _prepare_preview(self) -> None:
        self._clear_preview()
        self.controller.clear_dialogue()
        if not self.controller.status_projection.can_query_dialogue:
            self.status.setText(self.controller.status_projection.status_text)
            self._update_actions()
            return
        try:
            proposal = self._make_proposal()
        except (ValueError, ZoneInfoNotFoundError, OverflowError):
            self.status.setText(_INVALID_REQUEST)
            self._update_actions()
            return
        self._proposal = proposal
        self._preview_context = self._context_key()
        zone = ZoneInfo(proposal.timezone)
        window = (
            "весь день, 00:00 — 24:00"
            if proposal.kind == "agenda"
            else f"{proposal.window.starts_at.astimezone(zone):%H:%M} — "
            f"{proposal.window.ends_at.astimezone(zone):%H:%M}"
        )
        minimum = (
            "не применяется к списку событий"
            if proposal.kind == "agenda"
            else f"{proposal.minimum_minutes} мин."
        )
        query = "События за день" if proposal.kind == "agenda" else "Интервалы без событий"
        self.preview.setText(
            f"{query}; дата {proposal.day:%d.%m.%Y}; пояс {proposal.timezone}; "
            f"окно {window}; минимум: {minimum}.\n{self._source_text()}"
        )
        self.status.setText("Проверь точный запрос и разреши одно чтение для этой карточки.")
        self._update_actions()

    def _submit(self) -> None:
        proposal = self._proposal
        if (
            proposal is None
            or not self.consent.isChecked()
            or self._preview_context != self._context_key()
            or self.controller.busy
        ):
            self._clear_preview()
            self._update_actions()
            return
        # Consume the visible consent before dispatch; a retry always requires a new preview.
        self._clear_preview()
        self._update_actions()
        self.controller.submit_dialogue(proposal)
        self._render()

    def _cancel(self) -> None:
        self._clear_preview()
        self.controller.clear_dialogue()
        self._render()

    def _update_actions(self) -> None:
        current = self._context_key()
        available = self.controller.status_projection.can_query_dialogue
        preview_valid = self._proposal is not None and self._preview_context == current
        self.today_button.setEnabled(not self.controller.busy)
        self.tomorrow_button.setEnabled(not self.controller.busy)
        self.preview_button.setEnabled(available)
        self.refresh_card_button.setEnabled(available)
        self.consent.setEnabled(available and preview_valid)
        self.run_button.setEnabled(available and preview_valid and self.consent.isChecked())

    def _render(self) -> None:
        current = self._context_key()
        if self._context != current:
            self._clear_preview()
            self._context = current
        gaps = self.intent.currentData() == "unoccupied_intervals"
        for widget in (self.window_start, self.window_end, self.minimum_gap):
            widget.setEnabled(gaps)
        self.window_description.setText(
            "Укажи окно внутри одного дня и минимальную длительность интервала."
            if gaps
            else "Список событий: весь выбранный день, 00:00 — 24:00 в указанном поясе."
        )
        self.connection_summary.setText(self.controller.status_projection.status_text)
        self.sources.setText(self._source_text())
        self.status.setText(
            _STATUSES.get(
                self.controller.dialogue_status,
                "Запрос не выполнен. Для чтения нужны новая проверка запроса и согласие.",
            )
        )
        answer = self.controller.dialogue_answer
        self.answer.setPlainText(self._answer_text(answer) if answer is not None else "")
        self._update_actions()

    @staticmethod
    def _answer_text(answer: GroundedCalendarAnswer, *, agenda_first: bool = False) -> str:
        receipt, proposal = answer.receipt, answer.proposal
        zone = ZoneInfo(receipt.timezone)
        sources = "; ".join(f"{item.account_title} / {item.title}" for item in answer.calendars)
        names = {item.source: f"{item.account_title} / {item.title}" for item in answer.calendars}
        rows = [
            "Временная календарная карточка · macOS Calendar / EventKit",
            f"Дата: {proposal.day:%d.%m.%Y}. Пояс: {receipt.timezone}.",
            f"Окно: {proposal.window.starts_at.astimezone(zone):%d.%m %H:%M %z} — "
            f"{proposal.window.ends_at.astimezone(zone):%d.%m %H:%M %z}.",
            f"Источники: {sources or 'источник не предоставил данные'}.",
            "Покрытие: только выбранные календари. Синхронизация сервера не подтверждена.",
        ]
        if receipt.observed_at is not None and receipt.fresh_until is not None:
            rows.append(
                f"Локальный снимок: {receipt.observed_at.astimezone(zone):%d.%m.%Y %H:%M:%S %z}; "
                f"срок до {receipt.fresh_until.astimezone(zone):%d.%m.%Y %H:%M:%S %z}."
            )
        details_count = len(rows)
        if receipt.status not in {"ok", "empty", "partial"}:
            rows.append("Данные не получены. Отсутствие событий не установлено.")
        elif receipt.status == "partial" or answer.answer_kind == "incomplete":
            rows.append(
                "Ответ неполный. Интервалы без событий не определяются; "
                "отсутствие событий не установлено."
            )
            rows.extend(CalendarDialoguePanel._agenda_rows(answer.agenda, zone, names))
        elif answer.answer_kind == "unoccupied_intervals":
            rows.append(
                f"Интервалы без событий в выбранных календарях "
                f"(минимум {proposal.minimum_minutes} мин.). "
                "Они не подтверждают личную доступность."
            )
            rows.extend(
                f"{item.starts_at.astimezone(zone):%H:%M %z} — "
                f"{item.ends_at.astimezone(zone):%H:%M %z}"
                for item in answer.unoccupied
            )
            if not answer.unoccupied:
                rows.append("Интервалов такой длительности внутри указанного окна не найдено.")
        elif answer.answer_kind == "agenda":
            rows.extend(CalendarDialoguePanel._agenda_rows(answer.agenda, zone, names))
            if not answer.agenda:
                rows.append("Событий в выбранных календарях за это окно не найдено.")
        else:
            rows.append("Данные не получены. Отсутствие событий не установлено.")
        if agenda_first:
            rows = rows[details_count:] + rows[:details_count]
        return "\n\n".join(rows)

    @staticmethod
    def _agenda_rows(
        events: tuple[CalendarOccurrence, ...], zone: ZoneInfo, names: dict[CalendarSourceRef, str]
    ) -> list[str]:
        rows: list[str] = []
        for event in events:
            if event.all_day_start is not None and event.all_day_end is not None:
                last = event.all_day_end - timedelta(days=1)
                interval = f"Весь день: {event.all_day_start:%d.%m.%Y}"
                if last != event.all_day_start:
                    interval += f" — {last:%d.%m.%Y}"
                interval += f" ({event.timezone})"
            else:
                assert event.starts_at is not None
                assert event.ends_at is not None
                interval = (
                    f"{event.starts_at.astimezone(zone):%d.%m %H:%M %z} — "
                    f"{event.ends_at.astimezone(zone):%d.%m %H:%M %z}"
                )
            status = {"tentative": " · предварительно", "cancelled": " · отменено"}.get(
                event.status, ""
            )
            rows.append(
                f"{interval}{status}\n{event.title or '(Без названия)'}\n"
                f"{names.get(event.source, 'Выбранный календарь')}"
            )
        return rows
