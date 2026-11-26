"""Conservative chat intake: exact user times need no model recommendation."""

# ruff: noqa: RUF001 - intentional Russian user-facing text

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionDTO,
    CalendarActionScope,
)
from satori.application.assistant.calendar_chat_fields import (
    CalendarChatFields,
    anchor_calendar_fields,
    calendar_chat_fields,
    calendar_directive,
    calendar_fields_clarification,
)
from satori.application.assistant.calendar_chat_lexing import (
    calendar_clock_spans,
    calendar_date_spans,
)
from satori.application.assistant.calendar_planning_contracts import CalendarPlanningRequest
from satori.application.assistant.calendar_text import (
    CalendarTextClarification,
    planning_request_from_text,
)

_INTRO = re.compile(
    r"^(?:сатори[,! ]+)?(?:пожалуйста[,! ]+)?(?:"
    r"(?P<verb>найди|подбери|предложи|запланируй|поставь|создай|добавь)|"
    r"(?:можешь|можно|давай)\s+(?:мне\s+)?(?P<request>найти|подобрать|предложить|"
    r"запланировать|поставить|создать|добавить|создадим|добавим|запланируем|подбер[её]м|найд[её]м))\b",
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
}
_DAY = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\b|"
    r"20\d{2}-\d{2}-\d{2}",
    re.IGNORECASE,
)
_QUOTED = re.compile(r'«([^»\n]{1,500})»|"([^"\n]{1,500})"')
_DOMAIN = re.compile(
    r"календар|встреч|событи|созвон|при[её]м|занятие|трениров|\bвремя\b|\bокно\b",
    re.IGNORECASE,
)
_WINDOW = re.compile(r"\bс\s+(\d{1,2}):(\d{2})\s+до\s+(\d{1,2}):(\d{2})\b", re.IGNORECASE)
_AT = re.compile(r"\bв\s+(\d{1,2}):(\d{2})\b", re.IGNORECASE)
_DURATION = re.compile(
    r"\bна\s+(?:(\d{1,3})\s*(мин(?:ут(?:у|ы)?)?|час(?:а|ов)?)|(полчаса|час))\b", re.IGNORECASE
)
_EXACT = {"запланируй", "поставь", "создай", "добавь", "занеси", "внеси", "запиши"}

_MUTATION = re.compile(
    r"^(?:(?:привет|сатори|пожалуйста)[,!\s]+)*"
    r"(?:(?:перенеси|перепланируй|сдвинь|измени|отмени|удали)|"
    r"(?:можешь|можно)\s+(?:перенести|перепланировать|сдвинуть|"
    r"изменить|отменить|удалить))\s+(?P<body>.+)$",
    re.IGNORECASE,
)
_CALENDAR_OR_EVENT = re.compile(r"\b(?:календар\w*|событи\w*)\b", re.IGNORECASE)
_APPOINTMENT = re.compile(
    r"\b(?:созвон(?:а|е|у|ом)?|встреч[аиуе]|совещани[ея]|заняти[ея]|при[её]м(?:а|е)?)\b",
    re.IGNORECASE,
)
_NON_EVENT_OBJECT = re.compile(
    r"^(?:файл\w*|папк\w*|код|текст|строк\w*|документ\w*)\b", re.IGNORECASE
)
UNSUPPORTED_EXISTING_EVENT = (
    "Пока могу проверить календарь или добавить одно событие, "
    "но не изменить и не удалить существующее. Ничего не меняла."
)


def is_existing_calendar_mutation(text: str) -> bool:
    """Guard obvious edit commands so free chat cannot imply an effect."""
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 8000:
        return False
    visible = re.sub(r"```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)", " ", text)
    visible = _QUOTED.sub(" CALENDAR_TITLE ", visible)
    match = _MUTATION.fullmatch(visible.strip())
    if match is None:
        return False
    body = match.group("body")
    if _NON_EVENT_OBJECT.match(body):
        return False
    return bool(
        _CALENDAR_OR_EVENT.search(body)
        or (_APPOINTMENT.search(body) and (_DAY.search(body) or calendar_clock_spans(body)))
    )


class CalendarChatIntake(CalendarActionDTO):
    mode: Literal["exact", "plan"]
    request: CalendarPlanningRequest


