"""Transient desktop Telegram port contracts, never conversation or learning input.

The composition root owns runtime admission and protected authentication. These
records do not parse TDLib, start a worker, persist content or prove live access.
"""

import math
import unicodedata
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import LocalPrincipal

Operation = Literal["connect", "catalog", "select", "read", "conceal", "viewport", "close"]
MAX_MESSAGES = 50
MAX_ACCEPTED_PAGES = 512
MAX_MESSAGE_BYTES = 16 * 1024
MAX_PAGE_BYTES = 256 * 1024
MAX_CATALOG_AGE = 60.0
MAX_SESSION_AGE = 900.0
MAX_PAGE_AGE = 300.0
REQUEST_TIMEOUT = 30.0
AUTH_TIMEOUT = 300.0


class _Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    @field_validator("*", mode="after")
    @classmethod
    def nonzero_uuid(cls, value: object) -> object:
        if isinstance(value, UUID) and value.int == 0:
            raise ValueError("telegram_identifier_invalid")
        return value


def _plain(value: str) -> str:
    if any(unicodedata.category(char) in {"Cs", "Cc"} and char not in "\n\t" for char in value):
        raise ValueError("telegram_text_invalid")
    return value


def _deadline(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("telegram_deadline_invalid")
    return value


class TelegramRuntimeAdmission(_Record):
    """Trusted composition input, NOT a worker DTO or a UI-configurable flag.

    The root must verify these receipts and packaged bytes before injecting this
    record together with an owned dispatcher. Default composition provides neither.
    """

    principal: LocalPrincipal = Field(repr=False)
    runtime_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    selected_read_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)


class TelegramSession(_Record):
    """Issued by the trusted port AFTER verified getMe and human account confirmation."""

    session_id: UUID = Field(repr=False)
    account_epoch: UUID = Field(repr=False)
    account_label: str = Field(min_length=1, max_length=256, repr=False)
    account_confirmed: Literal[True]
    expires_mono: float

    _text = field_validator("account_label")(_plain)
    _time = field_validator("expires_mono")(_deadline)

    @field_validator("account_confirmed", mode="before")
    @classmethod
    def exact_confirmation(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("telegram_account_unconfirmed")
        return value


class TelegramChat(_Record):
    handle: UUID = Field(repr=False)
    title: str = Field(min_length=1, max_length=512, repr=False)
    eligible: bool

    _text = field_validator("title")(_plain)


class TelegramCatalog(_Record):
    catalog_id: UUID = Field(repr=False)
    chats: tuple[TelegramChat, ...] = Field(max_length=100, repr=False)
    expires_mono: float

    _time = field_validator("expires_mono")(_deadline)

    @model_validator(mode="after")
    def unique_handles(self) -> "TelegramCatalog":
        if len({chat.handle for chat in self.chats}) != len(self.chats):
            raise ValueError("telegram_catalog_invalid")
        return self


class TelegramSelection(_Record):
    lease_id: UUID = Field(repr=False)
    catalog_id: UUID = Field(repr=False)
    chat_handle: UUID = Field(repr=False)
    expires_mono: float

    _time = field_validator("expires_mono")(_deadline)


class TelegramMessage(_Record):
    handle: UUID = Field(repr=False)
    speaker: str = Field(max_length=256, repr=False)
    body: str = Field(max_length=MAX_MESSAGE_BYTES, repr=False)

    _text = field_validator("speaker", "body")(_plain)

    @field_validator("body")
    @classmethod
    def bounded_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("telegram_message_too_large")
        return value


class TelegramPage(_Record):
    page_id: UUID = Field(repr=False)
    lease_id: UUID = Field(repr=False)
    chat_handle: UUID = Field(repr=False)
    messages: tuple[TelegramMessage, ...] = Field(max_length=MAX_MESSAGES, repr=False)
    expires_mono: float

    _time = field_validator("expires_mono")(_deadline)

    @model_validator(mode="after")
    def bounded_unique_messages(self) -> "TelegramPage":
        if len({message.handle for message in self.messages}) != len(self.messages):
            raise ValueError("telegram_page_invalid")
        if sum(len(message.body.encode("utf-8")) for message in self.messages) > MAX_PAGE_BYTES:
            raise ValueError("telegram_page_too_large")
        return self


class TelegramRow(_Record):
    handle: UUID = Field(repr=False)
    first: int = Field(ge=0, le=2_000_000)
    after: int = Field(ge=1, le=2_000_000)

    @model_validator(mode="after")
    def full_span(self) -> "TelegramRow":
        if not 0 < self.after - self.first <= 1_000_000:
            raise ValueError("telegram_row_invalid")
        return self


class TelegramViewport(_Record):
    page_id: UUID = Field(repr=False)
    foreground: bool
    first: int = Field(ge=0, le=2_000_000)
    after: int = Field(ge=1, le=2_000_000)
    rows: tuple[TelegramRow, ...] = Field(max_length=MAX_MESSAGES, repr=False)

    @model_validator(mode="after")
    def bounded_frame(self) -> "TelegramViewport":
        if not 0 < self.after - self.first <= 10_000:
            raise ValueError("telegram_viewport_invalid")
        if len({row.handle for row in self.rows}) != len(self.rows):
            raise ValueError("telegram_viewport_invalid")
        if not self.foreground and self.rows:
            raise ValueError("telegram_viewport_invalid")
        if any(min(row.after, self.after) - max(row.first, self.first) < 1 for row in self.rows):
            raise ValueError("telegram_viewport_invalid")
        return self


@dataclass(frozen=True, eq=False)
class TelegramPendingToken:
    """One-use controller-minted capability; a reconstructed equal DTO is insufficient."""

    principal: LocalPrincipal = field(repr=False)
    generation: int
    request_id: UUID = field(repr=False)
    operation: Operation
    session_id: UUID | None = field(default=None, repr=False)
    account_epoch: UUID | None = field(default=None, repr=False)
    catalog_id: UUID | None = field(default=None, repr=False)
    lease_id: UUID | None = field(default=None, repr=False)
    chat_handle: UUID | None = field(default=None, repr=False)
    page_id: UUID | None = field(default=None, repr=False)


@dataclass(frozen=True)
class TelegramCommand:
    """Host intent for the future trusted port; NOT a direct r7 wire command.

    Auth secrets, source bodies, remote writes, Core, model and TTS have no fields.
    Viewport/visibility intents never grant access or renew an in-flight read.
    """

    token: TelegramPendingToken
    consent: bool = False
    viewport: TelegramViewport | None = field(default=None, repr=False)
