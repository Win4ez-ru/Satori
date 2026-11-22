"""Plain-text My Day surface with explicit OS consent and session-scoped calendar selection."""

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PySide6.QtCore import QDate, QEvent, QObject, Qt, QTimer, QTimeZone, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from satori.application.assistant.contracts import CalendarSnapshot, CalendarSourceRef
from satori.desktop.calendar_controller import CalendarController
from satori.desktop.checklist_ui import CheckListWidget
from satori.desktop.schedule_ui import ScheduleImportPanel
from satori.desktop.source_status import SourceStatus

_STATUSES = {
    "disconnected": "Календарь не подключён. Чтение не выполняется.",
    "connecting": "Проверяю доступ и список календарей. События ещё не читаются.",
    "requesting_access": "Разреши доступ в системном окне macOS. Можно отключить календарь здесь.",
    "select_calendars": "Отметь нужные календари и нажми «Использовать выбранные».",
    "catalog_empty": "Доступ разрешён, но доступных календарей не найдено.",
    "grant_ready": "Чтение выбранных календарей разрешено на час в этой сессии.",
    "reading": "Читаю только выбранные календари. Можно отключить доступ.",
    "ready": "События получены. Это временный снимок выбранных календарей.",
    "empty": "Событий за этот день в выбранных календарях не найдено.",
    "partial": (
        "Ответ неполный: достигнут лимит событий или размера; длинный текст мог быть сокращён."
    ),
    "grant_required": "Сначала разреши чтение выбранных календарей.",
    "grant_expired": "Часовое разрешение истекло. Данные убраны; выбери календари заново.",
    "snapshot_expired": "Данные старше пяти минут убраны. Для нового снимка нажми «Обновить».",
    "catalog_expired": "Список календарей устарел. Нажми «Подключить календарь» ещё раз.",
    "selection_invalid": "Выбери хотя бы один календарь из актуального списка.",
    "permission_not_determined": "Системный доступ не предоставлен. Подключи календарь явно.",
    "permission_denied": (
        "macOS не разрешила полный доступ. Проверь разрешение календарей в настройках системы."
    ),
    "permission_restricted": "Доступ к календарям ограничен системой.",
    "provider_unavailable": "Календарный компонент недоступен. События не загружены.",
    "provider_error": "Чтение календаря не удалось. Данные убраны; можно подключить заново.",
    "request_expired": "Чтение остановлено по времени. Данные убраны; повтори подключение.",
    "result_too_large": "Ответ превышает допустимый размер и не показан.",
    "catalog_too_large": "Доступно больше 100 календарей; этот список пока не поддерживается.",
    "timezone_invalid": "Введи часовой пояс IANA, например Europe/Moscow.",
}
_ERROR_TEXT = "Календарный ответ не прошёл проверку. Данные не показаны; подключи календарь заново."


def _plain_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _system_timezone() -> str:
    try:
        value = bytes(QTimeZone.systemTimeZoneId().data()).decode("utf-8")
        ZoneInfo(value)
        return value
    except (UnicodeError, ZoneInfoNotFoundError, ValueError):
        return "UTC"


