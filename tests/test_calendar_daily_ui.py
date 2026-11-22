"""Daily calendar controls use synthetic records and require a fresh display consent."""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Self
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, Qt
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
from satori.desktop import calendar_dialogue_ui
from satori.desktop.calendar_controller import CalendarController, Operation
from satori.desktop.calendar_dialogue_ui import CalendarDialoguePanel
from satori.desktop.calendar_ui import CalendarPanel
from satori.desktop.ui import DesktopApplication

NOW = datetime(2026, 10, 29, 23, 30, tzinfo=UTC)
SOURCE = CalendarSourceRef(account_id="PUBLIC DAILY ACCOUNT", calendar_id="PUBLIC DAILY CALENDAR")


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls.fromtimestamp(NOW.timestamp(), tz)


class SyntheticDailyController(CalendarController):
    def __init__(self, path: Path, clock: Clock) -> None:
        self.submissions: list[PendingCalendarRead] = []
        super().__init__(path, helper_command=lambda: None, clock=clock)
        self.clock = clock
        self.bind_dialogue(uuid4())

    def ready(self) -> None:
        self.permission_status = "full_access"
        self._coordinator.accept_catalog(
            CalendarCatalog(
                request_id=uuid4(),
                observed_at=self.clock(),
                calendars=(
                    CalendarDescriptor(
                        source=SOURCE,
                        title="<b>PUBLIC DAILY CALENDAR</b>",
                        account_title="PUBLIC DAILY ACCOUNT",
                    ),
                ),
            )
        )
        assert self.use_calendars((SOURCE,))

    def _start(
        self,
        operation: Operation,
        request: CalendarReadRequest | None = None,
        *,
        dialogue: PendingCalendarRead | None = None,
        schedule_body: dict[str, object] | None = None,
    ) -> None:
        assert operation == "read_events"
        assert dialogue is not None
        assert request == dialogue.request
        assert schedule_body is None
        self.submissions.append(dialogue)

    def finish_empty(self) -> None:
        pending = self.submissions[-1]
        assert self._dialogue is not None
        answer = self._dialogue.accept(
            pending,
            CalendarReadResult(
                request_id=pending.request.request_id,
                observed_at=self.clock(),
                status="ok",
                occurrences=(),
            ),
        )
        self._dialogue_status = answer.receipt.status
        self.dialogue_changed.emit()
        self._notify()


class DelayedShutdownController(SyntheticDailyController):
    """Hold the close receipt to exercise the panel's asynchronous forwarding boundary."""

    def __init__(self, path: Path, clock: Clock) -> None:
        self.finished = False
        self.close_calls = 0
        super().__init__(path, clock)

    @property
    def shutdown_complete(self) -> bool:
        return self.finished

    def close(self) -> None:
        self.close_calls += 1

    def finish_close(self) -> None:
        self.finished = True
        self.shutdown_finished.emit()


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    assert instance is None or isinstance(instance, QApplication)
    return instance or DesktopApplication([])


@pytest.fixture
def view(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[CalendarDialoguePanel, SyntheticDailyController, Clock]]:
    del app
    monkeypatch.setattr(calendar_dialogue_ui, "datetime", FrozenDateTime)
    clock = Clock()
    controller = SyntheticDailyController(tmp_path, clock)
    panel = CalendarDialoguePanel(controller)
    panel.timezone.setText("UTC")
    panel.day.setDate(QDate(2026, 10, 29))
    yield panel, controller, clock
    controller.close()
    panel.close()
    panel.deleteLater()
    controller.deleteLater()
    QApplication.processEvents()


@pytest.mark.parametrize(
    ("zone", "offset", "expected"),
    [
        ("Europe/Moscow", 0, QDate(2026, 10, 30)),
        ("Europe/Moscow", 1, QDate(2026, 10, 31)),
        ("America/Los_Angeles", 0, QDate(2026, 10, 29)),
        ("America/Los_Angeles", 1, QDate(2026, 10, 30)),
    ],
)
def test_daily_shortcut_previews_agenda_in_displayed_zone_without_reading(
    view: tuple[CalendarDialoguePanel, SyntheticDailyController, Clock],
    zone: str,
    offset: int,
    expected: QDate,
) -> None:
    panel, controller, _ = view
    controller.ready()
    panel.intent.setCurrentIndex(1)
    panel.timezone.setText(zone)
    button = panel.today_button if offset == 0 else panel.tomorrow_button
    button.click()
    assert panel.day.date() == expected
    assert panel.intent.currentData() == "agenda"
    assert panel.preview.text()
    assert zone in panel.preview.text()
    assert "00:00 — 24:00" in panel.preview.text()
    assert not panel.consent.isChecked()
    assert panel.consent.isEnabled()
    assert not panel.run_button.isEnabled()
    assert controller.submissions == []


