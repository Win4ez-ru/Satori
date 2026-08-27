"""Small deterministic validation helpers shared by Stage 2 domain values."""

import math
import re
from datetime import UTC, datetime

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def non_blank(value: str, field_name: str, *, maximum: int | None = None) -> str:
    """Normalize and validate a required string."""

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if maximum is not None and len(normalized) > maximum:
        raise ValueError(f"{field_name} must be at most {maximum} characters")
    return normalized


def positive_version(value: int, field_name: str) -> int:
    """Validate a positive schema or aggregate version."""

    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be positive")
    return value


def state_key(value: str, field_name: str = "key") -> str:
    """Validate a stable lower snake_case state key."""

    normalized = non_blank(value, field_name, maximum=64)
    if _KEY_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be lower snake_case")
    return normalized


def unit_interval(value: float, field_name: str) -> float:
    """Validate a finite floating-point value in the closed unit interval."""

    if isinstance(value, bool) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")
    return value


def aware_utc(value: datetime, field_name: str) -> datetime:
    """Require an aware timestamp and normalize it to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def sha256_hex(value: str, field_name: str = "content_hash") -> str:
    """Validate a lowercase SHA-256 hex digest."""

    normalized = non_blank(value, field_name)
    if _HASH_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized
