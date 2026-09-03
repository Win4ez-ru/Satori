"""Typed schema and invariants for one request-local Satori delivery decision."""

import math
from dataclasses import dataclass
from enum import StrEnum

from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V2,
    KNOWN_INTENT_TAGS_V2,
    V2_ACTION_INTENT_TAGS,
    V2_META_INTENT_TAGS,
    V2_RESPONSE_POINT_CODES,
    CognitionArtifactStatus,
    PositionStance,
    ResponseVerbosity,
)
from satori.application.conversation.character_agency import (
    CharacterAgencyDecision,
    CharacterAgencyDrive,
    CharacterAgencyReason,
    CharacterAgencyStatus,
)
from satori.application.conversation.character_expression import (
    BASELINE_CHARACTER_GUIDANCE_CODES,
    CharacterContinuationMode,
    CharacterGroundingMode,
    CharacterPressureLevel,
)
from satori.application.conversation.disclosure_contracts import DisclosureFacet

CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION = 1
CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION = 2
CHARACTER_DELIVERY_DECISION_V3_SCHEMA_VERSION = 3
CHARACTER_DELIVERY_DECISION_V4_SCHEMA_VERSION = 4
CHARACTER_DELIVERY_DECISION_V5_SCHEMA_VERSION = 5
CHARACTER_PRESENCE_PROJECTION_SCHEMA_VERSION = 1
CHARACTER_PRESENCE_PROJECTION_V2_SCHEMA_VERSION = 2
CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION = 3
CHARACTER_PRESENCE_PERSONALITY_CODES = (
    *BASELINE_CHARACTER_GUIDANCE_CODES,
    "grounded_optimism",
)
CHARACTER_PRESENCE_VALUE_KEYS = (
    "curiosity",
    "truth",
    "intellectual_honesty",
    "growth",
    "autonomy",
    "creativity",
    "competence",
    "connection",
    "compassion",
)

_SUPPORTED_STRATEGY_STATUSES = {
    CognitionArtifactStatus.APPLIED,
    CognitionArtifactStatus.FALLBACK,
}
_PRIMARY_INTENT_BY_STANCE = {
    PositionStance.ANSWER: "answer_directly",
    PositionStance.LISTEN: "listen_and_reflect",
    PositionStance.CHALLENGE: "challenge_gently",
    PositionStance.UNCERTAIN: "clarify_uncertainty",
    PositionStance.COLLABORATE: "support_decision",
    PositionStance.ACKNOWLEDGE: "acknowledge_correction",
}
_SUPPORTED_POINT_CODES = V2_RESPONSE_POINT_CODES
_SUPPORTED_FORBIDDEN_CLAIM_CODES = frozenset(
    {
        "unsupported_memory",
        "hidden_user_state",
        "durable_satori_belief",
        "false_certainty",
    }
)


class CharacterDeliveryGoal(StrEnum):
    """The single coherent conversational outcome for the current reply."""

    CELEBRATE_AND_CONTINUE = "celebrate_and_continue"
    PRACTICAL_CARE = "practical_care"
    STAY_PRESENT = "stay_present"
    CHALLENGE_CLAIM = "challenge_claim"
    ADVANCE_TOPIC = "advance_topic"
    HOLD_BOUNDARY = "hold_boundary"
    GUARDED_HELP = "guarded_help"
    BRIEF_GUARDED_ACKNOWLEDGEMENT = "brief_guarded_acknowledgement"
    OWNED_RESPONSE = "owned_response"
    ANSWER_PRECISELY = "answer_precisely"
    OWN_AND_REPAIR = "own_and_repair"
    NOTICE_REPETITION = "notice_repetition"
    CLARIFY_UNCERTAINTY = "clarify_uncertainty"
    SOCIAL_CONNECT = "social_connect"
    SELF_DISCLOSE = "self_disclose"
    RESPOND_TO_OBJECTION = "respond_to_objection"
    CLOSE_TOPIC = "close_topic"


class CharacterDeliveryVoice(StrEnum):
    """One merged voice choice; never a trait, mood or relationship owner."""

    THOUGHTFUL_PRECISION = "thoughtful_precision"
    ACCOUNTABLE_DIRECT = "accountable_direct"
    PLAYFUL_EDGE = "playful_edge"
    LIVELY_DRY_WARMTH = "lively_dry_warmth"
    PRACTICAL_GUARDED_CARE = "practical_guarded_care"
    OPEN_CARE = "open_care"
    ENGAGED_SKEPTICISM = "engaged_skepticism"
    ENERGIZED_COLLABORATION = "energized_collaboration"
    COOL_RESERVE = "cool_reserve"
    WARM_INDEPENDENCE = "warm_independence"
    REFLECTIVE_CANDOR = "reflective_candor"
    EASY_PLAYFUL_WARMTH = "easy_playful_warmth"


class CharacterPresenceStrength(StrEnum):
    """Provider-safe qualitative strength derived from live personality state."""

    DEFINING = "defining"
    STRONG = "strong"
    AVAILABLE = "available"


