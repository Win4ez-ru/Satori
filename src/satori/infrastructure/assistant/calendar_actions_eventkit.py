"""Bounded owned-process EventKit ports, without Qt or a second grant owner.

No helper is located, built or invoked automatically. Production composition must
provide a qualified fixed executable. The native action helper accepts READY/COMMIT
and creates one event; the read port reuses the existing selected-read helper.
"""

import asyncio
import base64
import errno
import hashlib
import json
import os
import signal
import stat
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionError,
    CalendarActionScope,
    CalendarCreateAttempt,
    CalendarLookupReceipt,
    CalendarProviderReceipt,
)
from satori.application.assistant.contracts import (
    MAX_RESULT_BYTES,
    CalendarFailureReason,
    CalendarOccurrence,
    CalendarReadRequest,
    CalendarReadResult,
    CalendarSourceRef,
    require_display_text,
    require_identifier,
    require_timezone,
    require_utc,
)

_MAX_FRAME = 32 * 1024
_MAX_READ_INPUT = 256 * 1024
_MAX_TIMEOUT = 30.0


def _now() -> datetime:
    return datetime.now(UTC)


def _json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_wire_key")
        result[key] = value
    return result


def _parse(raw: bytes) -> object:
    return json.loads(raw, object_pairs_hook=_object, parse_constant=lambda _: _bad_constant())


def _bad_constant() -> object:
    raise ValueError("nonfinite_wire_number")


def _qualified_helper(path: Path | None) -> Path:
    if sys.platform != "darwin" or path is None:
        raise CalendarActionError("calendar_action_provider_unavailable")
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise ValueError("unsafe_helper_path")
        info = path.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or info.st_mode & 0o022
            or not os.access(path, os.X_OK)
        ):
            raise ValueError("unsafe_helper_file")
        for parent in path.parents:
            item = parent.stat()
            sticky_root = item.st_uid == 0 and bool(item.st_mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(item.st_mode)
                or item.st_uid not in {0, os.getuid()}
                or (item.st_mode & 0o022 and not sticky_root)
            ):
                raise ValueError("unsafe_helper_parent")
        return path
    except (OSError, ValueError):
        raise CalendarActionError("calendar_action_provider_unavailable") from None


async def _spawn(
    path: Path, *arguments: str, limit: int = _MAX_FRAME
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        str(path),
        *arguments,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        start_new_session=True,
        limit=limit,
    )


def _fingerprint(path: Path) -> tuple[int, int, int, int, int, int]:
    item = _qualified_helper(path).stat()
    return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_mode, item.st_uid


async def _checked_spawn(
    path: Path, *arguments: str, limit: int = _MAX_FRAME
) -> asyncio.subprocess.Process:
    pin = _fingerprint(path)
    process = await _spawn(path, *arguments, limit=limit)
    try:
        if _fingerprint(path) != pin:
            raise CalendarActionError("calendar_action_provider_unavailable")
        return process
    except BaseException:
        await asyncio.shield(_dispose(process))
        raise


async def _dispose(process: asyncio.subprocess.Process | None) -> None:
    if process is None:
        return
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is None:
        signal_unavailable = False
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError as error:
            if error.errno not in {errno.ESRCH, errno.EPERM}:
                raise
            # Darwin may report EPERM for an already-zombie owned group. Only
            # reaping this exact child can establish completion; denial is not proof.
            signal_unavailable = True
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
        except TimeoutError:
            if signal_unavailable:
                # Do not escalate or change targets after a denied/missing group.
                raise
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError as error:
                if error.errno not in {errno.ESRCH, errno.EPERM}:
                    raise
            await asyncio.wait_for(process.wait(), timeout=1.0)


async def _frame(process: asyncio.subprocess.Process) -> bytes:
    if process.stdout is None:
        raise ValueError("helper_output_missing")
    raw = await process.stdout.readline()
    if not raw or not raw.endswith(b"\n") or len(raw) > _MAX_FRAME:
        raise ValueError("helper_frame_invalid")
    return raw


async def _exit(process: asyncio.subprocess.Process) -> None:
    if process.stdout is None or await process.stdout.read(1):
        raise ValueError("helper_trailing_output")
    if await process.wait() != 0:
        raise ValueError("helper_exit_invalid")


