"""Transient user-query binding, never a source grant or persistent state owner."""

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from satori.application.assistant.mail_lookup import valid_mail_lookup_query
from satori.application.assistant.mail_search_contracts import valid_mail_search_query


@dataclass(frozen=True)
class MailLookupHandoff:
    request_id: UUID
    query: str = field(repr=False)
    profile: Path = field(repr=False)
    session_id: str = field(repr=False)
    epoch: int
    issued_at: float
    deadline: float
    scope: Literal["selected", "mailbox"] = "selected"
    issued_wall: datetime | None = None
    expires_wall: datetime | None = None

    def __post_init__(self) -> None:
        if (
            self.request_id.int == 0
            or self.scope not in {"selected", "mailbox"}
            or not (
                valid_mail_search_query(self.query)
                if self.scope == "mailbox"
                else valid_mail_lookup_query(self.query)
            )
            or not self.profile.is_absolute()
            or not self.session_id
            or self.epoch < 0
            or not math.isfinite(self.issued_at)
            or not math.isfinite(self.deadline)
            or not self.issued_at < self.deadline <= self.issued_at + 300
        ):
            raise ValueError("mail_lookup_handoff_invalid")
        if self.scope == "mailbox" and (
            self.issued_wall is None
            or self.expires_wall is None
            or self.issued_wall.tzinfo is None
            or self.expires_wall.tzinfo is None
            or self.issued_wall.utcoffset() != UTC.utcoffset(None)
            or self.expires_wall.utcoffset() != UTC.utcoffset(None)
            or not 0 < (self.expires_wall - self.issued_wall).total_seconds() <= 300
        ):
            raise ValueError("mail_lookup_handoff_invalid")

    def current(
        self,
        *,
        profile: Path,
        session_id: str | None,
        epoch: int,
        now: float,
        wall_now: datetime | None = None,
    ) -> bool:
        return (
            self.profile == profile
            and self.session_id == session_id
            and self.epoch == epoch
            and math.isfinite(now)
            and self.issued_at <= now < self.deadline
            and (
                self.scope == "selected"
                or (
                    wall_now is not None
                    and wall_now.tzinfo is not None
                    and wall_now.utcoffset() == UTC.utcoffset(None)
                    and self.issued_wall is not None
                    and self.expires_wall is not None
                    and self.issued_wall <= wall_now < self.expires_wall
                )
            )
        )
