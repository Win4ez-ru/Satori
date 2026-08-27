"""Pure deterministic exact-search and ranking policy."""

import math
from dataclasses import dataclass
from datetime import datetime

from satori.application.retrieval.contracts import (
    IndexedMemoryCandidate,
    RetrievedMemory,
)


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Compute exact cosine similarity and reject incompatible/corrupt vectors."""

    if len(left) != len(right) or not left:
        raise ValueError("cosine vectors must be non-empty and have equal dimensions")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise ValueError("cosine vectors must have non-zero norm")
    result = sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
    return max(-1.0, min(1.0, result))


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    """Semantic-first v1 ranking with bounded secondary modifiers."""

    minimum_similarity: float
    candidate_limit: int
    top_k: int
    max_context_chars: int
    semantic_weight: float
    importance_weight: float
    recency_weight: float
    recency_half_life_days: float

    def __post_init__(self) -> None:
        if not -1.0 <= self.minimum_similarity <= 1.0:
            raise ValueError("minimum_similarity must be between -1 and 1")
        if min(self.candidate_limit, self.top_k, self.max_context_chars) < 1:
            raise ValueError("retrieval limits must be positive")
        weights = (self.semantic_weight, self.importance_weight, self.recency_weight)
        if any(weight < 0.0 for weight in weights) or not math.isclose(sum(weights), 1.0):
            raise ValueError("retrieval weights must be non-negative and sum to 1")
        if self.semantic_weight <= self.importance_weight + self.recency_weight:
            raise ValueError("semantic weight must dominate all secondary weights")
        if self.recency_half_life_days <= 0:
            raise ValueError("recency_half_life_days must be positive")

    def rank(
        self,
        query_vector: tuple[float, ...],
        candidates: tuple[IndexedMemoryCandidate, ...],
        *,
        now: datetime,
    ) -> tuple[RetrievedMemory, ...]:
        """Threshold semantically, bound the pool, then apply transparent reranking."""

        semantic_pool = [
            (cosine_similarity(query_vector, candidate.vector), candidate)
            for candidate in candidates
        ]
        semantic_pool = [item for item in semantic_pool if item[0] >= self.minimum_similarity]
        semantic_pool.sort(key=lambda item: (-item[0], item[1].memory.memory_id))

        ranked: list[RetrievedMemory] = []
        for semantic, candidate in semantic_pool[: self.candidate_limit]:
            memory = candidate.memory
            age_days = max(0.0, (now - memory.occurred_at).total_seconds() / 86_400)
            recency = 0.5 ** (age_days / self.recency_half_life_days)
            final = (
                self.semantic_weight * semantic
                + self.importance_weight * memory.importance
                + self.recency_weight * recency
            )
            ranked.append(
                RetrievedMemory(
                    memory_id=memory.memory_id,
                    source_interaction_id=memory.source_interaction_id,
                    summary=memory.summary,
                    occurred_at=memory.occurred_at,
                    importance=memory.importance,
                    confidence=memory.confidence,
                    semantic_similarity=semantic,
                    recency_score=recency,
                    final_score=final,
                    evidence_ids=tuple(item.evidence_id for item in memory.evidence),
                )
            )
        ranked.sort(
            key=lambda memory: (
                -memory.final_score,
                -memory.semantic_similarity,
                -memory.importance,
                -memory.recency_score,
                memory.memory_id,
            )
        )
        return tuple(ranked[: self.top_k])
