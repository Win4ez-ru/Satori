"""Owner-facing local call reviews, displayed as plain text."""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from satori.application.calls.intake import CallIntakeError
from satori.infrastructure.calls.inbox import CallInbox, CallSummary


class CallInboxDialog(QDialog):
    def __init__(self, inbox: CallInbox, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сводки звонков")
        self.resize(510, 360)
        self._inbox = inbox
        self._entries: list[CallSummary] = []
        layout = QVBoxLayout(self)
        self.calls = QListWidget()
        self.calls.setAccessibleName("Звонки")
        layout.addWidget(self.calls)
        self.message = QPlainTextEdit()
        self.message.setAccessibleName("Сообщение звонящего")
        self.message.setReadOnly(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox()
        refresh = QPushButton("Обновить")
        buttons.addButton(refresh, QDialogButtonBox.ButtonRole.ActionRole)
        close = QPushButton("Закрыть")
        buttons.addButton(close, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        refresh.clicked.connect(self.refresh)
        layout.addWidget(buttons)
        self.calls.currentRowChanged.connect(self._select)
        self.refresh()

    def refresh(self) -> None:
        row = self.calls.currentRow()
        selected = self._entries[row].call_key if row >= 0 else None
        self.calls.clear()
        self._entries = []
        self.message.clear()
        try:
            self._entries = self._inbox.entries()
        except (CallIntakeError, OSError, ValueError):
            self.message.setPlaceholderText("Ящик этого профиля недоступен.")
            return
        self.message.setPlaceholderText("Пока нет сводок звонков.")
        target = 0
        for i, entry in enumerate(self._entries):
            local = entry.received_at.astimezone()
            self.calls.addItem(f"{local:%d.%m.%Y %H:%M} · {entry.text.splitlines()[0][:70]}")
            if entry.call_key == selected:
                target = i
        if self._entries:
            self.calls.setCurrentRow(target)

    def _select(self, index: int) -> None:
        self.message.setPlainText(self._entries[index].text if index >= 0 else "")
