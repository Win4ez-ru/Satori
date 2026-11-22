"""Conservative spatial suggestions for one fully dated, seven-column weekly grid."""

import re
from collections.abc import Sequence
from datetime import date, time, timedelta
from itertools import pairwise

from satori.application.assistant.contracts import require_timezone
from satori.application.assistant.schedule_contracts import (
    ScheduleDraft,
    ScheduleOCRObservation,
    ScheduleRow,
)

_MONTHS = {
    "янв": 1,
    "январь": 1,
    "января": 1,
    "фев": 2,
    "февраль": 2,
    "февраля": 2,
    "мар": 3,
    "март": 3,
    "марта": 3,
    "апр": 4,
    "апрель": 4,
    "апреля": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июнь": 6,
    "июня": 6,
    "июл": 7,
    "июль": 7,
    "июля": 7,
    "авг": 8,
    "август": 8,
    "августа": 8,
    "сен": 9,
    "сент": 9,
    "сентябрь": 9,
    "сентября": 9,
    "окт": 10,
    "октябрь": 10,
    "октября": 10,
    "ноя": 11,
    "нояб": 11,
    "ноябрь": 11,
    "ноября": 11,
    "дек": 12,
    "декабрь": 12,
    "декабря": 12,
}
_WEEKDAY = (
    r"(?:(?P<weekday>пн|вт|\u0441\u0440|чт|пт|\u0441\u0431|вс|понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)"
    r"[,.]?\s+)?"
)
_WEEKDAYS = {
    "пн": 0,
    "понедельник": 0,
    "вт": 1,
    "вторник": 1,
    "\u0441\u0440": 2,
    "среда": 2,
    "чт": 3,
    "четверг": 3,
    "пт": 4,
    "пятница": 4,
    "\u0441\u0431": 5,
    "суббота": 5,
    "вс": 6,
    "воскресенье": 6,
}
_WORD_HEADER = re.compile(
    _WEEKDAY + r"(?P<day>\d{1,2})\s+(?P<month>[\u0430-\u044f\u0451]+)\.?\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
_DOT_HEADER = re.compile(
    _WEEKDAY + r"(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{4})", re.IGNORECASE
)
_ISO_HEADER = re.compile(
    _WEEKDAY + r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})", re.IGNORECASE
)
_RANGE = re.compile(r"(?P<start>\d{2}:\d{2})\s*[-\u2013\u2014]\s*(?P<end>\d{2}:\d{2})")
_MIN_CONFIDENCE = 0.9


def _header(text: str) -> date | None:
    for pattern in (_WORD_HEADER, _DOT_HEADER, _ISO_HEADER):
        match = pattern.fullmatch(text.strip())
        if match is None:
            continue
        try:
            month_text = match.group("month").lower()
            month = int(month_text) if month_text.isdecimal() else _MONTHS[month_text]
            value = date(int(match.group("year")), month, int(match.group("day")))
            weekday = match.group("weekday")
            if weekday is not None and _WEEKDAYS[weekday.lower()] != value.weekday():
                return None
            return value if 1970 <= value.year <= 2100 else None
        except (ValueError, KeyError):
            return None
    return None


