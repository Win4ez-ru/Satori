"""Canonical conversation partition; attribution is separate from access permission."""

import hashlib
import json
from dataclasses import dataclass

from satori.domain.validation import non_blank


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """One verified identity/counterparty namespace, independent of Character."""

    identity_id: str
    counterparty_id: str

    def __post_init__(self) -> None:
        for name in ("identity_id", "counterparty_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))


def scope_semantic_key(base_key: str, scope: MemoryScope) -> str:
    """Namespace the existing normalized identity without changing value policy."""

    material = json.dumps(
        [scope.identity_id, scope.counterparty_id, base_key],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
