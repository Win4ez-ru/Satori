"""Ephemeral source answers rendered beside the ordinary conversation history."""

# ruff: noqa: RUF001 - intentional Russian user-facing text.

from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from satori.desktop.source_answer_controller import ConversationSourceController


def _label(name: str) -> QLabel:
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setAccessibleName(name)
    return label


class ConversationSourcePanel(QFrame):
    """A presentation-only bubble; it never writes chat history or requests speech."""

    settings_requested = Signal()

    def __init__(
        self, controller: "ConversationSourceController", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("ConversationSourceAnswer")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setStyleSheet(
            "QFrame#ConversationSourceAnswer { border: 0; background: transparent; }"
            "QPlainTextEdit#SourceAnswerText { border: 0; background: transparent;"
            " color: #344139; font-size: 15px; }"
            "QToolButton { border: 0; background: transparent; color: #697b69;"
            " text-align: left; padding: 3px 0; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self.query = _label("Твоё поручение о сообщениях")
        layout.addWidget(self.query)
        self.provider = _label("Модель для анализа этого поручения")
        self.provider.setStyleSheet("font-size: 11px; color: #7a857c;")
        layout.addWidget(self.provider)
        role = _label("Автор ответа")
        role.setText("Сатори")
        layout.addWidget(role)
        self.answer = QPlainTextEdit()
        self.answer.setObjectName("SourceAnswerText")
        self.answer.setFrameShape(QFrame.Shape.NoFrame)
        self.answer.setReadOnly(True)
        self.answer.setAccessibleName("Ответ Сатори по найденным сообщениям")
        self.answer.setMinimumHeight(76)
        self.answer.setMaximumHeight(164)
        layout.addWidget(self.answer)
        self.connect_button = QPushButton("Подключить GPT")
        self.connect_button.hide()
        self.connect_button.clicked.connect(self.settings_requested)
        layout.addWidget(self.connect_button)
        controls = QHBoxLayout()
        self.details_button = QToolButton()
        self.details_button.setText("Что проверила")
        self.details_button.setCheckable(True)
        self.details_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_button.toggled.connect(self._details_changed)
        controls.addWidget(self.details_button)
        controls.addStretch()
        self.cancel_button = QPushButton("Остановить")
        self.cancel_button.clicked.connect(controller.clear)
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Источники и границы проверки сообщений")
        self.details.setMaximumHeight(120)
        self.details.hide()
        layout.addWidget(self.details)
        self._shown_request: object = None
        controller.changed.connect(self.refresh)
        self.refresh()

    def _details_changed(self, expanded: bool) -> None:
        self.details_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.details.setVisible(expanded and self.controller.card is not None)
        self.updateGeometry()

    def _fit_answer(self) -> None:
        margin = int(self.answer.document().documentMargin())
        width = max(1, self.answer.viewport().width() - 2 * margin)
        bounds = self.answer.fontMetrics().boundingRect(
            QRect(0, 0, width, 20000),
            int(Qt.TextFlag.TextWordWrap),
            self.answer.toPlainText() or " ",
        )
        height = min(164, max(56, bounds.height() + 2 * margin + 8))
        if self.answer.minimumHeight() != height or self.answer.maximumHeight() != height:
            self.answer.setFixedHeight(height)
            self.updateGeometry()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_answer()

    def refresh(self) -> None:
        card = self.controller.card
        self.setVisible(card is not None)
        if card is None:
            self._shown_request = None
            self.query.clear()
            self.provider.clear()
            self.answer.clear()
            self.details.clear()
            self.details_button.setChecked(False)
            return
        if self._shown_request != card.request_id:
            self._shown_request = card.request_id
            self.details_button.setChecked(False)
        excerpt = card.query if len(card.query) <= 240 else card.query[:237] + "…"
        self.query.setText("Ты: " + excerpt)
        provider, _, model = self.controller.provider.partition(":")
        name = {"openai": "OpenAI", "ollama": "Ollama"}.get(provider, provider)
        self.provider.setText("Анализ: " + name + (" · " + model if model else ""))
        self.provider.setVisible(bool(self.controller.provider))
        self.connect_button.setVisible(card.status == "gpt_required")
        self.answer.setPlainText(card.text)
        self._fit_answer()
        self.details.setPlainText("\n\n".join(card.details))
        self.details_button.setVisible(bool(card.details))
        self.cancel_button.setText("Остановить" if self.controller.busy else "Убрать")
        self._details_changed(self.details_button.isChecked())
