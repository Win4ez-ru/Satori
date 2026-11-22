"""Offline candidate checks. All identities, fields and worker output are synthetic."""

# ruff: noqa: RUF001 - intentional Russian UI assertion text

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QProcess, QRectF
from PySide6.QtWidgets import QApplication, QLineEdit, QScrollArea

from satori.desktop.telegram_port import (
    LineFramer,
    Message,
    WireRejected,
    WireState,
    schema_from,
    strict_object,
)
from satori.desktop.telegram_runtime import (
    WORKER_SHA256,
    TelegramActualAcceptance,
    TelegramRuntimeController,
    TelegramRuntimeQualification,
    qualification_for,
    verified_resources,
    worker_arguments,
)
from satori.desktop.telegram_ui import NativeTelegramPanel as TelegramPanel
from satori.desktop.telegram_ui import native_viewport_geometry as viewport_geometry
from satori.desktop.ui import DesktopApplication

BASE = Path(__file__).resolve().parents[1] / "src/satori/resources/telegram/backend"


@pytest.fixture(scope="session")
def app() -> QApplication:
    instance = QApplication.instance() or DesktopApplication([])
    assert isinstance(instance, QApplication)
    return instance


@pytest.fixture
def schema() -> dict[str, frozenset[str]]:
    return schema_from((BASE / "schema.json").read_bytes())


@dataclass
class Clock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


def selected(schema: dict[str, frozenset[str]]) -> WireState:
    s = WireState(schema)
    s.confirmed, s.session_deadline = True, 1000
    s.catalog_id, s.catalog_deadline, s.chats = "catalog", 160, (("chat", "Synthetic chat"),)
    s.selected, s.selected_title, s.selection_deadline = "chat", "Synthetic chat", 800
    s.page_id, s.page_deadline = "old-page", 400
    s.known_pages.add("old-page")
    s.messages = (Message("handle", "Synthetic sender", "<b>Synthetic text</b>", 1),)
    return s


def event(kind: str, seq: int, request: str | None = None, **payload: Any) -> dict[str, Any]:
    return {"v": 1, "event_seq": seq, "type": kind, "request_id": request, **payload}


def stale(seq: int, request: str | None = None, page: str | None = "old-page") -> dict[str, Any]:
    return event("page_stale", seq, request, reason="NEW_MESSAGE", page_id=page)


def page(seq: int, request: str, ident: str = "new-page") -> dict[str, Any]:
    return event(
        "page",
        seq,
        request,
        page_id=ident,
        expires_in_seconds=300,
        messages=[],
        bounded=True,
        omitted=0,
    )


def qualification(controller: TelegramRuntimeController) -> TelegramRuntimeQualification:
    return qualification_for(controller.principal)


class SyntheticProcess(QProcess):
    """No child process; lets presentation tests exercise actual controller paths."""

    def state(self) -> QProcess.ProcessState:
        return QProcess.ProcessState.Running


