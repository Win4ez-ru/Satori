"""Consistent check indicators without changing native item selection or model state."""

from PySide6.QtWidgets import QListWidget, QStyleFactory, QWidget


class CheckListWidget(QListWidget):
    """Use Qt's built-in painter for every checkbox, including unfocused macOS rows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The macOS style can omit item-view indicators inside a styled workspace.
        # Keep Qt's ordinary delegate, check-state role and mouse/keyboard behavior.
        # A separate, view-owned style avoids changing other controls or borrowing
        # QApplication's shared style lifetime.
        style = QStyleFactory.create("Fusion")
        if style is not None:
            style.setParent(self)
            self.setStyle(style)
