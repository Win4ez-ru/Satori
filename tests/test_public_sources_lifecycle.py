"""Owned public-source process races with synthetic receipts; no source/account I/O."""

import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QObject, QProcess, Signal
from PySide6.QtWidgets import QApplication

from satori.desktop.public_sources_controller import PublicSourcesController
from satori.desktop.ui import DesktopApplication
from satori.infrastructure.public_sources.wire import PublicSourceReply

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
PUBLIC_URL = "https://public.example/explicit-source"
BODY = "SYNTHETIC UNTRUSTED SOURCE BODY"


class Clock:
    def __init__(self) -> None:
        self.wall = NOW
        self.elapsed = 10.0

    def utc(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.elapsed

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.elapsed += seconds


class ControlledProcess(QObject):
    """A kill request never manufactures a process-finish receipt."""

    started = Signal()
    readyReadStandardOutput = Signal()
    readyReadStandardError = Signal()
    finished = Signal(int, object)
    errorOccurred = Signal(object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.active = QProcess.ProcessState.NotRunning
        self.payload = bytearray()
        self.output = bytearray()
        self.stderr = bytearray()
        self.program = ""
        self.arguments: list[str] = []
        self.kills = 0
        self.input_closed = False
        self.starts = 0

    def setProcessChannelMode(self, mode: object) -> None:
        del mode

    def setProgram(self, program: str) -> None:
        self.program = program

    def setArguments(self, arguments: list[str]) -> None:
        self.arguments = arguments

    def start(self) -> None:
        self.starts += 1
        self.active = QProcess.ProcessState.Starting

    def state(self) -> QProcess.ProcessState:
        return self.active

    def activate(self) -> None:
        self.active = QProcess.ProcessState.Running
        self.started.emit()

    def write(self, payload: bytes) -> int:
        self.payload.extend(payload)
        return len(payload)

    def closeWriteChannel(self) -> None:
        self.input_closed = True

    def readAllStandardOutput(self) -> QByteArray:
        result = QByteArray(bytes(self.output))
        self.output.clear()
        return result

    def readAllStandardError(self) -> QByteArray:
        result = QByteArray(bytes(self.stderr))
        self.stderr.clear()
        return result

    def push(self, payload: bytes) -> None:
        self.output.extend(payload)
        self.readyReadStandardOutput.emit()

    def finish(self, code: int = 0, *, crashed: bool = False) -> None:
        self.active = QProcess.ProcessState.NotRunning
        self.finished.emit(
            code, QProcess.ExitStatus.CrashExit if crashed else QProcess.ExitStatus.NormalExit
        )

    def kill(self) -> None:
        self.kills += 1

    def request(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.payload))


@pytest.fixture(scope="module")
def application() -> QApplication:
    application = QApplication.instance() or DesktopApplication([])
    assert isinstance(application, QApplication)
    return application


@pytest.fixture
def controlled(
    application: QApplication, tmp_path: Path
) -> Iterator[tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]]]:
    del application
    processes: list[ControlledProcess] = []
    helpers: list[bool] = []
    clock = Clock()

    def factory(parent: QObject) -> QProcess:
        process = ControlledProcess(parent)
        processes.append(process)
        return cast(QProcess, process)

    def helper() -> tuple[str, ...]:
        helpers.append(True)
        return (sys.executable, "-c", "raise SystemExit(0)")

    owner = PublicSourcesController(
        tmp_path,
        helper_command=helper,
        process_factory=factory,
        clock=clock.utc,
        monotonic=clock.monotonic,
    )
    owner.bind_session(uuid4())
    yield owner, processes, clock, helpers
    owner.shutdown()
    for process in processes:
        with suppress(RuntimeError):
            process.finish(-9, crashed=True)
    owner.deleteLater()
    QApplication.processEvents()


def dispatch(
    owner: PublicSourcesController, processes: list[ControlledProcess]
) -> ControlledProcess:
    owner.set_visible(True)
    before = len(processes)
    owner.read_url(PUBLIC_URL)
    assert len(processes) == before + 1
    process = processes[-1]
    process.activate()
    assert process.input_closed
    assert process.request()["url"] == PUBLIC_URL
    return process


