"""Immutable application contracts for Stage 4 conversation and replies."""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V1,
    INTENT_REGISTRY_VERSION_V2,
    CognitionPipelineTrace,
    PerceptionSignal,
    PositionStance,
    ResponseVerbosity,
)
from satori.application.conversation.character_agency import (
    CHARACTER_AGENCY_DECISION_SCHEMA_VERSION,
    CharacterAgencyAct,
    CharacterAgencyDecision,
    CharacterAgencyDrive,
    CharacterAgencyInitiative,
    CharacterAgencyLead,
    CharacterAgencyReason,
    CharacterAgencyStatus,
    CharacterAgencySubject,
)
from satori.application.conversation.character_delivery_contracts import (
    CHARACTER_PRESENCE_PERSONALITY_CODES,
    CHARACTER_PRESENCE_VALUE_KEYS,
    CharacterAffectSignal,
    CharacterAffectSignalCode,
    CharacterDeliveryDecision,
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
    CharacterPresenceStrength,
    CharacterRelationshipSignal,
    CharacterRelationshipSignalCode,
    validate_affect_presence_semantics,
    validate_relationship_presence_semantics,
)
from satori.application.conversation.character_expression import (
    CharacterContinuationMode,
    CharacterGroundingMode,
    CharacterPressureLevel,
)
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosureMode,
    ConversationalDisclosurePlan,
    DisclosureFacet,
    DisclosureRequestKind,
    is_satori_self_disclosure_plan,
    uses_personal_self_disclosure_delivery,
)
from satori.core.conversation import ConversationUsage
from satori.core.provider_metrics import ProviderExecutionMetrics

CONVERSATION_INCLUDED_SECTIONS = (
    "behavior_policy",
    "self_model",
    "self_consistency_facets",
    "personality_expression",
    "values",
    "retrieved_episodic_memory",
    "retrieved_semantic_memory",
    "current_user_world_models",
    "satori_epistemic_positions",
    "satori_inclinations",
    "relationship_expression_state",
    "emotional_expression_state",
    "recent_conversation",
    "dialogue_coherence",
    "cognition_response_strategy",
    "character_agency_decision",
    "character_delivery_decision",
    "character_presence_projection",
    "current_user_input",
)


