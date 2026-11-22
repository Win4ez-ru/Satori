"""Offline wire/contract checks; canned responses are not character evidence."""

import asyncio
import copy
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal

import pytest

from satori.core.affect import (
    AffectiveAppraisalRequest,
    AppraisalEpisodicContext,
    AppraisalFastState,
    AppraisalMoodState,
    AppraisalPreviousExchange,
    AppraisalTrait,
    AppraisalValue,
)
from satori.core.conversation import ConversationMessageRole
from satori.core.embedding import EmbeddingRequest, InvalidEmbeddingResponse
from satori.core.episode import EpisodeFormationRequest, EpisodeSourceMessage
from satori.core.relationship import RelationshipAppraisalRequest
from satori.core.semantic import (
    SemanticFormationRequest,
    SemanticSourceEvidence,
    SemanticSourceMemory,
)
from satori.infrastructure.providers.openai_http import OpenAITransportError
from tests.character_closeout_api_adapters import (
    CLOSEOUT_EMBEDDING_MODEL,
    CLOSEOUT_MODEL,
    CloseoutAffectiveAppraisalAdapter,
    CloseoutAPIError,
    CloseoutEmbeddingAdapter,
    CloseoutEpisodeFormationAdapter,
    CloseoutForegroundTransport,
    CloseoutRelationshipAppraisalAdapter,
    CloseoutSemanticFormationAdapter,
    CloseoutUsage,
)

NOW = datetime(2026, 10, 16, tzinfo=UTC)


