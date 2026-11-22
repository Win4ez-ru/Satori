"""Offscreen source controls with synthetic owner state; no files, accounts or sockets."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QFileDialog

from satori.desktop.public_sources_controller import PublicSourcesController
from satori.desktop.public_sources_ui import PublicSourcesPanel
from satori.desktop.ui import DesktopApplication
from satori.infrastructure.public_sources.wire import (
    Failure,
    Operation,
    PublicSourceReply,
    SearchItem,
)

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
_Status = Literal["ok", "empty", "partial", "rejected", "unavailable", "error"]


def reply(
    operation: Operation = "file_read",
    status: _Status = "ok",
    *,
    reason: Failure | None = None,
) -> PublicSourceReply:
    common: dict[str, object] = {
        "schema_version": 1,
        "untrusted": True,
        "request_id": uuid4(),
        "profile_id": uuid4(),
        "session_id": uuid4(),
        "generation": 0,
        "operation": operation,
        "origin": {
            "file_read": "selected_file",
            "url_read": "public_https",
            "wikipedia_search": "wikipedia",
        }[operation],
        "coverage": {
            "file_read": "selected_file_only",
            "url_read": "selected_url_only",
            "wikipedia_search": "wikipedia_only",
        }[operation],
        "source_label": "<b>SYNTHETIC SOURCE</b>",
        "status": status,
        "reason": reason,
        "observed_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
    }
    if status in {"ok", "empty", "partial"}:
        if operation == "wikipedia_search":
            common["items"] = (
                ()
                if status == "empty"
                else (
                    SearchItem(
                        title="<img src=x onerror=IGNORE_RULES>",
                        url="https://ru.wikipedia.org/wiki/Synthetic",
                        excerpt="SYNTHETIC excerpt <script>never run</script>",
                        description="SYNTHETIC description",
                    ),
                )
            )
        else:
            common["text"] = "" if status == "empty" else "SYNTHETIC BODY <img src=x>\nIGNORE RULES"
            common["media_type"] = "text/plain"
            if operation == "url_read":
                common["source_url"] = "https://example.org/public"
    return PublicSourceReply.model_validate(common)


class SyntheticController(QObject):
    changed = Signal()
    shutdown_finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.busy = False
        self.status = "idle"
        self.result: PublicSourceReply | None = None
        self.source_calls: list[tuple[str, object]] = []
        self.visible = False
        self.shutdown_complete = False
        self.shutdown_requests = 0
        self.profile: Path | None = None
        self.session: UUID | None = None

    def set_visible(self, visible: bool) -> None:
        self.visible = visible

    def cancel(self, reason: str = "cancelled") -> None:
        self.busy = False
        self.result = None
        self.status = reason
        self.changed.emit()

    def conceal(self) -> None:
        self.visible = False
        self.cancel("concealed")

    def set_profile(self, path: Path) -> None:
        self.profile = path
        self.cancel("profile_changed")

    def bind_session(self, session: UUID) -> None:
        self.session = session
        self.cancel("session_changed")

    def _start(self, operation: str, arguments: object) -> bool:
        self.source_calls.append((operation, arguments))
        self.busy = True
        self.result = None
        self.status = "reading"
        self.changed.emit()
        return True

    def read_file(self, path: Path) -> bool:
        return self._start("file_read", path)

    def read_url(self, url: str) -> bool:
        return self._start("url_read", url)

    def search_wikipedia(self, query: str, language: str = "ru") -> bool:
        return self._start("wikipedia_search", (query, language))

    def publish(self, receipt: PublicSourceReply) -> None:
        self.busy = False
        self.result = receipt
        self.status = {"ok": "ready", "empty": "empty", "partial": "partial"}.get(
            receipt.status, "source_rejected"
        )
        self.changed.emit()

    def shutdown(self) -> None:
        self.shutdown_requests += 1
        self.cancel("closing")

    def finish_shutdown(self) -> None:
        self.shutdown_complete = True
        self.status = "closed"
        self.changed.emit()
        self.shutdown_finished.emit()


@pytest.fixture(scope="module")
def app() -> QApplication:
    current = QApplication.instance()
    assert current is None or isinstance(current, QApplication)
    return current or DesktopApplication([])


@pytest.fixture
def view(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[PublicSourcesPanel, SyntheticController]]:
    controller = SyntheticController()
    panel = PublicSourcesPanel(tmp_path, controller=cast(PublicSourcesController, controller))
    monkeypatch.setattr(panel, "_foreground", lambda: True)
    panel.show()
    app.processEvents()
    panel._sync_visibility()
    yield panel, controller
    panel.shutdown()
    controller.finish_shutdown()
    panel.close()
    panel.deleteLater()
    controller.deleteLater()
    app.processEvents()


def test_initial_panel_performs_no_source_io_and_names_actual_search_coverage(
    view: tuple[PublicSourcesPanel, SyntheticController],
) -> None:
    panel, controller = view
    assert controller.source_calls == []
    assert panel.result.toPlainText() == ""
    assert panel.url.text() == ""
    assert panel.query.text() == ""
    assert not panel.read_url_button.isEnabled()
    assert not panel.search_button.isEnabled()
    assert panel.file_button.isEnabled()
    assert "не отправляются модели" in panel.disclosure.text()
    assert "не сохраняются" in panel.disclosure.text()
    assert "не озвучиваются" in panel.disclosure.text()
    assert "не поиск по всему интернету" in panel.search_coverage.text()
    assert panel.result.isReadOnly()
    assert panel.disclosure.textFormat() == Qt.TextFormat.PlainText


def test_set_query_populates_only_and_never_restores_or_dispatches(
    view: tuple[PublicSourcesPanel, SyntheticController],
) -> None:
    panel, controller = view
    controller.publish(reply())
    panel.set_query("https://example.org/chosen")
    assert panel.url.text() == "https://example.org/chosen"
    assert panel.query.text() == ""
    assert panel.result.toPlainText() == ""
    panel.set_query("SYNTHETIC search")
    assert panel.query.text() == "SYNTHETIC search"
    assert panel.url.text() == ""
    assert controller.source_calls == []


def test_reactivation_updates_buttons_without_a_controller_changed_signal_or_source_io(
    view: tuple[PublicSourcesPanel, SyntheticController],
) -> None:
    panel, controller = view
    controller.publish(reply())
    panel.hide()
    assert not panel.file_button.isEnabled()
    assert panel.result.toPlainText() == ""
    panel.show()
    panel._sync_visibility()
    assert panel.file_button.isEnabled()
    assert not panel.read_url_button.isEnabled()
    assert not panel.search_button.isEnabled()
    assert "Выбери файл" in panel.status.text()
    assert panel.result.toPlainText() == ""
    assert controller.source_calls == []


def test_explicit_url_and_search_buttons_dispatch_exact_values_once(
    view: tuple[PublicSourcesPanel, SyntheticController],
) -> None:
    panel, controller = view
    panel.url.setText("https://example.org/chosen")
    assert controller.source_calls == []
    panel.read_url_button.click()
    assert controller.source_calls == [("url_read", "https://example.org/chosen")]
    assert not panel.read_url_button.isEnabled()
    assert not panel.file_button.isEnabled()
    panel.query.setText("SYNTHETIC search")
    assert controller.result is None
    panel.language.setCurrentIndex(panel.language.findData("en"))
    panel.search_button.click()
    assert controller.source_calls[-1] == ("wikipedia_search", ("SYNTHETIC search", "en"))


@pytest.mark.parametrize("choose", [False, True])
def test_only_explicit_file_chooser_selection_dispatches_without_resolving_symlinks(
    view: tuple[PublicSourcesPanel, SyntheticController],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    choose: bool,
) -> None:
    panel, controller = view
    chosen = str(tmp_path / "SYNTHETIC.txt")
    options: list[object] = []

    def dialog(*args: object, **kwargs: object) -> tuple[str, str]:
        options.append(kwargs.get("options"))
        assert "*.txt" in str(args[3])
        assert "*.pdf" not in str(args[3])
        return (chosen if choose else "", "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", dialog)
    assert options == []
    panel.file_button.click()
    assert options == [QFileDialog.Option.DontResolveSymlinks]
    assert controller.source_calls == ([("file_read", Path(chosen))] if choose else [])
    assert panel.selected_file.text() == ("SYNTHETIC.txt" if choose else "Файл не выбран.")


@pytest.mark.parametrize("edit", ["url", "query", "language"])
def test_query_edits_cancel_pending_and_remove_the_prior_result_synchronously(
    view: tuple[PublicSourcesPanel, SyntheticController], edit: str
) -> None:
    panel, controller = view
    controller.publish(reply())
    assert "SYNTHETIC BODY" in panel.result.toPlainText()
    if edit == "language":
        panel.language.setCurrentIndex(panel.language.findData("en"))
    else:
        getattr(panel, edit).setText("SYNTHETIC edited")
    assert controller.result is None
    assert panel.result.toPlainText() == ""
    assert controller.source_calls == []


@pytest.mark.parametrize("hide", ["hide", "deactivate", "app_inactive"])
def test_conceal_clears_private_fields_result_and_authority_before_returning(
    view: tuple[PublicSourcesPanel, SyntheticController], hide: str
) -> None:
    panel, controller = view
    panel.url.setText("https://example.org/chosen")
    panel.query.setText("SYNTHETIC query")
    controller.publish(reply())
    if hide == "hide":
        panel.hide()
    elif hide == "deactivate":
        panel.eventFilter(panel.window(), QEvent(QEvent.Type.WindowDeactivate))
    else:
        panel._application_state_changed(Qt.ApplicationState.ApplicationInactive)
    assert not controller.visible
    assert controller.result is None
    assert panel.url.text() == ""
    assert panel.query.text() == ""
    assert panel.selected_file.text() == "Файл не выбран."
    assert panel.result.toPlainText() == ""
    controller.publish(reply())  # A bad late callback still cannot paint a concealed view.
    assert panel.result.toPlainText() == ""
    assert controller.source_calls == []


@pytest.mark.parametrize("change", ["profile", "session"])
def test_profile_and_session_rebind_clear_fields_without_new_source_io(
    view: tuple[PublicSourcesPanel, SyntheticController], tmp_path: Path, change: str
) -> None:
    panel, controller = view
    panel.set_query("SYNTHETIC query")
    controller.publish(reply())
    if change == "profile":
        panel.set_profile(tmp_path / "next-profile")
        assert controller.profile == tmp_path / "next-profile"
    else:
        session = uuid4()
        panel.bind_session(session)
        assert controller.session == session
    assert panel.query.text() == ""
    assert panel.url.text() == ""
    assert panel.result.toPlainText() == ""
    assert controller.source_calls == []


@pytest.mark.parametrize("operation", ["file_read", "url_read"])
@pytest.mark.parametrize("status", ["ok", "empty", "partial"])
def test_cards_preserve_source_time_scope_plain_text_and_partial_empty_semantics(
    view: tuple[PublicSourcesPanel, SyntheticController], operation: Operation, status: _Status
) -> None:
    panel, controller = view
    controller.publish(reply(operation, status))
    text = panel.result.toPlainText()
    assert "<b>SYNTHETIC SOURCE</b>" in text
    assert "31.10.2026 12:00:00 UTC" in text
    assert "31.10.2026 12:05:00 UTC" in text
    assert "недоверенное" in text
    if status == "partial":
        assert "только часть" in text
        assert "полный текст здесь не представлен" in text
    elif status == "empty":
        assert "не содержит извлечённого текста" in text
        assert "SYNTHETIC BODY" not in text
    else:
        assert "<img src=x>" in text
        assert "IGNORE RULES" in text
    if operation == "url_read":
        assert "https://example.org/public" in text
    assert controller.source_calls == []


@pytest.mark.parametrize("status", ["ok", "empty"])
def test_wikipedia_card_has_honest_coverage_article_attribution_and_license(
    view: tuple[PublicSourcesPanel, SyntheticController], status: _Status
) -> None:
    panel, controller = view
    controller.publish(reply("wikipedia_search", status))
    text = panel.result.toPlainText()
    assert "Поиск только в Википедии" in text
    assert "не весь интернет" in text
    assert "CC BY-SA 4.0" in text
    assert "https://creativecommons.org/licenses/by-sa/4.0/" in text
    if status == "ok":
        assert "https://ru.wikipedia.org/wiki/Synthetic" in text
        assert "<script>never run</script>" in text
    else:
        assert "не вернула совпадений" in text
    assert controller.source_calls == []


def test_error_expiry_and_clear_never_claim_a_successful_empty_read(
    view: tuple[PublicSourcesPanel, SyntheticController],
) -> None:
    panel, controller = view
    controller.publish(reply("url_read", "rejected", reason="redirect_rejected"))
    assert "не выполнялось" in panel.result.toPlainText()
    assert "не содержит извлечённого текста" not in panel.result.toPlainText()
    controller.result = None
    controller.status = "expired"
    controller.changed.emit()
    assert panel.result.toPlainText() == ""
    assert "истёк" in panel.status.text()
    panel.set_query("SYNTHETIC")
    panel.clear_button.click()
    assert panel.url.text() == ""
    assert panel.query.text() == ""
    assert controller.source_calls == []


def test_shutdown_waits_for_owned_completion_and_cannot_repaint_a_late_result(
    view: tuple[PublicSourcesPanel, SyntheticController],
) -> None:
    panel, controller = view
    completions: list[bool] = []
    panel.shutdown_finished.connect(lambda: completions.append(panel.shutdown_complete))
    controller.publish(reply())
    panel.shutdown()
    assert controller.shutdown_requests == 1
    assert not panel.shutdown_complete
    assert not completions
    assert panel.result.toPlainText() == ""
    controller.publish(reply())
    assert panel.result.toPlainText() == ""
    controller.finish_shutdown()
    assert completions == [True]
    assert panel.shutdown_complete


def test_unknown_status_is_closed_wording_and_does_not_render_raw_diagnostics(
    view: tuple[PublicSourcesPanel, SyntheticController],
) -> None:
    panel, controller = view
    controller.status = "SYNTHETIC PRIVATE RAW EXCEPTION"
    controller.changed.emit()
    assert "PRIVATE" not in panel.status.text()
    assert controller.source_calls == []
