"""RAM-only answers to the current Calendar clarification; no effect authority."""

# ruff: noqa: RUF001 - intentional Russian input and user-facing clarification.

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from satori.application.assistant.calendar_chat_fields import (
    QUOTED,
    CalendarChatFields,
    calendar_directive,
    calendar_fields_clarification,
    has_unsupported_calendar_semantics,
)
from satori.application.assistant.calendar_chat_lexing import (
    calendar_clock_spans,
    calendar_date_spans,
    parse_calendar_clock,
    resolve_calendar_day,
)

_CANCEL = re.compile(
    r"^(?:отмена|отмени|отменяем|не\s+надо|не\s+нужно|не\s+добавляй|не\s+создавай|"
    r"не\s+записывай|не\s+заноси|не\s+вноси|не\s+ставь|не\s+планируй|нет)(?:\b|[.!?])",
    re.IGNORECASE,
)
_ORDINARY = re.compile(
    r"^(?:привет|расскажи|объясни|покажи|почему|зачем|что\b|как\b|где\b|кто\b|"
    r"спасибо|поговорим|какая\b|какой\b|какие\b)\b",
    re.IGNORECASE,
)
_UNRELATED_SENTENCE = re.compile(
    r"^(?:(?:я|мы|мне|нам|у\s+меня|у\s+нас|сейчас|сегодня|кстати|вообще|слушай)\s+\S|"
    r"(?:рассказать|поговорить|обсудить)\b)",
    re.IGNORECASE,
)
_TASK_REFERENCE = re.compile(
    r"^(?:(?:верн[её]мся|возвращаясь)\s+)?"
    r"(?:(?:к|по)\s+(?:(?:этому|тому|нашему)\s+)?"
    r"(?:созвону|событию|поручению|встрече)|"
    r"для\s+(?:(?:этого|того|нашего)\s+)?(?:созвона|события|поручения|встречи)|"
    r"я\s+(?:про|о)\s+(?:этот\s+|тот\s+)?(?:созвон|событие|поручение|встречу))"
    r"\b\s*[,—:;-]?\s*",
    re.IGNORECASE,
)
_DATE_CORRECTION = re.compile(
    r"^(?:(?:я|мы)\s+(?:же\s+)?(?:уже\s+)?"
    r"(?:написал[аи]?|сказал[аи]?|говорил[аи]?|указал[аи]?)[,:]?\s*(?:что\s+)?|"
    r"(?:я|мы)\s+имел[аи]?\s+в\s+виду\s+|"
    r"(?:дата|день)(?:\s+события)?\s*[:—-]?\s*)",
    re.IGNORECASE,
)


def calendar_followup_reference(text: str) -> tuple[str, bool]:
    """Recognize a literal reference to the paused task, without granting anything."""
    value = text.strip()
    if value.casefold().strip(".!?") in {"продолжи", "продолжить"}:
        return value, True
    match = _TASK_REFERENCE.match(value)
    return (value[match.end() :], True) if match else (text, False)


def calendar_followup_is_date_correction(text: str) -> bool:
    return _DATE_CORRECTION.match(text.strip()) is not None and bool(calendar_date_spans(text))


@dataclass(frozen=True)
class CalendarPendingClarification:
    task_id: UUID
    session_id: str
    fields: CalendarChatFields
    waiting_for: str
    expires_at_monotonic: float
    timezone: str = "Europe/Moscow"
    anchor_date: date | None = None


@dataclass(frozen=True)
class CalendarFollowupResult:
    status: Literal["updated", "clarification", "cancelled", "expired"]
    fields: CalendarChatFields
    message: str


def _question(
    pending: CalendarPendingClarification, message: str | None = None
) -> CalendarFollowupResult:
    issue = calendar_fields_clarification(pending.fields)
    return CalendarFollowupResult(
        "clarification",
        pending.fields,
        message or (issue.message if issue else "Уточни, что именно изменить в этом событии."),
    )


def _updated(pending: CalendarPendingClarification, **changes: object) -> CalendarFollowupResult:
    try:
        values = pending.fields.model_dump()
        values.update(changes)
        repaired = {
            "title": "title",
            "day": "day",
            "starts_at": "exact_time",
            "ends_at": "duration",
            "duration_minutes": "duration",
            "requested_calendar_title": "calendar",
        }
        unresolved = tuple(
            field
            for field in pending.fields.unresolved_fields
            if field not in {repaired[name] for name in changes if name in repaired}
        )
        values["unresolved_fields"] = unresolved
        if pending.fields.unresolved_fields:
            values["needs_clarification"] = bool(unresolved or pending.fields.unsupported_semantics)
        fields = CalendarChatFields.model_validate(values)
    except (ValueError, TypeError):
        return _question(pending, "Не получилось однозначно понять уточнение. Скажи его ещё раз.")
    issue = calendar_fields_clarification(fields)
    return CalendarFollowupResult(
        "updated", fields, issue.message if issue else "Детали события уточнены."
    )


