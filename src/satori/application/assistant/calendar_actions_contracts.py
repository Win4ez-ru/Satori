"""Create-only calendar effects, separate from source reads and canonical memory."""

import hashlib
import json
import unicodedata
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import require_identifier, require_timezone, require_utc

MAX_CALENDAR_EVENT_DURATION = timedelta(days=7)
MAX_CALENDAR_EVENT_HORIZON = timedelta(days=1830)
MAX_CALENDAR_ACTION_CATALOG_AGE = timedelta(minutes=5)
MAX_CALENDAR_PROPOSAL_LIFETIME = timedelta(minutes=5)
MAX_CALENDAR_APPROVAL_LIFETIME = timedelta(minutes=2)
MAX_CALENDAR_PROVIDER_LIFETIME = timedelta(seconds=30)
CalendarActionProviderKind = Literal["macos_eventkit", "caldav", "synthetic_fixture"]
CalendarActionState = Literal[
    "proposed",
    "approved",
    "dispatch_claimed",
    "created",
    "rejected",
    "unknown",
    "reconciled_present",
    "reconciled_absent",
    "conflict",
]
CalendarActionCode = Literal[
    "calendar_action_invalid",
    "calendar_action_scope_mismatch",
    "calendar_action_catalog_stale",
    "calendar_action_permission_required",
    "calendar_action_not_writable",
    "calendar_action_time_invalid",
    "calendar_action_request_conflict",
    "calendar_action_review_required",
    "calendar_action_approval_expired",
    "calendar_action_stale",
    "calendar_action_revoked",
    "calendar_action_real_writes_disabled",
    "calendar_action_provider_unavailable",
    "calendar_action_provider_failed",
    "calendar_action_receipt_invalid",
    "calendar_action_unknown_no_replay",
    "calendar_action_reconcile_required",
    "calendar_action_clock_invalid",
    "calendar_action_store_invalid",
    "calendar_action_store_unavailable",
    "calendar_action_store_in_use",
    "calendar_action_store_replaced",
    "calendar_action_store_full",
    "calendar_action_concurrent_update",
]


class CalendarActionError(ValueError):
    """Closed diagnostics; no title, provider identifier or exception body."""

    def __init__(self, code: CalendarActionCode) -> None:
        self.code = code
        super().__init__(code)


class CalendarActionDTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


def _nonzero(value: UUID) -> UUID:
    if not value.int:
        raise ValueError("calendar_action_nonzero_id_required")
    return value


def _title(value: str) -> str:
    if not value.strip() or value != value.strip() or len(value) > 500:
        raise ValueError("calendar_action_title_invalid")
    if any(unicodedata.category(char) in {"Cc", "Cs", "Cf"} for char in value):
        raise ValueError("calendar_action_title_invalid")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("calendar_action_title_must_be_normalized_before_review")
    return value


def _second_utc(value: datetime) -> datetime:
    result = require_utc(value)
    if result.microsecond:
        raise ValueError("calendar_action_second_precision_required")
    return result


def calendar_action_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class CalendarActionScope(CalendarActionDTO):
    """Exact owner and selected resource; never a wildcard or read-derived write grant."""

    identity_id: UUID
    profile_id: UUID
    session_id: UUID
    generation: UUID
    provider: CalendarActionProviderKind
    account_id: str = Field(min_length=1, max_length=512, repr=False)
    calendar_id: str = Field(min_length=1, max_length=512, repr=False)
    timezone: str

    _ids = field_validator("identity_id", "profile_id", "session_id", "generation")(_nonzero)
    _resources = field_validator("account_id", "calendar_id")(require_identifier)
    _zone = field_validator("timezone")(require_timezone)

    def same_resource(self, other: "CalendarActionScope") -> bool:
        return (
            self.identity_id,
            self.profile_id,
            self.provider,
            self.account_id,
            self.calendar_id,
            self.timezone,
        ) == (
            other.identity_id,
            other.profile_id,
            other.provider,
            other.account_id,
            other.calendar_id,
            other.timezone,
        )


class CalendarActionCatalog(CalendarActionDTO):
    """A bounded external observation; not effect authority."""

    scope: CalendarActionScope
    revision: UUID
    observed_at: datetime
    full_access: bool
    writable: bool
    supports_events: bool
    subscribed: bool = False
    calendar_title: str = Field(min_length=1, max_length=512, repr=False)
    account_title: str = Field(min_length=1, max_length=512, repr=False)

    _revision = field_validator("revision")(_nonzero)
    _utc = field_validator("observed_at")(require_utc)
    _titles = field_validator("calendar_title", "account_title")(_title)


class CalendarCreateInput(CalendarActionDTO):
    request_id: UUID
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    title: str = Field(min_length=1, max_length=500, repr=False)
    starts_at: datetime
    ends_at: datetime
    timezone: str

    _request = field_validator("request_id")(_nonzero)
    _text = field_validator("title")(_title)
    _instants = field_validator("starts_at", "ends_at")(_second_utc)
    _zone = field_validator("timezone")(require_timezone)

    @model_validator(mode="after")
    def interval(self) -> Self:
        if not timedelta(0) < self.ends_at - self.starts_at <= MAX_CALENDAR_EVENT_DURATION:
            raise ValueError("calendar_action_duration_invalid")
        return self


