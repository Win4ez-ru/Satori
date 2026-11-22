"""Deterministic schedule review, inert export and one-use create policy.

No port in this module can read a calendar, execute a write, contact a provider,
or persist a photo, OCR, event body or canonical interaction.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import (
    CalendarSourceRef,
    LocalPrincipal,
    require_timezone,
    require_utc,
)
from satori.application.assistant.schedule_contracts import (
    MAX_PLAN_LIFETIME,
    MAX_SCHEDULE_BYTES,
    MAX_SCHEDULE_ROWS,
    MAX_SCHEDULE_WINDOW,
    MAX_SHIFT_DURATION,
    ReviewedSchedule,
    ScheduleDraft,
    ScheduleError,
    ScheduleEvent,
    SchedulePlan,
    ScheduleRow,
    ScheduleRowResult,
    ScheduleState,
    require_schedule_title,
)
from satori.application.assistant.schedule_weekly_ocr import (
    draft_from_weekly_ocr as draft_from_weekly_ocr,
)


class _PortableShift(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    title: str = Field(min_length=1, max_length=120, repr=False)
    start: datetime = Field(repr=False)
    end: datetime = Field(repr=False)

    _title = field_validator("title")(require_schedule_title)

    @field_validator("start", "end", mode="before")
    @classmethod
    def explicit_offset_wire(cls, value: object) -> object:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", value)
            is None
        ):
            raise ValueError("schedule_explicit_offset_required")
        return datetime.fromisoformat(value)


class _PortableSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    schema_version: Literal[1]
    time_zone: str
    shifts: tuple[_PortableShift, ...] = Field(
        min_length=1, max_length=MAX_SCHEDULE_ROWS, repr=False
    )

    _zone = field_validator("time_zone")(require_timezone)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schedule_integer_required")
        return value

    @model_validator(mode="after")
    def bounded_explicit_instants(self) -> "_PortableSchedule":
        zone = ZoneInfo(self.time_zone)
        seen: set[tuple[datetime, datetime]] = set()
        for row in self.shifts:
            if not row.title.strip():
                raise ValueError("schedule_title_required")
            for value in (row.start, row.end):
                if (
                    value.tzinfo is None
                    or value.utcoffset() is None
                    or value.microsecond
                    or value.astimezone(zone).utcoffset() != value.utcoffset()
                ):
                    raise ValueError("schedule_offset_zone_mismatch")
            start, end = row.start.astimezone(UTC), row.end.astimezone(UTC)
            if not timedelta(0) < end - start <= MAX_SHIFT_DURATION:
                raise ValueError("schedule_duration_invalid")
            if (start, end) in seen:
                raise ValueError("schedule_duplicate_row")
            seen.add((start, end))
        if max(end for _, end in seen) - min(start for start, _ in seen) > MAX_SCHEDULE_WINDOW:
            raise ValueError("schedule_window_invalid")
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScheduleError("schedule_duplicate_key")
        result[key] = value
    return result


def parse_schedule_json(data: bytes | str) -> ReviewedSchedule:
    """Read the shared Mac/iPhone v1 format; this alone grants no create permission.

    The portable document contains only schema_version, time_zone and shifts with
    title/start/end. Every offset must agree with the IANA zone. Header/legend are
    required for ambiguous photo drafts, not synthesized into the interchange file.
    """

    try:
        raw = data.encode("utf-8") if isinstance(data, str) else data
        if not raw or len(raw) > MAX_SCHEDULE_BYTES:
            raise ScheduleError("schedule_size_invalid")
        text = raw.decode("utf-8")
        json.loads(text, object_pairs_hook=_unique_object)
        portable = _PortableSchedule.model_validate_json(text)
        zone = ZoneInfo(portable.time_zone)
        rows: list[ScheduleRow] = []
        for shift in portable.shifts:
            start, end = shift.start.astimezone(zone), shift.end.astimezone(zone)
            rows.append(
                ScheduleRow(
                    date=start.date(),
                    start=start.time(),
                    end=end.time(),
                    end_date=end.date(),
                    title=shift.title,
                    start_fold=1 if start.fold else 0,
                    end_fold=1 if end.fold else 0,
                )
            )
        return ReviewedSchedule(
            schema="satori-schedule",
            version=1,
            year=rows[0].date.year,
            month=rows[0].date.month,
            timezone=portable.time_zone,
            legend="Exact reviewed JSON",
            reviewed=True,
            rows=tuple(rows),
        )
    except ScheduleError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ScheduleError("schedule_invalid") from error


def dump_schedule_json(schedule: ReviewedSchedule) -> bytes:
    """Produce a portable reviewed v1 file only after the caller's explicit export action."""

    # Revalidate rather than trusting model_construct/model_copy at a transport boundary.
    schedule = _validated(schedule)
    shifts: list[dict[str, str]] = []
    for row in schedule.rows:
        start, end = row.instants(schedule.timezone)
        shifts.append({"title": row.title, "start": start.isoformat(), "end": end.isoformat()})
    encoded = json.dumps(
        {"schema_version": 1, "time_zone": schedule.timezone, "shifts": shifts},
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    parse_schedule_json(encoded)
    return encoded + b"\n"


def _validated(schedule: ReviewedSchedule) -> ReviewedSchedule:
    try:
        return ReviewedSchedule.model_validate_json(schedule.model_dump_json(by_alias=True))
    except (ValueError, TypeError) as error:
        raise ScheduleError("schedule_invalid") from error


_OCR_ROW = re.compile(
    r"(?P<day>\d{4}-\d{2}-\d{2}|\d{1,2})\s+"
    r"(?P<start>\d{2}:\d{2})\s*[-\u2013\u2014]\s*(?P<end>\d{2}:\d{2})"
    r"(?:\s+(?P<end_date>\d{4}-\d{2}-\d{2}))?"
)


def draft_from_ocr(
    lines: Sequence[str], *, year: int | None, month: int | None, timezone: str, legend: str
) -> ScheduleDraft:
    """Recognize only explicit one-row-per-line dates and hours; never reconstruct a grid.

    A missing month/year/legend, an overnight interval without its explicit end date,
    or any complex/unrecognized layout is returned for manual review. OCR never sets
    the reviewed attestation, even when all simple lines match.
    """

    issues: list[str] = ["human_review_required"]
    if (
        type(year) is not int
        or type(month) is not int
        or not 1970 <= year <= 2100
        or not 1 <= month <= 12
    ):
        return ScheduleDraft(ambiguities=("month_year_required", "human_review_required"))
    if not legend.strip():
        return ScheduleDraft(ambiguities=("legend_required", "human_review_required"))
    if len(lines) > 500 or sum(len(line) for line in lines) > 64 * 1024:
        return ScheduleDraft(ambiguities=("ocr_limit_reached", "manual_rows_required"))
    rows: list[ScheduleRow] = []
    for line in lines:
        if not line.strip():
            continue
        match = _OCR_ROW.fullmatch(line.strip())
        if match is None:
            issues.append("unrecognized_layout")
            continue
        day_text = match.group("day")
        try:
            day = (
                date.fromisoformat(day_text)
                if "-" in day_text
                else date(year, month, int(day_text))
            )
            end_date = match.group("end_date") or day.isoformat()
            row = ScheduleRow.model_validate_json(
                json.dumps(
                    {
                        "date": day.isoformat(),
                        "start": match.group("start"),
                        "end": match.group("end"),
                        "end_date": end_date,
                        "title": "Рабочая смена",
                    }
                )
            )
            if day.year != year or day.month != month:
                raise ValueError("month_mismatch")
            row.instants(timezone)
            if len(rows) >= MAX_SCHEDULE_ROWS:
                issues.append("ocr_limit_reached")
                break
            rows.append(row)
        except (ValueError, TypeError):
            issues.append("ambiguous_row")
    if not rows:
        issues.append("manual_rows_required")
    return ScheduleDraft(rows=tuple(rows), ambiguities=tuple(dict.fromkeys(issues)))


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def event_content_token(title: str, start: datetime, end: datetime, time_zone: str) -> str:
    """Portable marker; matching is always additionally scoped to the exact calendar.

    Wire recipe: UTF-8 of satori-shift-v1, UTC start, UTC end and NFC title, joined
    with exactly three LF characters. UTC timestamps use YYYY-MM-DDTHH:MM:SSZ.
    Device-local IDs and IANA spellings never enter this portable marker.
    """

    del time_zone  # An equivalent instant in another IANA alias has the same content identity.
    content = "\n".join(
        (
            "satori-shift-v1",
            start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            unicodedata.normalize("NFC", title),
        )
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def rows_for_target(
    schedule: ReviewedSchedule, principal: LocalPrincipal, target: CalendarSourceRef
) -> tuple[ScheduleEvent, ...]:
    """Portable owned tokens; the encompassing plan binds principal and exact target."""

    # The public DTO is immutable, but callers could bypass validation with model_construct.
    schedule = _validated(schedule)
    del principal, target  # These belong to the one-use plan, not its portable content markers.
    rows: list[ScheduleEvent] = []
    for row in schedule.rows:
        start, end = row.instants(schedule.timezone)
        token = event_content_token(row.title, start, end, schedule.timezone)
        rows.append(
            ScheduleEvent(
                row_id=uuid5(NAMESPACE_URL, "satori-schedule:v1:" + token),
                title=row.title,
                start=start,
                end=end,
                time_zone=schedule.timezone,
                owned_token=token,
            )
        )
    return tuple(rows)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SchedulePlanCoordinator:
    """Sole ephemeral owner of this schedule's one-use write authorization.

    Preparing a preview grants nothing. Only a fully absent duplicate preflight
    followed by consume from an explicit Create gesture authorizes a single dispatch.
    Uncertain delivery keeps the frozen plan for read-only reconciliation and never
    reopens that plan for another write.
    """

    def __init__(
        self, principal: LocalPrincipal, *, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self.principal = principal
        self._clock = clock
        self._plan: SchedulePlan | None = None
        self._state: ScheduleState = "empty"
        self._outcomes: tuple[ScheduleRowResult, ...] = ()
        self._last_now: datetime | None = None

    @property
    def plan(self) -> SchedulePlan | None:
        return self._plan

    @property
    def state(self) -> ScheduleState:
        return self._state

    @property
    def outcomes(self) -> tuple[ScheduleRowResult, ...]:
        return self._outcomes

    def _now(self) -> datetime:
        try:
            now = require_utc(self._clock())
            if self._last_now is not None and now < self._last_now:
                raise ValueError("clock_regressed")
        except (ValueError, TypeError, AttributeError) as error:
            raise ScheduleError("schedule_clock_invalid") from error
        self._last_now = now
        return now

    def _current(self, plan: SchedulePlan, *, fresh: bool = True) -> None:
        if self._plan is None or plan != self._plan or plan.principal != self.principal:
            raise ScheduleError("schedule_stale_plan")
        if fresh and self._now() >= plan.expires_at:
            raise ScheduleError("schedule_plan_expired")

    def prepare(
        self,
        schedule: ReviewedSchedule,
        target: CalendarSourceRef,
        generation: int,
        *,
        writable: bool,
    ) -> SchedulePlan:
        if self._state in {"consumed", "unknown"}:
            raise ScheduleError("schedule_reconcile_required")
        if writable is not True:
            raise ScheduleError("schedule_target_not_writable")
        if type(generation) is not int or generation < 0:
            raise ScheduleError("schedule_generation_invalid")
        now = self._now()
        rows = rows_for_target(schedule, self.principal, target)
        plan_id = uuid4()
        values = {
            "plan_id": str(plan_id),
            "principal": self.principal.model_dump(mode="json"),
            "generation": generation,
            "target": target.model_dump(mode="json"),
            "rows": [row.model_dump(mode="json") for row in rows],
            "created_at": now.isoformat(),
            "expires_at": (now + MAX_PLAN_LIFETIME).isoformat(),
        }
        self._plan = SchedulePlan(
            plan_id=plan_id,
            principal=self.principal,
            generation=generation,
            target=target,
            rows=rows,
            created_at=now,
            expires_at=now + MAX_PLAN_LIFETIME,
            plan_fingerprint=_digest(values),
        )
        self._state = "prepared"
        self._outcomes = ()
        return self._plan

    def _results(
        self, plan: SchedulePlan, outcomes: tuple[ScheduleRowResult, ...], allowed: set[str]
    ) -> None:
        if len(outcomes) != len(plan.rows):
            raise ScheduleError("schedule_result_invalid")
        expected = {(row.row_id, row.owned_token) for row in plan.rows}
        actual = {(row.row_id, row.owned_token) for row in outcomes}
        if actual != expected or any(row.outcome not in allowed for row in outcomes):
            raise ScheduleError("schedule_result_invalid")

    def accept_preflight(self, plan: SchedulePlan, outcomes: tuple[ScheduleRowResult, ...]) -> bool:
        self._current(plan)
        if self._state != "prepared":
            raise ScheduleError("schedule_preflight_invalid")
        self._results(plan, outcomes, {"absent", "owned_exact", "conflict"})
        self._outcomes = outcomes
        ready = all(row.outcome == "absent" for row in outcomes)
        self._state = "ready" if ready else "conflict"
        return ready

    def consume(
        self,
        plan: SchedulePlan,
        *,
        principal: LocalPrincipal,
        generation: int,
        target: CalendarSourceRef,
    ) -> SchedulePlan:
        self._current(plan)
        if self._state != "ready":
            raise ScheduleError("schedule_create_not_ready")
        if principal != plan.principal or generation != plan.generation or target != plan.target:
            raise ScheduleError("schedule_scope_mismatch")
        self._state = "consumed"
        return plan

    def complete(self, plan: SchedulePlan, outcomes: tuple[ScheduleRowResult, ...]) -> None:
        self._current(plan, fresh=False)
        if self._state != "consumed":
            raise ScheduleError("schedule_completion_invalid")
        try:
            self._results(plan, outcomes, {"created"})
        except ScheduleError:
            self._state = "unknown"
            raise
        self._outcomes = outcomes
        self._state = "committed"

    def mark_unknown(self, plan_id: UUID) -> None:
        if self._plan is None or self._plan.plan_id != plan_id:
            raise ScheduleError("schedule_stale_plan")
        if self._state not in {"consumed", "unknown"}:
            raise ScheduleError("schedule_completion_invalid")
        self._state = "unknown"
        self._outcomes = ()

    def restore_unknown(self, plan: SchedulePlan) -> None:
        """Bind a matching journal record to a new preview for read-only reconciliation.

        The caller must match the persisted target and portable row tokens before
        this transition. A restored plan receives no preflight/create permission.
        """

        self._current(plan)
        if self._state != "prepared":
            raise ScheduleError("schedule_restore_invalid")
        self._state = "unknown"
        self._outcomes = ()

    def reconcile(self, plan: SchedulePlan, outcomes: tuple[ScheduleRowResult, ...]) -> None:
        self._current(plan, fresh=False)
        if self._state != "unknown":
            raise ScheduleError("schedule_reconcile_not_ready")
        self._results(plan, outcomes, {"absent", "owned_exact", "conflict", "unknown"})
        self._outcomes = outcomes
        # Unresolved rows must not permit a new plan, either.
        self._state = (
            "unknown" if any(row.outcome == "unknown" for row in outcomes) else "reconciled"
        )

    def invalidate(self) -> None:
        if self._state in {"consumed", "unknown"}:
            self._state = "unknown"
            return
        self._plan = None
        self._outcomes = ()
        self._state = "empty"


def _ics_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def _ics_fold(line: str) -> str:
    chunks: list[str] = []
    current = ""
    size = 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > 75:
            chunks.append(current)
            current = " "
            size = 1
        current += char
        size += width
    chunks.append(current)
    return "\r\n".join(chunks)


def export_schedule_ics(
    schedule: ReviewedSchedule, *, generated_at: datetime | None = None
) -> bytes:
    """Inert RFC 5545 file: UTC instants, no invitations, alarms, recurrence or import."""

    schedule = _validated(schedule)
    stamp = require_utc(generated_at if generated_at is not None else _utc_now()).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Satori//Reviewed Schedule v1//EN"]
    for row in schedule.rows:
        start, end = row.instants(schedule.timezone)
        token = event_content_token(row.title, start, end, schedule.timezone)
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{token}@schedule.satori.local",
                f"DTSTAMP:{stamp}",
                f"DTSTART:{start.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTEND:{end.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                "SUMMARY:" + _ics_text(row.title),
                f"URL:satori-calendar://shift/v1/{token}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_ics_fold(line) for line in lines) + "\r\n").encode("utf-8")
