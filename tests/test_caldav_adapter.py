"""Public synthetic DAV/ICS wire; never authenticate or contact an actual calendar."""

import asyncio
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from xml.sax.saxutils import escape

import pytest

from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionCatalog,
    CalendarActionScope,
    CalendarCreateAttempt,
    CalendarCreateInput,
    calendar_action_digest,
)
from satori.application.assistant.calendar_admission import calendar_host_admission
from satori.application.assistant.contracts import CalendarReadRequest, LocalPrincipal
from satori.infrastructure.assistant.caldav import ICloudCalDAVAdapter
from satori.infrastructure.assistant.caldav_ics import create_calendar
from satori.infrastructure.assistant.caldav_transport import CalDAVError, CalDAVResponse
from satori.infrastructure.assistant.calendar_actions_store import SQLiteCalendarActionStore

NOW = datetime(2026, 11, 3, 9, tzinfo=UTC)
HOME = "https://p42-caldav.icloud.com/123/calendars/"
WORK = HOME + "public-work/"
EVENT = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:public-event\r\n"
    "DTSTART:20261104T090000Z\r\nDTEND:20261104T100000Z\r\n"
    "SUMMARY:PUBLIC SYNTHETIC\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


def dav(href: str, properties: str, *, status: int = 200) -> str:
    return (
        f"<D:response><D:href>{escape(href)}</D:href><D:propstat>"
        f"<D:prop>{properties}</D:prop><D:status>HTTP/1.1 {status} OK</D:status>"
        "</D:propstat></D:response>"
    )


def xml(*resources: str) -> CalDAVResponse:
    body = (
        '<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
        + "".join(resources)
        + "</D:multistatus>"
    )
    return CalDAVResponse(207, {}, body.encode())


def props(*, privilege: str = "<D:privilege><D:write/></D:privilege>", report: bool = True) -> str:
    return (
        "<D:resourcetype><D:collection/><C:calendar/></D:resourcetype>"
        "<D:displayname>PUBLIC WORK</D:displayname>"
        '<C:supported-calendar-component-set><C:comp name="VEVENT"/>'
        "</C:supported-calendar-component-set>"
        + (
            f"<D:current-user-privilege-set>{privilege}</D:current-user-privilege-set>"
            if privilege
            else ""
        )
        + (
            "<D:supported-report-set><D:supported-report><D:report><C:calendar-query/>"
            "</D:report></D:supported-report></D:supported-report-set>"
            if report
            else ""
        )
    )


def discovery(
    *, privilege: str = "<D:privilege><D:write/></D:privilege>", report: bool = True
) -> list[CalDAVResponse]:
    return [
        xml(
            dav(
                "/",
                "<D:current-user-principal><D:href>https://p42-caldav.icloud.com/123/principal/</D:href></D:current-user-principal>",
            )
        ),
        xml(
            dav(
                "/123/principal/",
                f"<C:calendar-home-set><D:href>{HOME}</D:href></C:calendar-home-set>",
            )
        ),
        xml(dav(WORK, props(privilege=privilege, report=report))),
    ]


def report(event: str = EVENT, *, status: int = 200) -> CalDAVResponse:
    return xml(
        dav(
            WORK + "one.ics",
            '<D:getetag>"public-etag"</D:getetag><C:calendar-data>'
            + escape(event)
            + "</C:calendar-data>",
            status=status,
        )
    )


class Wire:
    def __init__(self, responses: Sequence[CalDAVResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], bytes]] = []
        self.on_ready: Callable[[str], None] | None = None
        self.saved = b""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        before_dispatch: Callable[[], None],
    ) -> CalDAVResponse:
        if self.on_ready:
            self.on_ready(method)
        before_dispatch()
        self.calls.append((method, url, dict(headers), body))
        if method == "PUT":
            self.saved = body
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if response.status == 200 and response.body == b"SAVED":
            return CalDAVResponse(200, {}, self.saved)
        return response


async def setup(
    wire: Wire, *, read_admission: Callable[[CalendarActionScope], None] | None = lambda _: None
) -> ICloudCalDAVAdapter:
    adapter = ICloudCalDAVAdapter(
        "public-account", wire, clock=lambda: NOW, read_admission=read_admission
    )
    catalog = await adapter.discover(uuid4(), before_read=lambda: None)
    assert catalog.status == "ok"
    assert len(catalog.calendars) == 1
    return adapter