def is_calendar_chat_request(text: str) -> bool:
    """Recognize only an imperative Calendar task, never an arbitrary Send."""
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 8000:
        return False
    if is_existing_calendar_mutation(text):
        return True
    directive = calendar_directive(text)
    if directive is None:
        return False
    outside_title = _QUOTED.sub('"CALENDAR_TITLE"', text)
    if re.search(r"\bпроект\w*\b.*\b(?:python|программ\w*|код\w*)\b", outside_title, re.IGNORECASE):
        return False
    if _DOMAIN.search(outside_title):
        return True
    verb = directive.verb
    # An arbitrary quoted event title is valid with an explicit scheduling interval.
    # A day alone cannot turn film/weather/place searches into Calendar operations.
    if len(_QUOTED.findall(text)) != 1 or not calendar_date_spans(outside_title):
        return False
    if verb.casefold() in _EXACT:
        # Malformed/approximate clock syntax remains on the local clarification
        # path; recognizing a task never resolves or authorizes its interval.
        return bool(
            calendar_clock_spans(outside_title)
            or re.search(r"(?<!\d)\d{1,4}:\d{1,4}(?!\d)", outside_title)
        )
    return bool(_WINDOW.search(outside_title) and _DURATION.search(outside_title))


def calendar_chat_intake(
    text: str,
    *,
    request_id: UUID,
    scope: CalendarActionScope,
    now: datetime,
    fields: CalendarChatFields | None = None,
) -> CalendarChatIntake | CalendarTextClarification:
    """Recognized tasks clarify locally; explicit form fields never change user_text."""
    if is_existing_calendar_mutation(text):
        return CalendarTextClarification(
            missing=("unsupported",), message=UNSUPPORTED_EXISTING_EVENT
        )
    directive = calendar_directive(text)
    if directive is None:
        return CalendarTextClarification(
            missing=("calendar_request",), message="Уточни календарное поручение."
        )
    try:
        edited = CalendarChatFields.model_validate(
            (fields or calendar_chat_fields(text)).model_dump()
        )
    except (ValueError, TypeError, AttributeError):
        return CalendarTextClarification(
            missing=("calendar_request",), message="Уточни детали одного календарного события."
        )
    edited = anchor_calendar_fields(edited, now=now, timezone=scope.timezone)
    clarification = calendar_fields_clarification(edited)
    if clarification is not None:
        return clarification
    # The synthetic syntax is an internal representation of separately authored fields.
    # The request preserves the literal original input; the title is never interpolated.
    verb = "Поставь" if edited.mode == "exact" else "Подбери"
    interval = (
        f"с {edited.starts_at} до {edited.ends_at}" if edited.ends_at else f"в {edited.starts_at}"
    )
    duration = f" на {edited.duration_minutes} минут" if edited.duration_minutes is not None else ""
    parsed = _legacy_intake(
        f'{verb} "CALENDAR_TITLE" {edited.day} {interval}{duration}',
        request_id=request_id,
        scope=scope,
        now=now,
    )
    if isinstance(parsed, CalendarTextClarification):
        return parsed
    if parsed.mode != edited.mode:
        return CalendarTextClarification(
            missing=("duration",),
            message="Проверь длительность и границы: они должны описывать одно событие.",
        )
    request = CalendarPlanningRequest.model_validate(
        parsed.request.model_copy(update={"user_text": text, "title": edited.title.strip()})
    )
    return CalendarChatIntake(mode=parsed.mode, request=request)


