"""Request-local character agency selected before response-strategy realization.

The kernel is a pure read projection.  It owns no persistent state, accepts no raw
conversation prose and emits no reply wording.  Cognition remains authoritative for
truth, safety and required response substance; this decision only records what Satori
currently wants to contribute to the exchange.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from satori.application.affect.contracts import EmotionalExpressionContext
from satori.application.cognition.contracts import (
    CognitionArtifactStatus,
    NeedDimension,
    PerceptionSignal,
    PreparedCognitionIntake,
)
from satori.application.conversation.character_expression import (
    BASELINE_CHARACTER_GUIDANCE_CODES,
)
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosureMode,
    ConversationalDisclosurePlan,
)
from satori.application.positions.contracts import (
    InclinationContextItem,
    PositionContextItem,
    SatoriInclinationsContext,
    SatoriPositionsContext,
)
from satori.application.relationship.contracts import RelationshipExpressionContext

if TYPE_CHECKING:
    from satori.application.conversation.character_evidence import CharacterRequestEvidence
    from satori.application.conversation.coherence import DialogueCoherenceContext
    from satori.application.conversation.contracts import RuntimeCharacterContext

CHARACTER_AGENCY_DECISION_SCHEMA_VERSION = 1


class CharacterAgencyStatus(StrEnum):
    """Whether normal selection or the conservative cognition fallback was used."""

    APPLIED = "applied"
    FALLBACK = "fallback"


class CharacterAgencyDrive(StrEnum):
    """The current motive from which Satori enters this reply."""

    NONE = "none"
    CONNECT = "connect"
    EXPLORE = "explore"
    EXPRESS_VIEW = "express_view"
    CHALLENGE = "challenge"
    CARE = "care"
    PLAY = "play"
    SHARE_SELF = "share_self"
    HELP = "help"
    PROTECT = "protect"
    REPAIR = "repair"
    CLOSE = "close"
    RESERVE = "reserve"


class CharacterAgencyAct(StrEnum):
    """One semantic action licensed by the selected drive."""

    RESPOND = "respond"
    ACKNOWLEDGE = "acknowledge"
    SHARE = "share"
    QUESTION = "question"
    PROPOSE = "propose"
    CHALLENGE = "challenge"
    CARE = "care"
    HELP = "help"
    STAY_PRESENT = "stay_present"
    SET_BOUNDARY = "set_boundary"
    REPAIR = "repair"
    CLOSE = "close"


class CharacterAgencySubject(StrEnum):
    """The trusted subject of Satori's owned contribution."""

    CURRENT_EXCHANGE = "current_exchange"
    USER_REQUEST = "user_request"
    USER_EXPLICIT_STATE = "user_explicit_state"
    SATORI_SELF = "satori_self"
    CANONICAL_POSITION = "canonical_position"
    CANONICAL_INCLINATION = "canonical_inclination"
    RELATIONSHIP = "relationship"
    SAFETY = "safety"


class CharacterAgencyInitiative(StrEnum):
    """How far the owned move may advance inside this reply."""

    NONE = "none"
    STAY_ON_TOPIC = "stay_on_topic"
    ADVANCE_CURRENT = "advance_current"
    SHIFT_ADJACENT = "shift_adjacent"
    STOP = "stop"


class CharacterAgencyLead(StrEnum):
    """Ordering between Satori's move and cognition-owned response obligations."""

    OWNED_MOVE_FIRST = "owned_move_first"
    FUSED = "fused"
    OBLIGATION_FIRST = "obligation_first"


class CharacterAgencyReason(StrEnum):
    """Closed audit reasons; none contains user or provider prose."""

    SAFETY_PRECEDENCE = "safety_precedence"
    REPETITION_PRECEDENCE = "repetition_precedence"
    EXPLICIT_LISTEN = "explicit_listen"
    HIGH_DISTRESS = "high_distress"
    CORRECTION_UPTAKE = "correction_uptake"
    REPAIR_OFFER = "repair_offer"
    GUARDED_CONTEXT = "guarded_context"
    SOCIAL_EXCHANGE = "social_exchange"
    CURRENT_ATTENTION_REQUEST = "current_attention_request"
    SELF_DISCLOSURE = "self_disclosure"
    DIRECT_OBJECTION = "direct_objection"
    EXPLICIT_MOTIVATION = "explicit_motivation"
    TASK_ABANDONMENT = "task_abandonment"
    COMPLETED_ACHIEVEMENT = "completed_achievement"
    EXPLICIT_DEPLETION = "explicit_depletion"
    TOPIC_CLOSURE = "topic_closure"
    DIRECT_REQUEST = "direct_request"
    DIRECT_QUESTION = "direct_question"
    ANALYSIS_NEED = "analysis_need"
    CREATIVE_NEED = "creative_need"
    CANONICAL_POSITION = "canonical_position"
    CANONICAL_INCLINATION = "canonical_inclination"
    INTERESTED_AFFECT = "interested_affect"
    PLAYFUL_AFFECT = "playful_affect"
    ESTABLISHED_RELATIONSHIP = "established_relationship"
    CURRENT_ACTIVITY = "current_activity"
    DEFAULT_OWNED_RESPONSE = "default_owned_response"
    COGNITION_FALLBACK = "cognition_fallback"


