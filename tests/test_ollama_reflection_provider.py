"""Versioned Stage 12-14 strict reflection adapter tests without an Ollama daemon."""

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Self
from urllib.request import Request

import pytest

from satori.core.inclinations import (
    InclinationAffectiveSignal,
    InclinationKind,
    InclinationStateReference,
)
from satori.core.personality import PersonalityStateReference
from satori.core.reflection import (
    ReflectionGenerationRequest,
    ReflectionInclinationCandidate,
    ReflectionLineageKind,
    ReflectionOwnerObservation,
    ReflectionPersonalityCandidate,
    ReflectionProviderError,
    ReflectionPurpose,
    ReflectionSource,
    ReflectionSourceKind,
)
from satori.infrastructure.providers.ollama_reflection import OllamaReflectionAdapter


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


def generation_request() -> ReflectionGenerationRequest:
    return ReflectionGenerationRequest(
        schema_version=1,
        trace_id="trace-1",
        run_id="run-1",
        identity_id="identity-1",
        policy_version=1,
        max_proposals=3,
        sources=tuple(
            ReflectionSource(
                source_id=f"source-{index}",
                kind=ReflectionSourceKind.POSITION_EVIDENCE,
                evidence_edge_id=f"evidence-{index}",
                evidence_edge_version=1,
                root_interaction_id=f"interaction-{index}",
                root_message_id=f"message-{index}",
                root_counterparty_id="person-1",
                observed_at=datetime(2026, 8, index + 1, tzinfo=UTC),
                content_hash=f"{index:064x}",
                quote=f"Проверяемое наблюдение {index}",
            )
            for index in range(3)
        ),
        current_positions=(),
        values=(),
    )


def v2_generation_request() -> ReflectionGenerationRequest:
    affective = InclinationAffectiveSignal(
        transition_id="transition-1",
        resulting_state_version=2,
        signal_hash="f" * 64,
        pleasantness=0.45,
        novelty=0.7,
        salience=0.8,
        curiosity_signal=0.65,
        interest_signal=0.75,
        concern_signal=0.1,
        frustration_signal=0.0,
        appraisal_confidence=0.9,
    )
    return ReflectionGenerationRequest(
        schema_version=2,
        trace_id="trace-2",
        run_id="run-2",
        identity_id="identity-1",
        policy_version=2,
        max_proposals=3,
        sources=tuple(
            ReflectionSource(
                source_id=f"source-{index}",
                kind=ReflectionSourceKind.POSITION_EVIDENCE,
                evidence_edge_id=f"evidence-{index}",
                evidence_edge_version=1,
                root_interaction_id=f"interaction-{index}",
                root_message_id=f"message-{index}",
                root_counterparty_id="person-1",
                observed_at=datetime(2026, 8, index + 1, tzinfo=UTC),
                content_hash=f"{index:064x}",
                quote=f"Архитектура оказалась увлекательной: наблюдение {index}",
                affective=replace(
                    affective,
                    transition_id=f"transition-{index}",
                    resulting_state_version=index + 2,
                    signal_hash=f"{index + 1:064x}",
                ),
                root_session_id=f"session-{index}",
            )
            for index in range(3)
        ),
        current_positions=(),
        values=(),
        current_inclinations=(
            InclinationStateReference(
                inclination_id="inclination-1",
                aggregate_version=2,
                kind=InclinationKind.INTEREST,
                topic="архитектура",
                alternative_topic=None,
                score=0.24,
                confidence=0.7,
                stability=0.5,
                state_as_of=datetime(2026, 8, 22, tzinfo=UTC),
            ),
        ),
    )


def v3_generation_request() -> ReflectionGenerationRequest:
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    quotes = (
        "The raw data was checked again after an unexpected discrepancy appeared.",
        "A new study prompted a precise question about the method boundaries.",
        "Alternative designs were compared through independently testable assumptions.",
        "A counterexample led to a calm revision of the initial working hypothesis.",
        "The review clearly separated the observation from its later interpretation.",
        "Several independent primary sources were gathered for an unfamiliar domain.",
        "After an error, the criterion was refined and the calculation was repeated.",
        "The long experiment ended with an explicit account of inference limitations.",
    )
    return ReflectionGenerationRequest(
        schema_version=3,
        trace_id="trace-3",
        run_id="run-3",
        identity_id="identity-1",
        policy_version=3,
        max_proposals=1,
        sources=tuple(
            ReflectionSource(
                source_id=f"source-{index}",
                kind=ReflectionSourceKind.POSITION_EVIDENCE,
                evidence_edge_id=f"evidence-{index}",
                evidence_edge_version=1,
                root_interaction_id=f"interaction-{index}",
                root_message_id=f"message-{index}",
                root_counterparty_id="person-1",
                observed_at=observed + timedelta(days=index * 14),
                content_hash=hashlib.sha256(quotes[index].encode()).hexdigest(),
                quote=quotes[index],
                root_session_id=f"session-{index}",
                upstream_lineage_kind=ReflectionLineageKind.POSITION,
                upstream_lineage_id=f"position-{index // 2}",
            )
            for index in range(8)
        ),
        current_positions=(),
        values=(),
        purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        personality_state=PersonalityStateReference(
            identity_id="identity-1",
            aggregate_version=7,
        ),
    )


