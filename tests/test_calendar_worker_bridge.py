"""Same-Core Calendar RPC/effects and real pipe shutdown, using public fixtures only."""

import asyncio
import json
import os
import selectors
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from io import StringIO
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionScope,
    CalendarCreateAttempt,
    CalendarLookupReceipt,
    CalendarProviderReceipt,
)
from satori.application.assistant.calendar_host_lease import CalendarHostLease
from satori.application.assistant.contracts import CalendarReadResult
from satori.desktop.backend import DesktopBackend, DesktopProviders
from satori.desktop.calendar_bridge import (
    CalendarHostResult,
    CalendarReadRPC,
    DesktopCalendarOperations,
    packaged_calendar_action_helper,
)
from satori.desktop.calendar_chat_controller import CalendarChatController
from satori.desktop.worker import run_worker
from satori.server.calendar_backend import CalendarBackendCommand
from tests.calendar_actions_fixture import FixtureCalendarActionProvider
from tests.test_calendar_backend_integration import BackendCalendarModel
from tests.test_calendar_chat_controller import EXACT, PLAN, TARGET, Clock, SyntheticCalendar
from tests.test_conversation import skip_episode_provider
from tests.test_server_runtime import REPOSITORY, prepared


class PublicEventKitFixturePort:
    """Translate only the fixture's provider tag; never locate/invoke EventKit."""

    provider_kind: Literal["macos_eventkit"] = "macos_eventkit"
    external_effects = False

    def __init__(self, clock: Clock) -> None:
        self.fixture = FixtureCalendarActionProvider(clock)

    async def create(
        self, attempt: CalendarCreateAttempt, *, before_effect: Callable[[], None]
    ) -> CalendarProviderReceipt:
        receipt = await self.fixture.create(attempt, before_effect=before_effect)
        return CalendarProviderReceipt.model_validate(
            receipt.model_copy(update={"provider": "macos_eventkit"})
        )

    async def lookup(
        self,
        attempt: CalendarCreateAttempt,
        scope: CalendarActionScope,
        *,
        before_read: Callable[[], None],
    ) -> CalendarLookupReceipt:
        receipt = await self.fixture.lookup(attempt, scope, before_read=before_read)
        return CalendarLookupReceipt.model_validate(
            receipt.model_copy(update={"provider": "macos_eventkit"})
        )


