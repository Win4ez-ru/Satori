"""Source-policy ports have no SQLite, lock or provider dependency."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from satori.domain.source_policy import (
    CanonicalRootRef,
    ProtectedContentFootprint,
    SourceEligibilitySnapshot,
    SourceExclusionReceipt,
)

SourceRepair = Callable[[SourceEligibilitySnapshot, SourceExclusionReceipt], None]


class SourceAdmissionPort(Protocol):
    def shared(self) -> AbstractContextManager[SourceEligibilitySnapshot]: ...

    def exclusive(self) -> AbstractContextManager[SourceEligibilitySnapshot]: ...

    def snapshot(
        self, identity_id: str | None = None, *, verify_mirror: bool = True
    ) -> SourceEligibilitySnapshot: ...

    def assert_binding(self, identity_id: str, floor_id: str, lineage_id: str) -> None: ...

    def check_root(self, root: CanonicalRootRef, purpose: str = "read") -> None: ...

    def check_footprint(
        self, footprint: ProtectedContentFootprint, purpose: str = "read"
    ) -> None: ...


class SourcePolicyControlPort(SourceAdmissionPort, Protocol):
    def commit_exclusion(
        self,
        root: CanonicalRootRef,
        *,
        expected_generation: int,
        request_id: str,
        preview_digest: str,
        trace_id: str,
    ) -> SourceExclusionReceipt: ...

    def receipt(self, request_id: str) -> SourceExclusionReceipt | None: ...

    def mark_repaired(self, request_id: str) -> SourceExclusionReceipt: ...

    def mark_repair_pending(self, request_id: str) -> SourceExclusionReceipt: ...
