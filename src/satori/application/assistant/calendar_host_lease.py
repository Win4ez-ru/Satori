"""Immutable, content-free projection of the sole desktop Calendar read grant."""

import math
from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionDTO,
    CalendarActionScope,
)
from satori.application.assistant.contracts import (
    CalendarDescriptor,
    CalendarGrant,
    require_utc,
)

MAX_HOST_LEASE_LIFETIME = timedelta(seconds=120)


class CalendarHostLease(CalendarActionDTO):
    """An expiring mirror, never another grant owner or a write approval.

    The GUI alone creates/removes the corresponding file. Worker admission must
    compare that exact immutable file and process lifetime at every boundary.
    Calendar names are catalog metadata; event bodies and requests are excluded.
    """

    schema_version: Literal[1] = 1
    full_access: Literal[True]
    lease_id: UUID
    request_id: UUID
    scope: CalendarActionScope = Field(repr=False)
    read_grant: CalendarGrant = Field(repr=False)
    controller_generation: int = Field(ge=0)
    calendars: tuple[CalendarDescriptor, ...] = Field(min_length=1, max_length=20, repr=False)
    catalog_revision: UUID
    catalog_observed_at: datetime
    issued_at: datetime
    expires_at: datetime
    monotonic_deadline: float
    ui_pid: int = Field(gt=1)

    _utc = field_validator("catalog_observed_at", "issued_at", "expires_at")(require_utc)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> Literal[1]:
        if type(value) is not int or value != 1:
            raise ValueError("calendar_host_lease_version_invalid")
        return 1

    @field_validator("full_access", mode="before")
    @classmethod
    def exact_full_access(cls, value: object) -> Literal[True]:
        if value is not True:
            raise ValueError("calendar_host_lease_full_access_required")
        return True

    @field_validator("lease_id", "request_id", "catalog_revision")
    @classmethod
    def nonzero(cls, value: UUID) -> UUID:
        if not value.int:
            raise ValueError("calendar_host_lease_id_invalid")
        return value

    @model_validator(mode="after")
    def coherent(self) -> Self:
        grant, scope = self.read_grant, self.scope
        if (
            scope.provider != "macos_eventkit"
            or scope.profile_id != grant.principal.profile_id
            or not grant.issued_at <= self.issued_at < self.expires_at <= grant.expires_at
            or self.expires_at - self.issued_at > MAX_HOST_LEASE_LIFETIME
            or self.catalog_observed_at > self.issued_at + timedelta(seconds=5)
            or not math.isfinite(self.monotonic_deadline)
            or self.monotonic_deadline <= 0
            or tuple(item.source for item in self.calendars) != grant.resources
            or any(item.source.account_id != scope.account_id for item in self.calendars)
            or any(item.source.provider != "macos_eventkit" for item in self.calendars)
        ):
            raise ValueError("calendar_host_lease_scope_invalid")
        target = next(
            (item for item in self.calendars if item.source.calendar_id == scope.calendar_id),
            None,
        )
        if target is None or not (
            target.writable is True
            and target.allows_modification is True
            and target.supports_events is True
            and target.source_kind not in {"subscribed", "birthdays", "unknown"}
        ):
            raise ValueError("calendar_host_lease_target_unwritable")
        return self
