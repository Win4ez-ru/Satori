"""Deterministic Stage 5 exact cosine, threshold, ranking, and eval fixtures."""

from datetime import UTC, datetime, timedelta

from satori.application.retrieval.contracts import IndexedMemoryCandidate
from satori.application.retrieval.policy import RetrievalPolicy, cosine_similarity
from satori.domain.memory import (
    EpisodicMemory,
    EpisodicMemoryEvidence,
    MemoryLifecycleStatus,
    MemoryProvenanceKind,
)

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def memory(
    memory_id: str,
    summary: str,
    *,
    importance: float = 0.5,
    age_days: int = 1,
) -> EpisodicMemory:
    occurred_at = NOW - timedelta(days=age_days)
    return EpisodicMemory(
        memory_id=memory_id,
        schema_version=1,
        source_interaction_id=f"interaction-{memory_id}",
        occurred_at=occurred_at,
        summary=summary,
        importance=importance,
        confidence=0.9,
        created_at=occurred_at,
        formation_method="fixture.v1",
        formation_version=1,
        lifecycle_status=MemoryLifecycleStatus.ACTIVE,
        evidence=(
            EpisodicMemoryEvidence(
                evidence_id=f"evidence-{memory_id}",
                memory_id=memory_id,
                source_message_id=f"message-{memory_id}",
                provenance_kind=MemoryProvenanceKind.EXPLICIT_USER_STATEMENT,
                quote=summary,
                observed_at=occurred_at,
            ),
        ),
    )


def policy(**overrides: float | int) -> RetrievalPolicy:
    values: dict[str, float | int] = {
        "minimum_similarity": 0.55,
        "candidate_limit": 32,
        "top_k": 4,
        "max_context_chars": 2400,
        "semantic_weight": 0.8,
        "importance_weight": 0.1,
        "recency_weight": 0.1,
        "recency_half_life_days": 30.0,
    }
    values.update(overrides)
    return RetrievalPolicy(**values)  # type: ignore[arg-type]


def test_cosine_similarity_is_exact_and_dimension_safe() -> None:
    assert cosine_similarity((1.0, 0.0), (1.0, 0.0)) == 1.0
    assert cosine_similarity((1.0, 0.0), (0.0, 1.0)) == 0.0


def test_semantic_relevance_dominates_secondary_ranking_factors() -> None:
    candidates = (
        IndexedMemoryCandidate(memory("relevant", "релевантно", importance=0.1), (0.99, 0.1)),
        IndexedMemoryCandidate(
            memory("less-relevant", "менее релевантно", importance=1.0, age_days=0),
            (0.75, 0.66),
        ),
    )

    ranked = policy().rank((1.0, 0.0), candidates, now=NOW)

    assert tuple(item.memory_id for item in ranked) == ("relevant", "less-relevant")
    assert ranked[0].final_score > ranked[1].final_score


def test_threshold_produces_explicit_no_result_for_distractors() -> None:
    ranked = policy().rank(
        (1.0, 0.0),
        (IndexedMemoryCandidate(memory("distractor", "другая тема"), (0.0, 1.0)),),
        now=NOW,
    )
    assert ranked == ()


def test_retrieval_eval_direct_paraphrase_distractor_and_no_result() -> None:
    """Deterministic miniature eval: recall@1=1, precision@1=1, no-result accuracy=1."""

    project = IndexedMemoryCandidate(memory("project", "первый запуск проекта"), (1.0, 0.0))
    trip = IndexedMemoryCandidate(memory("trip", "поездка в Казань"), (0.0, 1.0))
    candidates = (project, trip)
    cases = (
        ((1.0, 0.0), "project"),
        ((0.92, 0.08), "project"),
        ((0.0, 1.0), "trip"),
        ((-1.0, 0.0), None),
    )

    predictions = []
    for vector, _expected in cases:
        ranked = policy(top_k=1).rank(vector, candidates, now=NOW)
        predictions.append(ranked[0].memory_id if ranked else None)

    assert tuple(predictions) == tuple(expected for _, expected in cases)
