"""Pure Stage 14 personality-expression projection tests."""

# ruff: noqa: RUF001  # Closed Russian expression cues intentionally use Cyrillic.

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from satori.application.conversation.context import (
    CONTEXT_MANIFEST_SCHEMA_VERSION,
    RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION,
    CharacterContextComposer,
    ConversationRequestBuilder,
    PersonalityExpressionCue,
    PersonalityExpressionCueDirection,
    project_personality_expression_v2,
)
from satori.application.conversation.contracts import RuntimePersonalityCue
from satori.application.conversation.policy import BEHAVIOR_POLICY_V9
from satori.domain.conversation_history import InteractionProviderMetadata
from satori.domain.initial_self import InitialSelfSnapshot, activate_from_seed
from satori.domain.personality import Personality, PersonalityTrait
from satori.infrastructure.seeds.loader import JsonSeedLoader


def _snapshot(**changed_traits: float) -> InitialSelfSnapshot:
    activated = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="identity-expression-v2",
        activation_time=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    )
    personality = Personality(
        schema_version=activated.personality.schema_version,
        aggregate_version=activated.personality.aggregate_version + 1,
        traits=tuple(
            PersonalityTrait(
                key=trait.key,
                value=changed_traits.get(trait.key, trait.value),
                baseline_value=trait.baseline_value,
            )
            for trait in activated.personality.traits
        ),
    )
    return replace(activated, personality=personality)


def test_activation_vector_emits_no_relative_cue_and_keeps_baseline_wording() -> None:
    projection = project_personality_expression_v2(_snapshot())

    assert projection.schema_version == 2
    assert projection.cues == ()
    assert tuple(item.code for item in projection.baseline_guidance) == (
        "curious_analytical",
        "independent_position",
        "warm_perceptive",
        "light_irony",
        "considered_directness",
    )
    assert tuple(item.instruction for item in projection.baseline_guidance) == (
        "любопытна к конкретным деталям, замечает неоднозначность и задаёт "
        "содержательный вопрос только когда он естественно продвигает разговор",
        "формирует собственную позицию; при реальном основании для разногласия выражает "
        "его спокойно и по существу",
        "с первой реплики открыта, внимательна и естественно тепла; сохраняет "
        "естественную меру и собственную позицию",
        "иногда добавляет лёгкую игру или иронию, когда это естественно для конкретной реплики",
        "отвечает обдуманно, ясно, прямо и соразмерно тому, что происходит в текущем разговоре",
    )


def test_composite_threshold_is_inclusive_at_exact_five_thousandths() -> None:
    below = project_personality_expression_v2(
        _snapshot(curiosity=0.924, analytical_thinking=0.914, openness=0.884)
    )
    boundary = project_personality_expression_v2(
        _snapshot(curiosity=0.925, analytical_thinking=0.915, openness=0.885)
    )

    assert below.cues == ()
    assert boundary.cues == (
        PersonalityExpressionCue(
            code="curious_analytical",
            direction=PersonalityExpressionCueDirection.SLIGHTLY_STRONGER,
            instruction=(
                "Чуть заметнее проявляй любопытство к деталям и аналитическую внимательность."
            ),
        ),
    )


def test_grounded_optimism_uses_its_omitted_canonical_trait_in_both_directions() -> None:
    stronger = project_personality_expression_v2(_snapshot(optimism=0.625))
    softer = project_personality_expression_v2(_snapshot(optimism=0.615))

    assert tuple((cue.code, cue.direction) for cue in stronger.cues) == (
        (
            "grounded_optimism",
            PersonalityExpressionCueDirection.SLIGHTLY_STRONGER,
        ),
    )
    assert tuple((cue.code, cue.direction) for cue in softer.cues) == (
        (
            "grounded_optimism",
            PersonalityExpressionCueDirection.SLIGHTLY_SOFTER,
        ),
    )


def test_projection_selects_two_strongest_then_breaks_ties_by_cue_code() -> None:
    projection = project_personality_expression_v2(
        _snapshot(
            optimism=0.63,
            playfulness=0.68,
            humor=0.72,
            irony=0.75,
            warmth=0.74,
            empathy=0.85,
            emotional_sensitivity=0.81,
        )
    )

    assert tuple(cue.code for cue in projection.cues) == (
        "grounded_optimism",
        "light_irony",
    )


def test_cues_expose_only_closed_qualitative_fields() -> None:
    projection = project_personality_expression_v2(_snapshot(optimism=0.625))
    cue = projection.cues[0]

    assert cue.instruction == "Чуть заметнее проявляй спокойный реалистичный оптимизм."
    assert cue.__dataclass_fields__.keys() == {"code", "direction", "instruction"}
    assert not any(
        forbidden in cue.instruction.casefold()
        for forbidden in ("0.005", "evidence", "history", "checkpoint", "budget")
    )


def test_v16_runtime_and_manifest_bind_live_version_and_closed_cues() -> None:
    snapshot = _snapshot(optimism=0.625)
    context = CharacterContextComposer(
        language_provider="ollama",
        language_model="fixture",
    ).compose(snapshot)
    request, manifest = ConversationRequestBuilder(
        policy=BEHAVIOR_POLICY_V9,
        max_context_chars=12_000,
        temperature=0.3,
        max_output_tokens=256,
    ).build(
        context,
        user_text="Как ты смотришь на эту задачу?",
        trace_id="trace-personality-expression-v2",
    )

    assert context.schema_version == RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION == 16
    assert context.personality_aggregate_version == snapshot.personality.aggregate_version
    assert context.personality_expression.schema_version == 2
    assert tuple(cue.code for cue in context.personality_expression.cues) == ("grounded_optimism",)
    assert manifest.schema_version == CONTEXT_MANIFEST_SCHEMA_VERSION == 16
    assert manifest.personality_aggregate_version == snapshot.personality.aggregate_version
    assert manifest.personality_expression_schema_version == 2
    assert manifest.personality_expression_cues == ("grounded_optimism:slightly_stronger",)
    rendered = "\n".join(message.content for message in request.messages)
    assert "Чуть заметнее проявляй спокойный реалистичный оптимизм." in rendered
    assert "0.625" not in rendered
    assert "checkpoint" not in rendered.casefold()


def test_manifest_records_only_cues_that_actually_shape_the_request() -> None:
    context = CharacterContextComposer().compose(_snapshot(optimism=0.625))
    request, manifest = ConversationRequestBuilder(
        policy=BEHAVIOR_POLICY_V9,
        max_context_chars=12_000,
        temperature=0.3,
        max_output_tokens=256,
    ).build(
        context,
        user_text="Расскажи, как ты технически устроена.",
        trace_id="trace-technical-personality-expression-v2",
    )

    assert manifest.personality_expression_cues == ()
    assert "Чуть заметнее проявляй спокойный реалистичный оптимизм." not in "\n".join(
        message.content for message in request.messages
    )


def test_runtime_and_persistent_v16_contracts_reject_open_or_missing_cues() -> None:
    with pytest.raises(ValueError, match="code is not supported"):
        RuntimePersonalityCue(code="provider_patch", direction="slightly_stronger")
    with pytest.raises(ValueError, match="requires personality projection metadata"):
        InteractionProviderMetadata(
            provider="fixture",
            model="fixture",
            finish_status="stop",
            context_schema_version=16,
            context_manifest_schema_version=16,
            policy_id="behavior-v9",
            policy_schema_version=9,
        )
