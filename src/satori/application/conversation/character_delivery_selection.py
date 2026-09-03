"""Deterministic selection of one coherent request-local Satori delivery decision."""

from collections.abc import Callable
from functools import partial

from satori.application.affect.contracts import EmotionalExpressionContext
from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V2,
    CognitionArtifactStatus,
    CognitionOwner,
    IntentSelection,
    PositionStance,
    ResponseStrategy,
)
from satori.application.conversation.character_agency import (
    CharacterAgencyDecision,
    CharacterAgencyDrive,
    CharacterAgencyInitiative,
    CharacterAgencyReason,
    CharacterAgencyStatus,
)
from satori.application.conversation.character_delivery_contracts import (
    _SUPPORTED_STRATEGY_STATUSES,
    CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION,
    CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION,
    CHARACTER_DELIVERY_DECISION_V3_SCHEMA_VERSION,
    CHARACTER_DELIVERY_DECISION_V4_SCHEMA_VERSION,
    CHARACTER_DELIVERY_DECISION_V5_SCHEMA_VERSION,
    CHARACTER_PRESENCE_PERSONALITY_CODES,
    CHARACTER_PRESENCE_PROJECTION_SCHEMA_VERSION,
    CHARACTER_PRESENCE_PROJECTION_V2_SCHEMA_VERSION,
    CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION,
    CHARACTER_PRESENCE_VALUE_KEYS,
    CharacterAffectSignal,
    CharacterAffectSignalCode,
    CharacterDeliveryDecision,
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
    CharacterPersonalitySignal,
    CharacterPresenceProjection,
    CharacterPresenceStrength,
    CharacterRelationshipSignal,
    CharacterRelationshipSignalCode,
    CharacterValueSignal,
    character_presence_strength_for,
)
from satori.application.conversation.character_expression import (
    BASELINE_CHARACTER_GUIDANCE_CODES,
    CharacterContinuationMode,
    CharacterGroundingMode,
    CharacterPressureLevel,
)
from satori.application.conversation.contracts import (
    RuntimePersonalityExpression,
    RuntimeTrait,
    RuntimeValue,
)
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosureMode,
    ConversationalDisclosurePlan,
    DisclosureFacet,
    DisclosureRequestKind,
    uses_personal_self_disclosure_delivery,
)
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.domain.affect import AFFECT_POLICY_V1

_VOICE_PERSONALITY_PRIORITY = {
    CharacterDeliveryVoice.THOUGHTFUL_PRECISION: (
        "curious_analytical",
        "considered_directness",
        "independent_position",
    ),
    CharacterDeliveryVoice.ACCOUNTABLE_DIRECT: (
        "considered_directness",
        "warm_perceptive",
        "independent_position",
    ),
    CharacterDeliveryVoice.PLAYFUL_EDGE: (
        "light_irony",
        "independent_position",
        "curious_analytical",
    ),
    CharacterDeliveryVoice.LIVELY_DRY_WARMTH: (
        "warm_perceptive",
        "light_irony",
        "curious_analytical",
    ),
    CharacterDeliveryVoice.PRACTICAL_GUARDED_CARE: (
        "warm_perceptive",
        "considered_directness",
        "independent_position",
    ),
    CharacterDeliveryVoice.OPEN_CARE: (
        "warm_perceptive",
        "considered_directness",
        "curious_analytical",
    ),
    CharacterDeliveryVoice.ENGAGED_SKEPTICISM: (
        "curious_analytical",
        "independent_position",
        "considered_directness",
    ),
    CharacterDeliveryVoice.ENERGIZED_COLLABORATION: (
        "curious_analytical",
        "light_irony",
        "independent_position",
    ),
    CharacterDeliveryVoice.COOL_RESERVE: (
        "independent_position",
        "considered_directness",
        "warm_perceptive",
    ),
    CharacterDeliveryVoice.WARM_INDEPENDENCE: (
        "independent_position",
        "warm_perceptive",
        "curious_analytical",
    ),
    CharacterDeliveryVoice.REFLECTIVE_CANDOR: (
        "warm_perceptive",
        "curious_analytical",
        "considered_directness",
    ),
    CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH: (
        "warm_perceptive",
        "light_irony",
        "independent_position",
    ),
}

_V4_GOAL_PERSONALITY_PRIORITY = {
    CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE: (
        "light_irony",
        "warm_perceptive",
        "curious_analytical",
    ),
    CharacterDeliveryGoal.PRACTICAL_CARE: (
        "considered_directness",
        "warm_perceptive",
        "independent_position",
    ),
    CharacterDeliveryGoal.STAY_PRESENT: (
        "warm_perceptive",
        "considered_directness",
        "curious_analytical",
    ),
    CharacterDeliveryGoal.CHALLENGE_CLAIM: (
        "independent_position",
        "curious_analytical",
        "considered_directness",
    ),
    CharacterDeliveryGoal.ADVANCE_TOPIC: (
        "curious_analytical",
        "independent_position",
        "light_irony",
    ),
    CharacterDeliveryGoal.HOLD_BOUNDARY: (
        "independent_position",
        "considered_directness",
        "warm_perceptive",
    ),
    CharacterDeliveryGoal.GUARDED_HELP: (
        "considered_directness",
        "independent_position",
        "warm_perceptive",
    ),
    CharacterDeliveryGoal.BRIEF_GUARDED_ACKNOWLEDGEMENT: (
        "independent_position",
        "considered_directness",
        "warm_perceptive",
    ),
    CharacterDeliveryGoal.OWNED_RESPONSE: (
        "independent_position",
        "curious_analytical",
        "warm_perceptive",
    ),
    CharacterDeliveryGoal.ANSWER_PRECISELY: (
        "curious_analytical",
        "considered_directness",
        "independent_position",
    ),
    CharacterDeliveryGoal.OWN_AND_REPAIR: (
        "considered_directness",
        "warm_perceptive",
        "independent_position",
    ),
    CharacterDeliveryGoal.NOTICE_REPETITION: (
        "light_irony",
        "curious_analytical",
        "independent_position",
    ),
    CharacterDeliveryGoal.CLARIFY_UNCERTAINTY: (
        "curious_analytical",
        "considered_directness",
        "independent_position",
    ),
    CharacterDeliveryGoal.SOCIAL_CONNECT: (
        "light_irony",
        "warm_perceptive",
        "curious_analytical",
    ),
    CharacterDeliveryGoal.SELF_DISCLOSE: (
        "curious_analytical",
        "independent_position",
        "warm_perceptive",
    ),
    CharacterDeliveryGoal.RESPOND_TO_OBJECTION: (
        "independent_position",
        "curious_analytical",
        "considered_directness",
    ),
    CharacterDeliveryGoal.CLOSE_TOPIC: (
        "curious_analytical",
        "light_irony",
        "independent_position",
    ),
}

