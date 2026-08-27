"""SQLAlchemy exact-scan adapter for the rebuildable episodic embedding index."""

import hashlib
import math
from collections import defaultdict
from datetime import datetime

from sqlalchemy import and_, exists, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from satori.application.retrieval.contracts import IndexedMemoryCandidate
from satori.core.embedding import EmbeddingSpace
from satori.domain.memory import (
    EpisodicMemory,
    EpisodicMemoryEvidence,
    MemoryLifecycleStatus,
    MemoryProvenanceKind,
)
from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow, MemoryEvidenceRow
from satori.infrastructure.persistence.models.retrieval import EpisodicMemoryEmbeddingRow


def _space_predicate(space: EmbeddingSpace) -> ColumnElement[bool]:
    return and_(
        EpisodicMemoryEmbeddingRow.provider == space.provider,
        EpisodicMemoryEmbeddingRow.model == space.model,
        EpisodicMemoryEmbeddingRow.dimensions == space.dimensions,
        EpisodicMemoryEmbeddingRow.input_schema_version == space.input_schema_version,
    )


def _embedding_id(memory_id: str, space: EmbeddingSpace) -> str:
    material = (
        f"{memory_id}\0{space.provider}\0{space.model}\0{space.dimensions}"
        f"\0{space.input_schema_version}"
    )
    return hashlib.sha256(material.encode()).hexdigest()


class SQLAlchemyEpisodicMemoryIndexRepository:
    """Keep canonical reads and derived-vector writes behind the Stage 5 port."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_unindexed(
        self,
        space: EmbeddingSpace,
        *,
        rebuild: bool,
    ) -> tuple[EpisodicMemory, ...]:
        query = select(EpisodicMemoryRow).where(EpisodicMemoryRow.lifecycle_status == "active")
        if not rebuild:
            matching = exists().where(
                EpisodicMemoryEmbeddingRow.memory_id == EpisodicMemoryRow.memory_id,
                _space_predicate(space),
            )
            query = query.where(~matching)
        rows = tuple(
            self._session.execute(
                query.order_by(EpisodicMemoryRow.occurred_at, EpisodicMemoryRow.memory_id)
            ).scalars()
        )
        return self._map_memories(rows)

    def upsert(
        self,
        memory_id: str,
        space: EmbeddingSpace,
        vector: tuple[float, ...],
        *,
        indexed_at: datetime,
    ) -> None:
        if len(vector) != space.dimensions or any(not math.isfinite(value) for value in vector):
            raise ValueError("vector is incompatible with embedding space")
        statement = sqlite_insert(EpisodicMemoryEmbeddingRow).values(
            embedding_id=_embedding_id(memory_id, space),
            memory_id=memory_id,
            provider=space.provider,
            model=space.model,
            dimensions=space.dimensions,
            input_schema_version=space.input_schema_version,
            vector=list(vector),
            indexed_at=indexed_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[
                "memory_id",
                "provider",
                "model",
                "dimensions",
                "input_schema_version",
            ],
            set_={"vector": list(vector), "indexed_at": indexed_at},
        )
        self._session.execute(statement)

    def has_candidates(
        self,
        space: EmbeddingSpace,
        *,
        cutoff: datetime,
        excluded_interaction_id: str | None,
    ) -> bool:
        predicates = [
            EpisodicMemoryRow.lifecycle_status == "active",
            EpisodicMemoryRow.occurred_at <= cutoff,
            _space_predicate(space),
        ]
        if excluded_interaction_id is not None:
            predicates.append(EpisodicMemoryRow.source_interaction_id != excluded_interaction_id)
        query = (
            select(EpisodicMemoryRow.memory_id)
            .join(
                EpisodicMemoryEmbeddingRow,
                EpisodicMemoryEmbeddingRow.memory_id == EpisodicMemoryRow.memory_id,
            )
            .where(*predicates)
            .limit(1)
        )
        return self._session.execute(query).scalar_one_or_none() is not None

    def list_candidates(
        self,
        space: EmbeddingSpace,
        *,
        cutoff: datetime,
        excluded_interaction_id: str | None,
    ) -> tuple[IndexedMemoryCandidate, ...]:
        query = (
            select(EpisodicMemoryRow, EpisodicMemoryEmbeddingRow)
            .join(
                EpisodicMemoryEmbeddingRow,
                EpisodicMemoryEmbeddingRow.memory_id == EpisodicMemoryRow.memory_id,
            )
            .where(
                EpisodicMemoryRow.lifecycle_status == "active",
                EpisodicMemoryRow.occurred_at <= cutoff,
                _space_predicate(space),
            )
        )
        if excluded_interaction_id is not None:
            query = query.where(EpisodicMemoryRow.source_interaction_id != excluded_interaction_id)
        pairs = tuple(self._session.execute(query.order_by(EpisodicMemoryRow.memory_id)).all())
        memories = self._map_memories(tuple(pair[0] for pair in pairs))
        by_id = {memory.memory_id: memory for memory in memories}
        candidates: list[IndexedMemoryCandidate] = []
        for memory_row, embedding_row in pairs:
            raw_vector = embedding_row.vector
            if not isinstance(raw_vector, list):
                raise ValueError("persisted embedding vector is not a JSON array")
            vector = tuple(float(value) for value in raw_vector)
            if len(vector) != space.dimensions or any(not math.isfinite(v) for v in vector):
                raise ValueError("persisted embedding vector is corrupt")
            candidates.append(
                IndexedMemoryCandidate(memory=by_id[memory_row.memory_id], vector=vector)
            )
        return tuple(candidates)

    def _map_memories(self, rows: tuple[EpisodicMemoryRow, ...]) -> tuple[EpisodicMemory, ...]:
        if not rows:
            return ()
        memory_ids = tuple(row.memory_id for row in rows)
        evidence_rows = tuple(
            self._session.execute(
                select(MemoryEvidenceRow)
                .where(MemoryEvidenceRow.memory_id.in_(memory_ids))
                .order_by(MemoryEvidenceRow.memory_id, MemoryEvidenceRow.evidence_id)
            ).scalars()
        )
        evidence_by_memory: dict[str, list[EpisodicMemoryEvidence]] = defaultdict(list)
        for evidence in evidence_rows:
            evidence_by_memory[evidence.memory_id].append(
                EpisodicMemoryEvidence(
                    evidence_id=evidence.evidence_id,
                    memory_id=evidence.memory_id,
                    source_message_id=evidence.source_message_id,
                    provenance_kind=MemoryProvenanceKind(evidence.provenance_kind),
                    quote=evidence.quote,
                    observed_at=evidence.observed_at,
                )
            )
        return tuple(
            EpisodicMemory(
                memory_id=row.memory_id,
                schema_version=row.schema_version,
                source_interaction_id=row.source_interaction_id,
                occurred_at=row.occurred_at,
                summary=row.summary,
                importance=row.importance,
                confidence=row.confidence,
                created_at=row.created_at,
                formation_method=row.formation_method,
                formation_version=row.formation_version,
                lifecycle_status=MemoryLifecycleStatus(row.lifecycle_status),
                evidence=tuple(evidence_by_memory[row.memory_id]),
            )
            for row in rows
        )
