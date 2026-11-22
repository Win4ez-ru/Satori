"""Offline wire/schema/usage tests; no account access or generated output acceptance."""

import copy
import json
from collections.abc import Mapping
from typing import Any, cast

import pytest

from satori.infrastructure.providers.openai_http import OpenAIHttpStatusError, OpenAITransportError
from satori.infrastructure.providers.openai_structured import (
    MAX_STRUCTURED_RESPONSE_BYTES,
    OpenAIStructuredClient,
    OpenAIStructuredError,
    StructuredResult,
    normalize_schema,
)

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "version": {"type": "integer", "const": 1},
        "summary": {
            "anyOf": [{"type": "string", "minLength": 1, "maxLength": 40}, {"type": "null"}],
            "default": None,
        },
        "refs": {"type": "array", "items": {"type": "string"}, "maxItems": 3, "uniqueItems": True},
    },
    "required": ["version", "refs"],
    "additionalProperties": False,
}
DOCUMENT = {"version": 1, "summary": None, "refs": ["r1"]}


def envelope(document: object = DOCUMENT) -> dict[str, object]:
    return {
        "model": "gpt-6-luna",
        "status": "completed",
        "service_tier": "default",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(document),
                    }
                ],
            }
        ],
    }


class Transport:
    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self.body = json.dumps(envelope() if response is None else response).encode()
        self.error = error
        self.calls: list[tuple[str, Mapping[str, Any], float, int]] = []

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        self.calls.append((path, copy.deepcopy(payload), timeout_seconds, max_response_bytes))
        if self.error is not None:
            raise self.error
        return self.body


def generate(transport: Transport, schema: dict[str, object] = SCHEMA) -> StructuredResult:
    return OpenAIStructuredClient(transport).generate(
        model="gpt-6-luna",
        messages=[
            {"role": "system", "content": "Owned policy."},
            {"role": "user", "content": "untrusted source"},
        ],
        schema=schema,
        schema_name="episode_v1",
        max_output_tokens=128,
        timeout_seconds=3,
    )


def test_stateless_structured_request_no_tools_and_exact_unmodified_input() -> None:
    transport = Transport()
    result = OpenAIStructuredClient(transport).generate(
        model="gpt-6-luna",
        messages=[{"role": "user", "content": "  exact source\n"}],
        schema=SCHEMA,
        schema_name="v1",
        max_output_tokens=128,
        timeout_seconds=3,
    )
    path, payload, timeout, maximum = transport.calls[0]
    assert (path, timeout, maximum) == ("/responses", 3, MAX_STRUCTURED_RESPONSE_BYTES)
    assert payload["input"] == [{"role": "user", "content": "  exact source\n"}]
    assert payload["store"] is payload["background"] is payload["stream"] is False
    assert payload["service_tier"] == "default"
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["truncation"] == "disabled"
    assert "tools" not in payload
    assert "previous_response_id" not in payload
    assert "temperature" not in payload
    assert json.loads(result.content) == DOCUMENT
    assert result.usage is None
    assert result.metrics.load_duration_ns is result.metrics.total_duration_ns is None
    assert result.metrics.reasoning_output_tokens is None
    assert "untrusted source" not in repr(result)


def test_normalization_retains_nullable_bounds_and_original_unchanged() -> None:
    before = copy.deepcopy(SCHEMA)
    normalized = normalize_schema(SCHEMA)
    properties = cast(dict[str, dict[str, object]], normalized["properties"])
    assert normalized["required"] == ["version", "summary", "refs"]
    assert "default" not in properties["summary"]
    assert (
        properties["summary"]["anyOf"]
        == cast(dict[str, dict[str, object]], before["properties"])["summary"]["anyOf"]
    )
    assert "uniqueItems" not in properties["refs"]
    assert properties["refs"]["maxItems"] == 3
    assert before == SCHEMA


@pytest.mark.parametrize(
    "document",
    [
        {"version": 1, "summary": None, "refs": ["r1", "r1"]},
        {"version": 1, "refs": []},
        {"version": 1, "summary": "x" * 41, "refs": []},
        {"version": True, "summary": None, "refs": []},
        {"version": 1, "summary": None, "refs": [], "unexpected": "ignored?"},
        {"version": 1, "summary": None, "refs": ["r1"] * 4},
        [],
    ],
)
def test_local_and_wire_constraints_both_enforced(document: object) -> None:
    with pytest.raises(OpenAIStructuredError, match="response_invalid"):
        generate(Transport(envelope(document)))