def reply(process: ControlledProcess, clock: Clock, **changes: object) -> bytes:
    request = process.request()
    return (
        json.dumps(
            {
                "schema_version": 1,
                **{
                    key: request[key]
                    for key in ("request_id", "profile_id", "session_id", "generation", "operation")
                },
                "origin": "public_https",
                "coverage": "selected_url_only",
                "untrusted": True,
                "status": "ok",
                "reason": None,
                "source_label": "SYNTHETIC PUBLIC HTTPS",
                "source_url": request["url"],
                "media_type": "text/plain",
                "text": BODY,
                "items": [],
                "raw_bytes": len(BODY),
                "observed_at": clock.utc().isoformat(),
                "expires_at": (clock.utc() + timedelta(minutes=5)).isoformat(),
                **changes,
            }
        ).encode()
        + b"\n"
    )


def test_constructor_projection_and_idle_shutdown_never_resolve_helper(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
) -> None:
    owner, processes, _, helpers = controlled
    assert owner.result is None
    assert not owner.busy
    assert helpers == []
    assert processes == []
    completed: list[bool] = []
    owner.shutdown_finished.connect(lambda: completed.append(owner.shutdown_complete))
    owner.shutdown()
    owner.shutdown()
    assert owner.shutdown_complete
    assert completed == [True]
    assert helpers == []


def test_result_is_admitted_only_after_successful_process_finish(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
) -> None:
    owner, processes, clock, _ = controlled
    process = dispatch(owner, processes)
    process.push(reply(process, clock))
    assert owner.busy
    assert owner.result is None
    assert PUBLIC_URL not in process.arguments
    assert BODY not in process.arguments
    process.finish()
    assert not owner.busy
    observed = owner.result
    assert observed is not None
    assert observed.text == BODY
    assert str(observed.request_id) == process.request()["request_id"]


@pytest.mark.parametrize(
    "field", ["request_id", "profile_id", "session_id", "generation", "source_url"]
)
def test_wrong_correlation_or_selected_url_is_rejected(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
    field: str,
) -> None:
    owner, processes, clock, _ = controlled
    process = dispatch(owner, processes)
    wrong: object = (
        "https://other.example/unselected"
        if field == "source_url"
        else (process.request()[field] + 1 if field == "generation" else str(uuid4()))
    )
    process.push(reply(process, clock, **{field: wrong}))
    process.finish()
    assert owner.result is None
    assert not owner.busy


@pytest.mark.parametrize("action", ["cancel", "conceal", "profile", "session", "shutdown"])
def test_context_end_fences_output_and_finish_from_the_old_job(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
    tmp_path: Path,
    action: str,
) -> None:
    owner, processes, clock, _ = controlled
    process = dispatch(owner, processes)
    stale = reply(process, clock)
    if action == "cancel":
        owner.cancel()
    elif action == "conceal":
        owner.conceal()
    elif action == "profile":
        owner.set_profile(tmp_path / "different-profile")
    elif action == "session":
        owner.bind_session(uuid4())
    else:
        owner.shutdown()
    assert process.kills
    process.push(stale)
    process.finish()
    assert owner.result is None
    if action == "shutdown":
        assert owner.shutdown_complete


def test_retiring_process_prevents_replacement_until_actual_finished(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
) -> None:
    owner, processes, clock, helpers = controlled
    old = dispatch(owner, processes)
    old_reply = reply(old, clock)
    owner.cancel()
    owner.read_url("https://other.example/new-explicit-request")
    assert len(processes) == 1
    assert len(helpers) == 1
    old.push(old_reply)
    old.finish(-9, crashed=True)
    assert owner.result is None
    owner.set_visible(True)
    owner.read_url(PUBLIC_URL)
    assert len(processes) == 2
    newer = processes[-1]
    newer.activate()
    newer.push(reply(newer, clock))
    newer.finish()
    assert owner.result is not None


@pytest.mark.parametrize("seconds", [15.0, 16.0])
def test_process_deadline_is_rechecked_before_finish_without_waiting_for_timer(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
    seconds: float,
) -> None:
    owner, processes, clock, _ = controlled
    process = dispatch(owner, processes)
    stale = reply(process, clock)
    clock.advance(seconds)
    process.push(stale)
    process.finish()
    assert owner.result is None


def test_result_expiry_is_enforced_on_access_without_a_gui_tick(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
) -> None:
    owner, processes, clock, _ = controlled
    process = dispatch(owner, processes)
    process.push(reply(process, clock))
    process.finish()
    assert owner.result is not None
    clock.advance(300.0)
    assert owner.result is None


