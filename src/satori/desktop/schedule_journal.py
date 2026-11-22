"""Minimal pending-import metadata; never event bodies, dates, titles, photos or OCR."""

import json
import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import CalendarSourceRef, LocalPrincipal
from satori.application.assistant.schedule_contracts import ScheduleError, SchedulePlan

_NAME = "calendar-import-pending.json"
_MAX_BYTES = 64 * 1024


class ScheduleJournalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    schema_version: Literal[1]
    plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    target: CalendarSourceRef = Field(repr=False)
    row_tokens: tuple[str, ...] = Field(min_length=1, max_length=100, repr=False)
    principal: LocalPrincipal
    state: Literal["pending", "unknown"]

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schedule_journal_invalid")
        return value

    @model_validator(mode="after")
    def exact_tokens(self) -> "ScheduleJournalRecord":
        if len(set(self.row_tokens)) != len(self.row_tokens) or any(
            len(token) != 64 or any(char not in "0123456789abcdef" for char in token)
            for token in self.row_tokens
        ):
            raise ValueError("schedule_journal_invalid")
        return self


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("schedule_journal_invalid")
        value[key] = item
    return value


class ScheduleJournal:
    """One bounded unresolved import per profile directory, created only at Create.

    A fully written/fsynced private temporary file is linked into place with atomic
    no-replace semantics. An existing record, symlink or malformed file is never
    overwritten. Reads and clears use a directory descriptor and no-follow opens.
    """

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().absolute()

    def _directory_fd(self, *, create: bool = False) -> int | None:
        try:
            if create:
                self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            return os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            if not create:
                return None
            raise ScheduleError("schedule_journal_unavailable") from None
        except OSError:
            raise ScheduleError("schedule_journal_unavailable") from None

    def _read(self, directory_fd: int) -> tuple[ScheduleJournalRecord, os.stat_result] | None:
        try:
            descriptor = os.open(
                _NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise ScheduleError("schedule_journal_unavailable") from None
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or metadata.st_uid != os.getuid()
                or not 0 < metadata.st_size <= _MAX_BYTES
            ):
                raise ScheduleError("schedule_journal_invalid")
            content = bytearray()
            while len(content) <= _MAX_BYTES:
                block = os.read(descriptor, min(8192, _MAX_BYTES + 1 - len(content)))
                if not block:
                    break
                content.extend(block)
            if len(content) > _MAX_BYTES or len(content) != metadata.st_size:
                raise ScheduleError("schedule_journal_invalid")
            json.loads(content, object_pairs_hook=_unique)
            record = ScheduleJournalRecord.model_validate_json(content)
            return record, metadata
        except ScheduleError:
            raise
        except (ValueError, TypeError, RecursionError):
            raise ScheduleError("schedule_journal_invalid") from None
        except OSError:
            raise ScheduleError("schedule_journal_unavailable") from None
        finally:
            os.close(descriptor)

    def pending(self) -> ScheduleJournalRecord | None:
        directory_fd = self._directory_fd()
        if directory_fd is None:
            return None
        try:
            value = self._read(directory_fd)
            return value[0] if value is not None else None
        finally:
            os.close(directory_fd)

    def begin(self, plan: SchedulePlan) -> None:
        """Persist before stdin may reach EventKit. Any existing record blocks replay."""

        record = ScheduleJournalRecord(
            schema_version=1,
            plan_fingerprint=plan.plan_fingerprint,
            target=plan.target,
            row_tokens=tuple(row.owned_token for row in plan.rows),
            principal=plan.principal,
            state="pending",
        )
        content = (record.model_dump_json() + "\n").encode("utf-8")
        directory_fd = self._directory_fd(create=True)
        assert directory_fd is not None
        temporary = ".calendar-import-" + uuid4().hex + ".tmp"
        temporary_created = False
        try:
            if self._read(directory_fd) is not None:
                raise ScheduleError("schedule_journal_pending")
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            temporary_created = True
            try:
                os.fchmod(descriptor, 0o600)
                offset = 0
                while offset < len(content):
                    written = os.write(descriptor, content[offset:])
                    if written <= 0:
                        raise OSError("short_write")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            # link fails if ANY entry already exists, including a symlink inserted
            # after the read. It never follows or replaces the target entry.
            os.link(
                temporary,
                _NAME,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            os.unlink(temporary, dir_fd=directory_fd)
            temporary_created = False
            os.fsync(directory_fd)
        except ScheduleError:
            raise
        except OSError:
            raise ScheduleError("schedule_journal_unavailable") from None
        finally:
            if temporary_created:
                with suppress(OSError):
                    os.unlink(temporary, dir_fd=directory_fd)
            os.close(directory_fd)

    def clear(self, plan: SchedulePlan) -> None:
        """Clear only this resolved target/token set; new session fingerprints may differ."""

        directory_fd = self._directory_fd()
        if directory_fd is None:
            raise ScheduleError("schedule_journal_invalid")
        try:
            found = self._read(directory_fd)
            if found is None:
                raise ScheduleError("schedule_journal_invalid")
            record, previous = found
            if record.target != plan.target or set(record.row_tokens) != {
                row.owned_token for row in plan.rows
            }:
                raise ScheduleError("schedule_journal_mismatch")
            current = os.stat(_NAME, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
                previous.st_dev,
                previous.st_ino,
            ):
                raise ScheduleError("schedule_journal_unavailable")
            # All fallible preparation precedes the atomic unlink. If this sync
            # fails, the unresolved record remains. A crash can conservatively
            # resurrect an already resolved record; it cannot authorize a write.
            os.fsync(directory_fd)
            os.unlink(_NAME, dir_fd=directory_fd)
        except ScheduleError:
            raise
        except OSError:
            raise ScheduleError("schedule_journal_unavailable") from None
        finally:
            os.close(directory_fd)