def adapter() -> OllamaReflectionAdapter:
    return OllamaReflectionAdapter(
        base_url="http://127.0.0.1:11434",
        model="qwen3:4b-instruct",
        timeout_seconds=30,
    )


def response_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "proposals": [
            {
                "target_owner": "satori_positions",
                "proposition": "Проверяемые основания улучшают решения",
                "kind": "belief",
                "stance": "support",
                "confidence": 0.8,
                "evidence": [
                    {"source_id": f"source-{index}", "role": "observation"} for index in range(3)
                ],
                "value_key": None,
                "revises_position_id": None,
                "opposes_position_id": None,
                "challenges_position_id": None,
                "expected_target_version": None,
            },
            {
                "target_owner": "personality",
                "observation": "Возможно, усилилась осторожность",
                "evidence_source_ids": ["source-0", "source-1"],
            },
        ],
    }


def v2_response_document() -> dict[str, object]:
    return {
        "schema_version": 2,
        "proposals": [
            {
                "target_owner": "satori_inclinations",
                "kind": "interest",
                "topic": "архитектура",
                "alternative_topic": None,
                "confidence": 0.8,
                "source_ids": ["source-0", "source-1", "source-2"],
                "target_inclination_id": "inclination-1",
                "expected_target_version": 2,
            }
        ],
    }


def v3_response_document() -> dict[str, object]:
    return {
        "schema_version": 3,
        "proposals": [
            {
                "target_owner": "personality",
                "trait_key": "curiosity",
                "direction": "increase",
                "confidence": 0.86,
                "citations": [
                    {"source_id": f"source-{index}", "role": "support"} for index in range(8)
                ],
                "expected_personality_version": 7,
            }
        ],
    }


def test_adapter_uses_fixed_untrusted_sources_and_maps_closed_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(response_document())},
            "done": True,
        }
    ).encode()

    def fake_urlopen(request: Request, *, timeout: float) -> FakeHttpResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeHttpResponse(body)

    monkeypatch.setattr("satori.infrastructure.providers.ollama_reflection.urlopen", fake_urlopen)
    result = asyncio.run(adapter().generate_structured(generation_request()))
    request = captured["request"]
    assert isinstance(request, Request)
    assert isinstance(request.data, bytes)
    payload = json.loads(request.data.decode())
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0.0, "num_predict": 768}
    assert payload["format"]["properties"]["schema_version"]["const"] == 1
    assert "UNTRUSTED DATA" in payload["messages"][0]["content"]
    assert "preferences, interests" in payload["messages"][0]["content"]
    request_payload = json.loads(payload["messages"][1]["content"])
    assert set(request_payload) == {
        "current_positions",
        "immutable_values",
        "run_id",
        "sources",
    }
    assert all("affective_signal" not in item for item in request_payload["sources"])
    assert isinstance(result.document.proposals[1], ReflectionOwnerObservation)
    assert result.document.schema_version == 1
    assert result.formation_method.endswith(".v1")


