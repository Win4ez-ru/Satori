"""Production Window/MailPanel handoff with synthetic owners; no accounts or model calls."""

# ruff: noqa: RUF001 - exact Russian input with escaped command-prefix whitespace

import base64
import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.intents import parse_assistant_intent
from satori.application.assistant.mail_contracts import (
    MailReadRequest,
    MailReadResult,
    MailSnapshot,
)
from satori.desktop import ui
from satori.desktop.mail_controller import MailController
from satori.desktop.mail_lookup_handoff import MailLookupHandoff
from satori.desktop.mail_ui import MailPanel
from satori.desktop.settings import DesktopPreferences
from satori.desktop.ui import DesktopWindow
from tests.test_desktop_ui import application as application
from tests.test_mail_controller import Harness, wait_until
from tests.test_mail_snapshot_search_ui import BodyFixture, _publish
from tests.test_mail_ui import NOW, SyntheticController, _clock


@pytest.mark.parametrize(
    "query",
    ["API  Meeting", "Straße API", "ЁЖ ёж", "[x].*", "<b>literal</b>", "trailing  ", "x" * 128],
)
def test_mail_query_preserves_exact_literal_text_and_identity(query: str) -> None:
    request_id = uuid4()
    result = parse_assistant_intent(
        "Найди в разрешённой почте: " + query,
        now=NOW,
        timezone="Europe/Moscow",
        request_id=request_id,
    )
    assert result is not None
    assert result.kind == "selected_lookup"
    assert result.source == "mail"
    assert result.query == query
    assert result.request_id == request_id


@pytest.mark.parametrize("query", ["", "  ", "x" * 129, "a\nb", "a\tb", "a\u200bb"])
def test_invalid_query_is_clarification_and_never_silently_truncated(query: str) -> None:
    result = parse_assistant_intent(
        "найди в выбранной почте " + query, now=NOW, timezone="Europe/Moscow"
    )
    assert result is not None
    assert result.kind == "clarification"
    assert result.query is None


class HandoffController(SyntheticController):
    """Synthetic source owner returning the exact request metadata from explicit Read."""

    pending_read: MailReadRequest | None = None

    def read_selected(self, ids: tuple[str, ...]) -> UUID:  # type: ignore[override]
        # The old synthetic base predates the production request-correlation return value.
        self.reads.append(ids)
        proof = self.owner.select(ids, confirmed=True)
        self.owner.issue(proof, confirmed=True)
        self.pending_read = self.owner.prepare_read()
        return self.pending_read.request_id


def _deliver_selected_read(source: HandoffController, text: str) -> MailSnapshot:
    request = source.pending_read
    assert request is not None
    selection = request.grant.selection
    encoded = text.encode()
    raw = tuple(
        json.dumps(
            {
                "id": ref.message_id,
                "payload": {
                    "mimeType": "text/plain",
                    "body": {
                        "size": len(encoded),
                        "data": base64.urlsafe_b64encode(encoded).decode(),
                    },
                },
            }
        ).encode()
        for ref in selection.resources
    )
    snapshot = source.owner.accept_result(
        request,
        MailReadResult(
            request_id=request.request_id,
            principal=source.principal,
            account=selection.account,
            generation=selection.generation,
            selection_revision=selection.revision,
            observed=_clock(),
            messages=raw,
        ),
    )
    source.snapshot = snapshot
    source.status = "ready"
    source.pending_read = None
    source.changed.emit()
    return snapshot


@pytest.fixture
def lookup_window(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[DesktopWindow, SyntheticController, list[tuple[str, object]]]]:
    controller = HandoffController()
    panel_type = MailPanel

    def panel_factory(path: Path, *args: object, **kwargs: object) -> MailPanel:
        # Use the real widget; replace only its source transport/clock owner with synthetic data.
        return panel_type(path, controller=cast(MailController, controller))

    monkeypatch.setattr(ui, "MailPanel", panel_factory)
    result = DesktopWindow(
        DesktopPreferences(data_directory=tmp_path, repository_directory=tmp_path)
    )
    result.poll.stop()
    requests: list[tuple[str, object]] = []
    monkeypatch.setattr(
        result, "request", lambda command, data=None: requests.append((command, data))
    )
    result.session_id = "synthetic-open-A"
    result.activated = True
    result.show()
    result._open_conversation()
    application.processEvents()
    yield result, controller, requests
    result.quitting = True
    result.tray.hide()
    result.close()
    result.deleteLater()
    application.processEvents()


def _submit(window: DesktopWindow, query: str) -> None:
    window.editor.setPlainText("Найди в выбранной почте " + query)
    window.send()


def test_composer_stages_query_without_account_or_canonical_io(
    lookup_window: tuple[DesktopWindow, SyntheticController, list[tuple[str, object]]],
) -> None:
    window, source, requests = lookup_window
    _submit(window, "API  Meeting")
    panel = window.mail_panel
    assert window.tabs.currentWidget() is panel
    assert panel.search_query.text() == "API  Meeting"
    assert panel.search_results.toPlainText() == ""
    assert "Прочитать выбранные" in panel.search_status.text()
    assert requests == []
    assert source.connects == []
    assert source.reads == []
    assert source.lists == 0
    assert window.editor.toPlainText() == ""
    assert window.assistant.card is not None
    assert window.assistant.card.status == "source_local"
    handoff = panel._chat_lookup
    assert handoff is not None
    assert handoff.request_id == window.assistant.card.request_id
    assert handoff.profile == window.preferences.data_directory
    assert handoff.session_id == "synthetic-open-A"
    assert "API  Meeting" not in repr(handoff)


