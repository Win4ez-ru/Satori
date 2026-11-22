"""Strict bounded wire admission and response projection; no private-data diagnostics."""

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .contracts import DeviceContext, GatewayFault

MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 1_048_576
MAX_TEXT_CHARACTERS = 8_000
MAX_REPLY_CHARACTERS = 64_000
MAX_CARD_BODY_CHARACTERS = 24_000

Opaque = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\x00-\x1f\x7f]+$")]
Reply = Annotated[str, Field(max_length=MAX_REPLY_CHARACTERS)]
State = Literal["completed", "pending", "unknown", "failed"]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RequestID(WireModel):
    requestID: str

    @field_validator("requestID")
    @classmethod
    def valid_request_id(cls, value: str) -> str:
        if not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value
        ):
            raise ValueError("invalid_uuid")
        return str(UUID(value))


class ChatRequest(RequestID):
    sessionID: Opaque
    text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_CHARACTERS, repr=False)]

    @field_validator("text")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("invalid_text")
        return value


class PairRequest(WireModel):
    code: Annotated[str, Field(min_length=1, max_length=128, repr=False)]
    deviceName: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$")]


class EmptyRequest(WireModel):
    pass


def _date(value: str) -> str:
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|\+00:00)",
        value,
    ):
        raise ValueError("invalid_date")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_date") from None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("invalid_utc")
    return value


class SourceStatus(WireModel):
    id: Literal["calendar", "gmail", "telegram"]
    connection: Literal["connected", "disconnected", "expired", "unknown"]
    capabilities: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=80)]], Field(max_length=32)
    ]


class StatusData(WireModel):
    ready: bool
    coreState: Literal["ready", "starting", "stopped", "error"]
    profileID: Opaque
    sessionID: Opaque | None
    sourceStatuses: Annotated[list[SourceStatus], Field(max_length=3)]


class Session(WireModel):
    id: Opaque
    startedAt: str
    status: Literal["open", "closed"]
    _started_at = field_validator("startedAt")(_date)


class SessionsData(WireModel):
    activeSessionID: Opaque | None
    sessions: Annotated[list[Session], Field(max_length=100)]
    truncated: bool


class HistoryMessage(WireModel):
    id: Opaque
    role: Literal["user", "assistant"]
    text: Reply = Field(repr=False)
    createdAt: str
    status: State
    _created_at = field_validator("createdAt")(_date)


class HistoryData(WireModel):
    sessionID: Opaque
    messages: Annotated[list[HistoryMessage], Field(max_length=64)]
    truncated: bool


class Card(WireModel):
    type: Literal[
        "calendar",
        "reminder",
        "clarification",
        "connection_required",
        "file",
        "search",
        "places",
        "error",
    ]
    title: Annotated[str, Field(max_length=240)]
    body: Annotated[str, Field(max_length=MAX_CARD_BODY_CHARACTERS, repr=False)]
    source: Annotated[str, Field(max_length=240)] | None
    observedAt: str | None
    expiresAt: str | None
    status: Literal[
        "completed", "partial", "empty", "permission_required", "pending", "unknown", "failed"
    ]

    @field_validator("observedAt", "expiresAt")
    @classmethod
    def valid_optional_date(cls, value: str | None) -> str | None:
        return None if value is None else _date(value)


class ChatData(RequestID):
    state: State
    kind: Literal["conversation", "assistant"]
    sessionID: Opaque
    text: Reply = Field(repr=False)
    interactionID: Opaque | None
    card: Card | None = Field(repr=False)

    @model_validator(mode="after")
    def completed_assistant_requires_card(self) -> Self:
        if self.state == "completed" and self.kind == "assistant" and self.card is None:
            raise ValueError("assistant_card_required")
        return self


class NewSessionData(RequestID):
    state: State
    sessionID: Opaque | None


class Reminder(WireModel):
    id: str
    title: Annotated[str, Field(min_length=1, max_length=8000, repr=False)]
    dueAt: str
    timezone: Annotated[str, Field(min_length=1, max_length=100)]
    state: Literal["scheduled", "due", "completed", "cancelled", "unknown"]
    notificationState: Literal[
        "not_requested", "permission_required", "scheduled", "delivered", "unknown", "failed"
    ]
    _due_at = field_validator("dueAt")(_date)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return RequestID(requestID=value).requestID

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("invalid_zone") from None
        return value


class RemindersData(WireModel):
    items: Annotated[list[Reminder], Field(max_length=100)]
    truncated: bool


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GatewayFault("invalid_request")
        result[key] = value
    return result


def _valid_strings(value: object, depth: int = 0) -> None:
    if depth > 32:
        raise GatewayFault("invalid_request")
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise GatewayFault("invalid_request")
    elif isinstance(value, dict):
        for key, item in value.items():
            _valid_strings(key, depth + 1)
            _valid_strings(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _valid_strings(item, depth + 1)


def request_body(data: bytes, model: type[WireModel]) -> WireModel:
    if len(data) > MAX_REQUEST_BYTES:
        raise GatewayFault("too_large", 413)
    try:
        # A small depth limit prevents pathological bounded-size JSON from exhausting recursion.
        depth = 0
        quoted = escaped = False
        for character in data.decode("utf-8"):
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
            elif character == '"':
                quoted = True
            elif character in "[{":
                depth += 1
                if depth > 32:
                    raise GatewayFault("invalid_request")
            elif character in "]}":
                depth -= 1
        value = json.loads(data, object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise GatewayFault("invalid_request")
        _valid_strings(value)
        return model.model_validate(value)
    except (UnicodeError, ValueError, RecursionError, ValidationError):
        raise GatewayFault("invalid_request") from None


def json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        _valid_strings(dict(value))
        data = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode(
            "ascii"
        )
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise GatewayFault("operation_failed", 502) from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise GatewayFault("too_large", 413)
    return data


def response_data(
    operation: str, value: Mapping[str, object], context: DeviceContext
) -> dict[str, object]:
    value = dict(value)
    models: dict[str, type[WireModel]] = {
        "status": StatusData,
        "sessions": SessionsData,
        "history": HistoryData,
        "chat": ChatData,
        "new_session": NewSessionData,
        "reminders": RemindersData,
    }
    if operation not in models:
        raise GatewayFault("operation_failed", 502)
    collection = {
        "sessions": ("sessions", 100),
        "history": ("messages", 64),
        "reminders": ("items", 100),
    }.get(operation)
    if collection:
        field, maximum = collection
        items = value.get(field)
        if not isinstance(items, list):
            raise GatewayFault("operation_failed", 502)
        if len(items) > 10000:
            raise GatewayFault("too_large", 413)
        if len(items) > maximum:
            value[field] = items[-maximum:] if operation == "history" else items[:maximum]
            value["truncated"] = True
    try:
        result = models[operation].model_validate(value).model_dump()
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise GatewayFault("operation_failed", 502) from None
    if operation == "status" and result["profileID"] != context.scope.profile_id:
        raise GatewayFault("profile_changed", 409)
    if operation == "chat":
        card = result.get("card")
        if (
            isinstance(card, dict)
            and card.get("type") == "calendar"
            and "source.calendar" not in context.capabilities
        ):
            raise GatewayFault("source_grant_required", 403)
    while True:
        try:
            json_bytes({"ok": True, "data": result})
            return result
        except GatewayFault as fault:
            if not collection or fault.code != "too_large":
                raise
            field, _ = collection
            entries = result[field]
            if not isinstance(entries, list) or not entries:
                raise
            result[field] = entries[1:] if operation == "history" else entries[:-1]
            result["truncated"] = True
