"""Strict scoped metadata for the three existing knowledge state owners."""

from typing import Literal

from pydantic import field_validator

from satori.application.erasure.body_contracts import BodyErasureRequest
from satori.domain.source_policy import CanonicalRootRef

KNOWLEDGE_OWNERS = ("semantic_memory", "current_models", "positions")
KNOWLEDGE_RESIDUALS = (
    "canonical_and_episode_bodies_not_selected_by_this_operation",
    "reflection_appraisal_audit",
    "sqlite_free_pages_and_sidecars",
    "archives_staging_os_snapshots",
    "provider_caches_logs_external_copies",
    "identifiers_hashes_and_numeric_history",
)


class KnowledgeBodyPreview(BodyErasureRequest):
    root: CanonicalRootRef
    generation: int
    erasure_revision: int
    body_revision: int
    approval_digest: str
    scanned_rows: int
    semantic_claims: int
    user_model_claims: int
    world_model_claims: int
    positions: int
    inclinations: int
    position_quotes: int
    witness_transitions: int
    already_erased: int
    readiness: Literal["ready", "blocked"]
    blockers: tuple[str, ...]
    operation: Literal["erase_knowledge_bodies"] = "erase_knowledge_bodies"
    owners: tuple[str, ...] = KNOWLEDGE_OWNERS
    residual_surfaces: tuple[str, ...] = KNOWLEDGE_RESIDUALS
    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False

    @field_validator("physical_erasure", "execution_authorized", mode="before")
    @classmethod
    def deny_physical_claim(cls, value: object) -> Literal[False]:
        if value is not False:
            raise ValueError("knowledge_handling_is_not_full_physical_erasure")
        return False
