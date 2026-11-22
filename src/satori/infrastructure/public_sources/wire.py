"""Single-request private process envelopes, separate from canonical conversation wire."""

import unicodedata
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.infrastructure.public_sources.public_web import validate_public_url

MAX_INPUT_BYTES = 16 * 1024
MAX_OUTPUT_BYTES = 512 * 1024
Operation = Literal["file_read", "url_read", "wikipedia_search"]
Failure = Literal[
    "invalid_request",
    "file_unsupported",
    "file_unsafe",
    "file_changed",
    "file_too_large",
    "file_invalid_text",
    "file_unavailable",
    "url_unsafe",
    "redirect_rejected",
    "content_unsupported",
    "response_too_large",
    "response_invalid",
    "timeout",
    "cancelled",
    "network_unavailable",
    "tls_unavailable",
    "search_invalid",
    "source_rejected",
    "internal_error",
]


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class PublicSourceRequest(_DTO):
    """An exact foreground action, never an authorization from an untrusted source.

    Only the owning desktop writes this request over its own process pipe. These
    correlation fields are not authentication or grants for another process/device.
    Parent admission must fence profile, session, generation, request and expiry.
    """

    schema_version: Literal[1]
    request_id: UUID
    profile_id: UUID
    session_id: UUID
    generation: int = Field(ge=0, le=2**63 - 1)
    operation: Operation
    path: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    url: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    query: str | None = Field(default=None, min_length=1, max_length=300, repr=False)
    language: Literal["ru", "en"] | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version_invalid")
        return value

    @field_validator("request_id", "profile_id", "session_id")
    @classmethod
    def nonzero_id(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("correlation_invalid")
        return value

    @field_validator("path", "url", "query")
    @classmethod
    def no_controls(cls, value: str | None) -> str | None:
        if value is not None and (
            value.strip() != value
            or any(unicodedata.category(char).startswith("C") for char in value)
        ):
            raise ValueError("argument_invalid")
        return value

    @model_validator(mode="after")
    def exact_arguments(self) -> "PublicSourceRequest":
        provided = (self.path is not None, self.url is not None, self.query is not None)
        if (
            provided
            != {
                "file_read": (True, False, False),
                "url_read": (False, True, False),
                "wikipedia_search": (False, False, True),
            }[self.operation]
        ):
            raise ValueError("operation_arguments_invalid")
        if self.operation != "wikipedia_search" and self.language is not None:
            raise ValueError("operation_arguments_invalid")
        return self


class SearchItem(_DTO):
    title: str = Field(min_length=1, max_length=512, repr=False)
    url: str = Field(min_length=1, max_length=4096, repr=False)
    excerpt: str = Field(max_length=1200, repr=False)
    description: str | None = Field(default=None, max_length=512, repr=False)

    @field_validator("url")
    @classmethod
    def wikipedia_article(cls, value: str) -> str:
        target = validate_public_url(value)
        parsed = urlsplit(target.url)
        if (
            parsed.netloc not in {"ru.wikipedia.org", "en.wikipedia.org"}
            or not parsed.path.startswith("/wiki/")
            or len(parsed.path) <= len("/wiki/")
            or parsed.query
        ):
            raise ValueError("wikipedia_article_invalid")
        return value


class PublicSourceReply(_DTO):
    schema_version: Literal[1]
    request_id: UUID
    profile_id: UUID
    session_id: UUID
    generation: int = Field(ge=0, le=2**63 - 1)
    operation: Operation
    origin: Literal["selected_file", "public_https", "wikipedia"]
    status: Literal["ok", "empty", "partial", "rejected", "unavailable", "error"]
    reason: Failure | None = None
    observed_at: datetime
    expires_at: datetime
    source_label: str = Field(max_length=512, repr=False)
    source_url: str | None = Field(default=None, max_length=4096, repr=False)
    media_type: str | None = Field(default=None, max_length=100)
    text: str | None = Field(default=None, max_length=131_072, repr=False)
    items: tuple[SearchItem, ...] = Field(default=(), max_length=5, repr=False)
    raw_bytes: int = Field(default=0, ge=0, le=1024 * 1024)
    coverage: Literal["selected_file_only", "selected_url_only", "wikipedia_only"]
    untrusted: Literal[True]

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version_invalid")
        return value

    @field_validator("untrusted", mode="before")
    @classmethod
    def exact_boolean(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("untrusted_flag_invalid")
        return value

    @model_validator(mode="after")
    def coherent_receipt(self) -> "PublicSourceReply":
        if any(
            value.tzinfo is None or value.utcoffset() != timedelta(0)
            for value in (self.observed_at, self.expires_at)
        ):
            raise ValueError("utc_time_required")
        if not self.observed_at < self.expires_at <= self.observed_at + timedelta(minutes=5):
            raise ValueError("receipt_lifetime_invalid")
        expected = {
            "file_read": ("selected_file", "selected_file_only"),
            "url_read": ("public_https", "selected_url_only"),
            "wikipedia_search": ("wikipedia", "wikipedia_only"),
        }[self.operation]
        if (self.origin, self.coverage) != expected:
            raise ValueError("source_binding_invalid")
        failed = self.status in {"rejected", "unavailable", "error"}
        if failed != (self.reason is not None):
            raise ValueError("receipt_status_invalid")
        if failed and (self.text is not None or self.items or self.source_url is not None):
            raise ValueError("failure_content_forbidden")
        if self.status == "empty" and (self.text not in {None, ""} or self.items):
            raise ValueError("empty_content_invalid")
        if self.operation == "wikipedia_search" and self.text is not None:
            raise ValueError("search_content_invalid")
        if self.operation != "wikipedia_search" and self.items:
            raise ValueError("source_content_invalid")
        if self.operation != "url_read" and self.source_url is not None:
            raise ValueError("source_url_invalid")
        if not failed:
            if self.operation == "wikipedia_search":
                if self.status == "partial" or self.media_type is not None:
                    raise ValueError("search_content_invalid")
                if self.status == "ok" and not self.items:
                    raise ValueError("search_content_invalid")
            else:
                if self.text is None or (self.status != "empty" and not self.text):
                    raise ValueError("source_content_invalid")
                allowed = {"text/plain", "text/markdown", "text/csv", "application/json"}
                if self.operation == "url_read":
                    allowed.add("text/html")
                    if self.source_url is None:
                        raise ValueError("source_url_invalid")
                    validate_public_url(self.source_url)
                if self.media_type not in allowed:
                    raise ValueError("source_media_invalid")
        return self
