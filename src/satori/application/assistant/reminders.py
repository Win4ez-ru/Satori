"""Single reminder use-case owner; storage and OS delivery are separate receipts."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from satori.application.assistant.contracts import require_utc
from satori.application.assistant.reminder_contracts import (
    MAX_REMINDER_HORIZON_DAYS,
    DeliveryOperation,
    ReminderActionReceipt,
    ReminderCreate,
    ReminderDeliveryReceipt,
    ReminderDeliveryRequest,
    ReminderError,
    ReminderRecord,
    ReminderStore,
)


class ReminderManager:
    """Serialized owner, invoked only after an explicit UI/device-scoped user action."""

    def __init__(
        self,
        store: ReminderStore,
        *,
        clock: Callable[[], datetime] | None = None,
        recover_inflight: bool = True,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last_now = store.last_observed_at
        self.generation = uuid4()
        self._closed = False
        now = self._now()
        if recover_inflight:
            self.store.recover_inflight(now)

    def _now(self) -> datetime:
        if self._closed:
            raise ReminderError("reminder_owner_closed")
        try:
            now = require_utc(self._clock())
        except (ValueError, TypeError):
            raise ReminderError("reminder_clock_invalid") from None
        if self._last_now is not None and now < self._last_now:
            raise ReminderError("reminder_clock_invalid")
        self._last_now = now
        return now

    @property
    def records(self) -> tuple[ReminderRecord, ...]:
        self._now()
        return self.store.list()

    def create(self, proposal: ReminderCreate) -> ReminderActionReceipt:
        proposal = ReminderCreate.model_validate_json(proposal.model_dump_json())
        now = self._now()
        # Idempotent replay must work even after its original due time has passed.
        if not now < proposal.due_at <= now + timedelta(days=MAX_REMINDER_HORIZON_DAYS):
            existing = self.store.prior_create(proposal)
            if existing is None or existing.payload_digest != proposal.payload_digest:
                raise ReminderError("reminder_time_not_future")
        return self.store.create(proposal, now)

    def cancel(self, reminder_id: UUID, request_id: UUID) -> ReminderActionReceipt:
        if (
            not isinstance(reminder_id, UUID)
            or not isinstance(request_id, UUID)
            or not reminder_id.int
            or not request_id.int
        ):
            raise ReminderError("reminder_input_invalid")
        return self.store.cancel(reminder_id, request_id, self._now())

    def tick(self) -> tuple[ReminderRecord, ...]:
        return self.store.mark_due(self._now())

    def reconcile_action(self, request_id: UUID) -> ReminderActionReceipt | None:
        """Observe the durable original request; never replay a write or OS action."""
        if not isinstance(request_id, UUID) or not request_id.int:
            raise ReminderError("reminder_input_invalid")
        return self.store.reconcile_action(request_id, self._now())

    def begin_delivery(
        self, reminder_id: UUID, operation: DeliveryOperation, *, disclose_title: bool = False
    ) -> ReminderDeliveryRequest:
        return self.store.begin_delivery(
            reminder_id,
            operation,
            self.generation,
            self._now(),
            disclose_title=disclose_title,
        )

    def accept_delivery(
        self, request: ReminderDeliveryRequest, receipt: ReminderDeliveryReceipt
    ) -> ReminderRecord:
        if request.generation != self.generation:
            raise ReminderError("reminder_delivery_stale")
        receipt = ReminderDeliveryReceipt.model_validate_json(receipt.model_dump_json())
        return self.store.accept_delivery(request, receipt, self._now())

    def close(self) -> None:
        self._closed = True
        self.generation = uuid4()
        self.store.close()
