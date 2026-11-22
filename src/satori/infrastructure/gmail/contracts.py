"""Closed transient Gmail transport records; no credentials in UI results or logs."""

import re
from datetime import datetime
from pathlib import Path
from typing import Literal, cast, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from satori.application.assistant.contracts import require_utc
from satori.application.assistant.mail_contracts import (
    MailAccountRef,
    MailCatalog,
    MailHeaderClaim,
    MailSourceRef,
)
from satori.application.assistant.mail_search_contracts import MailSearchRequest

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SCOPES = frozenset({"openid", "email", GMAIL_SCOPE})
GmailErrorCode = Literal[
    "configuration_required",
    "configuration_invalid",
    "configuration_unavailable",
    "login_in_progress",
    "login_denied",
    "callback_invalid",
    "state_mismatch",
    "cancelled",
    "timeout",
    "not_connected",
    "auth_expired",
    "scope_rejected",
    "account_invalid",
    "account_mismatch",
    "http_unavailable",
    "http_error",
    "permission_denied",
    "response_invalid",
    "response_too_large",
    "tls_unavailable",
    "request_invalid",
]


GmailResponseDiagnostic = Literal[
    "http_status_type",
    "http_content_range",
    "http_content_encoding",
    "http_content_length_duplicate",
    "http_transfer_encoding",
    "http_content_length_syntax",
    "http_body_type",
    "http_body_length_mismatch",
    "list_json",
    "list_rows_shape",
    "list_rows_count",
    "list_row_shape",
    "list_identifier",
    "list_duplicate_identifier",
    "list_cursor",
    "metadata_json",
    "metadata_identifier",
    "metadata_payload_shape",
    "metadata_headers_shape",
    "metadata_header_count",
    "metadata_header_shape",
    "metadata_header_name",
    "metadata_header_duplicate",
    "metadata_header_value_type",
    "metadata_header_value_limit",
    "metadata_header_value_controls",
    "metadata_header_decoding",
    "metadata_header_decoded_limit",
    "metadata_header_decoded_controls",
]


def gmail_response_diagnostic(code: object, diagnostic: object) -> GmailResponseDiagnostic | None:
    """Admit only closed response-validation metadata, never provider/source text."""
    if type(code) is not str or code != "response_invalid" or type(diagnostic) is not str:
        return None
    if diagnostic not in get_args(GmailResponseDiagnostic):
        return None
    return cast(GmailResponseDiagnostic, diagnostic)


class GmailError(ValueError):
    def __init__(
        self, code: GmailErrorCode, *, response_diagnostic: GmailResponseDiagnostic | None = None
    ) -> None:
        self.code = code
        self.response_diagnostic = gmail_response_diagnostic(code, response_diagnostic)
        super().__init__(code)


class GmailDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class GmailClientConfiguration(GmailDTO):
    client_id: str = Field(min_length=1, max_length=255, repr=False)
    client_secret: SecretStr = Field(repr=False)

    @field_validator("client_id")
    @classmethod
    def installed_client_id(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", value) is None:
            raise ValueError("configuration_invalid")
        return value

    @field_validator("client_secret")
    @classmethod
    def desktop_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if not 1 <= len(secret) <= 4096 or not all(33 <= ord(char) <= 126 for char in secret):
            raise ValueError("configuration_invalid")
        return value


class GmailLoginStart(GmailDTO):
    attempt_id: UUID
    authorization_url: str = Field(repr=False)


class GmailAccount(GmailDTO):
    account: MailAccountRef = Field(repr=False)
    email_label: str = Field(min_length=1, max_length=320, repr=False)


class GmailMessageHeader(GmailDTO):
    source: MailSourceRef = Field(repr=False)
    headers: tuple[MailHeaderClaim, ...] = Field(max_length=64, repr=False)
    thread_id: str | None = Field(default=None, max_length=255, repr=False)
    internal_date: datetime | None = None

    @field_validator("internal_date")
    @classmethod
    def internal_utc(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    def _claim(self, name: str) -> str:
        return next((item.value for item in self.headers if item.name == name), "")

    @property
    def sender(self) -> str:
        return self._claim("from")

    @property
    def subject(self) -> str:
        return self._claim("subject")

    @property
    def date(self) -> str:
        return self._claim("date")


GmailHeaderOmissionReason = Literal[
    "metadata_header_value_controls", "metadata_header_decoded_controls"
]


class GmailHeaderOmission(GmailDTO):
    """Number of whole messages excluded; no omitted identity or source claims."""

    model_config = ConfigDict(revalidate_instances="always")

    reason: GmailHeaderOmissionReason
    count: int = Field(ge=1, le=20)


class GmailInboxPage(GmailDTO):
    catalog: MailCatalog = Field(repr=False)
    messages: tuple[GmailMessageHeader, ...] = Field(max_length=20, repr=False)
    more_available: bool
    completeness: Literal["complete", "partial", "unsupported"] = "complete"
    omissions: tuple[GmailHeaderOmission, ...] = Field(default=(), max_length=2)

    @property
    def omitted_count(self) -> int:
        return sum(omission.count for omission in self.omissions)

    @property
    def total_rows(self) -> int:
        return len(self.messages) + self.omitted_count

    @model_validator(mode="after")
    def exact_catalog_and_completeness(self) -> "GmailInboxPage":
        if (
            self.total_rows > 20
            or len({item.reason for item in self.omissions}) != len(self.omissions)
            or tuple(message.source.message_id for message in self.messages)
            != self.catalog.message_ids
            or any(message.source.account != self.catalog.account for message in self.messages)
        ):
            raise ValueError("gmail_page_invalid")
        expected = (
            "complete" if not self.omissions else "partial" if self.messages else "unsupported"
        )
        if self.completeness != expected:
            raise ValueError("gmail_page_invalid")
        return self


class GmailSearchPage(GmailDTO):
    """Provider-side query result bound to its exact ephemeral request, never a body grant."""

    request: MailSearchRequest = Field(repr=False)
    page: GmailInboxPage = Field(repr=False)

    @model_validator(mode="after")
    def exact_search_scope(self) -> "GmailSearchPage":
        observed = self.page.catalog.observed
        if (
            self.request.principal != self.page.catalog.principal
            or self.request.account != self.page.catalog.account
            or not self.request.created.wall <= observed.wall < self.request.expires_at
            or not self.request.created.monotonic
            <= observed.monotonic
            < self.request.monotonic_deadline
        ):
            raise ValueError("gmail_search_page_invalid")
        return self


class GmailDisconnectResult(GmailDTO):
    local_disconnected: Literal[True] = True
    remote: Literal["revoked", "failed", "unknown", "not_requested", "not_connected"]


def load_client_configuration(path: Path) -> GmailClientConfiguration:
    """Read only an explicitly selected bounded regular installed-client JSON file."""
    # Imported lazily to keep the configuration parser independent from account I/O.
    from satori.infrastructure.gmail.oauth import load_configuration

    return load_configuration(path)
