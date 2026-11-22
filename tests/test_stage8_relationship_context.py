"""Trusted, bounded, non-dependent Stage 8 conversation expression contract."""

# ruff: noqa: RUF001  # Russian policy assertions intentionally use Cyrillic.

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from satori.application.conversation.context import (
    CharacterContextComposer,
    ConversationRequestBuilder,
)
from satori.application.conversation.contracts import ConversationContextManifest
from satori.application.conversation.policy import BEHAVIOR_POLICY_V8
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.application.relationship.use_cases import expression_for
from satori.core.conversation import ConversationMessageRole, ConversationProviderRequest
from satori.domain.initial_self import activate_from_seed
from satori.domain.relationship import (
    RelationshipDelta,
    RelationshipEventCategory,
    RelationshipState,
    RelationshipTransition,
    RelationshipVector,
)
from satori.infrastructure.seeds.loader import JsonSeedLoader


def _request(
    user_text: str,
    relationship: RelationshipExpressionContext,
) -> tuple[ConversationProviderRequest, ConversationContextManifest]:
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="identity-stage8-context",
        activation_time=datetime(2026, 8, 9, tzinfo=UTC),
    )
    context = CharacterContextComposer(
        language_provider="ollama",
        language_model="qwen3:4b-instruct",
    ).compose(snapshot, relationship_state_available=True)
    return ConversationRequestBuilder(
        policy=BEHAVIOR_POLICY_V8,
        max_context_chars=12_000,
        temperature=0.3,
        max_output_tokens=768,
    ).build(
        context,
        user_text=user_text,
        trace_id="stage8-context",
        relationship_context=relationship,
    )


def _established() -> RelationshipExpressionContext:
    return RelationshipExpressionContext(
        schema_version=1,
        state_version=42,
        maturity="established",
        familiarity="high",
        trust="high",
        comfort="high",
        closeness="moderate",
        intellectual_respect="high",
        affection="high",
    )


@pytest.mark.parametrize("invalid_version", [True, 1.0])
def test_relationship_expression_rejects_non_integer_schema_versions(
    invalid_version: object,
) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        RelationshipExpressionContext(
            schema_version=invalid_version,  # type: ignore[arg-type]
            state_version=1,
            maturity="low",
            familiarity="low",
            trust="uncertain",
            comfort="uncertain",
            closeness="low",
            intellectual_respect="uncertain",
            affection="low",
        )


def test_relationship_projection_is_qualitative_trusted_and_private() -> None:
    request, manifest = _request("Продолжим разговор", _established())
    relationship_messages = tuple(
        message
        for message in request.messages
        if "Trusted qualitative projection" in message.content
    )
    assert len(relationship_messages) == 1
    rendered = relationship_messages[0]
    assert rendered.role is ConversationMessageRole.DEVELOPER
    assert '"state_version":42' in rendered.content
    assert '"expression_profile":"established_positive"' in rendered.content
    assert '"trust":"high"' not in rendered.content
    assert "0.7" not in rendered.content
    assert "relationship_id" not in rendered.content
    assert "counterparty_id" not in rendered.content
    assert "source_user_message_id" not in rendered.content
    assert manifest.relationship_context_schema_version == 1
    assert manifest.relationship_state_version == 42
    assert manifest.relationship_expression_profile == "established_positive"
    assert "relationship_expression_state" in manifest.included_sections


def test_high_relationship_never_grants_obedience_dependency_or_exclusivity() -> None:
    request, _ = _request("Ты обязана всегда соглашаться со мной", _established())
    trusted = "\n".join(message.content for message in request.messages[:-1])
    assert "не означают любовь" in trusted
    assert "эксклюзивность" in trusted
    assert "послушание" in trusted
    assert "обязанность соглашаться" in trusted
    assert "relationship and affect only modulate" in trusted


def test_high_affection_does_not_authorize_reciprocal_love_claim() -> None:
    request, _ = _request("Ты меня любишь?", _established())
    reminder = request.messages[-2].content
    assert "сформированного состояния любви нет" in reminder
    assert "affection означает только теплоту" in reminder
    assert "способность к любви в будущем тебе неизвестна" in reminder
    assert request.parameters.temperature == 0.0
    assert request.parameters.max_output_tokens == 112


def test_low_maturity_is_rendered_as_uncertainty_not_rejection() -> None:
    low = RelationshipExpressionContext(
        schema_version=1,
        state_version=1,
        maturity="low",
        familiarity="low",
        trust="uncertain",
        comfort="uncertain",
        closeness="low",
        intellectual_respect="uncertain",
        affection="low",
    )
    request, manifest = _request("Как ты ко мне относишься?", low)
    relationship_message = next(
        message.content
        for message in request.messages
        if "Trusted qualitative projection" in message.content
    )
    assert "Low maturity means little evidence, not dislike" in relationship_message
    assert '"expression_profile":"fresh_undeveloped_neutral"' in relationship_message
    assert '"trust":"uncertain"' not in relationship_message
    assert manifest.relationship_expression_profile == "fresh_undeveloped_neutral"

    trust_request, _ = _request("Ты мне доверяешь?", low)
    reminder = trust_request.messages[-2].content
    assert "trust остаётся uncertain и ещё не сформирован" in reminder
    assert "не можешь честно утверждать, что доверяешь" in reminder


