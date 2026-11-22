"""Synthetic schedule transport checks; no Vision, accounts or EventKit invocation."""

import json
import os
import sys
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from datetime import time as Time
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from satori.application.assistant.contracts import CalendarSourceRef
from satori.application.assistant.schedule_contracts import ReviewedSchedule, ScheduleRow
from satori.desktop.calendar_controller import CalendarController
from satori.desktop.ui import DesktopApplication

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
SOURCE = CalendarSourceRef(account_id="public-source", calendar_id="public-work")


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or DesktopApplication([])
    assert isinstance(application, QApplication)
    return application


def wait(app: QApplication, condition: Any) -> None:
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition()


@pytest.fixture
def controllers() -> Iterator[list[CalendarController]]:
    values: list[CalendarController] = []
    yield values
    for value in values:
        value.close()
        for job in tuple(value._retired):
            if not job.disposed:
                job.process.waitForFinished(1000)
        value.deleteLater()
    QApplication.processEvents()


@pytest.fixture
def helper(tmp_path: Path) -> Path:
    path = tmp_path / "public-schedule-helper.py"
    path.write_text(
        "import json,sys,time\n"
        "r=json.loads(sys.stdin.buffer.read()); op=r['operation'];mode=sys.argv[1]\n"
        "o={'schema_version':1,'request_id':r['request_id'],'operation':op,'status':'ok',"
        "'access':'full_access','error':None,'observed_at':'2026-10-26T12:00:00Z'}\n"
        "if op=='list_calendars':o['calendars']=[{'source_id':'public-source',"
        "'calendar_id':'public-work','title':'PUBLIC WORK','source_title':'PUBLIC ACCOUNT',"
        "'writable':mode!='readonly','source_kind':'caldav'}]\n"
        "elif op=='recognize_schedule_image':\n"
        " o.update(ocr_lines=[{'text':'26 09:00-18:00','confidence':.6}],ocr_partial=True)\n"
        "elif op in ('preflight_schedule','create_schedule','reconcile_schedule'):\n"
        " outcome='created' if op=='create_schedule' else 'owned_exact' "
        "if op=='reconcile_schedule' or mode=='duplicate' else 'absent'\n"
        " o.update(plan_fingerprint=r['plan_fingerprint'],schedule_results=["
        "{'row_id':x['row_id'],'owned_token':x['owned_token'],'outcome':outcome} "
        "for x in r['rows']])\n"
        " if mode=='wrong-fingerprint':o['plan_fingerprint']='0'*64\n"
        " if mode=='wrong-row':o['schedule_results'][0]['owned_token']='0'*64\n"
        " if mode=='duplicate-row':o['schedule_results'].append(o['schedule_results'][0])\n"
        " if mode=='future':o['observed_at']='2026-10-27T12:00:00Z'\n"
        " if mode=='late-create' and op=='create_schedule':time.sleep(30)\n"
        " if mode=='late-preflight' and op=='preflight_schedule':time.sleep(30)\n"
        " if mode=='bad-create' and op=='create_schedule':print('untrusted');sys.exit(0)\n"
        " if mode=='error-create' and op=='create_schedule':\n"
        "  o.pop('plan_fingerprint');o.pop('schedule_results');"
        "o.update(status='error',error='operation_unknown')\n"
        "if mode=='extra' and op=='preflight_schedule':o['event_body']='UNTRUSTED'\n"
        "if mode=='deny-create' and op=='create_schedule':\n"
        " o.pop('plan_fingerprint');o.pop('schedule_results');"
        "o.update(status='error',access='denied',error='permission_denied')\n"
        "if mode=='bad-ocr' and op=='recognize_schedule_image':"
        "o['ocr_lines'][0]['confidence']=True\n"
        "if mode in ('bounds-ocr','bad-bounds','infinite-bounds') "
        "and op=='recognize_schedule_image':\n"
        " o['ocr_lines'][0]['bounds']={'x':.2,'y':.2,'width':.1,'height':.1}\n"
        " if mode=='bad-bounds':o['ocr_lines'][0]['bounds']['x']=1.0\n"
        " if mode=='infinite-bounds':o['ocr_lines'][0]['bounds']['x']=float('nan')\n"
        "print(json.dumps(o));sys.exit(1 if o['status']=='error' else 0)\n"
    )
    return path


