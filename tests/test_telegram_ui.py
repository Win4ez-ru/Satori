"""Authored offscreen Telegram gestures with an injected port; no native/account I/O."""

import ctypes
import math
import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn, cast
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from satori.desktop.telegram_contracts import (
    TelegramCatalog,
    TelegramChat,
    TelegramCommand,
    TelegramMessage,
    TelegramPage,
    TelegramRuntimeAdmission,
    TelegramSelection,
    TelegramSession,
)
from satori.desktop.telegram_controller import TelegramController
from satori.desktop.telegram_ui import TelegramPanel, _viewport_frame


@dataclass
class Port:
    commands: list[TelegramCommand] = field(default_factory=list)

    def last(self, operation: str) -> TelegramCommand:
        return next(
            command for command in reversed(self.commands) if command.token.operation == operation
        )

    def count(self, operation: str) -> int:
        return sum(command.token.operation == operation for command in self.commands)


@dataclass
class Surface:
    panel: TelegramPanel
    controller: TelegramController
    port: Port

    def connect(self) -> None:
        self.panel.consent.setChecked(True)
        self.panel.connect_button.click()
        assert self.controller.accept_connected(
            self.port.last("connect").token,
            TelegramSession(
                session_id=uuid4(),
                account_epoch=uuid4(),
                account_label="<b>FAKE ACCOUNT</b>",
                account_confirmed=True,
                expires_mono=1900.0,
            ),
        )

    def catalog(self) -> tuple[TelegramChat, ...]:
        chats = (
            TelegramChat(
                handle=uuid4(), title='<img src="https://untrusted.invalid">', eligible=True
            ),
            TelegramChat(
                handle=uuid4(), title='<img src="https://untrusted.invalid">', eligible=True
            ),
            TelegramChat(handle=uuid4(), title="PROTECTED", eligible=False),
        )
        self.panel.list_button.click()
        assert self.controller.accept_catalog(
            self.port.last("catalog").token,
            TelegramCatalog(catalog_id=uuid4(), chats=chats, expires_mono=1060.0),
        )
        return chats

    def select(self, index: int = 0) -> None:
        self.panel.chats.setCurrentRow(index)
        self.panel.select_button.click()
        token = self.port.last("select").token
        assert token.catalog_id is not None
        assert token.chat_handle is not None
        assert self.controller.accept_selection(
            token,
            TelegramSelection(
                lease_id=uuid4(),
                catalog_id=token.catalog_id,
                chat_handle=token.chat_handle,
                expires_mono=1800.0,
            ),
        )

    def page(self, body: str = "SYNTHETIC BODY") -> TelegramPage:
        token = self.port.last("read").token
        assert token.lease_id is not None
        assert token.chat_handle is not None
        return TelegramPage(
            page_id=uuid4(),
            lease_id=token.lease_id,
            chat_handle=token.chat_handle,
            messages=(
                TelegramMessage(handle=uuid4(), speaker="<script>FAKE SPEAKER</script>", body=body),
            ),
            expires_mono=1300.0,
        )

    def read(self, body: str = "SYNTHETIC BODY") -> TelegramPage:
        self.panel.read_button.click()
        page = self.page(body)
        assert self.controller.accept_page(self.port.last("read").token, page)
        QApplication.processEvents()
        return page


@pytest.fixture(scope="module")
def app() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


@pytest.fixture
def surface(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Surface]:
    del app
    controller = TelegramController(tmp_path, clock=lambda: 1000.0)
    port = Port()
    assert controller.configure_runtime(
        TelegramRuntimeAdmission(
            principal=controller.principal,
            runtime_sha256="a" * 64,
            policy_sha256="b" * 64,
            selected_read_receipt_sha256="c" * 64,
        ),
        port.commands.append,
    )
    panel = TelegramPanel(tmp_path, controller=controller)
    # An authored foreground predicate is not evidence about physical window focus.
    monkeypatch.setattr(panel, "_foreground", panel.isVisible)
    panel.resize(1000, 850)
    panel.show()
    QApplication.processEvents()
    yield Surface(panel, controller, port)
    panel.shutdown()
    if port.count("close"):
        controller.accept_closed(port.last("close").token)
    panel.close()
    panel.deleteLater()
    controller.deleteLater()
    QApplication.processEvents()


