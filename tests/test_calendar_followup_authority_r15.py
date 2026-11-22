"""Natural original requests and corrections reach one public fixture effect, never a model."""

# ruff: noqa: RUF001 - literal Russian scheduling and correction fixtures.

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication

from satori.application.assistant.contracts import CalendarCatalog
from satori.desktop.calendar_conversation import CALENDAR_TASK_SECONDS
from tests.test_calendar_conversation_authority import (
    EVENTS,
    AuthorityCase,
    descriptor,
)

REPORTED = "привет, у меня завтра созвон в 8 вечера, внеси его в календарь события"
CORRECTION = "я же написал, что завтра, 5 ноября"


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.fixture
def case(application: QApplication, tmp_path: Path) -> Iterator[AuthorityCase]:
    del application
    value = AuthorityCase(tmp_path)
    yield value
    value.close()


def start(case: AuthorityCase, text: str = REPORTED, *, title: str = "созвон") -> None:
    assert case.chat.submit(text)
    fields = case.chat.fields
    assert fields.title == title
    assert fields.day == "2026-11-05"
    assert fields.starts_at == "20:00"
    assert fields.duration_minutes is None
    assert fields.requested_calendar_title is not None
    assert fields.requested_calendar_title.casefold() == "события"
    assert not fields.needs_clarification
    assert case.chat.pending_clarification is not None
    assert case.chat.pending_clarification.waiting_for == "end"
    assert case.sent == []
    assert case.reads == []
    assert case.port.fixture.effects == 0


def finish_one(
    case: AuthorityCase,
    *,
    day: int = 5,
    title: str = "созвон",
    start_hour: int = 20,
    catalog_ready: bool = False,
) -> None:
    if not catalog_ready:
        case.calendar.complete_catalog((descriptor(EVENTS, "События"),))
    assert len(case.sent) == 1
    authority = case.chat.conversation_authority
    assert authority is not None
    assert authority.request.title == title
    assert authority.request.window_start == datetime(2026, 11, day, start_hour - 3, tzinfo=UTC)
    assert authority.request.window_end == datetime(2026, 11, day, start_hour - 2, tzinfo=UTC)
    assert authority.request.scope.calendar_id == EVENTS.calendar_id
    asyncio.run(case.finish())
    assert case.port.fixture.create_calls == case.port.fixture.effects == 1
    assert case.reasoner.calls == 0
    assert len(case.reads) >= 3
    assert case.chat.card is not None
    assert case.chat.card.status == "created"


@pytest.mark.parametrize(
    ("text", "title"),
    [
        (REPORTED, "созвон"),
        ("Привет! У меня завтра созвон в 8 вечера, добавь его в календарь События", "созвон"),
        ("Добавь завтра в 8 вечера созвон в календарь События", "созвон"),
        ("Запиши созвон в 8 вечера завтра в календарь События", "созвон"),
        ("Созвон завтра в 8 вечера, запиши его в календарь События", "Созвон"),
        ("Внеси в календарь «События» созвон завтра в 8 вечера", "созвон"),
    ],
)
def test_full_natural_request_and_one_duration_reply_reach_exactly_one_effect(
    case: AuthorityCase,
    text: str,
    title: str,
) -> None:
    start(case, text, title=title)
    assert case.chat.answer("на час")
    assert case.port.fixture.effects == 0
    finish_one(case, title=title)


def test_reported_date_correction_keeps_other_fields_and_original_task_expiry(
    case: AuthorityCase,
) -> None:
    start(case)
    original = case.chat.fields
    pending = case.chat.pending_clarification
    assert pending is not None
    case.clock.tick += 7
    assert case.chat.answer(CORRECTION)
    assert case.chat.fields.model_dump(exclude={"day_expressions"}) == original.model_dump(
        exclude={"day_expressions"}
    )
    updated = case.chat.pending_clarification
    assert updated is not None
    assert updated.task_id == pending.task_id
    assert updated.expires_at_monotonic == pending.expires_at_monotonic
    assert updated.waiting_for == "end"
    assert case.sent == []
    assert case.chat.answer("на час")
    finish_one(case)


def test_consistent_aliases_after_midnight_use_original_civil_anchor(case: AuthorityCase) -> None:
    case.clock.now = datetime(2026, 11, 4, 20, 59, tzinfo=UTC)
    start(case)
    pending = case.chat.pending_clarification
    assert pending is not None
    case.clock.now += timedelta(minutes=2)
    case.clock.tick += 120
    assert case.chat.answer(CORRECTION)
    assert case.chat.fields.day == "2026-11-05"
    assert case.chat.pending_clarification is not None
    assert case.chat.pending_clarification.expires_at_monotonic == pending.expires_at_monotonic
    assert case.chat.answer("на час")
    finish_one(case)


