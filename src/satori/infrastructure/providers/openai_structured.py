"""Bounded stateless Responses transport for existing typed proposal adapters.

The caller still owns role-specific Pydantic/domain validation. Schema adaptation
changes only the provider dialect: original constraints are checked locally, and
no generated text, evidence, prompt or canonical state is rewritten here.
"""

import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.core.conversation import ConversationUsage
from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.infrastructure.providers.openai_http import (
    OpenAIHttpStatusError,
    OpenAITransportError,
)

MAX_STRUCTURED_RESPONSE_BYTES = 1_000_000
MAX_STRUCTURED_REQUEST_BYTES = 1_000_000
MAX_SCHEMA_BYTES = 120_000


class OpenAIStructuredTransport(Protocol):
    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes: ...


class OpenAIStructuredError(Exception):
    """Closed safe codes only, never exception strings or response fragments."""

    def __init__(
        self,
        code: Literal[
            "invalid_request",
            "unsupported_schema",
            "transport_unavailable",
            "rate_or_quota_limited",
            "credentials_rejected",
            "resource_not_found",
            "request_rejected",
            "response_invalid",
            "response_incomplete",
            "refused",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class StructuredResult:
    content: str = field(repr=False)
    model: str
    metrics: ProviderExecutionMetrics
    usage: ConversationUsage | None


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _constant(_: str) -> object:
    raise ValueError("nonfinite_json")


def bounded_json(body: bytes, maximum: int) -> object:
    """Strict UTF-8/finite/unique-key JSON with finite depth and node count."""
    if type(body) is not bytes or len(body) > maximum:
        raise ValueError("json_limit")
    value: object = json.loads(
        body.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant
    )
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 48 or nodes > 50_000:
            raise ValueError("json_limit")
        if type(item) is float and not math.isfinite(item):
            raise ValueError("nonfinite_json")
        if type(item) is str:
            item.encode("utf-8")
        elif type(item) is dict:
            for key, child in item.items():
                key.encode("utf-8")
                visit(child, depth + 1)
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)
    return value


def _reference(root: dict[str, object], ref: object) -> dict[str, object]:
    if type(ref) is not str or (ref != "#" and not ref.startswith("#/$defs/")):
        raise ValueError("unsupported_reference")
    target: object = root
    for component in ref[2:].split("/") if ref != "#" else []:
        if type(target) is not dict:
            raise ValueError("unsupported_reference")
        target = target[component.replace("~1", "/").replace("~0", "~")]
    if type(target) is not dict:
        raise ValueError("unsupported_reference")
    return cast(dict[str, object], target)


def normalize_schema(schema: dict[str, object]) -> dict[str, object]:
    """Adapt the supported local schema dialect without dropping local validation.

    Nullable defaults stay nullable, all object keys become required on the wire,
    uniqueItems is enforced locally, and discriminated oneOf is converted to anyOf
    only after proving its finite discriminator values are disjoint.
    """
    try:
        root = cast(
            dict[str, object],
            bounded_json(
                json.dumps(schema, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                MAX_SCHEMA_BYTES,
            ),
        )
        if type(root) is not dict or root.get("type") != "object" or "anyOf" in root:
            raise ValueError("root_object_required")
        properties_count = 0

        def convert(node: dict[str, object], depth: int) -> dict[str, object]:
            nonlocal properties_count
            if depth > 32:
                raise ValueError("schema_depth")
            forbidden = {
                "allOf",
                "not",
                "if",
                "then",
                "else",
                "patternProperties",
                "dependentRequired",
                "dependentSchemas",
                "prefixItems",
            }
            if forbidden & node.keys():
                raise ValueError("unsupported_schema")
            if "$ref" in node:
                _reference(root, node["$ref"])
            result = {
                key: item
                for key, item in node.items()
                if key not in {"default", "discriminator", "uniqueItems"}
            }
            for name in ("properties", "$defs"):
                mapping = node.get(name)
                if mapping is not None:
                    if type(mapping) is not dict or any(
                        type(v) is not dict for v in mapping.values()
                    ):
                        raise ValueError("schema_mapping")
                    result[name] = {
                        key: convert(value, depth + 1) for key, value in mapping.items()
                    }
            if node.get("type") == "object" or "properties" in node:
                properties = node.get("properties", {})
                if (
                    type(properties) is not dict
                    or node.get("additionalProperties", False) is not False
                ):
                    raise ValueError("free_form_object")
                properties_count += len(properties)
                if properties_count > 5000:
                    raise ValueError("schema_properties")
                result["properties"] = result.get("properties", {})
                result["required"] = list(properties)
                result["additionalProperties"] = False
            if "items" in node:
                if type(node["items"]) is not dict:
                    raise ValueError("schema_items")
                result["items"] = convert(cast(dict[str, object], node["items"]), depth + 1)
            for name in ("anyOf", "oneOf"):
                branches = node.get(name)
                if branches is not None:
                    if (
                        type(branches) is not list
                        or not branches
                        or any(type(v) is not dict for v in branches)
                    ):
                        raise ValueError("schema_union")
                    if name == "oneOf":
                        discriminator = node.get("discriminator")
                        if (
                            type(discriminator) is not dict
                            or type(discriminator.get("propertyName")) is not str
                        ):
                            raise ValueError("unproven_exclusive_union")
                        key = discriminator["propertyName"]
                        seen: set[str] = set()
                        for branch in branches:
                            resolved = (
                                _reference(root, branch["$ref"]) if "$ref" in branch else branch
                            )
                            fields = resolved.get("properties")
                            if type(fields) is not dict or type(fields.get(key)) is not dict:
                                raise ValueError("unproven_exclusive_union")
                            field_schema = fields[key]
                            values = field_schema.get("enum", [field_schema.get("const")])
                            if (
                                type(values) is not list
                                or not values
                                or any(type(v) is not str for v in values)
                            ):
                                raise ValueError("unproven_exclusive_union")
                            if seen.intersection(values):
                                raise ValueError("overlapping_union")
                            seen.update(values)
                        result.pop("oneOf", None)
                    result["anyOf"] = [convert(branch, depth + 1) for branch in branches]
            return result

        return convert(root, 0)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise OpenAIStructuredError("unsupported_schema") from None


def _matches(
    value: object, schema: dict[str, object], root: dict[str, object], depth: int = 0
) -> bool:
    """Check the local dialect before its owning role performs semantic validation."""
    if depth > 48:
        return False
    if "$ref" in schema and not _matches(value, _reference(root, schema["$ref"]), root, depth + 1):
        return False
    for name in ("anyOf", "oneOf"):
        if name in schema:
            branches = cast(list[dict[str, object]], schema[name])
            count = sum(_matches(value, branch, root, depth + 1) for branch in branches)
            if (name == "anyOf" and not count) or (name == "oneOf" and count != 1):
                return False
    kind = schema.get("type")
    kinds = kind if type(kind) is list else [kind] if kind is not None else []
    if kinds and not any(
        {
            "object": type(value) is dict,
            "array": type(value) is list,
            "string": type(value) is str,
            "boolean": type(value) is bool,
            "integer": type(value) is int,
            "number": type(value) in {int, float},
            "null": value is None,
        }.get(cast(str, item), False)
        for item in kinds
    ):
        return False
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        return False
    if "enum" in schema and not any(
        type(value) is type(item) and value == item for item in cast(list[object], schema["enum"])
    ):
        return False
    if type(value) is dict:
        properties = cast(dict[str, dict[str, object]], schema.get("properties", {}))
        if any(key not in value for key in cast(list[str], schema.get("required", []))):
            return False
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            return False
        if any(
            not _matches(child, properties[key], root, depth + 1)
            for key, child in value.items()
            if key in properties
        ):
            return False
    if type(value) is list:
        if len(value) < cast(int, schema.get("minItems", 0)) or len(value) > cast(
            int, schema.get("maxItems", 50_000)
        ):
            return False
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(
            value
        ):
            return False
        if "items" in schema and any(
            not _matches(child, cast(dict[str, object], schema["items"]), root, depth + 1)
            for child in value
        ):
            return False
    if type(value) is str:
        if len(value) < cast(int, schema.get("minLength", 0)) or len(value) > cast(
            int, schema.get("maxLength", 1_000_000)
        ):
            return False
        if "pattern" in schema and re.search(cast(str, schema["pattern"]), value) is None:
            return False
    if type(value) in {int, float}:
        number = cast(float, value)
        if "minimum" in schema and number < cast(float, schema["minimum"]):
            return False
        if "maximum" in schema and number > cast(float, schema["maximum"]):
            return False
        if "exclusiveMinimum" in schema and number <= cast(float, schema["exclusiveMinimum"]):
            return False
        if "exclusiveMaximum" in schema and number >= cast(float, schema["exclusiveMaximum"]):
            return False
    return True


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    model: str = Field(min_length=1, max_length=128)
    status: str
    service_tier: Literal["default"]
    output: list[dict[str, object]] = Field(max_length=4)
    usage: dict[str, object] | None = None


def _count(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 1_000_000_000_000:
        raise ValueError("usage_invalid")
    return value


def _usage(
    raw: dict[str, object] | None,
    maximum: int,
) -> tuple[ConversationUsage | None, int | None, int | None]:
    if raw is None:
        return None, None, None
    input_tokens, output_tokens = _count(raw.get("input_tokens")), _count(raw.get("output_tokens"))
    total_tokens = _count(raw.get("total_tokens"))
    if (
        total_tokens is not None
        and input_tokens is not None
        and output_tokens is not None
        and total_tokens != input_tokens + output_tokens
    ):
        raise ValueError("usage_invalid")
    details = raw.get("output_tokens_details")
    if details is not None and type(details) is not dict:
        raise ValueError("usage_invalid")
    reasoning = _count(details.get("reasoning_tokens")) if type(details) is dict else None
    if reasoning is not None and (output_tokens is None or reasoning > output_tokens):
        raise ValueError("usage_invalid")
    if output_tokens is not None and output_tokens > maximum:
        raise ValueError("usage_limit")
    cached = raw.get("input_tokens_details")
    if cached is not None and type(cached) is not dict:
        raise ValueError("usage_invalid")
    cached_read = _count(cached.get("cached_tokens")) if type(cached) is dict else None
    cached_write = _count(cached.get("cache_write_tokens")) if type(cached) is dict else None
    for count in (cached_read, cached_write):
        if count is not None and (input_tokens is None or count > input_tokens):
            raise ValueError("usage_invalid")
    # The current neutral contract requires both cache details together. Missing
    # write metadata is unknown, not zero; retain totals without inventing it.
    if cached_read is None or cached_write is None:
        cached_read = cached_write = None
    projected = ConversationUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_read,
        cache_write_input_tokens=cached_write,
    )
    visible = (
        output_tokens - reasoning if output_tokens is not None and reasoning is not None else None
    )
    return projected, reasoning, visible


@dataclass(frozen=True, slots=True)
class OpenAIStructuredClient:
    transport: OpenAIStructuredTransport = field(repr=False)

    def generate(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema: dict[str, object],
        schema_name: str,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> StructuredResult:
        started = time.perf_counter_ns()
        try:
            if (
                type(model) is not str
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", model)
                or type(schema_name) is not str
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", schema_name)
                or type(max_output_tokens) is not int
                or not 1 <= max_output_tokens <= 32_768
                or type(timeout_seconds) not in {int, float}
                or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 600
                or type(messages) is not list
                or not 1 <= len(messages) <= 64
            ):
                raise ValueError("request_invalid")
            for message in messages:
                if (
                    type(message) is not dict
                    or set(message) != {"role", "content"}
                    or message["role"] not in {"system", "developer", "user", "assistant"}
                    or type(message["content"]) is not str
                    or not message["content"]
                ):
                    raise ValueError("request_invalid")
            original_schema = cast(
                dict[str, object],
                bounded_json(
                    json.dumps(schema, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                    MAX_SCHEMA_BYTES,
                ),
            )
            wire_schema = normalize_schema(original_schema)
            payload: dict[str, object] = {
                "model": model,
                "input": messages,
                "max_output_tokens": max_output_tokens,
                "reasoning": {"effort": "none"},
                "service_tier": "default",
                "store": False,
                "background": False,
                "stream": False,
                "truncation": "disabled",
                "prompt_cache_options": {"mode": "explicit"},
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": wire_schema,
                    }
                },
            }
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_STRUCTURED_REQUEST_BYTES:
                raise ValueError("request_limit")
            payload = cast(dict[str, object], bounded_json(encoded, MAX_STRUCTURED_REQUEST_BYTES))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise OpenAIStructuredError("invalid_request") from None
        built = time.perf_counter_ns()
        try:
            admit_provider_dispatch()
            body = self.transport.post_json(
                "/responses",
                payload,
                timeout_seconds=timeout_seconds,
                max_response_bytes=MAX_STRUCTURED_RESPONSE_BYTES,
            )
        except OpenAIHttpStatusError as error:
            code: Literal[
                "rate_or_quota_limited",
                "credentials_rejected",
                "resource_not_found",
                "request_rejected",
                "transport_unavailable",
            ] = "request_rejected"
            if error.status == 429:
                code = "rate_or_quota_limited"
            elif error.status in {401, 403}:
                code = "credentials_rejected"
            elif error.status == 404:
                code = "resource_not_found"
            elif error.status in {408, 409, 425} or error.status >= 500:
                code = "transport_unavailable"
            raise OpenAIStructuredError(code) from None
        except (OpenAITransportError, OSError, TimeoutError):
            raise OpenAIStructuredError("transport_unavailable") from None
        received = time.perf_counter_ns()
        try:
            response = _Envelope.model_validate(bounded_json(body, MAX_STRUCTURED_RESPONSE_BYTES))
            if response.model != model:
                raise ValueError("model_mismatch")
            if response.status != "completed":
                raise OpenAIStructuredError("response_incomplete")
            content: str | None = None
            for item in response.output:
                if item.get("type") == "reasoning" and item.get("summary") == []:
                    if set(item) - {"type", "id", "summary", "status"}:
                        raise ValueError("reasoning_payload")
                    continue
                if (
                    item.get("type") != "message"
                    or item.get("role") != "assistant"
                    or item.get("status", "completed") != "completed"
                    or content is not None
                ):
                    raise ValueError("ambiguous_output")
                parts = item.get("content")
                if type(parts) is not list or len(parts) != 1 or type(parts[0]) is not dict:
                    raise ValueError("ambiguous_content")
                part = parts[0]
                if part.get("type") == "refusal":
                    raise OpenAIStructuredError("refused")
                if part.get("type") != "output_text" or type(part.get("text")) is not str:
                    raise ValueError("content_invalid")
                content = part["text"]
            if content is None:
                raise ValueError("missing_content")
            document = bounded_json(content.encode("utf-8"), MAX_STRUCTURED_RESPONSE_BYTES)
            if not _matches(document, original_schema, original_schema) or not _matches(
                document, wire_schema, wire_schema
            ):
                raise ValueError("schema_mismatch")
            usage, reasoning, visible = _usage(response.usage, max_output_tokens)
            metrics = ProviderExecutionMetrics(
                client_request_build_duration_ns=built - started,
                http_roundtrip_duration_ns=received - built,
                client_response_parse_duration_ns=time.perf_counter_ns() - received,
                requested_output_token_limit=max_output_tokens,
                provider_output_token_limit=max_output_tokens,
                reasoning_output_tokens=reasoning,
                visible_output_tokens=visible,
            )
            return StructuredResult(content, response.model, metrics, usage)
        except (ValueError, TypeError, KeyError, UnicodeError, ValidationError, RecursionError):
            raise OpenAIStructuredError("response_invalid") from None
