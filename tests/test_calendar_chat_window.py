"""Ordinary window routing and lease lifecycle; no Core process or live account."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarOccurrence,
    CalendarReadRequest,
    CalendarReadResult,
    CalendarSourceRef,
)
from satori.application.assistant.dialogue_contracts import PendingCalendarRead
from satori.desktop.settings import DesktopPreferences
from satori.desktop.ui import DesktopApplication, DesktopWindow

TEXT = "Поставь «PUBLIC встреча» завтра в 15:00 на час в календарь Работа"
TARGET = CalendarSourceRef(account_id="public-account", calendar_id="public-work")


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or DesktopApplication([])  # type: ignore[return-value]


@pytest.fixture
def window(application: QApplication, tmp_path: Path) -> Iterator[DesktopWindow]:
    del application
    root = tmp_path.resolve()
    root.chmod(0o700)
    result = DesktopWindow(
        DesktopPreferences(
            data_directory=root,
            repository_directory=root,
            voice_model_directory=root / "public-voice",
            speech_model_directory=root / "public-speech",
        ),
        calendar_foreground=lambda: True,
    )
    result.poll.stop()
    result.activated, result.busy, result.viewing_closed = True, False, False
    result.session_id = str(uuid4())
    result._calendar_identity = str(uuid4())
    result._calendar_provider = "ollama:qwen3:4b-instruct"
    yield result
    result.quitting = True
    result.tray.hide()
    result.close()
    result.deleteLater()
    QApplication.processEvents()


def test_ordinary_send_opens_calendar_card_without_canonical_request(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, object]] = []
    monkeypatch.setattr(window, "request", lambda command, data=None: sent.append((command, data)))
    before = window.transcript.toPlainText()
    window.editor.setPlainText(TEXT)
    window.send()
    for _ in range(5):
        QApplication.processEvents()
    assert sent == []
    assert window.editor.toPlainText() == ""
    assert window.transcript.toPlainText() == before
    assert window.calendar_chat.card is not None
    assert window.calendar_chat.card.draft == TEXT
    assert window.calendar_chat.conversational
    assert window.calendar_chat.card.status == "connection_required"
    assert window.calendar_panel.controller.grant is None
    assert not (window.preferences.data_directory / "calendar-host-leases").exists()


def test_ordinary_conversation_retains_existing_canonical_send(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, object]] = []
    monkeypatch.setattr(window, "request", lambda command, data=None: sent.append((command, data)))
    window.editor.setPlainText("Как ты сегодня?")
    window.send()
    assert len(sent) == 1
    assert sent[0][0] == "send"
    assert window.calendar_chat.card is None


def test_host_frame_requires_exact_calendar_parent_and_does_not_pop_pending(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[dict[str, object]] = []
    monkeypatch.setattr(window.calendar_chat, "handle_host_read", received.append)
    parent = str(uuid4())
    window.pending[parent] = "calendar"
    window.calendar_requests[parent] = (str(uuid4()), "preview")
    body: dict[str, object] = {"call_id": str(uuid4()), "request": {"PUBLIC": "fixture"}}
    window._receive({"id": str(uuid4()), "event": "calendar_host_request", "data": body})
    assert received == []
    window._receive({"id": parent, "event": "calendar_host_request", "data": body})
    assert received == [body]
    assert window.pending[parent] == "calendar"
    assert parent in window.calendar_requests
    window._receive({"id": parent, "event": "result", "data": {"status": "unavailable"}})
    assert parent not in window.pending
    assert parent not in window.calendar_requests
    assert received == [body]


@pytest.mark.parametrize("change", ["hide", "stop", "profile", "deactivate"])
def test_window_lifecycle_revokes_the_file_before_reusing_source_context(
    window: DesktopWindow, tmp_path: Path, change: str
) -> None:
    window.show()
    QApplication.processEvents()
    calendar = window.calendar_panel.controller
    calendar._coordinator.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=datetime.now(UTC),
            calendars=(
                CalendarDescriptor(
                    source=TARGET,
                    title="Работа",
                    account_title="PUBLIC iCloud",
                    writable=True,
                    allows_modification=True,
                    supports_events=True,
                    source_kind="caldav",
                ),
            ),
        )
    )
    calendar.permission_status = "full_access"
    calendar._catalog_phase = "active"
    assert calendar.use_calendars((TARGET,))
    window.editor.setPlainText(TEXT)
    window.send()
    files = list((window.preferences.data_directory / "calendar-host-leases").glob("*.json"))
    assert len(files) == 1
    assert window.calendar_chat.card is not None
    if change == "hide":
        window.hide()
    elif change == "stop":
        window.stop()
    elif change == "profile":
        other = tmp_path.resolve() / "public-other-profile"
        other.mkdir(mode=0o700)
        window.calendar_panel.set_profile(other)
    else:
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))
    if change in {"hide", "deactivate"}:
        assert window.calendar_chat.card is not None
        assert window.calendar_chat.card.status == "paused"
    else:
        assert window.calendar_chat.card is None
    assert not files[0].exists()


@pytest.mark.parametrize(
    "text",
    [
        "Мы завтра планируем созвон в 8 вечера, занеси его в календарь события",  # noqa: RUF001
        "Занеси созвон завтра в 8 вечера на час в календарь События",
        "Запиши созвон завтра примерно в 8 вечера в календарь События",
    ],
)
def test_reported_natural_request_never_reaches_canonical_send_or_model(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    sent: list[tuple[str, object]] = []
    monkeypatch.setattr(window, "request", lambda command, data=None: sent.append((command, data)))
    before = window.transcript.toPlainText()
    window.editor.setPlainText(text)
    window.send()
    assert sent == []
    assert window.transcript.toPlainText() == before
    assert window.editor.toPlainText() == ""
    assert window.calendar_chat.card is not None
    assert window.calendar_chat.card.draft == text
    assert window.calendar_panel.controller.grant is None
    assert not (window.preferences.data_directory / "calendar-host-leases").exists()


def test_unrelated_new_message_after_calendar_draft_keeps_normal_send(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, object]] = []
    monkeypatch.setattr(window, "request", lambda command, data=None: sent.append((command, data)))
    window.editor.setPlainText(
        "Мы завтра планируем созвон в 8 вечера, занеси его в календарь события"  # noqa: RUF001
    )
    window.send()
    assert sent == []
    window.editor.setPlainText("Объясни принцип работы транзистора")
    window.send()
    assert len(sent) == 1
    assert sent[0][0] == "send"
    payload = sent[0][1]
    assert isinstance(payload, dict)
    assert payload["text"] == "Объясни принцип работы транзистора"


@pytest.mark.parametrize(
    "text",
    [
        "Перенеси событие «PUBLIC встреча» на пятницу",
        "Сатори, можешь изменить время созвона завтра на 20:00?",
        "Удали событие «PUBLIC встреча» из календаря",
    ],
)
def test_existing_event_changes_are_honestly_unsupported_in_ordinary_chat(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(window, "request", lambda command, data=None: sent.append(command))
    window.editor.setPlainText(text)
    window.send()
    assert sent == []
    assert window.calendar_chat.card is not None
    assert window.calendar_chat.card.status == "unsupported"
    assert "Ничего не меняла" in window.calendar_chat.card.message
    assert window.calendar_chat.card.draft == ""
    assert window.transcript.toPlainText() == ""
    assert window.calendar_panel.controller.grant is None
    assert not (window.preferences.data_directory / "calendar-host-leases").exists()


@pytest.mark.parametrize(
    "text",
    [
        "Перенеси файл в другую папку",
        "Объясни, что значит «перенеси событие на пятницу»",
        "`Перенеси событие` напиши такую фразу в инструкции",
    ],
)
def test_non_calendar_mutation_and_quoted_context_keep_normal_chat(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(window, "request", lambda command, data=None: sent.append(command))
    window.editor.setPlainText(text)
    window.send()
    assert sent == ["send"]
    assert window.calendar_chat.card is None


def test_ordinary_chat_renders_exact_selected_calendar_read_without_core(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    window.show()
    window.activateWindow()
    QApplication.processEvents()
    calendar = window.calendar_panel.controller
    calendar._coordinator.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=datetime.now(UTC),
            calendars=(
                CalendarDescriptor(
                    source=TARGET,
                    title="Работа",
                    account_title="PUBLIC synthetic",
                    writable=True,
                ),
            ),
        )
    )
    calendar.permission_status = "full_access"
    calendar._catalog_phase = "active"
    assert calendar.use_calendars((TARGET,))
    intercepted: list[PendingCalendarRead] = []
    core: list[str] = []

    def fake_start(
        operation: str,
        request: CalendarReadRequest,
        *,
        dialogue: PendingCalendarRead,
    ) -> None:
        assert operation == "read_events"
        assert request == dialogue.request
        intercepted.append(dialogue)

    monkeypatch.setattr(calendar, "_start", fake_start)
    monkeypatch.setattr(window, "request", lambda command, data=None: core.append(command))
    window.editor.setPlainText("Что в календаре сегодня?")
    window.send()
    assert len(intercepted) == 1
    pending = intercepted[0]
    request = pending.request
    event_start = request.starts_at + timedelta(hours=9)
    assert calendar._dialogue is not None
    answer = calendar._dialogue.accept(
        pending,
        CalendarReadResult(
            request_id=request.request_id,
            observed_at=datetime.now(UTC),
            status="ok",
            occurrences=(
                CalendarOccurrence(
                    source=TARGET,
                    event_id="public-ordinary-event",
                    occurrence_id="public-ordinary-event-1",
                    title="PUBLIC synthetic event",
                    starts_at=event_start,
                    ends_at=event_start + timedelta(hours=1),
                    timezone=request.timezone,
                ),
            ),
        ),
    )
    assert answer.receipt.status == "ok"
    calendar.dialogue_changed.emit()
    assert window.assistant.card is not None
    assert window.assistant.card.status == "ok"
    assert "PUBLIC synthetic event" in window.assistant.card.body
    assert "09:00" in window.assistant.card.body
    assert core == []
    assert window.transcript.toPlainText() == ""
    assert window.editor.toPlainText() == ""
    calendar.disconnect_calendar()
    assert calendar.dialogue_answer is None
    assert window.assistant.card is not None
    assert "PUBLIC synthetic event" not in window.assistant.card.body
