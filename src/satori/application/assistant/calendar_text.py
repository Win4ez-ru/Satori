"""Conservative calendar task intake; unresolved civil times become clarification."""

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import Field

from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionDTO,
    CalendarActionScope,
)
from satori.application.assistant.calendar_planning_contracts import CalendarPlanningRequest


class CalendarTextClarification(CalendarActionDTO):
    status: Literal["clarification"] = "clarification"
    missing: tuple[str, ...]
    message: str = Field(repr=False)


_WEEKDAYS = ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье")
_INTRO = re.compile(
    r"^(?:сатори[,! ]+)?(?:найди|подбери|предложи|запланируй|поставь|создай|добавь)\b",
    re.IGNORECASE,
)


def planning_request_from_text(
    text: str, *, request_id: UUID, scope: CalendarActionScope, now: datetime
) -> CalendarPlanningRequest | CalendarTextClarification:
    """Support explicit one-day windows; a model cannot supply effect authority.

    Structured callers may provide the same DTO directly. This intake never falls
    through an unsupported effect into ordinary conversational Send.
    """

    missing: list[str] = []
    if len(text) > 8000 or not _INTRO.search(text.strip()):
        return CalendarTextClarification(
            missing=("calendar_request",),
            message="Уточни календарное поручение: что запланировать и когда искать время?",
        )
    zone = ZoneInfo(scope.timezone)
    today = now.astimezone(zone).date()
    days: set[date] = set()
    invalid = False
    for value in re.findall(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", text):
        try:
            days.add(date.fromisoformat(value))
        except ValueError:
            invalid = True
    lowered = text.casefold()
    for word, offset in (("сегодня", 0), ("завтра", 1), ("послезавтра", 2)):
        if re.search(r"\b" + word + r"\b", lowered):
            days.add(today + timedelta(days=offset))
    for index, weekday in enumerate(_WEEKDAYS):
        if re.search(r"\b" + weekday + r"\b", lowered):
            days.add(today + timedelta(days=(index - today.weekday()) % 7))
    if len(days) != 1 or invalid:
        missing.append("day")
    titles = re.findall(r"[«\"]([^»\"\n]{1,500})[»\"]", text)
    if len(titles) != 1:
        missing.append("title")
    window = re.search(r"\bс\s+(\d{1,2}):(\d{2})\s+до\s+(\d{1,2}):(\d{2})\b", lowered)  # noqa: RUF001
    if window is None:
        missing.append("time_window")
    minutes = None
    duration = re.search(r"\bна\s+(\d{1,3})\s*(мин(?:ут(?:у|ы)?)?|час(?:а|ов)?)\b", lowered)  # noqa: RUF001
    if duration:
        minutes = int(duration[1]) * (60 if duration[2].startswith("час") else 1)
    elif re.search(r"\bна\s+полчаса\b", lowered):  # noqa: RUF001
        minutes = 30
    elif re.search(r"\bна\s+час\b", lowered):  # noqa: RUF001
        minutes = 60
    if minutes is None:
        missing.append("duration")
    if missing:
        return CalendarTextClarification(
            missing=tuple(missing),
            message=(
                "Уточни название, день, окно поиска и длительность. "
                "Например: найди «Массаж» завтра с 14:00 до 20:00 на час."  # noqa: RUF001
            ),
        )
    assert window is not None
    assert minutes is not None
    day = next(iter(days))
    try:
        local = (
            datetime.combine(day, time(int(window[1]), int(window[2])), zone),
            datetime.combine(day, time(int(window[3]), int(window[4])), zone),
        )
        for value in local:
            if (
                value.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
                != value.replace(tzinfo=None)
                or value.replace(fold=1).utcoffset() != value.utcoffset()
            ):
                raise ValueError("ambiguous_time")
        if local[1].astimezone(UTC) <= now or day > today + timedelta(days=30):
            raise ValueError("calendar_window_out_of_range")
        return CalendarPlanningRequest(
            request_id=request_id,
            scope=scope,
            user_text=text,
            title=titles[0],
            window_start=local[0].astimezone(UTC),
            window_end=local[1].astimezone(UTC),
            duration_minutes=minutes,
        )
    except ValueError:
        return CalendarTextClarification(
            missing=("exact_time",),
            message=(
                "Нужны однозначные будущие часы в пределах одного дня "
                "и длительность от 5 до 720 минут."
            ),
        )