def owner_for(
    app: QApplication, helper: Path, controllers: list[CalendarController], *, mode: str = "ok"
) -> tuple[CalendarController, Clock]:
    clock = Clock()
    owner = CalendarController(
        helper.parent / "public-profile",
        helper_command=lambda: (sys.executable, str(helper), mode),
        clock=clock,
    )
    controllers.append(owner)
    owner.connect_calendar()
    wait(app, lambda: not owner.busy)
    assert owner.catalog
    return owner, clock


def schedule() -> ReviewedSchedule:
    return ReviewedSchedule(
        schema="satori-schedule",
        version=1,
        year=2026,
        month=10,
        timezone="Europe/Moscow",
        legend="PUBLIC: explicit times",
        reviewed=True,
        rows=(
            ScheduleRow(
                date=date(2026, 10, 26),
                start=Time(9),
                end=Time(18),
                end_date=date(2026, 10, 26),
                title="PUBLIC SHIFT",
            ),
        ),
    )


def prepared(
    app: QApplication, helper: Path, controllers: list[CalendarController], mode: str = "ok"
) -> CalendarController:
    owner, _ = owner_for(app, helper, controllers, mode=mode)
    assert owner.schedule.prepare(schedule(), SOURCE)
    assert owner.schedule.preflight()
    wait(app, lambda: not owner.busy)
    return owner


def test_no_native_operation_at_construction(
    app: QApplication, tmp_path: Path, controllers: list[CalendarController]
) -> None:
    calls: list[bool] = []
    owner = CalendarController(tmp_path, helper_command=lambda: calls.append(True))
    controllers.append(owner)
    assert owner.schedule.state == "empty"
    assert not owner.schedule.can_create
    assert not owner.schedule.can_reconcile
    owner.tick()
    app.processEvents()
    assert calls == []


