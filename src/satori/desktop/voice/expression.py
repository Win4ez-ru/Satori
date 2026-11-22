"""Read-only audio presentation of the current reply's existing delivery selection."""

from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from satori.application.conversation.character_delivery_contracts import (
    CharacterAffectSignal,
    CharacterAffectSignalCode,
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
    CharacterPresenceStrength,
    validate_affect_presence_semantics,
)
from satori.application.conversation.character_expression import CharacterPressureLevel
from satori.application.conversation.contracts import ConversationContextManifest

VoiceAffectProfile = Literal[
    "tense_non_hostile",
    "positive_light",
    "soft_negative_non_hostile",
    "interested_calm",
    "calm_even",
]
VoiceQualifier = Literal[
    "neutral",
    "precise",
    "direct",
    "gentle_amusement",
    "contained_strain",
    "reflective",
    "bright",
    "warm",
    "supportive",
    "reserved",
    "engaged",
]

_PRIMARY: dict[CharacterDeliveryVoice, tuple[VoiceQualifier, ...]] = {
    CharacterDeliveryVoice.THOUGHTFUL_PRECISION: ("precise",),
    CharacterDeliveryVoice.ACCOUNTABLE_DIRECT: ("direct",),
    CharacterDeliveryVoice.PLAYFUL_EDGE: ("gentle_amusement",),
    CharacterDeliveryVoice.LIVELY_DRY_WARMTH: ("warm", "gentle_amusement"),
    CharacterDeliveryVoice.PRACTICAL_GUARDED_CARE: ("supportive", "reserved"),
    CharacterDeliveryVoice.OPEN_CARE: ("supportive", "warm"),
    CharacterDeliveryVoice.ENGAGED_SKEPTICISM: ("engaged", "reflective"),
    CharacterDeliveryVoice.ENERGIZED_COLLABORATION: ("bright", "engaged"),
    CharacterDeliveryVoice.COOL_RESERVE: ("reserved",),
    CharacterDeliveryVoice.WARM_INDEPENDENCE: ("warm", "direct"),
    CharacterDeliveryVoice.REFLECTIVE_CANDOR: ("reflective",),
    CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH: ("warm", "gentle_amusement"),
}
_INSTRUCTIONS: dict[VoiceQualifier, str] = {
    "neutral": "Use a clear, natural, even tone.",
    "precise": "Keep the delivery attentive, clear and measured.",
    "direct": "Use steady, candid emphasis without scolding.",
    "gentle_amusement": (
        "Use light, understated amusement only where the supplied words support it; "
        "do not add laughter or make neutral words into a joke."
    ),
    "contained_strain": "Keep any strain contained and controlled, never angry or hostile.",
    "reflective": "Use a thoughtful, quieter contour and modest pauses without melodrama.",
    "bright": "Use slightly brighter, engaged energy without rushing or shouting.",
    "warm": "Use restrained warmth without intimacy or dependency cues.",
    "supportive": "Use gentle, calm care without pity or exaggerated reassurance.",
    "reserved": "Use a cooler, restrained tone without hostility, contempt or rejection.",
    "engaged": "Sound interested and attentive without sarcasm or contempt.",
}