def character_presence_strength_for(strength: float) -> CharacterPresenceStrength:
    """Map a canonical scalar to the only valid qualitative presence level."""

    if strength >= 0.82:
        return CharacterPresenceStrength.DEFINING
    if strength >= 0.68:
        return CharacterPresenceStrength.STRONG
    return CharacterPresenceStrength.AVAILABLE


class CharacterAffectSignalCode(StrEnum):
    """Closed provider-safe meanings derived from the canonical affect owner."""

    STEADY = "steady"
    ENGAGED_CURIOSITY = "engaged_curiosity"
    PLAYFUL_AMUSEMENT = "playful_amusement"
    POSITIVE_ENERGY = "positive_energy"
    PROTECTIVE_CONCERN = "protective_concern"
    FRUSTRATED_EDGE = "frustrated_edge"
    TENSE_FOCUS = "tense_focus"
    SUBDUED_MOOD = "subdued_mood"


class CharacterRelationshipSignalCode(StrEnum):
    """Closed expression meanings derived from the canonical relationship owner."""

    NEW_CONTACT = "new_contact"
    GROWING_FAMILIARITY = "growing_familiarity"
    EARNED_TRUST = "earned_trust"
    EASY_COMFORT = "easy_comfort"
    PERSONAL_CLOSENESS = "personal_closeness"
    INTELLECTUAL_RESPECT = "intellectual_respect"
    GROWING_AFFECTION = "growing_affection"
    RECENT_STRAIN = "recent_strain"
    LIMITED_TRUST = "limited_trust"
    LOW_COMFORT = "low_comfort"
    LIMITED_FAMILIARITY = "limited_familiarity"


@dataclass(frozen=True, slots=True)
class CharacterPersonalitySignal:
    """One request-local activation backed by an existing personality projection."""

    code: str
    strength: float
    level: CharacterPresenceStrength
    direction: str | None = None

    def __post_init__(self) -> None:
        if self.code not in CHARACTER_PRESENCE_PERSONALITY_CODES:
            raise ValueError("character presence personality code is not canonical")
        if (
            isinstance(self.strength, bool)
            or not isinstance(self.strength, (int, float))
            or not math.isfinite(self.strength)
            or not 0.0 <= self.strength <= 1.0
            or not isinstance(self.level, CharacterPresenceStrength)
        ):
            raise ValueError("character presence personality strength is invalid")
        if self.direction not in {None, "slightly_stronger", "slightly_softer"}:
            raise ValueError("character presence personality direction is invalid")
        if self.level is not character_presence_strength_for(float(self.strength)):
            raise ValueError("character presence personality level contradicts its strength")


@dataclass(frozen=True, slots=True)
class CharacterValueSignal:
    """One contextually salient existing core value, never a new value owner."""

    key: str
    strength: float
    level: CharacterPresenceStrength

    def __post_init__(self) -> None:
        if self.key not in CHARACTER_PRESENCE_VALUE_KEYS:
            raise ValueError("character presence value key is not canonical")
        if (
            isinstance(self.strength, bool)
            or not isinstance(self.strength, (int, float))
            or not math.isfinite(self.strength)
            or not 0.0 <= self.strength <= 1.0
            or not isinstance(self.level, CharacterPresenceStrength)
        ):
            raise ValueError("character presence value strength is invalid")
        if self.level is not character_presence_strength_for(float(self.strength)):
            raise ValueError("character presence value level contradicts its strength")


@dataclass(frozen=True, slots=True)
class CharacterAffectSignal:
    """One transient qualitative expression signal; affect remains the sole state owner."""

    code: CharacterAffectSignalCode
    level: CharacterPresenceStrength

    def __post_init__(self) -> None:
        if not isinstance(self.code, CharacterAffectSignalCode) or not isinstance(
            self.level, CharacterPresenceStrength
        ):
            raise ValueError("character presence affect signal is invalid")


@dataclass(frozen=True, slots=True)
class CharacterRelationshipSignal:
    """One transient qualitative expression signal; relationship remains the state owner."""

    code: CharacterRelationshipSignalCode
    level: CharacterPresenceStrength

    def __post_init__(self) -> None:
        if not isinstance(self.code, CharacterRelationshipSignalCode) or not isinstance(
            self.level, CharacterPresenceStrength
        ):
            raise ValueError("character presence relationship signal is invalid")


def validate_affect_presence_semantics(
    profile: str,
    signals: tuple[CharacterAffectSignal, ...],
) -> None:
    """Keep the coarse audit profile consistent with its causal affect signals."""

    codes = {item.code for item in signals}
    required_any = {
        "tense_non_hostile": {
            CharacterAffectSignalCode.PROTECTIVE_CONCERN,
            CharacterAffectSignalCode.FRUSTRATED_EDGE,
            CharacterAffectSignalCode.TENSE_FOCUS,
        },
        "positive_light": {
            CharacterAffectSignalCode.PLAYFUL_AMUSEMENT,
            CharacterAffectSignalCode.POSITIVE_ENERGY,
        },
        "soft_negative_non_hostile": {CharacterAffectSignalCode.SUBDUED_MOOD},
        "interested_calm": {CharacterAffectSignalCode.ENGAGED_CURIOSITY},
    }
    required = required_any.get(profile)
    if required is not None and not codes.intersection(required):
        raise ValueError("character presence affect profile and signals are inconsistent")


