"""Read-only Stage 2 personality aggregate."""

from dataclasses import dataclass

from satori.domain.validation import positive_version, state_key, unit_interval


@dataclass(frozen=True, slots=True)
class PersonalityTrait:
    """One validated current trait and its activation baseline."""

    key: str
    value: float
    baseline_value: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", state_key(self.key, "trait key"))
        unit_interval(self.value, f"trait {self.key} value")
        unit_interval(self.baseline_value, f"trait {self.key} baseline_value")


@dataclass(frozen=True, slots=True)
class Personality:
    """Versioned immutable trait collection for the active identity."""

    schema_version: int
    aggregate_version: int
    traits: tuple[PersonalityTrait, ...]

    def __post_init__(self) -> None:
        positive_version(self.schema_version, "personality schema_version")
        positive_version(self.aggregate_version, "personality aggregate_version")
        if not self.traits:
            raise ValueError("personality traits must not be empty")
        ordered = tuple(sorted(self.traits, key=lambda trait: trait.key))
        keys = tuple(trait.key for trait in ordered)
        if len(keys) != len(set(keys)):
            raise ValueError("personality trait keys must be unique")
        object.__setattr__(self, "traits", ordered)

    def trait(self, key: str) -> PersonalityTrait:
        """Read one trait without exposing mutation access."""

        normalized = state_key(key, "trait key")
        for trait in self.traits:
            if trait.key == normalized:
                return trait
        raise KeyError(normalized)
