"""Source-free home status from synthetic Calendar owners; no native or account I/O."""

import json
import os
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarOccurrence,
    CalendarReadResult,
    CalendarSourceRef,
)
from satori.application.assistant.dialogue_contracts import CalendarReadProposal, SourceInterval
from satori.desktop.calendar_controller import CalendarController
from satori.desktop.ui import DesktopApplication

NOW = datetime(2026, 10, 29, 9, tzinfo=UTC)
SOURCE = CalendarSourceRef(
    account_id="SYNTHETIC PRIVATE ACCOUNT ID", calendar_id="SYNTHETIC PRIVATE CALENDAR ID"
)
TITLE = "SYNTHETIC PRIVATE EVENT TITLE"


@dataclass
class Clock:
    value: datetime = NOW

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    assert instance is None or isinstance(instance, QApplication)
    return instance or DesktopApplication([])


@pytest.fixture
def view(
    app: QApplication, tmp_path: Path
) -> Iterator[tuple[CalendarController, Clock, list[str]]]:
    del app
    clock = Clock()
    calls: list[str] = []

    def forbidden_helper() -> None:
        calls.append("helper")
        raise AssertionError("projection must not request a native helper")

    controller = CalendarController(tmp_path, clock=clock, helper_command=forbidden_helper)
    controller.bind_dialogue(uuid4())
    yield controller, clock, calls
    controller.close()
    controller.deleteLater()
    QApplication.processEvents()


def select(controller: CalendarController, clock: Clock) -> None:
    controller._coordinator.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=clock(),
            calendars=(
                CalendarDescriptor(
                    source=SOURCE,
                    title="SYNTHETIC PRIVATE CALENDAR NAME",
                    account_title="SYNTHETIC PRIVATE ACCOUNT NAME",
                ),
            ),
        )
    )
    controller._catalog_phase = "active"
    controller.permission_status = "full_access"
    assert controller.use_calendars((SOURCE,))


def result(
    controller: CalendarController,
    clock: Clock,
    *,
    empty: bool = False,
    partial: bool = False,
) -> None:
    request = controller._coordinator.prepare_day(date(2026, 10, 29), "UTC")
    event = CalendarOccurrence(
        source=SOURCE,
        event_id="SYNTHETIC PRIVATE EVENT ID",
        occurrence_id="SYNTHETIC PRIVATE OCCURRENCE ID",
        title=TITLE,
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
        timezone="UTC",
    )
    snapshot = controller._coordinator.accept_result(
        request,
        CalendarReadResult(
            request_id=request.request_id,
            observed_at=clock(),
            occurrences=() if empty else (event,),
            status="partial" if partial else "ok",
            reason="limit_reached" if partial else None,
        ),
    )
    controller.last_successful_read_at = snapshot.observed_at
    controller._show_snapshot = True
    controller._notify("partial" if partial else "empty" if empty else "ready")


def test_startup_projection_has_no_authority_or_native_io(
    view: tuple[CalendarController, Clock, list[str]],
) -> None:
    controller, _, calls = view
    audit = controller.audit_events
    projection = controller.status_projection
    assert projection.connection == "disconnected"
    assert projection.permission == "unknown"
    assert projection.selected_count == 0
    assert projection.freshness == "not_read"
    assert projection.outcome == "none"
    assert projection.last_successful_read_at is None
    assert projection.can_connect
    assert not projection.can_refresh
    assert not projection.can_query_dialogue
    assert controller.audit_events == audit
    assert controller.grant is None
    assert calls == []