async def _bounded_output(process: asyncio.subprocess.Process, maximum: int) -> bytes:
    if process.stdout is None:
        raise ValueError("helper_output_missing")
    chunks: list[bytes] = []
    total = 0
    while True:
        part = await process.stdout.read(min(8192, maximum + 1 - total))
        if not part:
            return b"".join(chunks)
        total += len(part)
        if total > maximum:
            raise ValueError("helper_output_limit")
        chunks.append(part)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class _ActionReply(_DTO):
    schema_version: Literal[1]
    operation: Literal["prepare", "commit", "lookup"]
    attempt_id: UUID
    idempotency_token: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["ready", "created", "rejected", "unknown", "owned_exact", "absent", "conflict"]
    observed_at: datetime
    nonce: UUID | None = None
    event_id: str | None = Field(default=None, max_length=1024, repr=False)
    exact_fields_verified: bool = False
    fresh_store_verified: bool = False
    remote_sync_verified: Literal[False] = False
    reason: (
        Literal[
            "calendar_action_permission_required",
            "calendar_action_not_writable",
            "calendar_action_scope_mismatch",
            "calendar_action_stale",
            "calendar_action_provider_unavailable",
            "calendar_action_provider_failed",
            "calendar_action_unknown_no_replay",
        ]
        | None
    ) = None

    _utc = field_validator("observed_at")(require_utc)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_integer_required")
        return value

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.status == "ready":
            if self.operation != "prepare" or self.nonce is None or self.nonce.int == 0:
                raise ValueError("ready_binding_invalid")
            if self.event_id is not None or self.reason is not None or self.fresh_store_verified:
                raise ValueError("ready_payload_invalid")
        elif self.nonce is not None:
            raise ValueError("terminal_nonce_invalid")
        if self.status in {"created", "owned_exact"}:
            if not self.event_id or not self.exact_fields_verified or not self.fresh_store_verified:
                raise ValueError("unverified_positive_reply")
            require_identifier(self.event_id)
            if self.reason is not None:
                raise ValueError("positive_reply_reason_invalid")
        elif self.event_id is not None or self.exact_fields_verified:
            raise ValueError("negative_reply_payload_invalid")
        if self.status in {"absent", "conflict"} and not self.fresh_store_verified:
            raise ValueError("fresh_lookup_required")
        return self