class BridgeCase:
    def __init__(self, tmp_path: Path) -> None:
        self.root, _, self.session, _ = prepared(tmp_path)
        self.clock = Clock()
        self.model = BackendCalendarModel()
        self.backend = DesktopBackend(
            self.root,
            REPOSITORY,
            providers=DesktopProviders(self.model, skip_episode_provider()),
        )
        status = self.backend.open()
        assert isinstance(status["identity_id"], str)
        self.calendar = SyntheticCalendar(self.root, self.clock)
        self.chat = CalendarChatController(
            self.calendar, self.root, clock=self.clock, monotonic=self.clock.monotonic
        )
        self.chat.bind_context(status["identity_id"], self.session, "ollama:qwen3:4b-instruct")
        self.sent: list[dict[str, object]] = []
        self.chat.command_requested.connect(self.sent.append)
        self.frames: list[tuple[str, dict[str, object]]] = []
        self.revoke_before_accept = False
        self.corrupt_snapshot: str | None = None
        self.rpc = CalendarReadRPC(self.emit_host)
        self.chat.host_read_result.connect(self.accept_host)
        self.port = PublicEventKitFixturePort(self.clock)
        self.operations = DesktopCalendarOperations(
            self.backend,
            self.rpc,
            action_provider=self.port,
            clock=self.clock,
            monotonic_clock=self.clock.monotonic,
            parent_pid=os.getpid,
        )
        self.cursor = 0

    def emit_host(self, parent: str, data: dict[str, object]) -> None:
        self.frames.append((parent, data))
        self.chat.handle_host_read(data)
        if self.calendar.read_requests:
            request = self.calendar.read_requests[-1]
            snapshot = self.calendar._coordinator.accept_result(
                request,
                CalendarReadResult(
                    request_id=request.request_id,
                    observed_at=self.clock(),
                ),
            )
            self.calendar.planning_read_ready.emit(str(request.request_id), snapshot)

    def accept_host(self, value: dict[str, object]) -> None:
        wire = json.loads(json.dumps(value))
        if self.revoke_before_accept:
            self.chat.clear()
        if self.corrupt_snapshot is not None and isinstance(wire.get("snapshot"), dict):
            snapshot = wire["snapshot"]
            if self.corrupt_snapshot == "grant":
                snapshot["read_grant_id"] = str(uuid4())
            elif self.corrupt_snapshot == "coverage":
                snapshot["selected_calendar_ids"].append("public-ungranted-calendar")
            else:
                lease = CalendarHostLease.model_validate_json(
                    json.dumps(self.sent[0]["calendar_lease"])
                )
                snapshot["expires_at"] = (lease.expires_at + timedelta(seconds=1)).isoformat()
        self.rpc.accept(CalendarHostResult.model_validate_json(json.dumps(wire)))

    def submit(self, text: str) -> None:
        self.calendar.ready()
        assert self.chat.submit(text)
        self.chat.request_preview(TARGET, "Europe/Moscow")
        assert len(self.sent) == 1

    async def next(self, expected: str) -> dict[str, object]:
        value = self.sent[self.cursor]
        self.cursor += 1
        command = CalendarBackendCommand.model_validate_json(json.dumps(value["calendar"]))
        assert command.operation == expected
        request_id, session_id = str(value["request_id"]), str(value["session_id"])
        lease = (
            CalendarHostLease.model_validate_json(json.dumps(value["calendar_lease"]))
            if "calendar_lease" in value
            else None
        )
        result = await self.operations.execute(
            command,
            request_id=UUID(request_id),
            session_id=UUID(session_id),
            parent_command=f"public-command-{self.cursor}",
            lease=lease,
        )
        self.chat.accept_result(request_id, expected, result)
        return result

    async def close(self) -> None:
        self.operations.close()
        self.rpc.close()
        self.chat.close()
        self.calendar.close()
        await self.backend.close()
        self.chat.deleteLater()
        self.calendar.deleteLater()


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.mark.parametrize("mode", ["exact", "plan"])
def test_same_owned_core_and_gui_coordinator_reach_one_effect_and_readonly_reconcile(
    application: QApplication, tmp_path: Path, mode: str
) -> None:
    del application
    case = BridgeCase(tmp_path)
    case.submit(EXACT if mode == "exact" else PLAN)

    async def scenario() -> None:
        try:
            first = await case.next("exact_preview" if mode == "exact" else "preview")
            if mode == "plan":
                assert first["status"] == "disclosure_review_required"
                assert case.model.wire_sends == 0
                case.chat.disclose()
                assert (await case.next("disclose"))["status"] == "disclosure_approved"
                assert (await case.next("plan"))["status"] == "proposed"
            else:
                assert first["status"] == "proposed"
                assert first["model_called"] is False
            assert case.chat.card is not None
            assert case.chat.card.status == "proposed"
            assert case.port.fixture.effects == 0
            case.chat.create_reviewed()
            assert (await case.next("approve"))["state"] == "approved"
            created = await case.next("create")
            assert created["state"] == "created"
            assert case.port.fixture.create_calls == case.port.fixture.effects == 1
            case.chat.reconcile()
            assert (await case.next("reconcile"))["state"] == "reconciled_present"
            case.chat.reconcile()
            assert (await case.next("reconcile"))["state"] == "reconciled_present"
            assert case.port.fixture.lookup_calls == 2
            assert case.port.fixture.effects == 1
            assert case.model.wire_sends == (0 if mode == "exact" else 1)
            assert case.backend.history(case.session)["interactions"] == []
            assert case.backend.memories()["memories"] == []
            assert len(case.calendar.read_requests) >= 3
            assert len(case.frames) == len(case.calendar.read_requests)
            assert all(parent.startswith("public-command-") for parent, _ in case.frames)
            assert len({str(data["call_id"]) for _, data in case.frames}) == len(case.frames)
        finally:
            await case.close()

    asyncio.run(scenario())


def test_revoked_file_after_actual_gui_admission_blocks_model_and_effect(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    case = BridgeCase(tmp_path)
    case.submit(PLAN)
    case.revoke_before_accept = True

    async def scenario() -> None:
        try:
            result = await case.next("preview")
            assert result["status"] == "unavailable"
            assert case.calendar.grant is not None
            assert len(case.calendar.read_requests) == 1
            assert case.model.wire_sends == 0
            assert case.port.fixture.effects == 0
            assert case.chat.card is None
        finally:
            await case.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ["grant", "coverage", "expiry"])