def test_safe_discriminated_union_conversion_with_original_exclusivity() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "item": {
                "discriminator": {"propertyName": "kind"},
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"kind": {"const": "a", "type": "string"}},
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {"kind": {"const": "b", "type": "string"}},
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                ],
            }
        },
        "required": ["item"],
        "additionalProperties": False,
    }
    normalized = normalize_schema(schema)
    item = cast(dict[str, dict[str, object]], normalized["properties"])["item"]
    assert "oneOf" not in item
    assert "discriminator" not in item
    assert "anyOf" in item
    assert generate(Transport(envelope({"item": {"kind": "a"}})), schema) is not None
    with pytest.raises(OpenAIStructuredError, match="response_invalid"):
        generate(Transport(envelope({"item": {"kind": "c"}})), schema)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "items": {"type": "string"}},
        {"type": "object", "additionalProperties": {"type": "string"}},
        {"type": "object", "allOf": [{"type": "object"}]},
        {"type": "object", "properties": {"value": {"oneOf": [{"type": "string"}]}}},
        {"type": "object", "properties": {"value": {"$ref": "https://private.example/schema"}}},
    ],
)
def test_unsupported_or_unsafe_schema_refused_before_transport(schema: dict[str, object]) -> None:
    transport = Transport()
    with pytest.raises(OpenAIStructuredError, match="unsupported_schema"):
        generate(transport, schema)
    assert not transport.calls


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "queued", "in_progress"])
def test_only_completed_response_is_admitted(status: str) -> None:
    response = envelope() | {"status": status}
    with pytest.raises(OpenAIStructuredError, match="response_incomplete"):
        generate(Transport(response))


@pytest.mark.parametrize(
    "output",
    [
        [],
        [{"type": "function_call", "arguments": "secret"}],
        [{"type": "message", "role": "user", "content": []}],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "{}"},
                    {"type": "output_text", "text": "{}"},
                ],
            }
        ],
        [{"type": "reasoning", "summary": [{"text": "private reasoning"}]}],
    ],
)
def test_missing_ambiguous_tool_or_reasoning_content_refused(output: object) -> None:
    with pytest.raises(OpenAIStructuredError, match="response_invalid"):
        generate(Transport(envelope() | {"output": output}))


def test_refusal_is_a_closed_error_not_a_proposal() -> None:
    output = [
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "refusal", "refusal": "private refusal content"},
            ],
        }
    ]
    with pytest.raises(OpenAIStructuredError, match=r"^refused$"):
        generate(Transport(envelope() | {"output": output}))


@pytest.mark.parametrize("patch", [{"model": "other"}, {"service_tier": "priority"}])
def test_exact_model_and_default_tier_required(patch: dict[str, object]) -> None:
    with pytest.raises(OpenAIStructuredError, match="response_invalid"):
        generate(Transport(envelope() | patch))


def test_honest_usage_reasoning_and_partial_cache_metadata() -> None:
    usage = {
        "input_tokens": 50,
        "output_tokens": 20,
        "output_tokens_details": {"reasoning_tokens": 3},
        "input_tokens_details": {"cached_tokens": 10},
    }
    result = OpenAIStructuredClient(Transport(envelope() | {"usage": usage})).generate(
        model="gpt-6-luna",
        messages=[{"role": "user", "content": "source"}],
        schema=SCHEMA,
        schema_name="v1",
        max_output_tokens=128,
        timeout_seconds=3,
    )
    assert result.usage is not None
    assert result.usage.input_tokens == 50
    assert result.usage.output_tokens == 20
    assert result.usage.cached_input_tokens is result.usage.cache_write_input_tokens is None
    assert result.metrics.reasoning_output_tokens == 3
    assert result.metrics.visible_output_tokens == 17
    assert result.metrics.prompt_eval_duration_ns is result.metrics.eval_duration_ns is None


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -1},
        {"input_tokens": True},
        {"output_tokens": 129},
        {"output_tokens": 2, "output_tokens_details": {"reasoning_tokens": 3}},
        {"input_tokens": 2, "input_tokens_details": {"cached_tokens": 3}},
    ],
)
def test_inconsistent_usage_rejected_without_inventing_counts(usage: dict[str, object]) -> None:
    with pytest.raises(OpenAIStructuredError, match="response_invalid"):
        generate(Transport(envelope() | {"usage": usage}))


@pytest.mark.parametrize(
    "body",
    [
        b'{"model":"a","model":"b"}',
        b"\xff",
        b'{"x":NaN}',
        b'{"x":1e999}',
        b"x" * (MAX_STRUCTURED_RESPONSE_BYTES + 1),
    ],
)
def test_strict_bounded_json_failures_are_content_free(body: bytes) -> None:
    transport = Transport()
    transport.body = body
    with pytest.raises(OpenAIStructuredError, match=r"^response_invalid$"):
        generate(transport)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (OpenAIHttpStatusError(429), "rate_or_quota_limited"),
        (OpenAIHttpStatusError(401), "credentials_rejected"),
        (OpenAIHttpStatusError(404), "resource_not_found"),
        (OpenAIHttpStatusError(400), "request_rejected"),
        (OpenAIHttpStatusError(503), "transport_unavailable"),
        (OpenAITransportError("private transport detail"), "transport_unavailable"),
    ],
)
def test_transport_failure_codes_are_closed(error: Exception, code: str) -> None:
    with pytest.raises(OpenAIStructuredError, match=f"^{code}$"):
        generate(Transport(error=error))