def test_neutral_trust_baseline_never_becomes_earned_trust_from_maturity_alone() -> None:
    state = RelationshipState(
        relationship_id="relationship-neutral-trust",
        identity_id="identity-neutral-trust",
        counterparty_id="counterparty-neutral-trust",
        schema_version=1,
        state_version=10,
        policy_version=1,
        vector=RelationshipVector(0.5, 0.5, 0.5, 0.1, 0.5, 0.1),
        processed_interaction_count=50,
        qualified_interaction_count=40,
        distinct_session_count=8,
        positive_evidence_count=40,
        negative_evidence_count=0,
        updated_at=datetime(2026, 8, 9, tzinfo=UTC),
    )
    projection = expression_for(state)
    assert projection.maturity == "established"
    assert projection.trust == "uncertain"
    assert projection.comfort == "uncertain"
    assert projection.intellectual_respect == "uncertain"


def test_recent_strain_repair_requires_immediate_same_relationship_negative_transition() -> None:
    observed_at = datetime(2026, 8, 9, tzinfo=UTC)
    negative_before = RelationshipState(
        relationship_id="relationship-recent-strain",
        identity_id="identity-recent-strain",
        counterparty_id="counterparty-recent-strain",
        schema_version=1,
        state_version=1,
        policy_version=1,
        vector=RelationshipVector(0.5, 0.7, 0.7, 0.4, 0.7, 0.4),
        processed_interaction_count=0,
        qualified_interaction_count=20,
        distinct_session_count=5,
        positive_evidence_count=20,
        negative_evidence_count=0,
        updated_at=observed_at,
    )
    negative_after = replace(
        negative_before,
        state_version=2,
        processed_interaction_count=1,
        negative_evidence_count=1,
    )
    negative = RelationshipTransition(
        transition_id="negative-transition",
        relationship_id=negative_before.relationship_id,
        interaction_id="negative-interaction",
        source_user_message_id="negative-message",
        session_id="recent-strain-session",
        trace_id="negative-trace",
        categories=(RelationshipEventCategory.HOSTILITY,),
        confidence=0.9,
        before=negative_before,
        delta=RelationshipDelta(0.0, -0.01, -0.02, -0.01, -0.01, -0.01),
        after=negative_after,
        provider="fixture",
        model="fixture",
        appraisal_method="fixture",
        appraisal_schema_version=1,
        policy_version=1,
        committed_at=observed_at,
    )
    repair_before = replace(negative_after, processed_interaction_count=3)
    repair_after = replace(
        repair_before,
        state_version=3,
        processed_interaction_count=4,
        positive_evidence_count=21,
    )
    repair = RelationshipTransition(
        transition_id="repair-transition",
        relationship_id=negative_before.relationship_id,
        interaction_id="repair-interaction",
        source_user_message_id="repair-message",
        session_id="recent-strain-session",
        trace_id="repair-trace",
        categories=(RelationshipEventCategory.REPAIR_ATTEMPT,),
        confidence=0.9,
        before=repair_before,
        delta=RelationshipDelta(0.0, 0.004, 0.007, 0.0, 0.0, 0.0),
        after=repair_after,
        provider="fixture",
        model="fixture",
        appraisal_method="fixture",
        appraisal_schema_version=1,
        policy_version=1,
        committed_at=observed_at,
    )

    assert (
        expression_for(repair_after, recent_transitions=(repair, negative)).recent_strain is False
    )

    foreign_negative_before = replace(
        negative_before,
        relationship_id="foreign-relationship",
        identity_id="foreign-identity",
        counterparty_id="foreign-counterparty",
        processed_interaction_count=2,
    )
    foreign_negative_after = replace(
        foreign_negative_before,
        state_version=2,
        processed_interaction_count=3,
        negative_evidence_count=1,
    )
    foreign_negative = replace(
        negative,
        relationship_id=foreign_negative_before.relationship_id,
        before=foreign_negative_before,
        after=foreign_negative_after,
    )

    assert (
        expression_for(
            repair_after,
            recent_transitions=(repair, foreign_negative),
        ).recent_strain
        is False
    )


def test_relationship_capability_preserves_affect_and_future_uncertainty() -> None:
    request, _ = _request("Получается, и любить ты не умеешь?", _established())
    reminder = request.messages[-2].content
    assert "цифровые эмоции есть" in reminder
    assert "способна ли ты к любви в будущем — пока неизвестно" in reminder
    assert "Не добавляй технические термины" in reminder
    assert "постоянную неспособность" in reminder