def test_worker_rejects_snapshot_beyond_immutable_gui_lease(
    application: QApplication, tmp_path: Path, corruption: str
) -> None:
    del application
    case = BridgeCase(tmp_path)
    case.submit(PLAN)
    case.corrupt_snapshot = corruption

    async def scenario() -> None:
        try:
            result = await case.next("preview")
            assert result["status"] == "unavailable"
            assert case.model.wire_sends == 0
            assert case.port.fixture.effects == 0
            assert case.backend.history(case.session)["interactions"] == []
        finally:
            await case.close()

    asyncio.run(scenario())


def _await_pipe_worker_ready(process: subprocess.Popen[bytes], *, timeout: float = 30) -> None:
    """Observe the unopened worker's exact status response before measuring shutdown."""
    assert process.stdout is not None
    pending = bytearray()
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while b"\n" not in pending:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "public worker startup timed out"
            assert selector.select(remaining), "public worker startup timed out"
            chunk = os.read(process.stdout.fileno(), 4096)
            assert chunk, "public worker ended before readiness"
            pending.extend(chunk)
            assert len(pending) <= 65536, "public worker readiness exceeded bound"
    assert pending.count(b"\n") == 1, "public worker readiness frame invalid"
    assert pending.endswith(b"\n"), "public worker readiness frame invalid"
    ready = json.loads(pending)
    assert isinstance(ready, dict), "public worker readiness frame invalid"
    assert ready.get("id") == "public-ready", "public worker readiness correlation invalid"
    # Preserve the original scenario: the backend has not been opened/activated.
    # Its exact typed refusal proves the real command loop is accepting input.
    assert ready.get("event") == "error", "public worker unopened status refusal required"
    error = ready.get("error")
    assert isinstance(error, dict), "public worker readiness error required"
    assert error.get("code") == "runtime_not_open", "public worker readiness state invalid"
    assert "data" not in ready, "public worker unopened status data unexpected"


def test_real_worker_pipe_shutdown_exits_with_stdin_writer_still_open(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "satori.desktop.worker",
            "--data-directory",
            str(tmp_path.resolve()),
            "--repository-directory",
            str(REPOSITORY),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(b'{"id":"public-ready","command":"status","data":{}}\n')
        process.stdin.flush()
        _await_pipe_worker_ready(process)
        # Startup includes imports, backend construction and the actual ingress loop.
        # Its finite 30-second bound does not relax the five-second shutdown assertion.
        process.stdin.write(b'{"id":"public-stop","command":"shutdown","data":{}}\n')
        process.stdin.flush()
        assert process.wait(timeout=5) == 0
        # wait completed before closing stdin: a blocked input executor thread
        # cannot be hidden by communicate() closing the writer for the test.
        assert not process.stdin.closed
        assert process.stdout is not None
        rows = [json.loads(line) for line in process.stdout.read().splitlines()]
        assert len(rows) == 1
        assert rows[0]["id"] == "public-stop"
        assert rows[0]["data"]["closed"] is True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()


@pytest.mark.parametrize(
    "frame",
    [
        b'{"id":"foreign","event":"error","error":{"code":"runtime_not_open"}}\n',
        b'{"id":"public-ready","event":"error","error":{"code":"runtime_closed"}}\n',
        b'{"id":"public-ready","event":"error"}\n',
        b'{"id":"public-ready"}',
    ],
    ids=["foreign-request", "wrong-status-state", "missing-status-code", "partial-frame-eof"],
)
def test_shutdown_readiness_rejects_invalid_or_partial_worker_frame(frame: bytes) -> None:
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", f"import os;os.write(1,{frame!r})"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        with pytest.raises(AssertionError, match="public worker"):
            _await_pipe_worker_ready(process)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        assert process.stdout is not None
        process.stdout.close()


def test_shutdown_readiness_keeps_partial_frame_wait_bounded() -> None:
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", "import os,time;os.write(1,b'{');time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        with pytest.raises(AssertionError, match="public worker startup timed out"):
            _await_pipe_worker_ready(process, timeout=0.1)
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        assert process.stdout is not None
        process.stdout.close()


def _pipe_worker_cancellation_script() -> str:
    return (
        "import asyncio,sys\n"
        "from pathlib import Path\n"
        "from satori.desktop.backend import DesktopBackend\n"
        "from satori.desktop.worker import run_worker\n"
        "async def scenario():\n"
        " backend=DesktopBackend(Path(sys.argv[1]),Path(sys.argv[2]))\n"
        " worker=asyncio.create_task(run_worker(backend,sys.stdin,sys.stdout))\n"
        " await asyncio.sleep(0)\n"
        " await asyncio.sleep(0)\n"
        " worker.cancel()\n"
        " try: await asyncio.wait_for(worker,1)\n"
        " except asyncio.CancelledError: pass\n"
        " else: raise AssertionError('public cancellation not observed')\n"
        " assert all(t is asyncio.current_task() for t in asyncio.all_tasks())\n"
        "asyncio.run(scenario())\n"
    )


def _ready_script(script: str, *, startup_delay: float = 0) -> str:
    marker = "asyncio.run(scenario())\n"
    assert script.count(marker) == 1
    delay = f"import time\ntime.sleep({startup_delay!r})\n" if startup_delay else ""
    ready = (
        "import os\n" + delay + "_ready_fd=int(sys.argv[3])\n"
        "os.write(_ready_fd,b'R')\n"
        "os.close(_ready_fd)\n"
    )
    return script.replace(marker, ready + marker)


@contextmanager
def _ready_pipe_child(
    script: str, tmp_path: Path
) -> Iterator[tuple[subprocess.Popen[bytes], float]]:
    # The separate finite startup bound includes cold imports. A dedicated pipe
    # has no stdin EOF dependency, reader thread or inherited parent writer.
    ready_read, ready_write = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    startup = time.monotonic()
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                script,
                str(tmp_path.resolve()),
                str(REPOSITORY),
                str(ready_write),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            pass_fds=(ready_write,),
        )
        os.close(ready_write)
        ready_write = -1
        deadline = startup + 30
        with selectors.DefaultSelector() as selector:
            selector.register(ready_read, selectors.EVENT_READ)
            remaining = deadline - time.monotonic()
            assert remaining > 0, "public worker startup timed out"
            assert selector.select(remaining), "public worker startup timed out"
            # EOF/early import failure cannot stand in for the one-byte marker.
            assert os.read(ready_read, 1) == b"R", "public worker startup failed before READY"
        os.close(ready_read)
        ready_read = -1
        yield process, time.monotonic() - startup
    finally:
        for descriptor in (ready_read, ready_write):
            if descriptor >= 0:
                os.close(descriptor)
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()


