"""Scoped metadata-only approval for retained Reflection proposal payloads."""

from typing import Literal

from pydantic import field_validator

from satori.application.erasure.body_contracts import BodyErasureRequest
from satori.domain.source_policy import CanonicalRootRef

REFLECTION_OWNERS = ("reflection",)
REFLECTION_RESIDUALS = (
    "canonical_episode_and_knowledge_bodies_not_selected_by_this_operation",
    "reflection_attempt_metrics_appraisal_audit",
    "sqlite_free_pages_and_sidecars",
    "archives_staging_os_snapshots",
    "provider_caches_logs_external_copies",
    "identifiers_hashes_outcomes_and_numeric_history",
)


class ReflectionBodyPreview(BodyErasureRequest):
    root: CanonicalRootRef
    generation: int
    erasure_revision: int
    body_revision: int
    approval_digest: str
    scanned_rows: int
    reflection_proposals: int
    parent_runs: int
    source_rows: int
    attempt_rows: int
    outcome_rows: int
    metadata_references: int
    already_erased: int
    readiness: Literal["ready", "blocked"]
    blockers: tuple[str, ...]
    operation: Literal["erase_reflection_payloads"] = "erase_reflection_payloads"
    owners: tuple[str, ...] = REFLECTION_OWNERS
    residual_surfaces: tuple[str, ...] = REFLECTION_RESIDUALS
    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False

    @field_validator("physical_erasure", "execution_authorized", mode="before")
    @classmethod
    def deny_physical_claim(cls, value: object) -> Literal[False]:
        if value is not False:
            raise ValueError("reflection_handling_is_not_full_physical_erasure")
        return False
