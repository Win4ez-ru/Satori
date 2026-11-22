"""Conservative deterministic assistant intents; ordinary conversation stays ordinary."""

# ruff: noqa: RUF001 - intentional Russian commands and display text

import re
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.calendar_chat_lexing import (
    calendar_date_spans,
    resolve_calendar_day,
)
from satori.application.assistant.contracts import require_timezone, require_utc
from satori.application.assistant.mail_lookup import parse_mail_lookup_intent
from satori.application.assistant.reminder_contracts import reminder_title
from satori.application.assistant.schedule_contracts import local_instant
from satori.application.assistant.source_answer_intake import parse_source_answer_intent
from satori.application.assistant.telegram_reply_intake import parse_telegram_reply_text

_AGENDA_INTRO = re.compile(
    r"^(?:(?:привет|сатори)[,! ]+){0,2}(?:а )?(?:пожалуйста[, ]+)?"
    r"(?P<request>(?:посмотри|проверь|скажи|подскажи|"
    r"можешь (?:посмотреть|проверить|сказать|подсказать))"
    r"(?: по календарю)?(?:,? пожалуйста)?[, ]+)?"
)
_AGENDA_DAY_PREFIX = re.compile(r"(?:^| )(?:на|в|во) (?:этот |эту |это |ближайш(?:ий|ую|ее) )?$")
_AGENDA_QUERY_MARK = re.compile(r"\?|\bли\b|\bили нет\b")
_EVENTS = r"(?:что[- ](?:то|нибудь|либо)|(?:какие[- ](?:то|нибудь) )?(?:планы|события))"
_PRESENCE = r"(?:есть|будет|будут)"
_AGENDA_QUESTION = re.compile(
    r"(?:в календаре )?(?:"
    r"что (?:у меня(?: запланировано| есть| будет)?|запланировано у меня|в календаре)|"
    r"какие(?: у меня)? планы|покажи(?: мои)? (?:планы|календарь|расписание)|"
    r"(?:мои )?планы|(?:мой )?календарь|расписание|"
    rf"(?P<presence>(?:(?:у меня )?{_PRESENCE}(?: ли)?|{_PRESENCE}(?: ли)? у меня) {_EVENTS}|"
    rf"у меня {_EVENTS}|"
    rf"(?:у меня )?{_EVENTS} (?:{_PRESENCE}|запланирован[оы])))"
    r"(?: по планам| в календаре)?(?: или нет)?\s*[.!?]?"
)


def _natural_agenda_day(text: str, today: date) -> date | None:
    """Resolve one relative day in a bounded personal-calendar question."""

    intro = _AGENDA_INTRO.match(text)
    if intro is None:
        return None
    text = text[intro.end() :]
    spans = calendar_date_spans(text)
    if len(spans) != 1:
        return None
    span = spans[0]
    # Preserve the explicit-date and supported relative-day policies.
    if not span.value.isalpha() or span.value == "послезавтра":
        return None
    before = _AGENDA_DAY_PREFIX.sub("", text[: span.start])
    after = text[span.end :].removeprefix(",")
    question = " ".join(f"{before} {after}".split())
    match = _AGENDA_QUESTION.fullmatch(question)
    if match is None or (
        match["presence"] and not intro["request"] and _AGENDA_QUERY_MARK.search(question) is None
    ):
        return None
    resolved = resolve_calendar_day(span.value, anchor_date=today)
    return date.fromisoformat(resolved.day) if resolved.status == "resolved" else None


def _clarify(
    request_id: UUID,
    timezone: str,
    message: str,
    *,
    title: str | None = None,
    day: date | None = None,
) -> AssistantIntent:
    return AssistantIntent(
        request_id=request_id,
        kind="clarification",
        timezone=timezone,
        clarification=message,
        title=title,
        day=day,
    )


