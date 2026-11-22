"""Reviewed schedule temporal, portable-identity and one-use mutation-policy boundaries."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from satori.application.assistant.contracts import CalendarSourceRef, LocalPrincipal
from satori.application.assistant.schedule import (
    SchedulePlanCoordinator,
    draft_from_ocr,
    dump_schedule_json,
    event_content_token,
    export_schedule_ics,
    parse_schedule_json,
    rows_for_target,
)
from satori.application.assistant.schedule_contracts import (
    MAX_SCHEDULE_BYTES,
    ReviewedSchedule,
    ScheduleError,
    ScheduleOutcome,
    SchedulePlan,
    ScheduleRowResult,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
TARGET = CalendarSourceRef(account_id="fixture-account", calendar_id="fixture-work")
_INTEROP = json.loads(
    (
        Path(__file__).resolve().parents[1] / "docs/contracts/schedule-import-v1.examples.json"
    ).read_text(encoding="utf-8")
)
PRINCIPAL = LocalPrincipal(
    os_uid=501,
    profile_id=UUID("11111111-1111-4111-8111-111111111111"),
    session_id=UUID("22222222-2222-4222-8222-222222222222"),
)


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


def document(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": "satori-schedule",
        "version": 1,
        "year": 2026,
        "month": 10,
        "timezone": "Europe/Moscow",
        "legend": "Each row explicitly states one reviewed work shift.",
        "reviewed": True,
        "rows": [
            {
                "date": "2026-10-26",
                "start": "09:00",
                "end": "18:00",
                "end_date": "2026-10-26",
                "title": "Fixture shift",
            }
        ],
    }
    value.update(changes)
    return value


def parse_internal(data: bytes | str) -> ReviewedSchedule:
    try:
        return ReviewedSchedule.model_validate_json(data)
    except ValueError as error:
        raise ScheduleError("schedule_invalid") from error


def reviewed(**changes: Any) -> ReviewedSchedule:
    return parse_internal(json.dumps(document(**changes)))


def result(
    plan: SchedulePlan, outcome: ScheduleOutcome = "absent"
) -> tuple[ScheduleRowResult, ...]:
    return tuple(
        ScheduleRowResult(row_id=row.row_id, owned_token=row.owned_token, outcome=outcome)
        for row in plan.rows
    )


def policy(clock: Clock | None = None) -> SchedulePlanCoordinator:
    return SchedulePlanCoordinator(PRINCIPAL, clock=clock or Clock())


def prepared(owner: SchedulePlanCoordinator) -> SchedulePlan:
    return owner.prepare(reviewed(), TARGET, 7, writable=True)


def test_reviewed_json_roundtrip_preserves_exact_civil_fields() -> None:
    schedule = reviewed()
    encoded = dump_schedule_json(schedule)
    assert dump_schedule_json(parse_schedule_json(encoded)) == encoded
    assert schedule.rows[0].start.hour == 9
    assert schedule.rows[0].end_date == schedule.rows[0].date
    assert "Fixture shift" not in repr(schedule)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "other"},
        {"version": True},
        {"version": 2},
        {"year": True},
        {"year": "2026"},
        {"year": 1969},
        {"year": 2101},
        {"month": 0},
        {"month": 13},
        {"month": False},
        {"reviewed": False},
        {"reviewed": 1},
        {"legend": ""},
        {"legend": "   "},
        {"legend": "one\ntwo"},
        {"timezone": "Unknown/Zone"},
        {"timezone": " Europe/Moscow"},
        {"rows": []},
        {"attendees": ["forbidden"]},
    ],
)
def test_unresolved_or_extended_document_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ScheduleError, match="schedule_invalid"):
        reviewed(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"date": "2026-10-32"},
        {"start": "09:00:00.001"},
        {"start": "09:00+03:00"},
        {"end": "09:00"},
        {"end": "08:00"},
        {"end_date": "2026-10-25"},
        {"end_date": "2026-10-29"},
        {"title": ""},
        {"title": "\nATTENDEE:evil"},
        {"title": "x" * 129},
        {"start_fold": True},
        {"end_fold": 2},
        {"alarm": 1},
    ],
)
def test_invalid_or_unapproved_shift_rejected(changes: dict[str, Any]) -> None:
    value = document()
    value["rows"][0].update(changes)
    with pytest.raises(ScheduleError):
        parse_internal(json.dumps(value))


def test_missing_dates_or_header_not_guessed() -> None:
    for key in ("year", "month", "legend"):
        value = document()
        del value[key]
        with pytest.raises(ScheduleError):
            parse_internal(json.dumps(value))
    value = document()
    del value["rows"][0]["end_date"]
    with pytest.raises(ScheduleError):
        parse_internal(json.dumps(value))


def test_explicit_overnight_and_36_hour_boundary() -> None:
    value = document()
    value["rows"][0].update(start="22:00", end="06:00", end_date="2026-10-27")
    schedule = parse_internal(json.dumps(value))
    event = rows_for_target(schedule, PRINCIPAL, TARGET)[0]
    assert event.end - event.start == timedelta(hours=8)
    value["rows"][0].update(start="09:00", end="21:00", end_date="2026-10-27")
    parse_internal(json.dumps(value))
    value["rows"][0]["end"] = "21:01"
    with pytest.raises(ScheduleError):
        parse_internal(json.dumps(value))


def test_duplicate_interval_even_with_changed_title_rejected() -> None:
    value = document()
    value["rows"].append({**value["rows"][0], "title": "Different title"})
    with pytest.raises(ScheduleError):
        parse_internal(json.dumps(value))


def test_nonexistent_and_repeated_dst_hours_are_explicit() -> None:
    value = document(year=2026, month=3, timezone="Europe/Berlin")
    value["rows"][0].update(date="2026-03-29", start="02:30", end="04:00", end_date="2026-03-29")
    with pytest.raises(ScheduleError):
        parse_internal(json.dumps(value))
    value = document(timezone="Europe/Berlin")
    value["rows"][0].update(date="2026-10-25", start="02:30", end="04:00", end_date="2026-10-25")
    with pytest.raises(ScheduleError):
        parse_internal(json.dumps(value))
    value["rows"][0]["start_fold"] = 0
    early = rows_for_target(parse_internal(json.dumps(value)), PRINCIPAL, TARGET)[0]
    value["rows"][0]["start_fold"] = 1
    late = rows_for_target(parse_internal(json.dumps(value)), PRINCIPAL, TARGET)[0]
    assert late.start.astimezone(UTC) - early.start.astimezone(UTC) == timedelta(hours=1)
    assert late.owned_token != early.owned_token


@pytest.mark.parametrize(
    "raw", [b"", b"\xff", b"[]", b"null", b"{}", b"x" * (MAX_SCHEDULE_BYTES + 1)]
)
def test_bounded_file_parser_rejects_bad_input_without_content(raw: bytes) -> None:
    with pytest.raises(ScheduleError) as caught:
        parse_schedule_json(raw)
    assert str(caught.value).startswith("schedule_")
    assert raw[:100].decode("utf-8", errors="replace") not in str(caught.value) or not raw


def test_duplicate_and_escaped_duplicate_json_keys_rejected() -> None:
    raw = dump_schedule_json(reviewed()).decode()
    for suffix in ('"schema_version":1', '"\\u0073chema_version":1'):
        with pytest.raises(ScheduleError, match="schedule_duplicate_key"):
            parse_schedule_json(raw.rstrip()[:-1] + "," + suffix + "}")


def portable_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "time_zone": "Europe/Moscow",
        "shifts": [
            {
                "title": "Fixture shift",
                "start": "2026-10-26T09:00:00+03:00",
                "end": "2026-10-26T18:00:00+03:00",
            }
        ],
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"time_zone": "Europe/Invalid"},
        {"shifts": []},
        {"target": "must-not-travel-with-document"},
        {"reviewed": True},
    ],
)
def test_portable_document_rejects_wrong_shape_or_extra_permission(changes: dict[str, Any]) -> None:
    value = portable_document()
    value.update(changes)
    with pytest.raises(ScheduleError):
        parse_schedule_json(json.dumps(value))


@pytest.mark.parametrize(
    "changes",
    [
        {"start": "2026-10-26T09:00:00"},
        {"start": "2026-10-26T09:00:00+02:00"},
        {"start": "2026-10-26T09:00:00.000+03:00"},
        {"start": "2026-10-26T09:00+03:00"},
        {"start": "2026-10-32T09:00:00+03:00"},
        {"start": "2026-10-26T18:00:00+03:00"},
        {"end": "2026-10-28T09:01:00+03:00"},
        {"title": "\u0085control"},
        {"title": "x" * 121},
        {"alarms": []},
        {"calendar_id": "must-be-selected-locally"},
    ],
)
def test_portable_shift_requires_exact_offsets_bounds_and_no_actions(
    changes: dict[str, Any],
) -> None:
    value = portable_document()
    value["shifts"][0].update(changes)
    with pytest.raises(ScheduleError):
        parse_schedule_json(json.dumps(value))


def test_portable_dst_explicit_offsets_resolve_repeated_hour_and_reject_gap() -> None:
    value = portable_document()
    value["time_zone"] = "Europe/Berlin"
    value["shifts"][0].update(start="2026-10-25T02:30:00+02:00", end="2026-10-25T02:30:00+01:00")
    schedule = parse_schedule_json(json.dumps(value))
    assert schedule.rows[0].start_fold == 0
    assert schedule.rows[0].end_fold == 1
    event = rows_for_target(schedule, PRINCIPAL, TARGET)[0]
    assert event.end.astimezone(UTC) - event.start.astimezone(UTC) == timedelta(hours=1)
    value["shifts"][0].update(start="2026-03-29T02:30:00+01:00", end="2026-03-29T04:00:00+02:00")
    with pytest.raises(ScheduleError):
        parse_schedule_json(json.dumps(value))


def test_portable_whole_seconds_survive_preview_roundtrip_and_token() -> None:
    value = portable_document()
    value["shifts"][0].update(start="2026-10-26T09:00:17+03:00", end="2026-10-26T18:00:29+03:00")
    schedule = parse_schedule_json(json.dumps(value))
    assert schedule.rows[0].start.second == 17
    assert schedule.rows[0].end.second == 29
    exported = json.loads(dump_schedule_json(schedule))
    assert exported == value
    assert rows_for_target(schedule, PRINCIPAL, TARGET)[0].start.second == 17


def test_portable_crossmonth_plan_and_total_window_boundary() -> None:
    value = portable_document()
    value["shifts"].append(
        {
            "title": "Later fixture shift",
            "start": "2026-11-26T09:00:00+03:00",
            "end": "2026-11-26T18:00:00+03:00",
        }
    )
    schedule = parse_schedule_json(json.dumps(value))
    assert len(schedule.rows) == 2
    assert schedule.rows[1].date.month == 11
    value["shifts"][1].update(start="2026-12-27T09:00:00+03:00", end="2026-12-27T09:01:00+03:00")
    with pytest.raises(ScheduleError):
        parse_schedule_json(json.dumps(value))


def test_content_marker_uses_nfc_and_instants_not_iana_spelling() -> None:
    start, end = datetime(2026, 10, 26, 6, tzinfo=UTC), datetime(2026, 10, 26, 15, tzinfo=UTC)
    assert event_content_token("Caf\u00e9", start, end, "Europe/Moscow") == event_content_token(
        "Cafe\u0301", start, end, "Etc/UTC"
    )


@pytest.mark.parametrize("example", _INTEROP["accepted"], ids=lambda value: value["id"])
def test_shared_mac_iphone_accepted_plan_and_content_fingerprints(example: dict[str, Any]) -> None:
    schedule = parse_schedule_json(json.dumps(example["plan"], ensure_ascii=False))
    rows = rows_for_target(schedule, PRINCIPAL, TARGET)
    assert [row.owned_token for row in rows] == example["expected_row_fingerprints"]
    exported = parse_schedule_json(dump_schedule_json(schedule))
    assert rows_for_target(exported, PRINCIPAL, TARGET) == rows


@pytest.mark.parametrize("example", _INTEROP["rejected"], ids=lambda value: value["id"])
def test_shared_mac_iphone_rejected_plan(example: dict[str, Any]) -> None:
    with pytest.raises(ScheduleError):
        parse_schedule_json(json.dumps(example["plan"], ensure_ascii=False))


def test_ocr_never_invents_missing_month_or_decodes_a_grid() -> None:
    assert (
        draft_from_ocr(
            ["26 09:00-18:00"], year=None, month=10, timezone="Europe/Moscow", legend="hours"
        ).rows
        == ()
    )
    draft = draft_from_ocr(
        ["Alice | 1 2 3 4 5", "DAY NIGHT OFF OFF DAY"],
        year=2026,
        month=10,
        timezone="Europe/Moscow",
        legend="codes",
    )
    assert draft.rows == ()
    assert "manual_rows_required" in draft.ambiguities
    assert "Alice" not in repr(draft)


def test_simple_ocr_is_suggestion_and_night_end_is_not_inferred() -> None:
    draft = draft_from_ocr(
        ["26 09:00-18:00", "27 22:00-06:00", "28 22:00-06:00 2026-10-29"],
        year=2026,
        month=10,
        timezone="Europe/Moscow",
        legend="exact hours",
    )
    assert len(draft.rows) == 2
    assert "ambiguous_row" in draft.ambiguities
    assert "human_review_required" in draft.ambiguities


def test_owned_content_token_portable_but_plan_identity_is_local() -> None:
    schedule = reviewed()
    other_principal = PRINCIPAL.model_copy(
        update={"os_uid": 502, "profile_id": uuid4(), "session_id": uuid4()}
    )
    other_target = CalendarSourceRef(account_id="iphone-account", calendar_id="iphone-work")
    assert rows_for_target(schedule, PRINCIPAL, TARGET) == rows_for_target(
        schedule, other_principal, other_target
    )
    first, second = policy(), SchedulePlanCoordinator(other_principal, clock=Clock())
    one = first.prepare(schedule, TARGET, 1, writable=True)
    two = second.prepare(schedule, other_target, 1, writable=True)
    assert one.plan_fingerprint != two.plan_fingerprint
    assert one.rows[0].owned_token == two.rows[0].owned_token


def test_portable_content_token_known_golden() -> None:
    # Literal byte recipe is shared with the iPhone implementation, not device-local IDs.
    expected_bytes = b"satori-shift-v1\n2026-10-26T06:00:00Z\n2026-10-26T15:00:00Z\nFixture shift"
    import hashlib

    assert (
        event_content_token(
            "Fixture shift",
            datetime(2026, 10, 26, 6, tzinfo=UTC),
            datetime(2026, 10, 26, 15, tzinfo=UTC),
            "Europe/Moscow",
        )
        == hashlib.sha256(expected_bytes).hexdigest()
    )


def test_no_read_grant_or_writable_catalog_can_implicitly_authorize_create() -> None:
    owner = policy()
    with pytest.raises(ScheduleError, match="schedule_target_not_writable"):
        owner.prepare(reviewed(), TARGET, 7, writable=False)
    plan = prepared(owner)
    with pytest.raises(ScheduleError, match="schedule_create_not_ready"):
        owner.consume(plan, principal=PRINCIPAL, generation=7, target=TARGET)
    assert str(owner.state) == "prepared"


@pytest.mark.parametrize("outcome", ["owned_exact", "conflict"])
def test_any_duplicate_or_conflict_blocks_entire_plan(outcome: ScheduleOutcome) -> None:
    owner = policy()
    plan = prepared(owner)
    assert not owner.accept_preflight(plan, result(plan, outcome))
    with pytest.raises(ScheduleError):
        owner.consume(plan, principal=PRINCIPAL, generation=7, target=TARGET)


@pytest.mark.parametrize("wrong", ["principal", "generation", "target"])
def test_consume_bound_to_exact_scope(wrong: str) -> None:
    owner = policy()
    plan = prepared(owner)
    owner.accept_preflight(plan, result(plan))
    args = {"principal": PRINCIPAL, "generation": 7, "target": TARGET}
    args[wrong] = {
        "principal": PRINCIPAL.model_copy(update={"session_id": uuid4()}),
        "generation": 8,
        "target": CalendarSourceRef(account_id="fixture-account", calendar_id="different"),
    }[wrong]
    with pytest.raises(ScheduleError, match="schedule_scope_mismatch"):
        owner.consume(plan, **args)  # type: ignore[arg-type]
    assert str(owner.state) == "ready"


def test_single_use_consumed_before_dispatch_and_success_does_not_reopen() -> None:
    owner = policy()
    plan = prepared(owner)
    owner.accept_preflight(plan, result(plan))
    assert owner.consume(plan, principal=PRINCIPAL, generation=7, target=TARGET) == plan
    assert str(owner.state) == "consumed"
    with pytest.raises(ScheduleError):
        owner.consume(plan, principal=PRINCIPAL, generation=7, target=TARGET)
    owner.complete(plan, result(plan, "created"))
    assert str(owner.state) == "committed"
    with pytest.raises(ScheduleError):
        owner.consume(plan, principal=PRINCIPAL, generation=7, target=TARGET)


def test_unknown_requires_readonly_reconcile_and_never_reuses_same_plan() -> None:
    owner = policy()
    plan = prepared(owner)
    owner.accept_preflight(plan, result(plan))
    owner.consume(plan, principal=PRINCIPAL, generation=7, target=TARGET)
    owner.mark_unknown(plan.plan_id)
    owner.invalidate()
    assert owner.plan == plan
    assert str(owner.state) == "unknown"
    with pytest.raises(ScheduleError, match="schedule_reconcile_required"):
        prepared(owner)
    owner.reconcile(plan, result(plan, "absent"))
    assert str(owner.state) == "reconciled"
    with pytest.raises(ScheduleError):
        owner.consume(plan, principal=PRINCIPAL, generation=7, target=TARGET)
    newer = prepared(owner)
    assert newer.plan_id != plan.plan_id
    assert newer.rows == plan.rows
    assert str(owner.state) == "prepared"


def test_unresolved_reconcile_still_blocks_new_plan() -> None:
    owner = policy()
    plan = prepared(owner)
    owner.accept_preflight(plan, result(plan))
    owner.consume(plan, principal=PRINCIPAL, generation=7, target=TARGET)
    owner.mark_unknown(plan.plan_id)
    owner.reconcile(plan, result(plan, "unknown"))
    assert str(owner.state) == "unknown"
    with pytest.raises(ScheduleError):
        prepared(owner)


def test_partial_or_wrong_write_acknowledgement_is_unknown() -> None:
    owner = policy()
    plan = prepared(owner)
    owner.accept_preflight(plan, result(plan))
    owner.consume(plan, principal=PRINCIPAL, generation=7, target=TARGET)
    with pytest.raises(ScheduleError):
        owner.complete(plan, ())
    assert str(owner.state) == "unknown"


def test_preflight_identity_mismatch_duplicate_or_unknown_rejected() -> None:
    for bad in ((), result(prepared(policy()), "unknown")):
        owner = policy()
        plan = prepared(owner)
        with pytest.raises(ScheduleError):
            owner.accept_preflight(plan, bad)
    owner = policy()
    plan = prepared(owner)
    other = result(plan)[0].model_copy(update={"row_id": uuid4()})
    with pytest.raises(ScheduleError):
        owner.accept_preflight(plan, (other,))
    assert str(owner.state) == "prepared"


def test_expiry_and_clock_regression_fail_closed() -> None:
    clock = Clock()
    owner = policy(clock)
    plan = prepared(owner)
    owner.accept_preflight(plan, result(plan))
    clock.now += timedelta(minutes=5)
    with pytest.raises(ScheduleError, match="schedule_plan_expired"):
        owner.consume(plan, principal=PRINCIPAL, generation=7, target=TARGET)
    clock.now = NOW - timedelta(seconds=1)
    with pytest.raises(ScheduleError, match="schedule_clock_invalid"):
        prepared(owner)


def test_edit_invalidates_preflight_and_late_result_cannot_restore_it() -> None:
    owner = policy()
    plan = prepared(owner)
    owner.accept_preflight(plan, result(plan))
    owner.invalidate()
    assert str(owner.state) == "empty"
    with pytest.raises(ScheduleError, match="schedule_stale_plan"):
        owner.accept_preflight(plan, result(plan))


def test_ics_is_inert_escaped_utf8_folded_and_has_portable_marker() -> None:
    value = document()
    value["rows"][0]["title"] = "Смена, участок; путь\\" + "Ж" * 70
    schedule = parse_internal(json.dumps(value))
    encoded = export_schedule_ics(schedule, generated_at=NOW)
    lines = encoded.split(b"\r\n")
    assert all(len(line) <= 75 for line in lines)
    assert all(line.decode("utf-8") is not None for line in lines)
    text = encoded.decode().replace("\r\n ", "")
    assert "DTSTART:20261026T060000Z" in text
    assert "DTEND:20261026T150000Z" in text
    assert "SUMMARY:Смена\\, участок\\; путь\\\\" in text
    token = rows_for_target(schedule, PRINCIPAL, TARGET)[0].owned_token
    assert f"URL:satori-calendar://shift/v1/{token}" in text
    assert all(name not in text for name in ("ATTENDEE", "ORGANIZER", "METHOD:", "VALARM", "RRULE"))


def test_bypassed_model_validation_cannot_export_or_prepare() -> None:
    tampered = reviewed().model_copy(update={"reviewed": False})
    with pytest.raises(ScheduleError):
        export_schedule_ics(tampered)
    with pytest.raises(ScheduleError):
        policy().prepare(tampered, TARGET, 7, writable=True)
    with pytest.raises(ValidationError):
        ReviewedSchedule.model_validate({})