async def request(adapter: ICloudCalDAVAdapter) -> CalendarReadRequest:
    # Fresh discovery is an observation; actual grant authority is tested separately.
    source = next(iter(adapter._collections.values())).descriptor.source
    return CalendarReadRequest(
        request_id=uuid4(),
        principal=LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4()),
        grant_id=uuid4(),
        generation=1,
        resources=(source,),
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=2),
        timezone="Europe/Moscow",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )


def make_attempt(tmp_path: Path, adapter: ICloudCalDAVAdapter) -> CalendarCreateAttempt:
    source = next(iter(adapter._collections.values())).descriptor.source
    scope = CalendarActionScope(
        identity_id=uuid4(),
        profile_id=uuid4(),
        session_id=uuid4(),
        generation=uuid4(),
        provider="caldav",
        account_id=source.account_id,
        calendar_id=source.calendar_id,
        timezone="Europe/Moscow",
    )
    catalog = CalendarActionCatalog(
        scope=scope,
        revision=uuid4(),
        observed_at=NOW,
        full_access=True,
        writable=True,
        supports_events=True,
        calendar_title="PUBLIC WORK",
        account_title="iCloud",
    )
    command = CalendarCreateInput(
        request_id=uuid4(),
        context_digest="a" * 64,
        title="PUBLIC SYNTHETIC; кириллица, \\ exact",
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=1, hours=1),
        timezone="Europe/Moscow",
    )
    owner = CalendarActionOwner(
        SQLiteCalendarActionStore(tmp_path / "actions.sqlite3"),
        adapter,
        clock=lambda: NOW,
        real_writes_enabled=True,
    )
    proposed = owner.propose(command, scope=scope, catalog=catalog)
    approved = owner.approve(
        command.request_id, proposed.proposal.review_digest, scope=scope, catalog=catalog
    )
    assert approved.approval is not None
    attempt = CalendarCreateAttempt(
        attempt_id=uuid4(),
        proposal=approved.proposal,
        approval=approved.approval,
        idempotency_token=calendar_action_digest(
            {
                "schema": "satori.calendar-create-attempt.v1",
                "request_id": str(command.request_id),
                "payload_digest": approved.proposal.payload_digest,
            }
        ),
        claimed_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    owner.close()
    return attempt


def test_discovery_and_expanded_read() -> None:
    async def scenario() -> None:
        wire = Wire([*discovery(), report()])
        adapter = await setup(wire)
        result = await adapter.read_events(await request(adapter), before_read=lambda: None)
        assert result.status == "ok"
        assert len(result.occurrences) == 1
        assert result.occurrences[0].starts_at == NOW + timedelta(days=1)
        assert [call[0] for call in wire.calls] == ["PROPFIND"] * 3 + ["REPORT"]
        assert wire.calls[-1][2]["Depth"] == "1"
        assert b"expand" in wire.calls[-1][3]
        assert b"20261104T090000Z" in wire.calls[-1][3]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "body",
    [
        EVENT.replace("SUMMARY:", "RRULE:FREQ=DAILY\r\nSUMMARY:"),
        EVENT.replace("20261104T090000Z", "20261104T090000"),
        EVENT.replace("BEGIN:VEVENT", "BEGIN:VTIMEZONE\r\nEND:VTIMEZONE\r\nBEGIN:VEVENT"),
        EVENT.replace("DTEND:20261104T100000Z\r\n", ""),
    ],
)
def test_incomplete_projection_never_claims_free(body: str) -> None:
    async def scenario() -> None:
        adapter = await setup(Wire([*discovery(), report(body)]))
        result = await adapter.read_events(await request(adapter), before_read=lambda: None)
        assert result.status == "partial"
        assert result.reason == "unsupported_projection"

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", ["privilege", "report"])
def test_unknown_capability_stays_unknown(missing: str) -> None:
    async def scenario() -> None:
        wire = Wire(
            discovery(
                privilege="" if missing == "privilege" else "<D:privilege><D:write/></D:privilege>",
                report=missing != "report",
            )
        )
        adapter = await setup(wire)
        descriptor = next(iter(adapter._collections.values())).descriptor
        if missing == "privilege":
            assert descriptor.writable is None
        else:
            result = await adapter.read_events(await request(adapter), before_read=lambda: None)
            assert result.status == "partial"
            assert len(wire.calls) == 3

    asyncio.run(scenario())


