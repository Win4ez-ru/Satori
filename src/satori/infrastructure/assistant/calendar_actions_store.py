"""Private bounded SQLite journal for the calendar action owner, outside Core state."""

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from uuid import UUID

from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionError,
    CalendarActionRecord,
    CalendarActionState,
)
from satori.application.assistant.contracts import require_utc

_APPLICATION_ID = 0x53414331
_MAX_BYTES = 16 * 1024 * 1024
_MAX_RECORD_BYTES = 64 * 1024
_MAX_RECORDS = 1000
_RECONCILIATION_STATES: frozenset[CalendarActionState] = frozenset(
    {"reconciled_present", "reconciled_absent", "conflict", "unknown"}
)
_TRANSITIONS: dict[CalendarActionState, frozenset[CalendarActionState]] = {
    "proposed": frozenset({"approved"}),
    "approved": frozenset({"dispatch_claimed"}),
    "dispatch_claimed": frozenset({"created", "rejected", "unknown"}),
    "created": _RECONCILIATION_STATES,
    "rejected": _RECONCILIATION_STATES,
    "unknown": _RECONCILIATION_STATES,
    "reconciled_present": _RECONCILIATION_STATES,
    "reconciled_absent": _RECONCILIATION_STATES,
    "conflict": _RECONCILIATION_STATES,
}


def _private(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and not metadata.st_mode & 0o077
        and metadata.st_nlink == 1
    )


