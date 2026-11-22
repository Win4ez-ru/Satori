"""Explicit local schedule import; CalendarController owns the only native process."""

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from PySide6.QtCore import QObject, Signal

from satori.application.assistant.contracts import (
    CalendarSourceRef,
    require_display_text,
    require_utc,
)
from satori.application.assistant.schedule import (
    SchedulePlanCoordinator,
    export_schedule_ics,
    parse_schedule_json,
)
from satori.application.assistant.schedule_contracts import (
    ReviewedSchedule,
    ScheduleError,
    ScheduleOCRBounds,
    SchedulePlan,
    ScheduleRowResult,
)
from satori.desktop.schedule_journal import ScheduleJournal, ScheduleJournalRecord

if TYPE_CHECKING:
    from satori.desktop.calendar_controller import CalendarController

ScheduleOperation = Literal[
    "recognize_schedule_image", "preflight_schedule", "create_schedule", "reconcile_schedule"
]
ScheduleNativeError = Literal[
    "invalid_request",
    "permission_required",
    "permission_denied",
    "permission_restricted",
    "permission_request_failed",
    "calendar_unavailable",
    "calendar_scope_mismatch",
    "catalog_too_large",
    "calendar_data_invalid",
    "calendar_read_failed",
    "response_too_large",
    "operation_timeout",
    "platform_unavailable",
    "image_unavailable",
    "image_invalid",
    "ocr_failed",
    "calendar_not_writable",
    "schedule_conflict",
    "schedule_preflight_failed",
    "operation_unknown",
]
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class OCRLine(_Wire):
    text: str = Field(min_length=1, max_length=512, repr=False)
    confidence: float = Field(ge=0, le=1)
    bounds: ScheduleOCRBounds | None = Field(default=None, repr=False)

    _text = field_validator("text")(require_display_text)

    @field_validator("confidence", mode="before")
    @classmethod
    def numeric_confidence(cls, value: object) -> object:
        if type(value) not in {int, float}:
            raise ValueError("ocr_confidence_invalid")
        return value


class _Response(_Wire):
    schema_version: Literal[1]
    request_id: UUID
    operation: ScheduleOperation
    status: Literal["ok", "error"]
    access: Literal[
        "not_determined", "restricted", "denied", "write_only", "full_access", "unknown"
    ]
    error: ScheduleNativeError | None
    observed_at: datetime
    plan_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    schedule_results: tuple[ScheduleRowResult, ...] | None = Field(
        default=None, min_length=1, max_length=100, repr=False
    )
    ocr_lines: tuple[OCRLine, ...] | None = Field(default=None, max_length=200, repr=False)
    ocr_partial: bool | None = None

    _utc = field_validator("observed_at")(require_utc)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schedule_schema_invalid")
        return value

    @model_validator(mode="after")
    def payload(self) -> "_Response":
        if self.status == "error":
            if self.error is None or any(
                value is not None
                for value in (
                    self.plan_fingerprint,
                    self.schedule_results,
                    self.ocr_lines,
                    self.ocr_partial,
                )
            ):
                raise ValueError("schedule_error_payload_invalid")
        elif self.error is not None:
            raise ValueError("schedule_error_invalid")
        elif self.operation == "recognize_schedule_image":
            if (
                self.ocr_lines is None
                or self.ocr_partial is None
                or any(
                    value is not None for value in (self.plan_fingerprint, self.schedule_results)
                )
            ):
                raise ValueError("schedule_ocr_payload_invalid")
        elif (
            self.plan_fingerprint is None
            or self.schedule_results is None
            or any(value is not None for value in (self.ocr_lines, self.ocr_partial))
        ):
            raise ValueError("schedule_result_payload_invalid")
        return self


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


