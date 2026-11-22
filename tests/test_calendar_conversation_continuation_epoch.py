"""Queued continuation provenance; public command capture only, no Core or effects."""

from collections.abc import Iterator
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication

from satori.application.assistant.contracts import CalendarCatalog
from satori.desktop.calendar_conversation import CALENDAR_TASK_SECONDS
from tests.test_calendar_conversation_authority import (
    COMPLETE,
    EVENTS,
    MISSING,
    AuthorityCase,
    descriptor,
)

OTHER = "Запиши другой созвон завтра в 9 вечера на час в календарь События"


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.fixture
def case(application: QApplication, tmp_path: Path) -> Iterator[AuthorityCase]:
    del application
    value = AuthorityCase(tmp_path)
    yield value
    value.close()


def pump() -> None:
    for _ in range(5):
        QApplication.processEvents()


def catalog_ready_without_pump(case: AuthorityCase) -> None:
    case.calendar._coordinator.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=case.clock(),
            calendars=(descriptor(EVENTS, "События"),),
        )
    )
    case.calendar._catalog_phase = "active"
    case.calendar.changed.emit()


def queue_continuation(case: AuthorityCase) -> None:
    assert case.chat.submit(COMPLETE)
    assert case.sent == []
    catalog_ready_without_pump(case)
    # No event pumping here: this exact public R13 failure depends on the
    # catalog-ready continuation remaining queued across both focus events.
    assert case.sent == []


def assert_no_effect_start(case: AuthorityCase) -> None:
    assert case.sent == []
    assert case.reads == []
    assert case.chat.conversation_authority is None
    assert case.port.fixture.create_calls == case.port.fixture.effects == 0


def test_queued_catalog_callback_cannot_resume_after_focus_round_trip(
    case: AuthorityCase,
) -> None:
    queue_continuation(case)
    case.chat.set_foreground(False)
    case.chat.set_foreground(True)
    assert_no_effect_start(case)
    pump()
    assert_no_effect_start(case)
    assert case.chat.card is not None
    assert case.chat.card.status == "paused"
    assert case.chat.answer("продолжи")
    pump()
    assert len(case.sent) == 1
    assert cast(dict[str, object], case.sent[0]["calendar"])["operation"] == "exact_preview"
    assert case.chat.conversation_authority is not None


def test_consuming_old_callback_while_hidden_does_not_authorize_return(
    case: AuthorityCase,
) -> None:
    queue_continuation(case)
    case.chat.set_foreground(False)
    pump()
    assert_no_effect_start(case)
    case.chat.set_foreground(True)
    pump()
    assert_no_effect_start(case)
    assert case.chat.answer("продолжи")
    assert len(case.sent) == 1


def test_repeated_focus_events_do_not_rearm_old_queued_continuation(case: AuthorityCase) -> None:
    queue_continuation(case)
    for _ in range(3):
        case.chat.set_foreground(False)
        case.chat.set_foreground(True)
    pump()
    case.chat.tick()
    case.calendar.changed.emit()
    pump()
    assert_no_effect_start(case)
    assert case.chat.answer("продолжи")
    pump()
    assert len(case.sent) == 1


def test_late_catalog_completion_after_suspension_is_not_a_new_user_input(
    case: AuthorityCase,
) -> None:
    assert case.chat.submit(COMPLETE)
    case.chat.set_foreground(False)
    case.chat.set_foreground(True)
    catalog_ready_without_pump(case)
    pump()
    assert_no_effect_start(case)
    assert case.chat.answer("продолжи")
    assert len(case.sent) == 1


def test_old_queued_callback_cannot_replace_or_retire_a_new_task_request(
    case: AuthorityCase,
) -> None:
    queue_continuation(case)
    assert case.chat.submit(OTHER)
    request_id = case.chat.active_request_id
    authority = case.chat.conversation_authority
    assert request_id is not None
    assert authority is not None
    assert authority.request.title == "другой созвон"
    assert len(case.sent) == 1
    pump()
    assert case.chat.active_request_id == request_id
    assert case.chat.conversation_authority == authority
    assert len(case.sent) == 1
    assert case.port.fixture.effects == 0


def test_old_session_callback_cannot_advance_current_session_task(case: AuthorityCase) -> None:
    queue_continuation(case)
    session = str(uuid4())
    case.chat.bind_context(str(case.identity), session, case.reasoner.provider_id)
    assert case.chat.submit(OTHER)
    request_id = case.chat.active_request_id
    assert request_id is not None
    assert len(case.sent) == 1
    pump()
    assert case.chat.active_request_id == request_id
    assert len(case.sent) == 1
    authority = case.chat.conversation_authority
    assert authority is not None
    assert str(authority.request.scope.session_id) == session


@pytest.mark.parametrize("boundary", ["profile", "close", "expiry", "clear"])
def test_queued_continuation_never_crosses_task_lifecycle_boundary(
    case: AuthorityCase,
    boundary: str,
) -> None:
    queue_continuation(case)
    if boundary == "profile":
        path = case.root / "replacement-profile"
        path.mkdir(mode=0o700)
        case.chat.set_profile(path)
    elif boundary == "close":
        case.chat.close()
    elif boundary == "expiry":
        case.clock.tick += CALENDAR_TASK_SECONDS + 1
    else:
        case.chat.clear()
    pump()
    assert_no_effect_start(case)
    assert case.chat.card is None
    assert not case.chat.answer("продолжи")


def test_hidden_answer_cannot_release_resume_latch(case: AuthorityCase) -> None:
    queue_continuation(case)
    case.chat.set_foreground(False)
    assert not case.chat.answer("продолжи")
    case.chat.set_foreground(True)
    pump()
    assert_no_effect_start(case)
    assert case.chat.answer("продолжи")
    assert len(case.sent) == 1


def test_unrelated_input_or_affirmation_does_not_release_resume_latch(
    case: AuthorityCase,
) -> None:
    queue_continuation(case)
    case.chat.set_foreground(False)
    case.chat.set_foreground(True)
    assert not case.chat.answer("Расскажи, что такое календарь")
    assert not case.chat.answer("да")
    pump()
    assert_no_effect_start(case)
    assert case.chat.answer("продолжи")
    assert len(case.sent) == 1


def test_valid_missing_field_answer_can_resume_without_an_extra_continue(
    case: AuthorityCase,
) -> None:
    assert case.chat.submit(MISSING)
    case.chat.set_foreground(False)
    case.chat.set_foreground(True)
    assert case.chat.answer("да")
    pump()
    assert_no_effect_start(case)
    assert case.chat.answer("на час")
    catalog_ready_without_pump(case)
    pump()
    assert len(case.sent) == 1
    assert case.chat.conversation_authority is not None
    assert case.port.fixture.effects == 0
