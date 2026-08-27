"""Episode formation as an idempotent derived projection of raw conversation history."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from satori.application.memory.ports import EpisodicMemoryUnitOfWork
from satori.core.clock import Clock
from satori.core.conversation import ConversationMessageRole
from satori.core.episode import (
    EpisodeFormationProviderResponse,
    EpisodeFormationRequest,
    EpisodeSourceMessage,
)
from satori.core.ids import IdGenerator
from satori.core.ports.providers import StructuredGenerationPort
from satori.domain.conversation_history import ConversationInteraction, InteractionStatus
from satori.domain.memory import (
    EPISODE_FORMATION_POLICY_VERSION,
    EPISODE_FORMATION_VERSION,
    EPISODIC_MEMORY_SCHEMA_VERSION,
    EpisodeDecisionDraft,
    EpisodeDecisionKind,
    EpisodeFormationDecision,
    EpisodicMemory,
    EpisodicMemoryEvidence,
    MemoryLifecycleStatus,
    MemoryManager,
    MemoryProvenanceKind,
    episode_idempotency_key,
)

EPISODE_REQUEST_SCHEMA_VERSION = 1
EpisodicMemoryUnitOfWorkFactory = Callable[[], EpisodicMemoryUnitOfWork]
EpisodeFormationProvider = StructuredGenerationPort[
    EpisodeFormationRequest,
    EpisodeFormationProviderResponse,
]


def _log_fields(**fields: object) -> dict[str, object]:
    return {"satori_fields": fields}


@dataclass(slots=True)
class FormEpisodeForInteraction:
    """Run extraction outside a transaction, then atomically commit owner decision."""

    unit_of_work_factory: EpisodicMemoryUnitOfWorkFactory
    provider: EpisodeFormationProvider
    memory_manager: MemoryManager
    clock: Clock
    id_generator: IdGenerator
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.memory"))

    async def execute(
        self,
        interaction: ConversationInteraction,
        *,
        trace_id: str,
    ) -> EpisodeFormationDecision:
        """Create, skip, or reject once per source interaction/formation version."""

        if interaction.status is not InteractionStatus.COMPLETED:
            raise ValueError("episode formation requires a completed interaction")
        key = episode_idempotency_key(interaction.interaction_id, EPISODE_FORMATION_VERSION)
        with self.unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.episodic_memory.get_decision(key)
        if existing is not None:
            return existing

        self.logger.info(
            "episode_formation_started",
            extra=_log_fields(
                interaction_id=interaction.interaction_id,
                formation_version=EPISODE_FORMATION_VERSION,
            ),
        )
        messages = (interaction.user_message, interaction.assistant_message)
        request = EpisodeFormationRequest(
            schema_version=EPISODE_REQUEST_SCHEMA_VERSION,
            trace_id=trace_id,
            interaction_id=interaction.interaction_id,
            occurred_at=interaction.started_at,
            formation_version=EPISODE_FORMATION_VERSION,
            messages=tuple(
                EpisodeSourceMessage(
                    message_id=message.message_id,
                    role=(
                        ConversationMessageRole.USER
                        if message.role.value == "user"
                        else ConversationMessageRole.ASSISTANT
                    ),
                    content=message.content,
                )
                for message in messages
                if message is not None
            ),
        )
        try:
            provider_response = await self.provider.generate_structured(request)
            draft = self.memory_manager.evaluate(provider_response.proposal, interaction)
            decision = self._materialize_decision(
                key=key,
                interaction=interaction,
                draft=draft,
                provider_response=provider_response,
                trace_id=trace_id,
            )
            with self.unit_of_work_factory() as unit_of_work:
                recorded = unit_of_work.episodic_memory.record_decision(
                    decision,
                    audit_event_id=self.id_generator.new(),
                )
                if recorded:
                    unit_of_work.commit()
                else:
                    prior = unit_of_work.episodic_memory.get_decision(key)
                    if prior is None:
                        raise RuntimeError("formation replay decision disappeared")
                    decision = prior
        except Exception as error:
            self.logger.warning(
                "episode_formation_failed",
                extra=_log_fields(
                    interaction_id=interaction.interaction_id,
                    formation_version=EPISODE_FORMATION_VERSION,
                    error_type=type(error).__name__,
                ),
            )
            raise

        event_name = {
            EpisodeDecisionKind.CREATED: "episode_created",
            EpisodeDecisionKind.SKIPPED: "episode_skipped",
            EpisodeDecisionKind.REJECTED: "episode_rejected",
        }[decision.kind]
        fields: dict[str, object] = {
            "interaction_id": interaction.interaction_id,
            "formation_version": decision.formation_version,
            "policy_version": decision.policy_version,
            "reason_code": decision.reason_code,
            "provider": decision.provider,
            "model": decision.model,
        }
        if provider_response.metrics is not None:
            fields.update(provider_response.metrics.as_log_fields())
        if decision.memory is not None:
            fields["memory_id"] = decision.memory.memory_id
            fields["evidence_count"] = len(decision.memory.evidence)
        self.logger.info(event_name, extra=_log_fields(**fields))
        return decision

    def _materialize_decision(
        self,
        *,
        key: str,
        interaction: ConversationInteraction,
        draft: EpisodeDecisionDraft,
        provider_response: EpisodeFormationProviderResponse,
        trace_id: str,
    ) -> EpisodeFormationDecision:
        decided_at = self.clock.now()
        memory: EpisodicMemory | None = None
        if draft.kind is EpisodeDecisionKind.CREATED:
            assert draft.normalized_summary is not None
            assert draft.importance is not None
            assert draft.confidence is not None
            memory_id = self.id_generator.new()
            evidence = tuple(
                EpisodicMemoryEvidence(
                    evidence_id=self.id_generator.new(),
                    memory_id=memory_id,
                    source_message_id=message_id,
                    provenance_kind=MemoryProvenanceKind.EXPLICIT_USER_STATEMENT,
                    quote=quote,
                    observed_at=interaction.started_at,
                )
                for message_id, quote in draft.source_quotes
            )
            memory = EpisodicMemory(
                memory_id=memory_id,
                schema_version=EPISODIC_MEMORY_SCHEMA_VERSION,
                source_interaction_id=interaction.interaction_id,
                occurred_at=interaction.started_at,
                summary=draft.normalized_summary,
                importance=draft.importance,
                confidence=draft.confidence,
                created_at=decided_at,
                formation_method=provider_response.formation_method,
                formation_version=EPISODE_FORMATION_VERSION,
                lifecycle_status=MemoryLifecycleStatus.ACTIVE,
                evidence=evidence,
            )
        return EpisodeFormationDecision(
            decision_id=self.id_generator.new(),
            idempotency_key=key,
            source_interaction_id=interaction.interaction_id,
            formation_version=EPISODE_FORMATION_VERSION,
            policy_version=EPISODE_FORMATION_POLICY_VERSION,
            kind=draft.kind,
            reason_code=draft.reason_code,
            decided_at=decided_at,
            trace_id=trace_id,
            formation_method=provider_response.formation_method,
            provider=provider_response.provider,
            model=provider_response.model,
            memory=memory,
        )


@dataclass(frozen=True, slots=True)
class GetEpisodicMemories:
    """Explicit-ID/debug read only; never inject memories into conversation context."""

    unit_of_work_factory: EpisodicMemoryUnitOfWorkFactory

    def execute(self, *, interaction_id: str | None = None) -> tuple[EpisodicMemory, ...]:
        """Load durable episodes without performing semantic retrieval."""

        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.episodic_memory.list_memories(interaction_id=interaction_id)
