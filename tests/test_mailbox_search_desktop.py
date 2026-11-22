"""Synthetic Gmail account, legacy panel searches and the local conversation read."""

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from threading import Event, get_ident
from typing import cast
from uuid import uuid4

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.intents import parse_assistant_intent
from satori.application.assistant.mail_contracts import (
    MailCatalog,
    MailHeaderClaim,
    MailSourceRef,
)
from satori.application.assistant.mail_search_contracts import MailSearchRequest
from satori.application.assistant.source_answer_contracts import (
    SourceObservation,
    SourceSearchPlan,
    SourceTaskRequest,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_task_reads import task_snapshot
from satori.desktop import ui
from satori.desktop.mail_ui import MailPanel
from satori.desktop.settings import DesktopPreferences
from satori.desktop.ui import DesktopWindow
from satori.infrastructure.gmail import GmailInboxPage, GmailMessageHeader
from satori.infrastructure.gmail.contracts import GmailSearchPage
from tests.test_mail_controller import Clock, FakeConnector, Harness, wait_until
from tests.test_mail_controller import app as app


class SearchConnector(FakeConnector):
    def __init__(self, principal: LocalPrincipal, clock: Clock) -> None:
        super().__init__(principal, clock)
        self.search_gate = Event()
        self.search_gate.set()
        self.search_started = Event()
        self.searches: list[MailSearchRequest] = []
        self.mutate_search: Callable[[GmailSearchPage], GmailSearchPage] = lambda result: result

    def search_headers(self, request: MailSearchRequest, *, cancel: Event) -> GmailSearchPage:
        # Deliberately ignore cancellation: the real controller must reject stale delivery.
        del cancel
        self.calls.append(("search", get_ident()))
        self.searches.append(request)
        page = GmailInboxPage(
            catalog=MailCatalog(
                principal=self.principal,
                account=self.identity.account,
                catalog_id=uuid4(),
                observed=self.clock(),
                message_ids=("m1",),
            ),
            messages=(
                GmailMessageHeader(
                    source=MailSourceRef(account=self.identity.account, message_id="m1"),
                    headers=(
                        MailHeaderClaim(name="subject", value="<b>PUBLIC Архив Счёт</b>", index=0),
                        MailHeaderClaim(name="from", value="PUBLIC sender@example.test", index=1),
                        MailHeaderClaim(name="date", value="PUBLIC date", index=2),
                    ),
                ),
            ),
            more_available=True,
        )
        result = GmailSearchPage(request=request, page=page)
        self.search_started.set()
        assert self.search_gate.wait(5)
        self._raise_failure()
        return self.mutate_search(result)


class SearchHarness(Harness):
    def factory(self, principal: LocalPrincipal) -> SearchConnector:
        connector = SearchConnector(principal, self.clock)
        self.instances.append(connector)
        return connector

    @property
    def connector(self) -> SearchConnector:
        return cast(SearchConnector, self.instances[-1])


@pytest.fixture
def search_harness(app: QApplication, tmp_path: Path) -> Iterator[SearchHarness]:
    harness = SearchHarness(app, tmp_path)
    harness.controller._timer.stop()
    yield harness
    for instance in harness.instances:
        instance.login_gate.set()
        instance.read_gate.set()
        instance.list_gate.set()
        instance.disconnect_gate.set()
        cast(SearchConnector, instance).search_gate.set()
    harness.controller.shutdown()
    wait_until(app, lambda: harness.controller.shutdown_complete)
    harness.controller.deleteLater()
    app.processEvents()


def test_connected_search_fetches_without_inbox_selection_or_body_read(
    search_harness: SearchHarness, app: QApplication
) -> None:
    h = search_harness
    h.connect()
    request_id = uuid4()
    assert h.controller.search_mailbox("Счёт <b>ЁЖ</b>", request_id)
    wait_until(app, lambda: not h.controller.busy)
    assert [name for name, _ in h.connector.calls] == ["begin", "login", "search"]
    assert h.connector.searches[0].query == "Счёт <b>ЁЖ</b>"
    assert h.connector.searches[0].request_id == request_id
    assert h.controller.search_result_id == request_id
    assert len(h.controller.headers) == 1
    assert h.controller._owner.grant is None
    assert h.controller.snapshot is None
    assert h.connector.reads == []
    assert h.controller.last_receipt is not None
    assert h.controller.last_receipt.operation == "search"
    assert h.controller.last_receipt.code == "searched"
    # Metadata-only receipts/audit do not retain the query or mail text.
    assert "Счёт" not in repr(h.controller.last_receipt)
    assert "Счёт" not in repr(tuple(h.controller.audit))


@pytest.mark.parametrize("field", ["request_id", "query", "principal", "account"])
def test_mismatched_search_envelope_never_admits_headers(
    search_harness: SearchHarness, app: QApplication, field: str
) -> None:
    h = search_harness
    h.connect()

    def mutate(result: GmailSearchPage) -> GmailSearchPage:
        original = result.request
        changes: dict[str, object] = {
            "request_id": uuid4(),
            "query": "OTHER QUERY",
            "principal": original.principal.model_copy(update={"session_id": uuid4()}),
            "account": original.account.model_copy(update={"subject": "OTHER-ACCOUNT"}),
        }
        request = original.model_copy(update={field: changes[field]})
        return result.model_copy(update={"request": request})

    h.connector.mutate_search = mutate
    assert h.controller.search_mailbox("PUBLIC", uuid4())
    wait_until(app, lambda: not h.controller.busy)
    assert h.controller.headers == ()
    assert h.controller.search_result_id is None
    assert h.controller._owner.grant is None
    assert h.controller.last_receipt is not None
    assert h.controller.last_receipt.code == "response_invalid"


@pytest.mark.parametrize("clock_change", ["expired", "wall_rollback", "mono_rollback"])
def test_expired_or_rolled_back_search_is_rejected_at_delivery(
    search_harness: SearchHarness, app: QApplication, clock_change: str
) -> None:
    h = search_harness
    h.connect()
    h.connector.search_gate.clear()
    assert h.controller.search_mailbox("PUBLIC", uuid4())
    wait_until(app, h.connector.search_started.is_set)
    if clock_change == "expired":
        h.clock.advance(31)
    elif clock_change == "wall_rollback":
        h.clock.wall -= timedelta(seconds=1)
    else:
        h.clock.monotonic -= 1
    h.connector.search_gate.set()
    wait_until(app, lambda: not h.controller.busy)
    assert h.controller.headers == ()
    assert h.controller.search_result_id is None
    assert h.controller.last_receipt is not None
    assert h.controller.last_receipt.code == "request_invalid"


def test_cancel_exact_search_rejects_late_delivery_but_preserves_account(
    search_harness: SearchHarness, app: QApplication
) -> None:
    h = search_harness
    h.connect()
    h.connector.search_gate.clear()
    request_id = uuid4()
    assert h.controller.search_mailbox("PUBLIC", request_id)
    wait_until(app, h.connector.search_started.is_set)
    h.controller.cancel_search(request_id)
    assert h.controller._job is not None
    assert h.controller._job.cancelled.is_set()
    h.connector.search_gate.set()
    wait_until(app, lambda: not h.controller.busy)
    assert h.controller.account == h.connector.identity
    assert h.controller.headers == ()
    assert h.controller.search_result_id is None
    assert not h.controller.search_mailbox("PUBLIC", request_id)
    assert len(h.connector.searches) == 1


def test_old_search_cleanup_does_not_cancel_newer_search(
    search_harness: SearchHarness, app: QApplication
) -> None:
    h = search_harness
    h.connect()
    old, new = uuid4(), uuid4()
    assert h.controller.search_mailbox("FIRST", old)
    wait_until(app, lambda: not h.controller.busy)
    h.connector.search_gate.clear()
    h.connector.search_started.clear()
    assert h.controller.search_mailbox("SECOND", new)
    wait_until(app, h.connector.search_started.is_set)
    generation = h.controller._generation
    h.controller.cancel_search(old)
    assert h.controller._generation == generation
    assert h.controller._job is not None
    assert not h.controller._job.cancelled.is_set()
    h.connector.search_gate.set()
    wait_until(app, lambda: not h.controller.busy)
    assert h.controller.search_result_id == new
    assert [request.query for request in h.connector.searches] == ["FIRST", "SECOND"]


def test_cleanup_does_not_cancel_subsequent_explicit_read(
    search_harness: SearchHarness, app: QApplication
) -> None:
    h = search_harness
    h.connect()
    request_id = uuid4()
    assert h.controller.search_mailbox("PUBLIC", request_id)
    wait_until(app, lambda: not h.controller.busy)
    h.connector.read_gate.clear()
    assert h.controller.read_selected(("m1",)) is not None
    generation = h.controller._generation
    h.controller.cancel_search(request_id)
    assert h.controller._generation == generation
    assert h.controller._job is not None
    assert not h.controller._job.cancelled.is_set()
    h.connector.read_gate.set()
    wait_until(app, lambda: not h.controller.busy)
    assert h.controller.snapshot is not None
    assert len(h.connector.reads) == 1


@pytest.mark.parametrize("change", ["disconnect", "profile", "poll_expiry"])
def test_connection_profile_and_timeout_fence_late_search(
    search_harness: SearchHarness, app: QApplication, tmp_path: Path, change: str
) -> None:
    h = search_harness
    h.connect()
    h.connector.search_gate.clear()
    assert h.controller.search_mailbox("PUBLIC", uuid4())
    wait_until(app, h.connector.search_started.is_set)
    if change == "disconnect":
        h.controller.disconnect_account()
    elif change == "profile":
        h.controller.set_profile(tmp_path / "NEW-PROFILE")
    else:
        h.clock.advance(31)
        h.controller.poll()
    h.connector.search_gate.set()
    wait_until(app, lambda: not h.controller.busy)
    assert h.controller.headers == ()
    assert h.controller.search_result_id is None


@pytest.fixture
def search_window(
    search_harness: SearchHarness, app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[DesktopWindow, SearchHarness, list[tuple[str, object]]]]:
    h = search_harness
    original_panel = MailPanel

    def panel_factory(path: Path, *args: object, **kwargs: object) -> MailPanel:
        return original_panel(path, controller=h.controller)

    monkeypatch.setattr(ui, "MailPanel", panel_factory)
    window = DesktopWindow(
        DesktopPreferences(
            data_directory=h.path,
            repository_directory=h.path,
            voice_model_directory=h.path / "PUBLIC-EMPTY-WEIGHTS",
            speech_model_directory=h.path / "PUBLIC-EMPTY-SPEECH-WEIGHTS",
        )
    )
    window.poll.stop()
    requests: list[tuple[str, object]] = []
    monkeypatch.setattr(
        window, "request", lambda command, data=None: requests.append((command, data))
    )
    window.session_id = "PUBLIC-CONVERSATION-A"
    window.activated = True
    window.show()
    window._open_conversation()
    app.processEvents()
    yield window, h, requests
    for instance in h.instances:
        cast(SearchConnector, instance).search_gate.set()
    window.quitting = True
    window.tray.hide()
    window.close()
    window.deleteLater()
    app.processEvents()


def _submit(window: DesktopWindow, query: str = "PUBLIC") -> None:
    # Keep the existing panel/generation/catalog/viewport regression surface. This
    # is an explicit call to the legacy API, not the current ordinary Send route.
    window.editor.setPlainText("Найди в почте: " + query)
    intent = parse_assistant_intent(
        window.editor.toPlainText(), now=datetime.now(UTC), timezone="Europe/Moscow"
    )
    assert intent is not None
    assert intent.kind == "mailbox_search"
    window.editor.clear()
    assert window._mail_lookup_handoff(intent)


def test_window_search_is_one_automatic_request_before_calendar_without_core_io(
    search_window: tuple[DesktopWindow, SearchHarness, list[tuple[str, object]]],
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, h, requests = search_window
    h.connect()

    def forbidden(*args: object, **kwargs: object) -> bool:
        raise AssertionError("mailbox_search_entered_calendar")

    monkeypatch.setattr(window.calendar_chat, "answer", forbidden)
    monkeypatch.setattr(window.calendar_chat, "submit", forbidden)
    previous_history = window.transcript.toPlainText()
    _submit(window, "созвон <b>ЁЖ</b>")
    wait_until(app, lambda: not h.controller.busy)
    for _ in range(3):
        h.controller.changed.emit()
        window.mail_panel._apply_chat_lookup()
    assert len(h.connector.searches) == 1
    assert h.connector.searches[0].query == "созвон <b>ЁЖ</b>"
    assert window.tabs.currentWidget() is window.mail_panel
    assert window.mail_panel.selection_count == 0
    assert h.controller._owner.grant is None
    assert h.connector.reads == []
    assert requests == []
    assert window.transcript.toPlainText() == previous_history
    assert window.editor.toPlainText() == ""
    summary = window.mail_panel.chat_lookup_summary
    assert summary.textFormat() == Qt.TextFormat.PlainText
    assert "созвон <b>ЁЖ</b>" in summary.text()
    assert "спам и корзину" in summary.text()
    assert "уточни запрос" in summary.text()
    assert "<b>PUBLIC Архив Счёт</b>" in window.mail_panel.mailbox_results.toPlainText()
    assert "sender@example.test" in window.mail_panel.mailbox_results.toPlainText()
    assert window.mail_panel.mailbox_results.isVisible()
    assert window.mail_panel.scroll_area.verticalScrollBar().value() == 0
    viewport = window.mail_panel.scroll_area.viewport()
    assert viewport.rect().contains(summary.mapTo(viewport, summary.rect().topLeft()))
    assert viewport.rect().contains(summary.mapTo(viewport, summary.rect().bottomRight()))


def test_disconnected_query_waits_for_connect_without_selection_then_runs_once(
    search_window: tuple[DesktopWindow, SearchHarness, list[tuple[str, object]]],
    app: QApplication,
) -> None:
    window, h, requests = search_window
    _submit(window)
    assert h.instances == []
    assert "Подключи Gmail" in window.mail_panel.chat_lookup_summary.text()
    assert "Письма выбирать не нужно" in window.mail_panel.chat_lookup_summary.text()
    # Synthetic login stays in this foreground lease. Actual deactivation revokes it separately.
    h.connect()
    wait_until(app, lambda: not h.controller.busy)
    assert len(h.connector.searches) == 1
    assert h.connector.reads == []
    assert window.mail_panel.mailbox_results.isVisible()
    assert requests == []


@pytest.mark.parametrize("change", ["hide", "deactivate", "conversation", "edit", "expiry"])
def test_window_scope_retirement_cancels_exact_search_and_drops_late_result(
    search_window: tuple[DesktopWindow, SearchHarness, list[tuple[str, object]]],
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    window, h, requests = search_window
    h.connect()
    h.connector.search_gate.clear()
    _submit(window)
    wait_until(app, h.connector.search_started.is_set)
    panel = window.mail_panel
    handoff = panel._chat_lookup
    assert handoff is not None
    if change == "hide":
        window._open_conversation()
    elif change == "deactivate":
        window.event(QEvent(QEvent.Type.WindowDeactivate))
    elif change == "conversation":
        window.session_id = "PUBLIC-CONVERSATION-B"
        panel._apply_chat_lookup()
    elif change == "edit":
        window.editor.setPlainText("replacement draft")
    else:
        monkeypatch.setattr(ui, "monotonic", lambda: handoff.deadline)
        panel._apply_chat_lookup()
    assert panel._chat_lookup is None
    assert panel.mailbox_results.toPlainText() == ""
    assert panel.chat_lookup_summary.text() == ""
    assert h.controller._job is not None
    assert h.controller._job.cancelled.is_set()
    h.connector.search_gate.set()
    wait_until(app, lambda: not h.controller.busy)
    assert h.controller.headers == ()
    assert h.controller.account is not None
    assert requests == []


def test_stale_search_finish_cannot_restart_or_replace_new_chat_query(
    search_window: tuple[DesktopWindow, SearchHarness, list[tuple[str, object]]],
    app: QApplication,
) -> None:
    window, h, requests = search_window
    h.connect()
    h.connector.search_gate.clear()
    _submit(window, "FIRST")
    wait_until(app, h.connector.search_started.is_set)
    old = window.mail_panel._chat_lookup
    assert old is not None
    window._open_conversation()
    _submit(window, "SECOND")
    new = window.mail_panel._chat_lookup
    assert new is not None
    assert new.request_id != old.request_id
    h.connector.search_gate.set()
    wait_until(app, lambda: len(h.connector.searches) == 2 and not h.controller.busy)
    assert window.mail_panel._chat_lookup is new
    assert h.controller.search_result_id == new.request_id
    assert [request.query for request in h.connector.searches] == ["FIRST", "SECOND"]
    assert "SECOND" in window.mail_panel.chat_lookup_summary.text()
    assert requests == []


def test_mailbox_handoff_requires_both_wall_and_monotonic_freshness(
    search_window: tuple[DesktopWindow, SearchHarness, list[tuple[str, object]]],
) -> None:
    window, _, _ = search_window
    _submit(window)
    handoff = window.mail_panel._chat_lookup
    assert handoff is not None
    assert handoff.issued_wall is not None
    assert handoff.current(
        profile=handoff.profile,
        session_id=handoff.session_id,
        epoch=handoff.epoch,
        now=handoff.issued_at,
        wall_now=handoff.issued_wall,
    )
    assert not handoff.current(
        profile=handoff.profile,
        session_id=handoff.session_id,
        epoch=handoff.epoch,
        now=handoff.issued_at,
        wall_now=handoff.issued_wall - timedelta(microseconds=1),
    )
    with pytest.raises(ValueError, match="mail_lookup_handoff_invalid"):
        replace(handoff, issued_wall=datetime.now(UTC), expires_wall=None)


def test_browser_deactivation_retires_query_without_auto_resume_after_login(
    search_window: tuple[DesktopWindow, SearchHarness, list[tuple[str, object]]],
    app: QApplication,
) -> None:
    window, h, requests = search_window
    _submit(window)
    assert "затем повтори запрос в чате" in window.mail_panel.chat_lookup_summary.text()

    def browser(url: str) -> bool:
        del url
        window.event(QEvent(QEvent.Type.WindowDeactivate))
        return True

    h.controller._browser = browser
    h.connect()
    assert h.controller.account is not None
    assert h.connector.searches == []
    assert window.mail_panel._chat_lookup is None
    assert window.mail_panel.chat_lookup_summary.text() == ""
    _submit(window)
    wait_until(app, lambda: not h.controller.busy)
    assert len(h.connector.searches) == 1
    assert requests == []


def test_hidden_connected_mail_owner_answers_normal_chat_without_core_or_body_reads(
    search_window: tuple[DesktopWindow, SearchHarness, list[tuple[str, object]]],
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, h, requests = search_window
    window._open_settings_section("mail")
    assert window.mail_panel.isVisible()
    h.connect()
    binding = h.controller.task_account_binding
    assert binding is not None
    window._open_conversation()
    app.processEvents()
    assert not window.mail_panel.isVisible()
    assert h.controller.task_account_binding == binding

    window.session_id = str(uuid4())
    window._calendar_identity = str(uuid4())
    window._calendar_provider = "ollama:PUBLIC"
    assert not window.supports_source_answers
    monkeypatch.setattr(window.source_answer, "_foreground", lambda: True)
    monkeypatch.setattr(window.source_answer, "_clock", lambda: h.clock.wall)
    monkeypatch.setattr(window.source_answer, "_monotonic", lambda: h.clock.monotonic)

    def read_headers(
        request: SourceTaskRequest, plan: SourceSearchPlan, *, cancel: Event
    ) -> SourceTaskSnapshot:
        assert not cancel.is_set()
        assert request.sources == ("mail",)
        assert plan.mail_headers_only
        assert plan.mail_query == "in:inbox"
        assert plan.window_start == datetime(2026, 10, 19, 21, tzinfo=UTC)
        item = SourceObservation(
            source="mail",
            source_ref="mail:PUBLIC-ONE",
            observed_at=h.clock.wall,
            event_at=h.clock.wall - timedelta(minutes=15),
            title="PUBLIC invoice <b>test</b>",
            sender="PUBLIC sender",
            completeness="metadata_only",
        )
        return task_snapshot(request, plan, "mail", h.clock.wall, (item,), status="searched")

    monkeypatch.setattr(h.connector, "task_search", read_headers)
    window.editor.setPlainText("Есть у меня новые письма?")  # noqa: RUF001 - Russian user text
    window.send()
    wait_until(app, lambda: not window.source_answer.busy and not h.controller.busy)
    card = window.source_answer.card
    assert card is not None
    assert card.status == "answered"
    assert "PUBLIC sender — PUBLIC invoice <b>test</b>" in card.text
    assert "Gmail: проверено" in "\n".join(card.details)
    assert window.tabs.currentWidget() is window.conversation_page
    assert h.controller.task_account_binding == binding
    assert h.connector.reads == []
    assert requests == []


def test_window_wall_rollback_inside_lease_retires_completed_search(
    search_window: tuple[DesktopWindow, SearchHarness, list[tuple[str, object]]],
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, h, _ = search_window
    h.connect()
    _submit(window)
    wait_until(app, lambda: not h.controller.busy)
    handoff = window.mail_panel._chat_lookup
    assert handoff is not None
    assert handoff.issued_wall is not None
    wall = [handoff.issued_wall + timedelta(seconds=2)]

    class WallClock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> "WallClock":
            return cls.fromtimestamp(wall[0].timestamp(), tz)

    monkeypatch.setattr(ui, "datetime", WallClock)
    assert window._mail_lookup_current(handoff)
    wall[0] -= timedelta(seconds=1)
    window.mail_panel._apply_chat_lookup()
    assert window.mail_panel._chat_lookup is None
    assert window.mail_panel.mailbox_results.toPlainText() == ""
    assert h.controller.headers == ()
    assert h.controller.account is not None