def test_clock_rollback_clears_card_and_cannot_accept_old_finished(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
) -> None:
    owner, processes, clock, _ = controlled
    process = dispatch(owner, processes)
    valid = reply(process, clock)
    clock.wall -= timedelta(seconds=1)
    process.push(valid)
    process.finish()
    assert owner.result is None


@pytest.mark.parametrize("code", [1, 2, -9])
def test_success_shaped_output_does_not_override_a_failed_process_exit(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
    code: int,
) -> None:
    owner, processes, clock, _ = controlled
    process = dispatch(owner, processes)
    process.push(reply(process, clock))
    process.finish(code, crashed=code < 0)
    assert owner.result is None


def test_shutdown_waits_for_actual_finished_despite_error_and_not_running_state(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
) -> None:
    owner, processes, _, _ = controlled
    process = dispatch(owner, processes)
    completions: list[bool] = []
    owner.shutdown_finished.connect(lambda: completions.append(owner.shutdown_complete))
    owner.shutdown()
    assert not owner.shutdown_complete
    process.active = QProcess.ProcessState.NotRunning
    process.errorOccurred.emit(QProcess.ProcessError.Crashed)
    assert not owner.shutdown_complete
    assert completions == []
    process.finish(-9, crashed=True)
    assert owner.shutdown_complete
    assert completions == [True]
    owner.shutdown()
    assert completions == [True]


def test_failed_to_start_needs_no_finished_signal(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
) -> None:
    owner, processes, _, _ = controlled
    owner.set_visible(True)
    owner.read_url(PUBLIC_URL)
    process = processes[-1]
    process.active = QProcess.ProcessState.NotRunning
    process.errorOccurred.emit(QProcess.ProcessError.FailedToStart)
    owner.shutdown()
    assert owner.result is None
    assert owner.shutdown_complete


_SYNTHETIC_HELPER = """
import json,sys,time
from datetime import UTC,datetime,timedelta
request=json.load(sys.stdin)
now=datetime.now(UTC)
reply={'schema_version':1,'origin':'public_https','coverage':'selected_url_only',
'untrusted':True,'status':'ok','source_label':'SYNTHETIC HELPER','source_url':request['url'],
'media_type':'text/plain','text':'SYNTHETIC PIPE BODY','raw_bytes':19,
'observed_at':now.isoformat(),'expires_at':(now+timedelta(minutes=5)).isoformat()}
for key in ('request_id','profile_id','session_id','generation','operation'):
 reply[key]=request[key]
print(json.dumps(reply),flush=True)
time.sleep(.15)
"""