def _non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _positive_version(value: int, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be positive")
    return value


def _non_negative_count(value: int, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _normalized_unique_ids(values: object, field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValueError(f"{field_name} must be an ID collection")
    items = tuple(values)
    normalized_items: list[str] = []
    for value in items:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must contain string IDs")
        normalized_items.append(_non_blank(value, f"{field_name} item"))
    normalized = tuple(normalized_items)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must contain unique IDs")
    return normalized


def _presence_signal_code(
    value: object,
    *,
    allowed_codes: set[str],
    allow_direction: bool,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must contain strings")
    parts = value.split(":")
    if len(parts) not in ({2, 3} if allow_direction else {2}):
        raise ValueError(f"{field_name} signal shape is not supported")
    code, level, *direction = parts
    if code not in allowed_codes or level not in {item.value for item in CharacterPresenceStrength}:
        raise ValueError(f"{field_name} signal code or level is not supported")
    if direction and direction[0] not in {"slightly_stronger", "slightly_softer"}:
        raise ValueError(f"{field_name} signal direction is not supported")
    return code


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _non_blank(self.key, "runtime trait key"))
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not math.isfinite(self.value)
            or not 0.0 <= self.value <= 1.0
        ):
            raise ValueError("runtime trait value must be finite and between zero and one")


@dataclass(frozen=True, slots=True)
class RuntimeValue:
    """One core value projected for generation context."""

    key: str
    strength: float
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _non_blank(self.key, "runtime value key"))
        if (
            isinstance(self.strength, bool)
            or not isinstance(self.strength, (int, float))
            or not math.isfinite(self.strength)
            or not 0.0 <= self.strength <= 1.0
        ):
            raise ValueError("runtime value strength must be finite and between zero and one")
        object.__setattr__(
            self,
            "description",
            _non_blank(self.description, "runtime value description"),
        )


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
        traits = tuple(self.traits)
        values = tuple(self.values)
        if not traits or not values:
            raise ValueError("runtime context requires traits and values")
        if not all(isinstance(item, RuntimeTrait) for item in traits) or not all(
            isinstance(item, RuntimeValue) for item in values
        ):
            raise ValueError("runtime context requires typed traits and values")
        trait_keys = tuple(item.key for item in traits)
        value_keys = tuple(item.key for item in values)
        if len(trait_keys) != len(set(trait_keys)) or len(value_keys) != len(set(value_keys)):
            raise ValueError("runtime context trait and value keys must be unique")
        object.__setattr__(self, "traits", traits)
        object.__setattr__(self, "values", values)
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
    relationship_recent_strain: bool | None = field(default=None, compare=False)
    affect_expression_profile: str | None = field(default=None, compare=False)
    recent_conversation_turn_count: int = field(default=0, compare=False)
    recent_conversation_chars: int = field(default=0, compare=False)
    recent_conversation_user_message_ids: tuple[str, ...] = field(default=(), compare=False)
    # Stage 8.1 dialogue calibration is deliberately transient. These values describe
    # the live request/retry decision, are not written to canonical history, and thus
    # cannot participate in idempotent reply equality after a stored replay.
    disclosure_primary_mode: str = field(default="general", compare=False)
    disclosure_facets: tuple[str, ...] = field(default=(), compare=False)
    disclosure_request_kind: str = field(default="none", compare=False)
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
    cognition_preserve_uncertainty: bool | None = field(default=None, compare=False)
    cognition_intent_registry_version: int | None = field(default=None, compare=False)
    cognition_primary_intent: str | None = field(default=None, compare=False)
    cognition_intent_tags: tuple[str, ...] = field(default=(), compare=False)
    cognition_required_point_codes: tuple[str, ...] = field(default=(), compare=False)
    cognition_forbidden_claim_codes: tuple[str, ...] = field(default=(), compare=False)
    cognition_strategy_tone: str | None = field(default=None, compare=False)
    cognition_response_verbosity: str | None = field(default=None, compare=False)
    cognition_fallback_reasons: tuple[str, ...] = field(default=(), compare=False)
    cognition_template_registry_version: int | None = field(default=None, compare=False)
    cognition_template_id: str | None = field(default=None, compare=False)
    cognition_template_schema_version: int | None = field(default=None, compare=False)
    # Checkpoint 14.3 agency is one request-local, typed decision selected before
    # cognition realization. These fields are observability only, never a durable
    # desire, interest, position or replay authority.
    character_agency_decision_schema_version: int | None = field(
        default=None,
        compare=False,
    )
    character_agency_status: str | None = field(default=None, compare=False)
    character_agency_drive: str | None = field(default=None, compare=False)
    character_agency_act: str | None = field(default=None, compare=False)
    character_agency_subject: str | None = field(default=None, compare=False)
    character_agency_initiative: str | None = field(default=None, compare=False)
    character_agency_lead: str | None = field(default=None, compare=False)
    character_agency_source_personality_codes: tuple[str, ...] = field(
        default=(),
        compare=False,
    )
    character_agency_source_value_key: str | None = field(default=None, compare=False)
    character_agency_reason_codes: tuple[str, ...] = field(default=(), compare=False)
    character_agency_source_refs: tuple[str, ...] = field(default=(), compare=False)
    character_agency_subject_ref: str | None = field(default=None, compare=False)
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
    character_contribution_mode: str | None = field(default=None, compare=False)
    character_motivational_posture: str | None = field(default=None, compare=False)
    character_pressure_level: str | None = field(default=None, compare=False)
    character_acknowledgement_mode: str | None = field(default=None, compare=False)
    character_continuation_mode: str | None = field(default=None, compare=False)
    # Policy v24 replaces the historical multi-axis plan with one direct, transient decision.
    # These codes are observability only and never become replay or persistent-self authority.
    character_delivery_decision_schema_version: int | None = field(
        default=None,
        compare=False,
    )
    character_delivery_goal: str | None = field(default=None, compare=False)
    character_delivery_voice: str | None = field(default=None, compare=False)
    character_delivery_grounding: str | None = field(default=None, compare=False)
    character_delivery_continuation: str | None = field(default=None, compare=False)
    character_delivery_pressure: str | None = field(default=None, compare=False)
    character_delivery_position_stance: str | None = field(default=None, compare=False)
    character_delivery_preserve_uncertainty: bool | None = field(
        default=None,
        compare=False,
    )
    # Policy v26 observes the unified causal bridge without persisting a second state owner.
    character_presence_projection_schema_version: int | None = field(
        default=None,
        compare=False,
    )
    character_presence_personality_signals: tuple[str, ...] = field(
        default=(),
        compare=False,
    )
    character_presence_value_signals: tuple[str, ...] = field(default=(), compare=False)
    character_presence_affect_signals: tuple[str, ...] = field(default=(), compare=False)
    character_presence_relationship_signals: tuple[str, ...] = field(
        default=(),
        compare=False,
    )
    character_presence_memory_use_licensed: bool | None = field(
        default=None,
        compare=False,
    )

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "context manifest schema_version")
        _positive_version(self.policy_schema_version, "context manifest policy_schema_version")
        _positive_version(
            self.character_context_schema_version,
            "context manifest character_context_schema_version",
        )
        _non_negative_count(self.user_content_chars, "context manifest user_content_chars")
        object.__setattr__(self, "policy_id", _non_blank(self.policy_id, "policy_id"))
        included_sections = tuple(self.included_sections)
        if len(included_sections) != len(set(included_sections)) or not all(
            isinstance(section, str) and section for section in included_sections
        ):
            raise ValueError("context manifest included_sections must be unique non-blank strings")
        object.__setattr__(self, "included_sections", included_sections)
        if not set(included_sections) <= set(CONVERSATION_INCLUDED_SECTIONS):
            raise ValueError("context manifest included_sections contain an unknown section")
        if self.policy_id != f"satori.conversation.behavior.v{self.policy_schema_version}":
            raise ValueError("context manifest policy_id and schema_version must agree")
        if self.policy_schema_version >= 28:
            if self.schema_version != 17:
                raise ValueError("behavior policy v28 requires context manifest schema v17")
        elif self.schema_version >= 17:
            raise ValueError("historical behavior policy cannot use context manifest schema v17")
        optional_versions = (
            (
                self.personality_aggregate_version,
                "personality aggregate_version",
            ),
            (
                self.personality_expression_schema_version,
                "personality expression schema_version",
            ),
            (
                self.user_model_context_schema_version,
                "user model context schema_version",
            ),
            (
                self.world_model_context_schema_version,
                "world model context schema_version",
            ),
            (
                self.position_context_schema_version,
                "position context schema_version",
            ),
            (
                self.inclination_context_schema_version,
                "inclination context schema_version",
            ),
            (
                self.emotion_context_schema_version,
                "emotion context schema_version",
            ),
            (self.emotion_state_version, "emotion state_version"),
            (self.mood_state_version, "mood state_version"),
            (
                self.relationship_context_schema_version,
                "relationship context schema_version",
            ),
            (self.relationship_state_version, "relationship state_version"),
            (
                self.dialogue_coherence_schema_version,
                "dialogue coherence schema_version",
            ),
            (
                self.cognition_pipeline_schema_version,
                "cognition pipeline schema_version",
            ),
            (
                self.cognition_intent_registry_version,
                "cognition intent registry_version",
            ),
            (
                self.cognition_template_registry_version,
                "cognition template registry_version",
            ),
            (
                self.cognition_template_schema_version,
                "cognition template schema_version",
            ),
            (
                self.character_agency_decision_schema_version,
                "character agency decision schema_version",
            ),
            (
                self.character_expression_plan_schema_version,
                "character expression plan schema_version",
            ),
            (
                self.character_delivery_decision_schema_version,
                "character delivery decision schema_version",
            ),
            (
                self.character_presence_projection_schema_version,
                "character presence projection schema_version",
            ),
        )
        for version, field_name in optional_versions:
            if version is not None:
                _positive_version(version, field_name)
        _non_negative_count(
            self.recent_conversation_turn_count,
            "recent conversation turn_count",
        )
        _non_negative_count(
            self.recent_conversation_chars,
            "recent conversation chars",
        )
        _positive_version(
            self.consecutive_same_user_message_count,
            "consecutive same user message count",
        )
        _non_negative_count(
            self.recent_generic_question_count,
            "recent generic question count",
        )
        id_fields = (
            "available_past_evidence_ids",
            "retrieved_memory_ids",
            "retrieved_semantic_claim_ids",
            "user_model_context_claim_ids",
            "world_model_context_claim_ids",
            "position_context_ids",
            "inclination_context_ids",
            "recent_conversation_user_message_ids",
        )
        for field_name in id_fields:
            object.__setattr__(
                self,
                field_name,
                _normalized_unique_ids(getattr(self, field_name), field_name),
            )
        closed_statuses = (
            (
                self.retrieval_status,
                {"not_requested", "retrieved", "no_relevant_memory", "unavailable"},
                "retrieval",
            ),
            (
                self.semantic_retrieval_status,
                {"not_requested", "retrieved", "no_result"},
                "semantic retrieval",
            ),
            (
                self.model_context_status,
                {"not_requested", "available", "empty"},
                "model context",
            ),
            (
                self.position_context_status,
                {"not_requested", "available", "empty"},
                "position context",
            ),
            (
                self.inclination_context_status,
                {"not_requested", "available", "empty"},
                "inclination context",
            ),
            (
                self.emotion_appraisal_status,
                {"not_requested", "applied", "skipped", "rejected", "unavailable"},
                "emotion appraisal",
            ),
            (
                self.cognition_pipeline_status,
                {"not_requested", "applied", "fallback"},
                "cognition pipeline",
            ),
        )
        for status, allowed, field_name in closed_statuses:
            if status not in allowed:
                raise ValueError(f"context manifest {field_name} status is not supported")

        retrieval_requested = self.retrieval_status != "not_requested"
        if ("retrieved_episodic_memory" in included_sections) is not retrieval_requested:
            raise ValueError("episodic retrieval status and included section must agree")
        if (self.retrieval_status == "retrieved") is not bool(self.retrieved_memory_ids):
            raise ValueError("retrieval status and memory IDs must agree")

        semantic_requested = self.semantic_retrieval_status != "not_requested"
        if ("retrieved_semantic_memory" in included_sections) is not semantic_requested:
            raise ValueError("semantic retrieval status and included section must agree")
        if (self.semantic_retrieval_status == "retrieved") is not bool(
            self.retrieved_semantic_claim_ids
        ):
            raise ValueError("semantic retrieval status and claim IDs must agree")

        model_available = self.model_context_status == "available"
        if ("current_user_world_models" in included_sections) is not model_available:
            raise ValueError("model context status and included section must agree")
        model_versions = (
            self.user_model_context_schema_version,
            self.world_model_context_schema_version,
        )
        model_ids = (
            *self.user_model_context_claim_ids,
            *self.world_model_context_claim_ids,
        )
        if model_available:
            if (
                any(version is None for version in model_versions)
                or self.user_model_context_schema_version != self.world_model_context_schema_version
                or not model_ids
            ):
                raise ValueError("available model context requires matching schemas and claim IDs")
        elif any(version is not None for version in model_versions) or model_ids:
            raise ValueError("unavailable model context cannot contain schemas or claim IDs")

        position_available = self.position_context_status == "available"
        if ("satori_epistemic_positions" in included_sections) is not position_available:
            raise ValueError("position context status and included section must agree")
        if position_available:
            if self.position_context_schema_version is None or not self.position_context_ids:
                raise ValueError("available position context requires schema and position IDs")
        elif self.position_context_schema_version is not None or self.position_context_ids:
            raise ValueError("unavailable position context cannot contain schema or position IDs")

        inclination_available = self.inclination_context_status == "available"
        if ("satori_inclinations" in included_sections) is not inclination_available:
            raise ValueError("inclination context status and included section must agree")
        if inclination_available:
            if self.inclination_context_schema_version is None or not self.inclination_context_ids:
                raise ValueError(
                    "available inclination context requires schema and inclination IDs"
                )
        elif self.inclination_context_schema_version is not None or self.inclination_context_ids:
            raise ValueError(
                "unavailable inclination context cannot contain schema or inclination IDs"
            )
        if (
            isinstance(self.inclination_curiosity_influence, bool)
            or not isinstance(self.inclination_curiosity_influence, (int, float))
            or not math.isfinite(self.inclination_curiosity_influence)
            or not 0.0 <= self.inclination_curiosity_influence <= 0.20
        ):
            raise ValueError("inclination curiosity influence must be finite and in [0, 0.20]")
        if not inclination_available and self.inclination_curiosity_influence != 0.0:
            raise ValueError("unavailable inclination context cannot influence curiosity")

        emotion_requested = self.emotion_appraisal_status != "not_requested"
        if ("emotional_expression_state" in included_sections) is not emotion_requested:
            raise ValueError("emotion appraisal status and included section must agree")
        emotion_metadata = (
            self.emotion_context_schema_version,
            self.emotion_state_version,
            self.mood_state_version,
            self.emotion_state_as_of,
        )
        if emotion_requested:
            if any(item is None for item in emotion_metadata):
                raise ValueError("requested emotion context requires complete versioned metadata")
        elif any(item is not None for item in emotion_metadata):
            raise ValueError("unrequested emotion context cannot contain emotion metadata")

        relationship_versions = (
            self.relationship_context_schema_version,
            self.relationship_state_version,
        )
        relationship_available = all(version is not None for version in relationship_versions)
        if any(version is None for version in relationship_versions) and any(
            version is not None for version in relationship_versions
        ):
            raise ValueError(
                "relationship context schema and state versions must be supplied together"
            )
        if ("relationship_expression_state" in included_sections) is not relationship_available:
            raise ValueError("relationship context versions and included section must agree")
        if not relationship_available and self.relationship_expression_profile is not None:
            raise ValueError("relationship profile requires relationship context")

        recent_available = self.recent_conversation_turn_count > 0
        if ("recent_conversation" in included_sections) is not recent_available:
            raise ValueError("recent conversation count and included section must agree")
        if recent_available:
            if (
                self.recent_conversation_chars < 1
                or len(self.recent_conversation_user_message_ids)
                != self.recent_conversation_turn_count
            ):
                raise ValueError("recent conversation requires exact chars and user message IDs")
        elif self.recent_conversation_chars or self.recent_conversation_user_message_ids:
            raise ValueError("absent recent conversation cannot contain chars or message IDs")

        cognition_requested = self.cognition_pipeline_status != "not_requested"
        cognition_section_expected = cognition_requested and self.policy_schema_version < 24
        if ("cognition_response_strategy" in included_sections) is not cognition_section_expected:
            raise ValueError("cognition status and included section must agree")

        expected_evidence_ids = {
            *self.recent_conversation_user_message_ids,
            *self.retrieved_memory_ids,
            *self.retrieved_semantic_claim_ids,
            *self.user_model_context_claim_ids,
            *self.world_model_context_claim_ids,
            *self.position_context_ids,
            *self.inclination_context_ids,
        }
        if set(self.available_past_evidence_ids) != expected_evidence_ids:
            raise ValueError("available past evidence IDs must match included context IDs")
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
        disclosure_modes = {mode.value for mode in ConversationalDisclosureMode}
        disclosure_facet_codes = {facet.value for facet in DisclosureFacet}
        disclosure_request_kinds = {kind.value for kind in DisclosureRequestKind}
        disclosure_facets = tuple(self.disclosure_facets)
        if self.disclosure_primary_mode not in disclosure_modes:
            raise ValueError("context manifest disclosure mode is not supported")
        if self.disclosure_request_kind not in disclosure_request_kinds:
            raise ValueError("context manifest disclosure request kind is not supported")
        if (
            len(disclosure_facets) != len(set(disclosure_facets))
            or not set(disclosure_facets) <= disclosure_facet_codes
        ):
            raise ValueError("context manifest disclosure facets must be unique closed codes")
        object.__setattr__(self, "disclosure_facets", disclosure_facets)
        disclosure_plan = ConversationalDisclosurePlan(
            primary_mode=ConversationalDisclosureMode(self.disclosure_primary_mode),
            required_facets=tuple(DisclosureFacet(facet) for facet in disclosure_facets),
            policy_schema_version=self.policy_schema_version,
            request_kind=DisclosureRequestKind(self.disclosure_request_kind),
        )
        if (
            self.relationship_recent_strain is not None
            and type(self.relationship_recent_strain) is not bool
        ):
            raise ValueError("relationship_recent_strain must be boolean when supplied")
        if (
            self.relationship_context_schema_version is None
            and self.relationship_recent_strain is not None
        ):
            raise ValueError("relationship strain requires relationship context")
        cognition_requested = self.cognition_pipeline_status != "not_requested"
        if (
            self.policy_schema_version >= 24
            and cognition_requested
            and self.relationship_context_schema_version is not None
            and self.relationship_recent_strain is None
        ):
            raise ValueError("fresh v24 relationship context requires an explicit strain boolean")
        cognition_tuples = (
            tuple(self.cognition_perception_topics),
            tuple(self.cognition_perception_signals),
            tuple(self.cognition_need_dimensions),
            tuple(self.cognition_intent_tags),
            tuple(self.cognition_required_point_codes),
            tuple(self.cognition_forbidden_claim_codes),
            tuple(self.cognition_fallback_reasons),
        )
        for field_name, values in zip(
            (
                "cognition_perception_topics",
                "cognition_perception_signals",
                "cognition_need_dimensions",
                "cognition_intent_tags",
                "cognition_required_point_codes",
                "cognition_forbidden_claim_codes",
                "cognition_fallback_reasons",
            ),
            cognition_tuples,
            strict=True,
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must contain unique codes")
            object.__setattr__(self, field_name, values)
        known_perception_signals = {signal.value for signal in PerceptionSignal}
        if not set(self.cognition_perception_signals) <= known_perception_signals:
            raise ValueError("cognition perception signals must contain closed codes")
        self_disclosure_signal = PerceptionSignal.SELF_DISCLOSURE_REQUEST.value
        has_self_disclosure_signal = self_disclosure_signal in self.cognition_perception_signals
        if self.policy_schema_version < 25 and has_self_disclosure_signal:
            raise ValueError("self-disclosure cognition signal requires behavior policy v25")
        if (
            self.policy_schema_version >= 25
            and cognition_requested
            and has_self_disclosure_signal is not is_satori_self_disclosure_plan(disclosure_plan)
        ):
            raise ValueError("v25 disclosure plan and cognition signal must have exact parity")
        explicit_presence_signals = {
            PerceptionSignal.EXPLICIT_LISTEN_REQUEST.value,
            PerceptionSignal.HIGH_DISTRESS.value,
            PerceptionSignal.HARMFUL_OVEREXTENSION.value,
        }
        if (
            has_self_disclosure_signal
            and not set(self.cognition_perception_signals).intersection(explicit_presence_signals)
            and self.cognition_position_stance != PositionStance.ANSWER.value
        ):
            raise ValueError("unopposed self-disclosure request requires cognition answer stance")
        cognition_scalar_metadata = (
            self.cognition_pipeline_schema_version,
            self.cognition_position_stance,
            self.cognition_preserve_uncertainty,
            self.cognition_intent_registry_version,
            self.cognition_primary_intent,
            self.cognition_strategy_tone,
            self.cognition_response_verbosity,
            self.cognition_template_registry_version,
            self.cognition_template_id,
            self.cognition_template_schema_version,
        )
        if cognition_requested:
            if self.cognition_pipeline_status not in {"applied", "fallback"}:
                raise ValueError("context manifest cognition status is not supported")
            if type(self.cognition_preserve_uncertainty) is not bool:
                raise ValueError("cognition_preserve_uncertainty must be boolean when requested")
            required_cognition_tuples = (
                self.cognition_perception_topics,
                self.cognition_need_dimensions,
                self.cognition_intent_tags,
                self.cognition_required_point_codes,
                self.cognition_forbidden_claim_codes,
            )
            if any(item is None for item in cognition_scalar_metadata) or any(
                not values for values in required_cognition_tuples
            ):
                raise ValueError("requested cognition requires complete versioned metadata")
            assert self.cognition_pipeline_schema_version is not None
            assert self.cognition_intent_registry_version is not None
            assert self.cognition_primary_intent is not None
            assert self.cognition_response_verbosity is not None
            assert self.cognition_template_registry_version is not None
            assert self.cognition_template_id is not None
            assert self.cognition_template_schema_version is not None
            for value, field_name in (
                (self.cognition_pipeline_schema_version, "cognition pipeline schema_version"),
                (self.cognition_intent_registry_version, "cognition intent registry_version"),
                (
                    self.cognition_template_registry_version,
                    "cognition template registry_version",
                ),
                (self.cognition_template_schema_version, "cognition template schema_version"),
            ):
                _positive_version(value, field_name)
            expected_cognition = (
                (
                    INTENT_REGISTRY_VERSION_V2,
                    3,
                    "satori.cognition.response-substance",
                    3,
                )
                if self.policy_schema_version >= 25
                else (
                    INTENT_REGISTRY_VERSION_V2,
                    2,
                    "satori.cognition.response-substance",
                    2,
                )
                if self.policy_schema_version >= 24
                else (
                    INTENT_REGISTRY_VERSION_V1,
                    1,
                    "satori.cognition.response-strategy",
                    1,
                )
            )
            actual_cognition = (
                self.cognition_intent_registry_version,
                self.cognition_template_registry_version,
                self.cognition_template_id,
                self.cognition_template_schema_version,
            )
            if actual_cognition != expected_cognition:
                raise ValueError("behavior policy cognition registry metadata does not match")
            if self.cognition_primary_intent not in self.cognition_intent_tags:
                raise ValueError("cognition primary intent must be present in intent tags")
            if self.cognition_primary_intent not in self.cognition_required_point_codes:
                raise ValueError("cognition primary intent must be a required point")
            if set(self.cognition_forbidden_claim_codes) != {
                "unsupported_memory",
                "hidden_user_state",
                "durable_satori_belief",
                "false_certainty",
            }:
                raise ValueError("cognition forbidden claim boundary is incomplete")
            if self.cognition_response_verbosity not in {"brief", "medium", "detailed"}:
                raise ValueError("cognition response verbosity is not supported")
            if (self.cognition_pipeline_status == "fallback") is not bool(
                self.cognition_fallback_reasons
            ):
                raise ValueError(
                    "context manifest cognition fallback status and reasons must agree"
                )
        elif any(item is not None for item in cognition_scalar_metadata) or any(cognition_tuples):
            raise ValueError("unrequested cognition cannot contain cognition metadata")

        agency_personality_codes = _normalized_unique_ids(
            self.character_agency_source_personality_codes,
            "character_agency_source_personality_codes",
        )
        agency_reason_codes = _normalized_unique_ids(
            self.character_agency_reason_codes,
            "character_agency_reason_codes",
        )
        agency_source_refs = _normalized_unique_ids(
            self.character_agency_source_refs,
            "character_agency_source_refs",
        )
        object.__setattr__(
            self,
            "character_agency_source_personality_codes",
            agency_personality_codes,
        )
        object.__setattr__(self, "character_agency_reason_codes", agency_reason_codes)
        object.__setattr__(self, "character_agency_source_refs", agency_source_refs)
        agency_required_scalars = (
            self.character_agency_decision_schema_version,
            self.character_agency_status,
            self.character_agency_drive,
            self.character_agency_act,
            self.character_agency_subject,
            self.character_agency_initiative,
            self.character_agency_lead,
            self.character_agency_source_value_key,
        )
        agency_metadata_present = bool(
            any(item is not None for item in agency_required_scalars)
            or agency_personality_codes
            or agency_reason_codes
            or agency_source_refs
            or self.character_agency_subject_ref is not None
        )
        agency_decision: CharacterAgencyDecision | None = None
        agency_section_included = "character_agency_decision" in included_sections
        if self.policy_schema_version >= 28:
            transient_agency_omitted_for_replay = (
                not agency_metadata_present and not cognition_requested
            )
            if agency_section_included is transient_agency_omitted_for_replay:
                raise ValueError("character agency metadata and included section must agree")
            if not cognition_requested and agency_metadata_present:
                raise ValueError(
                    "character agency metadata requires a fresh behavior policy v28 turn"
                )
            if not transient_agency_omitted_for_replay:
                if (
                    self.character_agency_decision_schema_version
                    != CHARACTER_AGENCY_DECISION_SCHEMA_VERSION
                    or any(item is None for item in agency_required_scalars)
                    or not agency_personality_codes
                    or not agency_reason_codes
                    or not agency_source_refs
                ):
                    raise ValueError("behavior policy v28 requires a complete character agency")
                assert self.character_agency_status is not None
                assert self.character_agency_drive is not None
                assert self.character_agency_act is not None
                assert self.character_agency_subject is not None
                assert self.character_agency_initiative is not None
                assert self.character_agency_lead is not None
                assert self.character_agency_source_value_key is not None
                agency_decision = CharacterAgencyDecision(
                    schema_version=CHARACTER_AGENCY_DECISION_SCHEMA_VERSION,
                    status=CharacterAgencyStatus(self.character_agency_status),
                    drive=CharacterAgencyDrive(self.character_agency_drive),
                    act=CharacterAgencyAct(self.character_agency_act),
                    subject=CharacterAgencySubject(self.character_agency_subject),
                    initiative=CharacterAgencyInitiative(self.character_agency_initiative),
                    lead=CharacterAgencyLead(self.character_agency_lead),
                    source_personality_codes=agency_personality_codes,
                    source_value_key=self.character_agency_source_value_key,
                    reason_codes=tuple(CharacterAgencyReason(code) for code in agency_reason_codes),
                    source_refs=agency_source_refs,
                    subject_ref=self.character_agency_subject_ref,
                )
                if agency_decision.status.value != self.cognition_pipeline_status:
                    raise ValueError("character agency and completed cognition status must agree")
                if (
                    CharacterAgencyReason.SOCIAL_EXCHANGE in agency_decision.reason_codes
                    and disclosure_plan.primary_mode is not ConversationalDisclosureMode.SOCIAL
                ):
                    raise ValueError(
                        "social agency requires the authoritative social disclosure plan"
                    )
                if (
                    agency_decision.subject is CharacterAgencySubject.CANONICAL_POSITION
                    and agency_decision.subject_ref not in self.position_context_ids
                ):
                    raise ValueError(
                        "canonical position agency ref must be present in position context"
                    )
                if (
                    agency_decision.subject is CharacterAgencySubject.CANONICAL_INCLINATION
                    and agency_decision.subject_ref not in self.inclination_context_ids
                ):
                    raise ValueError(
                        "canonical inclination agency ref must be present in inclination context"
                    )
        elif agency_metadata_present or agency_section_included:
            raise ValueError("historical behavior policy cannot contain character agency metadata")

        legacy_character_fields = (
            self.character_expression_register,
            self.character_owned_reaction,
            self.character_semantic_move,
            self.character_wit,
            self.character_care,
            self.character_openness,
            self.character_initiative,
            self.character_relational_ease,
            self.character_contribution_mode,
            self.character_motivational_posture,
            self.character_pressure_level,
            self.character_acknowledgement_mode,
            self.character_continuation_mode,
        )
        delivery_fields = (
            self.character_delivery_goal,
            self.character_delivery_voice,
            self.character_delivery_grounding,
            self.character_delivery_continuation,
            self.character_delivery_pressure,
            self.character_delivery_position_stance,
            self.character_delivery_preserve_uncertainty,
        )
        if self.policy_schema_version >= 24:
            transient_delivery_omitted_for_replay = (
                self.character_delivery_decision_schema_version is None
                and all(item is None for item in delivery_fields)
                and self.cognition_pipeline_status == "not_requested"
            )
            if (
                "character_delivery_decision" in included_sections
            ) is transient_delivery_omitted_for_replay:
                raise ValueError("delivery decision metadata and included section must agree")
            if self.character_expression_plan_schema_version is not None or any(
                item is not None for item in legacy_character_fields
            ):
                raise ValueError("behavior policy v24 cannot contain legacy character plan fields")
            if not transient_delivery_omitted_for_replay and (
                self.character_delivery_decision_schema_version
                != (
                    5
                    if self.policy_schema_version >= 28
                    else 4
                    if self.policy_schema_version >= 27
                    else 3
                    if self.policy_schema_version == 26
                    else 2
                    if self.policy_schema_version >= 25
                    else 1
                )
                or any(item is None for item in delivery_fields)
            ):
                raise ValueError("behavior policy requires a complete delivery decision")
            if not transient_delivery_omitted_for_replay and (
                type(self.character_delivery_preserve_uncertainty) is not bool
            ):
                raise ValueError("character delivery uncertainty must be boolean")
            if not transient_delivery_omitted_for_replay and self.cognition_position_stance is None:
                raise ValueError("behavior policy v24 requires cognition stance metadata")
            if not transient_delivery_omitted_for_replay and (
                self.character_delivery_position_stance != self.cognition_position_stance
            ):
                raise ValueError("delivery decision must preserve cognition stance")
            if not transient_delivery_omitted_for_replay and (
                self.character_delivery_preserve_uncertainty != self.cognition_preserve_uncertainty
            ):
                raise ValueError("delivery decision must preserve cognition uncertainty")
            if not transient_delivery_omitted_for_replay and self.character_delivery_goal not in {
                "celebrate_and_continue",
                "practical_care",
                "stay_present",
                "challenge_claim",
                "advance_topic",
                "hold_boundary",
                "guarded_help",
                "brief_guarded_acknowledgement",
                "owned_response",
                "answer_precisely",
                "own_and_repair",
                "notice_repetition",
                "clarify_uncertainty",
                "social_connect",
                "self_disclose",
                "respond_to_objection",
                "close_topic",
            }:
                raise ValueError("character delivery goal is not supported")
            if (
                not transient_delivery_omitted_for_replay
                and self.policy_schema_version < 25
                and self.character_delivery_goal in {"social_connect", "self_disclose"}
            ):
                raise ValueError("character delivery goal requires behavior policy v25")
            if (
                not transient_delivery_omitted_for_replay
                and self.policy_schema_version < 27
                and self.character_delivery_goal in {"respond_to_objection", "close_topic"}
            ):
                raise ValueError("operational objection and closure require behavior policy v27")
            if (
                not transient_delivery_omitted_for_replay
                and self.character_delivery_goal == "social_connect"
                and self.disclosure_primary_mode != ConversationalDisclosureMode.SOCIAL.value
            ):
                raise ValueError("social delivery requires social disclosure mode")
            if (
                not transient_delivery_omitted_for_replay
                and self.character_delivery_goal == "self_disclose"
                and (
                    not uses_personal_self_disclosure_delivery(disclosure_plan)
                    or not has_self_disclosure_signal
                    or self.cognition_position_stance != "answer"
                    or self.character_delivery_grounding != "trusted_context"
                )
            ):
                raise ValueError(
                    "self-disclosure delivery requires an answer-bound personal disclosure plan"
                )
            if (
                not transient_delivery_omitted_for_replay
                and uses_personal_self_disclosure_delivery(disclosure_plan)
                and self.cognition_position_stance == PositionStance.ANSWER.value
                and self.character_delivery_goal
                not in {
                    CharacterDeliveryGoal.SELF_DISCLOSE.value,
                    CharacterDeliveryGoal.GUARDED_HELP.value,
                    CharacterDeliveryGoal.BRIEF_GUARDED_ACKNOWLEDGEMENT.value,
                    CharacterDeliveryGoal.HOLD_BOUNDARY.value,
                    CharacterDeliveryGoal.NOTICE_REPETITION.value,
                }
            ):
                raise ValueError(
                    "personal self-disclosure plan requires licensed personal delivery"
                )
            if not transient_delivery_omitted_for_replay and self.character_delivery_voice not in {
                "thoughtful_precision",
                "accountable_direct",
                "playful_edge",
                "lively_dry_warmth",
                "practical_guarded_care",
                "open_care",
                "engaged_skepticism",
                "energized_collaboration",
                "cool_reserve",
                "warm_independence",
                "reflective_candor",
                "easy_playful_warmth",
            }:
                raise ValueError("character delivery voice is not supported")
            if (
                not transient_delivery_omitted_for_replay
                and self.character_delivery_grounding
                not in {
                    "reaction_only",
                    "explicit_input_only",
                    "trusted_context",
                }
            ):
                raise ValueError("character delivery grounding is not supported")
            if (
                not transient_delivery_omitted_for_replay
                and self.character_delivery_continuation
                not in {
                    "complete",
                    "open",
                    "guarded",
                    "boundary",
                }
            ):
                raise ValueError("character delivery continuation is not supported")
            if (
                not transient_delivery_omitted_for_replay
                and self.character_delivery_pressure
                not in {
                    "none",
                    "gentle",
                    "moderate",
                    "firm",
                }
            ):
                raise ValueError("character delivery pressure is not supported")
            if not transient_delivery_omitted_for_replay:
                assert self.character_delivery_decision_schema_version is not None
                assert self.character_delivery_goal is not None
                assert self.character_delivery_voice is not None
                assert self.character_delivery_grounding is not None
                assert self.character_delivery_continuation is not None
                assert self.character_delivery_pressure is not None
                assert self.character_delivery_position_stance is not None
                assert self.character_delivery_preserve_uncertainty is not None
                assert self.cognition_intent_registry_version is not None
                assert self.cognition_primary_intent is not None
                assert self.cognition_response_verbosity is not None
                CharacterDeliveryDecision(
                    schema_version=self.character_delivery_decision_schema_version,
                    goal=CharacterDeliveryGoal(self.character_delivery_goal),
                    voice=CharacterDeliveryVoice(self.character_delivery_voice),
                    grounding=CharacterGroundingMode(self.character_delivery_grounding),
                    continuation=CharacterContinuationMode(self.character_delivery_continuation),
                    pressure=CharacterPressureLevel(self.character_delivery_pressure),
                    position_stance=PositionStance(self.character_delivery_position_stance),
                    preserve_uncertainty=self.character_delivery_preserve_uncertainty,
                    cognition_intent_registry_version=self.cognition_intent_registry_version,
                    cognition_primary_intent=self.cognition_primary_intent,
                    cognition_intent_tags=self.cognition_intent_tags,
                    required_point_codes=self.cognition_required_point_codes,
                    forbidden_claim_codes=self.cognition_forbidden_claim_codes,
                    response_verbosity=ResponseVerbosity(self.cognition_response_verbosity),
                    required_disclosure_facets=(
                        disclosure_plan.required_facets
                        if self.character_delivery_decision_schema_version >= 2
                        else ()
                    ),
                    agency=(
                        agency_decision
                        if self.character_delivery_decision_schema_version >= 5
                        else None
                    ),
                )
            presence_tuples = (
                tuple(self.character_presence_personality_signals),
                tuple(self.character_presence_value_signals),
                tuple(self.character_presence_affect_signals),
                tuple(self.character_presence_relationship_signals),
            )
            if self.policy_schema_version >= 26 and not transient_delivery_omitted_for_replay:
                if type(
                    self.character_presence_projection_schema_version
                ) is not int or self.character_presence_projection_schema_version != (
                    3
                    if self.policy_schema_version >= 28
                    else 2
                    if self.policy_schema_version >= 27
                    else 1
                ):
                    raise ValueError("behavior policy requires its exact character presence schema")
                if "character_presence_projection" not in included_sections:
                    raise ValueError("fresh character presence must be listed as included")
                if not all(presence_tuples[:2]):
                    raise ValueError("character presence requires personality and value signals")
                if self.policy_schema_version >= 27 and len(presence_tuples[1]) != 1:
                    raise ValueError("behavior policy v27+ requires exactly one value guard")
                if type(self.character_presence_memory_use_licensed) is not bool:
                    raise ValueError("character presence requires an exact memory-use license")
                if (self.emotion_context_schema_version is None) is not (not presence_tuples[2]):
                    raise ValueError("character presence affect observability is inconsistent")
                if (self.relationship_context_schema_version is None) is not (
                    not presence_tuples[3]
                ):
                    raise ValueError(
                        "character presence relationship observability is inconsistent"
                    )
                signal_contracts = (
                    (
                        "character_presence_personality_signals",
                        set(CHARACTER_PRESENCE_PERSONALITY_CODES),
                        True,
                    ),
                    (
                        "character_presence_value_signals",
                        set(CHARACTER_PRESENCE_VALUE_KEYS),
                        False,
                    ),
                    (
                        "character_presence_affect_signals",
                        {item.value for item in CharacterAffectSignalCode},
                        False,
                    ),
                    (
                        "character_presence_relationship_signals",
                        {item.value for item in CharacterRelationshipSignalCode},
                        False,
                    ),
                )
                for signals, (field_name, allowed_codes, allow_direction) in zip(
                    presence_tuples,
                    signal_contracts,
                    strict=True,
                ):
                    codes = tuple(
                        _presence_signal_code(
                            signal,
                            allowed_codes=allowed_codes,
                            allow_direction=allow_direction,
                            field_name=field_name,
                        )
                        for signal in signals
                    )
                    if len(signals) > 3 or len(codes) != len(set(codes)):
                        raise ValueError("character presence signals must be bounded and unique")
                    object.__setattr__(self, field_name, signals)
                affect_presence = tuple(
                    CharacterAffectSignal(
                        code=CharacterAffectSignalCode(signal.split(":")[0]),
                        level=CharacterPresenceStrength(signal.split(":")[1]),
                    )
                    for signal in presence_tuples[2]
                )
                relationship_presence = tuple(
                    CharacterRelationshipSignal(
                        code=CharacterRelationshipSignalCode(signal.split(":")[0]),
                        level=CharacterPresenceStrength(signal.split(":")[1]),
                    )
                    for signal in presence_tuples[3]
                )
                if affect_presence:
                    if self.affect_expression_profile not in {
                        "tense_non_hostile",
                        "positive_light",
                        "soft_negative_non_hostile",
                        "interested_calm",
                        "calm_even",
                    }:
                        raise ValueError("character presence affect profile is not supported")
                    validate_affect_presence_semantics(
                        self.affect_expression_profile,
                        affect_presence,
                    )
                elif self.affect_expression_profile is not None:
                    raise ValueError("affect profile requires character presence signals")
                if relationship_presence:
                    if self.relationship_expression_profile not in {
                        "fresh_undeveloped_neutral",
                        "developing_neutral",
                        "established_positive",
                        "guarded_only_when_relationally_relevant",
                    }:
                        raise ValueError("character presence relationship profile is not supported")
                    validate_relationship_presence_semantics(
                        self.relationship_expression_profile,
                        relationship_presence,
                    )
                elif self.relationship_expression_profile is not None:
                    raise ValueError("relationship profile requires character presence signals")
                has_recent_strain_signal = any(
                    signal.code is CharacterRelationshipSignalCode.RECENT_STRAIN
                    for signal in relationship_presence
                )
                if relationship_presence and (
                    self.relationship_recent_strain is not has_recent_strain_signal
                ):
                    raise ValueError(
                        "relationship strain observability and presence signal must agree"
                    )
                directional_presence_cues = {
                    f"{parts[0]}:{parts[2]}"
                    for signal in presence_tuples[0]
                    if len(parts := signal.split(":")) == 3
                }
                observed_cues = set(self.personality_expression_cues)
                cue_mismatch = (
                    not directional_presence_cues <= observed_cues
                    if self.policy_schema_version >= 27
                    else directional_presence_cues != observed_cues
                )
                if cue_mismatch:
                    raise ValueError(
                        "personality cue observability and presence directions must agree"
                    )
                if self.policy_schema_version >= 28:
                    if agency_decision is None:
                        raise ValueError("fresh behavior policy v28 requires character agency")
                    presence_personality_codes = {
                        signal.split(":", 1)[0] for signal in presence_tuples[0]
                    }
                    if not set(agency_decision.source_personality_codes) <= (
                        presence_personality_codes
                    ):
                        raise ValueError(
                            "character agency personality sources must be present in character "
                            "presence"
                        )
                    presence_value_keys = {signal.split(":", 1)[0] for signal in presence_tuples[1]}
                    if presence_value_keys != {agency_decision.source_value_key}:
                        raise ValueError(
                            "character agency value source must match the character presence guard"
                        )
                if (
                    self.character_delivery_goal
                    == CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE.value
                    and self.character_delivery_grounding
                    == CharacterGroundingMode.TRUSTED_CONTEXT.value
                    and self.character_presence_memory_use_licensed is not True
                ):
                    raise ValueError("trusted celebration grounding requires retrieved memory")
                expected_memory_use_license = (
                    self.retrieval_status == "retrieved"
                    and self.character_delivery_grounding
                    == CharacterGroundingMode.TRUSTED_CONTEXT.value
                )
                if self.character_presence_memory_use_licensed is not expected_memory_use_license:
                    raise ValueError(
                        "character presence memory-use license contradicts retrieval and grounding"
                    )
            elif (
                self.character_presence_projection_schema_version is not None
                or any(presence_tuples)
                or self.character_presence_memory_use_licensed is not None
            ):
                raise ValueError("character presence metadata requires a fresh policy v26 turn")
            elif "character_presence_projection" in included_sections:
                raise ValueError("included character presence requires fresh projection metadata")
        elif (
            self.character_delivery_decision_schema_version is not None
            or any(item is not None for item in delivery_fields)
            or self.character_presence_projection_schema_version is not None
            or self.character_presence_memory_use_licensed is not None
            or any(
                (
                    self.character_presence_personality_signals,
                    self.character_presence_value_signals,
                    self.character_presence_affect_signals,
                    self.character_presence_relationship_signals,
                )
            )
        ):
            raise ValueError("legacy behavior policy cannot contain v24 delivery fields")
        elif "character_delivery_decision" in included_sections:
            raise ValueError("legacy behavior policy cannot include a delivery decision")
        elif "character_presence_projection" in included_sections:
            raise ValueError("legacy behavior policy cannot include character presence")
        if self.character_expression_plan_schema_version is not None:
            _positive_version(
                self.character_expression_plan_schema_version,
                "character expression plan schema_version",
            )
            if self.character_expression_plan_schema_version not in {2, 3, 4, 5}:
                raise ValueError("context manifest character expression plan is not supported")
            if (
                23 <= self.policy_schema_version < 24
                and self.character_expression_plan_schema_version != 5
            ):
                raise ValueError("behavior policy v23 requires character expression plan v5")
            if (
                21 <= self.policy_schema_version < 23
                and self.character_expression_plan_schema_version != 4
            ):
                raise ValueError("behavior policy v21/v22 requires character expression plan v4")
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
                "cool_reserve",
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
                "restrained_hurt",
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
            support_axes = (
                self.character_contribution_mode,
                self.character_motivational_posture,
                self.character_pressure_level,
            )
            flow_axes = (
                self.character_acknowledgement_mode,
                self.character_continuation_mode,
            )
            if self.character_expression_plan_schema_version == 2:
                if self.policy_schema_version >= 20:
                    raise ValueError("behavior policy v20 requires character expression plan v3")
                if any(item is not None for item in (*support_axes, *flow_axes)):
                    raise ValueError("character expression plan v2 cannot contain support axes")
            else:
                if self.policy_schema_version < 20:
                    raise ValueError("character expression plan v3 requires behavior policy v20")
                if any(item is None for item in support_axes):
                    raise ValueError("character expression plan v3 requires complete support axes")
                if self.character_contribution_mode not in {
                    "owned_evaluation",
                    "emotional_reaction",
                    "playful_reframe",
                    "specific_question",
                    "grounded_direction",
                    "quiet_presence",
                    "protective_boundary",
                    "substantive_advance",
                }:
                    raise ValueError("character contribution mode is not supported")
                if self.character_motivational_posture not in {
                    "none",
                    "supportive_push",
                    "playful_challenge",
                    "firm_mobilization",
                    "protective_stop",
                }:
                    raise ValueError("character motivational posture is not supported")
                if self.character_pressure_level not in {
                    "none",
                    "gentle",
                    "moderate",
                    "firm",
                }:
                    raise ValueError("character pressure level is not supported")
                contribution = self.character_contribution_mode
                posture = self.character_motivational_posture
                pressure = self.character_pressure_level
                assert contribution is not None
                assert posture is not None
                assert pressure is not None
                allowed_pressure = {
                    "none": {"none"},
                    "supportive_push": {"gentle"},
                    "playful_challenge": {"gentle", "moderate"},
                    "firm_mobilization": {"moderate"},
                    "protective_stop": {"firm"},
                }
                if pressure not in allowed_pressure[posture]:
                    raise ValueError("character motivational posture and pressure are inconsistent")
                required_contribution = {
                    "supportive_push": "grounded_direction",
                    "playful_challenge": "playful_reframe",
                    "firm_mobilization": "grounded_direction",
                    "protective_stop": "protective_boundary",
                }
                expected_contribution = required_contribution.get(posture)
                if expected_contribution is not None and contribution != expected_contribution:
                    raise ValueError(
                        "character motivational posture and contribution are inconsistent"
                    )
                if contribution == "protective_boundary" and posture != "protective_stop":
                    raise ValueError("protective boundary requires protective stop posture")
                if self.character_expression_plan_schema_version == 3:
                    if any(item is not None for item in flow_axes):
                        raise ValueError("character expression plan v3 cannot contain flow axes")
                else:
                    if self.policy_schema_version < 21:
                        raise ValueError(
                            "character expression plan v4 requires behavior policy v21"
                        )
                    if any(item is None for item in flow_axes):
                        raise ValueError("character expression plan v4 requires complete flow axes")
                    if self.character_acknowledgement_mode not in {
                        "omit",
                        "implicit",
                        "contextual",
                    }:
                        raise ValueError("character acknowledgement mode is not supported")
                    if self.character_continuation_mode not in {
                        "complete",
                        "open",
                        "guarded",
                        "boundary",
                    }:
                        raise ValueError("character continuation mode is not supported")
        elif any(item is not None for item in legacy_character_fields):
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
