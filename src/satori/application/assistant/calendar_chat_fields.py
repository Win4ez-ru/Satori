"""Inert, user-authored Calendar fields and bounded natural-language intake."""

# ruff: noqa: RUF001 - intentional Russian input and user-facing text.

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from satori.application.assistant.calendar_actions_contracts import CalendarActionDTO
from satori.application.assistant.calendar_chat_lexing import (
    calendar_clock_spans,
    calendar_date_spans,
    parse_calendar_clock,
    resolve_calendar_day,
)
from satori.application.assistant.calendar_text import CalendarTextClarification

DAY = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\b|"
    r"20\d{2}-\d{2}-\d{2}",
    re.IGNORECASE,
)
QUOTED = re.compile(r'«([^»\n]{1,500})»|"([^"\n]{1,500})"')
WINDOW = re.compile(r"\bс\s+(\d{1,2}):(\d{2})\s+до\s+(\d{1,2}):(\d{2})\b", re.IGNORECASE)
AT = re.compile(r"\bв\s+(\d{1,2}):(\d{2})\b", re.IGNORECASE)
DURATION = re.compile(
    r"\bна\s*(?:(\d{1,4})\s*(мин(?:ут(?:у|ы)?)?|час(?:а|ов)?)|"
    r"(полтора\s*часа|полчаса|час))\b",
    re.IGNORECASE,
)
_COLLOQUIAL = re.compile(r"\bв\s+(\d{1,2})(?::(\d{2}))?\s+(утра|дня|вечера|ночи)\b", re.IGNORECASE)
_PLAIN_AT = re.compile(r"\bв\s+(\d{1,2})(?![\d:])\b", re.IGNORECASE)
_MASK_QUOTED = re.compile(r'«[^»]*(?:»|$)|"[^"]*(?:"|$)')
_INTRO = re.compile(
    r"^(?:сатори[,! ]+)?(?:пожалуйста[,! ]+)?(?:ты\s+)?(?:"
    r"(?P<verb>найди|подбери|предложи|запланируй|поставь|создай|добавь|занеси|внеси|запиши)|"
    r"(?:можешь|можно|давай)\s+(?:мне\s+)?(?P<request>найти|подобрать|предложить|"
    r"запланировать|поставить|создать|добавить|занести|внести|записать|создадим|добавим|"
    r"запланируем|подбер[её]м|найд[её]м))\b",
    re.IGNORECASE,
)
_REQUEST_VERBS = {
    "найти": "найди",
    "найдём": "найди",
    "найдем": "найди",
    "подобрать": "подбери",
    "подберём": "подбери",
    "подберем": "подбери",
    "предложить": "предложи",
    "запланировать": "запланируй",
    "запланируем": "запланируй",
    "поставить": "поставь",
    "создать": "создай",
    "создадим": "создай",
    "добавить": "добавь",
    "добавим": "добавь",
    "занести": "занеси",
    "внести": "внеси",
    "записать": "запиши",
}
_LATER = re.compile(
    r"(?P<separator>[,;.!?\n]\s*|\s+)(?:пожалуйста[,! ]+)?"
    r"(?P<verb>занеси|внеси|запиши|добавь|поставь|создай|запланируй)\b",
    re.IGNORECASE,
)
_META = re.compile(
    r"\b(?:если|например|пример|цитирую|цитата|команда|фраза|объясни|объяснить|"
    r"сказал[аи]?|написал[аи]?|попросил[аи]?|спросил[аи]?|не|ни)\b",
    re.IGNORECASE,
)
_UNCERTAIN = re.compile(
    r"\b(?:не|ни|или|либо|примерно|около|позже|раньше|следующ\w*|ближайш\w*)\b",
    re.IGNORECASE,
)
_CALENDAR = re.compile(r"\bв\s+календар[ье]\b", re.IGNORECASE)
_SCHEDULE = re.compile(
    r"^(?:мы|я)\s+(?:(?P<day>сегодня|завтра|послезавтра|20\d{2}-\d{2}-\d{2})\s+)?"
    r"(?:планируем|планирую|хотим|хочу)\s+(?P<body>.+)$",
    re.IGNORECASE,
)
_DESCRIPTION = re.compile(
    r"^(?P<day>сегодня|завтра|послезавтра|20\d{2}-\d{2}-\d{2})\s+"
    r"(?P<body>(?:созвон|встреча|занятие|при[её]м|тренировка)\b.+)$",
    re.IGNORECASE,
)
_UNSUPPORTED = re.compile(
    r"\b(?:кажд\w*|ежеднев\w*|еженедел\w*|ежемесяч\w*|повтор\w*|приглас\w*|"
    r"участник\w*|гост\w*|напоминани\w*|напомни|предупреди|перенеси|удали|отправь)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_MODIFIER = re.compile(
    r"\b(?:если|или|либо|при\s+условии|после|перед|через|по\s+времени|по\s+местному|"
    r"с\s+половиной|примерно|около|позже|раньше|следующ\w*|ближайш\w*|"
    r"москве|лондону|амстердаму|берлину|UTC|GMT)\b|"
    r"\d+[,.]\d+\s*час",
    re.IGNORECASE,
)


