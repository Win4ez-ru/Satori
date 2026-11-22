"""Query-derived exact related-body capability; never a human selection proof."""

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from satori.application.assistant.contracts import require_utc
from satori.application.assistant.mail_contracts import MailClock, MailDTO, mail_identifier
from satori.application.assistant.mail_search_contracts import MailSearchRequest


class MailQueryReadRequest(MailDTO):
    task_request_id: UUID
    search: MailSearchRequest = Field(repr=False)
    catalog_id: UUID
    message_ids: tuple[str, ...] = Field(min_length=1, max_length=3, repr=False)
    issued: MailClock
    expires_at: datetime
    monotonic_deadline: float = Field(ge=0, allow_inf_nan=False)

    _utc = field_validator("expires_at")(require_utc)

    @field_validator("message_ids")
    @classmethod
    def exact_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("mail_task_read_invalid")
        for item in value:
            mail_identifier(item)
        return value

    @model_validator(mode="after")
    def bound_to_search(self) -> "MailQueryReadRequest":
        if (
            self.task_request_id != self.search.request_id
            or not self.search.created.wall <= self.issued.wall < self.expires_at
            or self.expires_at > self.search.expires_at
            or not self.search.created.monotonic <= self.issued.monotonic < self.monotonic_deadline
            or self.monotonic_deadline > self.search.monotonic_deadline
        ):
            raise ValueError("mail_task_read_invalid")
        return self