@pytest.fixture
def surface(
    app: QApplication,
    schema: dict[str, frozenset[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[
    tuple[TelegramPanel, TelegramRuntimeController, Clock, list[tuple[str, dict[str, object]]]]
]:
    clock = Clock()
    controller = TelegramRuntimeController(tmp_path, clock=clock)
    controller.state = selected(schema)
    controller.process = SyntheticProcess(controller)
    controller.qualification = qualification(controller)
    controller.visible = True
    commands: list[tuple[str, dict[str, object]]] = []

    def send(
        name: str,
        fields: dict[str, object] | None = None,
        *,
        starting: bool = False,
        track_conceal: bool = False,
    ) -> bool:
        assert controller.state is not None
        controller.state.register("request_" + str(len(commands)), name, clock())
        commands.append((name, fields or {}))
        return True

    monkeypatch.setattr(controller, "_send", send)
    panel = TelegramPanel(tmp_path, controller=controller)
    monkeypatch.setattr(panel, "_foreground", lambda: True)
    panel.resize(900, 900)
    panel._render()
    yield panel, controller, clock, commands
    panel.timer.stop()
    controller._timer.stop()
    controller.process = None
    panel.deleteLater()


def test_default_construction_and_status_are_zero_process(
    app: QApplication, tmp_path: Path
) -> None:
    c = TelegramRuntimeController(tmp_path)
    for _ in range(3):
        c.poll()
        assert c.source_status.connection == "unavailable"
        assert c.source_status.snapshot == "not_read"
    assert c.process is None
    assert c.state is None
    assert c.qualification is None
    c.shutdown()
    assert c.shutdown_complete


def test_native_panel_fits_720px_desktop_without_clipping_its_viewport(
    app: QApplication, tmp_path: Path
) -> None:
    from satori.desktop.settings import DesktopPreferences
    from satori.desktop.ui import DesktopWindow

    window = DesktopWindow(
        DesktopPreferences(
            data_directory=tmp_path,
            repository_directory=tmp_path,
            voice_model_directory=tmp_path / "weights",
            speech_model_directory=tmp_path / "speech-weights",
        )
    )
    window.poll.stop()
    try:
        window.show()
        panel = window.telegram_panel
        window.tabs.setCurrentWidget(panel)
        app.processEvents()
        assert window.height() <= 720
        assert panel.findChildren(QScrollArea) == [panel.scroll_area]
        for control in (
            panel.api_id,
            panel.api_hash,
            panel.connect_button,
            panel.auth_value,
            panel.auth_submit,
            panel.confirm_button,
            panel.chats,
            panel.read_button,
            panel.disconnect_button,
            panel.scroll_area.viewport(),
        ):
            for point in (control.rect().topLeft(), control.rect().bottomRight()):
                assert panel.rect().contains(control.mapTo(panel, point))
                assert window.rect().contains(control.mapTo(window, point))
        assert panel.scroll_area.viewport().height() > 0
        assert panel.controller.process is None
        assert window.process.state() == QProcess.ProcessState.NotRunning
    finally:
        window.quitting = True
        window.tray.hide()
        window.close()
        window.deleteLater()


def test_qualification_does_not_create_actual_acceptance(app: QApplication, tmp_path: Path) -> None:
    c = TelegramRuntimeController(tmp_path)
    assert c.configure_runtime(qualification(c), tmp_path)
    assert c.connect_enabled
    assert c.process is None
    assert c.acceptance.status == "NOT_RUN"
    assert c.acceptance.receipt_sha256 is None
    c.shutdown()


def test_qualification_is_principal_bound(app: QApplication, tmp_path: Path) -> None:
    a, b = TelegramRuntimeController(tmp_path), TelegramRuntimeController(tmp_path)
    assert not a.configure_runtime(qualification(b), tmp_path)
    a.shutdown()
    b.shutdown()


def test_actual_acceptance_requires_honest_separate_receipt() -> None:
    with pytest.raises(ValueError, match="telegram_actual_acceptance_invalid"):
        TelegramActualAcceptance(status="PASS")
    with pytest.raises(ValueError, match="telegram_actual_acceptance_invalid"):
        TelegramActualAcceptance(status="NOT_RUN", receipt_sha256="0" * 64)
    assert TelegramActualAcceptance().receipt_sha256 is None


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":"\xff"}',
        b"[]",
        b"{} {}",
        b'{"a":{' + b'"a":[' * 25 + b"0" + b"]}" * 25 + b"}}",
    ],
)
def test_strict_json_rejects_invalid_bounded_frames(raw: bytes) -> None:
    with pytest.raises(WireRejected):
        strict_object(raw)


def test_strict_json_rejects_escaped_duplicate_and_oversize() -> None:
    for raw in (b'{"a":1,"\\u0061":2}', b" " * 524289):
        with pytest.raises(WireRejected):
            strict_object(raw)


def test_line_framer_fragmented_utf8_and_eof() -> None:
    f = LineFramer()
    raw = json.dumps({"text": "Synthetic Я"}, ensure_ascii=False).encode()
    output: list[bytes] = []
    for byte in raw + b"\n":
        output.extend(f.feed(bytes([byte])))
    assert strict_object(output[0])["text"] == "Synthetic Я"
    f.eof()
    f.feed(b"{")
    with pytest.raises(WireRejected):
        f.eof()


def test_line_framer_exact_limit_and_next_byte() -> None:
    f = LineFramer()
    for _ in range(8):
        f.feed(b" " * 65536)
    with pytest.raises(WireRejected):
        f.feed(b" ")


def test_schema_requires_exact_root_keys(schema: dict[str, frozenset[str]]) -> None:
    value = json.loads((BASE / "schema.json").read_bytes())
    value.pop("page_stale_reason")
    with pytest.raises(WireRejected):
        schema_from(json.dumps(value).encode())
    assert schema["page_stale_reason"] == {"NEW_MESSAGE"}


def test_stale_preserves_exact_selection_and_account_deadlines(
    schema: dict[str, frozenset[str]],
) -> None:
    s = selected(schema)
    s.accept(stale(1), 110)
    assert s.stale
    assert s.page_id is None
    assert not s.messages
    assert s.epoch == 1
    assert s.selected == "chat"
    assert s.confirmed
    assert s.selection_deadline == 800
    assert s.session_deadline == 1000
    assert not s.terminal
    assert s.first_failure is None
    assert not s.sent


def test_idle_stale_old_page_cannot_cancel_new_read(schema: dict[str, frozenset[str]]) -> None:
    s = selected(schema)
    s.register("B", "read", 110)
    epoch = s.epoch
    s.accept(stale(1), 111)
    assert s.pending["read"] == "B"
    assert s.epoch == epoch
    assert not s.stale
    s.accept(page(2, "B"), 112)
    assert s.page_id == "new-page"


def test_correlated_stale_read_retires_late_page(schema: dict[str, frozenset[str]]) -> None:
    s = selected(schema)
    s.register("A", "read", 110)
    s.accept(stale(1, "A", None), 111)
    s.accept(page(2, "A", "late-A-page"), 112)
    assert s.stale
    assert s.page_id is None
    assert not s.pending
    s.register("B", "read", 113)
    epoch = s.epoch
    s.accept(stale(3, "A", None), 114)
    assert s.pending["read"] == "B"
    assert s.epoch == epoch
    s.accept(page(4, "B"), 115)
    assert not s.stale
    assert s.page_id == "new-page"


@pytest.mark.parametrize(
    "change", ["extra", "missing", "null_idle", "unknown_reason", "bool_page", "empty_page"]
)
def test_stale_exact_keys_and_types(schema: dict[str, frozenset[str]], change: str) -> None:
    s = selected(schema)
    value = stale(1)
    if change == "extra":
        value["raw_frame"] = "Synthetic"
    elif change == "missing":
        del value["page_id"]
    elif change == "null_idle":
        value["page_id"] = None
    elif change == "unknown_reason":
        value["reason"] = "SOURCE_CHANGED"
    elif change == "bool_page":
        value["page_id"] = True
    else:
        value["page_id"] = ""
    with pytest.raises(WireRejected):
        s.accept(value, 110)


def test_terminal_stale_tail_cannot_block_cleanup(schema: dict[str, frozenset[str]]) -> None:
    s = selected(schema)
    s.reject()
    s.accept(stale(1), 110)
    s.accept(event("closed", 2, cleanup="CONFIRMED", reason="USER_CLOSE"), 111)
    assert s.closed
    assert s.cleanup == "CONFIRMED"
    assert not s.messages


def test_sequence_and_foreign_requests_reject(schema: dict[str, frozenset[str]]) -> None:
    for e in (stale(2), stale(1, "foreign")):
        with pytest.raises(WireRejected):
            selected(schema).accept(e, 110)


def test_first_backend_failure_survives_teardown(schema: dict[str, frozenset[str]]) -> None:
    s = selected(schema)
    s.accept(
        event(
            "status",
            1,
            phase="IDLE",
            reason="NATIVE_ERROR",
            operation="READ",
            step="HISTORY",
            decoder_reason="NONE",
            wire_bucket="NONE",
        ),
        110,
    )
    first = s.diagnostics
    s.accept(
        event(
            "status",
            2,
            phase="STOPPED",
            reason="NONE",
            operation="CLOSE",
            step="NATIVE_CLOSE",
            decoder_reason="NONE",
            wire_bucket="NONE",
        ),
        111,
    )
    assert s.terminal
    assert s.diagnostics == first


def test_catalog_expiry_state_preserves_selected_lease(schema: dict[str, frozenset[str]]) -> None:
    s = selected(schema)
    assert s.expire(161)
    assert s.catalog_id is None
    assert s.selected == "chat"
    assert s.page_id == "old-page"
    assert s.selection_deadline == 800
    assert s.page_deadline == 400


def test_actual_poll_catalog_expiry_does_not_conceal_or_block_read(surface: Any) -> None:
    panel, c, clock, commands = surface
    clock.now = 161
    c.poll()
    panel._render()
    assert c.state.catalog_id is None
    assert c.state.selected == "chat"
    assert c.state.page_id == "old-page"
    assert not commands
    assert panel.chats.count() == 0
    assert panel.read_button.isEnabled()
    panel.read_button.click()
    assert [name for name, _ in commands] == ["read"]


def test_actual_panel_quiet31s_keeps_text_and_emits_passive_activity(surface: Any) -> None:
    panel, c, clock, commands = surface
    c.last_activity = 100
    for second in range(1, 32):
        clock.now = 100 + second
        panel._tick()
    assert c.state.page_id == "old-page"
    assert c.state.selected == "chat"
    assert c.state.messages
    assert c.state.page_deadline == 400
    assert c.state.selection_deadline == 800
    assert commands
    assert all(name == "viewport" for name, _ in commands)
    assert commands[-1][1]["foreground"] is True
    assert commands[-1][1]["active"] is False


def test_actual_receive_stale_banner_no_autoread(surface: Any) -> None:
    panel, c, _clock, commands = surface
    c._receive(stale(1))
    assert not commands
    assert not c.state.messages
    assert c.state.selected == "chat"
    assert panel.status.text() == "В чате новое сообщение. Обнови просмотр"
    assert panel.read_button.text() == "Обновить"
    assert panel.read_button.isEnabled()
    panel.read_button.click()
    assert [name for name, _ in commands] == ["read"]
    assert panel.status.text() == "В чате новое сообщение. Обнови просмотр"


def test_ignored_stale_and_old_page_preserve_new_request_timeout(surface: Any) -> None:
    _panel, c, clock, _commands = surface
    c.state.register("A", "read", 100)
    c.state.accept(stale(1, "A", None), 101)
    c.state.register("B", "read", 102)
    c._request_id, c._request_deadline = "B", 132
    clock.now = 103
    c._receive(stale(2))
    c._receive(page(3, "A"))
    assert c.state.pending["read"] == "B"
    assert c._request_deadline == 132
    clock.now = 132
    c.poll()
    assert c.state.terminal
    assert c.closing
    assert c.io_diagnostic == "REQUEST_TIMEOUT"


def test_delayed_page_after_request_deadline_never_adopted(surface: Any) -> None:
    _panel, c, clock, _commands = surface
    c.state.register("A", "read", 100)
    c._request_id, c._request_deadline = "A", 130
    clock.now = 131
    c._receive(page(1, "A"))
    assert c.state.terminal
    assert c.state.page_id is None
    assert c.closing


def test_hide_handler_clears_protected_inputs_and_source(surface: Any) -> None:
    panel, c, _clock, commands = surface
    for field in (panel.api_id, panel.api_hash, panel.auth_value):
        field.setText("SYNTHETIC SECRET")
    panel.conceal()
    assert all(not field.text() for field in (panel.api_id, panel.api_hash, panel.auth_value))
    assert c.state.selected is None
    assert not c.state.messages
    assert c.state.confirmed
    assert [name for name, _ in commands] == ["conceal"]


def test_window_deactivation_handler_clears_source(surface: Any) -> None:
    panel, c, _clock, _commands = surface
    panel._observed = panel
    panel.eventFilter(panel, QEvent(QEvent.Type.WindowDeactivate))
    assert not c.visible
    assert c.state.page_id is None


def test_ordinary_focus_switch_clears_hash_but_keeps_numeric_api_id(surface: Any) -> None:
    panel, c, _clock, commands = surface
    c.state = None
    panel.api_id.setText("123456")
    panel.api_hash.setText("a" * 32)
    panel.consent.setChecked(True)
    panel._observed = panel
    panel.eventFilter(panel, QEvent(QEvent.Type.WindowDeactivate))
    assert panel.api_id.text() == "123456"
    assert not panel.api_hash.text()
    assert panel.consent.isChecked()
    assert commands == []


def test_focus_loss_during_code_keeps_pending_login_and_account_confirmation(
    surface: Any, schema: dict[str, frozenset[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    panel, c, _clock, commands = surface
    state = WireState(schema, session_started=100)
    state.register("CODE", "auth_submit", 100)
    c.state = state
    c._request_id, c._request_deadline, c._auth_deadline = "CODE", 130, 400
    panel.auth_value.setText("synthetic")
    panel._observed = panel
    panel.eventFilter(panel, QEvent(QEvent.Type.WindowDeactivate))
    assert not panel.auth_value.text()
    assert c._request_id == "CODE"
    assert c._auth_deadline == 400
    assert commands == []

    c._receive(
        event(
            "account",
            1,
            "CODE",
            account_token="confirm-token",
            label="Synthetic",
            session_seconds=850,
        )
    )
    assert c._request_id is None
    assert not panel.account.text()
    assert not c.confirm_account()
    assert state.account_token == "confirm-token"
    original_deadline = state.session_deadline
    monkeypatch.setattr(panel, "_foreground", lambda: True)
    panel._tick()
    assert panel.account.text() == "Synthetic"
    assert panel.confirm_button.isEnabled()
    panel._confirm()
    assert commands == [("confirm_account", {"account_token": "confirm-token"})]
    assert state.session_deadline == original_deadline


def test_confirmed_workday_login_survives_old_limit_and_focus_without_new_auth(
    surface: Any,
    schema: dict[str, frozenset[str]],
) -> None:
    panel, controller, clock, commands = surface
    state = WireState(schema)
    state.register("PUBLIC_CONNECT", "connect", clock())
    controller.state = state
    controller._request_id, controller._request_deadline = "PUBLIC_CONNECT", 130
    controller._receive(
        event(
            "account",
            1,
            "PUBLIC_CONNECT",
            account_token="PUBLIC_ACCOUNT",
            label="PUBLIC",
            session_seconds=86400,
        )
    )
    panel._confirm()
    confirm_request = state.pending["confirm_account"]
    controller._receive(event("ready", 2, confirm_request, session_seconds=86400))
    assert state.confirmed
    assert state.session_deadline == 86500
    assert "24" in panel.consent.text()
    assert "После перезапуска нужен новый вход" in panel.details_text.text()
    assert panel.disconnect_button.isEnabled()
    for now in (1001.0, 43200.0, 86499.0):
        clock.now = now
        panel._observed = panel
        panel.eventFilter(panel, QEvent(QEvent.Type.WindowDeactivate))
        controller.poll()
        assert state.confirmed
        assert not state.terminal
        assert state.session_deadline == 86500
    assert commands == [("confirm_account", {"account_token": "PUBLIC_ACCOUNT"})]
    clock.now = 86500
    controller.poll()
    assert state.terminal
    assert not state.confirmed
    assert commands[-1] == ("close", {})


@pytest.mark.parametrize("seconds", [900, 86400])
def test_session_bounds_accept_legacy_and_workday_without_deadline_renewal(
    schema: dict[str, frozenset[str]],
    seconds: int,
) -> None:
    state = WireState(schema)
    state.register("PUBLIC_CONNECT", "connect", 100)
    state.accept(
        event(
            "account",
            1,
            "PUBLIC_CONNECT",
            account_token="PUBLIC_ACCOUNT",
            label="PUBLIC",
            session_seconds=seconds,
        ),
        110,
    )
    deadline = state.session_deadline
    state.register("PUBLIC_CONFIRM", "confirm_account", 120)
    state.accept(event("ready", 2, "PUBLIC_CONFIRM", session_seconds=seconds), 120)
    assert state.confirmed
    assert state.session_deadline == deadline <= 100 + 86400


def test_host_rejects_worker_session_above_original_workday_bound(
    schema: dict[str, frozenset[str]],
) -> None:
    state = WireState(schema)
    state.register("PUBLIC_CONNECT", "connect", 100)
    with pytest.raises(WireRejected):
        state.accept(
            event(
                "account",
                1,
                "PUBLIC_CONNECT",
                account_token="PUBLIC_ACCOUNT",
                label="PUBLIC",
                session_seconds=86401,
            ),
            100,
        )


def test_source_expiry_finishes_correlated_view_request_without_stopping_login(
    surface: Any,
) -> None:
    _panel, controller, clock, commands = surface
    state = controller.state
    assert state is not None
    state.session_deadline = 86500
    state.register("PUBLIC_READ", "read", clock())
    controller._request_id, controller._request_deadline = "PUBLIC_READ", 130
    controller._receive(event("cleared", 1, "PUBLIC_READ", reason="PAGE_EXPIRED"))
    assert controller._request_id is None
    assert controller._request_deadline == 0
    assert state.confirmed
    assert not state.terminal
    assert state.selected is None
    assert not state.messages
    clock.now = 1001
    controller.poll()
    assert state.confirmed
    assert commands == []


def test_interrupted_read_waits_for_correlated_conceal_before_new_catalog(
    surface: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel, c, _clock, _commands = surface
    monkeypatch.setattr(c, "_send", TelegramRuntimeController._send.__get__(c))
    monkeypatch.setattr(c, "_flush_writes", lambda *_: None)
    state = c.state
    assert state is not None
    state.register("READ", "read", 100)
    c._request_id, c._request_deadline = "READ", 130
    panel.conceal()
    ident = c._request_id
    assert ident is not None
    assert ident != "READ"
    assert json.loads(c._pending_writes[0])["command"] == "conceal"
    assert len(c._pending_writes) == 1
    panel.conceal()
    assert len(c._pending_writes) == 1
    c.set_foreground(True)
    assert not c.list_enabled
    c._receive(page(1, "READ"))
    assert state.page_id is None
    assert not state.messages
    c._receive(event("cleared", 2, ident, reason="CONCEALED"))
    assert not c.list_enabled
    c._receive(
        event(
            "status",
            3,
            ident,
            phase="READY",
            reason="NONE",
            operation="CONCEAL",
            step="NONE",
            decoder_reason="NONE",
            wire_bucket="NONE",
        )
    )
    assert c.list_enabled
    assert state.confirmed
    assert state.session_deadline == 1000
    assert c._request_id is None
    assert not c.closing
    assert len(c._pending_writes) == 1


def test_secure_fields_are_masked_and_plain_labels_disable_links(surface: Any) -> None:
    panel, _c, _clock, _commands = surface
    assert all(
        field.echoMode() == QLineEdit.EchoMode.Password
        for field in (panel.api_id, panel.api_hash, panel.auth_value)
    )
    assert all(
        not label.openExternalLinks() for label in (panel.account, panel.status, panel.selected)
    )


def test_source_status_is_content_free_and_nonmutating(surface: Any) -> None:
    panel, c, _clock, commands = surface
    before = (
        c.state.epoch,
        c.state.session_deadline,
        c.state.selection_deadline,
        c.state.page_deadline,
    )
    record = panel.source_status
    assert record.source == "telegram"
    assert record.selected_count == 1
    assert record.snapshot == "fresh"
    assert set(record.model_dump()) == {
        "source",
        "connection",
        "selected_count",
        "snapshot",
        "observed_at",
        "busy",
        "problem",
        "catalog_state",
    }
    assert record.catalog_state is None
    assert "Synthetic" not in record.model_dump_json()
    assert not commands
    assert before == (
        c.state.epoch,
        c.state.session_deadline,
        c.state.selection_deadline,
        c.state.page_deadline,
    )


def test_viewport_quantization_keeps_full_body_and_actual_common_origin() -> None:
    span, rows = viewport_geometry(QRectF(0, 0, 100, 100), [("h", QRectF(0, -20, 100, 150))], 2)
    assert span == [40, 240]
    assert rows == [{"handle": "h", "top": 0, "bottom": 300}]


def test_fixed_dispatch_has_no_external_python_or_secrets() -> None:
    fields = (Path("/public/lib.dylib"), "0" * 64, Path("/private/session"))
    assert worker_arguments(*fields, frozen=True)[:1] == ["--telegram-worker"]
    assert worker_arguments(*fields, frozen=False)[:2] == ["-m", "satori.desktop.telegram_worker"]
    assert not any(
        "Python.framework" in field or "api_hash" in field
        for field in worker_arguments(*fields, frozen=True)
    )


def test_runtime_config_pins_and_no_sdk_load(app: QApplication, tmp_path: Path) -> None:
    c = TelegramRuntimeController(tmp_path)
    resources = tmp_path / "resources"
    backend = resources / "telegram/backend"
    sdk = resources / "telegram/sdk"
    backend.mkdir(parents=True)
    sdk.mkdir()
    (backend / "worker.py.source").write_bytes(b"# SYNTHETIC NONEXECUTED WORKER")
    (backend / "schema.json").write_bytes((BASE / "schema.json").read_bytes())
    (sdk / "lib.dylib").write_bytes(b"SYNTHETIC NONLIBRARY BYTES")
    payload = []
    for path in (backend / "worker.py.source", backend / "schema.json", sdk / "lib.dylib"):
        payload.append(
            {
                "path": str(path.relative_to(resources)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )
    config = {
        "v": 2,
        "worker": {"path": payload[0]["path"], "sha256": payload[0]["sha256"]},
        "library": {"path": payload[2]["path"], "sha256": payload[2]["sha256"]},
        "payload": payload,
    }
    config_path = resources / "telegram/runtime-config.json"
    config_path.write_text(json.dumps(config))
    q = TelegramRuntimeQualification(
        principal=c.principal,
        runtime_config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
        policy_sha256="0" * 64,
        backend_sha256=cast(str, payload[0]["sha256"]),
        sdk_sha256=cast(str, payload[2]["sha256"]),
    )
    with pytest.raises(WireRejected):
        verified_resources(resources, q)
    assert c.process is None
    (sdk / "lib.dylib").write_bytes(b"CHANGED")
    with pytest.raises(WireRejected):
        verified_resources(resources, q)
    c.shutdown()


def test_real_qprocess_synthetic_worker_lifetime_and_generation_fence(
    app: QApplication,
    schema: dict[str, frozenset[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import satori.desktop.telegram_runtime as module

    script = """import sys,json
seq=0
def emit(event_type,request=None,**values):
 global seq
 seq+=1
 print(json.dumps(dict(v=1,event_seq=seq,type=event_type,request_id=request,**values)),flush=True)
emit('status',phase='DISCONNECTED',reason='NONE',operation='NONE',step='NONE',decoder_reason='NONE',wire_bucket='NONE')
for line in sys.stdin:
 command=json.loads(line)
 if command['command']=='connect':
  emit('auth',command['request_id'],challenge_id='synthetic',kind='PHONE')
 if command['command']=='close':
  emit('closed',command['request_id'],cleanup='NOT_CREATED',reason='USER_CLOSE')
  break
"""
    monkeypatch.setattr(
        module, "verified_resources", lambda *_: (tmp_path / "unused.dylib", schema)
    )
    monkeypatch.setattr(module, "worker_arguments", lambda *_, **__: ["-c", script])
    c = TelegramRuntimeController(tmp_path)
    assert c.configure_runtime(qualification(c), tmp_path)
    c.set_foreground(True)
    assert c.connect_account("1", "0" * 32, consent=True)
    process, generation = c.process, c.generation
    assert process is not None
    assert not c.shutdown_complete
    deadline = time.monotonic() + 5
    while c.state and c.state.challenge is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert c.state is not None
    assert c.state.challenge == ("synthetic", "PHONE")
    assert "0" * 32 not in process.arguments()
    assert "1" not in process.arguments()
    c.shutdown()
    assert not c.shutdown_complete
    while not c.shutdown_complete and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert c.shutdown_complete
    assert c.state
    assert c.state.closed
    assert c.state.cleanup == "NOT_CREATED"
    assert c.exit_description == "exit 0"
    assert c.acceptance.status == "NOT_RUN"
    epoch = c.state.epoch
    c._channel_ended(process, generation)
    c._started(process, generation)
    assert c.state.epoch == epoch
    assert c.process is None
    c.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_unknown_stale_page_is_rejected(schema: dict[str, frozenset[str]]) -> None:
    with pytest.raises(WireRejected):
        selected(schema).accept(stale(1, page="foreign-page"), 110)


def test_known_page_metadata_has_finite_bound(schema: dict[str, frozenset[str]]) -> None:
    s = selected(schema)
    s.known_pages = {f"known_{i}" for i in range(512)}
    s.register("A", "read", 110)
    with pytest.raises(WireRejected):
        s.accept(page(1, "A"), 111)
    assert len(s.known_pages) == 512


def test_old_epoch_page_id_is_known_for_later_stale_tail(schema: dict[str, frozenset[str]]) -> None:
    s = selected(schema)
    s.register("A", "read", 110)
    s.conceal()
    s.accept(page(1, "A", "late-known-page"), 111)
    s.accept(stale(2, page="late-known-page"), 112)
    assert s.page_id is None
    assert "late-known-page" in s.known_pages


def test_reused_page_token_cannot_gain_fresh_epoch(schema: dict[str, frozenset[str]]) -> None:
    s = selected(schema)
    s.register("A", "read", 110)
    with pytest.raises(WireRejected):
        s.accept(page(1, "A", "old-page"), 111)


def test_qualification_for_is_pure_and_independent_from_actual_read(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = TelegramRuntimeController(tmp_path)
    monkeypatch.setattr(
        Path, "read_bytes", lambda _: (_ for _ in ()).throw(AssertionError("unexpected file read"))
    )
    q = qualification_for(c.principal)
    assert q.backend_sha256 == WORKER_SHA256
    assert c.configure_runtime(q, tmp_path)
    assert c.acceptance.status == "NOT_RUN"
    assert c.process is None
    c.shutdown()


def test_crash_exit_does_not_invent_actual_signal(surface: Any) -> None:
    _panel, c, _clock, _commands = surface
    process = c.process
    assert process is not None
    c._finished(process, c.generation, 6, QProcess.ExitStatus.CrashExit)
    assert c.exit_description == "CRASH_EXIT / SIGNAL_UNKNOWN"
    assert c.shutdown_complete


def test_pending_account_preview_cannot_extend_request_deadline(
    surface: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel, c, _clock, commands = surface
    c.state.confirmed = False
    c.state.account_preview_needed = True
    c._request_id, c._request_deadline = "preview", 130
    panel._render()
    assert not panel.confirm_button.isEnabled()
    assert not c.confirm_account()
    assert c._request_deadline == 130
    assert commands == []


def test_static_resource_loader_accepts_only_exact_synthetic_qualified_bytes(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import satori.desktop.telegram_runtime as module

    files = {
        module.WORKER_PATH: b"# SYNTHETIC NONEXECUTED WORKER",
        module.SCHEMA_PATH: (BASE / "schema.json").read_bytes(),
        module.LIBRARY_PATH: b"SYNTHETIC NONLIBRARY BYTES",
    }
    pins = {name: (hashlib.sha256(raw).hexdigest(), len(raw)) for name, raw in files.items()}
    for key, name in (
        ("WORKER_SHA256", module.WORKER_PATH),
        ("SCHEMA_SHA256", module.SCHEMA_PATH),
        ("LIBRARY_SHA256", module.LIBRARY_PATH),
    ):
        monkeypatch.setattr(module, key, pins[name][0])
    monkeypatch.setattr(module, "EXPECTED_PAYLOAD", pins)
    policy = b"S" * 6951
    monkeypatch.setattr(module, "POLICY_SHA256", hashlib.sha256(policy).hexdigest())
    for name, raw in {
        **files,
        module.POLICY_PATH: policy,
        "telegram/runtime-config.json": module.runtime_config_bytes(),
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    resources = module.load_runtime_resources(tmp_path)
    assert resources.library == tmp_path / module.LIBRARY_PATH
    (tmp_path / module.POLICY_PATH).write_bytes(b"CHANGED")
    with pytest.raises(WireRejected):
        module.load_runtime_resources(tmp_path)


def test_source_status_invalid_clock_keeps_unknown_selected_validity(
    surface: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel, c, _clock, commands = surface
    monkeypatch.setattr(c, "clock", lambda: float("nan"))
    assert panel.source_status.selected_count is None
    assert panel.source_status.snapshot == "unknown"
    assert panel.source_status.problem == "unknown"
    assert not commands

    def failed_clock() -> float:
        raise ValueError("SYNTHETIC")

    monkeypatch.setattr(c, "clock", failed_clock)
    assert panel.source_status.selected_count is None
    assert not commands


def test_shutdown_detaches_global_filter_before_synchronous_callbacks(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QWidget

    probes: list[bool] = []

    class ObservedPanel(TelegramPanel):
        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            if event.type() == QEvent.Type.User:
                probes.append(True)
            return super().eventFilter(watched, event)

    panel, unrelated = ObservedPanel(tmp_path), QWidget()
    observations: list[tuple[str, int]] = []

    def observe(name: str) -> None:
        app.sendEvent(unrelated, QEvent(QEvent.Type.User))
        observations.append((name, len(probes)))

    original_shutdown = panel.controller.shutdown

    def shutdown() -> None:
        observe("controller.shutdown")
        original_shutdown()

    monkeypatch.setattr(panel.controller, "shutdown", shutdown)
    panel.controller.shutdown_finished.connect(lambda: observe("shutdown_finished"))
    try:
        assert panel.controller.process is None
        panel.conceal()
        app.sendEvent(unrelated, QEvent(QEvent.Type.User))
        assert len(probes) == 1
        panel.shutdown()
        assert observations == [("controller.shutdown", 1), ("shutdown_finished", 1)]
        panel.shutdown()
        app.sendEvent(unrelated, QEvent(QEvent.Type.User))
        assert len(probes) == 1
        assert panel.controller.shutdown_complete
        assert not panel.timer.isActive()
    finally:
        app.removeEventFilter(panel)
        panel.timer.stop()
        panel.controller._timer.stop()
        panel.deleteLater()
        unrelated.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
