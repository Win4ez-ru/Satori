"""Strict immutable public metadata; a fresh check does not authorize execution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from satori.domain.erasure import ErasureRoute
from satori.domain.source_policy import CanonicalRootRef


class ErasurePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    message_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    counterparty_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")


class ErasureCheckRequest(ErasurePreviewRequest):
    expected_generation: int = Field(ge=0)
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ErasurePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    root: CanonicalRootRef
    floor_id: str
    installation_id: str
    generation: int = Field(ge=0)
    policy_version: str
    source_policy_version: str
    schema_revision: str
    schema_digest: str
    store_digest: str
    routing_digest: str
    preview_digest: str
    routes: tuple[ErasureRoute, ...]
    scanned_rows: int
    affected_payload_versions: int
    uncertain_payload_versions: int
    assessment_status: Literal["complete", "blocked"]
    assessment_blockers: tuple[str, ...]
    inventory_scope: Literal["current_main_database"] = "current_main_database"
    external_retention: Literal["not_assessed"] = "not_assessed"
    unassessed_surfaces: tuple[str, ...]
    physical_readiness: Literal["blocked"] = "blocked"
    execution_blockers: tuple[str, ...]
    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False
    automatic_processing: Literal[False] = False
    historical_numeric_state_retained: Literal[True] = True


class ErasureCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    preview_digest: str
    generation: int
    review_status: Literal["current"] = "current"
    assessment_status: Literal["complete", "blocked"]
    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False