def test_hidden_old_snapshot_is_not_searched_until_explicit_new_read(
    lookup_window: tuple[DesktopWindow, SyntheticController, list[tuple[str, object]]],
) -> None:
    window, source, requests = lookup_window
    source.catalog_ready()
    old = _publish(source, (BodyFixture("old API  Meeting source"),))
    _submit(window, "API  Meeting")
    panel = window.mail_panel
    assert panel.search_results.toPlainText() == ""
    assert panel._chat_lookup_expected_read is None
    source.changed.emit()
    panel._render()
    assert panel.search_results.toPlainText() == ""
    assert source.reads == []
    # User makes a separate selected read in the existing source-local controls.
    panel.messages.item(0).setCheckState(Qt.CheckState.Checked)
    panel.read_button.click()
    assert source.reads == [("m0",)]
    assert isinstance(source, HandoffController)
    new = _deliver_selected_read(source, "fresh API  Meeting source")
    assert new.snapshot_id != old.snapshot_id
    assert "fresh API  Meeting" in panel.search_results.toPlainText()
    assert "old API" not in panel.search_results.toPlainText()
    assert "не по всей почте" in panel.search_status.text()
    assert requests == []


def test_visible_fresh_snapshot_search_uses_existing_exact_body_coverage(
    lookup_window: tuple[DesktopWindow, SyntheticController, list[tuple[str, object]]],
) -> None:
    window, source, requests = lookup_window
    window._open_settings_section("mail")
    source.catalog_ready()
    _publish(source, (BodyFixture("API  Meeting api  meeting"),))
    proposal = parse_assistant_intent(
        "Найди в выбранной почте API  Meeting", now=NOW, timezone="UTC"
    )
    assert proposal is not None
    window.assistant.dispatch(proposal)
    panel = window.mail_panel
    assert panel.search_query.text() == "API  Meeting"
    assert "Совпадений: 1" in panel.search_status.text()
    assert "1 выбранных писем" in panel.search_status.text()
    assert "только их текст" in panel.search_status.text()
    assert "API  Meeting" in panel.search_results.toPlainText()
    assert source.reads == []
    assert source.lists == 0
    assert requests == []