def test_conflicting_date_aliases_stay_blocking_until_date_is_resolved(
    case: AuthorityCase,
) -> None:
    start(case)
    original = case.chat.fields
    assert case.chat.answer("я же написал, что завтра, 6 ноября")
    assert case.chat.fields.title == original.title
    assert case.chat.fields.starts_at == original.starts_at
    assert case.chat.fields.requested_calendar_title == original.requested_calendar_title
    assert case.chat.card is not None
    assert case.chat.card.status == "clarification"
    assert case.chat.answer("на час")
    assert case.chat.fields.duration_minutes == 60
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert case.port.fixture.effects == 0
    assert case.chat.answer("к этому событию: завтра, 5 ноября")
    finish_one(case)


def test_explicit_date_replacement_changes_only_user_corrected_field(case: AuthorityCase) -> None:
    start(case)
    original = case.chat.fields
    assert case.chat.answer("к этому событию: дата 6 ноября")
    assert case.chat.fields.day == "2026-11-06"
    assert case.chat.fields.title == original.title
    assert case.chat.fields.starts_at == original.starts_at
    assert case.chat.fields.requested_calendar_title == original.requested_calendar_title
    assert case.sent == []
    assert case.chat.answer("на час")
    finish_one(case, day=6)


def test_one_related_reply_can_supply_date_alias_and_duration_without_field_loss(
    case: AuthorityCase,
) -> None:
    start(case)
    assert case.chat.answer("к этому событию: завтра, 5 ноября, на час")
    assert case.chat.fields.day == "2026-11-05"
    assert case.chat.fields.duration_minutes == 60
    assert case.chat.fields.title == "созвон"
    finish_one(case)


def test_date_correction_before_queued_preview_retires_old_interval_and_creates_only_new_day(
    case: AuthorityCase,
) -> None:
    complete = REPORTED.replace("в 8 вечера", "в 8 вечера на час")
    assert case.chat.submit(complete)
    assert case.chat.fields.title == "созвон"
    assert case.chat.fields.day == "2026-11-05"
    assert case.sent == []
    case.calendar._coordinator.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=case.clock(),
            calendars=(descriptor(EVENTS, "События"),),
        )
    )
    case.calendar._catalog_phase = "active"
    case.calendar.changed.emit()
    # Keep the original catalog-ready continuation queued until the correction
    # has selected a new task revision; do not seed any authored field manually.
    assert case.sent == []
    assert case.chat.answer("к этому событию: дата 6 ноября")
    assert case.chat.fields.day == "2026-11-06"
    request_id = case.chat.active_request_id
    for _ in range(5):
        QApplication.processEvents()
    assert case.chat.active_request_id == request_id
    assert len(case.sent) == 1
    finish_one(case, day=6, catalog_ready=True)


@pytest.mark.parametrize("reference", ["продолжи", "к этому событию: на час", "к созвону: на час"])
def test_after_topic_shift_only_explicit_task_reference_can_resume(
    case: AuthorityCase,
    reference: str,
) -> None:
    start(case)
    original = case.chat.fields
    pending = case.chat.pending_clarification
    assert pending is not None
    assert not case.chat.answer("Расскажи про китайскую кухню")
    case.chat.pause_for_chat()
    assert case.chat.fields == original
    assert not case.chat.answer("на час")
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert case.chat.answer(reference)
    if reference == "продолжи":
        assert case.sent == []
        assert case.chat.answer("на час")
    finish_one(case)


def test_related_date_after_topic_shift_asks_reference_instead_of_ordinary_model_fallback(
    case: AuthorityCase,
) -> None:
    start(case)
    original = case.chat.fields
    case.chat.pause_for_chat()
    assert case.chat.answer(CORRECTION)
    assert case.chat.fields == original
    assert case.chat.card is not None
    assert case.chat.card.status == "clarification"
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert not case.chat.answer("на час")
    assert case.chat.answer("к этому событию: на час")
    finish_one(case)


@pytest.mark.parametrize(
    "text",
    [
        "Объясни фразу «я же написал, что завтра, 5 ноября»",
        "Я заплатил 5 ноября за кофе",
        "Он сказал: завтра, 5 ноября",
    ],
)
def test_unrelated_or_represented_dates_do_not_update_a_pending_calendar_task(
    case: AuthorityCase,
    text: str,
) -> None:
    start(case)
    original = case.chat.fields
    assert not case.chat.answer(text)
    assert case.chat.fields == original
    assert case.sent == []
    assert case.port.fixture.effects == 0