def parse_assistant_intent(
    text: str, *, now: datetime, timezone: str, request_id: UUID | None = None
) -> AssistantIntent | None:
    """Parse only bounded known commands. No I/O, grants, habits or provider calls."""
    now = require_utc(now)
    timezone = require_timezone(timezone)
    request_id = request_id or uuid4()
    reply = parse_telegram_reply_text(text, request_id=request_id, timezone=timezone)
    if reply is not None:
        return reply
    mail_lookup = parse_mail_lookup_intent(text, timezone=timezone, request_id=request_id)
    if mail_lookup is not None:
        return mail_lookup
    source_question = parse_source_answer_intent(
        text, now=now, timezone=timezone, request_id=request_id
    )
    if source_question is not None:
        return source_question
    original = re.sub(r"\s+", " ", text.strip())
    if not original or len(original) > 8000:
        return None
    normalized = re.sub(r"\s+", " ", original.casefold().replace("ё", "е"))
    today = now.astimezone(ZoneInfo(timezone)).date()
    if re.fullmatch(r"(?:покажи |список |мои |посмотри )?(?:напоминания|задачи)[.!?]?", normalized):
        return AssistantIntent(request_id=request_id, kind="reminder_list", timezone=timezone)
    cancel = re.fullmatch(
        r"(?:отмени|удали|отменить) напоминание(?:\s+([0-9a-f-]{36}))?[.!?]?", normalized
    )
    if cancel:
        try:
            selected = UUID(cancel.group(1)) if cancel.group(1) else None
        except ValueError:
            selected = None
        if selected is None:
            return _clarify(
                request_id, timezone, "Выбери напоминание в списке, чтобы отменить его."
            )
        return AssistantIntent(
            request_id=request_id, kind="reminder_cancel", timezone=timezone, reminder_id=selected
        )
    agenda_day = _natural_agenda_day(normalized, today)
    if agenda_day is not None:
        return AssistantIntent(
            request_id=request_id, kind="calendar_agenda", timezone=timezone, day=agenda_day
        )
    gaps = re.fullmatch(
        r"(?:когда(?: у меня)?|покажи(?: мои)?|какие(?: у меня)?|есть(?: ли)? у меня)"
        r"(?: (сегодня|завтра))? (?:свободные окна|свободное окно|свободное время|окна)"
        r"(?: (сегодня|завтра))?[.!?]?",
        normalized,
    )
    if gaps is not None and any(gaps.groups()):
        days = {group for group in gaps.groups() if group}
        if len(days) != 1:
            return _clarify(request_id, timezone, "Укажи один день для просмотра календаря.")
        token = next(iter(days))
        day = today + timedelta(days=1 if token == "завтра" else 0)
        return AssistantIntent(
            request_id=request_id, kind="calendar_gaps", timezone=timezone, day=day
        )
    dated_agenda = re.fullmatch(
        r"(?:что в календаре|покажи календарь|какие планы)(?: на)? (\d{1,2}) "
        r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
        r"(?: (\d{4})(?: года)?)?[.!?]?",
        normalized,
    )
    if dated_agenda:
        if dated_agenda.group(3) is None:
            return _clarify(
                request_id, timezone, "Укажи год календарной даты, чтобы не выбрать его ошибочно."
            )
        try:
            months = [
                "января",
                "февраля",
                "марта",
                "апреля",
                "мая",
                "июня",
                "июля",
                "августа",
                "сентября",
                "октября",
                "ноября",
                "декабря",
            ]
            month = months.index(dated_agenda.group(2)) + 1
            day = date(int(dated_agenda.group(3)), month, int(dated_agenda.group(1)))
        except ValueError:
            return _clarify(request_id, timezone, "Проверь точную календарную дату.")
        return AssistantIntent(
            request_id=request_id, kind="calendar_agenda", timezone=timezone, day=day
        )
    lookup = re.match(
        r"^найди в (?:разрешенной |выбранной )?"
        r"(переписке|чате|телеграме|telegram)\s*[,:]?\s*(.+)$",
        normalized,
    )
    if lookup:
        return AssistantIntent(
            request_id=request_id,
            kind="selected_lookup",
            timezone=timezone,
            source="telegram",
            query=original[lookup.start(2) :][:1000],
        )
    prefix = re.match(
        r"^(?:напомни(?: мне)?|(?:поставь|создай) напоминание)\b\s*[,：:]?\s*", normalized
    )
    if prefix is None:
        return None
    remaining = original[prefix.end() :].strip()
    lower = normalized[prefix.end() :].strip()
    if re.match(r"^(?:что|как|о чем|кто)\b", lower):
        return None
    relative = re.match(
        r"^через\s+(\d{1,5})\s+(минут\w*|час\w*|дн\w*|день)\b\s*[,：:]?\s*(.*)$", lower
    )
    if relative:
        number = int(relative.group(1))
        unit = relative.group(2)
        seconds = number * (
            60 if unit.startswith("минут") else 3600 if unit.startswith("час") else 86400
        )
        title = remaining[relative.start(3) :].strip()
        if not 0 < seconds <= 31 * 86400:
            return _clarify(request_id, timezone, "Укажи срок от минуты до 31 дня или точную дату.")
        try:
            title = reminder_title(title)
        except ValueError:
            return _clarify(request_id, timezone, "Что именно нужно напомнить?")
        return AssistantIntent(
            request_id=request_id,
            kind="reminder_create",
            timezone=timezone,
            title=title,
            due_at=now + timedelta(seconds=seconds),
        )
    day = today
    date_prefix = re.match(
        r"^(сегодня|завтра|послезавтра|\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})\b\s*", lower
    )
    if date_prefix:
        token = date_prefix.group(1)
        try:
            day = (
                today + timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[token])
                if token in {"сегодня", "завтра", "послезавтра"}
                else date.fromisoformat(token)
                if "-" in token
                else datetime.strptime(token, "%d.%m.%Y").date()
            )
        except (ValueError, OverflowError):
            return _clarify(request_id, timezone, "Проверь точную дату напоминания.")
        remaining = remaining[date_prefix.end() :].strip()
        lower = lower[date_prefix.end() :].strip()
    exact = re.match(r"^(?:в\s+)?(\d{1,2}):(\d{2})\b\s*[,：:]?\s*(.*)$", lower)
    if exact is None:
        title = re.sub(
            r"^(?:вечером|утром|дн[её]м|ночью)\b\s*", "", remaining, flags=re.IGNORECASE
        ).strip()
        return _clarify(
            request_id,
            timezone,
            "Укажи точное время, например 19:00. Напоминание ещё не создано.",
            title=title[:500] or None,
            day=day,
        )
    try:
        title = reminder_title(remaining[exact.start(3) :].strip())
        due = local_instant(
            day, time(int(exact.group(1)), int(exact.group(2))), timezone
        ).astimezone(UTC)
    except (ValueError, OverflowError):
        return _clarify(
            request_id,
            timezone,
            "Проверь текст и время. Неоднозначный час нужно уточнить.",
            day=day,
        )
    if due <= now:
        return _clarify(
            request_id,
            timezone,
            "Это время уже прошло. Укажи будущую дату и время.",
            title=title,
            day=day,
        )
    return AssistantIntent(
        request_id=request_id,
        kind="reminder_create",
        timezone=timezone,
        title=title,
        due_at=due,
    )
