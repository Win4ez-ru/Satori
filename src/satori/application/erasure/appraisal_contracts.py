"""Scoped metadata-only approval for retained appraisal explanation copies."""

from typing import Literal

from pydantic import field_validator

from satori.application.erasure.body_contracts import BodyErasureRequest
from satori.domain.source_policy import CanonicalRootRef

APPRAISAL_OWNERS = ("affect",)
APPRAISAL_RESIDUALS = (
    "canonical_episode_and_knowledge_bodies_not_selected_by_this_operation",
    "other_appraisal_audit_explanations_not_selected_by_this_operation",
    "sqlite_free_pages_and_sidecars",
    "archives_staging_os_snapshots",
    "provider_caches_logs_external_copies",
    "identifiers_hashes_outcomes_and_numeric_history",
)


class AppraisalBodyPreview(BodyErasureRequest):
    root: CanonicalRootRef
    generation: int
    erasure_revision: int
    body_revision: int
    approval_digest: str
    scanned_rows: int
    appraisal_transitions: int
    audit_duplicates: int
    input_receipts: int
    metadata_references: int
    already_erased: int
    readiness: Literal["ready", "blocked"]
    blockers: tuple[str, ...]
    operation: Literal["erase_appraisal_explanations"] = "erase_appraisal_explanations"
    owners: tuple[str, ...] = APPRAISAL_OWNERS
    residual_surfaces: tuple[str, ...] = APPRAISAL_RESIDUALS
    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False

    @field_validator("physical_erasure", "execution_authorized", mode="before")
    @classmethod
    def deny_physical_claim(cls, value: object) -> Literal[False]:
        if value is not False:
            raise ValueError("appraisal_handling_is_not_full_physical_erasure")
        return False
