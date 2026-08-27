"""Stage 5 indexing, exact candidate search, and graceful retrieval orchestration."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from satori.application.retrieval.contracts import (
    IndexingReport,
    RetrievalQuery,
    RetrievalStatus,
    RetrievedMemory,
    RetrievedMemoryContext,
    memory_context_json,
)
from satori.application.retrieval.policy import RetrievalPolicy
from satori.application.retrieval.ports import EpisodicMemoryIndexUnitOfWork
from satori.core.clock import Clock
from satori.core.embedding import EmbeddingRequest, EmbeddingResponse, EmbeddingSpace
from satori.core.ports.providers import EmbeddingPort
from satori.domain.memory import EpisodicMemory

EMBEDDING_INPUT_SCHEMA_VERSION = 1
RETRIEVED_MEMORY_CONTEXT_SCHEMA_VERSION = 1
MemoryIndexUnitOfWorkFactory = Callable[[], EpisodicMemoryIndexUnitOfWork]


class EmbeddingProvider(EmbeddingPort[EmbeddingRequest, EmbeddingResponse], Protocol):
    """Embedding capability whose comparison space is known before persistence reads."""

    @property
    def space(self) -> EmbeddingSpace:
        """Return the adapter's exact configured vector space."""


def _log_fields(**fields: object) -> dict[str, object]:
    return {"satori_fields": fields}


def _validate_response(
    response: EmbeddingResponse,
    *,
    expected_space: EmbeddingSpace,
    expected_count: int,
) -> None:
    if response.space != expected_space:
        raise ValueError("embedding provider returned a different vector space")
    if len(response.vectors) != expected_count:
        raise ValueError("embedding provider returned the wrong vector count")


@dataclass(slots=True)
class IndexEpisodicMemory:
    """Add one post-commit episode to the derived index without touching its source."""

    unit_of_work_factory: MemoryIndexUnitOfWorkFactory
    provider: EmbeddingProvider
    clock: Clock
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.retrieval"))

    async def execute(self, memory: EpisodicMemory, *, trace_id: str) -> None:
        started = time.perf_counter()
        response = await self.provider.embed(
            EmbeddingRequest(
                schema_version=EMBEDDING_INPUT_SCHEMA_VERSION,
                trace_id=trace_id,
                texts=(memory.summary,),
            )
        )
        _validate_response(response, expected_space=self.provider.space, expected_count=1)
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.memory_index.upsert(
                memory.memory_id,
                response.space,
                response.vectors[0],
                indexed_at=self.clock.now(),
            )
            unit_of_work.commit()
        self.logger.info(
            "memory_indexed",
            extra=_log_fields(
                memory_id=memory.memory_id,
                embedding_space=response.space.key,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                **(response.metrics.as_log_fields() if response.metrics else {}),
            ),
        )


@dataclass(slots=True)
class IndexAllEpisodicMemories:
    """Idempotent backfill/rebuild in bounded batches with per-batch failure isolation."""

    unit_of_work_factory: MemoryIndexUnitOfWorkFactory
    provider: EmbeddingProvider
    clock: Clock
    batch_size: int = 32
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.retrieval"))

    async def execute(self, *, trace_id: str, rebuild: bool = False) -> IndexingReport:
        with self.unit_of_work_factory() as unit_of_work:
            memories = unit_of_work.memory_index.list_unindexed(
                self.provider.space,
                rebuild=rebuild,
            )
        indexed = 0
        failed = 0
        for offset in range(0, len(memories), self.batch_size):
            batch = memories[offset : offset + self.batch_size]
            try:
                response = await self.provider.embed(
                    EmbeddingRequest(
                        schema_version=EMBEDDING_INPUT_SCHEMA_VERSION,
                        trace_id=trace_id,
                        texts=tuple(memory.summary for memory in batch),
                    )
                )
                _validate_response(
                    response,
                    expected_space=self.provider.space,
                    expected_count=len(batch),
                )
                with self.unit_of_work_factory() as unit_of_work:
                    for memory, vector in zip(batch, response.vectors, strict=True):
                        unit_of_work.memory_index.upsert(
                            memory.memory_id,
                            response.space,
                            vector,
                            indexed_at=self.clock.now(),
                        )
                    unit_of_work.commit()
                indexed += len(batch)
            except Exception as error:
                failed += len(batch)
                self.logger.warning(
                    "memory_index_batch_failed",
                    extra=_log_fields(
                        embedding_space=self.provider.space.key,
                        batch_size=len(batch),
                        error_type=type(error).__name__,
                    ),
                )
        report = IndexingReport(
            considered=len(memories),
            indexed=indexed,
            failed=failed,
            space=self.provider.space,
        )
        self.logger.info(
            "memory_index_completed",
            extra=_log_fields(
                embedding_space=self.provider.space.key,
                rebuild=rebuild,
                considered=report.considered,
                indexed=report.indexed,
                failed=report.failed,
            ),
        )
        return report