def test_multistatus_property_failure_is_partial() -> None:
    async def scenario() -> None:
        adapter = await setup(Wire([*discovery(), report(status=403)]))
        result = await adapter.read_events(await request(adapter), before_read=lambda: None)
        assert result.status == "partial"
        assert not result.occurrences

    asyncio.run(scenario())


def test_create_conditional_put_and_fresh_exact_get(tmp_path: Path) -> None:
    async def scenario() -> None:
        wire = Wire(
            [
                *discovery(),
                xml(dav(WORK, props())),
                xml(),
                CalDAVResponse(201),
                CalDAVResponse(200, {}, b"SAVED"),
            ]
        )
        adapter = await setup(wire)
        attempt = make_attempt(tmp_path, adapter)
        admitted: list[int] = []
        receipt = await adapter.create(attempt, before_effect=lambda: admitted.append(1))
        assert receipt.status == "created"
        assert receipt.exact_fields_verified
        assert receipt.remote_sync_verified is False
        assert admitted == [1]
        assert [c[0] for c in wire.calls[-4:]] == ["PROPFIND", "REPORT", "PUT", "GET"]
        put = wire.calls[-2]
        assert put[2]["If-None-Match"] == "*"
        assert put[1] == WORK + attempt.idempotency_token + ".ics"
        assert create_calendar(attempt, NOW) == put[3]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response",
    [
        CalDAVResponse(204),
        CalDAVResponse(302, {"location": WORK}),
        CalDAVError("caldav_transport_unavailable"),
        CalDAVResponse(200),
    ],
)
def test_put_uncertainty_never_retries(
    tmp_path: Path, response: CalDAVResponse | Exception
) -> None:
    async def scenario() -> None:
        wire = Wire([*discovery(), xml(dav(WORK, props())), xml(), response])
        adapter = await setup(wire)
        receipt = await adapter.create(make_attempt(tmp_path, adapter), before_effect=lambda: None)
        assert receipt.status == "unknown"
        assert sum(c[0] == "PUT" for c in wire.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "preflight", [report(), report(EVENT.replace("SUMMARY:", "RRULE:FREQ=DAILY\r\nSUMMARY:"))]
)
def test_overlap_or_partial_preflight_prevents_put(
    tmp_path: Path, preflight: CalDAVResponse
) -> None:
    async def scenario() -> None:
        wire = Wire([*discovery(), xml(dav(WORK, props())), preflight])
        adapter = await setup(wire)
        receipt = await adapter.create(
            make_attempt(tmp_path, adapter), before_effect=lambda: pytest.fail("effect")
        )
        assert receipt.status == "rejected"
        assert all(c[0] != "PUT" for c in wire.calls)

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["REPORT", "PUT"])
def test_host_revoke_after_transport_queue_before_bytes(tmp_path: Path, stage: str) -> None:
    async def scenario() -> None:
        wire = Wire([*discovery(), xml(dav(WORK, props())), xml(), CalDAVResponse(201)])
        adapter = await setup(wire)
        attempt = make_attempt(tmp_path, adapter)
        live = [True]
        wire.on_ready = lambda method: live.__setitem__(0, False) if method == stage else None

        def guard() -> None:
            if not live[0]:
                raise ValueError("public revoked")

        with calendar_host_admission(guard):
            receipt = await adapter.create(attempt, before_effect=lambda: pytest.fail("effect"))
        assert receipt.status == "rejected"
        assert all(c[0] != "PUT" for c in wire.calls)

    asyncio.run(scenario())


def test_lookup_only_stable_get_never_rearms(tmp_path: Path) -> None:
    async def scenario() -> None:
        wire = Wire([*discovery(), CalDAVResponse(404), CalDAVResponse(200, {}, b"edited")])
        adapter = await setup(wire)
        attempt = make_attempt(tmp_path, adapter)
        calls: list[int] = []
        absent = await adapter.lookup(
            attempt, attempt.proposal.scope, before_read=lambda: calls.append(1)
        )
        conflict = await adapter.lookup(
            attempt, attempt.proposal.scope, before_read=lambda: calls.append(1)
        )
        assert absent.status == "absent"
        assert conflict.status == "conflict"
        assert calls == [1, 1]
        assert [c[0] for c in wire.calls[-2:]] == ["GET", "GET"]

    asyncio.run(scenario())