class EventKitCalendarActionProvider:
    provider_kind: Literal["macos_eventkit"] = "macos_eventkit"
    external_effects = True

    def __init__(self, helper_path: Path | None = None, *, clock: Callable[[], datetime] = _now):
        self._helper = helper_path
        self._clock = clock

    def _wire(self, attempt: CalendarCreateAttempt, operation: str) -> dict[str, object]:
        attempt = CalendarCreateAttempt.model_validate(attempt)
        if attempt.proposal.scope.provider != "macos_eventkit":
            raise CalendarActionError("calendar_action_scope_mismatch")
        material = attempt.proposal.payload_material()
        return {
            "schema_version": 1,
            "operation": operation,
            "attempt_id": str(attempt.attempt_id),
            "idempotency_token": attempt.idempotency_token,
            "payload_digest": attempt.proposal.payload_digest,
            "payload_json_base64": base64.b64encode(_json(material)).decode("ascii"),
            "claimed_at": attempt.claimed_at.timestamp(),
            "expires_at": attempt.expires_at.timestamp(),
            "read_deadline": (require_utc(self._clock()) + timedelta(seconds=30)).timestamp(),
        }

    def _reply(self, raw: bytes, attempt: CalendarCreateAttempt) -> _ActionReply:
        # JSON-mode validation parses wire UUID/time strings; duplicate keys are checked first.
        _parse(raw)
        reply = _ActionReply.model_validate_json(raw)
        now = require_utc(self._clock())
        if (
            reply.attempt_id != attempt.attempt_id
            or reply.idempotency_token != attempt.idempotency_token
            or reply.payload_digest != attempt.proposal.payload_digest
            or reply.observed_at < attempt.claimed_at
            or reply.observed_at > now + timedelta(seconds=5)
        ):
            raise ValueError("helper_reply_binding_invalid")
        return reply

    def _receipt(
        self, attempt: CalendarCreateAttempt, status: Literal["rejected", "unknown"], reason: str
    ) -> CalendarProviderReceipt:
        return CalendarProviderReceipt.model_validate(
            {
                "attempt_id": attempt.attempt_id,
                "idempotency_token": attempt.idempotency_token,
                "payload_digest": attempt.proposal.payload_digest,
                "provider": "macos_eventkit",
                "status": status,
                "observed_at": self._clock(),
                "reason": reason,
            }
        )

    async def create(
        self, attempt: CalendarCreateAttempt, *, before_effect: Callable[[], None]
    ) -> CalendarProviderReceipt:
        wire = self._wire(attempt, "prepare")
        process: asyncio.subprocess.Process | None = None
        committed = False
        try:
            helper = _qualified_helper(self._helper)
            seconds = min(_MAX_TIMEOUT, (attempt.expires_at - self._clock()).total_seconds())
            if seconds <= 0:
                return self._receipt(attempt, "rejected", "calendar_action_approval_expired")
            async with asyncio.timeout(seconds):
                process = await _checked_spawn(helper, "--calendar-actions-v1")
                if process.stdin is None:
                    raise ValueError("helper_input_missing")
                raw = _json(wire) + b"\n"
                if len(raw) > _MAX_FRAME:
                    raise ValueError("helper_input_limit")
                process.stdin.write(raw)
                await process.stdin.drain()
                ready = self._reply(await _frame(process), attempt)
                if ready.operation != "prepare":
                    raise ValueError("helper_prepare_reply_invalid")
                if ready.status != "ready":
                    if ready.status != "rejected":
                        raise ValueError("helper_prepare_outcome_invalid")
                    await _exit(process)
                    return self._receipt(
                        attempt, "rejected", ready.reason or "calendar_action_provider_failed"
                    )
                if self._clock() >= attempt.expires_at:
                    return self._receipt(attempt, "rejected", "calendar_action_approval_expired")
                # No await between the owner's one-use last-boundary guard and COMMIT write.
                before_effect()
                committed = True
                process.stdin.write(
                    _json(
                        {
                            "schema_version": 1,
                            "operation": "commit",
                            "attempt_id": str(attempt.attempt_id),
                            "idempotency_token": attempt.idempotency_token,
                            "payload_digest": attempt.proposal.payload_digest,
                            "nonce": str(ready.nonce),
                        }
                    )
                    + b"\n"
                )
                await process.stdin.drain()
                process.stdin.close()
                final = self._reply(await _frame(process), attempt)
                if final.operation != "commit" or final.status not in {
                    "created",
                    "rejected",
                    "unknown",
                }:
                    raise ValueError("helper_commit_reply_invalid")
                await _exit(process)
                if self._clock() >= attempt.expires_at:
                    return self._receipt(attempt, "unknown", "calendar_action_unknown_no_replay")
                return CalendarProviderReceipt.model_validate(
                    final.model_dump(exclude={"schema_version", "operation", "nonce"})
                    | {"provider": "macos_eventkit"}
                )
        except asyncio.CancelledError:
            raise
        except CalendarActionError:
            # Preserve a last-boundary policy refusal for the existing owner to classify.
            if process is not None and not committed:
                raise
            return self._receipt(
                attempt,
                "unknown" if committed else "rejected",
                "calendar_action_unknown_no_replay"
                if committed
                else "calendar_action_provider_unavailable",
            )
        except Exception:
            return self._receipt(
                attempt,
                "unknown" if committed else "rejected",
                "calendar_action_unknown_no_replay"
                if committed
                else "calendar_action_provider_failed",
            )
        finally:
            await asyncio.shield(_dispose(process))

    async def lookup(
        self,
        attempt: CalendarCreateAttempt,
        scope: CalendarActionScope,
        *,
        before_read: Callable[[], None],
    ) -> CalendarLookupReceipt:
        wire = self._wire(attempt, "lookup")
        if not attempt.proposal.scope.same_resource(CalendarActionScope.model_validate(scope)):
            raise CalendarActionError("calendar_action_scope_mismatch")
        process: asyncio.subprocess.Process | None = None
        try:
            helper = _qualified_helper(self._helper)
            async with asyncio.timeout(_MAX_TIMEOUT):
                process = await _checked_spawn(helper, "--calendar-actions-v1")
                if process.stdin is None:
                    raise ValueError("helper_input_missing")
                raw = _json(wire) + b"\n"
                if len(raw) > _MAX_FRAME:
                    raise ValueError("helper_input_limit")
                before_read()
                process.stdin.write(raw)
                await process.stdin.drain()
                process.stdin.close()
                reply = self._reply(await _frame(process), attempt)
                if reply.operation != "lookup" or reply.status not in {
                    "owned_exact",
                    "absent",
                    "conflict",
                    "unknown",
                }:
                    raise ValueError("helper_lookup_outcome_invalid")
                await _exit(process)
                return CalendarLookupReceipt.model_validate(
                    reply.model_dump(
                        exclude={
                            "schema_version",
                            "operation",
                            "nonce",
                            "reason",
                            "remote_sync_verified",
                        }
                    )
                    | {"provider": "macos_eventkit"}
                )
        except asyncio.CancelledError:
            raise
        except CalendarActionError:
            raise
        except Exception:
            return CalendarLookupReceipt(
                attempt_id=attempt.attempt_id,
                idempotency_token=attempt.idempotency_token,
                payload_digest=attempt.proposal.payload_digest,
                provider="macos_eventkit",
                status="unknown",
                observed_at=self._clock(),
            )
        finally:
            await asyncio.shield(_dispose(process))


