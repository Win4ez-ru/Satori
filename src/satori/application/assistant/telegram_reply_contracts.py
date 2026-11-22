"""One reviewed Telegram text reply; these are not canonical conversation evidence."""

import hashlib
import math
import unicodedata
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import LocalPrincipal, require_utc


class ReplyDenied(ValueError):
    """Closed diagnostic code only, never provider/source exception text."""


class ReplyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    @field_validator("*", mode="after")
    @classmethod
    def nonzero_uuid(cls, value: object) -> object:
        if isinstance(value, UUID) and value.int == 0:
            raise ValueError("reply_identifier_invalid")
        return value


def finite_monotonic(value: float) -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError("reply_clock_invalid")
    return value


class ReplyClock(ReplyModel):
    wall: datetime
    monotonic: float

    _utc = field_validator("wall")(require_utc)

    _finite = field_validator("monotonic")(finite_monotonic)


class SelectedReplyScope(ReplyModel):
    """Trusted receive-owner proof of one current personal chat/page, not a send grant."""

    principal: LocalPrincipal
    auth_session_id: UUID
    account_epoch: UUID
    generation: int = Field(ge=1)
    selection_id: UUID
    chat_handle: UUID
    page_id: UUID
    expires_at: datetime
    monotonic_deadline: float

    _utc = field_validator("expires_at")(require_utc)
    _finite = field_validator("monotonic_deadline")(finite_monotonic)


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scope_digest(scope: SelectedReplyScope) -> str:
    return text_digest(scope.model_dump_json())


class ReplyDraft(ReplyModel):
    request_id: UUID
    scope: SelectedReplyScope
    reply_to_handle: UUID
    text: str = Field(min_length=1, max_length=4000, repr=False)
    created: ReplyClock
    expires_at: datetime
    monotonic_deadline: float

    _utc = field_validator("expires_at")(require_utc)
    _finite = field_validator("monotonic_deadline")(finite_monotonic)

    @field_validator("text")
    @classmethod
    def plain_text(cls, value: str) -> str:
        if (
            not value.strip()
            or len(value.encode("utf-8")) > 16 * 1024
            or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} and c not in "\n\t" for c in value)
        ):
            raise ValueError("reply_text_invalid")
        return value

    @property
    def payload_digest(self) -> str:
        return text_digest(
            scope_digest(self.scope) + "\n" + str(self.reply_to_handle) + "\n" + self.text
        )


class ReplyApproval(ReplyModel):
    """Retained one-use send grant minted only by the trusted approval action."""

    approval_id: UUID
    request_id: UUID
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_at: datetime
    expires_at: datetime
    monotonic_deadline: float

    _utc = field_validator("approved_at", "expires_at")(require_utc)
    _finite = field_validator("monotonic_deadline")(finite_monotonic)


ReplyState = Literal["unknown", "sent", "failed"]


class ReplyReceipt(ReplyModel):
    request_id: UUID
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: ReplyState
    sending_id: int = Field(ge=1, lt=1 << 31)
    message_id: int | None = None
    code: Literal["dispatch_claimed", "native_pending", "native_sent", "native_failed", "unknown"]

    @model_validator(mode="after")
    def coherent(self) -> "ReplyReceipt":
        if self.message_id is not None and not 0 < abs(self.message_id) < 1 << 53:
            raise ValueError("reply_message_id_invalid")
        if self.state == "sent" and (
            self.code != "native_sent" or self.message_id is None or self.message_id <= 0
        ):
            raise ValueError("reply_sent_receipt_invalid")
        if self.state == "failed" and self.code != "native_failed":
            raise ValueError("reply_failed_receipt_invalid")
        if self.state == "unknown" and self.code not in {
            "dispatch_claimed",
            "native_pending",
            "unknown",
        }:
            raise ValueError("reply_unknown_receipt_invalid")
        if self.code == "native_pending" and self.message_id is None:
            raise ValueError("reply_pending_message_required")
        return self


class ReplyPort(Protocol):
    """One actual dispatch or a read-only lookup, guarded in the owned receive lane."""

    def validate_recipient(self, draft: ReplyDraft) -> None:
        """Pure trusted handle-map check; no lookup, account connection or RPC."""
        ...

    def send_once(
        self, draft: ReplyDraft, sending_id: int, before_send: "DispatchGuard"
    ) -> ReplyReceipt: ...

    def reconcile(
        self, draft: ReplyDraft, prior: ReplyReceipt, before_read: "DispatchGuard"
    ) -> ReplyReceipt: ...


class DispatchGuard(Protocol):
    def __call__(self) -> None: ...


class ReplyJournal(Protocol):
    def get(self, request_id: UUID) -> ReplyReceipt | None: ...

    def claim(
        self, draft: ReplyDraft, approval: ReplyApproval, sending_id: int, at: datetime
    ) -> ReplyReceipt: ...

    def record(self, receipt: ReplyReceipt, at: datetime) -> ReplyReceipt: ...
