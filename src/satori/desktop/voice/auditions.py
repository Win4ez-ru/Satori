"""Fixed public voice auditions; their delivery never represents current Core state."""

from dataclasses import dataclass
from typing import Final, Literal

from satori.application.conversation.character_delivery_contracts import (
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
)
from satori.application.conversation.character_expression import CharacterPressureLevel
from satori.desktop.voice.expression import VoiceDelivery


@dataclass(frozen=True, slots=True)
class PublicVoiceAudition:
    """An authored public phrase and closed synthetic direction for manual preview."""

    id: Literal["neutral", "curious", "subtle_humor", "reserved"]
    label: str
    text: str
    delivery: VoiceDelivery


def _delivery(voice: CharacterDeliveryVoice) -> VoiceDelivery:
    return VoiceDelivery(
        availability="selected",
        source_policy_version=24,
        delivery_voice=voice,
        delivery_goal=CharacterDeliveryGoal.ANSWER_PRECISELY,
        delivery_pressure=CharacterPressureLevel.NONE,
    )


PUBLIC_VOICE_AUDITIONS: Final[tuple[PublicVoiceAudition, ...]] = (
    PublicVoiceAudition(
        id="neutral",
        label="Нейтрально",
        text=(
            "Давай начнём с главного. Сначала проверим исходные данные, "  # noqa: RUF001
            "потом выберем следующий шаг."
        ),
        delivery=VoiceDelivery(),
    ),
    PublicVoiceAudition(
        id="curious",
        label="Интерес",
        text=(
            "Подожди, вот это уже интересно. Если убрать последнее допущение, "
            "результат действительно меняется?"
        ),
        delivery=_delivery(CharacterDeliveryVoice.ENGAGED_SKEPTICISM),
    ),
    PublicVoiceAudition(
        id="subtle_humor",
        label="Тонкая шутка",
        text="План хороший. Осталось объяснить это понедельнику. Думаю, он начнёт торговаться.",
        delivery=_delivery(CharacterDeliveryVoice.PLAYFUL_EDGE),
    ),
    PublicVoiceAudition(
        id="reserved",
        label="Сдержанно",
        text="Мне это было неприятно. Я хочу договорить. Давай продолжим чуть спокойнее.",
        delivery=_delivery(CharacterDeliveryVoice.COOL_RESERVE),
    ),
)


def public_voice_audition(value: object) -> PublicVoiceAudition | None:
    """Resolve only an exact catalogue ID; never accept free text or coerce input."""

    if type(value) is not str:
        return None
    for audition in PUBLIC_VOICE_AUDITIONS:
        if audition.id == value:
            return audition
    return None
