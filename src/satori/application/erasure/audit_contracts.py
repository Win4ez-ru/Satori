"""Scoped, content-free audit retention coverage; never execution permission."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from satori.application.erasure.contracts import ErasurePreviewRequest
from satori.domain.audit_metadata import AuditMetadataFamily, AuditMetadataUnknownReason


class AuditMetadataStatusRequest(ErasurePreviewRequest):
    """The canonical root proves scope before private audit census."""


class AuditFamilyCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    family: AuditMetadataFamily
    certified_metadata_events: int = Field(ge=0)
    owned_content_events: int = Field(ge=0)
    unknown_events: int = Field(ge=0)


class AuditMetadataStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract_version: Literal[1] = 1
    schema_revision: str
    generation: int = Field(ge=0)
    erasure_revision: int = Field(ge=0)
    body_revision: int = Field(ge=0)
    scanned_rows: int = Field(ge=0)
    total_events: int = Field(ge=0)
    total_bindings: int = Field(ge=0)
    unknown_binding_rows: int = Field(ge=0)
    certified_metadata_events: int = Field(ge=0)
    owned_content_events: int = Field(ge=0)
    unknown_events: int = Field(ge=0)
    coverage: Literal["complete", "partial"]
    counts_by_family: tuple[AuditFamilyCoverage, ...]
    safe_reason_codes: tuple[AuditMetadataUnknownReason, ...]
    inventory_scope: Literal["current_main_database"] = "current_main_database"
    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False
    automatic_processing: Literal[False] = False
