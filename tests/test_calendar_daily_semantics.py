"""Synthetic civil-day and live-selection boundaries for daily Calendar delivery."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import uuid4

import pytest

from satori.application.assistant.calendar import CalendarAccessCoordinator, CalendarAccessError
from satori.application.assistant.contracts import (
    CalendarAuditEvent,
    CalendarCatalog,
    CalendarDescriptor,
    CalendarOccurrence,
    CalendarReadResult,
    CalendarSourceRef,
    LocalPrincipal,
)

NOW = datetime(2026, 10, 29, 9, tzinfo=UTC)
SOURCE = CalendarSourceRef(account_id="SYNTHETIC ACCOUNT", calendar_id="SYNTHETIC CALENDAR")
OTHER = CalendarSourceRef(account_id="OTHER ACCOUNT", calendar_id="OTHER CALENDAR")


@dataclass
class Clock:
    value: datetime = NOW

    def __call__(self) -> datetime:
        return self.value


def owner(clock: Clock, audit: list[CalendarAuditEvent]) -> CalendarAccessCoordinator:
    reads = CalendarAccessCoordinator(
        LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4()),
        clock=clock,
        audit=audit.append,
    )
    reads.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=clock(),
            calendars=(
                CalendarDescriptor(
                    source=SOURCE,
                    title="SYNTHETIC <b>selected</b>",
                    account_title="SYNTHETIC account",
                ),
                CalendarDescriptor(
                    source=OTHER, title="SYNTHETIC unselected", account_title="OTHER account"
                ),
            ),
        )
    )
    return reads


def test_live_selection_metadata_never_issues_grant_or_prepares_read() -> None:
    clock = Clock()
    audit: list[CalendarAuditEvent] = []
    reads = owner(clock, audit)
    events_before, generation = tuple(audit), reads.generation
    assert reads.granted_calendars == ()
    assert reads.grant is None
    assert reads.snapshot is None
    assert tuple(audit) == events_before
    assert reads.generation == generation


def test_catalog_expiry_keeps_only_exact_live_grant_selection_without_new_authority() -> None:
    clock = Clock()
    audit: list[CalendarAuditEvent] = []
    reads = owner(clock, audit)
    selected = (reads.catalog[0],)
    grant = reads.issue((SOURCE,))
    events_before, generation = tuple(audit), reads.generation
    clock.value += timedelta(minutes=5, seconds=1)

    assert reads.catalog == ()
    assert reads.catalog_diagnostics.state == "stale"
    assert reads.granted_calendars == selected
    assert reads.grant == grant
    assert reads.grant.resources == (SOURCE,)
    assert reads.grant.expires_at == NOW + timedelta(hours=1)
    assert reads.snapshot is None
    assert reads.generation == generation
    assert tuple(audit) == events_before

    # Reading grant-bound display metadata also preserves an existing pending command.
    request = reads.prepare_day(date(2026, 10, 29), "Europe/Moscow")
    assert reads.granted_calendars == selected
    assert reads.grant == grant
    snapshot = reads.accept_result(
        request,
        CalendarReadResult(request_id=request.request_id, observed_at=clock(), occurrences=()),
    )
    assert snapshot.calendars == selected
    assert snapshot.request.resources == (SOURCE,)
    assert snapshot.status == "ok"


@pytest.mark.parametrize("ending", ["expire", "revoke", "selection_edit", "catalog_replace"])
def test_ending_a_grant_drops_selected_descriptors_and_event_payloads(
    ending: Literal["expire", "revoke", "selection_edit", "catalog_replace"],
) -> None:
    clock = Clock()
    audit: list[CalendarAuditEvent] = []
    reads = owner(clock, audit)
    reads.issue((SOURCE,))
    request = reads.prepare_day(date(2026, 10, 29), "UTC")
    reads.accept_result(
        request,
        CalendarReadResult(
            request_id=request.request_id,
            observed_at=clock(),
            occurrences=(
                CalendarOccurrence(
                    source=SOURCE,
                    event_id="SYNTHETIC EVENT",
                    occurrence_id="SYNTHETIC OCCURRENCE",
                    title="SYNTHETIC event body",
                    starts_at=NOW,
                    ends_at=NOW + timedelta(hours=1),
                    timezone="UTC",
                ),
            ),
        ),
    )
    assert reads.granted_calendars
    assert reads.snapshot is not None
    if ending == "expire":
        clock.value += timedelta(hours=1)
    elif ending == "revoke":
        reads.revoke()
    elif ending == "selection_edit":
        reads.suspend_selection()
    else:
        reads.accept_catalog(CalendarCatalog(request_id=uuid4(), observed_at=clock(), calendars=()))

    assert reads.granted_calendars == ()
    assert reads.grant is None
    assert reads.snapshot is None
    assert "selected" not in repr(audit)
    assert "event body" not in repr(audit)
    restarted = CalendarAccessCoordinator(reads.principal, clock=clock)
    assert restarted.granted_calendars == ()
    assert restarted.grant is None
    assert restarted.snapshot is None


@pytest.mark.parametrize(
    ("day", "zone"),
    [
        (date(2011, 12, 30), "Pacific/Apia"),
        (date(2018, 11, 4), "America/Sao_Paulo"),
        # Use an observed historical fallback, not a projected DST recurrence.
        (date(2015, 11, 1), "America/Havana"),
    ],
)
def test_unsupported_civil_day_boundary_fails_before_preparing_source_read(
    day: date, zone: str
) -> None:
    clock = Clock()
    audit: list[CalendarAuditEvent] = []
    reads = owner(clock, audit)
    grant = reads.issue((SOURCE,))
    with pytest.raises(CalendarAccessError, match=r"^scope_mismatch$"):
        reads.prepare_day(day, zone)
    assert reads.grant == grant
    assert reads.snapshot is None
    assert not any(event.event == "read_prepared" for event in audit)


@pytest.mark.parametrize(("day", "hours"), [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)])
def test_supported_dst_day_keeps_true_elapsed_duration(day: date, hours: int) -> None:
    clock = Clock()
    audit: list[CalendarAuditEvent] = []
    reads = owner(clock, audit)
    grant = reads.issue((SOURCE,))
    request = reads.prepare_day(day, "Europe/Berlin")
    assert request.ends_at - request.starts_at == timedelta(hours=hours)
    assert request.resources == grant.resources
    assert reads.granted_calendars[0].source == SOURCE
