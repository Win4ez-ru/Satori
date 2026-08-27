"""Provider-neutral contracts for evidence-grounded semantic-memory formation."""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.core.provider_metrics import ProviderExecutionMetrics


def _non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class SemanticClaimKind(StrEnum):
    """Epistemic origin retained for the complete lifetime of a claim."""

    EXPLICIT_FACT = "explicit_fact"
    INFERRED_FACT = "inferred_fact"
    HYPOTHESIS = "hypothesis"
    ATTRIBUTED_STATEMENT = "attributed_statement"


class SemanticValueKind(StrEnum):
    """Small v1 value algebra; no generic knowledge graph is implied."""

    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"


SemanticScalar = str | float | bool


def validate_semantic_scalar(kind: SemanticValueKind, value: SemanticScalar) -> None:
    """Reject bool/number coercion and non-finite or blank semantic values."""

    if kind is SemanticValueKind.TEXT:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("text semantic value must not be blank")
        return
    if kind is SemanticValueKind.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("number semantic value must be numeric")
        if not math.isfinite(float(value)):
            raise ValueError("number semantic value must be finite")
        return
    if type(value) is not bool:
        raise ValueError("boolean semantic value must be a boolean")


@dataclass(frozen=True, slots=True)
class SemanticSourceEvidence:
    """One root user span inherited through an episodic-memory evidence edge."""

    memory_evidence_id: str
    source_message_id: str
    quote: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "memory_evidence_id",
            _non_blank(self.memory_evidence_id, "memory_evidence_id"),
        )
        object.__setattr__(
            self,
            "source_message_id",
            _non_blank(self.source_message_id, "source_message_id"),
        )
        object.__setattr__(self, "quote", _non_blank(self.quote, "quote"))


@dataclass(frozen=True, slots=True)
class SemanticSourceMemory:
    """Bounded episodic input treated as untrusted data by the provider."""

    memory_id: str
    source_interaction_id: str
    occurred_at: datetime
    summary: str
    evidence: tuple[SemanticSourceEvidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_id", _non_blank(self.memory_id, "memory_id"))
        object.__setattr__(
            self,
            "source_interaction_id",
            _non_blank(self.source_interaction_id, "source_interaction_id"),
        )
        _aware(self.occurred_at, "semantic source occurred_at")
        object.__setattr__(self, "summary", _non_blank(self.summary, "summary"))
        evidence = tuple(self.evidence)
        if not evidence:
            raise ValueError("semantic source memory requires root user evidence")
        ids = tuple(item.memory_evidence_id for item in evidence)
        if len(ids) != len(set(ids)):
            raise ValueError("semantic source memory evidence IDs must be unique")
        object.__setattr__(self, "evidence", evidence)


@dataclass(frozen=True, slots=True)
class SemanticClaimProposal:
    """Untrusted claim proposal; only deterministic policy may commit it."""

    subject: str
    predicate: str
    value_kind: SemanticValueKind
    value: SemanticScalar
    polarity: bool
    claim_kind: SemanticClaimKind
    confidence: float
    evidence_memory_ids: tuple[str, ...]
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    corrects_claim_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _non_blank(self.subject, "subject"))
        object.__setattr__(self, "predicate", _non_blank(self.predicate, "predicate"))
        validate_semantic_scalar(self.value_kind, self.value)
        if type(self.polarity) is not bool:
            raise ValueError("polarity must be a boolean")
        if (
            isinstance(self.confidence, bool)
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("semantic proposal confidence must be in [0, 1]")
        evidence_ids = tuple(
            _non_blank(item, "evidence_memory_id") for item in self.evidence_memory_ids
        )
        if not evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("semantic proposal evidence memory IDs must be non-empty and unique")
        object.__setattr__(self, "evidence_memory_ids", evidence_ids)
        if self.valid_from is not None:
            _aware(self.valid_from, "valid_from")
        if self.valid_until is not None:
            _aware(self.valid_until, "valid_until")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until <= self.valid_from
        ):
            raise ValueError("valid_until must be after valid_from")
        if self.corrects_claim_id is not None:
            object.__setattr__(
                self,
                "corrects_claim_id",
                _non_blank(self.corrects_claim_id, "corrects_claim_id"),
            )


@dataclass(frozen=True, slots=True)
class SemanticFormationProposal:
    """Bounded zero-or-more proposal document from a replaceable provider."""

    schema_version: int
    claims: tuple[SemanticClaimProposal, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("semantic proposal schema_version must be positive")
        object.__setattr__(self, "claims", tuple(self.claims))


@dataclass(frozen=True, slots=True)
class SemanticFormationRequest:
    """Versioned bounded source set for one incremental formation attempt."""

    schema_version: int
    trace_id: str
    source_memory_id: str
    formation_version: int
    max_claims: int
    memories: tuple[SemanticSourceMemory, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("semantic request schema_version must be positive")
        object.__setattr__(self, "trace_id", _non_blank(self.trace_id, "trace_id"))
        object.__setattr__(
            self,
            "source_memory_id",
            _non_blank(self.source_memory_id, "source_memory_id"),
        )
        if type(self.formation_version) is not int or self.formation_version < 1:
            raise ValueError("semantic formation_version must be positive")
        if type(self.max_claims) is not int or self.max_claims < 1:
            raise ValueError("semantic max_claims must be positive")
        memories = tuple(self.memories)
        ids = tuple(item.memory_id for item in memories)
        if not memories or self.source_memory_id not in ids or len(ids) != len(set(ids)):
            raise ValueError("semantic request requires unique memories including its source")
        object.__setattr__(self, "memories", memories)


@dataclass(frozen=True, slots=True)
class SemanticFormationProviderResponse:
    """Structured provider result with reproducibility metadata."""

    proposal: SemanticFormationProposal
    provider: str
    model: str
    formation_method: str
    metrics: ProviderExecutionMetrics | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _non_blank(self.provider, "provider"))
        object.__setattr__(self, "model", _non_blank(self.model, "model"))
        object.__setattr__(
            self,
            "formation_method",
            _non_blank(self.formation_method, "formation_method"),
        )


class SemanticFormationProviderError(Exception):
    """Typed failure at the semantic structured-generation boundary."""

    def __init__(self, provider: str, model: str, message: str) -> None:
        self.provider = _non_blank(provider, "provider")
        self.model = _non_blank(model, "model")
        super().__init__(_non_blank(message, "message"))
