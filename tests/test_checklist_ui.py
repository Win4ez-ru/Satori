"""Visible per-row check states with ordinary Qt keyboard/mouse selection."""

import os
from collections.abc import Iterator
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleFactory,
    QStyleOptionViewItem,
)

from satori.desktop.checklist_ui import CheckListWidget
from satori.desktop.workspace_ui import DesktopWorkspace


@pytest.fixture
def checklist() -> Iterator[CheckListWidget]:
    app = QApplication.instance() or QApplication([])
    shell = DesktopWorkspace()
    # Exercise the styled ancestor that exposed the macOS paint problem without
    # changing QApplication's shared style or requiring a live account.
    native = QStyleFactory.create("macOS")
    if native is not None:
        native.setParent(shell)
        shell.setStyle(native)
    view = CheckListWidget()
    shell.pages.addTab(view, "Synthetic choices")
    shell.resize(800, 420)
    for row in range(7):
        item = QListWidgetItem(f"Synthetic choice {row + 1}", view)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, f"synthetic-id-{row}")
    shell.show()
    view.setCurrentRow(1)
    view.setFocus()
    app.processEvents()
    yield view
    shell.close()
    shell.deleteLater()
    app.processEvents()


def _indicator_rect(view: CheckListWidget, row: int) -> QRect:
    option = QStyleOptionViewItem()
    option.initFrom(view)
    option.rect = view.visualItemRect(view.item(row))
    delegate = cast(QStyledItemDelegate, view.itemDelegate())
    delegate.initStyleOption(option, view.model().index(row, 0))
    return view.style().subElementRect(
        QStyle.SubElement.SE_ItemViewItemCheckIndicator, option, view
    )


def _indicator_image(view: CheckListWidget, row: int) -> QImage:
    app = QApplication.instance()
    assert app is not None
    app.processEvents()
    rect = _indicator_rect(view, row)
    assert rect.width() >= 10
    assert rect.height() >= 10
    assert view.viewport().rect().contains(rect)
    return view.viewport().grab(rect).toImage()


def _different_pixels(before: QImage, after: QImage) -> int:
    assert before.size() == after.size()
    return sum(
        before.pixel(x, y) != after.pixel(x, y)
        for x in range(before.width())
        for y in range(before.height())
    )


@pytest.mark.parametrize("row", range(7))
def test_each_row_visibly_reflects_check_state(checklist: CheckListWidget, row: int) -> None:
    unchecked = _indicator_image(checklist, row)
    checklist.item(row).setCheckState(Qt.CheckState.Checked)
    checked = _indicator_image(checklist, row)
    assert _different_pixels(unchecked, checked) >= 20
    checklist.item(row).setCheckState(Qt.CheckState.Unchecked)
    assert _different_pixels(unchecked, _indicator_image(checklist, row)) == 0


def test_highlight_does_not_check_a_row_and_space_toggles_only_current(
    checklist: CheckListWidget,
) -> None:
    assert checklist.item(1).isSelected()
    assert all(checklist.item(row).checkState() == Qt.CheckState.Unchecked for row in range(7))
    QTest.keyClick(checklist, Qt.Key.Key_Space)
    assert checklist.item(1).checkState() == Qt.CheckState.Checked
    checklist.setCurrentRow(5)
    assert checklist.item(1).checkState() == Qt.CheckState.Checked
    assert checklist.item(5).checkState() == Qt.CheckState.Unchecked
    QTest.keyClick(checklist, Qt.Key.Key_Space)
    assert [
        row for row in range(7) if checklist.item(row).checkState() == Qt.CheckState.Checked
    ] == [
        1,
        5,
    ]
    assert checklist.item(5).data(Qt.ItemDataRole.UserRole) == "synthetic-id-5"


def test_mouse_indicator_hit_target_and_checked_display_agree(checklist: CheckListWidget) -> None:
    before = _indicator_image(checklist, 4)
    QTest.mouseClick(
        checklist.viewport(), Qt.MouseButton.LeftButton, pos=_indicator_rect(checklist, 4).center()
    )
    assert checklist.item(4).checkState() == Qt.CheckState.Checked
    assert _different_pixels(before, _indicator_image(checklist, 4)) >= 20


def test_scrolled_last_row_keeps_visible_state(checklist: CheckListWidget) -> None:
    for row in range(7, 40):
        item = QListWidgetItem(f"Synthetic choice {row + 1}", checklist)
        item.setCheckState(Qt.CheckState.Unchecked)
    checklist.scrollToBottom()
    before = _indicator_image(checklist, 39)
    checklist.item(39).setCheckState(Qt.CheckState.Checked)
    assert _different_pixels(before, _indicator_image(checklist, 39)) >= 20


def test_disabled_checked_and_unchecked_states_remain_distinguishable(
    checklist: CheckListWidget,
) -> None:
    checklist.setEnabled(False)
    before = _indicator_image(checklist, 3)
    checklist.item(3).setCheckState(Qt.CheckState.Checked)
    assert _different_pixels(before, _indicator_image(checklist, 3)) >= 20


def test_styles_are_view_owned_and_do_not_replace_application_style(
    checklist: CheckListWidget,
) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    original = app.style()
    other = CheckListWidget()
    styles = checklist.findChildren(QStyle)
    assert any(style.objectName().lower() == "fusion" for style in styles)
    assert all(style.parent() is checklist for style in styles)
    assert other.style() is not checklist.style()
    assert app.style() is original
    other.deleteLater()
    app.processEvents()
