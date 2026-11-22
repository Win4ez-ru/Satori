"""Explicit owner adoption of current files, never proof of an original volume."""

import hashlib
import json
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class RecoveryFileReview(BaseModel):
    """Fresh byte/stat binding; hashes disclose no protected payload bodies."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    device: int = Field(ge=0)
    inode: int = Field(ge=0)
    size: int = Field(ge=0)
    modified_ns: int = Field(ge=0)
    changed_ns: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DeviceBindingRecoveryPlan(BaseModel):
    """Content-free, deterministic review of exactly the generation-zero slice."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format: Literal["satori-device-binding-adoption-v1"] = "satori-device-binding-adoption-v1"
    owner_adoption_required: Literal[True] = True
    original_volume_proven: Literal[False] = False
    offline_runtime_required: Literal[True] = True
    policy_directory: str
    canonical_database: str
    policy_directory_inode: int = Field(ge=0)
    canonical_directory_inode: int = Field(ge=0)
    previous_device: int = Field(ge=0)
    current_device: int = Field(ge=0)
    owner_uid: int = Field(ge=0)
    descriptor: RecoveryFileReview
    floor: RecoveryFileReview
    lock: RecoveryFileReview
    canonical: RecoveryFileReview
    identity_id: str = Field(min_length=1, max_length=128)
    installation_id: str = Field(min_length=1, max_length=128)
    floor_id: str = Field(min_length=1, max_length=128)
    lineage_id: str = Field(min_length=1, max_length=128)
    generation: Literal[0] = 0
    erasure_revision: Literal[0] = 0
    canonical_revision: Literal["0024_checkpoint_reasons"] = "0024_checkpoint_reasons"
    policy_revision: Literal["policy_0002"] = "policy_0002"
    canonical_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adopted_descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def computed_digest(self) -> str:
        material = self.model_dump(mode="json", exclude={"review_sha256"})
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()


class AdoptCurrentBindingFiles(BaseModel):
    """These explicit declarations are operator decisions, not authentication."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    plan: DeviceBindingRecoveryPlan
    expected_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{7,127}$")
    owner_adopts_current_files: Literal[True]
    runtime_confirmed_offline: Literal[True]


type BindingAdoptionStatus = Literal[
    "adopted", "adopted_audit_pending", "not_applied_review_consumed", "outcome_unknown"
]


class DeviceBindingRecoveryReceipt(BaseModel):
    """A consumed review remains consumed after failure; no blind retry/rollback."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format: Literal["satori-device-binding-adoption-receipt-v1"] = (
        "satori-device-binding-adoption-receipt-v1"
    )
    request_id: str
    review_sha256: str
    owner_uid: int = Field(ge=0)
    status: BindingAdoptionStatus
    owner_adoption: Literal[True] = True
    original_volume_proven: Literal[False] = False
    review_consumed: Literal[True] = True
    descriptor_devices_updated: bool
    canonical_floor_lock_unchanged: bool
    normal_binding_valid: bool
    automatic_processing: Literal[False] = False
    backup_path: str
    claim_path: str
    receipt_path: str
    expected_adopted_descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_descriptor_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class DeviceBindingRecoveryPort(Protocol):
    """The existing ledger owner remains the only descriptor writer."""

    def preview(self) -> DeviceBindingRecoveryPlan: ...

    def adopt(self, command: AdoptCurrentBindingFiles) -> DeviceBindingRecoveryReceipt: ...
