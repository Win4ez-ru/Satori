"""Private user-authored reminders and correlated notification receipts."""

import hashlib
import json
import unicodedata
from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import require_timezone, require_utc

MAX_REMINDERS = 1000
MAX_REMINDER_TITLE = 500
MAX_REMINDER_HORIZON_DAYS = 1830
ReminderState = Literal["scheduled", "due", "cancelled"]
DeliveryState = Literal[
    "not_requested",
    "submitting",
    "scheduled",
    "delivered",
    "cancelling",
    "cancelled",
    "unknown",
    "unavailable",
    "absent",
]
DeliveryOperation = Literal["schedule", "cancel", "reconcile"]


class ReminderError(ValueError):
    """Closed, content-free diagnostics; never include titles or storage paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def reminder_title(value: str) -> str:
    value = value.strip()
    if (
        not value
        or len(value) > MAX_REMINDER_TITLE
        or any(
            unicodedata.category(char) in {"Cc", "Cs"}
            or char in "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
            for char in value
        )
    ):
        raise ValueError("reminder_title_invalid")
    return value


def reminder_digest(title: str, due_at: datetime, timezone: str) -> str:
    value = {"title": title, "due_at": require_utc(due_at).isoformat(), "timezone": timezone}
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ReminderDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class ReminderCreate(ReminderDTO):
    request_id: UUID
    title: str = Field(min_length=1, max_length=MAX_REMINDER_TITLE, repr=False)
    due_at: datetime
    timezone: str

    _title = field_validator("title")(reminder_title)
    _due = field_validator("due_at")(require_utc)
    _zone = field_validator("timezone")(require_timezone)

    @property
    def payload_digest(self) -> str:
        return reminder_digest(self.title, self.due_at, self.timezone)


class ReminderRecord(ReminderDTO):
    schema_version: Literal[1] = 1
    reminder_id: UUID
    request_id: UUID
    title: str = Field(min_length=1, max_length=MAX_REMINDER_TITLE, repr=False)
    due_at: datetime
    timezone: str
    created_at: datetime
    updated_at: datetime
    state: ReminderState = "scheduled"
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    delivery_state: DeliveryState = "not_requested"
    delivery_attempt_id: UUID | None = None
    delivery_operation: DeliveryOperation | None = None
    notification_identifier: str = Field(min_length=1, max_length=100)

    _title = field_validator("title")(reminder_title)
    _dates = field_validator("due_at", "created_at", "updated_at")(require_utc)
    _zone = field_validator("timezone")(require_timezone)

    @model_validator(mode="after")
    def coherent(self) -> "ReminderRecord":
        if self.payload_digest != reminder_digest(self.title, self.due_at, self.timezone):
            raise ValueError("reminder_payload_digest_mismatch")
        if self.updated_at < self.created_at:
            raise ValueError("reminder_clock_invalid")
        if self.notification_identifier != f"satori.reminder.{self.reminder_id}":
            raise ValueError("reminder_notification_identifier_invalid")
        if (self.delivery_attempt_id is None) != (self.delivery_operation is None):
            raise ValueError("reminder_delivery_attempt_required")
        return self


class ReminderActionReceipt(ReminderDTO):
    request_id: UUID
    operation: Literal["create", "cancel"]
    status: Literal["created", "existing", "cancelled", "unchanged"]
    record: ReminderRecord
    observed_at: datetime
    origin: Literal["local_reminder_store"] = "local_reminder_store"
    authority: Literal["explicit_user_request"] = "explicit_user_request"

    _observed = field_validator("observed_at")(require_utc)


class ReminderDeliveryRequest(ReminderDTO):
    schema_version: Literal[1] = 1
    owner_id: UUID
    generation: UUID
    attempt_id: UUID
    reminder_id: UUID
    operation: DeliveryOperation
    notification_identifier: str = Field(min_length=1, max_length=100)
    due_at: datetime
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Generic lock-screen content is the default; title disclosure is a separate opt-in.
    title: str = Field(default="Напоминание Сатори", max_length=MAX_REMINDER_TITLE, repr=False)

    _due = field_validator("due_at")(require_utc)


class ReminderDeliveryReceipt(ReminderDTO):
    attempt_id: UUID
    reminder_id: UUID
    operation: DeliveryOperation
    notification_identifier: str = Field(min_length=1, max_length=100)
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["scheduled", "delivered", "cancelled", "absent", "unknown", "unavailable"]
    observed_at: datetime
    # True only after an actual platform request/readback; a saved row is not a receipt.
    platform: Literal["native_os", "local_watchdog"] = "native_os"

    _observed = field_validator("observed_at")(require_utc)

    @model_validator(mode="after")
    def actual_native_positive(self) -> "ReminderDeliveryReceipt":
        if (
            self.status in {"scheduled", "delivered", "cancelled", "absent"}
            and self.platform != "native_os"
        ):
            raise ValueError("reminder_native_receipt_required")
        return self


class ReminderDeliveryPort(Protocol):
    def submit(
        self,
        request: ReminderDeliveryRequest,
        complete: Callable[[ReminderDeliveryReceipt], None],
    ) -> None:
        """Bounded asynchronous platform operation, never a canonical/model callback."""


class ReminderStore(Protocol):
    owner_id: UUID

    @property
    def last_observed_at(self) -> datetime | None: ...
    def list(self) -> tuple[ReminderRecord, ...]: ...
    def prior_create(self, proposal: ReminderCreate) -> ReminderRecord | None: ...
    def create(self, proposal: ReminderCreate, now: datetime) -> ReminderActionReceipt: ...
    def cancel(
        self, reminder_id: UUID, request_id: UUID, now: datetime
    ) -> ReminderActionReceipt: ...
    def reconcile_action(self, request_id: UUID, now: datetime) -> ReminderActionReceipt | None: ...
    def mark_due(self, now: datetime) -> tuple[ReminderRecord, ...]: ...
    def recover_inflight(self, now: datetime) -> None: ...
    def begin_delivery(
        self,
        reminder_id: UUID,
        operation: DeliveryOperation,
        generation: UUID,
        now: datetime,
        *,
        disclose_title: bool = False,
    ) -> ReminderDeliveryRequest: ...
    def accept_delivery(
        self,
        request: ReminderDeliveryRequest,
        receipt: ReminderDeliveryReceipt,
        now: datetime,
    ) -> ReminderRecord: ...
    def close(self) -> None: ...
