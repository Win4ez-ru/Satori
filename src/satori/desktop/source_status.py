"""Content-free presentation of the existing personal-source owners."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SourceKey = Literal["calendar", "mail", "telegram"]
ConnectionStatus = Literal[
    "disconnected", "connecting", "connected", "expired", "unavailable", "closing", "error"
]
SnapshotStatus = Literal["not_read", "fresh", "partial", "stale", "unknown"]
CatalogStatus = Literal["partial", "unsupported", "empty"]
SourceProblem = Literal[
    "none", "permission", "expired", "unavailable", "rejected", "transport", "unknown"
]


class SourceStatus(BaseModel):
    """UI metadata only; neither a source grant nor an account/read receipt.

    Owners calculate freshness using their original deadlines. Reading this
    projection must not connect, read, refresh, renew grants or disclose content.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: SourceKey
    connection: ConnectionStatus
    selected_count: int | None = Field(default=None, ge=0, le=100)
    snapshot: SnapshotStatus = "not_read"
    observed_at: datetime | None = None
    busy: bool = False
    problem: SourceProblem = "none"
    # Optional metadata-list availability is independent from a selected-body snapshot.
    catalog_state: CatalogStatus | None = None

    @field_validator("observed_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source_status_timezone_required")
        return value
