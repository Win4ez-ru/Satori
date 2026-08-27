"""Minimal auditable activation lifecycle event."""

from dataclasses import dataclass
from datetime import datetime

from satori.domain.identity import SeedProvenance
from satori.domain.validation import aware_utc, non_blank, positive_version


@dataclass(frozen=True, slots=True)
class ActivationAuditEvent:
    """Record the first persistent mutation without creating a large audit subsystem."""

    event_id: str
    schema_version: int
    identity_id: str
    occurred_at: datetime
    trace_id: str
    seed_provenance: SeedProvenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", non_blank(self.event_id, "event_id", maximum=128))
        positive_version(self.schema_version, "audit schema_version")
        object.__setattr__(
            self,
            "identity_id",
            non_blank(self.identity_id, "identity_id", maximum=128),
        )
        object.__setattr__(self, "occurred_at", aware_utc(self.occurred_at, "occurred_at"))
        object.__setattr__(self, "trace_id", non_blank(self.trace_id, "trace_id", maximum=128))
