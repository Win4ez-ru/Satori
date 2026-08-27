"""Bounded untrusted current-model projections for conversation context."""

import json
from dataclasses import dataclass
from datetime import datetime

from satori.core.models import ModelEpistemicKind, ModelScalar, ModelValueKind
from satori.domain.validation import aware_utc, non_blank, positive_version, unit_interval

CURRENT_MODELS_CONTEXT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CurrentModelContextClaim:
    claim_id: str
    owner: str
    epistemic_kind: ModelEpistemicKind
    predicate: str
    value_kind: ModelValueKind
    value: ModelScalar
    confidence: float
    valid_from: datetime
    subject_kind: str | None = None
    subject_label: str | None = None

    def __post_init__(self) -> None:
        for name in ("claim_id", "owner", "predicate"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        if self.owner not in {"user", "world"}:
            raise ValueError("current model context owner is not supported")
        if self.owner == "world":
            if self.subject_kind is None or self.subject_label is None:
                raise ValueError("world context claim requires a subject")
            object.__setattr__(
                self,
                "subject_kind",
                non_blank(self.subject_kind, "subject_kind", maximum=32),
            )
            object.__setattr__(
                self,
                "subject_label",
                non_blank(self.subject_label, "subject_label", maximum=120),
            )
        elif self.subject_kind is not None or self.subject_label is not None:
            raise ValueError("user context claim cannot contain a world subject")
        unit_interval(self.confidence, "current model context confidence")
        object.__setattr__(self, "valid_from", aware_utc(self.valid_from, "valid_from"))


@dataclass(frozen=True, slots=True)
class CurrentModelsContext:
    schema_version: int
    status: str
    as_of: datetime
    user_claims: tuple[CurrentModelContextClaim, ...]
    world_claims: tuple[CurrentModelContextClaim, ...]
    excluded_claim_count: int

    def __post_init__(self) -> None:
        positive_version(self.schema_version, "current models context schema_version")
        if self.status not in {"available", "empty"}:
            raise ValueError("current models context status is not supported")
        object.__setattr__(self, "as_of", aware_utc(self.as_of, "as_of"))
        object.__setattr__(self, "user_claims", tuple(self.user_claims))
        object.__setattr__(self, "world_claims", tuple(self.world_claims))
        if self.excluded_claim_count < 0:
            raise ValueError("excluded_claim_count must be non-negative")
        all_claims = (*self.user_claims, *self.world_claims)
        ids = tuple(item.claim_id for item in all_claims)
        if len(ids) != len(set(ids)):
            raise ValueError("current models context claim IDs must be unique")
        if self.status == "empty" and all_claims:
            raise ValueError("empty current models context cannot contain claims")
        if self.status == "available" and not all_claims:
            raise ValueError("available current models context requires claims")

    @property
    def grounding_ids(self) -> tuple[str, ...]:
        return tuple(item.claim_id for item in (*self.user_claims, *self.world_claims))

    @property
    def user_claim_ids(self) -> tuple[str, ...]:
        return tuple(item.claim_id for item in self.user_claims)

    @property
    def world_claim_ids(self) -> tuple[str, ...]:
        return tuple(item.claim_id for item in self.world_claims)


def current_models_context_json(context: CurrentModelsContext) -> str:
    """Serialize only bounded claim values and metadata, never source-message content."""

    def claim_payload(claim: CurrentModelContextClaim) -> dict[str, object]:
        return {
            "claim_id": claim.claim_id,
            "owner": claim.owner,
            "subject_kind": claim.subject_kind,
            "subject_label": claim.subject_label,
            "predicate": claim.predicate,
            "value_kind": claim.value_kind.value,
            "value": claim.value,
            "epistemic_kind": claim.epistemic_kind.value,
            "confidence": claim.confidence,
            "valid_from": claim.valid_from.isoformat(),
        }

    return json.dumps(
        {
            "schema_version": context.schema_version,
            "status": context.status,
            "as_of": context.as_of.isoformat(),
            "user_claims": [claim_payload(item) for item in context.user_claims],
            "world_claims": [claim_payload(item) for item in context.world_claims],
            "excluded_claim_count": context.excluded_claim_count,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
