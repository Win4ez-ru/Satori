"""Strict offline mail capabilities and untrusted source projection records.

No record grants network access or admits a canonical conversation/evidence item.
"""

import hashlib
import math
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Literal, cast, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import LocalPrincipal, require_identifier, require_utc

MAX_MAIL_MESSAGES = 10
MAX_MAIL_CATALOG = 20
MAX_MAIL_MESSAGE_BYTES = 512 * 1024
MAX_MAIL_BATCH_BYTES = 1024 * 1024
MAX_MAIL_DECODED_BYTES = 64 * 1024
MAX_MAIL_BATCH_DECODED_BYTES = 128 * 1024
MAX_MAIL_TEXT_CHARS = 16000
MAX_MAIL_BATCH_TEXT_CHARS = 24000
MAX_MAIL_GRANT_LIFETIME = timedelta(hours=1)
MAX_MAIL_REQUEST_LIFETIME = timedelta(seconds=30)
MAX_MAIL_SNAPSHOT_AGE = timedelta(minutes=5)
MAIL_PROJECTION_VERSION: Literal["gmail-local-text-v5"] = "gmail-local-text-v5"

MailAccessCode = Literal[
    "clock_invalid",
    "catalog_invalid",
    "catalog_expired",
    "selection_invalid",
    "grant_required",
    "grant_expired",
    "scope_mismatch",
    "stale_request",
    "request_expired",
    "result_invalid",
    "result_too_large",
    "snapshot_expired",
    "audit_unavailable",
    "invalid_message",
    "message_too_large",
    "batch_too_large",
    "unsupported_structure",
    "invalid_header",
    "invalid_body",
    "mime_limit",
]
MailAccessDiagnostic = Literal[
    "header_structure",
    "header_count_limit",
    "header_name_type",
    "header_name_limit",
    "header_name_controls",
    "header_name_syntax",
    "header_value_type",
    "header_value_limit",
    "header_folding",
    "header_bare_cr",
    "header_bare_lf",
    "header_controls",
    "header_format_controls",
    "header_surrogate_controls",
    "header_duplicate",
    "header_decoded_limit",
    "header_decoded_controls",
    "header_mime_mismatch",
    "header_mime_parameter",
    "header_mime_duplicate_parameter",
    "header_mime_extended_charset",
    "header_mime_charset",
    "body_structure",
    "body_encoded_limit",
    "body_base64_alphabet",
    "body_base64_length",
    "body_base64_padding",
    "body_base64_decode",
    "body_decoded_limit",
    "body_base64_noncanonical",
    "body_container_size",
    "body_size_value",
    "body_size_mismatch",
    "body_charset_decode",
    "body_controls",
    "body_format_controls",
    "body_html_format_controls",
    "body_surrogate_controls",
    "body_html_controls",
]


def mail_access_diagnostic(code: object, diagnostic: object) -> MailAccessDiagnostic | None:
    """Return only a closed diagnostic belonging to its existing error category."""
    if type(diagnostic) is not str or diagnostic not in get_args(MailAccessDiagnostic):
        return None
    if type(code) is not str or code not in {"invalid_header", "invalid_body"}:
        return None
    prefix = "header_" if code == "invalid_header" else "body_"
    return cast(MailAccessDiagnostic, diagnostic) if diagnostic.startswith(prefix) else None


MailOmissionReason = Literal[
    "attachment_omitted",
    "nested_message_omitted",
    "external_body_omitted",
    "unsupported_mime",
    "unsupported_charset",
    "html_lossy",
    "html_discarded_format_controls",
    "empty_body",
    "text_truncated",
    "inline_resource_omitted",
    "alternative_omitted",
    "alternative_not_decoded",
    "header_decoding_unsupported",
]


class MailAccessError(ValueError):
    """Expose only validated closed metadata, never source or provider exception text."""

    def __init__(
        self, code: MailAccessCode, diagnostic: MailAccessDiagnostic | None = None
    ) -> None:
        if type(code) is not str or code not in get_args(MailAccessCode):
            raise ValueError("mail_access_code_invalid")
        if diagnostic is not None and mail_access_diagnostic(code, diagnostic) is None:
            raise ValueError("mail_access_diagnostic_invalid")
        self.code: MailAccessCode = code
        self.diagnostic: MailAccessDiagnostic | None = diagnostic
        super().__init__(code)


class MailDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    @field_validator("*")
    @classmethod
    def nonzero_uuid(cls, value: object) -> object:
        if isinstance(value, UUID) and value.int == 0:
            raise ValueError("mail_uuid_invalid")
        return value


def mail_identifier(value: str) -> str:
    require_identifier(value)
    if not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,255}", value):
        raise ValueError("mail_identifier_invalid")
    return value