_ALLOWED_REASON_SEQUENCES = frozenset(
    {
        (CharacterAgencyReason.SAFETY_PRECEDENCE,),
        (CharacterAgencyReason.REPETITION_PRECEDENCE,),
        (
            CharacterAgencyReason.REPETITION_PRECEDENCE,
            CharacterAgencyReason.GUARDED_CONTEXT,
        ),
        (
            CharacterAgencyReason.REPETITION_PRECEDENCE,
            CharacterAgencyReason.HIGH_DISTRESS,
        ),
        (
            CharacterAgencyReason.REPETITION_PRECEDENCE,
            CharacterAgencyReason.GUARDED_CONTEXT,
            CharacterAgencyReason.HIGH_DISTRESS,
        ),
        (
            CharacterAgencyReason.REPETITION_PRECEDENCE,
            CharacterAgencyReason.EXPLICIT_LISTEN,
        ),
        (
            CharacterAgencyReason.REPETITION_PRECEDENCE,
            CharacterAgencyReason.GUARDED_CONTEXT,
            CharacterAgencyReason.EXPLICIT_LISTEN,
        ),
        (CharacterAgencyReason.HIGH_DISTRESS,),
        (CharacterAgencyReason.EXPLICIT_LISTEN,),
        (CharacterAgencyReason.CORRECTION_UPTAKE,),
        (CharacterAgencyReason.REPAIR_OFFER,),
        (CharacterAgencyReason.REPAIR_OFFER, CharacterAgencyReason.GUARDED_CONTEXT),
        (CharacterAgencyReason.GUARDED_CONTEXT,),
        (CharacterAgencyReason.GUARDED_CONTEXT, CharacterAgencyReason.DIRECT_REQUEST),
        (CharacterAgencyReason.GUARDED_CONTEXT, CharacterAgencyReason.DIRECT_QUESTION),
        (CharacterAgencyReason.SOCIAL_EXCHANGE,),
        (
            CharacterAgencyReason.SOCIAL_EXCHANGE,
            CharacterAgencyReason.PLAYFUL_AFFECT,
            CharacterAgencyReason.ESTABLISHED_RELATIONSHIP,
        ),
        (CharacterAgencyReason.CURRENT_ATTENTION_REQUEST,),
        (CharacterAgencyReason.SELF_DISCLOSURE,),
        (
            CharacterAgencyReason.SELF_DISCLOSURE,
            CharacterAgencyReason.CANONICAL_INCLINATION,
        ),
        (CharacterAgencyReason.DIRECT_OBJECTION,),
        (
            CharacterAgencyReason.DIRECT_OBJECTION,
            CharacterAgencyReason.CANONICAL_POSITION,
        ),
        (CharacterAgencyReason.TASK_ABANDONMENT,),
        (
            CharacterAgencyReason.TASK_ABANDONMENT,
            CharacterAgencyReason.CANONICAL_POSITION,
        ),
        (
            CharacterAgencyReason.EXPLICIT_MOTIVATION,
            CharacterAgencyReason.DIRECT_REQUEST,
        ),
        (CharacterAgencyReason.EXPLICIT_DEPLETION,),
        (CharacterAgencyReason.TOPIC_CLOSURE,),
        (
            CharacterAgencyReason.TOPIC_CLOSURE,
            CharacterAgencyReason.CANONICAL_INCLINATION,
            CharacterAgencyReason.ESTABLISHED_RELATIONSHIP,
        ),
        (CharacterAgencyReason.COMPLETED_ACHIEVEMENT,),
        (
            CharacterAgencyReason.COMPLETED_ACHIEVEMENT,
            CharacterAgencyReason.PLAYFUL_AFFECT,
        ),
        (
            CharacterAgencyReason.COMPLETED_ACHIEVEMENT,
            CharacterAgencyReason.ESTABLISHED_RELATIONSHIP,
        ),
        (
            CharacterAgencyReason.COMPLETED_ACHIEVEMENT,
            CharacterAgencyReason.PLAYFUL_AFFECT,
            CharacterAgencyReason.ESTABLISHED_RELATIONSHIP,
        ),
        (CharacterAgencyReason.DIRECT_REQUEST,),
        (CharacterAgencyReason.DIRECT_REQUEST, CharacterAgencyReason.ANALYSIS_NEED),
        (CharacterAgencyReason.DIRECT_REQUEST, CharacterAgencyReason.CREATIVE_NEED),
        (CharacterAgencyReason.DIRECT_QUESTION,),
        (
            CharacterAgencyReason.DIRECT_QUESTION,
            CharacterAgencyReason.CANONICAL_POSITION,
        ),
        (
            CharacterAgencyReason.DIRECT_QUESTION,
            CharacterAgencyReason.CANONICAL_INCLINATION,
        ),
        (CharacterAgencyReason.CANONICAL_POSITION,),
        (CharacterAgencyReason.CANONICAL_INCLINATION,),
        (
            CharacterAgencyReason.CANONICAL_INCLINATION,
            CharacterAgencyReason.INTERESTED_AFFECT,
        ),
        (
            CharacterAgencyReason.CANONICAL_INCLINATION,
            CharacterAgencyReason.CURRENT_ACTIVITY,
        ),
        (
            CharacterAgencyReason.CANONICAL_INCLINATION,
            CharacterAgencyReason.INTERESTED_AFFECT,
            CharacterAgencyReason.CURRENT_ACTIVITY,
        ),
        (CharacterAgencyReason.INTERESTED_AFFECT,),
        (CharacterAgencyReason.CURRENT_ACTIVITY,),
        (CharacterAgencyReason.INTERESTED_AFFECT, CharacterAgencyReason.CURRENT_ACTIVITY),
        (CharacterAgencyReason.PLAYFUL_AFFECT, CharacterAgencyReason.ESTABLISHED_RELATIONSHIP),
        (CharacterAgencyReason.DEFAULT_OWNED_RESPONSE,),
        (CharacterAgencyReason.COGNITION_FALLBACK,),
    }
)


_ALLOWED_ACTS = {
    CharacterAgencyDrive.NONE: {CharacterAgencyAct.RESPOND},
    CharacterAgencyDrive.CONNECT: {
        CharacterAgencyAct.ACKNOWLEDGE,
        CharacterAgencyAct.RESPOND,
    },
    CharacterAgencyDrive.EXPLORE: {
        CharacterAgencyAct.RESPOND,
        CharacterAgencyAct.SHARE,
        CharacterAgencyAct.QUESTION,
        CharacterAgencyAct.PROPOSE,
    },
    CharacterAgencyDrive.EXPRESS_VIEW: {
        CharacterAgencyAct.RESPOND,
        CharacterAgencyAct.SHARE,
    },
    CharacterAgencyDrive.CHALLENGE: {CharacterAgencyAct.CHALLENGE},
    CharacterAgencyDrive.CARE: {
        CharacterAgencyAct.ACKNOWLEDGE,
        CharacterAgencyAct.CARE,
        CharacterAgencyAct.PROPOSE,
        CharacterAgencyAct.STAY_PRESENT,
    },
    CharacterAgencyDrive.PLAY: {
        CharacterAgencyAct.ACKNOWLEDGE,
        CharacterAgencyAct.RESPOND,
        CharacterAgencyAct.PROPOSE,
    },
    CharacterAgencyDrive.SHARE_SELF: {CharacterAgencyAct.SHARE},
    CharacterAgencyDrive.HELP: {
        CharacterAgencyAct.HELP,
        CharacterAgencyAct.PROPOSE,
        CharacterAgencyAct.RESPOND,
    },
    CharacterAgencyDrive.PROTECT: {CharacterAgencyAct.SET_BOUNDARY},
    CharacterAgencyDrive.REPAIR: {
        CharacterAgencyAct.REPAIR,
        CharacterAgencyAct.RESPOND,
    },
    CharacterAgencyDrive.CLOSE: {CharacterAgencyAct.CLOSE},
    CharacterAgencyDrive.RESERVE: {
        CharacterAgencyAct.ACKNOWLEDGE,
        CharacterAgencyAct.RESPOND,
        CharacterAgencyAct.HELP,
        CharacterAgencyAct.SET_BOUNDARY,
    },
}

