"""Bounded literal calendar clocks/dates shared by initial tasks and field answers."""

# ruff: noqa: RUF001 - deliberate Russian literals, no generated instructions.

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

HOUR_WORDS = {
    "ноль": 0,
    "ноля": 0,
    "один": 1,
    "одного": 1,
    "час": 1,
    "два": 2,
    "двух": 2,
    "три": 3,
    "трех": 3,
    "трёх": 3,
    "четыре": 4,
    "четырех": 4,
    "четырёх": 4,
    "пять": 5,
    "пяти": 5,
    "шесть": 6,
    "шести": 6,
    "семь": 7,
    "семи": 7,
    "восемь": 8,
    "восьми": 8,
    "девять": 9,
    "девяти": 9,
    "десять": 10,
    "десяти": 10,
    "одиннадцать": 11,
    "одиннадцати": 11,
    "двенадцать": 12,
    "двенадцати": 12,
}
HOUR_PATTERN = r"(?:\d{1,2}|" + "|".join(sorted(HOUR_WORDS, key=len, reverse=True)) + r")"
_CLOCK_BODY = (
    r"(?P<hour>" + HOUR_PATTERN + r")(?::(?P<minute>\d{2}))?"
    r"(?:\s+час(?:а|ов)?)?(?:\s*(?P<part>утра|дня|вечера|ночи))?"
)
_CLOCK = re.compile(_CLOCK_BODY, re.IGNORECASE)
_CLOCK_SPAN = re.compile(
    r"(?<!\w)(?P<marker>до|в|с)\s*(?P<hour>\d{1,4}|"
    + "|".join(sorted(HOUR_WORDS, key=len, reverse=True))
    + r")(?::(?P<minute>\d{1,4}))?(?:\s+час(?:а|ов)?)?"
    r"(?:\s*(?P<part>утра|дня|вечера|ночи))?(?!\w|:)",
    re.IGNORECASE,
)
_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_WEEKDAYS = ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье")
_CIVIL = r"\d{1,2}\s+(?:" + "|".join(_MONTHS) + r")(?:\s+20\d{2}(?:\s*года)?)?"
_DATE = re.compile(
    r"(?<!\w)(?:20\d{2}-\d{2}-\d{2}|"
    + _CIVIL
    + r"|послезавтра|завтра|сегодня|"
    + "|".join(_WEEKDAYS)
    + r")(?!\w)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CalendarLiteralSpan:
    start: int
    end: int
    value: str


@dataclass(frozen=True)
class CalendarClockSpan:
    start: int
    end: int
    value: str
    role: Literal["start", "end"]
    status: Literal["resolved", "ambiguous", "invalid"]


@dataclass(frozen=True)
class CalendarDayResolution:
    status: Literal["missing", "resolved", "ambiguous", "invalid"]
    day: str
    spans: tuple[CalendarLiteralSpan, ...]


def parse_calendar_clock(text: str) -> tuple[int, int, bool] | None:
    """Parse a literal clock body, with no inference from unrelated state."""
    if not isinstance(text, str) or len(text) > 128:
        return None
    match = _CLOCK.fullmatch(text.strip())
    if match is None:
        return None
    value = match["hour"].casefold()
    hour = int(value) if value.isdecimal() else HOUR_WORDS[value]
    minute = int(match["minute"] or "0")
    if hour > 23 or minute > 59:
        return None
    part = (match["part"] or "").casefold()
    explicit = bool(part or match["minute"] is not None or hour == 0 or hour > 12)
    if part:
        if part == "утра" and 1 <= hour <= 11:
            pass
        elif part == "дня" and 1 <= hour <= 12:
            hour = hour if hour == 12 else hour + 12
        elif part == "вечера" and 1 <= hour <= 11:
            hour += 12
        elif part == "ночи" and (1 <= hour <= 5 or hour == 12):
            hour %= 12
        else:
            return None
    return hour, minute, explicit


def calendar_clock_spans(text: str) -> tuple[CalendarClockSpan, ...]:
    if not isinstance(text, str) or len(text) > 8000:
        return ()
    values = []
    for match in _CLOCK_SPAN.finditer(text):
        body = text[match.start("hour") : match.end()]
        clock = parse_calendar_clock(body)
        role: Literal["start", "end"] = "end" if match["marker"].casefold() == "до" else "start"
        status: Literal["resolved", "ambiguous", "invalid"] = (
            "invalid" if clock is None else "resolved" if clock[2] else "ambiguous"
        )
        value = f"{clock[0]:02d}:{clock[1]:02d}" if clock is not None and clock[2] else ""
        values.append(CalendarClockSpan(match.start(), match.end(), value, role, status))
    return tuple(values)


def calendar_date_spans(text: str) -> tuple[CalendarLiteralSpan, ...]:
    if not isinstance(text, str) or len(text) > 8000:
        return ()
    return tuple(CalendarLiteralSpan(m.start(), m.end(), m[0]) for m in _DATE.finditer(text))


def _resolve_literal(value: str, anchor: date) -> date | None:
    value = " ".join(value.casefold().split())
    if value in {"сегодня", "завтра", "послезавтра"}:
        return anchor + timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[value])
    if value in _WEEKDAYS:
        return anchor + timedelta(days=(_WEEKDAYS.index(value) - anchor.weekday()) % 7)
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    match = re.fullmatch(r"(\d{1,2})\s+(\w+)(?:\s+(20\d{2})(?:\s*года)?)?", value)
    if match is None or match[2] not in _MONTHS:
        return None
    day, month = int(match[1]), _MONTHS[match[2]]
    if match[3]:
        return date(int(match[3]), month, day)
    candidates = []
    for year in (anchor.year, anchor.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if 0 <= (candidate - anchor).days <= 30:
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else None


def resolve_calendar_day(text: str, *, anchor_date: date) -> CalendarDayResolution:
    """Consistent aliases resolve once against the command's original civil date."""
    spans = calendar_date_spans(text)
    if not spans:
        return CalendarDayResolution("missing", "", spans)
    values = set()
    try:
        for span in spans:
            resolved = _resolve_literal(span.value, anchor_date)
            if resolved is None:
                return CalendarDayResolution("invalid", "", spans)
            values.add(resolved)
    except (ValueError, OverflowError):
        return CalendarDayResolution("invalid", "", spans)
    if len(values) != 1:
        return CalendarDayResolution("ambiguous", "", spans)
    return CalendarDayResolution("resolved", next(iter(values)).isoformat(), spans)
