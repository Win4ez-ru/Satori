"""Bounded, transient reviewed schedules; never canonical conversation or memory."""

from datetime import UTC, datetime, time, timedelta
from datetime import date as Date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import (
    CalendarSourceRef,
    LocalPrincipal,
    require_display_text,
    require_timezone,
    require_utc,
)

MAX_SCHEDULE_ROWS = 100
MAX_SCHEDULE_BYTES = 256 * 1024
MAX_SHIFT_DURATION = timedelta(hours=36)
MAX_SCHEDULE_WINDOW = timedelta(days=62)
MAX_PLAN_LIFETIME = timedelta(minutes=5)
ScheduleOutcome = Literal["absent", "owned_exact", "conflict", "created", "unknown"]
ScheduleState = Literal[
    "empty", "prepared", "ready", "conflict", "consumed", "unknown", "committed", "reconciled"
]


class ScheduleError(ValueError):
    """A content-free failure code suitable for a local UI."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _ScheduleDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


def _minute(value: time) -> time:
    if value.tzinfo is not None or value.microsecond:
        raise ValueError("schedule_whole_second_required")
    return value


def _token(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("schedule_token_invalid")
    return value


def require_schedule_title(value: str) -> str:
    require_display_text(value)
    if not value.strip() or any(127 <= ord(char) <= 159 for char in value):
        raise ValueError("schedule_title_invalid")
    return value


def local_instant(day: Date, value: time, zone: str, fold: int | None = None) -> datetime:
    """Reject nonexistent civil times and require explicit choice for a repeated hour."""

    _minute(value)
    naive = datetime.combine(day, value)
    timezone = ZoneInfo(require_timezone(zone))
    candidates: dict[int, datetime] = {}
    for candidate_fold in (0, 1):
        candidate = naive.replace(tzinfo=timezone, fold=candidate_fold)
        restored = candidate.astimezone(UTC).astimezone(timezone)
        if restored.replace(tzinfo=None) == naive and restored.fold == candidate_fold:
            candidates[candidate_fold] = candidate
    if not candidates:
        raise ValueError("schedule_nonexistent_local_time")
    if len(candidates) > 1 and fold is None:
        raise ValueError("schedule_ambiguous_local_time")
    chosen = fold if fold is not None else next(iter(candidates))
    if chosen not in candidates:
        raise ValueError("schedule_fold_invalid")
    return candidates[chosen]


class ScheduleRow(_ScheduleDTO):
    date: Date = Field(repr=False)
    start: time = Field(repr=False)
    end: time = Field(repr=False)
    end_date: Date = Field(repr=False)
    title: str = Field(min_length=1, max_length=120, repr=False)
    start_fold: Literal[0, 1] | None = None
    end_fold: Literal[0, 1] | None = None

    _times = field_validator("start", "end")(_minute)
    _title = field_validator("title")(require_schedule_title)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("schedule_title_required")
        return value

    @field_validator("start_fold", "end_fold", mode="before")
    @classmethod
    def exact_fold(cls, value: object) -> object:
        if value is not None and type(value) is not int:
            raise ValueError("schedule_fold_invalid")
        return value

    def instants(self, timezone: str) -> tuple[datetime, datetime]:
        starts = local_instant(self.date, self.start, timezone, self.start_fold)
        ends = local_instant(self.end_date, self.end, timezone, self.end_fold)
        duration = ends.astimezone(UTC) - starts.astimezone(UTC)
        if not timedelta(0) < duration <= MAX_SHIFT_DURATION:
            raise ValueError("schedule_duration_invalid")
        return starts, ends


class ReviewedSchedule(_ScheduleDTO):
    schema_name: Literal["satori-schedule"] = Field(alias="schema")
    version: Literal[1]
    year: int = Field(ge=1970, le=2100)
    month: int = Field(ge=1, le=12)
    timezone: str
    legend: str = Field(min_length=1, max_length=2048, repr=False)
    reviewed: Literal[True]
    rows: tuple[ScheduleRow, ...] = Field(min_length=1, max_length=MAX_SCHEDULE_ROWS, repr=False)

    _zone = field_validator("timezone")(require_timezone)
    _legend = field_validator("legend")(require_display_text)

    @field_validator("version", "year", "month", mode="before")
    @classmethod
    def exact_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schedule_integer_required")
        return value

    @field_validator("reviewed", mode="before")
    @classmethod
    def explicit_review(cls, value: object) -> object:
        if value is not True:
            raise ValueError("schedule_review_required")
        return value

    @model_validator(mode="after")
    def exact_bounded_rows(self) -> "ReviewedSchedule":
        if not self.legend.strip():
            raise ValueError("schedule_legend_required")
        seen: set[tuple[datetime, datetime]] = set()
        bounds: list[tuple[datetime, datetime]] = []
        for row in self.rows:
            start, end = row.instants(self.timezone)
            pair = (start.astimezone(UTC), end.astimezone(UTC))
            if pair in seen:
                raise ValueError("schedule_duplicate_row")
            seen.add(pair)
            bounds.append(pair)
        if max(end for _, end in bounds) - min(start for start, _ in bounds) > MAX_SCHEDULE_WINDOW:
            raise ValueError("schedule_window_invalid")
        return self


class ScheduleDraft(_ScheduleDTO):
    """Suggestions only; any OCR path must still pass explicit human review."""

    rows: tuple[ScheduleRow, ...] = Field(default=(), max_length=MAX_SCHEDULE_ROWS, repr=False)
    ambiguities: tuple[str, ...] = Field(default=(), max_length=MAX_SCHEDULE_ROWS + 4)
    year: int | None = Field(default=None, ge=1970, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)


class ScheduleOCRBounds(_ScheduleDTO):
    """Vision normalized coordinates with the origin at the bottom left."""

    model_config = ConfigDict(allow_inf_nan=False)

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @field_validator("x", "y", "width", "height", mode="before")
    @classmethod
    def exact_numeric(cls, value: object) -> object:
        if type(value) not in {int, float}:
            raise ValueError("schedule_ocr_bounds_invalid")
        return value

    @model_validator(mode="after")
    def contained(self) -> "ScheduleOCRBounds":
        if self.x + self.width > 1 + 1e-9 or self.y + self.height > 1 + 1e-9:
            raise ValueError("schedule_ocr_bounds_invalid")
        return self


class ScheduleOCRObservation(_ScheduleDTO):
    model_config = ConfigDict(allow_inf_nan=False)

    text: str = Field(min_length=1, max_length=512, repr=False)
    confidence: float = Field(ge=0, le=1)
    bounds: ScheduleOCRBounds = Field(repr=False)

    _text = field_validator("text")(require_display_text)

    @field_validator("confidence", mode="before")
    @classmethod
    def exact_numeric(cls, value: object) -> object:
        if type(value) not in {int, float}:
            raise ValueError("schedule_ocr_confidence_invalid")
        return value


class ScheduleEvent(_ScheduleDTO):
    row_id: UUID
    title: str = Field(min_length=1, max_length=120, repr=False)
    start: datetime = Field(repr=False)
    end: datetime = Field(repr=False)
    time_zone: str
    owned_token: str = Field(min_length=64, max_length=64, repr=False)

    _title = field_validator("title")(require_schedule_title)
    _zone = field_validator("time_zone")(require_timezone)
    _owned = field_validator("owned_token")(_token)

    @model_validator(mode="after")
    def bounded_time(self) -> "ScheduleEvent":
        if any(
            value.tzinfo is None or value.utcoffset() is None or value.microsecond
            for value in (self.start, self.end)
        ):
            raise ValueError("schedule_exact_offset_second_required")
        if (
            not timedelta(0)
            < self.end.astimezone(UTC) - self.start.astimezone(UTC)
            <= MAX_SHIFT_DURATION
        ):
            raise ValueError("schedule_duration_invalid")
        if not self.title.strip() or self.row_id.int == 0:
            raise ValueError("schedule_event_invalid")
        return self


class SchedulePlan(_ScheduleDTO):
    plan_id: UUID
    principal: LocalPrincipal
    generation: int = Field(ge=0)
    target: CalendarSourceRef = Field(repr=False)
    rows: tuple[ScheduleEvent, ...] = Field(min_length=1, max_length=MAX_SCHEDULE_ROWS, repr=False)
    created_at: datetime
    expires_at: datetime
    plan_fingerprint: str = Field(min_length=64, max_length=64, repr=False)

    _dates = field_validator("created_at", "expires_at")(require_utc)
    _fingerprint = field_validator("plan_fingerprint")(_token)

    @model_validator(mode="after")
    def coherent_plan(self) -> "SchedulePlan":
        if not timedelta(0) < self.expires_at - self.created_at <= MAX_PLAN_LIFETIME:
            raise ValueError("schedule_plan_lifetime_invalid")
        if len({row.row_id for row in self.rows}) != len(self.rows) or len(
            {row.owned_token for row in self.rows}
        ) != len(self.rows):
            raise ValueError("schedule_duplicate_row")
        return self


class ScheduleRowResult(_ScheduleDTO):
    row_id: UUID
    owned_token: str = Field(min_length=64, max_length=64, repr=False)
    outcome: ScheduleOutcome

    _owned = field_validator("owned_token")(_token)