@pytest.mark.parametrize("boundary", ["session", "profile", "expiry", "close"])
def test_date_correction_never_resurrects_retired_task(case: AuthorityCase, boundary: str) -> None:
    start(case)
    if boundary == "session":
        case.chat.bind_context(str(case.identity), str(uuid4()), case.reasoner.provider_id)
    elif boundary == "profile":
        profile = case.root / "different-profile"
        profile.mkdir(mode=0o700)
        case.chat.set_profile(profile)
    elif boundary == "expiry":
        case.clock.tick += CALENDAR_TASK_SECONDS + 1
        case.chat.tick()
    else:
        case.chat.close()
    assert not case.chat.answer(CORRECTION)
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert case.port.fixture.effects == 0


def test_calendar_correction_cannot_rearm_an_unknown_effect(case: AuthorityCase) -> None:
    start(case)
    assert case.chat.answer("на час")
    case.calendar.complete_catalog((descriptor(EVENTS, "События"),))
    asyncio.run(case.execute("exact_preview"))
    asyncio.run(case.execute("approve"))
    request_id = case.chat.active_request_id
    case.port.fixture.fail_after_create = True
    assert asyncio.run(case.execute("create"))["state"] == "unknown"
    assert case.port.fixture.effects == 1
    assert case.chat.answer(CORRECTION)
    assert case.chat.active_request_id == request_id
    assert case.chat.card is not None
    assert case.chat.card.status == "unknown"
    assert len(case.sent) == 3
    assert case.port.fixture.create_calls == 1


def test_a_date_correction_never_invents_a_missing_event_title(case: AuthorityCase) -> None:
    assert case.chat.submit("Добавь завтра в 8 вечера в календарь События")
    assert case.chat.fields.title == ""
    assert case.chat.answer(CORRECTION)
    assert case.chat.fields.title == ""
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert case.chat.answer("созвон")
    assert case.chat.fields.title == "созвон"
    assert case.chat.answer("на час")
    finish_one(case)


def test_date_repair_does_not_silently_drop_unsupported_recurrence(case: AuthorityCase) -> None:
    assert case.chat.submit(
        "привет, у меня завтра созвон в 8 вечера каждый день, внеси его в календарь События"
    )
    assert case.chat.answer(CORRECTION)
    assert case.chat.answer("на час")
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert case.port.fixture.effects == 0


@pytest.mark.parametrize(
    "text",
    [
        "Добавь созвон завтра в 20 на час или полчаса в календарь События",
        "Добавь созвон завтра в 20:00 и в 20:00 на час в календарь События",
        "Добавь два созвона завтра в 20 на час в календарь События",
    ],
)
def test_span_extraction_cannot_turn_alternatives_or_multiple_events_into_one_exact_effect(
    case: AuthorityCase,
    text: str,
) -> None:
    assert case.chat.submit(text)
    if case.calendar.refreshes:
        case.calendar.complete_catalog((descriptor(EVENTS, "События"),))
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert case.port.fixture.effects == 0


@pytest.mark.parametrize("title", ["созвон", "Созвон и итоги"])
def test_explicit_clock_repair_keeps_original_title_and_creates_only_the_chosen_interval(
    case: AuthorityCase,
    title: str,
) -> None:
    authored_title = f"«{title}»" if " и " in title else title
    assert case.chat.submit(
        f"Добавь {authored_title} завтра в 20:00 и в 21:00 на час в календарь События"
    )
    assert case.chat.fields.title == title
    assert case.chat.fields.day == "2026-11-05"
    assert case.chat.fields.starts_at == ""
    assert case.chat.fields.duration_minutes == 60
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert case.chat.answer("в 22")
    assert case.chat.fields.title == title
    assert case.chat.fields.starts_at == "22:00"
    finish_one(case, title=title, start_hour=22)


def test_multiple_clock_reply_stays_operational_until_one_clock_is_explicitly_chosen(
    case: AuthorityCase,
) -> None:
    start(case)
    original = case.chat.fields
    assert case.chat.answer("в 8 вечера и в 9 вечера")
    assert case.chat.fields.title == original.title
    assert case.chat.fields.day == original.day
    assert case.chat.fields.requested_calendar_title == original.requested_calendar_title
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert case.port.fixture.effects == 0
    assert case.chat.answer("на час")
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert case.chat.answer("в 22")
    finish_one(case, start_hour=22)


def test_multiple_clocks_in_unrelated_work_description_do_not_revise_pending_event(
    case: AuthorityCase,
) -> None:
    start(case)
    original = case.chat.fields
    assert not case.chat.answer("я работаю с 8 утра до 6 вечера")
    assert case.chat.fields == original
    assert case.sent == []
    assert case.port.fixture.effects == 0
