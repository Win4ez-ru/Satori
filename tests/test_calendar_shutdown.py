"""Owned helper shutdown uses terminal receipts, never an EventKit or account operation."""

import os
import sys
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from datetime import time as CivilTime
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtWidgets import QApplication

from satori.application.assistant.schedule_contracts import ReviewedSchedule, ScheduleRow
from satori.desktop.calendar_controller import CalendarController
from satori.desktop.ui import DesktopApplication

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


class DelayedProcess(QObject):
    """A controllable child transport; kill requests do not manufacture finished."""

    started = Signal()
    readyReadStandardOutput = Signal()
    readyReadStandardError = Signal()
    finished = Signal(int, object)
    errorOccurred = Signal(object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.launched = False
        self.active_state = QProcess.ProcessState.Running
        self.stopped = False
        self.kills = 0

    def setProcessChannelMode(self, value: object) -> None:
        del value

    def setProgram(self, value: str) -> None:
        del value

    def setArguments(self, value: list[str]) -> None:
        del value

    def start(self) -> None:
        self.launched = True

    def state(self) -> QProcess.ProcessState:
        return (
            self.active_state
            if self.launched and not self.stopped
            else QProcess.ProcessState.NotRunning
        )

    def kill(self) -> None:
        self.kills += 1

    def finish(self) -> None:
        self.stopped = True
        self.finished.emit(-9, QProcess.ExitStatus.CrashExit)


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or DesktopApplication([])
    assert isinstance(application, QApplication)
    return application


@pytest.fixture
def controlled(
    app: QApplication, tmp_path: Path
) -> Iterator[tuple[CalendarController, list[DelayedProcess]]]:
    del app
    processes: list[DelayedProcess] = []

    def factory(parent: QObject) -> Any:
        process = DelayedProcess(parent)
        processes.append(process)
        return process

    owner = CalendarController(
        tmp_path,
        helper_command=lambda: (sys.executable,),
        process_factory=factory,
        clock=lambda: NOW,
    )
    yield owner, processes
    owner.close()
    for process in processes:
        process.finish()
    owner.deleteLater()
    QApplication.processEvents()


def test_idle_close_completes_once_without_startup_or_shutdown_source_io(
    app: QApplication, tmp_path: Path
) -> None:
    del app
    calls: list[bool] = []
    owner = CalendarController(tmp_path, helper_command=lambda: calls.append(True))
    observed: list[tuple[bool, bool, bool]] = []
    owner.shutdown_finished.connect(
        lambda: observed.append(
            (owner.shutdown_complete, owner.poll.isActive(), owner.grant is not None)
        )
    )
    assert not owner.shutdown_complete
    owner.close()
    owner.close()
    assert owner.shutdown_complete
    assert observed == [(True, False, False)]
    assert calls == []
    assert list(tmp_path.iterdir()) == []
    owner.deleteLater()


@pytest.mark.parametrize(
    "active_state", [QProcess.ProcessState.Running, QProcess.ProcessState.Starting]
)
def test_shutdown_waits_nonblocking_for_actual_finished_and_ignores_error_as_completion(
    controlled: tuple[CalendarController, list[DelayedProcess]],
    active_state: QProcess.ProcessState,
) -> None:
    owner, processes = controlled
    observed: list[bool] = []
    owner.shutdown_finished.connect(lambda: observed.append(owner.shutdown_complete))
    owner.connect_calendar()
    job = owner._job
    assert job is not None
    processes[0].active_state = active_state
    owner.close()
    assert processes[0].kills
    assert not owner.shutdown_complete
    assert not observed
    assert job in owner._retired
    assert not job.disposed
    for error in (
        QProcess.ProcessError.Crashed,
        QProcess.ProcessError.ReadError,
        QProcess.ProcessError.WriteError,
    ):
        processes[0].errorOccurred.emit(error)
        assert not owner.shutdown_complete
        assert not job.disposed
    owner.close()
    processes[0].finish()
    assert job.disposed
    assert owner.shutdown_complete
    assert observed == [True]
    processes[0].finish()
    assert observed == [True]


def test_not_running_state_before_finished_does_not_release_launched_child(
    controlled: tuple[CalendarController, list[DelayedProcess]],
) -> None:
    owner, processes = controlled
    owner.connect_calendar()
    job = owner._job
    assert job is not None
    processes[0].stopped = True
    processes[0].errorOccurred.emit(QProcess.ProcessError.Crashed)
    owner.close()
    assert not owner.shutdown_complete
    assert job in owner._retired
    assert not job.disposed
    processes[0].finish()
    assert owner.shutdown_complete


def test_all_retired_helpers_must_finish_before_the_single_shutdown_receipt(
    controlled: tuple[CalendarController, list[DelayedProcess]],
) -> None:
    owner, processes = controlled
    observed: list[bool] = []
    owner.shutdown_finished.connect(lambda: observed.append(True))
    owner.connect_calendar()
    owner.disconnect_calendar()
    owner.connect_calendar()
    assert len(processes) == 2
    owner.close()
    assert len(owner._retired) == 2
    processes[1].finish()
    assert not owner.shutdown_complete
    assert not observed
    processes[0].finish()
    assert owner.shutdown_complete
    assert observed == [True]


def test_failed_to_start_is_terminal_without_a_finished_signal(
    controlled: tuple[CalendarController, list[DelayedProcess]],
) -> None:
    owner, processes = controlled
    observed: list[bool] = []
    owner.shutdown_finished.connect(lambda: observed.append(True))
    owner.connect_calendar()
    owner.close()
    assert not owner.shutdown_complete
    processes[0].stopped = True
    processes[0].errorOccurred.emit(QProcess.ProcessError.FailedToStart)
    assert owner.shutdown_complete
    assert observed == [True]


def test_reentrant_close_before_start_needs_no_nonexistent_finished_signal(
    controlled: tuple[CalendarController, list[DelayedProcess]],
) -> None:
    owner, processes = controlled
    observed: list[bool] = []
    owner.shutdown_finished.connect(lambda: observed.append(True))
    owner.changed.connect(lambda: owner.close() if owner.busy else None)
    owner.connect_calendar()
    assert len(processes) == 1
    assert not processes[0].launched
    assert owner.shutdown_complete
    assert observed == [True]


def test_python_launch_failure_without_a_child_does_not_wait_for_finished(
    controlled: tuple[CalendarController, list[DelayedProcess]], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, processes = controlled

    def failed_start(self: DelayedProcess) -> None:
        del self
        raise OSError("synthetic launch failure")

    monkeypatch.setattr(DelayedProcess, "start", failed_start)
    owner.connect_calendar()
    assert len(processes) == 1
    assert not processes[0].launched
    assert not owner.busy
    owner.close()
    assert owner.shutdown_complete


def _wait(app: QApplication, condition: Any) -> None:
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition()


def test_real_owned_synthetic_pipe_stops_before_completion_and_keeps_create_unknown(
    app: QApplication, tmp_path: Path
) -> None:
    helper = tmp_path / "public-calendar-shutdown-helper.py"
    helper.write_text(
        "import json,sys,time\n"
        "r=json.loads(sys.stdin.buffer.read());op=r['operation']\n"
        "o={'schema_version':1,'request_id':r['request_id'],'operation':op,'status':'ok',"
        "'access':'full_access','error':None,'observed_at':'2026-10-29T12:00:00Z'}\n"
        "if op=='list_calendars':o['calendars']=[{'source_id':'PUBLIC SOURCE',"
        "'calendar_id':'PUBLIC TARGET','source_title':'PUBLIC ACCOUNT','title':'PUBLIC CALENDAR',"
        "'writable':True}]\n"
        "if op=='preflight_schedule':o.update(plan_fingerprint=r['plan_fingerprint'],"
        "schedule_results=[{'row_id':x['row_id'],'owned_token':x['owned_token'],"
        "'outcome':'absent'} for x in r['rows']])\n"
        "if op=='create_schedule':time.sleep(30)\n"
        "print(json.dumps(o))\n"
    )
    owner = CalendarController(
        tmp_path / "profile",
        helper_command=lambda: (sys.executable, str(helper)),
        clock=lambda: NOW,
    )
    observed: list[bool] = []
    owner.shutdown_finished.connect(lambda: observed.append(owner.shutdown_complete))
    owner.connect_calendar()
    _wait(app, lambda: not owner.busy)
    target = owner.catalog[0].source
    reviewed = ReviewedSchedule(
        schema="satori-schedule",
        version=1,
        year=2026,
        month=10,
        timezone="UTC",
        legend="PUBLIC explicit times",
        reviewed=True,
        rows=(
            ScheduleRow(
                date=date(2026, 10, 29),
                start=CivilTime(9),
                end=CivilTime(18),
                end_date=date(2026, 10, 29),
                title="PUBLIC SHIFT",
            ),
        ),
    )
    try:
        assert owner.schedule.prepare(reviewed, target)
        assert owner.schedule.preflight()
        _wait(app, lambda: not owner.busy)
        assert owner.schedule.create()
        job = owner._job
        assert job is not None
        finished_states: list[QProcess.ProcessState] = []
        job.process.finished.connect(lambda *_: finished_states.append(job.process.state()))
        _wait(app, lambda: job.process.state() == QProcess.ProcessState.Running)
        owner.close()
        assert owner.schedule.state == "unknown"
        assert not owner.schedule.can_create
        assert not owner.shutdown_complete
        assert not job.disposed
        _wait(app, lambda: owner.shutdown_complete)
        assert job.disposed
        assert finished_states == [QProcess.ProcessState.NotRunning]
        assert owner.schedule.state == "unknown"
        assert observed == [True]
    finally:
        owner.close()
        _wait(app, lambda: owner.shutdown_complete)
        owner.deleteLater()