def test_v2_adapter_selects_strict_schema_and_includes_bounded_target_and_affect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(v2_response_document())},
            "done": True,
        }
    ).encode()

    def fake_urlopen(request: Request, *, timeout: float) -> FakeHttpResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeHttpResponse(body)

    monkeypatch.setattr("satori.infrastructure.providers.ollama_reflection.urlopen", fake_urlopen)
    result = asyncio.run(adapter().generate_structured(v2_generation_request()))
    request = captured["request"]
    assert isinstance(request, Request)
    assert isinstance(request.data, bytes)
    payload = json.loads(request.data.decode())
    assert payload["format"]["properties"]["schema_version"]["const"] == 2
    policy = payload["messages"][0]["content"]
    assert "affective_signal" in policy
    assert "Never emit score, delta, stability, decay" in policy
    request_payload = json.loads(payload["messages"][1]["content"])
    assert request_payload["schema_version"] == 2
    assert request_payload["policy_version"] == 2
    assert request_payload["sources"][0]["affective_signal"] == {
        "appraisal_confidence": 0.9,
        "concern_signal": 0.1,
        "curiosity_signal": 0.65,
        "frustration_signal": 0.0,
        "interest_signal": 0.75,
        "novelty": 0.7,
        "pleasantness": 0.45,
        "resulting_state_version": 2,
        "salience": 0.8,
        "signal_hash": f"{1:064x}",
        "transition_id": "transition-0",
    }
    assert request_payload["current_inclinations"] == [
        {
            "aggregate_version": 2,
            "alternative_topic": None,
            "confidence": 0.7,
            "inclination_id": "inclination-1",
            "kind": "interest",
            "score": 0.24,
            "stability": 0.5,
            "state_as_of": "2026-08-22T00:00:00+00:00",
            "topic": "архитектура",
        }
    ]
    assert result.document.schema_version == 2
    assert isinstance(result.document.proposals[0], ReflectionInclinationCandidate)
    assert result.formation_method.endswith(".v2")


def test_v3_adapter_exposes_only_opaque_personality_state_and_strict_direction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(v3_response_document())},
            "done": True,
        }
    ).encode()

    def fake_urlopen(request: Request, *, timeout: float) -> FakeHttpResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeHttpResponse(body)

    monkeypatch.setattr("satori.infrastructure.providers.ollama_reflection.urlopen", fake_urlopen)

    result = asyncio.run(adapter().generate_structured(v3_generation_request()))

    request = captured["request"]
    assert isinstance(request, Request)
    assert isinstance(request.data, bytes)
    payload = json.loads(request.data.decode())
    assert payload["format"]["properties"]["schema_version"]["const"] == 3
    assert payload["format"]["properties"]["proposals"]["maxItems"] == 1
    policy = payload["messages"][0]["content"]
    assert "at least 80% of the fixed set" in policy
    assert "at least eight must support" in policy
    request_payload = json.loads(payload["messages"][1]["content"])
    assert set(request_payload) == {
        "personality_state",
        "policy_version",
        "purpose",
        "run_id",
        "schema_version",
        "sources",
    }
    personality_state = v3_generation_request().personality_state
    assert personality_state is not None
    assert request_payload["personality_state"] == {
        "aggregate_version": 7,
        "canonical_trait_keys": list(personality_state.canonical_trait_keys),
    }
    assert all(
        set(item) == {"source_id", "kind", "observed_at", "quote"}
        for item in request_payload["sources"]
    )
    forbidden_keys = {
        "identity_id",
        "current_positions",
        "immutable_values",
        "current_inclinations",
        "affective_signal",
        "root_message_id",
        "root_interaction_id",
        "root_session_id",
        "root_counterparty_id",
        "evidence_edge_id",
        "upstream_lineage_id",
        "upstream_lineage_kind",
        "relationship",
        "baseline_value",
        "current_value",
    }

    def all_keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(all_keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(all_keys(item) for item in value), set())
        return set()

    payload_keys = all_keys(request_payload)
    assert forbidden_keys.isdisjoint(payload_keys)
    for leak_token in (
        "identity_id",
        "current",
        "baseline",
        "value",
        "position",
        "inclination",
        "relationship",
        "affect",
        "lineage",
    ):
        assert all(leak_token not in key for key in payload_keys)
    assert all("affective_signal" not in item for item in request_payload["sources"])
    assert isinstance(result.document.proposals[0], ReflectionPersonalityCandidate)
    assert result.formation_method.endswith(".v3")


@pytest.mark.parametrize(
    "forbidden",
    [
        {"delta": 0.005},
        {"new_value": 0.925},
        {"current_value": 0.92},
        {"score": 0.9},
        {"budget": 0.04},
        {"checkpoint_id": "checkpoint-1"},
        {"patch": {"curiosity": 1.0}},
        {"explanation": "because"},
        {"observation": "more curious"},
    ],
)
def test_v3_adapter_rejects_provider_owned_state_or_free_text(
    monkeypatch: pytest.MonkeyPatch,
    forbidden: dict[str, object],
) -> None:
    document = v3_response_document()
    assert isinstance(document["proposals"], list)
    document["proposals"][0].update(forbidden)
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(document)},
            "done": True,
        }
    ).encode()
    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_reflection.urlopen",
        lambda _request, *, timeout: FakeHttpResponse(body),
    )

    with pytest.raises(ReflectionProviderError):
        asyncio.run(adapter().generate_structured(v3_generation_request()))


