"""Whole user utterances retain literal fields before any clarification or effect."""

# ruff: noqa: RUF001 - public Russian scheduling corpus.

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from satori.application.assistant.calendar_chat_fields import (
    anchor_calendar_fields,
    calendar_chat_fields,
    calendar_fields_clarification,
    has_unsupported_calendar_semantics,
    is_direct_calendar_create_request,
)
from satori.application.assistant.calendar_chat_intake import (
    CalendarChatIntake,
    calendar_chat_intake,
    is_calendar_chat_request,
)
from satori.application.assistant.calendar_chat_lexing import (
    calendar_clock_spans,
    parse_calendar_clock,
    resolve_calendar_day,
)
from satori.application.assistant.calendar_text import CalendarTextClarification
from tests.test_calendar_text import scope

NOW = datetime(2026, 11, 4, 9, tzinfo=UTC)


@pytest.mark.parametrize(
    "text",
    [
        "привет, у меня завтра созвон в 8 вечера, внеси его в календарь события",
        "Привет! У меня завтра созвон в восемь вечера, внеси его в календарь События",
        "Сатори, привет, у нас завтра созвон в восемь вечера, занеси его в календарь События",
        "Добавь завтра в 8 вечера созвон в календарь События",
        "Запиши созвон в 8 вечера завтра в календарь События",
        "Созвон завтра в 8 вечера, запиши его в календарь События",
        "Внеси в календарь «События» созвон завтра в 8 вечера",
        "Можешь внести созвон завтра в восемь вечера в календарь События?",
        "Ты можешь добавить завтра созвон в 20:00 в календарь События?",
        "Мы завтра планируем созвон в восемь вечера занеси его в календарь События",
        "Завтра у меня созвон в восемь вечера, внеси его в календарь События",
    ],
)
def test_description_and_word_order_never_drop_known_fields(text: str) -> None:
    assert is_calendar_chat_request(text)
    assert is_direct_calendar_create_request(text)
    fields = anchor_calendar_fields(calendar_chat_fields(text), now=NOW, timezone="Europe/Moscow")
    assert fields.title.casefold() == "созвон"
    assert fields.day == "2026-11-05"
    assert fields.starts_at == "20:00"
    assert fields.requested_calendar_title is not None
    assert fields.requested_calendar_title.casefold() == "события"
    assert fields.duration_minutes is None
    assert not fields.unsupported_semantics
    clarification = calendar_fields_clarification(fields)
    assert clarification is not None
    assert clarification.missing == ("duration",)
    parsed = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(parsed, CalendarTextClarification)
    assert parsed.missing == ("duration",)


@pytest.mark.parametrize(
    "text",
    [
        "Занеси созвон завтра в ВОСЕМЬ вечера на час в календарь События",
        "Добавь завтра на час в восемь вечера созвон в календарь События",
        "Созвон завтра на час в20:00, внеси его в календарь События",
        "Внеси в календарь «События» завтра в восемь вечера на час созвон",
        "Внеси в календарь «Рабочие встречи» завтра в восемь вечера на час созвон",
        "Можешь добавить «Массаж» 5 ноября в восемь вечера на час?",
        "Поставь созвон завтра, 5 ноября 2026 года, в 20:00 на час в календарь События",
        "Создай «PUBLIC созвон» 2026-11-05, завтра с 20:00 до 21:00",
    ],
)
def test_complete_literal_input_reaches_exact_intake_without_model_rewrite(text: str) -> None:
    assert is_calendar_chat_request(text)
    assert is_direct_calendar_create_request(text)
    parsed = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(parsed, CalendarChatIntake)
    assert parsed.mode == "exact"
    assert parsed.request.user_text == text
    assert parsed.request.window_start == datetime(2026, 11, 5, 17, tzinfo=UTC)
    assert parsed.request.window_end == datetime(2026, 11, 5, 18, tzinfo=UTC)
    assert parsed.request.duration_minutes == 60


@pytest.mark.parametrize(
    "text",
    [
        "Добавь завтра в 20:00 на час в календарь События",
        "Поставь на завтра в восемь вечера на час событие в календарь События",
    ],
)
def test_missing_title_is_not_replaced_by_a_preposition(text: str) -> None:
    fields = calendar_chat_fields(text)
    if "событие" in text:
        assert fields.title == "событие"
    else:
        assert fields.title == ""
        issue = calendar_fields_clarification(fields)
        assert issue is not None
        assert issue.missing == ("title",)


