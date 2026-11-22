"""Current-task natural field answers are inert, anchored and never a write grant."""

# ruff: noqa: RUF001 - deliberate Russian public scheduling phrases.

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from satori.application.assistant.calendar_chat_fields import (
    CalendarChatFields,
    anchor_calendar_fields,
    calendar_chat_fields,
    is_direct_calendar_create_request,
)
from satori.application.assistant.calendar_chat_followup import (
    CalendarFollowupResult,
    CalendarPendingClarification,
    calendar_chat_followup,
)
from satori.application.assistant.calendar_chat_intake import (
    CalendarChatIntake,
    calendar_chat_intake,
    is_calendar_chat_request,
)
from satori.application.assistant.calendar_text import CalendarTextClarification
from tests.test_calendar_text import scope

NOW = datetime(2026, 11, 4, 9, tzinfo=UTC)
TEXT = "Мы завтра планируем созвон в 8 вечера, занеси его в календарь события"


def pending(waiting_for: str = "duration", **changes: object) -> CalendarPendingClarification:
    fields = anchor_calendar_fields(calendar_chat_fields(TEXT), now=NOW, timezone="Europe/Moscow")
    fields = CalendarChatFields.model_validate(fields.model_copy(update=changes))
    return CalendarPendingClarification(
        task_id=uuid4(),
        session_id=str(uuid4()),
        fields=fields,
        waiting_for=waiting_for,
        expires_at_monotonic=400,
        timezone="Europe/Moscow",
        anchor_date=date(2026, 11, 4),
    )


def reply(
    text: str, task: CalendarPendingClarification | None = None
) -> CalendarFollowupResult | None:
    task = task or pending()
    return calendar_chat_followup(
        text, pending=task, task_id=task.task_id, session_id=task.session_id, monotonic_now=101
    )


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("на час", 60),
        ("час", 60),
        ("полчаса", 30),
        ("на полчаса", 30),
        ("на полтора часа", 90),
        ("наполторачаса", 90),
        ("60 минут", 60),
        ("60минут", 60),
    ],
)
def test_missing_duration_understands_natural_answers_and_preserves_original_input(
    text: str,
    minutes: int,
) -> None:
    task = pending()
    result = reply(text, task)
    assert result is not None
    assert result.status == "updated"
    assert result.fields.duration_minutes == minutes
    assert result.fields.day == "2026-11-05"
    assert task.fields.duration_minutes is None
    parsed = calendar_chat_intake(
        TEXT, request_id=uuid4(), scope=scope(), now=NOW, fields=result.fields
    )
    assert isinstance(parsed, CalendarChatIntake)
    assert parsed.request.user_text == TEXT
    assert parsed.mode == "exact"
    assert parsed.request.duration_minutes == minutes


@pytest.mark.parametrize("text", ["до21", "до 21", "до21:00", "до 21:00", "додевяти", "до девяти"])
def test_evening_start_resolves_unique_same_day_end(text: str) -> None:
    result = reply(text)
    assert result is not None
    assert result.status == "updated"
    assert (result.fields.starts_at, result.fields.ends_at, result.fields.duration_minutes) == (
        "20:00",
        "21:00",
        60,
    )


def test_unqualified_morning_end_asks_part_of_day_instead_of_using_duration_bound_to_guess() -> (
    None
):
    result = reply("до девяти", pending(starts_at="08:00"))
    assert result is not None
    assert result.status == "clarification"
    assert result.fields.ends_at == ""
    assert result.fields.duration_minutes is None


@pytest.mark.parametrize(
    "text", ["до25:00", "до21:99", "до20:00", "до03:00", "на0 минут", "на1000минут"]
)
def test_invalid_cross_day_or_unbounded_duration_never_supplies_complete_fields(text: str) -> None:
    result = reply(text)
    assert result is not None
    assert result.status == "clarification"
    assert result.fields.duration_minutes is None


def test_plain_number_is_only_an_end_answer_when_waiting_for_end() -> None:
    duration_result = reply("21")
    assert duration_result is not None
    assert duration_result.status == "clarification"
    end_result = reply("21", pending("end"))
    assert end_result is not None
    assert end_result.status == "updated"
    assert end_result.fields.ends_at == "21:00"
    start_result = reply("21", pending("exact_time", starts_at=""))
    assert start_result is not None
    assert start_result.status == "clarification"


