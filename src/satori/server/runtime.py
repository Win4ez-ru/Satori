"""Non-network headless lifecycle over one existing, current Core installation.

The lease coordinates this adapter's instances only. Legacy desktop/CLI writers
must be separately stopped; this is not a migration or offline-writer proof.
"""

import asyncio
import fcntl
import os
import sqlite3
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import UUID

from alembic.script import ScriptDirectory
from pydantic import BaseModel, ConfigDict, Field, model_validator

from satori.application.assistant.calendar_admission import calendar_host_admission
from satori.application.assistant.calendar_service import CalendarAssistantService
from satori.config import Settings
from satori.desktop.backend import DesktopBackend, DesktopProviders
from satori.infrastructure.persistence.migrations import build_alembic_config
from satori.server.calendar_backend import (
    CalendarBackendCommand,
    CalendarRuntimeBindings,
    execute_calendar_command,
)

type HeadlessFailure = Literal[
    "unsafe_path",
    "unsafe_storage",
    "owner_busy",
    "owner_unavailable",
    "storage_changed",
    "wrong_event_loop",
    "store_not_quiescent",
    "schema_upgrade_required",
    "runtime_not_new",
    "activation_required",
    "drain_unknown",
    "startup_rejected",
    "runtime_unavailable",
    "invalid_command",
    "busy",
    "command_failed",
]

# Closed metadata vocabulary from PostResponseProcessor and DesktopBackend.
# Unknown future codes cannot become arbitrary strings in the public diagnostics.
_BACKGROUND_FAILURE_PHASES = frozenset(
    {
        "relationship_processing",
        "current_models",
        "satori_positions",
        "episode_formation",
        "episode_embedding",
        "semantic_consolidation",
        "memory_lifecycle_inspection",
        "reflection_processing",
        "personality_reflection_processing",
        "worker_failure",
    }
)


class HeadlessError(Exception):
    """Finite diagnostics, without user text, paths or chained exception strings."""

    def __init__(self, code: HeadlessFailure) -> None:
        self.code = code
        super().__init__(code)