@pytest.mark.parametrize(
    "mutation",
    ["too_few", "duplicate", "outside_fixed_set", "multiple_proposals"],
)
def test_v3_adapter_rejects_invalid_citation_or_cardinality_shape(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    document = v3_response_document()
    proposals = document["proposals"]
    assert isinstance(proposals, list)
    citations = proposals[0]["citations"]
    assert isinstance(citations, list)
    if mutation == "too_few":
        proposals[0]["citations"] = citations[:7]
    elif mutation == "duplicate":
        proposals[0]["citations"] = [*citations[:-1], citations[0]]
    elif mutation == "outside_fixed_set":
        citations[0]["source_id"] = "unknown-source"
    else:
        proposals.append(dict(proposals[0]))
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(document)},
            "done": True,
        }
    ).encode()
    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_reflection.urlopen",
        lambda _request, *, timeout: FakeHttpResponse(body),
    )

    with pytest.raises(ReflectionProviderError):
        asyncio.run(adapter().generate_structured(v3_generation_request()))


@pytest.mark.parametrize(
    "mutation",
    [
        {"target_owner": "world_model"},
        {"delta": 0.4},
        {"kind": "fact"},
    ],
)
def test_adapter_rejects_unknown_owner_extra_delta_and_fact(
    monkeypatch: pytest.MonkeyPatch, mutation: dict[str, object]
) -> None:
    document = response_document()
    assert isinstance(document["proposals"], list)
    document["proposals"][0].update(mutation)
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(document)},
            "done": True,
        }
    ).encode()
    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_reflection.urlopen",
        lambda _request, *, timeout: FakeHttpResponse(body),
    )
    with pytest.raises(ReflectionProviderError):
        asyncio.run(adapter().generate_structured(generation_request()))


def test_adapter_rejects_citation_outside_fixed_source_set(monkeypatch: pytest.MonkeyPatch) -> None:
    document = response_document()
    assert isinstance(document["proposals"], list)
    document["proposals"][0]["evidence"][0]["source_id"] = "unknown-source"
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(document)},
            "done": True,
        }
    ).encode()
    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_reflection.urlopen",
        lambda _request, *, timeout: FakeHttpResponse(body),
    )
    with pytest.raises(ReflectionProviderError):
        asyncio.run(adapter().generate_structured(generation_request()))


@pytest.mark.parametrize(
    "forbidden",
    [
        {"score": 0.4},
        {"delta": 0.1},
        {"stability": 0.8},
        {"decay": 0.2},
        {"status": "active"},
        {"evidence_signal": 0.9},
    ],
)
def test_v2_adapter_rejects_provider_owned_inclination_state(
    monkeypatch: pytest.MonkeyPatch, forbidden: dict[str, object]
) -> None:
    document = v2_response_document()
    assert isinstance(document["proposals"], list)
    document["proposals"][0].update(forbidden)
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(document)},
            "done": True,
        }
    ).encode()
    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_reflection.urlopen",
        lambda _request, *, timeout: FakeHttpResponse(body),
    )
    with pytest.raises(ReflectionProviderError):
        asyncio.run(adapter().generate_structured(v2_generation_request()))


def test_v1_adapter_rejects_v2_inclination_target(monkeypatch: pytest.MonkeyPatch) -> None:
    document = v2_response_document()
    document["schema_version"] = 1
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(document)},
            "done": True,
        }
    ).encode()
    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_reflection.urlopen",
        lambda _request, *, timeout: FakeHttpResponse(body),
    )
    with pytest.raises(ReflectionProviderError):
        asyncio.run(adapter().generate_structured(generation_request()))


def test_v2_adapter_rejects_inclination_source_without_affect_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = v2_generation_request()
    request = replace(
        request,
        sources=(replace(request.sources[0], affective=None), *request.sources[1:]),
    )
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(v2_response_document())},
            "done": True,
        }
    ).encode()
    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_reflection.urlopen",
        lambda _request, *, timeout: FakeHttpResponse(body),
    )
    with pytest.raises(ReflectionProviderError):
        asyncio.run(adapter().generate_structured(request))