def test_cancelling_pipe_worker_does_not_require_eof_or_leave_a_reader(tmp_path: Path) -> None:
    # Preserve both cancellation guards after imports: an abandoned executor
    # readline must still fail within five seconds with the stdin writer open.
    script = _ready_script(_pipe_worker_cancellation_script())
    with _ready_pipe_child(script, tmp_path) as (process, _startup_seconds):
        assert process.wait(timeout=5) == 0
        assert process.stdin is not None
        assert not process.stdin.closed
        assert process.stdout is not None
        assert process.stdout.read() == b""


def test_pipe_worker_startup_readiness_keeps_slow_import_phase_outside_cancellation(
    tmp_path: Path,
) -> None:
    script = _ready_script(_pipe_worker_cancellation_script(), startup_delay=5.1)
    with _ready_pipe_child(script, tmp_path) as (process, startup_seconds):
        assert 5 < startup_seconds < 30
        assert process.wait(timeout=5) == 0
        assert process.stdin is not None
        assert not process.stdin.closed
        assert process.stdout is not None
        assert process.stdout.read() == b""


def test_pipe_worker_readiness_does_not_hide_an_abandoned_executor_reader(tmp_path: Path) -> None:
    # Inject the exact historical failure shape into the owned public subprocess:
    # cancelling the asyncio task cannot unblock its default-executor readline.
    script = _pipe_worker_cancellation_script()
    script = script.replace(
        "async def scenario():\n",
        "import satori.desktop.worker as worker_module\n"
        "_entered=None\n"
        "async def abandoned_ingress(source,commands,rpc):\n"
        " loop=asyncio.get_running_loop()\n"
        " def blocking_read():\n"
        "  loop.call_soon_threadsafe(_entered.set)\n"
        "  return source.readline()\n"
        " await asyncio.to_thread(blocking_read)\n"
        "worker_module.route_ingress=abandoned_ingress\n"
        "async def scenario():\n"
        " global _entered\n"
        " _entered=asyncio.Event()\n",
    ).replace(
        " worker.cancel()\n", " await asyncio.wait_for(_entered.wait(),1)\n worker.cancel()\n"
    )
    with _ready_pipe_child(_ready_script(script), tmp_path) as (process, _startup_seconds):
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        assert process.poll() is None
        assert process.stdin is not None
        assert not process.stdin.closed
    assert process.poll() is not None