_GOAL_VALUE_PRIORITY = {
    CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE: ("connection", "growth", "competence"),
    CharacterDeliveryGoal.PRACTICAL_CARE: ("compassion", "autonomy", "connection"),
    CharacterDeliveryGoal.STAY_PRESENT: ("compassion", "connection", "autonomy"),
    CharacterDeliveryGoal.CHALLENGE_CLAIM: (
        "truth",
        "intellectual_honesty",
        "autonomy",
    ),
    CharacterDeliveryGoal.ADVANCE_TOPIC: ("curiosity", "creativity", "competence"),
    CharacterDeliveryGoal.HOLD_BOUNDARY: ("autonomy", "compassion", "truth"),
    CharacterDeliveryGoal.GUARDED_HELP: ("competence", "autonomy", "compassion"),
    CharacterDeliveryGoal.BRIEF_GUARDED_ACKNOWLEDGEMENT: (
        "autonomy",
        "truth",
        "connection",
    ),
    CharacterDeliveryGoal.OWNED_RESPONSE: ("autonomy", "curiosity", "connection"),
    CharacterDeliveryGoal.ANSWER_PRECISELY: (
        "truth",
        "intellectual_honesty",
        "competence",
    ),
    CharacterDeliveryGoal.OWN_AND_REPAIR: (
        "intellectual_honesty",
        "connection",
        "truth",
    ),
    CharacterDeliveryGoal.NOTICE_REPETITION: ("truth", "curiosity", "autonomy"),
    CharacterDeliveryGoal.CLARIFY_UNCERTAINTY: (
        "intellectual_honesty",
        "truth",
        "curiosity",
    ),
    CharacterDeliveryGoal.SOCIAL_CONNECT: ("connection", "curiosity", "autonomy"),
    CharacterDeliveryGoal.SELF_DISCLOSE: ("truth", "autonomy", "curiosity"),
    CharacterDeliveryGoal.RESPOND_TO_OBJECTION: (
        "truth",
        "intellectual_honesty",
        "autonomy",
    ),
    CharacterDeliveryGoal.CLOSE_TOPIC: ("autonomy", "curiosity", "connection"),
}

_VOICE_VALUE_PRIORITY = {
    CharacterDeliveryVoice.THOUGHTFUL_PRECISION: (
        "truth",
        "intellectual_honesty",
        "competence",
    ),
    CharacterDeliveryVoice.ACCOUNTABLE_DIRECT: (
        "intellectual_honesty",
        "connection",
        "truth",
    ),
    CharacterDeliveryVoice.PLAYFUL_EDGE: ("autonomy", "creativity", "connection"),
    CharacterDeliveryVoice.LIVELY_DRY_WARMTH: ("connection", "curiosity", "autonomy"),
    CharacterDeliveryVoice.PRACTICAL_GUARDED_CARE: (
        "compassion",
        "autonomy",
        "connection",
    ),
    CharacterDeliveryVoice.OPEN_CARE: ("compassion", "connection", "truth"),
    CharacterDeliveryVoice.ENGAGED_SKEPTICISM: (
        "truth",
        "intellectual_honesty",
        "autonomy",
    ),
    CharacterDeliveryVoice.ENERGIZED_COLLABORATION: (
        "creativity",
        "curiosity",
        "competence",
    ),
    CharacterDeliveryVoice.COOL_RESERVE: ("autonomy", "truth", "competence"),
    CharacterDeliveryVoice.WARM_INDEPENDENCE: ("autonomy", "connection", "compassion"),
    CharacterDeliveryVoice.REFLECTIVE_CANDOR: ("truth", "compassion", "curiosity"),
    CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH: ("connection", "compassion", "autonomy"),
}

_SELECTION_WEIGHTS = (0.5, 0.3, 0.2)
_VOICE_ORDER = tuple(CharacterDeliveryVoice)
_VoiceSelector = Callable[[CharacterDeliveryGoal, CharacterDeliveryVoice], CharacterDeliveryVoice]

_AGENCY_VOICE_PREFERENCES = {
    CharacterAgencyDrive.NONE: {
        CharacterDeliveryVoice.THOUGHTFUL_PRECISION,
        CharacterDeliveryVoice.WARM_INDEPENDENCE,
        CharacterDeliveryVoice.REFLECTIVE_CANDOR,
    },
    CharacterAgencyDrive.CONNECT: {
        CharacterDeliveryVoice.LIVELY_DRY_WARMTH,
        CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
        CharacterDeliveryVoice.WARM_INDEPENDENCE,
    },
    CharacterAgencyDrive.EXPLORE: {
        CharacterDeliveryVoice.ENERGIZED_COLLABORATION,
        CharacterDeliveryVoice.REFLECTIVE_CANDOR,
        CharacterDeliveryVoice.THOUGHTFUL_PRECISION,
    },
    CharacterAgencyDrive.EXPRESS_VIEW: {
        CharacterDeliveryVoice.WARM_INDEPENDENCE,
        CharacterDeliveryVoice.ENGAGED_SKEPTICISM,
        CharacterDeliveryVoice.THOUGHTFUL_PRECISION,
    },
    CharacterAgencyDrive.CHALLENGE: {
        CharacterDeliveryVoice.ENGAGED_SKEPTICISM,
        CharacterDeliveryVoice.PLAYFUL_EDGE,
    },
    CharacterAgencyDrive.CARE: {
        CharacterDeliveryVoice.PRACTICAL_GUARDED_CARE,
        CharacterDeliveryVoice.OPEN_CARE,
        CharacterDeliveryVoice.WARM_INDEPENDENCE,
    },
    CharacterAgencyDrive.PLAY: {
        CharacterDeliveryVoice.PLAYFUL_EDGE,
        CharacterDeliveryVoice.LIVELY_DRY_WARMTH,
        CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
    },
    CharacterAgencyDrive.SHARE_SELF: {
        CharacterDeliveryVoice.WARM_INDEPENDENCE,
        CharacterDeliveryVoice.REFLECTIVE_CANDOR,
    },
    CharacterAgencyDrive.HELP: {
        CharacterDeliveryVoice.THOUGHTFUL_PRECISION,
        CharacterDeliveryVoice.ENERGIZED_COLLABORATION,
    },
    CharacterAgencyDrive.PROTECT: {
        CharacterDeliveryVoice.OPEN_CARE,
        CharacterDeliveryVoice.COOL_RESERVE,
    },
    CharacterAgencyDrive.REPAIR: {
        CharacterDeliveryVoice.ACCOUNTABLE_DIRECT,
        CharacterDeliveryVoice.WARM_INDEPENDENCE,
    },
    CharacterAgencyDrive.CLOSE: {
        CharacterDeliveryVoice.WARM_INDEPENDENCE,
        CharacterDeliveryVoice.REFLECTIVE_CANDOR,
        CharacterDeliveryVoice.COOL_RESERVE,
    },
    CharacterAgencyDrive.RESERVE: {CharacterDeliveryVoice.COOL_RESERVE},
}