def _duration(text: str) -> int | None:
    value = re.sub(r"\s+", " ", text.casefold()).strip()
    value = re.sub(r"^на\s*", "", value)
    if value in {"час", "один час"}:
        return 60
    if value == "полчаса":
        return 30
    if value in {"полтора часа", "полторачаса"}:
        return 90
    match = re.fullmatch(r"(\d{1,3})\s*(мин(?:ут(?:у|ы)?)?|час(?:а|ов)?)", value)
    if match is not None:
        amount = int(match[1]) * (60 if match[2].startswith("час") else 1)
        return amount if 5 <= amount <= 720 else None
    return None


def _clock(text: str) -> tuple[int, int, bool] | None:
    return parse_calendar_clock(text)


def _field_question(
    pending: CalendarPendingClarification, field: str, message: str, **changes: object
) -> CalendarFollowupResult:
    fields = pending.fields.model_copy(
        update={
            **changes,
            "unresolved_fields": tuple(dict.fromkeys((*pending.fields.unresolved_fields, field))),
            "needs_clarification": True,
        }
    )
    return CalendarFollowupResult("clarification", fields, message)


def _date_question(pending: CalendarPendingClarification, message: str) -> CalendarFollowupResult:
    return _field_question(pending, "day", message)


def _duration_question(
    pending: CalendarPendingClarification, message: str
) -> CalendarFollowupResult:
    return _field_question(
        pending,
        "duration",
        message,
        duration_minutes=None,
        ends_at=pending.fields.ends_at if pending.fields.mode == "plan" else "",
    )


def _day_correction(
    pending: CalendarPendingClarification, text: str, *, related: bool = False
) -> CalendarFollowupResult | None:
    value = _DATE_CORRECTION.sub("", text.strip(), count=1)
    spans = calendar_date_spans(value)
    if not spans:
        return None
    residual = value
    for span in reversed(spans):
        residual = residual[: span.start] + " " + residual[span.end :]
    residual = re.sub(r"[\s,.!?;—-]+", " ", residual).strip(" :").casefold()
    related = related or _DATE_CORRECTION.match(text.strip()) is not None
    literal_duration = _duration(residual)
    literal_end = re.fullmatch(r"до\s*(.+)", residual, re.IGNORECASE)
    plain_date = (
        not residual
        or re.fullmatch(r"(?:(?:на|в|это|именно|то есть|а именно)\s*)+", residual) is not None
    )
    if not plain_date and literal_duration is None and literal_end is None:
        return (
            _date_question(pending, "Уточни один день события и недостающие детали.")
            if related
            else None
        )
    original = pending.anchor_date
    if original is None:
        if any(re.fullmatch(r"20\d{2}-\d{2}-\d{2}", span.value) is None for span in spans):
            return _date_question(
                pending, "Укажи полную дату события, чтобы не сдвинуть его из-за смены дня."
            )
        original = date(2000, 1, 1)
    result = resolve_calendar_day(value, anchor_date=original)
    if result.status != "resolved":
        return _date_question(
            pending,
            "Даты в уточнении не совпали. На какой один день поставить событие?"
            if result.status == "ambiguous"
            else "Такую дату не удалось проверить. На какой день поставить событие?",
        )
    changes: dict[str, object] = {"day": result.day, "day_expressions": ()}
    if literal_duration is not None:
        changes.update(
            duration_minutes=literal_duration,
            ends_at=pending.fields.ends_at if pending.fields.mode == "plan" else "",
        )
    updated = _updated(pending, **changes)
    if literal_end is not None:
        return _end(
            CalendarPendingClarification(
                task_id=pending.task_id,
                session_id=pending.session_id,
                fields=updated.fields,
                waiting_for=pending.waiting_for,
                expires_at_monotonic=pending.expires_at_monotonic,
                timezone=pending.timezone,
                anchor_date=pending.anchor_date,
            ),
            literal_end[1],
        )
    return updated


