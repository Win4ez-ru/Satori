"""Read-only Stage 2 values aggregate."""

from dataclasses import dataclass
from enum import StrEnum

from satori.domain.validation import non_blank, positive_version, state_key, unit_interval


class ValueOrigin(StrEnum):
    """Known provenance kind for a value."""

    INITIAL_SEED = "initial_seed"


@dataclass(frozen=True, slots=True)
class CoreValue:
    """One named value with explicit initial salience and provenance."""

    key: str
    strength: float
    description: str
    origin: ValueOrigin

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", state_key(self.key, "value key"))
        unit_interval(self.strength, f"value {self.key} strength")
        object.__setattr__(
            self,
            "description",
            non_blank(self.description, f"value {self.key} description", maximum=500),
        )


@dataclass(frozen=True, slots=True)
class Values:
    """Versioned immutable value collection for the active identity."""

    schema_version: int
    aggregate_version: int
    items: tuple[CoreValue, ...]

    def __post_init__(self) -> None:
        positive_version(self.schema_version, "values schema_version")
        positive_version(self.aggregate_version, "values aggregate_version")
        if not self.items:
            raise ValueError("values must not be empty")
        ordered = tuple(sorted(self.items, key=lambda item: item.key))
        keys = tuple(item.key for item in ordered)
        if len(keys) != len(set(keys)):
            raise ValueError("value keys must be unique")
        object.__setattr__(self, "items", ordered)

    def value(self, key: str) -> CoreValue:
        """Read one value without exposing mutation access."""

        normalized = state_key(key, "value key")
        for item in self.items:
            if item.key == normalized:
                return item
        raise KeyError(normalized)
