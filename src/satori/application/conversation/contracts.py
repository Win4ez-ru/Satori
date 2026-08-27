"""Immutable application contracts for Stage 4 conversation and replies."""

from dataclasses import dataclass, field
from datetime import datetime

from satori.application.cognition.contracts import CognitionPipelineTrace
from satori.core.conversation import ConversationUsage
from satori.core.provider_metrics import ProviderExecutionMetrics


def _non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _positive_version(value: int, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class RuntimeSelfModel:
    """Trusted transient self-knowledge derived from authoritative runtime state."""

    schema_version: int
    name: str
    identity_kind: str
    gender_expression: str
    russian_grammatical_gender: str
    continuity: str
    memory_capabilities: tuple[str, ...]
    affective_capabilities: tuple[str, ...]
    embodiment_status: str
    relationship_status: str
    language_model_role: str
    current_language_provider: str
    current_language_model: str
    current_development_limits: tuple[str, ...]

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "self-model schema_version")
        for field_name in (
            "name",
            "identity_kind",
            "gender_expression",
            "russian_grammatical_gender",
            "continuity",
            "embodiment_status",
            "relationship_status",
            "language_model_role",
            "current_language_provider",
            "current_language_model",
        ):
            object.__setattr__(
                self,
                field_name,
                _non_blank(getattr(self, field_name), f"self-model {field_name}"),
            )
        for field_name in (
            "memory_capabilities",
            "affective_capabilities",
            "current_development_limits",
        ):
            values = tuple(
                _non_blank(value, f"self-model {field_name} item")
                for value in getattr(self, field_name)
            )
            if len(values) != len(set(values)):
                raise ValueError(f"self-model {field_name} items must be unique")
            object.__setattr__(self, field_name, values)


@dataclass(frozen=True, slots=True)
class RuntimePersonalityGuidance:
    """One soft behavioral interpretation backed by current persistent trait values."""

    code: str
    source_traits: tuple[str, ...]
    strength: float
    instruction: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _non_blank(self.code, "guidance code"))
        source_traits = tuple(
            _non_blank(trait, "guidance source trait") for trait in self.source_traits
        )
        if not source_traits or len(source_traits) != len(set(source_traits)):
            raise ValueError("guidance source traits must be non-empty and unique")
        object.__setattr__(self, "source_traits", source_traits)
        if isinstance(self.strength, bool) or not 0.0 <= self.strength <= 1.0:
            raise ValueError("guidance strength must be between zero and one")
        object.__setattr__(
            self,
            "instruction",
            _non_blank(self.instruction, "guidance instruction"),
        )


@dataclass(frozen=True, slots=True)
class RuntimePersonalityCue:
    """Closed qualitative current-versus-activation expression cue."""

    code: str
    direction: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _non_blank(self.code, "personality cue code"))
        if self.code not in {
            "curious_analytical",
            "independent_position",
            "warm_perceptive",
            "light_irony",
            "considered_directness",
            "grounded_optimism",
        }:
            raise ValueError("personality cue code is not supported")
        if self.direction not in {"slightly_stronger", "slightly_softer"}:
            raise ValueError("personality cue direction is not supported")


@dataclass(frozen=True, slots=True)
class RuntimePersonalityExpression:
    """Versioned derived voice guidance; never a second personality source."""

    schema_version: int
    guidance: tuple[RuntimePersonalityGuidance, ...]
    cues: tuple[RuntimePersonalityCue, ...] = ()

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "personality expression schema_version")
        guidance = tuple(self.guidance)
        codes = tuple(item.code for item in guidance)
        if not codes or len(codes) != len(set(codes)):
            raise ValueError("personality guidance codes must be non-empty and unique")
        object.__setattr__(self, "guidance", guidance)
        cues = tuple(self.cues)
        cue_codes = tuple(item.code for item in cues)
        if len(cues) > 2 or len(cue_codes) != len(set(cue_codes)):
            raise ValueError("personality expression accepts at most two unique cues")
        if self.schema_version not in {1, 2} or (self.schema_version == 1 and cues):
            raise ValueError("personality expression schema and cues are inconsistent")
        object.__setattr__(self, "cues", cues)


@dataclass(frozen=True, slots=True)
class RuntimeTrait:
    """One bounded trait projected from authoritative personality state."""

    key: str
    value: float