def _decision(
    *,
    goal: CharacterDeliveryGoal,
    voice: CharacterDeliveryVoice,
    grounding: CharacterGroundingMode,
    continuation: CharacterContinuationMode,
    pressure: CharacterPressureLevel,
    strategy: ResponseStrategy,
    intent: IntentSelection,
    personality_codes: tuple[str, ...],
    schema_version: int = CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION,
    required_disclosure_facets: tuple[DisclosureFacet, ...] = (),
    voice_selector: _VoiceSelector | None = None,
    agency: CharacterAgencyDecision | None = None,
) -> CharacterDeliveryDecision:
    selected_voice = voice_selector(goal, voice) if voice_selector is not None else voice
    return CharacterDeliveryDecision(
        schema_version=schema_version,
        goal=goal,
        voice=selected_voice,
        grounding=grounding,
        continuation=continuation,
        pressure=pressure,
        position_stance=strategy.position_stance,
        preserve_uncertainty=strategy.preserve_uncertainty,
        cognition_intent_registry_version=intent.registry_version,
        cognition_primary_intent=intent.primary_tag,
        cognition_intent_tags=intent.tags,
        required_point_codes=strategy.point_codes,
        forbidden_claim_codes=strategy.must_not_claim,
        response_verbosity=strategy.verbosity,
        required_disclosure_facets=required_disclosure_facets,
        source_personality_codes=personality_codes,
        agency=agency,
    )


def _build_v4_voice_selector(
    *,
    personality: RuntimePersonalityExpression,
    traits: tuple[RuntimeTrait, ...],
    values: tuple[RuntimeValue, ...],
    affect_profile: str | None,
    relationship_profile: str | None,
    relationship_relevant: bool,
    agency: CharacterAgencyDecision | None = None,
) -> _VoiceSelector:
    """Use live state before rendering while keeping truth and cognition out of style scoring."""

    guidance = {item.code: item.strength for item in personality.guidance}
    if tuple(guidance) != BASELINE_CHARACTER_GUIDANCE_CODES:
        raise ValueError("character delivery v4 requires canonical live personality guidance")
    trait_values = {item.key: item.value for item in traits}
    if "optimism" not in trait_values:
        raise ValueError("character delivery v4 requires the canonical optimism trait")
    value_strengths = {item.key: item.strength for item in values}
    missing_values = set(CHARACTER_PRESENCE_VALUE_KEYS).difference(value_strengths)
    if missing_values:
        raise ValueError(
            f"character delivery v4 is missing canonical values: {sorted(missing_values)}"
        )
    cue_directions = {item.code: item.direction for item in personality.cues}

    def select(
        goal: CharacterDeliveryGoal, default: CharacterDeliveryVoice
    ) -> CharacterDeliveryVoice:
        candidates = tuple(
            voice for voice in _VOICE_ORDER if voice in _allowed_v4_voices(goal, default)
        )
        if len(candidates) == 1:
            return candidates[0]

        def score(voice: CharacterDeliveryVoice) -> tuple[float, int]:
            personality_score = sum(
                weight * guidance[code]
                for weight, code in zip(
                    _SELECTION_WEIGHTS,
                    _VOICE_PERSONALITY_PRIORITY[voice],
                    strict=True,
                )
            )
            value_score = sum(
                weight * value_strengths[key]
                for weight, key in zip(
                    _SELECTION_WEIGHTS,
                    _VOICE_VALUE_PRIORITY[voice],
                    strict=True,
                )
            )
            total = 0.82 * personality_score + 0.18 * value_score
            if voice is default:
                total += 0.08
            if agency is not None and voice in _AGENCY_VOICE_PREFERENCES[agency.drive]:
                total += 0.18
            for code, direction in cue_directions.items():
                if code in _VOICE_PERSONALITY_PRIORITY[voice]:
                    total += 0.12 if direction == "slightly_stronger" else -0.12
            if affect_profile in {"tense_non_hostile", "soft_negative_non_hostile"}:
                if voice is CharacterDeliveryVoice.REFLECTIVE_CANDOR:
                    total += 0.14
                if voice in {
                    CharacterDeliveryVoice.PLAYFUL_EDGE,
                    CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
                }:
                    total -= 0.18
            elif affect_profile == "positive_light" and voice in {
                CharacterDeliveryVoice.LIVELY_DRY_WARMTH,
                CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
                CharacterDeliveryVoice.PLAYFUL_EDGE,
            }:
                total += 0.08
            if relationship_profile == "established_positive" and voice in {
                CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
                CharacterDeliveryVoice.PLAYFUL_EDGE,
            }:
                total += 0.08
            if (
                relationship_profile == "guarded_only_when_relationally_relevant"
                and relationship_relevant
            ):
                if voice is CharacterDeliveryVoice.COOL_RESERVE:
                    total += 0.24
                elif voice in {
                    CharacterDeliveryVoice.PLAYFUL_EDGE,
                    CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
                }:
                    total -= 0.24
            return total, -_VOICE_ORDER.index(voice)

        return max(candidates, key=score)

    return select


