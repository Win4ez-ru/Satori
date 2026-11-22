"""Public audition catalogue boundaries; no runtime, provider, GUI or audio."""

from dataclasses import FrozenInstanceError, fields
from operator import setitem

import pytest
from pydantic import ValidationError

from satori.application.conversation.character_delivery_contracts import (
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
)
from satori.application.conversation.character_expression import CharacterPressureLevel
from satori.desktop.voice.auditions import (
    PUBLIC_VOICE_AUDITIONS,
    PublicVoiceAudition,
    public_voice_audition,
)
from satori.desktop.voice.expression import VoiceDelivery


def test_catalogue_is_an_immutable_tuple_of_frozen_public_cases() -> None:
    assert isinstance(PUBLIC_VOICE_AUDITIONS, tuple)
    assert tuple(case.id for case in PUBLIC_VOICE_AUDITIONS) == (
        "neutral",
        "curious",
        "subtle_humor",
        "reserved",
    )
    with pytest.raises(TypeError):
        setitem(PUBLIC_VOICE_AUDITIONS, 0, PUBLIC_VOICE_AUDITIONS[1])  # type: ignore[call-overload]
    for case in PUBLIC_VOICE_AUDITIONS:
        with pytest.raises(FrozenInstanceError):
            case.text = "PRIVATE DRAFT"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            case.delivery.availability = "unavailable"


@pytest.mark.parametrize(
    ("identifier", "label", "text"),
    [
        (
            "neutral",
            "Нейтрально",
            "Давай начнём с главного. Сначала проверим исходные данные, "  # noqa: RUF001
            "потом выберем следующий шаг.",
        ),
        (
            "curious",
            "Интерес",
            "Подожди, вот это уже интересно. Если убрать последнее допущение, "
            "результат действительно меняется?",
        ),
        (
            "subtle_humor",
            "Тонкая шутка",
            "План хороший. Осталось объяснить это понедельнику. Думаю, он начнёт торговаться.",
        ),
        (
            "reserved",
            "Сдержанно",
            "Мне это было неприятно. Я хочу договорить. Давай продолжим чуть спокойнее.",
        ),
    ],
)
def test_exact_public_text_and_russian_label(identifier: str, label: str, text: str) -> None:
    case = public_voice_audition(identifier)
    assert case is not None
    assert case.label == label
    assert case.text == text
    assert case is next(item for item in PUBLIC_VOICE_AUDITIONS if item.id == identifier)


@pytest.mark.parametrize(
    ("identifier", "voice", "qualifiers"),
    [
        ("neutral", None, ("neutral",)),
        ("curious", CharacterDeliveryVoice.ENGAGED_SKEPTICISM, ("engaged", "reflective")),
        ("subtle_humor", CharacterDeliveryVoice.PLAYFUL_EDGE, ("gentle_amusement",)),
        ("reserved", CharacterDeliveryVoice.COOL_RESERVE, ("reserved",)),
    ],
)
def test_audition_uses_only_existing_closed_delivery(
    identifier: str, voice: CharacterDeliveryVoice | None, qualifiers: tuple[str, ...]
) -> None:
    case = public_voice_audition(identifier)
    assert case is not None
    assert case.delivery.qualifiers == qualifiers
    assert case.delivery.delivery_voice is voice
    assert case.delivery.affect_profile is None
    assert case.delivery.affect_signals == ()
    if voice is None:
        assert case.delivery == VoiceDelivery()
    else:
        assert case.delivery.availability == "selected"
        assert case.delivery.source_policy_version == 24
        assert case.delivery.delivery_goal is CharacterDeliveryGoal.ANSWER_PRECISELY
        assert case.delivery.delivery_pressure is CharacterPressureLevel.NONE


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        24,
        b"neutral",
        ["neutral"],
        {"id": "neutral", "text": "PRIVATE DRAFT"},
        "",
        " neutral",
        "neutral ",
        "NEUTRAL",
        "neutral\n",
        "interest",
        "playful",
        "reserved_hurt",
        "[curious]",
        "PRIVATE DRAFT",
    ],
)
def test_unknown_or_malformed_input_does_not_become_an_audition(value: object) -> None:
    assert public_voice_audition(value) is None


def test_lookup_does_not_coerce_objects_or_accept_string_subclasses() -> None:
    class CoercibleInput:
        def __str__(self) -> str:
            pytest.fail("Audition lookup cannot stringify external input")

    class StringSubclass(str):
        pass

    assert public_voice_audition(CoercibleInput()) is None
    assert public_voice_audition(StringSubclass("neutral")) is None


def test_catalogue_cannot_take_a_draft_history_credential_or_voice_id() -> None:
    assert tuple(field.name for field in fields(PublicVoiceAudition)) == (
        "id",
        "label",
        "text",
        "delivery",
    )
    for case in PUBLIC_VOICE_AUDITIONS:
        assert public_voice_audition(case.text) is None
        assert "PRIVATE" not in case.text
        assert "[" not in case.text
        assert "]" not in case.text
        assert "instruction" not in case.delivery.model_dump()