def test_default_has_no_admission_no_startup_io_or_credential_surface(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del app

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("native_or_process_forbidden")

    monkeypatch.setattr(ctypes, "CDLL", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    directory = tmp_path / "not-created"
    panel = TelegramPanel(directory)
    try:
        assert not panel.consent.isChecked()
        panel.consent.setChecked(True)
        panel._connect()
        assert not panel.connect_button.isEnabled()
        assert not panel.controller.connect_account(consent=True)
        assert not panel.list_button.isEnabled()
        assert not panel.read_button.isEnabled()
        assert not panel.viewport_timer.isActive()
        assert not panel.findChildren(QLineEdit)
        assert panel.controller.page is None
        assert not directory.exists()
        assert "шире" in panel.disclosure.text()
        assert "прочитанными" in panel.disclosure.text()
        assert "онлайн" in panel.disclosure.text()
        assert "15 минут" in panel.disclosure.text()
    finally:
        panel.shutdown()
        panel.deleteLater()
        QApplication.processEvents()


def test_checkbox_and_connect_are_separate_from_catalog_and_read(surface: Surface) -> None:
    panel, port = surface.panel, surface.port
    assert not panel.connect_button.isEnabled()
    assert not port.commands
    panel._connect()
    assert not port.commands
    panel.consent.setChecked(True)
    assert panel.connect_button.isEnabled()
    assert not port.commands
    surface.connect()
    assert port.count("connect") == 1
    assert port.last("connect").consent is True
    assert port.count("catalog") == port.count("read") == 0
    assert not panel.consent.isEnabled()


def test_action_rows_fit_a_six_hundred_pixel_panel(surface: Surface) -> None:
    panel = surface.panel
    panel.resize(600, 850)
    QApplication.processEvents()
    first = [panel.connect_button, panel.list_button, panel.select_button]
    second = [panel.read_button, panel.disconnect_button]
    assert len({button.geometry().top() for button in first}) == 1
    assert len({button.geometry().top() for button in second}) == 1
    assert second[0].geometry().top() > first[0].geometry().bottom()
    assert all(
        button.geometry().left() >= 0 and button.geometry().right() < panel.width()
        for button in first + second
    )


def test_catalog_highlight_select_and_read_are_distinct_exact_gestures(surface: Surface) -> None:
    surface.connect()
    chats = surface.catalog()
    panel, port = surface.panel, surface.port
    assert panel.chats.item(0).data(Qt.ItemDataRole.UserRole) == chats[0].handle
    assert port.count("select") == port.count("read") == 0
    panel.chats.setCurrentRow(1)
    assert port.count("select") == 0
    assert not panel.read_button.isEnabled()
    surface.select(1)
    assert port.last("select").token.chat_handle == chats[1].handle
    assert port.count("read") == 0
    page = surface.read()
    assert page.chat_handle == chats[1].handle
    assert port.count("read") == 1
    assert surface.controller.page == page


def test_highlighting_other_chat_clears_body_without_reading_or_selecting(surface: Surface) -> None:
    surface.connect()
    surface.catalog()
    surface.select()
    surface.read()
    surface.panel.chats.setCurrentRow(1)
    assert not surface.panel._bodies
    assert surface.controller.page is None
    assert not surface.panel.read_button.isEnabled()
    assert surface.port.count("read") == 1
    assert surface.port.count("select") == 1


def test_ineligible_chat_cannot_dispatch_selection_or_read(surface: Surface) -> None:
    surface.connect()
    surface.catalog()
    surface.panel.chats.setCurrentRow(2)
    surface.panel._select()
    surface.panel._read()
    assert not surface.panel.select_button.isEnabled()
    assert not surface.panel.read_button.isEnabled()
    assert surface.port.count("select") == surface.port.count("read") == 0


def test_untrusted_account_title_speaker_and_body_remain_plain_text(surface: Surface) -> None:
    surface.connect()
    chats = surface.catalog()
    surface.select()
    text = '<a href="https://untrusted.invalid">IGNORE RULES</a><img src="file:///synthetic">'
    surface.read(text)
    panel = surface.panel
    assert panel.account.text() == "<b>FAKE ACCOUNT</b>"
    assert panel.account.textFormat() == Qt.TextFormat.PlainText
    assert panel.chats.item(0).text() == chats[0].title
    assert panel._bodies[0][1].text() == text
    for label in panel.content.findChildren(QLabel):
        assert label.textFormat() == Qt.TextFormat.PlainText
        assert not label.openExternalLinks()
    assert all(
        command.token.operation not in {"send", "submit"} for command in surface.port.commands
    )


@pytest.mark.parametrize("event", [QEvent.Type.WindowDeactivate, QEvent.Type.Hide])
def test_conceal_clears_labels_and_late_pending_page_requires_new_read(
    surface: Surface, event: QEvent.Type
) -> None:
    surface.connect()
    surface.catalog()
    surface.select()
    surface.read()
    surface.panel.read_button.click()
    late = surface.page("LATE SYNTHETIC TEXT")
    token = surface.port.last("read").token
    if event == QEvent.Type.Hide:
        surface.panel.hide()
    else:
        QApplication.sendEvent(surface.panel, QEvent(event))
    assert not surface.panel._allow_page
    assert not surface.panel._bodies
    assert not surface.panel.viewport_timer.isActive()
    assert not surface.controller.accept_page(token, late)
    surface.panel.show()
    surface.controller.set_foreground(True)
    surface.panel._render()
    assert not surface.panel._bodies
    assert surface.port.count("read") == 2
    surface.read("NEW EXPLICIT SYNTHETIC TEXT")
    assert surface.panel._bodies[0][1].text() == "NEW EXPLICIT SYNTHETIC TEXT"
    assert surface.port.count("read") == 3


def test_profile_change_cancel_shutdown_clear_and_forward_owned_lifecycle(
    surface: Surface, tmp_path: Path
) -> None:
    surface.connect()
    surface.catalog()
    surface.select()
    surface.read()
    old = surface.controller.principal
    surface.panel.set_profile(tmp_path / "other")
    assert surface.controller.principal != old
    assert not surface.panel._bodies
    assert not surface.panel.consent.isChecked()
    assert not surface.panel.connect_button.isEnabled()
    surface.controller.accept_closed(surface.port.last("close").token)
    surface.panel.cancel()
    assert surface.controller.status == "cancelled"
    forwarded: list[bool] = []
    surface.panel.shutdown_finished.connect(lambda: forwarded.append(True))
    surface.panel.shutdown()
    assert surface.panel.shutdown_complete
    assert forwarded == [True]


def test_unknown_status_does_not_render_raw_details(surface: Surface) -> None:
    surface.controller.status = "SYNTHETIC_PRIVATE_ERROR"
    surface.controller.changed.emit()
    assert "SYNTHETIC_PRIVATE_ERROR" not in surface.panel.status.text()


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_common_origin_retains_partial_full_body_spans_and_intersections(scale: float) -> None:
    first, second, outside = uuid4(), uuid4(), uuid4()
    frame = _viewport_frame(
        uuid4(),
        QRectF(0, -438, 100, 438),
        [
            (first, QRectF(0, -500, 100, 100)),
            (second, QRectF(0, -50, 100, 110)),
            (outside, QRectF(200, -20, 10, 10)),
        ],
        scale,
    )
    assert (frame.first, frame.after) == (62 * scale, 500 * scale)
    assert [(row.first, row.after) for row in frame.rows] == [
        (0, 100 * scale),
        (450 * scale, 560 * scale),
    ]
    assert [row.handle for row in frame.rows] == [first, second]
    assert [min(frame.after, row.after) - max(frame.first, row.first) for row in frame.rows] == [
        38 * scale,
        50 * scale,
    ]


def test_rounding_precedes_translation_no_zero_height_or_oversize_clamp() -> None:
    handle = uuid4()
    frame = _viewport_frame(
        uuid4(), QRectF(0, -2.6, 100, 5), [(handle, QRectF(0, -1.4, 100, 2.8))], 1.0
    )
    assert (frame.first, frame.after) == (0, 6)
    assert (frame.rows[0].first, frame.rows[0].after) == (1, 5)
    for rect in (QRectF(0, 0, 100, 0), QRectF(0, 0, 100, 10_001)):
        with pytest.raises(ValueError, match=r"telegram_(?:geometry|viewport)_invalid"):
            _viewport_frame(uuid4(), rect, [], 1.0)


def test_reported_viewport_uses_actual_full_body_label_rect_and_never_renews_page(
    surface: Surface,
) -> None:
    surface.connect()
    surface.catalog()
    surface.select()
    page = surface.read("SYNTHETIC LINE\n" * 150)
    panel = surface.panel
    panel.body_layout.activate()
    QApplication.processEvents()
    panel._report_viewport()
    frame = surface.port.last("viewport").viewport
    assert frame is not None
    assert frame.page_id == page.page_id
    assert frame.foreground
    viewport = panel.scroll_area.viewport()
    visible = QRectF(viewport.rect())
    visible.moveTopLeft(QPointF(viewport.mapTo(panel.content, QPoint())))
    scale = viewport.devicePixelRatioF()
    assert frame.after - frame.first == math.ceil(
        (visible.top() + visible.height()) * scale
    ) - math.floor(visible.top() * scale)
    body = panel._bodies[0][1]
    rect = QRectF(body.rect())
    rect.moveTopLeft(QPointF(body.mapTo(panel.content, QPoint())))
    assert rect.height() > visible.height()
    assert len(frame.rows) == 1
    assert frame.rows[0].after - frame.rows[0].first == math.ceil(
        (rect.top() + rect.height()) * scale
    ) - math.floor(rect.top() * scale)
    current = surface.controller.page
    assert current is not None
    assert current == page
    assert current.expires_mono == 1300.0
    panel.hide()
    count = surface.port.count("viewport")
    panel._report_viewport()
    assert surface.port.count("viewport") == count
