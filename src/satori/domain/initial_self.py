"""Activation seed and immutable initial-self snapshot contracts."""

from dataclasses import dataclass
from datetime import datetime

from satori.domain.identity import Identity, SeedProvenance
from satori.domain.personality import Personality, PersonalityTrait
from satori.domain.validation import non_blank, positive_version, sha256_hex, state_key
from satori.domain.values import CoreValue, ValueOrigin, Values

INITIAL_SELF_SNAPSHOT_SCHEMA_VERSION = 1
IDENTITY_SCHEMA_VERSION = 1
INITIAL_AGGREGATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class SeedTrait:
    """One validated trait input from a seed boundary."""

    key: str
    value: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", state_key(self.key, "seed trait key"))
        PersonalityTrait(self.key, self.value, self.value)


@dataclass(frozen=True, slots=True)
class SeedValue:
    """One validated value input from a seed boundary."""

    key: str
    strength: float
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", state_key(self.key, "seed value key"))
        CoreValue(self.key, self.strength, self.description, ValueOrigin.INITIAL_SEED)


@dataclass(frozen=True, slots=True)
class InitialSatoriSeed:
    """Validated typed input to explicit activation."""

    schema_version: int
    seed_id: str
    content_hash: str
    identity_name: str
    personality_schema_version: int
    traits: tuple[SeedTrait, ...]
    values_schema_version: int
    values: tuple[SeedValue, ...]

    def __post_init__(self) -> None:
        positive_version(self.schema_version, "seed schema_version")
        object.__setattr__(self, "seed_id", non_blank(self.seed_id, "seed_id", maximum=128))
        object.__setattr__(self, "content_hash", sha256_hex(self.content_hash))
        object.__setattr__(
            self,
            "identity_name",
            non_blank(self.identity_name, "identity name", maximum=100),
        )
        positive_version(self.personality_schema_version, "personality schema_version")
        positive_version(self.values_schema_version, "values schema_version")
        self._validate_unique_non_empty(self.traits, "trait")
        self._validate_unique_non_empty(self.values, "value")
        object.__setattr__(self, "traits", tuple(sorted(self.traits, key=lambda item: item.key)))
        object.__setattr__(self, "values", tuple(sorted(self.values, key=lambda item: item.key)))

    @staticmethod
    def _validate_unique_non_empty(
        items: tuple[SeedTrait, ...] | tuple[SeedValue, ...], label: str
    ) -> None:
        if not items:
            raise ValueError(f"seed {label}s must not be empty")
        keys = tuple(item.key for item in items)
        if len(keys) != len(set(keys)):
            raise ValueError(f"seed {label} keys must be unique")


@dataclass(frozen=True, slots=True)
class InitialSelfSnapshot:
    """Versioned read-only view of all state that exists after Stage 2 activation."""

    schema_version: int
    identity: Identity
    personality: Personality
    values: Values

    def __post_init__(self) -> None:
        positive_version(self.schema_version, "self snapshot schema_version")


def activate_from_seed(
    seed: InitialSatoriSeed,
    *,
    identity_id: str,
    activation_time: datetime,
) -> InitialSelfSnapshot:
    """Construct the initial persistent self; this is not a general mutation API."""

    provenance = SeedProvenance(
        seed_id=seed.seed_id,
        seed_schema_version=seed.schema_version,
        seed_content_hash=seed.content_hash,
    )
    identity = Identity(
        identity_id=identity_id,
        name=seed.identity_name,
        activation_time=activation_time,
        identity_version=IDENTITY_SCHEMA_VERSION,
        seed_provenance=provenance,
    )
    personality = Personality(
        schema_version=seed.personality_schema_version,
        aggregate_version=INITIAL_AGGREGATE_VERSION,
        traits=tuple(
            PersonalityTrait(key=trait.key, value=trait.value, baseline_value=trait.value)
            for trait in seed.traits
        ),
    )
    values = Values(
        schema_version=seed.values_schema_version,
        aggregate_version=INITIAL_AGGREGATE_VERSION,
        items=tuple(
            CoreValue(
                key=value.key,
                strength=value.strength,
                description=value.description,
                origin=ValueOrigin.INITIAL_SEED,
            )
            for value in seed.values
        ),
    )
    return InitialSelfSnapshot(
        schema_version=INITIAL_SELF_SNAPSHOT_SCHEMA_VERSION,
        identity=identity,
        personality=personality,
        values=values,
    )
