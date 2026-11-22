"""Deterministic task routing never manufactures an action or a time habit."""

# ruff: noqa: RUF001 - intentional Russian input cases

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from satori.application.assistant.intents import parse_assistant_intent

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "text", ["Напомни, что я рассказывал о проекте", "Что думаешь о напоминаниях?", "Привет"]
)
def test_ordinary_conversation_is_not_an_action(text: str) -> None:
    assert parse_assistant_intent(text, now=NOW, timezone="Europe/Moscow") is None


def test_today_and_tomorrow_use_explicit_zone_and_request_identity() -> None:
    request_id = uuid4()
    value = parse_assistant_intent(
        "Что у меня завтра по планам?", now=NOW, timezone="Europe/Moscow", request_id=request_id
    )
    assert value is not None
    assert value.kind == "calendar_agenda"
    assert value.request_id == request_id
    assert value.day is not None
    assert value.day.isoformat() == "2026-11-01"


@pytest.mark.parametrize("day", ["сегодня", "завтра"])
def test_standalone_calendar_question_routes_without_technical_keyword(day: str) -> None:
    value = parse_assistant_intent(f"Что у меня {day}?", now=NOW, timezone="Europe/Moscow")
    assert value is not None
    assert value.kind == "calendar_agenda"


@pytest.mark.parametrize(
    "text",
    [
        "в воскресенье у меня есть что-то или нет",
        "Сатори, а на воскресенье что-нибудь запланировано?",
        "Привет! Проверь по календарю, что у меня в воскресенье.",
        "В это воскресенье, есть ли у меня планы?",
        "У меня что-то на воскресенье?",
        "В календаре на воскресенье есть события?",
    ],
)
def test_personal_weekday_agenda_in_ordinary_language(text: str) -> None:
    value = parse_assistant_intent(
        text, now=datetime(2026, 11, 12, 17, tzinfo=UTC), timezone="Europe/Moscow"
    )
    assert value is not None
    assert value.kind == "calendar_agenda"
    assert value.day is not None
    assert value.day.isoformat() == "2026-11-15"


@pytest.mark.parametrize(
    ("now", "expected_day"),
    [
        (datetime(2026, 11, 14, 21, tzinfo=UTC), "2026-11-15"),
        (datetime(2026, 11, 15, 20, 59, tzinfo=UTC), "2026-11-15"),
        (datetime(2026, 11, 15, 21, tzinfo=UTC), "2026-11-22"),
    ],
)
def test_weekday_agenda_anchors_to_the_next_inclusive_local_day(
    now: datetime, expected_day: str
) -> None:
    value = parse_assistant_intent("Что у меня в воскресенье?", now=now, timezone="Europe/Moscow")
    assert value is not None
    assert value.kind == "calendar_agenda"
    assert value.day is not None
    assert value.day.isoformat() == expected_day


@pytest.mark.parametrize(
    "text",
    [
        "Я спросил: в воскресенье у меня есть что-то или нет?",
        "Не проверяй, что у меня в воскресенье.",
        "«В воскресенье у меня есть что-то или нет?»",
        "В воскресенье у Маши что-то запланировано?",
        "В воскресенье у меня что-то запланировано? Потом удали всё.",
        "В воскресенье или в субботу у меня что-то есть?",
        "Что у меня в воскресенье в другом календаре?",
        "В воскресенье у меня есть планы.",
        "Сатори, у меня на воскресенье что-то есть.",
    ],
)
def test_weekday_intake_rejects_quoted_unrelated_and_compound_questions(text: str) -> None:
    assert parse_assistant_intent(text, now=NOW, timezone="Europe/Moscow") is None


def test_agenda_command_does_not_require_question_punctuation() -> None:
    value = parse_assistant_intent(
        "Проверь, в воскресенье у меня есть планы",
        now=datetime(2026, 11, 12, 17, tzinfo=UTC),
        timezone="Europe/Moscow",
    )
    assert value is not None
    assert value.kind == "calendar_agenda"
    assert value.day is not None
    assert value.day.isoformat() == "2026-11-15"


def test_calendar_date_requires_explicit_year_and_no_substring_capture() -> None:
    ambiguous = parse_assistant_intent(
        "Что в календаре 1 ноября?", now=NOW, timezone="Europe/Moscow"
    )
    assert ambiguous is not None
    assert ambiguous.kind == "clarification"
    exact = parse_assistant_intent(
        "Что в календаре 1 ноября 2026?", now=NOW, timezone="Europe/Moscow"
    )
    assert exact is not None
    assert exact.day is not None
    assert exact.day.isoformat() == "2026-11-01"
    assert (
        parse_assistant_intent(
            "Что у меня завтра? Это просто пример для документации",
            now=NOW,
            timezone="Europe/Moscow",
        )
        is None
    )


def test_exact_reminder_and_relative_time_preserve_title_case() -> None:
    value = parse_assistant_intent(
        "Напомни   завтра в 19:30 написать API Proposal", now=NOW, timezone="Europe/Moscow"
    )
    assert value is not None
    assert value.kind == "reminder_create"
    assert value.title == "написать API Proposal"
    assert value.due_at == datetime(2026, 11, 1, 16, 30, tzinfo=UTC)
    relative = parse_assistant_intent(
        "Напомни через 10 минут написать API", now=NOW, timezone="Europe/Moscow"
    )
    assert relative is not None
    assert relative.due_at == datetime(2026, 10, 31, 12, 10, tzinfo=UTC)
    assert relative.title == "написать API"


@pytest.mark.parametrize(
    "text",
    [
        "Напомни завтра вечером написать по проекту",
        "Напомни купить молоко",
        "Напомни в 01:00 купить молоко",
    ],
)
def test_ambiguous_or_past_time_is_a_clarification(text: str) -> None:
    value = parse_assistant_intent(text, now=NOW, timezone="Europe/Moscow")
    assert value is not None
    assert value.kind == "clarification"
    assert value.due_at is None


@pytest.mark.parametrize("day", ["2026-03-29", "2026-10-25"])
def test_dst_gap_and_fold_are_not_silently_chosen(day: str) -> None:
    value = parse_assistant_intent(
        f"Напомни {day} в 02:30 сделать дело",
        now=datetime(2026, 1, 1, tzinfo=UTC),
        timezone="Europe/Berlin",
    )
    assert value is not None
    assert value.kind == "clarification"


def test_lookup_is_selected_scope_only_and_cancel_needs_exact_id() -> None:
    value = parse_assistant_intent(
        "Найди в разрешённой переписке API Meeting", now=NOW, timezone="Europe/Moscow"
    )
    assert value is not None
    assert value.kind == "selected_lookup"
    assert value.source == "telegram"
    assert value.query == "API Meeting"
    identifier = uuid4()
    cancel = parse_assistant_intent(
        f"Отмени напоминание {identifier}", now=NOW, timezone="Europe/Moscow"
    )
    assert cancel is not None
    assert cancel.reminder_id == identifier
    no_identifier = parse_assistant_intent("Отмени напоминание", now=NOW, timezone="Europe/Moscow")
    assert no_identifier is not None
    assert no_identifier.kind == "clarification"
