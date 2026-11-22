"""Typed assistant requests, distinct from canonical conversation and source evidence."""

from datetime import date as Date
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from satori.application.assistant.contracts import require_timezone, require_utc
from satori.application.assistant.mail_search_contracts import valid_mail_search_query

AssistantIntentKind = Literal[
    "calendar_agenda",
    "calendar_gaps",
    "reminder_create",
    "reminder_list",
    "reminder_cancel",
    "selected_lookup",
    "mailbox_search",
    "source_answer",
    "telegram_reply",
    "clarification",
]


class AssistantIntent(BaseModel):
    """An input proposal; validation alone never grants a capability or executes it."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    request_id: UUID
    kind: AssistantIntentKind
    timezone: str
    day: Date | None = None
    title: str | None = Field(default=None, max_length=500, repr=False)
    due_at: datetime | None = None
    reminder_id: UUID | None = None
    query: str | None = Field(default=None, max_length=8000, repr=False)
    source: Literal["mail", "telegram"] | None = None
    reply_text: str | None = Field(default=None, max_length=4000, repr=False)
    clarification: str | None = Field(default=None, max_length=1000)

    _zone = field_validator("timezone")(require_timezone)

    @field_validator("query")
    @classmethod
    def bounded_query(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None and info.data.get("kind") != "source_answer" and len(value) > 1000:
            raise ValueError("assistant_lookup_query_too_long")
        return value

    @field_validator("due_at")
    @classmethod
    def utc_optional(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def coherent(self) -> "AssistantIntent":
        if self.request_id.int == 0:
            raise ValueError("assistant_request_id_required")
        if self.kind in {"calendar_agenda", "calendar_gaps"} and self.day is None:
            raise ValueError("assistant_calendar_day_required")
        if self.kind == "reminder_create" and (not self.title or self.due_at is None):
            raise ValueError("assistant_reminder_details_required")
        if self.kind == "reminder_cancel" and self.reminder_id is None:
            raise ValueError("assistant_reminder_id_required")
        if self.kind == "selected_lookup" and not self.query:
            raise ValueError("assistant_lookup_query_required")
        if self.kind == "mailbox_search" and (
            self.source != "mail" or self.query is None or not valid_mail_search_query(self.query)
        ):
            raise ValueError("assistant_mail_search_query_required")
        if self.kind == "source_answer" and not self.query:
            raise ValueError("assistant_source_question_required")
        if self.kind == "telegram_reply" and not self.reply_text:
            raise ValueError("assistant_reply_text_required")
        if self.kind != "telegram_reply" and self.reply_text is not None:
            raise ValueError("assistant_reply_text_unexpected")
        if self.kind == "clarification" and not self.clarification:
            raise ValueError("assistant_clarification_required")
        return self