def wait_for(predicate: Callable[[], bool], seconds: float = 3.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not predicate():
        QApplication.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_real_owned_synthetic_pipe_admits_after_exit_without_source_or_network(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    owner = PublicSourcesController(
        tmp_path, helper_command=lambda: (sys.executable, "-c", _SYNTHETIC_HELPER)
    )
    try:
        owner.bind_session(uuid4())
        owner.set_visible(True)
        owner.read_url(PUBLIC_URL)
        wait_for(lambda: owner.result is not None)
        assert not owner.busy
        assert owner.result is not None
        assert owner.result.text == "SYNTHETIC PIPE BODY"
        owner.shutdown()
        wait_for(lambda: owner.shutdown_complete)
    finally:
        owner.shutdown()
        wait_for(lambda: owner.shutdown_complete)
        owner.deleteLater()
        QApplication.processEvents()


def test_cached_result_expires_by_monotonic_budget_when_wall_clock_stalls(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
) -> None:
    owner, processes, clock, _ = controlled
    process = dispatch(owner, processes)
    process.push(reply(process, clock))
    process.finish()
    assert owner.result is not None
    clock.elapsed += 300.0
    assert clock.wall == NOW
    assert owner.result is None


def test_reentrant_quit_during_reading_signal_never_starts_unowned_child(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
) -> None:
    owner, processes, _, _ = controlled
    owner.set_visible(True)
    owner.changed.connect(lambda: owner.shutdown() if owner.status == "reading" else None)
    owner.read_url(PUBLIC_URL)
    assert owner.shutdown_complete
    assert owner.result is None
    assert not owner.busy
    assert len(processes) == 1
    assert processes[0].starts == 0
    assert processes[0].payload == b""


def test_reentrant_quit_during_helper_resolution_does_not_leave_unlaunched_job(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    owner: PublicSourcesController | None = None

    def helper() -> tuple[str, ...]:
        assert owner is not None
        owner.shutdown()
        return (sys.executable, "-c", "raise SystemExit(0)")

    owner = PublicSourcesController(tmp_path, helper_command=helper)
    try:
        owner.set_visible(True)
        owner.read_url(PUBLIC_URL)
        assert owner.shutdown_complete
        assert not owner.busy
        assert owner.result is None
    finally:
        owner.shutdown()
        owner.deleteLater()
        QApplication.processEvents()


@pytest.mark.parametrize("output", ["multiple_frames", "duplicate_id", "oversized"])
def test_output_boundaries_never_admit_or_rearm_an_extra_request(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
    output: str,
) -> None:
    owner, processes, clock, helpers = controlled
    process = dispatch(owner, processes)
    valid = reply(process, clock)
    if output == "multiple_frames":
        malformed = valid + valid
    elif output == "duplicate_id":
        malformed = b'{"request_id":"00000000-0000-0000-0000-000000000000",' + valid[1:]
    else:
        malformed = b"x" * (512 * 1024 + 1)
    process.push(malformed)
    process.finish()
    assert owner.result is None
    assert len(processes) == 1
    assert len(helpers) == 1


@pytest.mark.parametrize("observation", ["before_request", "future", "already_expired"])
def test_receipt_time_cannot_cross_creation_or_expiry_bounds(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
    observation: str,
) -> None:
    owner, processes, clock, _ = controlled
    process = dispatch(owner, processes)
    observed = (
        clock.utc() + timedelta(seconds=3)
        if observation == "future"
        else (
            clock.utc() - timedelta(seconds=1) if observation == "before_request" else clock.utc()
        )
    )
    expires = observed + timedelta(minutes=5)
    if observation == "already_expired":
        clock.advance(5.0)
        expires = clock.utc()
    process.push(
        reply(process, clock, observed_at=observed.isoformat(), expires_at=expires.isoformat())
    )
    process.finish()
    assert owner.result is None


@pytest.mark.parametrize("change", ["profile", "session"])
def test_reentrant_request_observes_new_identity_before_change_notification(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
    tmp_path: Path,
    change: str,
) -> None:
    owner, processes, clock, _ = controlled
    owner.set_visible(True)
    previous_profile, previous_session = owner.profile_id, owner.session_id
    target_session = uuid4()
    expected_status = "profile_changed" if change == "profile" else "session_changed"

    def explicit_new_action() -> None:
        if owner.status == expected_status:
            owner.read_url(PUBLIC_URL)

    owner.changed.connect(explicit_new_action)
    if change == "profile":
        owner.set_profile(tmp_path / "new-profile")
        assert owner.profile_id != previous_profile
    else:
        owner.bind_session(target_session)
        assert owner.session_id == target_session
    assert owner.session_id != previous_session
    assert len(processes) == 1
    process = processes[-1]
    process.activate()
    request = process.request()
    assert request["profile_id"] == str(owner.profile_id)
    assert request["session_id"] == str(owner.session_id)
    process.push(reply(process, clock))
    process.finish()
    assert owner.result is not None
    assert owner.result.session_id == owner.session_id


@pytest.mark.parametrize("elapsed_clock", ["wall", "monotonic"])
def test_final_parse_cannot_admit_a_reply_after_original_request_deadline(
    controlled: tuple[PublicSourcesController, list[ControlledProcess], Clock, list[bool]],
    monkeypatch: pytest.MonkeyPatch,
    elapsed_clock: str,
) -> None:
    owner, processes, clock, _ = controlled
    process = dispatch(owner, processes)
    encoded = reply(process, clock)
    original = PublicSourceReply.model_validate_json

    def parse_then_reach_deadline(
        cls: type[PublicSourceReply], /, data: str | bytes | bytearray, **kwargs: Any
    ) -> PublicSourceReply:
        del cls
        result = original(data, **kwargs)
        if elapsed_clock == "wall":
            clock.wall += timedelta(seconds=15)
        else:
            clock.elapsed += 15.0
        return result

    monkeypatch.setattr(
        PublicSourceReply, "model_validate_json", classmethod(parse_then_reach_deadline)
    )
    process.push(encoded)
    assert owner.result is None
    process.finish()
    assert owner.result is None
    assert owner.status == "timeout"
