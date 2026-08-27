"""Persistent identity and seed provenance values."""

from dataclasses import dataclass
from datetime import datetime

from satori.domain.validation import aware_utc, non_blank, positive_version, sha256_hex


@dataclass(frozen=True, slots=True)
class SeedProvenance:
    """Describe the one-time seed from which an identity was activated."""

    seed_id: str
    seed_schema_version: int
    seed_content_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "seed_id", non_blank(self.seed_id, "seed_id", maximum=128))
        positive_version(self.seed_schema_version, "seed_schema_version")
        object.__setattr__(
            self,
            "seed_content_hash",
            sha256_hex(self.seed_content_hash, "seed_content_hash"),
        )


@dataclass(frozen=True, slots=True)
class Identity:
    """Stable identity independent from name, process, session, or provider."""

    identity_id: str
    name: str
    activation_time: datetime
    identity_version: int
    seed_provenance: SeedProvenance

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "identity_id",
            non_blank(self.identity_id, "identity_id", maximum=128),
        )
        object.__setattr__(self, "name", non_blank(self.name, "name", maximum=100))
        object.__setattr__(
            self,
            "activation_time",
            aware_utc(self.activation_time, "activation_time"),
        )
        positive_version(self.identity_version, "identity_version")
