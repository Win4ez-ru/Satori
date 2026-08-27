"""Pydantic boundary from versioned JSON resources to domain seed input."""

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from satori.domain.errors import InvalidSeed, UnsupportedSeedVersion
from satori.domain.initial_self import InitialSatoriSeed, SeedTrait, SeedValue

SUPPORTED_SEED_SCHEMA_VERSIONS = (1,)
CANONICAL_V1_TRAITS = frozenset(
    {
        "curiosity",
        "analytical_thinking",
        "openness",
        "empathy",
        "emotional_sensitivity",
        "warmth",
        "independence",
        "assertiveness",
        "self_confidence",
        "playfulness",
        "humor",
        "irony",
        "patience",
        "optimism",
        "impulsivity",
    }
)
CANONICAL_V1_VALUES = frozenset(
    {
        "curiosity",
        "truth",
        "intellectual_honesty",
        "growth",
        "autonomy",
        "creativity",
        "competence",
        "connection",
        "compassion",
    }
)
KEY_PATTERN = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
SEED_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,127}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        strict=True,
        str_strip_whitespace=True,
    )


class _IdentityDocument(_StrictModel):
    name: str = Field(min_length=1, max_length=100)


class _TraitDocument(_StrictModel):
    key: str = Field(pattern=KEY_PATTERN, max_length=64)
    value: float = Field(ge=0.0, le=1.0)


class _PersonalityDocument(_StrictModel):
    schema_version: int = Field(ge=1)
    traits: tuple[_TraitDocument, ...] = Field(min_length=1)

    @field_validator("traits", mode="before")
    @classmethod
    def accept_json_array(cls, value: object) -> object:
        if not isinstance(value, list):
            raise ValueError("personality traits must be a JSON array")
        return tuple(value)

    @model_validator(mode="after")
    def validate_v1_trait_set(self) -> Self:
        keys = tuple(trait.key for trait in self.traits)
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate trait keys")
        if self.schema_version != 1:
            raise ValueError("unsupported personality schema version")
        if set(keys) != CANONICAL_V1_TRAITS:
            raise ValueError("personality v1 must contain exactly the canonical trait keys")
        return self


class _ValueDocument(_StrictModel):
    key: str = Field(pattern=KEY_PATTERN, max_length=64)
    strength: float = Field(ge=0.0, le=1.0)
    description: str = Field(min_length=1, max_length=500)


class _ValuesDocument(_StrictModel):
    schema_version: int = Field(ge=1)
    items: tuple[_ValueDocument, ...] = Field(min_length=1)

    @field_validator("items", mode="before")
    @classmethod
    def accept_json_array(cls, value: object) -> object:
        if not isinstance(value, list):
            raise ValueError("values items must be a JSON array")
        return tuple(value)

    @model_validator(mode="after")
    def validate_v1_value_set(self) -> Self:
        keys = tuple(item.key for item in self.items)
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate value keys")
        if self.schema_version != 1:
            raise ValueError("unsupported values schema version")
        if set(keys) != CANONICAL_V1_VALUES:
            raise ValueError("values v1 must contain exactly the canonical value keys")
        return self


class _SeedDocument(_StrictModel):
    schema_version: int
    seed_id: str = Field(pattern=SEED_ID_PATTERN)
    identity: _IdentityDocument
    personality: _PersonalityDocument
    values: _ValuesDocument


class JsonSeedLoader:
    """Load trusted package or future external JSON only after strict validation."""

    def load_canonical(self) -> InitialSatoriSeed:
        """Load the package's canonical Satori v1 seed."""

        resource = resources.files("satori.resources.seeds").joinpath("satori-v1.json")
        return self.loads(resource.read_text(encoding="utf-8"))

    def load_path(self, path: Path) -> InitialSatoriSeed:
        """Load a seed path through the same untrusted-input validation boundary."""

        try:
            content = path.expanduser().resolve().read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise InvalidSeed(f"cannot read seed: {error}") from error
        return self.loads(content)

    def loads(self, content: str) -> InitialSatoriSeed:
        """Parse, version-check, validate, canonicalize, hash, and map JSON."""

        try:
            raw: object = json.loads(content)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise InvalidSeed(f"malformed seed JSON: {error}") from error
        if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
            raise InvalidSeed("seed document must be a JSON object with string keys")
        payload = cast(dict[str, object], raw)
        schema_version = payload.get("schema_version")
        if type(schema_version) is not int or schema_version not in SUPPORTED_SEED_SCHEMA_VERSIONS:
            raise UnsupportedSeedVersion(schema_version, SUPPORTED_SEED_SCHEMA_VERSIONS)
        try:
            document = _SeedDocument.model_validate(payload)
        except ValidationError as error:
            raise InvalidSeed(f"seed validation failed: {error}") from error

        canonical = json.dumps(
            document.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        content_hash = hashlib.sha256(canonical).hexdigest()
        try:
            return InitialSatoriSeed(
                schema_version=document.schema_version,
                seed_id=document.seed_id,
                content_hash=content_hash,
                identity_name=document.identity.name,
                personality_schema_version=document.personality.schema_version,
                traits=tuple(
                    SeedTrait(key=trait.key, value=trait.value)
                    for trait in document.personality.traits
                ),
                values_schema_version=document.values.schema_version,
                values=tuple(
                    SeedValue(
                        key=value.key,
                        strength=value.strength,
                        description=value.description,
                    )
                    for value in document.values.items
                ),
            )
        except ValueError as error:
            raise InvalidSeed(f"seed domain validation failed: {error}") from error