@dataclass(slots=True)
class RetrieveEpisodicMemories:
    """Embed current input, exact-scan compatible prior episodes, and degrade safely."""

    unit_of_work_factory: MemoryIndexUnitOfWorkFactory
    provider: EmbeddingProvider
    policy: RetrievalPolicy
    monotonic: Callable[[], float] = time.perf_counter
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.retrieval"))

    async def execute(self, query: RetrievalQuery) -> RetrievedMemoryContext:
        started = self.monotonic()
        try:
            candidate_started = self.monotonic()
            with self.unit_of_work_factory() as unit_of_work:
                has_candidates = unit_of_work.memory_index.has_candidates(
                    self.provider.space,
                    cutoff=query.cutoff,
                    excluded_interaction_id=query.current_interaction_id,
                )
            precheck_ms = (self.monotonic() - candidate_started) * 1000
            if not has_candidates:
                result = RetrievedMemoryContext(
                    schema_version=RETRIEVED_MEMORY_CONTEXT_SCHEMA_VERSION,
                    status=RetrievalStatus.NO_RELEVANT_MEMORY,
                    space=self.provider.space,
                    candidate_search_ranking_latency_ms=precheck_ms,
                )
                self.logger.info(
                    "memory_retrieval_completed",
                    extra=_log_fields(
                        retrieval_status=result.status.value,
                        embedding_space=self.provider.space.key,
                        candidate_count=0,
                        selected_count=0,
                        selected_memory_ids=[],
                        embedding_skipped=True,
                        embedding_latency_ms=0.0,
                        candidate_search_ranking_latency_ms=round(precheck_ms, 3),
                        latency_ms=round((self.monotonic() - started) * 1000, 3),
                    ),
                )
                return result

            embedding_started = self.monotonic()
            response = await self.provider.embed(
                EmbeddingRequest(
                    schema_version=EMBEDDING_INPUT_SCHEMA_VERSION,
                    trace_id=query.trace_id,
                    texts=(query.text,),
                )
            )
            _validate_response(response, expected_space=self.provider.space, expected_count=1)
            embedding_ms = (self.monotonic() - embedding_started) * 1000
            candidate_started = self.monotonic()
            with self.unit_of_work_factory() as unit_of_work:
                candidates = unit_of_work.memory_index.list_candidates(
                    response.space,
                    cutoff=query.cutoff,
                    excluded_interaction_id=query.current_interaction_id,
                )
            ranked = self.policy.rank(response.vectors[0], candidates, now=query.cutoff)
            selected: list[RetrievedMemory] = []
            selected_summaries: set[str] = set()
            for memory in ranked:
                normalized_summary = " ".join(memory.summary.casefold().split())
                if normalized_summary in selected_summaries:
                    continue
                proposed = RetrievedMemoryContext(
                    schema_version=RETRIEVED_MEMORY_CONTEXT_SCHEMA_VERSION,
                    status=RetrievalStatus.RETRIEVED,
                    memories=tuple([*selected, memory]),
                    space=response.space,
                    candidate_count=len(candidates),
                )
                if len(memory_context_json(proposed)) > self.policy.max_context_chars:
                    break
                selected.append(memory)
                selected_summaries.add(normalized_summary)
            candidate_ms = precheck_ms + (self.monotonic() - candidate_started) * 1000
            if selected:
                result = RetrievedMemoryContext(
                    schema_version=RETRIEVED_MEMORY_CONTEXT_SCHEMA_VERSION,
                    status=RetrievalStatus.RETRIEVED,
                    memories=tuple(selected),
                    space=response.space,
                    candidate_count=len(candidates),
                    embedding_latency_ms=embedding_ms,
                    candidate_search_ranking_latency_ms=candidate_ms,
                    provider_metrics=response.metrics,
                )
            else:
                result = RetrievedMemoryContext(
                    schema_version=RETRIEVED_MEMORY_CONTEXT_SCHEMA_VERSION,
                    status=RetrievalStatus.NO_RELEVANT_MEMORY,
                    space=response.space,
                    candidate_count=len(candidates),
                    embedding_latency_ms=embedding_ms,
                    candidate_search_ranking_latency_ms=candidate_ms,
                    provider_metrics=response.metrics,
                )
            provider_fields = response.metrics.as_log_fields() if response.metrics else {}
            self.logger.info(
                "memory_retrieval_completed",
                extra=_log_fields(
                    retrieval_status=result.status.value,
                    embedding_space=response.space.key,
                    candidate_count=len(candidates),
                    selected_count=len(result.memories),
                    selected_memory_ids=list(result.memory_ids),
                    top_score=(result.memories[0].final_score if result.memories else None),
                    embedding_skipped=False,
                    embedding_latency_ms=round(embedding_ms, 3),
                    candidate_search_ranking_latency_ms=round(candidate_ms, 3),
                    latency_ms=round((self.monotonic() - started) * 1000, 3),
                    **provider_fields,
                ),
            )
            return result
        except Exception as error:
            self.logger.warning(
                "memory_retrieval_unavailable",
                extra=_log_fields(
                    retrieval_status=RetrievalStatus.UNAVAILABLE.value,
                    embedding_space=self.provider.space.key,
                    error_type=type(error).__name__,
                    latency_ms=round((self.monotonic() - started) * 1000, 3),
                ),
            )
            return RetrievedMemoryContext(
                schema_version=RETRIEVED_MEMORY_CONTEXT_SCHEMA_VERSION,
                status=RetrievalStatus.UNAVAILABLE,
                space=self.provider.space,
                failure_kind=type(error).__name__,
            )