@pytest.mark.parametrize(
    "literal", ["завтра, 5 ноября", "2026-11-05, завтра", "5 ноября 2026 года, завтра"]
)
def test_consistent_day_aliases_share_one_original_civil_anchor(literal: str) -> None:
    result = resolve_calendar_day(literal, anchor_date=date(2026, 11, 4))
    assert result.status == "resolved"
    assert result.day == "2026-11-05"
    fields = anchor_calendar_fields(
        calendar_chat_fields(f"Запиши созвон {literal} в20:00 на час"),
        now=NOW,
        timezone="Europe/Moscow",
    )
    assert fields.day == "2026-11-05"
    assert fields.day_expressions == ()
    assert "day" not in fields.unresolved_fields
    reanchored = anchor_calendar_fields(
        fields, now=datetime(2026, 11, 5, 23, tzinfo=UTC), timezone="Europe/Moscow"
    )
    assert reanchored.day == fields.day


@pytest.mark.parametrize(
    "literal", ["завтра, 6 ноября", "2026-11-06, завтра", "31 ноября", "2026-99-99"]
)
def test_invalid_or_contradictory_day_keeps_other_fields_and_blocks_exact_intake(
    literal: str,
) -> None:
    text = f"Запиши созвон {literal} в20:00 на час в календарь События"
    fields = anchor_calendar_fields(calendar_chat_fields(text), now=NOW, timezone="Europe/Moscow")
    assert fields.title == "созвон"
    assert fields.starts_at == "20:00"
    assert fields.duration_minutes == 60
    assert fields.needs_clarification
    assert "day" in fields.unresolved_fields
    assert not fields.unsupported_semantics
    parsed = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(parsed, CalendarTextClarification)
    assert parsed.missing == ("day",)


@pytest.mark.parametrize(
    ("literal", "clock"),
    [
        ("в восемь вечера", "20:00"),
        ("в восьми вечера", "20:00"),
        ("в 8 часов вечера", "20:00"),
        ("в 20:30", "20:30"),
        ("в два дня", "14:00"),
        ("в12ночи", "00:00"),
    ],
)
def test_spoken_and_numeric_start_literals_use_the_same_clock_lexer(
    literal: str, clock: str
) -> None:
    spans = calendar_clock_spans(literal)
    assert len(spans) == 1
    assert spans[0].status == "resolved"
    assert spans[0].value == clock


@pytest.mark.parametrize("literal", ["в25:00", "в21:99", "в100:00", "в8:0", "в8:000", "в12вечера"])
def test_invalid_clock_is_a_field_issue_not_an_exception_or_partial_prefix(literal: str) -> None:
    text = f"Запиши созвон завтра {literal} на час в календарь События"
    fields = calendar_chat_fields(text)
    assert fields.starts_at == ""
    assert "exact_time" in fields.unresolved_fields
    assert fields.title == "созвон"
    assert isinstance(
        calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW),
        CalendarTextClarification,
    )


def test_unqualified_start_is_recoverable_without_dropping_other_fields() -> None:
    fields = calendar_chat_fields("Занеси созвон завтра в8 на час в календарь События")
    assert fields.starts_at == ""
    assert fields.unresolved_fields == ("exact_time",)
    assert fields.title == "созвон"
    assert fields.day == "завтра"
    assert fields.duration_minutes == 60
    assert not fields.unsupported_semantics
    assert is_direct_calendar_create_request("Занеси созвон завтра в8 на час в календарь События")


@pytest.mark.parametrize(
    "modifier",
    [
        "каждую неделю",
        "и пригласи друзей",
        "с напоминанием",
        "после работы",
        "по времени Амстердама",
        "по Лондону",
        "если я освобожусь",
        "на час с половиной",
    ],
)
def test_unsupported_semantics_are_sticky_even_with_other_complete_fields(modifier: str) -> None:
    text = f"Запиши созвон завтра в20:00 на час {modifier} в календарь События"
    fields = calendar_chat_fields(text)
    assert has_unsupported_calendar_semantics(text)
    assert fields.unsupported_semantics
    assert fields.needs_clarification
    assert not is_direct_calendar_create_request(text)
    assert isinstance(
        calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW),
        CalendarTextClarification,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Он написал: «Занеси созвон завтра в восемь вечера в календарь События»",
        "Не заноси созвон завтра в календарь События",
        "Если завтра созвон в20:00, добавь его в календарь События",
        "Например, завтра созвон в20:00, внеси его в календарь События",
        "Объясни фразу «занеси созвон в календарь События»",
    ],
)
def test_report_quote_negation_and_conditional_cannot_supply_direct_intent(text: str) -> None:
    assert not is_calendar_chat_request(text)
    assert not is_direct_calendar_create_request(text)