def test_foreign_discovery_href_is_never_requested() -> None:
    async def scenario() -> None:
        wire = Wire(
            [
                xml(
                    dav(
                        "/",
                        "<D:current-user-principal><D:href>https://evil.invalid/steal</D:href></D:current-user-principal>",
                    )
                )
            ]
        )
        result = await ICloudCalDAVAdapter("public", wire).discover(
            uuid4(), before_read=lambda: None
        )
        assert result.status == "error"
        assert len(wire.calls) == 1

    asyncio.run(scenario())


def test_create_requires_separate_repeatable_read_admission(tmp_path: Path) -> None:
    async def scenario() -> None:
        wire = Wire(discovery())
        adapter = await setup(wire, read_admission=None)
        receipt = await adapter.create(
            make_attempt(tmp_path, adapter), before_effect=lambda: pytest.fail("effect")
        )
        assert receipt.status == "rejected"
        assert receipt.reason == "calendar_action_permission_required"
        assert len(wire.calls) == 3

    asyncio.run(scenario())


def test_source_revoke_before_queued_preflight_read(tmp_path: Path) -> None:
    async def scenario() -> None:
        live = [True]

        def source_guard(scope: CalendarActionScope) -> None:
            if not live[0]:
                raise ValueError("PUBLIC SOURCE REVOKED")

        wire = Wire(discovery())
        adapter = await setup(wire, read_admission=source_guard)
        attempt = make_attempt(tmp_path, adapter)
        wire.on_ready = lambda _: live.__setitem__(0, False)
        receipt = await adapter.create(attempt, before_effect=lambda: pytest.fail("effect"))
        assert receipt.status == "rejected"
        assert len(wire.calls) == 3

    asyncio.run(scenario())