class CalendarCreateProposal(CalendarActionDTO):
    proposal_id: UUID
    owner_epoch: UUID
    command: CalendarCreateInput = Field(repr=False)
    scope: CalendarActionScope = Field(repr=False)
    catalog_revision: UUID
    calendar_title: str = Field(repr=False)
    account_title: str = Field(repr=False)
    proposed_at: datetime
    expires_at: datetime
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    _ids = field_validator("proposal_id", "owner_epoch", "catalog_revision")(_nonzero)
    _utc = field_validator("proposed_at", "expires_at")(require_utc)
    _titles = field_validator("calendar_title", "account_title")(_title)

    def payload_material(self) -> dict[str, object]:
        return {
            "schema": "satori.calendar-create.v1",
            "command": self.command.model_dump(mode="json"),
            "resource": {
                key: value
                for key, value in self.scope.model_dump(mode="json").items()
                if key not in {"session_id", "generation"}
            },
        }

    def review_material(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"review_digest"})

    @model_validator(mode="after")
    def binding(self) -> Self:
        if self.command.timezone != self.scope.timezone:
            raise ValueError("calendar_action_timezone_mismatch")
        if not timedelta(0) < self.expires_at - self.proposed_at <= MAX_CALENDAR_PROPOSAL_LIFETIME:
            raise ValueError("calendar_action_proposal_lifetime_invalid")
        if calendar_action_digest(self.payload_material()) != self.payload_digest:
            raise ValueError("calendar_action_payload_mismatch")
        if calendar_action_digest(self.review_material()) != self.review_digest:
            raise ValueError("calendar_action_review_mismatch")
        return self


class CalendarCreateApproval(CalendarActionDTO):
    approval_id: UUID
    proposal_id: UUID
    request_id: UUID
    owner_epoch: UUID
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: CalendarActionScope = Field(repr=False)
    approved_at: datetime
    expires_at: datetime
    authority: Literal["explicit_reviewed_create"] = "explicit_reviewed_create"

    _ids = field_validator("approval_id", "proposal_id", "request_id", "owner_epoch")(_nonzero)
    _utc = field_validator("approved_at", "expires_at")(require_utc)

    @model_validator(mode="after")
    def lifetime(self) -> Self:
        if not timedelta(0) < self.expires_at - self.approved_at <= MAX_CALENDAR_APPROVAL_LIFETIME:
            raise ValueError("calendar_action_approval_lifetime_invalid")
        return self


class CalendarCreateAttempt(CalendarActionDTO):
    attempt_id: UUID
    proposal: CalendarCreateProposal = Field(repr=False)
    approval: CalendarCreateApproval = Field(repr=False)
    idempotency_token: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    claimed_at: datetime
    expires_at: datetime

    _id = field_validator("attempt_id")(_nonzero)
    _utc = field_validator("claimed_at", "expires_at")(require_utc)

    @model_validator(mode="after")
    def binding(self) -> Self:
        p, a = self.proposal, self.approval
        if (a.proposal_id, a.request_id, a.owner_epoch, a.review_digest, a.scope) != (
            p.proposal_id,
            p.command.request_id,
            p.owner_epoch,
            p.review_digest,
            p.scope,
        ) or not a.approved_at <= self.claimed_at < self.expires_at <= a.expires_at:
            raise ValueError("calendar_action_attempt_binding_invalid")
        if self.expires_at - self.claimed_at > MAX_CALENDAR_PROVIDER_LIFETIME:
            raise ValueError("calendar_action_attempt_lifetime_invalid")
        return self


class CalendarProviderReceipt(CalendarActionDTO):
    attempt_id: UUID
    idempotency_token: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: CalendarActionProviderKind
    status: Literal["created", "rejected", "unknown"]
    observed_at: datetime
    event_id: str | None = Field(default=None, max_length=1024, repr=False)
    exact_fields_verified: bool = False
    fresh_store_verified: bool = False
    remote_sync_verified: Literal[False] = False
    reason: CalendarActionCode | None = None

    _id = field_validator("attempt_id")(_nonzero)
    _utc = field_validator("observed_at")(require_utc)

    @model_validator(mode="after")
    def positive_receipt(self) -> Self:
        if self.status == "created":
            if not self.event_id or not self.exact_fields_verified or not self.fresh_store_verified:
                raise ValueError("calendar_action_verified_local_receipt_required")
            require_identifier(self.event_id)
            if self.reason is not None:
                raise ValueError("calendar_action_positive_reason_invalid")
        elif self.event_id is not None or self.exact_fields_verified or self.fresh_store_verified:
            raise ValueError("calendar_action_unconfirmed_receipt_has_no_event")
        return self


