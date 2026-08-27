"""Provider-neutral contracts for compact relationship-event appraisal."""

from dataclasses import dataclass
from datetime import datetime

from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.domain.validation import aware_utc, non_blank, positive_version, unit_interval


class RelationshipAppraisalProviderError(Exception):
    """A replaceable relationship classifier failed without owning domain state."""

    def __init__(self, provider: str, model: str, message: str) -> None:
        self.provider = provider
        self.model = model
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RelationshipAppraisalRequest:
    """One canonical user event and opaque allowed provenance handles."""

    schema_version: int
    interaction_id: str
    user_message_id: str
    user_content: str
    observed_at: datetime
    trace_id: str

    def __post_init__(self) -> None:
        positive_version(self.schema_version, "relationship appraisal schema_version")
        for name in ("interaction_id", "user_message_id", "trace_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        if not self.user_content.strip():
            raise ValueError("relationship appraisal user_content must not be blank")
        object.__setattr__(self, "observed_at", aware_utc(self.observed_at, "observed_at"))


@dataclass(frozen=True, slots=True)
class RelationshipAppraisalProposal:
    """Categorical evidence proposal; it can never set relationship dimensions."""

    schema_version: int
    categories: tuple[str, ...]
    confidence: float
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        positive_version(self.schema_version, "relationship appraisal schema_version")
        if not 1 <= len(self.categories) <= 3 or len(set(self.categories)) != len(self.categories):
            raise ValueError("relationship categories must contain one to three unique values")
        object.__setattr__(
            self,
            "categories",
            tuple(non_blank(item, "relationship category", maximum=64) for item in self.categories),
        )
        unit_interval(self.confidence, "relationship appraisal confidence")
        if not self.source_refs or len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError("relationship source_refs must be non-empty and unique")
        object.__setattr__(
            self,
            "source_refs",
            tuple(
                non_blank(item, "relationship source_ref", maximum=128) for item in self.source_refs
            ),
        )


@dataclass(frozen=True, slots=True)
class RelationshipAppraisalResponse:
    """Validated adapter response with metadata-only timing decomposition."""

    proposal: RelationshipAppraisalProposal
    provider: str
    model: str
    appraisal_method: str
    metrics: ProviderExecutionMetrics | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "model", "appraisal_method"):
            maximum = 256 if name == "model" else 128
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=maximum))
