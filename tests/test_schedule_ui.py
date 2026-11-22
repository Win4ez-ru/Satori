"""Synthetic schedule review gestures; no EventKit, photos, account or Core operations."""

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QTableWidgetItem

from satori.application.assistant.contracts import (
    CalendarCatalogDiagnostics,
    CalendarDescriptor,
    CalendarSourceRef,
    LocalPrincipal,
)
from satori.application.assistant.schedule import dump_schedule_json
from satori.application.assistant.schedule_contracts import ReviewedSchedule, ScheduleOCRBounds
from satori.desktop.calendar_controller import CalendarController
from satori.desktop.schedule_ui import ScheduleImportPanel
from satori.desktop.ui import DesktopApplication

SOURCE = CalendarSourceRef(account_id="PUBLIC-SOURCE", calendar_id="PUBLIC-CALENDAR")
OTHER = CalendarSourceRef(account_id="OTHER-SOURCE", calendar_id="OTHER-CALENDAR")
CATALOG_OBSERVED = datetime(2027, 2, 1, 12, tzinfo=UTC)


@dataclass
class OCRLine:
    text: str
    confidence: float
    bounds: ScheduleOCRBounds | None = None


class ScheduleFake(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.status = "idle"
        self.state = "empty"
        self.busy = False
        self.ocr_lines: tuple[OCRLine, ...] = ()
        self.ocr_partial = False
        self.prepared_plan: object | None = None
        self.can_create = False
        self.can_reconcile = False
        self.receipt: dict[str, object] | None = None
        self.reviewed_schedule: ReviewedSchedule | None = None
        self.invalidations = 0
        self.image_paths: list[Path] = []
        self.prepares: list[tuple[ReviewedSchedule, CalendarSourceRef]] = []
        self.preflights = 0
        self.creates = 0
        self.reconciles = 0

    def invalidate_preview(self) -> None:
        self.invalidations += 1
        self.can_create = False
        self.prepared_plan = None
        self.state = "empty"
        self.status = "idle"
        self.changed.emit()

    def recognize_image(self, path: Path) -> None:
        self.image_paths.append(path)

    def load_reviewed(self, payload: bytes) -> None:
        del payload

    def prepare(self, schedule: ReviewedSchedule, target: CalendarSourceRef) -> bool:
        self.prepares.append((schedule, target))
        self.reviewed_schedule = schedule
        self.status = "preview_ready"
        self.state = "prepared"
        self.prepared_plan = object()
        self.changed.emit()
        return True

    def preflight(self) -> bool:
        self.preflights += 1
        self.can_create = True
        self.status = "ready_to_create"
        self.state = "ready"
        self.changed.emit()
        return True

    def create(self) -> bool:
        self.creates += 1
        self.can_create = False
        self.status = "committed"
        self.state = "committed"
        self.receipt = {"created": 1}
        self.changed.emit()
        return True

    def reconcile(self) -> bool:
        self.reconciles += 1
        return True

    def export_ics(self) -> bytes:
        return b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"


class CalendarFake(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.schedule = ScheduleFake()
        self.catalog: tuple[CalendarDescriptor, ...] = ()
        self.busy = False
        self.status = "disconnected"
        self.connects = 0
        self.catalog_diagnostics = CalendarCatalogDiagnostics(state="never_loaded")
        self.principal = LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4())

    def publish_catalog(self) -> None:
        self.publish_calendars(
            (
                CalendarDescriptor(
                    source=SOURCE, title="Работа", account_title="PUBLIC iCloud", writable=True
                ),
                CalendarDescriptor(
                    source=OTHER, title="Работа", account_title="PUBLIC iCloud", writable=True
                ),
                CalendarDescriptor(
                    source=CalendarSourceRef(account_id="READONLY", calendar_id="HOLIDAY"),
                    title="Праздники",
                    account_title="PUBLIC",
                    writable=False,
                ),
            )
        )

    def publish_calendars(self, descriptors: tuple[CalendarDescriptor, ...]) -> None:
        self.catalog = descriptors
        self.catalog_diagnostics = CalendarCatalogDiagnostics(
            state="fresh",
            total_count=len(descriptors),
            writable_count=sum(item.write_capability == "writable" for item in descriptors),
            read_only_count=sum(item.write_capability == "read_only" for item in descriptors),
            unreported_count=sum(item.write_capability == "not_reported" for item in descriptors),
            unsupported_count=sum(
                item.write_capability == "unsupported_entity" for item in descriptors
            ),
            observed_at=CATALOG_OBSERVED,
            age_seconds=0.0,
        )
        self.status = "select_calendars"
        self.changed.emit()

    def connect_calendar(self) -> None:
        self.connects += 1
        self.catalog = ()
        self.busy = True
        self.status = "connecting"
        previous = self.catalog_diagnostics
        self.catalog_diagnostics = CalendarCatalogDiagnostics(
            state="loading",
            total_count=previous.total_count,
            writable_count=previous.writable_count,
            read_only_count=previous.read_only_count,
            unreported_count=previous.unreported_count,
            unsupported_count=previous.unsupported_count,
            observed_at=previous.observed_at,
            age_seconds=previous.age_seconds,
        )
        self.changed.emit()


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or DesktopApplication([])  # type: ignore[return-value]


@pytest.fixture
def view(app: QApplication) -> Iterator[tuple[ScheduleImportPanel, CalendarFake]]:
    del app
    calendar = CalendarFake()
    panel = ScheduleImportPanel(cast(CalendarController, calendar))
    yield panel, calendar
    panel.close()
    panel.deleteLater()
    calendar.schedule.deleteLater()
    calendar.deleteLater()
    QApplication.processEvents()


def fill(panel: ScheduleImportPanel) -> None:
    panel.year.setText("2026")
    panel.month.setText("10")
    panel.timezone.setText("Europe/Moscow")
    panel.legend.setText("PUBLIC: exact selected row and explicit working hours")
    panel.add_button.click()
    for column, value in enumerate(
        ("2026-10-26", "09:00", "18:00", "2026-10-26", "Рабочая смена", "", "")
    ):
        panel.rows.setItem(0, column, QTableWidgetItem(value))
    panel.target.setCurrentIndex(1)
    panel.reviewed.setChecked(True)


def prepare(panel: ScheduleImportPanel, calendar: CalendarFake) -> None:
    calendar.publish_catalog()
    fill(panel)
    panel.prepare_button.click()


def table_item(panel: ScheduleImportPanel, row: int, column: int) -> QTableWidgetItem:
    item = panel.rows.item(row, column)
    assert item is not None
    return item


def catalog_entries(
    *, writable_count: int = 5, missing_flags: bool = False, unsupported: bool = False
) -> tuple[CalendarDescriptor, ...]:
    entries: list[CalendarDescriptor] = []
    for index in range(7):
        fields: dict[str, object] = {
            "source": {"account_id": "PUBLIC-SOURCE", "calendar_id": f"PUBLIC-{index}"},
            "title": f"PUBLIC CALENDAR {index}",
            "account_title": "PUBLIC ACCOUNT",
        }
        if not missing_flags:
            fields["writable"] = index < writable_count and not unsupported
        if unsupported:
            fields["supports_events"] = False
        entries.append(CalendarDescriptor.model_validate_json(json.dumps(fields)))
    return tuple(entries)


def synthetic_twenty_two_rows() -> ReviewedSchedule:
    first = date(2027, 2, 1)
    rows = []
    for index in range(22):
        day = (first + timedelta(days=index)).isoformat()
        rows.append(
            {
                "date": day,
                "start": "09:00",
                "end": "18:00",
                "end_date": day,
                "title": "PUBLIC SHIFT",
            }
        )
    return ReviewedSchedule.model_validate_json(
        json.dumps(
            {
                "schema": "satori-schedule",
                "version": 1,
                "year": 2027,
                "month": 2,
                "timezone": "Europe/Moscow",
                "legend": "PUBLIC: synthetic fixed hours",
                "reviewed": True,
                "rows": rows,
            }
        )
    )


def form_contents(panel: ScheduleImportPanel) -> tuple[object, ...]:
    return (
        panel.year.text(),
        panel.month.text(),
        panel.timezone.text(),
        panel.legend.text(),
        panel.ocr.toPlainText(),
        panel.reviewed.isChecked(),
        tuple(
            tuple(
                table_item(panel, row, column).text() for column in range(panel.rows.columnCount())
            )
            for row in range(panel.rows.rowCount())
        ),
    )


def test_fresh_catalog_reports_seven_resources_and_only_five_writable_targets(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    calendar.publish_calendars(catalog_entries())
    assert panel.target.count() == 6
    assert panel.target.currentData() is None
    text = panel.catalog_status.text()
    assert "Календарей: 7" in text
    assert "доступна запись: 5" in text
    assert "создание не разрешено: 2" in text
    assert "Возраст списка: 0" in text
    assert calendar.connects == 0
    assert calendar.schedule.creates == 0


def test_explicit_nonwritable_flags_are_reported_without_claiming_os_permission_denial(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    calendar.publish_calendars(catalog_entries(writable_count=0))
    assert panel.target.count() == 1
    text = panel.catalog_status.text()
    assert "создание не разрешено: 7" in text
    assert "не разрешено ни одним календарём" in text
    assert "Системный доступ запрещён" not in text
    assert "Компонент не сообщил" not in text


def test_missing_write_flags_are_unknown_compatibility_and_never_writable_or_readonly(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    calendar.publish_calendars(catalog_entries(missing_flags=True))
    assert panel.target.count() == 1
    text = panel.catalog_status.text()
    assert "возможность записи не сообщена: 7" in text
    assert "Компонент не сообщил возможность записи" in text
    assert "запрет записи не подтверждён" in text
    assert "не разрешено ни одним календарём" not in text
    assert not panel.create_button.isEnabled()


def test_unsupported_event_entities_are_separate_from_unreported_write_flags(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    calendar.publish_calendars(catalog_entries(unsupported=True))
    assert panel.target.count() == 1
    text = panel.catalog_status.text()
    assert "неподдерживаемые ресурсы: 7" in text
    assert "не поддерживает создание событий" in text
    assert "Компонент не сообщил" not in text


def test_stale_catalog_clears_targets_and_offers_explicit_refresh_without_auto_connect(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    calendar.publish_calendars(catalog_entries())
    panel.target.setCurrentIndex(1)
    calendar.catalog = ()
    calendar.catalog_diagnostics = calendar.catalog_diagnostics.model_copy(
        update={"state": "stale", "age_seconds": 301.0}
    )
    calendar.changed.emit()
    panel.show()
    QApplication.processEvents()
    assert panel.target.count() == 1
    assert panel.target.currentData() is None
    assert "устарел" in panel.catalog_status.text()
    assert "Обновить список календарей" in panel.catalog_status.text()
    assert "301 сек" in panel.catalog_status.text()
    assert panel.catalog_refresh_button.isVisible()
    assert panel.catalog_refresh_button.isEnabled()
    assert calendar.connects == 0
    assert not panel.create_button.isEnabled()


def test_direct_catalog_refresh_preserves_all_synthetic_twenty_two_rows_and_metadata(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    calendar.publish_catalog()
    panel._show_document(synthetic_twenty_two_rows())
    panel.target.setCurrentIndex(1)
    panel.reviewed.setChecked(True)
    panel.prepare_button.click()
    panel.preflight_button.click()
    panel.confirm.setChecked(True)
    assert panel.create_button.isEnabled()
    before = form_contents(panel)
    panel.catalog_refresh_button.click()
    assert calendar.connects == 1
    assert calendar.busy
    assert form_contents(panel) == before
    assert "Строк смен в форме: 22" in panel.status.text()
    assert "22" in panel.draft_status.text()
    assert "Обновляю список" in panel.catalog_status.text()
    assert not panel.catalog_refresh_button.isEnabled()
    assert panel.target.currentData() is None
    assert panel.preview.toPlainText() == ""
    assert not panel.confirm.isChecked()
    assert not panel.create_button.isEnabled()
    assert calendar.schedule.creates == 0
    calendar.busy = False
    calendar.publish_calendars(catalog_entries())
    assert form_contents(panel) == before
    assert panel.target.currentData() is None
    assert panel.target.count() == 6
    assert panel.catalog_refresh_button.isEnabled()


@pytest.mark.parametrize("busy_owner", ["calendar", "schedule"])
def test_catalog_refresh_is_disabled_during_any_owned_operation(
    view: tuple[ScheduleImportPanel, CalendarFake],
    busy_owner: str,
) -> None:
    panel, calendar = view
    if busy_owner == "calendar":
        calendar.busy = True
        calendar.changed.emit()
    else:
        calendar.schedule.busy = True
        calendar.schedule.changed.emit()
    assert not panel.catalog_refresh_button.isEnabled()
    panel.catalog_refresh_button.click()
    panel._refresh_catalog()
    assert calendar.connects == 0
    assert calendar.schedule.creates == 0


def test_edits_keep_loaded_row_count_in_status_instead_of_requesting_another_file(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    panel._show_document(synthetic_twenty_two_rows())
    panel.legend.setText("PUBLIC corrected legend")
    assert calendar.schedule.status == "idle"
    assert "Строк смен в форме: 22" in panel.status.text()
    assert "Выбери фото" not in panel.status.text()
    assert panel.rows.rowCount() == 22
    assert calendar.connects == 0
    assert calendar.schedule.prepares == []


def weekly_lines(*, overnight: bool = False) -> tuple[OCRLine, ...]:
    first = date(2027, 2, 1)
    headers = tuple(
        OCRLine(
            text=(first + timedelta(days=index)).isoformat(),
            confidence=0.99,
            bounds=ScheduleOCRBounds(x=0.04 + index * 0.14, y=0.9, width=0.08, height=0.04),
        )
        for index in range(7)
    )
    return (
        *headers,
        OCRLine(
            text="22:00-06:00" if overnight else "09:00-18:00",
            confidence=0.99,
            bounds=ScheduleOCRBounds(x=0.05, y=0.55, width=0.06, height=0.05),
        ),
    )


def test_unedited_weekly_ocr_uses_bounds_and_fills_only_explicit_full_header_metadata(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    calendar.schedule.ocr_lines = weekly_lines()
    calendar.schedule.changed.emit()
    panel.parse_button.click()
    assert panel.rows.rowCount() == 1
    assert table_item(panel, 0, 0).text() == "2027-02-01"
    assert table_item(panel, 0, 1).text() == "09:00:00"
    assert panel.year.text() == "2027"
    assert panel.month.text() == "2"
    assert panel.legend.text() == ""
    assert "смысл обозначений" in panel.ambiguities.text()
    assert not panel.reviewed.isChecked()
    assert calendar.schedule.prepares == []
    assert calendar.schedule.creates == 0


def test_editing_ocr_text_discards_original_weekly_bounds_and_uses_plain_manual_path(
    view: tuple[ScheduleImportPanel, CalendarFake],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from satori.desktop import schedule_ui

    panel, calendar = view
    calendar.schedule.ocr_lines = weekly_lines()
    calendar.schedule.changed.emit()
    panel.year.setText("2027")
    panel.month.setText("2")
    panel.legend.setText("PUBLIC: explicit edited row")
    panel.ocr.setPlainText("2027-02-03 10:00-17:00")

    def forbidden_weekly(*args: object, **kwargs: object) -> None:
        pytest.fail("Edited OCR must never reuse its original spatial association")

    monkeypatch.setattr(schedule_ui, "draft_from_weekly_ocr", forbidden_weekly)
    panel.parse_button.click()
    assert panel.rows.rowCount() == 1
    assert table_item(panel, 0, 0).text() == "2027-02-03"
    assert table_item(panel, 0, 1).text() == "10:00:00"
    assert not panel.reviewed.isChecked()
    assert calendar.schedule.prepares == []


@pytest.mark.parametrize("case", ["missing_header", "conflicting_year", "overnight"])
def test_weekly_ocr_never_guesses_missing_dates_conflicting_metadata_or_next_day(
    view: tuple[ScheduleImportPanel, CalendarFake],
    case: str,
) -> None:
    panel, calendar = view
    observations = weekly_lines(overnight=case == "overnight")
    if case == "missing_header":
        observations = observations[1:]
    calendar.schedule.ocr_lines = observations
    calendar.schedule.changed.emit()
    if case == "conflicting_year":
        panel.year.setText("2026")
    panel.parse_button.click()
    assert panel.rows.rowCount() == 0
    if case == "missing_header":
        assert panel.year.text() == panel.month.text() == ""
    elif case == "conflicting_year":
        assert panel.year.text() == "2026"
    else:
        assert "ночной смены" in panel.ambiguities.text()
    assert calendar.schedule.prepares == []
    assert not panel.create_button.isEnabled()


def test_weekly_parse_keeps_partial_ocr_warning_and_never_prepares_automatically(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    calendar.schedule.ocr_lines = weekly_lines()
    calendar.schedule.ocr_partial = True
    calendar.schedule.changed.emit()
    panel.parse_button.click()
    assert "не полностью" in panel.ambiguities.text()
    assert not panel.reviewed.isChecked()
    assert calendar.schedule.prepares == []


def test_startup_has_no_file_or_calendar_operation_and_no_target_default(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    assert calendar.schedule.image_paths == []
    assert calendar.schedule.prepares == []
    assert calendar.schedule.creates == 0
    assert panel.rows.rowCount() == 0
    assert panel.year.text() == panel.month.text() == ""
    assert panel.timezone.text() == "Europe/Moscow"
    assert not panel.create_button.isEnabled()
    assert not panel.confirm.isEnabled()
    calendar.publish_catalog()
    assert panel.target.count() == 3
    assert panel.target.currentData() is None
    assert panel.target.itemText(1) != panel.target.itemText(2)
    assert panel.target.itemData(1) == SOURCE
    assert panel.target.itemData(2) == OTHER


def test_preview_preflight_and_final_checked_create_are_separate_gestures(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    prepare(panel, calendar)
    assert calendar.schedule.prepares[0][1] == SOURCE
    assert "2026-10-26T09:00:00+03:00" in panel.preview.toPlainText()
    assert "2026-10-26T18:00:00+03:00" in panel.preview.toPlainText()
    assert "Работа" in panel.preview.toPlainText()
    assert calendar.schedule.preflights == calendar.schedule.creates == 0
    assert not panel.create_button.isEnabled()
    panel.preflight_button.click()
    assert calendar.schedule.preflights == 1
    assert not panel.confirm.isChecked()
    assert not panel.create_button.isEnabled()
    panel.confirm.setChecked(True)
    assert "1" in panel.confirm.text()
    assert "Работа" in panel.confirm.text()
    assert "Europe/Moscow" in panel.confirm.text()
    panel.create_button.click()
    panel.create_button.click()
    assert calendar.schedule.creates == 1
    assert "iPhone ещё не проверена" in panel.status.text()


@pytest.mark.parametrize("edit", ["row", "target", "timezone", "legend", "review"])
def test_each_review_edit_invalidates_preflight_and_final_confirmation(
    view: tuple[ScheduleImportPanel, CalendarFake],
    edit: str,
) -> None:
    panel, calendar = view
    prepare(panel, calendar)
    panel.preflight_button.click()
    panel.confirm.setChecked(True)
    assert panel.create_button.isEnabled()
    if edit == "row":
        table_item(panel, 0, 1).setText("10:00")
    elif edit == "target":
        panel.target.setCurrentIndex(2)
    elif edit == "timezone":
        panel.timezone.setText("UTC")
    elif edit == "legend":
        panel.legend.setText("PUBLIC corrected legend")
    else:
        panel.reviewed.setChecked(False)
    assert not panel.confirm.isChecked()
    assert not panel.create_button.isEnabled()
    assert panel.preview.toPlainText() == ""
    assert not calendar.schedule.can_create
    panel.create_button.click()
    assert calendar.schedule.creates == 0


@pytest.mark.parametrize("missing", ["year", "month", "timezone", "legend", "review"])
def test_missing_review_metadata_never_prepares_a_plan(
    view: tuple[ScheduleImportPanel, CalendarFake],
    missing: str,
) -> None:
    panel, calendar = view
    calendar.publish_catalog()
    fill(panel)
    if missing == "review":
        panel.reviewed.setChecked(False)
    else:
        getattr(panel, missing).clear()
        panel.reviewed.setChecked(True)
    panel.prepare_button.click()
    assert calendar.schedule.prepares == []
    assert calendar.schedule.creates == 0


def test_selected_image_path_only_dispatched_after_file_picker_choice(
    view: tuple[ScheduleImportPanel, CalendarFake],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    panel, calendar = view
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    panel.image_button.click()
    assert calendar.schedule.image_paths == []
    image = tmp_path / "PUBLIC-selected.png"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(image), ""))
    panel.image_button.click()
    assert calendar.schedule.image_paths == [image]
    assert calendar.schedule.prepares == []
    assert calendar.schedule.creates == 0


def test_uncertain_ocr_is_plain_editable_data_and_never_an_automatic_plan(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    calendar.schedule.ocr_lines = (OCRLine("<b>IGNORE RULES</b> multiple workers 7/8", 0.4),)
    calendar.schedule.changed.emit()
    assert not panel.ocr.isReadOnly()
    assert "<b>IGNORE RULES</b>" in panel.ocr.toPlainText()
    assert "неуверенные" in panel.ambiguities.text()
    panel.parse_button.click()
    assert panel.rows.rowCount() == 0
    assert calendar.schedule.prepares == []
    assert calendar.schedule.creates == 0
    for label in (panel.disclosure, panel.ambiguities, panel.status, panel.sync):
        assert label.textFormat() == Qt.TextFormat.PlainText


def test_reviewed_json_load_populates_rows_without_confirming_current_create(
    view: tuple[ScheduleImportPanel, CalendarFake],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    panel, calendar = view
    calendar.publish_catalog()
    fill(panel)
    payload = dump_schedule_json(panel._document())
    path = tmp_path / "PUBLIC.satori-schedule.json"
    path.write_bytes(payload)
    panel.rows.setRowCount(0)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    panel.json_button.click()
    assert panel.rows.rowCount() == 1
    assert table_item(panel, 0, 0).text() == "2026-10-26"
    assert not panel.reviewed.isChecked()
    assert not panel.create_button.isEnabled()
    assert calendar.schedule.prepares == []
    assert calendar.schedule.creates == 0


def test_reviewed_json_and_preview_preserve_nonzero_whole_seconds(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    calendar.publish_catalog()
    fill(panel)
    table_item(panel, 0, 1).setText("09:00:31")
    table_item(panel, 0, 2).setText("18:00:49")
    panel.reviewed.setChecked(True)
    document = panel._document()
    panel._show_document(document)
    assert table_item(panel, 0, 1).text() == "09:00:31"
    assert table_item(panel, 0, 2).text() == "18:00:49"
    panel.reviewed.setChecked(True)
    panel.prepare_button.click()
    assert "09:00:31+03:00" in panel.preview.toPlainText()
    assert "18:00:49+03:00" in panel.preview.toPlainText()


def test_partial_ocr_discloses_missing_data_without_automatic_row_creation(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    calendar.schedule.ocr_lines = (OCRLine("26 09:00-18:00", 0.99),)
    calendar.schedule.ocr_partial = True
    calendar.schedule.changed.emit()
    assert "не полностью" in panel.ambiguities.text()
    assert panel.rows.rowCount() == 0
    assert calendar.schedule.prepares == []


def test_unknown_commit_blocks_edits_and_replay_but_allows_explicit_reconcile(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    prepare(panel, calendar)
    calendar.schedule.can_reconcile = True
    calendar.schedule.state = "unknown"
    calendar.schedule.status = "unknown"
    calendar.schedule.changed.emit()
    assert not panel.rows.isEnabled()
    assert not panel.prepare_button.isEnabled()
    assert not panel.preflight_button.isEnabled()
    assert not panel.create_button.isEnabled()
    assert panel.reconcile_button.isEnabled()
    panel.reconcile_button.click()
    assert calendar.schedule.reconciles == 1
    assert calendar.schedule.creates == 0


def test_unknown_outcome_still_blocks_edits_when_target_is_temporarily_unavailable(
    view: tuple[ScheduleImportPanel, CalendarFake],
) -> None:
    panel, calendar = view
    prepare(panel, calendar)
    calendar.schedule.state = "unknown"
    calendar.schedule.status = "schedule_unknown"
    calendar.schedule.can_reconcile = False
    calendar.schedule.changed.emit()
    assert not panel.rows.isEnabled()
    assert not panel.prepare_button.isEnabled()
    assert not panel.create_button.isEnabled()
    assert not panel.reconcile_button.isEnabled()
    assert "неизвестен" in panel.status.text()


@pytest.mark.parametrize("transition", ["disconnect", "profile"])
def test_lifecycle_clears_ui_copies_of_photo_and_review_data(
    view: tuple[ScheduleImportPanel, CalendarFake],
    transition: str,
) -> None:
    panel, calendar = view
    prepare(panel, calendar)
    panel.ocr.setPlainText("PUBLIC OCR PHOTO BODY")
    if transition == "disconnect":
        calendar.status = "disconnected"
    else:
        calendar.principal = calendar.principal.model_copy(update={"session_id": uuid4()})
    calendar.changed.emit()
    assert panel.ocr.toPlainText() == ""
    assert panel.rows.rowCount() == 0
    assert panel.legend.text() == ""
    assert panel.preview.toPlainText() == ""
    assert not panel.confirm.isChecked()


def test_ics_export_only_writes_chosen_file_and_does_not_import_or_create(
    view: tuple[ScheduleImportPanel, CalendarFake],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    panel, calendar = view
    prepare(panel, calendar)
    path = tmp_path / "PUBLIC.ics"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    panel.export_ics_button.click()
    assert path.read_bytes().startswith(b"BEGIN:VCALENDAR")
    assert calendar.schedule.creates == 0
    assert calendar.schedule.preflights == 0
    assert "не запускались" in panel.status.text()
