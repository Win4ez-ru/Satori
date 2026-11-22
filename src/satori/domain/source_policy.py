"""Provider-independent contracts for terminal canonical-root exclusion."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.domain.validation import aware_utc, non_blank, positive_version, sha256_hex

SOURCE_POLICY_VERSION = "canonical_source_policy_v1"
MAX_FOOTPRINT_ROOTS = 512
MAX_FOOTPRINT_PAYLOADS = 128


class SourcePolicyError(RuntimeError):
    """A safe reason code; storage paths and protected content are never included."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = non_blank(reason_code, "reason_code", maximum=128)
        super().__init__(self.reason_code)


class FootprintStatus(StrEnum):
    COMPLETE = "complete"
    UNKNOWN = "unknown"
    OVERFLOW = "overflow"


@dataclass(frozen=True, slots=True)
class PayloadRef:
    kind: str
    payload_id: str
    version: int = 1

    def __post_init__(self) -> None:
        for name in ("kind", "payload_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        positive_version(self.version, "version")


ContentDependency = PayloadRef


@dataclass(frozen=True, slots=True)
class ProtectedContentFootprint:
    """Actual content dependencies, distinct from an owner's accepted evidence."""

    root_message_ids: tuple[str, ...] = ()
    payload_refs: tuple[PayloadRef, ...] = ()
    status: FootprintStatus = FootprintStatus.COMPLETE
    legacy: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, FootprintStatus) or type(self.legacy) is not bool:
            raise ValueError("invalid_source_footprint")
        roots = tuple(non_blank(root, "message_id", maximum=128) for root in self.root_message_ids)
        if len(set(roots)) != len(roots) or len(set(self.payload_refs)) != len(self.payload_refs):
            raise ValueError("duplicate_source_dependency")
        if any(not isinstance(ref, PayloadRef) for ref in self.payload_refs):
            raise ValueError("invalid_source_dependency")
        object.__setattr__(self, "root_message_ids", roots)
        object.__setattr__(self, "payload_refs", tuple(self.payload_refs))
        if len(roots) > MAX_FOOTPRINT_ROOTS or len(self.payload_refs) > MAX_FOOTPRINT_PAYLOADS:
            object.__setattr__(self, "status", FootprintStatus.OVERFLOW)


@dataclass(frozen=True, slots=True)
class CanonicalRootRef:
    """A canonical USER root proved by InteractionLog, never supplied by a provider."""

    lineage_id: str
    identity_id: str
    counterparty_id: str
    session_id: str
    interaction_id: str
    message_id: str

    def __post_init__(self) -> None:
        for name in (
            "lineage_id",
            "identity_id",
            "counterparty_id",
            "session_id",
            "interaction_id",
            "message_id",
        ):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))


@dataclass(frozen=True, slots=True)
class SourceEligibilitySnapshot:
    floor_id: str
    installation_id: str
    lineage_id: str
    identity_id: str
    generation: int
    excluded_message_ids: frozenset[str]
    policy_version: str = SOURCE_POLICY_VERSION
    erasure_revision: int = 0
    body_revision: int = 0
    body_head_digest: str = ""

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("invalid_policy_generation")
        if type(self.erasure_revision) is not int or self.erasure_revision < 0:
            raise ValueError("invalid_erasure_revision")
        if type(self.body_revision) is not int or self.body_revision < 0:
            raise ValueError("invalid_body_revision")
        if self.body_revision:
            sha256_hex(self.body_head_digest, "body_head_digest")
        elif self.body_head_digest:
            raise ValueError("invalid_empty_body_head")
        for name in ("floor_id", "installation_id", "lineage_id", "identity_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        object.__setattr__(self, "excluded_message_ids", frozenset(self.excluded_message_ids))


class ExclusionPhase(StrEnum):
    REPAIR_PENDING = "exclusion_committed_repair_pending"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class SourceExclusionReceipt:
    request_id: str
    decision_id: str
    root: CanonicalRootRef
    floor_id: str
    expected_generation: int
    result_generation: int
    preview_digest: str
    decided_at: datetime
    trace_id: str
    phase: ExclusionPhase = ExclusionPhase.REPAIR_PENDING
    reason_code: str = "canonical_source_excluded"
    approved_by: str = "local_operator"
    policy_version: str = SOURCE_POLICY_VERSION

    def __post_init__(self) -> None:
        for name in ("request_id", "decision_id", "floor_id", "trace_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        for value in (self.expected_generation, self.result_generation):
            if type(value) is not int or value < 0:
                raise ValueError("invalid_policy_generation")
        if self.result_generation not in {self.expected_generation, self.expected_generation + 1}:
            raise ValueError("invalid_policy_generation_transition")
        sha256_hex(self.preview_digest, "preview_digest")
        object.__setattr__(self, "decided_at", aware_utc(self.decided_at, "decided_at"))
        if self.approved_by != "local_operator" or not isinstance(self.phase, ExclusionPhase):
            raise ValueError("invalid_source_approval")

    @property
    def exclusion_committed(self) -> bool:
        return True

    @property
    def changed(self) -> bool:
        return self.result_generation > self.expected_generation


def exclusion_effects() -> dict[str, bool]:
    """Content-free product effects, stable across Character implementations."""

    return {
        "source_excluded": True,
        "ordinary_disclosure_allowed": False,
        "new_provider_input_allowed": False,
        "new_evidence_allowed": False,
        "canonical_history_retained": True,
        "historical_numeric_state_retained": True,
        "physical_erasure": False,
        "technical_permission_granted": False,
    }


def exclusion_preview_digest(root: CanonicalRootRef, snapshot: SourceEligibilitySnapshot) -> str:
    material = {
        "root": {
            name: getattr(root, name)
            for name in (
                "lineage_id",
                "identity_id",
                "counterparty_id",
                "session_id",
                "interaction_id",
                "message_id",
            )
        },
        "floor_id": snapshot.floor_id,
        "installation_id": snapshot.installation_id,
        "generation": snapshot.generation,
        "policy_version": SOURCE_POLICY_VERSION,
        "effects": exclusion_effects(),
    }
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