def _legacy_intake(
    text: str,
    *,
    request_id: UUID,
    scope: CalendarActionScope,
    now: datetime,
) -> CalendarChatIntake | CalendarTextClarification:
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 8000:
        return CalendarTextClarification(
            missing=("calendar_request",), message="Уточни короткое календарное поручение."
        )
    intro = _INTRO.search(text.strip())
    if intro is None:
        return CalendarTextClarification(
            missing=("calendar_request",), message="Уточни календарное поручение."
        )
    verb = intro["verb"]
    if verb is None:
        verb = _REQUEST_VERBS[intro["request"].casefold()]
    tail = text.strip()[intro.end() :].strip()
    titles = list(_QUOTED.finditer(tail))
    outside_title = _QUOTED.sub('"CALENDAR_TITLE"', tail)
    if re.search(
        r"\b(?:не|ни|или|либо|примерно|около|позже|раньше|следующ\w*|ближайш\w*)\b",
        outside_title,
        re.IGNORECASE,
    ):
        return CalendarTextClarification(
            missing=("exact_time",), message="Уточни нужное время без отрицаний и альтернатив."
        )
    title: str | None = None
    if len(titles) == 1:
        quoted = titles[0]
        title = quoted[1] if quoted[1] is not None else quoted[2]
        remaining = tail[: quoted.start()] + tail[quoted.end() :]
        if any(character in remaining for character in '«»"'):
            return CalendarTextClarification(
                missing=("title",), message="Уточни одно название события."
            )
        tail = tail[: quoted.start()] + '"CALENDAR_TITLE"' + tail[quoted.end() :]
    elif (
        not titles and not any(c in tail for c in '«»"') and (day := _DAY.search(tail)) is not None
    ):
        title = re.sub(r"\s+в$", "", tail[: day.start()].strip(), flags=re.IGNORECASE)
        title = re.sub(r"^мне\s+", "", title, flags=re.IGNORECASE)
        if title.casefold() in {
            "окно",
            "время",
            "свободное время",
            "свободное окно",
            "на",
            "в",
            "мне",
            "с",
            "для",
        }:
            title = None
        tail = '"CALENDAR_TITLE" ' + tail[day.start() :]
    if title is None or not title.strip() or len(title) > 500 or any(c in tail for c in "«»"):
        return CalendarTextClarification(
            missing=("title",), message="Уточни название события и когда его запланировать."
        )
    normalized = verb + " " + tail
    syntax = tail.replace('"CALENDAR_TITLE"', " ")
    for pattern in (_WINDOW, _AT, _DURATION, _DAY):
        syntax = pattern.sub(" ", syntax)
    syntax = re.sub(r"\b(?:на|в|мне)\b", " ", syntax, flags=re.IGNORECASE)
    if re.search(r"[^\s,?.!]", syntax):
        return CalendarTextClarification(
            missing=("exact_time",),
            message="Укажи один день, точное время и длительность без уточнений.",
        )
    windows, starts, durations = (
        list(_WINDOW.finditer(normalized)),
        list(_AT.finditer(normalized)),
        list(_DURATION.finditer(normalized)),
    )
    duration_clauses = re.sub(
        r"\bна\s+(?=" + _DAY.pattern + ")", "", outside_title, flags=re.IGNORECASE
    )
    if len(re.findall(r"\bна\b", duration_clauses, re.IGNORECASE)) != len(durations):
        return CalendarTextClarification(
            missing=("duration",), message="Укажи длительность целым числом минут или часов."
        )
    if len(windows) > 1 or len(starts) > 1 or len(durations) > 1 or (windows and starts):
        return CalendarTextClarification(
            missing=("exact_time",), message="Укажи один день и один интервал события или поиска."
        )
    exact_verb = verb.casefold() in _EXACT
    inferred_duration = False
    if not windows and len(starts) == len(durations) == 1 and exact_verb:
        start, duration = starts[0], durations[0]
        if duration[3]:
            minutes = 30 if duration[3].casefold() == "полчаса" else 60
        else:
            minutes = int(duration[1]) * (60 if duration[2].casefold().startswith("час") else 1)
        hour, minute = int(start[1]), int(start[2])
        end = hour * 60 + minute + minutes
        if hour > 23 or minute > 59 or end >= 24 * 60:
            return CalendarTextClarification(
                missing=("exact_time",), message="Уточни интервал в пределах одного дня."
            )
        normalized = (
            normalized[: start.start()]
            + f"с {hour:02d}:{minute:02d} до {end // 60:02d}:{end % 60:02d}"
            + normalized[start.end() :]
        )
    elif len(windows) == 1 and not durations and exact_verb:
        # First validate civil endpoints using the existing timezone/DST rules.
        normalized += " на 5 минут"
        inferred_duration = True
    parsed = planning_request_from_text(normalized, request_id=request_id, scope=scope, now=now)
    if isinstance(parsed, CalendarTextClarification):
        return parsed
    elapsed = int((parsed.window_end - parsed.window_start).total_seconds() // 60)
    if inferred_duration and not 5 <= elapsed <= 720:
        return CalendarTextClarification(
            missing=("duration",), message="Длительность события должна быть от 5 до 720 минут."
        )
    update: dict[str, object] = {"user_text": text, "title": title}
    if inferred_duration:
        update["duration_minutes"] = elapsed
    parsed = CalendarPlanningRequest.model_validate(parsed.model_copy(update=update))
    mode: Literal["exact", "plan"] = (
        "exact" if exact_verb and elapsed == parsed.duration_minutes else "plan"
    )
    return CalendarChatIntake(mode=mode, request=parsed)
