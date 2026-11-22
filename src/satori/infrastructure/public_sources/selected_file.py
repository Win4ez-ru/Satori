"""One-use, no-follow reads of an explicitly chosen, owner-controlled text file.

Paths and file bodies are never instructions, audit fields, provider context or
canonical conversation. Callers must keep the result in a transient plain-text card.
Blocking filesystem syscalls still require an owned helper's outer hard deadline.
"""

import csv
import hashlib
import io
import json
import math
import os
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

MAX_FILE_BYTES = 1024 * 1024
MAX_DISPLAY_BYTES = 128 * 1024
MAX_PATH_BYTES = 4096
MAX_FILE_READ_SECONDS = 2.0
SELECTION_LIFETIME = timedelta(seconds=120)
CARD_LIFETIME = timedelta(minutes=5)
_READ_CHUNK = 64 * 1024
_MEDIA_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
}

FileReadCode = Literal[
    "selection_invalid",
    "selection_expired",
    "reader_closed",
    "path_invalid",
    "path_unavailable",
    "file_unsupported",
    "file_not_regular",
    "file_owner_mismatch",
    "file_permissions_unsafe",
    "file_hardlinked",
    "file_too_large",
    "file_changed",
    "content_unsupported",
    "content_invalid",
    "read_failed",
    "read_timeout",
    "cancelled",
    "clock_invalid",
    "platform_unavailable",
]