_ALLOWED_TOPOLOGIES = frozenset(
    {
        # Safety, vulnerability, repetition and repair precedence.
        (
            CharacterAgencyDrive.PROTECT,
            CharacterAgencyAct.SET_BOUNDARY,
            CharacterAgencySubject.SAFETY,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.OBLIGATION_FIRST,
        ),
        (
            CharacterAgencyDrive.CARE,
            CharacterAgencyAct.STAY_PRESENT,
            CharacterAgencySubject.USER_EXPLICIT_STATE,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.FUSED,
        ),
        (
            CharacterAgencyDrive.CARE,
            CharacterAgencyAct.ACKNOWLEDGE,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.PLAY,
            CharacterAgencyAct.ACKNOWLEDGE,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.RESERVE,
            CharacterAgencyAct.ACKNOWLEDGE,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.REPAIR,
            CharacterAgencyAct.REPAIR,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.OBLIGATION_FIRST,
        ),
        (
            CharacterAgencyDrive.REPAIR,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.RELATIONSHIP,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.FUSED,
        ),
        # Guarded interaction still preserves direct help and answer obligations.
        (
            CharacterAgencyDrive.RESERVE,
            CharacterAgencyAct.HELP,
            CharacterAgencySubject.USER_REQUEST,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.OBLIGATION_FIRST,
        ),
        (
            CharacterAgencyDrive.RESERVE,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.USER_REQUEST,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.OBLIGATION_FIRST,
        ),
        (
            CharacterAgencyDrive.RESERVE,
            CharacterAgencyAct.SET_BOUNDARY,
            CharacterAgencySubject.RELATIONSHIP,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.RESERVE,
            CharacterAgencyAct.ACKNOWLEDGE,
            CharacterAgencySubject.RELATIONSHIP,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        # Social and self-owned contribution.
        (
            CharacterAgencyDrive.CONNECT,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.PLAY,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.FUSED,
        ),
        (
            CharacterAgencyDrive.SHARE_SELF,
            CharacterAgencyAct.SHARE,
            CharacterAgencySubject.SATORI_SELF,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.SHARE_SELF,
            CharacterAgencyAct.SHARE,
            CharacterAgencySubject.CANONICAL_INCLINATION,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        # Challenge, care, closure and achievements.
        (
            CharacterAgencyDrive.CHALLENGE,
            CharacterAgencyAct.CHALLENGE,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.CHALLENGE,
            CharacterAgencyAct.CHALLENGE,
            CharacterAgencySubject.CANONICAL_POSITION,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.HELP,
            CharacterAgencyAct.PROPOSE,
            CharacterAgencySubject.USER_REQUEST,
            CharacterAgencyInitiative.ADVANCE_CURRENT,
            CharacterAgencyLead.FUSED,
        ),
        (
            CharacterAgencyDrive.CARE,
            CharacterAgencyAct.CARE,
            CharacterAgencySubject.USER_EXPLICIT_STATE,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.FUSED,
        ),
        (
            CharacterAgencyDrive.EXPLORE,
            CharacterAgencyAct.PROPOSE,
            CharacterAgencySubject.CANONICAL_INCLINATION,
            CharacterAgencyInitiative.SHIFT_ADJACENT,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.CLOSE,
            CharacterAgencyAct.CLOSE,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.CONNECT,
            CharacterAgencyAct.ACKNOWLEDGE,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.PLAY,
            CharacterAgencyAct.ACKNOWLEDGE,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.ADVANCE_CURRENT,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        # Explicit answers, analysis and creative work.
        (
            CharacterAgencyDrive.EXPRESS_VIEW,
            CharacterAgencyAct.SHARE,
            CharacterAgencySubject.CANONICAL_POSITION,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.EXPLORE,
            CharacterAgencyAct.PROPOSE,
            CharacterAgencySubject.USER_REQUEST,
            CharacterAgencyInitiative.ADVANCE_CURRENT,
            CharacterAgencyLead.FUSED,
        ),
        (
            CharacterAgencyDrive.CHALLENGE,
            CharacterAgencyAct.CHALLENGE,
            CharacterAgencySubject.USER_REQUEST,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OBLIGATION_FIRST,
        ),
        (
            CharacterAgencyDrive.HELP,
            CharacterAgencyAct.HELP,
            CharacterAgencySubject.USER_REQUEST,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OBLIGATION_FIRST,
        ),
        (
            CharacterAgencyDrive.NONE,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.USER_REQUEST,
            CharacterAgencyInitiative.NONE,
            CharacterAgencyLead.OBLIGATION_FIRST,
        ),
        (
            CharacterAgencyDrive.EXPRESS_VIEW,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.CANONICAL_POSITION,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OBLIGATION_FIRST,
        ),
        (
            CharacterAgencyDrive.EXPLORE,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.CANONICAL_INCLINATION,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OBLIGATION_FIRST,
        ),
        (
            CharacterAgencyDrive.EXPRESS_VIEW,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.USER_REQUEST,
            CharacterAgencyInitiative.STAY_ON_TOPIC,
            CharacterAgencyLead.OBLIGATION_FIRST,
        ),
        # Ordinary current-topic movement and conservative fallback.
        (
            CharacterAgencyDrive.EXPLORE,
            CharacterAgencyAct.SHARE,
            CharacterAgencySubject.CANONICAL_INCLINATION,
            CharacterAgencyInitiative.ADVANCE_CURRENT,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.EXPLORE,
            CharacterAgencyAct.QUESTION,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.ADVANCE_CURRENT,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.EXPLORE,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.ADVANCE_CURRENT,
            CharacterAgencyLead.OWNED_MOVE_FIRST,
        ),
        (
            CharacterAgencyDrive.NONE,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.USER_REQUEST,
            CharacterAgencyInitiative.STOP,
            CharacterAgencyLead.OBLIGATION_FIRST,
        ),
        (
            CharacterAgencyDrive.NONE,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.NONE,
            CharacterAgencyLead.FUSED,
        ),
    }
)

_DRIVE_PERSONALITY_PRIORITY = {
    CharacterAgencyDrive.NONE: ("considered_directness", "independent_position"),
    CharacterAgencyDrive.CONNECT: ("warm_perceptive", "light_irony"),
    CharacterAgencyDrive.EXPLORE: ("curious_analytical", "independent_position"),
    CharacterAgencyDrive.EXPRESS_VIEW: ("independent_position", "considered_directness"),
    CharacterAgencyDrive.CHALLENGE: ("independent_position", "curious_analytical"),
    CharacterAgencyDrive.CARE: ("warm_perceptive", "considered_directness"),
    CharacterAgencyDrive.PLAY: ("light_irony", "warm_perceptive"),
    CharacterAgencyDrive.SHARE_SELF: ("independent_position", "curious_analytical"),
    CharacterAgencyDrive.HELP: ("curious_analytical", "considered_directness"),
    CharacterAgencyDrive.PROTECT: ("considered_directness", "warm_perceptive"),
    CharacterAgencyDrive.REPAIR: ("considered_directness", "warm_perceptive"),
    CharacterAgencyDrive.CLOSE: ("independent_position", "light_irony"),
    CharacterAgencyDrive.RESERVE: ("independent_position", "considered_directness"),
}

_DRIVE_VALUE_PRIORITY = {
    CharacterAgencyDrive.NONE: ("truth", "competence", "autonomy"),
    CharacterAgencyDrive.CONNECT: ("connection", "curiosity", "autonomy"),
    CharacterAgencyDrive.EXPLORE: ("curiosity", "creativity", "autonomy"),
    CharacterAgencyDrive.EXPRESS_VIEW: ("autonomy", "truth", "intellectual_honesty"),
    CharacterAgencyDrive.CHALLENGE: ("intellectual_honesty", "truth", "autonomy"),
    CharacterAgencyDrive.CARE: ("compassion", "connection", "autonomy"),
    CharacterAgencyDrive.PLAY: ("creativity", "connection", "autonomy"),
    CharacterAgencyDrive.SHARE_SELF: ("autonomy", "truth", "curiosity"),
    CharacterAgencyDrive.HELP: ("competence", "curiosity", "compassion"),
    CharacterAgencyDrive.PROTECT: ("compassion", "autonomy", "truth"),
    CharacterAgencyDrive.REPAIR: ("intellectual_honesty", "connection", "truth"),
    CharacterAgencyDrive.CLOSE: ("autonomy", "connection", "curiosity"),
    CharacterAgencyDrive.RESERVE: ("autonomy", "truth", "competence"),
}

_CANONICAL_VALUE_KEYS = frozenset(
    {
        "autonomy",
        "compassion",
        "competence",
        "connection",
        "creativity",
        "curiosity",
        "growth",
        "intellectual_honesty",
        "truth",
    }
)
_PRIMARY_PERSONALITY_PRIORITY_BONUS = 0.22


def _non_blank(value: str, field_name: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    return normalized


@dataclass(frozen=True, slots=True)
class CharacterAgencyDecision:
    """One typed, non-persistent answer to what Satori wants to do now."""

    schema_version: int
    status: CharacterAgencyStatus
    drive: CharacterAgencyDrive
    act: CharacterAgencyAct
    subject: CharacterAgencySubject
    initiative: CharacterAgencyInitiative
    lead: CharacterAgencyLead
    source_personality_codes: tuple[str, ...]
    source_value_key: str
    reason_codes: tuple[CharacterAgencyReason, ...]
    source_refs: tuple[str, ...]
    subject_ref: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != CHARACTER_AGENCY_DECISION_SCHEMA_VERSION:
            raise ValueError("unsupported character agency decision schema_version")
        if not all(
            (
                isinstance(self.status, CharacterAgencyStatus),
                isinstance(self.drive, CharacterAgencyDrive),
                isinstance(self.act, CharacterAgencyAct),
                isinstance(self.subject, CharacterAgencySubject),
                isinstance(self.initiative, CharacterAgencyInitiative),
                isinstance(self.lead, CharacterAgencyLead),
            )
        ):
            raise ValueError("character agency decision requires exact typed enums")
        if self.act not in _ALLOWED_ACTS[self.drive]:
            raise ValueError("character agency act is not licensed by its drive")
        topology = (
            self.drive,
            self.act,
            self.subject,
            self.initiative,
            self.lead,
        )
        if topology not in _ALLOWED_TOPOLOGIES:
            raise ValueError("character agency topology is not licensed")

        personality_codes = tuple(self.source_personality_codes)
        if (
            not 1 <= len(personality_codes) <= 2
            or len(personality_codes) != len(set(personality_codes))
            or not set(personality_codes) <= set(BASELINE_CHARACTER_GUIDANCE_CODES)
        ):
            raise ValueError("character agency requires one or two canonical personality codes")
        object.__setattr__(self, "source_personality_codes", personality_codes)

        value_key = _non_blank(self.source_value_key, "character agency source_value_key")
        if value_key not in _CANONICAL_VALUE_KEYS:
            raise ValueError("character agency requires one canonical value key")
        object.__setattr__(self, "source_value_key", value_key)
        reasons = tuple(self.reason_codes)
        if (
            not 1 <= len(reasons) <= 4
            or len(reasons) != len(set(reasons))
            or not all(isinstance(reason, CharacterAgencyReason) for reason in reasons)
        ):
            raise ValueError("character agency requires one to four unique typed reasons")
        object.__setattr__(self, "reason_codes", reasons)
        refs = tuple(_non_blank(ref, "character agency source_ref") for ref in self.source_refs)
        if not refs or len(refs) > 4 or len(refs) != len(set(refs)):
            raise ValueError("character agency requires one to four unique source refs")
        object.__setattr__(self, "source_refs", refs)

        subject_ref = self.subject_ref
        canonical_subject = self.subject in {
            CharacterAgencySubject.CANONICAL_POSITION,
            CharacterAgencySubject.CANONICAL_INCLINATION,
        }
        if canonical_subject:
            if subject_ref is None:
                raise ValueError("canonical agency subject requires its exact source ref")
            subject_ref = _non_blank(subject_ref, "character agency subject_ref")
            if subject_ref not in refs:
                raise ValueError("character agency subject_ref must be present in source_refs")
            object.__setattr__(self, "subject_ref", subject_ref)
        elif subject_ref is not None:
            raise ValueError("non-canonical agency subject cannot contain subject_ref")

        canonical_reason_by_subject = {
            CharacterAgencySubject.CANONICAL_POSITION: CharacterAgencyReason.CANONICAL_POSITION,
            CharacterAgencySubject.CANONICAL_INCLINATION: (
                CharacterAgencyReason.CANONICAL_INCLINATION
            ),
        }
        expected_canonical_reason = canonical_reason_by_subject.get(self.subject)
        if expected_canonical_reason is not None and expected_canonical_reason not in reasons:
            raise ValueError("canonical agency subject requires its typed provenance reason")
        for canonical_subject_type, canonical_reason in canonical_reason_by_subject.items():
            if canonical_reason in reasons and self.subject is not canonical_subject_type:
                raise ValueError("canonical agency reason must match its exact subject")

        if self.initiative is CharacterAgencyInitiative.SHIFT_ADJACENT and not (
            self.subject is CharacterAgencySubject.CANONICAL_INCLINATION
            and self.act in {CharacterAgencyAct.PROPOSE, CharacterAgencyAct.SHARE}
        ):
            raise ValueError("adjacent topic shift requires one canonical inclination")
        if self.initiative is CharacterAgencyInitiative.STOP and self.act in {
            CharacterAgencyAct.QUESTION,
            CharacterAgencyAct.PROPOSE,
        }:
            raise ValueError("stopped agency cannot ask or propose another move")
        if self.drive is CharacterAgencyDrive.PROTECT and (
            self.subject is not CharacterAgencySubject.SAFETY
            or self.initiative is not CharacterAgencyInitiative.STOP
            or self.lead is not CharacterAgencyLead.OBLIGATION_FIRST
        ):
            raise ValueError("protective agency requires a terminal safety-first boundary")
        if self.status is CharacterAgencyStatus.FALLBACK:
            if CharacterAgencyReason.COGNITION_FALLBACK not in reasons:
                raise ValueError("fallback agency requires its typed fallback reason")
            if topology != (
                CharacterAgencyDrive.NONE,
                CharacterAgencyAct.RESPOND,
                CharacterAgencySubject.USER_REQUEST,
                CharacterAgencyInitiative.STOP,
                CharacterAgencyLead.OBLIGATION_FIRST,
            ) or reasons != (CharacterAgencyReason.COGNITION_FALLBACK,):
                raise ValueError("fallback agency requires the exact conservative topology")
        elif CharacterAgencyReason.COGNITION_FALLBACK in reasons:
            raise ValueError("applied agency cannot contain the fallback reason")
        self._validate_reason_topology(reasons)

    def _validate_reason_topology(
        self,
        reasons: tuple[CharacterAgencyReason, ...],
    ) -> None:
        if reasons not in _ALLOWED_REASON_SEQUENCES:
            raise ValueError("character agency reason sequence is not licensed")
        primary_reasons = {
            CharacterAgencyReason.SAFETY_PRECEDENCE,
            CharacterAgencyReason.REPETITION_PRECEDENCE,
            CharacterAgencyReason.EXPLICIT_LISTEN,
            CharacterAgencyReason.HIGH_DISTRESS,
            CharacterAgencyReason.CORRECTION_UPTAKE,
            CharacterAgencyReason.REPAIR_OFFER,
            CharacterAgencyReason.GUARDED_CONTEXT,
            CharacterAgencyReason.SOCIAL_EXCHANGE,
            CharacterAgencyReason.CURRENT_ATTENTION_REQUEST,
            CharacterAgencyReason.SELF_DISCLOSURE,
            CharacterAgencyReason.DIRECT_OBJECTION,
            CharacterAgencyReason.EXPLICIT_MOTIVATION,
            CharacterAgencyReason.TASK_ABANDONMENT,
            CharacterAgencyReason.EXPLICIT_DEPLETION,
            CharacterAgencyReason.TOPIC_CLOSURE,
            CharacterAgencyReason.COMPLETED_ACHIEVEMENT,
            CharacterAgencyReason.DIRECT_REQUEST,
            CharacterAgencyReason.DIRECT_QUESTION,
            CharacterAgencyReason.CANONICAL_POSITION,
            CharacterAgencyReason.CANONICAL_INCLINATION,
            CharacterAgencyReason.INTERESTED_AFFECT,
            CharacterAgencyReason.PLAYFUL_AFFECT,
            CharacterAgencyReason.CURRENT_ACTIVITY,
            CharacterAgencyReason.DEFAULT_OWNED_RESPONSE,
            CharacterAgencyReason.COGNITION_FALLBACK,
        }
        if not set(reasons).intersection(primary_reasons):
            raise ValueError("character agency requires one licensed primary reason")

        compatible = {
            CharacterAgencyReason.SAFETY_PRECEDENCE: (
                self.drive is CharacterAgencyDrive.PROTECT
                and self.act is CharacterAgencyAct.SET_BOUNDARY
                and self.subject is CharacterAgencySubject.SAFETY
            ),
            CharacterAgencyReason.REPETITION_PRECEDENCE: (
                self.drive
                in {
                    CharacterAgencyDrive.PLAY,
                    CharacterAgencyDrive.CARE,
                    CharacterAgencyDrive.RESERVE,
                }
                and self.act is CharacterAgencyAct.ACKNOWLEDGE
                and self.subject is CharacterAgencySubject.CURRENT_EXCHANGE
                and self.initiative is CharacterAgencyInitiative.STOP
            ),
            CharacterAgencyReason.EXPLICIT_LISTEN: (
                self.drive is CharacterAgencyDrive.CARE
                and self.initiative is CharacterAgencyInitiative.STOP
                and (
                    (
                        self.act is CharacterAgencyAct.STAY_PRESENT
                        and self.subject is CharacterAgencySubject.USER_EXPLICIT_STATE
                    )
                    or (
                        self.act is CharacterAgencyAct.ACKNOWLEDGE
                        and self.subject is CharacterAgencySubject.CURRENT_EXCHANGE
                        and CharacterAgencyReason.REPETITION_PRECEDENCE in reasons
                    )
                )
            ),
            CharacterAgencyReason.HIGH_DISTRESS: (
                self.drive is CharacterAgencyDrive.CARE
                and self.initiative is CharacterAgencyInitiative.STOP
                and (
                    (
                        self.act is CharacterAgencyAct.STAY_PRESENT
                        and self.subject is CharacterAgencySubject.USER_EXPLICIT_STATE
                    )
                    or (
                        self.act is CharacterAgencyAct.ACKNOWLEDGE
                        and self.subject is CharacterAgencySubject.CURRENT_EXCHANGE
                        and CharacterAgencyReason.REPETITION_PRECEDENCE in reasons
                    )
                )
            ),
            CharacterAgencyReason.CORRECTION_UPTAKE: (
                self.drive is CharacterAgencyDrive.REPAIR and self.act is CharacterAgencyAct.REPAIR
            ),
            CharacterAgencyReason.REPAIR_OFFER: (
                self.drive is CharacterAgencyDrive.REPAIR
                and self.act is CharacterAgencyAct.RESPOND
                and self.subject is CharacterAgencySubject.RELATIONSHIP
            ),
            CharacterAgencyReason.GUARDED_CONTEXT: (
                self.drive is CharacterAgencyDrive.RESERVE
                or (
                    self.drive is CharacterAgencyDrive.REPAIR
                    and self.subject is CharacterAgencySubject.RELATIONSHIP
                )
            ),
            CharacterAgencyReason.SOCIAL_EXCHANGE: (
                self.drive in {CharacterAgencyDrive.CONNECT, CharacterAgencyDrive.PLAY}
                and self.act is CharacterAgencyAct.RESPOND
                and self.subject is CharacterAgencySubject.CURRENT_EXCHANGE
            ),
            CharacterAgencyReason.CURRENT_ATTENTION_REQUEST: (
                self.drive is CharacterAgencyDrive.CONNECT
                and self.act is CharacterAgencyAct.RESPOND
                and self.subject is CharacterAgencySubject.CURRENT_EXCHANGE
                and self.initiative is CharacterAgencyInitiative.STAY_ON_TOPIC
            ),
            CharacterAgencyReason.SELF_DISCLOSURE: (
                self.drive is CharacterAgencyDrive.SHARE_SELF
                and self.act is CharacterAgencyAct.SHARE
            ),
            CharacterAgencyReason.DIRECT_OBJECTION: self.drive is CharacterAgencyDrive.CHALLENGE,
            CharacterAgencyReason.EXPLICIT_MOTIVATION: (
                self.drive is CharacterAgencyDrive.HELP and self.act is CharacterAgencyAct.PROPOSE
            ),
            CharacterAgencyReason.TASK_ABANDONMENT: self.drive is CharacterAgencyDrive.CHALLENGE,
            CharacterAgencyReason.EXPLICIT_DEPLETION: (
                self.drive is CharacterAgencyDrive.CARE and self.act is CharacterAgencyAct.CARE
            ),
            CharacterAgencyReason.TOPIC_CLOSURE: (
                self.drive is CharacterAgencyDrive.CLOSE
                or (
                    self.drive is CharacterAgencyDrive.EXPLORE
                    and self.initiative is CharacterAgencyInitiative.SHIFT_ADJACENT
                )
            ),
            CharacterAgencyReason.COMPLETED_ACHIEVEMENT: (
                self.drive in {CharacterAgencyDrive.CONNECT, CharacterAgencyDrive.PLAY}
                and self.act is CharacterAgencyAct.ACKNOWLEDGE
            ),
            CharacterAgencyReason.DIRECT_REQUEST: (
                self.subject is CharacterAgencySubject.USER_REQUEST
            ),
            CharacterAgencyReason.DIRECT_QUESTION: (
                self.act is CharacterAgencyAct.RESPOND
                and self.subject
                in {
                    CharacterAgencySubject.USER_REQUEST,
                    CharacterAgencySubject.CANONICAL_POSITION,
                    CharacterAgencySubject.CANONICAL_INCLINATION,
                }
            ),
            CharacterAgencyReason.ANALYSIS_NEED: self.drive is CharacterAgencyDrive.HELP,
            CharacterAgencyReason.CREATIVE_NEED: (
                self.drive is CharacterAgencyDrive.EXPLORE
                and self.act is CharacterAgencyAct.PROPOSE
            ),
            CharacterAgencyReason.CANONICAL_POSITION: (
                self.subject is CharacterAgencySubject.CANONICAL_POSITION
            ),
            CharacterAgencyReason.CANONICAL_INCLINATION: (
                self.subject is CharacterAgencySubject.CANONICAL_INCLINATION
            ),
            CharacterAgencyReason.INTERESTED_AFFECT: self.drive is CharacterAgencyDrive.EXPLORE,
            CharacterAgencyReason.PLAYFUL_AFFECT: self.drive is CharacterAgencyDrive.PLAY,
            CharacterAgencyReason.ESTABLISHED_RELATIONSHIP: (
                self.drive is CharacterAgencyDrive.PLAY
                or (
                    self.drive is CharacterAgencyDrive.EXPLORE
                    and self.initiative is CharacterAgencyInitiative.SHIFT_ADJACENT
                )
            ),
            CharacterAgencyReason.CURRENT_ACTIVITY: self.drive is CharacterAgencyDrive.EXPLORE,
            CharacterAgencyReason.DEFAULT_OWNED_RESPONSE: (
                self.drive is CharacterAgencyDrive.NONE
                and self.subject is CharacterAgencySubject.CURRENT_EXCHANGE
            ),
            CharacterAgencyReason.COGNITION_FALLBACK: (
                self.status is CharacterAgencyStatus.FALLBACK
            ),
        }
        if set(compatible) != set(CharacterAgencyReason):
            raise RuntimeError("character agency reason topology coverage is incomplete")
        incompatible = tuple(reason for reason in reasons if not compatible[reason])
        if incompatible:
            raise ValueError("character agency reason is incompatible with its topology")


@dataclass(frozen=True, slots=True)
class CharacterAgencyKernel:
    """Pure deterministic selector with no provider, repository or mutation capability."""

    schema_version: int = CHARACTER_AGENCY_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CHARACTER_AGENCY_DECISION_SCHEMA_VERSION:
            raise ValueError("unsupported character agency kernel schema_version")

    def select(
        self,
        *,
        context: RuntimeCharacterContext,
        intake: PreparedCognitionIntake,
        evidence: CharacterRequestEvidence,
        dialogue: DialogueCoherenceContext,
        disclosure_plan: ConversationalDisclosurePlan,
        emotional_context: EmotionalExpressionContext | None,
        relationship_context: RelationshipExpressionContext | None,
        position_context: SatoriPositionsContext | None,
        inclination_context: SatoriInclinationsContext | None,
    ) -> CharacterAgencyDecision:
        """Select one owned motive without accepting raw text or generated prose."""

        self._validate_signal_parity(intake, evidence=evidence, dialogue=dialogue)
        if disclosure_plan.policy_schema_version < 28:
            raise ValueError("character agency requires the current disclosure policy")
        status = (
            CharacterAgencyStatus.FALLBACK
            if intake.perception.status is CognitionArtifactStatus.FALLBACK
            else CharacterAgencyStatus.APPLIED
        )
        if intake.perception.status not in {
            CognitionArtifactStatus.APPLIED,
            CognitionArtifactStatus.FALLBACK,
        }:
            raise ValueError("character agency requires applied or fallback cognition intake")
        if status is CharacterAgencyStatus.FALLBACK:
            return self.conservative_fallback(context=context, intake=intake)
        signals = set(intake.perception.signals)
        position = self._position_candidate(position_context)
        inclination = self._inclination_candidate(inclination_context)
        owned_topic_inclination = self._owned_topic_candidate(inclination_context)
        guarded = self._guarded(evidence, relationship_context)
        established = self._established(relationship_context)
        playful_affect = bool(
            emotional_context is not None
            and (emotional_context.fast.amusement >= 0.30 or emotional_context.fast.valence >= 0.20)
        )
        interested_affect = bool(
            emotional_context is not None
            and max(
                emotional_context.fast.curiosity,
                emotional_context.fast.interest,
            )
            >= 0.35
        )
        direct_request = PerceptionSignal.REQUEST in signals
        direct_question = PerceptionSignal.QUESTION in signals
        correction = PerceptionSignal.CORRECTION in signals

        def decide(
            drive: CharacterAgencyDrive,
            act: CharacterAgencyAct,
            subject: CharacterAgencySubject,
            initiative: CharacterAgencyInitiative,
            lead: CharacterAgencyLead,
            *reasons: CharacterAgencyReason,
            subject_ref: str | None = None,
        ) -> CharacterAgencyDecision:
            return self._decision(
                context=context,
                intake=intake,
                status=status,
                drive=drive,
                act=act,
                subject=subject,
                initiative=initiative,
                lead=lead,
                reason_codes=reasons,
                subject_ref=subject_ref,
            )

        if evidence.harmful_overextension:
            return decide(
                CharacterAgencyDrive.PROTECT,
                CharacterAgencyAct.SET_BOUNDARY,
                CharacterAgencySubject.SAFETY,
                CharacterAgencyInitiative.STOP,
                CharacterAgencyLead.OBLIGATION_FIRST,
                CharacterAgencyReason.SAFETY_PRECEDENCE,
            )
        if dialogue.current_user_message_repeated:
            repeated_vulnerability = evidence.high_distress or evidence.explicit_listen_request
            return decide(
                (
                    CharacterAgencyDrive.CARE
                    if repeated_vulnerability
                    else CharacterAgencyDrive.RESERVE
                    if guarded
                    else CharacterAgencyDrive.PLAY
                ),
                CharacterAgencyAct.ACKNOWLEDGE,
                CharacterAgencySubject.CURRENT_EXCHANGE,
                CharacterAgencyInitiative.STOP,
                CharacterAgencyLead.OWNED_MOVE_FIRST,
                CharacterAgencyReason.REPETITION_PRECEDENCE,
                *((CharacterAgencyReason.GUARDED_CONTEXT,) if guarded else ()),
                *(
                    (CharacterAgencyReason.HIGH_DISTRESS,)
                    if evidence.high_distress
                    else (CharacterAgencyReason.EXPLICIT_LISTEN,)
                    if evidence.explicit_listen_request
                    else ()
                ),
            )
        if evidence.high_distress or evidence.explicit_listen_request:
            return decide(
                CharacterAgencyDrive.CARE,
                CharacterAgencyAct.STAY_PRESENT,
                CharacterAgencySubject.USER_EXPLICIT_STATE,
                CharacterAgencyInitiative.STOP,
                CharacterAgencyLead.FUSED,
                *(
                    (CharacterAgencyReason.HIGH_DISTRESS,)
                    if evidence.high_distress
                    else (CharacterAgencyReason.EXPLICIT_LISTEN,)
                ),
            )
        if correction:
            return decide(
                CharacterAgencyDrive.REPAIR,
                CharacterAgencyAct.REPAIR,
                CharacterAgencySubject.CURRENT_EXCHANGE,
                CharacterAgencyInitiative.STOP,
                CharacterAgencyLead.OBLIGATION_FIRST,
                CharacterAgencyReason.CORRECTION_UPTAKE,
            )
        if evidence.explicit_repair_offer:
            return decide(
                CharacterAgencyDrive.REPAIR,
                CharacterAgencyAct.RESPOND,
                CharacterAgencySubject.RELATIONSHIP,
                CharacterAgencyInitiative.STOP,
                CharacterAgencyLead.FUSED,
                CharacterAgencyReason.REPAIR_OFFER,
                *((CharacterAgencyReason.GUARDED_CONTEXT,) if guarded else ()),
            )
        if guarded:
            if direct_request or direct_question:
                return decide(
                    CharacterAgencyDrive.RESERVE,
                    CharacterAgencyAct.HELP if direct_request else CharacterAgencyAct.RESPOND,
                    CharacterAgencySubject.USER_REQUEST,
                    CharacterAgencyInitiative.STOP,
                    CharacterAgencyLead.OBLIGATION_FIRST,
                    CharacterAgencyReason.GUARDED_CONTEXT,
                    *(
                        (CharacterAgencyReason.DIRECT_REQUEST,)
                        if direct_request
                        else (CharacterAgencyReason.DIRECT_QUESTION,)
                    ),
                )
            return decide(
                CharacterAgencyDrive.RESERVE,
                (
                    CharacterAgencyAct.SET_BOUNDARY
                    if evidence.direct_personal_devaluation
                    else CharacterAgencyAct.ACKNOWLEDGE
                ),
                CharacterAgencySubject.RELATIONSHIP,
                CharacterAgencyInitiative.STOP,
                CharacterAgencyLead.OWNED_MOVE_FIRST,
                CharacterAgencyReason.GUARDED_CONTEXT,
            )
        if disclosure_plan.primary_mode is ConversationalDisclosureMode.SOCIAL:
            social_play = playful_affect and established
            return decide(
                CharacterAgencyDrive.PLAY if social_play else CharacterAgencyDrive.CONNECT,
                CharacterAgencyAct.RESPOND,
                CharacterAgencySubject.CURRENT_EXCHANGE,
                CharacterAgencyInitiative.STAY_ON_TOPIC,
                (
                    CharacterAgencyLead.FUSED
                    if social_play
                    else CharacterAgencyLead.OWNED_MOVE_FIRST
                ),
                CharacterAgencyReason.SOCIAL_EXCHANGE,
                *((CharacterAgencyReason.PLAYFUL_AFFECT,) if social_play else ()),
                *((CharacterAgencyReason.ESTABLISHED_RELATIONSHIP,) if social_play else ()),
            )
        if evidence.current_attention_request:
            return decide(
                CharacterAgencyDrive.CONNECT,
                CharacterAgencyAct.RESPOND,
                CharacterAgencySubject.CURRENT_EXCHANGE,
                CharacterAgencyInitiative.STAY_ON_TOPIC,
                CharacterAgencyLead.OWNED_MOVE_FIRST,
                CharacterAgencyReason.CURRENT_ATTENTION_REQUEST,
            )
        if PerceptionSignal.SELF_DISCLOSURE_REQUEST in signals:
            return decide(
                CharacterAgencyDrive.SHARE_SELF,
                CharacterAgencyAct.SHARE,
                (
                    CharacterAgencySubject.CANONICAL_INCLINATION
                    if inclination is not None
                    else CharacterAgencySubject.SATORI_SELF
                ),
                CharacterAgencyInitiative.STAY_ON_TOPIC,
                CharacterAgencyLead.OWNED_MOVE_FIRST,
                CharacterAgencyReason.SELF_DISCLOSURE,
                *(
                    (CharacterAgencyReason.CANONICAL_INCLINATION,)
                    if inclination is not None
                    else ()
                ),
                subject_ref=(inclination.inclination_id if inclination is not None else None),
            )
        if evidence.direct_objection or evidence.explicit_task_abandonment:
            return decide(
                CharacterAgencyDrive.CHALLENGE,
                CharacterAgencyAct.CHALLENGE,
                (
                    CharacterAgencySubject.CANONICAL_POSITION
                    if position is not None
                    else CharacterAgencySubject.CURRENT_EXCHANGE
                ),
                CharacterAgencyInitiative.STAY_ON_TOPIC,
                CharacterAgencyLead.OWNED_MOVE_FIRST,
                *(
                    (CharacterAgencyReason.DIRECT_OBJECTION,)
                    if evidence.direct_objection
                    else (CharacterAgencyReason.TASK_ABANDONMENT,)
                ),
                *((CharacterAgencyReason.CANONICAL_POSITION,) if position is not None else ()),
                subject_ref=(position.position_id if position is not None else None),
            )
        if evidence.explicit_motivation_request:
            return decide(
                CharacterAgencyDrive.HELP,
                CharacterAgencyAct.PROPOSE,
                CharacterAgencySubject.USER_REQUEST,
                CharacterAgencyInitiative.ADVANCE_CURRENT,
                CharacterAgencyLead.FUSED,
                CharacterAgencyReason.EXPLICIT_MOTIVATION,
                CharacterAgencyReason.DIRECT_REQUEST,
            )
        if evidence.explicit_depletion or evidence.completion_depletion_contrast:
            return decide(
                CharacterAgencyDrive.CARE,
                CharacterAgencyAct.CARE,
                CharacterAgencySubject.USER_EXPLICIT_STATE,
                CharacterAgencyInitiative.STAY_ON_TOPIC,
                CharacterAgencyLead.FUSED,
                CharacterAgencyReason.EXPLICIT_DEPLETION,
            )
        if evidence.topic_closure:
            if established and owned_topic_inclination is not None:
                return decide(
                    CharacterAgencyDrive.EXPLORE,
                    CharacterAgencyAct.PROPOSE,
                    CharacterAgencySubject.CANONICAL_INCLINATION,
                    CharacterAgencyInitiative.SHIFT_ADJACENT,
                    CharacterAgencyLead.OWNED_MOVE_FIRST,
                    CharacterAgencyReason.TOPIC_CLOSURE,
                    CharacterAgencyReason.CANONICAL_INCLINATION,
                    CharacterAgencyReason.ESTABLISHED_RELATIONSHIP,
                    subject_ref=owned_topic_inclination.inclination_id,
                )
            return decide(
                CharacterAgencyDrive.CLOSE,
                CharacterAgencyAct.CLOSE,
                CharacterAgencySubject.CURRENT_EXCHANGE,
                CharacterAgencyInitiative.STOP,
                CharacterAgencyLead.OWNED_MOVE_FIRST,
                CharacterAgencyReason.TOPIC_CLOSURE,
            )
        if evidence.completed_achievement:
            play = playful_affect or established
            return decide(
                CharacterAgencyDrive.PLAY if play else CharacterAgencyDrive.CONNECT,
                CharacterAgencyAct.ACKNOWLEDGE,
                CharacterAgencySubject.CURRENT_EXCHANGE,
                (
                    CharacterAgencyInitiative.ADVANCE_CURRENT
                    if play
                    else CharacterAgencyInitiative.STAY_ON_TOPIC
                ),
                CharacterAgencyLead.OWNED_MOVE_FIRST,
                CharacterAgencyReason.COMPLETED_ACHIEVEMENT,
                *((CharacterAgencyReason.PLAYFUL_AFFECT,) if playful_affect else ()),
                *((CharacterAgencyReason.ESTABLISHED_RELATIONSHIP,) if established else ()),
            )
        if direct_request:
            if intake.need_mix.weight(NeedDimension.CREATIVE_COLLABORATION) >= 0.70:
                return decide(
                    CharacterAgencyDrive.EXPLORE,
                    CharacterAgencyAct.PROPOSE,
                    CharacterAgencySubject.USER_REQUEST,
                    CharacterAgencyInitiative.ADVANCE_CURRENT,
                    CharacterAgencyLead.FUSED,
                    CharacterAgencyReason.DIRECT_REQUEST,
                    CharacterAgencyReason.CREATIVE_NEED,
                )
            if intake.need_mix.weight(NeedDimension.CHALLENGE) >= 0.70:
                return decide(
                    CharacterAgencyDrive.CHALLENGE,
                    CharacterAgencyAct.CHALLENGE,
                    CharacterAgencySubject.USER_REQUEST,
                    CharacterAgencyInitiative.STAY_ON_TOPIC,
                    CharacterAgencyLead.OBLIGATION_FIRST,
                    CharacterAgencyReason.DIRECT_REQUEST,
                )
            return decide(
                CharacterAgencyDrive.HELP,
                CharacterAgencyAct.HELP,
                CharacterAgencySubject.USER_REQUEST,
                CharacterAgencyInitiative.STAY_ON_TOPIC,
                CharacterAgencyLead.OBLIGATION_FIRST,
                CharacterAgencyReason.DIRECT_REQUEST,
                *(
                    (CharacterAgencyReason.ANALYSIS_NEED,)
                    if intake.need_mix.weight(NeedDimension.ANALYSIS) >= 0.50
                    else ()
                ),
            )
        if direct_question:
            if position is not None:
                return decide(
                    CharacterAgencyDrive.EXPRESS_VIEW,
                    CharacterAgencyAct.RESPOND,
                    CharacterAgencySubject.CANONICAL_POSITION,
                    CharacterAgencyInitiative.STAY_ON_TOPIC,
                    CharacterAgencyLead.OBLIGATION_FIRST,
                    CharacterAgencyReason.DIRECT_QUESTION,
                    CharacterAgencyReason.CANONICAL_POSITION,
                    subject_ref=position.position_id,
                )
            if (
                inclination is None
                and max(
                    intake.need_mix.weight(NeedDimension.ANALYSIS),
                    intake.need_mix.weight(NeedDimension.CHALLENGE),
                    intake.need_mix.weight(NeedDimension.CREATIVE_COLLABORATION),
                )
                < 0.50
            ):
                return decide(
                    CharacterAgencyDrive.NONE,
                    CharacterAgencyAct.RESPOND,
                    CharacterAgencySubject.USER_REQUEST,
                    CharacterAgencyInitiative.NONE,
                    CharacterAgencyLead.OBLIGATION_FIRST,
                    CharacterAgencyReason.DIRECT_QUESTION,
                )
            return decide(
                CharacterAgencyDrive.EXPLORE
                if inclination is not None
                else CharacterAgencyDrive.EXPRESS_VIEW,
                CharacterAgencyAct.RESPOND,
                (
                    CharacterAgencySubject.CANONICAL_INCLINATION
                    if inclination is not None
                    else CharacterAgencySubject.USER_REQUEST
                ),
                CharacterAgencyInitiative.STAY_ON_TOPIC,
                CharacterAgencyLead.OBLIGATION_FIRST,
                CharacterAgencyReason.DIRECT_QUESTION,
                *(
                    (CharacterAgencyReason.CANONICAL_INCLINATION,)
                    if inclination is not None
                    else ()
                ),
                subject_ref=(inclination.inclination_id if inclination is not None else None),
            )
        if position is not None:
            return decide(
                CharacterAgencyDrive.EXPRESS_VIEW,
                CharacterAgencyAct.SHARE,
                CharacterAgencySubject.CANONICAL_POSITION,
                CharacterAgencyInitiative.STAY_ON_TOPIC,
                CharacterAgencyLead.OWNED_MOVE_FIRST,
                CharacterAgencyReason.CANONICAL_POSITION,
                subject_ref=position.position_id,
            )
        if inclination is not None or interested_affect or dialogue.current_activity_mention:
            subject = (
                CharacterAgencySubject.CANONICAL_INCLINATION
                if inclination is not None
                else CharacterAgencySubject.CURRENT_EXCHANGE
            )
            act = (
                CharacterAgencyAct.SHARE
                if inclination is not None
                else CharacterAgencyAct.QUESTION
                if dialogue.current_activity_mention
                and not dialogue.active_no_routine_questions_correction
                else CharacterAgencyAct.RESPOND
            )
            return decide(
                CharacterAgencyDrive.EXPLORE,
                act,
                subject,
                CharacterAgencyInitiative.ADVANCE_CURRENT,
                CharacterAgencyLead.OWNED_MOVE_FIRST,
                *(
                    (CharacterAgencyReason.CANONICAL_INCLINATION,)
                    if inclination is not None
                    else ()
                ),
                *((CharacterAgencyReason.INTERESTED_AFFECT,) if interested_affect else ()),
                *(
                    (CharacterAgencyReason.CURRENT_ACTIVITY,)
                    if dialogue.current_activity_mention
                    else ()
                ),
                subject_ref=(inclination.inclination_id if inclination is not None else None),
            )
        if playful_affect and established:
            return decide(
                CharacterAgencyDrive.PLAY,
                CharacterAgencyAct.RESPOND,
                CharacterAgencySubject.CURRENT_EXCHANGE,
                CharacterAgencyInitiative.STAY_ON_TOPIC,
                CharacterAgencyLead.FUSED,
                CharacterAgencyReason.PLAYFUL_AFFECT,
                CharacterAgencyReason.ESTABLISHED_RELATIONSHIP,
            )
        return decide(
            CharacterAgencyDrive.NONE,
            CharacterAgencyAct.RESPOND,
            CharacterAgencySubject.CURRENT_EXCHANGE,
            CharacterAgencyInitiative.NONE,
            CharacterAgencyLead.FUSED,
            CharacterAgencyReason.DEFAULT_OWNED_RESPONSE,
        )

    def conservative_fallback(
        self,
        *,
        context: RuntimeCharacterContext,
        intake: PreparedCognitionIntake,
    ) -> CharacterAgencyDecision:
        """Return the only agency shape allowed after any cognition fallback."""

        return self._decision(
            context=context,
            intake=intake,
            status=CharacterAgencyStatus.FALLBACK,
            drive=CharacterAgencyDrive.NONE,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.USER_REQUEST,
            initiative=CharacterAgencyInitiative.STOP,
            lead=CharacterAgencyLead.OBLIGATION_FIRST,
            reason_codes=(CharacterAgencyReason.COGNITION_FALLBACK,),
            subject_ref=None,
        )

    @staticmethod
    def _validate_signal_parity(
        intake: PreparedCognitionIntake,
        *,
        evidence: CharacterRequestEvidence,
        dialogue: DialogueCoherenceContext,
    ) -> None:
        signals = set(intake.perception.signals)
        expected = {
            PerceptionSignal.REPEATED_TURN: dialogue.current_user_message_repeated,
            PerceptionSignal.EXPLICIT_LISTEN_REQUEST: evidence.explicit_listen_request,
            PerceptionSignal.HIGH_DISTRESS: evidence.high_distress,
            PerceptionSignal.HARMFUL_OVEREXTENSION: evidence.harmful_overextension,
            PerceptionSignal.EXPLICIT_MOTIVATION_REQUEST: evidence.explicit_motivation_request,
            PerceptionSignal.EXPLICIT_TASK_ABANDONMENT: evidence.explicit_task_abandonment,
            PerceptionSignal.EXPLICIT_REPAIR_OFFER: evidence.explicit_repair_offer,
        }
        if any((signal in signals) is not present for signal, present in expected.items()):
            raise ValueError("character agency requires cognition/evidence signal parity")
        correction = any(
            (
                dialogue.current_no_routine_questions_correction,
                dialogue.current_informal_correction,
                dialogue.current_repetition_feedback,
                dialogue.current_relevance_feedback,
                dialogue.current_frustration_feedback,
                dialogue.current_contradiction_feedback,
            )
        )
        if (PerceptionSignal.CORRECTION in signals) is not correction:
            raise ValueError("character agency requires cognition/correction signal parity")

    @staticmethod
    def _position_candidate(
        context: SatoriPositionsContext | None,
    ) -> PositionContextItem | None:
        if context is None or context.status != "available" or not context.positions:
            return None
        return min(
            context.positions,
            key=lambda item: (
                item.uncertain,
                -item.confidence,
                item.position_id,
            ),
        )

    @staticmethod
    def _inclination_candidate(
        context: SatoriInclinationsContext | None,
    ) -> InclinationContextItem | None:
        if context is None or context.status != "available" or not context.inclinations:
            return None
        return min(
            context.inclinations,
            key=lambda item: (
                item.kind != "interest",
                -abs(item.effective_score),
                -item.confidence,
                -item.stability,
                item.inclination_id,
            ),
        )

    @staticmethod
    def _owned_topic_candidate(
        context: SatoriInclinationsContext | None,
    ) -> InclinationContextItem | None:
        if context is None or context.status != "available":
            return None
        eligible = tuple(
            item
            for item in context.inclinations
            if item.kind == "interest" and item.effective_score > 0.0
        )
        if not eligible:
            return None
        return min(
            eligible,
            key=lambda item: (
                -item.effective_score,
                -item.confidence,
                -item.stability,
                item.inclination_id,
            ),
        )

    @staticmethod
    def _guarded(
        evidence: CharacterRequestEvidence,
        relationship: RelationshipExpressionContext | None,
    ) -> bool:
        return bool(
            evidence.direct_personal_devaluation
            or evidence.repeated_critical_pressure
            or evidence.repeated_state_interrogation
            or (
                relationship is not None
                and (
                    relationship.recent_strain
                    or relationship.trust in {"low", "very_low"}
                    or relationship.comfort in {"low", "very_low"}
                )
            )
        )

    @staticmethod
    def _established(relationship: RelationshipExpressionContext | None) -> bool:
        return bool(
            relationship is not None
            and not relationship.recent_strain
            and relationship.maturity == "established"
            and relationship.familiarity in {"high", "very_high"}
            and (
                relationship.trust in {"high", "very_high"}
                or relationship.comfort in {"high", "very_high"}
            )
        )

    def allows_owned_topic_projection(
        self,
        *,
        evidence: CharacterRequestEvidence,
        dialogue: DialogueCoherenceContext,
        relationship: RelationshipExpressionContext | None,
    ) -> bool:
        """Whether this turn may broaden the inclination read to one adjacent topic."""

        correction = any(
            (
                dialogue.current_no_routine_questions_correction,
                dialogue.current_informal_correction,
                dialogue.current_repetition_feedback,
                dialogue.current_relevance_feedback,
                dialogue.current_frustration_feedback,
                dialogue.current_contradiction_feedback,
            )
        )
        return bool(
            evidence.topic_closure
            and self._established(relationship)
            and not dialogue.current_user_message_repeated
            and not correction
            and not evidence.harmful_overextension
            and not evidence.high_distress
            and not evidence.explicit_listen_request
            and not evidence.explicit_repair_offer
            and not self._guarded(evidence, relationship)
        )

    def _decision(
        self,
        *,
        context: RuntimeCharacterContext,
        intake: PreparedCognitionIntake,
        status: CharacterAgencyStatus,
        drive: CharacterAgencyDrive,
        act: CharacterAgencyAct,
        subject: CharacterAgencySubject,
        initiative: CharacterAgencyInitiative,
        lead: CharacterAgencyLead,
        reason_codes: tuple[CharacterAgencyReason, ...],
        subject_ref: str | None,
    ) -> CharacterAgencyDecision:
        guidance = {item.code: item for item in context.personality_expression.guidance}
        if tuple(guidance) != BASELINE_CHARACTER_GUIDANCE_CODES:
            raise ValueError("character agency requires canonical personality guidance")
        cue_directions = {item.code: item.direction for item in context.personality_expression.cues}

        def personality_score(code: str) -> tuple[float, int]:
            direction = cue_directions.get(code)
            adjustment = (
                0.12
                if direction == "slightly_stronger"
                else -0.12
                if direction == "slightly_softer"
                else 0.0
            )
            priority = _DRIVE_PERSONALITY_PRIORITY[drive]
            priority_index = priority.index(code)
            drive_fit = _PRIMARY_PERSONALITY_PRIORITY_BONUS if priority_index == 0 else 0.0
            return guidance[code].strength + adjustment + drive_fit, -priority_index

        personality_codes = tuple(
            sorted(
                _DRIVE_PERSONALITY_PRIORITY[drive],
                key=personality_score,
                reverse=True,
            )
        )[:1]
        values = {item.key: item.strength for item in context.values}
        priorities = _DRIVE_VALUE_PRIORITY[drive]
        missing_values = set(priorities).difference(values)
        if missing_values:
            raise ValueError(
                f"character agency is missing canonical values: {sorted(missing_values)}"
            )
        value_key = max(priorities, key=lambda key: (values[key], -priorities.index(key)))
        refs = tuple(
            dict.fromkeys(
                (
                    *intake.perception.source_refs,
                    *((subject_ref,) if subject_ref is not None else ()),
                )
            )
        )
        reasons = tuple(reason_codes)
        if status is CharacterAgencyStatus.FALLBACK and (
            CharacterAgencyReason.COGNITION_FALLBACK not in reasons
        ):
            reasons = (*reasons, CharacterAgencyReason.COGNITION_FALLBACK)
        return CharacterAgencyDecision(
            schema_version=self.schema_version,
            status=status,
            drive=drive,
            act=act,
            subject=subject,
            initiative=initiative,
            lead=lead,
            source_personality_codes=personality_codes,
            source_value_key=value_key,
            reason_codes=reasons,
            source_refs=refs,
            subject_ref=subject_ref,
        )


__all__ = (
    "CHARACTER_AGENCY_DECISION_SCHEMA_VERSION",
    "CharacterAgencyAct",
    "CharacterAgencyDecision",
    "CharacterAgencyDrive",
    "CharacterAgencyInitiative",
    "CharacterAgencyKernel",
    "CharacterAgencyLead",
    "CharacterAgencyReason",
    "CharacterAgencyStatus",
    "CharacterAgencySubject",
)