def test_bare_single_digit_is_still_ambiguous_in_a_combined_duration_end_question() -> None:
    result = reply("9", pending("end"))
    assert result is not None
    assert result.status == "clarification"
    assert result.fields.ends_at == ""


def test_duration_answer_preserves_a_planning_search_window() -> None:
    result = reply("на час", pending(mode="plan", starts_at="14:00", ends_at="20:00"))
    assert result is not None
    assert result.status == "updated"
    assert result.fields.mode == "plan"
    assert result.fields.ends_at == "20:00"
    assert result.fields.duration_minutes == 60


def test_unrelated_word_starting_with_do_is_not_an_end_clause() -> None:
    assert reply("домой") is None


@pytest.mark.parametrize("text", ["в 8 вечера", "в8вечера", "восемь вечера", "в 21", "21:00"])
def test_start_answer_can_use_a_qualified_natural_clock(text: str) -> None:
    result = reply(text, pending("exact_time", starts_at=""))
    assert result is not None
    assert result.status == "updated"
    assert result.fields.starts_at == ("21:00" if "21" in text else "20:00")


@pytest.mark.parametrize(
    "text", ["отмена", "не надо", "нет", "не добавляй", "не ставь", "не планируй"]
)
def test_negation_cancels_only_the_current_task(text: str) -> None:
    task = pending()
    result = reply(text, task)
    assert result is not None
    assert result.status == "cancelled"
    assert result.fields == task.fields


@pytest.mark.parametrize("text", ["да", "ок", "подтверждаю", "как хочешь"])
def test_generic_agreement_never_fills_a_missing_field_or_supplies_approval(text: str) -> None:
    result = reply(text)
    assert result is not None
    assert result.status == "clarification"
    assert result.fields.duration_minutes is None


@pytest.mark.parametrize("change", ["task", "session", "expired", "nan"])
def test_context_and_original_monotonic_expiry_fence_short_answers(change: str) -> None:
    task = pending()
    result = calendar_chat_followup(
        "на час",
        pending=task,
        task_id=uuid4() if change == "task" else task.task_id,
        session_id="other" if change == "session" else task.session_id,
        monotonic_now=float("nan") if change == "nan" else 400 if change == "expired" else 101,
    )
    assert result is not None
    assert result.status == "expired"
    assert result.fields.duration_minutes is None


@pytest.mark.parametrize("waiting", ["duration", "calendar", "title"])
@pytest.mark.parametrize(
    "text", ["я устал", "мне сейчас грустно", "сегодня работаю над Сатори", "Расскажи о звёздах"]
)
def test_unrelated_conversation_is_not_swallowed_as_a_calendar_field(
    waiting: str, text: str
) -> None:
    assert reply(text, pending(waiting)) is None


@pytest.mark.parametrize("text", ["События", "в календарь События", "iCloud / События"])
def test_named_calendar_reply_is_only_a_name_hint_for_the_live_host(text: str) -> None:
    result = reply(text, pending("calendar"))
    assert result is not None
    assert result.status == "updated"
    assert result.fields.requested_calendar_title == text.removeprefix("в календарь ")
    assert result.fields.duration_minutes is None


def test_explicit_quoted_title_preserves_words_that_would_otherwise_be_ordinary_conversation() -> (
    None
):
    result = reply("«Я устал»", pending("title", title=""))
    assert result is not None
    assert result.status == "updated"
    assert result.fields.title == "Я устал"


def test_delayed_day_answer_uses_initial_reference_date_not_event_day_or_new_wall_clock() -> None:
    task = pending("day", day="")
    result = reply("завтра", task)
    assert result is not None
    assert result.status == "updated"
    assert result.fields.day == "2026-11-05"
    assert task.anchor_date == date(2026, 11, 4)
    unanchored = reply("завтра", replace(task, anchor_date=None))
    assert unanchored is not None
    assert unanchored.status == "clarification"


@pytest.mark.parametrize("anchor", [date(2026, 11, 4), None])
def test_invalid_day_answer_never_completes_the_request(anchor: date | None) -> None:
    result = reply("2026-99-99", replace(pending("day", day=""), anchor_date=anchor))
    assert result is not None
    assert result.status == "clarification"
    assert result.fields.day == ""


