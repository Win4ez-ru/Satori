"""Faithful public synthetic calendar port; never selected by normal composition."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionScope,
    CalendarCreateAttempt,
    CalendarCreateInput,
    CalendarLookupReceipt,
    CalendarProviderReceipt,
)


@dataclass(frozen=True)
class FixtureCalendarEvent:
    event_id: str
    scope: CalendarActionScope
    command: CalendarCreateInput
    token: str
    cancelled: bool = False


class FixtureCalendarActionProvider:
    provider_kind: Literal["synthetic_fixture"] = "synthetic_fixture"
    external_effects = False

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self.clock = clock
        self.events: dict[str, FixtureCalendarEvent] = {}
        self.create_calls = 0
        self.effects = 0
        self.lookup_calls = 0
        self.fail_after_create = False
        self.on_ready: Callable[[], None] | None = None
        self.after_effect: Callable[[], None] | None = None
        self.receipt_transform: (
            Callable[[CalendarProviderReceipt], CalendarProviderReceipt] | None
        ) = None
        self.pause_before_effect: asyncio.Event | None = None

    @staticmethod
    def _same_calendar(left: CalendarActionScope, right: CalendarActionScope) -> bool:
        return (left.provider, left.account_id, left.calendar_id) == (
            right.provider,
            right.account_id,
            right.calendar_id,
        )

    def _overlap(self, attempt: CalendarCreateAttempt) -> bool:
        command, scope = attempt.proposal.command, attempt.proposal.scope
        return any(
            self._same_calendar(event.scope, scope)
            and not event.cancelled
            and event.command.starts_at < command.ends_at
            and event.command.ends_at > command.starts_at
            for event in self.events.values()
        )

    async def create(
        self, attempt: CalendarCreateAttempt, *, before_effect: Callable[[], None]
    ) -> CalendarProviderReceipt:
        self.create_calls += 1
        if self._overlap(attempt):
            return CalendarProviderReceipt(
                attempt_id=attempt.attempt_id,
                idempotency_token=attempt.idempotency_token,
                payload_digest=attempt.proposal.payload_digest,
                provider="synthetic_fixture",
                status="rejected",
                observed_at=self.clock(),
                reason="calendar_action_stale",
            )
        if self.on_ready is not None:
            self.on_ready()
        if self.pause_before_effect is not None:
            await self.pause_before_effect.wait()
        before_effect()
        if self._overlap(attempt):
            return CalendarProviderReceipt(
                attempt_id=attempt.attempt_id,
                idempotency_token=attempt.idempotency_token,
                payload_digest=attempt.proposal.payload_digest,
                provider="synthetic_fixture",
                status="rejected",
                observed_at=self.clock(),
                reason="calendar_action_stale",
            )
        event_id = "public-synthetic-event-" + str(self.effects + 1)
        self.events[attempt.idempotency_token] = FixtureCalendarEvent(
            event_id, attempt.proposal.scope, attempt.proposal.command, attempt.idempotency_token
        )
        self.effects += 1
        if self.after_effect is not None:
            self.after_effect()
        if self.fail_after_create:
            raise TimeoutError("synthetic transport outcome unknown")
        receipt = CalendarProviderReceipt(
            attempt_id=attempt.attempt_id,
            idempotency_token=attempt.idempotency_token,
            payload_digest=attempt.proposal.payload_digest,
            provider="synthetic_fixture",
            status="created",
            observed_at=self.clock(),
            event_id=event_id,
            exact_fields_verified=True,
            fresh_store_verified=True,
        )
        return self.receipt_transform(receipt) if self.receipt_transform is not None else receipt

    async def lookup(
        self,
        attempt: CalendarCreateAttempt,
        scope: CalendarActionScope,
        *,
        before_read: Callable[[], None],
    ) -> CalendarLookupReceipt:
        self.lookup_calls += 1
        before_read()
        event = self.events.get(attempt.idempotency_token)
        exact = (
            event is not None
            and self._same_calendar(event.scope, scope)
            and event.command == attempt.proposal.command
        )
        status: Literal["owned_exact", "absent", "conflict"] = (
            "owned_exact" if exact else ("absent" if event is None else "conflict")
        )
        return CalendarLookupReceipt(
            attempt_id=attempt.attempt_id,
            idempotency_token=attempt.idempotency_token,
            payload_digest=attempt.proposal.payload_digest,
            provider="synthetic_fixture",
            status=status,
            observed_at=self.clock(),
            event_id=event.event_id if exact and event is not None else None,
            exact_fields_verified=exact,
            fresh_store_verified=True,
        )