@dataclass(frozen=True, slots=True)
class RuntimeValue:
    """One core value projected for generation context."""

    key: str
    strength: float
    description: str


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """Explicit persistence and retrieval capability boundaries."""

    conversation_scope: str = "single_turn"
    conversation_history_persisted: bool = True
    episodic_memory_storage_available: bool = True
    episodic_memory_retrieval_available: bool = False
    semantic_memory_retrieval_available: bool = False
    session_history_available: bool = False
    long_term_memory_available: bool = False
    relationship_state_available: bool = False
    emotional_state_available: bool = False
    user_model_available: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeSelfConsistencyMatrix:
    """Derived capability truth for one turn, never a second source of self state."""

    schema_version: int
    persistent_identity: bool
    feminine_russian: bool
    persistent_personality: bool
    persistent_values: bool
    canonical_history: bool
    episodic_memory: bool
    semantic_memory: bool
    perfect_recall: bool
    digital_affect: bool
    digital_mood: bool
    biological_physiology: bool
    relationship_state: bool
    love_primitive: bool
    dependency_state: bool
    physical_body: bool
    visual_input: bool
    human_equivalent_consciousness: str
    creator_identity: str

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "self-consistency schema_version")
        object.__setattr__(
            self,
            "human_equivalent_consciousness",
            _non_blank(
                self.human_equivalent_consciousness,
                "self-consistency human_equivalent_consciousness",
            ),
        )
        object.__setattr__(
            self,
            "creator_identity",
            _non_blank(self.creator_identity, "self-consistency creator_identity"),
        )


@dataclass(frozen=True, slots=True)
class RecentConversationTurn:
    """One canonical completed user/assistant pair used only for immediate continuity."""

    interaction_id: str
    user_message_id: str
    user_content: str
    assistant_message_id: str
    assistant_content: str

    def __post_init__(self) -> None:
        for field_name in ("interaction_id", "user_message_id", "assistant_message_id"):
            object.__setattr__(self, field_name, _non_blank(getattr(self, field_name), field_name))
        if not self.user_content.strip() or not self.assistant_content.strip():
            raise ValueError("recent conversation content must not be blank")


@dataclass(frozen=True, slots=True)
class RecentConversationContext:
    """Bounded read projection of canonical session history, never long-term memory."""

    schema_version: int
    turns: tuple[RecentConversationTurn, ...]
    content_chars: int
    excluded_turn_count: int

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "recent conversation schema_version")
        object.__setattr__(self, "turns", tuple(self.turns))
        if self.content_chars < 0 or self.excluded_turn_count < 0:
            raise ValueError("recent conversation counts must be non-negative")
        actual_chars = sum(
            len(turn.user_content) + len(turn.assistant_content) for turn in self.turns
        )
        if self.content_chars != actual_chars:
            raise ValueError("recent conversation content_chars does not match its turns")

    @property
    def user_evidence_ids(self) -> tuple[str, ...]:
        """Only canonical user messages can ground claims about what the user said."""

        return tuple(turn.user_message_id for turn in self.turns)


@dataclass(frozen=True, slots=True)
class RuntimeCharacterContext:
    """Bounded versioned projection of persistent self for one provider call."""

    schema_version: int
    personality_aggregate_version: int
    self_model: RuntimeSelfModel
    personality_expression: RuntimePersonalityExpression
    traits: tuple[RuntimeTrait, ...]
    values: tuple[RuntimeValue, ...]
    capabilities: RuntimeCapabilities
    self_consistency: RuntimeSelfConsistencyMatrix

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "runtime context schema_version")
        _positive_version(
            self.personality_aggregate_version,
            "personality aggregate_version",
        )
        object.__setattr__(self, "traits", tuple(self.traits))
        object.__setattr__(self, "values", tuple(self.values))
        if not self.traits or not self.values:
            raise ValueError("runtime context requires traits and values")
        if self.schema_version >= 16 and self.personality_expression.schema_version != 2:
            raise ValueError("runtime context v16 requires personality expression v2")


@dataclass(frozen=True, slots=True)
class BehaviorPrinciple:
    """One semantic rule in the trusted generation policy."""

    code: str
    instruction: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _non_blank(self.code, "principle code"))
        object.__setattr__(
            self,
            "instruction",
            _non_blank(self.instruction, "principle instruction"),
        )


@dataclass(frozen=True, slots=True)
class BehaviorPolicy:
    """Versioned trusted policy distinct from persistent personality data."""

    policy_id: str
    schema_version: int
    principles: tuple[BehaviorPrinciple, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _non_blank(self.policy_id, "policy_id"))
        _positive_version(self.schema_version, "behavior policy schema_version")
        object.__setattr__(self, "principles", tuple(self.principles))
        codes = tuple(principle.code for principle in self.principles)
        if not codes or len(codes) != len(set(codes)):
            raise ValueError("behavior policy principle codes must be non-empty and unique")