def validate_relationship_presence_semantics(
    profile: str,
    signals: tuple[CharacterRelationshipSignal, ...],
) -> None:
    """Keep relationship audit profile and projected affordances mutually consistent."""

    codes = {item.code for item in signals}
    guarded = {
        CharacterRelationshipSignalCode.RECENT_STRAIN,
        CharacterRelationshipSignalCode.LIMITED_TRUST,
        CharacterRelationshipSignalCode.LOW_COMFORT,
    }
    if profile == "fresh_undeveloped_neutral":
        if CharacterRelationshipSignalCode.NEW_CONTACT not in codes or codes.intersection(guarded):
            raise ValueError("fresh relationship profile and signals are inconsistent")
    elif profile == "guarded_only_when_relationally_relevant":
        if not codes.intersection(guarded):
            raise ValueError("guarded relationship profile requires a guarded signal")
    elif codes.intersection(guarded | {CharacterRelationshipSignalCode.NEW_CONTACT}):
        raise ValueError("non-guarded relationship profile carries a guarded signal")
    if profile == "established_positive" and not codes.intersection(
        {
            CharacterRelationshipSignalCode.EARNED_TRUST,
            CharacterRelationshipSignalCode.EASY_COMFORT,
            CharacterRelationshipSignalCode.PERSONAL_CLOSENESS,
            CharacterRelationshipSignalCode.INTELLECTUAL_RESPECT,
            CharacterRelationshipSignalCode.GROWING_AFFECTION,
        }
    ):
        raise ValueError("established relationship profile requires an earned affordance")


@dataclass(frozen=True, slots=True)
class CharacterPresenceProjection:
    """One transient causal bridge from live Satori state to provider delivery."""

    schema_version: int
    personality_aggregate_version: int
    decision: "CharacterDeliveryDecision"
    personality_signals: tuple[CharacterPersonalitySignal, ...]
    value_signals: tuple[CharacterValueSignal, ...]
    affect_signals: tuple[CharacterAffectSignal, ...]
    relationship_signals: tuple[CharacterRelationshipSignal, ...]
    affect_profile: str | None
    affect_relevant: bool
    relationship_profile: str | None
    relationship_relevant: bool
    memory_use_licensed: bool
    canonical_position_available: bool
    topic_inclination_available: bool

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {
            CHARACTER_PRESENCE_PROJECTION_SCHEMA_VERSION,
            CHARACTER_PRESENCE_PROJECTION_V2_SCHEMA_VERSION,
            CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION,
        }:
            raise ValueError("unsupported character presence projection schema_version")
        if (
            type(self.personality_aggregate_version) is not int
            or self.personality_aggregate_version < 1
        ):
            raise ValueError("character presence requires a positive personality aggregate version")
        expected_decision_schema = {
            CHARACTER_PRESENCE_PROJECTION_SCHEMA_VERSION: (
                CHARACTER_DELIVERY_DECISION_V3_SCHEMA_VERSION
            ),
            CHARACTER_PRESENCE_PROJECTION_V2_SCHEMA_VERSION: (
                CHARACTER_DELIVERY_DECISION_V4_SCHEMA_VERSION
            ),
            CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION: (
                CHARACTER_DELIVERY_DECISION_V5_SCHEMA_VERSION
            ),
        }[self.schema_version]
        if self.decision.schema_version != expected_decision_schema:
            raise ValueError(
                "character presence schema requires its exact character delivery decision"
            )
        personality_signals = tuple(self.personality_signals)
        personality_codes = tuple(item.code for item in personality_signals)
        if (
            not personality_signals
            or len(personality_signals) > 3
            or len(personality_codes) != len(set(personality_codes))
            or not set(personality_codes) <= set(CHARACTER_PRESENCE_PERSONALITY_CODES)
        ):
            raise ValueError("character presence requires one to three unique personality signals")
        value_signals = tuple(self.value_signals)
        value_keys = tuple(item.key for item in value_signals)
        if not value_signals or len(value_signals) > 3 or len(value_keys) != len(set(value_keys)):
            raise ValueError("character presence requires one to three unique value signals")
        if (
            self.schema_version >= CHARACTER_PRESENCE_PROJECTION_V2_SCHEMA_VERSION
            and len(value_signals) != 1
        ):
            raise ValueError("character presence v2 requires exactly one value guard")
        if self.schema_version >= CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION:
            if self.decision.agency is None:
                raise ValueError("character presence v3 requires character agency")
            if not set(self.decision.agency.source_personality_codes) <= set(personality_codes):
                raise ValueError(
                    "character agency personality sources must be realized by presence"
                )
            if set(value_keys) != {self.decision.agency.source_value_key}:
                raise ValueError("character agency value source must match the presence guard")
        affect_signals = tuple(self.affect_signals)
        affect_codes = tuple(item.code for item in affect_signals)
        if (
            len(affect_signals) > 3
            or len(affect_codes) != len(set(affect_codes))
            or (self.affect_profile is None) is not (not affect_signals)
        ):
            raise ValueError("character presence affect signals do not match available affect")
        relationship_signals = tuple(self.relationship_signals)
        relationship_codes = tuple(item.code for item in relationship_signals)
        if (
            len(relationship_signals) > 3
            or len(relationship_codes) != len(set(relationship_codes))
            or (self.relationship_profile is None) is not (not relationship_signals)
        ):
            raise ValueError(
                "character presence relationship signals do not match available relationship"
            )
        if self.affect_profile not in {
            None,
            "tense_non_hostile",
            "positive_light",
            "soft_negative_non_hostile",
            "interested_calm",
            "calm_even",
        }:
            raise ValueError("character presence affect profile is not supported")
        if self.relationship_profile not in {
            None,
            "fresh_undeveloped_neutral",
            "developing_neutral",
            "established_positive",
            "guarded_only_when_relationally_relevant",
        }:
            raise ValueError("character presence relationship profile is not supported")
        if self.affect_profile is not None:
            validate_affect_presence_semantics(self.affect_profile, affect_signals)
        if self.relationship_profile is not None:
            validate_relationship_presence_semantics(
                self.relationship_profile,
                relationship_signals,
            )
        for field_name in (
            "affect_relevant",
            "relationship_relevant",
            "memory_use_licensed",
            "canonical_position_available",
            "topic_inclination_available",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"character presence {field_name} must be boolean")
        if self.affect_relevant and self.affect_profile is None:
            raise ValueError("relevant affect requires an available affect profile")
        if self.relationship_relevant and self.relationship_profile is None:
            raise ValueError("relevant relationship requires an available relationship profile")
        if (
            self.memory_use_licensed
            and self.decision.grounding is not CharacterGroundingMode.TRUSTED_CONTEXT
        ):
            raise ValueError("memory use requires trusted-context grounding")
        if (
            self.decision.goal is CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE
            and self.decision.grounding is CharacterGroundingMode.TRUSTED_CONTEXT
            and not self.memory_use_licensed
        ):
            raise ValueError("trusted celebration grounding requires retrieved memory")
        object.__setattr__(self, "personality_signals", personality_signals)
        object.__setattr__(self, "value_signals", value_signals)
        object.__setattr__(self, "affect_signals", affect_signals)
        object.__setattr__(self, "relationship_signals", relationship_signals)


