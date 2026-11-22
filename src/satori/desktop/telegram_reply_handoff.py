"""Transient user text; this record contains no recipient or Telegram read/send grant."""

from datetime import datetime, timedelta
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from satori.application.assistant.contracts import LocalPrincipal, require_utc
from satori.application.assistant.telegram_reply_contracts import (
    ReplyDraft,
    ReplyModel,
    finite_monotonic,
)


class TelegramReplyHandoff(ReplyModel):
    request_id: UUID
    principal: LocalPrincipal
    conversation_id: UUID
    epoch: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=4000, repr=False)
    created_at: datetime
    expires_at: datetime
    started_monotonic: float
    expires_monotonic: float

    _utc = field_validator("created_at", "expires_at")(require_utc)
    _finite = field_validator("started_monotonic", "expires_monotonic")(finite_monotonic)

    @field_validator("text")
    @classmethod
    def plain_text(cls, value: str) -> str:
        return ReplyDraft.plain_text(value)

    @model_validator(mode="after")
    def bounded(self) -> "TelegramReplyHandoff":
        wall = (self.expires_at - self.created_at).total_seconds()
        elapsed = self.expires_monotonic - self.started_monotonic
        if not 0 < wall <= timedelta(minutes=2).total_seconds() or wall != elapsed:
            raise ValueError("reply_handoff_deadline_invalid")
        return self
