"""Stage 4 episodic-memory owner policy and immutable records."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.core.episode import EpisodeFormationProposal
from satori.domain.conversation_history import (
    ConversationInteraction,
    HistoricalMessageRole,
    InteractionStatus,
)
from satori.domain.validation import aware_utc, non_blank, positive_version, unit_interval

EPISODIC_MEMORY_SCHEMA_VERSION = 1
EPISODE_FORMATION_POLICY_VERSION = 1
EPISODE_FORMATION_VERSION = 1
MIN_EPISODE_IMPORTANCE = 0.5
MAX_EPISODE_SUMMARY_CHARS = 500
MAX_EVIDENCE_QUOTE_CHARS = 500


class EpisodeDecisionKind(StrEnum):
    """Terminal deterministic outcome for one formation version/source pair."""

    CREATED = "created"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class MemoryProvenanceKind(StrEnum):
    """Stage 4 provenance kinds accepted as canonical episode evidence."""

    EXPLICIT_USER_STATEMENT = "explicit_user_statement"


class MemoryLifecycleStatus(StrEnum):
    """Minimal lifecycle before later forgetting/consolidation stages."""

    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class EpisodicMemoryEvidence:
    """Exact user-authored source span retained with an episode."""

    evidence_id: str
    memory_id: str
    source_message_id: str
    provenance_kind: MemoryProvenanceKind
    quote: str
    observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_id", non_blank(self.evidence_id, "evidence_id", maximum=128)
        )
        object.__setattr__(self, "memory_id", non_blank(self.memory_id, "memory_id", maximum=128))
        object.__setattr__(
            self,
            "source_message_id",
            non_blank(self.source_message_id, "source_message_id", maximum=128),
        )
        object.__setattr__(
            self, "quote", non_blank(self.quote, "quote", maximum=MAX_EVIDENCE_QUOTE_CHARS)
        )
        object.__setattr__(self, "observed_at", aware_utc(self.observed_at, "observed_at"))


@dataclass(frozen=True, slots=True)
class EpisodicMemory:
    """Selective source-grounded representation of one completed interaction."""

    memory_id: str
    schema_version: int
    source_interaction_id: str
    occurred_at: datetime
    summary: str
    importance: float
    confidence: float
    created_at: datetime
    formation_method: str
    formation_version: int
    lifecycle_status: MemoryLifecycleStatus
    evidence: tuple[EpisodicMemoryEvidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_id", non_blank(self.memory_id, "memory_id", maximum=128))
        positive_version(self.schema_version, "memory schema_version")
        object.__setattr__(
            self,
            "source_interaction_id",
            non_blank(self.source_interaction_id, "source_interaction_id", maximum=128),
        )
        object.__setattr__(self, "occurred_at", aware_utc(self.occurred_at, "occurred_at"))
        object.__setattr__(
            self,
            "summary",
            non_blank(self.summary, "summary", maximum=MAX_EPISODE_SUMMARY_CHARS),
        )
        unit_interval(self.importance, "importance")
        unit_interval(self.confidence, "confidence")
        object.__setattr__(self, "created_at", aware_utc(self.created_at, "created_at"))
        object.__setattr__(
            self,
            "formation_method",
            non_blank(self.formation_method, "formation_method", maximum=128),
        )
        positive_version(self.formation_version, "formation_version")
        evidence = tuple(self.evidence)
        if not evidence:
            raise ValueError("episodic memory requires evidence")
        if any(item.memory_id != self.memory_id for item in evidence):
            raise ValueError("episodic evidence must reference its memory")
        object.__setattr__(self, "evidence", evidence)


@dataclass(frozen=True, slots=True)
class EpisodeFormationDecision:
    """Persisted owner decision; rejected/skipped proposals create no memory."""

    decision_id: str
    idempotency_key: str
    source_interaction_id: str
    formation_version: int
    policy_version: int
    kind: EpisodeDecisionKind
    reason_code: str
    decided_at: datetime
    trace_id: str
    formation_method: str
    provider: str
    model: str
    memory: EpisodicMemory | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_id", non_blank(self.decision_id, "decision_id", maximum=128)
        )
        object.__setattr__(
            self,
            "idempotency_key",
            non_blank(self.idempotency_key, "idempotency_key", maximum=256),
        )
        object.__setattr__(
            self,
            "source_interaction_id",
            non_blank(self.source_interaction_id, "source_interaction_id", maximum=128),
        )
        positive_version(self.formation_version, "formation_version")
        positive_version(self.policy_version, "policy_version")
        object.__setattr__(
            self, "reason_code", non_blank(self.reason_code, "reason_code", maximum=64)
        )
        object.__setattr__(self, "decided_at", aware_utc(self.decided_at, "decided_at"))
        object.__setattr__(self, "trace_id", non_blank(self.trace_id, "trace_id", maximum=128))
        object.__setattr__(
            self,
            "formation_method",
            non_blank(self.formation_method, "formation_method", maximum=128),
        )
        object.__setattr__(self, "provider", non_blank(self.provider, "provider", maximum=128))
        object.__setattr__(self, "model", non_blank(self.model, "model", maximum=256))
        if (self.kind is EpisodeDecisionKind.CREATED) != (self.memory is not None):
            raise ValueError("only a created decision may contain memory")
        if self.memory is not None:
            if self.memory.source_interaction_id != self.source_interaction_id:
                raise ValueError("decision memory source does not match")
            if self.memory.formation_version != self.formation_version:
                raise ValueError("decision memory formation version does not match")


@dataclass(frozen=True, slots=True)
class EpisodeDecisionDraft:
    """Owner decision before application assigns stable record IDs."""

    kind: EpisodeDecisionKind
    reason_code: str
    normalized_summary: str | None = None
    importance: float | None = None
    confidence: float | None = None
    source_quotes: tuple[tuple[str, str], ...] = ()


class MemoryManager:
    """Deterministically validate an untrusted episode proposal against its source."""

    def evaluate(
        self,
        proposal: EpisodeFormationProposal,
        interaction: ConversationInteraction,
    ) -> EpisodeDecisionDraft:
        """Return create/skip/reject without performing persistence or semantic inference."""

        if interaction.status is not InteractionStatus.COMPLETED:
            return EpisodeDecisionDraft(EpisodeDecisionKind.REJECTED, "interaction_not_completed")
        if proposal.schema_version != 1:
            return EpisodeDecisionDraft(EpisodeDecisionKind.REJECTED, "unsupported_proposal_schema")
        if not proposal.should_create:
            if (
                proposal.summary is not None
                or proposal.importance is not None
                or proposal.confidence is not None
                or proposal.evidence
            ):
                return EpisodeDecisionDraft(EpisodeDecisionKind.REJECTED, "invalid_skip_payload")
            return EpisodeDecisionDraft(EpisodeDecisionKind.SKIPPED, "provider_selected_skip")
        if proposal.summary is None or not proposal.summary.strip():
            return EpisodeDecisionDraft(EpisodeDecisionKind.REJECTED, "summary_missing")
        summary = proposal.summary.strip()
        if len(summary) > MAX_EPISODE_SUMMARY_CHARS:
            return EpisodeDecisionDraft(EpisodeDecisionKind.REJECTED, "summary_too_long")
        if proposal.importance is None or proposal.confidence is None:
            return EpisodeDecisionDraft(EpisodeDecisionKind.REJECTED, "scores_missing")
        if proposal.importance < MIN_EPISODE_IMPORTANCE:
            return EpisodeDecisionDraft(EpisodeDecisionKind.SKIPPED, "below_importance_threshold")
        if not proposal.evidence:
            return EpisodeDecisionDraft(EpisodeDecisionKind.REJECTED, "evidence_missing")

        messages = {
            message.message_id: message
            for message in (interaction.user_message, interaction.assistant_message)
            if message is not None
        }
        source_quotes: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for evidence in proposal.evidence:
            message = messages.get(evidence.message_id)
            quote = evidence.quote.strip()
            if message is None:
                return EpisodeDecisionDraft(EpisodeDecisionKind.REJECTED, "source_message_missing")
            if message.role is not HistoricalMessageRole.USER:
                return EpisodeDecisionDraft(
                    EpisodeDecisionKind.REJECTED, "assistant_output_not_evidence"
                )
            if len(quote) > MAX_EVIDENCE_QUOTE_CHARS or quote not in message.content:
                return EpisodeDecisionDraft(
                    EpisodeDecisionKind.REJECTED, "evidence_quote_not_grounded"
                )
            key = (message.message_id, quote)
            if key not in seen:
                seen.add(key)
                source_quotes.append(key)
        return EpisodeDecisionDraft(
            kind=EpisodeDecisionKind.CREATED,
            reason_code="source_grounded_episode",
            normalized_summary=summary,
            importance=proposal.importance,
            confidence=proposal.confidence,
            source_quotes=tuple(source_quotes),
        )


def episode_idempotency_key(interaction_id: str, formation_version: int) -> str:
    """Create the stable replay key for one source and algorithm version."""

    source = non_blank(interaction_id, "interaction_id")
    version = positive_version(formation_version, "formation_version")
    return f"episode:{source}:{version}"
