"""Single create-only calendar owner; no OS, model, account or canonical-state I/O."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from satori.application.assistant.calendar_actions_contracts import (
    MAX_CALENDAR_ACTION_CATALOG_AGE,
    MAX_CALENDAR_APPROVAL_LIFETIME,
    MAX_CALENDAR_EVENT_HORIZON,
    MAX_CALENDAR_PROPOSAL_LIFETIME,
    MAX_CALENDAR_PROVIDER_LIFETIME,
    CalendarActionCatalog,
    CalendarActionCode,
    CalendarActionError,
    CalendarActionProvider,
    CalendarActionRecord,
    CalendarActionScope,
    CalendarActionState,
    CalendarActionStore,
    CalendarCreateApproval,
    CalendarCreateAttempt,
    CalendarCreateInput,
    CalendarCreateProposal,
    CalendarLookupReceipt,
    CalendarProviderReceipt,
    calendar_action_digest,
)
from satori.application.assistant.contracts import require_utc


class CalendarActionOwner:
    """Explicit reviewed create, durable one-use dispatch, read-only reconciliation.

    ``precondition_guard`` is repeatable, synchronous and must raise on revoked
    device/session/source/planning authority. It grants no permission by itself.
    A provider must call the supplied one-use callback immediately before effect.
    Receipt admission revalidates the same current scope after the awaited I/O.
    """

    def __init__(
        self,
        store: CalendarActionStore,
        provider: CalendarActionProvider,
        *,
        clock: Callable[[], datetime] | None = None,
        real_writes_enabled: bool = False,
    ) -> None:
        if type(real_writes_enabled) is not bool:
            raise CalendarActionError("calendar_action_invalid")
        self.store, self.provider = store, provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last_now = store.last_observed_at
        self._epoch = uuid4()
        self._closed = False
        self._catalog: CalendarActionCatalog | None = None
        self._real_writes_enabled = real_writes_enabled
        self._inflight: set[UUID] = set()
        store.recover_inflight(self._now())

    def _now(self) -> datetime:
        if self._closed:
            raise CalendarActionError("calendar_action_revoked")
        try:
            now = require_utc(self._clock())
            if self._last_now is not None and now < self._last_now:
                raise ValueError
        except (TypeError, ValueError):
            self.revoke()
            raise CalendarActionError("calendar_action_clock_invalid") from None
        self._last_now = now
        return now

    def revoke(self) -> None:
        """Never converts an in-flight effect to 'not written'. Its claim remains."""
        self._epoch = uuid4()
        self._catalog = None

    def accept_catalog(self, catalog: CalendarActionCatalog) -> None:
        """Record a separate explicit observation; this issues no write grant."""
        try:
            checked = CalendarActionCatalog.model_validate(catalog)
            self._check_catalog(checked.scope, checked, self._now(), for_write=False)
        except CalendarActionError:
            self.revoke()
            raise
        except (TypeError, ValueError):
            self.revoke()
            raise CalendarActionError("calendar_action_catalog_stale") from None
        self._catalog = checked

    def _check_catalog(
        self,
        scope: CalendarActionScope,
        catalog: CalendarActionCatalog,
        now: datetime,
        *,
        for_write: bool,
    ) -> None:
        if scope != catalog.scope or scope.provider != self.provider.provider_kind:
            raise CalendarActionError("calendar_action_scope_mismatch")
        if not -timedelta(seconds=5) <= now - catalog.observed_at < MAX_CALENDAR_ACTION_CATALOG_AGE:
            raise CalendarActionError("calendar_action_catalog_stale")
        if not catalog.full_access:
            raise CalendarActionError("calendar_action_permission_required")
        if not catalog.supports_events or (
            for_write and (not catalog.writable or catalog.subscribed)
        ):
            raise CalendarActionError("calendar_action_not_writable")

    def _current(
        self, scope: CalendarActionScope, catalog: CalendarActionCatalog, *, for_write: bool
    ) -> datetime:
        now = self._now()
        self._check_catalog(scope, catalog, now, for_write=for_write)
        if self._catalog != catalog:
            raise CalendarActionError("calendar_action_stale")
        return now

    def get(self, request_id: UUID, *, scope: CalendarActionScope) -> CalendarActionRecord | None:
        self._now()
        record = self.store.get(request_id)
        if record is not None and not record.proposal.scope.same_resource(scope):
            raise CalendarActionError("calendar_action_scope_mismatch")
        return record

    def propose(
        self,
        command: CalendarCreateInput,
        *,
        scope: CalendarActionScope,
        catalog: CalendarActionCatalog,
    ) -> CalendarActionRecord:
        command = CalendarCreateInput.model_validate(command)
        scope = CalendarActionScope.model_validate(scope)
        catalog = CalendarActionCatalog.model_validate(catalog)
        self.accept_catalog(catalog)
        now = self._current(scope, catalog, for_write=True)
        if command.timezone != scope.timezone:
            raise CalendarActionError("calendar_action_scope_mismatch")
        existing = self.get(command.request_id, scope=scope)
        if existing is not None:
            if existing.proposal.command != command:
                raise CalendarActionError("calendar_action_request_conflict")
            return existing
        if not now < command.starts_at <= now + MAX_CALENDAR_EVENT_HORIZON:
            raise CalendarActionError("calendar_action_time_invalid")
        resource = scope.model_dump(mode="json", exclude={"session_id", "generation"})
        payload_digest = calendar_action_digest(
            {
                "schema": "satori.calendar-create.v1",
                "command": command.model_dump(mode="json"),
                "resource": resource,
            }
        )
        material = {
            "proposal_id": str(uuid4()),
            "owner_epoch": str(self._epoch),
            "command": command.model_dump(mode="json"),
            "scope": scope.model_dump(mode="json"),
            "catalog_revision": str(catalog.revision),
            "calendar_title": catalog.calendar_title,
            "account_title": catalog.account_title,
            "proposed_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + MAX_CALENDAR_PROPOSAL_LIFETIME).isoformat().replace("+00:00", "Z"),
            "payload_digest": payload_digest,
        }
        proposal = CalendarCreateProposal.model_validate_json(
            json.dumps(material | {"review_digest": calendar_action_digest(material)})
        )
        record = CalendarActionRecord(
            revision=1, proposal=proposal, state="proposed", updated_at=now
        )
        self.store.insert(record)
        return record

    def approve(
        self,
        request_id: UUID,
        review_digest: str,
        *,
        scope: CalendarActionScope,
        catalog: CalendarActionCatalog,
    ) -> CalendarActionRecord:
        self.accept_catalog(catalog)
        now = self._current(scope, catalog, for_write=True)
        record = self._required(request_id, scope)
        self._review_current(record, scope, catalog, now)
        if record.state != "proposed" or review_digest != record.proposal.review_digest:
            raise CalendarActionError("calendar_action_review_required")
        approval = CalendarCreateApproval(
            approval_id=uuid4(),
            proposal_id=record.proposal.proposal_id,
            request_id=request_id,
            owner_epoch=self._epoch,
            review_digest=review_digest,
            scope=scope,
            approved_at=now,
            expires_at=min(now + MAX_CALENDAR_APPROVAL_LIFETIME, record.proposal.expires_at),
        )
        return self._replace(record, state="approved", approval=approval, now=now)

    def _required(self, request_id: UUID, scope: CalendarActionScope) -> CalendarActionRecord:
        record = self.get(request_id, scope=scope)
        if record is None:
            raise CalendarActionError("calendar_action_review_required")
        return record

    def _review_current(
        self,
        record: CalendarActionRecord,
        scope: CalendarActionScope,
        catalog: CalendarActionCatalog,
        now: datetime,
    ) -> None:
        p = record.proposal
        if p.owner_epoch != self._epoch:
            raise CalendarActionError("calendar_action_revoked")
        if p.scope != scope or p.catalog_revision != catalog.revision:
            raise CalendarActionError("calendar_action_scope_mismatch")
        if now >= p.expires_at or now >= p.command.starts_at:
            raise CalendarActionError("calendar_action_approval_expired")

    def _replace(
        self,
        record: CalendarActionRecord,
        *,
        state: CalendarActionState,
        now: datetime,
        reason: CalendarActionCode | None = None,
        approval: CalendarCreateApproval | None = None,
        attempt: CalendarCreateAttempt | None = None,
        receipt: CalendarProviderReceipt | None = None,
        lookup: CalendarLookupReceipt | None = None,
    ) -> CalendarActionRecord:
        value = record.model_dump()
        value.update(revision=record.revision + 1, state=state, updated_at=now, reason=reason)
        for key, item in (
            ("approval", approval),
            ("attempt", attempt),
            ("receipt", receipt),
            ("lookup", lookup),
        ):
            if item is not None:
                value[key] = item
        updated = CalendarActionRecord.model_validate(value)
        self.store.replace(updated, expected_revision=record.revision)
        return updated

    def _guard(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            self.revoke()
            raise CalendarActionError("calendar_action_revoked") from None

    async def dispatch(
        self,
        request_id: UUID,
        approval_id: UUID,
        *,
        scope: CalendarActionScope,
        catalog: CalendarActionCatalog,
        precondition_guard: Callable[[], None],
    ) -> CalendarActionRecord:
        self.accept_catalog(catalog)
        now = self._current(scope, catalog, for_write=True)
        record = self._required(request_id, scope)
        if record.approval is None or record.approval.approval_id != approval_id:
            raise CalendarActionError("calendar_action_review_required")
        if record.state not in {"proposed", "approved"}:
            # Historical receipts and unknown claims are observed, never redispatched.
            return record
        if record.state != "approved":
            raise CalendarActionError("calendar_action_review_required")
        self._review_current(record, scope, catalog, now)
        if now >= record.approval.expires_at:
            raise CalendarActionError("calendar_action_approval_expired")
        if self.provider.external_effects and not self._real_writes_enabled:
            raise CalendarActionError("calendar_action_real_writes_disabled")
        self._guard(precondition_guard)
        now = self._current(scope, catalog, for_write=True)
        self._review_current(record, scope, catalog, now)
        if now >= record.approval.expires_at:
            raise CalendarActionError("calendar_action_approval_expired")
        token = calendar_action_digest(
            {
                "schema": "satori.calendar-create-attempt.v1",
                "request_id": str(request_id),
                "payload_digest": record.proposal.payload_digest,
            }
        )
        attempt = CalendarCreateAttempt(
            attempt_id=uuid4(),
            proposal=record.proposal,
            approval=record.approval,
            idempotency_token=token,
            claimed_at=now,
            expires_at=min(now + MAX_CALENDAR_PROVIDER_LIFETIME, record.approval.expires_at),
        )
        record = self._replace(record, state="dispatch_claimed", attempt=attempt, now=now)
        admitted = False
        callback_used = False

        def before_effect() -> None:
            nonlocal admitted, callback_used
            if callback_used:
                raise CalendarActionError("calendar_action_stale")
            callback_used = True
            self._guard(precondition_guard)
            current = self._current(scope, catalog, for_write=True)
            self._review_current(record, scope, catalog, current)
            if current >= attempt.expires_at:
                raise CalendarActionError("calendar_action_approval_expired")
            admitted = True

        self._inflight.add(request_id)
        try:
            receipt = await self.provider.create(attempt, before_effect=before_effect)
            receipt = CalendarProviderReceipt.model_validate(receipt)
            self._guard(precondition_guard)
            now = self._current(scope, catalog, for_write=True)
            self._review_current(record, scope, catalog, now)
            self._receipt_binding(attempt, receipt, now)
            if now >= attempt.expires_at:
                raise CalendarActionError("calendar_action_receipt_invalid")
            if receipt.status == "created" and not admitted:
                raise CalendarActionError("calendar_action_receipt_invalid")
            if receipt.status == "created":
                return self._replace(record, state="created", receipt=receipt, now=now)
            state: CalendarActionState = "rejected" if receipt.status == "rejected" else "unknown"
            return self._replace(
                record, state=state, receipt=receipt, reason=receipt.reason, now=now
            )
        except asyncio.CancelledError:
            self._unknown(record)
            raise
        except CalendarActionError as error:
            if not admitted and callback_used:
                return self._replace(record, state="rejected", reason=error.code, now=self._now())
            return self._unknown(record)
        except Exception:
            return self._unknown(record)
        finally:
            self._inflight.discard(request_id)

    def _unknown(self, record: CalendarActionRecord) -> CalendarActionRecord:
        # If storage itself is unavailable the durable dispatch claim remains the
        # reconciliation fence. Never manufacture success or create a new claim.
        return self._replace(
            record, state="unknown", reason="calendar_action_unknown_no_replay", now=self._now()
        )

    @staticmethod
    def _receipt_binding(
        attempt: CalendarCreateAttempt,
        receipt: CalendarProviderReceipt | CalendarLookupReceipt,
        now: datetime,
    ) -> None:
        if (
            receipt.attempt_id != attempt.attempt_id
            or receipt.idempotency_token != attempt.idempotency_token
            or receipt.payload_digest != attempt.proposal.payload_digest
            or receipt.provider != attempt.proposal.scope.provider
            or receipt.observed_at < attempt.claimed_at
            or receipt.observed_at > now + timedelta(seconds=5)
        ):
            raise CalendarActionError("calendar_action_receipt_invalid")

    async def reconcile(
        self,
        request_id: UUID,
        *,
        scope: CalendarActionScope,
        catalog: CalendarActionCatalog,
        precondition_guard: Callable[[], None],
    ) -> CalendarActionRecord:
        """Fresh read authority may observe an old attempt; it can never re-arm it."""
        self.accept_catalog(catalog)
        self._current(scope, catalog, for_write=False)
        record = self._required(request_id, scope)
        if record.attempt is None or request_id in self._inflight:
            raise CalendarActionError("calendar_action_reconcile_required")
        epoch = self._epoch
        used = False

        def before_read() -> None:
            nonlocal used
            if used or self._epoch != epoch:
                raise CalendarActionError("calendar_action_revoked")
            used = True
            self._guard(precondition_guard)
            self._current(scope, catalog, for_write=False)
            if self._epoch != epoch:
                raise CalendarActionError("calendar_action_revoked")

        try:
            receipt = await self.provider.lookup(record.attempt, scope, before_read=before_read)
            receipt = CalendarLookupReceipt.model_validate(receipt)
            self._guard(precondition_guard)
            now = self._current(scope, catalog, for_write=False)
            if not used or self._epoch != epoch:
                raise CalendarActionError("calendar_action_revoked")
            self._receipt_binding(record.attempt, receipt, now)
            if now - receipt.observed_at > MAX_CALENDAR_PROVIDER_LIFETIME:
                raise CalendarActionError("calendar_action_receipt_invalid")
            state: CalendarActionState = {
                "owned_exact": "reconciled_present",
                "absent": "reconciled_absent",
                "conflict": "conflict",
                "unknown": "unknown",
            }[receipt.status]  # type: ignore[assignment]
            return self._replace(record, state=state, lookup=receipt, now=now)
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._unknown(record)

    def close(self) -> None:
        self.revoke()
        self._closed = True
        self.store.close()