def offline_api_response(
    document: object = None, *, text: str | None = None, output_tokens: int = 23
) -> dict[str, Any]:
    """An explicitly synthetic Responses envelope, available to offline harness checks."""

    return {
        "model": CLOSEOUT_MODEL,
        "status": "completed",
        "service_tier": "default",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text if text is not None else json.dumps(document),
                    }
                ],
            }
        ],
        "usage": {
            "input_tokens": 123,
            "output_tokens": output_tokens,
            "total_tokens": 123 + output_tokens,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


class RecordingGuard:
    def __init__(self, *, deny: bool = False) -> None:
        self.deny = deny
        self.reservations: list[tuple[str, str, Mapping[str, Any]]] = []
        self.settlements: list[tuple[str, CloseoutUsage | None, str | None, str]] = []

    def reserve(self, operation: str, model: str, payload: Mapping[str, Any]) -> str:
        if self.deny:
            raise RuntimeError("offline budget denied")
        self.reservations.append((operation, model, payload))
        return f"reservation-{len(self.reservations)}"

    def settle(
        self,
        reservation_id: str,
        *,
        usage: CloseoutUsage | None,
        response_model: str | None,
        outcome: str,
    ) -> None:
        self.settlements.append((reservation_id, usage, response_model, outcome))


class ScriptedCloseoutTransport:
    """No sockets: route realistic request shapes to visibly synthetic documents.

    Positive memory proposals cite actual supplied user evidence. This exercises owner
    plumbing without claiming the API would choose, understand, or remember that content.
    """

    def __init__(self, body: bytes | None = None, *, error: Exception | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        assert timeout_seconds > 0
        assert max_response_bytes > 0
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if self.body is not None:
            return self.body
        if path == "/embeddings":
            return json.dumps(
                {
                    "object": "list",
                    "model": CLOSEOUT_EMBEDDING_MODEL,
                    "data": [{"object": "embedding", "index": 0, "embedding": [1.0] * 768}],
                    "usage": {"prompt_tokens": 12, "total_tokens": 12},
                }
            ).encode()
        assert path == "/responses"
        schema_name = payload.get("text", {}).get("format", {}).get("name", "foreground")
        source = json.loads(payload["input"][1]["content"]) if schema_name != "foreground" else {}
        if "affect" in schema_name:
            document: object = {"v": 2, "k": ["neutral_social"], "q": 90, "r": ["e"]}
        elif "relationship" in schema_name:
            document = {"v": 1, "k": ["neutral_contact"], "q": 90, "r": ["i", "u"]}
        elif "episode" in schema_name:
            user = next(item for item in source["messages"] if item["role"] == "user")
            quote = user["content"][:500]
            document = {
                "schema_version": 1,
                "should_create": True,
                "summary": quote,
                "importance": 0.7,
                "confidence": 0.9,
                "evidence": [{"message_id": user["message_id"], "quote": quote}],
            }
        elif "semantic" in schema_name:
            matching = [item for item in source["memories"] if "Маяк" in item["summary"]]
            claims = []
            if matching:
                claims.append(
                    {
                        "subject": "user",
                        "predicate": "works_on_project",
                        "value_kind": "text",
                        "text_value": "Маяк",
                        "number_value": None,
                        "boolean_value": None,
                        "polarity": True,
                        "claim_kind": "explicit_fact",
                        "confidence": 0.9,
                        "evidence_memory_ids": [source["source_memory_id"]],
                        "valid_from": None,
                        "valid_until": None,
                        "corrects_claim_id": None,
                    }
                )
            document = {"schema_version": 1, "claims": claims}
        else:
            return json.dumps(offline_api_response(text="Давай посмотрим.")).encode()
        return json.dumps(offline_api_response(document)).encode()


def affect_request() -> AffectiveAppraisalRequest:
    return AffectiveAppraisalRequest(
        schema_version=1,
        trace_id="trace-affect",
        interaction_id="interaction-1",
        appraised_at=NOW,
        user_content="Я работаю над проектом Маяк.",
        traits=(AppraisalTrait("curiosity", 0.8),),
        values=(AppraisalValue("truth", 0.9, "Точность суждений."),),
        fast_state=AppraisalFastState(0.0, 0.12, 0.08, 0.18, 0.16, 0.05, 0.08, 0.04, 0.55),
        mood_state=AppraisalMoodState(0.0, 0.3, 0.1),
    )


def relationship_request() -> RelationshipAppraisalRequest:
    return RelationshipAppraisalRequest(1, "interaction-1", "user-1", "Привет.", NOW, "trace-rel")


def episode_request() -> EpisodeFormationRequest:
    return EpisodeFormationRequest(
        1,
        "trace-episode",
        "interaction-1",
        NOW,
        1,
        (
            EpisodeSourceMessage(
                "user-1", ConversationMessageRole.USER, "Я работаю над проектом Маяк."
            ),
            EpisodeSourceMessage("assistant-1", ConversationMessageRole.ASSISTANT, "Любопытно."),
        ),
    )


def semantic_request() -> SemanticFormationRequest:
    return SemanticFormationRequest(
        1,
        "trace-semantic",
        "memory-1",
        1,
        4,
        (
            SemanticSourceMemory(
                "memory-1",
                "interaction-1",
                NOW,
                "Пользователь работает над проектом Маяк.",
                (SemanticSourceEvidence("evidence-1", "user-1", "Я работаю над проектом Маяк."),),
            ),
        ),
    )


def test_structured_ports_reuse_contracts_and_charge_once() -> None:
    transport = ScriptedCloseoutTransport()
    guard = RecordingGuard()
    affect = asyncio.run(
        CloseoutAffectiveAppraisalAdapter(
            CLOSEOUT_MODEL, 30.0, 512, transport, guard
        ).generate_structured(affect_request())
    )
    relation = asyncio.run(
        CloseoutRelationshipAppraisalAdapter(
            CLOSEOUT_MODEL, 30.0, 256, transport, guard
        ).generate_structured(relationship_request())
    )
    episode = asyncio.run(
        CloseoutEpisodeFormationAdapter(
            CLOSEOUT_MODEL, 30.0, 768, transport, guard
        ).generate_structured(episode_request())
    )
    semantic = asyncio.run(
        CloseoutSemanticFormationAdapter(
            CLOSEOUT_MODEL, 30.0, 1024, transport, guard
        ).generate_structured(semantic_request())
    )
    assert affect.provider == relation.provider == episode.provider == semantic.provider == "openai"
    assert affect.proposal.reason_codes == ("neutral_social",)
    assert affect.proposal.source_refs == ("interaction-1",)
    assert relation.proposal.source_refs == ("interaction-1", "user-1")
    assert episode.proposal.evidence[0].message_id == "user-1"
    assert semantic.proposal.claims[0].evidence_memory_ids == ("memory-1",)
    assert "evaluation.openai" in affect.appraisal_method
    assert "evaluation.openai" in episode.formation_method
    assert affect.metrics is not None
    assert affect.metrics.load_duration_ns is None
    assert affect.metrics.prompt_eval_count == 123
    assert affect.metrics.reasoning_output_tokens == 0
    assert [item[0] for item in guard.reservations] == [
        "affect",
        "relationship",
        "episode",
        "semantic",
    ]
    assert len(transport.calls) == len(guard.settlements) == 4
    assert all(item[3] == "completed" for item in guard.settlements)
    assert all(item[1] == CloseoutUsage(123, 23, 0, 0) for item in guard.settlements)
    for path, payload in transport.calls:
        assert path == "/responses"
        assert payload["reasoning"] == {"effort": "none"}
        assert payload["store"] is False
        assert payload["service_tier"] == "default"
        assert "tools" not in payload
        assert payload["text"]["format"]["strict"] is True
        schema = payload["text"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_previous_pair_stays_untrusted_context_not_new_source() -> None:
    request = replace(
        affect_request(),
        schema_version=2,
        previous_exchange=AppraisalPreviousExchange(
            1, "prior-interaction", "prior-user", "Ты ошиблась.", "prior-assistant", "Смотрю."
        ),
        episodic_context=(AppraisalEpisodicContext("memory-1", "Раньше обсуждали это.", 0.5, 0.9),),
    )
    transport = ScriptedCloseoutTransport()
    response = asyncio.run(
        CloseoutAffectiveAppraisalAdapter(
            CLOSEOUT_MODEL, 30.0, 512, transport, RecordingGuard()
        ).generate_structured(request)
    )
    payload = transport.calls[0][1]
    source = json.loads(payload["input"][1]["content"])
    assert source["previous_exchange"]["interaction_id"] == "prior-interaction"
    assert source["allowed_refs"] == ["e", "m0"]
    assert response.appraisal_method.endswith("previous_exchange.v1")


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_usage",
        "negative_usage",
        "bool_usage",
        "inconsistent_total",
        "over_cap",
        "unexpected_model",
        "unexpected_tier",
        "incomplete",
        "refusal",
        "extra_output",
        "hidden_reasoning",
        "reasoning_tokens",
        "cache_over_input",
        "cache_sum_over_input",
        "unknown_ref",
        "missing_event_ref",
        "duplicate_category",
        "unknown_category",
        "extra_proposal_key",
        "duplicate_json_key",
    ],
)
def test_invalid_responses_stop_and_settle_once(mutation: str) -> None:
    document: dict[str, Any] = {"v": 2, "k": ["neutral_social"], "q": 90, "r": ["e"]}
    response = offline_api_response(document)
    if mutation == "missing_usage":
        del response["usage"]
    elif mutation == "negative_usage":
        response["usage"]["input_tokens"] = -1
    elif mutation == "bool_usage":
        response["usage"]["input_tokens"] = True
    elif mutation == "inconsistent_total":
        response["usage"]["total_tokens"] = 999
    elif mutation == "over_cap":
        response["usage"]["output_tokens"] = 513
        response["usage"]["total_tokens"] = 636
    elif mutation == "unexpected_model":
        response["model"] = "another-model"
    elif mutation == "unexpected_tier":
        response["service_tier"] = "flex"
    elif mutation == "incomplete":
        response["status"] = "incomplete"
    elif mutation == "refusal":
        response["output"][0]["content"] = [{"type": "refusal", "refusal": "declined"}]
    elif mutation == "extra_output":
        response["output"].append(copy.deepcopy(response["output"][0]))
    elif mutation == "hidden_reasoning":
        response["output"].insert(0, {"type": "reasoning", "summary": []})
    elif mutation == "reasoning_tokens":
        response["usage"]["output_tokens_details"]["reasoning_tokens"] = 1
    elif mutation == "cache_over_input":
        response["usage"]["input_tokens_details"]["cached_tokens"] = 124
    elif mutation == "cache_sum_over_input":
        response["usage"]["input_tokens_details"] = {"cached_tokens": 100, "cache_write_tokens": 24}
    elif mutation == "unknown_ref":
        document["r"] = ["e", "prior-user"]
    elif mutation == "missing_event_ref":
        document["r"] = ["m0"]
    elif mutation == "duplicate_category":
        document["k"] = ["neutral_social", "neutral_social"]
    elif mutation == "unknown_category":
        document["k"] = ["offended_forever"]
    elif mutation == "extra_proposal_key":
        document["mood"] = "angry"
    if mutation in {
        "unknown_ref",
        "missing_event_ref",
        "duplicate_category",
        "unknown_category",
        "extra_proposal_key",
    }:
        response["output"][0]["content"][0]["text"] = json.dumps(document)
    if mutation == "duplicate_json_key":
        response["output"][0]["content"][0]["text"] = (
            '{"v":2,"v":1,"k":["neutral_social"],"q":90,"r":["e"]}'
        )
    transport = ScriptedCloseoutTransport(json.dumps(response).encode())
    guard = RecordingGuard()
    with pytest.raises(CloseoutAPIError):
        asyncio.run(
            CloseoutAffectiveAppraisalAdapter(
                CLOSEOUT_MODEL, 30.0, 512, transport, guard
            ).generate_structured(affect_request())
        )
    assert len(transport.calls) == len(guard.reservations) == len(guard.settlements) == 1
    assert guard.settlements[0][3] != "completed"


def test_budget_denial_prevents_any_transport_and_transport_failure_never_retries() -> None:
    transport = ScriptedCloseoutTransport()
    guard = RecordingGuard(deny=True)
    with pytest.raises(RuntimeError, match="budget denied"):
        asyncio.run(
            CloseoutAffectiveAppraisalAdapter(
                CLOSEOUT_MODEL, 30.0, 512, transport, guard
            ).generate_structured(affect_request())
        )
    assert not transport.calls
    assert not guard.settlements
    failed = ScriptedCloseoutTransport(error=OpenAITransportError("private internal detail"))
    guard = RecordingGuard()
    with pytest.raises(CloseoutAPIError, match="no retry") as caught:
        asyncio.run(
            CloseoutAffectiveAppraisalAdapter(
                CLOSEOUT_MODEL, 30.0, 512, failed, guard
            ).generate_structured(affect_request())
        )
    assert "private internal detail" not in str(caught.value)
    assert len(failed.calls) == len(guard.settlements) == 1
    assert guard.settlements[0][1] is None


def test_embedding_real_space_and_purpose_are_checked_and_accounted() -> None:
    transport = ScriptedCloseoutTransport()
    guard = RecordingGuard()
    operations: tuple[Literal["embedding_query", "embedding_index"], ...] = (
        "embedding_query",
        "embedding_index",
    )
    for operation in operations:
        adapter = CloseoutEmbeddingAdapter(
            CLOSEOUT_EMBEDDING_MODEL, 768, 1, 30.0, transport, guard, operation
        )
        result = asyncio.run(adapter.embed(EmbeddingRequest(1, "trace-embed", ("Проект Маяк.",))))
        assert result.space.provider == "openai"
        assert result.space.dimensions == 768
        assert len(result.vectors[0]) == 768
        assert result.metrics is not None
        assert result.metrics.prompt_eval_count == 12
    assert [entry[0] for entry in guard.reservations] == ["embedding_query", "embedding_index"]
    assert all(entry[1] == CloseoutUsage(12, 0, 0, 0) for entry in guard.settlements)
    assert all(entry[3] == "completed" for entry in guard.settlements)


@pytest.mark.parametrize(
    "mutation", ["model", "dimensions", "nan", "zero", "index", "count", "usage"]
)
def test_invalid_embedding_never_enters_vector_space(mutation: str) -> None:
    body: dict[str, Any] = {
        "object": "list",
        "model": CLOSEOUT_EMBEDDING_MODEL,
        "data": [{"object": "embedding", "index": 0, "embedding": [1.0] * 768}],
        "usage": {"prompt_tokens": 12, "total_tokens": 12},
    }
    if mutation == "model":
        body["model"] = "another-space"
    elif mutation == "dimensions":
        body["data"][0]["embedding"] = [1.0] * 767
    elif mutation == "nan":
        body["data"][0]["embedding"][0] = float("nan")
    elif mutation == "zero":
        body["data"][0]["embedding"] = [0.0] * 768
    elif mutation == "index":
        body["data"][0]["index"] = 1
    elif mutation == "count":
        body["data"].append(copy.deepcopy(body["data"][0]))
    else:
        body["usage"]["total_tokens"] = 13
    guard = RecordingGuard()
    adapter = CloseoutEmbeddingAdapter(
        CLOSEOUT_EMBEDDING_MODEL,
        768,
        1,
        30.0,
        ScriptedCloseoutTransport(json.dumps(body).encode()),
        guard,
        "embedding_query",
    )
    with pytest.raises((CloseoutAPIError, InvalidEmbeddingResponse)):
        asyncio.run(adapter.embed(EmbeddingRequest(1, "trace", ("Маяк.",))))
    assert len(guard.settlements) == 1
    assert guard.settlements[0][3] != "completed"


def test_foreground_transport_preserves_request_and_validates_paid_attempt() -> None:
    payload: dict[str, Any] = {
        "model": CLOSEOUT_MODEL,
        "input": [{"role": "user", "content": "Привет."}],
        "max_output_tokens": 768,
        "reasoning": {"effort": "none"},
        "store": False,
        "service_tier": "default",
    }
    raw = ScriptedCloseoutTransport(json.dumps(offline_api_response(text="Привет.")).encode())
    guard = RecordingGuard()
    transport = CloseoutForegroundTransport(raw, guard)
    result = transport.post_json(
        "/responses", payload, timeout_seconds=30.0, max_response_bytes=10000
    )
    assert json.loads(result)["output"][0]["content"][0]["text"] == "Привет."
    assert raw.calls == [("/responses", payload)]
    assert guard.reservations[0][0] == "foreground"
    assert guard.settlements[0][3] == "completed"
    payload["tools"] = [{"type": "web_search"}]
    with pytest.raises(CloseoutAPIError):
        transport.post_json("/responses", payload, timeout_seconds=30.0, max_response_bytes=10000)
    assert len(raw.calls) == 1
