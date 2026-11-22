"""Owned one-shot public-source transport and transient results, outside the Core."""

import json
import math
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import ValidationError
from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from satori.infrastructure.public_sources.wire import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    PublicSourceReply,
    PublicSourceRequest,
)

REQUEST_SECONDS = 15.0


def public_source_command() -> Sequence[str]:
    """Fixed worker entry point; no source text, path or credential in arguments."""
    if getattr(sys, "frozen", False):
        return (sys.executable, "--public-source-worker")
    return (sys.executable, "-m", "satori.desktop", "--public-source-worker")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key] = value
    return result


@dataclass(eq=False)
class _Job:
    process: QProcess
    timer: QTimer
    request: PublicSourceRequest = field(repr=False)
    created_at: datetime
    deadline: float
    payload: bytes = field(repr=False)
    output: bytearray = field(default_factory=bytearray, repr=False)
    launched: bool = False
    terminal: bool = False
    disposed: bool = False


class PublicSourcesController(QObject):
    """One profile/session owns one exact foreground action and one transient card.

    Construction, visibility, polling and status display do not start a helper.
    A result is admitted only after the expected owned process actually finishes.
    Hidden/deactivated surfaces retain no source text or pending authority.
    """

    changed = Signal()
    shutdown_finished = Signal()

    def __init__(
        self,
        data_directory: Path,
        parent: QObject | None = None,
        *,
        helper_command: Callable[[], Sequence[str] | None] = public_source_command,
        process_factory: Callable[[QObject], QProcess] = QProcess,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        self._helper_command = helper_command
        self._process_factory = process_factory
        self._clock, self._monotonic = clock, monotonic
        self.profile_id = self._profile(data_directory)
        self.session_id = uuid4()
        self.generation = 0
        self.status = "idle"
        self.reason: str | None = None
        self._visible = False
        self._closed = False
        self._closing = False
        self._shutdown_emitted = False
        self._job: _Job | None = None
        self._retired: set[_Job] = set()
        self._result: PublicSourceReply | None = None
        self._result_deadline: float | None = None
        self._last_wall: datetime | None = None
        self._last_mono: float | None = None
        self.poll = QTimer(self)
        self.poll.setInterval(1000)
        self.poll.timeout.connect(self.tick)
        self.poll.start()

    @staticmethod
    def _profile(path: Path) -> UUID:
        return uuid5(NAMESPACE_URL, str(path.expanduser().resolve(strict=False)))

    @property
    def busy(self) -> bool:
        return self._job is not None or bool(self._retired)

    @property
    def shutdown_complete(self) -> bool:
        return self._closed and not self._closing and not self.busy

    @property
    def result(self) -> PublicSourceReply | None:
        result = self._result
        if result is None:
            return None
        if not self._visible or self._closed:
            self._result = None
            return None
        try:
            now, mono = self._times()
        except ValueError:
            self.cancel("clock_invalid")
            return None
        if (
            now >= result.expires_at
            or self._result_deadline is None
            or mono >= self._result_deadline
        ):
            self.cancel("expired")
            return None
        return result

    def _times(self) -> tuple[datetime, float]:
        try:
            wall, mono = self._clock(), self._monotonic()
        except Exception:
            raise ValueError("clock_invalid") from None
        if (
            not isinstance(wall, datetime)
            or wall.tzinfo is None
            or wall.utcoffset() != timedelta(0)
            or type(mono) not in {float, int}
            or not math.isfinite(mono)
            or (self._last_wall is not None and wall < self._last_wall)
            or (self._last_mono is not None and mono < self._last_mono)
        ):
            raise ValueError("clock_invalid")
        self._last_wall, self._last_mono = wall, mono
        return wall, mono

    def set_visible(self, visible: bool) -> None:
        if type(visible) is not bool or self._closed:
            return
        if not visible:
            self.conceal()
        else:
            # Visibility supplies no read authority and never restores a card.
            self._visible = True

    def conceal(self) -> None:
        self._visible = False
        self.cancel("concealed")

    def bind_session(self, session_id: UUID) -> None:
        if self._closed or type(session_id) is not UUID or session_id.int == 0:
            return
        if session_id != self.session_id:
            self.session_id = session_id
            self.cancel("session_changed")

    def set_profile(self, data_directory: Path) -> None:
        if self._closed:
            return
        self.profile_id = self._profile(data_directory)
        self.session_id = uuid4()
        self._last_wall = self._last_mono = None
        self.cancel("profile_changed")

    def read_file(self, path: Path) -> bool:
        return self._begin("file_read", path=str(path))

    def read_url(self, url: str) -> bool:
        return self._begin("url_read", url=url)

    def search_wikipedia(self, query: str, language: str = "ru") -> bool:
        return self._begin("wikipedia_search", query=query, language=language)

    def cancel(self, reason: str = "cancelled") -> None:
        # No caller/source string can turn into a diagnostic or status label.
        safe = (
            reason
            if type(reason) is str
            and reason
            in {
                "cancelled",
                "concealed",
                "session_changed",
                "profile_changed",
                "expired",
                "timeout",
                "clock_invalid",
                "response_invalid",
                "unavailable",
                "closing",
                "closed",
                "query_invalid",
            }
            else "cancelled"
        )
        self.generation += 1
        self._result = None
        self._result_deadline = None
        self.reason = None
        job, self._job = self._job, None
        if job is not None:
            job.timer.stop()
            job.payload = b""
            job.output.clear()
            self._retired.add(job)
            if job.process.state() != QProcess.ProcessState.NotRunning:
                job.process.kill()
            self._dispose(job, notify=False)
        self.status = "closing" if self._closed and not self.shutdown_complete else safe
        self.changed.emit()
        self._finish_shutdown()

    def _begin(self, operation: str, **arguments: object) -> bool:
        if self._closed or self.busy:
            return False
        if not self._visible:
            self.cancel("concealed")
            return False
        self._result = None
        self._result_deadline = None
        self.reason = None
        self.generation += 1
        try:
            now, mono = self._times()
            request = PublicSourceRequest.model_validate(
                {
                    "schema_version": 1,
                    "request_id": uuid4(),
                    "profile_id": self.profile_id,
                    "session_id": self.session_id,
                    "generation": self.generation,
                    "operation": operation,
                    **arguments,
                }
            )
            payload = request.model_dump_json().encode()
            if len(payload) > MAX_INPUT_BYTES:
                self.cancel("query_invalid")
                return False
        except ValidationError:
            self.cancel("query_invalid")
            return False
        except (ValueError, TypeError, OSError):
            self.cancel("clock_invalid")
            return False
        try:
            command = self._helper_command()
            if not self._new_request_current(request):
                return False
            if (
                not command
                or len(command) > 16
                or any(
                    type(value) is not str or not value or len(value) > 4096 or "\x00" in value
                    for value in command
                )
                or not Path(command[0]).is_absolute()
            ):
                self.cancel("unavailable")
                return False
        except Exception:
            if self._new_request_current(request):
                self.cancel("unavailable")
            return False
        if not self._new_request_current(request):
            return False
        try:
            process = self._process_factory(self)
        except Exception:
            if self._new_request_current(request):
                self.cancel("unavailable")
            return False
        if not self._new_request_current(request):
            process.deleteLater()
            return False
        timer = QTimer(self)
        timer.setSingleShot(True)
        job = _Job(process, timer, request, now, mono + REQUEST_SECONDS, payload)
        self._job = job
        process.started.connect(lambda: self._started(job))
        process.readyReadStandardOutput.connect(lambda: self._read(job))
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.finished.connect(lambda code, state: self._finished(job, code, state))
        process.errorOccurred.connect(lambda error: self._process_error(job, error))
        timer.timeout.connect(lambda: self._timeout(job))
        try:
            process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
            process.setProgram(command[0])
            process.setArguments(list(command[1:]))
            timer.start(int(REQUEST_SECONDS * 1000))
            self.status = "reading"
            self.changed.emit()
            if self._current(job):
                job.launched = True
                try:
                    process.start()
                except Exception:
                    if process.state() == QProcess.ProcessState.NotRunning:
                        job.terminal = True
                    raise
        except Exception:
            self.cancel("unavailable")
            return False
        return self._job is job

    def _new_request_current(self, request: PublicSourceRequest) -> bool:
        return (
            not self._closed
            and self._visible
            and not self.busy
            and request.generation == self.generation
            and request.profile_id == self.profile_id
            and request.session_id == self.session_id
        )

    def _current(self, job: _Job) -> bool:
        return (
            not self._closed
            and self._visible
            and self._job is job
            and job.request.profile_id == self.profile_id
            and job.request.session_id == self.session_id
            and job.request.generation == self.generation
        )

    def _eligible(self, job: _Job) -> bool:
        if not self._current(job):
            return False
        try:
            now, mono = self._times()
        except (ValueError, TypeError, AttributeError):
            self.cancel("clock_invalid")
            return False
        if mono >= job.deadline or now >= job.created_at + timedelta(seconds=REQUEST_SECONDS):
            self.cancel("timeout")
            return False
        return True

    def _started(self, job: _Job) -> None:
        if not self._eligible(job):
            return
        if job.process.write(job.payload) != len(job.payload):
            self.cancel("unavailable")
            return
        job.payload = b""
        job.process.closeWriteChannel()

    def _read(self, job: _Job) -> None:
        if not self._eligible(job):
            return
        data = job.process.readAllStandardOutput().data()
        if not self._current(job):
            return
        if len(data) + len(job.output) > MAX_OUTPUT_BYTES:
            self.cancel("response_invalid")
            return
        job.output.extend(data)

    def _timeout(self, job: _Job) -> None:
        if self._current(job):
            self.cancel("timeout")

    def _process_error(self, job: _Job, error: QProcess.ProcessError) -> None:
        if job.disposed:
            return
        if error == QProcess.ProcessError.FailedToStart:
            job.terminal = True
        if self._current(job):
            self.cancel("unavailable")
        elif error == QProcess.ProcessError.FailedToStart:
            self._dispose(job)

    def _finished(self, job: _Job, code: int, state: QProcess.ExitStatus) -> None:
        if job.disposed:
            return
        job.terminal = True
        if not self._eligible(job):
            self._dispose(job)
            return
        self._read(job)
        if not self._current(job):
            self._dispose(job)
            return
        data = bytes(job.output)
        self._job = None
        self._dispose(job, notify=False)
        try:
            json.loads(data, object_pairs_hook=_unique)
            result = PublicSourceReply.model_validate_json(data)
            now, mono = self._times()
            self._validate_reply(job, result, now)
            success = result.status in {"ok", "empty", "partial"}
            if state != QProcess.ExitStatus.NormalExit or code != (0 if success else 1):
                raise ValueError("response_invalid")
            # Parsing and typed admission are part of the original finite request.
            now, mono = self._times()
            if mono >= job.deadline or now >= job.created_at + timedelta(seconds=REQUEST_SECONDS):
                self.cancel("timeout")
                return
            if now >= result.expires_at:
                self.cancel("expired")
                return
        except (ValueError, ValidationError, TypeError, RecursionError):
            self.cancel("response_invalid")
            return
        # No user callback occurs between final identity/freshness checks and admission.
        if (
            self._closed
            or not self._visible
            or self.generation != job.request.generation
            or self.profile_id != job.request.profile_id
            or self.session_id != job.request.session_id
        ):
            return
        self._result = result
        self._result_deadline = mono + min(300.0, (result.expires_at - now).total_seconds())
        self.reason = result.reason
        self.status = (
            "ready"
            if result.status == "ok"
            else result.status
            if result.status in {"empty", "partial", "unavailable"}
            else "source_rejected"
        )
        self.changed.emit()

    @staticmethod
    def _validate_reply(job: _Job, reply: PublicSourceReply, now: datetime) -> None:
        expected = job.request
        if (
            reply.request_id != expected.request_id
            or reply.profile_id != expected.profile_id
            or reply.session_id != expected.session_id
            or reply.generation != expected.generation
            or reply.operation != expected.operation
            or reply.observed_at < job.created_at
            or reply.observed_at > now + timedelta(seconds=2)
            or now >= reply.expires_at
        ):
            raise ValueError("response_binding_invalid")
        if reply.status in {"ok", "empty", "partial"}:
            if (
                expected.operation == "file_read"
                and reply.source_label != Path(expected.path or "").name
            ):
                raise ValueError("response_binding_invalid")
            if expected.operation == "url_read" and reply.source_url != expected.url:
                raise ValueError("response_binding_invalid")
            if expected.operation == "wikipedia_search":
                language = expected.language or "ru"
                if reply.source_label != f"Wikipedia ({language}) only" or any(
                    urlsplit(item.url).netloc != f"{language}.wikipedia.org" for item in reply.items
                ):
                    raise ValueError("response_binding_invalid")

    def tick(self) -> None:
        if self._closed:
            return
        if self._job is not None:
            self._eligible(self._job)
        if self._result is not None:
            _ = self.result

    def _dispose(self, job: _Job, *, notify: bool = True) -> None:
        if job.disposed:
            return
        job.timer.stop()
        job.payload = b""
        job.output.clear()
        if job.process.state() != QProcess.ProcessState.NotRunning or (
            job.launched and not job.terminal
        ):
            self._retired.add(job)
            return
        was_retired = job in self._retired
        job.disposed = True
        self._retired.discard(job)
        job.timer.deleteLater()
        job.process.deleteLater()
        if notify and was_retired and not self._closed:
            self.changed.emit()
        self._finish_shutdown()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._closing = True
        self._visible = False
        self.poll.stop()
        self.cancel("closing")
        for job in tuple(self._retired):
            job.process.kill()
        self._closing = False
        self._finish_shutdown()

    def _finish_shutdown(self) -> None:
        if self.shutdown_complete and not self._shutdown_emitted:
            self.status = "closed"
            self._shutdown_emitted = True
            self.shutdown_finished.emit()
