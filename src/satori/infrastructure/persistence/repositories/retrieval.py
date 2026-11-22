"""SQLAlchemy exact-scan adapter for the rebuildable episodic embedding index."""

import hashlib
import math
from collections import defaultdict
from datetime import datetime

from sqlalchemy import and_, delete, exists, literal, select
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
from satori.domain.memory_scope import MemoryScope
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.memory_scope import (
    admitted_episode_predicate,
    resolve_memory_scope,
)
from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow, MemoryEvidenceRow
from satori.infrastructure.persistence.models.retrieval import EpisodicMemoryEmbeddingRow
from satori.infrastructure.persistence.source_policy import require_source_inputs


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


def _require_scope(scope: MemoryScope) -> None:
    if not isinstance(scope, MemoryScope):
        raise ValueError("memory_scope_required")


class SQLAlchemyEpisodicMemoryIndexRepository:
    """Keep canonical reads and derived-vector writes behind the Stage 5 port."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def erase_bodies(self, memory_ids: tuple[str, ...]) -> None:
        """Remove every vector space for exact existing owner selectors."""

        if not memory_ids or len(set(memory_ids)) != len(memory_ids):
            raise SourcePolicyError("body_target_invalid")
        existing_ids = set(
            self._session.execute(
                select(EpisodicMemoryRow.memory_id).where(
                    EpisodicMemoryRow.memory_id.in_(memory_ids),
                )
            ).scalars()
        )
        if existing_ids != set(memory_ids):
            raise SourcePolicyError("body_owner_state_changed")
        self._session.execute(
            delete(EpisodicMemoryEmbeddingRow).where(
                EpisodicMemoryEmbeddingRow.memory_id.in_(memory_ids)
            )
        )

    def list_unindexed(
        self,
        space: EmbeddingSpace,
        *,
        scope: MemoryScope,
        rebuild: bool,
        limit: int | None = None,
    ) -> tuple[EpisodicMemory, ...]:
        _require_scope(scope)
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 101):
            raise ValueError("memory_index_limit_invalid")
        query = select(EpisodicMemoryRow).where(admitted_episode_predicate(self._session, scope))
        if not rebuild:
            matching = exists().where(
                EpisodicMemoryEmbeddingRow.memory_id == EpisodicMemoryRow.memory_id,
                _space_predicate(space),
            )
            query = query.where(~matching)
        query = query.order_by(EpisodicMemoryRow.occurred_at, EpisodicMemoryRow.memory_id)
        if limit is not None:
            query = query.limit(limit)
        rows = tuple(self._session.execute(query).scalars())
        return self._map_memories(rows)

    def get_eligible(self, memory_id: str) -> tuple[MemoryScope, EpisodicMemory] | None:
        scope = resolve_memory_scope(self._session, memory_id)
        if scope is None:
            return None
        row = self._session.execute(
            select(EpisodicMemoryRow).where(
                EpisodicMemoryRow.memory_id == memory_id,
                admitted_episode_predicate(self._session, scope),
            )
        ).scalar_one_or_none()
        return (scope, self._map_memories((row,))[0]) if row is not None else None

    def upsert(
        self,
        memory_id: str,
        space: EmbeddingSpace,
        vector: tuple[float, ...],
        *,
        scope: MemoryScope,
        expected_version: int,
        indexed_at: datetime,
        input_id: str | None = None,
    ) -> None:
        _require_scope(scope)
        require_source_inputs(self._session, input_id)
        if resolve_memory_scope(self._session, memory_id) != scope:
            raise ValueError("memory_source_ineligible")
        if len(vector) != space.dimensions or any(not math.isfinite(value) for value in vector):
            raise ValueError("vector is incompatible with embedding space")
        values = {
            "embedding_id": _embedding_id(memory_id, space),
            "memory_id": memory_id,
            "provider": space.provider,
            "model": space.model,
            "dimensions": space.dimensions,
            "input_schema_version": space.input_schema_version,
            "vector": list(vector),
            "indexed_at": indexed_at,
        }
        table = EpisodicMemoryEmbeddingRow.__table__
        source = select(
            *(literal(value, type_=table.c[name].type) for name, value in values.items())
        ).where(
            exists(
                select(EpisodicMemoryRow.memory_id).where(
                    EpisodicMemoryRow.memory_id == memory_id,
                    EpisodicMemoryRow.aggregate_version == expected_version,
                    admitted_episode_predicate(self._session, scope),
                )
            )
        )
        statement = sqlite_insert(EpisodicMemoryEmbeddingRow).from_select(list(values), source)
        returning = statement.on_conflict_do_update(
            index_elements=[
                "memory_id",
                "provider",
                "model",
                "dimensions",
                "input_schema_version",
            ],
            set_={"vector": list(vector), "indexed_at": indexed_at},
        ).returning(EpisodicMemoryEmbeddingRow.embedding_id)
        if self._session.execute(returning).scalar_one_or_none() is None:
            raise ValueError("memory_source_changed")

    def has_candidates(
        self,
        space: EmbeddingSpace,
        *,
        scope: MemoryScope,
        cutoff: datetime,
        excluded_interaction_id: str | None,
    ) -> bool:
        _require_scope(scope)
        predicates = [
            admitted_episode_predicate(self._session, scope),
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
        scope: MemoryScope,
        cutoff: datetime,
        excluded_interaction_id: str | None,
    ) -> tuple[IndexedMemoryCandidate, ...]:
        _require_scope(scope)
        query = (
            select(EpisodicMemoryRow, EpisodicMemoryEmbeddingRow)
            .join(
                EpisodicMemoryEmbeddingRow,
                EpisodicMemoryEmbeddingRow.memory_id == EpisodicMemoryRow.memory_id,
            )
            .where(
                admitted_episode_predicate(self._session, scope),
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
            if evidence.body_state != "available" or evidence.quote is None:
                raise SourcePolicyError("episode_body_unavailable")
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
        memories = []
        for row in rows:
            if row.body_state != "available" or row.summary is None:
                raise SourcePolicyError("episode_body_unavailable")
            memories.append(
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
                    aggregate_version=row.aggregate_version,
                    evidence=tuple(evidence_by_memory[row.memory_id]),
                )
            )
        return tuple(memories)