def _elapsed(fields: CalendarChatFields, end: str, timezone: str) -> int | None:
    try:
        day = date.fromisoformat(fields.day)
        zone = ZoneInfo(timezone)
        start = time.fromisoformat(fields.starts_at)
        finish = time.fromisoformat(end)
        values = [datetime.combine(day, clock, zone) for clock in (start, finish)]
        for value in values:
            if (
                value.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
                != value.replace(tzinfo=None)
                or value.replace(fold=1).utcoffset() != value.utcoffset()
            ):
                return None
        minutes = int((values[1].astimezone(UTC) - values[0].astimezone(UTC)).total_seconds() // 60)
        return minutes if 5 <= minutes <= 720 else None
    except (ValueError, KeyError, OverflowError):
        return None


def _end(pending: CalendarPendingClarification, text: str) -> CalendarFollowupResult:
    parsed = _clock(text)
    if (
        parsed is None
        or pending.fields.mode != "exact"
        or not pending.fields.starts_at
        or "day" in pending.fields.unresolved_fields
    ):
        return _duration_question(
            pending, "Во сколько закончится событие? Уточни однозначное время в этом же дне."
        )
    hour, minute, explicit = parsed
    if not explicit and int(pending.fields.starts_at[:2]) < 12:
        return _duration_question(pending, "Это утро или вечер? Уточни время окончания.")
    candidates = {hour} if explicit else {hour % 12, hour % 12 + 12}
    valid = [
        (f"{value:02d}:{minute:02d}", elapsed)
        for value in candidates
        if (elapsed := _elapsed(pending.fields, f"{value:02d}:{minute:02d}", pending.timezone))
        is not None
    ]
    if len(valid) != 1:
        return _duration_question(
            pending, "Уточни время окончания: нужен однозначный интервал в пределах одного дня."
        )
    end, duration = valid[0]
    return _updated(pending, ends_at=end, duration_minutes=duration)


def calendar_chat_followup(
    text: str,
    *,
    pending: CalendarPendingClarification,
    task_id: UUID,
    session_id: str,
    monotonic_now: float,
    related: bool = False,
) -> CalendarFollowupResult | None:
    """Consume only a field answer bound to this still-current pending task.

    No result is a write approval. The caller owns the original unextended TTL,
    must anchor day at initial submit, and must retire this context on terminal,
    hidden, replaced or changed-session state. None routes unrelated text normally.
    """
    if (
        pending.task_id != task_id
        or pending.session_id != session_id
        or not math.isfinite(monotonic_now)
        or not math.isfinite(pending.expires_at_monotonic)
        or monotonic_now >= pending.expires_at_monotonic
    ):
        return CalendarFollowupResult(
            "expired", pending.fields, "Это уточнение уже не относится к текущему поручению."
        )
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 8000:
        return _question(pending, "Скажи коротко, какую деталь события уточнить.")
    value = text.strip().strip(".!?").strip()
    if _CANCEL.match(value):
        return CalendarFollowupResult(
            "cancelled", pending.fields, "Поручение отменено, событие не создаю."
        )
    if value.casefold() in {
        "да",
        "ага",
        "угу",
        "ок",
        "окей",
        "хорошо",
        "подтверждаю",
        "как хочешь",
    }:
        return _question(
            pending, "Нужно уточнить недостающую деталь; одного подтверждения пока недостаточно."
        )
    if calendar_directive(text) is not None or _ORDINARY.match(value):
        return None
    if has_unsupported_calendar_semantics(value) and (
        related
        or calendar_followup_is_date_correction(value)
        or _UNRELATED_SENTENCE.match(value) is None
    ):
        return CalendarFollowupResult(
            "clarification",
            pending.fields.model_copy(
                update={"unsupported_semantics": True, "needs_clarification": True}
            ),
            "Это поручение поддерживает одно обычное событие. "
            "Повторы, гости и другие действия требуют отдельного нового поручения.",
        )
    if pending.fields.unsupported_semantics or (
        pending.fields.needs_clarification and not pending.fields.unresolved_fields
    ):
        return _question(pending)
    correction = _day_correction(pending, value, related=related)
    if correction is not None:
        return correction
    waiting = pending.waiting_for
    explicit_duration = re.match(r"^на(?=\s|пол|\d)", value, re.IGNORECASE)
    if explicit_duration is not None and waiting not in {"duration", "end", "ends_at"}:
        duration = _duration(value)
        if duration is not None:
            return _updated(
                pending,
                duration_minutes=duration,
                ends_at=pending.fields.ends_at if pending.fields.mode == "plan" else "",
            )
        return _duration_question(pending, "Уточни длительность события, например «на час».")
    clock_spans = calendar_clock_spans(value)
    if len(clock_spans) > 1 and clock_spans[0].start == 0 and clock_spans[-1].end == len(value):
        between = value
        for span in reversed(clock_spans):
            between = between[: span.start] + " " + between[span.end :]
        if re.fullmatch(r"(?:\s|[,;:]|\b(?:и|или|либо)\b)*", between, re.IGNORECASE):
            return _field_question(
                pending,
                "exact_time",
                "Тут несколько времён. Назови одно время начала, а затем длительность.",
                starts_at="",
            )
    if (
        len(clock_spans) == 1
        and clock_spans[0].start == 0
        and clock_spans[0].end == len(value)
        and clock_spans[0].role == "start"
    ):
        if clock_spans[0].status == "resolved":
            return _updated(pending, starts_at=clock_spans[0].value)
        return _field_question(
            pending, "exact_time", "Уточни точное время начала: утро, день или вечер?", starts_at=""
        )
    if re.match(r"^в\s+календар[ье]\b", value, re.IGNORECASE):
        waiting = "calendar"
    elif re.match(r"^(?:назови|название|назовём|назовем)\b", value, re.IGNORECASE):
        waiting = "title"
    if waiting in {"duration", "end", "ends_at"}:
        duration = _duration(value)
        if duration is not None:
            return _updated(
                pending,
                duration_minutes=duration,
                ends_at=pending.fields.ends_at if pending.fields.mode == "plan" else "",
            )
        end = re.fullmatch(r"до(\s*.+)", value, re.IGNORECASE)
        if end is not None and (
            end[1][:1].isspace()
            or _clock(end[1].strip()) is not None
            or re.match(r"\d", end[1]) is not None
        ):
            return _end(pending, end[1].strip())
        parsed_clock = _clock(value)
        if parsed_clock is not None:
            return (
                _end(pending, value)
                if waiting in {"end", "ends_at"} and parsed_clock[2]
                else _duration_question(
                    pending,
                    "Это время окончания? Скажи, например, «до девяти» или назови длительность.",
                )
            )
        if re.match(r"^(?:на(?=\s|пол|\d)|до\b|пол|\d)", value, re.IGNORECASE):
            return _duration_question(
                pending,
                "Уточни длительность или время окончания — например, «на час» или «до девяти».",
            )
        return None
    if waiting in {"exact_time", "start", "starts_at"}:
        if re.fullmatch(r"\d{1,2}", value):
            return _field_question(
                pending,
                "exact_time",
                "Это время начала? Скажи, например, «в девять вечера».",
                starts_at="",
            )
        parsed = _clock(re.sub(r"^(?:в|с)(?:\s+|(?=\d))", "", value, flags=re.IGNORECASE))
        if parsed is not None and parsed[2]:
            return _updated(pending, starts_at=f"{parsed[0]:02d}:{parsed[1]:02d}")
        if parsed is not None or re.match(r"^(?:в\b|с\b|\d)", value, re.IGNORECASE):
            return _field_question(
                pending, "exact_time", "Уточни, это утро, день или вечер?", starts_at=""
            )
        return None
    if waiting == "day":
        return _question(pending) if re.fullmatch(r"\d{1,2}", value) else None
    if waiting == "calendar":
        explicit_calendar = re.match(r"^в\s+календар[ье]\b", value, re.IGNORECASE) is not None
        if (
            not explicit_calendar
            and QUOTED.fullmatch(value) is None
            and _UNRELATED_SENTENCE.match(value)
        ):
            return None
        hint = re.sub(r"^в\s+календар[ье]\s*", "", value, flags=re.IGNORECASE)
        quoted = QUOTED.fullmatch(hint)
        hint = (quoted[1] or quoted[2]) if quoted is not None else hint
        if hint and len(hint) <= 8000 and "\n" not in hint:
            return _updated(pending, requested_calendar_title=hint)
        return _field_question(
            pending,
            "calendar",
            "Какой из подключённых календарей выбрать?",
            requested_calendar_title=None,
        )
    if waiting == "title":
        quoted = QUOTED.fullmatch(value)
        explicit_title = re.match(r"^(?:назови|название|назовём|назовем)\b", value, re.IGNORECASE)
        if quoted is None and explicit_title is None and _UNRELATED_SENTENCE.match(value):
            return None
        title = (
            (quoted[1] or quoted[2])
            if quoted is not None
            else re.sub(
                r"^(?:назови|название|назовём|назовем)\s*[:—-]?\s*", "", value, flags=re.IGNORECASE
            )
        )
        if 0 < len(title) <= 500 and "\n" not in title:
            return _updated(pending, title=title)
        return _field_question(
            pending, "title", "Как назвать это событие? Нужен один короткий заголовок.", title=""
        )
    return None
