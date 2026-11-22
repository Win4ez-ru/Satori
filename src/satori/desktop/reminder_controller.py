"""Qt adapter for a single private reminder owner and optional correlated OS delivery."""

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from satori.application.assistant.reminder_contracts import (
    DeliveryOperation,
    ReminderActionReceipt,
    ReminderCreate,
    ReminderDeliveryPort,
    ReminderDeliveryReceipt,
    ReminderDeliveryRequest,
    ReminderError,
    ReminderRecord,
)
from satori.application.assistant.reminders import ReminderManager
from satori.infrastructure.assistant.reminder_store import SQLiteReminderStore


class ReminderController(QObject):
    """No I/O on construction; existing stores restore after an explicit root hook."""

    changed = Signal()
    due = Signal(object)
    delivery_requested = Signal(object)
    _delivery_completed = Signal(object, object)

    def __init__(
        self,
        data_directory: Path,
        parent: QObject | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        delivery: ReminderDeliveryPort | None = None,
    ) -> None:
        super().__init__(parent)
        self._directory = data_directory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic_clock
        self._delivery = delivery
        self._manager: ReminderManager | None = None
        self._records: tuple[ReminderRecord, ...] = ()
        self._pending: dict[UUID, tuple[ReminderDeliveryRequest, float]] = {}
        self._closed = False
        self.status = "disabled"
        self.diagnostic: str | None = None
        self.last_receipt: ReminderActionReceipt | None = None
        self.disclose_notification_title = False
        self._delivery_completed.connect(self._accept_delivery, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.tick)

    @property
    def records(self) -> tuple[ReminderRecord, ...]:
        return self._records

    @property
    def enabled(self) -> bool:
        return self._manager is not None and not self._closed

    @property
    def notification_available(self) -> bool:
        return self._delivery is not None

    @property
    def store_path(self) -> Path:
        return self._directory / "assistant" / "reminders.sqlite3"

    def restore_existing(self) -> bool:
        """Restore only the fixed prior user-enabled store, never create on startup."""
        if self._closed:
            return False
        try:
            exists = self.store_path.exists() or self.store_path.is_symlink()
        except OSError:
            self._error("reminder_store_unavailable")
            return False
        return self.enable() if exists else False

    def enable(self) -> bool:
        if self._closed:
            return False
        if self._manager is not None:
            return True
        store: SQLiteReminderStore | None = None
        try:
            store = SQLiteReminderStore(self.store_path)
            self._manager = ReminderManager(store, clock=self._clock)
        except ReminderError as error:
            if store is not None:
                store.close()
            self._error(error.code)
            return False
        self.status = "ready"
        self.diagnostic = None
        self._timer.start()
        self.tick()
        return True

    def _error(self, code: str) -> None:
        self.status = "error"
        self.diagnostic = code
        self.changed.emit()

    def refresh(self) -> None:
        if self._manager is None:
            return
        try:
            self._records = self._manager.records
        except ReminderError as error:
            self._records = ()
            self._error(error.code)
            return
        self.changed.emit()

    def create(
        self, title: str, due_at: datetime, timezone: str, request_id: UUID | None = None
    ) -> ReminderActionReceipt | None:
        if not self.enable() or self._manager is None:
            return None
        try:
            proposal = ReminderCreate(
                request_id=request_id or uuid4(),
                title=title,
                due_at=due_at,
                timezone=timezone,
            )
            receipt = self._manager.create(proposal)
        except ReminderError as error:
            self._error(error.code)
            return None
        except ValueError:
            self._error("reminder_input_invalid")
            return None
        self.last_receipt = receipt
        self.status = receipt.status
        self.diagnostic = None
        self.refresh()
        if (
            self._delivery is not None
            and receipt.record.state == "scheduled"
            and receipt.record.delivery_state in {"not_requested", "unavailable"}
        ):
            self.request_delivery(receipt.record.reminder_id)
        return receipt

    def cancel(
        self, reminder_id: UUID, request_id: UUID | None = None
    ) -> ReminderActionReceipt | None:
        if self._manager is None:
            self._error("reminder_not_enabled")
            return None
        try:
            receipt = self._manager.cancel(reminder_id, request_id or uuid4())
        except ReminderError as error:
            self._error(error.code)
            return None
        self.last_receipt = receipt
        self.status = receipt.status
        self.diagnostic = None
        self.refresh()
        if receipt.record.delivery_state in {
            "submitting",
            "scheduled",
            "unknown",
            "cancelling",
            "delivered",
        }:
            self._dispatch(reminder_id, "cancel")
        return receipt

    def reconcile_action(self, request_id: UUID) -> ReminderActionReceipt | None:
        """Read an existing durable action, including cancel IDs; never enable or replay."""
        if self._closed:
            return None
        if not isinstance(request_id, UUID) or not request_id.int:
            self._error("reminder_input_invalid")
            return None
        temporary: ReminderManager | None = None
        store: SQLiteReminderStore | None = None
        try:
            manager = self._manager
            if manager is None:
                if not self.store_path.exists() and not self.store_path.is_symlink():
                    return None
                store = SQLiteReminderStore(self.store_path, read_only=True)
                temporary = ReminderManager(store, clock=self._clock, recover_inflight=False)
                manager = temporary
            receipt = manager.reconcile_action(request_id)
        except ReminderError as error:
            self._error(error.code)
            return None
        except OSError:
            self._error("reminder_store_unavailable")
            return None
        finally:
            if temporary is not None:
                temporary.close()
            elif store is not None:
                store.close()
        self.diagnostic = None
        if receipt is not None:
            self.last_receipt = receipt
            if self._manager is not None:
                current = {
                    record.reminder_id: record for record in (*self._records, receipt.record)
                }
                self._records = tuple(
                    sorted(
                        current.values(),
                        key=lambda record: (
                            record.due_at,
                            record.created_at,
                            str(record.reminder_id),
                        ),
                    )
                )
            self.changed.emit()
        return receipt

    def set_delivery_port(
        self, delivery: ReminderDeliveryPort | None, *, schedule_existing: bool = False
    ) -> None:
        self._delivery = delivery
        if delivery is not None and schedule_existing:
            for record in self.records:
                if record.state == "scheduled" and record.delivery_state in {
                    "not_requested",
                    "unavailable",
                }:
                    self.request_delivery(record.reminder_id)
        self.changed.emit()

    def request_delivery(self, reminder_id: UUID) -> bool:
        return self._dispatch(reminder_id, "schedule")

    def reconcile_delivery(self, reminder_id: UUID) -> bool:
        return self._dispatch(reminder_id, "reconcile")

    def _dispatch(self, reminder_id: UUID, operation: DeliveryOperation) -> bool:
        if self._manager is None or self._closed:
            return False
        try:
            request = self._manager.begin_delivery(
                reminder_id,
                operation,
                disclose_title=self.disclose_notification_title,
            )
        except ReminderError as error:
            self._error(error.code)
            return False
        self._pending[request.attempt_id] = (request, self._monotonic() + 15)
        self.delivery_requested.emit(request)
        self.refresh()

        def complete(receipt: ReminderDeliveryReceipt) -> None:
            # A destroyed UI cannot authorize a late callback.
            with suppress(RuntimeError):
                self._delivery_completed.emit(request, receipt)

        if self._delivery is None:
            complete(
                self._unknown(request, status="unknown" if operation == "cancel" else "unavailable")
            )
        else:
            try:
                self._delivery.submit(request, complete)
            except Exception:
                # Native dispatch may already have happened; do not retry it.
                complete(self._unknown(request))
        return True

    def _unknown(
        self, request: ReminderDeliveryRequest, *, status: str = "unknown"
    ) -> ReminderDeliveryReceipt:
        return ReminderDeliveryReceipt.model_validate(
            {
                "attempt_id": request.attempt_id,
                "reminder_id": request.reminder_id,
                "operation": request.operation,
                "notification_identifier": request.notification_identifier,
                "payload_digest": request.payload_digest,
                "status": status,
                "observed_at": self._clock(),
                "platform": "local_watchdog",
            }
        )

    def _accept_delivery(self, request: object, receipt: object) -> None:
        if not isinstance(request, ReminderDeliveryRequest) or not isinstance(
            receipt, ReminderDeliveryReceipt
        ):
            return
        pending = self._pending.get(request.attempt_id)
        if pending is None or pending[0] != request or self._closed or self._manager is None:
            return
        if self._monotonic() >= pending[1]:
            receipt = self._unknown(request)
        self._pending.pop(request.attempt_id, None)
        try:
            self._manager.accept_delivery(request, receipt)
        except (ReminderError, ValueError) as error:
            with suppress(ReminderError, ValueError):
                self._manager.accept_delivery(request, self._unknown(request))
            self._error(
                error.code if isinstance(error, ReminderError) else "reminder_delivery_invalid"
            )
            return
        self.refresh()

    def tick(self) -> None:
        if self._closed or self._manager is None:
            return
        for request, deadline in tuple(self._pending.values()):
            if self._monotonic() >= deadline:
                self._accept_delivery(request, self._unknown(request))
        try:
            due = self._manager.tick()
            current = self._manager.records
        except ReminderError as error:
            self._error(error.code)
            return
        changed = current != self._records
        self._records = current
        for record in due:
            self.due.emit(record)
        if due:
            self.status = "due"
        if changed:
            self.changed.emit()

    def set_profile(self, data_directory: Path) -> None:
        self.shutdown()
        self._directory = data_directory
        self._closed = False
        self.status = "disabled"
        self.diagnostic = None
        self.last_receipt = None
        self.restore_existing()
        self.changed.emit()

    def shutdown(self) -> None:
        self._closed = True
        self._timer.stop()
        self._pending.clear()
        self._records = ()
        if self._manager is not None:
            self._manager.close()
            self._manager = None
