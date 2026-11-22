"""Identity-scoped logical coverage; a current review is never execution permission."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from satori.domain.erasure_preparation import ErasurePreparationOwner
from satori.domain.retention import (
    RETENTION_POLICY_VERSION,
    RetentionReasonCount,
    RetentionTableCounts,
)


class RetentionStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    identity_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")


class RetentionCheckRequest(RetentionStatusRequest):
    expected_generation: int = Field(ge=0)
    expected_erasure_revision: int = Field(ge=0)
    expected_body_revision: int = Field(ge=0)
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RetentionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract_version: Literal[1] = 1
    policy_version: str = RETENTION_POLICY_VERSION
    source_policy_version: str
    identity_id: str
    lineage_id: str
    floor_id: str
    installation_id: str
    generation: int = Field(ge=0)
    erasure_revision: int = Field(ge=0)
    body_revision: int = Field(ge=0)
    body_head_digest: str
    schema_revision: str
    schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    store_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    classification_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scanned_rows: int = Field(ge=0, le=100_000)
    work_units: int = Field(ge=0, le=1_000_000)
    total_rows: int = Field(ge=0, le=100_000)
    supported_retained_rows: int = Field(ge=0)
    owned_content_rows: int = Field(ge=0)
    unknown_rows: int = Field(ge=0)
    table_counts: tuple[RetentionTableCounts, ...]
    reason_counts: tuple[RetentionReasonCount, ...]
    coverage: Literal["complete", "partial"]
    logical_readiness: Literal["retained_only", "owned_content_remaining", "blocked_unknown"]
    inventory_scope: Literal["current_main_database"] = "current_main_database"
    whole_head_current: Literal[True] = True
    external_retention: Literal["not_assessed"] = "not_assessed"
    unassessed_surfaces: tuple[str, ...]
    physical_readiness: Literal["blocked"] = "blocked"
    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False
    automatic_processing: Literal[False] = False
    historical_numeric_state_retained: Literal[True] = True

    @model_validator(mode="after")
    def exact_partition(self) -> "RetentionStatus":
        if self.policy_version != RETENTION_POLICY_VERSION:
            raise ValueError("unsupported_retention_policy")
        if self.scanned_rows != self.total_rows or (
            self.supported_retained_rows + self.owned_content_rows + self.unknown_rows
            != self.total_rows
        ):
            raise ValueError("retention_row_partition_incomplete")
        for field in (
            "total_rows",
            "supported_retained_rows",
            "owned_content_rows",
            "unknown_rows",
        ):
            if sum(getattr(item, field) for item in self.table_counts) != getattr(self, field):
                raise ValueError("retention_table_partition_incomplete")
        if len({item.table for item in self.table_counts}) != len(self.table_counts):
            raise ValueError("duplicate_retention_table")
        if {item.owner for item in self.table_counts} != set(ErasurePreparationOwner):
            raise ValueError("retention_owner_coverage_incomplete")
        expected_coverage = "partial" if self.unknown_rows else "complete"
        expected_readiness = (
            "blocked_unknown"
            if self.unknown_rows
            else "owned_content_remaining"
            if self.owned_content_rows
            else "retained_only"
        )
        if self.coverage != expected_coverage or self.logical_readiness != expected_readiness:
            raise ValueError("retention_readiness_partition_mismatch")
        return self


class RetentionCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    review_status: Literal["current"] = "current"
    review_digest: str
    generation: int = Field(ge=0)
    erasure_revision: int = Field(ge=0)
    body_revision: int = Field(ge=0)
    coverage: Literal["complete", "partial"]
    logical_readiness: Literal["retained_only", "owned_content_remaining", "blocked_unknown"]
    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False
