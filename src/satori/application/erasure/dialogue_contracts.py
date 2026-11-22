"""Content-free operator review for the InteractionLog owner."""

from typing import Literal

from pydantic import field_validator

from satori.application.erasure.body_contracts import BodyErasureRequest
from satori.domain.source_policy import CanonicalRootRef

DIALOGUE_OWNERS = ("interaction_log",)
DIALOGUE_RESIDUALS = (
    "semantic_user_world_claims",
    "positions_inclinations_and_retained_quotes",
    "reflection_appraisal_audit",
    "sqlite_free_pages_and_sidecars",
    "archives_staging_os_snapshots",
    "provider_caches_logs_external_copies",
    "identifiers_and_numeric_history",
)


class DialogueBodyPreview(BodyErasureRequest):
    root: CanonicalRootRef
    generation: int
    erasure_revision: int
    body_revision: int
    approval_digest: str
    scanned_rows: int
    user_bodies: int
    assistant_bodies: int
    historical_witnesses: int
    already_erased: int
    readiness: Literal["ready", "blocked"]
    blockers: tuple[str, ...]
    operation: Literal["erase_dialogue_bodies"] = "erase_dialogue_bodies"
    owners: tuple[str, ...] = DIALOGUE_OWNERS
    residual_surfaces: tuple[str, ...] = DIALOGUE_RESIDUALS
    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False

    # Pydantic Literal[False] accepts 0; require exact booleans as the episode contract does.
    @field_validator("physical_erasure", "execution_authorized", mode="before")
    @classmethod
    def deny_physical_claim(cls, value: object) -> Literal[False]:
        if value is not False:
            raise ValueError("dialogue_handling_is_not_full_physical_erasure")
        return False