@pytest.mark.parametrize(
    "event", ["hide", "deactivate", "session", "profile", "edit", "deadline", "rollback", "cancel"]
)
def test_staged_query_cannot_cross_window_or_scope_lifecycle(
    lookup_window: tuple[DesktopWindow, SyntheticController, list[tuple[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
    event: str,
) -> None:
    window, source, requests = lookup_window
    _submit(window, "needle")
    panel = window.mail_panel
    draft = panel._chat_lookup
    assert draft is not None
    if event == "hide":
        window._open_conversation()
    elif event == "deactivate":
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))
    elif event == "session":
        window.session_ids = ["synthetic-open-B"]
        window.select_session(0)
        requests.clear()
    elif event == "profile":
        window.preferences = window.preferences.model_copy(
            update={"data_directory": Path("/other")}
        )
        panel._apply_chat_lookup()
    elif event == "edit":
        window.editor.setPlainText("ordinary unsent replacement")
    elif event == "deadline":
        monkeypatch.setattr(ui, "monotonic", lambda: draft.deadline)
        panel._apply_chat_lookup()
    elif event == "rollback":
        monkeypatch.setattr(ui, "monotonic", lambda: draft.issued_at - 1)
        panel._apply_chat_lookup()
    else:
        panel.cancel()
    assert panel._chat_lookup is None
    assert panel.search_query.text() == ""
    source.catalog_ready()
    _publish(source, (BodyFixture("needle stale callback"),))
    assert panel.search_results.toPlainText() == ""
    assert requests == []
    assert source.reads == []


def test_consumed_query_clears_on_expiry_or_different_snapshot_without_transient_result(
    lookup_window: tuple[DesktopWindow, SyntheticController, list[tuple[str, object]]],
) -> None:
    window, source, _ = lookup_window
    _submit(window, "needle")
    source.catalog_ready()
    panel = window.mail_panel
    panel.messages.item(0).setCheckState(Qt.CheckState.Checked)
    panel.read_button.click()
    assert isinstance(source, HandoffController)
    _deliver_selected_read(source, "needle first snapshot")
    assert panel.search_results.toPlainText()
    writes: list[str] = []
    panel.search_results.textChanged.connect(
        lambda: writes.append(panel.search_results.toPlainText())
    )
    _publish(source, (BodyFixture("needle new unexpected snapshot"),))
    assert panel._chat_lookup is None
    assert panel.search_query.text() == ""
    assert panel.search_results.toPlainText() == ""
    assert not any("unexpected" in text for text in writes)


def test_device_dispatch_cannot_reuse_desktop_mail_handoff(
    lookup_window: tuple[DesktopWindow, SyntheticController, list[tuple[str, object]]],
) -> None:
    window, source, _ = lookup_window
    proposal = AssistantIntent(
        request_id=uuid4(), kind="selected_lookup", timezone="UTC", source="mail", query="needle"
    )
    window.assistant.dispatch(proposal, capabilities=frozenset({"mail"}))
    assert window.tabs.currentWidget() is window.conversation_page
    assert window.mail_panel._chat_lookup is None
    assert source.reads == []


def test_real_controller_explicit_read_and_expiry_fence_staged_query(
    application: QApplication, tmp_path: Path
) -> None:
    harness = Harness(application, tmp_path)
    panel = MailPanel(tmp_path, controller=harness.controller)
    panel.show()
    scope = {"current": True}
    draft = MailLookupHandoff(uuid4(), "PUBLIC SYNTHETIC", tmp_path, "synthetic-A", 1, 10, 310)
    try:
        assert panel.stage_chat_lookup(
            draft, lambda _draft: scope["current"], require_new_read=True
        )
        assert harness.instances == []
        harness.listed()
        assert not panel.search_results.toPlainText()
        before = list(harness.connector.calls)
        # This is the existing explicit source-owner action, not an automatic handoff read.
        panel.messages.item(0).setCheckState(Qt.CheckState.Checked)
        panel.read_button.click()
        wait_until(application, lambda: not harness.controller.busy)
        assert "PUBLIC SYNTHETIC" in panel.search_results.toPlainText()
        assert [name for name, _ in harness.connector.calls[len(before) :]] == ["read"]
        harness.clock.advance(301)
        harness.controller.poll()
        assert panel._chat_lookup is None
        assert panel.search_query.text() == ""
        assert panel.search_results.toPlainText() == ""
    finally:
        panel.shutdown()
        wait_until(application, lambda: harness.controller.shutdown_complete)
        panel.close()
        panel.deleteLater()


def test_handoff_rejects_invalid_deadline_and_redacts_user_text(tmp_path: Path) -> None:
    draft = MailLookupHandoff(uuid4(), "PRIVATE QUERY", tmp_path, "chat-A", 1, 10, 310)
    assert "PRIVATE QUERY" not in repr(draft)
    assert not draft.current(profile=tmp_path, session_id="chat-B", epoch=1, now=20)
    with pytest.raises(ValueError, match="mail_lookup_handoff_invalid"):
        replace(draft, deadline=float("nan"))
    with pytest.raises(ValueError, match="mail_lookup_handoff_invalid"):
        replace(draft, deadline=311)


@pytest.mark.parametrize(
    "prefix",
    [
        "\nнайди в выбранной почте ",
        "найди\nв\tвыбранной\tпочте: ",
        "Найди\u00a0в\u00a0выбранной\u00a0почте ",
        "НАЙДИ В ВЫБРАННОЙ ПИСЬМАХ ",
    ],
)
def test_prefix_spacing_cannot_normalize_the_literal_query(prefix: str) -> None:
    result = parse_assistant_intent(prefix + "Straße  ЁЖ  ", now=NOW, timezone="UTC")
    assert result is not None
    assert result.kind == "selected_lookup"
    assert result.query == "Straße  ЁЖ  "


def test_pre_handoff_inflight_read_cannot_satisfy_new_query(
    application: QApplication, tmp_path: Path
) -> None:
    harness = Harness(application, tmp_path)
    panel = MailPanel(tmp_path, controller=harness.controller)
    panel.show()
    try:
        harness.listed()
        panel.messages.item(0).setCheckState(Qt.CheckState.Checked)
        harness.connector.read_gate.clear()
        panel.read_button.click()
        assert harness.controller.busy
        first_read = harness.connector.reads[-1] if harness.connector.reads else None
        draft = MailLookupHandoff(uuid4(), "PUBLIC SYNTHETIC", tmp_path, "A", 1, 10, 310)
        assert panel.stage_chat_lookup(draft, lambda _draft: True, require_new_read=True)
        assert panel._chat_lookup_expected_read is None
        harness.connector.read_gate.set()
        wait_until(application, lambda: not harness.controller.busy)
        assert harness.controller.snapshot is not None
        old_id = harness.controller.snapshot.request.request_id
        assert first_read is None or first_read.request_id == old_id
        assert panel.search_results.toPlainText() == ""
        # A later separate UI Read must own the exact request that supplies results.
        panel.read_button.click()
        expected = panel._chat_lookup_expected_read
        assert expected is not None
        assert expected != old_id
        wait_until(application, lambda: not harness.controller.busy)
        assert harness.controller.snapshot is not None
        assert harness.controller.snapshot.request.request_id == expected
        assert "PUBLIC SYNTHETIC" in panel.search_results.toPlainText()
    finally:
        harness.connector.read_gate.set()
        panel.shutdown()
        wait_until(application, lambda: harness.controller.shutdown_complete)
        panel.close()
        panel.deleteLater()
