"""Content-free operator diagnostics for canonical memory attribution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryScopeStatus(BaseModel):
    """Counts distinguish missing memory from retained but unverified sources."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    identity_id: str
    counterparty_id: str
    verified_sessions_in_scope: int = Field(ge=0)
    unverified_sessions_for_identity: int = Field(ge=0)
    eligible_episodes_in_scope: int = Field(ge=0)
    quarantined_episodes_for_identity: int = Field(ge=0)
