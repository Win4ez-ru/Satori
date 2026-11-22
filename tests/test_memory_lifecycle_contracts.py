"""Operator lifecycle requests reject coercion and mutable boundary objects."""

import pytest
from pydantic import ValidationError

from satori.application.memory.lifecycle_contracts import MemoryLifecycleCommand
from satori.domain.memory import MemoryLifecycleStatus


def _payload() -> dict[str, object]:
    return {
        "memory_id": "synthetic-memory",
        "target_status": MemoryLifecycleStatus.ARCHIVED,
        "expected_version": 1,
        "request_id": "synthetic-request",
        "preview_digest": "a" * 64,
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("expected_version", True),
        ("expected_version", "1"),
        ("expected_version", 0),
        ("target_status", "archived"),
        ("memory_id", "   "),
        ("request_id", ""),
        ("preview_digest", "A" * 64),
        ("preview_digest", "a" * 63),
        ("approval", "provider"),
    ],
)
def test_lifecycle_command_has_strict_operator_boundary(key: str, value: object) -> None:
    payload = _payload()
    payload[key] = value
    with pytest.raises(ValidationError):
        MemoryLifecycleCommand.model_validate(payload)


def test_lifecycle_command_is_immutable() -> None:
    command = MemoryLifecycleCommand.model_validate(_payload())
    with pytest.raises(ValidationError, match="frozen_instance"):
        command.request_id = "changed-request"
