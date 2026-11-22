"""Exact user intervals, search windows and ambiguous chat tasks stay distinct."""

# ruff: noqa: RUF001 - public Russian calendar inputs.

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from satori.application.assistant.calendar_chat_intake import (
    CalendarChatIntake,
    calendar_chat_intake,
    is_calendar_chat_request,
)
from satori.application.assistant.calendar_text import CalendarTextClarification
from tests.test_calendar_text import scope

NOW = datetime(2026, 11, 3, 9, tzinfo=UTC)


@pytest.mark.parametrize(
    "text",
    [
        "Создай «PUBLIC встреча» завтра с 15:00 до 16:00",
        "Поставь «PUBLIC встреча» завтра в 15:00 на час",
        "Сатори, добавь «PUBLIC встреча» 2026-11-04 с 15:00 до 16:00 на 60 минут",
    ],
)
def test_explicit_interval_is_exact_and_preserves_real_user_input(text: str) -> None:
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarChatIntake)
    assert result.mode == "exact"
    assert result.request.user_text == text
    assert result.request.title == "PUBLIC встреча"
    assert result.request.window_start == datetime(2026, 11, 4, 12, tzinfo=UTC)
    assert result.request.window_end == datetime(2026, 11, 4, 13, tzinfo=UTC)
    assert result.request.duration_minutes == 60


@pytest.mark.parametrize(
    "text",
    [
        "Найди «PUBLIC встреча» завтра с 14:00 до 20:00 на час",
        "Поставь «PUBLIC встреча» завтра с 14:00 до 20:00 на час",
        "Предложи «PUBLIC встреча» завтра с 15:00 до 16:00 на час",
        "Найди «Массаж» завтра с 14:00 до 20:00 на час",
    ],
)
def test_search_or_wider_window_never_silently_becomes_exact(text: str) -> None:
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarChatIntake)
    assert result.mode == "plan"


@pytest.mark.parametrize(
    "text",
    [
        "Поставь встречу завтра",
        "Добавь «PUBLIC» сегодня или завтра с 15:00 до 16:00",
        "Создай «PUBLIC» завтра с 15:00 до 16:00 или с 17:00 до 18:00",
        "Поставь «PUBLIC» завтра в 15:00 или в 17:00 на час",
        "Поставь «PUBLIC» завтра в 15:00 на час или на 30 минут",
        "Поставь «PUBLIC» завтра в 25:00 на час",
        "Поставь «PUBLIC» завтра в 23:30 на час",
        "Создай «PUBLIC» завтра с 08:00 до 23:00",
        "Поставь «PUBLIC» не в среду с 15:00 до 16:00",
        "Создай «PUBLIC» не 2026-11-04 с 15:00 до 16:00",
        "Поставь встречу не завтра с 15:00 до 16:00",
        "Поставь «PUBLIC» завтра в 15:00 или позже на час",
        "Поставь «PUBLIC» завтра примерно в 15:00 на час",
        "Поставь «PUBLIC» завтра около 15:00 на час",
        "Поставь «PUBLIC» завтра с 15:00 до 16:00 на полтора часа",
        "Поставь «PUBLIC» завтра с 15:00 до 16:00 на 1,5 часа",
        "Поставь «PUBLIC» в следующий вторник с 15:00 до 16:00",
        "Поставь встречу в следующую среду с 15:00 до 16:00",
        "Поставь «PUBLIC» завтра с 15:00 до 16:00 на час с половиной",
        "Поставь «PUBLIC» завтра с 15:00 до 16:00 по времени Амстердама",
    ],
)
def test_ambiguous_missing_or_unsupported_interval_requires_clarification(text: str) -> None:
    assert is_calendar_chat_request(text)
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarTextClarification)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Добавь встречу в календарь", True),
        ("Запланируй созвон", True),
        ("Найди окно завтра", True),
        ("Не добавляй встречу завтра", False),
        ("Объясни, как добавить событие в календарь", False),
        ("Создай проект на Python", False),
        ("Напомни через 15 минут", False),
        ("Привет", False),
        ("Подбери мне фильм на завтра", False),
        ("Найди прогноз погоды на завтра", False),
        ("Предложи ресторан на завтра", False),
        ("Создай проект на Python завтра", False),
        ("Найди отчёт 2026-11-04", False),
        ("Создай «Отчёт» завтра с 15:00 до 16:00", True),
        ("Найди «Массаж» завтра с 14:00 до 20:00 на час", True),
        ("Найди фильм «Встреча» на завтра", False),
    ],
)
def test_only_calendar_imperatives_leave_normal_conversation(text: str, expected: bool) -> None:
    assert is_calendar_chat_request(text) is expected


