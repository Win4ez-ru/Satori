"""Stored retention inventory stays independent of providers and Character."""

from typing import Protocol

from satori.domain.erasure import ErasureInventory
from satori.domain.source_policy import SourceEligibilitySnapshot


class ErasureInventoryPort(Protocol):
    def inspect(
        self,
        message_id: str,
        *,
        counterparty_id: str,
        snapshot: SourceEligibilitySnapshot,
    ) -> ErasureInventory:
        """Prove target scope first, then assess a consistent bounded stored snapshot."""
        ...
