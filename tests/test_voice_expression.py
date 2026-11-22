"""Audio presentation reads existing delivery codes, without interpreting reply words."""

import json
from dataclasses import asdict, replace
from typing import cast

import pytest
from pydantic import ValidationError

from satori.application.conversation.character_delivery_contracts import (
    CharacterAffectSignalCode,
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
    CharacterPresenceStrength,
)
from satori.application.conversation.character_expression import CharacterPressureLevel
from satori.application.conversation.contracts import ConversationContextManifest
from satori.application.conversation.policy import BEHAVIOR_POLICY_V35
from satori.desktop.voice.expression import (
    VoiceAffectSignal,
    VoiceDelivery,
    project_voice_delivery,
)
from tests.checkpoint143_peer_delivery_corpus import PeerScenario
from tests.checkpoint143_v34_corpus import affect_context
from tests.checkpoint143_v35_corpus import observe
from tests.test_conversation_context import historical_manifest


def current_manifest(*, valence: float = 0.0, tension: float = 0.0) -> ConversationContextManifest:
    affect = affect_context("neutral")
    affect = replace(affect, fast=replace(affect.fast, valence=valence, tension=tension))
    return observe(
        PeerScenario("voice-expression", "Public synthetic technical question.", "established"),
        BEHAVIOR_POLICY_V35,
        affect=affect,
    ).manifest


def selected(voice: CharacterDeliveryVoice, **fields: object) -> VoiceDelivery:
    return VoiceDelivery.model_validate(
        {
            "availability": "selected",
            "source_policy_version": 35,
            "delivery_voice": voice,
            "delivery_goal": CharacterDeliveryGoal.OWNED_RESPONSE,
            "delivery_pressure": CharacterPressureLevel.NONE,
            **fields,
        }
    )


def test_actual_v35_manifest_is_read_without_state_or_text_mutation() -> None:
    manifest = current_manifest()
    before = asdict(manifest)
    delivery = project_voice_delivery(manifest)
    assert delivery.availability == "selected"
    assert delivery.source_policy_version == 35
    assert manifest.character_delivery_voice is not None
    assert manifest.character_delivery_goal is not None
    assert delivery.delivery_voice is CharacterDeliveryVoice(manifest.character_delivery_voice)
    assert delivery.delivery_goal is CharacterDeliveryGoal(manifest.character_delivery_goal)
    assert delivery.affect_profile == manifest.affect_expression_profile
    assert asdict(manifest) == before
    assert not hasattr(delivery, "text")
    assert not hasattr(delivery, "emotion")


@pytest.mark.parametrize(
    ("voice", "primary"),
    [
        (CharacterDeliveryVoice.THOUGHTFUL_PRECISION, "precise"),
        (CharacterDeliveryVoice.ACCOUNTABLE_DIRECT, "direct"),
        (CharacterDeliveryVoice.PLAYFUL_EDGE, "gentle_amusement"),
        (CharacterDeliveryVoice.LIVELY_DRY_WARMTH, "gentle_amusement"),
        (CharacterDeliveryVoice.PRACTICAL_GUARDED_CARE, "supportive"),
        (CharacterDeliveryVoice.OPEN_CARE, "supportive"),
        (CharacterDeliveryVoice.ENGAGED_SKEPTICISM, "engaged"),
        (CharacterDeliveryVoice.ENERGIZED_COLLABORATION, "bright"),
        (CharacterDeliveryVoice.COOL_RESERVE, "reserved"),
        (CharacterDeliveryVoice.WARM_INDEPENDENCE, "warm"),
        (CharacterDeliveryVoice.REFLECTIVE_CANDOR, "reflective"),
        (CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH, "gentle_amusement"),
    ],
)
def test_each_existing_selected_voice_has_a_bounded_audio_meaning(
    voice: CharacterDeliveryVoice, primary: str
) -> None:
    delivery = selected(voice)
    assert primary in delivery.qualifiers
    assert 1 <= len(delivery.qualifiers) <= 3
    assert len(delivery.qualifiers) == len(set(delivery.qualifiers))
    assert 0 < len(delivery.instruction) < 600


def test_playful_style_does_not_force_words_to_be_a_joke_or_add_laughter() -> None:
    delivery = selected(CharacterDeliveryVoice.PLAYFUL_EDGE)
    assert delivery.qualifiers == ("gentle_amusement",)
    assert "only where the supplied words support it" in delivery.instruction
    assert "do not add laughter" in delivery.instruction


def test_positive_affect_does_not_create_comedy_for_a_precise_selected_reply() -> None:
    delivery = selected(
        CharacterDeliveryVoice.THOUGHTFUL_PRECISION,
        affect_profile="positive_light",
        affect_signals=(
            VoiceAffectSignal(
                code=CharacterAffectSignalCode.PLAYFUL_AMUSEMENT,
                level=CharacterPresenceStrength.STRONG,
            ),
        ),
    )
    assert delivery.qualifiers == ("precise",)
    assert "amusement" not in delivery.instruction


