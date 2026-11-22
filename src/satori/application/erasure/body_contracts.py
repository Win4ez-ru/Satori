"""Strict metadata-only review of a bounded pair of existing owners."""

from typing import Literal

from pydantic import Field, field_validator

from satori.application.erasure.contracts import ErasurePreviewRequest
from satori.domain.source_policy import CanonicalRootRef

BODY_OWNERS = ("memory_manager", "retrieval_index")
BODY_RESIDUALS = (
    "canonical_and_assistant_text",
    "semantic_user_world_claims",
    "positions_inclinations",
    "reflection_appraisal_audit",
    "sqlite_free_pages_and_sidecars",
    "archives_staging_os_snapshots",
    "provider_caches_logs_external_copies",
    "identifiers_and_numeric_history",
)


class BodyErasureRequest(ErasurePreviewRequest):
    intent_request_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")


class BodyErasureCommand(BodyErasureRequest):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    expected_generation: int = Field(ge=0)
    expected_erasure_revision: int = Field(ge=0)
    expected_body_revision: int = Field(ge=0)
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class BodyErasurePreview(BodyErasureRequest):
    root: CanonicalRootRef
    generation: int
    erasure_revision: int
    body_revision: int
    approval_digest: str
    scanned_rows: int
    summaries: int
    quotes: int
    vectors: int
    already_erased: int
    readiness: Literal["ready", "blocked"]
    blockers: tuple[str, ...]
    operation: Literal["erase_episode_bodies"] = "erase_episode_bodies"
    owners: tuple[str, ...] = BODY_OWNERS
    residual_surfaces: tuple[str, ...] = BODY_RESIDUALS
    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False

    @field_validator("physical_erasure", "execution_authorized", mode="before")
    @classmethod
    def deny_physical_claim(cls, value: object) -> Literal[False]:
        if value is not False:
            raise ValueError("bounded_body_handling_is_not_full_physical_erasure")
        return False


class BodyErasureResult(BodyErasureRequest):
    root: CanonicalRootRef
    request_id: str
    action_id: str
    body_revision: int
    main_projection_current: bool
    materialized_body_revision: int
    effective_body_head_digest: str
    selected_bodies_removed: bool
    phase: Literal["body_application_pending", "body_application_complete"]
    failure_reason: str | None = None
    owners: tuple[str, ...] = BODY_OWNERS
    residual_surfaces: tuple[str, ...] = BODY_RESIDUALS
    physical_erasure: Literal[False] = False

    @field_validator("physical_erasure", mode="before")
    @classmethod
    def deny_physical_claim(cls, value: object) -> Literal[False]:
        if value is not False:
            raise ValueError("bounded_body_handling_is_not_full_physical_erasure")
        return False
