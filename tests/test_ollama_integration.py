"""Optional real-provider smoke test; excluded unless explicitly enabled."""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from satori.config import Settings
from satori.core.affect import (
    AffectiveAppraisalRequest,
    AppraisalFastState,
    AppraisalMoodState,
    AppraisalTrait,
    AppraisalValue,
)
from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderRequest,
)
from satori.core.embedding import EmbeddingRequest
from satori.core.relationship import RelationshipAppraisalRequest
from satori.infrastructure.providers.ollama import OllamaConversationAdapter
from satori.infrastructure.providers.ollama_affect import OllamaAffectiveAppraisalAdapter
from satori.infrastructure.providers.ollama_embedding import OllamaEmbeddingAdapter
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from satori.infrastructure.providers.ollama_relationship import (
    OllamaRelationshipAppraisalAdapter,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SATORI_RUN_OLLAMA_INTEGRATION") != "1",
        reason="set SATORI_RUN_OLLAMA_INTEGRATION=1 with local Ollama installed",
    ),
]


def test_real_ollama_returns_non_empty_text() -> None:
    """Verify the configured local model manually without burdening normal CI."""

    settings = Settings()
    adapter = OllamaConversationAdapter(
        base_url=settings.conversation_provider_base_url,
        model=settings.conversation_model,
        timeout_seconds=settings.conversation_timeout_seconds,
    )
    request = ConversationProviderRequest(
        schema_version=1,
        trace_id="manual-integration",
        context_schema_version=1,
        messages=(
            ConversationMessage(ConversationMessageRole.SYSTEM, "Reply briefly."),
            ConversationMessage(ConversationMessageRole.USER, "Say hello in Russian."),
        ),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.0,
            max_output_tokens=32,
        ),
    )

    result = asyncio.run(adapter.generate(request))

    assert result.text.strip()


def test_real_ollama_returns_configured_embedding_space() -> None:
    """Verify the optional local embedding model and exact configured dimensions."""

    settings = Settings()
    adapter = OllamaEmbeddingAdapter(
        base_url=settings.embedding_provider_base_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        input_schema_version=1,
        timeout_seconds=settings.embedding_timeout_seconds,
    )

    result = asyncio.run(
        adapter.embed(EmbeddingRequest(1, "manual-embedding", ("Первый запуск проекта",)))
    )

    assert result.space == adapter.space
    assert len(result.vectors[0]) == settings.embedding_dimensions


def test_real_ollama_returns_grounded_affective_appraisal() -> None:
    """Verify the configured model can satisfy the Stage 7 strict appraisal schema."""

    settings = Settings()
    adapter = OllamaAffectiveAppraisalAdapter(
        base_url=settings.conversation_provider_base_url,
        model=settings.conversation_model,
        timeout_seconds=settings.conversation_timeout_seconds,
        max_output_tokens=settings.affective_appraisal_max_output_tokens,
    )
    request = AffectiveAppraisalRequest(
        schema_version=1,
        trace_id="manual-affective-appraisal",
        interaction_id="manual-positive-interaction",
        appraised_at=datetime(2026, 7, 30, tzinfo=UTC),
        user_content="Я закончил важную работу, и результат оказался отличным.",
        traits=(
            AppraisalTrait("curiosity", 0.8),
            AppraisalTrait("emotional_sensitivity", 0.65),
        ),
        values=(AppraisalValue("growth", 0.9, "Развитие через понимание."),),
        fast_state=AppraisalFastState(
            valence=0.0,
            arousal=0.12,
            tension=0.08,
            curiosity=0.18,
            interest=0.16,
            amusement=0.05,
            concern=0.08,
            frustration=0.04,
            situational_confidence=0.55,
        ),
        mood_state=AppraisalMoodState(valence=0.0, energy=0.3, tension=0.1),
    )

    result = asyncio.run(adapter.generate_structured(request))

    assert result.proposal.schema_version == 1
    assert request.interaction_id in result.proposal.source_refs
    assert result.proposal.pleasantness >= 0.0
    assert result.proposal.appraisal_confidence >= 0.35


def test_real_ollama_relationship_semantic_corpus() -> None:
    """The configured classifier preserves the ten critical Stage 8 distinctions."""

    settings = Settings()
    client = OllamaHttpClient(settings.relationship_appraisal_provider_base_url)
    adapter = OllamaRelationshipAppraisalAdapter(
        base_url=settings.relationship_appraisal_provider_base_url,
        model=settings.relationship_appraisal_model,
        timeout_seconds=settings.relationship_appraisal_timeout_seconds,
        max_output_tokens=settings.relationship_appraisal_max_output_tokens,
        context_window=settings.relationship_appraisal_context_window,
        keep_alive=settings.ollama_keep_alive,
        http_client=client,
    )
    document = cast(
        dict[str, object],
        json.loads(
            (Path(__file__).parent / "fixtures/stage8_relationship_appraisal_v1.json").read_text()
        ),
    )
    corpus = cast(list[dict[str, object]], document["scenarios"])
    try:
        for index, scenario in enumerate(corpus):
            user_content = str(scenario["text"])
            expected_any = set(cast(list[str], scenario["expected_any"]))
            forbidden = set(cast(list[str], scenario["forbidden"]))
            result = asyncio.run(
                adapter.generate_structured(
                    RelationshipAppraisalRequest(
                        schema_version=1,
                        interaction_id=f"relationship-corpus-interaction-{index}",
                        user_message_id=f"relationship-corpus-message-{index}",
                        user_content=user_content,
                        observed_at=datetime(2026, 8, 9, tzinfo=UTC),
                        trace_id=f"relationship-corpus-trace-{index}",
                    )
                )
            )
            categories = set(result.proposal.categories)
            assert categories & expected_any, (user_content, categories)
            assert not categories & forbidden, (user_content, categories)
    finally:
        client.close()