class CalendarPanel(QWidget):
    """Selected-calendar reads and separately reviewed schedule creation outside Core."""

    shutdown_finished = Signal()

    def __init__(
        self,
        data_directory: Path,
        parent: QWidget | None = None,
        *,
        controller: CalendarController | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller or CalendarController(data_directory, self)
        self._catalog: tuple[object, ...] = ()
        self._observed_window: QWidget | None = None
        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        self.read_page = QScrollArea()
        self.read_page.setWidgetResizable(True)
        read_content = QWidget()
        self.read_page.setWidget(read_content)
        self.tabs.addTab(self.read_page, "Мой день")
        layout = QVBoxLayout(read_content)
        self.disclosure = _plain_label(
            "macOS запрашивает полный доступ ко всем календарям: чтение и изменение. "
            "Сатори использует только чтение выбранных календарей на срок до часа; "
            "создание смен разрешается отдельно во вкладке «Импорт смен», после предпросмотра и "
            "одноразового подтверждения. Отключение здесь отзывает разрешение Сатори, "
            "но системный доступ macOS остаётся до отзыва в настройках системы. "
            "События не отправляются модели и не сохраняются в память или историю чата."
        )
        layout.addWidget(self.disclosure)
        actions = QHBoxLayout()
        self.connect_button = QPushButton("Подключить календарь")
        self.disconnect_button = QPushButton("Отключить")
        actions.addWidget(self.connect_button)
        actions.addWidget(self.disconnect_button)
        layout.addLayout(actions)
        self.calendars = CheckListWidget()
        self.calendars.setAccessibleName("Календари для разрешённого чтения")
        self.calendars.setMaximumHeight(150)
        layout.addWidget(self.calendars)
        self.use_button = QPushButton("Использовать выбранные")
        self.use_button.setToolTip(
            "Разрешить чтение этих календарей на час и показать выбранный день"
        )
        layout.addWidget(self.use_button)
        date_actions = QHBoxLayout()
        self.today_button = QPushButton("Сегодня")
        self.tomorrow_button = QPushButton("Завтра")
        self.day = QDateEdit(QDate.currentDate())
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("dd.MM.yyyy")
        self.day.setAccessibleName("День календаря")
        date_actions.addWidget(self.today_button)
        date_actions.addWidget(self.tomorrow_button)
        date_actions.addWidget(self.day)
        layout.addLayout(date_actions)
        zone_actions = QHBoxLayout()
        zone_actions.addWidget(_plain_label("Часовой пояс (IANA):"))
        self.timezone = QLineEdit(_system_timezone())
        self.timezone.setMaxLength(128)
        self.timezone.setAccessibleName("Часовой пояс IANA")
        self.refresh_button = QPushButton("Обновить")
        zone_actions.addWidget(self.timezone)
        zone_actions.addWidget(self.refresh_button)
        layout.addLayout(zone_actions)
        self.auto_refresh = QCheckBox("Обновлять раз в минуту, пока открыт «Мой день»")
        self.auto_refresh.setToolTip(
            "Только в пределах текущего разрешения на чтение; скрытие вкладки останавливает опрос"
        )
        layout.addWidget(self.auto_refresh)
        self.auto_refresh_timer = QTimer(self)
        self.auto_refresh_timer.setInterval(60_000)
        self.auto_refresh_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.auto_refresh_timer.timeout.connect(self._auto_refresh)
        self._visibility_update = QTimer(self)
        self._visibility_update.setSingleShot(True)
        self._visibility_update.timeout.connect(self._update_auto_refresh)
        self.connection_summary = _plain_label()
        self.connection_summary.setAccessibleName("Состояние подключения календаря")
        self.status = _plain_label()
        self.permission = _plain_label()
        self.selection = _plain_label()
        self.last_read = _plain_label()
        self.synchronization = _plain_label(
            "Источник — локальный EventKit macOS. Свежий локальный снимок не подтверждает "
            "обновление iCloud или доставку на iPhone; удалённая синхронизация не проверена."
        )
        self.source = _plain_label()
        self.freshness = _plain_label()
        self.grant = _plain_label()
        for label in (
            self.connection_summary,
            self.status,
            self.permission,
            self.selection,
            self.last_read,
            self.source,
            self.freshness,
            self.grant,
            self.synchronization,
        ):
            layout.addWidget(label)
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setAccessibleName("События выбранных календарей")
        layout.addWidget(self.events)
        self.schedule_panel = ScheduleImportPanel(self.controller, self)
        self.tabs.addTab(self.schedule_panel, "Импорт смен")
        self.connect_button.clicked.connect(self.controller.connect_calendar)
        self.disconnect_button.clicked.connect(self.controller.disconnect_calendar)
        self.use_button.clicked.connect(self._use)
        self.calendars.itemChanged.connect(self._selection_changed)
        self.today_button.clicked.connect(lambda: self._navigate(0))
        self.tomorrow_button.clicked.connect(lambda: self._navigate(1))
        self.refresh_button.clicked.connect(self._refresh)
        self.auto_refresh.toggled.connect(self._update_auto_refresh)
        self.tabs.currentChanged.connect(self._update_auto_refresh)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.applicationStateChanged.connect(self._update_auto_refresh)
        self.controller.changed.connect(self._render)
        self.controller.shutdown_finished.connect(self.shutdown_finished.emit)
        self._render()

    @property
    def shutdown_complete(self) -> bool:
        return self.controller.shutdown_complete

    @property
    def source_status(self) -> SourceStatus:
        return self.controller.source_status

    def set_profile(self, data_directory: Path) -> None:
        self.auto_refresh.setChecked(False)
        self.controller.set_profile(data_directory)

    def _update_auto_refresh(self) -> None:
        allowed = (
            self.auto_refresh.isChecked()
            and self.isVisible()
            and self._foreground()
            and self.tabs.currentWidget() is self.read_page
            and self.controller.grant is not None
        )
        if allowed and not self.auto_refresh_timer.isActive():
            self.auto_refresh_timer.start()
        elif not allowed:
            self.auto_refresh_timer.stop()

    def _foreground(self) -> bool:
        window = self.window()
        return (
            not window.isMinimized()
            and window.isActiveWindow()
            and QApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        )

    def _auto_refresh(self) -> None:
        self._update_auto_refresh()
        if self.auto_refresh_timer.isActive() and not self.controller.busy:
            self._refresh()

    def _selection_changed(self, item: QListWidgetItem) -> None:
        del item
        self.controller.selection_changed()

    def _selected(self) -> tuple[CalendarSourceRef, ...]:
        selected: list[CalendarSourceRef] = []
        for index in range(self.calendars.count()):
            item = self.calendars.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                source = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(source, CalendarSourceRef):
                    selected.append(source)
        return tuple(selected)

    def _use(self) -> None:
        if self.controller.use_calendars(self._selected()):
            self._refresh()

    def _navigate(self, offset: int) -> None:
        # Derive today in the displayed zone, not the machine's possibly different date.
        try:
            current = datetime.now(ZoneInfo(self.timezone.text())).date() + timedelta(days=offset)
        except (ValueError, ZoneInfoNotFoundError):
            self.status.setText(_STATUSES["timezone_invalid"])
            return
        self.day.setDate(QDate(current.year, current.month, current.day))
        self._refresh()

    def _refresh(self) -> None:
        chosen = self.day.date()
        self.controller.read_day(
            date(chosen.year(), chosen.month(), chosen.day()), self.timezone.text()
        )

    def _render(self) -> None:
        catalog = self.controller.catalog
        if catalog != self._catalog:
            self.calendars.blockSignals(True)
            self.calendars.clear()
            for descriptor in catalog:
                item = QListWidgetItem(f"{descriptor.account_title} — {descriptor.title}")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, descriptor.source)
                self.calendars.addItem(item)
            self.calendars.blockSignals(False)
            self._catalog = catalog
        snapshot = self.controller.snapshot
        grant = self.controller.grant
        busy = self.controller.busy
        self.connect_button.setEnabled(not busy)
        self.disconnect_button.setEnabled(busy or grant is not None or bool(catalog))
        self.use_button.setEnabled(bool(catalog) and not busy and bool(self._selected()))
        self.refresh_button.setEnabled(grant is not None and not busy)
        self.today_button.setEnabled(grant is not None and not busy)
        self.tomorrow_button.setEnabled(grant is not None and not busy)
        self.auto_refresh.setEnabled(grant is not None)
        self._update_auto_refresh()
        self.connection_summary.setText(self.controller.status_projection.status_text)
        self.status.setText(_STATUSES.get(self.controller.status, _ERROR_TEXT))
        access = {
            "full_access": "Полный доступ был подтверждён macOS при последней проверке.",
            "not_determined": "Системное разрешение ещё не предоставлено.",
            "write_only": (
                "macOS разрешает только добавление; чтение и проверка дубликатов недоступны."
            ),
            "denied": "Системный доступ запрещён.",
            "restricted": "Системный доступ ограничен.",
        }.get(self.controller.permission_status, "Системное разрешение в этой сессии не проверено.")
        self.permission.setText("Разрешение macOS: " + access)
        selected_names = "; ".join(
            f"{item.account_title} / {item.title}" for item in self.controller.selected_calendars
        )
        self.selection.setText(
            f"Отмечено для чтения: {len(self._selected())}; "
            f"в действующем разрешении: {len(grant.resources) if grant else 0}."
            + ("\n" + f"Выбранные календари: {selected_names}." if selected_names else "")
        )
        observed = self.controller.last_successful_read_at
        zone_name = self.timezone.text() if self._valid_zone() else "UTC"
        self.last_read.setText(
            "Последнее успешное локальное чтение: "
            + (
                observed.astimezone(ZoneInfo(zone_name)).strftime("%d.%m.%Y %H:%M:%S")
                + f" ({zone_name})."
                if observed is not None
                else "в этой сессии ещё не выполнялось."
            )
        )
        if grant is not None:
            zone_name = self.timezone.text() if self._valid_zone() else "UTC"
            until = grant.expires_at.astimezone(ZoneInfo(zone_name)).strftime("%H:%M:%S")
            self.grant.setText(f"Чтение выбранных календарей разрешено до {until} ({zone_name}).")
        else:
            self.grant.setText("Разрешение на чтение не действует.")
        if snapshot is None:
            self.source.clear()
            self.freshness.clear()
            self.events.clear()
            return
        zone = ZoneInfo(snapshot.request.timezone)
        self.source.setText(
            "Источники: "
            + "; ".join(f"{item.account_title} / {item.title}" for item in snapshot.calendars)
        )
        self.freshness.setText(
            f"Снимок: {snapshot.observed_at.astimezone(zone).strftime('%d.%m.%Y %H:%M:%S')} "
            f"({snapshot.request.timezone}). Действителен до "
            f"{snapshot.expires_at.astimezone(zone).strftime('%H:%M:%S')}."
        )
        self.events.setPlainText(self._event_text(snapshot))

    def _valid_zone(self) -> bool:
        try:
            ZoneInfo(self.timezone.text())
            return True
        except (ZoneInfoNotFoundError, ValueError):
            return False

    @staticmethod
    def _event_text(snapshot: CalendarSnapshot) -> str:
        titles = {
            item.source: f"{item.account_title} / {item.title}" for item in snapshot.calendars
        }
        zone = ZoneInfo(snapshot.request.timezone)
        rows: list[str] = []
        for event in snapshot.occurrences:
            if event.all_day_start is not None and event.all_day_end is not None:
                last_day = event.all_day_end - timedelta(days=1)
                interval = event.all_day_start.strftime("%d.%m.%Y")
                if last_day != event.all_day_start:
                    interval += " — " + last_day.strftime("%d.%m.%Y")
                when = f"Весь день: {interval} ({event.timezone})"
            else:
                assert event.starts_at is not None
                assert event.ends_at is not None
                when = (
                    f"{event.starts_at.astimezone(zone).strftime('%d.%m %H:%M %z')} — "
                    f"{event.ends_at.astimezone(zone).strftime('%d.%m %H:%M %z')}"
                )
            status = " · предварительно" if event.status == "tentative" else ""
            rows.append(
                f"{when}{status}\n{event.title or '(Без названия)'}\n{titles[event.source]}"
            )
        if not rows:
            return (
                "Получена только часть данных; событий в этой части нет."
                if snapshot.status == "partial"
                else "Событий за выбранный день нет."
            )
        return "\n\n".join(rows)

    def shutdown(self) -> None:
        self.auto_refresh_timer.stop()
        self._visibility_update.stop()
        self.controller.close()

    def hideEvent(self, event: QHideEvent) -> None:
        self.auto_refresh_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        window = self.window()
        if window is not self._observed_window:
            if self._observed_window is not None:
                self._observed_window.removeEventFilter(self)
            self._observed_window = window
            window.installEventFilter(self)
        self._update_auto_refresh()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._observed_window and event.type() in {
            QEvent.Type.ActivationChange,
            QEvent.Type.WindowStateChange,
        }:
            self._visibility_update.start(0)
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)
