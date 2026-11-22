"""Closed audit-accountability contracts; no certification from JSON field names."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from satori.domain.validation import non_blank, sha256_hex

AUDIT_METADATA_CONTRACT_VERSION = 1


class AuditMetadataFamily(StrEnum):
    """Existing owner events; Appraisal is classified by its separate paired proof."""

    ACTIVATION = "activation"
    EPISODE_FORMATION = "episode_formation"
    MEMORY_LIFECYCLE = "memory_lifecycle"
    SEMANTIC_FORMATION = "semantic_formation"
    USER_MODEL_FORMATION = "user_model_formation"
    WORLD_MODEL_FORMATION = "world_model_formation"
    POSITION_FORMATION = "position_formation"
    REFLECTION_POSITION = "reflection_position"
    REFLECTION_INCLINATION = "reflection_inclination"
    REFLECTION_PERSONALITY = "reflection_personality"
    REFLECTION_DISABLED = "reflection_disabled"
    RELATIONSHIP_TRANSITION = "relationship_transition"
    CHECKPOINT_APPROVAL = "checkpoint_approval"
    PERSONALITY_RESTORE = "personality_restore"
    SOURCE_WITHDRAWAL = "source_withdrawal"
    APPRAISAL_TRANSITION = "appraisal_transition"


AUDIT_METADATA_FIELDS_BY_FAMILY: Mapping[AuditMetadataFamily, str | None] = MappingProxyType(
    {
        AuditMetadataFamily.ACTIVATION: None,
        AuditMetadataFamily.EPISODE_FORMATION: "episode_decision_id",
        AuditMetadataFamily.MEMORY_LIFECYCLE: "lifecycle_decision_id",
        AuditMetadataFamily.SEMANTIC_FORMATION: "semantic_decision_id",
        AuditMetadataFamily.USER_MODEL_FORMATION: "model_decision_id",
        AuditMetadataFamily.WORLD_MODEL_FORMATION: "model_decision_id",
        AuditMetadataFamily.POSITION_FORMATION: "position_decision_id",
        AuditMetadataFamily.REFLECTION_POSITION: "reflection_outcome_id",
        AuditMetadataFamily.REFLECTION_INCLINATION: "reflection_outcome_id",
        AuditMetadataFamily.REFLECTION_PERSONALITY: "reflection_outcome_id",
        AuditMetadataFamily.REFLECTION_DISABLED: "reflection_outcome_id",
        AuditMetadataFamily.RELATIONSHIP_TRANSITION: "relationship_decision_id",
        AuditMetadataFamily.CHECKPOINT_APPROVAL: "checkpoint_approval_id",
        AuditMetadataFamily.PERSONALITY_RESTORE: "restore_event_id",
        AuditMetadataFamily.SOURCE_WITHDRAWAL: "withdrawal_receipt_id",
    }
)


@dataclass(frozen=True, slots=True)
class AuditBindingRef:
    """One closed owner receipt; activation references its identity itself."""

    family: AuditMetadataFamily
    owner_record_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.family, AuditMetadataFamily) or (
            self.family not in AUDIT_METADATA_FIELDS_BY_FAMILY
        ):
            raise ValueError("unsupported_audit_binding_family")
        object.__setattr__(
            self,
            "owner_record_id",
            non_blank(self.owner_record_id, "owner_record_id", maximum=128),
        )


@dataclass(frozen=True, slots=True)
class AuditMetadataBinding:
    """Prospective event fingerprint plus an exact owner reference, never audit prose."""

    event_id: str
    identity_id: str
    reference: AuditBindingRef
    event_digest: str
    source_input_id: str | None = None
    contract_version: int = AUDIT_METADATA_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("event_id", "identity_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        if not isinstance(self.reference, AuditBindingRef):
            raise ValueError("invalid_audit_binding_reference")
        if (
            type(self.contract_version) is not int
            or self.contract_version != AUDIT_METADATA_CONTRACT_VERSION
        ):
            raise ValueError("unsupported_audit_metadata_contract")
        sha256_hex(self.event_digest, "event_digest")
        if self.source_input_id is not None:
            object.__setattr__(
                self,
                "source_input_id",
                non_blank(self.source_input_id, "source_input_id", maximum=128),
            )
        if (
            self.reference.family is AuditMetadataFamily.ACTIVATION
            and self.reference.owner_record_id != self.identity_id
        ):
            raise ValueError("audit_activation_identity_mismatch")


class AuditMetadataCoverageStatus(StrEnum):
    CERTIFIED_METADATA = "certified_metadata"
    OWNED_CONTENT = "owned_content"
    UNKNOWN = "unknown"


class AuditMetadataUnknownReason(StrEnum):
    MISSING_BINDING = "missing_binding"
    UNSUPPORTED_CONTRACT = "unsupported_contract"
    UNSUPPORTED_FAMILY = "unsupported_family"
    UNSUPPORTED_EVENT_VERSION = "unsupported_event_version"
    INVALID_SHAPE = "invalid_shape"
    OWNER_PROOF_UNPROVEN = "owner_proof_unproven"
    SCOPE_UNPROVEN = "scope_unproven"
    DUPLICATE_EVENT = "duplicate_event"
    CONTENT_PRESENT = "content_present"
    TAMPERED_BINDING = "tampered_binding"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    APPRAISAL_UNPROVEN = "appraisal_unproven"


@dataclass(frozen=True, slots=True)
class AuditMetadataClassification:
    """Private census item; public coverage exposes counts, not unrelated event IDs."""

    event_id: str
    status: AuditMetadataCoverageStatus
    family: AuditMetadataFamily | None = None
    reason: AuditMetadataUnknownReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AuditMetadataCoverageStatus):
            raise ValueError("invalid_audit_metadata_status")
        if self.status is AuditMetadataCoverageStatus.UNKNOWN:
            # Preserve the exact untrusted census key privately. Invalid legacy
            # keys are UNKNOWN, not invented surrogate IDs or metadata grants.
            # The infrastructure census bounds total input bytes before loading.
            if type(self.event_id) is not str:
                raise ValueError("invalid_audit_event_key")
        else:
            object.__setattr__(self, "event_id", non_blank(self.event_id, "event_id", maximum=128))
        if self.family is not None and not isinstance(self.family, AuditMetadataFamily):
            raise ValueError("invalid_audit_metadata_family")
        if self.reason is not None and not isinstance(self.reason, AuditMetadataUnknownReason):
            raise ValueError("invalid_audit_metadata_reason")
        if self.status is AuditMetadataCoverageStatus.CERTIFIED_METADATA:
            if self.family is None or self.reason is not None:
                raise ValueError("invalid_certified_audit_metadata")
        elif self.status is AuditMetadataCoverageStatus.OWNED_CONTENT:
            if (
                self.family is not AuditMetadataFamily.APPRAISAL_TRANSITION
                or self.reason is not AuditMetadataUnknownReason.CONTENT_PRESENT
            ):
                raise ValueError("invalid_owned_audit_content")
        elif self.reason is None:
            raise ValueError("missing_unknown_audit_reason")


@dataclass(frozen=True, slots=True)
class AuditMetadataCoverage:
    """Complete bounded census partition, including preserved UNKNOWN legacy events."""

    total_events: int
    certified_metadata_events: int
    owned_content_events: int
    unknown_events: int

    def __post_init__(self) -> None:
        for name in (
            "total_events",
            "certified_metadata_events",
            "owned_content_events",
            "unknown_events",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError("invalid_audit_metadata_count")
        if self.total_events != (
            self.certified_metadata_events + self.owned_content_events + self.unknown_events
        ):
            raise ValueError("incomplete_audit_metadata_coverage")
