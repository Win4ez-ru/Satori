"""Plain-text transient Calendar card embedded in the ordinary conversation page."""

# ruff: noqa: RUF001 - intentional Russian user-facing text.

from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from satori.application.assistant.calendar_chat_fields import CalendarChatFields
from satori.application.assistant.contracts import CalendarSourceRef
from satori.desktop.calendar_chat_controller import CalendarChatController


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class CalendarConversationPanel(QFrame):
    """One transient conversation, with no configuration form or exact-task reapproval."""

    settings_requested = Signal()

    def __init__(self, controller: CalendarChatController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("CalendarConversation")
        layout = QVBoxLayout(self)
        self.draft = _label()
        self.draft.setAccessibleName("Твоё поручение календарю")
        self.status = _label()
        self.status.setAccessibleName("Ответ Сатори о календаре")
        layout.addWidget(self.draft)
        layout.addWidget(self.status)
        self.connect_button = QPushButton("Подключить календарь")
        self.connect_button.clicked.connect(self.settings_requested)
        layout.addWidget(self.connect_button)
        self.disclosure = QCheckBox(
            "Разрешаю передать выбранные события модели для подбора времени"
        )
        self.disclose_button = QPushButton("Подобрать время")
        self.disclose_button.clicked.connect(controller.disclose)
        self.disclosure.toggled.connect(self.refresh)
        layout.addWidget(self.disclosure)
        layout.addWidget(self.disclose_button)
        self.create_button = QPushButton("Добавить предложенное время")
        self.create_button.clicked.connect(controller.create_reviewed)
        layout.addWidget(self.create_button)
        self.reconcile_button = QPushButton("Проверить результат")
        self.reconcile_button.clicked.connect(controller.reconcile)
        layout.addWidget(self.reconcile_button)
        controller.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        card = self.controller.card
        self.setVisible(card is not None)
        if card is None:
            self.draft.clear()
            self.status.clear()
            self.disclosure.setChecked(False)
            return
        self.draft.setText("Ты: " + card.draft)
        self.status.setText("Сатори: " + card.message)
        self.connect_button.setVisible(card.status == "connection_required")
        planning = card.status == "disclosure_review"
        self.disclosure.setVisible(planning)
        self.disclose_button.setVisible(planning)
        self.disclose_button.setEnabled(not self.controller.busy and self.disclosure.isChecked())
        self.create_button.setVisible(
            card.status == "proposed" and self.controller.conversation_authority is None
        )
        self.create_button.setEnabled(not self.controller.busy)
        self.reconcile_button.setVisible(card.status == "unknown")
        self.reconcile_button.setEnabled(
            not self.controller.busy and self.controller.active_request_id is not None
        )

    def hideEvent(self, event: QHideEvent) -> None:
        if self.controller.card is not None:
            self.controller.suspend()
        super().hideEvent(event)


class CalendarChatPanel(QFrame):
    """Visible read/disclosure/exact-effect choices; no canonical chat or TTS."""

    def __init__(self, controller: CalendarChatController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._shown_request: str | None = None
        self._shown_proposal: str | None = None
        self._resources: tuple[CalendarSourceRef, ...] = ()
        self._shown_fields: CalendarChatFields | None = None
        self._form_draft: str | None = None
        self._form_dirty = False
        self._refreshing = False
        self.setObjectName("CalendarChatCard")
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(_label("Календарное поручение"), 1)
        self.clear_button = QPushButton("Убрать")
        self.clear_button.clicked.connect(controller.clear)
        header.addWidget(self.clear_button)
        layout.addLayout(header)
        self.draft = _label()
        self.draft.setAccessibleName("Текст календарного поручения")
        layout.addWidget(self.draft)
        target = QHBoxLayout()
        target.addWidget(_label("Добавить в:"))
        self.calendar = QComboBox()
        self.calendar.setAccessibleName("Календарь назначения для одного события")
        self.calendar.activated.connect(self._choose_calendar)
        target.addWidget(self.calendar, 1)
        target.addWidget(_label("Часовой пояс:"))
        self.timezone = QLineEdit("Europe/Moscow")
        self.timezone.setMaxLength(128)
        self.timezone.setAccessibleName("Часовой пояс календарного поручения IANA")
        target.addWidget(self.timezone)
        layout.addLayout(target)
        self.requested_calendar = _label()
        layout.addWidget(self.requested_calendar)
        self.fields_form = QWidget()
        form = QGridLayout(self.fields_form)
        form.setContentsMargins(0, 0, 0, 0)
        self.title_input = QLineEdit()
        self.title_input.setMaxLength(500)
        self.title_input.setAccessibleName("Название одного события")
        self.day_input = QLineEdit()
        self.day_input.setMaxLength(32)
        self.day_input.setPlaceholderText("YYYY-MM-DD или завтра")
        self.day_input.setAccessibleName("День календарного поручения")
        self.start_input = QLineEdit()
        self.start_input.setMaxLength(5)
        self.start_input.setPlaceholderText("HH:MM")
        self.start_input.setAccessibleName("Начало события или окна поиска")
        self.end_input = QLineEdit()
        self.end_input.setMaxLength(5)
        self.end_input.setPlaceholderText("необязательно")
        self.end_input.setAccessibleName("Конец события или окна поиска")
        self.duration_input = QLineEdit()
        self.duration_input.setMaxLength(4)
        self.duration_input.setPlaceholderText("не указана")
        self.duration_input.setAccessibleName("Длительность события в минутах")
        form.addWidget(_label("Название:"), 0, 0)
        form.addWidget(self.title_input, 0, 1, 1, 7)
        for column, (name, widget) in enumerate(
            (
                ("День:", self.day_input),
                ("Начало:", self.start_input),
                ("Конец:", self.end_input),
                ("Минут:", self.duration_input),
            )
        ):
            form.addWidget(_label(name), 1, column * 2)
            form.addWidget(widget, 1, column * 2 + 1)
        self.apply_fields_button = QPushButton("Применить уточнённые поля")
        self.replace_unsupported = QCheckBox(
            "Заменить неоднозначное поручение одним обычным событием по этим полям"
        )
        self.replace_unsupported.setAccessibleName(
            "Явно заменить неподдерживаемое поручение одним обычным событием"
        )
        form.addWidget(self.replace_unsupported, 2, 0, 1, 8)
        self.apply_fields_button.clicked.connect(self._apply_fields)
        form.addWidget(self.apply_fields_button, 3, 0, 1, 8)
        for widget in (
            self.title_input,
            self.day_input,
            self.start_input,
            self.end_input,
            self.duration_input,
        ):
            widget.textChanged.connect(self._fields_changed)
        layout.addWidget(self.fields_form)
        self.preview_button = QPushButton("Прочитать выбранные календари")
        self.preview_button.clicked.connect(self._preview)
        layout.addWidget(self.preview_button)
        self.status = _label()
        self.status.setAccessibleName("Состояние календарного поручения")
        layout.addWidget(self.status)
        self.disclosure = QCheckBox("Разрешаю одну передачу перечисленных данных этой модели")
        self.disclosure.setAccessibleName("Разрешение передачи календарного контекста модели")
        self.disclosure.toggled.connect(self._buttons)
        layout.addWidget(self.disclosure)
        self.disclose_button = QPushButton("Подобрать время с Сатори")
        self.disclose_button.clicked.connect(controller.disclose)
        layout.addWidget(self.disclose_button)
        self.review = QPlainTextEdit()
        self.review.setReadOnly(True)
        self.review.setAccessibleName("Точные детали предлагаемого события")
        self.review.setMaximumHeight(170)
        layout.addWidget(self.review)
        self.confirm = QCheckBox("Проверил название, календарь, дату и время — создать это событие")
        self.confirm.toggled.connect(self._buttons)
        layout.addWidget(self.confirm)
        action = QHBoxLayout()
        self.create_button = QPushButton("Создать это событие")
        self.create_button.clicked.connect(controller.create_reviewed)
        action.addWidget(self.create_button)
        self.reconcile_button = QPushButton("Проверить результат без повторного создания")
        self.reconcile_button.clicked.connect(controller.reconcile)
        action.addWidget(self.reconcile_button)
        layout.addLayout(action)
        self.boundary = _label(
            "Карточка временная: её данные не попадают в историю разговора, память или озвучку. "
            "Передача модели и создание разрешаются отдельно. Скрытие карточки отзывает её доступ."
        )
        layout.addWidget(self.boundary)
        controller.changed.connect(self.refresh)
        self.refresh()

    def _preview(self) -> None:
        index = self.calendar.currentIndex()
        if not self._form_dirty and 0 <= index < len(self._resources):
            self.controller.request_preview(self._resources[index], self.timezone.text().strip())

    def _choose_calendar(self, index: int) -> None:
        if 0 <= index < len(self._resources):
            self.controller.choose_calendar(self._resources[index])

    def _fields_changed(self) -> None:
        if not self._refreshing:
            self._form_dirty = True
            self._buttons()

    def _apply_fields(self) -> None:
        widgets = (
            self.title_input,
            self.day_input,
            self.start_input,
            self.end_input,
            self.duration_input,
        )
        values = tuple(widget.text() for widget in widgets)
        try:
            duration = values[4].strip()
            if duration and (not duration.isascii() or not duration.isdigit()):
                raise ValueError("calendar_duration_invalid")
            fields = CalendarChatFields(
                title=values[0].strip(),
                day=values[1].strip(),
                starts_at=values[2].strip(),
                ends_at=values[3].strip(),
                duration_minutes=int(duration) if duration else None,
                mode=self.controller.fields.mode,
                requested_calendar_title=self.controller.requested_calendar_title,
            )
            if fields.duration_minutes is not None and fields.duration_minutes < 5:
                raise ValueError("calendar_duration_invalid")
        except ValueError:
            self.controller.reject_fields()
            self._refreshing = True
            for widget, value in zip(widgets, values, strict=True):
                widget.setText(value)
            self._refreshing = False
            self._form_dirty = True
            self._buttons()
            return
        self._form_dirty = False
        self.controller.set_fields(fields, replace_unsupported=self.replace_unsupported.isChecked())

    def _buttons(self) -> None:
        card = self.controller.card
        if card is None:
            for button in (
                self.preview_button,
                self.disclose_button,
                self.create_button,
                self.reconcile_button,
            ):
                button.setEnabled(False)
            return
        idle = not self.controller.busy
        self.preview_button.setEnabled(
            idle
            and card.status in {"draft", "clarification", "unavailable"}
            and bool(self._resources)
            and self.calendar.currentIndex() >= 0
            and not self._form_dirty
        )
        self.disclose_button.setEnabled(
            idle and card.status == "disclosure_review" and self.disclosure.isChecked()
        )
        self.create_button.setEnabled(
            idle and card.status == "proposed" and self.confirm.isChecked()
        )
        self.reconcile_button.setEnabled(
            idle and card.status in {"unknown", "created", "reconciled_present"}
        )

    def refresh(self) -> None:
        card = self.controller.card
        self.setVisible(card is not None)
        if card is None:
            self.draft.clear()
            self.status.clear()
            self.review.clear()
            self.disclosure.setChecked(False)
            self.confirm.setChecked(False)
            self._shown_request, self._shown_proposal = None, None
            self._shown_fields, self._form_draft = None, None
            self._form_dirty = False
            self.replace_unsupported.setChecked(False)
            return
        descriptors = self.controller.writable_calendars
        resources = tuple(item.source for item in descriptors)
        if resources != self._resources:
            self._resources = resources
            self.calendar.clear()
            for item in descriptors:
                self.calendar.addItem(f"{item.account_title} / {item.title}", item.source)
        preferred = self.controller.preferred_calendar
        self.calendar.setCurrentIndex(resources.index(preferred) if preferred in resources else -1)
        hint = self.controller.requested_calendar_title
        self.requested_calendar.setVisible(hint is not None)
        self.requested_calendar.setText(
            "Запрошен календарь: " + hint + ". Другой календарь можно выбрать явно." if hint else ""
        )
        self.replace_unsupported.setVisible(self.controller.fields.needs_clarification)
        if self._form_draft != card.draft or (
            not self._form_dirty and self._shown_fields != self.controller.fields
        ):
            self._refreshing = True
            fields = self.controller.fields
            for widget, value in (
                (self.title_input, fields.title),
                (self.day_input, fields.day),
                (self.start_input, fields.starts_at),
                (self.end_input, fields.ends_at),
                (
                    self.duration_input,
                    str(fields.duration_minutes) if fields.duration_minutes is not None else "",
                ),
            ):
                widget.setText(value)
            self._shown_fields, self._form_draft = fields, card.draft
            self._form_dirty = False
            self._refreshing = False
        active = self.controller.active_request_id
        if active != self._shown_request:
            self.disclosure.setChecked(False)
            self.confirm.setChecked(False)
            self._shown_request = active
        proposal = card.proposal
        proposal_id = str(proposal.proposal_id) if proposal is not None else None
        if proposal_id != self._shown_proposal:
            self.confirm.setChecked(False)
            self._shown_proposal = proposal_id
        self.draft.setText(card.draft)
        self.status.setText(("Выполняю…\n" if self.controller.busy else "") + card.message)
        if card.provider:
            self.status.setText(self.status.text() + f"\nМодель: {card.provider}")
        show_disclosure = card.status == "disclosure_review"
        self.disclosure.setVisible(show_disclosure)
        self.disclose_button.setVisible(show_disclosure)
        self.review.setVisible(proposal is not None)
        if proposal is not None:
            command = proposal.command
            zone = ZoneInfo(command.timezone)
            self.review.setPlainText(
                f"Название: {command.title}\n"
                f"Календарь: {proposal.account_title} / {proposal.calendar_title}\n"
                f"Начало: {command.starts_at.astimezone(zone):%d.%m.%Y %H:%M}\n"
                f"Конец: {command.ends_at.astimezone(zone):%d.%m.%Y %H:%M}\n"
                f"Часовой пояс: {command.timezone}"
            )
        else:
            self.review.clear()
        self.confirm.setVisible(card.status == "proposed")
        self.create_button.setVisible(card.status == "proposed")
        editable = (
            card.status in {"draft", "clarification", "unavailable"} and not self.controller.busy
        )
        self.calendar.setEnabled(editable)
        self.timezone.setEnabled(editable)
        self.fields_form.setVisible(editable)
        self.fields_form.setEnabled(editable)
        self.preview_button.setVisible(card.status in {"draft", "clarification", "unavailable"})
        self.reconcile_button.setVisible(
            card.status in {"unknown", "created", "reconciled_present"}
        )
        self._buttons()

    def hideEvent(self, event: QHideEvent) -> None:
        if self.controller.card is not None:
            self.controller.clear()
        super().hideEvent(event)