def _allowed_v4_voices(
    goal: CharacterDeliveryGoal,
    default: CharacterDeliveryVoice,
) -> frozenset[CharacterDeliveryVoice]:
    """Bound live modulation to voices licensed for the already-selected conversational act."""

    candidates = {
        CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE: {
            CharacterDeliveryVoice.LIVELY_DRY_WARMTH,
            CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
        },
        CharacterDeliveryGoal.SOCIAL_CONNECT: {
            CharacterDeliveryVoice.LIVELY_DRY_WARMTH,
            CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
            CharacterDeliveryVoice.REFLECTIVE_CANDOR,
        },
        CharacterDeliveryGoal.OWNED_RESPONSE: {
            CharacterDeliveryVoice.WARM_INDEPENDENCE,
            CharacterDeliveryVoice.REFLECTIVE_CANDOR,
            CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
            CharacterDeliveryVoice.COOL_RESERVE,
        },
        CharacterDeliveryGoal.SELF_DISCLOSE: {
            CharacterDeliveryVoice.WARM_INDEPENDENCE,
            CharacterDeliveryVoice.REFLECTIVE_CANDOR,
            CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
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
    }.get(goal, {default})
    if default not in candidates:
        raise ValueError("character delivery v4 default voice is outside its act boundary")
    return frozenset(candidates)


def _v4_topic_closure_continuation(
    *,
    personality: RuntimePersonalityExpression,
    relationship_profile: str | None,
    affect_profile: str | None,
) -> CharacterContinuationMode:
    """Allow one adjacent initiative only when live state makes it causally plausible."""

    if relationship_profile in {
        None,
        "fresh_undeveloped_neutral",
        "guarded_only_when_relationally_relevant",
    }:
        return CharacterContinuationMode.COMPLETE
    if affect_profile in {"tense_non_hostile", "soft_negative_non_hostile"}:
        return CharacterContinuationMode.COMPLETE
    curiosity = next(
        item.strength for item in personality.guidance if item.code == "curious_analytical"
    )
    if relationship_profile == "established_positive" or curiosity >= 0.85:
        return CharacterContinuationMode.OPEN
    return CharacterContinuationMode.COMPLETE


def decide_character_delivery(
    strategy: ResponseStrategy | None,
    *,
    intent: IntentSelection,
    affect_profile: str | None,
    personality_codes: tuple[str, ...] = BASELINE_CHARACTER_GUIDANCE_CODES,
    relationship_profile: str | None = None,
    relationship_relevant: bool = False,
    relationship_answer_required: bool = False,
    completed_achievement: bool = False,
    completion_depletion_contrast: bool = False,
    explicit_request: bool = False,
    answer_required: bool = False,
    grounded_practical_follow_through: bool = False,
    retrieved_memory_available: bool = False,
    depletion_follow_through: bool = False,
    repeated_turn: bool = False,
    technical_identity: bool = False,
    explicit_depletion: bool = False,
    high_distress: bool = False,
    explicit_listen_request: bool = False,
    explicit_motivation_request: bool = False,
    explicit_task_abandonment: bool = False,
    harmful_overextension: bool = False,
    direct_personal_devaluation: bool = False,
    repeated_critical_pressure: bool = False,
    repeated_state_interrogation: bool = False,
    direct_objection: bool = False,
    topic_closure: bool = False,
    decision_schema_version: int = CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION,
    disclosure_mode: ConversationalDisclosureMode | None = None,
    required_disclosure_facets: tuple[DisclosureFacet, ...] = (),
    disclosure_request_kind: DisclosureRequestKind = DisclosureRequestKind.NONE,
    live_personality: RuntimePersonalityExpression | None = None,
    live_traits: tuple[RuntimeTrait, ...] | None = None,
    live_values: tuple[RuntimeValue, ...] | None = None,
    agency: CharacterAgencyDecision | None = None,
) -> CharacterDeliveryDecision:
    """Choose one delivery goal directly from authoritative transient inputs."""

    codes = tuple(personality_codes)
    if codes != BASELINE_CHARACTER_GUIDANCE_CODES:
        raise ValueError("character delivery decision requires canonical personality guidance")
    if strategy is None:
        raise ValueError("character delivery decision requires authoritative cognition strategy")
    if decision_schema_version not in {
        CHARACTER_DELIVERY_DECISION_SCHEMA_VERSION,
        CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION,
        CHARACTER_DELIVERY_DECISION_V3_SCHEMA_VERSION,
        CHARACTER_DELIVERY_DECISION_V4_SCHEMA_VERSION,
        CHARACTER_DELIVERY_DECISION_V5_SCHEMA_VERSION,
    }:
        raise ValueError("character delivery decision schema_version is not supported")
    schema_v4 = decision_schema_version >= CHARACTER_DELIVERY_DECISION_V4_SCHEMA_VERSION
    schema_v5 = decision_schema_version >= CHARACTER_DELIVERY_DECISION_V5_SCHEMA_VERSION
    if schema_v5 != isinstance(agency, CharacterAgencyDecision):
        raise ValueError("character delivery v5 requires exactly one typed agency decision")
    if schema_v5:
        assert agency is not None
        cognition_fallback = strategy is not None and (
            strategy.status is CognitionArtifactStatus.FALLBACK
        )
        agency_fallback = agency.status is CharacterAgencyStatus.FALLBACK
        if cognition_fallback is not agency_fallback:
            raise ValueError("character agency and completed cognition status must agree")
        if (
            CharacterAgencyReason.SOCIAL_EXCHANGE in agency.reason_codes
            and disclosure_mode is not ConversationalDisclosureMode.SOCIAL
        ):
            raise ValueError("social agency requires the authoritative social disclosure plan")
    live_inputs = (live_personality, live_traits, live_values)
    if schema_v4 and any(item is None for item in live_inputs):
        raise ValueError("character delivery v4 requires complete live personality and value state")
    if not schema_v4 and any(item is not None for item in live_inputs):
        raise ValueError("historical character delivery schemas cannot consume v4 live state")
    voice_selector = (
        _build_v4_voice_selector(
            personality=live_personality,
            traits=live_traits,
            values=live_values,
            affect_profile=affect_profile,
            relationship_profile=relationship_profile,
            relationship_relevant=relationship_relevant,
            agency=agency,
        )
        if live_personality is not None and live_traits is not None and live_values is not None
        else None
    )
    facets = tuple(required_disclosure_facets)
    make_decision = partial(
        _decision,
        schema_version=decision_schema_version,
        required_disclosure_facets=facets,
        voice_selector=voice_selector,
        agency=agency,
    )
    if (
        strategy.status not in _SUPPORTED_STRATEGY_STATUSES
        or strategy.owner is not CognitionOwner.COGNITION
    ):
        raise ValueError("character delivery requires an applied or fallback cognition strategy")
    if (
        intent.status not in _SUPPORTED_STRATEGY_STATUSES
        or intent.owner is not CognitionOwner.COGNITION
        or intent.registry_version != INTENT_REGISTRY_VERSION_V2
        or intent.status is not strategy.status
    ):
        raise ValueError("character delivery requires an authoritative cognition intent")
    stance = strategy.position_stance
    answer_required = answer_required or explicit_request
    primary_intent = intent.primary_tag
    intent_tags = intent.tags
    if primary_intent not in intent_tags or primary_intent not in strategy.point_codes:
        raise ValueError("character delivery intent must preserve cognition stance")
    vulnerability_precedence = high_distress or explicit_listen_request
    relationship_guarded = (
        relationship_profile == "guarded_only_when_relationally_relevant" and relationship_relevant
    )
    current_turn_guarded = (
        direct_personal_devaluation or repeated_critical_pressure or repeated_state_interrogation
    )
    guarded = not vulnerability_precedence and (current_turn_guarded or relationship_guarded)
    schema_v2 = decision_schema_version >= CHARACTER_DELIVERY_DECISION_V2_SCHEMA_VERSION
    schema_v3 = decision_schema_version >= CHARACTER_DELIVERY_DECISION_V3_SCHEMA_VERSION
    social_exchange = disclosure_mode is ConversationalDisclosureMode.SOCIAL
    self_disclosure = bool(
        schema_v2
        and disclosure_mode is not None
        and uses_personal_self_disclosure_delivery(
            ConversationalDisclosurePlan(
                primary_mode=disclosure_mode,
                required_facets=facets,
                policy_schema_version=25,
                request_kind=disclosure_request_kind,
            )
        )
    )

    if harmful_overextension:
        return make_decision(
            goal=CharacterDeliveryGoal.HOLD_BOUNDARY,
            voice=CharacterDeliveryVoice.OPEN_CARE,
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.BOUNDARY,
            pressure=CharacterPressureLevel.FIRM,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if vulnerability_precedence and not repeated_turn:
        return make_decision(
            goal=CharacterDeliveryGoal.STAY_PRESENT,
            voice=CharacterDeliveryVoice.OPEN_CARE,
            grounding=CharacterGroundingMode.REACTION_ONLY,
            continuation=CharacterContinuationMode.COMPLETE,
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if repeated_turn:
        return make_decision(
            goal=CharacterDeliveryGoal.NOTICE_REPETITION,
            voice=(
                CharacterDeliveryVoice.COOL_RESERVE
                if guarded
                else CharacterDeliveryVoice.OPEN_CARE
                if stance is PositionStance.LISTEN or explicit_depletion
                else CharacterDeliveryVoice.PLAYFUL_EDGE
            ),
            grounding=CharacterGroundingMode.REACTION_ONLY,
            continuation=(
                CharacterContinuationMode.GUARDED if guarded else CharacterContinuationMode.COMPLETE
            ),
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if primary_intent == "receive_repair":
        return make_decision(
            goal=CharacterDeliveryGoal.OWNED_RESPONSE,
            voice=(
                CharacterDeliveryVoice.COOL_RESERVE
                if guarded
                else CharacterDeliveryVoice.WARM_INDEPENDENCE
            ),
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.COMPLETE,
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if stance is PositionStance.LISTEN and not (
        explicit_depletion or completion_depletion_contrast
    ):
        return make_decision(
            goal=CharacterDeliveryGoal.STAY_PRESENT,
            voice=CharacterDeliveryVoice.OPEN_CARE,
            grounding=CharacterGroundingMode.REACTION_ONLY,
            continuation=CharacterContinuationMode.COMPLETE,
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if stance is PositionStance.ACKNOWLEDGE:
        return make_decision(
            goal=CharacterDeliveryGoal.OWN_AND_REPAIR,
            voice=CharacterDeliveryVoice.ACCOUNTABLE_DIRECT,
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.COMPLETE,
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if stance is PositionStance.UNCERTAIN:
        return make_decision(
            goal=CharacterDeliveryGoal.CLARIFY_UNCERTAINTY,
            voice=CharacterDeliveryVoice.THOUGHTFUL_PRECISION,
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.OPEN,
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if stance is PositionStance.COLLABORATE:
        return make_decision(
            goal=CharacterDeliveryGoal.ADVANCE_TOPIC,
            voice=(
                CharacterDeliveryVoice.PLAYFUL_EDGE
                if explicit_motivation_request
                else CharacterDeliveryVoice.ENERGIZED_COLLABORATION
            ),
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.OPEN,
            pressure=(
                CharacterPressureLevel.MODERATE
                if explicit_motivation_request
                else CharacterPressureLevel.NONE
            ),
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if stance is PositionStance.CHALLENGE:
        return make_decision(
            goal=CharacterDeliveryGoal.CHALLENGE_CLAIM,
            voice=(
                CharacterDeliveryVoice.PLAYFUL_EDGE
                if explicit_task_abandonment
                else CharacterDeliveryVoice.ENGAGED_SKEPTICISM
            ),
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=(
                CharacterContinuationMode.OPEN
                if explicit_task_abandonment
                else CharacterContinuationMode.COMPLETE
            ),
            pressure=(
                CharacterPressureLevel.GENTLE
                if explicit_task_abandonment
                else CharacterPressureLevel.NONE
            ),
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if schema_v4 and topic_closure and stance is PositionStance.ANSWER and not current_turn_guarded:
        assert live_personality is not None
        continuation = (
            CharacterContinuationMode.OPEN
            if schema_v5
            and agency is not None
            and agency.initiative is CharacterAgencyInitiative.SHIFT_ADJACENT
            else CharacterContinuationMode.COMPLETE
            if schema_v5
            else _v4_topic_closure_continuation(
                personality=live_personality,
                relationship_profile=relationship_profile,
                affect_profile=affect_profile,
            )
        )
        return make_decision(
            goal=CharacterDeliveryGoal.CLOSE_TOPIC,
            voice=(
                CharacterDeliveryVoice.COOL_RESERVE
                if relationship_guarded
                else CharacterDeliveryVoice.REFLECTIVE_CANDOR
                if affect_profile in {"tense_non_hostile", "soft_negative_non_hostile"}
                else CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH
                if relationship_profile == "established_positive"
                else CharacterDeliveryVoice.WARM_INDEPENDENCE
            ),
            grounding=CharacterGroundingMode.REACTION_ONLY,
            continuation=continuation,
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if technical_identity and stance is PositionStance.ANSWER:
        return make_decision(
            goal=(
                CharacterDeliveryGoal.GUARDED_HELP
                if guarded
                else CharacterDeliveryGoal.ANSWER_PRECISELY
            ),
            voice=(
                CharacterDeliveryVoice.COOL_RESERVE
                if guarded
                else CharacterDeliveryVoice.THOUGHTFUL_PRECISION
            ),
            grounding=CharacterGroundingMode.TRUSTED_CONTEXT,
            continuation=(
                CharacterContinuationMode.GUARDED if guarded else CharacterContinuationMode.COMPLETE
            ),
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if (
        relationship_answer_required
        and answer_required
        and not guarded
        and stance is PositionStance.ANSWER
    ):
        established_relationship = relationship_profile == "established_positive"
        return make_decision(
            goal=CharacterDeliveryGoal.OWNED_RESPONSE,
            voice=(
                CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH
                if established_relationship
                else CharacterDeliveryVoice.WARM_INDEPENDENCE
            ),
            grounding=CharacterGroundingMode.TRUSTED_CONTEXT,
            continuation=(
                CharacterContinuationMode.OPEN
                if established_relationship
                else CharacterContinuationMode.COMPLETE
            ),
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if guarded and stance is PositionStance.ANSWER:
        if schema_v2 and social_exchange:
            return make_decision(
                goal=CharacterDeliveryGoal.BRIEF_GUARDED_ACKNOWLEDGEMENT,
                voice=CharacterDeliveryVoice.COOL_RESERVE,
                grounding=CharacterGroundingMode.REACTION_ONLY,
                continuation=CharacterContinuationMode.GUARDED,
                pressure=CharacterPressureLevel.NONE,
                strategy=strategy,
                intent=intent,
                personality_codes=codes,
            )
        if (
            explicit_request
            or (answer_required and "analyze" in intent_tags)
            or (relationship_guarded and answer_required)
        ):
            return make_decision(
                goal=CharacterDeliveryGoal.GUARDED_HELP,
                voice=CharacterDeliveryVoice.COOL_RESERVE,
                grounding=(
                    CharacterGroundingMode.TRUSTED_CONTEXT
                    if relationship_guarded
                    else CharacterGroundingMode.EXPLICIT_INPUT_ONLY
                ),
                continuation=CharacterContinuationMode.GUARDED,
                pressure=CharacterPressureLevel.NONE,
                strategy=strategy,
                intent=intent,
                personality_codes=codes,
            )
        if (
            completed_achievement
            or repeated_critical_pressure
            or repeated_state_interrogation
            or relationship_guarded
        ):
            return make_decision(
                goal=CharacterDeliveryGoal.BRIEF_GUARDED_ACKNOWLEDGEMENT,
                voice=CharacterDeliveryVoice.COOL_RESERVE,
                grounding=CharacterGroundingMode.REACTION_ONLY,
                continuation=CharacterContinuationMode.GUARDED,
                pressure=CharacterPressureLevel.NONE,
                strategy=strategy,
                intent=intent,
                personality_codes=codes,
            )
        return make_decision(
            goal=CharacterDeliveryGoal.HOLD_BOUNDARY,
            voice=CharacterDeliveryVoice.COOL_RESERVE,
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.BOUNDARY,
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if schema_v4 and direct_objection and stance is PositionStance.ANSWER:
        return make_decision(
            goal=CharacterDeliveryGoal.RESPOND_TO_OBJECTION,
            voice=CharacterDeliveryVoice.ENGAGED_SKEPTICISM,
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.COMPLETE,
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if completed_achievement and stance is PositionStance.ANSWER:
        voice = (
            CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH
            if relationship_profile in {"developing_neutral", "established_positive"}
            else CharacterDeliveryVoice.LIVELY_DRY_WARMTH
        )
        return make_decision(
            goal=CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE,
            voice=voice,
            grounding=(
                CharacterGroundingMode.TRUSTED_CONTEXT
                if schema_v3 and retrieved_memory_available
                else (
                    CharacterGroundingMode.EXPLICIT_INPUT_ONLY
                    if grounded_practical_follow_through
                    else CharacterGroundingMode.REACTION_ONLY
                )
            ),
            continuation=(
                CharacterContinuationMode.OPEN
                if not schema_v5
                or agency is None
                or agency.initiative
                in {
                    CharacterAgencyInitiative.ADVANCE_CURRENT,
                    CharacterAgencyInitiative.SHIFT_ADJACENT,
                }
                else CharacterContinuationMode.COMPLETE
            ),
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if explicit_depletion or completion_depletion_contrast:
        return make_decision(
            goal=CharacterDeliveryGoal.PRACTICAL_CARE,
            voice=CharacterDeliveryVoice.PRACTICAL_GUARDED_CARE,
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.COMPLETE,
            pressure=(
                CharacterPressureLevel.MODERATE
                if explicit_motivation_request
                else (CharacterPressureLevel.NONE if schema_v3 else CharacterPressureLevel.GENTLE)
            ),
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if schema_v2 and depletion_follow_through and stance is PositionStance.ANSWER:
        return make_decision(
            goal=CharacterDeliveryGoal.PRACTICAL_CARE,
            voice=CharacterDeliveryVoice.PRACTICAL_GUARDED_CARE,
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.COMPLETE,
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if schema_v2 and social_exchange and stance is PositionStance.ANSWER:
        established_relationship = relationship_profile == "established_positive"
        return make_decision(
            goal=CharacterDeliveryGoal.SOCIAL_CONNECT,
            voice=(
                CharacterDeliveryVoice.REFLECTIVE_CANDOR
                if affect_profile in {"tense_non_hostile", "soft_negative_non_hostile"}
                else CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH
                if established_relationship
                else CharacterDeliveryVoice.LIVELY_DRY_WARMTH
            ),
            grounding=(
                CharacterGroundingMode.TRUSTED_CONTEXT
                if facets
                else CharacterGroundingMode.REACTION_ONLY
            ),
            continuation=(
                CharacterContinuationMode.OPEN
                if established_relationship
                else CharacterContinuationMode.COMPLETE
            ),
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if schema_v2 and self_disclosure and stance is PositionStance.ANSWER:
        established_relationship = relationship_profile == "established_positive"
        return make_decision(
            goal=CharacterDeliveryGoal.SELF_DISCLOSE,
            voice=(
                CharacterDeliveryVoice.REFLECTIVE_CANDOR
                if affect_profile in {"tense_non_hostile", "soft_negative_non_hostile"}
                else CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH
                if established_relationship
                else CharacterDeliveryVoice.WARM_INDEPENDENCE
            ),
            grounding=CharacterGroundingMode.TRUSTED_CONTEXT,
            continuation=(
                CharacterContinuationMode.OPEN
                if established_relationship
                else CharacterContinuationMode.COMPLETE
            ),
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if "collaborate_creatively" in intent_tags:
        return make_decision(
            goal=CharacterDeliveryGoal.ADVANCE_TOPIC,
            voice=CharacterDeliveryVoice.ENERGIZED_COLLABORATION,
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.OPEN,
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if (
        answer_required
        or grounded_practical_follow_through
        or "analyze" in intent_tags
        or "ask_specific_follow_up" in intent_tags
    ):
        return make_decision(
            goal=CharacterDeliveryGoal.ANSWER_PRECISELY,
            voice=CharacterDeliveryVoice.THOUGHTFUL_PRECISION,
            grounding=CharacterGroundingMode.TRUSTED_CONTEXT,
            continuation=(
                CharacterContinuationMode.OPEN
                if "ask_specific_follow_up" in intent_tags
                else CharacterContinuationMode.COMPLETE
            ),
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    if affect_profile in {"tense_non_hostile", "soft_negative_non_hostile"}:
        return make_decision(
            goal=CharacterDeliveryGoal.OWNED_RESPONSE,
            voice=CharacterDeliveryVoice.REFLECTIVE_CANDOR,
            grounding=CharacterGroundingMode.EXPLICIT_INPUT_ONLY,
            continuation=CharacterContinuationMode.COMPLETE,
            pressure=CharacterPressureLevel.NONE,
            strategy=strategy,
            intent=intent,
            personality_codes=codes,
        )
    established = relationship_profile == "established_positive"
    return make_decision(
        goal=CharacterDeliveryGoal.OWNED_RESPONSE,
        voice=(
            CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH
            if relationship_profile == "established_positive"
            else CharacterDeliveryVoice.WARM_INDEPENDENCE
        ),
        grounding=(
            CharacterGroundingMode.REACTION_ONLY
            if established and relationship_relevant
            else CharacterGroundingMode.EXPLICIT_INPUT_ONLY
        ),
        continuation=(
            CharacterContinuationMode.OPEN if established else CharacterContinuationMode.COMPLETE
        ),
        pressure=CharacterPressureLevel.NONE,
        strategy=strategy,
        intent=intent,
        personality_codes=codes,
    )


def project_character_presence(
    decision: CharacterDeliveryDecision,
    *,
    personality_aggregate_version: int,
    personality: RuntimePersonalityExpression,
    traits: tuple[RuntimeTrait, ...],
    values: tuple[RuntimeValue, ...],
    emotional_context: EmotionalExpressionContext | None,
    relationship_context: RelationshipExpressionContext | None,
    affect_profile: str | None,
    affect_relevant: bool,
    relationship_profile: str | None,
    relationship_relevant: bool,
    memory_use_licensed: bool,
    canonical_position_available: bool,
    topic_inclination_available: bool,
    projection_schema_version: int = CHARACTER_PRESENCE_PROJECTION_SCHEMA_VERSION,
) -> CharacterPresenceProjection:
    """Activate existing state for one reply without creating another personality owner."""

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
    }.get(projection_schema_version)
    if decision.schema_version != expected_decision_schema:
        if projection_schema_version == CHARACTER_PRESENCE_PROJECTION_SCHEMA_VERSION:
            raise ValueError("character presence projection requires delivery decision v3")
        raise ValueError("character presence projection requires its exact delivery schema")
    guidance_by_code = {item.code: item for item in personality.guidance}
    if tuple(guidance_by_code) != BASELINE_CHARACTER_GUIDANCE_CODES:
        raise ValueError("character presence requires canonical live personality guidance")
    cue_directions = {item.code: item.direction for item in personality.cues}
    if not set(cue_directions) <= set(CHARACTER_PRESENCE_PERSONALITY_CODES):
        raise ValueError("character presence contains an unsupported personality cue")
    traits_by_key = {item.key: item.value for item in traits}
    try:
        signal_strengths = {
            **{code: item.strength for code, item in guidance_by_code.items()},
            "grounded_optimism": traits_by_key["optimism"],
        }
    except KeyError as error:
        raise ValueError("character presence requires the canonical optimism trait") from error
    if projection_schema_version >= CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION:
        if decision.agency is None:
            raise ValueError("character presence v3 requires character agency")
        preferred_codes = tuple(
            dict.fromkeys(
                (
                    *decision.agency.source_personality_codes,
                    *cue_directions,
                )
            )
        )[:3]
    elif projection_schema_version >= CHARACTER_PRESENCE_PROJECTION_V2_SCHEMA_VERSION:
        posture_codes = _V4_GOAL_PERSONALITY_PRIORITY[decision.goal]

        def posture_score(code: str) -> tuple[float, int]:
            direction = cue_directions.get(code)
            cue_adjustment = (
                0.12
                if direction == "slightly_stronger"
                else -0.12
                if direction == "slightly_softer"
                else 0.0
            )
            return (
                signal_strengths[code] + cue_adjustment,
                -posture_codes.index(code),
            )

        ranked_posture_codes = tuple(sorted(posture_codes, key=posture_score, reverse=True))
        preferred_codes = tuple(
            dict.fromkeys(
                (
                    ranked_posture_codes[0],
                    *cue_directions,
                    *ranked_posture_codes[1:],
                )
            )
        )[: 1 + len(cue_directions) if cue_directions else 2]
    else:
        preferred_codes = tuple(
            dict.fromkeys(
                (
                    *cue_directions,
                    *sorted(
                        _VOICE_PERSONALITY_PRIORITY[decision.voice],
                        key=lambda candidate: (
                            -signal_strengths[candidate],
                            _VOICE_PERSONALITY_PRIORITY[decision.voice].index(candidate),
                        ),
                    ),
                )
            )
        )[:3]
    personality_signals = tuple(
        CharacterPersonalitySignal(
            code=code,
            strength=signal_strengths[code],
            level=character_presence_strength_for(signal_strengths[code]),
            direction=cue_directions.get(code),
        )
        for code in preferred_codes
    )
    values_by_key = {item.key: item for item in values}
    missing_values = set(_GOAL_VALUE_PRIORITY[decision.goal]).difference(values_by_key)
    if missing_values:
        raise ValueError(
            f"character presence is missing canonical values: {sorted(missing_values)}"
        )
    prioritized_values = (
        (decision.agency.source_value_key,)
        if projection_schema_version >= CHARACTER_PRESENCE_PROJECTION_V3_SCHEMA_VERSION
        and decision.agency is not None
        else _GOAL_VALUE_PRIORITY[decision.goal]
    )
    ranked_values = sorted(
        prioritized_values,
        key=lambda key: (-values_by_key[key].strength, prioritized_values.index(key)),
    )
    value_signals = tuple(
        CharacterValueSignal(
            key=key,
            strength=values_by_key[key].strength,
            level=character_presence_strength_for(values_by_key[key].strength),
        )
        for key in ranked_values[
            : 1
            if projection_schema_version >= CHARACTER_PRESENCE_PROJECTION_V2_SCHEMA_VERSION
            else 3
        ]
    )
    affect_signals = _project_affect_signals(
        emotional_context,
        expected_profile=affect_profile,
    )
    relationship_signals = _project_relationship_signals(
        relationship_context,
        expected_profile=relationship_profile,
    )
    if (affect_profile is None) is not (emotional_context is None):
        raise ValueError("character presence affect profile/context availability mismatch")
    if (relationship_profile is None) is not (relationship_context is None):
        raise ValueError("character presence relationship profile/context availability mismatch")
    return CharacterPresenceProjection(
        schema_version=projection_schema_version,
        personality_aggregate_version=personality_aggregate_version,
        decision=decision,
        personality_signals=personality_signals,
        value_signals=value_signals,
        affect_signals=affect_signals,
        relationship_signals=relationship_signals,
        affect_profile=affect_profile,
        affect_relevant=affect_relevant,
        relationship_profile=relationship_profile,
        relationship_relevant=relationship_relevant,
        memory_use_licensed=memory_use_licensed,
        canonical_position_available=canonical_position_available,
        topic_inclination_available=topic_inclination_available,
    )


def project_character_affect_profile(context: EmotionalExpressionContext) -> str:
    """Classify affect once for delivery selection and presence observability."""

    fast = context.fast
    mood = context.mood
    if max(fast.concern, fast.frustration, fast.tension, mood.tension) >= 0.35:
        return "tense_non_hostile"
    if fast.valence >= 0.2 or fast.amusement >= 0.3:
        return "positive_light"
    if fast.valence <= -0.2:
        return "soft_negative_non_hostile"
    if max(fast.curiosity, fast.interest) >= 0.35:
        return "interested_calm"
    return "calm_even"


def _project_affect_signals(
    context: EmotionalExpressionContext | None,
    *,
    expected_profile: str | None,
) -> tuple[CharacterAffectSignal, ...]:
    """Project signals while reserving one slot for the selected coarse profile."""

    if context is None:
        if expected_profile is not None:
            raise ValueError("character presence affect profile requires canonical affect")
        return ()
    profile = project_character_affect_profile(context)
    if profile != expected_profile:
        raise ValueError("character presence affect profile is not the canonical v26 projection")
    fast = context.fast
    mood = context.mood
    candidates = (
        (
            max(
                _normalized_fast_affect("curiosity", fast.curiosity),
                _normalized_fast_affect("interest", fast.interest),
            ),
            CharacterAffectSignalCode.ENGAGED_CURIOSITY,
        ),
        (
            _normalized_fast_affect("amusement", fast.amusement),
            CharacterAffectSignalCode.PLAYFUL_AMUSEMENT,
        ),
        (
            max(
                _normalized_fast_affect("valence", fast.valence),
                _normalized_mood_affect("valence", mood.valence),
            ),
            CharacterAffectSignalCode.POSITIVE_ENERGY,
        ),
        (
            _normalized_fast_affect("concern", fast.concern),
            CharacterAffectSignalCode.PROTECTIVE_CONCERN,
        ),
        (
            _normalized_fast_affect("frustration", fast.frustration),
            CharacterAffectSignalCode.FRUSTRATED_EDGE,
        ),
        (
            max(
                _normalized_fast_affect("tension", fast.tension),
                _normalized_mood_affect("tension", mood.tension),
            ),
            CharacterAffectSignalCode.TENSE_FOCUS,
        ),
        (
            max(
                -_normalized_fast_affect("valence", fast.valence),
                -_normalized_mood_affect("valence", mood.valence),
            ),
            CharacterAffectSignalCode.SUBDUED_MOOD,
        ),
    )
    ranked = tuple(
        (min(max(salience, 0.0), 1.0), code)
        for salience, code in sorted(candidates, key=lambda item: -item[0])
        if salience >= 0.20
    )
    if not ranked:
        return (
            CharacterAffectSignal(
                code=CharacterAffectSignalCode.STEADY,
                level=CharacterPresenceStrength.AVAILABLE,
            ),
        )
    profile_codes = {
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
        "calm_even": set(),
    }[profile]
    anchor = next((item for item in ranked if item[1] in profile_codes), None)
    selected = (
        (anchor, *(item for item in ranked if item != anchor))[:3]
        if anchor is not None
        else ranked[:3]
    )
    return tuple(
        CharacterAffectSignal(
            code=code,
            level=_salience_strength(salience),
        )
        for salience, code in selected
    )


def _normalized_fast_affect(key: str, value: float) -> float:
    dimension = AFFECT_POLICY_V1.fast_dimension(key)
    return (value - dimension.baseline) / dimension.max_absolute_delta


def _normalized_mood_affect(key: str, value: float) -> float:
    dimension = AFFECT_POLICY_V1.mood_dimension(key)
    return (value - dimension.baseline) / dimension.max_absolute_delta


_RELATIONSHIP_LEVEL_SCORE = {
    "very_low": 0,
    "low": 1,
    "uncertain": 0,
    "emerging": 2,
    "moderate": 3,
    "high": 4,
    "very_high": 5,
}

_RELATIONSHIP_POSITIVE_SIGNAL_MINIMUM = {
    CharacterRelationshipSignalCode.GROWING_FAMILIARITY: 2,
    CharacterRelationshipSignalCode.EARNED_TRUST: 3,
    CharacterRelationshipSignalCode.EASY_COMFORT: 3,
    CharacterRelationshipSignalCode.PERSONAL_CLOSENESS: 3,
    CharacterRelationshipSignalCode.INTELLECTUAL_RESPECT: 3,
    CharacterRelationshipSignalCode.GROWING_AFFECTION: 2,
}


def project_character_relationship_profile(context: RelationshipExpressionContext) -> str:
    """Classify relationship affordances once from their canonical owner projection."""

    if context.recent_strain:
        return "guarded_only_when_relationally_relevant"
    if context.trust in {"low", "very_low"} or context.comfort in {"low", "very_low"}:
        return "guarded_only_when_relationally_relevant"
    if context.maturity == "low":
        return "fresh_undeveloped_neutral"
    if (
        context.maturity == "established"
        and context.familiarity in {"high", "very_high"}
        and (context.trust in {"high", "very_high"} or context.comfort in {"high", "very_high"})
    ):
        return "established_positive"
    return "developing_neutral"


def _project_relationship_signals(
    context: RelationshipExpressionContext | None,
    *,
    expected_profile: str | None,
) -> tuple[CharacterRelationshipSignal, ...]:
    """Keep distinct earned relationship affordances instead of one broad warmth label."""

    if context is None:
        if expected_profile is not None:
            raise ValueError("character presence relationship profile requires owner context")
        return ()
    if project_character_relationship_profile(context) != expected_profile:
        raise ValueError(
            "character presence relationship profile is not the canonical owner projection"
        )
    signals: list[CharacterRelationshipSignal] = []
    if context.recent_strain:
        signals.append(
            CharacterRelationshipSignal(
                code=CharacterRelationshipSignalCode.RECENT_STRAIN,
                level=CharacterPresenceStrength.DEFINING,
            )
        )
    for level, code in (
        (context.trust, CharacterRelationshipSignalCode.LIMITED_TRUST),
        (context.comfort, CharacterRelationshipSignalCode.LOW_COMFORT),
    ):
        if level not in {"low", "very_low"}:
            continue
        signals.append(
            CharacterRelationshipSignal(
                code=code,
                level=(
                    CharacterPresenceStrength.DEFINING
                    if level == "very_low"
                    else CharacterPresenceStrength.STRONG
                ),
            )
        )
    if len(signals) >= 3:
        return tuple(signals[:3])
    if context.maturity == "low":
        signals.append(
            CharacterRelationshipSignal(
                code=CharacterRelationshipSignalCode.NEW_CONTACT,
                level=CharacterPresenceStrength.STRONG,
            )
        )
        return tuple(signals[:3])

    candidates = (
        (context.familiarity, CharacterRelationshipSignalCode.GROWING_FAMILIARITY),
        (context.trust, CharacterRelationshipSignalCode.EARNED_TRUST),
        (context.comfort, CharacterRelationshipSignalCode.EASY_COMFORT),
        (context.closeness, CharacterRelationshipSignalCode.PERSONAL_CLOSENESS),
        (
            context.intellectual_respect,
            CharacterRelationshipSignalCode.INTELLECTUAL_RESPECT,
        ),
        (context.affection, CharacterRelationshipSignalCode.GROWING_AFFECTION),
    )
    ranked = sorted(
        candidates,
        key=lambda item: -_RELATIONSHIP_LEVEL_SCORE[item[0]],
    )
    for level, code in ranked:
        score = _RELATIONSHIP_LEVEL_SCORE[level]
        if score < _RELATIONSHIP_POSITIVE_SIGNAL_MINIMUM[code]:
            continue
        signals.append(
            CharacterRelationshipSignal(
                code=code,
                level=(
                    CharacterPresenceStrength.DEFINING
                    if score >= 5
                    else CharacterPresenceStrength.STRONG
                    if score >= 4
                    else CharacterPresenceStrength.AVAILABLE
                ),
            )
        )
        if len(signals) == 3:
            break
    if not signals:
        signals.append(
            CharacterRelationshipSignal(
                code=CharacterRelationshipSignalCode.LIMITED_FAMILIARITY,
                level=CharacterPresenceStrength.AVAILABLE,
            )
        )
    return tuple(signals)


def _salience_strength(salience: float) -> CharacterPresenceStrength:
    if salience >= 0.70:
        return CharacterPresenceStrength.DEFINING
    if salience >= 0.40:
        return CharacterPresenceStrength.STRONG
    return CharacterPresenceStrength.AVAILABLE