@dataclass(frozen=True, slots=True)
class ConversationContextManifest:
    """Concise observable description of what entered runtime context."""

    schema_version: int
    policy_id: str
    policy_schema_version: int
    character_context_schema_version: int
    included_sections: tuple[str, ...] = field(compare=False)
    user_content_chars: int
    personality_aggregate_version: int | None = None
    personality_expression_schema_version: int | None = None
    personality_expression_cues: tuple[str, ...] = ()
    available_past_evidence_ids: tuple[str, ...] = field(default=(), compare=False)
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
    emotion_appraisal_status: str = "not_requested"
    emotion_context_schema_version: int | None = None
    emotion_state_version: int | None = None
    mood_state_version: int | None = None
    emotion_state_as_of: datetime | None = None
    relationship_context_schema_version: int | None = None
    relationship_state_version: int | None = None
    relationship_expression_profile: str | None = field(default=None, compare=False)
    affect_expression_profile: str | None = field(default=None, compare=False)
    recent_conversation_turn_count: int = field(default=0, compare=False)
    recent_conversation_chars: int = field(default=0, compare=False)
    recent_conversation_user_message_ids: tuple[str, ...] = field(default=(), compare=False)
    # Stage 8.1 dialogue calibration is deliberately transient. These values describe
    # the live request/retry decision, are not written to canonical history, and thus
    # cannot participate in idempotent reply equality after a stored replay.
    disclosure_primary_mode: str = field(default="general", compare=False)
    disclosure_facets: tuple[str, ...] = field(default=(), compare=False)
    dialogue_coherence_schema_version: int | None = field(default=None, compare=False)
    consecutive_same_user_message_count: int = field(default=1, compare=False)
    recent_assistant_high_similarity: bool = field(default=False, compare=False)
    recent_generic_question_count: int = field(default=0, compare=False)
    active_style_corrections: tuple[str, ...] = field(default=(), compare=False)
    duplicate_response_detected: bool = field(default=False, compare=False)
    regeneration_attempted: bool = field(default=False, compare=False)
    response_regenerated: bool = field(default=False, compare=False)
    regeneration_reason: str | None = field(default=None, compare=False)
    cognition_pipeline_schema_version: int | None = field(default=None, compare=False)
    cognition_pipeline_status: str = field(default="not_requested", compare=False)
    cognition_perception_topics: tuple[str, ...] = field(default=(), compare=False)
    cognition_perception_signals: tuple[str, ...] = field(default=(), compare=False)
    cognition_need_dimensions: tuple[str, ...] = field(default=(), compare=False)
    cognition_position_stance: str | None = field(default=None, compare=False)
    cognition_intent_tags: tuple[str, ...] = field(default=(), compare=False)
    cognition_strategy_tone: str | None = field(default=None, compare=False)
    cognition_fallback_reasons: tuple[str, ...] = field(default=(), compare=False)
    cognition_template_id: str | None = field(default=None, compare=False)
    cognition_template_schema_version: int | None = field(default=None, compare=False)
    # Checkpoint 14.2 character expression is request-local metadata. It is deliberately not
    # persisted as personality, mood, relationship state or replay authority.
    character_expression_plan_schema_version: int | None = field(default=None, compare=False)
    character_expression_register: str | None = field(default=None, compare=False)
    character_owned_reaction: str | None = field(default=None, compare=False)
    character_semantic_move: str | None = field(default=None, compare=False)
    character_wit: str | None = field(default=None, compare=False)
    character_care: str | None = field(default=None, compare=False)
    character_openness: str | None = field(default=None, compare=False)
    character_initiative: str | None = field(default=None, compare=False)
    character_relational_ease: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "context manifest schema_version")
        versions = (
            self.personality_aggregate_version,
            self.personality_expression_schema_version,
        )
        cues = tuple(self.personality_expression_cues)
        if self.schema_version >= 16:
            if self.character_context_schema_version < 16:
                raise ValueError("context manifest v16 requires character context v16")
            if any(item is None for item in versions):
                raise ValueError("context manifest v16 requires personality projection metadata")
            assert self.personality_aggregate_version is not None
            assert self.personality_expression_schema_version is not None
            _positive_version(
                self.personality_aggregate_version,
                "personality aggregate_version",
            )
            _positive_version(
                self.personality_expression_schema_version,
                "personality expression schema_version",
            )
            if self.personality_expression_schema_version != 2:
                raise ValueError("context manifest v16 requires personality expression v2")
        elif any(item is not None for item in versions) or cues:
            raise ValueError("legacy context manifest cannot contain personality v16 metadata")
        if len(cues) > 2 or len(cues) != len(set(cues)):
            raise ValueError("context manifest accepts at most two unique personality cues")
        cue_codes: list[str] = []
        for cue in cues:
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
                raise ValueError("context manifest personality cue is not supported")
            cue_codes.append(code)
        if len(cue_codes) != len(set(cue_codes)):
            raise ValueError("context manifest personality cue codes must be unique")
        object.__setattr__(self, "personality_expression_cues", cues)
        if self.character_expression_plan_schema_version is not None:
            _positive_version(
                self.character_expression_plan_schema_version,
                "character expression plan schema_version",
            )
            if self.character_expression_plan_schema_version != 2:
                raise ValueError("context manifest requires character expression plan v2")
            if self.character_expression_register not in {
                "warm_independence",
                "wry_warmth",
                "guarded_concern",
                "quiet_open_care",
                "playful_edge",
                "lively_collaboration",
                "reflective_candor",
                "direct_repair",
                "thoughtful_precision",
            }:
                raise ValueError("character expression register is not supported")
            if self.character_owned_reaction not in {
                "reserved_interest",
                "guarded_approval",
                "sober_concern",
                "open_concern",
                "engaged_skepticism",
                "energized_interest",
                "reflective_concern",
                "accountable_regret",
                "focused_confidence",
            }:
                raise ValueError("character owned reaction is not supported")
            if self.character_semantic_move not in {
                "add_concrete_observation",
                "mark_hard_won_result",
                "connect_explicit_contrast",
                "respond_to_explicit_vulnerability",
                "test_current_claim",
                "advance_shared_idea",
                "own_and_repair",
                "answer_precisely",
                "acknowledge_repetition",
            }:
                raise ValueError("character semantic move is not supported")
            expression_axes = (
                self.character_wit,
                self.character_care,
                self.character_openness,
                self.character_initiative,
            )
            if self.policy_schema_version >= 19 and any(item is None for item in expression_axes):
                raise ValueError("behavior policy v19 requires complete character expression axes")
            if any(item is not None for item in expression_axes):
                if any(item is None for item in expression_axes):
                    raise ValueError("character expression axes must be supplied together")
                if self.character_wit not in {
                    "none",
                    "restrained",
                    "situation_directed",
                    "playful",
                }:
                    raise ValueError("character wit is not supported")
                if self.character_care not in {
                    "precise",
                    "understated",
                    "open",
                    "practical",
                }:
                    raise ValueError("character care is not supported")
                if self.character_openness not in {"reserved", "balanced", "direct"}:
                    raise ValueError("character openness is not supported")
                if self.character_initiative not in {
                    "responsive",
                    "concrete_next_step",
                    "active_collaboration",
                }:
                    raise ValueError("character initiative is not supported")
            if self.character_relational_ease not in {
                "baseline",
                "fresh",
                "developing",
                "established",
                "guarded",
            }:
                raise ValueError("character relational ease is not supported")
        elif any(
            item is not None
            for item in (
                self.character_expression_register,
                self.character_owned_reaction,
                self.character_semantic_move,
                self.character_wit,
                self.character_care,
                self.character_openness,
                self.character_initiative,
                self.character_relational_ease,
            )
        ):
            raise ValueError("character expression register requires a plan schema version")