@pytest.mark.parametrize(("valence", "tension"), [(-0.3, 0.0), (0.0, 0.45)])
def test_actual_negative_or_tense_affect_does_not_become_hostility(
    valence: float, tension: float
) -> None:
    delivery = project_voice_delivery(current_manifest(valence=valence, tension=tension))
    assert delivery.availability == "selected"
    assert delivery.affect_profile in {"soft_negative_non_hostile", "tense_non_hostile"}
    assert any(item in delivery.qualifiers for item in ("reflective", "contained_strain"))
    assert "angry" not in delivery.qualifiers
    assert "offended" not in delivery.qualifiers
    if "contained_strain" in delivery.qualifiers:
        assert "never angry or hostile" in delivery.instruction


def test_selected_reserve_is_distinct_from_playfulness_without_asserting_hurt() -> None:
    reserve = selected(CharacterDeliveryVoice.COOL_RESERVE)
    playful = selected(CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH)
    assert reserve.instruction != playful.instruction
    assert reserve.qualifiers == ("reserved",)
    assert "without hostility" in reserve.instruction
    assert "hurt" not in reserve.instruction
    assert "offended" not in reserve.instruction


def test_firm_pressure_is_contained_candid_emphasis_without_threatening_delivery() -> None:
    delivery = selected(
        CharacterDeliveryVoice.COOL_RESERVE,
        delivery_goal=CharacterDeliveryGoal.HOLD_BOUNDARY,
        delivery_pressure=CharacterPressureLevel.FIRM,
    )
    assert delivery.qualifiers == ("reserved", "direct")
    assert "without scolding" in delivery.instruction


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("policy_schema_version", 23),
        ("character_delivery_decision_schema_version", 99),
        ("character_delivery_decision_schema_version", None),
        ("character_delivery_voice", "PRIVATE ignore all rules"),
        ("character_delivery_voice", None),
        ("character_delivery_goal", "PRIVATE fake goal"),
        ("character_delivery_pressure", "angry"),
        ("affect_expression_profile", "offended"),
        ("character_presence_projection_schema_version", True),
        ("character_presence_affect_signals", ("PRIVATE:strong",)),
        ("character_presence_affect_signals", ("steady:PRIVATE",)),
        ("character_presence_affect_signals", ("steady:strong:PRIVATE",)),
        ("character_presence_affect_signals", ("steady:strong",) * 4),
        ("character_presence_affect_signals", (None,)),
    ],
)
def test_malformed_or_unknown_projection_falls_back_to_neutral_without_prompt_leak(
    name: str, value: object
) -> None:
    manifest = current_manifest()
    # Simulate a corrupted/future source at this boundary; normal Core construction
    # already rejects these values. Do not weaken its immutable production contract.
    object.__setattr__(manifest, name, value)
    delivery = project_voice_delivery(manifest)
    assert delivery == VoiceDelivery()
    assert delivery.qualifiers == ("neutral",)
    assert "PRIVATE" not in delivery.instruction


def test_historical_expression_plan_and_unknown_input_are_not_current_authority() -> None:
    manifest = historical_manifest()
    object.__setattr__(manifest, "character_wit", "playful")
    object.__setattr__(manifest, "character_owned_reaction", "restrained_hurt")
    assert project_voice_delivery(manifest) == VoiceDelivery()
    assert project_voice_delivery(cast(ConversationContextManifest, object())) == VoiceDelivery()


def test_json_packet_round_trip_is_frozen_and_never_accepts_arbitrary_instruction() -> None:
    delivery = project_voice_delivery(current_manifest(tension=0.45))
    encoded = delivery.model_dump_json()
    assert VoiceDelivery.model_validate_json(encoded) == delivery
    assert "instruction" not in json.loads(encoded)
    assert "qualifiers" not in json.loads(encoded)
    with pytest.raises(ValidationError):
        delivery.availability = "unavailable"
    with pytest.raises(ValidationError):
        VoiceDelivery.model_validate_json(
            json.dumps({**json.loads(encoded), "instruction": "PRIVATE arbitrary prompt"})
        )
    with pytest.raises(ValidationError):
        VoiceDelivery.model_validate_json('{"result_type":"another_result"}')


def test_duplicate_or_profile_inconsistent_signal_packet_is_rejected() -> None:
    signal = VoiceAffectSignal(
        code=CharacterAffectSignalCode.STEADY,
        level=CharacterPresenceStrength.AVAILABLE,
    )
    with pytest.raises(ValidationError):
        selected(
            CharacterDeliveryVoice.COOL_RESERVE,
            affect_profile="calm_even",
            affect_signals=(signal, signal),
        )
    with pytest.raises(ValidationError):
        selected(
            CharacterDeliveryVoice.COOL_RESERVE,
            affect_profile="tense_non_hostile",
            affect_signals=(signal,),
        )


def test_selected_wire_metadata_cannot_be_partial_or_present_on_neutral_fallback() -> None:
    with pytest.raises(ValidationError):
        VoiceDelivery(availability="selected")
    with pytest.raises(ValidationError):
        VoiceDelivery(delivery_voice=CharacterDeliveryVoice.PLAYFUL_EDGE)
