"""Offscreen mobile settings gestures with fake owners and synthetic private namespaces."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QLayout, QSizePolicy, QVBoxLayout, QWidget

from satori.desktop import mobile_settings_ui as ui
from satori.desktop.mobile_gateway.contracts import CoreBridge, ScopeBinding
from satori.desktop.workspace_ui import _STYLE


class Bridge:
    def submit(self, *_: object) -> None:
        pytest.fail("Settings must never submit Core operations.")


class Runtime:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.auth = self
        self.identity = self
        self.server = self
        self.port = 8766
        self.sha256 = "a" * 64
        self.pairings: list[set[str]] = []
        self.cancelled = 0
        self.revoked = 0
        self.closed = 0
        self.fail_revoke = False

    def begin_pairing(self, capabilities: set[str]) -> str:
        self.pairings.append(set(capabilities))
        return "123456"

    def cancel_pairing(self) -> None:
        self.cancelled += 1

    def revoke_all(self) -> None:
        self.events.append("revoke")
        if self.fail_revoke:
            raise OSError("sensitive cause must never be displayed")
        self.revoked += 1

    def close(self) -> None:
        self.events.append("close")
        self.cancel_pairing()
        self.closed += 1


@pytest.fixture
def app() -> Iterator[QApplication]:
    application = QApplication.instance() or QApplication([])
    assert isinstance(application, QApplication)
    yield application
    application.processEvents()


@pytest.fixture
def panel(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]]]:
    runtimes: list[Runtime] = []
    calls: list[tuple[object, ...]] = []
    events: list[str] = []

    def open_runtime(*arguments: object) -> ui._Runtime:
        calls.append(arguments)
        runtime = Runtime(events)
        runtimes.append(runtime)
        return cast(ui._Runtime, runtime)

    monkeypatch.setattr(ui, "_open_runtime", open_runtime)
    widget = ui.MobileSettingsPanel(
        cast(CoreBridge, Bridge()),
        lambda: ScopeBinding("uid", "profile", 0),
        tmp_path / "profile-a",
    )
    yield widget, runtimes, calls, events
    widget.shutdown()
    widget.deleteLater()
    app.processEvents()


def test_constructor_is_off_without_owner_io_or_core_calls(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    tmp_path: Path,
) -> None:
    widget, _, calls, _ = panel
    assert not widget.enabled
    assert calls == []
    assert not (tmp_path / "profile-a").exists()
    assert widget.bind_host.text() == "127.0.0.1"
    assert widget.advertised_host.text() == "127.0.0.1"
    assert widget.port.value() == 8766
    assert not widget.allow_lan.isChecked()
    assert not widget.chat_capability.isChecked()
    assert not widget.history_capability.isChecked()
    assert not widget.reminders_capability.isChecked()
    assert not widget.pair_button.isEnabled()
    widget.refresh_status()
    assert calls == []


def test_enable_is_explicit_and_renders_endpoint_and_tls_pin(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    tmp_path: Path,
) -> None:
    widget, runtimes, calls, _ = panel
    widget.enable_button.click()
    assert widget.enabled
    assert len(calls) == 1
    assert calls[0][2] == tmp_path / "profile-a" / "mobile-v1"
    assert calls[0][3:] == ("127.0.0.1", "127.0.0.1", 8766, False)
    assert widget.endpoint_label.text() == "https://127.0.0.1:8766"
    assert runtimes[0].sha256 in widget.fingerprint_label.text()
    assert "проверь на телефоне" in widget.status_label.text()


@pytest.mark.parametrize(
    "address", ["0.0.0.0", "8.8.8.8", "169.254.169.254", "::", "::1", "fd00::1"]
)
def test_unsafe_or_ipv6_bind_is_rejected_before_owner_creation(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    address: str,
) -> None:
    widget, _, calls, _ = panel
    widget.bind_host.setText(address)
    widget.allow_lan.setChecked(True)
    widget.enable_button.click()
    assert not widget.enabled
    assert calls == []


@pytest.mark.parametrize(
    "address", ["https://mac.local", "*.local", "8.8.8.8", "metadata.google.internal", "::1"]
)
def test_unsafe_advertised_identity_is_rejected_before_owner_creation(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    address: str,
) -> None:
    widget, _, calls, _ = panel
    widget.advertised_host.setText(address)
    widget.allow_lan.setChecked(True)
    widget.enable_button.click()
    assert calls == []


def test_private_lan_needs_explicit_checkbox_and_separate_advertised_identity(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
) -> None:
    widget, _, calls, _ = panel
    widget.bind_host.setText("192.168.1.20")
    widget.advertised_host.setText("satori-mac.local")
    widget.enable_button.click()
    assert calls == []
    widget.allow_lan.setChecked(True)
    widget.enable_button.click()
    assert calls[0][3:] == ("192.168.1.20", "satori-mac.local", 8766, True)


def test_pairing_grants_only_explicit_choices_and_clears_after_local_window(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget, runtimes, _, _ = panel
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    widget.enable_button.click()
    widget.reminders_capability.setChecked(True)
    widget.pair_button.click()
    assert runtimes[0].pairings == [{"status", "reminders"}]
    assert widget.pair_code_label.text() == "123456"
    assert "120" in widget.pair_timer_label.text()
    now[0] = 221
    widget.refresh_status()
    assert widget.pair_code_label.text() == ""
    assert widget.pair_timer_label.text() == ""
    assert runtimes[0].cancelled >= 1


def test_chat_without_history_never_silently_expands_permissions(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
) -> None:
    widget, runtimes, _, _ = panel
    widget.enable_button.click()
    widget.chat_capability.setChecked(True)
    assert not widget.history_capability.isChecked()
    assert not widget.pair_button.isEnabled()
    assert "история" in widget.capabilities_hint.text()
    widget._begin_pairing()
    assert runtimes[0].pairings == []
    widget.history_capability.setChecked(True)
    assert widget.pair_button.isEnabled()
    widget.pair_button.click()
    assert runtimes[0].pairings == [{"status", "chat", "history"}]


def test_capability_edit_cancels_pairing_and_never_mutates_existing_grant(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
) -> None:
    widget, runtimes, _, _ = panel
    widget.enable_button.click()
    widget.pair_button.click()
    widget.history_capability.setChecked(True)
    assert widget.pair_code_label.text() == ""
    assert runtimes[0].cancelled >= 1
    assert runtimes[0].revoked == 0


def test_disable_shutdown_and_core_stop_close_runtime_without_revocation_or_restart(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
) -> None:
    widget, runtimes, calls, _ = panel
    widget.enable_button.click()
    widget.pair_button.click()
    widget.disable_button.click()
    assert not widget.enabled
    assert widget.pair_code_label.text() == ""
    assert widget.endpoint_label.text() == ""
    assert runtimes[0].closed == 1
    assert runtimes[0].revoked == 0
    widget.enable_button.click()
    widget.core_stopped()
    widget.refresh_status()
    assert not widget.enabled
    assert len(calls) == 2
    assert runtimes[1].closed == 1
    assert runtimes[1].revoked == 0


def test_endpoint_edit_stops_old_listener_without_automatic_rebind(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
) -> None:
    widget, runtimes, calls, _ = panel
    widget.enable_button.click()
    widget.port.setValue(8767)
    assert not widget.enabled
    assert runtimes[0].closed == 1
    assert len(calls) == 1


def test_revoke_is_durable_before_owner_close_and_clears_code(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
) -> None:
    widget, runtimes, _, events = panel
    widget.enable_button.click()
    widget.pair_button.click()
    widget.revoke_button.click()
    assert events == ["revoke", "close"]
    assert runtimes[0].revoked == 1
    assert widget.pair_code_label.text() == ""
    assert not widget.enabled


def test_profile_switch_revokes_active_owner_before_close_and_stays_off(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    tmp_path: Path,
) -> None:
    widget, _, calls, events = panel
    widget.enable_button.click()
    widget.set_profile(tmp_path / "profile-b")
    assert events == ["revoke", "close"]
    assert not widget.enabled
    assert len(calls) == 1


def test_profile_switch_after_disable_still_revokes_old_namespace(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget, _, _, _ = panel
    revocations: list[Path] = []
    monkeypatch.setattr(ui, "_revoke_namespace", lambda path, _: revocations.append(path))
    widget.enable_button.click()
    widget.shutdown()
    widget.set_profile(tmp_path / "profile-b")
    widget.set_profile(tmp_path / "profile-a")
    assert revocations == [
        tmp_path / "profile-a" / "mobile-v1",
        tmp_path / "profile-b" / "mobile-v1",
    ]
    assert not widget.enabled


def test_failed_durable_revoke_rejects_new_profile_and_blocks_reenable(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    tmp_path: Path,
) -> None:
    widget, runtimes, calls, _ = panel
    widget.enable_button.click()
    runtimes[0].fail_revoke = True
    with pytest.raises(ui.MobileProfileChangeError, match=r"^mobile_profile_revoke_failed$"):
        widget.set_profile(tmp_path / "profile-b")
    assert not widget.enabled
    assert not widget.enable_button.isEnabled()
    assert len(calls) == 1
    assert "sensitive cause" not in widget.status_label.text()


def test_real_preserved_grant_is_revoked_across_a_b_a_at_same_generation(
    app: QApplication,
    tmp_path: Path,
) -> None:
    from satori.desktop.mobile_gateway.auth import AuthOwner
    from satori.desktop.mobile_gateway.contracts import GatewayFault
    from satori.desktop.mobile_gateway.private_store import PrivateStore

    first = tmp_path / "a"
    first.mkdir(mode=0o700)
    scope = ScopeBinding("uid", "stable-a", 0)
    store = PrivateStore(first / "mobile-v1")
    auth = AuthOwner(store, lambda: scope, "synthetic-transport")
    credentials = auth.pair(auth.begin_pairing({"chat", "history"}), "synthetic device")
    token = credentials["token"]
    assert isinstance(token, str)
    store.close()
    widget = ui.MobileSettingsPanel(cast(CoreBridge, Bridge()), lambda: scope, first)
    try:
        widget.set_profile(tmp_path / "b")
        widget.set_profile(first)
        store = PrivateStore(first / "mobile-v1")
        try:
            reopened = AuthOwner(store, lambda: scope, "synthetic-transport")
            with pytest.raises(GatewayFault):
                reopened.authenticate(token)
        finally:
            store.close()
    finally:
        widget.shutdown()
        widget.deleteLater()
        app.processEvents()


def test_explicit_rotation_revokes_before_close_and_never_restarts_listener(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget, _, calls, events = panel

    def rotate(_: Path, host: str) -> str:
        assert host == "127.0.0.1"
        events.append("rotate")
        return "b" * 64

    monkeypatch.setattr(ui, "_rotate_namespace", rotate)
    widget.enable_button.click()
    widget.pair_button.click()
    widget.rotate_button.click()
    assert events == ["revoke", "close", "rotate"]
    assert not widget.enabled
    assert len(calls) == 1
    assert widget.pair_code_label.text() == ""
    assert "b" * 64 in widget.fingerprint_label.text()


def test_rotation_recovery_works_while_off_with_changed_san(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget, _, calls, events = panel

    def rotate(*_: object) -> str:
        events.append("rotate")
        return "c" * 64

    monkeypatch.setattr(ui, "_revoke_namespace", lambda *_: events.append("revoke-persisted"))
    monkeypatch.setattr(ui, "_rotate_namespace", rotate)
    widget.advertised_host.setText("new-mac.local")
    widget.rotate_button.click()
    assert events == ["revoke-persisted", "rotate"]
    assert calls == []
    assert not widget.enabled
    assert widget.endpoint_label.text() == "https://new-mac.local:8766"


def test_rotation_does_not_touch_certificate_if_durable_revocation_fails(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget, runtimes, _, events = panel

    def rotate(*_: object) -> str:
        events.append("rotate")
        return "d" * 64

    monkeypatch.setattr(ui, "_rotate_namespace", rotate)
    widget.enable_button.click()
    runtimes[0].fail_revoke = True
    widget.rotate_button.click()
    assert events == ["revoke", "close"]
    assert not widget.enabled
    assert not widget.enable_button.isEnabled()


def test_invalid_rotation_identity_is_rejected_before_any_private_io(
    panel: tuple[ui.MobileSettingsPanel, list[Runtime], list[tuple[object, ...]], list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget, _, _, events = panel

    def rotate(*_: object) -> str:
        events.append("rotate")
        return "e" * 64

    monkeypatch.setattr(ui, "_revoke_namespace", lambda *_: events.append("revoke"))
    monkeypatch.setattr(ui, "_rotate_namespace", rotate)
    widget.advertised_host.setText("8.8.8.8")
    widget.rotate_button.click()
    assert events == []


@pytest.mark.parametrize("width", [570, 780])
def test_source_pane_scroll_preserves_controls_under_ignored_parent_constraints(
    app: QApplication,
    tmp_path: Path,
    width: int,
) -> None:
    host = QWidget()
    host.setObjectName("SatoriWorkspace")
    host.setStyleSheet(_STYLE)
    host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
    widget = ui.MobileSettingsPanel(
        cast(CoreBridge, Bridge()), lambda: ScopeBinding("uid", "profile", 0), tmp_path
    )
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    layout.addWidget(widget)
    host.resize(width, 627)
    try:
        host.show()
        app.processEvents()
        controls = (
            widget.bind_host,
            widget.advertised_host,
            widget.port,
            widget.allow_lan,
            widget.chat_capability,
            widget.history_capability,
            widget.reminders_capability,
            widget.enable_button,
            widget.disable_button,
            widget.pair_button,
            widget.revoke_button,
            widget.rotate_button,
        )
        assert all(control.height() >= 32 for control in controls)
        assert widget.scroll_area.widgetResizable()
        assert widget.scroll_area.verticalScrollBar().maximum() > 0
        assert widget.scroll_content.height() > widget.scroll_area.viewport().height()
        assert widget.scroll_area.horizontalScrollBar().maximum() == 0
        assert widget.reminders_capability.text() == "Напоминания: просмотр, создание и отмена"
        assert widget.reminders_capability.width() >= widget.reminders_capability.sizeHint().width()
        widget.scroll_area.ensureWidgetVisible(widget.rotate_button, 0, 8)
        app.processEvents()
        viewport = widget.scroll_area.viewport()
        position = widget.rotate_button.mapTo(viewport, QPoint(0, 0))
        assert position.y() >= 0
        assert position.y() + widget.rotate_button.height() <= viewport.height()
        assert widget.scroll_area.verticalScrollBar().value() > 0
        widget.scroll_area.verticalScrollBar().setValue(0)
        app.processEvents()
        assert widget.bind_host.mapTo(viewport, QPoint(0, 0)).y() >= 0
        assert widget.bind_host.height() >= 32
    finally:
        widget.shutdown()
        host.close()
        host.deleteLater()
        app.processEvents()
