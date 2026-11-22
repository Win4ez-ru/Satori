"""Private per-census decisions and work budget; no persistent authority."""

from dataclasses import dataclass

from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.retention import RetentionClass, RetentionFacet, RetentionReason
from satori.domain.source_policy import SourcePolicyError


@dataclass(frozen=True, slots=True)
class RowRetention:
    category: RetentionClass
    facets: frozenset[RetentionFacet] = frozenset()
    reasons: tuple[RetentionReason, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.category, RetentionClass):
            raise ValueError("invalid_retention_class")
        if any(not isinstance(item, RetentionFacet) for item in self.facets):
            raise ValueError("invalid_retention_facet")
        if any(not isinstance(item, RetentionReason) for item in self.reasons):
            raise ValueError("invalid_retention_reason")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("duplicate_retention_reason")
        if (self.category is RetentionClass.UNKNOWN) != bool(self.reasons):
            raise ValueError("retention_reason_class_mismatch")
        if (
            RetentionFacet.TERMINAL_BODY in self.facets
            and self.category is not RetentionClass.SUPPORTED_RETAINED
        ):
            raise ValueError("unproved_retention_terminal_facet")


@dataclass(slots=True)
class RetentionWorkBudget:
    limit: int = MAX_ERASURE_ASSESSMENT_WORK
    work: int = 0

    def charge(self, amount: int = 1) -> None:
        if type(amount) is not int or amount < 0:
            raise ValueError("invalid_retention_work")
        if type(self.limit) is not int or not 0 <= self.limit <= MAX_ERASURE_ASSESSMENT_WORK:
            raise ValueError("invalid_retention_work_limit")
        self.work += amount
        if self.work > self.limit:
            raise SourcePolicyError("retention_assessment_capacity_exceeded")
