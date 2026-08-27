"""Immutable application contracts for emotional expression and appraisal status."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.domain.affect import (
    AffectiveStateSnapshot,
    AffectiveTransitionDraft,
    FastAffectiveState,
    MoodState,
)

EMOTIONAL_EXPRESSION_CONTEXT_SCHEMA_VERSION = 1


class EmotionAppraisalStatus(StrEnum):
    """Observable result of the optional structured appraisal step."""

    APPLIED = "applied"
    SKIPPED = "skipped"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EmotionalExpressionContext:
    """Versioned immutable affect snapshot supplied separately to generation."""

    schema_version: int
    state_version: int
    mood_version: int
    as_of: datetime
    fast: FastAffectiveState
    mood: MoodState
    appraisal_status: EmotionAppraisalStatus


@dataclass(frozen=True, slots=True)
class PreparedAffectiveContext:
    """Tentative state for expression plus an optional owner-approved commit plan."""

    expression: EmotionalExpressionContext
    materialized_pre_event: AffectiveStateSnapshot
    transition: AffectiveTransitionDraft | None
    appraisal_status: EmotionAppraisalStatus
    reason_code: str
    provider: str | None = None
    model: str | None = None
    appraisal_method: str | None = None
    materialization_latency_ms: float = 0.0
    request_build_latency_ms: float = 0.0
    appraisal_latency_ms: float = 0.0
    provider_metrics: ProviderExecutionMetrics | None = None
