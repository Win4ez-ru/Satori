"""Stored whole-main coverage stays outside cognition and Character."""

from typing import Protocol

from satori.application.erasure.retention_contracts import RetentionStatusRequest
from satori.domain.retention import RetentionInventory
from satori.domain.source_policy import SourceEligibilitySnapshot


class RetentionInventoryPort(Protocol):
    def inspect(
        self, request: RetentionStatusRequest, snapshot: SourceEligibilitySnapshot
    ) -> RetentionInventory:
        """Prove identity and current complete heads before one protected census."""
        ...