class CalendarChatFields(CalendarActionDTO):
    """RAM-only editable fields. They grant no read, disclosure or effect authority."""

    title: str = Field(default="", max_length=500, repr=False)
    day: str = Field(default="", max_length=32)
    starts_at: str = Field(default="", pattern=r"^(?:\d{2}:\d{2})?$")
    ends_at: str = Field(default="", pattern=r"^(?:\d{2}:\d{2})?$")
    duration_minutes: int | None = Field(default=None, ge=5, le=720)
    mode: Literal["exact", "plan"] = "exact"
    requested_calendar_title: str | None = Field(default=None, min_length=1, max_length=8000)
    needs_clarification: bool = False
    unresolved_fields: tuple[str, ...] = ()
    unsupported_semantics: bool = False
    day_expressions: tuple[str, ...] = Field(default=(), max_length=16)


@dataclass(frozen=True)
class CalendarDirective:
    verb: str
    body: str
    requested_calendar_title: str | None
    extended: bool
    target_ambiguous: bool = False
    title_ambiguous: bool = False


def _masked(text: str) -> str:
    """Keep offsets while excluding quoted/code examples from directive recognition."""
    value = re.sub(r"```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)", lambda m: " " * len(m[0]), text)
    return _MASK_QUOTED.sub(lambda m: " " * len(m[0]), value)


def normalize_calendar_title(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split()).casefold()


def has_unsupported_calendar_semantics(text: str) -> bool:
    """Known unsupported operations/modifiers stay blocked across field repair."""
    outside = _masked(text)
    return bool(
        _UNSUPPORTED.search(outside)
        or _UNSUPPORTED_MODIFIER.search(outside)
        or re.search(
            r"\b(?:два|две|три|четыре|несколько|\d+)\s+"
            r"(?:созвон\w*|встреч\w*|событи\w*|заняти\w*)\b",
            outside,
            re.IGNORECASE,
        )
    )


_GREETING = re.compile(r"^(?:(?:привет|здравствуй|здравствуйте|сатори)[,!?.\s]+)+", re.IGNORECASE)
_EVENT_NOUN = re.compile(
    r"\b(?:созвон|встреч[ауи]?|заняти[ея]|при[её]м|тренировк[ау]|собеседование|совещание)\b",
    re.IGNORECASE,
)
_CONTEXT_PREFIX = re.compile(
    r"^(?:(?:у\s+меня|у\s+нас|мы|я|мне|нам|пожалуйста|планируем|планирую|хочу|хотим|"
    r"запланирован[аоы]?|назначен[аоы]?|будет|будут|есть|ожидается)\b[\s,:—-]*)+",
    re.IGNORECASE,
)


def _description_supported(text: str) -> bool:
    masked = _masked(text)
    return bool(
        (calendar_date_spans(masked) or calendar_clock_spans(masked))
        and (_EVENT_NOUN.search(masked) or len(QUOTED.findall(text)) == 1)
    )