class VoiceAffectSignal(BaseModel):
    """A closed copy of an existing qualitative signal, not a new affect estimate."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    code: CharacterAffectSignalCode
    level: CharacterPresenceStrength


class VoiceDelivery(BaseModel):
    """Ephemeral presentation data; arbitrary TTS instructions are never input fields.

    Use ``model_validate_json`` at a JSON transport boundary so strict enum and tuple
    validation applies to their JSON representation. Instructions are derived locally.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    result_type: Literal["voice_delivery"] = "voice_delivery"
    schema_version: Literal[1] = 1
    availability: Literal["selected", "unavailable"] = "unavailable"
    source_policy_version: int | None = Field(default=None, ge=24)
    delivery_voice: CharacterDeliveryVoice | None = None
    delivery_goal: CharacterDeliveryGoal | None = None
    delivery_pressure: CharacterPressureLevel | None = None
    affect_profile: VoiceAffectProfile | None = None
    affect_signals: tuple[VoiceAffectSignal, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def coherent_source(self) -> "VoiceDelivery":
        source = (
            self.source_policy_version,
            self.delivery_voice,
            self.delivery_goal,
            self.delivery_pressure,
        )
        if self.availability == "unavailable":
            if (
                any(item is not None for item in source)
                or self.affect_profile
                or self.affect_signals
            ):
                raise ValueError("neutral voice delivery cannot claim selected source metadata")
            return self
        if any(item is None for item in source):
            raise ValueError("selected voice delivery requires its current source codes")
        codes = tuple(signal.code for signal in self.affect_signals)
        if len(codes) != len(set(codes)):
            raise ValueError("voice affect signals must be unique")
        if self.affect_profile is None:
            if self.affect_signals:
                raise ValueError("voice affect signals require their existing profile")
        elif self.affect_signals:
            validate_affect_presence_semantics(
                self.affect_profile,
                tuple(
                    CharacterAffectSignal(code=signal.code, level=signal.level)
                    for signal in self.affect_signals
                ),
            )
        elif self.source_policy_version is not None and self.source_policy_version >= 26:
            raise ValueError("current voice affect profile requires existing presence signals")
        return self

    @property
    def qualifiers(self) -> tuple[VoiceQualifier, ...]:
        """Map existing selection to at most three modest audio-only qualities."""

        if self.availability != "selected" or self.delivery_voice is None:
            return ("neutral",)
        selected = list(_PRIMARY.get(self.delivery_voice, ("neutral",)))
        secondary: VoiceQualifier | None = None
        if self.affect_profile == "tense_non_hostile":
            secondary = "contained_strain"
        elif self.affect_profile == "soft_negative_non_hostile":
            secondary = "reflective"
        elif self.affect_profile == "interested_calm":
            secondary = "engaged"
        elif self.affect_profile == "positive_light" and self.delivery_voice in {
            CharacterDeliveryVoice.PLAYFUL_EDGE,
            CharacterDeliveryVoice.LIVELY_DRY_WARMTH,
            CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH,
            CharacterDeliveryVoice.ENERGIZED_COLLABORATION,
        }:
            secondary = "bright"
        if secondary is not None and secondary not in selected:
            selected.append(secondary)
        # Pressure is licensed conversational emphasis, never permission to sound
        # threatening. Quiet presence remains gentle even with positive affect.
        if self.delivery_goal is CharacterDeliveryGoal.STAY_PRESENT:
            selected = [item for item in selected if item != "bright"]
        if self.delivery_pressure is CharacterPressureLevel.FIRM and "direct" not in selected:
            selected = [*selected[:2], "direct"]
        return tuple(selected[:3])

    @property
    def instruction(self) -> str:
        """Return fixed engine-only guidance; no identity, user text or free prompt."""

        return " ".join(_INSTRUCTIONS[qualifier] for qualifier in self.qualifiers)


def project_voice_delivery(manifest: ConversationContextManifest) -> VoiceDelivery:
    """Read this committed reply's transient selection; unknown and legacy stay neutral.

    This function does not inspect reply words or read any state owner. Correlation,
    replay suppression and cancellation belong to the enclosing desktop send path.
    """

    if (
        not isinstance(manifest, ConversationContextManifest)
        or type(manifest.policy_schema_version) is not int
        or manifest.policy_schema_version < 24
        or type(manifest.character_delivery_decision_schema_version) is not int
        or manifest.character_delivery_decision_schema_version not in {1, 2, 3, 4, 5}
    ):
        return VoiceDelivery()
    if manifest.policy_schema_version >= 26 and (
        type(manifest.character_presence_projection_schema_version) is not int
        or manifest.character_presence_projection_schema_version not in {1, 2, 3}
    ):
        return VoiceDelivery()
    raw_signals = manifest.character_presence_affect_signals
    if not isinstance(raw_signals, tuple) or len(raw_signals) > 3:
        return VoiceDelivery()
    voice = manifest.character_delivery_voice
    goal = manifest.character_delivery_goal
    pressure = manifest.character_delivery_pressure
    if not isinstance(voice, str) or not isinstance(goal, str) or not isinstance(pressure, str):
        return VoiceDelivery()
    signals: list[VoiceAffectSignal] = []
    try:
        for signal in raw_signals:
            if not isinstance(signal, str) or len(signal) > 80:
                return VoiceDelivery()
            code, level = signal.split(":")
            signals.append(
                VoiceAffectSignal(
                    code=CharacterAffectSignalCode(code),
                    level=CharacterPresenceStrength(level),
                )
            )
        return VoiceDelivery(
            availability="selected",
            source_policy_version=manifest.policy_schema_version,
            delivery_voice=CharacterDeliveryVoice(voice),
            delivery_goal=CharacterDeliveryGoal(goal),
            delivery_pressure=CharacterPressureLevel(pressure),
            affect_profile=cast(VoiceAffectProfile | None, manifest.affect_expression_profile),
            affect_signals=tuple(signals),
        )
    except (TypeError, ValueError):
        return VoiceDelivery()