def test_calendar_first_ambiguous_unquoted_name_requires_literal_target_repair() -> None:
    text = "Внеси в календарь Рабочие встречи созвон завтра в20:00 на час"
    fields = anchor_calendar_fields(calendar_chat_fields(text), now=NOW, timezone="Europe/Moscow")
    assert fields.title == ""
    assert fields.day == "2026-11-05"
    assert fields.starts_at == "20:00"
    assert "title" in fields.unresolved_fields
    assert "calendar" in fields.unresolved_fields
    assert isinstance(
        calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW),
        CalendarTextClarification,
    )


def test_quoted_literal_title_does_not_become_an_unsupported_operation() -> None:
    text = "Создай «Пригласи друзей завтра» завтра в20:00 на час"
    assert not has_unsupported_calendar_semantics(text)
    parsed = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(parsed, CalendarChatIntake)
    assert parsed.request.title == "Пригласи друзей завтра"


def test_oversized_title_is_not_truncated_and_malformed_clock_body_is_total() -> None:
    fields = calendar_chat_fields(f"Занеси {'С' * 501} завтра в20:00 на час в календарь События")
    assert fields.title == ""
    assert "title" in fields.unresolved_fields
    for literal in ["8:0", "25:00", "в8", "8 вечера потом", "9999", ""]:
        assert parse_calendar_clock(literal) is None


@pytest.mark.parametrize(
    "tail",
    [
        "в20:00 и в20:00 на час",
        "в20:00 или в20:00 на час",
        "в20:00 на час или полчаса",
        "в20:00 на час и напомни мне",
        "в20:00 на час и предупреди Диму",
    ],
)
def test_multiple_anchors_choices_and_secondary_actions_cannot_be_silently_dropped(
    tail: str,
) -> None:
    text = f"Добавь созвон завтра {tail} в календарь События"
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarTextClarification)


def test_explicit_multiple_events_cannot_be_reinterpreted_as_one_literal_title() -> None:
    text = "Добавь два созвона завтра в20:00 на час в календарь События"
    assert not is_direct_calendar_create_request(text)
    assert isinstance(
        calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW),
        CalendarTextClarification,
    )


@pytest.mark.parametrize("duration", ["на полтора часа", "наполторачаса"])
def test_named_initial_duration_is_a_field_instead_of_an_event_title_suffix(duration: str) -> None:
    text = f"Запиши созвон завтра в восемь вечера {duration} в календарь События"
    fields = calendar_chat_fields(text)
    assert fields.title == "созвон"
    assert fields.duration_minutes == 90
    assert not fields.unsupported_semantics
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarChatIntake)
    assert result.mode == "exact"
    assert result.request.title == "созвон"
    assert result.request.duration_minutes == 90
    assert result.request.user_text == text
    assert result.request.window_end == datetime(2026, 11, 5, 18, 30, tzinfo=UTC)


def test_oversized_initial_numeric_duration_is_not_absorbed_into_the_title() -> None:
    fields = calendar_chat_fields("Запиши созвон завтра в20:00 на1000минут в календарь События")
    assert fields.title == "созвон"
    assert fields.duration_minutes is None
    assert "duration" in fields.unresolved_fields


@pytest.mark.parametrize("title", ["созвон", "«Созвон и итоги»"])
def test_only_the_connector_between_actual_clock_spans_is_removed_from_the_title(
    title: str,
) -> None:
    text = f"Добавь {title} завтра в20:00 и в21:00 на час в календарь События"
    fields = calendar_chat_fields(text)
    assert fields.title == title.strip("«»")
    assert fields.starts_at == ""
    assert fields.unresolved_fields == ("exact_time",)
    assert not fields.unsupported_semantics
    assert is_direct_calendar_create_request(text)
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarTextClarification)
    assert result.missing == ("exact_time",)


def test_title_conjunction_outside_clock_spans_is_never_removed() -> None:
    text = "Добавь созвон и встречу завтра в20:00 на час в календарь События"
    fields = calendar_chat_fields(text)
    assert fields.title == "созвон и встречу"
    assert fields.unsupported_semantics
    assert not is_direct_calendar_create_request(text)