class _ReadEvent(_DTO):
    # Wire mirror of the existing native helper's EventRow, without importing Qt controller.
    source_id: str = Field(min_length=1, max_length=512, repr=False)
    calendar_id: str = Field(min_length=1, max_length=512, repr=False)
    event_id: str = Field(min_length=1, max_length=512, repr=False)
    occurrence_start: datetime = Field(repr=False)
    title: str = Field(max_length=512, repr=False)
    start: datetime = Field(repr=False)
    end: datetime = Field(repr=False)
    all_day: bool
    start_date: date | None
    end_date: date | None
    time_zone: str
    status: Literal["confirmed", "tentative"]

    _ids = field_validator("source_id", "calendar_id", "event_id")(require_identifier)
    _text = field_validator("title")(require_display_text)
    _utc = field_validator("occurrence_start", "start", "end")(require_utc)
    _zone = field_validator("time_zone")(require_timezone)

    @model_validator(mode="after")
    def coherent_time(self) -> Self:
        if self.end <= self.start:
            raise ValueError("read_wire_time_invalid")
        if self.all_day:
            if self.start_date is None or self.end_date is None or self.end_date <= self.start_date:
                raise ValueError("read_wire_all_day_invalid")
        elif self.start_date is not None or self.end_date is not None:
            raise ValueError("read_wire_time_invalid")
        return self


class _ReadReply(_DTO):
    schema_version: Literal[1]
    request_id: UUID
    operation: Literal["read_events"]
    status: Literal["ok", "error"]
    access: Literal[
        "not_determined", "restricted", "denied", "write_only", "full_access", "unknown"
    ]
    error: (
        Literal[
            "invalid_request",
            "permission_required",
            "permission_denied",
            "permission_restricted",
            "permission_request_failed",
            "calendar_unavailable",
            "calendar_scope_mismatch",
            "catalog_too_large",
            "calendar_data_invalid",
            "calendar_read_failed",
            "response_too_large",
            "operation_timeout",
            "platform_unavailable",
        ]
        | None
    )
    observed_at: datetime
    events: tuple[_ReadEvent, ...] | None = Field(default=None, max_length=100, repr=False)
    partial: bool | None = None

    _utc = field_validator("observed_at")(require_utc)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_integer_required")
        return value

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.status == "ok":
            if self.error is not None or self.events is None or self.partial is None:
                raise ValueError("read_wire_payload_invalid")
        elif self.error is None or self.events is not None or self.partial is not None:
            raise ValueError("read_wire_error_invalid")
        return self


