"""Immutable, bounded Stage 10 cognition artifacts without raw chain-of-thought."""

import math
from dataclasses import dataclass
from enum import StrEnum

COGNITION_PIPELINE_SCHEMA_VERSION = 1
PERCEPTION_SCHEMA_VERSION = 1
NEED_MIX_SCHEMA_VERSION = 1
RETRIEVAL_PLAN_SCHEMA_VERSION = 1
APPRAISAL_ARTIFACT_SCHEMA_VERSION = 1
INTERNAL_POSITION_SCHEMA_VERSION = 1
INTENT_SCHEMA_VERSION = 1
RESPONSE_STRATEGY_SCHEMA_VERSION = 1
INTENT_REGISTRY_VERSION_V1 = 1
INTENT_REGISTRY_VERSION_V2 = 2
# Historical callers keep the Stage 10 registry unless they opt into the v24 pipeline.
INTENT_REGISTRY_VERSION = INTENT_REGISTRY_VERSION_V1


def _non_blank(value: str, field_name: str, *, maximum: int = 280) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    return normalized


def _positive_version(value: int, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _unit_interval(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and between zero and one")
    return value


def _unique_strings(
    values: tuple[str, ...],
    field_name: str,
    *,
    allow_empty: bool = True,
    maximum_items: int = 16,
    maximum_chars: int = 128,
) -> tuple[str, ...]:
    normalized = tuple(
        _non_blank(value, f"{field_name} item", maximum=maximum_chars) for value in values
    )
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if len(normalized) > maximum_items:
        raise ValueError(f"{field_name} exceeds {maximum_items} items")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must contain unique items")
    return normalized


class CognitionArtifactStatus(StrEnum):
    """Observable outcome of one transient pipeline step."""

    APPLIED = "applied"
    SKIPPED = "skipped"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    FALLBACK = "fallback"


class CognitionOwner(StrEnum):
    """Component accountable for an artifact or decision."""

    COGNITION = "cognition"
    MEMORY_QUERY = "memory_query"
    EMOTION_MANAGER = "emotion_manager"


class PerceivedTopic(StrEnum):
    """Small non-entity topic registry; never a durable user profile."""

    GENERAL = "general"
    TECHNICAL = "technical"
    EMOTIONAL = "emotional"
    RELATIONSHIP = "relationship"
    MEMORY = "memory"
    SELF = "self"
    PROJECT = "project"
    DECISION = "decision"
    CREATIVE = "creative"


class PerceptionSignal(StrEnum):
    """Current-turn signals derived without asserting hidden user state."""

    QUESTION = "question"
    REQUEST = "request"
    DISTRESS_LANGUAGE = "distress_language"
    CORRECTION = "correction"
    UNCERTAINTY_LANGUAGE = "uncertainty_language"
    CHALLENGE_REQUEST = "challenge_request"
    REPEATED_TURN = "repeated_turn"
    EXPLICIT_LISTEN_REQUEST = "explicit_listen_request"
    HIGH_DISTRESS = "high_distress"
    HARMFUL_OVEREXTENSION = "harmful_overextension"
    EXPLICIT_MOTIVATION_REQUEST = "explicit_motivation_request"
    EXPLICIT_TASK_ABANDONMENT = "explicit_task_abandonment"
    EXPLICIT_REPAIR_OFFER = "explicit_repair_offer"
    SELF_DISCLOSURE_REQUEST = "self_disclosure_request"


class NeedDimension(StrEnum):
    """Extensible V1 weighted need registry."""

    INFORMATION = "information"
    ANALYSIS = "analysis"
    EMOTIONAL_PRESENCE = "emotional_presence"
    DECISION_SUPPORT = "decision_support"
    CHALLENGE = "challenge"
    ACCOUNTABILITY = "accountability"
    REASSURANCE = "reassurance"
    CREATIVE_COLLABORATION = "creative_collaboration"


class RetrievalQueryMode(StrEnum):
    """How the existing retrieval request was sourced."""

    CURRENT_INPUT = "current_input"
    CONSERVATIVE_FALLBACK = "conservative_fallback"


class PositionStance(StrEnum):
    """Concise current-turn stance, not a durable Satori belief."""

    ANSWER = "answer"
    LISTEN = "listen"
    CHALLENGE = "challenge"
    UNCERTAIN = "uncertain"
    COLLABORATE = "collaborate"
    ACKNOWLEDGE = "acknowledge"


_PRIMARY_INTENT_BY_POSITION_STANCE = {
    PositionStance.ANSWER: "answer_directly",
    PositionStance.LISTEN: "listen_and_reflect",
    PositionStance.CHALLENGE: "challenge_gently",
    PositionStance.UNCERTAIN: "clarify_uncertainty",
    PositionStance.COLLABORATE: "support_decision",
    PositionStance.ACKNOWLEDGE: "acknowledge_correction",
}


class ResponseTone(StrEnum):
    """Bounded expression tone selected after position."""

    WARM_DIRECT = "warm_direct"
    WARM_GENTLE = "warm_gentle"
    ANALYTICAL = "analytical"
    CONCISE_NEUTRAL = "concise_neutral"
    PLAYFUL = "playful"


class ResponseVerbosity(StrEnum):
    """Qualitative response length target."""

    BRIEF = "brief"
    MEDIUM = "medium"
    DETAILED = "detailed"


KNOWN_INTENT_TAGS_V1 = frozenset(
    {
        "answer_directly",
        "listen_and_reflect",
        "analyze",
        "acknowledge_correction",
        "clarify_uncertainty",
        "challenge_gently",
        "support_decision",
        "collaborate_creatively",
        "preserve_evidence_boundary",
        "ask_specific_follow_up",
    }
)
KNOWN_INTENT_TAGS_V2 = KNOWN_INTENT_TAGS_V1 | {
    "notice_repetition",
    "hold_safety_boundary",
    "receive_repair",
}
V2_META_INTENT_TAGS = frozenset({"notice_repetition", "hold_safety_boundary", "receive_repair"})
V2_ACTION_INTENT_TAGS = frozenset(
    {*_PRIMARY_INTENT_BY_POSITION_STANCE.values(), *V2_META_INTENT_TAGS}
)
V2_SUPPLEMENTAL_POINT_CODES = frozenset(
    {
        "address_current_request",
        "state_uncertainty",
        "presence_before_advice",
        "topic_relevant_inclination",
    }
)
V2_RESPONSE_POINT_CODES = V2_ACTION_INTENT_TAGS | V2_SUPPLEMENTAL_POINT_CODES
KNOWN_INTENT_TAGS = KNOWN_INTENT_TAGS_V1
_KNOWN_INTENT_TAGS_BY_REGISTRY = {
    INTENT_REGISTRY_VERSION_V1: KNOWN_INTENT_TAGS_V1,
    INTENT_REGISTRY_VERSION_V2: KNOWN_INTENT_TAGS_V2,
}


@dataclass(frozen=True, slots=True)
class CognitionDialogueSignals:
    """Only bounded transient dialogue facts needed by the cognition planner."""

    repeated_turn: bool = False
    correction_active: bool = False
    no_routine_questions: bool = False
    current_activity: bool = False
    explicit_listen_request: bool = False
    high_distress: bool = False
    harmful_overextension: bool = False
    explicit_motivation_request: bool = False
    explicit_task_abandonment: bool = False
    explicit_repair_offer: bool = False
    self_disclosure_request: bool = False


@dataclass(frozen=True, slots=True)
class Perception:
    """Current-message perception with no raw content or inferred biography."""

    schema_version: int
    status: CognitionArtifactStatus
    topics: tuple[PerceivedTopic, ...]
    signals: tuple[PerceptionSignal, ...]
    confidence: float
    source_refs: tuple[str, ...]
    owner: CognitionOwner = CognitionOwner.COGNITION

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "perception schema_version")
        topics = tuple(self.topics)
        signals = tuple(self.signals)
        if not topics or len(topics) != len(set(topics)):
            raise ValueError("perception topics must be non-empty and unique")
        if len(signals) != len(set(signals)):
            raise ValueError("perception signals must be unique")
        _unit_interval(self.confidence, "perception confidence")
        object.__setattr__(self, "topics", topics)
        object.__setattr__(self, "signals", signals)
        object.__setattr__(
            self,
            "source_refs",
            _unique_strings(self.source_refs, "perception source_refs", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class NeedWeight:
    """One weighted current-turn need."""

    dimension: NeedDimension
    weight: float

    def __post_init__(self) -> None:
        _unit_interval(self.weight, f"{self.dimension.value} need weight")


@dataclass(frozen=True, slots=True)
class NeedMix:
    """Weighted needs preserve mixed requests and explicit uncertainty."""

    schema_version: int
    status: CognitionArtifactStatus
    needs: tuple[NeedWeight, ...]
    uncertainty: float
    risk_flags: tuple[str, ...]
    source_refs: tuple[str, ...]
    owner: CognitionOwner = CognitionOwner.COGNITION

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "need mix schema_version")
        needs = tuple(self.needs)
        dimensions = tuple(need.dimension for need in needs)
        if not needs or len(dimensions) != len(set(dimensions)):
            raise ValueError("need mix dimensions must be non-empty and unique")
        _unit_interval(self.uncertainty, "need mix uncertainty")
        object.__setattr__(self, "needs", needs)
        object.__setattr__(
            self,
            "risk_flags",
            _unique_strings(self.risk_flags, "need mix risk_flags", maximum_items=8),
        )
        object.__setattr__(
            self,
            "source_refs",
            _unique_strings(self.source_refs, "need mix source_refs", allow_empty=False),
        )

    def weight(self, dimension: NeedDimension) -> float:
        """Return one registered weight or zero when absent."""

        return next((item.weight for item in self.needs if item.dimension is dimension), 0.0)


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    """Typed manifest for the existing source-text retrieval query."""

    schema_version: int
    status: CognitionArtifactStatus
    query_mode: RetrievalQueryMode
    include_episodic: bool
    include_semantic: bool
    include_current_models: bool
    source_refs: tuple[str, ...]
    owner: CognitionOwner = CognitionOwner.MEMORY_QUERY

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "retrieval plan schema_version")
        object.__setattr__(
            self,
            "source_refs",
            _unique_strings(self.source_refs, "retrieval plan source_refs", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class PreparedCognitionIntake:
    """Pre-retrieval artifacts and explicit fallback metadata."""

    perception: Perception
    need_mix: NeedMix
    retrieval_plan: RetrievalPlan
    fallback_reasons: tuple[str, ...]
    perception_ms: float
    need_mix_ms: float
    retrieval_plan_ms: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fallback_reasons",
            _unique_strings(self.fallback_reasons, "intake fallback_reasons", maximum_items=4),
        )
        statuses = (
            self.perception.status,
            self.need_mix.status,
            self.retrieval_plan.status,
        )
        if len(set(statuses)) != 1:
            raise ValueError("cognition intake artifacts must share one status")
        if (statuses[0] is CognitionArtifactStatus.FALLBACK) is not bool(self.fallback_reasons):
            raise ValueError("cognition intake fallback status and reasons must agree")
        if (
            self.perception.owner is not CognitionOwner.COGNITION
            or self.need_mix.owner is not CognitionOwner.COGNITION
            or self.retrieval_plan.owner is not CognitionOwner.MEMORY_QUERY
        ):
            raise ValueError("cognition intake artifact owner is invalid")
        for field_name in ("perception_ms", "need_mix_ms", "retrieval_plan_ms"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{field_name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class AppraisalArtifact:
    """Trace projection of the existing provider proposal and owner handoff."""

    schema_version: int
    status: CognitionArtifactStatus
    reason_code: str
    source_refs: tuple[str, ...]
    appraisal_confidence: float | None
    emotion_state_version: int | None
    mood_state_version: int | None
    owner: CognitionOwner = CognitionOwner.EMOTION_MANAGER

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "appraisal artifact schema_version")
        object.__setattr__(
            self, "reason_code", _non_blank(self.reason_code, "appraisal reason_code", maximum=64)
        )
        object.__setattr__(
            self,
            "source_refs",
            _unique_strings(self.source_refs, "appraisal source_refs", allow_empty=False),
        )
        if self.appraisal_confidence is not None:
            _unit_interval(self.appraisal_confidence, "appraisal confidence")
        for field_name in ("emotion_state_version", "mood_state_version"):
            value = getattr(self, field_name)
            if value is not None:
                _positive_version(value, field_name)


@dataclass(frozen=True, slots=True)
class InternalPosition:
    """Concise current-turn position summary, never durable belief or raw reasoning."""

    schema_version: int
    status: CognitionArtifactStatus
    stance: PositionStance
    summary: str
    confidence: float
    supporting_point_codes: tuple[str, ...]
    concern_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    requires_uncertainty: bool
    owner: CognitionOwner = CognitionOwner.COGNITION

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "internal position schema_version")
        if type(self.requires_uncertainty) is not bool:
            raise ValueError("internal position requires_uncertainty must be boolean")
        object.__setattr__(
            self,
            "summary",
            _non_blank(self.summary, "internal position summary", maximum=280),
        )
        _unit_interval(self.confidence, "internal position confidence")
        object.__setattr__(
            self,
            "supporting_point_codes",
            _unique_strings(
                self.supporting_point_codes,
                "internal position supporting_point_codes",
                allow_empty=False,
                maximum_items=8,
                maximum_chars=64,
            ),
        )
        object.__setattr__(
            self,
            "concern_codes",
            _unique_strings(
                self.concern_codes,
                "internal position concern_codes",
                maximum_items=8,
                maximum_chars=64,
            ),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _unique_strings(
                self.evidence_refs, "internal position evidence_refs", allow_empty=False
            ),
        )


@dataclass(frozen=True, slots=True)
class IntentSelection:
    """One primary intent plus additive versioned intent tags."""

    schema_version: int
    registry_version: int
    status: CognitionArtifactStatus
    primary_tag: str
    tags: tuple[str, ...]
    priority: float
    source_refs: tuple[str, ...]
    owner: CognitionOwner = CognitionOwner.COGNITION

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "intent schema_version")
        registry_version = _positive_version(self.registry_version, "intent registry_version")
        registered_tags = _KNOWN_INTENT_TAGS_BY_REGISTRY.get(registry_version)
        if registered_tags is None:
            raise ValueError("intent registry_version is not supported")
        primary = _non_blank(self.primary_tag, "intent primary_tag", maximum=64)
        tags = _unique_strings(
            self.tags,
            "intent tags",
            allow_empty=False,
            maximum_items=8,
            maximum_chars=64,
        )
        unknown = set(tags).difference(registered_tags)
        if unknown:
            raise ValueError(f"intent tags are not registered: {sorted(unknown)}")
        if primary not in tags:
            raise ValueError("intent primary_tag must be present in tags")
        if registry_version == INTENT_REGISTRY_VERSION_V2 and (
            set(tags).intersection(V2_ACTION_INTENT_TAGS) != {primary}
        ):
            raise ValueError("v2 intent tags must contain exactly the primary action")
        _unit_interval(self.priority, "intent priority")
        object.__setattr__(self, "primary_tag", primary)
        object.__setattr__(self, "tags", tags)
        object.__setattr__(
            self,
            "source_refs",
            _unique_strings(self.source_refs, "intent source_refs", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class ResponseStrategy:
    """Validated expression plan that preserves position and evidence boundaries."""

    schema_version: int
    status: CognitionArtifactStatus
    position_stance: PositionStance
    preserve_uncertainty: bool
    tone: ResponseTone
    verbosity: ResponseVerbosity
    humor: float
    softness: float
    point_codes: tuple[str, ...]
    must_not_claim: tuple[str, ...]
    source_refs: tuple[str, ...]
    curiosity_influence: float = 0.0
    owner: CognitionOwner = CognitionOwner.COGNITION

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "response strategy schema_version")
        if type(self.preserve_uncertainty) is not bool:
            raise ValueError("response strategy preserve_uncertainty must be boolean")
        _unit_interval(self.humor, "response strategy humor")
        _unit_interval(self.softness, "response strategy softness")
        if (
            isinstance(self.curiosity_influence, bool)
            or not math.isfinite(self.curiosity_influence)
            or not 0.0 <= self.curiosity_influence <= 0.20
        ):
            raise ValueError("response strategy curiosity_influence must be in [0, 0.20]")
        object.__setattr__(
            self,
            "point_codes",
            _unique_strings(
                self.point_codes,
                "response strategy point_codes",
                allow_empty=False,
                maximum_items=8,
                maximum_chars=64,
            ),
        )
        object.__setattr__(
            self,
            "must_not_claim",
            _unique_strings(
                self.must_not_claim,
                "response strategy must_not_claim",
                allow_empty=False,
                maximum_items=8,
                maximum_chars=64,
            ),
        )
        object.__setattr__(
            self,
            "source_refs",
            _unique_strings(self.source_refs, "response strategy source_refs", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class CognitionStepTimings:
    """Deterministic application-only timing decomposition."""

    perception_ms: float
    need_mix_ms: float
    retrieval_plan_ms: float
    appraisal_handoff_ms: float
    position_ms: float
    intent_ms: float
    strategy_ms: float
    total_ms: float

    def __post_init__(self) -> None:
        for field_name in (
            "perception_ms",
            "need_mix_ms",
            "retrieval_plan_ms",
            "appraisal_handoff_ms",
            "position_ms",
            "intent_ms",
            "strategy_ms",
            "total_ms",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{field_name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class CognitionPipelineTrace:
    """Complete transient trace; normal logs must omit summary prose and raw content."""

    schema_version: int
    status: CognitionArtifactStatus
    perception: Perception
    need_mix: NeedMix
    retrieval_plan: RetrievalPlan
    appraisal: AppraisalArtifact
    internal_position: InternalPosition
    intent: IntentSelection
    response_strategy: ResponseStrategy
    fallback_reasons: tuple[str, ...]
    timings: CognitionStepTimings

    def __post_init__(self) -> None:
        _positive_version(self.schema_version, "cognition pipeline schema_version")
        object.__setattr__(
            self,
            "fallback_reasons",
            _unique_strings(self.fallback_reasons, "pipeline fallback_reasons", maximum_items=8),
        )
        if (self.status is CognitionArtifactStatus.FALLBACK) is not bool(self.fallback_reasons):
            raise ValueError("cognition fallback status and reasons must agree")
        status_artifacts = (
            self.perception,
            self.need_mix,
            self.retrieval_plan,
            self.internal_position,
            self.intent,
            self.response_strategy,
        )
        if any(artifact.status is not self.status for artifact in status_artifacts):
            raise ValueError("cognition pipeline decision artifacts must share one status")
        if (
            self.perception.owner is not CognitionOwner.COGNITION
            or self.need_mix.owner is not CognitionOwner.COGNITION
            or self.retrieval_plan.owner is not CognitionOwner.MEMORY_QUERY
            or self.appraisal.owner is not CognitionOwner.EMOTION_MANAGER
            or self.internal_position.owner is not CognitionOwner.COGNITION
            or self.intent.owner is not CognitionOwner.COGNITION
            or self.response_strategy.owner is not CognitionOwner.COGNITION
        ):
            raise ValueError("cognition pipeline decision artifact owner is invalid")
        if self.response_strategy.position_stance is not self.internal_position.stance:
            raise ValueError("response strategy cannot reverse the internal position stance")
        stance = self.internal_position.stance
        primary_intent = self.intent.primary_tag
        if self.intent.registry_version == INTENT_REGISTRY_VERSION_V2:
            point_codes = set(self.response_strategy.point_codes)
            if not point_codes <= V2_RESPONSE_POINT_CODES:
                raise ValueError("v2 response strategy contains an unsupported point code")
            if point_codes.intersection(V2_ACTION_INTENT_TAGS) != {primary_intent}:
                raise ValueError(
                    "v2 response strategy must contain exactly the primary action point"
                )
            if primary_intent in V2_META_INTENT_TAGS:
                if point_codes != {primary_intent}:
                    raise ValueError("v2 meta intent requires a singleton action point")
            elif "address_current_request" not in point_codes:
                raise ValueError("v2 non-meta intent must address the current request")
        if primary_intent == "receive_repair":
            if stance is not PositionStance.ANSWER:
                raise ValueError("repair cognition intent requires answer stance")
        elif primary_intent == "hold_safety_boundary":
            if stance not in {PositionStance.ANSWER, PositionStance.LISTEN}:
                raise ValueError("safety cognition intent cannot reverse the internal position")
        elif (
            primary_intent != "notice_repetition"
            and primary_intent != (_PRIMARY_INTENT_BY_POSITION_STANCE[stance])
        ):
            raise ValueError("cognition intent cannot reverse the internal position stance")
        if primary_intent not in self.response_strategy.point_codes:
            raise ValueError("response strategy must preserve the primary cognition intent")
        if (
            self.internal_position.requires_uncertainty
            and not self.response_strategy.preserve_uncertainty
        ):
            raise ValueError("response strategy must preserve material uncertainty")