def calendar_directive(text: str) -> CalendarDirective | None:
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 8000:
        return None
    text = text.strip()
    masked = _masked(text)
    greeting = _GREETING.match(masked)
    offset = greeting.end() if greeting is not None else 0
    intro = _INTRO.search(masked[offset:])
    preceding = ""
    if intro is None:
        later = _LATER.search(masked)
        if later is None or _META.search(masked[: later.start()]):
            return None
        # An embedded directive must explicitly address the Calendar surface.
        if not _CALENDAR.search(masked[later.end() :]):
            return None
        verb = later["verb"].casefold()
        preceding = text[: later.start()].strip(" ,;.!?\n")
        # Without punctuation only an explicit scheduling description may bind
        # the later imperative. This is not a global search for command words.
        if not any(
            character in later["separator"] for character in ",;.!?\n"
        ) and not _description_supported(preceding):
            return None
        tail = text[later.end() :].strip()
    else:
        verb = intro["verb"]
        if verb is None:
            verb = _REQUEST_VERBS[intro["request"].casefold()]
        verb = verb.casefold()
        tail = text[offset + intro.end() :].strip()
    target = None
    target_ambiguous = False
    title_ambiguous = False
    marker = _CALENDAR.search(_masked(tail))
    if marker is not None:
        suffix = tail[marker.end() :].strip().rstrip(".!?").strip()
        before = tail[: marker.start()].strip()
        quoted = QUOTED.match(suffix)
        if quoted is not None:
            target = quoted[1] or quoted[2]
            tail = (before + " " + suffix[quoted.end() :]).strip()
        else:
            # A terminal unquoted name is literal. A calendar-first name must
            # have a boundary: comma, quoted name, or a later clarification.
            comma = re.match(r"([^,;]+)[,;]\s*(.+)", suffix)
            temporal = calendar_date_spans(_masked(suffix)) + calendar_clock_spans(_masked(suffix))
            event = _EVENT_NOUN.search(_masked(suffix))
            if comma is not None and not calendar_date_spans(comma[1]):
                target = comma[1].strip()
                tail = (before + " " + comma[2]).strip()
            elif temporal:
                boundaries = [span.start for span in temporal]
                if event is not None:
                    boundaries.append(event.start())
                    title_ambiguous = len(list(_EVENT_NOUN.finditer(_masked(suffix)))) > 1
                boundary = min(boundaries, default=0)
                candidate = suffix[:boundary].strip()
                target = candidate or None
                target_ambiguous = True
                tail = (before + " " + suffix[boundary:]).strip()
            else:
                target = suffix or None
                tail = before
    if preceding and re.fullmatch(
        r"(?:его|её|ее|это|эту\s+встречу|этот\s+созвон|это\s+событие)?[.!?]?", tail, re.IGNORECASE
    ):
        # Preserve the actual description even if its word order is unfamiliar.
        # Field extraction, not a two-sentence template list, decides what is known.
        tail = preceding
    extended = bool(
        preceding
        or marker is not None
        or calendar_clock_spans(_masked(tail))
        or calendar_date_spans(_masked(tail))
    )
    return CalendarDirective(
        verb,
        tail,
        target,
        extended or verb in {"занеси", "внеси", "запиши"},
        target_ambiguous,
        title_ambiguous,
    )


def _qualified_clock(match: re.Match[str]) -> str:
    hour, minute = int(match[1]), int(match[2] or "0")
    part = match[3].casefold()
    if minute > 59:
        return ""
    if part == "утра" and 1 <= hour <= 11:
        pass
    elif part == "дня" and 1 <= hour <= 12:
        hour = hour if hour == 12 else hour + 12
    elif part == "вечера" and 1 <= hour <= 11:
        hour += 12
    elif part == "ночи" and (1 <= hour <= 5 or hour == 12):
        hour %= 12
    else:
        return ""
    return f"{hour:02d}:{minute:02d}"


