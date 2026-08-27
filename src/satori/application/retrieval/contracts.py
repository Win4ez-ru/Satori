"""Immutable contracts for indexing, ranking, and conversation memory context."""

import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.core.embedding import EmbeddingSpace
from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.domain.memory import EpisodicMemory


class RetrievalStatus(StrEnum):
    """Observable terminal retrieval outcomes."""

    RETRIEVED = "retrieved"
    NO_RELEVANT_MEMORY = "no_relevant_memory"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class IndexedMemoryCandidate:
    """Canonical episode paired with one compatible derived vector."""

    memory: EpisodicMemory
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class RetrievedMemory:
    """One selected episode with transparent deterministic score components."""

    memory_id: str
    source_interaction_id: str
    summary: str
    occurred_at: datetime
    importance: float
    confidence: float
    semantic_similarity: float
    recency_score: float
    final_score: float
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.semantic_similarity, self.recency_score, self.final_score):
            if not math.isfinite(value):
                raise ValueError("retrieval scores must be finite")
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))


@dataclass(frozen=True, slots=True)
class RetrievedMemoryContext:
    """Bounded untrusted data envelope passed to conversation generation."""

    schema_version: int
    status: RetrievalStatus
    memories: tuple[RetrievedMemory, ...] = ()
    space: EmbeddingSpace | None = None
    candidate_count: int = 0
    failure_kind: str | None = None
    embedding_latency_ms: float = 0.0
    candidate_search_ranking_latency_ms: float = 0.0
    provider_metrics: ProviderExecutionMetrics | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "memories", tuple(self.memories))
        if self.status is RetrievalStatus.RETRIEVED and not self.memories:
            raise ValueError("retrieved status requires memories")
        if self.status is not RetrievalStatus.RETRIEVED and self.memories:
            raise ValueError("non-retrieved status cannot contain memories")

    @property
    def memory_ids(self) -> tuple[str, ...]:
        return tuple(memory.memory_id for memory in self.memories)

    @property
    def grounding_ids(self) -> tuple[str, ...]:
        """Provider-visible memory handles accepted by the grounding gate."""

        return self.memory_ids


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """One current-turn query with an explicit anti-self-retrieval boundary."""

    text: str
    trace_id: str
    cutoff: datetime
    current_interaction_id: str | None


@dataclass(frozen=True, slots=True)
class IndexingReport:
    """Concise idempotent indexing result."""

    considered: int
    indexed: int
    failed: int
    space: EmbeddingSpace | None


def memory_context_json(context: RetrievedMemoryContext) -> str:
    """Canonical payload shared by budgeting and prompt rendering."""

    payload = {
        "schema_version": context.schema_version,
        "status": context.status.value,
        "memories": [
            {
                "memory_id": memory.memory_id,
                "source_interaction_id": memory.source_interaction_id,
                "evidence_ids": list(memory.evidence_ids),
                "summary": memory.summary,
                "occurred_at": memory.occurred_at.isoformat(),
                "importance": round(memory.importance, 6),
                "confidence": round(memory.confidence, 6),
                "semantic_similarity": round(memory.semantic_similarity, 6),
                "final_score": round(memory.final_score, 6),
            }
            for memory in context.memories
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
