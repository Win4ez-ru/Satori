"""Immutable existing-owner retention assessments, never a content mutation plan."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum

from satori.domain.erasure import ErasureDisposition, ErasureInventory
from satori.domain.erasure_intent import ErasureIntent
from satori.domain.validation import aware_utc, sha256_hex

ERASURE_OWNER_CONTRACT_VERSION = 1


class ErasurePreparationOwner(StrEnum):
    INTERACTION_LOG = "interaction_log"
    MEMORY_MANAGER = "memory_manager"
    RETRIEVAL_INDEX = "retrieval_index"
    SEMANTIC_MEMORY = "semantic_memory"
    CURRENT_MODELS = "current_models"
    POSITIONS = "positions"
    REFLECTION = "reflection"
    AFFECT = "affect"
    RELATIONSHIP = "relationship"
    PERSONALITY = "personality"
    AUDIT = "audit"
    INITIAL_SELF = "initial_self"
    INITIAL_VALUES = "initial_values"
    SOURCE_POLICY = "source_policy"
    SCHEMA_REGISTRY = "schema_registry"


class ErasureBodyReadiness(StrEnum):
    RETAIN_ONLY = "retain_only"
    BLOCKED_EXECUTOR = "blocked_executor"
    BLOCKED_OWNER_POLICY = "blocked_owner_policy"
    BLOCKED_PROVENANCE = "blocked_provenance"


_REASONS = frozenset(
    {
        "owner_retained_metadata_only",
        "owner_content_executor_not_implemented",
        "owner_retention_policy_not_implemented",
        "owner_content_provenance_incomplete",
    }
)


@dataclass(frozen=True, slots=True)
class ErasureOwnerAssessment:
    owner: ErasurePreparationOwner
    body_readiness: ErasureBodyReadiness
    reason_codes: tuple[str, ...]
    content_rows: int
    numeric_rows: int
    metadata_rows: int
    uncertain_rows: int
    contract_version: int = ERASURE_OWNER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.owner, ErasurePreparationOwner) or not isinstance(
            self.body_readiness, ErasureBodyReadiness
        ):
            raise ValueError("invalid_erasure_owner_contract")
        if (
            type(self.contract_version) is not int
            or self.contract_version != ERASURE_OWNER_CONTRACT_VERSION
        ):
            raise ValueError("unsupported_erasure_owner_contract")
        for field in ("content_rows", "numeric_rows", "metadata_rows", "uncertain_rows"):
            if type(getattr(self, field)) is not int or getattr(self, field) < 0:
                raise ValueError("invalid_erasure_preparation_count")
        reasons = tuple(self.reason_codes)
        if not reasons or len(set(reasons)) != len(reasons) or not set(reasons).issubset(_REASONS):
            raise ValueError("invalid_erasure_preparation_reason")
        object.__setattr__(self, "reason_codes", reasons)
        if self.body_readiness is ErasureBodyReadiness.RETAIN_ONLY and (
            self.content_rows or self.uncertain_rows
        ):
            raise ValueError("erasure_content_cannot_be_retain_only")
        expected_reason = {
            ErasureBodyReadiness.RETAIN_ONLY: "owner_retained_metadata_only",
            ErasureBodyReadiness.BLOCKED_EXECUTOR: "owner_content_executor_not_implemented",
            ErasureBodyReadiness.BLOCKED_OWNER_POLICY: "owner_retention_policy_not_implemented",
            ErasureBodyReadiness.BLOCKED_PROVENANCE: "owner_content_provenance_incomplete",
        }[self.body_readiness]
        if reasons != (expected_reason,):
            raise ValueError("erasure_preparation_reason_status_mismatch")
        if self.uncertain_rows > self.content_rows:
            raise ValueError("invalid_erasure_preparation_count")
        if (
            self.body_readiness is ErasureBodyReadiness.BLOCKED_PROVENANCE
            and not self.uncertain_rows
        ):
            raise ValueError("erasure_provenance_blocker_requires_uncertain_content")


@dataclass(frozen=True, slots=True)
class ErasureOwnerPreparation:
    intent: ErasureIntent
    source_generation: int
    schema_revision: str
    schema_digest: str
    store_digest: str
    routing_digest: str
    retention_digest: str
    assessment_status: str
    assessments: tuple[ErasureOwnerAssessment, ...]
    prepared_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.intent, ErasureIntent):
            raise ValueError("invalid_erasure_preparation_intent")
        if (
            type(self.source_generation) is not int
            or self.source_generation < self.intent.expected_source_generation
        ):
            raise ValueError("invalid_erasure_preparation_generation")
        for field in ("schema_digest", "store_digest", "routing_digest", "retention_digest"):
            sha256_hex(getattr(self, field), field)
        if self.assessment_status not in {"complete", "blocked"} or not self.schema_revision:
            raise ValueError("invalid_erasure_preparation_assessment")
        assessments = tuple(self.assessments)
        if any(not isinstance(item, ErasureOwnerAssessment) for item in assessments):
            raise ValueError("invalid_erasure_preparation_owner")
        if len(assessments) != len(ErasurePreparationOwner) or {
            item.owner for item in assessments
        } != set(ErasurePreparationOwner):
            raise ValueError("erasure_preparation_owner_coverage_incomplete")
        object.__setattr__(
            self, "assessments", tuple(sorted(assessments, key=lambda item: item.owner))
        )
        object.__setattr__(self, "prepared_at", aware_utc(self.prepared_at, "prepared_at"))

    @property
    def preparation_digest(self) -> str:
        """Time/trace-free deterministic binding; refreshes append, retries do not."""
        intent = asdict(self.intent)
        intent.pop("decided_at")
        intent.pop("trace_id")
        material = {
            "intent": intent,
            "source_generation": self.source_generation,
            "schema_revision": self.schema_revision,
            "schema_digest": self.schema_digest,
            # The full-store/routing fingerprints include our own projection rows.
            # Retain them as evidence, but use retention_digest for replay freshness.
            "retention_digest": self.retention_digest,
            "assessment_status": self.assessment_status,
            "assessments": [asdict(item) for item in self.assessments],
        }
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()


def prepare_owner_contracts(
    intent: ErasureIntent,
    inventory: ErasureInventory,
    *,
    source_generation: int,
    prepared_at: datetime,
) -> ErasureOwnerPreparation:
    """Counts describe owner requirements; no table selectors or write capability exist."""
    if inventory.root != intent.root:
        raise ValueError("erasure_preparation_target_mismatch")
    owners = {ErasurePreparationOwner(route.owner) for route in inventory.routes}
    if owners != set(ErasurePreparationOwner):
        raise ValueError("erasure_preparation_owner_coverage_incomplete")
    assessments = []
    for owner in ErasurePreparationOwner:
        routes = tuple(
            route
            for route in inventory.routes
            if route.owner == owner.value
            and route.table not in {"erasure_policy_bindings", "erasure_preparations"}
        )
        content = sum(
            route.rows
            for route in routes
            if route.disposition
            in {
                ErasureDisposition.REDACT_CONTENT,
                ErasureDisposition.REMOVE_INDEX,
                ErasureDisposition.REVIEW_UNCERTAIN,
            }
        )
        uncertain = sum(route.uncertain_rows for route in routes)
        numeric = sum(
            route.rows for route in routes if route.disposition is ErasureDisposition.RETAIN_NUMERIC
        )
        metadata = sum(
            route.rows
            for route in routes
            if route.disposition is ErasureDisposition.RETAIN_METADATA
        )
        if uncertain:
            readiness = ErasureBodyReadiness.BLOCKED_PROVENANCE
            reasons = ("owner_content_provenance_incomplete",)
        elif content:
            readiness = ErasureBodyReadiness.BLOCKED_EXECUTOR
            reasons = ("owner_content_executor_not_implemented",)
        else:
            readiness = ErasureBodyReadiness.RETAIN_ONLY
            reasons = ("owner_retained_metadata_only",)
        assessments.append(
            ErasureOwnerAssessment(owner, readiness, reasons, content, numeric, metadata, uncertain)
        )
    return ErasureOwnerPreparation(
        intent,
        source_generation,
        inventory.schema_revision,
        inventory.schema_digest,
        inventory.store_digest,
        inventory.routing_digest,
        inventory.retention_digest,
        "blocked" if inventory.blockers else "complete",
        tuple(assessments),
        prepared_at,
    )
