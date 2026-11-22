"""Versioned metadata only; private snapshot bytes stay outside preview DTOs."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SnapshotInventory(BaseModel):
    """Validated stored-state inventory, independent of Character projections."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    identity_id: str | None
    alembic_revision: str
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    table_counts: dict[str, int]

    @field_validator("table_counts")
    @classmethod
    def nonnegative_counts(cls, value: dict[str, int]) -> dict[str, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("negative table count")
        return value


class RecoveryManifest(SnapshotInventory):
    """Strict initial archive format; encryption is a subsequent gate."""

    format: Literal["satori-state-v1"]
    app_version: Literal["0.1.0"]
    created_at: datetime
    snapshot_bytes: int = Field(gt=0, le=268_435_456)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("created_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("archive time must include timezone")
        return value


class RecoveryPreview(BaseModel):
    """Content-free result binding activation to the complete reviewed archive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    archive_sha256: str
    manifest: RecoveryManifest
    encrypted: Literal[False] = False
    automatic_processing: Literal[False] = False


class RecoveryError(ValueError):
    """Safe reason code; never expose imported values, SQL or SQLite diagnostics."""
