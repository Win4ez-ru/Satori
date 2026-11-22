"""Transient Telegram surfaces and owned native login; no conversation route."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

# ruff: noqa: RUF001 - intentional Russian user-facing text
from uuid import UUID, uuid4

from pydantic import ValidationError
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from satori.desktop.source_status import SourceStatus
from satori.desktop.telegram_contracts import (
    TelegramChat,
    TelegramPage,
    TelegramRow,
    TelegramViewport,
)
from satori.desktop.telegram_controller import TelegramController
from satori.desktop.telegram_page_search import (
    MAX_QUERY_CHARS,
    TelegramPageSearchQuery,
    TelegramPageSearchResult,
)
from satori.desktop.telegram_reply_ui import TelegramReplyPanel
from satori.desktop.telegram_runtime import TelegramRuntimeController

_UNAVAILABLE = "Подключение Telegram в этой сборке ещё недоступно."
_STATUSES = {
    "runtime_unavailable": _UNAVAILABLE,
    "disconnected": "Telegram отключён. Вход запускается только по твоему решению.",
    "connecting": "Вход и подтверждение аккаунта выполняются в отдельном защищённом окне.",
    "connected": "Аккаунт подтверждён. Список чатов загружается отдельно.",
    "listing": "Загружаю ограниченный список чатов.",
    "choose_chat": "Выдели чат и отдельно нажми «Выбрать чат».",
    "selecting": "Проверяю выбранный чат.",
    "selected": "Чат выбран. Для показа текста нажми «Прочитать выбранный».",
    "reading": "Читаю ограниченную текстовую страницу выбранного чата.",
    "ready": "Текст показан локально и временно. Скрытие панели убирает его.",
    "source_changed": "Источник изменился. Выбери чат и подтверди чтение заново.",
    "catalog_expired": "Список устарел. Загрузи его заново вручную.",
    "selection_expired": "Выбор истёк. Выбери чат заново.",
    "page_expired": "Страница устарела и убрана. Для нового снимка нажми чтение.",
    "expired": "Срок подключения истёк. Подключись заново вручную.",
    "cancelled": "Операция отменена, локальный текст убран.",
    "shutting_down": "Закрываю локальное подключение Telegram.",
    "source_rejected": "Ответ не прошёл проверку. Текст убран, повторного чтения нет.",
    "auth_failed": "Вход не подтверждён. Текст не показан.",
    "runtime_failed": "Подключение остановлено. Текст убран, автоматического повтора нет.",
    "timeout": "Время ожидания истекло. Автоматического повтора нет.",
}


def _plain(text: str = "") -> QLabel:
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setOpenExternalLinks(False)
    label.setWordWrap(True)
    label.setText(text)
    return label


def _viewport_frame(
    page_id: UUID, viewport: QRectF, bodies: list[tuple[UUID, QRectF]], scale: float
) -> TelegramViewport:
    """Quantize measured full rectangles, then translate every endpoint together."""
    if not math.isfinite(scale) or scale <= 0 or viewport.height() <= 0:
        raise ValueError("telegram_geometry_invalid")
    first = math.floor(viewport.top() * scale)
    after = math.ceil((viewport.top() + viewport.height()) * scale)
    rows = [
        (handle, math.floor(rect.top() * scale), math.ceil((rect.top() + rect.height()) * scale))
        for handle, rect in bodies
        if rect.width() > 0
        and rect.height() > 0
        and rect.intersected(viewport).height() * scale >= 1
        and rect.intersected(viewport).width() > 0
    ]
    origin = min([first, *(start for _, start, _ in rows)])
    return TelegramViewport(
        page_id=page_id,
        foreground=True,
        first=first - origin,
        after=after - origin,
        rows=tuple(
            TelegramRow(handle=handle, first=start - origin, after=end - origin)
            for handle, start, end in rows
        ),
    )


class TelegramPanel(QWidget):
    """Root-admitted controller projection; the UI cannot grant runtime admission."""

    shutdown_finished = Signal()

    def __init__(
        self,
        data_directory: Path,
        parent: QWidget | None = None,
        *,
        controller: TelegramController | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = (
            controller if controller is not None else TelegramController(data_directory, self)
        )
        self._principal = self.controller.principal
        self._allow_page = False
        self._page: TelegramPage | None = None
        self._catalog: tuple[TelegramChat, ...] = ()
        self._bodies: list[tuple[UUID, QLabel]] = []
        self._observed_window: QWidget | None = None
        layout = QVBoxLayout(self)
        self.disclosure = _plain(
            "Отдельный пользовательский клиент Telegram получает доступ к данным аккаунта шире "
            "выбранного чата и может синхронизировать их. Этот экран разрешает локальное чтение "
            "только явно выбранного чата. Просмотр может отмечать сообщения прочитанными и менять "
            "статус онлайн. Подключение действует до 15 минут в этой сессии; после закрытия нужен "
            "новый вход. После аварии могут остаться локальные файлы; "
            "очистка не гарантирует полного стирания. Текст не отправляется модели, "
            "не озвучивается и не попадает в память или историю Сатори."
        )
        layout.addWidget(self.disclosure)
        self.consent = QCheckBox("Разрешаю отдельное временное подключение Telegram")
        layout.addWidget(self.consent)
        self.connect_button = QPushButton("Подключить Telegram")
        self.list_button = QPushButton("Загрузить список чатов")
        self.select_button = QPushButton("Выбрать чат")
        self.read_button = QPushButton("Прочитать выбранный")
        self.disconnect_button = QPushButton("Отключить локально")
        for buttons in (
            (self.connect_button, self.list_button, self.select_button),
            (self.read_button, self.disconnect_button),
        ):
            actions = QHBoxLayout()
            for button in buttons:
                actions.addWidget(button)
            layout.addLayout(actions)
        self.account = _plain()
        self.status = _plain()
        layout.addWidget(self.account)
        layout.addWidget(self.status)
        self.chats = QListWidget()
        self.chats.setAccessibleName("Чаты Telegram для отдельного выбора")
        self.chats.setMaximumHeight(150)
        layout.addWidget(self.chats)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAccessibleName("Временный текст выбранного чата Telegram")
        self.content = QWidget()
        self.body_layout = QVBoxLayout(self.content)
        self.body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.body_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.scroll_area.setWidget(self.content)
        layout.addWidget(self.scroll_area)
        self.viewport_timer = QTimer(self)
        self.viewport_timer.setInterval(250)
        self.viewport_timer.timeout.connect(self._report_viewport)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._report_viewport)
        self.scroll_area.viewport().installEventFilter(self)
        self.connect_button.clicked.connect(self._connect)
        self.list_button.clicked.connect(self._list)
        self.select_button.clicked.connect(self._select)
        self.read_button.clicked.connect(self._read)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.consent.toggled.connect(self._render)
        self.chats.currentItemChanged.connect(self._selection_changed)
        self.controller.changed.connect(self._render)
        self.controller.shutdown_finished.connect(self.shutdown_finished.emit)
        self._render()

    @property
    def shutdown_complete(self) -> bool:
        return self.controller.shutdown_complete

    def _chosen(self) -> UUID | None:
        item = self.chats.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return (
            value
            if isinstance(value, UUID)
            and any(chat.handle == value and chat.eligible for chat in self._catalog)
            else None
        )

    def _connect(self) -> None:
        if self.consent.isChecked() and self.controller.connect_enabled:
            self.controller.connect_account(consent=True)

    def _list(self) -> None:
        if self.controller.list_enabled:
            self._allow_page = False
            self._clear_page()
            self.controller.list_chats()

    def _select(self) -> None:
        handle = self._chosen()
        if handle is not None and self.controller.select_enabled:
            self._allow_page = False
            self._clear_page()
            self.controller.select_chat(handle)

    def _read(self) -> None:
        if not self._foreground():
            return
        self.controller.set_foreground(True)
        chosen = self._chosen()
        if (
            chosen is not None
            and self.controller.read_enabled
            and chosen == self.controller.selected_handle
        ):
            self._allow_page = True
            if not self.controller.read_selected():
                self._allow_page = False
            self._render()

    def _selection_changed(self) -> None:
        self._conceal()
        self.controller.set_foreground(self._foreground())
        self._render()

    def _clear_page(self) -> None:
        self.viewport_timer.stop()
        self._page = None
        self._bodies.clear()
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            if item and (widget := item.widget()):
                for label in widget.findChildren(QLabel):
                    label.clear()
                widget.deleteLater()

    def _render(self) -> None:
        if self._allow_page and not self._foreground():
            self._conceal()
        if self._principal != self.controller.principal:
            self._principal = self.controller.principal
            self._allow_page = False
            self.consent.setChecked(False)
        self.status.setText(_STATUSES.get(self.controller.status, _UNAVAILABLE))
        self.account.setText(self.controller.account_label)
        if self._catalog != self.controller.chats:
            chosen = self._chosen()
            self._catalog = self.controller.chats
            self.chats.blockSignals(True)
            self.chats.clear()
            for chat in self._catalog:
                item = QListWidgetItem(chat.title, self.chats)
                item.setData(Qt.ItemDataRole.UserRole, chat.handle)
                if not chat.eligible:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                if chat.handle == chosen:
                    self.chats.setCurrentItem(item)
            self.chats.blockSignals(False)
        self.connect_button.setEnabled(self.consent.isChecked() and self.controller.connect_enabled)
        self.consent.setEnabled(self.controller.shutdown_complete and not self.controller.busy)
        self.list_button.setEnabled(self.controller.list_enabled)
        self.select_button.setEnabled(self.controller.select_enabled and self._chosen() is not None)
        self.read_button.setEnabled(
            self.controller.read_enabled
            and self._chosen() is not None
            and self._chosen() == self.controller.selected_handle
        )
        self.disconnect_button.setEnabled(not self.controller.shutdown_complete)
        page = self.controller.page if self._allow_page else None
        if page != self._page:
            self._clear_page()
            self._page = page
            if page is not None:
                for message in page.messages:
                    card = QWidget()
                    card_layout = QVBoxLayout(card)
                    card_layout.addWidget(_plain(message.speaker))
                    body = _plain(message.body)
                    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
                    card_layout.addWidget(body)
                    self.body_layout.addWidget(card)
                    self._bodies.append((message.handle, body))
        if self._page is not None and self._foreground() and not self.controller.busy:
            self.viewport_timer.start()
        else:
            self.viewport_timer.stop()

    def _foreground(self) -> bool:
        return (
            self.isVisible() and self.window().isActiveWindow() and not self.window().isMinimized()
        )

    def _report_viewport(self) -> None:
        self.controller.poll()
        if self._page is None or not self._allow_page or self.controller.busy:
            self.viewport_timer.stop()
            return
        if not self._foreground():
            self._conceal()
            return
        if self.controller.page != self._page:
            self._render()
            return
        viewport = self.scroll_area.viewport()
        visible = QRectF(viewport.rect())
        visible.moveTopLeft(QPointF(viewport.mapTo(self.content, QPoint())))
        bodies = []
        for handle, label in self._bodies:
            rect = QRectF(label.rect())
            rect.moveTopLeft(QPointF(label.mapTo(self.content, QPoint())))
            bodies.append((handle, rect))
        try:
            frame = _viewport_frame(
                self._page.page_id, visible, bodies, viewport.devicePixelRatioF()
            )
        except ValueError:
            self._conceal()
            return
        self.controller.report_viewport(frame)

    def _conceal(self) -> None:
        self._allow_page = False
        self._clear_page()
        self.controller.set_foreground(False)

    def _disconnect(self) -> None:
        self._conceal()
        self.consent.setChecked(False)
        self.controller.disconnect_account()

    def set_profile(self, data_directory: Path) -> None:
        self._conceal()
        self.consent.setChecked(False)
        self.controller.set_profile(data_directory)

    def cancel(self) -> None:
        self._conceal()
        self.controller.cancel()

    def conceal(self) -> None:
        """Root hook for tray/tab hiding; removes text without starting a read."""
        self._conceal()

    def shutdown(self) -> None:
        self._conceal()
        self.controller.shutdown()

    def showEvent(self, event: QShowEvent) -> None:
        window = self.window()
        if window is not self._observed_window:
            if self._observed_window is not None:
                self._observed_window.removeEventFilter(self)
            self._observed_window = window
            window.installEventFilter(self)
        self.controller.set_foreground(self._foreground())
        self._render()
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self._conceal()
        super().hideEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowDeactivate:
            self._conceal()
        elif event.type() == QEvent.Type.ActivationChange:
            if not self._foreground():
                self._conceal()
            else:
                self.controller.set_foreground(True)
            self._render()
        super().changeEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._observed_window and event.type() == QEvent.Type.WindowDeactivate:
            self._conceal()
        if (
            watched is self._observed_window
            and event.type() == QEvent.Type.WindowStateChange
            and not self._foreground()
        ):
            self._conceal()
        if watched is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            self._report_viewport()
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)


def _native_plain(value: str = "") -> QLabel:
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setOpenExternalLinks(False)
    label.setWordWrap(True)
    label.setText(value)
    return label


def _native_protected(placeholder: str) -> QLineEdit:
    field = QLineEdit()
    field.setEchoMode(QLineEdit.EchoMode.Password)
    field.setInputMethodHints(
        Qt.InputMethodHint.ImhHiddenText
        | Qt.InputMethodHint.ImhSensitiveData
        | Qt.InputMethodHint.ImhNoPredictiveText
    )
    field.setPlaceholderText(placeholder)
    field.setMaxLength(4096)
    return field


def native_viewport_geometry(
    viewport: QRectF, bodies: list[tuple[str, QRectF]], scale: float
) -> tuple[list[int], list[dict[str, object]]]:
    if not math.isfinite(scale) or not 0 < scale <= 8 or viewport.height() <= 0 or len(bodies) > 50:
        raise ValueError("telegram_geometry_invalid")
    spans = [(math.floor(viewport.top() * scale), math.ceil(viewport.bottom() * scale))]
    rows = []
    for handle, rect in bodies:
        if not all(math.isfinite(v) for v in (rect.top(), rect.bottom())) or rect.height() <= 0:
            raise ValueError("telegram_geometry_invalid")
        top, bottom = math.floor(rect.top() * scale), math.ceil(rect.bottom() * scale)
        rows.append((handle, top, bottom))
        spans.append((top, bottom))
    if any(
        bottom <= top or abs(top) >= 1_000_000 or abs(bottom) >= 1_000_000 for top, bottom in spans
    ):
        raise ValueError("telegram_geometry_invalid")
    origin = min(0, *(top for top, _ in spans))
    if any(bottom - origin > 1_000_000 for _, bottom in spans):
        raise ValueError("telegram_geometry_invalid")
    return [spans[0][0] - origin, spans[0][1] - origin], [
        {"handle": handle, "top": top - origin, "bottom": bottom - origin}
        for handle, top, bottom in rows
    ]


class NativeTelegramPanel(QWidget):
    """No process or account action on construction, show, tab switch or polling."""

    shutdown_finished = Signal()

    def __init__(
        self,
        data_directory: Path,
        parent: QWidget | None = None,
        *,
        controller: TelegramRuntimeController | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller or TelegramRuntimeController(data_directory, self)
        self._catalog: tuple[tuple[str, str], ...] = ()
        self._rendered_page: str | None = None
        self._bodies: list[tuple[str, QLabel]] = []
        self._challenge: tuple[str, str] | None = None
        self._principal = self.controller.principal
        self._observed: QObject | None = None
        self._window_handle: QObject | None = None
        self._event_filter_application: QApplication | None = None
        self._search_result: TelegramPageSearchResult | None = None
        self._search_source: tuple[object, ...] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.privacy_summary = _native_plain(
            "Вход до закрытия Сатори, максимум 24 часа. Клиент может синхронизировать данные "
            "шире выбранного чата; "
            "просмотр меняет отметки прочтения. Текст локален, без модели и озвучки."
        )
        self.details_button = QToolButton()
        self.details_button.setText("Подробнее")
        self.details_button.setCheckable(True)
        self.details_button.setArrowType(Qt.ArrowType.RightArrow)
        self.details_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_button.setAccessibleName("Подробнее о доступе Telegram и сроках просмотра")
        row = QHBoxLayout()
        row.addWidget(self.privacy_summary, 1)
        row.addWidget(self.details_button)
        layout.addLayout(row)
        self.details_text = _native_plain(
            "Telegram читает один явно выбранный личный чат. Отдельный клиент может "
            "синхронизировать данные аккаунта шире выбора. Просмотр меняет обычные "
            "отметки прочтения и может отметить более ранние сообщения. В Core, историю, "
            "память, модель и озвучку текст не передаётся. Отправки нет.\n\n"
            "Выбор действует до 15 минут, страница до 5 минут. Реальное скрытие окна "
            "убирает текст и выбор. При переключении приложений вход сохраняется до "
            "закрытия Сатори или 24 часов с начала подключения. Истечение просмотра убирает "
            "текст и выбор, сохраняя вход. После перезапуска нужен новый вход. "
            "Закрытие локального клиента не доказывает Logout или физическое стирание; "
            "серверный сеанс проверяется отдельно в Telegram → Устройства."
        )
        self._read_only_details = self.details_text.text()
        self.details_text.hide()
        self.details_button.toggled.connect(self._toggle_details)
        self.consent = QCheckBox("Разрешаю подключение Telegram до закрытия Сатори · до 24 часов")
        layout.addWidget(self.consent)
        self.api_id = _native_protected("API ID · только здесь")
        self.api_hash = _native_protected("API hash · только здесь")
        self.api_hash.setMaxLength(32)
        self.connect_button = QPushButton("Подключить Telegram")
        row = QHBoxLayout()
        widget: QWidget
        for widget in (self.api_id, self.api_hash, self.connect_button):
            row.addWidget(widget)
        layout.addLayout(row)
        self.auth_label = _native_plain("Защищённый вход")
        self.auth_value = _native_protected("Телефон, код или пароль · только здесь")
        self.auth_submit = QPushButton("Продолжить вход")
        row = QHBoxLayout()
        for widget in (self.auth_label, self.auth_value, self.auth_submit):
            row.addWidget(widget)
        layout.addLayout(row)
        self.account = _native_plain()
        self.confirm_button = QPushButton("Подтвердить аккаунт")
        row = QHBoxLayout()
        row.addWidget(self.account)
        row.addWidget(self.confirm_button)
        layout.addLayout(row)
        self.list_button = QPushButton("Показать список чатов")
        self.select_button = QPushButton("Выбрать один чат")
        self.read_button = QPushButton("Прочитать выбранный")
        self.conceal_button = QPushButton("Скрыть и снять выбор")
        self.disconnect_button = QPushButton("Отключить локально")
        for controls in (
            (self.list_button, self.select_button, self.read_button),
            (self.conceal_button, self.disconnect_button),
        ):
            row = QHBoxLayout()
            for widget in controls:
                row.addWidget(widget)
            layout.addLayout(row)
        self.chats = QListWidget()
        self.chats.setMaximumHeight(140)
        self.chats.setAccessibleName("Личные чаты для явного выбора")
        layout.addWidget(self.chats)
        self.view_consent = QCheckBox("Разрешаю выбранный просмотр и обычные отметки прочтения")
        layout.addWidget(self.view_consent)
        self.selected = _native_plain("Чат не выбран")
        self.status = _native_plain()
        self.acceptance = _native_plain()
        self.diagnostics = _native_plain()
        for widget in (self.selected, self.status, self.acceptance, self.diagnostics):
            layout.addWidget(widget)
        self.search_query = QLineEdit()
        self.search_query.setMaxLength(MAX_QUERY_CHARS)
        self.search_query.setPlaceholderText("Буквальный фрагмент текущей страницы")
        self.search_query.setAccessibleName("Фрагмент для поиска в текущей странице Telegram")
        self.search_button = QPushButton("Найти на странице")
        row = QHBoxLayout()
        row.addWidget(self.search_query)
        row.addWidget(self.search_button)
        layout.addLayout(row)
        layout.addWidget(
            _native_plain(
                "Поиск только в свежей выбранной странице: до 50 сообщений. "
                "Поиск по всей переписке и аккаунту недоступен."
            )
        )
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.addWidget(self.details_text)
        self.search_result = QPlainTextEdit()
        self.search_result.setReadOnly(True)
        self.search_result.setMaximumHeight(144)
        self.search_result.setAccessibleName("Временный результат поиска по странице Telegram")
        self.search_result.hide()
        content_layout.addWidget(self.search_result)
        self.body_layout = QVBoxLayout()
        self.body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        content_layout.addLayout(self.body_layout)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.content)
        self.scroll_area.setAccessibleName("Временный локальный текст Telegram")
        layout.addWidget(self.scroll_area)
        self.reply_panel = TelegramReplyPanel(self.controller, self)
        content_layout.addWidget(self.reply_panel)
        self.connect_button.clicked.connect(self._connect)
        self.auth_submit.clicked.connect(self._auth)
        self.confirm_button.clicked.connect(self._confirm)
        self.list_button.clicked.connect(self._list)
        self.select_button.clicked.connect(self._select)
        self.read_button.clicked.connect(self._read)
        self.conceal_button.clicked.connect(self.conceal)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.search_button.clicked.connect(self._search)
        self.search_query.textChanged.connect(self._search_query_changed)
        for field in (self.api_id, self.api_hash, self.auth_value):
            field.textChanged.connect(self._render)
        self.consent.toggled.connect(self._render)
        self.view_consent.toggled.connect(self._render)
        self.chats.currentItemChanged.connect(self._render)
        self.controller.changed.connect(self._render)
        self.controller.shutdown_finished.connect(self.shutdown_finished.emit)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._report_viewport)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            self._event_filter_application = app
            app.installEventFilter(self)
        self._render()

    @property
    def shutdown_complete(self) -> bool:
        return self.controller.shutdown_complete

    @property
    def source_status(self) -> SourceStatus:
        return self.controller.source_status

    def _toggle_details(self, expanded: bool) -> None:
        self.details_text.setVisible(expanded)
        self.details_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )

    def set_search_query(self, text: str) -> None:
        """Prefill a user-authored literal only; never connect, fetch or execute search."""
        self._clear_search()
        if not text:
            return
        try:
            TelegramPageSearchQuery(request_id=uuid4(), needle=text)
        except ValidationError:
            self._search_notice("Введи буквальный фрагмент до 128 символов без управляющих знаков.")
            return
        self.search_query.setText(text)

    def _clear_search(self, *, clear_query: bool = True) -> None:
        self._search_result = None
        self.search_result.clear()
        self.search_result.hide()
        if clear_query:
            self.search_query.clear()

    def _search_notice(self, text: str) -> None:
        if self.search_result.toPlainText() != text:
            self.search_result.setPlainText(text)
        self.search_result.show()

    def _search_query_changed(self) -> None:
        self._clear_search(clear_query=False)
        self._update_search_button()

    def _update_search_button(self) -> None:
        self.search_button.setEnabled(
            bool(self.search_query.text())
            and self._foreground()
            and self.controller.visible
            and self.controller.live
        )

    def _search(self) -> None:
        self._clear_search(clear_query=False)
        if not self._foreground():
            self._clear_search()
            return
        try:
            query = TelegramPageSearchQuery(request_id=uuid4(), needle=self.search_query.text())
        except ValidationError:
            self._search_notice("Введи буквальный фрагмент до 128 символов без управляющих знаков.")
            return
        result = self.controller.search_current_page(query)
        if result.status == "unavailable":
            self._search_notice(
                "Нужна свежая выбранная страница. Проверь подключение, выбор чата "
                "и явно прочитай или обнови страницу."
            )
            return
        self._search_result = result
        self._render_search()

    def _search_page_binding(self) -> tuple[object, ...] | None:
        """Presentation invalidation only; the runtime helper remains admission authority."""
        state = self.controller.state
        if (
            not state
            or not state.page_id
            or not state.selected
            or not state.confirmed
            or state.stale
            or state.terminal
            or state.closed
            or state.pending
            or not self._foreground()
            or not self.controller.visible
            or not self.controller.live
            or self.controller.closing
            or self.controller.shutting_down
            or self.controller.qualification is None
            or self.controller.qualification.principal != self.controller.principal
        ):
            return None
        try:
            now = self.controller.clock()
            if (
                type(now) not in (int, float)
                or not math.isfinite(now)
                or state.last_clock is None
                or type(state.last_clock) not in (int, float)
                or not math.isfinite(state.last_clock)
                or now < state.last_clock
                or any(
                    type(deadline) not in (int, float)
                    or not math.isfinite(deadline)
                    or now >= deadline
                    for deadline in (
                        state.session_deadline,
                        state.selection_deadline,
                        state.page_deadline,
                    )
                )
            ):
                return None
        except Exception:
            return None
        return (
            self.controller.principal,
            self.controller.generation,
            state.epoch,
            state.selected,
            state.page_id,
            state.session_deadline,
            state.selection_deadline,
            state.page_deadline,
        )

    def _render_search(self) -> None:
        state = self.controller.state
        source = self._search_page_binding()
        if self._search_source is not None and source != self._search_source:
            self._clear_search()
        self._search_source = source
        if self.controller.closing or self.controller.shutting_down or (state and state.terminal):
            self._clear_search()
        result = self._search_result
        if result is not None:
            if not self._foreground() or not self.controller.page_search_is_current(result):
                self._clear_search()
            else:
                assert result.scope is not None
                assert result.expires_at_monotonic is not None
                try:
                    now = self.controller.clock()
                    remaining = result.expires_at_monotonic - now
                    valid_clock = (
                        type(now) in (int, float)
                        and math.isfinite(now)
                        and now >= result.scope.checked_at_monotonic
                        and remaining > 0
                    )
                except Exception:
                    valid_clock = False
                    remaining = 0
                if not valid_clock:
                    self._clear_search()
                else:
                    observed = result.scope.observed_at.astimezone().strftime("%d.%m.%Y %H:%M:%S")
                    initial_remaining = math.ceil(
                        result.expires_at_monotonic - result.scope.checked_at_monotonic
                    )
                    text = [
                        "Telegram · только текущая выбранная страница",
                        f"Проверено сообщений: {result.searched_messages} из загруженных, "
                        f"совпадений: {result.matching_messages}, показано: {len(result.hits)}.",
                        f"Страница получена: {observed}. "
                        f"На момент поиска до истечения страницы: {initial_remaining} с.",
                    ]
                    if result.source_omitted:
                        text.append(f"Источник пропустил сообщений: {result.source_omitted}.")
                    if result.hits_truncated:
                        text.append("Часть совпадений не показана из-за ограничения результата.")
                    if not result.hits:
                        text.append("В текущей странице буквальных совпадений нет.")
                    for hit in result.hits:
                        try:
                            date = (
                                datetime.fromtimestamp(hit.source_date_unix)
                                .astimezone()
                                .strftime("%d.%m.%Y %H:%M")
                            )
                        except (ValueError, OSError, OverflowError):
                            date = "Время сообщения недоступно"
                        excerpt = (
                            ("…" if hit.clipped_before else "")
                            + hit.excerpt
                            + ("…" if hit.clipped_after else "")
                        )
                        text.extend(("", f"Сообщение источника · {hit.sender} · {date}", excerpt))
                    self._search_notice("\n".join(text))
        self._update_search_button()

    def _foreground(self) -> bool:
        window = self.window()
        handle = window.windowHandle()
        return (
            self.isVisible()
            and window.isActiveWindow()
            and not window.isMinimized()
            and handle is not None
            and handle.isExposed()
        )

    def _clear_secrets(self) -> None:
        self.api_id.clear()
        self.api_hash.clear()
        self.auth_value.clear()

    def _connect(self) -> None:
        if not self._foreground() or not self.consent.isChecked():
            return
        ident, secret = self.api_id.text(), self.api_hash.text()
        self._clear_secrets()
        self.controller.set_foreground(True)
        self.controller.connect_account(ident, secret, consent=True)
        self._render()

    def _auth(self) -> None:
        value = self.auth_value.text()
        self.auth_value.clear()
        if self._foreground():
            self.controller.submit_auth(value)

    def _confirm(self) -> None:
        if self._foreground():
            self.controller.confirm_account()

    def _list(self) -> None:
        if self._foreground():
            self.controller.set_foreground(True)
            self.controller.list_chats()

    def _select(self) -> None:
        item = self.chats.currentItem()
        if item and self._foreground() and self.view_consent.isChecked():
            self.controller.set_foreground(True)
            self.controller.select_chat(item.data(Qt.ItemDataRole.UserRole), consent=True)

    def _read(self) -> None:
        if self._foreground():
            self.controller.set_foreground(True)
            self.controller.read_selected()

    def _clear_page(self) -> None:
        self._rendered_page = None
        self._bodies.clear()
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            if item and (widget := item.widget()):
                for label in widget.findChildren(QLabel):
                    label.clear()
                widget.deleteLater()

    def _render(self) -> None:
        state = self.controller.state
        foreground = self._foreground() and self.controller.visible
        self.details_text.setText(
            self._read_only_details.replace(
                "Отправки нет.",
                "Ответ отправляется только после отдельной проверки точного текста "
                "и нового разрешения. Право чтения не разрешает отправку.",
            )
            if self.controller.reviewed_reply_enabled
            else self._read_only_details
        )
        if self.controller.principal != self._principal:
            self._principal = self.controller.principal
            self._clear_secrets()
            self._clear_search()
            self.consent.setChecked(False)
            self.view_consent.setChecked(False)
        challenge = state.challenge if state else None
        if challenge != self._challenge:
            self._challenge = challenge
            self.auth_value.clear()
        self.api_id.setEnabled(self.controller.connect_enabled)
        self.api_hash.setEnabled(self.controller.connect_enabled)
        self.consent.setEnabled(self.controller.shutdown_complete)
        self.connect_button.setEnabled(
            self.controller.connect_enabled
            and foreground
            and self.consent.isChecked()
            and bool(self.api_id.text())
            and len(self.api_hash.text()) == 32
        )
        self.auth_label.setText(
            {"PHONE": "Телефон", "CODE": "Код входа", "PASSWORD": "Пароль 2FA"}.get(
                challenge[1] if challenge else "", "Защищённый вход"
            )
        )
        self.auth_value.setEnabled(self.controller.live and foreground and challenge is not None)
        self.auth_submit.setEnabled(
            self.controller.live
            and not self.controller.busy
            and foreground
            and challenge is not None
            and bool(self.auth_value.text())
        )
        self.account.setText(state.account_label if state and foreground else "")
        self.confirm_button.setText(
            "Показать аккаунт" if state and state.account_preview_needed else "Подтвердить аккаунт"
        )
        self.confirm_button.setEnabled(
            self.controller.live
            and not self.controller.busy
            and foreground
            and bool(
                state
                and not state.confirmed
                and (state.account_token or state.account_preview_needed)
                and "confirm_account" not in state.pending
            )
        )
        self.list_button.setEnabled(self.controller.list_enabled and foreground)
        current_catalog = state.chats if state and foreground else ()
        if current_catalog != self._catalog:
            self._catalog = current_catalog
            # Picker expiry must not trigger a selection/conceal callback.
            self.chats.blockSignals(True)
            self.chats.clear()
            for token, title in current_catalog:
                item = QListWidgetItem(title, self.chats)
                item.setData(Qt.ItemDataRole.UserRole, token)
            self.chats.blockSignals(False)
            self.view_consent.setChecked(False)
        self.select_button.setEnabled(
            self.controller.select_enabled
            and foreground
            and self.chats.currentItem() is not None
            and self.view_consent.isChecked()
        )
        self.read_button.setText("Обновить" if state and state.stale else "Прочитать выбранный")
        self.read_button.setEnabled(self.controller.read_enabled and foreground)
        self.disconnect_button.setEnabled(
            not self.controller.shutdown_complete and not self.controller.closing
        )
        self.conceal_button.setEnabled(self.controller.live)
        self.selected.setText(
            "Выбран: " + state.selected_title
            if state and state.selected and foreground
            else "Чат не выбран"
        )
        if state and state.stale:
            status = "В чате новое сообщение. Обнови просмотр"
        else:
            status = {
                "runtime_unavailable": "Подключение пока не квалифицировано для этой сборки.",
                "disconnected": "Telegram отключён. Вход запускается только по твоему решению.",
                "connecting": "Запускаю защищённое подключение.",
                "auth": "Введи данные входа только в защищённое поле.",
                "account_confirm": "Проверь показанный аккаунт и отдельно подтверди его.",
                "connected": "Аккаунт подтверждён. Список чатов загружается отдельно.",
                "choose_chat": "Выбери один личный чат и разреши обычные отметки прочтения.",
                "selected": "Чат выбран. Прочитай одну страницу явно.",
                "reading": "Читаю ограниченную страницу выбранного чата.",
                "ready": "Одна временная ограниченная страница; полнота истории не гарантируется.",
                "stopped": "Локальный процесс остановлен. Это не подтверждение Telegram Logout.",
                "runtime_failed": (
                    "Подключение остановлено. Текст убран, автоматического повтора нет."
                ),
            }.get(self.controller.status, "Доступ к источнику закрыт.")
        self.status.setText(status)
        self.acceptance.setText("Реальная проверка чтения: " + self.controller.acceptance.status)
        self.diagnostics.setText(
            (state.diagnostics if state else "DISCONNECTED")
            + " / IO "
            + self.controller.io_diagnostic
            + " / "
            + self.controller.exit_description
            + " / CLEANUP "
            + (state.cleanup if state else "NOT_CREATED")
        )
        page = state.page_id if state and foreground else None
        if page != self._rendered_page:
            self._clear_page()
            self._rendered_page = page
            if page is not None and state:
                for message in state.messages:
                    card = QWidget()
                    row = QVBoxLayout(card)
                    try:
                        source_time = (
                            datetime.fromtimestamp(message.date)
                            .astimezone()
                            .strftime("%d.%m.%Y %H:%M")
                        )
                    except (ValueError, OSError, OverflowError):
                        source_time = "Время источника недоступно"
                    row.addWidget(_native_plain(message.sender + " · " + source_time))
                    body = _native_plain(message.text)
                    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
                    row.addWidget(body)
                    if self.controller.reviewed_reply_enabled:
                        reply_button = QPushButton("Ответить на это сообщение")
                        reply_button.clicked.connect(
                            lambda _checked=False, handle=message.handle: self._reply_to_message(
                                handle
                            )
                        )
                        row.addWidget(reply_button)
                    self.body_layout.addWidget(card)
                    self._bodies.append((message.handle, body))
        self._render_search()
        self.reply_panel.refresh()

    def _reply_to_message(self, handle: str) -> None:
        self.reply_panel.select_target(handle)
        self.scroll_area.ensureWidgetVisible(self.reply_panel)

    def _report_viewport(self) -> None:
        state = self.controller.state
        if not state or not state.page_id:
            return
        if not self._foreground():
            self.conceal(preserve_api_id=True)
            return
        if state.page_id != self._rendered_page:
            self._render()
        viewport = self.scroll_area.viewport()
        visible = QRectF(viewport.rect())
        visible.moveTopLeft(QPointF(viewport.mapTo(self.content, QPoint())))
        bodies = []
        for handle, label in self._bodies:
            rect = QRectF(label.rect())
            rect.moveTopLeft(QPointF(label.mapTo(self.content, QPoint())))
            bodies.append((handle, rect))
        try:
            span, rows = native_viewport_geometry(visible, bodies, viewport.devicePixelRatioF())
        except ValueError:
            self.conceal()
            return
        self.controller.report_viewport(span, rows)

    def _tick(self) -> None:
        self.controller.poll()
        foreground = self._foreground()
        if not foreground and self.controller.visible:
            self.conceal(preserve_api_id=True)
        else:
            self.controller.set_foreground(foreground)
        self._render()
        if foreground:
            self._report_viewport()

    def conceal(self, *, preserve_api_id: bool = False) -> None:
        self.reply_panel.retire_handoff()
        self.controller.conceal()
        self._clear_search()
        self._search_source = None
        api_id = self.api_id.text()
        if not (
            preserve_api_id
            and len(api_id) <= 10
            and api_id.isascii()
            and api_id.isdecimal()
            and 0 < int(api_id) < (1 << 31)
        ):
            self.api_id.clear()
        self.api_hash.clear()
        self.auth_value.clear()
        self._clear_page()
        self.view_consent.setChecked(False)

    def _disconnect(self) -> None:
        self._clear_secrets()
        self.conceal()
        self.consent.setChecked(False)
        self.controller.disconnect_account()

    def set_profile(self, data_directory: Path) -> None:
        self.conceal()
        self.consent.setChecked(False)
        self.controller.set_profile(data_directory)

    def cancel(self) -> None:
        self._clear_secrets()
        self.controller.cancel()

    def shutdown(self) -> None:
        # Detach before synchronous shutdown callbacks or widget destruction can quit Qt.
        app = self._event_filter_application
        self._event_filter_application = None
        if app is not None:
            app.removeEventFilter(self)
        self._observed = None
        self._window_handle = None
        self.timer.stop()
        self._clear_search()
        self._search_source = None
        self._clear_secrets()
        self._clear_page()
        self.controller.shutdown()

    def showEvent(self, event: QShowEvent) -> None:
        window = self.window()
        self._observed = window
        self._window_handle = window.windowHandle()
        self.controller.set_foreground(self._foreground())
        self._render()
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self.conceal(preserve_api_id=True)
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if (
            isinstance(watched, QWidget)
            and watched.window() is self.window()
            and kind
            in {
                QEvent.Type.KeyPress,
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseMove,
                QEvent.Type.Wheel,
                QEvent.Type.TouchBegin,
            }
        ):
            self.controller.mark_activity()
        if watched in (self._observed, self._window_handle) and (
            kind
            in {
                QEvent.Type.WindowDeactivate,
                QEvent.Type.Hide,
                QEvent.Type.HideToParent,
            }
            or (
                kind in {QEvent.Type.WindowStateChange, QEvent.Type.Expose}
                and not self._foreground()
            )
        ):
            self.conceal(preserve_api_id=True)
        return super().eventFilter(watched, event)
