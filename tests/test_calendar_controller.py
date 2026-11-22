"""Selected-calendar controller tests use synthetic owned Python helpers, never EventKit."""

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtWidgets import QApplication

from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarSourceRef,
)
from satori.application.assistant.dialogue_contracts import CalendarReadProposal, SourceInterval
from satori.desktop.calendar_controller import CalendarController, _Event
from satori.desktop.ui import DesktopApplication

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
SOURCE = CalendarSourceRef(account_id="synthetic-account", calendar_id="synthetic-calendar")


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or DesktopApplication([])  # type: ignore[return-value]


def wait_until(app: QApplication, condition: Any, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition()


@pytest.fixture
def helper(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-calendar-helper.py"
    path.write_text(
        "import datetime as dt, json, os, sys, time\n"
        "r=json.loads(sys.stdin.buffer.read())\n"
        "mode=sys.argv[1]; op=r['operation']\n"
        "out={'schema_version':1,'request_id':r['request_id'],'operation':op,'status':'ok',"
        "'access':'full_access','error':None,'observed_at':'2026-10-20T12:00:00Z'}\n"
        "if mode=='access' and op=='status': out['access']='not_determined'\n"
        "if mode=='denied': out.update(status='error',access='denied',error='permission_denied')\n"
        "elif op=='list_calendars':\n"
        " out['calendars']=[{'source_id':'synthetic-account','calendar_id':'synthetic-calendar',"
        "'title':'PUBLIC CALENDAR','source_title':'PUBLIC ACCOUNT'}]\n"
        " if mode=='two-accounts': out['calendars'].append({'source_id':'other-account',"
        "'calendar_id':'synthetic-calendar','title':'SAME NAME','source_title':'OTHER ACCOUNT'})\n"
        "elif op=='read_events':\n"
        " start=dt.datetime.fromisoformat(r['start'])+dt.timedelta(hours=1)\n"
        " end=start+dt.timedelta(hours=1)\n"
        " source=r['selected_calendars'][0]\n"
        " event={'source_id':source['source_id'],'calendar_id':source['calendar_id'],"
        "'event_id':'event-1',"
        "'occurrence_start':start.isoformat(),'title':'PUBLIC EVENT <b>literal</b>',"
        "'start':start.isoformat(),'end':end.isoformat(),'all_day':False,"
        "'start_date':None,'end_date':None,'time_zone':r['time_zone'],'status':'tentative'}\n"
        " out.update(events=[event],partial=mode=='partial')\n"
        " if mode=='empty':out['events']=[]\n"
        " if mode=='wrong-source':event['source_id']='unselected-account'\n"
        " if mode=='duplicate':out['events'].append(dict(event))\n"
        " if mode=='outside':event['start']='2020-01-01T00:00:00Z';"
        "event['end']='2020-01-01T01:00:00Z'\n"
        " if mode=='all-day':\n"
        "  event.update(all_day=True,start_date=r['start'][:10],end_date=r['end'][:10])\n"
        " if mode=='many-events':out['events']=[dict(event,event_id=str(i)) for i in range(101)]\n"
        " if mode=='future':out['observed_at']='2026-10-21T12:00:00Z'\n"
        " if mode=='late-read':time.sleep(30)\n"
        "if mode=='wrong-id':out['request_id']='00000000-0000-0000-0000-000000000001'\n"
        "if mode=='bool-version':out['schema_version']=True\n"
        "if mode=='float-version':out['schema_version']=1.0\n"
        "if mode=='wrong-op':out['operation']='read_events'\n"
        "if mode=='extra':out['private_body']='SECRET UNTRUSTED BODY'\n"
        "if mode=='unknown-error':out.update(status='error',error='SECRET UNTRUSTED BODY')\n"
        "if mode in ('unavailable','read-error'):\n"
        " out.update(status='error',error='platform_unavailable' if mode=='unavailable' "
        "else 'calendar_read_failed')\n"
        " out.pop('events',None);out.pop('partial',None);out.pop('calendars',None)\n"
        "if mode=='bad-json':print('SECRET UNTRUSTED BODY');raise SystemExit(0)\n"
        "if mode=='huge':os.write(1,b'x'*524289);raise SystemExit(0)\n"
        "if mode=='stderr':os.write(2,b'SECRET UNTRUSTED BODY')\n"
        "if mode=='late-status' and op=='status':time.sleep(30)\n"
        "data=json.dumps(out).encode()\n"
        "if mode=='fragment':\n"
        " for i in range(0,len(data),7):os.write(1,data[i:i+7]);time.sleep(.001)\n"
        "else:sys.stdout.buffer.write(data)\n"
    )
    return path


@pytest.fixture
def controllers() -> Iterator[list[CalendarController]]:
    values: list[CalendarController] = []
    yield values
    for controller in values:
        controller.close()
        QApplication.processEvents()
        for job in tuple(controller._retired):
            if not job.disposed:
                job.process.waitForFinished(1000)
        controller.deleteLater()
    QApplication.processEvents()


def controller_for(
    helper: Path,
    controllers: list[CalendarController],
    *,
    mode: str = "normal",
    clock: Clock | None = None,
) -> CalendarController:
    result = CalendarController(
        helper.parent / "private-profile",
        helper_command=lambda: (sys.executable, str(helper), mode),
        clock=clock or Clock(),
    )
    controllers.append(result)
    return result


def connect(app: QApplication, controller: CalendarController) -> None:
    controller.connect_calendar()
    wait_until(app, lambda: not controller.busy)
    assert controller.catalog


def read(app: QApplication, controller: CalendarController) -> None:
    assert controller.use_calendars((controller.catalog[0].source,))
    controller.read_day(date(2026, 10, 20), "UTC")
    wait_until(app, lambda: not controller.busy)


def dialogue_query(controller: CalendarController, *, agenda: bool = False) -> CalendarReadProposal:
    chat_id = controller.dialogue_chat_id
    assert chat_id is not None
    return CalendarReadProposal(
        proposal_id=uuid4(),
        principal=controller.principal,
        chat_id=chat_id,
        turn_id=uuid4(),
        kind="agenda" if agenda else "unoccupied_intervals",
        day=date(2026, 10, 20),
        timezone="UTC",
        window=SourceInterval(starts_at=NOW, ends_at=NOW + timedelta(hours=6)),
    )


def prepare_dialogue(app: QApplication, controller: CalendarController) -> None:
    connect(app, controller)
    assert controller.use_calendars((SOURCE,))
    controller.bind_dialogue(uuid4())


def test_constructor_and_timer_do_not_resolve_or_start_any_native_command(
    app: QApplication, tmp_path: Path, controllers: list[CalendarController]
) -> None:
    calls: list[bool] = []

    def command() -> None:
        calls.append(True)

    controller = CalendarController(tmp_path, helper_command=command)
    controllers.append(controller)
    controller.tick()
    app.processEvents()
    assert calls == []
    assert controller.snapshot is None
    assert controller.grant is None
    assert controller.catalog == ()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", ["normal", "access", "fragment", "stderr"])
def test_explicit_connect_catalog_then_apply_and_read_owned_helper(
    app: QApplication, helper: Path, controllers: list[CalendarController], mode: str
) -> None:
    controller = controller_for(helper, controllers, mode=mode)
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    connect(app, controller)
    assert controller.grant is None
    assert controller.snapshot is None
    assert controller.catalog[0].title == "PUBLIC CALENDAR"
    read(app, controller)
    snapshot = controller.snapshot
    assert snapshot is not None
    assert snapshot.request.resources == (SOURCE,)
    assert snapshot.request.max_events == 100
    assert snapshot.request.max_bytes == 524288
    assert snapshot.occurrences[0].title == "PUBLIC EVENT <b>literal</b>"
    assert snapshot.occurrences[0].status == "tentative"
    assert snapshot.request.ends_at - snapshot.request.starts_at == timedelta(days=1)
    assert all("SECRET" not in status for status in statuses)
    assert list(helper.parent.iterdir()) == [helper]


@pytest.mark.parametrize("mode", ["partial", "empty", "all-day"])
def test_partial_empty_and_all_day_remain_distinct(
    app: QApplication, helper: Path, controllers: list[CalendarController], mode: str
) -> None:
    controller = controller_for(helper, controllers, mode=mode)
    connect(app, controller)
    read(app, controller)
    snapshot = controller.snapshot
    assert snapshot is not None
    assert controller.status == {"partial": "partial", "empty": "empty", "all-day": "ready"}[mode]
    if mode == "partial":
        assert snapshot.status == "partial"
        assert snapshot.reason == "limit_reached"
    elif mode == "empty":
        assert snapshot.occurrences == ()
    else:
        assert snapshot.occurrences[0].all_day_end == date(2026, 10, 21)
        assert snapshot.occurrences[0].starts_at is None


@pytest.mark.parametrize("mode", ["wrong-source", "duplicate", "outside", "many-events", "future"])
def test_read_payload_scope_duplicates_bounds_and_future_time_fail_closed(
    app: QApplication, helper: Path, controllers: list[CalendarController], mode: str
) -> None:
    controller = controller_for(helper, controllers, mode=mode)
    connect(app, controller)
    read(app, controller)
    assert controller.snapshot is None
    assert controller.grant is None
    assert controller.status in {"source_mismatch", "result_invalid", "invalid_response"}


@pytest.mark.parametrize(
    "mode",
    [
        "wrong-id",
        "wrong-op",
        "bool-version",
        "float-version",
        "extra",
        "unknown-error",
        "bad-json",
        "huge",
    ],
)
def test_uncorrelated_or_malformed_output_never_creates_grant_or_shows_body(
    app: QApplication, helper: Path, controllers: list[CalendarController], mode: str
) -> None:
    controller = controller_for(helper, controllers, mode=mode)
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.connect_calendar()
    wait_until(app, lambda: not controller.busy)
    assert controller.snapshot is None
    assert controller.grant is None
    assert controller.catalog == ()
    assert controller.status in {"invalid_response", "result_too_large"}
    assert all("SECRET" not in status for status in statuses)


def test_permission_denial_and_missing_helper_are_explicit_without_payload(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    controller = controller_for(helper, controllers, mode="denied")
    controller.connect_calendar()
    wait_until(app, lambda: not controller.busy)
    assert controller.status == "permission_denied"
    assert controller.catalog == ()
    missing = CalendarController(helper.parent, helper_command=lambda: None)
    controllers.append(missing)
    missing.connect_calendar()
    assert missing.status == "provider_unavailable"
    assert not missing.busy


def test_same_calendar_id_in_two_accounts_does_not_expand_selection(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    controller = controller_for(helper, controllers, mode="two-accounts")
    connect(app, controller)
    assert len(controller.catalog) == 2
    read(app, controller)
    assert controller.snapshot is not None
    assert controller.snapshot.request.resources == (SOURCE,)
    assert all(event.source == SOURCE for event in controller.snapshot.occurrences)


def test_selection_edit_stops_old_read_and_requires_apply_again(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    controller = controller_for(helper, controllers)
    connect(app, controller)
    read(app, controller)
    controller.selection_changed()
    assert controller.snapshot is None
    assert controller.grant is None
    assert controller.catalog
    controller.read_day(date(2026, 10, 20), "UTC")
    assert not controller.busy
    assert controller.status == "grant_required"


def test_snapshot_and_grant_expire_without_automatic_fetch(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    clock = Clock()
    controller = controller_for(helper, controllers, clock=clock)
    connect(app, controller)
    read(app, controller)
    clock.value += timedelta(minutes=5)
    controller.tick()
    assert controller.snapshot is None
    assert controller.status == "snapshot_expired"
    assert controller.grant is not None
    assert not controller.busy
    clock.value += timedelta(hours=1)
    controller.tick()
    assert controller.grant is None
    assert controller.catalog == ()
    assert controller.status == "grant_expired"


def test_invalid_zone_clears_old_view_before_any_new_read(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    controller = controller_for(helper, controllers)
    connect(app, controller)
    read(app, controller)
    assert controller.snapshot is not None
    controller.read_day(date(2026, 10, 21), "not-an-IANA-zone")
    assert controller.snapshot is None
    assert not controller.busy


def test_profile_disconnect_and_close_kill_only_the_owned_helper(
    app: QApplication, helper: Path, controllers: list[CalendarController]
) -> None:
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        controller = controller_for(helper, controllers, mode="late-read")
        connect(app, controller)
        assert controller.use_calendars((SOURCE,))
        principal = controller.principal
        controller.read_day(date(2026, 10, 20), "UTC")
        job = controller._job
        assert job is not None
        wait_until(app, lambda: job.process.state() == QProcess.ProcessState.Running)
        owned_pid = job.process.processId()
        assert owned_pid > 0
        assert owned_pid != unrelated.pid
        finished: list[tuple[int, QProcess.ExitStatus]] = []
        job.process.finished.connect(lambda code, state: finished.append((code, state)))
        controller.set_profile(helper.parent / "other-profile")
        assert controller.principal != principal
        assert controller.principal.profile_id != principal.profile_id
        assert controller.snapshot is None
        assert controller.grant is None
        assert controller.catalog == ()
        # finished/deleteLater may delete the Qt wrapper before this polling turn.
        wait_until(app, lambda: bool(finished) and job.disposed)
        assert finished[0][1] == QProcess.ExitStatus.CrashExit
        assert job not in controller._retired
        controller._started(job)
        controller._read(job)
        controller._process_error(job, QProcess.ProcessError.Crashed)
        controller._finished(job, -9, QProcess.ExitStatus.CrashExit)
        assert controller.snapshot is None
        assert unrelated.poll() is None
        controller.close()
        controller.connect_calendar()
        assert not controller.busy
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


class DelayedProcess(QObject):
    started = Signal()
    readyReadStandardOutput = Signal()
    readyReadStandardError = Signal()
    finished = Signal(int, object)
    errorOccurred = Signal(object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.writes: list[bytes] = []
        self.killed = False
        self.started_command = False
        self.stopped = False
        self.active_state = QProcess.ProcessState.Running

    def setProcessChannelMode(self, value: object) -> None:
        del value

    def setProgram(self, value: str) -> None:
        del value

    def setArguments(self, value: list[str]) -> None:
        del value

    def start(self) -> None:
        self.started_command = True

    def state(self) -> QProcess.ProcessState:
        return (
            self.active_state
            if self.started_command and not self.stopped
            else QProcess.ProcessState.NotRunning
        )

    def write(self, value: bytes) -> int:
        self.writes.append(value)
        return len(value)

    def closeWriteChannel(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True

    def finish(self) -> None:
        if not self.stopped:
            self.stopped = True
            self.finished.emit(-9, QProcess.ExitStatus.CrashExit)

    def waitForFinished(self, timeout: int) -> bool:
        del timeout
        self.finish()
        return True


@pytest.mark.parametrize(
    "process_state", [QProcess.ProcessState.Running, QProcess.ProcessState.Starting]
)
@pytest.mark.parametrize(
    "error",
    [
        QProcess.ProcessError.Crashed,
        QProcess.ProcessError.ReadError,
        QProcess.ProcessError.WriteError,
    ],
)
def test_retired_helper_waits_for_finished_and_late_errors_preserve_new_owned_job(
    app: QApplication,
    tmp_path: Path,
    controllers: list[CalendarController],
    process_state: QProcess.ProcessState,
    error: QProcess.ProcessError,
) -> None:
    del app
    processes: list[DelayedProcess] = []

    def factory(parent: QObject) -> Any:
        process = DelayedProcess(parent)
        processes.append(process)
        return process

    controller = CalendarController(
        tmp_path, helper_command=lambda: (sys.executable,), process_factory=factory, clock=Clock()
    )
    controllers.append(controller)
    controller.connect_calendar()
    old = controller._job
    assert old is not None
    processes[0].active_state = process_state
    controller.disconnect_calendar()
    assert processes[0].killed
    assert old in controller._retired
    processes[0].errorOccurred.emit(error)
    assert not old.disposed
    assert old in controller._retired
    controller._dispose(old)  # The last-resort guard must also reject active disposal.
    assert not old.disposed
    controller.connect_calendar()
    newer = controller._job
    assert newer is not None
    assert newer is not old
    processes[0].errorOccurred.emit(error)
    assert controller._job is newer
    assert not processes[1].killed
    processes[0].finish()
    assert old.disposed
    assert old not in controller._retired
    controller._process_error(old, error)
    controller._finished(old, -9, QProcess.ExitStatus.CrashExit)
    assert controller._job is newer
    assert not newer.disposed
    assert not processes[1].killed


@pytest.mark.parametrize("retired", [False, True])
def test_failed_to_start_can_dispose_without_finished_signal(
    app: QApplication,
    tmp_path: Path,
    controllers: list[CalendarController],
    retired: bool,
) -> None:
    del app
    processes: list[DelayedProcess] = []

    def factory(parent: QObject) -> Any:
        process = DelayedProcess(parent)
        processes.append(process)
        return process

    controller = CalendarController(
        tmp_path, helper_command=lambda: (sys.executable,), process_factory=factory, clock=Clock()
    )
    controllers.append(controller)
    controller.connect_calendar()
    job = controller._job
    assert job is not None
    finished: list[bool] = []
    processes[0].finished.connect(lambda *_: finished.append(True))
    if retired:
        controller.disconnect_calendar()
    processes[0].stopped = True
    processes[0].errorOccurred.emit(QProcess.ProcessError.FailedToStart)
    assert job.disposed
    assert job not in controller._retired
    assert not finished
    assert controller._job is None


@pytest.mark.parametrize("advance", [timedelta(seconds=30), timedelta(hours=1)])
def test_delayed_process_start_rechecks_grant_and_request_before_stdin_dispatch(
    app: QApplication, tmp_path: Path, controllers: list[CalendarController], advance: timedelta
) -> None:
    del app
    clock = Clock()
    processes: list[DelayedProcess] = []

    def factory(parent: QObject) -> Any:
        process = DelayedProcess(parent)
        processes.append(process)
        return process

    controller = CalendarController(
        tmp_path, helper_command=lambda: (sys.executable,), process_factory=factory, clock=clock
    )
    controllers.append(controller)
    controller._coordinator.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=clock(),
            calendars=(CalendarDescriptor(source=SOURCE, title="PUBLIC", account_title="PUBLIC"),),
        )
    )
    assert controller.use_calendars((SOURCE,))
    controller.read_day(date(2026, 10, 20), "UTC")
    process = processes[0]
    clock.value += advance
    process.started.emit()
    assert process.writes == []
    assert process.killed
    assert controller.snapshot is None
    assert controller.grant is None
    # Model terminal state/finished before allowing the production disposal guard.
    process.finish()


def test_all_day_identity_uses_original_local_occurrence_date_not_transport_zone() -> None:
    raw: dict[str, Any] = {
        "source_id": "a",
        "calendar_id": "c",
        "event_id": "e",
        "title": "PUBLIC",
        "occurrence_start": "2026-10-19T21:00:00Z",
        "start": "2026-10-20T00:00:00Z",
        "end": "2026-10-21T00:00:00Z",
        "all_day": True,
        "start_date": "2026-10-20",
        "end_date": "2026-10-21",
        "time_zone": "Europe/Moscow",
        "status": "confirmed",
    }
    first = CalendarController._occurrence(_Event.model_validate_json(json.dumps(raw)))
    raw["occurrence_start"] = "2026-10-20T00:00:00Z"
    second = CalendarController._occurrence(_Event.model_validate_json(json.dumps(raw)))
    assert first.occurrence_id == second.occurrence_id


def test_dialogue_dispatch_uses_exact_window_and_one_result_admission(
    app: QApplication,
    helper: Path,
    controllers: list[CalendarController],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = controller_for(helper, controllers)
    prepare_dialogue(app, controller)
    original = controller._coordinator.accept_result
    calls: list[object] = []

    def accepted(request: Any, result: Any) -> Any:
        calls.append(request)
        return original(request, result)

    monkeypatch.setattr(controller._coordinator, "accept_result", accepted)
    proposal = dialogue_query(controller)
    controller.submit_dialogue(proposal)
    job = controller._job
    assert job is not None
    assert job.dialogue is not None
    assert job.request is not None
    assert job.request.starts_at == proposal.window.starts_at
    assert job.request.ends_at == proposal.window.ends_at
    wait_until(app, lambda: not controller.busy)
    answer = controller.dialogue_answer
    assert answer is not None
    assert answer.receipt.proposal_id == proposal.proposal_id
    assert answer.receipt.request_id == job.request_id
    assert answer.receipt.read_window == proposal.window
    assert len(calls) == 1
    assert answer.unoccupied
    assert controller.snapshot is None  # No mismatched day projection in My day.
    controller._finished(job, 0, QProcess.ExitStatus.NormalExit)
    assert len(calls) == 1
    assert controller.dialogue_answer == answer


@pytest.mark.parametrize("invalid_query", [False, True])
def test_my_day_read_start_clears_card_before_result_even_if_new_query_is_invalid(
    app: QApplication,
    helper: Path,
    controllers: list[CalendarController],
    invalid_query: bool,
) -> None:
    controller = controller_for(helper, controllers)
    prepare_dialogue(app, controller)
    controller.submit_dialogue(dialogue_query(controller))
    wait_until(app, lambda: not controller.busy)
    assert controller.dialogue_answer is not None
    controller._helper_command = lambda: (sys.executable, str(helper), "late-read")
    controller.read_day(date(2026, 10, 20), "Missing/Zone" if invalid_query else "UTC")
    assert controller.dialogue_answer is None
    assert controller.dialogue_status == "superseded"
    assert controller.busy is not invalid_query


@pytest.mark.parametrize("action", ["cancel", "selection", "disconnect", "profile", "chat"])
def test_dialogue_lifecycle_fences_late_helper_and_preserves_scoped_ownership(
    app: QApplication,
    helper: Path,
    controllers: list[CalendarController],
    action: str,
) -> None:
    controller = controller_for(helper, controllers)
    prepare_dialogue(app, controller)
    grant = controller.grant
    controller._helper_command = lambda: (sys.executable, str(helper), "late-read")
    controller.submit_dialogue(dialogue_query(controller))
    old = controller._job
    assert old is not None
    if action == "cancel":
        controller.clear_dialogue()
    elif action == "selection":
        controller.selection_changed()
    elif action == "disconnect":
        controller.disconnect_calendar()
    elif action == "profile":
        controller.set_profile(helper.parent / "other")
    else:
        controller.bind_dialogue(uuid4())
    controller._started(old)
    controller._read(old)
    controller._finished(old, 0, QProcess.ExitStatus.NormalExit)
    assert controller.dialogue_answer is None
    assert not controller.busy
    assert (controller.grant == grant) if action in {"cancel", "chat"} else controller.grant is None


def test_cancel_dialogue_does_not_cancel_my_day_owned_read(
    app: QApplication,
    helper: Path,
    controllers: list[CalendarController],
) -> None:
    controller = controller_for(helper, controllers)
    prepare_dialogue(app, controller)
    controller._helper_command = lambda: (sys.executable, str(helper), "late-read")
    controller.read_day(date(2026, 10, 20), "UTC")
    job = controller._job
    controller.clear_dialogue()
    assert controller._job is job
    assert controller.busy


def test_old_dialogue_callback_does_not_discard_new_read(
    app: QApplication,
    helper: Path,
    controllers: list[CalendarController],
) -> None:
    controller = controller_for(helper, controllers)
    prepare_dialogue(app, controller)
    controller._helper_command = lambda: (sys.executable, str(helper), "late-read")
    controller.submit_dialogue(dialogue_query(controller))
    old = controller._job
    assert old is not None
    controller._helper_command = lambda: (sys.executable, str(helper), "normal")
    newer = dialogue_query(controller)
    controller.submit_dialogue(newer)
    current = controller._job
    assert current is not None
    assert current is not old
    controller._finished(old, 0, QProcess.ExitStatus.NormalExit)
    assert controller._job is current
    wait_until(app, lambda: not controller.busy)
    assert controller.dialogue_answer is not None
    assert controller.dialogue_answer.receipt.proposal_id == newer.proposal_id


@pytest.mark.parametrize(
    "mode", ["partial", "denied", "bad-json", "empty", "unavailable", "read-error"]
)
def test_dialogue_partial_denied_error_and_empty_stay_distinct(
    app: QApplication,
    helper: Path,
    controllers: list[CalendarController],
    mode: str,
) -> None:
    controller = controller_for(helper, controllers)
    prepare_dialogue(app, controller)
    controller._helper_command = lambda: (sys.executable, str(helper), mode)
    controller.submit_dialogue(dialogue_query(controller))
    job = controller._job
    assert job is not None
    wait_until(app, lambda: not controller.busy)
    answer = controller.dialogue_answer
    if mode == "partial":
        assert answer is not None
        assert answer.receipt.status == "partial"
        assert not answer.unoccupied
    elif mode == "empty":
        assert answer is not None
        assert answer.receipt.status == "empty"
        assert answer.unoccupied
    elif mode in {"unavailable", "read-error"}:
        assert answer is not None
        assert not answer.agenda
        assert not answer.unoccupied
        assert answer.receipt.status == ("unavailable" if mode == "unavailable" else "error")
        assert answer.receipt.request_id == job.request_id
    else:
        assert answer is None
        assert controller.dialogue_status == (
            "permission_denied" if mode == "denied" else "invalid_response"
        )
        if mode == "denied":
            receipts = [
                item
                for item in controller.dialogue_audit_events
                if item.event == "receipt_accepted"
            ]
            assert len(receipts) == 1
            assert receipts[0].outcome == "denied"
            assert receipts[0].request_id == job.request_id


def test_dialogue_expiry_clears_body_and_requires_new_explicit_action(
    app: QApplication,
    helper: Path,
    controllers: list[CalendarController],
) -> None:
    clock = Clock()
    controller = controller_for(helper, controllers, clock=clock)
    prepare_dialogue(app, controller)
    controller.submit_dialogue(dialogue_query(controller, agenda=True))
    wait_until(app, lambda: not controller.busy)
    assert controller.dialogue_answer is not None
    clock.value += timedelta(minutes=5)
    controller.tick()
    assert controller.dialogue_answer is None
    assert controller.dialogue_status == "stale"
    assert controller.grant is not None


def test_grant_expiry_while_dialogue_reading_cancels_and_reports_closed_state(
    app: QApplication,
    helper: Path,
    controllers: list[CalendarController],
) -> None:
    clock = Clock()
    controller = controller_for(helper, controllers, clock=clock)
    prepare_dialogue(app, controller)
    controller._helper_command = lambda: (sys.executable, str(helper), "late-read")
    controller.submit_dialogue(dialogue_query(controller))
    assert controller.dialogue_status == "reading"
    clock.value += timedelta(hours=1)
    controller.tick()
    assert not controller.busy
    assert controller.dialogue_answer is None
    assert controller.dialogue_status == "grant_expired"