_ALLOWED_TOPOLOGIES = {
    CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE: {
        (
            CharacterGroundingMode.REACTION_ONLY,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.TRUSTED_CONTEXT,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.REACTION_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.TRUSTED_CONTEXT,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
    },
    CharacterDeliveryGoal.PRACTICAL_CARE: {
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.GENTLE,
        ),
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.MODERATE,
        ),
    },
    CharacterDeliveryGoal.STAY_PRESENT: {
        (
            CharacterGroundingMode.REACTION_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        )
    },
    CharacterDeliveryGoal.CHALLENGE_CLAIM: {
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.GENTLE,
        ),
    },
    CharacterDeliveryGoal.ADVANCE_TOPIC: {
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.MODERATE,
        ),
    },
    CharacterDeliveryGoal.HOLD_BOUNDARY: {
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.BOUNDARY,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.BOUNDARY,
            CharacterPressureLevel.FIRM,
        ),
    },
    CharacterDeliveryGoal.GUARDED_HELP: {
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.GUARDED,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.TRUSTED_CONTEXT,
            CharacterContinuationMode.GUARDED,
            CharacterPressureLevel.NONE,
        ),
    },
    CharacterDeliveryGoal.BRIEF_GUARDED_ACKNOWLEDGEMENT: {
        (
            CharacterGroundingMode.REACTION_ONLY,
            CharacterContinuationMode.GUARDED,
            CharacterPressureLevel.NONE,
        )
    },
    CharacterDeliveryGoal.OWNED_RESPONSE: {
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.REACTION_ONLY,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.TRUSTED_CONTEXT,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.TRUSTED_CONTEXT,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
    },
    CharacterDeliveryGoal.ANSWER_PRECISELY: {
        (
            CharacterGroundingMode.TRUSTED_CONTEXT,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.TRUSTED_CONTEXT,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
    },
    CharacterDeliveryGoal.OWN_AND_REPAIR: {
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        )
    },
    CharacterDeliveryGoal.NOTICE_REPETITION: {
        (
            CharacterGroundingMode.REACTION_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.REACTION_ONLY,
            CharacterContinuationMode.GUARDED,
            CharacterPressureLevel.NONE,
        ),
    },
    CharacterDeliveryGoal.CLARIFY_UNCERTAINTY: {
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        )
    },
    CharacterDeliveryGoal.SOCIAL_CONNECT: {
        (
            CharacterGroundingMode.REACTION_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.REACTION_ONLY,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.TRUSTED_CONTEXT,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.TRUSTED_CONTEXT,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
    },
    CharacterDeliveryGoal.SELF_DISCLOSE: {
        (
            CharacterGroundingMode.TRUSTED_CONTEXT,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.TRUSTED_CONTEXT,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
    },
    CharacterDeliveryGoal.RESPOND_TO_OBJECTION: {
        (
            CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        )
    },
    CharacterDeliveryGoal.CLOSE_TOPIC: {
        (
            CharacterGroundingMode.REACTION_ONLY,
            CharacterContinuationMode.COMPLETE,
            CharacterPressureLevel.NONE,
        ),
        (
            CharacterGroundingMode.REACTION_ONLY,
            CharacterContinuationMode.OPEN,
            CharacterPressureLevel.NONE,
        ),
    },
}

_ALLOWED_VOICES = {
    CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE: {
        CharacterDeliveryVoice.LIVELY_DRY_WARMTH,
        CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
    },
    CharacterDeliveryGoal.PRACTICAL_CARE: {CharacterDeliveryVoice.PRACTICAL_GUARDED_CARE},
    CharacterDeliveryGoal.STAY_PRESENT: {CharacterDeliveryVoice.OPEN_CARE},
    CharacterDeliveryGoal.CHALLENGE_CLAIM: {
        CharacterDeliveryVoice.ENGAGED_SKEPTICISM,
        CharacterDeliveryVoice.PLAYFUL_EDGE,
    },
    CharacterDeliveryGoal.ADVANCE_TOPIC: {
        CharacterDeliveryVoice.ENERGIZED_COLLABORATION,
        CharacterDeliveryVoice.PLAYFUL_EDGE,
    },
    CharacterDeliveryGoal.HOLD_BOUNDARY: {
        CharacterDeliveryVoice.OPEN_CARE,
        CharacterDeliveryVoice.COOL_RESERVE,
    },
    CharacterDeliveryGoal.GUARDED_HELP: {CharacterDeliveryVoice.COOL_RESERVE},
    CharacterDeliveryGoal.BRIEF_GUARDED_ACKNOWLEDGEMENT: {CharacterDeliveryVoice.COOL_RESERVE},
    CharacterDeliveryGoal.OWNED_RESPONSE: {
        CharacterDeliveryVoice.WARM_INDEPENDENCE,
        CharacterDeliveryVoice.REFLECTIVE_CANDOR,
        CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
        CharacterDeliveryVoice.COOL_RESERVE,
    },
    CharacterDeliveryGoal.ANSWER_PRECISELY: {CharacterDeliveryVoice.THOUGHTFUL_PRECISION},
    CharacterDeliveryGoal.OWN_AND_REPAIR: {CharacterDeliveryVoice.ACCOUNTABLE_DIRECT},
    CharacterDeliveryGoal.NOTICE_REPETITION: {
        CharacterDeliveryVoice.PLAYFUL_EDGE,
        CharacterDeliveryVoice.OPEN_CARE,
        CharacterDeliveryVoice.COOL_RESERVE,
    },
    CharacterDeliveryGoal.CLARIFY_UNCERTAINTY: {CharacterDeliveryVoice.THOUGHTFUL_PRECISION},
    CharacterDeliveryGoal.SOCIAL_CONNECT: {
        CharacterDeliveryVoice.LIVELY_DRY_WARMTH,
        CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
        CharacterDeliveryVoice.REFLECTIVE_CANDOR,
    },
    CharacterDeliveryGoal.SELF_DISCLOSE: {
        CharacterDeliveryVoice.WARM_INDEPENDENCE,
        CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
        CharacterDeliveryVoice.REFLECTIVE_CANDOR,
    },
    CharacterDeliveryGoal.RESPOND_TO_OBJECTION: {
        CharacterDeliveryVoice.ENGAGED_SKEPTICISM,
        CharacterDeliveryVoice.THOUGHTFUL_PRECISION,
        CharacterDeliveryVoice.WARM_INDEPENDENCE,
    },
    CharacterDeliveryGoal.CLOSE_TOPIC: {
        CharacterDeliveryVoice.LIVELY_DRY_WARMTH,
        CharacterDeliveryVoice.PLAYFUL_EDGE,
        CharacterDeliveryVoice.WARM_INDEPENDENCE,
        CharacterDeliveryVoice.REFLECTIVE_CANDOR,
        CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
        CharacterDeliveryVoice.COOL_RESERVE,
    },
}


@dataclass(frozen=True, slots=True)
class CharacterDeliveryDecision:
    """Direct transient projection that replaces the legacy multi-axis provider realization."""

    schema_version: int
    goal: CharacterDeliveryGoal
    voice: CharacterDeliveryVoice
    grounding: CharacterGroundingMode
    continuation: CharacterContinuationMode
    pressure: CharacterPressureLevel
    position_stance: PositionStance
    preserve_uncertainty: bool
    cognition_intent_registry_version: int
    cognition_primary_intent: str
    cognition_intent_tags: tuple[str, ...]
    required_point_codes: tuple[str, ...]
    forbidden_claim_codes: tuple[str, ...]
    response_verbosity: ResponseVerbosity
    required_disclosure_facets: tuple[DisclosureFacet, ...] = ()
    source_personality_codes: tuple[str, ...] = BASELINE_CHARACTER_GUIDANCE_CODES
    agency: CharacterAgencyDecision | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {
            CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION,
            CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION,
            CHARACTER_DELIVERY_DECISION_V3_SCHEMA_VERSION,
            CHARACTER_DELIVERY_DECISION_V4_SCHEMA_VERSION,
            CHARACTER_DELIVERY_DECISION_V5_SCHEMA_VERSION,
        }:
            raise ValueError("unsupported character delivery decision schema_version")
        disclosure_facets = tuple(self.required_disclosure_facets)
        if len(disclosure_facets) != len(set(disclosure_facets)) or not all(
            isinstance(facet, DisclosureFacet) for facet in disclosure_facets
        ):
            raise ValueError("character delivery disclosure facets must be unique typed values")
        if self.schema_version == CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION and disclosure_facets:
            raise ValueError("character delivery v1 cannot contain disclosure facets")
        object.__setattr__(self, "required_disclosure_facets", disclosure_facets)
        if type(self.preserve_uncertainty) is not bool:
            raise ValueError("character delivery preserve_uncertainty must be boolean")
        if not (
            isinstance(self.goal, CharacterDeliveryGoal)
            and isinstance(self.voice, CharacterDeliveryVoice)
            and isinstance(self.grounding, CharacterGroundingMode)
            and isinstance(self.continuation, CharacterContinuationMode)
            and isinstance(self.pressure, CharacterPressureLevel)
            and isinstance(self.position_stance, PositionStance)
            and isinstance(self.response_verbosity, ResponseVerbosity)
        ):
            raise ValueError("character delivery decision requires exact typed enum fields")
        if self.goal is CharacterDeliveryGoal.SOCIAL_CONNECT and frozenset(
            disclosure_facets
        ) not in {
            frozenset(),
            frozenset({DisclosureFacet.AFFECT}),
        }:
            raise ValueError("social connection accepts only an optional affect facet")
        if self.goal is CharacterDeliveryGoal.SELF_DISCLOSE:
            personal_facets = frozenset(
                {
                    DisclosureFacet.IDENTITY,
                    DisclosureFacet.MEMORY,
                    DisclosureFacet.AFFECT,
                    DisclosureFacet.INTERESTS,
                    DisclosureFacet.EMBODIMENT,
                    DisclosureFacet.CONSCIOUSNESS_BOUNDARY,
                }
            )
            facet_set = frozenset(disclosure_facets)
            direct_personal_facets = {
                DisclosureFacet.IDENTITY,
                DisclosureFacet.AFFECT,
                DisclosureFacet.INTERESTS,
            }
            if not facet_set <= personal_facets or (
                len(facet_set) == 1 and not facet_set.intersection(direct_personal_facets)
            ):
                raise ValueError("self disclosure requires a closed personal facet set")
        codes = tuple(self.source_personality_codes)
        if codes != BASELINE_CHARACTER_GUIDANCE_CODES:
            raise ValueError("character delivery decision requires canonical personality guidance")
        object.__setattr__(self, "source_personality_codes", codes)
        if self.schema_version >= CHARACTER_DELIVERY_DECISION_V5_SCHEMA_VERSION:
            if not isinstance(self.agency, CharacterAgencyDecision):
                raise ValueError("character delivery v5 requires one typed agency decision")
            if not set(self.agency.source_personality_codes) <= set(codes):
                raise ValueError("character agency personality sources are not canonical")
            if self.agency.source_value_key not in CHARACTER_PRESENCE_VALUE_KEYS:
                raise ValueError("character agency value source is not canonical")
        elif self.agency is not None:
            raise ValueError("historical character delivery cannot contain character agency")
        intent_tags = tuple(self.cognition_intent_tags)
        required_points = tuple(self.required_point_codes)
        forbidden_claims = tuple(self.forbidden_claim_codes)
        if (
            not intent_tags
            or len(intent_tags) != len(set(intent_tags))
            or self.cognition_intent_registry_version != INTENT_REGISTRY_VERSION_V2
            or not set(intent_tags) <= KNOWN_INTENT_TAGS_V2
            or "preserve_evidence_boundary" not in intent_tags
        ):
            raise ValueError("character delivery requires closed cognition intent tags")
        primary_intent = self.cognition_primary_intent
        repetition_intent = primary_intent == "notice_repetition"
        safety_intent = primary_intent == "hold_safety_boundary"
        repair_intent = primary_intent == "receive_repair"
        meta_intent = primary_intent in V2_META_INTENT_TAGS
        if (
            self.schema_version >= CHARACTER_DELIVERY_DECISION_V5_SCHEMA_VERSION
            and self.agency is not None
            and self.agency.status is CharacterAgencyStatus.APPLIED
        ):
            assert self.agency is not None
            agency_reasons = set(self.agency.reason_codes)
            agency_safety = CharacterAgencyReason.SAFETY_PRECEDENCE in agency_reasons
            agency_repetition = CharacterAgencyReason.REPETITION_PRECEDENCE in agency_reasons
            agency_repair_offer = CharacterAgencyReason.REPAIR_OFFER in agency_reasons
            if safety_intent is not agency_safety or (
                safety_intent and self.agency.drive is not CharacterAgencyDrive.PROTECT
            ):
                raise ValueError("character agency must preserve cognition-owned safety intent")
            if repetition_intent is not agency_repetition:
                raise ValueError("character agency must preserve cognition-owned repetition intent")
            if repair_intent is not agency_repair_offer:
                raise ValueError("character agency must preserve cognition-owned repair intent")
            correction_intent = primary_intent == "acknowledge_correction"
            agency_correction = CharacterAgencyReason.CORRECTION_UPTAKE in agency_reasons
            if correction_intent is not agency_correction:
                raise ValueError("character agency must preserve cognition-owned correction intent")
            vulnerable_presence = bool(
                agency_reasons.intersection(
                    {
                        CharacterAgencyReason.HIGH_DISTRESS,
                        CharacterAgencyReason.EXPLICIT_LISTEN,
                    }
                )
                and not repetition_intent
            )
            if vulnerable_presence and primary_intent != "listen_and_reflect":
                raise ValueError("character agency must preserve cognition-owned listen intent")
        if set(intent_tags).intersection(V2_ACTION_INTENT_TAGS) != {primary_intent}:
            raise ValueError("character delivery requires exactly one cognition action intent")
        if primary_intent not in intent_tags or (
            not meta_intent and primary_intent != _PRIMARY_INTENT_BY_STANCE[self.position_stance]
        ):
            raise ValueError("character delivery intent must preserve cognition stance")
        action_points = set(required_points).intersection(V2_ACTION_INTENT_TAGS)
        if (
            not required_points
            or len(required_points) != len(set(required_points))
            or not set(required_points) <= _SUPPORTED_POINT_CODES
            or action_points != {primary_intent}
            or primary_intent not in required_points
            or (not meta_intent and "address_current_request" not in required_points)
            or (repetition_intent and set(required_points) != {"notice_repetition"})
            or (safety_intent and set(required_points) != {"hold_safety_boundary"})
            or (repair_intent and set(required_points) != {"receive_repair"})
        ):
            raise ValueError("character delivery requires closed cognition point codes")
        if (
            len(forbidden_claims) != len(set(forbidden_claims))
            or set(forbidden_claims) != _SUPPORTED_FORBIDDEN_CLAIM_CODES
        ):
            raise ValueError("character delivery requires the complete cognition claim boundary")
        object.__setattr__(self, "cognition_intent_tags", intent_tags)
        object.__setattr__(self, "required_point_codes", required_points)
        object.__setattr__(self, "forbidden_claim_codes", forbidden_claims)
        topology = (self.grounding, self.continuation, self.pressure)
        v4_only_goals = {
            CharacterDeliveryGoal.RESPOND_TO_OBJECTION,
            CharacterDeliveryGoal.CLOSE_TOPIC,
        }
        if self.goal in v4_only_goals and (
            self.schema_version < CHARACTER_DELIVERY_DECISION_V4_SCHEMA_VERSION
        ):
            raise ValueError("operational objection and closure delivery require schema v4")
        if topology not in _ALLOWED_TOPOLOGIES.get(self.goal, set()):
            raise ValueError("character delivery topology is not licensed for its goal")
        if self.voice not in _ALLOWED_VOICES.get(self.goal, set()):
            raise ValueError("character delivery voice is not licensed for its goal")
        allowed_goals = {
            PositionStance.ANSWER: {
                CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE,
                CharacterDeliveryGoal.HOLD_BOUNDARY,
                CharacterDeliveryGoal.GUARDED_HELP,
                CharacterDeliveryGoal.BRIEF_GUARDED_ACKNOWLEDGEMENT,
                CharacterDeliveryGoal.OWNED_RESPONSE,
                CharacterDeliveryGoal.ANSWER_PRECISELY,
                CharacterDeliveryGoal.NOTICE_REPETITION,
                CharacterDeliveryGoal.SOCIAL_CONNECT,
                CharacterDeliveryGoal.SELF_DISCLOSE,
                CharacterDeliveryGoal.PRACTICAL_CARE,
                CharacterDeliveryGoal.RESPOND_TO_OBJECTION,
                CharacterDeliveryGoal.CLOSE_TOPIC,
            },
            PositionStance.LISTEN: {
                CharacterDeliveryGoal.PRACTICAL_CARE,
                CharacterDeliveryGoal.STAY_PRESENT,
                CharacterDeliveryGoal.HOLD_BOUNDARY,
                CharacterDeliveryGoal.NOTICE_REPETITION,
            },
            PositionStance.CHALLENGE: {CharacterDeliveryGoal.CHALLENGE_CLAIM},
            PositionStance.UNCERTAIN: {CharacterDeliveryGoal.CLARIFY_UNCERTAINTY},
            PositionStance.COLLABORATE: {CharacterDeliveryGoal.ADVANCE_TOPIC},
            PositionStance.ACKNOWLEDGE: {CharacterDeliveryGoal.OWN_AND_REPAIR},
        }
        safety_boundary = (
            self.goal is CharacterDeliveryGoal.HOLD_BOUNDARY
            and self.pressure is CharacterPressureLevel.FIRM
        )
        if repetition_intent and self.goal is not CharacterDeliveryGoal.NOTICE_REPETITION:
            raise ValueError("repetition cognition intent requires repetition delivery")
        if not repetition_intent and self.goal is CharacterDeliveryGoal.NOTICE_REPETITION:
            raise ValueError("repetition delivery requires cognition-owned repetition intent")
        if safety_intent is not safety_boundary:
            raise ValueError("firm safety delivery requires cognition-owned safety intent")
        if safety_intent and self.position_stance not in {
            PositionStance.ANSWER,
            PositionStance.LISTEN,
        }:
            raise ValueError("safety cognition intent cannot reverse its position stance")
        if repair_intent and self.goal is not CharacterDeliveryGoal.OWNED_RESPONSE:
            raise ValueError("repair cognition intent requires owned repair reception")
        if repair_intent and self.position_stance is not PositionStance.ANSWER:
            raise ValueError("repair cognition intent requires answer stance")
        if not meta_intent and self.goal not in allowed_goals[self.position_stance]:
            raise ValueError("character delivery goal cannot reverse cognition stance")
        if self.goal is CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE and (
            self.grounding
            not in {
                CharacterGroundingMode.REACTION_ONLY,
                CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
                *(
                    {CharacterGroundingMode.TRUSTED_CONTEXT}
                    if self.schema_version >= CHARACTER_DELIVERY_DECISION_V3_SCHEMA_VERSION
                    else set()
                ),
            }
            or self.continuation
            not in (
                {CharacterContinuationMode.OPEN, CharacterContinuationMode.COMPLETE}
                if self.schema_version >= CHARACTER_DELIVERY_DECISION_V5_SCHEMA_VERSION
                else {CharacterContinuationMode.OPEN}
            )
            or self.pressure is not CharacterPressureLevel.NONE
        ):
            raise ValueError("celebration delivery requires a licensed reaction flow")
        if self.goal is CharacterDeliveryGoal.PRACTICAL_CARE:
            if self.grounding is not CharacterGroundingMode.EXPLICIT_INPUT_ONLY:
                raise ValueError("practical care requires explicit-input grounding")
            if self.pressure not in {
                CharacterPressureLevel.NONE,
                CharacterPressureLevel.GENTLE,
                CharacterPressureLevel.MODERATE,
            }:
                raise ValueError("practical care requires bounded pressure")
            if (
                self.pressure is CharacterPressureLevel.NONE
                and self.schema_version < CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION
            ):
                raise ValueError("pressure-free practical care requires character delivery v2")
        if self.goal is CharacterDeliveryGoal.SOCIAL_CONNECT and self.schema_version < 2:
            raise ValueError("social connection requires character delivery v2")
        if self.goal is CharacterDeliveryGoal.SELF_DISCLOSE:
            if self.schema_version < 2:
                raise ValueError("self disclosure requires character delivery v2")
            if not disclosure_facets:
                raise ValueError("self disclosure requires at least one requested facet")
        if self.goal is CharacterDeliveryGoal.STAY_PRESENT and (
            self.grounding is not CharacterGroundingMode.REACTION_ONLY
            or self.pressure is not CharacterPressureLevel.NONE
        ):
            raise ValueError("quiet presence cannot add claims or pressure")
        if (
            self.goal is CharacterDeliveryGoal.HOLD_BOUNDARY
            and self.continuation is not CharacterContinuationMode.BOUNDARY
        ):
            raise ValueError("boundary delivery requires boundary continuation")
        if self.goal is CharacterDeliveryGoal.BRIEF_GUARDED_ACKNOWLEDGEMENT and (
            self.grounding is not CharacterGroundingMode.REACTION_ONLY
            or self.continuation is not CharacterContinuationMode.GUARDED
        ):
            raise ValueError("guarded acknowledgement requires a guarded reaction-only flow")
        if self.position_stance is PositionStance.LISTEN:
            if self.goal in {
                CharacterDeliveryGoal.CHALLENGE_CLAIM,
                CharacterDeliveryGoal.ADVANCE_TOPIC,
            }:
                raise ValueError("listen stance cannot be replaced by argumentative delivery")
            if self.goal is not CharacterDeliveryGoal.HOLD_BOUNDARY and self.pressure not in {
                CharacterPressureLevel.NONE,
                CharacterPressureLevel.GENTLE,
            }:
                raise ValueError("listen stance cannot carry more than gentle pressure")