def _erase(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _title_remainder(value: str) -> str:
    value = value.strip(" ,;.!?\n")
    for _ in range(4):
        old = value
        value = _GREETING.sub("", value).strip(" ,;.!?\n")
        value = _CONTEXT_PREFIX.sub("", value).strip(" ,;.!?\n")
        if old == value:
            break
    return " ".join(value.split()).strip(" ,;.!?")


def calendar_chat_fields(text: str) -> CalendarChatFields:
    """Keep independently known literal fields regardless of their word order."""
    directive = calendar_directive(text)
    if directive is None:
        return CalendarChatFields()
    body = directive.body
    outside = _masked(body)
    days = calendar_date_spans(outside)
    clocks = calendar_clock_spans(outside)
    durations = list(DURATION.finditer(outside))
    issues: set[str] = set()
    unsupported = has_unsupported_calendar_semantics(text)
    dates = tuple(span.value for span in days)
    if len(dates) > 16:
        dates = ()
        issues.add("day")
    day = dates[0] if len(dates) == 1 else ""
    if len(dates) > 1:
        issues.add("day")  # The original civil anchor resolves alias consistency.
    starts = [span for span in clocks if span.role == "start"]
    ends = [span for span in clocks if span.role == "end"]
    start_values = {span.value for span in starts if span.status == "resolved"}
    end_values = {span.value for span in ends if span.status == "resolved"}
    start = next(iter(start_values)) if len(start_values) == 1 else ""
    end = next(iter(end_values)) if len(end_values) == 1 else ""
    if len(starts) > 1 or any(span.status != "resolved" for span in starts):
        start = ""
        issues.add("exact_time")
    if len(ends) > 1 or any(span.status != "resolved" for span in ends):
        end = ""
        if start and int(start[:2]) >= 12 and len(ends) == 1:
            literal = body[ends[0].start : ends[0].end]
            parsed = parse_calendar_clock(re.sub(r"^до\s*", "", literal, flags=re.IGNORECASE))
            if parsed is not None:
                options = [
                    f"{hour:02d}:{parsed[1]:02d}"
                    for hour in {parsed[0] % 12, parsed[0] % 12 + 12}
                    if 5 <= hour * 60 + parsed[1] - (int(start[:2]) * 60 + int(start[3:])) <= 720
                ]
                if len(options) == 1:
                    end = options[0]
        if not end:
            issues.add("exact_time")
    if ends and not starts:
        issues.add("exact_time")
    duration_values = set()
    for match in durations:
        named_duration = re.sub(r"\s+", "", match[3] or "").casefold()
        minutes = (
            90
            if named_duration == "полторачаса"
            else 30
            if named_duration == "полчаса"
            else 60
            if match[3]
            else int(match[1]) * (60 if match[2].casefold().startswith("час") else 1)
        )
        if 5 <= minutes <= 720:
            duration_values.add(minutes)
        else:
            issues.add("duration")
    duration = next(iter(duration_values)) if len(duration_values) == 1 else None
    if len(duration_values) > 1:
        issues.add("duration")
    erased = [(span.start, span.end) for span in days] + [(span.start, span.end) for span in clocks]
    erased += [(match.start(), match.end()) for match in durations]
    # A conjunction physically connecting two recognized clock literals is time
    # syntax, not part of the event title. Multiple clocks still require an
    # explicit field answer; erasing the connector never chooses one of them.
    for left, right in pairwise(clocks):
        connector = outside[left.end : right.start]
        if re.fullmatch(r"[\s,;]*(?:и|или|либо)[\s,;]*", connector, re.IGNORECASE):
            erased.append((left.end, right.start))
    for span in days:
        prefix = re.search(r"\b(?:на|в)\s+$", outside[: span.start], re.IGNORECASE)
        if prefix is not None:
            erased.append((prefix.start(), span.start))
    residual = _title_remainder(_erase(outside, erased))
    quoted = list(QUOTED.finditer(body))
    if len(quoted) == 1:
        title = quoted[0][1] or quoted[0][2]
        if residual:
            unsupported = True
    elif not quoted and not any(char in body for char in '«»"`'):
        title = residual
        if title.casefold() in {
            "на",
            "в",
            "мне",
            "нам",
            "его",
            "её",
            "ее",
            "это",
            "окно",
            "время",
            "свободное время",
            "свободное окно",
        }:
            title = ""
        if re.search(r"\b(?:не|ни)\b", outside, re.IGNORECASE):
            unsupported = True
        if len(list(_EVENT_NOUN.finditer(residual))) > 1 and re.search(
            r"[,;]|\bи\b", residual, re.IGNORECASE
        ):
            unsupported = True
        if re.search(
            r"\b(?:пригласи|отправь|удали|создай|добавь|внеси|запиши|перенеси)\b",
            residual,
            re.IGNORECASE,
        ):
            unsupported = True
    else:
        title = ""
        issues.add("title")
    if len(title) > 500:
        title = ""
        issues.add("title")
    if directive.target_ambiguous:
        issues.add("calendar")
    if directive.title_ambiguous:
        title = ""
        issues.add("title")
    mode: Literal["exact", "plan"] = (
        "plan" if directive.verb in {"найди", "подбери", "предложи"} else "exact"
    )
    if start and end and duration is not None:
        minutes = (int(end[:2]) - int(start[:2])) * 60 + int(end[3:]) - int(start[3:])
        if minutes != duration:
            mode = "plan"
    return CalendarChatFields(
        title=title,
        day=day,
        starts_at=start,
        ends_at=end,
        duration_minutes=duration,
        mode=mode,
        requested_calendar_title=directive.requested_calendar_title,
        needs_clarification=unsupported or bool(issues),
        unresolved_fields=tuple(sorted(issues)),
        unsupported_semantics=unsupported,
        day_expressions=dates,
    )


def calendar_fields_clarification(fields: CalendarChatFields) -> CalendarTextClarification | None:
    if fields.unsupported_semantics or (
        fields.needs_clarification and not fields.unresolved_fields
    ):
        return CalendarTextClarification(
            missing=("unsupported",),
            message=(
                "В поручении есть пока не поддерживаемая часть. "
                "Могу добавить одно обычное событие без повторов и приглашений."
            ),
        )
    issues = set(fields.unresolved_fields)
    if not fields.title.strip() or "title" in issues:
        return CalendarTextClarification(missing=("title",), message="Как назвать событие?")
    if not fields.day or "day" in issues:
        return CalendarTextClarification(missing=("day",), message="На какой день его поставить?")
    if (
        not fields.starts_at
        or "exact_time" in issues
        or (fields.mode == "plan" and not fields.ends_at)
    ):
        return CalendarTextClarification(
            missing=("exact_time",),
            message="До какого времени искать окно?"
            if fields.starts_at and fields.mode == "plan"
            else "Во сколько начало?",
        )
    if "duration" in issues or (
        fields.duration_minutes is None and (fields.mode == "plan" or not fields.ends_at)
    ):
        return CalendarTextClarification(
            missing=("duration",),
            message=(
                "На сколько запланировать событие? Можно назвать длительность или время окончания."
            ),
        )
    if "calendar" in issues:
        return CalendarTextClarification(
            missing=("calendar",), message="В какой календарь добавить событие?"
        )
    return None


def anchor_calendar_fields(
    fields: CalendarChatFields, *, now: datetime, timezone: str
) -> CalendarChatFields:
    """Resolve all date aliases against the original civil anchor, retaining other fields."""
    issues = set(fields.unresolved_fields)
    legacy_unsupported = (
        fields.needs_clarification and not issues and not fields.unsupported_semantics
    )
    if not fields.day and not fields.day_expressions and "day" not in issues:
        return fields
    try:
        if now.tzinfo is None:
            raise ValueError("aware_clock_required")
        anchor = now.astimezone(ZoneInfo(timezone)).date()
        expressions = fields.day_expressions or (fields.day,)
        resolution = resolve_calendar_day(" ; ".join(expressions), anchor_date=anchor)
        if resolution.status != "resolved":
            raise ValueError("unresolved_day")
        issues.discard("day")
        return fields.model_copy(
            update={
                "day": resolution.day,
                "day_expressions": (),
                "unresolved_fields": tuple(sorted(issues)),
                "needs_clarification": fields.unsupported_semantics
                or legacy_unsupported
                or bool(issues),
            }
        )
    except (ValueError, KeyError, OverflowError):
        issues.add("day")
        return fields.model_copy(
            update={"unresolved_fields": tuple(sorted(issues)), "needs_clarification": True}
        )


def is_direct_calendar_create_request(text: str) -> bool:
    """Necessary user-intent predicate, never a read grant or write approval.

    The host must still bind the current task/session/revision, fully resolved
    user fields, fresh selected read and exact action proposal before approval.
    """
    directive = calendar_directive(text)
    if directive is None or directive.verb not in {
        "запланируй",
        "поставь",
        "создай",
        "добавь",
        "занеси",
        "внеси",
        "запиши",
    }:
        return False
    outside = _masked(text)
    if re.search(r"\bпроект\w*\b.*\b(?:python|программ\w*|код\w*)\b", outside, re.IGNORECASE):
        return False
    domain = re.search(
        r"календар|встреч|событи|созвон|при[её]м|занятие|трениров", outside, re.IGNORECASE
    )
    titled_interval = (
        len(QUOTED.findall(text)) == 1
        and calendar_date_spans(outside)
        and calendar_clock_spans(outside)
    )
    if not domain and not titled_interval:
        return False
    fields = calendar_chat_fields(text)
    return (
        fields.mode == "exact"
        and not fields.unsupported_semantics
        and (not fields.needs_clarification or bool(fields.unresolved_fields))
    )
