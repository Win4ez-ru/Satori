"""Bounded private SQLite reminder owner; no accounts, model calls or notification I/O."""

import hashlib
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ValidationError

from satori.application.assistant.contracts import require_utc
from satori.application.assistant.reminder_contracts import (
    MAX_REMINDERS,
    DeliveryOperation,
    ReminderActionReceipt,
    ReminderCreate,
    ReminderDeliveryReceipt,
    ReminderDeliveryRequest,
    ReminderError,
    ReminderRecord,
)

_APPLICATION_ID = 0x53415231
_MAX_BYTES = 16 * 1024 * 1024
_MAX_REQUESTS = 10000


def _private(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and not metadata.st_mode & 0o077
        and metadata.st_nlink == 1
    )


def _normal_path(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise ReminderError("reminder_store_path_invalid")
    if len(path.parts) > 1 and path.parts[1] in {"tmp", "var"}:
        alias = Path("/") / path.parts[1]
        if alias.is_symlink():
            metadata = alias.lstat()
            target = Path("/private") / path.parts[1]
            if metadata.st_uid != 0 or alias.resolve() != target:
                raise ReminderError("reminder_store_path_invalid")
            path = target.joinpath(*path.parts[2:])
    return path


def _directory(path: Path, *, create: bool = True) -> int:
    """Walk/create by directory descriptor; never follow user-controlled symlinks."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(name, mode=0o700, dir_fd=descriptor)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ReminderError("reminder_store_directory_not_private")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class SQLiteReminderStore:
    """One serialized owner and process lock per explicitly enabled private store."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = _normal_path(path)
        self._read_only = read_only
        self._directory_fd = -1
        self._file_fd = -1
        self._lock_fd = -1
        self._connection: sqlite3.Connection | None = None
        self._identity: tuple[int, int] | None = None
        try:
            self._directory_fd = _directory(self.path.parent, create=not read_only)
            self._lock_fd = os.open(
                self.path.name + ".lock",
                os.O_RDWR | os.O_NOFOLLOW | (0 if read_only else os.O_CREAT),
                0o600,
                dir_fd=self._directory_fd,
            )
            if not _private(os.fstat(self._lock_fd)):
                raise ReminderError("reminder_store_lock_invalid")
            import fcntl

            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ReminderError("reminder_store_in_use") from None
            created = False
            if read_only:
                self._file_fd = os.open(
                    self.path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._directory_fd
                )
            else:
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
                raise ReminderError("reminder_store_file_invalid")
            if not created and os.pread(self._file_fd, 16, 0) != b"SQLite format 3\0":
                raise ReminderError("reminder_store_schema_invalid")
            self._identity = metadata.st_dev, metadata.st_ino
            self._check()
            if read_only and (
                os.pread(self._file_fd, 2, 18) != b"\x01\x01"
                or any(
                    (self.path.parent / (self.path.name + suffix)).exists()
                    for suffix in ("-journal", "-wal", "-shm")
                )
            ):
                raise ReminderError("reminder_store_recovery_required")
            self._connection = sqlite3.connect(
                self.path.as_uri() + ("?mode=ro" if read_only else "?mode=rw"),
                uri=True,
                timeout=0.2,
                isolation_level=None,
            )
            self._check()
            connection = self._db
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            if read_only:
                connection.execute("PRAGMA query_only=ON")
            if created:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                connection.execute("PRAGMA user_version=1")
                with self._transaction():
                    connection.execute(
                        "CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
                    )
                    connection.execute(
                        "CREATE TABLE reminders (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
                    )
                    connection.execute(
                        "CREATE TABLE requests (id TEXT PRIMARY KEY, digest TEXT NOT NULL, "
                        "reminder_id TEXT NOT NULL REFERENCES reminders(id), "
                        "operation TEXT NOT NULL)"
                    )
                    connection.execute(
                        "CREATE TABLE audit (seq INTEGER PRIMARY KEY, event TEXT NOT NULL, "
                        "reminder_id TEXT NOT NULL, request_id TEXT, outcome TEXT NOT NULL, "
                        "observed_at TEXT NOT NULL)"
                    )
                    connection.execute("INSERT INTO meta VALUES ('owner_id', ?)", (str(uuid4()),))
                os.fsync(self._directory_fd)
            if (
                connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != 1
            ):
                raise ReminderError("reminder_store_schema_invalid")
            row = connection.execute("SELECT value FROM meta WHERE name='owner_id'").fetchone()
            if row is None or not isinstance(row[0], str):
                raise ReminderError("reminder_store_schema_invalid")
            self.owner_id = UUID(row[0])
            if not self.owner_id.int or str(self.owner_id) != row[0]:
                raise ReminderError("reminder_store_schema_invalid")
            if not read_only:
                self.list()
        except ReminderError:
            self.close()
            raise
        except (OSError, ValueError, sqlite3.Error, ImportError):
            self.close()
            raise ReminderError("reminder_store_unavailable") from None

    @property
    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ReminderError("reminder_store_closed")
        return self._connection

    def _check(self) -> None:
        if self._identity is None:
            raise ReminderError("reminder_store_closed")
        current = os.stat(self.path.name, dir_fd=self._directory_fd, follow_symlinks=False)
        if not _private(current) or (current.st_dev, current.st_ino) != self._identity:
            raise ReminderError("reminder_store_replaced")
        if current.st_size > _MAX_BYTES:
            raise ReminderError("reminder_store_limit")
        parent = self.path.parent.lstat()
        bound = os.fstat(self._directory_fd)
        if stat.S_ISLNK(parent.st_mode) or (parent.st_dev, parent.st_ino) != (
            bound.st_dev,
            bound.st_ino,
        ):
            raise ReminderError("reminder_store_replaced")
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                sidecar = os.stat(
                    self.path.name + suffix, dir_fd=self._directory_fd, follow_symlinks=False
                )
            except FileNotFoundError:
                continue
            if not _private(sidecar):
                raise ReminderError("reminder_store_sidecar_invalid")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        try:
            if self._read_only:
                raise ReminderError("reminder_store_read_only")
            self._check()
            self._db.execute("BEGIN IMMEDIATE")
            yield
            self._check()
            self._db.execute("COMMIT")
            try:
                self._check()
            except (ReminderError, OSError):
                raise ReminderError("reminder_store_outcome_unknown") from None
        except BaseException as error:
            if self._connection is not None and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(error, ReminderError):
                raise
            if isinstance(error, (OSError, sqlite3.Error, ValueError, TypeError)):
                raise ReminderError("reminder_store_unavailable") from None
            raise

    def _read_record(self, data: str) -> ReminderRecord:
        if not isinstance(data, str) or len(data.encode()) > 8192:
            raise ReminderError("reminder_store_record_invalid")
        try:
            return ReminderRecord.model_validate_json(data)
        except (ValidationError, ValueError):
            raise ReminderError("reminder_store_record_invalid") from None

    def list(self) -> tuple[ReminderRecord, ...]:
        try:
            self._check()
            rows = self._db.execute(
                "SELECT id, data FROM reminders LIMIT ?", (MAX_REMINDERS + 1,)
            ).fetchall()
            if len(rows) > MAX_REMINDERS:
                raise ReminderError("reminder_store_limit")
            records = tuple(self._read_record(row[1]) for row in rows)
            if any(
                str(record.reminder_id) != row[0] for record, row in zip(records, rows, strict=True)
            ):
                raise ReminderError("reminder_store_record_invalid")
            self._check()
            return tuple(
                sorted(records, key=lambda r: (r.due_at, r.created_at, str(r.reminder_id)))
            )
        except (OSError, sqlite3.Error):
            raise ReminderError("reminder_store_unavailable") from None

    def get(self, reminder_id: UUID) -> ReminderRecord:
        found = next((record for record in self.list() if record.reminder_id == reminder_id), None)
        if found is None:
            raise ReminderError("reminder_not_found")
        return found

    def _put(self, record: ReminderRecord) -> None:
        record = ReminderRecord.model_validate_json(record.model_dump_json())
        self._db.execute(
            "INSERT INTO reminders(id,data) VALUES (?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (str(record.reminder_id), record.model_dump_json()),
        )

    def _audit(
        self, event: str, record: ReminderRecord, now: datetime, request_id: UUID | None = None
    ) -> None:
        self._db.execute(
            "INSERT INTO audit(event,reminder_id,request_id,outcome,observed_at) "
            "VALUES (?,?,?,?,?)",
            (
                event,
                str(record.reminder_id),
                str(request_id) if request_id else None,
                record.state,
                now.isoformat(),
            ),
        )
        self._db.execute(
            "DELETE FROM audit WHERE seq <= (SELECT COALESCE(MAX(seq),0)-2000 FROM audit)"
        )
        self._db.execute(
            "INSERT INTO meta(name,value) VALUES ('last_observed_at',?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (now.isoformat(),),
        )

    @property
    def last_observed_at(self) -> datetime | None:
        try:
            self._check()
            row = self._db.execute(
                "SELECT value FROM meta WHERE name='last_observed_at'"
            ).fetchone()
            return require_utc(datetime.fromisoformat(row[0])) if row else None
        except (OSError, ValueError, TypeError, sqlite3.Error):
            raise ReminderError("reminder_store_record_invalid") from None

    def _prior(self, request_id: UUID, digest: str, operation: str) -> ReminderRecord | None:
        row = self._db.execute(
            "SELECT digest,reminder_id,operation FROM requests WHERE id=?", (str(request_id),)
        ).fetchone()
        if row is None:
            return None
        if row[0] != digest or row[2] != operation:
            raise ReminderError("reminder_request_conflict")
        return self.get(UUID(row[1]))

    def _request(self, request_id: UUID, digest: str, reminder_id: UUID, operation: str) -> None:
        if self._db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] >= _MAX_REQUESTS:
            raise ReminderError("reminder_store_limit")
        self._db.execute(
            "INSERT INTO requests VALUES (?,?,?,?)",
            (str(request_id), digest, str(reminder_id), operation),
        )

    def prior_create(self, proposal: ReminderCreate) -> ReminderRecord | None:
        try:
            self._check()
            return self._prior(proposal.request_id, proposal.payload_digest, "create")
        except ReminderError:
            raise
        except (OSError, sqlite3.Error, ValueError):
            raise ReminderError("reminder_store_record_invalid") from None

    def reconcile_action(self, request_id: UUID, now: datetime) -> ReminderActionReceipt | None:
        """One indexed request lookup with its current record and exact store owner."""
        if not isinstance(request_id, UUID) or not request_id.int:
            raise ReminderError("reminder_input_invalid")
        try:
            now = require_utc(now)
            self._check()
            rows = self._db.execute(
                "SELECT meta.value, requests.id, requests.digest, requests.reminder_id, "
                "requests.operation, reminders.id, reminders.data FROM meta "
                "LEFT JOIN requests ON requests.id=? "
                "LEFT JOIN reminders ON reminders.id=requests.reminder_id "
                "WHERE meta.name='owner_id' LIMIT 2",
                (str(request_id),),
            ).fetchall()
            if len(rows) != 1 or rows[0][0] != str(self.owner_id):
                raise ReminderError("reminder_store_owner_invalid")
            row = rows[0]
            if row[1] is None:
                if any(value is not None for value in row[2:]):
                    raise ReminderError("reminder_store_request_invalid")
                self._check()
                return None
            if (
                any(not isinstance(value, str) for value in row)
                or row[1] != str(request_id)
                or row[4] not in {"create", "cancel"}
                or row[3] != row[5]
            ):
                raise ReminderError("reminder_store_request_invalid")
            record = self._read_record(row[6])
            if row[3] != str(record.reminder_id) or record.updated_at > now:
                raise ReminderError("reminder_store_request_invalid")
            operation: Literal["create", "cancel"] = row[4]
            expected = (
                record.payload_digest
                if operation == "create"
                else hashlib.sha256(f"cancel:{record.reminder_id}".encode()).hexdigest()
            )
            if row[2] != expected or (operation == "cancel" and record.state != "cancelled"):
                raise ReminderError("reminder_store_request_invalid")
            self._check()
            return ReminderActionReceipt(
                request_id=request_id,
                operation=operation,
                status="existing" if operation == "create" else "unchanged",
                record=record,
                observed_at=now,
            )
        except ReminderError:
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError):
            raise ReminderError("reminder_store_request_invalid") from None

    def create(self, proposal: ReminderCreate, now: datetime) -> ReminderActionReceipt:
        with self._transaction():
            prior = self._prior(proposal.request_id, proposal.payload_digest, "create")
            if prior is not None:
                return ReminderActionReceipt(
                    request_id=proposal.request_id,
                    operation="create",
                    status="existing",
                    record=prior,
                    observed_at=now,
                )
            records = self.list()
            duplicate = next(
                (
                    r
                    for r in records
                    if r.state != "cancelled" and r.payload_digest == proposal.payload_digest
                ),
                None,
            )
            if duplicate is not None:
                self._request(
                    proposal.request_id, proposal.payload_digest, duplicate.reminder_id, "create"
                )
                return ReminderActionReceipt(
                    request_id=proposal.request_id,
                    operation="create",
                    status="existing",
                    record=duplicate,
                    observed_at=now,
                )
            if len(records) >= MAX_REMINDERS:
                raise ReminderError("reminder_store_limit")
            identifier = uuid4()
            record = ReminderRecord(
                reminder_id=identifier,
                request_id=proposal.request_id,
                title=proposal.title,
                due_at=proposal.due_at,
                timezone=proposal.timezone,
                created_at=now,
                updated_at=now,
                payload_digest=proposal.payload_digest,
                notification_identifier=f"satori.reminder.{identifier}",
            )
            self._put(record)
            self._request(proposal.request_id, proposal.payload_digest, identifier, "create")
            self._audit("created", record, now, proposal.request_id)
        return ReminderActionReceipt(
            request_id=proposal.request_id,
            operation="create",
            status="created",
            record=self.get(identifier),
            observed_at=now,
        )

    def cancel(self, reminder_id: UUID, request_id: UUID, now: datetime) -> ReminderActionReceipt:
        digest = hashlib.sha256(f"cancel:{reminder_id}".encode()).hexdigest()
        with self._transaction():
            prior = self._prior(request_id, digest, "cancel")
            if prior is not None:
                return ReminderActionReceipt(
                    request_id=request_id,
                    operation="cancel",
                    status="unchanged",
                    record=prior,
                    observed_at=now,
                )
            record = self.get(reminder_id)
            status: Literal["cancelled", "unchanged"] = (
                "unchanged" if record.state == "cancelled" else "cancelled"
            )
            record = record.model_copy(update={"state": "cancelled", "updated_at": now})
            self._put(record)
            self._request(request_id, digest, reminder_id, "cancel")
            self._audit("cancelled", record, now, request_id)
        return ReminderActionReceipt(
            request_id=request_id,
            operation="cancel",
            status=status,
            record=self.get(reminder_id),
            observed_at=now,
        )

    def mark_due(self, now: datetime) -> tuple[ReminderRecord, ...]:
        due = tuple(r for r in self.list() if r.state == "scheduled" and r.due_at <= now)
        if not due:
            return ()
        with self._transaction():
            for record in due:
                current = record.model_copy(update={"state": "due", "updated_at": now})
                self._put(current)
                self._audit("due", current, now)
        return tuple(self.get(record.reminder_id) for record in due)

    def recover_inflight(self, now: datetime) -> None:
        pending = tuple(r for r in self.list() if r.delivery_state in {"submitting", "cancelling"})
        if pending:
            with self._transaction():
                for record in pending:
                    current = record.model_copy(
                        update={"delivery_state": "unknown", "updated_at": now}
                    )
                    self._put(current)
                    self._audit("delivery_recovered_unknown", current, now)

    def begin_delivery(
        self,
        reminder_id: UUID,
        operation: DeliveryOperation,
        generation: UUID,
        now: datetime,
        *,
        disclose_title: bool = False,
    ) -> ReminderDeliveryRequest:
        with self._transaction():
            record = self.get(reminder_id)
            if operation == "schedule" and (
                record.state == "cancelled"
                or record.delivery_state not in {"not_requested", "unavailable", "absent"}
            ):
                raise ReminderError("reminder_delivery_reconcile_required")
            if operation == "schedule" and record.due_at <= now:
                raise ReminderError("reminder_due_no_reschedule")
            if operation == "cancel" and record.state != "cancelled":
                raise ReminderError("reminder_cancel_required")
            attempt = uuid4()
            current = record.model_copy(
                update={
                    "delivery_attempt_id": attempt,
                    "delivery_operation": operation,
                    "delivery_state": "cancelling"
                    if operation == "cancel"
                    else "submitting"
                    if operation == "schedule"
                    else "unknown",
                    "updated_at": now,
                }
            )
            self._put(current)
            self._audit("delivery_" + operation, current, now)
        return ReminderDeliveryRequest(
            owner_id=self.owner_id,
            generation=generation,
            attempt_id=attempt,
            reminder_id=reminder_id,
            operation=operation,
            notification_identifier=record.notification_identifier,
            due_at=record.due_at,
            payload_digest=record.payload_digest,
            title=record.title if disclose_title else "Напоминание Сатори",
        )

    def accept_delivery(
        self, request: ReminderDeliveryRequest, receipt: ReminderDeliveryReceipt, now: datetime
    ) -> ReminderRecord:
        with self._transaction():
            record = self.get(request.reminder_id)
            if (
                request.owner_id != self.owner_id
                or record.delivery_attempt_id != request.attempt_id
                or record.delivery_operation != request.operation
                or request.payload_digest != record.payload_digest
                or request.notification_identifier != record.notification_identifier
                or request.due_at != record.due_at
                or receipt.attempt_id != request.attempt_id
                or receipt.reminder_id != request.reminder_id
                or receipt.operation != request.operation
                or receipt.payload_digest != record.payload_digest
                or receipt.notification_identifier != record.notification_identifier
            ):
                raise ReminderError("reminder_delivery_stale")
            if receipt.observed_at > now:
                raise ReminderError("reminder_delivery_clock_invalid")
            status = receipt.status
            if request.operation == "schedule" and status not in {
                "scheduled",
                "unknown",
                "unavailable",
            }:
                raise ReminderError("reminder_delivery_invalid")
            if request.operation == "cancel" and status not in {
                "cancelled",
                "absent",
                "unknown",
                "unavailable",
            }:
                raise ReminderError("reminder_delivery_invalid")
            if record.state == "cancelled" and status == "scheduled":
                status = "unknown"
            current = record.model_copy(
                update={
                    "delivery_state": status,
                    "delivery_attempt_id": None,
                    "delivery_operation": None,
                    "updated_at": now,
                }
            )
            self._put(current)
            self._audit("delivery_receipt", current, now)
        return self.get(request.reminder_id)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        for name in ("_file_fd", "_lock_fd", "_directory_fd"):
            descriptor = getattr(self, name)
            if descriptor >= 0:
                os.close(descriptor)
                setattr(self, name, -1)
        self._identity = None