def test_original_relative_day_is_frozen_before_a_later_duration_answer() -> None:
    task = pending()
    assert task.fields.day == "2026-11-05"
    result = reply("на час", task)
    assert result is not None
    assert result.fields.day == task.fields.day


@pytest.mark.parametrize(
    "text",
    [
        "Мы завтра планируем созвон в 8 вечера занеси его в календарь события",
        "Завтра созвон в 8 вечера занеси его в календарь События",
    ],
)
def test_safe_embedded_directive_does_not_depend_on_punctuation(text: str) -> None:
    assert is_calendar_chat_request(text)
    assert is_direct_calendar_create_request(text)
    fields = calendar_chat_fields(text)
    assert fields.title.casefold() == "созвон"
    assert fields.starts_at == "20:00"
    assert fields.requested_calendar_title is not None
    assert fields.duration_minutes is None


@pytest.mark.parametrize(
    "text",
    [
        "Я говорил занеси созвон завтра в календарь События",
        "Например завтра созвон в8 вечера занеси его в календарь События",
        "Мы завтра не планируем созвон занеси его в календарь События",
        "Он написал «занеси созвон завтра в календарь События»",
        "Создай проект на Python",
        "Предложи созвон завтра с 14:00 до 20:00 на час",
        "Поставь созвон завтра с 14:00 до 20:00 на час",
    ],
)
def test_non_direct_or_wider_window_input_is_not_create_intent(text: str) -> None:
    assert not is_direct_calendar_create_request(text)


def test_duration_reply_does_not_erase_unsupported_recurrence_or_invitations() -> None:
    text = "Запиши созвон завтра в 8 вечера каждую неделю в календарь События"
    fields = calendar_chat_fields(text)
    assert fields.needs_clarification
    assert not is_direct_calendar_create_request(text)
    result = reply("на час", replace(pending(), fields=fields))
    assert result is not None
    assert result.status == "clarification"
    assert result.fields.needs_clarification
    parsed = calendar_chat_intake(
        text, request_id=uuid4(), scope=scope(), now=NOW, fields=result.fields
    )
    assert isinstance(parsed, CalendarTextClarification)


def test_invalid_date_and_oversized_title_do_not_raise_or_truncate() -> None:
    invalid = anchor_calendar_fields(
        CalendarChatFields(day="2026-99-99"), now=NOW, timezone="Europe/Moscow"
    )
    assert invalid.needs_clarification
    text = f"Запиши {'С' * 501} завтра в8 вечера на час в календарь События"
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarTextClarification)
    answer = reply("Т" * 501, pending("title", title=""))
    assert answer is not None
    assert answer.status == "clarification"


@pytest.mark.parametrize(
    ("text", "missing"),
    [
        ("на 0 минут", "duration"),
        ("в 25:99", "exact_time"),
        ("назови " + "Т" * 501, "title"),
    ],
)
def test_invalid_explicit_correction_retires_the_previous_valid_field(
    text: str, missing: str
) -> None:
    task = pending(duration_minutes=60)
    rejected = reply(text, task)
    assert rejected is not None
    assert rejected.status == "clarification"
    assert missing in rejected.fields.unresolved_fields
    assert rejected.fields.needs_clarification
    if missing == "duration":
        assert rejected.fields.duration_minutes is None
    elif missing == "exact_time":
        assert rejected.fields.starts_at == ""
    else:
        assert rejected.fields.title == ""
    parsed = calendar_chat_intake(
        TEXT, request_id=uuid4(), scope=scope(), now=NOW, fields=rejected.fields
    )
    assert isinstance(parsed, CalendarTextClarification)


def test_unsupported_followup_stays_blocked_after_a_later_valid_duration() -> None:
    task = pending()
    unsupported = reply("на час каждый день", task)
    assert unsupported is not None
    assert unsupported.status == "clarification"
    assert unsupported.fields.unsupported_semantics
    later = reply("на час", replace(task, fields=unsupported.fields))
    assert later is not None
    assert later.status == "clarification"
    assert later.fields.unsupported_semantics
    assert later.fields.duration_minutes is None