@pytest.mark.parametrize(
    "script",
    [
        "raise ImportError('public startup fixture')\n",
        "import os,sys\nos.write(int(sys.argv[3]),b'X')\nos.close(int(sys.argv[3]))\n",
    ],
)
def test_pipe_worker_readiness_rejects_early_exit_and_wrong_marker(
    tmp_path: Path,
    script: str,
) -> None:
    with (
        pytest.raises(AssertionError, match="startup failed before READY"),
        _ready_pipe_child(script, tmp_path),
    ):
        pytest.fail("an invalid readiness marker cannot admit cancellation testing")


def test_eof_during_calendar_rpc_invalidates_fetch_and_drains_all_owners(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    case = BridgeCase(tmp_path)
    case.submit(PLAN)
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    writer_closed = False

    def close_writer() -> None:
        nonlocal writer_closed
        if not writer_closed:
            os.close(write_fd)
            writer_closed = True

    class PublicDestination(StringIO):
        def write(self, value: str) -> int:
            count = super().write(value)
            if json.loads(value).get("event") == "calendar_host_request":
                close_writer()
            return count

    destination = PublicDestination()
    command = {"id": "public-eof-read", "command": "calendar", "data": case.sent[0]}
    os.write(write_fd, (json.dumps(command) + "\n").encode("utf-8"))

    def calendar_factory(
        backend: DesktopBackend, rpc: CalendarReadRPC
    ) -> DesktopCalendarOperations:
        return DesktopCalendarOperations(
            backend,
            rpc,
            action_provider=case.port,
            clock=case.clock,
            monotonic_clock=case.clock.monotonic,
            parent_pid=os.getpid,
        )

    async def scenario() -> None:
        try:
            result = await asyncio.wait_for(
                run_worker(case.backend, source, destination, calendar_factory=calendar_factory),
                timeout=3,
            )
            assert result == 0
            assert writer_closed is True
            rows = [json.loads(line) for line in destination.getvalue().splitlines()]
            assert [row["event"] for row in rows] == ["calendar_host_request", "result"]
            assert rows[-1]["data"]["status"] == "unavailable"
            assert case.calendar.read_requests == []
            assert case.model.wire_sends == 0
            assert case.port.fixture.create_calls == 0
        finally:
            # Also bounds cleanup under a regression to abandoned executor I/O.
            close_writer()
            await case.close()

    try:
        asyncio.run(scenario())
    finally:
        source.close()
        close_writer()


@pytest.mark.parametrize("layout", ["regular", "directory_alias", "leaf_alias"])
def test_packaged_action_helper_resolves_only_fixed_internal_placement(
    tmp_path: Path, layout: str
) -> None:
    from satori.infrastructure.assistant.calendar_actions_eventkit import _qualified_helper

    contents = tmp_path.resolve() / "Satori.app/Contents"
    executable = contents / "MacOS/Satori"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"PUBLIC-NOT-EXECUTED")
    advertised = contents / "Resources/calendar-actions-native/SatoriCalendarActions"
    target = (
        advertised
        if layout == "regular"
        else contents / "Frameworks/calendar-actions-native/SatoriCalendarActions"
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(b"PUBLIC-HELPER-NOT-EXECUTED")
    target.chmod(0o700)
    if layout == "directory_alias":
        advertised.parent.parent.mkdir(parents=True)
        advertised.parent.symlink_to(target.parent)
    elif layout == "leaf_alias":
        advertised.parent.mkdir(parents=True)
        advertised.symlink_to(target)
    result = packaged_calendar_action_helper(executable)
    assert result is not None
    assert result == target
    assert result.resolve(strict=True) == result
    if sys.platform == "darwin":
        assert _qualified_helper(result) == result


@pytest.mark.parametrize("layout", ["outside", "foreign_internal", "missing", "relative"])
def test_packaged_action_helper_rejects_outside_foreign_missing_or_relative(
    tmp_path: Path, layout: str
) -> None:
    contents = tmp_path.resolve() / "Satori.app/Contents"
    executable = contents / "MacOS/Satori"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"PUBLIC-NOT-EXECUTED")
    advertised = contents / "Resources/calendar-actions-native/SatoriCalendarActions"
    if layout in {"outside", "foreign_internal"}:
        target = (
            tmp_path.resolve() / "outside/SatoriCalendarActions"
            if layout == "outside"
            else contents / "Frameworks/foreign/SatoriCalendarActions"
        )
        target.parent.mkdir(parents=True)
        target.write_bytes(b"PUBLIC-NOT-EXECUTED")
        advertised.parent.mkdir(parents=True)
        advertised.symlink_to(target)
    supplied = Path("Satori.app/Contents/MacOS/Satori") if layout == "relative" else executable
    assert packaged_calendar_action_helper(supplied) is None