def mail_digest(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("mail_digest_invalid")
    return value


class MailClock(MailDTO):
    wall: datetime
    monotonic: float = Field(ge=0, allow_inf_nan=False)

    _utc = field_validator("wall")(require_utc)


class MailAccountRef(MailDTO):
    provider: Literal["gmail"] = "gmail"
    issuer: Literal["https://accounts.google.com"] = "https://accounts.google.com"
    subject: str = Field(min_length=1, max_length=255, repr=False)
    client_registration: str = Field(min_length=1, max_length=255, repr=False)

    _identifiers = field_validator("subject", "client_registration")(mail_identifier)


class MailSourceRef(MailDTO):
    account: MailAccountRef = Field(repr=False)
    message_id: str = Field(min_length=1, max_length=255, repr=False)

    _identifier = field_validator("message_id")(mail_identifier)


class MailPartRef(MailDTO):
    path: tuple[int, ...] = Field(max_length=8)
    provider_part_id: str | None = Field(default=None, max_length=255, repr=False)

    @field_validator("path")
    @classmethod
    def bounded_path(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(type(index) is not int or not 0 <= index < 64 for index in value):
            raise ValueError("mime_path_invalid")
        return value

    @field_validator("provider_part_id")
    @classmethod
    def part_identifier(cls, value: str | None) -> str | None:
        return require_identifier(value) if value else value


class MailHeaderClaim(MailDTO):
    name: str = Field(min_length=1, max_length=64)
    value: str = Field(max_length=2048, repr=False)
    index: int = Field(ge=0, lt=64)
    trust: Literal["external_untrusted"] = "external_untrusted"

    @field_validator("name")
    @classmethod
    def header_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9!#$%&'*+.^_`|~-]{1,64}", value):
            raise ValueError("mail_header_name_invalid")
        return value

    @field_validator("value")
    @classmethod
    def header_value(cls, value: str) -> str:
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char != "\t" for char in value):
            raise ValueError("mail_header_value_invalid")
        return value


class MailTextSpan(MailDTO):
    span_id: str
    part: MailPartRef
    start: int = Field(ge=0, le=MAX_MAIL_TEXT_CHARS)
    end: int = Field(ge=0, le=MAX_MAIL_TEXT_CHARS)
    text_digest: str
    attribution: Literal["external_untrusted", "quoted_claim"] = "external_untrusted"

    _digests = field_validator("span_id", "text_digest")(mail_digest)

    @model_validator(mode="after")
    def positive_span(self) -> "MailTextSpan":
        if self.start >= self.end:
            raise ValueError("mail_span_empty")
        return self


class MailOmission(MailDTO):
    reason: MailOmissionReason
    part: MailPartRef | None = None


class MailProjection(MailDTO):
    source: MailSourceRef = Field(repr=False)
    thread_id: str | None = Field(default=None, max_length=255, repr=False)
    internal_date: datetime | None = None
    history_id: str | None = Field(default=None, max_length=255, repr=False)
    headers: tuple[MailHeaderClaim, ...] = Field(default=(), max_length=64, repr=False)
    text: str = Field(max_length=MAX_MAIL_TEXT_CHARS, repr=False)
    spans: tuple[MailTextSpan, ...] = Field(default=(), max_length=64, repr=False)
    format: Literal["plain", "html_plaintext", "none"]
    completeness: Literal["complete", "partial", "unsupported"]
    omissions: tuple[MailOmission, ...] = Field(default=(), max_length=128)
    raw_payload_digest: str
    projection_digest: str
    decoded_bytes: int = Field(ge=0, le=MAX_MAIL_DECODED_BYTES)
    wire_bytes: int = Field(ge=1, le=MAX_MAIL_MESSAGE_BYTES)
    trust: Literal["external_untrusted"] = "external_untrusted"
    projection_version: Literal["gmail-local-text-v5"] = MAIL_PROJECTION_VERSION

    _digests = field_validator("raw_payload_digest", "projection_digest")(mail_digest)

    @field_validator("internal_date")
    @classmethod
    def optional_date(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def source_spans(self) -> "MailProjection":
        previous = 0
        seen: set[str] = set()
        for span in self.spans:
            if span.start < previous or span.end > len(self.text) or span.span_id in seen:
                raise ValueError("mail_span_invalid")
            if self.text[previous : span.start].strip():
                raise ValueError("mail_span_gap")
            if (
                hashlib.sha256(self.text[span.start : span.end].encode()).hexdigest()
                != span.text_digest
            ):
                raise ValueError("mail_span_digest_invalid")
            seen.add(span.span_id)
            previous = span.end
        if self.text[previous:].strip():
            raise ValueError("mail_span_missing")
        incomplete = any(item.reason != "alternative_omitted" for item in self.omissions)
        if self.completeness == "complete" and (
            incomplete or self.format != "plain" or not self.text
        ):
            raise ValueError("mail_completeness_invalid")
        return self

    @property
    def processing_eligible(self) -> bool:
        return self.completeness == "complete" and self.format == "plain" and bool(self.text)


class MailCatalog(MailDTO):
    principal: LocalPrincipal
    account: MailAccountRef = Field(repr=False)
    catalog_id: UUID
    observed: MailClock
    message_ids: tuple[str, ...] = Field(max_length=MAX_MAIL_CATALOG, repr=False)

    @field_validator("message_ids")
    @classmethod
    def ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            mail_identifier(item)
        if len(set(value)) != len(value):
            raise ValueError("mail_duplicate_ids")
        return value


class MailSelectionProof(MailDTO):
    principal: LocalPrincipal
    account: MailAccountRef = Field(repr=False)
    catalog_id: UUID
    selection_id: UUID
    generation: int = Field(ge=1)
    revision: int = Field(ge=1)
    resources: tuple[MailSourceRef, ...] = Field(
        min_length=1, max_length=MAX_MAIL_MESSAGES, repr=False
    )

    @model_validator(mode="after")
    def scope(self) -> "MailSelectionProof":
        if len(set(self.resources)) != len(self.resources):
            raise ValueError("mail_duplicate_resources")
        if any(source.account != self.account for source in self.resources):
            raise ValueError("mail_cross_account_selection")
        return self


class MailReadGrant(MailDTO):
    grant_id: UUID
    selection: MailSelectionProof = Field(repr=False)
    issued: MailClock
    expires_at: datetime
    monotonic_deadline: float = Field(ge=0, allow_inf_nan=False)
    operation: Literal["mail_read_local"] = "mail_read_local"

    _utc = field_validator("expires_at")(require_utc)

    @model_validator(mode="after")
    def lifetime(self) -> "MailReadGrant":
        seconds = (self.expires_at - self.issued.wall).total_seconds()
        if not 0 < seconds <= MAX_MAIL_GRANT_LIFETIME.total_seconds():
            raise ValueError("mail_grant_expiry_invalid")
        if not math.isclose(self.monotonic_deadline - self.issued.monotonic, seconds, abs_tol=1e-6):
            raise ValueError("mail_deadline_invalid")
        return self


class MailReadRequest(MailDTO):
    request_id: UUID
    grant: MailReadGrant = Field(repr=False)
    created: MailClock
    expires_at: datetime
    monotonic_deadline: float = Field(ge=0, allow_inf_nan=False)

    _utc = field_validator("expires_at")(require_utc)

    @model_validator(mode="after")
    def lifetime(self) -> "MailReadRequest":
        seconds = (self.expires_at - self.created.wall).total_seconds()
        if not 0 < seconds <= MAX_MAIL_REQUEST_LIFETIME.total_seconds():
            raise ValueError("mail_request_expiry_invalid")
        if (
            self.expires_at > self.grant.expires_at
            or self.monotonic_deadline > self.grant.monotonic_deadline
        ):
            raise ValueError("mail_request_outlives_grant")
        if not math.isclose(
            self.monotonic_deadline - self.created.monotonic, seconds, abs_tol=1e-6
        ):
            raise ValueError("mail_deadline_invalid")
        return self


class MailReadResult(MailDTO):
    request_id: UUID
    principal: LocalPrincipal
    account: MailAccountRef = Field(repr=False)
    generation: int = Field(ge=1)
    selection_revision: int = Field(ge=1)
    observed: MailClock
    messages: tuple[bytes, ...] = Field(min_length=1, max_length=MAX_MAIL_MESSAGES, repr=False)

    @model_validator(mode="after")
    def bytes_bounded(self) -> "MailReadResult":
        if any(len(raw) > MAX_MAIL_MESSAGE_BYTES for raw in self.messages):
            raise ValueError("mail_message_too_large")
        if sum(map(len, self.messages)) > MAX_MAIL_BATCH_BYTES:
            raise ValueError("mail_batch_too_large")
        return self


class MailSnapshot(MailDTO):
    snapshot_id: UUID
    request: MailReadRequest = Field(repr=False)
    observed: MailClock
    expires_at: datetime
    monotonic_deadline: float = Field(ge=0, allow_inf_nan=False)
    messages: tuple[MailProjection, ...] = Field(
        min_length=1, max_length=MAX_MAIL_MESSAGES, repr=False
    )

    _utc = field_validator("expires_at")(require_utc)

    @model_validator(mode="after")
    def scope_and_freshness(self) -> "MailSnapshot":
        if tuple(item.source for item in self.messages) != self.request.grant.selection.resources:
            raise ValueError("mail_snapshot_scope_invalid")
        seconds = (self.expires_at - self.observed.wall).total_seconds()
        if not 0 < seconds <= MAX_MAIL_SNAPSHOT_AGE.total_seconds():
            raise ValueError("mail_snapshot_expiry_invalid")
        if (
            self.expires_at > self.request.grant.expires_at
            or self.monotonic_deadline > self.request.grant.monotonic_deadline
        ):
            raise ValueError("mail_snapshot_outlives_grant")
        if not math.isclose(
            self.monotonic_deadline - self.observed.monotonic, seconds, abs_tol=1e-6
        ):
            raise ValueError("mail_deadline_invalid")
        return self


class MailAuditEvent(MailDTO):
    event: Literal[
        "catalog_admitted",
        "selection_confirmed",
        "grant_issued",
        "grant_revoked",
        "read_prepared",
        "read_admitted",
        "read_rejected",
        "snapshot_expired",
    ]
    occurred_at: datetime
    generation: int = Field(ge=0)
    request_id: UUID | None = None
    grant_id: UUID | None = None
    resource_count: int = Field(ge=0, le=MAX_MAIL_CATALOG, default=0)
    reason: MailAccessCode | None = None

    _utc = field_validator("occurred_at")(require_utc)