class HeadlessCommand(BaseModel):
    """Private in-process interface; not an authenticated remote API."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    operation: Literal[
        "status", "sessions", "history", "send", "reconcile", "new_session", "calendar"
    ]
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    text: str | None = Field(default=None, min_length=1, max_length=8000, repr=False)
    calendar: CalendarBackendCommand | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def check_scope(self) -> "HeadlessCommand":
        expected = {
            "status": set(),
            "sessions": set(),
            "history": {"session_id"},
            "send": {"session_id", "request_id", "text"},
            "reconcile": {"session_id", "request_id"},
            "new_session": {"request_id"},
            "calendar": {"session_id", "request_id", "calendar"},
        }[self.operation]
        actual = self.model_fields_set - {"operation"}
        if actual != expected or any(getattr(self, key) is None for key in expected):
            raise ValueError("invalid_operation_scope")
        return self


def _absolute_unlinked(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise HeadlessError("unsafe_path")
    for parent in reversed((path, *path.parents)):
        if stat.S_ISLNK(parent.lstat().st_mode):
            raise HeadlessError("unsafe_path")
    return path


def _private_regular(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_uid == os.getuid()
        and metadata.st_nlink == 1
    )


class _OwnerLease:
    """Keep the exact root and lock descriptors until owner drain completes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory_fd: int | None = None
        self.lock_fd: int | None = None

    def acquire(self) -> None:
        try:
            _absolute_unlinked(self.root)
            descriptor = self.directory_fd = os.open(
                self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            metadata = os.fstat(descriptor)
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise HeadlessError("unsafe_storage")
            self.lock_fd = os.open(
                ".satori-headless.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=descriptor,
            )
            if not _private_regular(os.fstat(self.lock_fd)):
                raise HeadlessError("unsafe_storage")
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HeadlessError("owner_busy") from None
            self.validate()
        except HeadlessError:
            self.release()
            raise
        except OSError:
            self.release()
            raise HeadlessError("unsafe_storage") from None

    def validate(self) -> None:
        if self.directory_fd is None or self.lock_fd is None:
            raise HeadlessError("owner_unavailable")
        try:
            _absolute_unlinked(self.root)
            directory = os.fstat(self.directory_fd)
            observed = self.root.lstat()
            opened = os.fstat(self.lock_fd)
            lock = os.stat(".satori-headless.lock", dir_fd=self.directory_fd, follow_symlinks=False)
            if (
                (directory.st_dev, directory.st_ino) != (observed.st_dev, observed.st_ino)
                or observed.st_uid != os.getuid()
                or stat.S_IMODE(observed.st_mode) != 0o700
                or (opened.st_dev, opened.st_ino) != (lock.st_dev, lock.st_ino)
                or not _private_regular(lock)
            ):
                raise HeadlessError("storage_changed")
        except OSError:
            raise HeadlessError("storage_changed") from None

    def release(self) -> None:
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
        if self.directory_fd is not None:
            os.close(self.directory_fd)
            self.directory_fd = None


class HeadlessRuntime:
    """One event loop, one active command, one existing backend owner.

    Cancelling a caller does not cancel the owned command. The lane stays busy
    until its actual terminal result; reconciliation is read-only, never replay.
    No activation, implicit session creation, provider switch or transport exists.
    """

    def __init__(
        self,
        data_directory: Path,
        repository_directory: Path,
        *,
        providers: DesktopProviders | None = None,
        runtime_settings: Settings | None = None,
        calendar_bindings: CalendarRuntimeBindings | None = None,
    ) -> None:
        self._root = data_directory
        self._repository = repository_directory
        self._providers = providers
        self._runtime_settings = runtime_settings
        self._calendar_bindings = calendar_bindings
        self._calendar: CalendarAssistantService | None = None
        self._lease = _OwnerLease(data_directory)
        self._backend: DesktopBackend | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._active: asyncio.Task[dict[str, object]] | None = None
        self._close_task: asyncio.Task[dict[str, object]] | None = None
        self._state: Literal["new", "ready", "closing", "closed", "unknown"] = "new"
        self._background_processing: Literal["not_observed", "complete", "partial"] = "not_observed"
        self._background_failures: tuple[str, ...] = ()

    def _check_loop(self) -> None:
        if self._loop is not asyncio.get_running_loop():
            raise HeadlessError("wrong_event_loop")

    def _preflight_schema(self) -> None:
        """Inspect only schema version; reject rather than migrate an old store."""
        database = self._root / "satori.db"
        if not _private_regular(database.lstat()):
            raise HeadlessError("unsafe_storage")
        sidecars = [self._root / ("satori.db" + suffix) for suffix in ("-journal", "-wal", "-shm")]
        if any(path.exists() or path.is_symlink() for path in sidecars):
            raise HeadlessError("store_not_quiescent")
        config = build_alembic_config("sqlite://", self._repository / "alembic.ini")
        head = ScriptDirectory.from_config(config).get_current_head()
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            versions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        if versions != [(head,)]:
            raise HeadlessError("schema_upgrade_required")

    async def start(self) -> dict[str, object]:
        if self._state != "new":
            raise HeadlessError("runtime_not_new")
        self._loop = asyncio.get_running_loop()
        self._lease.acquire()
        try:
            self._preflight_schema()
            self._lease.validate()
            self._backend = DesktopBackend(
                self._root,
                self._repository,
                providers=self._providers,
                runtime_settings=self._runtime_settings,
            )
            status = self._backend.open()
            if status["activated"] is not True:
                raise HeadlessError("activation_required")
            if self._calendar_bindings is not None:
                self._calendar = self._backend.build_calendar_assistant(self._calendar_bindings)
            self._lease.validate()
            self._state = "ready"
            return self.metadata()
        except Exception as error:
            # Startup cleanup has its own owner too: a cancelled starting caller
            # must not abandon a backend drain or release its lease early.
            self._state = "closing"
            self._close_task = asyncio.create_task(self._drain())
            self._close_task.add_done_callback(self._consume_failure)
            await asyncio.shield(self._close_task)
            if isinstance(error, HeadlessError):
                raise error from None
            raise HeadlessError("startup_rejected") from None

    def metadata(self) -> dict[str, object]:
        """Safe diagnostics omit identity, sessions, paths and conversation data."""
        return {
            "state": self._state,
            "network_listener": False,
            "migration_enabled": False,
            "activation_enabled": False,
            "command_pending": self._active is not None and not self._active.done(),
            "background_processing": self._background_processing,
            "post_response_failures": list(self._background_failures),
        }

    def _observe_background(self, receipt: dict[str, object]) -> None:
        """Separate a closed owner from the completeness of its task report.

        A terminal task failure does not make owner drain unknown. A malformed
        background report establishes neither success nor a safe failure code;
        when the owner explicitly confirms close, retain not_observed instead.
        """
        if receipt.get("closed") is not True:
            raise HeadlessError("drain_unknown")
        phases = receipt.get("post_response_failures")
        if (
            type(phases) is not list
            or len(phases) > len(_BACKGROUND_FAILURE_PHASES)
            or any(
                type(phase) is not str or phase not in _BACKGROUND_FAILURE_PHASES
                for phase in phases
            )
        ):
            self._background_processing = "not_observed"
            self._background_failures = ()
            return
        self._background_failures = tuple(sorted(set(phases)))
        self._background_processing = "partial" if self._background_failures else "complete"

    async def execute(
        self, command: HeadlessCommand, *, dispatch_guard: Callable[[], None] | None = None
    ) -> dict[str, object]:
        self._check_loop()
        if self._state != "ready":
            raise HeadlessError("runtime_unavailable")
        if not isinstance(command, HeadlessCommand):
            raise HeadlessError("invalid_command")
        if dispatch_guard is not None and not callable(dispatch_guard):
            raise HeadlessError("invalid_command")
        if command.operation == "new_session" and dispatch_guard is None:
            # Only a host with a durable pre-dispatch request journal may create
            # mobile sessions. Trusted stdio alone supplies no such acknowledgement.
            raise HeadlessError("invalid_command")
        if self._active is not None and not self._active.done():
            raise HeadlessError("busy")
        self._lease.validate()
        self._active = asyncio.create_task(self._execute(command, dispatch_guard))
        self._active.add_done_callback(self._consume_failure)
        return await asyncio.shield(self._active)

    @staticmethod
    def _consume_failure(task: asyncio.Task[dict[str, object]]) -> None:
        if not task.cancelled():
            task.exception()

    async def _execute(
        self, command: HeadlessCommand, dispatch_guard: Callable[[], None] | None
    ) -> dict[str, object]:
        assert self._backend is not None
        try:
            # The mobile admission may have been revoked while this task was queued.
            # Revalidate in the owned lane immediately before the backend operation.
            if dispatch_guard is not None and command.operation not in {"send", "new_session"}:
                dispatch_guard()
            match command.operation:
                case "status":
                    return self.metadata()
                case "sessions":
                    return self._backend.history()
                case "history":
                    assert command.session_id is not None
                    return self._backend.history(command.session_id)
                case "send":
                    assert command.text is not None
                    assert command.request_id is not None
                    assert command.session_id is not None
                    return await self._backend.send_for_session(
                        command.text,
                        command.request_id,
                        command.session_id,
                        dispatch_guard=dispatch_guard,
                    )
                case "reconcile":
                    assert command.session_id is not None
                    assert command.request_id is not None
                    return self._backend.request_status(command.session_id, command.request_id)
                case "new_session":
                    # The gateway journal acknowledges the request before dispatch.
                    # An unknown outcome is never automatically replayed.
                    return await self._backend.new_mobile_session(dispatch_guard=dispatch_guard)
                case "calendar":
                    if self._calendar is None or command.calendar is None:
                        return {
                            "status": "unavailable",
                            "reason": "calendar_backend_not_configured",
                            "event_created": False,
                        }
                    assert command.request_id is not None
                    assert command.session_id is not None
                    with calendar_host_admission(dispatch_guard):
                        return await execute_calendar_command(
                            self._calendar,
                            command.calendar,
                            request_id=UUID(command.request_id),
                            session_id=UUID(command.session_id),
                        )
        except Exception:
            raise HeadlessError("command_failed") from None

    async def close(self) -> dict[str, object]:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        self._check_loop()
        if self._state == "closed":
            return self.metadata()
        if self._state == "new":
            self._state = "closed"
            return self.metadata()
        if self._state == "unknown":
            raise HeadlessError("drain_unknown")
        if self._close_task is None:
            self._state = "closing"
            self._close_task = asyncio.create_task(self._drain())
            self._close_task.add_done_callback(self._consume_failure)
        return await asyncio.shield(self._close_task)

    async def _drain(self) -> dict[str, object]:
        try:
            if self._active is not None:
                await asyncio.gather(self._active, return_exceptions=True)
            if self._backend is not None:
                if self._calendar is not None:
                    self._calendar.close()
                    self._calendar = None
                self._observe_background(await self._backend.close())
            self._lease.validate()
            self._lease.release()
        except Exception:
            self._state = "unknown"
            raise HeadlessError("drain_unknown") from None
        self._state = "closed"
        return self.metadata()