def draft_from_weekly_ocr(
    observations: Sequence[ScheduleOCRObservation],
    *,
    timezone: str,
    year: int | None = None,
    month: int | None = None,
    legend: str = "",
) -> ScheduleDraft:
    """Suggest only unambiguous explicit hours below seven full-date week headers.

    A box touching a column edge, low confidence, repeated ranges in a column, a
    missing/full-date header or an implicit overnight interval is never admitted as
    a row. Unknown labels are not interpreted as a person, shift code or instruction.
    Every returned row is a suggestion requiring human review before any create plan.
    """

    issues = ["human_review_required"]
    if not legend.strip():
        issues.append("legend_required")
    if len(observations) > 200:
        return ScheduleDraft(ambiguities=("ocr_limit_reached", "manual_rows_required"))
    try:
        require_timezone(timezone)
    except ValueError:
        return ScheduleDraft(ambiguities=("timezone_required", "manual_rows_required"))
    headers = [(value, item) for item in observations if (value := _header(item.text)) is not None]
    if len(headers) != 7:
        return ScheduleDraft(
            ambiguities=("seven_full_date_headers_required", "manual_rows_required")
        )
    headers.sort(key=lambda item: item[1].bounds.x + item[1].bounds.width / 2)
    days = [day for day, _ in headers]
    if days[0].weekday() != 0 or days != [days[0] + timedelta(days=index) for index in range(7)]:
        return ScheduleDraft(ambiguities=("ambiguous_week_headers", "manual_rows_required"))
    if (year is not None and (type(year) is not int or year != days[0].year)) or (
        month is not None and (type(month) is not int or month != days[0].month)
    ):
        return ScheduleDraft(ambiguities=("header_metadata_mismatch", "manual_rows_required"))
    if any(item.confidence < _MIN_CONFIDENCE for _, item in headers):
        return ScheduleDraft(ambiguities=("uncertain_date_header", "manual_rows_required"))
    centers = [item.bounds.x + item.bounds.width / 2 for _, item in headers]
    distances = [right - left for left, right in pairwise(centers)]
    if min(distances) < 0.035 or max(distances) > min(distances) * 1.5:
        return ScheduleDraft(ambiguities=("ambiguous_column_geometry", "manual_rows_required"))
    header_midpoints = [item.bounds.y + item.bounds.height / 2 for _, item in headers]
    header_height = max(item.bounds.height for _, item in headers)
    if max(header_midpoints) - min(header_midpoints) > max(0.015, header_height * 0.65):
        return ScheduleDraft(ambiguities=("ambiguous_header_row", "manual_rows_required"))
    edges = [max(0.0, centers[0] - distances[0] / 2)]
    edges.extend((left + right) / 2 for left, right in pairwise(centers))
    edges.append(min(1.0, centers[-1] + distances[-1] / 2))
    margin = max(0.004, min(distances) * 0.05)
    if any(
        item.bounds.x < edges[index] + margin
        or item.bounds.x + item.bounds.width > edges[index + 1] - margin
        for index, (_, item) in enumerate(headers)
    ):
        return ScheduleDraft(ambiguities=("ambiguous_header_bounds", "manual_rows_required"))
    below = min(item.bounds.y for _, item in headers) - max(0.005, header_height * 0.15)
    candidates: dict[int, list[ScheduleOCRObservation]] = {}
    blocked_columns: set[int] = set()
    for item in observations:
        match = _RANGE.fullmatch(item.text.strip())
        if match is None:
            continue
        box = item.bounds
        if box.y + box.height > below:
            issues.append("time_range_not_below_header")
            continue
        fits = [
            index
            for index in range(7)
            if box.x >= edges[index] + margin and box.x + box.width <= edges[index + 1] - margin
        ]
        if len(fits) != 1:
            issues.append("ambiguous_time_column")
            blocked_columns.update(
                index
                for index in range(7)
                if box.x < edges[index + 1] and box.x + box.width > edges[index]
            )
            continue
        candidates.setdefault(fits[0], []).append(item)
    rows: list[ScheduleRow] = []
    for index in sorted(candidates):
        items = candidates[index]
        if index in blocked_columns:
            continue
        if len(items) != 1:
            issues.append("multiple_ranges_in_column")
            continue
        if items[0].confidence < _MIN_CONFIDENCE:
            issues.append("uncertain_time_range")
            continue
        match = _RANGE.fullmatch(items[0].text.strip())
        assert match is not None
        try:
            start, end = (
                time.fromisoformat(match.group("start")),
                time.fromisoformat(match.group("end")),
            )
            if end <= start:
                issues.append("explicit_end_date_required")
                continue
            row = ScheduleRow(
                date=days[index], start=start, end=end, end_date=days[index], title="Рабочая смена"
            )
            row.instants(timezone)
            rows.append(row)
        except ValueError:
            issues.append("ambiguous_time_range")
    if not rows:
        issues.append("manual_rows_required")
    return ScheduleDraft(
        rows=tuple(rows),
        ambiguities=tuple(dict.fromkeys(issues)),
        year=days[0].year,
        month=days[0].month,
    )
