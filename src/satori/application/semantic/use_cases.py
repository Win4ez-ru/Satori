"""Incremental semantic formation, backfill, and immutable read models."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from satori.application.semantic.contracts import (
    RetrievedSemanticClaim,
    RetrievedSemanticContext,
    semantic_context_json,
)
from satori.application.semantic.ports import SemanticMemoryUnitOfWork
from satori.core.clock import Clock
from satori.core.ids import IdGenerator
from satori.core.ports.providers import StructuredGenerationPort
from satori.core.semantic import (
    SemanticFormationProviderResponse,
    SemanticFormationRequest,
    SemanticSourceEvidence,
    SemanticSourceMemory,
)
from satori.domain.semantic_memory import (
    SEMANTIC_FORMATION_POLICY_VERSION,
    SEMANTIC_FORMATION_VERSION,
    SemanticClaim,
    SemanticClaimRevision,
    SemanticFormationDecision,
    SemanticMemoryManager,
    semantic_idempotency_key,
)

SEMANTIC_REQUEST_SCHEMA_VERSION = 1
SemanticUnitOfWorkFactory = Callable[[], SemanticMemoryUnitOfWork]
SemanticFormationProvider = StructuredGenerationPort[
    SemanticFormationRequest, SemanticFormationProviderResponse
]


def _log_fields(**fields: object) -> dict[str, object]:
    return {"satori_fields": fields}


@dataclass(frozen=True, slots=True)
class SemanticBackfillReport:
    """Metadata-only result of deterministic missing-source processing."""

    considered: int
    applied: int
    skipped: int
    rejected: int
    failed: int


@dataclass(slots=True)
class FormSemanticMemory:
    """Call the provider outside a transaction, then ask the sole owner to commit."""

    unit_of_work_factory: SemanticUnitOfWorkFactory
    provider: SemanticFormationProvider
    manager: SemanticMemoryManager
    clock: Clock
    id_generator: IdGenerator
    max_claims_per_memory: int
    max_source_memories: int
    monotonic: Callable[[], float] = time.perf_counter
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.semantic"))

    async def execute(self, source_memory_id: str, *, trace_id: str) -> SemanticFormationDecision:
        """Apply or replay exactly one terminal v1 decision for an episodic source."""

        key = semantic_idempotency_key(source_memory_id, SEMANTIC_FORMATION_VERSION)
        with self.unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.semantic_memory.get_decision(key)
            source_memories = unit_of_work.semantic_memory.get_source_memories(
                source_memory_id, limit=self.max_source_memories
            )
        if existing is not None:
            return existing
        if not source_memories:
            raise ValueError("semantic source episodic memory does not exist")

        request = SemanticFormationRequest(
            schema_version=SEMANTIC_REQUEST_SCHEMA_VERSION,
            trace_id=trace_id,
            source_memory_id=source_memory_id,
            formation_version=SEMANTIC_FORMATION_VERSION,
            max_claims=self.max_claims_per_memory,
            memories=tuple(
                SemanticSourceMemory(
                    memory_id=memory.memory_id,
                    source_interaction_id=memory.source_interaction_id,
                    occurred_at=memory.occurred_at,
                    summary=memory.summary,
                    evidence=tuple(
                        SemanticSourceEvidence(
                            memory_evidence_id=evidence.evidence_id,
                            source_message_id=evidence.source_message_id,
                            quote=evidence.quote,
                        )
                        for evidence in memory.evidence
                    ),
                )
                for memory in source_memories
            ),
        )
        self.logger.info(
            "semantic_formation_started",
            extra=_log_fields(
                source_memory_id=source_memory_id,
                formation_version=SEMANTIC_FORMATION_VERSION,
                input_memory_count=len(request.memories),
            ),
        )
        started = self.monotonic()
        try:
            response = await self.provider.generate_structured(request)
            decision_id = self.id_generator.new()
            with self.unit_of_work_factory() as unit_of_work:
                current = unit_of_work.semantic_memory.list_claims()
                plan = self.manager.evaluate(
                    response.proposal,
                    source_memory_id=source_memory_id,
                    memories=request.memories,
                    existing_claims=current,
                    max_claims=self.max_claims_per_memory,
                    now=self.clock.now(),
                    decision_id=decision_id,
                    formation_method=response.formation_method,
                    new_id=self.id_generator.new,
                )
                decision = SemanticFormationDecision(
                    decision_id=decision_id,
                    idempotency_key=key,
                    source_memory_id=source_memory_id,
                    formation_version=SEMANTIC_FORMATION_VERSION,
                    policy_version=SEMANTIC_FORMATION_POLICY_VERSION,
                    kind=plan.kind,
                    reason_code=plan.reason_code,
                    created_count=plan.created_count,
                    merged_count=plan.merged_count,
                    superseded_count=plan.superseded_count,
                    disputed_count=plan.disputed_count,
                    rejected_count=plan.rejected_count,
                    claim_ids=plan.claim_ids,
                    decided_at=self.clock.now(),
                    trace_id=trace_id,
                    formation_method=response.formation_method,
                    provider=response.provider,
                    model=response.model,
                )
                recorded = unit_of_work.semantic_memory.record_decision(
                    decision,
                    plan,
                    audit_event_id=self.id_generator.new(),
                )
                if recorded:
                    unit_of_work.commit()
                else:
                    prior = unit_of_work.semantic_memory.get_decision(key)
                    if prior is None:
                        raise RuntimeError("semantic replay decision disappeared")
                    decision = prior
        except Exception as error:
            self.logger.warning(
                "semantic_formation_failed",
                extra=_log_fields(
                    source_memory_id=source_memory_id,
                    formation_version=SEMANTIC_FORMATION_VERSION,
                    error_type=type(error).__name__,
                    latency_ms=round((self.monotonic() - started) * 1000, 3),
                ),
            )
            raise
        self.logger.info(
            "semantic_formation_decided",
            extra=_log_fields(
                source_memory_id=source_memory_id,
                decision_id=decision.decision_id,
                decision_kind=decision.kind.value,
                reason_code=decision.reason_code,
                claim_ids=list(decision.claim_ids),
                created_count=decision.created_count,
                merged_count=decision.merged_count,
                superseded_count=decision.superseded_count,
                disputed_count=decision.disputed_count,
                rejected_count=decision.rejected_count,
                provider=decision.provider,
                model=decision.model,
                formation_version=decision.formation_version,
                latency_ms=round((self.monotonic() - started) * 1000, 3),
                **(response.metrics.as_log_fields() if response.metrics else {}),
            ),
        )
        return decision


@dataclass(frozen=True, slots=True)
class BackfillSemanticMemory:
    """Restartable deterministic traversal of missing source decisions."""

    unit_of_work_factory: SemanticUnitOfWorkFactory
    form_semantic: FormSemanticMemory

    async def execute(self, *, trace_id: str, limit: int) -> SemanticBackfillReport:
        if limit < 1:
            raise ValueError("semantic backfill limit must be positive")
        with self.unit_of_work_factory() as unit_of_work:
            memory_ids = unit_of_work.semantic_memory.list_unprocessed_memory_ids(limit=limit)
        applied = skipped = rejected = failed = 0
        for memory_id in memory_ids:
            try:
                decision = await self.form_semantic.execute(memory_id, trace_id=trace_id)
            except Exception:
                failed += 1
                continue
            if decision.kind.value == "applied":
                applied += 1
            elif decision.kind.value == "skipped":
                skipped += 1
            else:
                rejected += 1
        return SemanticBackfillReport(len(memory_ids), applied, skipped, rejected, failed)


@dataclass(frozen=True, slots=True)
class GetSemanticClaims:
    """Immutable semantic list/get/history queries for CLI and tests."""

    unit_of_work_factory: SemanticUnitOfWorkFactory

    def list(
        self, *, active_only: bool = True, predicate: str | None = None
    ) -> tuple[SemanticClaim, ...]:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.semantic_memory.list_claims(
                active_only=active_only, predicate=predicate
            )

    def inspect(
        self, claim_id: str
    ) -> tuple[SemanticClaim, tuple[SemanticClaimRevision, ...]] | None:
        with self.unit_of_work_factory() as unit_of_work:
            claim = unit_of_work.semantic_memory.get_claim(claim_id)
            if claim is None:
                return None
            return claim, unit_of_work.semantic_memory.list_revisions(claim_id)


@dataclass(frozen=True, slots=True)
class RetrieveSemanticClaims:
    """Select active claims only through already-retrieved episodic provenance."""

    unit_of_work_factory: SemanticUnitOfWorkFactory
    top_k: int
    max_context_chars: int

    def execute(self, memory_ids: tuple[str, ...]) -> RetrievedSemanticContext:
        if not memory_ids:
            return RetrievedSemanticContext(status="no_result", claims=())
        relevant = set(memory_ids)
        with self.unit_of_work_factory() as unit_of_work:
            claims = unit_of_work.semantic_memory.list_claims(active_only=True)
        ranked = sorted(
            (
                claim
                for claim in claims
                if any(evidence.memory_id in relevant for evidence in claim.evidence)
            ),
            key=lambda claim: (
                -claim.confidence,
                -len({item.root_message_id for item in claim.evidence}),
                claim.claim_id,
            ),
        )
        selected: list[RetrievedSemanticClaim] = []
        for claim in ranked[: self.top_k]:
            candidate = RetrievedSemanticClaim(
                claim_id=claim.claim_id,
                subject=claim.subject,
                predicate=claim.predicate,
                value_kind=claim.value_kind.value,
                value=claim.value,
                polarity=claim.polarity,
                claim_kind=claim.claim_kind.value,
                confidence=claim.confidence,
                evidence_memory_ids=tuple(
                    dict.fromkeys(
                        item.memory_id for item in claim.evidence if item.memory_id in relevant
                    )
                ),
                root_message_ids=tuple(
                    dict.fromkeys(item.root_message_id for item in claim.evidence)
                ),
            )
            trial = RetrievedSemanticContext(status="retrieved", claims=(*selected, candidate))
            if len(semantic_context_json(trial)) > self.max_context_chars:
                break
            selected.append(candidate)
        return RetrievedSemanticContext(
            status="retrieved" if selected else "no_result",
            claims=tuple(selected),
        )
