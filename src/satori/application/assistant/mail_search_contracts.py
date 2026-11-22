"""One explicit foreground mailbox query; not a selected-body grant or stored state."""

import math
import unicodedata
from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import LocalPrincipal, require_utc
from satori.application.assistant.mail_contracts import MailAccountRef, MailClock, MailDTO

MAX_MAIL_SEARCH_QUERY = 512
MAX_MAIL_SEARCH_QUERY_CHARS = MAX_MAIL_SEARCH_QUERY
MAX_MAIL_SEARCH_SECONDS = 30


def valid_mail_search_query(value: str) -> bool:
    return (
        type(value) is str
        and bool(value.strip())
        and len(value) <= MAX_MAIL_SEARCH_QUERY_CHARS
        and not any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)
    )


class MailSearchRequest(MailDTO):
    """Typed authority supplied by a current explicit user query for one bounded search."""

    model_config = ConfigDict(revalidate_instances="always")

    request_id: UUID
    principal: LocalPrincipal = Field(repr=False)
    account: MailAccountRef = Field(repr=False)
    query: str = Field(min_length=1, max_length=MAX_MAIL_SEARCH_QUERY_CHARS, repr=False)
    created: MailClock
    expires_at: datetime
    monotonic_deadline: float = Field(ge=0, allow_inf_nan=False)

    _utc = field_validator("expires_at")(require_utc)

    @field_validator("query")
    @classmethod
    def bounded_query(cls, value: str) -> str:
        if not valid_mail_search_query(value):
            raise ValueError("mail_search_query_invalid")
        return value

    @model_validator(mode="after")
    def bounded_lifetime(self) -> "MailSearchRequest":
        wall_seconds = (self.expires_at - self.created.wall).total_seconds()
        mono_seconds = self.monotonic_deadline - self.created.monotonic
        if (
            not 0 < wall_seconds <= MAX_MAIL_SEARCH_SECONDS
            or not 0 < mono_seconds <= MAX_MAIL_SEARCH_SECONDS
            or not math.isclose(wall_seconds, mono_seconds, rel_tol=0, abs_tol=1e-9)
        ):
            raise ValueError("mail_search_lifetime_invalid")
        return self
