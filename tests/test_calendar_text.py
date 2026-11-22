"""Bounded public intake cases: exact civil time, ambiguity and clarification."""

# ruff: noqa: RUF001 - public Russian task input exercises the real parser.

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from satori.application.assistant.calendar_actions_contracts import CalendarActionScope
from satori.application.assistant.calendar_planning_contracts import CalendarPlanningRequest
from satori.application.assistant.calendar_text import (
    CalendarTextClarification,
    planning_request_from_text,
)


def scope(timezone: str = "Europe/Moscow") -> CalendarActionScope:
    return CalendarActionScope(
        identity_id=uuid4(),
        profile_id=uuid4(),
        session_id=uuid4(),
        generation=uuid4(),
        provider="synthetic_fixture",
        account_id="public-account",
        calendar_id="public-work",
        timezone=timezone,
    )


@pytest.mark.parametrize("day", ["завтра", "2026-11-04"])
def test_explicit_calendar_task_has_exact_timezone_and_original_title(day: str) -> None:
    request = planning_request_from_text(
        f"Найди «PUBLIC Массаж» {day} с 14:00 до 20:00 на час",
        request_id=uuid4(),
        scope=scope(),
        now=datetime(2026, 11, 3, 9, tzinfo=UTC),
    )
    assert isinstance(request, CalendarPlanningRequest)
    assert request.title == "PUBLIC Массаж"
    assert request.window_start == datetime(2026, 11, 4, 11, tzinfo=UTC)
    assert request.window_end == datetime(2026, 11, 4, 17, tzinfo=UTC)
    assert request.duration_minutes == 60


def test_ambiguous_days_require_clarification_before_planning() -> None:
    result = planning_request_from_text(
        "Найди «PUBLIC встреча» сегодня или завтра с 14:00 до 20:00 на час",
        request_id=uuid4(),
        scope=scope(),
        now=datetime(2026, 11, 3, 9, tzinfo=UTC),
    )
    assert isinstance(result, CalendarTextClarification)
    assert "day" in result.missing


def test_missing_effect_parameters_are_not_guessed() -> None:
    result = planning_request_from_text(
        "Создай встречу завтра вечером",
        request_id=uuid4(),
        scope=scope(),
        now=datetime(2026, 11, 3, 9, tzinfo=UTC),
    )
    assert isinstance(result, CalendarTextClarification)
    assert {"title", "time_window", "duration"} <= set(result.missing)


def test_ambiguous_dst_fold_is_not_silently_selected() -> None:
    result = planning_request_from_text(
        "Найди «PUBLIC встреча» 2026-10-25 с 02:00 до 03:00 на полчаса",
        request_id=uuid4(),
        scope=scope("Europe/Amsterdam"),
        now=datetime(2026, 10, 24, 9, tzinfo=UTC),
    )
    assert isinstance(result, CalendarTextClarification)
    assert result.missing == ("exact_time",)
