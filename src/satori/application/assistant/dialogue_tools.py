"""One local calendar-card use case; no I/O, canonical Send, persistence or model."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from satori.application.assistant.calendar import (
    CalendarAccessCoordinator,
    CalendarAccessError,
)
from satori.application.assistant.contracts import (
    CalendarOccurrence,
    CalendarReadResult,
    require_utc,
)
from satori.application.assistant.dialogue_contracts import (
    CalendarDialogueAudit,
    CalendarReadProposal,
    CalendarSourceReceipt,
    DisclosurePermit,
    GroundedCalendarAnswer,
    PendingCalendarRead,
    SourceInterval,
    _unambiguous_midnight,
)


class CalendarDialogueDenied(ValueError):
    """Closed codes only; exception text must not contain source/model material."""


class CalendarDialogueSession:
    """Owns only transient disclosure; CalendarAccessCoordinator remains read owner.

    Use on one serialized application loop. Read/helper I/O is outside the owner.
    No permit/receipt supplied from the wire is trusted merely because it validates.
    Only the retained, exact current values are admitted. A new prepare fences old work.
    """

    def __init__(
        self,
        reads: CalendarAccessCoordinator,
        chat_id: UUID,
        *,
        clock: Callable[[], datetime],
        audit: Callable[[CalendarDialogueAudit], None],
    ) -> None:
        self._reads = reads
        self._chat_id = chat_id
        self._clock = clock
        self._audit = audit
        self._last_now: datetime | None = None
        self._permit: DisclosurePermit | None = None
        self._pending: PendingCalendarRead | None = None
        self._answer: GroundedCalendarAnswer | None = None
        self._consumed = False

    def _now(self) -> datetime:
        try:
            now = require_utc(self._clock())
        except (ValueError, TypeError, AttributeError):
            self.clear()
            raise CalendarDialogueDenied("clock_invalid") from None
        if self._last_now is not None and now < self._last_now:
            self.clear()
            raise CalendarDialogueDenied("clock_invalid")
        self._last_now = now
        return now

    def clear(self) -> None:
        """Also called on tab/profile/session change, Cancel, Disconnect and Quit."""
        previous = self._permit
        self._permit = None
        self._pending = None
        self._answer = None
        self._consumed = False
        if previous is not None:
            self._emit(
                "consent_cleared",
                self._last_now or previous.issued_at,
                permit_id=previous.permit_id,
            )

    def _emit(self, event: str, now: datetime, **fields: object) -> None:
        metadata = CalendarDialogueAudit.model_validate(
            {"event": event, "occurred_at": now, **fields}
        )
        try:
            self._audit(metadata)
        except Exception:
            self._permit = None
            self._pending = None
            self._answer = None
            self._consumed = False
            raise CalendarDialogueDenied("audit_unavailable") from None

    @property
    def pending(self) -> PendingCalendarRead | None:
        """The retained exact read, after rechecking the display permit and grant."""
        pending = self._pending
        if pending is None:
            return None
        try:
            self._admit(pending.permit)
        except (CalendarDialogueDenied, CalendarAccessError):
            self.clear()
            return None
        return pending

    def consent(self, proposal: CalendarReadProposal) -> DisclosurePermit:
        """Issue local-display consent from an explicit, exact foreground UI action."""
        self.clear()
        now = self._now()
        try:
            proposal = CalendarReadProposal.model_validate(proposal.model_dump())
        except ValidationError:
            raise CalendarDialogueDenied("proposal_invalid") from None
        grant = self._reads.grant
        if (
            grant is None
            or proposal.principal != self._reads.principal
            or proposal.chat_id != self._chat_id
        ):
            raise CalendarDialogueDenied("principal_or_read_grant_mismatch")
        permit = DisclosurePermit(
            permit_id=uuid4(),
            proposal=proposal,
            read_grant_id=grant.grant_id,
            read_generation=grant.generation,
            resources=grant.resources,
            issued_at=now,
            expires_at=min(grant.expires_at, now + timedelta(minutes=5)),
        )
        self._emit("consent_issued", now, permit_id=permit.permit_id)
        self._permit = permit
        return permit

    def _admit(self, permit: DisclosurePermit) -> datetime:
        # A late callback is rejected without destroying the newer request's consent.
        if self._permit != permit:
            raise CalendarDialogueDenied("stale_disclosure")
        now = self._now()
        grant = self._reads.grant
        if (
            now >= permit.expires_at
            or grant is None
            or grant.grant_id != permit.read_grant_id
            or grant.generation != permit.read_generation
            or grant.resources != permit.resources
            or grant.principal != permit.proposal.principal
        ):
            self.clear()
            raise CalendarDialogueDenied("disclosure_revoked_or_expired")
        return now

    def prepare(self, permit: DisclosurePermit) -> PendingCalendarRead:
        self._admit(permit)
        if self._consumed:
            raise CalendarDialogueDenied("one_read_per_consent")
        # Consume before preparation: downstream failure never re-arms a consent.
        self._consumed = True
        request = self._reads.prepare_read(
            permit.proposal.window.starts_at,
            permit.proposal.window.ends_at,
            permit.proposal.timezone,
        )
        pending = PendingCalendarRead(permit=permit, request=request)
        self._emit(
            "read_prepared",
            self._now(),
            permit_id=permit.permit_id,
            request_id=request.request_id,
        )
        self._pending = pending
        return pending

    def accept(
        self, pending: PendingCalendarRead, result: CalendarReadResult
    ) -> GroundedCalendarAnswer:
        """Validate exact read receipt and current consent after source I/O."""
        now = self._admit(pending.permit)
        if self._pending != pending:
            raise CalendarDialogueDenied("stale_or_replayed_read")
        self._pending = None
        # Correlation failure is not a negative read receipt from this source.
        if result.request_id != pending.request.request_id:
            self.clear()
            raise CalendarDialogueDenied("receipt_correlation_mismatch")
        try:
            snapshot = self._reads.accept_result(pending.request, result)
        except CalendarAccessError as error:
            status = (
                "denied"
                if error.code.startswith("permission_")
                else "unavailable"
                if error.code == "provider_unavailable"
                else "error"
            )
            answer = GroundedCalendarAnswer(
                receipt=self._receipt(pending, status, now, reason=error.code),
                proposal=pending.permit.proposal,
                answer_kind="unavailable",
            )
            if status == "denied" or error.code in {
                "stale_request",
                "grant_expired",
                "grant_required",
            }:
                self.clear()
            else:
                self._answer = answer
            return answer
        self._admit(pending.permit)
        if snapshot.observed_at >= pending.permit.expires_at:
            self.clear()
            return GroundedCalendarAnswer(
                receipt=self._receipt(pending, "error", now, reason="result_invalid"),
                proposal=pending.permit.proposal,
                answer_kind="unavailable",
            )
        proposal = pending.permit.proposal
        try:
            intervals = tuple((event, _interval(event)) for event in snapshot.occurrences)
        except (ValueError, OverflowError):
            self.clear()
            return GroundedCalendarAnswer(
                receipt=self._receipt(pending, "error", now, reason="result_invalid"),
                proposal=proposal,
                answer_kind="unavailable",
            )
        events = tuple(
            event
            for event, interval in intervals
            if interval.starts_at < proposal.window.ends_at
            and interval.ends_at > proposal.window.starts_at
        )
        status = "partial" if snapshot.status == "partial" else "ok" if events else "empty"
        answer = GroundedCalendarAnswer(
            receipt=self._receipt(
                pending,
                status,
                now,
                observed_at=snapshot.observed_at,
                fresh_until=min(snapshot.expires_at, pending.permit.expires_at),
                reason=snapshot.reason,
            ),
            proposal=proposal,
            calendars=snapshot.calendars,
            agenda=events if proposal.kind == "agenda" or snapshot.status == "partial" else (),
            unoccupied=(
                _unoccupied(proposal, events)
                if proposal.kind == "unoccupied_intervals" and snapshot.status == "ok"
                else ()
            ),
            answer_kind="incomplete" if snapshot.status == "partial" else proposal.kind,
        )
        self._answer = answer
        return answer

    def _receipt(
        self, pending: PendingCalendarRead, status: str, now: datetime, **fields: object
    ) -> CalendarSourceReceipt:
        permit = pending.permit
        receipt = CalendarSourceReceipt.model_validate(
            {
                "receipt_id": uuid4(),
                "proposal_id": permit.proposal.proposal_id,
                "turn_id": permit.proposal.turn_id,
                "chat_id": permit.proposal.chat_id,
                "request_id": pending.request.request_id,
                "permit_id": permit.permit_id,
                "grant_id": permit.read_grant_id,
                "generation": permit.read_generation,
                "principal": permit.proposal.principal,
                "read_window": SourceInterval(
                    starts_at=pending.request.starts_at, ends_at=pending.request.ends_at
                ),
                "timezone": pending.request.timezone,
                "resources": permit.resources,
                "status": status,
                "received_at": now,
                **fields,
            }
        )
        self._emit(
            "receipt_accepted",
            now,
            permit_id=permit.permit_id,
            request_id=pending.request.request_id,
            receipt_id=receipt.receipt_id,
            outcome=status,
        )
        return receipt

    def current_answer(self) -> GroundedCalendarAnswer | None:
        answer, permit = self._answer, self._permit
        if answer is None or permit is None:
            return None
        try:
            now = self._admit(permit)
            if answer.receipt.status in {"denied", "unavailable", "error"}:
                return answer
            snapshot = self._reads.snapshot
            if (
                answer.receipt.fresh_until is None
                or now >= answer.receipt.fresh_until
                or snapshot is None
                or snapshot.request.request_id != answer.receipt.request_id
            ):
                self.clear()
                return None
        except (CalendarDialogueDenied, CalendarAccessError):
            self.clear()
            return None
        return answer


def _interval(event: CalendarOccurrence) -> SourceInterval:
    if event.starts_at is not None and event.ends_at is not None:
        return SourceInterval(starts_at=event.starts_at, ends_at=event.ends_at)
    assert event.all_day_start is not None
    assert event.all_day_end is not None
    zone = ZoneInfo(event.timezone)
    return SourceInterval(
        starts_at=_unambiguous_midnight(event.all_day_start, zone),
        ends_at=_unambiguous_midnight(event.all_day_end, zone),
    )


def _unoccupied(
    proposal: CalendarReadProposal, events: tuple[CalendarOccurrence, ...]
) -> tuple[SourceInterval, ...]:
    """UTC interval union/complement; all active/tentative events conservatively block.

    Native availability is not exported by ADR-0082. This is absence of selected
    calendar records, not personal availability. Partial results never reach here.
    """
    window = proposal.window
    cursor = window.starts_at
    minimum = timedelta(minutes=proposal.minimum_minutes)
    gaps: list[SourceInterval] = []
    intervals = sorted((_interval(event) for event in events), key=lambda item: item.starts_at)
    for item in intervals:
        start, end = (
            max(item.starts_at, window.starts_at),
            min(item.ends_at, window.ends_at),
        )
        if start - cursor >= minimum:
            gaps.append(SourceInterval(starts_at=cursor, ends_at=start))
        cursor = max(cursor, end)
    if window.ends_at - cursor >= minimum:
        gaps.append(SourceInterval(starts_at=cursor, ends_at=window.ends_at))
    return tuple(gaps)