def test_read_grant_does_not_authorize_any_schedule_write(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    owner, _ = owner_for(app, helper, controllers)
    assert owner.use_calendars((SOURCE,))
    assert not owner.schedule.create()
    assert not owner.busy
    assert owner.grant is not None


def test_transport_has_no_unprepared_or_overridden_operation_route(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    owner, _ = owner_for(app, helper, controllers)
    assert not owner.start_schedule("create_schedule", {"operation": "list_calendars"})
    assert not owner.start_schedule("recognize_schedule_image", {"image_path": "/public/image"})
    assert not owner.busy


def test_reentrant_signal_cannot_substitute_target_or_rows_under_consumed_permit(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    owner = prepared(app, helper, controllers)
    attempted: list[bool] = []

    def substitute() -> None:
        if owner.schedule.status != "create_schedule" or attempted:
            return
        plan = owner.schedule.prepared_plan
        assert plan is not None
        changed = owner.schedule._body(plan)
        changed["target"] = {"source_id": "other-source", "calendar_id": "other-calendar"}
        attempted.append(owner.start_schedule("create_schedule", changed))

    owner.schedule.changed.connect(substitute)
    assert owner.schedule.create()
    wait(app, lambda: not owner.busy)
    assert attempted == [False]
    assert owner.schedule.state == "committed"


def test_reentrant_edit_after_consume_stops_dispatch_and_requires_reconcile(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    owner = prepared(app, helper, controllers)
    edited: list[bool] = []

    def edit() -> None:
        if owner.schedule.status == "create_schedule" and not edited:
            edited.append(True)
            owner.schedule.invalidate_preview()

    owner.schedule.changed.connect(edit)
    assert not owner.schedule.create()
    assert not owner.busy
    assert owner.schedule.state == "unknown"
    assert not owner.schedule.can_create


def test_preview_preflight_and_one_explicit_create_use_same_owned_transport(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    owner = prepared(app, helper, controllers)
    assert owner.grant is None  # write consent is independent of read grant
    assert owner.schedule.can_create
    plan = owner.schedule.prepared_plan
    assert plan is not None
    assert owner.schedule.create()
    job = owner._job
    assert job is not None
    assert job.request is None
    assert job.schedule_operation
    assert owner.schedule.state == "consumed"
    assert not owner.schedule.create()  # cannot replay while in flight
    wait(app, lambda: not owner.busy)
    assert owner.schedule.state == "committed"
    assert not owner.schedule.can_create
    assert not owner.schedule.create()
    receipt = owner.schedule.receipt
    assert receipt is not None
    assert receipt["local_eventkit_only"] is True
    assert receipt["remote_icloud_sync"] == "not_verified"
    assert "PUBLIC SHIFT" not in json.dumps(receipt)


@pytest.mark.parametrize("mode", ["readonly", "duplicate"])
def test_unwritable_or_existing_rows_cannot_create(
    app: QApplication, helper: Path, controllers: list[CalendarController], mode: str
) -> None:
    owner, _ = owner_for(app, helper, controllers, mode=mode)
    if mode == "readonly":
        assert not owner.schedule.prepare(schedule(), SOURCE)
    else:
        assert owner.schedule.prepare(schedule(), SOURCE)
        assert owner.schedule.preflight()
        wait(app, lambda: not owner.busy)
        assert owner.schedule.state == "conflict"
    assert not owner.schedule.can_create
    assert not owner.schedule.create()


@pytest.mark.parametrize(
    "mode", ["wrong-fingerprint", "wrong-row", "duplicate-row", "future", "extra"]
)
def test_invalid_preflight_never_issues_create_permit(
    app: QApplication, helper: Path, controllers: list[CalendarController], mode: str
) -> None:
    owner = prepared(app, helper, controllers, mode)
    assert owner.schedule.status == "invalid_response"
    assert not owner.schedule.can_create


@pytest.mark.parametrize("mode", ["bad-create", "error-create", "late-create"])
def test_uncertain_create_requires_explicit_reconciliation_and_never_replays(
    app: QApplication, helper: Path, controllers: list[CalendarController], mode: str
) -> None:
    owner = prepared(app, helper, controllers, mode)
    assert owner.schedule.create()
    if mode == "late-create":
        job = owner._job
        assert job is not None
        owner._timed_out(job)
    else:
        wait(app, lambda: not owner.busy)
    assert owner.schedule.state == "unknown"
    assert owner.schedule.status == "schedule_unknown"
    assert not owner.schedule.create()
    assert not owner.schedule.prepare(schedule(), SOURCE)
    assert owner.schedule.can_reconcile
    assert owner.schedule.reconcile()
    wait(app, lambda: not owner.busy)
    assert owner.schedule.state == "reconciled"
    assert not owner.schedule.can_create


def test_edit_and_context_change_invalidate_unspent_preview(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    owner = prepared(app, helper, controllers)
    owner.schedule.invalidate_preview()
    assert owner.schedule.prepared_plan is None
    assert not owner.schedule.create()
    assert owner.schedule.prepare(schedule(), SOURCE)
    owner.selection_changed()
    assert owner.schedule.prepared_plan is None


def test_edit_cancels_only_pending_preflight_not_read_grant(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    owner, _ = owner_for(app, helper, controllers, mode="late-preflight")
    assert owner.use_calendars((SOURCE,))
    assert owner.schedule.prepare(schedule(), SOURCE)
    assert owner.schedule.preflight()
    owner.schedule.invalidate_preview()
    assert not owner.busy
    assert not owner.schedule.busy
    assert owner.grant is not None
    assert owner.schedule.prepared_plan is None


def test_expired_confirmation_cannot_start_create(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    owner, clock = owner_for(app, helper, controllers)
    assert owner.schedule.prepare(schedule(), SOURCE)
    assert owner.schedule.preflight()
    wait(app, lambda: not owner.busy)
    clock.value += timedelta(minutes=5)
    assert not owner.schedule.can_create
    assert not owner.schedule.create()
    assert not owner.busy


def test_profile_change_clears_import_data_and_permission_read_metadata(
    app: QApplication, helper: Path, controllers: list[CalendarController], tmp_path: Path
) -> None:
    owner = prepared(app, helper, controllers)
    owner.last_successful_read_at = NOW
    owner.set_profile(tmp_path / "different-public-profile")
    assert owner.schedule.prepared_plan is None
    assert owner.schedule.reviewed_schedule is None
    assert owner.schedule.receipt is None
    assert owner.permission_status == "unknown"
    assert owner.last_successful_read_at is None


def test_unknown_metadata_survives_restart_and_requires_same_reviewed_rows_for_reconcile(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    original = prepared(app, helper, controllers, "bad-create")
    assert original.schedule.create()
    wait(app, lambda: not original.busy)
    assert original.schedule.state == "unknown"
    journal = helper.parent / "public-profile" / "calendar-import-pending.json"
    content = journal.read_text()
    assert "PUBLIC SHIFT" not in content
    assert "2026-10-26" not in content
    assert "09:00" not in content
    original.close()
    restored, _ = owner_for(app, helper, controllers)
    assert restored.schedule.status == "schedule_recovery_required"
    assert not restored.schedule.can_create
    assert restored.schedule.prepare(schedule(), SOURCE)
    assert restored.schedule.state == "unknown"
    assert not restored.schedule.preflight()
    assert not restored.schedule.create()
    assert restored.schedule.can_reconcile
    assert restored.schedule.reconcile()
    wait(app, lambda: not restored.busy)
    assert restored.schedule.state == "reconciled"
    assert not journal.exists()
    assert restored.schedule.prepare(schedule(), SOURCE)
    assert restored.schedule.state == "prepared"
    assert not restored.schedule.can_create


def test_invalid_pending_journal_blocks_create_without_discarding_record(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    directory = helper.parent / "public-profile"
    directory.mkdir()
    journal = directory / "calendar-import-pending.json"
    journal.write_text("{}")
    journal.chmod(0o600)
    owner, _ = owner_for(app, helper, controllers)
    assert owner.schedule.status == "schedule_journal_invalid"
    assert not owner.schedule.prepare(schedule(), SOURCE)
    assert not owner.schedule.create()
    assert not owner.busy
    assert journal.read_text() == "{}"


def test_permission_loss_during_create_revokes_read_grant_and_keeps_unknown_receipt(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    owner, _ = owner_for(app, helper, controllers, mode="deny-create")
    assert owner.use_calendars((SOURCE,))
    assert owner.schedule.prepare(schedule(), SOURCE)
    assert owner.schedule.preflight()
    wait(app, lambda: not owner.busy)
    assert owner.schedule.create()
    wait(app, lambda: not owner.busy)
    assert owner.permission_status == "denied"
    assert owner.grant is None
    assert owner.snapshot is None
    assert owner.catalog == ()
    assert owner.schedule.state == "unknown"
    assert not owner.schedule.can_create
    assert not owner.schedule.can_reconcile


def test_catalog_reconnect_preserves_local_review_but_invalidates_write_permit(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    owner = prepared(app, helper, controllers)
    assert owner.schedule.reviewed_schedule is not None
    owner.connect_calendar()
    wait(app, lambda: not owner.busy)
    assert owner.schedule.reviewed_schedule is not None
    assert owner.schedule.prepared_plan is None
    assert not owner.schedule.can_create
    owner.disconnect_calendar()
    assert owner.schedule.reviewed_schedule is None


@pytest.mark.parametrize("mode", ["ok", "bad-ocr", "bounds-ocr", "bad-bounds", "infinite-bounds"])
def test_selected_image_ocr_is_transient_and_does_not_prepare_write(
    app: QApplication,
    helper: Path,
    controllers: list[CalendarController],
    tmp_path: Path,
    mode: str,
) -> None:
    owner, _ = owner_for(app, helper, controllers, mode=mode)
    image = tmp_path / "public-not-a-real-image.png"
    image.write_bytes(b"synthetic selected file; fake handles OCR")
    owner.schedule.recognize_image(image)
    job = owner._job
    assert job is not None
    assert job.timer.interval() == 120_000
    wait(app, lambda: not owner.busy)
    assert owner.schedule.prepared_plan is None
    assert owner.schedule.reviewed_schedule is None
    assert not owner.schedule.can_create
    if mode in {"ok", "bounds-ocr"}:
        assert owner.schedule.ocr_lines[0].confidence == 0.6
        assert owner.schedule.ocr_partial
        if mode == "bounds-ocr":
            bounds = owner.schedule.ocr_lines[0].bounds
            assert bounds is not None
            assert bounds.x == 0.2
    else:
        assert owner.schedule.status == "invalid_response"
        assert not owner.schedule.ocr_lines
