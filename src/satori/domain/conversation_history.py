"""Immutable Stage 4 session, interaction, and raw-message records."""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.core.conversation import ConversationProviderFailureReason
from satori.domain.validation import aware_utc, non_blank, positive_version


class SessionStatus(StrEnum):
    """Minimal conversational-container lifecycle."""

    OPEN = "open"
    CLOSED = "closed"


class SessionKind(StrEnum):
    """Whether a caller owns a multi-turn session or talk created a short one."""

    EXPLICIT = "explicit"
    IMPLICIT = "implicit"


class InteractionStatus(StrEnum):
    """Durable non-streaming interaction lifecycle."""

    PENDING = "pending"
    FAILED = "failed"
    COMPLETED = "completed"


class HistoricalMessageRole(StrEnum):
    """Only content actually exchanged between the user and Satori."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ConversationSession:
    """Stable conversation container; never a long-term memory."""

    session_id: str
    identity_id: str
    schema_version: int
    kind: SessionKind
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None = None
    counterparty_id: str = "local-default"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", non_blank(self.session_id, "session_id", maximum=128)
        )
        object.__setattr__(
            self,
            "identity_id",
            non_blank(self.identity_id, "identity_id", maximum=128),
        )
        positive_version(self.schema_version, "session schema_version")
        object.__setattr__(self, "started_at", aware_utc(self.started_at, "started_at"))
        object.__setattr__(
            self,
            "counterparty_id",
            non_blank(self.counterparty_id, "counterparty_id", maximum=128),
        )
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", aware_utc(self.ended_at, "ended_at"))
        if self.status is SessionStatus.OPEN and self.ended_at is not None:
            raise ValueError("open session cannot have ended_at")
        if self.status is SessionStatus.CLOSED and self.ended_at is None:
            raise ValueError("closed session requires ended_at")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("session ended_at cannot precede started_at")


@dataclass(frozen=True, slots=True)
class HistoricalMessage:
    """Append-only raw conversational content."""

    message_id: str
    session_id: str
    interaction_id: str
    schema_version: int
    role: HistoricalMessageRole
    content: str
    created_at: datetime
    sequence: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "message_id", non_blank(self.message_id, "message_id", maximum=128)
        )
        object.__setattr__(
            self, "session_id", non_blank(self.session_id, "session_id", maximum=128)
        )
        object.__setattr__(
            self,
            "interaction_id",
            non_blank(self.interaction_id, "interaction_id", maximum=128),
        )
        positive_version(self.schema_version, "message schema_version")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("message content must not be blank")
        object.__setattr__(self, "created_at", aware_utc(self.created_at, "created_at"))
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("message sequence must be a positive integer")


@dataclass(frozen=True, slots=True)
class InteractionProviderMetadata:
    """Minimal generation metadata stored separately from raw conversation text."""

    provider: str
    model: str
    finish_status: str
    context_schema_version: int
    context_manifest_schema_version: int
    policy_id: str
    policy_schema_version: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    retrieval_status: str = "not_requested"
    retrieved_memory_ids: tuple[str, ...] = ()
    semantic_retrieval_status: str = "not_requested"
    retrieved_semantic_claim_ids: tuple[str, ...] = ()
    model_context_status: str = "not_requested"
    user_model_context_schema_version: int | None = None
    user_model_context_claim_ids: tuple[str, ...] = ()
    world_model_context_schema_version: int | None = None
    world_model_context_claim_ids: tuple[str, ...] = ()
    position_context_status: str = "not_requested"
    position_context_schema_version: int | None = None
    position_context_ids: tuple[str, ...] = ()
    inclination_context_status: str = "not_requested"
    inclination_context_schema_version: int | None = None
    inclination_context_ids: tuple[str, ...] = ()
    inclination_curiosity_influence: float = 0.0
    personality_aggregate_version: int | None = None
    personality_expression_schema_version: int | None = None
    personality_expression_cues: tuple[str, ...] = ()
    emotion_appraisal_status: str = "not_requested"
    emotion_context_schema_version: int | None = None
    emotion_state_version: int | None = None
    mood_state_version: int | None = None
    emotion_state_as_of: datetime | None = None
    relationship_context_schema_version: int | None = None
    relationship_state_version: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", non_blank(self.provider, "provider", maximum=128))
        object.__setattr__(self, "model", non_blank(self.model, "model", maximum=256))
        object.__setattr__(
            self,
            "finish_status",
            non_blank(self.finish_status, "finish_status", maximum=64),
        )
        positive_version(self.context_schema_version, "context_schema_version")
        positive_version(
            self.context_manifest_schema_version,
            "context_manifest_schema_version",
        )
        object.__setattr__(self, "policy_id", non_blank(self.policy_id, "policy_id", maximum=128))
        positive_version(self.policy_schema_version, "policy_schema_version")
        object.__setattr__(
            self,
            "retrieval_status",
            non_blank(self.retrieval_status, "retrieval_status", maximum=64),
        )
        memory_ids = tuple(
            non_blank(memory_id, "retrieved_memory_id", maximum=128)
            for memory_id in self.retrieved_memory_ids
        )
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("retrieved_memory_ids must be unique")
        object.__setattr__(self, "retrieved_memory_ids", memory_ids)
        object.__setattr__(
            self,
            "semantic_retrieval_status",
            non_blank(
                self.semantic_retrieval_status,
                "semantic_retrieval_status",
                maximum=64,
            ),
        )
        semantic_claim_ids = tuple(
            non_blank(claim_id, "retrieved_semantic_claim_id", maximum=128)
            for claim_id in self.retrieved_semantic_claim_ids
        )
        if len(semantic_claim_ids) != len(set(semantic_claim_ids)):
            raise ValueError("retrieved_semantic_claim_ids must be unique")
        object.__setattr__(self, "retrieved_semantic_claim_ids", semantic_claim_ids)
        object.__setattr__(
            self,
            "model_context_status",
            non_blank(self.model_context_status, "model_context_status", maximum=32),
        )
        user_model_ids = tuple(
            non_blank(claim_id, "user_model_context_claim_id", maximum=128)
            for claim_id in self.user_model_context_claim_ids
        )
        world_model_ids = tuple(
            non_blank(claim_id, "world_model_context_claim_id", maximum=128)
            for claim_id in self.world_model_context_claim_ids
        )
        if len(user_model_ids) != len(set(user_model_ids)):
            raise ValueError("user_model_context_claim_ids must be unique")
        if len(world_model_ids) != len(set(world_model_ids)):
            raise ValueError("world_model_context_claim_ids must be unique")
        object.__setattr__(self, "user_model_context_claim_ids", user_model_ids)
        object.__setattr__(self, "world_model_context_claim_ids", world_model_ids)
        model_versions = (
            self.user_model_context_schema_version,
            self.world_model_context_schema_version,
        )
        if self.model_context_status in {"not_requested", "empty"}:
            if (
                any(value is not None for value in model_versions)
                or user_model_ids
                or world_model_ids
            ):
                raise ValueError("empty model context metadata cannot contain claims or versions")
        elif self.model_context_status == "available":
            if any(value is None for value in model_versions) or not (
                user_model_ids or world_model_ids
            ):
                raise ValueError("available model context metadata requires versions and claims")
            for field_name, value in (
                ("user_model_context_schema_version", self.user_model_context_schema_version),
                ("world_model_context_schema_version", self.world_model_context_schema_version),
            ):
                assert value is not None
                positive_version(value, field_name)
        else:
            raise ValueError("model_context_status is not supported")
        object.__setattr__(
            self,
            "position_context_status",
            non_blank(self.position_context_status, "position_context_status", maximum=32),
        )
        position_ids = tuple(
            non_blank(position_id, "position_context_id", maximum=128)
            for position_id in self.position_context_ids
        )
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("position_context_ids must be unique")
        object.__setattr__(self, "position_context_ids", position_ids)
        if self.position_context_status in {"not_requested", "empty"}:
            if self.position_context_schema_version is not None or position_ids:
                raise ValueError("empty position context metadata cannot contain state")
        elif self.position_context_status == "available":
            if self.position_context_schema_version is None or not position_ids:
                raise ValueError("available position context requires version and IDs")
            positive_version(
                self.position_context_schema_version, "position_context_schema_version"
            )
        else:
            raise ValueError("position_context_status is not supported")
        object.__setattr__(
            self,
            "inclination_context_status",
            non_blank(
                self.inclination_context_status,
                "inclination_context_status",
                maximum=32,
            ),
        )
        inclination_ids = tuple(
            non_blank(inclination_id, "inclination_context_id", maximum=128)
            for inclination_id in self.inclination_context_ids
        )
        if len(inclination_ids) != len(set(inclination_ids)):
            raise ValueError("inclination_context_ids must be unique")
        object.__setattr__(self, "inclination_context_ids", inclination_ids)
        influence = self.inclination_curiosity_influence
        if (
            isinstance(influence, bool)
            or not math.isfinite(influence)
            or not 0.0 <= influence <= 0.20
        ):
            raise ValueError("inclination_curiosity_influence must be in [0, 0.20]")
        if self.inclination_context_status in {"not_requested", "empty"}:
            if (
                self.inclination_context_schema_version is not None
                or inclination_ids
                or influence != 0.0
            ):
                raise ValueError("empty inclination context metadata cannot contain state")
        elif self.inclination_context_status == "available":
            if self.inclination_context_schema_version is None or not inclination_ids:
                raise ValueError("available inclination context requires version and IDs")
            positive_version(
                self.inclination_context_schema_version,
                "inclination_context_schema_version",
            )
        else:
            raise ValueError("inclination_context_status is not supported")
        personality_versions = (
            self.personality_aggregate_version,
            self.personality_expression_schema_version,
        )
        personality_cues = tuple(
            non_blank(item, "personality_expression_cue", maximum=96)
            for item in self.personality_expression_cues
        )
        if len(personality_cues) > 2 or len(personality_cues) != len(set(personality_cues)):
            raise ValueError("personality expression accepts at most two unique cues")
        if self.context_manifest_schema_version >= 16:
            if self.context_schema_version < 16:
                raise ValueError("manifest v16 requires character context v16")
            if any(item is None for item in personality_versions):
                raise ValueError("manifest v16 requires personality projection metadata")
            assert self.personality_aggregate_version is not None
            assert self.personality_expression_schema_version is not None
            positive_version(
                self.personality_aggregate_version,
                "personality_aggregate_version",
            )
            positive_version(
                self.personality_expression_schema_version,
                "personality_expression_schema_version",
            )
            if self.personality_expression_schema_version != 2:
                raise ValueError("manifest v16 requires personality expression v2")
        elif any(item is not None for item in personality_versions) or personality_cues:
            raise ValueError("legacy manifest cannot contain personality projection metadata")
        personality_cue_codes: list[str] = []
        for cue in personality_cues:
            code, separator, direction = cue.partition(":")
            if (
                not separator
                or code
                not in {
                    "curious_analytical",
                    "independent_position",
                    "warm_perceptive",
                    "light_irony",
                    "considered_directness",
                    "grounded_optimism",
                }
                or direction not in {"slightly_stronger", "slightly_softer"}
            ):
                raise ValueError("personality expression cue is not supported")
            personality_cue_codes.append(code)
        if len(personality_cue_codes) != len(set(personality_cue_codes)):
            raise ValueError("personality expression cue codes must be unique")
        object.__setattr__(self, "personality_expression_cues", personality_cues)
        object.__setattr__(
            self,
            "emotion_appraisal_status",
            non_blank(
                self.emotion_appraisal_status,
                "emotion_appraisal_status",
                maximum=64,
            ),
        )
        emotion_values = (
            self.emotion_context_schema_version,
            self.emotion_state_version,
            self.mood_state_version,
        )
        if self.emotion_appraisal_status == "not_requested":
            if any(value is not None for value in emotion_values) or self.emotion_state_as_of:
                raise ValueError("not-requested emotion metadata must not contain state versions")
        else:
            if any(value is None for value in emotion_values) or self.emotion_state_as_of is None:
                raise ValueError("emotion context metadata requires versions and state time")
            for field_name, value in (
                ("emotion_context_schema_version", self.emotion_context_schema_version),
                ("emotion_state_version", self.emotion_state_version),
                ("mood_state_version", self.mood_state_version),
            ):
                assert value is not None
                positive_version(value, field_name)
            object.__setattr__(
                self,
                "emotion_state_as_of",
                aware_utc(self.emotion_state_as_of, "emotion_state_as_of"),
            )
        for field_name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer or None")
        relationship_values = (
            self.relationship_context_schema_version,
            self.relationship_state_version,
        )
        if any(value is not None for value in relationship_values):
            if any(value is None for value in relationship_values):
                raise ValueError("relationship context metadata requires both versions")
            for field_name, value in (
                ("relationship_context_schema_version", self.relationship_context_schema_version),
                ("relationship_state_version", self.relationship_state_version),
            ):
                assert value is not None
                positive_version(value, field_name)


@dataclass(frozen=True, slots=True)
class InteractionFailureMetadata:
    """Content-free reason for one failed interaction attempt."""

    kind: str
    reason: ConversationProviderFailureReason | None = None
    provider: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", non_blank(self.kind, "failure kind", maximum=128))
        provider_values = (self.provider, self.model)
        if self.reason is None:
            if any(value is not None for value in provider_values):
                raise ValueError("legacy/non-provider failure cannot contain provider metadata")
            return
        if not isinstance(self.reason, ConversationProviderFailureReason):
            raise ValueError("failure reason must be a ConversationProviderFailureReason")
        if any(value is None for value in provider_values):
            raise ValueError("typed provider failure requires provider and model")
        assert self.provider is not None
        assert self.model is not None
        object.__setattr__(
            self,
            "provider",
            non_blank(self.provider, "failure provider", maximum=128),
        )
        object.__setattr__(
            self,
            "model",
            non_blank(self.model, "failure model", maximum=256),
        )


@dataclass(frozen=True, slots=True)
class ConversationInteraction:
    """One user-to-Satori turn with an atomic completed-message pair."""

    interaction_id: str
    session_id: str
    client_request_id: str
    trace_id: str
    schema_version: int
    status: InteractionStatus
    started_at: datetime
    user_message: HistoricalMessage
    assistant_message: HistoricalMessage | None = None
    completed_at: datetime | None = None
    provider_metadata: InteractionProviderMetadata | None = None
    failure: InteractionFailureMetadata | None = None
    relationship_processing_required: bool = True
    model_processing_required: bool = True
    position_processing_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "interaction_id",
            non_blank(self.interaction_id, "interaction_id", maximum=128),
        )
        object.__setattr__(
            self, "session_id", non_blank(self.session_id, "session_id", maximum=128)
        )
        object.__setattr__(
            self,
            "client_request_id",
            non_blank(self.client_request_id, "client_request_id", maximum=128),
        )
        object.__setattr__(self, "trace_id", non_blank(self.trace_id, "trace_id", maximum=128))
        positive_version(self.schema_version, "interaction schema_version")
        if type(self.relationship_processing_required) is not bool:
            raise ValueError("relationship_processing_required must be boolean")
        if type(self.model_processing_required) is not bool:
            raise ValueError("model_processing_required must be boolean")
        if type(self.position_processing_required) is not bool:
            raise ValueError("position_processing_required must be boolean")
        object.__setattr__(self, "started_at", aware_utc(self.started_at, "started_at"))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", aware_utc(self.completed_at, "completed_at"))
        if self.user_message.role is not HistoricalMessageRole.USER:
            raise ValueError("interaction user_message must have the user role")
        if (
            self.user_message.interaction_id != self.interaction_id
            or self.user_message.session_id != self.session_id
            or self.user_message.sequence != 1
        ):
            raise ValueError("interaction user_message references or sequence do not match")
        if self.status is InteractionStatus.COMPLETED:
            if (
                self.assistant_message is None
                or self.completed_at is None
                or self.provider_metadata is None
                or self.failure is not None
            ):
                raise ValueError("completed interaction requires reply metadata and no failure")
            if self.assistant_message.role is not HistoricalMessageRole.ASSISTANT:
                raise ValueError("assistant_message must have the assistant role")
            if (
                self.assistant_message.interaction_id != self.interaction_id
                or self.assistant_message.session_id != self.session_id
                or self.assistant_message.sequence != 2
            ):
                raise ValueError(
                    "interaction assistant_message references or sequence do not match"
                )
            if self.completed_at < self.started_at:
                raise ValueError("completed_at cannot precede started_at")
        else:
            if (
                self.assistant_message is not None
                or self.completed_at is not None
                or self.provider_metadata is not None
            ):
                raise ValueError(
                    "incomplete interaction cannot contain a committed assistant reply"
                )
            if self.status is InteractionStatus.PENDING and self.failure is not None:
                raise ValueError("pending interaction cannot contain failure metadata")
            if self.status is InteractionStatus.FAILED and self.failure is None:
                raise ValueError("failed interaction requires failure metadata")


@dataclass(frozen=True, slots=True)
class ConversationHistorySnapshot:
    """Immutable read model returned by history queries."""

    sessions: tuple[ConversationSession, ...]
    interactions: tuple[ConversationInteraction, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sessions", tuple(self.sessions))
        object.__setattr__(self, "interactions", tuple(self.interactions))
