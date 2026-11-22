"""Audit certification is retention evidence, not a domain mutation or grant."""

from satori.application.erasure.audit_contracts import (
    AuditMetadataStatus,
    AuditMetadataStatusRequest,
)
from satori.application.erasure.audit_ports import AuditMetadataInventoryPort
from satori.application.source_policy.ports import SourceAdmissionPort


class AuditMetadataReader:
    def __init__(self, control: SourceAdmissionPort, inventory: AuditMetadataInventoryPort) -> None:
        self._control = control
        self._inventory = inventory

    def execute(self, request: AuditMetadataStatusRequest) -> AuditMetadataStatus:
        with self._control.shared() as snapshot:
            return self._inventory.status(request, snapshot)
