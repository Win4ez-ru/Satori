"""Read-only audit coverage behind the existing trusted policy lease."""

from typing import Protocol

from satori.application.erasure.audit_contracts import (
    AuditMetadataStatus,
    AuditMetadataStatusRequest,
)
from satori.domain.source_policy import SourceEligibilitySnapshot


class AuditMetadataInventoryPort(Protocol):
    def status(
        self, request: AuditMetadataStatusRequest, snapshot: SourceEligibilitySnapshot
    ) -> AuditMetadataStatus: ...
