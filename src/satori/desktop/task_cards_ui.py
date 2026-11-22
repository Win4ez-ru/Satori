"""Plain-text transient assistant result, separate from canonical history."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

from satori.desktop.assistant_controller import AssistantController


class TaskCardView(QFrame):
    action_requested = Signal(str)

    def __init__(self, controller: AssistantController) -> None:
        super().__init__()
        self.controller = controller
        self.setObjectName("AssistantTaskCard")
        self.setMinimumHeight(230)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setObjectName("TaskCardTitle")
        header.addWidget(self.title, 1)
        close = QPushButton("Убрать")
        close.clicked.connect(controller.clear)
        header.addWidget(close)
        layout.addLayout(header)
        self.body = QTextEdit()
        self.body.setReadOnly(True)
        self.body.setAcceptRichText(False)
        self.body.setAccessibleName("Результат задачи Сатори")
        self.body.setMinimumHeight(100)
        self.body.setMaximumHeight(230)
        layout.addWidget(self.body)
        footer = QHBoxLayout()
        self.source = QLabel()
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        self.source.setWordWrap(True)
        self.source.setObjectName("TaskCardSource")
        footer.addWidget(self.source, 1)
        self.action = QPushButton("Открыть")
        self.action.clicked.connect(self._action)
        footer.addWidget(self.action)
        layout.addLayout(footer)
        controller.changed.connect(self.refresh)
        self.refresh()

    def _action(self) -> None:
        card = self.controller.card
        if card is not None and card.action is not None:
            self.action_requested.emit(card.action)

    def refresh(self) -> None:
        card = self.controller.card
        self.setVisible(card is not None)
        self.title.setText(card.title if card else "")
        self.body.setPlainText(card.body if card else "")
        self.source.setText(f"{card.source} · {card.status}" if card else "")
        self.action.setVisible(card is not None and card.action is not None)