def test_selected_grant_is_connected_but_does_not_imply_fresh_data(
    view: tuple[CalendarController, Clock, list[str]],
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    grant, audit = controller.grant, controller.audit_events
    projection = controller.status_projection
    assert projection.connection == "connected"
    assert projection.permission == "full_access"
    assert projection.selected_count == 1
    assert projection.freshness == "not_read"
    assert projection.outcome == "none"
    assert projection.can_refresh
    assert projection.can_query_dialogue
    assert projection.observed_at is None
    assert projection.fresh_until is None
    assert projection.age_seconds is None
    assert controller.grant == grant
    assert controller.audit_events == audit
    assert calls == []


@pytest.mark.parametrize(
    ("empty", "partial", "outcome"),
    [
        (False, False, "ok"),
        (True, False, "empty"),
        (False, True, "partial"),
        (True, True, "partial"),
    ],
)
def test_complete_empty_and_partial_snapshots_have_distinct_metadata(
    view: tuple[CalendarController, Clock, list[str]],
    empty: bool,
    partial: bool,
    outcome: Literal["ok", "empty", "partial"],
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    result(controller, clock, empty=empty, partial=partial)
    clock.value += timedelta(seconds=7)
    projection = controller.status_projection
    assert projection.connection == "connected"
    assert projection.freshness == "fresh"
    assert projection.outcome == outcome
    assert projection.observed_at == NOW
    assert projection.fresh_until == NOW + timedelta(minutes=5)
    assert projection.last_successful_read_at == NOW
    assert projection.age_seconds == 7
    if partial:
        assert "неполный" in projection.status_text
        assert "событий нет" not in projection.status_text
    assert calls == []


def test_transient_dialogue_answer_supplies_freshness_without_exposing_its_content(
    view: tuple[CalendarController, Clock, list[str]],
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    session = controller._dialogue
    chat = controller.dialogue_chat_id
    assert session is not None
    assert chat is not None
    proposal = CalendarReadProposal(
        proposal_id=uuid4(),
        principal=controller.principal,
        chat_id=chat,
        turn_id=uuid4(),
        kind="agenda",
        day=date(2026, 10, 29),
        timezone="UTC",
        window=SourceInterval(
            starts_at=datetime(2026, 10, 29, tzinfo=UTC),
            ends_at=datetime(2026, 10, 30, tzinfo=UTC),
        ),
    )
    pending = session.prepare(session.consent(proposal))
    answer = session.accept(
        pending,
        CalendarReadResult(request_id=pending.request.request_id, observed_at=clock()),
    )
    controller._had_dialogue_answer = True
    controller._show_snapshot = False
    controller._dialogue_status = answer.receipt.status
    controller.last_successful_read_at = clock()
    controller._notify("grant_ready")
    projection = controller.status_projection
    assert controller.snapshot is None
    assert projection.freshness == "fresh"
    assert projection.outcome == "empty"
    assert projection.observed_at == NOW
    assert projection.fresh_until == NOW + timedelta(minutes=5)
    assert calls == []


def test_projection_reports_expired_snapshot_immediately_before_timer_tick(
    view: tuple[CalendarController, Clock, list[str]],
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    result(controller, clock)
    clock.value += timedelta(minutes=5)
    projection = controller.status_projection
    assert projection.connection == "connected"
    assert projection.freshness == "stale"
    assert projection.outcome == "none"
    assert projection.observed_at is None
    assert projection.fresh_until is None
    assert projection.can_refresh
    assert controller.snapshot is None
    assert calls == []


def test_expired_grant_is_not_an_empty_result_or_a_renewed_capability(
    view: tuple[CalendarController, Clock, list[str]],
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    result(controller, clock)
    clock.value += timedelta(hours=1)
    projection = controller.status_projection
    assert projection.connection == "session_expired"
    assert projection.selected_count == 0
    assert projection.outcome == "none"
    assert not projection.can_refresh
    assert not projection.can_query_dialogue
    assert controller.selected_calendars == ()
    assert controller.snapshot is None
    assert calls == []


@pytest.mark.parametrize(
    ("status", "connection"),
    [
        ("permission_denied", "permission_denied"),
        ("permission_restricted", "permission_denied"),
        ("provider_unavailable", "unavailable"),
        ("provider_error", "error"),
        ("request_expired", "error"),
        ("invalid_response", "error"),
    ],
)
def test_closed_failures_never_become_empty_success(
    view: tuple[CalendarController, Clock, list[str]], status: str, connection: str
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    controller._failure(status)
    projection = controller.status_projection
    assert projection.connection == connection
    assert projection.freshness == "not_read"
    assert projection.outcome == "none"
    assert projection.selected_count == 0
    assert not projection.can_refresh
    assert not projection.can_query_dialogue
    assert calls == []


def test_projection_is_immutable_and_contains_no_source_or_authority_identifiers(
    view: tuple[CalendarController, Clock, list[str]],
) -> None:
    controller, clock, _ = view
    select(controller, clock)
    result(controller, clock)
    grant = controller.grant
    assert grant is not None
    projection = controller.status_projection
    serializations = repr(projection) + json.dumps(asdict(projection), default=str)
    for sensitive in (
        "SYNTHETIC PRIVATE",
        TITLE,
        SOURCE.account_id,
        SOURCE.calendar_id,
        str(grant.grant_id),
        str(controller.principal.profile_id),
        str(controller.principal.session_id),
    ):
        assert sensitive not in serializations
    with pytest.raises(FrozenInstanceError):
        projection.selected_count = 99  # type: ignore[misc]


def test_stale_catalog_does_not_remove_live_session_query_or_extend_its_grant(
    view: tuple[CalendarController, Clock, list[str]],
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    grant = controller.grant
    clock.value += timedelta(minutes=6)
    audit = controller.audit_events
    projection = controller.status_projection
    assert projection.catalog_state == "stale"
    assert projection.selected_count == 1
    assert projection.can_query_dialogue
    assert projection.can_refresh
    assert projection.freshness == "not_read"
    assert controller.catalog == ()
    assert tuple(item.source for item in controller.selected_calendars) == (SOURCE,)
    assert controller.grant == grant
    assert controller.audit_events == audit
    assert calls == []


def test_profile_change_drops_selection_freshness_and_query_authority(
    view: tuple[CalendarController, Clock, list[str]], tmp_path: Path
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    result(controller, clock)
    previous = controller.principal
    controller.set_profile(tmp_path / "other-profile")
    projection = controller.status_projection
    assert controller.principal != previous
    assert projection.connection == "disconnected"
    assert projection.permission == "unknown"
    assert projection.selected_count == 0
    assert projection.freshness == "not_read"
    assert projection.outcome == "none"
    assert projection.last_successful_read_at is None
    assert not projection.can_refresh
    assert not projection.can_query_dialogue
    assert calls == []


def test_clock_reversal_is_error_and_cannot_keep_refresh_authority(
    view: tuple[CalendarController, Clock, list[str]],
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    result(controller, clock)
    clock.value -= timedelta(seconds=1)
    projection = controller.status_projection
    assert projection.connection == "error"
    assert projection.selected_count == 0
    assert projection.outcome == "none"
    assert not projection.can_refresh
    assert not projection.can_query_dialogue
    assert calls == []


@pytest.mark.parametrize(
    ("empty", "partial", "expected"),
    [(False, False, "fresh"), (True, False, "fresh"), (True, True, "partial")],
)
def test_common_home_projection_is_content_free_and_preserves_partial(
    view: tuple[CalendarController, Clock, list[str]],
    empty: bool,
    partial: bool,
    expected: str,
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    result(controller, clock, empty=empty, partial=partial)
    grant, audit = controller.grant, controller.audit_events
    common = controller.source_status
    assert common.source == "calendar"
    assert common.connection == "connected"
    assert common.selected_count == 1
    assert common.snapshot == expected
    assert common.observed_at == NOW
    assert common.problem == "none"
    assert "SYNTHETIC PRIVATE" not in common.model_dump_json()
    assert "SYNTHETIC PRIVATE" not in repr(common)
    assert controller.grant == grant
    assert controller.audit_events == audit
    assert calls == []


@pytest.mark.parametrize(
    ("code", "connection", "problem"),
    [
        ("permission_denied", "error", "permission"),
        ("permission_not_determined", "error", "permission"),
        ("provider_unavailable", "unavailable", "unavailable"),
        ("provider_error", "error", "transport"),
        ("request_expired", "error", "transport"),
        ("invalid_response", "error", "rejected"),
        ("clock_invalid", "error", "rejected"),
    ],
)
def test_common_home_failure_never_becomes_a_fresh_snapshot(
    view: tuple[CalendarController, Clock, list[str]], code: str, connection: str, problem: str
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    result(controller, clock)
    controller._failure(code)
    common = controller.source_status
    assert common.connection == connection
    assert common.problem == problem
    assert common.snapshot in {"unknown", "not_read"}
    assert common.observed_at is None
    assert common.selected_count == 0
    assert calls == []


def test_common_home_expiry_and_restart_do_not_restore_selection(
    view: tuple[CalendarController, Clock, list[str]],
) -> None:
    controller, clock, calls = view
    select(controller, clock)
    result(controller, clock)
    clock.value += timedelta(minutes=5)
    assert controller.source_status.snapshot == "stale"
    assert controller.source_status.selected_count == 1
    clock.value += timedelta(minutes=55)
    common = controller.source_status
    assert common.connection == "expired"
    assert common.problem == "expired"
    assert common.selected_count == 0
    assert common.snapshot != "fresh"
    controller.close()
    common = controller.source_status
    assert common.connection == "disconnected"
    assert common.selected_count == 0
    assert common.snapshot == "not_read"
    assert calls == []