class EventKitCalendarReadProvider:
    """Existing explicit read-grant → fixed native read port, never a grant owner."""

    def __init__(self, helper_path: Path | None = None, *, clock: Callable[[], datetime] = _now):
        self._helper = helper_path
        self._clock = clock

    def _failed(
        self, request: CalendarReadRequest, reason: CalendarFailureReason
    ) -> CalendarReadResult:
        return CalendarReadResult(
            request_id=request.request_id,
            observed_at=self._clock(),
            status="unavailable" if reason == "provider_unavailable" else "error",
            reason=reason,
        )

    async def read_events(
        self, request: CalendarReadRequest, *, before_read: Callable[[], None]
    ) -> CalendarReadResult:
        request = CalendarReadRequest.model_validate(request)
        process: asyncio.subprocess.Process | None = None
        try:
            helper = _qualified_helper(self._helper)
            seconds = min(_MAX_TIMEOUT, (request.expires_at - self._clock()).total_seconds())
            if seconds <= 0:
                return self._failed(request, "provider_error")
            raw = _json(
                {
                    "schema_version": 1,
                    "request_id": str(request.request_id),
                    "operation": "read_events",
                    "selected_calendars": [
                        {"source_id": item.account_id, "calendar_id": item.calendar_id}
                        for item in request.resources
                    ],
                    "start": request.starts_at.isoformat(),
                    "end": request.ends_at.isoformat(),
                    "time_zone": request.timezone,
                    "max_events": request.max_events,
                }
            )
            if len(raw) > _MAX_READ_INPUT:
                return self._failed(request, "invalid_response")
            async with asyncio.timeout(seconds):
                process = await _checked_spawn(helper, limit=MAX_RESULT_BYTES + 1)
                if process.stdin is None or process.stdout is None:
                    raise ValueError("helper_pipe_missing")
                before_read()
                process.stdin.write(raw)
                await process.stdin.drain()
                process.stdin.close()
                data = await _bounded_output(process, request.max_bytes)
                if await process.wait() != 0:
                    raise ValueError("read_helper_failed")
                _parse(data)
                reply = _ReadReply.model_validate_json(data)
                if (
                    reply.request_id != request.request_id
                    or not request.created_at
                    <= reply.observed_at
                    <= self._clock() + timedelta(seconds=5)
                    or self._clock() >= request.expires_at
                ):
                    raise ValueError("read_reply_binding_invalid")
                if reply.status == "error" or reply.access != "full_access":
                    return self._negative(request, reply)
                rows = tuple(self._occurrence(item) for item in reply.events or ())
                if len(rows) > request.max_events or any(
                    item.source not in request.resources for item in rows
                ):
                    raise ValueError("read_source_mismatch")
                return CalendarReadResult(
                    request_id=request.request_id,
                    observed_at=reply.observed_at,
                    status="partial" if reply.partial else "ok",
                    occurrences=rows,
                    reason="limit_reached" if reply.partial else None,
                )
        except asyncio.CancelledError:
            raise
        except CalendarActionError:
            return self._failed(request, "provider_unavailable")
        except Exception:
            return self._failed(request, "invalid_response")
        finally:
            await asyncio.shield(_dispose(process))

    def _negative(self, request: CalendarReadRequest, reply: _ReadReply) -> CalendarReadResult:
        reason: CalendarFailureReason
        if reply.access == "not_determined" or reply.error == "permission_required":
            reason = "permission_not_determined"
        elif reply.access == "restricted" or reply.error == "permission_restricted":
            reason = "permission_restricted"
        elif reply.access in {"denied", "write_only"} or reply.error == "permission_denied":
            reason = "permission_denied"
        elif reply.error in {"platform_unavailable", "calendar_unavailable"}:
            reason = "provider_unavailable"
        else:
            reason = "provider_error"
        return CalendarReadResult(
            request_id=request.request_id,
            observed_at=reply.observed_at,
            status="permission_denied"
            if reason.startswith("permission_")
            else "unavailable"
            if reason == "provider_unavailable"
            else "error",
            reason=reason,
        )

    @staticmethod
    def _occurrence(item: _ReadEvent) -> CalendarOccurrence:
        occurrence_key = (
            item.occurrence_start.astimezone(ZoneInfo(item.time_zone)).date().isoformat()
            if item.all_day
            else item.occurrence_start.isoformat()
        )
        identity = json.dumps(
            [item.source_id, item.calendar_id, item.event_id, occurrence_key], ensure_ascii=False
        ).encode()
        return CalendarOccurrence(
            source=CalendarSourceRef(account_id=item.source_id, calendar_id=item.calendar_id),
            event_id=item.event_id,
            occurrence_id=hashlib.sha256(identity).hexdigest(),
            title=item.title,
            starts_at=None if item.all_day else item.start,
            ends_at=None if item.all_day else item.end,
            all_day_start=item.start_date,
            all_day_end=item.end_date,
            timezone=item.time_zone,
            status=item.status,
        )