def _normal_path(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise CalendarActionError("calendar_action_store_invalid")
    if len(path.parts) > 1 and path.parts[1] in {"tmp", "var"}:
        alias = Path("/") / path.parts[1]
        target = Path("/private") / path.parts[1]
        if alias.is_symlink():
            if alias.lstat().st_uid != 0 or alias.resolve() != target:
                raise CalendarActionError("calendar_action_store_invalid")
            path = target.joinpath(*path.parts[2:])
    return path


def _directory(path: Path) -> int:
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            with suppress(FileExistsError):
                os.mkdir(name, mode=0o700, dir_fd=descriptor)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise CalendarActionError("calendar_action_store_invalid")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class SQLiteCalendarActionStore:
    """One locked writer; CAS revisions and immutable intent/claim prevent replay.

    The private record contains only the reviewed user-authored action and correlated
    receipts. Calendar snapshots, source bodies, credentials and prompts never enter it.
    Opening is explicit; callers must not create a replacement journal on uncertainty.
    """

    def __init__(self, path: Path) -> None:
        self.path = _normal_path(path)
        self._directory_fd = self._file_fd = self._lock_fd = -1
        self._connection: sqlite3.Connection | None = None
        self._identity: tuple[int, int] | None = None
        self._lock_identity: tuple[int, int] | None = None
        self._directory_identity: tuple[int, int] | None = None
        self._pid = os.getpid()
        try:
            self._directory_fd = _directory(self.path.parent)
            directory_metadata = os.fstat(self._directory_fd)
            self._directory_identity = directory_metadata.st_dev, directory_metadata.st_ino
            self._lock_fd = os.open(
                self.path.name + ".lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=self._directory_fd,
            )
            lock_metadata = os.fstat(self._lock_fd)
            if not _private(lock_metadata):
                raise CalendarActionError("calendar_action_store_invalid")
            self._lock_identity = lock_metadata.st_dev, lock_metadata.st_ino
            import fcntl

            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CalendarActionError("calendar_action_store_in_use") from None
            created = False
            try:
                self._file_fd = os.open(
                    self.path.name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self._directory_fd,
                )
                created = True
            except FileExistsError:
                self._file_fd = os.open(
                    self.path.name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=self._directory_fd
                )
            metadata = os.fstat(self._file_fd)
            if not _private(metadata) or metadata.st_size > _MAX_BYTES:
                raise CalendarActionError("calendar_action_store_invalid")
            if not created and os.pread(self._file_fd, 16, 0) != b"SQLite format 3\0":
                raise CalendarActionError("calendar_action_store_invalid")
            self._identity = metadata.st_dev, metadata.st_ino
            self._check()
            self._connection = sqlite3.connect(
                self.path.as_uri() + "?mode=rw", uri=True, timeout=0.2, isolation_level=None
            )
            connection = self._db
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            if created:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                connection.execute("PRAGMA user_version=1")
                with self._transaction():
                    connection.execute(
                        "CREATE TABLE actions (request_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
                    )
            if (
                connection.execute("PRAGMA application_id").fetchone() != (_APPLICATION_ID,)
                or connection.execute("PRAGMA user_version").fetchone() != (1,)
                or connection.execute("PRAGMA journal_mode").fetchone() != ("delete",)
                or connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
                or connection.execute(
                    "SELECT type,name FROM sqlite_schema "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                != [("table", "actions")]
                or connection.execute("PRAGMA table_info(actions)").fetchall()
                != [(0, "request_id", "TEXT", 0, None, 1), (1, "data", "TEXT", 1, None, 0)]
            ):
                raise CalendarActionError("calendar_action_store_invalid")
            self._records()
            self._check()
        except CalendarActionError:
            self.close()
            raise
        except (OSError, sqlite3.Error, ValueError):
            self.close()
            raise CalendarActionError("calendar_action_store_unavailable") from None

    @property
    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise CalendarActionError("calendar_action_store_unavailable")
        return self._connection

    def _check(self) -> None:
        try:
            if os.getpid() != self._pid:
                raise CalendarActionError("calendar_action_store_invalid")
            directory = self.path.parent.lstat()
            current = os.stat(self.path.name, dir_fd=self._directory_fd, follow_symlinks=False)
            held = os.fstat(self._file_fd)
            lock = os.stat(
                self.path.name + ".lock", dir_fd=self._directory_fd, follow_symlinks=False
            )
            held_lock = os.fstat(self._lock_fd)
            for suffix in ("-journal", "-wal", "-shm"):
                try:
                    sidecar = os.stat(
                        self.path.name + suffix,
                        dir_fd=self._directory_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    continue
                if suffix != "-journal" or not _private(sidecar) or sidecar.st_size > _MAX_BYTES:
                    raise CalendarActionError("calendar_action_store_invalid")
            if (
                stat.S_ISLNK(directory.st_mode)
                or directory.st_uid != os.getuid()
                or directory.st_mode & 0o077
                or (directory.st_dev, directory.st_ino) != self._directory_identity
                or not _private(current)
                or not _private(held)
                or (current.st_dev, current.st_ino) != self._identity
                or (held.st_dev, held.st_ino) != self._identity
                or current.st_size > _MAX_BYTES
                or not _private(lock)
                or not _private(held_lock)
                or (lock.st_dev, lock.st_ino) != self._lock_identity
                or (held_lock.st_dev, held_lock.st_ino) != self._lock_identity
            ):
                raise CalendarActionError("calendar_action_store_replaced")
        except OSError:
            raise CalendarActionError("calendar_action_store_replaced") from None

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._check()
        try:
            self._db.execute("BEGIN IMMEDIATE")
            yield
            self._check()
            self._db.execute("COMMIT")
            os.fsync(self._file_fd)
            os.fsync(self._directory_fd)
        except BaseException:
            with suppress(sqlite3.Error):
                self._db.execute("ROLLBACK")
            raise

    @staticmethod
    def _parse(request_id: str, data: str) -> CalendarActionRecord:
        try:
            if len(data.encode("utf-8")) > _MAX_RECORD_BYTES:
                raise ValueError

            def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
                result: dict[str, object] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            json.loads(data, object_pairs_hook=unique)
            record = CalendarActionRecord.model_validate_json(data)
            if str(record.proposal.command.request_id) != request_id:
                raise ValueError
            return record
        except (ValueError, TypeError):
            raise CalendarActionError("calendar_action_store_invalid") from None

    def _records(self) -> tuple[CalendarActionRecord, ...]:
        self._check()
        rows = self._db.execute(
            "SELECT request_id,data FROM actions LIMIT ?", (_MAX_RECORDS + 1,)
        ).fetchall()
        if len(rows) > _MAX_RECORDS:
            raise CalendarActionError("calendar_action_store_full")
        return tuple(self._parse(request_id, data) for request_id, data in rows)

    @property
    def last_observed_at(self) -> datetime | None:
        return max((record.updated_at for record in self._records()), default=None)

    def get(self, request_id: UUID) -> CalendarActionRecord | None:
        self._check()
        row = self._db.execute(
            "SELECT data FROM actions WHERE request_id=?", (str(request_id),)
        ).fetchone()
        return self._parse(str(request_id), row[0]) if row is not None else None

    @staticmethod
    def _serialized(record: CalendarActionRecord) -> str:
        checked = CalendarActionRecord.model_validate(record)
        data = checked.model_dump_json()
        if len(data.encode("utf-8")) > _MAX_RECORD_BYTES:
            raise CalendarActionError("calendar_action_store_full")
        return data

    def insert(self, record: CalendarActionRecord) -> None:
        data = self._serialized(record)
        if record.state != "proposed" or record.revision != 1:
            raise CalendarActionError("calendar_action_store_invalid")
        with self._transaction():
            if self.get(record.proposal.command.request_id) is not None:
                raise CalendarActionError("calendar_action_request_conflict")
            if self._db.execute("SELECT COUNT(*) FROM actions").fetchone()[0] >= _MAX_RECORDS:
                raise CalendarActionError("calendar_action_store_full")
            self._db.execute(
                "INSERT INTO actions VALUES (?,?)", (str(record.proposal.command.request_id), data)
            )

    def replace(self, record: CalendarActionRecord, *, expected_revision: int) -> None:
        data = self._serialized(record)
        with self._transaction():
            previous = self.get(record.proposal.command.request_id)
            if previous is None or previous.revision != expected_revision:
                raise CalendarActionError("calendar_action_concurrent_update")
            if (
                record.revision != previous.revision + 1
                or record.proposal != previous.proposal
                or (previous.approval is not None and record.approval != previous.approval)
                or (previous.attempt is not None and record.attempt != previous.attempt)
                or record.state not in _TRANSITIONS[previous.state]
                or record.updated_at < previous.updated_at
            ):
                raise CalendarActionError("calendar_action_store_invalid")
            self._db.execute(
                "UPDATE actions SET data=? WHERE request_id=?",
                (data, str(record.proposal.command.request_id)),
            )

    def recover_inflight(self, now: datetime) -> None:
        now = require_utc(now)
        for record in self._records():
            if record.state == "dispatch_claimed":
                value = record.model_dump()
                value.update(
                    state="unknown",
                    revision=record.revision + 1,
                    updated_at=now,
                    reason="calendar_action_unknown_no_replay",
                )
                self.replace(
                    CalendarActionRecord.model_validate(value), expected_revision=record.revision
                )

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        for name in ("_file_fd", "_lock_fd", "_directory_fd"):
            descriptor = getattr(self, name, -1)
            if descriptor >= 0:
                os.close(descriptor)
                setattr(self, name, -1)