@dataclass(frozen=True, slots=True)
class TalkInput:
    """One idempotent user turn, optionally within an explicit session."""

    user_text: str
    trace_id: str
    client_request_id: str
    session_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.user_text, str) or not self.user_text.strip():
            raise ValueError("user_text must not be blank")
        object.__setattr__(self, "trace_id", _non_blank(self.trace_id, "trace_id"))
        object.__setattr__(
            self,
            "client_request_id",
            _non_blank(self.client_request_id, "client_request_id"),
        )
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _non_blank(self.session_id, "session_id"))


@dataclass(frozen=True, slots=True)
class TurnPhaseTimings:
    """Monotonic duration decomposition for one canonical turn."""

    intake_ms: float = 0.0
    recent_context_ms: float = 0.0
    relationship_projection_ms: float = 0.0
    retrieval_embedding_ms: float = 0.0
    retrieval_search_ranking_ms: float = 0.0
    affect_materialization_ms: float = 0.0
    appraisal_request_build_ms: float = 0.0
    emotion_appraisal_ms: float = 0.0
    cognition_planning_ms: float = 0.0
    context_assembly_ms: float = 0.0
    conversation_generation_ms: float = 0.0
    response_regeneration_ms: float = 0.0
    grounding_validation_ms: float = 0.0
    canonical_commit_ms: float = 0.0
    committed_reply_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class SatoriReply:
    """Validated final text plus non-sensitive generation metadata."""

    text: str
    provider: str
    model: str
    finish_status: str
    usage: ConversationUsage | None
    context_manifest: ConversationContextManifest
    session_id: str
    interaction_id: str
    client_request_id: str
    replayed: bool = field(default=False, compare=False)
    timings: TurnPhaseTimings = field(default_factory=TurnPhaseTimings, compare=False)
    provider_metrics: ProviderExecutionMetrics | None = field(default=None, compare=False)
    appraisal_provider_metrics: ProviderExecutionMetrics | None = field(default=None, compare=False)
    retrieval_provider_metrics: ProviderExecutionMetrics | None = field(default=None, compare=False)
    cognition_trace: CognitionPipelineTrace | None = field(default=None, compare=False)
