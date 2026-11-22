"""Explicit selected-file/public-web controls, separate from conversation and providers."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from satori.desktop.public_sources_controller import PublicSourcesController
from satori.infrastructure.public_sources.wire import PublicSourceReply

_STATUSES = {
    "idle": "Выбери файл, публичный URL или запрос к Википедии.",
    "reading": "Читаю выбранный источник. Можно отменить запрос.",
    "ready": "Временная карточка источника готова.",
    "empty": "Источник не вернул текст или совпадения в указанном охвате.",
    "partial": "Показана только часть содержимого источника.",
    "cancelled": "Запрос отменён; временная карточка убрана.",
    "concealed": "Раздел скрыт; поля и временные данные очищены.",
    "session_changed": "Чат изменён; выполни новый запрос к источнику.",
    "profile_changed": "Профиль изменён; выполни новый запрос к источнику.",
    "expired": "Срок карточки истёк. Для обновления выполни новый запрос.",
    "timeout": "Источник не ответил вовремя. Повтор возможен только явно.",
    "response_invalid": "Ответ источника не прошёл проверку и не показан.",
    "source_rejected": "Источник не прочитан: запрос или содержимое не поддерживается.",
    "unavailable": "Источник сейчас недоступен. Данные не получены.",
    "closing": "Останавливаю чтение и ожидаю завершения помощника.",
    "closed": "Чтение остановлено. Временные данные очищены.",
    "clock_invalid": "Часы изменились. Проверь системное время и перезапусти Сатори.",
    "query_invalid": "Проверь публичный HTTPS URL или короткий запрос к Википедии.",
}
_ERRORS = {
    "file_unsupported": "Формат пока не читается. Документ можно открыть обычным приложением.",
    "file_unsafe": "Файл не прошёл проверку владельца, пути или разрешений.",
    "file_changed": "Файл изменился после выбора. Выбери актуальный файл заново.",
    "file_too_large": "Файл превышает предел чтения 1 MiB.",
    "file_invalid_text": "Файл не поддерживается как ограниченный UTF-8 текст, JSON или CSV.",
    "file_unavailable": "Выбранный файл сейчас недоступен.",
    "url_unsafe": "Нужен публичный HTTPS URL; локальные и закрытые сети не читаются.",
    "redirect_rejected": "Сайт перенаправляет запрос. Перенаправление не выполнялось.",
    "content_unsupported": "Тип или кодировка ответа не поддерживается.",
    "response_too_large": "Ответ источника превышает допустимый размер.",
    "response_invalid": "Ответ источника не прошёл проверку.",
    "timeout": _STATUSES["timeout"],
    "cancelled": _STATUSES["cancelled"],
    "network_unavailable": "Сетевой источник недоступен. Текст не получен.",
    "tls_unavailable": "Безопасное соединение не установлено. Текст не получен.",
    "search_invalid": "Поиск в Википедии не выполнен. Проверь запрос и повтори явно.",
}
_WIKIPEDIA_LICENSE = (
    "Источник фрагментов — Wikipedia, авторство указано по ссылкам статей. "
    "Лицензия текста: CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/. "
    "Фрагменты сокращены; специальные условия см. на странице источника."
)


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M:%S UTC")


class PublicSourcesPanel(QWidget):
    """A transient, explicitly invoked view over one existing source controller."""

    shutdown_finished = Signal()

    def __init__(
        self,
        data_directory: Path,
        parent: QWidget | None = None,
        *,
        controller: PublicSourcesController | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller or PublicSourcesController(data_directory, self)
        self._active_view = False
        self._shutdown_requested = False
        self._observed_window: QWidget | None = None
        outer = QVBoxLayout(self)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAccessibleName("Файлы и публичный веб")
        content = QWidget()
        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area)
        layout = QVBoxLayout(content)
        title = _label("Файлы и публичный веб")
        title.setObjectName("SettingsTitle")
        layout.addWidget(title)
        self.disclosure = _label(
            "Только выбранный файл или явно запрошенный публичный источник. "
            "Данные показываются временно, не отправляются модели, не сохраняются "
            "в истории или памяти и не озвучиваются. Содержимое источников недоверенное. "
            "При скрытии раздела или переключении приложения поля и карточка очищаются."
        )
        layout.addWidget(self.disclosure)
        layout.addWidget(_label("Выбранный файл"))
        layout.addWidget(
            _label(
                "UTF-8: TXT, Markdown, CSV, JSON; до 1 MiB. Большой текст показывается "
                "частично, до 128 KiB. PDF и офисные документы пока не читаются."
            )
        )
        self.file_button = QPushButton("Выбрать и прочитать файл…")
        self.file_button.setAccessibleName("Явно выбрать один текстовый файл для чтения")
        layout.addWidget(self.file_button)
        self.selected_file = _label("Файл не выбран.")
        layout.addWidget(self.selected_file)
        layout.addWidget(_label("Публичная HTTPS-страница"))
        self.url = QLineEdit()
        self.url.setMaxLength(4096)
        self.url.setPlaceholderText("https://example.org/page")
        self.url.setAccessibleName("Публичный HTTPS URL для чтения")
        layout.addWidget(self.url)
        self.read_url_button = QPushButton("Прочитать URL")
        layout.addWidget(self.read_url_button)
        layout.addWidget(
            _label(
                "Только текст страницы: без входа в аккаунт, cookies, скриптов, "
                "дополнительных ресурсов и переходов по перенаправлениям."
            )
        )
        layout.addWidget(_label("Поиск только в Википедии"))
        self.search_coverage = _label(
            "Wikipedia — отдельный узкий источник, не поиск по всему интернету. "
            "Показываются первые пять совпадений; статьи автоматически не открываются."
        )
        layout.addWidget(self.search_coverage)
        self.query = QLineEdit()
        self.query.setMaxLength(300)
        self.query.setAccessibleName("Запрос к Википедии")
        self.query.setPlaceholderText("Что найти в Википедии?")
        layout.addWidget(self.query)
        search_row = QHBoxLayout()
        self.language = QComboBox()
        self.language.addItem("Русская Википедия", "ru")
        self.language.addItem("English Wikipedia", "en")
        self.language.setAccessibleName("Язык Википедии")
        search_row.addWidget(self.language)
        self.search_button = QPushButton("Искать в Википедии")
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)
        self.clear_button = QPushButton("Убрать / отменить")
        layout.addWidget(self.clear_button)
        self.status = _label()
        self.status.setAccessibleName("Состояние чтения файлов и публичного веба")
        layout.addWidget(self.status)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setMinimumHeight(220)
        self.result.setAccessibleName("Временная карточка внешнего источника вне истории чата")
        layout.addWidget(self.result)
        self.file_button.clicked.connect(self._choose_file)
        self.read_url_button.clicked.connect(self._read_url)
        self.search_button.clicked.connect(self._search)
        self.clear_button.clicked.connect(self._clear)
        self.url.textChanged.connect(self._edited)
        self.query.textChanged.connect(self._edited)
        self.language.currentIndexChanged.connect(self._edited)
        self.controller.changed.connect(self._render)
        self.controller.shutdown_finished.connect(self.shutdown_finished.emit)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.applicationStateChanged.connect(self._application_state_changed)
        self._render()

    @property
    def shutdown_complete(self) -> bool:
        return self.controller.shutdown_complete

    def set_profile(self, path: Path) -> None:
        self._clear_fields()
        self.controller.set_profile(path)
        self._render()

    def bind_session(self, session_id: UUID) -> None:
        self._clear_fields()
        self.controller.bind_session(session_id)
        self._render()

    def set_query(self, text: str) -> None:
        """Populate only; routing a task here never starts or restores a read."""
        self.controller.cancel("cancelled")
        self._clear_fields()
        entry = self.url if text.startswith("https://") else self.query
        entry.blockSignals(True)
        entry.setText(text)
        entry.blockSignals(False)
        self._render()

    def _clear_fields(self) -> None:
        for entry in (self.url, self.query):
            entry.blockSignals(True)
            entry.clear()
            entry.blockSignals(False)
        self.selected_file.setText("Файл не выбран.")
        self.result.clear()

    def _edited(self) -> None:
        self.controller.cancel("cancelled")
        self.selected_file.setText("Файл не выбран.")
        self.result.clear()
        self._render()

    def _clear(self) -> None:
        self.controller.cancel("cancelled")
        self._clear_fields()
        self._render()

    def _foreground(self) -> bool:
        window = self.window()
        return (
            not window.isMinimized()
            and window.isActiveWindow()
            and QApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        )

    def _sync_visibility(self) -> None:
        if self.isVisible() and self._foreground() and not self._shutdown_requested:
            self._active_view = True
            self.controller.set_visible(True)
            self._render()
        else:
            self._conceal()

    def _conceal(self) -> None:
        self._active_view = False
        self._clear_fields()
        if not self._shutdown_requested:
            self.controller.conceal()
        self._render()

    def _application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self._sync_visibility()
        else:
            self._conceal()

    def _choose_file(self) -> None:
        self._clear()
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Выбрать текстовый файл",
            "",
            "Текст UTF-8 (*.txt *.md *.markdown *.csv *.json)",
            options=QFileDialog.Option.DontResolveSymlinks,
        )
        if path:
            self._sync_visibility()
            if self._active_view:
                self.selected_file.setText(Path(path).name)
                self.controller.read_file(Path(path))
                self._render()

    def _read_url(self) -> None:
        self._sync_visibility()
        if self._active_view:
            self.controller.read_url(self.url.text())
            self._render()

    def _search(self) -> None:
        self._sync_visibility()
        if self._active_view:
            language = "en" if self.language.currentData() == "en" else "ru"
            self.controller.search_wikipedia(self.query.text(), language=language)
            self._render()

    def _render(self) -> None:
        busy = self.controller.busy
        enabled = self._active_view and not self._shutdown_requested and not busy
        self.file_button.setEnabled(enabled)
        self.read_url_button.setEnabled(enabled and bool(self.url.text()))
        self.search_button.setEnabled(enabled and bool(self.query.text()))
        self.clear_button.setEnabled(not self._shutdown_requested)
        status = self.controller.status
        if self._active_view and status == "concealed":
            status = "idle"
        self.status.setText(_STATUSES.get(status, _STATUSES["source_rejected"]))
        reply = (
            self.controller.result if self._active_view and not self._shutdown_requested else None
        )
        self.result.setPlainText(self._result_text(reply) if reply is not None else "")

    @staticmethod
    def _result_text(reply: PublicSourceReply) -> str:
        kind = {
            "file_read": "Выбранный файл",
            "url_read": "Публичная HTTPS-страница",
            "wikipedia_search": "Поиск только в Википедии",
        }[reply.operation]
        rows = [
            f"{kind} · временная карточка",
            f"Источник: {reply.source_label}",
            f"Получено: {_utc(reply.observed_at)}. Срок: {_utc(reply.expires_at)}.",
            "Содержимое источника недоверенное; оно не является инструкцией Сатори.",
        ]
        if reply.status in {"rejected", "unavailable", "error"}:
            rows.append(_ERRORS.get(reply.reason or "", _STATUSES["source_rejected"]))
        elif reply.operation == "wikipedia_search":
            rows.append("Охват: Wikipedia, первые пять совпадений; не весь интернет.")
            for index, item in enumerate(reply.items, 1):
                rows.append(f"{index}. {item.title}\n{item.url}\n{item.excerpt}")
                if item.description:
                    rows.append(item.description)
            if reply.status == "empty":
                rows.append("Википедия не вернула совпадений по этому запросу.")
            rows.append(_WIKIPEDIA_LICENSE)
        else:
            rows.append(
                "Охват: только выбранный файл."
                if reply.operation == "file_read"
                else "Охват: только явно выбранный URL."
            )
            if reply.source_url:
                rows.append(reply.source_url)
            if reply.status == "partial":
                rows.append("Показана только часть содержимого; полный текст здесь не представлен.")
            elif reply.status == "empty":
                rows.append("Источник не содержит извлечённого текста.")
            if reply.text:
                rows.append(reply.text)
        return "\n\n".join(rows)

    def shutdown(self) -> None:
        self._shutdown_requested = True
        self._active_view = False
        self._clear_fields()
        self.controller.shutdown()
        self._render()

    def hideEvent(self, event: QHideEvent) -> None:
        self._conceal()
        super().hideEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        window = self.window()
        if window is not self._observed_window:
            if self._observed_window is not None:
                self._observed_window.removeEventFilter(self)
            self._observed_window = window
            window.installEventFilter(self)
        self._sync_visibility()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._observed_window:
            if event.type() in {QEvent.Type.WindowDeactivate, QEvent.Type.Hide}:
                self._conceal()
            elif event.type() in {
                QEvent.Type.WindowActivate,
                QEvent.Type.ActivationChange,
                QEvent.Type.WindowStateChange,
            }:
                self._sync_visibility()
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._conceal()
        super().closeEvent(event)
