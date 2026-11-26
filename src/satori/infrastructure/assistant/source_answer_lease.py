"""Private revocable GUI lease file; no content, account access or grant creation."""

import os
import stat
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from satori.application.assistant.contracts import require_utc
from satori.application.assistant.source_answer_contracts import (
    SourceAnswerError,
    SourceTaskLease,
    monotonic_window_is_current,
)

_MAX_LEASE_BYTES = 256 * 1024
_DIRECTORY = "source-task-leases"


def _now() -> datetime:
    return datetime.now(UTC)


def _fail() -> SourceAnswerError:
    return SourceAnswerError("source_task_lease_unavailable")


def _material(lease: SourceTaskLease) -> bytes:
    value = SourceTaskLease.model_validate(lease).model_dump_json().encode("utf-8")
    if len(value) > _MAX_LEASE_BYTES:
        raise _fail()
    return value


def _private(info: os.stat_result, *, directory: bool, uid: int) -> None:
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    mode = 0o700 if directory else 0o600
    if not kind or info.st_uid != uid or stat.S_IMODE(info.st_mode) != mode:
        raise _fail()
    if not directory and info.st_nlink != 1:
        raise _fail()


def _directory(root: Path, *, create: bool) -> int:
    if not root.is_absolute() or ".." in root.parts:
        raise _fail()
    for parent in (root, *root.parents):
        if parent.is_symlink():
            raise _fail()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _private(os.fstat(root_fd), directory=True, uid=os.getuid())
        if create:
            with suppress(FileExistsError):
                os.mkdir(_DIRECTORY, mode=0o700, dir_fd=root_fd)
        child = os.open(_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            _private(os.fstat(child), directory=True, uid=os.getuid())
            root_named, root_held = os.stat(root, follow_symlinks=False), os.fstat(root_fd)
            if (root_named.st_dev, root_named.st_ino) != (root_held.st_dev, root_held.st_ino):
                raise _fail()
            by_name = os.stat(_DIRECTORY, dir_fd=root_fd, follow_symlinks=False)
            held = os.fstat(child)
            if (by_name.st_dev, by_name.st_ino) != (held.st_dev, held.st_ino):
                raise _fail()
            return child
        except BaseException:
            os.close(child)
            raise
    finally:
        os.close(root_fd)


class SourceTaskLeaseOwner:
    """GUI-only file writer; authority remains in its conversation task/source coordinators."""

    def __init__(
        self,
        data_directory: Path,
        *,
        clock: Callable[[], datetime] = _now,
        monotonic: Callable[[], float] = time.monotonic,
        ui_pid: Callable[[], int] = os.getpid,
    ) -> None:
        self._root, self._clock, self._monotonic, self._pid = (
            data_directory,
            clock,
            monotonic,
            ui_pid,
        )
        self._active: tuple[UUID, int, int] | None = None

    def issue(self, lease: SourceTaskLease) -> Path:
        try:
            raw = _material(lease)
            now = require_utc(self._clock())
            tick = self._monotonic()
            if (
                lease.ui_pid != self._pid()
                or lease.os_uid != os.getuid()
                or not lease.issued_at <= now < lease.expires_at
                or not monotonic_window_is_current(
                    tick, lease.monotonic_deadline, lease.expires_at - lease.issued_at
                )
            ):
                raise _fail()
            self.revoke()
            directory = _directory(self._root, create=True)
            try:
                name = lease.lease_id.hex + ".json"
                descriptor = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    mode=0o600,
                    dir_fd=directory,
                )
                try:
                    _private(os.fstat(descriptor), directory=False, uid=os.getuid())
                    created = os.fstat(descriptor)
                    self._active = (lease.lease_id, created.st_dev, created.st_ino)
                    with os.fdopen(descriptor, "wb", closefd=False) as output:
                        output.write(raw)
                        output.flush()
                        os.fsync(output.fileno())
                finally:
                    os.close(descriptor)
                os.fsync(directory)
            finally:
                os.close(directory)
            return self._root / _DIRECTORY / name
        except (OSError, ValueError, TypeError):
            raise _fail() from None

    def revoke(self) -> None:
        active = self._active
        if active is None:
            return
        try:
            directory = _directory(self._root, create=False)
            try:
                name = active[0].hex + ".json"
                try:
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    self._active = None
                    return
                _private(info, directory=False, uid=os.getuid())
                if (info.st_dev, info.st_ino) != active[1:]:
                    raise _fail()
                os.unlink(name, dir_fd=directory)
                os.fsync(directory)
                self._active = None
            finally:
                os.close(directory)
        except (OSError, ValueError, TypeError):
            raise _fail() from None

    def close(self) -> None:
        self.revoke()


class SourceTaskLeaseReader:
    """Synchronous readback at reads, model dispatch, answer and result publication."""

    def __init__(
        self,
        data_directory: Path,
        lease: SourceTaskLease,
        *,
        clock: Callable[[], datetime] = _now,
        monotonic: Callable[[], float] = time.monotonic,
        parent_pid: Callable[[], int] = os.getppid,
    ) -> None:
        self.lease = SourceTaskLease.model_validate(lease)
        self._root, self._raw = data_directory, _material(self.lease)
        self._clock, self._monotonic, self._parent_pid = clock, monotonic, parent_pid
        self._last_now: datetime | None = None
        self._last_tick: float | None = None
        self._file_identity: tuple[int, int] | None = None
        self._directory_identity: tuple[int, int] | None = None

    def require(self) -> None:
        try:
            now, tick = require_utc(self._clock()), self._monotonic()
            lease = self.lease
            if (
                self._parent_pid() != lease.ui_pid
                or lease.os_uid != os.getuid()
                or not lease.issued_at <= now < lease.expires_at
                or not monotonic_window_is_current(
                    tick, lease.monotonic_deadline, lease.expires_at - lease.issued_at
                )
                or (self._last_now is not None and now < self._last_now)
                or (self._last_tick is not None and tick < self._last_tick)
            ):
                raise _fail()
            directory = _directory(self._root, create=False)
            try:
                directory_stat = os.fstat(directory)
                directory_identity = (directory_stat.st_dev, directory_stat.st_ino)
                if (
                    self._directory_identity is not None
                    and self._directory_identity != directory_identity
                ):
                    raise _fail()
                self._directory_identity = directory_identity
                descriptor = os.open(
                    lease.lease_id.hex + ".json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
                )
                try:
                    info = os.fstat(descriptor)
                    _private(info, directory=False, uid=os.getuid())
                    identity = (info.st_dev, info.st_ino)
                    if self._file_identity is not None and identity != self._file_identity:
                        raise _fail()
                    if (
                        info.st_size != len(self._raw)
                        or os.read(descriptor, _MAX_LEASE_BYTES + 1) != self._raw
                    ):
                        raise _fail()
                    self._file_identity = identity
                finally:
                    os.close(descriptor)
            finally:
                os.close(directory)
            self._last_now, self._last_tick = now, tick
        except (OSError, ValueError, TypeError):
            raise _fail() from None
