"""Strict metadata-only operator boundaries; approval labels are not authentication."""

from pydantic import BaseModel, ConfigDict, Field

from satori.domain.source_policy import CanonicalRootRef, SourceExclusionReceipt


class SourceExclusionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    message_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    expected_generation: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceExclusionEffects(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_excluded: bool
    ordinary_disclosure_allowed: bool
    new_provider_input_allowed: bool
    new_evidence_allowed: bool
    canonical_history_retained: bool
    historical_numeric_state_retained: bool
    physical_erasure: bool
    technical_permission_granted: bool


class SourceExclusionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    root: CanonicalRootRef
    floor_id: str
    installation_id: str
    generation: int
    already_excluded: bool
    preview_digest: str
    effects: SourceExclusionEffects
    approved_by: str = "local_operator"


__all__ = [
    "SourceExclusionCommand",
    "SourceExclusionEffects",
    "SourceExclusionPreview",
    "SourceExclusionReceipt",
]