def test_shortcut_without_grant_shows_connection_guidance_without_calendar_io(
    view: tuple[CalendarDialoguePanel, SyntheticDailyController, Clock],
) -> None:
    panel, controller, _ = view
    panel.today_button.click()
    assert panel.preview.text() == ""
    assert panel.status.text() == controller.status_projection.status_text
    assert "IANA" not in panel.status.text()
    assert not panel.consent.isEnabled()
    assert not panel.refresh_card_button.isEnabled()
    assert controller.submissions == []


def test_refresh_card_clears_old_answer_and_requires_new_one_use_consent(
    view: tuple[CalendarDialoguePanel, SyntheticDailyController, Clock],
) -> None:
    panel, controller, _ = view
    controller.ready()
    panel.today_button.click()
    panel.consent.setChecked(True)
    panel.run_button.click()
    previous = controller.submissions[-1].permit.proposal
    controller.finish_empty()
    assert panel.answer.toPlainText()
    panel.refresh_card_button.click()
    assert panel.answer.toPlainText() == ""
    assert panel.preview.text()
    assert not panel.consent.isChecked()
    assert not panel.run_button.isEnabled()
    assert len(controller.submissions) == 1
    panel.consent.setChecked(True)
    panel.run_button.click()
    current = controller.submissions[-1].permit.proposal
    assert len(controller.submissions) == 2
    assert current.day == previous.day
    assert current.kind == previous.kind
    assert current.window == previous.window
    assert current.proposal_id != previous.proposal_id
    assert current.turn_id != previous.turn_id


def test_same_day_shortcut_consumes_previous_checked_preview_without_reading(
    view: tuple[CalendarDialoguePanel, SyntheticDailyController, Clock],
) -> None:
    panel, controller, _ = view
    controller.ready()
    panel.today_button.click()
    previous = panel._proposal
    panel.consent.setChecked(True)
    panel.today_button.click()
    assert panel._proposal is not None
    assert panel._proposal != previous
    assert not panel.consent.isChecked()
    assert not panel.run_button.isEnabled()
    assert controller.submissions == []


def test_catalog_expiry_preserves_current_grant_selection_for_new_dialogue_preview(
    view: tuple[CalendarDialoguePanel, SyntheticDailyController, Clock],
) -> None:
    panel, controller, clock = view
    controller.ready()
    clock.value += timedelta(minutes=6)
    controller.tick()
    assert controller.catalog == ()
    assert controller.grant is not None
    assert tuple(item.source for item in controller.selected_calendars) == (SOURCE,)
    panel.today_button.click()
    assert "PUBLIC DAILY CALENDAR" in panel.sources.text()
    assert panel.preview.text()
    assert panel.consent.isEnabled()
    assert not panel.consent.isChecked()
    assert controller.submissions == []


def test_grant_expiry_clears_selection_and_blocks_refresh_without_hidden_reconnect(
    view: tuple[CalendarDialoguePanel, SyntheticDailyController, Clock],
) -> None:
    panel, controller, clock = view
    controller.ready()
    panel.today_button.click()
    panel.consent.setChecked(True)
    clock.value += timedelta(minutes=61)
    controller.tick()
    assert controller.grant is None
    assert controller.selected_calendars == ()
    assert panel.preview.text() == ""
    assert not panel.consent.isChecked()
    assert not panel.refresh_card_button.isEnabled()
    assert controller.submissions == []


