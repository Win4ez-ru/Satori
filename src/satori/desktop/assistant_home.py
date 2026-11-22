"""One local entry point for conversation and the personal-source panels."""

# ruff: noqa: RUF001 - intentional Russian user-facing text

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from satori.desktop.source_status import SourceKey, SourceStatus

_NAMES: dict[SourceKey, str] = {
    "calendar": "Календарь",
    "mail": "Gmail",
    "telegram": "Telegram",
}
_SOURCES: tuple[SourceKey, ...] = ("calendar", "mail", "telegram")
_CONNECTIONS = {
    "disconnected": "Не подключён",
    "connecting": "Подключается",
    "connected": "Подключён в этой сессии",
    "expired": "Нужно подключиться заново",
    "unavailable": "Подключение недоступно в этой сборке",
    "closing": "Завершается подключение",
    "error": "Нужна проверка подключения",
}
_SNAPSHOTS = {
    "not_read": "Данные ещё не прочитаны",
    "fresh": "Есть свежий снимок данных",
    "partial": "Показана только часть данных",
    "stale": "Данные устарели. Обнови их в источнике",
    "unknown": "Свежесть данных неизвестна",
}
_PROBLEMS = {
    "none": "",
    "permission": "Проверь разрешение на доступ",
    "expired": "Срок доступа или выбора истёк",
    "unavailable": "Источник сейчас недоступен",
    "rejected": "Ответ источника не прошёл проверку",
    "transport": "Не удалось завершить обмен с источником",
    "unknown": "Причина ошибки пока неизвестна",
}
_CATALOGS = {
    "partial": "Список писем частичный: часть заголовков недоступна",
    "unsupported": "В полученной странице нет доступных заголовков писем",
    "empty": "Полученная страница входящих пуста",
}


def _plain(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setOpenExternalLinks(False)
    return label


class AssistantHome(QWidget):
    """Project metadata and navigate; source operations stay in their owners."""

    def __init__(
        self,
        readers: dict[SourceKey, Callable[[], SourceStatus]],
        navigate: Callable[[SourceKey], None],
        open_conversation: Callable[[], None],
        parent: QWidget | None = None,
        *,
        settings_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self._readers = dict(readers)
        self._navigate = navigate
        self.status_labels: dict[SourceKey, QLabel] = {}
        self.open_buttons: dict[SourceKey, QPushButton] = {}
        layout = QVBoxLayout(self)
        heading = _plain("Подключения" if settings_mode else "Сатори рядом")
        heading.setStyleSheet("font-size: 22px; font-weight: 600")
        layout.addWidget(heading)
        layout.addWidget(
            _plain(
                "Выбери источник, чтобы настроить подключение и доступ к данным."
                if settings_mode
                else "Здесь можно поговорить со мной или открыть нужный источник и заняться делом."
            )
        )
        self.conversation_button = QPushButton("Открыть разговор")
        self.conversation_button.clicked.connect(open_conversation)
        self.conversation_button.setVisible(not settings_mode)
        layout.addWidget(self.conversation_button)
        layout.addWidget(_plain("Твои подключения"))
        for key in _SOURCES:
            self._add_source(key, layout)
        layout.addWidget(
            _plain("Открой источник, чтобы выбрать данные и выполнить нужное действие.")
        )
        layout.addStretch()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def _add_source(self, key: SourceKey, layout: QVBoxLayout) -> None:
        card = QFrame()
        card.setObjectName("SourceConnectionCard")
        card.setFrameShape(QFrame.Shape.StyledPanel)
        row = QHBoxLayout(card)
        text = QVBoxLayout()
        text.addWidget(_plain(_NAMES[key]))
        status = _plain()
        status.setAccessibleName(f"{_NAMES[key]}: состояние подключения")
        text.addWidget(status)
        row.addLayout(text, 1)
        button = QPushButton(f"Открыть: {_NAMES[key]}")
        button.clicked.connect(lambda _checked=False, source=key: self._navigate(source))
        row.addWidget(button)
        self.status_labels[key] = status
        self.open_buttons[key] = button
        layout.addWidget(card)

    def refresh(self) -> None:
        """A failed status projection stays unknown, without account operations."""
        for key, label in self.status_labels.items():
            reader = self._readers.get(key)
            try:
                value = reader() if reader is not None else None
                if not isinstance(value, SourceStatus) or value.source != key:
                    raise ValueError("source_status_mismatch")
                value = SourceStatus.model_validate(value.model_dump(warnings=False))
            except Exception:
                value = SourceStatus(
                    source=key, connection="unavailable", snapshot="unknown", problem="unknown"
                )
            details = [_CONNECTIONS[value.connection]]
            if value.connection == "connected":
                if value.selected_count is None:
                    details.append("Выбор данных пока неизвестен")
                elif value.selected_count:
                    details.append(f"Выбрано источников: {value.selected_count}")
                else:
                    details.append("Данные для просмотра ещё не выбраны")
                details.append(_SNAPSHOTS[value.snapshot])
            if value.observed_at is not None:
                details.append(f"Последнее чтение: {value.observed_at.astimezone():%d.%m %H:%M %Z}")
            if value.connection == "connected" and value.catalog_state is not None:
                details.append(_CATALOGS[value.catalog_state])
            if value.busy:
                details.append("Операция выполняется")
            if value.problem != "none":
                details.append(_PROBLEMS[value.problem])
            label.setText(" · ".join(details))
