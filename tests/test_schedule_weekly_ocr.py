"""Authored synthetic OCR geometry only; no photograph or personal schedule fixtures."""

from datetime import date, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from satori.application.assistant.schedule_contracts import (
    ScheduleOCRBounds,
    ScheduleOCRObservation,
)
from satori.application.assistant.schedule_weekly_ocr import draft_from_weekly_ocr

MONDAY = date(2026, 11, 2)


def observation(
    text: str, column: int, *, y: float = 0.6, width: float = 0.08, confidence: float = 0.99
) -> ScheduleOCRObservation:
    center = 0.08 + column * 0.14
    return ScheduleOCRObservation(
        text=text,
        confidence=confidence,
        bounds=ScheduleOCRBounds(x=center - width / 2, y=y, width=width, height=0.03),
    )


def headers(*, style: str = "russian") -> list[ScheduleOCRObservation]:
    result: list[ScheduleOCRObservation] = []
    for index in range(7):
        day = MONDAY + timedelta(days=index)
        text = (
            f"{day.day} ноя {day.year}"
            if style == "russian"
            else day.strftime("%d.%m.%Y")
            if style == "numeric"
            else day.isoformat()
        )
        result.append(observation(text, index, y=0.86, width=0.09))
    return result


@pytest.mark.parametrize("style", ["russian", "numeric", "iso"])
def test_seven_full_date_headers_assign_only_exact_column_below(style: str) -> None:
    draft = draft_from_weekly_ocr(
        [*headers(style=style), observation("09:00-17:00", 0), observation("11:00-19:00", 6)],
        timezone="Europe/Moscow",
        legend="Explicit hours; synthetic fixture",
    )
    assert [row.date for row in draft.rows] == [MONDAY, MONDAY + timedelta(days=6)]
    assert draft.year == 2026
    assert draft.month == 11
    assert draft.ambiguities == ("human_review_required",)


def test_bbox_coordinates_are_bottom_origin() -> None:
    draft = draft_from_weekly_ocr(
        [*headers(), observation("09:00-17:00", 0, y=0.93)],
        timezone="Europe/Moscow",
        legend="Explicit hours",
    )
    assert not draft.rows
    assert "time_range_not_below_header" in draft.ambiguities


def test_overnight_never_inferred_from_zero_end_time() -> None:
    draft = draft_from_weekly_ocr(
        [*headers(), observation("12:00-20:00", 0), observation("16:00-00:00", 1)],
        timezone="Europe/Moscow",
        legend="Explicit hours",
    )
    assert len(draft.rows) == 1
    assert draft.rows[0].date == MONDAY
    assert "explicit_end_date_required" in draft.ambiguities


def test_missing_legend_keeps_only_suggestions_with_required_correction() -> None:
    draft = draft_from_weekly_ocr(
        [*headers(), observation("09:00-17:00", 0)], timezone="Europe/Moscow"
    )
    assert len(draft.rows) == 1
    assert "legend_required" in draft.ambiguities
    assert "human_review_required" in draft.ambiguities


@pytest.mark.parametrize(("year", "month"), [(2025, None), (None, 10), (True, 11)])
def test_explicit_header_cannot_be_overridden_by_conflicting_fields(
    year: int | None, month: int | None
) -> None:
    draft = draft_from_weekly_ocr(
        [*headers(), observation("09:00-17:00", 0)],
        timezone="Europe/Moscow",
        year=year,
        month=month,
        legend="Explicit hours",
    )
    assert not draft.rows
    assert "header_metadata_mismatch" in draft.ambiguities


@pytest.mark.parametrize("count", [0, 6, 8])
def test_exactly_seven_full_date_headers_required(count: int) -> None:
    values = headers()
    values = values[:count] if count < 7 else [*values, observation("9 ноя 2026", 0, y=0.4)]
    draft = draft_from_weekly_ocr([*values, observation("09:00-17:00", 0)], timezone="UTC")
    assert not draft.rows
    assert "seven_full_date_headers_required" in draft.ambiguities


