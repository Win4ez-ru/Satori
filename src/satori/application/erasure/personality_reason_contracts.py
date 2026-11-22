"""Strict local review of identity-owned operator reason bodies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from satori.domain.erasure_personality_reasons import (
    MAX_CHECKPOINT_REASON_TARGETS,
    CheckpointReasonScope,
    CheckpointReasonSelector,
)

CHECKPOINT_REASON_OWNERS = ("personality",)
CHECKPOINT_REASON_RESIDUALS = (
    "other_owner_bodies_not_selected_by_this_operation",
    "legacy_audit_unknown_and_unsupported_scalar_codes",
    "identifiers_hashes_numeric_history_and_influence",
    "sqlite_free_pages_and_sidecars",
    "archives_staging_os_snapshots",
    "provider_caches_logs_external_copies",
)


class CheckpointReasonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    identity_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    selectors: tuple[CheckpointReasonSelector, ...] = Field(
        default=(), max_length=MAX_CHECKPOINT_REASON_TARGETS
    )

    @field_validator("selectors")
    @classmethod
    def exact_selectors(
        cls, value: tuple[CheckpointReasonSelector, ...]
    ) -> tuple[CheckpointReasonSelector, ...]:
        keys = tuple(item.key for item in value)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("checkpoint_reason_selectors_must_be_unique_and_ordered")
        return value


class CheckpointReasonCommand(CheckpointReasonRequest):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    expected_generation: int = Field(ge=0)
    expected_erasure_revision: int = Field(ge=0)
    expected_body_revision: int = Field(ge=0)
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class _LogicalDisposition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False

    @field_validator("physical_erasure", "execution_authorized", mode="before")
    @classmethod
    def deny_physical_claim(cls, value: object) -> Literal[False]:
        if value is not False:
            raise ValueError("checkpoint_reason_disposition_is_not_physical_erasure")
        return False


class CheckpointReasonPreview(_LogicalDisposition):
    scope: CheckpointReasonScope
    generation: int
    erasure_revision: int
    body_revision: int
    approval_digest: str
    scanned_rows: int
    approval_reasons: int
    restore_reasons: int
    metadata_references: int
    already_erased: int
    readiness: Literal["ready", "blocked"]
    blockers: tuple[str, ...]
    operation: Literal["erase_checkpoint_reasons"] = "erase_checkpoint_reasons"
    owners: tuple[str, ...] = CHECKPOINT_REASON_OWNERS
    residual_surfaces: tuple[str, ...] = CHECKPOINT_REASON_RESIDUALS


class CheckpointReasonResult(_LogicalDisposition):
    scope: CheckpointReasonScope
    request_id: str
    action_id: str
    body_revision: int
    main_projection_current: bool
    materialized_body_revision: int
    effective_body_head_digest: str
    selected_bodies_removed: bool
    phase: Literal["body_application_pending", "body_application_complete"]
    failure_reason: str | None = None
    owners: tuple[str, ...] = CHECKPOINT_REASON_OWNERS
    residual_surfaces: tuple[str, ...] = CHECKPOINT_REASON_RESIDUALS