def test_shared_summary_is_plain_text_and_does_not_contain_source_titles(
    view: tuple[CalendarDialoguePanel, SyntheticDailyController, Clock],
) -> None:
    panel, controller, _ = view
    controller.ready()
    assert panel.connection_summary.text() == controller.status_projection.status_text
    assert panel.connection_summary.textFormat() == Qt.TextFormat.PlainText
    assert "PUBLIC DAILY CALENDAR" not in panel.connection_summary.text()
    assert "PUBLIC DAILY ACCOUNT" not in panel.connection_summary.text()


def test_my_day_keeps_exact_granted_selection_visible_after_catalog_expiry(
    app: QApplication, tmp_path: Path
) -> None:
    del app
    clock = Clock()
    controller = SyntheticDailyController(tmp_path, clock)
    panel = CalendarPanel(tmp_path, controller=controller)
    try:
        controller.ready()
        clock.value += timedelta(minutes=6)
        controller.tick()
        assert panel.calendars.count() == 0
        assert "в действующем разрешении: 1" in panel.selection.text()
        assert "PUBLIC DAILY ACCOUNT / <b>PUBLIC DAILY CALENDAR</b>" in panel.selection.text()
        assert panel.selection.textFormat() == Qt.TextFormat.PlainText
        assert panel.connection_summary.text() == controller.status_projection.status_text
        assert controller.submissions == []
    finally:
        panel.shutdown()
        panel.close()
        panel.deleteLater()
        controller.deleteLater()
        QApplication.processEvents()


def test_my_day_timed_event_displays_both_offsets_across_repeated_hour(
    app: QApplication, tmp_path: Path
) -> None:
    del app
    clock = Clock()
    controller = SyntheticDailyController(tmp_path, clock)
    try:
        controller.ready()
        request = controller._coordinator.prepare_day(date(2026, 10, 25), "Europe/Berlin")
        snapshot = controller._coordinator.accept_result(
            request,
            CalendarReadResult(
                request_id=request.request_id,
                observed_at=clock(),
                status="ok",
                occurrences=(
                    CalendarOccurrence(
                        source=SOURCE,
                        event_id="PUBLIC FOLD EVENT",
                        occurrence_id="PUBLIC FOLD OCCURRENCE",
                        title="PUBLIC FOLD TITLE",
                        starts_at=datetime(2026, 10, 25, 0, 45, tzinfo=UTC),
                        ends_at=datetime(2026, 10, 25, 1, 15, tzinfo=UTC),
                        timezone="Europe/Berlin",
                        status="confirmed",
                    ),
                ),
            ),
        )
        text = CalendarPanel._event_text(snapshot)
        assert "25.10 02:45 +0200 — 25.10 02:15 +0100" in text
    finally:
        controller.close()
        controller.deleteLater()
        QApplication.processEvents()


def test_panel_waits_for_controller_close_receipt_and_forwards_it_once(
    app: QApplication, tmp_path: Path
) -> None:
    del app
    controller = DelayedShutdownController(tmp_path, Clock())
    panel = CalendarPanel(tmp_path, controller=controller)
    receipts: list[bool] = []
    panel.shutdown_finished.connect(lambda: receipts.append(panel.shutdown_complete))
    try:
        panel.auto_refresh_timer.start()
        panel.shutdown()
        assert controller.close_calls == 1
        assert not panel.auto_refresh_timer.isActive()
        assert not panel.shutdown_complete
        assert receipts == []
        QApplication.processEvents()
        assert receipts == []
        controller.finish_close()
        assert panel.shutdown_complete
        assert receipts == [True]
    finally:
        controller.finished = True
        CalendarController.close(controller)
        panel.close()
        panel.deleteLater()
        controller.deleteLater()
        QApplication.processEvents()


def test_idle_panel_shutdown_is_idempotent_and_emits_one_completion(
    app: QApplication, tmp_path: Path
) -> None:
    del app
    controller = SyntheticDailyController(tmp_path, Clock())
    panel = CalendarPanel(tmp_path, controller=controller)
    receipts: list[bool] = []
    panel.shutdown_finished.connect(lambda: receipts.append(panel.shutdown_complete))
    try:
        panel.shutdown()
        panel.shutdown()
        QApplication.processEvents()
        assert panel.shutdown_complete
        assert receipts == [True]
        assert controller.submissions == []
    finally:
        panel.close()
        panel.deleteLater()
        controller.deleteLater()
        QApplication.processEvents()
