"""Owned Qt process port for fixed r5 worker dispatch; no Core/model route."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.source_answer_contracts import (
    SourceSearchPlan,
    SourceTaskRequest,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_task_reads import task_search_admission, task_snapshot
from satori.desktop.source_status import (
    ConnectionStatus,
    SnapshotStatus,
    SourceProblem,
    SourceStatus,
)
from satori.desktop.telegram_page_search import (
    TelegramPageSearchQuery,
    TelegramPageSearchResult,
    result_is_current,
    search_current_page,
)
from satori.desktop.telegram_task_protocol import verified_task_protocol

from .telegram_port import (
    COMMAND_FIELDS,
    INPUT_CAP,
    LineFramer,
    Sent,
    WireRejected,
    WireState,
    require,
    schema_from,
    strict_object,
)
from .telegram_reply_native import REPLY_COMMANDS
from .telegram_reply_wire import (
    TelegramReplyQualification,
    TelegramReplyView,
    parse_reply_frame,
)
from .telegram_reply_worker import PROTOCOL_SHA256, verified_reply_protocol

WORKER_PATH = "telegram/backend/worker.py.source"
SCHEMA_PATH = "telegram/backend/schema.json"
LIBRARY_PATH = "telegram/sdk/libtdjson.1.8.67.dylib"
POLICY_PATH = "telegram/policy.md"
WORKER_SHA256 = "69235f84d83bc49a01fba8a4e342775027d2a1a433fbef5bb25e88c127bef910"
SCHEMA_SHA256 = "cc50233f8e00c4ebef02498ff39b630322fdd029dde2698c84b114efb670f597"
LIBRARY_SHA256 = "8df0ee7cfc9c0fc2479cec0a6cde0b57243ad9a53a6d7dff1b9a6606b5703ad8"
POLICY_SHA256 = "bc76f956560195e01e88e085505297f3b87e1b4cbe2d24fcd1f5074c5f101eae"
_VIEW_REQUESTS = frozenset({"catalog", "select_chat", "read"})
_TRACKED_REQUESTS = _VIEW_REQUESTS | {"auth_submit", "account_preview", "confirm_account"}
EXPECTED_PAYLOAD = {
    WORKER_PATH: (WORKER_SHA256, 69216),
    SCHEMA_PATH: (SCHEMA_SHA256, 2331),
    LIBRARY_PATH: (LIBRARY_SHA256, 28144840),
}


@dataclass(frozen=True)
class TelegramRuntimeResources:
    root: Path
    config_sha256: str
    library: Path
    schema: dict[str, frozenset[str]]


def resources_root() -> Path:
    """The packaged resource location only; never a checkout/tmp fallback search."""
    if bool(getattr(sys, "frozen", False)):
        return Path(sys.executable).resolve().parent.parent / "Resources"
    return Path(__file__).resolve().parents[1] / "resources"


def load_runtime_resources(root: Path | None = None) -> TelegramRuntimeResources:
    """Qualify only fixed public payload/policy bytes, without SDK loading."""
    actual = root if root is not None else resources_root()
    config_path = contained(actual, "telegram/runtime-config.json")
    raw = config_path.read_bytes()
    require(raw == runtime_config_bytes())
    value = strict_object(raw, cap=262144)
    require(
        set(value) == {"v", "worker", "library", "payload"}
        and type(value["v"]) is int
        and value["v"] == 2
    )
    require(value["worker"] == {"path": WORKER_PATH, "sha256": WORKER_SHA256})
    require(value["library"] == {"path": LIBRARY_PATH, "sha256": LIBRARY_SHA256})
    require(type(value["payload"]) is list and len(value["payload"]) == len(EXPECTED_PAYLOAD))
    require(
        all(
            type(item) is dict
            and set(item) == {"path", "sha256", "bytes"}
            and type(item["path"]) is str
            and type(item["sha256"]) is str
            and type(item["bytes"]) is int
            for item in value["payload"]
        )
    )
    supplied = {item["path"]: (item["sha256"], item["bytes"]) for item in value["payload"]}
    require(len(supplied) == len(value["payload"]) and supplied == EXPECTED_PAYLOAD)
    for relative, (digest, size) in EXPECTED_PAYLOAD.items():
        path = contained(actual, relative)
        require(path.stat().st_size == size and sha256(path) == digest)
    policy = contained(actual, POLICY_PATH)
    require(policy.stat().st_size == 6951 and sha256(policy) == POLICY_SHA256)
    schema = schema_from(contained(actual, SCHEMA_PATH).read_bytes())
    return TelegramRuntimeResources(
        actual, sha256(config_path), contained(actual, LIBRARY_PATH), schema
    )


def runtime_config_bytes() -> bytes:
    value = {
        "v": 2,
        "worker": {"path": WORKER_PATH, "sha256": WORKER_SHA256},
        "library": {"path": LIBRARY_PATH, "sha256": LIBRARY_SHA256},
        "payload": [
            {"path": path, "sha256": digest, "bytes": size}
            for path, (digest, size) in sorted(EXPECTED_PAYLOAD.items())
        ],
    }
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def qualification_for(principal: LocalPrincipal) -> TelegramRuntimeQualification:
    """Pure static root composition; no package read, SDK or account access."""
    return TelegramRuntimeQualification(
        principal=principal,
        runtime_config_sha256=hashlib.sha256(runtime_config_bytes()).hexdigest(),
        policy_sha256=POLICY_SHA256,
        backend_sha256=WORKER_SHA256,
        sdk_sha256=LIBRARY_SHA256,
    )


class TelegramRuntimeQualification(BaseModel):
    """Root-only engineering qualification proposed by ADR-0092; not live acceptance."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    principal: LocalPrincipal = Field(repr=False)
    runtime_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    backend_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sdk_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TelegramActualAcceptance(BaseModel):
    """Root-owned real verification record, never derived from frames or fixtures."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    status: Literal["NOT_RUN", "PARTIAL", "PASS"] = "NOT_RUN"
    receipt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def honest_receipt(self) -> TelegramActualAcceptance:
        if (self.status == "NOT_RUN" and self.receipt_sha256 is not None) or (
            self.status == "PASS" and self.receipt_sha256 is None
        ):
            raise ValueError("telegram_actual_acceptance_invalid")
        return self


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def contained(root: Path, relative: str) -> Path:
    require(type(relative) is str and not relative.startswith("/") and "\\" not in relative)
    parts = relative.split("/")
    require(bool(parts) and all(p not in {"", ".", ".."} for p in parts))
    result = root
    for part in parts:
        result /= part
        require(not result.is_symlink())
    require(result.is_file() and result.resolve().is_relative_to(root.resolve()))
    return result


def verified_resources(
    resources: Path, qualification: TelegramRuntimeQualification
) -> tuple[Path, dict[str, frozenset[str]]]:
    """Exact static payload qualification; no SDK, session or account access."""
    require(
        qualification.backend_sha256 == WORKER_SHA256
        and qualification.sdk_sha256 == LIBRARY_SHA256
        and qualification.policy_sha256 == POLICY_SHA256
    )
    result = load_runtime_resources(resources)
    require(result.config_sha256 == qualification.runtime_config_sha256)
    return result.library, result.schema


def worker_arguments(
    library: Path,
    library_sha256: str,
    session_root: Path,
    *,
    frozen: bool,
    reply_principal: LocalPrincipal | None = None,
    reply_generation: int = 0,
) -> list[str]:
    name = "telegram_reply_worker" if reply_principal is not None else "telegram_worker"
    prefix = (
        ["--telegram-reply-worker" if reply_principal is not None else "--telegram-worker"]
        if frozen
        else ["-m", "satori.desktop." + name]
    )
    return [
        *prefix,
        "--library",
        str(library),
        "--library-sha256",
        library_sha256,
        "--session-root",
        str(session_root),
        *(
            [
                "--reply-principal",
                reply_principal.model_dump_json(),
                "--reply-generation",
                str(reply_generation),
            ]
            if reply_principal is not None
            else []
        ),
    ]


@dataclass
class _TelegramTaskFlight:
    request: SourceTaskRequest
    plan: SourceSearchPlan
    guard: Callable[[], bool]
    finished: Callable[[SourceTaskSnapshot], None]
    binding: tuple[object, ...]
    wire_id: str
    started_wall: datetime
    started_mono: float
    deadline: float


class TelegramRuntimeController(QObject):
    """One QProcess per explicit Connect; generations retire only after actual finish."""

    changed = Signal()
    shutdown_finished = Signal()

    def __init__(
        self,
        data_directory: Path,
        parent: QObject | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        self.data_directory = data_directory.expanduser().resolve(strict=False)
        self.principal = self._principal(self.data_directory)
        self.clock = clock
        self.qualification: TelegramRuntimeQualification | None = None
        self.acceptance = TelegramActualAcceptance()
        self.resources: Path | None = None
        self.state: WireState | None = None
        self.process: QProcess | None = None
        self.generation = 0
        self.closing = False
        self.shutting_down = False
        self._shutdown_emitted = False
        self._next_profile: Path | None = None
        self._framer = LineFramer()
        self._pending_writes: list[bytes] = []
        self._stderr_bytes = 0
        self._request_deadline = 0.0
        self._request_id: str | None = None
        self._auth_deadline = 0.0
        self._close_deadline = 0.0
        self._kill_deadline = 0.0
        self._sample = 0
        self._observed_at: datetime | None = None
        self.visible = False
        self.last_activity = self.clock()
        self.status = "runtime_unavailable"
        self.io_diagnostic = "NOT_STARTED"
        self.exit_description = "NOT_OBSERVED"
        self._task_flight: _TelegramTaskFlight | None = None
        self._task_retained: tuple[UUID, str, SourceTaskRequest, SourceSearchPlan] | None = None
        self._task_sent: dict[str, tuple[UUID, str]] = {}
        self._task_completed: set[str] = set()
        self._task_revocation_epoch = 0
        self._reply_qualification: TelegramReplyQualification | None = None
        self.reply_view = TelegramReplyView()
        self._reply_pending: tuple[str, tuple[object, ...] | None, str] | None = None
        self._reply_preview_input: tuple[UUID, str, str] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.poll)
        self._timer.start()

    @staticmethod
    def _principal(path: Path) -> LocalPrincipal:
        return LocalPrincipal(
            os_uid=os.getuid(), profile_id=uuid5(NAMESPACE_URL, str(path)), session_id=uuid4()
        )

    @property
    def shutdown_complete(self) -> bool:
        return self.process is None

    @property
    def source_status(self) -> SourceStatus:
        """Content-free projection: never polls, connects, dispatches or renews."""
        state = self.state
        try:
            now = self.clock()
        except Exception:
            now = math.nan
        connection: ConnectionStatus
        problem: SourceProblem
        snapshot: SnapshotStatus
        valid_clock = type(now) in (int, float) and math.isfinite(now)
        if not valid_clock:
            connection, problem = "error", "unknown"
        elif self.closing:
            connection, problem = "closing", "none"
        elif self.qualification is None:
            connection, problem = "unavailable", "unavailable"
        elif (
            state and state.session_deadline and (not valid_clock or now >= state.session_deadline)
        ):
            connection, problem = "expired", "expired"
        elif state and (state.first_failure or self.status == "runtime_failed"):
            connection, problem = "error", "rejected"
        elif self.process is None:
            connection, problem = "disconnected", "none"
        elif state and state.confirmed:
            connection, problem = "connected", "none"
        else:
            connection, problem = "connecting", "none"
        selected = bool(
            state
            and state.selected
            and valid_clock
            and now < state.selection_deadline
            and now < state.session_deadline
            and not state.terminal
        )
        if state and (
            state.stale or (state.page_id and valid_clock and now >= state.page_deadline)
        ):
            snapshot = "stale"
        elif state and state.page_id and selected and valid_clock and now < state.page_deadline:
            snapshot = "partial" if state.omitted else "fresh"
        elif connection == "error" or not valid_clock:
            snapshot = "unknown"
        else:
            snapshot = "not_read"
        return SourceStatus(
            source="telegram",
            connection=connection,
            problem=problem,
            selected_count=(
                None if state and state.selected and not valid_clock else int(selected)
            ),
            snapshot=snapshot,
            observed_at=self._observed_at,
            busy=self.busy,
        )

    @property
    def connect_enabled(self) -> bool:
        return (
            self.qualification is not None
            and self.resources is not None
            and self.process is None
            and not self.shutting_down
        )

    @property
    def live(self) -> bool:
        return (
            self.process is not None
            and self.process.state() == QProcess.ProcessState.Running
            and not self.closing
            and self.state is not None
            and not self.state.terminal
        )

    @property
    def busy(self) -> bool:
        return (
            bool(self.state and self.state.pending)
            or self._request_id is not None
            or self._task_flight is not None
            or self.closing
        )

    @property
    def list_enabled(self) -> bool:
        return (
            self.live
            and bool(self.state and self.state.confirmed)
            and not self.busy
            and self.visible
        )

    @property
    def select_enabled(self) -> bool:
        return self.list_enabled and bool(
            self.state and self.state.catalog_id and self.clock() < self.state.catalog_deadline
        )

    @property
    def read_enabled(self) -> bool:
        # A live selected lease is independent from the one-minute picker catalog.
        return (
            self.live
            and self.visible
            and self._task_flight is None
            and bool(
                self.state
                and self.state.confirmed
                and self.state.selected
                and self.clock() < self.state.selection_deadline
                and "read" not in self.state.pending
            )
        )

    def configure_runtime(
        self,
        qualification: TelegramRuntimeQualification,
        resources: Path,
        *,
        acceptance: TelegramActualAcceptance | None = None,
    ) -> bool:
        """Trusted root composition only; qualifying is not selected-read acceptance."""
        if self.process is not None or self.shutting_down:
            return False
        try:
            checked = TelegramRuntimeQualification.model_validate(qualification)
            actual = TelegramActualAcceptance.model_validate(
                acceptance or TelegramActualAcceptance()
            )
        except ValidationError:
            return False
        if (
            checked.principal != self.principal
            or checked.backend_sha256 != WORKER_SHA256
            or checked.sdk_sha256 != LIBRARY_SHA256
            or checked.policy_sha256 != POLICY_SHA256
            or checked.runtime_config_sha256 != hashlib.sha256(runtime_config_bytes()).hexdigest()
        ):
            return False
        # Full readback happens immediately before explicit Connect, not on tab open.
        self.qualification, self.resources, self.acceptance = checked, resources, actual
        self.status = "disconnected"
        self.changed.emit()
        return True

    def mark_activity(self) -> None:
        self.last_activity = self.clock()

    @property
    def active(self) -> bool:
        return self.clock() - self.last_activity <= 15

    def connect_account(self, api_id: str, api_hash: str, *, consent: bool) -> bool:
        if (
            not self.connect_enabled
            or not self.visible
            or consent is not True
            or not api_id.isascii()
            or not api_id.isdigit()
            or not 0 < int(api_id) < (1 << 31)
            or len(api_hash) != 32
            or any(c not in "0123456789abcdefABCDEF" for c in api_hash)
        ):
            return False
        try:
            assert self.qualification is not None
            assert self.resources is not None
            library, schema = verified_resources(self.resources, self.qualification)
            if self._reply_qualification is not None:
                require(self._reply_qualification.principal == self.principal)
                verified_reply_protocol(self.resources)
            parent = self.data_directory
            require(
                parent.is_dir()
                and not parent.is_symlink()
                and parent.stat().st_uid == os.getuid()
                and not stat.S_IMODE(parent.stat().st_mode) & 0o022
            )
        except (OSError, ValueError, AssertionError):
            self.status, self.io_diagnostic = "runtime_failed", "CONFIG_REJECTED"
            self.changed.emit()
            return False
        self.generation += 1
        generation = self.generation
        self.state = WireState(schema)
        self._framer, self._pending_writes, self._stderr_bytes = LineFramer(), [], 0
        self._sample, self.closing, self.exit_description = 0, False, "NOT_OBSERVED"
        process = QProcess(self)
        self.process = process
        process.setProgram(sys.executable)
        process.setArguments(
            worker_arguments(
                library,
                self.qualification.sdk_sha256,
                parent / "telegram-temporary",
                frozen=bool(getattr(sys, "frozen", False)),
                reply_principal=self.principal if self._reply_qualification is not None else None,
                reply_generation=generation,
            )
        )
        process.setWorkingDirectory(str(parent))
        environment = QProcessEnvironment()
        for key, value in {
            "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }.items():
            environment.insert(key, value)
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(lambda: self._read_stdout(process, generation))
        process.readyReadStandardError.connect(lambda: self._read_stderr(process, generation))
        process.readChannelFinished.connect(lambda: self._channel_ended(process, generation))
        process.bytesWritten.connect(lambda _: self._flush_writes(process, generation))
        process.started.connect(lambda: self._started(process, generation))
        process.errorOccurred.connect(lambda error: self._process_error(process, generation, error))
        process.finished.connect(
            lambda code, status: self._finished(process, generation, code, status)
        )
        self.status, self.io_diagnostic = "connecting", "STARTING"
        self._auth_deadline = self.clock() + 300
        # QProcess retains the write, never argv/env. No command/body logging exists.
        if not self._send(
            "connect",
            {"session_only": True, "api_id": int(api_id), "api_hash": api_hash.lower()},
            starting=True,
        ):
            self._fail("WRITE_REJECTED")
            return False
        process.start()
        self.changed.emit()
        return True

    def _current(self, process: QProcess, generation: int) -> bool:
        return process is self.process and generation == self.generation

    def _started(self, process: QProcess, generation: int) -> None:
        if self._current(process, generation):
            self.io_diagnostic = "RUNNING"
            self._flush_writes(process, generation)
            self.changed.emit()

    def _send(
        self,
        command: str,
        fields: dict[str, object] | None = None,
        *,
        starting: bool = False,
        track_conceal: bool = False,
    ) -> bool:
        if (
            self.process is None
            or self.state is None
            or (not starting and self.process.state() != QProcess.ProcessState.Running)
        ):
            return False
        value = fields or {}
        try:
            require(command in COMMAND_FIELDS and set(value) == COMMAND_FIELDS[command])
            require(not track_conceal or command == "conceal")
            if command in _TRACKED_REQUESTS:
                require(self._request_id is None)
            if command == "conceal" and self._request_id is not None:
                pending = self.state.sent.get(self._request_id)
                require(pending is not None)
                assert pending is not None
                if pending.command == "conceal":
                    return True
                require(not track_conceal or pending.command in _VIEW_REQUESTS)
            ident = uuid4().hex
            payload = (
                json.dumps(
                    {"v": 1, "command": command, "request_id": ident, **value},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )
            require(
                len(payload) <= INPUT_CAP
                and len(self._pending_writes) < 8
                and sum(map(len, self._pending_writes)) + len(payload) + self.process.bytesToWrite()
                <= 65536
            )
            self.state.register(ident, command, self.clock())
            self._pending_writes.append(payload)
            if command in _TRACKED_REQUESTS or track_conceal:
                self._request_deadline = self.clock() + 30
                self._request_id = ident
            if not starting:
                self._flush_writes(self.process, self.generation)
            return True
        except (ValueError, UnicodeError, TypeError):
            return False

    def _flush_writes(self, process: QProcess, generation: int) -> None:
        if (
            not self._current(process, generation)
            or process.state() != QProcess.ProcessState.Running
        ):
            return
        while self._pending_writes:
            payload = self._pending_writes[0]
            written = process.write(payload)
            if written < 0:
                self._fail("WRITE_FAILED")
                return
            if written == 0:
                return
            if written < len(payload):
                self._pending_writes[0] = payload[written:]
                return
            self._pending_writes.pop(0)

    def _read_stdout(self, process: QProcess, generation: int) -> None:
        if not self._current(process, generation):
            return
        process.setReadChannel(QProcess.ProcessChannel.StandardOutput)
        try:
            total = 0
            while process.bytesAvailable() > 0:
                chunk = bytes(process.read(min(65536, process.bytesAvailable())).data())
                require(bool(chunk))
                total += len(chunk)
                require(total <= 1024 * 1024)
                for frame in self._framer.feed(chunk):
                    self._receive(strict_object(frame))
        except (ValueError, OSError):
            self._fail("INVALID_FRAME")

    def _read_stderr(self, process: QProcess, generation: int) -> None:
        if not self._current(process, generation):
            return
        process.setReadChannel(QProcess.ProcessChannel.StandardError)
        while process.bytesAvailable() > 0:
            chunk = process.read(min(65536, process.bytesAvailable()))
            self._stderr_bytes += chunk.size()
            if self._stderr_bytes > 1024 * 1024:
                self._fail("STDERR_BOUND")
                return

    def _channel_ended(self, process: QProcess, generation: int) -> None:
        if not self._current(process, generation):
            return
        if self.state:
            self.state.reject()
        self._pending_writes.clear()
        process.closeWriteChannel()
        self.closing = True
        self._close_deadline = self.clock() + 35
        self._read_stdout(process, generation)
        self.changed.emit()

    def _receive(self, event: dict[str, object]) -> None:
        assert self.state is not None
        if type(event.get("v")) is int and event.get("v") == 3:
            self._receive_task(event)
            return
        if event.get("v") == 2:
            self._receive_reply(event)
            return
        now = self.clock()
        require(type(now) in (int, float) and math.isfinite(now))
        if not self.closing and (
            (self._request_deadline and now >= self._request_deadline)
            or (self._auth_deadline and now >= self._auth_deadline)
        ):
            self._fail("REQUEST_TIMEOUT")
        if self.state.expire(now) and self.state.terminal and not self.closing:
            self._request_close()
        previous = self.state.challenge
        previous_page = self.state.page_id
        kind = self.state.accept(event, now)
        if self._reply_preview_input is not None and self._reply_binding() is None:
            self._clear_reply_local()
        if (
            kind == "page"
            and self.state.page_id is not None
            and self.state.page_id != previous_page
        ):
            self._observed_at = datetime.now(UTC)
        if (
            event.get("request_id") == self._request_id
            and self._request_id is not None
            and (
                kind in {"auth", "account", "ready", "catalog", "selection", "page", "page_stale"}
                or (kind == "cleared" and event.get("reason") in {"GRANT_EXPIRED", "PAGE_EXPIRED"})
                or (
                    kind == "status"
                    and self.state.sent[self._request_id].command == "conceal"
                    and event.get("operation") == "CONCEAL"
                    and event.get("step") == "NONE"
                    and event.get("reason") == "NONE"
                )
            )
        ):
            self._request_deadline = 0
            self._request_id = None
        if kind == "ready":
            self._auth_deadline = 0
        if self.state.challenge != previous:
            self._auth_deadline = min(self._auth_deadline or self.clock() + 300, self.clock() + 300)
        if not self.visible and self.state.page_id is not None:
            self.conceal()
        self.status = (
            "page_stale"
            if self.state.stale
            else {
                "READY": "connected",
                "AUTH": "auth",
                "ACCOUNT_CONFIRM": "account_confirm",
                "CATALOG": "choose_chat",
                "SELECTED": "selected",
                "READ": "reading",
                "IDLE": "ready",
                "STOPPED": "stopped",
            }.get(self.state.phase, "connecting")
        )
        self.changed.emit()
        if self.state.terminal and not self.closing:
            self._request_close()

    def _fail(self, code: str) -> None:
        self._clear_reply_local()
        if self.io_diagnostic in {"NOT_STARTED", "STARTING", "RUNNING"}:
            self.io_diagnostic = code
        if self.state:
            self.state.reject()
        self.status = "runtime_failed"
        self._request_close()
        self.changed.emit()

    def _process_error(
        self, process: QProcess, generation: int, error: QProcess.ProcessError
    ) -> None:
        if not self._current(process, generation):
            return
        self._fail("PROCESS_ERROR")
        if error == QProcess.ProcessError.FailedToStart:
            self._finished(process, generation, -1, QProcess.ExitStatus.CrashExit)

    def _finished(
        self, process: QProcess, generation: int, code: int, status: QProcess.ExitStatus
    ) -> None:
        if not self._current(process, generation):
            return
        # Revoke before consuming the final tail: it may contain cleanup metadata,
        # but may never repopulate source text after actual process termination.
        if self.state:
            self.state.reject()
        self._pending_writes.clear()
        self._read_stdout(process, generation)
        self._read_stderr(process, generation)
        try:
            self._framer.eof()
        except WireRejected:
            if self.io_diagnostic in {"RUNNING", "STARTING"}:
                self.io_diagnostic = "PARTIAL_FRAME_EOF"
        self.exit_description = (
            "exit " + str(code)
            if status == QProcess.ExitStatus.NormalExit
            else "CRASH_EXIT / SIGNAL_UNKNOWN"
        )
        self.generation += 1
        self.process, self.closing, self._close_deadline, self._kill_deadline = None, False, 0, 0
        self._request_deadline = self._auth_deadline = 0
        self._request_id = None
        self.status = "stopped"
        process.deleteLater()
        if self._next_profile is not None:
            self._apply_profile(self._next_profile)
            self._next_profile = None
        self.changed.emit()
        self._finish_shutdown()

    def submit_auth(self, value: str) -> bool:
        if (
            not self.live
            or self.busy
            or not self.visible
            or not self.state
            or not self.state.challenge
            or not value
        ):
            return False
        ident, kind = self.state.challenge
        self.state.challenge = None
        result = self._send("auth_submit", {"challenge_id": ident, "kind": kind, "value": value})
        self.changed.emit()
        return result

    def confirm_account(self) -> bool:
        if not self.live or self.busy or not self.visible or not self.state or self.state.confirmed:
            return False
        if self.state.account_preview_needed:
            return self._send("account_preview")
        if self.state.account_token:
            result = self._send("confirm_account", {"account_token": self.state.account_token})
            self.changed.emit()
            return result
        return False

    def list_chats(self) -> bool:
        self.poll()
        if not self.list_enabled or self.state is None:
            return False
        self.state.clear_selection()
        self.state.clear_catalog()
        result = self._send("catalog")
        self.changed.emit()
        return result

    def select_chat(self, token: str, *, consent: bool) -> bool:
        self.poll()
        if (
            not self.select_enabled
            or not self.state
            or consent is not True
            or not any(c[0] == token for c in self.state.chats)
        ):
            return False
        self.state.clear_selection()
        self.state.pending_selection_token = token
        result = self._send(
            "select_chat",
            {"catalog_id": self.state.catalog_id, "chat_token": token, "read_view_consent": True},
        )
        self.changed.emit()
        return result

    def read_selected(self) -> bool:
        self.poll()
        if not self.read_enabled:
            return False
        result = self._send("read")
        self.changed.emit()
        return result

    @property
    def task_account_binding(self) -> tuple[object, ...] | None:
        state = self.state
        if (
            not self.live
            or state is None
            or not state.confirmed
            or self.clock() >= state.session_deadline
            or not self.reviewed_reply_enabled
        ):
            return None
        return (
            self.principal,
            self.generation,
            self.process,
            state,
            state.session_started,
            state.session_deadline,
            self._task_revocation_epoch,
        )

    def _task_current(self, flight: _TelegramTaskFlight) -> bool:
        try:
            now, wall = self.clock(), datetime.now(UTC)
            task_search_admission(flight.request, flight.plan, "telegram", wall)
            return (
                self._task_flight is flight
                and flight.binding == self.task_account_binding
                and now >= flight.started_mono
                and wall >= flight.started_wall
                and flight.guard()
                and not self.shutting_down
            )
        except Exception:
            return False

    def _send_task_packet(
        self, request: SourceTaskRequest, plan: SourceSearchPlan, operation: str, ident: str
    ) -> bool:
        if self.process is None or not self.live or len(self._task_sent) >= 512:
            return False
        try:
            payload = (
                json.dumps(
                    {
                        "v": 3,
                        "request_id": ident,
                        "command": operation,
                        "request": request.model_dump(mode="json"),
                        "plan": plan.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode()
            require(len(payload) <= INPUT_CAP and len(self._pending_writes) < 8)
            require(
                sum(map(len, self._pending_writes)) + len(payload) + self.process.bytesToWrite()
                <= 65536
            )
            self._task_sent[ident] = (request.request_id, operation)
            self._pending_writes.append(payload)
            self._flush_writes(self.process, self.generation)
            return True
        except (ValueError, UnicodeError, TypeError):
            return False

    def start_task_search(
        self,
        request: SourceTaskRequest,
        plan: SourceSearchPlan,
        *,
        guard: Callable[[], bool],
        finished: Callable[[SourceTaskSnapshot], None],
    ) -> bool:
        if self.busy or self.shutting_down:
            return False
        binding = self.task_account_binding
        if binding is None or self.resources is None:
            return False
        try:
            verified_task_protocol(self.resources)
            request = SourceTaskRequest.model_validate(request.model_dump())
            plan = SourceSearchPlan.model_validate(plan.model_dump())
            wall, now = datetime.now(UTC), self.clock()
            task_search_admission(request, plan, "telegram", wall)
            if request.scope.profile_id != self.principal.profile_id or not guard():
                return False
        except Exception:
            return False
        ident = uuid4().hex
        flight = _TelegramTaskFlight(
            request, plan, guard, finished, binding, ident, wall, now, now + 30
        )
        self._task_flight = flight
        self._task_retained = None
        if not self._send_task_packet(request, plan, "task_search", ident):
            self._task_flight = None
            return False
        self.changed.emit()
        return True

    def cancel_task_search(self, task_id: UUID) -> None:
        flight, retained = self._task_flight, self._task_retained
        if flight is not None and flight.request.request_id == task_id:
            self._task_flight = None
            # The existing owned command reader interrupts the sole receive lane
            # immediately on conceal; the task handler turns it into a bounded error.
            self._send("conceal")
            self._send_task_packet(flight.request, flight.plan, "task_cancel", uuid4().hex)
        elif retained is not None and retained[0] == task_id:
            self._task_retained = None
            self._send_task_packet(retained[2], retained[3], "task_cancel", uuid4().hex)
        else:
            return
        self.changed.emit()

    def _receive_task(self, event: dict[str, object]) -> None:
        state = self.state
        require(state is not None)
        assert state is not None
        require(type(event.get("event_seq")) is int and event["event_seq"] == state.sequence + 1)
        ident = event.get("request_id")
        require(type(ident) is str and ident in self._task_sent)
        assert isinstance(ident, str)
        task_id, operation = self._task_sent[ident]
        require(event.get("task_id") == str(task_id))
        kind = event.get("type")
        common = {"v", "event_seq", "type", "request_id", "task_id"}
        require(
            (
                kind == "task_result"
                and set(event) == common | {"snapshot"}
                and operation == "task_search"
            )
            or (
                kind == "task_invalidated"
                and set(event) == common | {"reason"}
                and event.get("reason") == "source_changed"
                and (
                    ident in self._task_completed
                    or (self._task_flight is not None and self._task_flight.wire_id == ident)
                )
            )
            or (kind == "task_cancelled" and set(event) == common and operation == "task_cancel")
        )
        state.sequence += 1
        if kind == "task_invalidated":
            retained = self._task_retained
            flight = self._task_flight
            if (retained is not None and retained[:2] == (task_id, ident)) or (
                flight is not None and flight.wire_id == ident
            ):
                self._task_retained = None
                self._task_flight = None
                self._task_revocation_epoch += 1
                self.changed.emit()
            return
        require(ident not in self._task_completed)
        self._task_completed.add(ident)
        if kind == "task_cancelled":
            return
        flight = self._task_flight
        if flight is None or flight.wire_id != ident or not self._task_current(flight):
            return
        snapshot = SourceTaskSnapshot.model_validate_json(json.dumps(event.get("snapshot")))
        require(
            snapshot.request_id == flight.request.request_id
            and snapshot.scope == flight.request.scope
            and snapshot.plan_digest == flight.plan.digest
            and {item.source for item in snapshot.coverage} == {"telegram"}
            and all(
                item.source == "telegram"
                and flight.request.created_at <= item.observed_at <= snapshot.observed_at
                and (
                    item.event_at is None
                    or flight.plan.window_start <= item.event_at < flight.plan.window_end
                )
                for item in snapshot.items
            )
            and all(
                c.query == flight.plan.telegram_query
                and c.window_start == flight.plan.window_start
                and c.window_end == flight.plan.window_end
                for c in snapshot.coverage
            )
            and flight.request.created_at <= snapshot.observed_at < flight.request.expires_at
        )
        if not self._task_current(flight):
            return
        self._task_flight = None
        self._task_retained = (task_id, ident, flight.request, flight.plan)
        flight.finished(snapshot)
        self.changed.emit()

    def search_current_page(self, query: TelegramPageSearchQuery) -> TelegramPageSearchResult:
        """Local current-page projection only; no poll, fetch, model call or state write."""
        return search_current_page(self, query)

    def page_search_is_current(self, result: TelegramPageSearchResult) -> bool:
        """Required before transient display; the result is not source authority."""
        return result_is_current(self, result)

    def configure_reviewed_replies(self, qualification: TelegramReplyQualification) -> bool:
        """Root-only opt-in to new code; no account or send grant is created."""
        if self.process is not None or self.shutting_down:
            return False
        try:
            checked = TelegramReplyQualification.model_validate(qualification)
        except ValidationError:
            return False
        if checked.principal != self.principal or checked.protocol_sha256 != PROTOCOL_SHA256:
            return False
        self._reply_qualification = checked
        self.reply_view = TelegramReplyView(state="idle")
        self.changed.emit()
        return True

    @property
    def reviewed_reply_enabled(self) -> bool:
        return self._reply_qualification is not None

    def _reply_binding(self) -> tuple[object, ...] | None:
        state = self.state
        now = self.clock()
        if (
            not self.reviewed_reply_enabled
            or not self.live
            or not self.visible
            or state is None
            or not state.confirmed
            or state.stale
            or not state.page_id
            or not state.selected
            or now >= min(state.session_deadline, state.selection_deadline, state.page_deadline)
        ):
            return None
        return (self.principal, self.generation, state.epoch, state.selected, state.page_id)

    @property
    def reply_ready(self) -> bool:
        return self._reply_binding() is not None and not self.busy

    def _clear_reply_local(self) -> None:
        uncertain = self._reply_pending is not None and self._reply_pending[2] in {
            "reply_send",
            "reply_reconcile",
        }
        self.reply_view = TelegramReplyView(state="unknown" if uncertain else "idle")
        self._reply_preview_input = None

    def discard_reply(self) -> None:
        """Editing/selection/hide removes the prior exact preview and approval."""
        self._clear_reply_local()
        if self.reply_ready:
            self._send_reply("reply_discard", {})
        self.changed.emit()

    def _send_reply(self, operation: str, fields: dict[str, object]) -> bool:
        if not self.reply_ready or self.process is None or self.state is None:
            return False
        try:
            require(operation in REPLY_COMMANDS and set(fields) == REPLY_COMMANDS[operation])
            require(len(self.state.sent) < 4096 and not self._pending_writes)
            ident = uuid4().hex
            payload = (
                json.dumps(
                    {
                        "v": 2,
                        "capability": "reviewed_reply_v1",
                        "command": operation,
                        "request_id": ident,
                        **fields,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
                + b"\n"
            )
            require(
                len(payload) <= INPUT_CAP and self.process.bytesToWrite() + len(payload) <= 65536
            )
            self.state.sent[ident] = Sent(operation, self.state.epoch, self.clock())
            self._reply_pending = ident, self._reply_binding(), operation
            self._request_id, self._request_deadline = ident, self.clock() + 30
            self._pending_writes.append(payload)
            self._flush_writes(self.process, self.generation)
            self.changed.emit()
            return True
        except (ValueError, TypeError, UnicodeError):
            return False

    def preview_reply(
        self, message_handle: str, text: str, *, action_id: UUID | None = None
    ) -> bool:
        state = self.state
        if (
            not self.reply_ready
            or state is None
            or not any(m.handle == message_handle for m in state.messages)
        ):
            return False
        action_id = action_id or uuid4()
        self._clear_reply_local()
        self._reply_preview_input = action_id, message_handle, text
        return self._send_reply(
            "reply_preview",
            {
                "action_id": str(action_id),
                "page_id": state.page_id,
                "message_handle": message_handle,
                "text": text,
            },
        )

    def approve_reply(self) -> bool:
        view = self.reply_view
        if view.state != "preview" or view.action_id is None or view.payload_digest is None:
            return False
        return self._send_reply(
            "reply_approve",
            {
                "action_id": str(view.action_id),
                "payload_digest": view.payload_digest,
                "confirmed": True,
            },
        )

    def send_reply(self) -> bool:
        view = self.reply_view
        if (
            view.state != "approved"
            or view.action_id is None
            or view.approval_id is None
            or view.expires_at is None
            or datetime.now(UTC) >= view.expires_at
        ):
            return False
        sent = self._send_reply(
            "reply_send", {"action_id": str(view.action_id), "approval_id": str(view.approval_id)}
        )
        if sent and self.reply_view == view:
            self.reply_view = replace(view, state="sending", approval_id=None)
            self.changed.emit()
        return sent

    def reconcile_reply(self) -> bool:
        view = self.reply_view
        if view.state != "unknown" or view.action_id is None:
            return False
        return self._send_reply("reply_reconcile", {"action_id": str(view.action_id)})

    def _receive_reply(self, event: dict[str, object]) -> None:
        state, pending = self.state, self._reply_pending
        require(self.reviewed_reply_enabled and state is not None and pending is not None)
        assert state is not None
        assert pending is not None
        now = self.clock()
        require(type(now) in (int, float) and math.isfinite(now))
        if self._request_deadline and now >= self._request_deadline:
            self._fail("REQUEST_TIMEOUT")
        if state.expire(now) and state.terminal and not self.closing:
            self._request_close()
        ident, binding, operation = pending
        require(event.get("request_id") == ident)
        view = parse_reply_frame(event, sequence=state.sequence)
        state.sequence += 1
        self._reply_pending = None
        if self._request_id == ident:
            self._request_id, self._request_deadline = None, 0
        if binding != self._reply_binding() or self.closing:
            self.reply_view = TelegramReplyView(
                state="unknown" if operation in {"reply_send", "reply_reconcile"} else "idle"
            )
            self._reply_preview_input = None
            self.changed.emit()
            return
        expected = self._reply_preview_input
        if view.state == "preview":
            require(operation == "reply_preview" and expected is not None)
            assert expected is not None
            require(
                (view.action_id, view.message_handle, view.text) == expected
                and event.get("page_id") == state.page_id
            )
        elif view.state == "approved":
            require(operation == "reply_approve" and self.reply_view.state == "preview")
            require(
                view.action_id == self.reply_view.action_id
                and view.payload_digest == self.reply_view.payload_digest
            )
            view = replace(
                view,
                text=self.reply_view.text,
                chat_title=self.reply_view.chat_title,
                message_handle=self.reply_view.message_handle,
            )
        elif view.state in {"sent", "failed", "unknown"}:
            require(operation in {"reply_send", "reply_reconcile"})
            if view.action_id is not None:
                require(view.action_id == self.reply_view.action_id)
            view = replace(view, action_id=self.reply_view.action_id)
        self.reply_view = view
        self.changed.emit()

    def report_viewport(self, viewport: list[int], rows: list[dict[str, object]]) -> bool:
        self.poll()
        if not self.live or not self.visible or not self.state or self.state.page_id is None:
            return False
        self._sample += 1
        return self._send(
            "viewport",
            {
                "page_id": self.state.page_id,
                "sample_id": self._sample,
                "foreground": True,
                "active": self.active,
                "viewport": viewport,
                "rows": rows,
            },
        )

    def set_foreground(self, visible: bool) -> None:
        if not visible and self.visible:
            self.conceal()
        self.visible = bool(visible)

    def conceal(self) -> None:
        self._clear_reply_local()
        self.visible = False
        state = self.state
        if (
            state
            and state.confirmed
            and (
                state.catalog_id
                or state.selected is not None
                or state.page_id
                or _VIEW_REQUESTS.intersection(state.pending)
            )
        ):
            state.conceal()
            if self.live and not self._send("conceal", track_conceal=True):
                self._fail("CONCEAL_FAILED")
                return
        self.changed.emit()

    def _request_close(self) -> None:
        self._task_flight = None
        self._task_retained = None
        self._clear_reply_local()
        if self.process is None or self.closing:
            return
        self.closing = True
        self._pending_writes.clear()
        if self.state:
            self.state.reject()
        if self.process.state() == QProcess.ProcessState.Running and not self._send("close"):
            self.process.closeWriteChannel()
        self._close_deadline = self.clock() + 35

    def disconnect_account(self) -> None:
        self._request_close()
        self.changed.emit()

    def cancel(self) -> None:
        self.disconnect_account()

    def _apply_profile(self, path: Path) -> None:
        self._task_flight = None
        self._task_retained = None
        self._task_sent.clear()
        self._task_completed.clear()
        self._task_revocation_epoch += 1
        self.data_directory = path.expanduser().resolve(strict=False)
        self.principal = self._principal(self.data_directory)
        self.qualification, self.resources, self.state = None, None, None
        self._reply_qualification = None
        self._clear_reply_local()
        self.acceptance = TelegramActualAcceptance()
        self._observed_at = None
        self.status = "runtime_unavailable"

    def set_profile(self, data_directory: Path) -> None:
        if self.process is not None:
            self._next_profile = data_directory
            self.disconnect_account()
        else:
            self._apply_profile(data_directory)
        self.changed.emit()

    def shutdown(self) -> None:
        self.shutting_down = True
        self._request_close()
        self._finish_shutdown()

    def _finish_shutdown(self) -> None:
        if self.shutting_down and self.shutdown_complete and not self._shutdown_emitted:
            self._shutdown_emitted = True
            self._timer.stop()
            self.shutdown_finished.emit()

    def poll(self) -> None:
        if self.process is None:
            return
        flight = self._task_flight
        if flight is not None:
            if not self._task_current(flight):
                self.cancel_task_search(flight.request.request_id)
            elif self.clock() >= flight.deadline:
                failure = task_snapshot(
                    flight.request,
                    flight.plan,
                    "telegram",
                    datetime.now(UTC),
                    (),
                    status="error",
                    detail="telegram_task_timeout",
                )
                callback = flight.finished
                self.cancel_task_search(flight.request.request_id)
                if flight.guard():
                    callback(failure)
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            self._fail("CLOCK_REJECTED")
            return
        if self.closing:
            if self._kill_deadline and now >= self._kill_deadline:
                self.process.kill()
                self._kill_deadline = 0
            elif self._close_deadline and now >= self._close_deadline:
                self.process.terminate()
                self._close_deadline, self._kill_deadline = 0, now + 5
            return
        if (self._request_deadline and now >= self._request_deadline) or (
            self._auth_deadline and now >= self._auth_deadline
        ):
            self._fail("REQUEST_TIMEOUT")
            return
        previous_selection = self.state.selected if self.state else None
        previous_page = self.state.page_id if self.state else None
        if self.state and self.state.expire(now):
            if self.state.terminal:
                self._request_close()
            elif self.live and (
                (previous_selection is not None and self.state.selected is None)
                or (previous_page is not None and self.state.page_id is None)
            ):
                self._send("conceal")
            self.changed.emit()
