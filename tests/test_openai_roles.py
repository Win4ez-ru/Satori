"""All structured ports through synthetic Responses and unchanged local owners' schemas."""

import asyncio
import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from satori.application.source_policy.dispatch import source_dispatch_admission
from satori.core.affect import AffectiveAppraisalProviderError, AppraisalPreviousExchange
from satori.core.episode import EpisodeFormationProviderError
from satori.core.models import ModelFormationProviderError
from satori.core.positions import PositionFormationProviderError
from satori.core.reflection import ReflectionProviderError
from satori.core.relationship import RelationshipAppraisalProviderError
from satori.core.semantic import SemanticFormationProviderError
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.providers.ollama_affect import OllamaAffectiveAppraisalAdapter
from satori.infrastructure.providers.ollama_episode import OllamaEpisodeFormationAdapter
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from satori.infrastructure.providers.ollama_models import OllamaModelFormationAdapter
from satori.infrastructure.providers.ollama_positions import OllamaPositionFormationAdapter
from satori.infrastructure.providers.ollama_reflection import OllamaReflectionAdapter
from satori.infrastructure.providers.ollama_relationship import OllamaRelationshipAppraisalAdapter
from satori.infrastructure.providers.ollama_semantic import OllamaSemanticFormationAdapter
from satori.infrastructure.providers.openai_roles import (
    OpenAIAffectiveAppraisalAdapter,
    OpenAIEpisodeFormationAdapter,
    OpenAIModelFormationAdapter,
    OpenAIPositionFormationAdapter,
    OpenAIReflectionAdapter,
    OpenAIRelationshipAppraisalAdapter,
    OpenAISemanticFormationAdapter,
)
from satori.infrastructure.providers.openai_structured import (
    OpenAIStructuredClient,
    normalize_schema,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests import test_ollama_affect_provider as affect
from tests import test_ollama_episode_provider as episode
from tests import test_ollama_models_provider as models
from tests import test_ollama_positions_provider as positions
from tests import test_ollama_reflection_provider as reflection
from tests import test_ollama_relationship_provider as relationship
from tests import test_ollama_semantic_provider as semantic
from tests.test_source_policy_dispatch import _exclude, _unit

KINDS = (
    "episode",
    "semantic",
    "models",
    "positions",
    "affect",
    "relationship",
    "reflection1",
    "reflection2",
    "reflection3",
)

# Captured from the original pre-extraction adapters against the same synthetic
# inputs. Neither runtime path computes this oracle from the new shared helpers.
BEFORE_EXTRACTION_WIRE_SHA256 = {
    "episode": "8a1cb2266a639b06e564e2b1da11d13002ace954f485554e8367351154e814f6",
    "semantic": "9169f49709329be9c0ade6c7715fc83dadabdc0392edf544e76e5690e4f6e805",
    "models": "41a9eee813e2c492f12e7feb39969a098abb1ed976340d552ae42c967afbf78a",
    "positions": "7295c2db15df677c8946495093ffb6ef8cf61dc1edd7b97f1eb19bea4896f2e9",
    "affect": "043bf5d185d169aa7abc9eb95c998d7d626017bfb3461284f1cb996490571811",
    "relationship": "9a53649563a2b3654305c39a01cf8e230d5a75d38dd5d7e2340f6e4198fe44bd",
    "reflection1": "34344ff324d5d0f7b6b563e70343c54223392798eb1c39e206c6ad6acdbff160",
    "reflection2": "0214f92aa04eca98a812bf5ab23bd66306dafa84bedebe85ee0e6b242240e913",
    "reflection3": "abdb2507172bc8319d2eed58628c7ae7668a94c0f392c86fabf30b5487e70f56",
}


def case(kind: str) -> tuple[Any, dict[str, object], Any, Any, type[Exception]]:
    if kind == "episode":
        return (
            episode.request(),
            {
                "schema_version": 1,
                "should_create": True,
                "summary": "User launched the project.",
                "importance": 0.8,
                "confidence": 0.95,
                "evidence": [{"message_id": "message-user", "quote": "Я впервые запустил проект."}],
            },
            OpenAIEpisodeFormationAdapter,
            OllamaEpisodeFormationAdapter,
            EpisodeFormationProviderError,
        )
    if kind == "semantic":
        return (
            semantic.request(),
            {
                "schema_version": 1,
                "claims": [
                    {
                        "subject": "user",
                        "predicate": "name",
                        "value_kind": "text",
                        "text_value": "Алексей",
                        "number_value": None,
                        "boolean_value": None,
                        "polarity": True,
                        "claim_kind": "explicit_fact",
                        "confidence": 0.97,
                        "evidence_memory_ids": ["memory-1"],
                        "valid_from": None,
                        "valid_until": None,
                        "corrects_claim_id": None,
                    }
                ],
            },
            OpenAISemanticFormationAdapter,
            OllamaSemanticFormationAdapter,
            SemanticFormationProviderError,
        )
    if kind == "models":
        return (
            models.request(),
            models.valid_proposal(),
            OpenAIModelFormationAdapter,
            OllamaModelFormationAdapter,
            ModelFormationProviderError,
        )
    if kind == "positions":
        return (
            positions.formation_request(),
            positions.valid_proposal(),
            OpenAIPositionFormationAdapter,
            OllamaPositionFormationAdapter,
            PositionFormationProviderError,
        )
    if kind == "affect":
        return (
            affect.appraisal_request(),
            affect.valid_proposal(),
            OpenAIAffectiveAppraisalAdapter,
            OllamaAffectiveAppraisalAdapter,
            AffectiveAppraisalProviderError,
        )
    if kind == "relationship":
        return (
            relationship.request(),
            {"v": 1, "k": ["collaborative_reasoning"], "q": 94, "r": ["i", "u"]},
            OpenAIRelationshipAppraisalAdapter,
            OllamaRelationshipAppraisalAdapter,
            RelationshipAppraisalProviderError,
        )
    if kind == "reflection1":
        request, document = reflection.generation_request(), reflection.response_document()
    elif kind == "reflection2":
        request, document = reflection.v2_generation_request(), reflection.v2_response_document()
    elif kind == "reflection3":
        request, document = reflection.v3_generation_request(), reflection.v3_response_document()
    else:
        raise AssertionError("unknown test case")
    return (
        request,
        document,
        OpenAIReflectionAdapter,
        OllamaReflectionAdapter,
        ReflectionProviderError,
    )


class OpenAITransport:
    def __init__(self, document: dict[str, object], *, timeout: bool = False) -> None:
        self.document = document
        self.timeout = timeout
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        assert path == "/responses"
        assert timeout_seconds == 30
        assert max_response_bytes > 0
        self.calls.append(dict(payload))
        if self.timeout:
            raise TimeoutError("private-server-error-must-not-escape")
        return json.dumps(
            {
                "status": "completed",
                "model": payload["model"],
                "service_tier": "default",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(self.document)}],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            }
        ).encode()


