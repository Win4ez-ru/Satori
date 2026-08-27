"""Provider-neutral typed contracts for bounded personality evolution."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

type PersonalityTraitKey = Literal[
    "analytical_thinking",
    "assertiveness",
    "curiosity",
    "emotional_sensitivity",
    "empathy",
    "humor",
    "impulsivity",
    "independence",
    "irony",
    "openness",
    "optimism",
    "patience",
    "playfulness",
    "self_confidence",
    "warmth",
]

CANONICAL_TRAIT_KEYS: tuple[PersonalityTraitKey, ...] = (
    "analytical_thinking",
    "assertiveness",
    "curiosity",
    "emotional_sensitivity",
    "empathy",
    "humor",
    "impulsivity",
    "independence",
    "irony",
    "openness",
    "optimism",
    "patience",
    "playfulness",
    "self_confidence",
    "warmth",
)


class _StrictContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


class PersonalityDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"


class PersonalityCitationRole(StrEnum):
    SUPPORT = "support"
    COUNTEREVIDENCE = "counterevidence"


class PersonalityCitation(_StrictContract):
    """One opaque citation from a fixed personality-purpose reflection set."""

    source_id: str = Field(min_length=1, max_length=128)
    role: PersonalityCitationRole


class PersonalityStateReference(_StrictContract):
    """Target identity/version without exposing current or baseline trait values."""

    identity_id: str = Field(min_length=1, max_length=128)
    aggregate_version: int = Field(ge=1)
    canonical_trait_keys: tuple[PersonalityTraitKey, ...] = CANONICAL_TRAIT_KEYS

    @model_validator(mode="after")
    def exact_canonical_keys(self) -> "PersonalityStateReference":
        if self.canonical_trait_keys != CANONICAL_TRAIT_KEYS:
            raise ValueError("canonical_trait_keys must be the exact ordered Stage 14 set")
        return self


class PersonalityChangeProposal(_StrictContract):
    """Untrusted semantic direction with no provider-owned magnitude or state patch."""

    trait_key: PersonalityTraitKey
    direction: PersonalityDirection
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    citations: tuple[PersonalityCitation, ...] = Field(min_length=8, max_length=12)
    expected_personality_version: int = Field(ge=1)

    @model_validator(mode="after")
    def unique_citations(self) -> "PersonalityChangeProposal":
        source_ids = tuple(item.source_id for item in self.citations)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("personality proposal citations must be unique")
        return self


class PersonalityRestoreProposal(_StrictContract):
    """Explicit local restore request; it is never produced by reflection or a provider."""

    checkpoint_id: str = Field(min_length=1, max_length=128)
    checkpoint_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_personality_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=240)
