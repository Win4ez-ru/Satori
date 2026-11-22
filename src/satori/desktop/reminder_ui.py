"""Exact-time local reminders with honest persistence and OS-delivery status."""

# ruff: noqa: RUF001 - intentional Russian display text

from datetime import UTC, datetime, time, timedelta
from datetime import date as Date
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from satori.application.assistant.schedule_contracts import local_instant
from satori.desktop.reminder_controller import ReminderController
from satori.desktop.reminder_delivery import NativeReminderDeliveryPort

_STATES = {"scheduled": "Запланировано", "due": "Пора выполнить", "cancelled": "Отменено"}
_DELIVERY = {
    "not_requested": "В приложении; OS-уведомление не запрошено",
    "submitting": "OS: ожидаю подтверждение",
    "scheduled": "OS: планирование подтверждено",
    "delivered": "OS: уведомление найдено среди доставленных",
    "cancelling": "OS: отмена ожидает подтверждения",
    "cancelled": "OS: отмена подтверждена",
    "absent": "OS: уведомление не найдено",
    "unknown": "OS: исход неизвестен, нужна проверка",
    "unavailable": "OS: уведомления недоступны",
}
_ERRORS = {
    "reminder_time_not_future": "Укажи будущее время напоминания.",
    "reminder_request_conflict": "Этот запрос связан с другими параметрами. Нужен новый запрос.",
    "reminder_clock_invalid": "Часы изменились назад. Проверь время и перезапусти раздел.",
    "reminder_store_in_use": "Напоминания уже открыты другим экземпляром Сатори.",
    "reminder_store_limit": "Достигнут предел локального хранилища напоминаний.",
    "reminder_delivery_reconcile_required": (
        "Не повторяю OS-запрос с неизвестным исходом. Сначала проверь состояние."
    ),
}


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class ReminderPanel(QWidget):
    def __init__(
        self,
        data_directory: Path,
        parent: QWidget | None = None,
        *,
        controller: ReminderController | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller or ReminderController(data_directory, self)
        self._notification_adapter: NativeReminderDeliveryPort | None = None
        layout = QVBoxLayout(self)
        heading = _label("Задачи и напоминания")
        heading.setObjectName("SettingsTitle")
        layout.addWidget(heading)
        self.delivery_note = _label(
            "Напоминания сохраняются локально и переживают перезапуск. Пока Сатори работает, "
            "она отмечает наступившие; после запуска показывает просроченные. "
            "При закрытом приложении или спящем Mac своевременность зависит от отдельно "
            "подтверждённого OS-уведомления."
        )
        layout.addWidget(self.delivery_note)
        self.notification_status = _label("macOS: разрешение уведомлений ещё не проверено.")
        layout.addWidget(self.notification_status)
        native_buttons = QHBoxLayout()
        self.authorize_notifications_button = QPushButton("Разрешить уведомления macOS…")
        self.authorize_notifications_button.setEnabled(False)
        self.authorize_notifications_button.clicked.connect(self._authorize_notifications)
        native_buttons.addWidget(self.authorize_notifications_button)
        self.check_notifications_button = QPushButton("Проверить разрешение macOS")
        self.check_notifications_button.setEnabled(False)
        self.check_notifications_button.clicked.connect(self._check_notifications)
        native_buttons.addWidget(self.check_notifications_button)
        layout.addLayout(native_buttons)
        self.notification_preview = QCheckBox("Показывать текст напоминания в OS-уведомлении")
        self.notification_preview.setChecked(False)
        self.notification_preview.toggled.connect(self._set_preview)
        layout.addWidget(self.notification_preview)
        self.enable_button = QPushButton("Включить локальные напоминания")
        self.enable_button.clicked.connect(self.controller.enable)
        layout.addWidget(self.enable_button)
        self.title = QLineEdit()
        self.title.setMaxLength(500)
        self.title.setPlaceholderText("Что нужно сделать?")
        self.title.setAccessibleName("Текст напоминания")
        layout.addWidget(self.title)
        row = QHBoxLayout()
        initial = datetime.now(UTC).astimezone(ZoneInfo("Europe/Moscow")) + timedelta(minutes=5)
        self.day = QDateEdit(QDate(initial.year, initial.month, initial.day))
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("dd.MM.yyyy")
        self.day.setAccessibleName("Дата напоминания")
        self.time = QTimeEdit(QTime(initial.hour, initial.minute))
        self.time.setDisplayFormat("HH:mm")
        self.time.setAccessibleName("Точное время напоминания")
        self.timezone = QLineEdit("Europe/Moscow")
        self.timezone.setMaxLength(128)
        self.timezone.setAccessibleName("Часовой пояс напоминания IANA")
        for widget in (self.day, self.time, self.timezone):
            row.addWidget(widget)
        layout.addLayout(row)
        self.create_button = QPushButton("Создать напоминание")
        self.create_button.clicked.connect(self._create)
        layout.addWidget(self.create_button)
        self.list = QListWidget()
        self.list.setAccessibleName("Сохранённые напоминания")
        layout.addWidget(self.list, 1)
        self.show_cancelled = QCheckBox("Показать отменённые")
        self.show_cancelled.toggled.connect(self._render)
        layout.addWidget(self.show_cancelled)
        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("Отменить выбранное")
        self.cancel_button.clicked.connect(self._cancel)
        self.reconcile_button = QPushButton("Проверить OS-уведомление")
        self.reconcile_button.clicked.connect(self._reconcile)
        self.schedule_notification_button = QPushButton("Запланировать OS-уведомление")
        self.schedule_notification_button.clicked.connect(self._schedule_notification)
        self.refresh_button = QPushButton("Обновить список")
        self.refresh_button.clicked.connect(self.controller.refresh)
        for button in (
            self.cancel_button,
            self.reconcile_button,
            self.schedule_notification_button,
            self.refresh_button,
        ):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status = _label()
        self.status.setAccessibleName("Состояние напоминаний")
        layout.addWidget(self.status)
        self.list.currentRowChanged.connect(self._selection_changed)
        self.controller.changed.connect(self._render)
        self._render()

    def set_notification_adapter(self, adapter: NativeReminderDeliveryPort) -> None:
        if self._notification_adapter is not None:
            self._notification_adapter.authorization_changed.disconnect(self._notification_state)
        self._notification_adapter = adapter
        adapter.authorization_changed.connect(self._notification_state)
        self.authorize_notifications_button.setEnabled(True)
        self.check_notifications_button.setEnabled(True)
        self._notification_state(adapter.status)

    def _authorize_notifications(self) -> None:
        if self._notification_adapter is not None:
            self._notification_adapter.authorize()

    def _check_notifications(self) -> None:
        if self._notification_adapter is not None:
            self._notification_adapter.refresh_authorization()

    def _notification_state(self, state: str) -> None:
        states = {
            "authorized": "macOS: уведомления разрешены; планирование проверяется отдельно.",
            "denied": "macOS: уведомления запрещены. Изменить можно в системных настройках.",
            "not_determined": "macOS: разрешение ещё не запрашивалось.",
            "unavailable": "macOS: помощник уведомлений недоступен.",
            "unknown": "macOS: состояние разрешения неизвестно.",
        }
        self.notification_status.setText(states.get(state, states["unknown"]))

    def _set_preview(self, checked: bool) -> None:
        self.controller.disclose_notification_title = checked

    def _selected(self) -> UUID | None:
        item = self.list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return value if isinstance(value, UUID) else None

    def _selection_changed(self, _row: int) -> None:
        selected = self._selected()
        record = next((r for r in self.controller.records if r.reminder_id == selected), None)
        self.cancel_button.setEnabled(record is not None and record.state != "cancelled")
        self.reconcile_button.setEnabled(
            record is not None and self.controller.notification_available
        )
        self.schedule_notification_button.setEnabled(
            record is not None
            and record.state == "scheduled"
            and record.delivery_state in {"not_requested", "unavailable", "absent"}
            and self.controller.notification_available
        )

    def _create(self) -> None:
        date, value = self.day.date(), self.time.time()
        try:
            due = local_instant(
                Date(date.year(), date.month(), date.day()),
                time(value.hour(), value.minute()),
                self.timezone.text(),
            ).astimezone(UTC)
        except ValueError:
            self.status.setText(
                "Проверь дату, точное время и часовой пояс. Неоднозначный час не выбран."
            )
            return
        if self.controller.create(self.title.text(), due, self.timezone.text()) is not None:
            self.title.clear()

    def _cancel(self) -> None:
        selected = self._selected()
        if selected is not None:
            self.controller.cancel(selected)

    def _reconcile(self) -> None:
        selected = self._selected()
        if selected is not None:
            self.controller.reconcile_delivery(selected)

    def _schedule_notification(self) -> None:
        selected = self._selected()
        if selected is not None:
            self.controller.request_delivery(selected)

    def _render(self) -> None:
        selected = self._selected()
        self.list.blockSignals(True)
        self.list.clear()
        for record in self.controller.records:
            if record.state == "cancelled" and not self.show_cancelled.isChecked():
                continue
            due = record.due_at.astimezone(ZoneInfo(record.timezone))
            item = QListWidgetItem(
                f"{record.title}\n{due:%d.%m.%Y %H:%M} · {record.timezone}"
                f" · {_STATES[record.state]}"
                f"\n{_DELIVERY[record.delivery_state]}",
                self.list,
            )
            item.setData(Qt.ItemDataRole.UserRole, record.reminder_id)
            if record.reminder_id == selected:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self.enable_button.setVisible(not self.controller.enabled)
        self.refresh_button.setEnabled(self.controller.enabled)
        self._selection_changed(self.list.currentRow())
        if self.controller.diagnostic is not None:
            self.status.setText(
                _ERRORS.get(
                    self.controller.diagnostic,
                    "Операция не завершена. Записи не заменены; проверь подключение раздела.",
                )
            )
        else:
            self.status.setText(
                {
                    "disabled": "Локальное хранилище ещё не включено.",
                    "created": "Напоминание сохранено. Состояние OS-уведомления показано отдельно.",
                    "existing": "Такое напоминание уже сохранено; повторная запись не создана.",
                    "cancelled": "Локально отменено. Состояние OS-отмены показано отдельно.",
                    "unchanged": "Запрос уже выполнен; повторного изменения нет.",
                    "due": "Есть наступившие напоминания.",
                }.get(self.controller.status, "Сохранённые напоминания загружены.")
            )