def test_exact_dst_ambiguity_cannot_choose_a_fold() -> None:
    result = calendar_chat_intake(
        "Создай «PUBLIC» 2026-10-25 с 02:00 до 03:00",
        request_id=uuid4(),
        scope=scope("Europe/Amsterdam"),
        now=datetime(2026, 10, 24, tzinfo=UTC),
    )
    assert isinstance(result, CalendarTextClarification)


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("Поставь встречу с Димой завтра в 15:00 на час", "встречу с Димой"),
        ("Поставь на завтра встречу в 15:00 на час", "встречу"),
        ("Создай мне на завтра встречу в 15:00 на час", "встречу"),
        ("Можешь добавить встречу с Димой завтра в 15:00 на час?", "встречу с Димой"),
        ("Давай создадим созвон завтра с 15:00 до 16:00", "созвон"),
        ("Создай «Не завтра» завтра с 15:00 до 16:00", "Не завтра"),
        (
            "Пожалуйста, создай «Отчёт завтра в 15:00» завтра с 15:00 до 16:00",
            "Отчёт завтра в 15:00",
        ),
    ],
)
def test_ordinary_requests_keep_literal_title_and_exact_user_time(text: str, title: str) -> None:
    assert is_calendar_chat_request(text)
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarChatIntake)
    assert result.mode == "exact"
    assert result.request.title == title
    assert result.request.user_text == text
    assert result.request.window_start == datetime(2026, 11, 4, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "text",
    [
        "Можешь не добавлять встречу завтра",
        "Можешь объяснить, как создать встречу завтра?",
        "Давай не будем ставить встречу завтра",
    ],
)
def test_negations_and_explanations_are_not_action_requests(text: str) -> None:
    assert not is_calendar_chat_request(text)


REPORTED_NATURAL_REQUEST = "Мы завтра планируем созвон в 8 вечера, занеси его в календарь события"


@pytest.mark.parametrize(
    "text",
    [
        REPORTED_NATURAL_REQUEST,
        "Мы завтра планируем созвон в 8 вечера на час, занеси его в календарь события",
        "Занеси созвон завтра в 8 вечера на час в календарь События",
        "Запиши созвон завтра в 20:00 на час в календарь События",
        "Сатори, пожалуйста, запиши созвон завтра в 20:00 на час",
    ],
)
def test_natural_calendar_request_is_routed_even_when_fields_need_clarification(text: str) -> None:
    assert is_calendar_chat_request(text)


def test_reported_natural_request_requires_duration_without_an_implicit_hour() -> None:
    result = calendar_chat_intake(
        REPORTED_NATURAL_REQUEST, request_id=uuid4(), scope=scope(), now=NOW
    )
    assert isinstance(result, CalendarTextClarification)
    assert "duration" in result.missing


@pytest.mark.parametrize(
    "text",
    [
        "Мы завтра планируем созвон в 8 вечера на час, занеси его в календарь события",
        "Занеси созвон завтра в 8 вечера на час в календарь События",
        "Запиши созвон завтра в 20:00 на час в календарь События",
    ],
)
def test_complete_natural_request_uses_exact_evening_time_and_original_input(text: str) -> None:
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarChatIntake)
    assert result.mode == "exact"
    assert result.request.user_text == text
    assert result.request.title.casefold() == "созвон"
    assert result.request.window_start == datetime(2026, 11, 4, 17, tzinfo=UTC)
    assert result.request.window_end == datetime(2026, 11, 4, 18, tzinfo=UTC)
    assert result.request.duration_minutes == 60


@pytest.mark.parametrize(
    "text",
    [
        "Мы завтра планируем созвон в 8 вечера, но не заноси его в календарь события",
        "Не записывай созвон завтра в календарь События",
        "Объясни, как записать созвон завтра в календарь События",
        "Почему команда «занеси созвон завтра в календарь» не работает?",
        "Он написал: «Занеси созвон завтра в календарь События»",
        "Мы завтра планируем созвон в 8 вечера, обсудим календарь потом",
        "Создай проект календаря на Python",
        "Найди фильм «Созвон» на завтра",
    ],
)
def test_natural_calendar_routing_excludes_negation_quotes_and_ordinary_discussion(
    text: str,
) -> None:
    assert not is_calendar_chat_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "Занеси созвон завтра или послезавтра в 8 вечера на час в календарь События",
        "Запиши созвон завтра примерно в 8 вечера на час в календарь События",
        "Запиши созвон завтра в 8 или 9 вечера на час в календарь События",
    ],
)
def test_recognized_natural_ambiguity_never_becomes_a_guessed_effect(text: str) -> None:
    assert is_calendar_chat_request(text)
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarTextClarification)


@pytest.mark.parametrize(
    "text",
    [
        "Запиши «Созвон» завтра в 8 вечера и в 21:00 на час в календарь События",
        "Запиши «Созвон» завтра в 15:00 и в 8 вечера на час в календарь События",
        "Запиши «Созвон» завтра в 8 вечера на час каждую неделю в календарь События",
        "Запиши «Созвон» завтра в 8 вечера на час по времени Амстердама в календарь События",
        "Запиши «Созвон» завтра в 12 вечера на час в календарь События",
        "Запиши «Созвон» завтра в 8 вечера на час с половиной в календарь События",
        "Запиши «Созвон» завтра в 8 вечера на 1,5 часа в календарь События",
    ],
)
def test_natural_projection_cannot_drop_unsupported_syntax_or_choose_one_time(text: str) -> None:
    assert is_calendar_chat_request(text)
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarTextClarification)


def test_natural_title_is_never_silently_truncated_to_fit_a_proposal() -> None:
    text = f"Запиши {'С' * 501} завтра в 8 вечера на час в календарь События"
    assert is_calendar_chat_request(text)
    result = calendar_chat_intake(text, request_id=uuid4(), scope=scope(), now=NOW)
    assert isinstance(result, CalendarTextClarification)