class ScheduleController(QObject):
    """One ephemeral import owner, outside conversation, learning, providers and TTS."""

    changed = Signal()

    def __init__(
        self, owner: "CalendarController", *, data_directory: Path, clock: Callable[[], datetime]
    ) -> None:
        super().__init__(owner)
        self._owner = owner
        self._clock = clock
        self._policy = SchedulePlanCoordinator(owner.principal, clock=clock)
        self._context = (owner.principal, owner.generation)
        self._pending: ScheduleOperation | None = None
        self._pending_plan: SchedulePlan | None = None
        self._pending_body: bytes | None = None
        self._requested_at: datetime | None = None
        self.status = "idle"
        self.ocr_lines: tuple[OCRLine, ...] = ()
        self.ocr_partial = False
        self.reviewed_schedule: ReviewedSchedule | None = None
        self.receipt: dict[str, object] | None = None
        self._journal = ScheduleJournal(data_directory)
        self._journal_pending: ScheduleJournalRecord | None = None
        self._journal_error: str | None = None
        self._read_journal()
        if self._journal_error or self._journal_pending:
            self.status = self._journal_error or "schedule_recovery_required"
        owner.changed.connect(self._context_changed)

    def _read_journal(self) -> None:
        try:
            self._journal_pending = self._journal.pending()
            self._journal_error = None
        except ScheduleError as error:
            self._journal_error = error.code

    def set_profile(self, data_directory: Path) -> None:
        self._journal = ScheduleJournal(data_directory)
        self._journal_pending = None
        self._journal_error = None
        self._read_journal()

    def _journal_matches(self, plan: SchedulePlan) -> bool:
        record = self._journal_pending
        return record is not None and (
            record.principal.os_uid == plan.principal.os_uid
            and record.principal.profile_id == plan.principal.profile_id
            and record.target == plan.target
            and set(record.row_tokens) == {row.owned_token for row in plan.rows}
        )

    @property
    def busy(self) -> bool:
        return self._pending is not None

    @property
    def prepared_plan(self) -> SchedulePlan | None:
        return self._policy.plan

    @property
    def state(self) -> str:
        return self._policy.state

    def _target_current(self, plan: SchedulePlan) -> bool:
        return plan.principal == self._owner.principal and any(
            item.source == plan.target and item.writable for item in self._owner.catalog
        )

    def _fresh_binding(self, plan: SchedulePlan) -> bool:
        try:
            return (
                self._target_current(plan)
                and plan.generation == self._owner.generation
                and require_utc(self._clock()) < plan.expires_at
            )
        except (ValueError, TypeError, AttributeError):
            return False

    @property
    def can_create(self) -> bool:
        plan = self.prepared_plan
        return (
            plan is not None
            and self._policy.state == "ready"
            and not self.busy
            and not self._owner.busy
            and self._fresh_binding(plan)
            and self._journal_pending is None
            and self._journal_error is None
        )

    @property
    def can_reconcile(self) -> bool:
        plan = self.prepared_plan
        return (
            plan is not None
            and self._policy.state == "unknown"
            and not self.busy
            and not self._owner.busy
            and self._target_current(plan)
            and self._journal_error is None
            and self._journal_matches(plan)
        )

    def _notify(self, status: str) -> None:
        self.status = status
        self.changed.emit()

    def _context_changed(self) -> None:
        context = (self._owner.principal, self._owner.generation)
        if context == self._context:
            return
        principal_changed = context[0] != self._context[0]
        self._context = context
        if self.busy:
            self.transport_failed("schedule_context_changed")
        if principal_changed:
            self._policy = SchedulePlanCoordinator(context[0], clock=self._clock)
            self.ocr_lines = ()
            self.ocr_partial = False
            self.reviewed_schedule = None
            self.receipt = None
        else:
            self._policy.invalidate()
            # A revoked/changed Calendar context retains at most a frozen uncertain
            # plan for reconciliation; the photograph and editable OCR are discarded.
            if self._owner.status == "disconnected":
                self.ocr_lines = ()
                self.ocr_partial = False
                self.reviewed_schedule = None
        self._notify(
            "schedule_unknown"
            if self._policy.state == "unknown"
            else self._journal_error or "schedule_recovery_required"
            if self._journal_error or self._journal_pending
            else "idle"
        )

    def invalidate_preview(self) -> None:
        """An edit can never authorize a changed row using a previous confirmation."""
        if self.busy:
            self._owner.cancel_schedule()
        self._policy.invalidate()
        self.reviewed_schedule = None
        self.receipt = None
        self._notify("schedule_unknown" if self._policy.state == "unknown" else "idle")

    def recognize_image(self, path: Path) -> None:
        if self.busy or self._owner.busy or self._policy.state in {"consumed", "unknown"}:
            return
        self.invalidate_preview()
        self.ocr_lines = ()
        self.ocr_partial = False
        try:
            selected = path.expanduser().resolve(strict=True)
            if not selected.is_file() or not 0 < selected.stat().st_size <= MAX_IMAGE_BYTES:
                raise ValueError("image_invalid")
        except (OSError, ValueError, RuntimeError):
            self._notify("image_unavailable")
            return
        self._dispatch("recognize_schedule_image", {"image_path": str(selected)})

    def load_reviewed(self, data: bytes) -> None:
        if self.busy or self._policy.state in {"consumed", "unknown"}:
            return
        self.invalidate_preview()
        self.ocr_lines = ()
        self.ocr_partial = False
        try:
            self.reviewed_schedule = parse_schedule_json(data)
        except ScheduleError as error:
            self._notify(error.code)
            return
        self._notify("schedule_loaded")

    def prepare(self, schedule: ReviewedSchedule, target: CalendarSourceRef) -> bool:
        if self.busy or self._owner.busy:
            return False
        try:
            self._read_journal()
            if self._journal_error:
                raise ScheduleError(self._journal_error)
            writable = any(item.source == target and item.writable for item in self._owner.catalog)
            plan = self._policy.prepare(schedule, target, self._owner.generation, writable=writable)
            if self._journal_pending is not None:
                if not self._journal_matches(plan):
                    self._policy.invalidate()
                    raise ScheduleError("schedule_recovery_mismatch")
                self._policy.restore_unknown(plan)
        except (ScheduleError, ValueError) as error:
            self._notify(error.code if isinstance(error, ScheduleError) else "schedule_invalid")
            return False
        self.reviewed_schedule = schedule
        self.receipt = None
        self._notify("schedule_unknown" if self._policy.state == "unknown" else "schedule_prepared")
        return True

    def _body(self, plan: SchedulePlan) -> dict[str, object]:
        return {
            "target": {"source_id": plan.target.account_id, "calendar_id": plan.target.calendar_id},
            "rows": [row.model_dump(mode="json") for row in plan.rows],
            "plan_fingerprint": plan.plan_fingerprint,
        }

    def preflight(self) -> bool:
        plan = self.prepared_plan
        if plan is None or self._policy.state != "prepared" or not self._fresh_binding(plan):
            self._notify("schedule_stale_plan")
            return False
        return self._dispatch("preflight_schedule", self._body(plan), plan)

    def create(self) -> bool:
        if not self.can_create:
            self._notify("schedule_confirmation_required")
            return False
        plan = self.prepared_plan
        assert plan is not None
        try:
            self._policy.consume(
                plan,
                principal=self._owner.principal,
                generation=self._owner.generation,
                target=plan.target,
            )
            self._journal.begin(plan)
            self._read_journal()
            if self._journal_error or not self._journal_matches(plan):
                raise ScheduleError(self._journal_error or "schedule_journal_invalid")
        except ScheduleError as error:
            if self._policy.state == "consumed":
                self._policy.mark_unknown(plan.plan_id)
            self._notify(error.code)
            return False
        return self._dispatch("create_schedule", self._body(plan), plan)

    def reconcile(self) -> bool:
        plan = self.prepared_plan
        if not self.can_reconcile or plan is None:
            self._notify("schedule_reconcile_unavailable")
            return False
        return self._dispatch("reconcile_schedule", self._body(plan), plan)

    def export_ics(self) -> bytes:
        if self.reviewed_schedule is None:
            raise ScheduleError("schedule_review_required")
        return export_schedule_ics(self.reviewed_schedule, generated_at=require_utc(self._clock()))

    def _dispatch(
        self,
        operation: ScheduleOperation,
        body: dict[str, object],
        plan: SchedulePlan | None = None,
    ) -> bool:
        if self.busy or self._owner.busy:
            if operation == "create_schedule" and plan is not None:
                self._policy.mark_unknown(plan.plan_id)
                self._notify("schedule_unknown")
            return False
        self._pending, self._pending_plan = operation, plan
        # Freeze the exact capability payload before emitting any reentrant UI signal.
        frozen_body = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self._pending_body = frozen_body
        try:
            self._requested_at = require_utc(self._clock())
        except (ValueError, TypeError, AttributeError):
            self.transport_failed("clock_invalid")
            return False
        self._notify(operation)
        if self._pending != operation or self._pending_body != frozen_body:
            return False
        started = self._owner.start_schedule(operation, json.loads(frozen_body))
        if not started and self._pending is not None:
            self.transport_failed("provider_unavailable")
        return started

    def authorize_transport(self, operation: str, body: dict[str, object] | None = None) -> bool:
        if self._pending != operation or self._pending_body is None:
            return False
        if body is not None:
            try:
                actual = json.dumps(
                    body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            except (TypeError, ValueError):
                return False
            if actual != self._pending_body:
                return False
        plan = self._pending_plan
        if plan is None:
            return operation == "recognize_schedule_image"
        if operation == "reconcile_schedule":
            return self._target_current(plan) and self._policy.state == "unknown"
        return self._fresh_binding(plan) and self._policy.state == (
            "consumed" if operation == "create_schedule" else "prepared"
        )

    def transport_failed(self, code: str) -> None:
        operation, plan = self._pending, self._pending_plan
        self._pending = None
        self._pending_plan = None
        self._pending_body = None
        self._requested_at = None
        if operation == "create_schedule" and plan is not None:
            self._policy.mark_unknown(plan.plan_id)
        self._notify("schedule_unknown" if self._policy.state == "unknown" else code)

    def accept_transport(
        self, operation: str, request_id: UUID, data: bytes, exit_code: int, normal_exit: bool
    ) -> None:
        if operation != self._pending:
            return
        plan = self._pending_plan
        try:
            json.loads(data, object_pairs_hook=_unique)
            result = _Response.model_validate_json(data)
            now = require_utc(self._clock())
            if (
                not normal_exit
                or exit_code not in {0, 1}
                or (result.status == "ok" and exit_code != 0)
                or result.request_id != request_id
                or result.operation != operation
                or result.observed_at > now + timedelta(seconds=2)
                or self._requested_at is None
                or result.observed_at < self._requested_at - timedelta(seconds=1)
                or not self.authorize_transport(operation)
            ):
                raise ValueError("schedule_response_invalid")
            if result.status == "error":
                if operation != "recognize_schedule_image":
                    self._owner.permission_status = result.access
                self.transport_failed(result.error or "invalid_response")
                if operation != "recognize_schedule_image":
                    self._owner.observe_permission_loss(result.access)
                return
            if operation == "recognize_schedule_image":
                self.ocr_lines = result.ocr_lines or ()
                self.ocr_partial = bool(result.ocr_partial)
            else:
                self._owner.permission_status = result.access
                if (
                    plan is None
                    or result.access != "full_access"
                    or (result.plan_fingerprint != plan.plan_fingerprint)
                ):
                    raise ValueError("schedule_scope_mismatch")
                outcomes = result.schedule_results or ()
                if operation == "preflight_schedule":
                    self._policy.accept_preflight(plan, outcomes)
                elif operation == "create_schedule":
                    self._policy.complete(plan, outcomes)
                else:
                    self._policy.reconcile(plan, outcomes)
                self.receipt = {
                    "operation": operation,
                    "plan_fingerprint": plan.plan_fingerprint,
                    "observed_at": result.observed_at.isoformat(),
                    "local_eventkit_only": True,
                    "remote_icloud_sync": "not_verified",
                    "outcomes": {
                        name: sum(row.outcome == name for row in outcomes)
                        for name in ("absent", "owned_exact", "conflict", "created", "unknown")
                    },
                }
                if self._policy.state in {"committed", "reconciled"}:
                    try:
                        self._journal.clear(plan)
                        self._journal_pending = None
                        self._journal_error = None
                    except ScheduleError as error:
                        # Local acknowledgement remains visible; an uncleared journal
                        # still blocks any later Create, including after restart.
                        self._journal_error = error.code
        except (ValidationError, ScheduleError, ValueError, TypeError, RecursionError):
            self.transport_failed("invalid_response")
            return
        self._pending = None
        self._pending_plan = None
        self._pending_body = None
        self._requested_at = None
        self._notify(
            self._journal_error
            or (
                "ocr_ready" if operation == "recognize_schedule_image" else "schedule_" + self.state
            )
        )
