"""Strict bounded scalar and JSON contracts for one private retention census."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime

from satori.domain.retention import RetentionReason
from satori.infrastructure.persistence.retention_contracts import RetentionWorkBudget

MAX_RETAINED_EMBEDDING_DIMENSIONS = 8192
MAX_RETAINED_EMBEDDING_BYTES = 262_144


class UnprovedRetention(ValueError):
    def __init__(self, reason: RetentionReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def require(condition: bool, reason: RetentionReason = RetentionReason.ROW_SHAPE_UNPROVEN) -> None:
    if not condition:
        raise UnprovedRetention(reason)


def text(value: object, maximum: int = 128) -> str:
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum)
    assert isinstance(value, str)
    return value


def integer(value: object, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    require(type(value) is int and minimum <= value <= maximum)
    assert isinstance(value, int)
    return value


def number(value: object, minimum: float = -1.0, maximum: float = 1.0) -> float:
    require(type(value) in (int, float))
    assert isinstance(value, (int, float))
    require(math.isfinite(value) and minimum <= value <= maximum)
    return float(value)


def timestamp(value: object) -> datetime:
    if isinstance(value, str):
        require(len(value) <= 64)
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise UnprovedRetention(RetentionReason.ROW_SHAPE_UNPROVEN) from None
    require(isinstance(value, datetime))
    assert isinstance(value, datetime)
    # SQLite UTCDateTime persists naive UTC strings; other offsets normalize to UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def digest(value: object) -> str:
    result = text(value, 64)
    require(len(result) == 64 and all(c in "0123456789abcdef" for c in result))
    return result


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def document(value: object, budget: RetentionWorkBudget) -> object:
    if isinstance(value, str):
        require(len(value.encode("utf-8")) <= 131_072, RetentionReason.STRUCTURED_VALUE_UNPROVEN)
        try:
            value = json.loads(value, object_pairs_hook=_pairs)
        except (ValueError, RecursionError):
            raise UnprovedRetention(RetentionReason.STRUCTURED_VALUE_UNPROVEN) from None
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        budget.charge()
        item, depth = stack.pop()
        nodes += 1
        require(depth <= 32 and nodes <= 8192, RetentionReason.STRUCTURED_VALUE_UNPROVEN)
        if isinstance(item, dict):
            require(len(item) <= 512, RetentionReason.STRUCTURED_VALUE_UNPROVEN)
            require(all(isinstance(k, str) and len(k) <= 128 for k in item))
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            require(len(item) <= 512, RetentionReason.STRUCTURED_VALUE_UNPROVEN)
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, str):
            require(len(item) <= 131_072, RetentionReason.STRUCTURED_VALUE_UNPROVEN)
        elif type(item) is float:
            require(math.isfinite(item), RetentionReason.STRUCTURED_VALUE_UNPROVEN)
        else:
            require(
                item is None or type(item) in (bool, int), RetentionReason.STRUCTURED_VALUE_UNPROVEN
            )
    return value


def embedding_vector(value: object, dimensions: object, budget: RetentionWorkBudget) -> list[float]:
    """The closed vector role has its own size bound; general JSON stays unchanged."""
    reason = RetentionReason.STRUCTURED_VALUE_UNPROVEN
    require(
        type(dimensions) is int and 1 <= dimensions <= MAX_RETAINED_EMBEDDING_DIMENSIONS, reason
    )
    assert isinstance(dimensions, int)
    budget.charge()
    if isinstance(value, str):
        require(len(value) <= MAX_RETAINED_EMBEDDING_BYTES, reason)
        require(len(value.encode("utf-8")) <= MAX_RETAINED_EMBEDDING_BYTES, reason)
        try:
            value = json.loads(value, object_pairs_hook=_pairs)
        except (ValueError, RecursionError):
            raise UnprovedRetention(reason) from None
    require(isinstance(value, list) and len(value) == dimensions, reason)
    assert isinstance(value, list)
    budget.charge(dimensions)
    result: list[float] = []
    norm_squared: int | float = 0
    encoded_bytes = dimensions + 1  # Brackets and compact commas for decoded input.
    try:
        for component in value:
            require(type(component) in (int, float), reason)
            assert isinstance(component, (int, float))
            require(math.isfinite(component), reason)
            encoded_bytes += len(json.dumps(component, allow_nan=False))
            require(encoded_bytes <= MAX_RETAINED_EMBEDDING_BYTES, reason)
            result.append(float(component))
            norm_squared += component * component
        # Preserve EmbeddingResponse's nonzero floating-point norm contract. Finite
        # components such as 1e100 are valid; no extra magnitude/provider rule is added.
        require(math.sqrt(norm_squared) != 0.0, reason)
    except (ValueError, TypeError, OverflowError):
        raise UnprovedRetention(reason) from None
    return result


def object_fields(value: object, fields: set[str]) -> Mapping[str, object]:
    require(
        isinstance(value, dict) and set(value) == fields, RetentionReason.STRUCTURED_VALUE_UNPROVEN
    )
    assert isinstance(value, dict)
    return value


def id_list(value: object, *, maximum: int = 512, nonempty: bool = False) -> tuple[str, ...]:
    require(isinstance(value, list), RetentionReason.STRUCTURED_VALUE_UNPROVEN)
    assert isinstance(value, list)
    require(len(value) <= maximum and (bool(value) or not nonempty))
    result = tuple(text(item) for item in value)
    require(len(set(result)) == len(result))
    return result
