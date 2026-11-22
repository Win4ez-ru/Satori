"""Closed reply-v1 presentation frames; native identifiers never reach this wire."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.telegram_reply_contracts import ReplyDraft
from satori.desktop.telegram_port import BASE, require
from satori.desktop.telegram_reply_worker import PROTOCOL_SHA256


class TelegramReplyQualification(BaseModel):
    """Root-only opt-in to versioned code, never a permission to send."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    principal: LocalPrincipal = Field(repr=False)
    capability: Literal["reviewed_reply_v1"] = "reviewed_reply_v1"
    protocol_sha256: str = PROTOCOL_SHA256


def reply_qualification_for(principal: LocalPrincipal) -> TelegramReplyQualification:
    return TelegramReplyQualification(principal=principal)


@dataclass(frozen=True)
class TelegramReplyView:
    state: str = "unavailable"
    action_id: UUID | None = None
    message_handle: str | None = field(default=None, repr=False)
    payload_digest: str | None = field(default=None, repr=False)
    approval_id: UUID | None = None
    text: str = field(default="", repr=False)
    chat_title: str = field(default="", repr=False)
    expires_at: datetime | None = None


def parse_reply_frame(event: dict[str, object], *, sequence: int) -> TelegramReplyView:
    """No relaxed old-schema parsing: reply frames require their separate capability."""
    require(event.get("v") == 2 and type(event.get("v")) is int)
    require(event.get("capability") == "reviewed_reply_v1")
    require(type(event.get("event_seq")) is int and event["event_seq"] == sequence + 1)
    require(event.get("type") == "reply" and type(event.get("request_id")) is str)
    status = event.get("status")
    require(type(status) is str)
    assert isinstance(status, str)
    common = BASE | {"capability", "status"}
    extras = {
        "preview": {
            "action_id",
            "page_id",
            "message_handle",
            "payload_digest",
            "text",
            "chat_title",
            "expires_at",
        },
        "approved": {"action_id", "approval_id", "payload_digest", "expires_at"},
        "sent": {"action_id", "code"},
        "failed": {"action_id", "code"},
        "unknown": {"action_id", "code"},
        "unavailable": set(),
        "discarded": set(),
    }
    require(status in extras)
    require(
        set(event) == common | extras[status]
        or (status == "unknown" and set(event) in (common, common | {"action_id"}))
    )
    if (
        status in {"unavailable", "discarded", "unknown"}
        and "payload_digest" not in event
        and "code" not in event
    ):
        return TelegramReplyView(state=status)
    action = event.get("action_id")
    require(type(action) is str)
    assert isinstance(action, str)
    action_id = UUID(action)
    require(action_id.int != 0)
    if status in {"sent", "failed", "unknown"}:
        require(
            type(event["code"]) is str
            and event["code"]
            in {"native_sent", "native_failed", "dispatch_claimed", "native_pending", "unknown"}
        )
        require(
            (status != "sent" or event["code"] == "native_sent")
            and (status != "failed" or event["code"] == "native_failed")
        )
        require(
            status != "unknown"
            or event["code"] in {"dispatch_claimed", "native_pending", "unknown"}
        )
        return TelegramReplyView(state=status, action_id=action_id)
    payload = event.get("payload_digest")
    require(
        type(payload) is str
        and len(payload) == 64
        and all(c in "0123456789abcdef" for c in payload)
    )
    assert isinstance(payload, str)
    expiry = event.get("expires_at")
    require(type(expiry) is str)
    assert isinstance(expiry, str)
    expires_at = datetime.fromisoformat(expiry)
    offset = expires_at.utcoffset()
    require(expires_at.tzinfo is not None and offset is not None and offset.total_seconds() == 0)
    if status == "approved":
        approval = event.get("approval_id")
        require(type(approval) is str)
        assert isinstance(approval, str)
        return TelegramReplyView(
            state=status,
            action_id=action_id,
            payload_digest=payload,
            approval_id=UUID(approval),
            expires_at=expires_at,
        )
    handle, text, title, page = (
        event.get(k) for k in ("message_handle", "text", "chat_title", "page_id")
    )
    require(all(type(v) is str for v in (handle, text, title, page)))
    assert isinstance(handle, str)
    assert isinstance(text, str)
    assert isinstance(title, str)
    assert isinstance(page, str)
    require(UUID(hex=handle).int != 0 and UUID(hex=page).int != 0)
    require(1 <= len(text) <= 4000)
    ReplyDraft.plain_text(text)
    require(len(title) <= 1024 and all(ord(c) >= 32 for c in title))
    return TelegramReplyView(
        state=status,
        action_id=action_id,
        message_handle=handle,
        payload_digest=payload,
        text=text,
        chat_title=title,
        expires_at=expires_at,
    )