class CalendarLookupReceipt(CalendarActionDTO):
    attempt_id: UUID
    idempotency_token: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: CalendarActionProviderKind
    status: Literal["owned_exact", "absent", "conflict", "unknown"]
    observed_at: datetime
    event_id: str | None = Field(default=None, max_length=1024, repr=False)
    exact_fields_verified: bool = False
    fresh_store_verified: bool = False
    coverage: Literal["approved_calendar_and_bounded_interval"] = (
        "approved_calendar_and_bounded_interval"
    )

    _id = field_validator("attempt_id")(_nonzero)
    _utc = field_validator("observed_at")(require_utc)

    @model_validator(mode="after")
    def exact_readback(self) -> Self:
        if self.status == "owned_exact":
            if not self.event_id or not self.exact_fields_verified or not self.fresh_store_verified:
                raise ValueError("calendar_action_verified_lookup_required")
            require_identifier(self.event_id)
        elif self.event_id is not None or self.exact_fields_verified:
            raise ValueError("calendar_action_unconfirmed_lookup_has_no_event")
        if self.status in {"absent", "conflict"} and not self.fresh_store_verified:
            raise ValueError("calendar_action_fresh_lookup_required")
        return self


class CalendarActionRecord(CalendarActionDTO):
    schema_version: Literal[1] = 1
    revision: int = Field(ge=1, le=10000)
    proposal: CalendarCreateProposal = Field(repr=False)
    state: CalendarActionState
    updated_at: datetime
    approval: CalendarCreateApproval | None = Field(default=None, repr=False)
    attempt: CalendarCreateAttempt | None = Field(default=None, repr=False)
    receipt: CalendarProviderReceipt | None = Field(default=None, repr=False)
    lookup: CalendarLookupReceipt | None = Field(default=None, repr=False)
    reason: CalendarActionCode | None = None

    _utc = field_validator("updated_at")(require_utc)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("calendar_action_schema_invalid")
        return value

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.updated_at < self.proposal.proposed_at:
            raise ValueError("calendar_action_record_clock_invalid")
        if self.state != "proposed" and self.approval is None:
            raise ValueError("calendar_action_approval_missing")
        if self.state not in {"proposed", "approved"} and self.attempt is None:
            raise ValueError("calendar_action_attempt_missing")
        if self.state == "proposed" and any(
            value is not None for value in (self.approval, self.attempt, self.receipt, self.lookup)
        ):
            raise ValueError("calendar_action_unapproved_record_invalid")
        if self.state == "approved" and any(
            value is not None for value in (self.attempt, self.receipt, self.lookup)
        ):
            raise ValueError("calendar_action_undispatched_record_invalid")
        if self.approval is not None and (
            self.approval.proposal_id,
            self.approval.request_id,
            self.approval.owner_epoch,
            self.approval.review_digest,
            self.approval.scope,
        ) != (
            self.proposal.proposal_id,
            self.proposal.command.request_id,
            self.proposal.owner_epoch,
            self.proposal.review_digest,
            self.proposal.scope,
        ):
            raise ValueError("calendar_action_approval_binding_invalid")
        if self.attempt is not None and (
            self.attempt.proposal != self.proposal or self.attempt.approval != self.approval
        ):
            raise ValueError("calendar_action_record_binding_invalid")
        for receipt in (self.receipt, self.lookup):
            if receipt is not None and (
                self.attempt is None
                or receipt.attempt_id != self.attempt.attempt_id
                or receipt.idempotency_token != self.attempt.idempotency_token
                or receipt.payload_digest != self.proposal.payload_digest
                or receipt.provider != self.proposal.scope.provider
            ):
                raise ValueError("calendar_action_receipt_binding_invalid")
        if self.state == "created" and (self.receipt is None or self.receipt.status != "created"):
            raise ValueError("calendar_action_created_receipt_missing")
        if self.state.startswith("reconciled_") or self.state == "conflict":
            expected = {
                "reconciled_present": "owned_exact",
                "reconciled_absent": "absent",
                "conflict": "conflict",
            }[self.state]
            if self.lookup is None or self.lookup.status != expected:
                raise ValueError("calendar_action_lookup_receipt_missing")
        return self


class CalendarActionStore(Protocol):
    @property
    def last_observed_at(self) -> datetime | None: ...
    def get(self, request_id: UUID) -> CalendarActionRecord | None: ...
    def insert(self, record: CalendarActionRecord) -> None: ...
    def replace(self, record: CalendarActionRecord, *, expected_revision: int) -> None: ...
    def recover_inflight(self, now: datetime) -> None: ...
    def close(self) -> None: ...


class CalendarActionProvider(Protocol):
    """No general executor: exact create and bounded token lookup only."""

    @property
    def provider_kind(self) -> CalendarActionProviderKind: ...
    @property
    def external_effects(self) -> bool: ...
    async def create(
        self, attempt: CalendarCreateAttempt, *, before_effect: Callable[[], None]
    ) -> CalendarProviderReceipt: ...
    async def lookup(
        self,
        attempt: CalendarCreateAttempt,
        scope: CalendarActionScope,
        *,
        before_read: Callable[[], None],
    ) -> CalendarLookupReceipt: ...