class OllamaTransport:
    """Test fixture only: exercise the preserved real Ollama parser as baseline."""

    def __init__(self, document: dict[str, object]) -> None:
        self.document = document
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        assert path == "/api/chat"
        assert timeout_seconds == 30
        assert max_response_bytes > 0
        self.calls.append(dict(payload))
        return json.dumps(
            {
                "model": "local-test-model",
                "done": True,
                "message": {"role": "assistant", "content": json.dumps(self.document)},
            }
        ).encode()


def openai_adapter(kind: str, *, timeout: bool = False) -> tuple[Any, OpenAITransport, Any]:
    request, document, adapter_type, _, _ = case(kind)
    transport = OpenAITransport(copy.deepcopy(document), timeout=timeout)
    adapter = adapter_type(
        client=OpenAIStructuredClient(transport),
        model="gpt-test-model",
        timeout_seconds=30,
        max_output_tokens=768,
    )
    return adapter, transport, request


@pytest.mark.parametrize("kind", KINDS)
def test_all_role_policies_schema_and_typed_proposals_match_preserved_path(kind: str) -> None:
    new, transport, request = openai_adapter(kind)
    _, document, _, old_type, _ = case(kind)
    local = OllamaTransport(document)
    old = old_type(
        base_url="http://127.0.0.1:11434",
        model="local-test-model",
        timeout_seconds=30,
        max_output_tokens=768,
        http_client=cast(OllamaHttpClient, local),
    )
    result = asyncio.run(new.generate_structured(request))
    prior = asyncio.run(old.generate_structured(request))
    assert len(transport.calls) == len(local.calls) == 1
    current_wire, prior_wire = transport.calls[0], local.calls[0]
    original = json.dumps(
        {"messages": prior_wire["messages"], "schema": prior_wire["format"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(original).hexdigest() == BEFORE_EXTRACTION_WIRE_SHA256[kind]
    assert current_wire["input"] == prior_wire["messages"]
    assert current_wire["text"]["format"]["schema"] == normalize_schema(prior_wire["format"])
    assert current_wire["text"]["format"]["strict"] is True
    assert current_wire["store"] is False
    assert current_wire["background"] is False
    assert current_wire["reasoning"] == {"effort": "none"}
    assert "keep_alive" not in current_wire
    assert "think" not in current_wire
    assert result.provider == "openai"
    assert result.model == "gpt-test-model"
    value = "document" if kind.startswith("reflection") else "proposal"
    assert getattr(result, value) == getattr(prior, value)
    method = "appraisal_method" if kind in {"affect", "relationship"} else "formation_method"
    assert getattr(result, method) == getattr(prior, method).replace("ollama.", "openai.", 1)
    assert result.metrics.http_roundtrip_duration_ns is not None
    assert result.metrics.load_duration_ns is None


@pytest.mark.parametrize("kind", KINDS)
def test_unknown_fields_reject_with_closed_typed_failure_and_no_retry(kind: str) -> None:
    adapter, transport, request = openai_adapter(kind)
    transport.document["untrusted_private_payload"] = "must-not-escape"
    error_type = case(kind)[4]
    with pytest.raises(error_type) as failure:
        asyncio.run(adapter.generate_structured(request))
    assert failure.value.provider == "openai"  # type: ignore[attr-defined]
    assert "must-not-escape" not in str(failure.value)
    assert failure.value.__cause__ is None
    assert len(transport.calls) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_paid_transport_uncertainty_never_falls_back_or_retries(kind: str) -> None:
    adapter, transport, request = openai_adapter(kind, timeout=True)
    with pytest.raises(case(kind)[4]) as failure:
        asyncio.run(adapter.generate_structured(request))
    assert failure.value.provider == "openai"  # type: ignore[attr-defined]
    assert str(failure.value) == "transport_unavailable"
    assert failure.value.__cause__ is None
    assert len(transport.calls) == 1


@pytest.mark.parametrize("kind", ["semantic", "models"])
def test_cross_field_scalar_contract_still_rejects_multiple_values(kind: str) -> None:
    adapter, transport, request = openai_adapter(kind)
    field = "claims" if kind == "semantic" else "user_claims"
    claims = cast(list[dict[str, object]], transport.document[field])
    claims[0]["boolean_value"] = True
    with pytest.raises(case(kind)[4]):
        asyncio.run(adapter.generate_structured(request))


@pytest.mark.parametrize("kind", ["affect", "relationship"])
def test_compact_appraisals_reject_duplicate_and_unknown_refs(kind: str) -> None:
    adapter, transport, request = openai_adapter(kind)
    transport.document["r"] = ["e", "e"] if kind == "affect" else ["i", "i"]
    with pytest.raises(case(kind)[4]):
        asyncio.run(adapter.generate_structured(request))
    transport.document["r"] = ["unadmitted-source"]
    with pytest.raises(case(kind)[4]):
        asyncio.run(adapter.generate_structured(request))


def test_reflection_keeps_exact_fixed_source_and_affect_attachment_validation() -> None:
    adapter, transport, request = openai_adapter("reflection1")
    proposals = cast(list[dict[str, Any]], transport.document["proposals"])
    proposals[0]["evidence"][0]["source_id"] = "not-in-fixed-run"
    with pytest.raises(ReflectionProviderError):
        asyncio.run(adapter.generate_structured(request))
    adapter, _, request = openai_adapter("reflection2")
    without_affect = replace(
        request, sources=tuple(replace(x, affective=None) for x in request.sources)
    )
    with pytest.raises(ReflectionProviderError):
        asyncio.run(adapter.generate_structured(without_affect))


def test_previous_exchange_is_context_only_and_changes_provenance_version() -> None:
    adapter, transport, request = openai_adapter("affect")
    previous = AppraisalPreviousExchange(
        schema_version=1,
        interaction_id="prior-interaction",
        user_message_id="prior-user",
        user_content="Synthetic previous user text.",
        assistant_message_id="prior-assistant",
        assistant_content="Synthetic previous reply.",
    )
    request = replace(request, schema_version=2, previous_exchange=previous)
    result = asyncio.run(adapter.generate_structured(request))
    assert result.appraisal_method.endswith(".previous_exchange.v1")
    assert result.proposal.source_refs == (request.interaction_id,)
    data = json.loads(transport.calls[0]["input"][1]["content"])
    assert data["previous_exchange"]["interaction_id"] == "prior-interaction"
    assert data["allowed_refs"] == ["e"]


@pytest.mark.parametrize("kind", KINDS)
def test_revoked_source_never_reaches_any_structured_transport(kind: str, tmp_path: Path) -> None:
    policy = SourcePolicyRuntime(tmp_path / "trusted", "satori")
    policy.enroll(reviewed=True, new_installation=True, lineage_id="lineage")
    _exclude(policy)
    adapter, transport, request = openai_adapter(kind)
    with (
        source_dispatch_admission(lambda: _unit(policy, []), "bound-input"),
        pytest.raises(SourcePolicyError),
    ):
        asyncio.run(adapter.generate_structured(request))
    assert transport.calls == []