def test_cancelled_queued_worker_cannot_send_late_put(tmp_path: Path) -> None:
    async def scenario() -> None:
        ready, release, finished = threading.Event(), threading.Event(), threading.Event()

        class HoldingWire(Wire):
            def request(
                self,
                method: str,
                url: str,
                *,
                headers: Mapping[str, str],
                body: bytes,
                before_dispatch: Callable[[], None],
            ) -> CalDAVResponse:
                try:
                    return super().request(
                        method, url, headers=headers, body=body, before_dispatch=before_dispatch
                    )
                finally:
                    if method == "PUT":
                        finished.set()

        wire = HoldingWire([*discovery(), xml(dav(WORK, props())), xml(), CalDAVResponse(201)])
        adapter = await setup(wire)
        attempt = make_attempt(tmp_path, adapter)

        def hold(method: str) -> None:
            if method == "PUT":
                ready.set()
                if not release.wait(2):
                    raise AssertionError("synthetic worker did not release")

        wire.on_ready = hold
        task = asyncio.create_task(
            adapter.create(attempt, before_effect=lambda: pytest.fail("effect"))
        )
        assert await asyncio.to_thread(ready.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        assert all(c[0] != "PUT" for c in wire.calls)

    asyncio.run(scenario())


def test_actual_owner_unknown_claim_and_absence_never_resend(tmp_path: Path) -> None:
    async def scenario() -> None:
        wire = Wire(
            [
                *discovery(),
                xml(dav(WORK, props())),
                xml(),
                CalDAVError("caldav_transport_unavailable"),
                CalDAVResponse(404),
            ]
        )
        adapter = await setup(wire)
        template = make_attempt(tmp_path, adapter)
        scope = template.proposal.scope
        catalog = CalendarActionCatalog(
            scope=scope,
            revision=uuid4(),
            observed_at=NOW,
            full_access=True,
            writable=True,
            supports_events=True,
            calendar_title="PUBLIC WORK",
            account_title="iCloud",
        )
        owner = CalendarActionOwner(
            SQLiteCalendarActionStore(tmp_path / "owner.sqlite3"),
            adapter,
            clock=lambda: NOW,
            real_writes_enabled=True,
        )
        command = template.proposal.command
        proposal = owner.propose(command, scope=scope, catalog=catalog)
        approved = owner.approve(
            command.request_id, proposal.proposal.review_digest, scope=scope, catalog=catalog
        )
        assert approved.approval is not None
        owner_thread = threading.get_ident()
        callback_threads: list[int] = []

        def sqlite_guard() -> None:
            assert owner.get(command.request_id, scope=scope) is not None
            callback_threads.append(threading.get_ident())

        try:
            unknown = await owner.dispatch(
                command.request_id,
                approved.approval.approval_id,
                scope=scope,
                catalog=catalog,
                precondition_guard=sqlite_guard,
            )
            assert unknown.state == "unknown"
            again = await owner.dispatch(
                command.request_id,
                approved.approval.approval_id,
                scope=scope,
                catalog=catalog,
                precondition_guard=sqlite_guard,
            )
            assert again == unknown
            absent = await owner.reconcile(
                command.request_id, scope=scope, catalog=catalog, precondition_guard=sqlite_guard
            )
            assert absent.state == "reconciled_absent"
            final = await owner.dispatch(
                command.request_id,
                approved.approval.approval_id,
                scope=scope,
                catalog=catalog,
                precondition_guard=sqlite_guard,
            )
            assert final.state == "reconciled_absent"
            assert sum(c[0] == "PUT" for c in wire.calls) == 1
            assert callback_threads
            assert set(callback_threads) == {owner_thread}
        finally:
            owner.close()

    asyncio.run(scenario())


def test_discovery_rechecks_read_admission_before_publication() -> None:
    async def scenario() -> None:
        calls = [0]

        def grant() -> None:
            calls[0] += 1
            if calls[0] == 5:
                raise ValueError("PUBLIC POST-AWAIT REVOKE")

        wire = Wire(discovery())
        adapter = ICloudCalDAVAdapter("public-account", wire, clock=lambda: NOW)
        catalog = await adapter.discover(uuid4(), before_read=grant)
        assert calls == [5]
        assert catalog.status == "error"
        assert not catalog.calendars
        assert not adapter._collections

    asyncio.run(scenario())


def test_create_get_failure_after_conditional_put_is_unknown(tmp_path: Path) -> None:
    async def scenario() -> None:
        wire = Wire(
            [
                *discovery(),
                xml(dav(WORK, props())),
                xml(),
                CalDAVResponse(201),
                CalDAVResponse(200, {}, b"EDITED OR MISSING REQUIRED FIELDS"),
            ]
        )
        adapter = await setup(wire)
        receipt = await adapter.create(make_attempt(tmp_path, adapter), before_effect=lambda: None)
        assert receipt.status == "unknown"
        assert receipt.event_id is None
        assert not receipt.fresh_store_verified
        assert sum(c[0] == "PUT" for c in wire.calls) == 1

    asyncio.run(scenario())


def test_expanded_recurrence_preserves_every_intersecting_instance() -> None:
    async def scenario() -> None:
        instance = EVENT.replace(
            "DTSTART:20261104T090000Z", "RECURRENCE-ID:20261105T090000Z\r\nDTSTART:20261105T080000Z"
        ).replace("DTEND:20261104T100000Z", "DTEND:20261105T083000Z")
        expanded = (
            EVENT.removesuffix("END:VCALENDAR\r\n") + instance[instance.index("BEGIN:VEVENT") :]
        )
        adapter = await setup(Wire([*discovery(), report(expanded)]))
        result = await adapter.read_events(await request(adapter), before_read=lambda: None)
        assert result.status == "ok"
        assert len(result.occurrences) == 2
        assert len({item.event_id for item in result.occurrences}) == 1
        assert len({item.occurrence_id for item in result.occurrences}) == 2

    asyncio.run(scenario())


def test_conditional_collision_never_overwrites_or_reads_again(tmp_path: Path) -> None:
    async def scenario() -> None:
        wire = Wire([*discovery(), xml(dav(WORK, props())), xml(), CalDAVResponse(412)])
        adapter = await setup(wire)
        receipt = await adapter.create(make_attempt(tmp_path, adapter), before_effect=lambda: None)
        assert receipt.status == "rejected"
        assert receipt.reason == "calendar_action_request_conflict"
        assert [call[0] for call in wire.calls[-3:]] == ["PROPFIND", "REPORT", "PUT"]

    asyncio.run(scenario())


def test_create_read_guard_receives_exact_attempt_resource(tmp_path: Path) -> None:
    async def scenario() -> None:
        seen: list[CalendarActionScope] = []
        wire = Wire(
            [
                *discovery(),
                xml(dav(WORK, props())),
                xml(),
                CalDAVResponse(201),
                CalDAVResponse(200, {}, b"SAVED"),
            ]
        )
        adapter = await setup(wire, read_admission=seen.append)
        attempt = make_attempt(tmp_path, adapter)
        receipt = await adapter.create(attempt, before_effect=lambda: None)
        assert receipt.status == "created"
        assert seen == [attempt.proposal.scope] * 4

    asyncio.run(scenario())