class FileReadError(ValueError):
    """A closed diagnostic code with no path, native exception or source body."""

    def __init__(self, code: FileReadCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    uid: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class FileSelection:
    """Metadata preview, not a transferable or serialized capability.

    Only the exact pending object held by its SelectedFileReader can be consumed.
    A new selection or owner restart invalidates an older object immediately.
    """

    selection_id: UUID = field(repr=False)
    path: Path = field(repr=False)
    identity: _FileIdentity = field(repr=False)
    media_type: str
    selected_at: datetime
    expires_at: datetime

    @property
    def name(self) -> str:
        """Local plain-text preview only; callers must not log this source name."""
        return self.path.name

    @property
    def size_bytes(self) -> int:
        return self.identity.size


@dataclass(frozen=True, slots=True)
class FileReadResult:
    """A bounded local observation; partial content never claims a whole document."""

    status: Literal["ok", "empty", "partial"]
    media_type: str
    text: str = field(repr=False)
    display_name: str = field(repr=False)
    raw_bytes: int
    displayed_bytes: int
    content_sha256: str = field(repr=False)
    observed_at: datetime
    expires_at: datetime


def _identity(metadata: os.stat_result) -> _FileIdentity:
    if not stat.S_ISREG(metadata.st_mode):
        raise FileReadError("file_not_regular")
    if metadata.st_uid != os.getuid():
        raise FileReadError("file_owner_mismatch")
    if metadata.st_mode & 0o022:
        raise FileReadError("file_permissions_unsafe")
    if metadata.st_nlink != 1:
        raise FileReadError("file_hardlinked")
    if not 0 <= metadata.st_size <= MAX_FILE_BYTES:
        raise FileReadError("file_too_large")
    return _FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        uid=metadata.st_uid,
        mode=metadata.st_mode,
        links=metadata.st_nlink,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def _validate_path(path: Path) -> str:
    if not isinstance(path, Path):
        raise FileReadError("path_invalid")
    try:
        valid = (
            path.is_absolute()
            and path.name not in {"", ".", ".."}
            and ".." not in path.parts
            and len(os.fsencode(path)) <= MAX_PATH_BYTES
            and not any(
                ord(character) < 32 or 127 <= ord(character) <= 159 for character in str(path)
            )
        )
    except (ValueError, UnicodeError):
        raise FileReadError("path_invalid") from None
    if not valid:
        raise FileReadError("path_invalid")
    media_type = _MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise FileReadError("file_unsupported")
    return media_type


@contextmanager
def _bound_parent(path: Path) -> Iterator[tuple[int, str]]:
    """Walk exact ancestors relative to held dirfds; never resolve a symlink."""
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")):
        raise FileReadError("platform_unavailable")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parent.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        yield descriptor, path.name
    finally:
        os.close(descriptor)


@contextmanager
def _bound_file(path: Path) -> Iterator[tuple[int, _FileIdentity]]:
    try:
        with _bound_parent(path) as (parent, name):
            expected = _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                actual = _identity(os.fstat(descriptor))
                if expected != actual:
                    raise FileReadError("file_changed")
                yield descriptor, actual
            finally:
                os.close(descriptor)
    except FileReadError:
        raise
    except (OSError, RuntimeError):
        raise FileReadError("path_unavailable") from None


def _invalid_json_constant(value: str) -> None:
    del value
    raise FileReadError("content_invalid")


def _validate_content(payload: bytes, media_type: str) -> str:
    try:
        text = payload.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise FileReadError("content_unsupported") from None
    if any(
        (ord(character) < 32 and character not in "\t\r\n")
        or 127 <= ord(character) <= 159
        or "\u202a" <= character <= "\u202e"
        or "\u2066" <= character <= "\u2069"
        for character in text
    ):
        raise FileReadError("content_unsupported")
    try:
        if media_type == "application/json":
            root = json.loads(text, parse_constant=_invalid_json_constant)
            pending = [(root, 0)]
            nodes = 0
            while pending:
                value, depth = pending.pop()
                nodes += 1
                if nodes > 100_000 or depth > 64:
                    raise FileReadError("content_invalid")
                if isinstance(value, float) and not math.isfinite(value):
                    raise FileReadError("content_invalid")
                if isinstance(value, dict):
                    pending.extend((child, depth + 1) for child in value.values())
                elif isinstance(value, list):
                    pending.extend((child, depth + 1) for child in value)
        elif media_type == "text/csv":
            for row_number, row in enumerate(csv.reader(io.StringIO(text), strict=True), 1):
                if row_number > 10_000 or len(row) > 256:
                    raise FileReadError("content_invalid")
    except (ValueError, RecursionError, csv.Error):
        raise FileReadError("content_invalid") from None
    return text


class SelectedFileReader:
    """One local owner of one pending explicit file selection, with no persistence."""

    def __init__(self, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock
        self._last_now: datetime | None = None
        self._pending: FileSelection | None = None
        self._closed = False

    def _now(self) -> datetime:
        try:
            now = self._clock()
            if (
                not isinstance(now, datetime)
                or now.tzinfo is None
                or now.utcoffset() != timedelta(0)
                or (self._last_now is not None and now < self._last_now)
            ):
                raise ValueError("invalid_clock")
        except (ValueError, TypeError, OverflowError):
            self.clear()
            raise FileReadError("clock_invalid") from None
        self._last_now = now.astimezone(UTC)
        return self._last_now

    def clear(self) -> None:
        self._pending = None

    def close(self) -> None:
        self.clear()
        self._closed = True

    def select(self, path: Path) -> FileSelection:
        """Metadata-only preview; this never reads file content or lists a directory."""
        self.clear()
        if self._closed:
            raise FileReadError("reader_closed")
        now = self._now()
        media_type = _validate_path(path)
        with _bound_file(path) as (_, identity):
            selected = FileSelection(
                uuid4(), path, identity, media_type, now, now + SELECTION_LIFETIME
            )
        self._pending = selected
        return selected

    def read(
        self, selection: FileSelection, *, cancelled: Callable[[], bool] = lambda: False
    ) -> FileReadResult:
        """Consume once before I/O; changed files and all failures require a new choice."""
        if self._closed:
            raise FileReadError("reader_closed")
        if selection is not self._pending or self._pending is None:
            raise FileReadError("selection_invalid")
        self._pending = None
        if self._now() >= selection.expires_at:
            raise FileReadError("selection_expired")
        deadline = time.monotonic() + MAX_FILE_READ_SECONDS

        def check() -> None:
            if cancelled():
                raise FileReadError("cancelled")
            if time.monotonic() >= deadline:
                raise FileReadError("read_timeout")
            if self._now() >= selection.expires_at:
                raise FileReadError("selection_expired")

        check()
        try:
            with _bound_file(selection.path) as (descriptor, before):
                if before != selection.identity:
                    raise FileReadError("file_changed")
                chunks: list[bytes] = []
                size = 0
                while True:
                    check()
                    chunk = os.read(descriptor, min(_READ_CHUNK, MAX_FILE_BYTES + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise FileReadError("file_too_large")
                after = _identity(os.fstat(descriptor))
                if before != after or size != after.size:
                    raise FileReadError("file_changed")
            # Reopen the entire path: ancestor renames must not validate via an old dirfd.
            with _bound_file(selection.path) as (_, current):
                if current != before:
                    raise FileReadError("file_changed")
        except FileReadError:
            raise
        except OSError:
            raise FileReadError("read_failed") from None
        check()
        payload = b"".join(chunks)
        text = _validate_content(payload, selection.media_type)
        encoded = text.encode("utf-8")
        partial = len(encoded) > MAX_DISPLAY_BYTES
        displayed = (
            encoded[:MAX_DISPLAY_BYTES].decode("utf-8", errors="ignore") if partial else text
        )
        observed_at = self._now()
        check()
        return FileReadResult(
            status="partial" if partial else "ok" if text else "empty",
            media_type=selection.media_type,
            text=displayed,
            display_name=selection.name,
            raw_bytes=len(payload),
            displayed_bytes=len(displayed.encode("utf-8")),
            content_sha256=hashlib.sha256(payload).hexdigest(),
            observed_at=observed_at,
            expires_at=observed_at + CARD_LIFETIME,
        )