def test_date_headers_need_no_invented_year_or_month() -> None:
    values = [observation(str(index + 2), index, y=0.86) for index in range(7)]
    draft = draft_from_weekly_ocr(
        [*values, observation("09:00-17:00", 0)], timezone="UTC", year=2026, month=11
    )
    assert not draft.rows
    assert "seven_full_date_headers_required" in draft.ambiguities


@pytest.mark.parametrize(
    "kind", ["order", "nonconsecutive", "weekday", "mixed_row", "low_confidence"]
)
def test_ambiguous_headers_fail_closed(kind: str) -> None:
    values = headers()
    if kind == "order":
        values[0], values[1] = (
            observation("3 ноя 2026", 0, y=0.86),
            observation("2 ноя 2026", 1, y=0.86),
        )
    elif kind == "nonconsecutive":
        values[2] = observation("12 ноя 2026", 2, y=0.86)
    elif kind == "weekday":
        values[1] = observation("Пн, 3 ноя 2026", 1, y=0.86)
    elif kind == "mixed_row":
        values[2] = observation("4 ноя 2026", 2, y=0.5)
    else:
        values[2] = observation("4 ноя 2026", 2, y=0.86, confidence=0.89)
    draft = draft_from_weekly_ocr([*values, observation("09:00-17:00", 0)], timezone="UTC")
    assert not draft.rows


def test_valid_weekday_prefix_is_checked_against_explicit_date() -> None:
    values = headers()
    values[0] = observation("Пн, 2 ноября 2026", 0, y=0.86)
    draft = draft_from_weekly_ocr([*values, observation("09:00-17:00", 0)], timezone="UTC")
    assert len(draft.rows) == 1


@pytest.mark.parametrize(
    "kind", ["low_confidence", "edge", "overlapping", "multiple", "duplicate_person_rows"]
)
def test_uncertain_or_multiple_ranges_are_never_selected_arbitrarily(kind: str) -> None:
    times = [observation("09:00-17:00", 0)]
    if kind == "low_confidence":
        times = [observation("09:00-17:00", 0, confidence=0.8)]
    elif kind in {"edge", "overlapping"}:
        times.append(
            ScheduleOCRObservation(
                text="10:00-18:00",
                confidence=0.99,
                bounds=ScheduleOCRBounds(x=0.125, y=0.5, width=0.08, height=0.03),
            )
        )
    elif kind == "multiple":
        times.append(observation("10:00-18:00", 0, y=0.4))
    else:
        times.append(observation("09:00-17:00", 0, y=0.4, confidence=0.7))
    draft = draft_from_weekly_ocr([*headers(), *times], timezone="UTC")
    assert not draft.rows


def test_shift_codes_and_person_labels_never_become_times_or_identity() -> None:
    draft = draft_from_weekly_ocr(
        [
            *headers(),
            observation("PUBLIC PERSON", 0),
            observation("DAY", 1),
            observation("NIGHT", 2),
        ],
        timezone="UTC",
        legend="Unknown codes",
    )
    assert not draft.rows
    assert "manual_rows_required" in draft.ambiguities
    assert "PUBLIC PERSON" not in repr(draft)


@pytest.mark.parametrize(
    "changes",
    [
        {"x": -0.1},
        {"y": 1.01},
        {"width": 0},
        {"height": -1},
        {"x": 0.9, "width": 0.2},
        {"y": 0.9, "height": 0.2},
        {"x": float("nan")},
        {"height": float("inf")},
        {"x": True},
    ],
)
def test_ocr_bounds_are_finite_positive_and_contained(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ScheduleOCRBounds(**{"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1, **changes})


def test_ocr_observation_hides_source_text_and_rejects_bool_confidence() -> None:
    value = observation("PUBLIC SOURCE", 0)
    assert "PUBLIC SOURCE" not in repr(value)
    with pytest.raises(ValidationError):
        ScheduleOCRObservation(text="PUBLIC", confidence=True, bounds=value.bounds)


def test_weekly_grid_output_limit_has_no_partial_guessed_rows() -> None:
    values = [observation("irrelevant", 0)] * 201
    draft = draft_from_weekly_ocr(values, timezone="UTC")
    assert not draft.rows
    assert draft.ambiguities == ("ocr_limit_reached", "manual_rows_required")
