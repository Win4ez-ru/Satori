"""Transient, reference-bound dialogue interpretation without a persistent owner.

Offsets are half-open Unicode code-point spans in actual supplied canonical text.
They establish quotation reachability, never semantic truth or fresh state evidence.
This neutral module intentionally imports neither agency nor conversation contracts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from satori.core.conversation import ConversationProviderFailureReason, ConversationUsage
from satori.core.provider_metrics import ProviderExecutionMetrics

if TYPE_CHECKING:
    from satori.application.conversation.contracts import RecentConversationContext

PRIOR_DIALOGUE_RELATION_SCHEMA_VERSION = 1
PRIOR_DIALOGUE_RELATION_MAX_ID_CHARS = 128
PRIOR_DIALOGUE_RELATION_MAX_QUOTE_CHARS = 512


class PriorDialogueRelationKind(StrEnum):
    SHIFT_FROM_PRIOR_MOVE = "shift_from_prior_move"
    CONTINUES = "continues"
    CLOSES = "closes"
    PRIOR_ALREADY_CLOSED = "prior_already_closed"
    ABSTAIN = "abstain"


class PriorDialogueRelationUncertainty(StrEnum):
    SUPPORTED = "supported"
    MATERIAL = "material"


def validate_relation_ids(
    *,
    current_user_message_id: str,
    prior_interaction_id: str,
    prior_user_message_id: str,
    prior_assistant_message_id: str,
) -> None:
    """Require exact caller-owned identifiers without whitespace normalization."""

    for value in (
        current_user_message_id,
        prior_interaction_id,
        prior_user_message_id,
        prior_assistant_message_id,
    ):
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > PRIOR_DIALOGUE_RELATION_MAX_ID_CHARS
        ):
            raise ValueError("prior dialogue relation IDs must be exact and bounded")
    if len({current_user_message_id, prior_user_message_id, prior_assistant_message_id}) != 3:
        raise ValueError("prior dialogue relation message IDs must be distinct")


def _validate_span(span: tuple[int, int] | None) -> None:
    if (
        not isinstance(span, tuple)
        or len(span) != 2
        or any(type(value) is not int for value in span)
        or span[0] < 0
        or span[1] <= span[0]
        or span[1] - span[0] > PRIOR_DIALOGUE_RELATION_MAX_QUOTE_CHARS
    ):
        raise ValueError("prior dialogue relation span must be exact and bounded")


@dataclass(frozen=True, slots=True)
class PriorDialogueRelation:
    """App-bound interpretation; no raw prose, provider IDs or durable lifetime."""

    schema_version: int
    kind: PriorDialogueRelationKind
    uncertainty: PriorDialogueRelationUncertainty
    current_user_message_id: str
    prior_interaction_id: str
    prior_user_message_id: str
    prior_assistant_message_id: str
    prior_assistant_span: tuple[int, int] | None
    current_user_span: tuple[int, int] | None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("prior dialogue relation schema_version must be 1")
        if not isinstance(self.kind, PriorDialogueRelationKind) or not isinstance(
            self.uncertainty, PriorDialogueRelationUncertainty
        ):
            raise ValueError("prior dialogue relation kind and uncertainty must be typed")
        validate_relation_ids(
            current_user_message_id=self.current_user_message_id,
            prior_interaction_id=self.prior_interaction_id,
            prior_user_message_id=self.prior_user_message_id,
            prior_assistant_message_id=self.prior_assistant_message_id,
        )
        if self.kind is PriorDialogueRelationKind.ABSTAIN:
            if self.prior_assistant_span is not None or self.current_user_span is not None:
                raise ValueError("abstain cannot contain dialogue relation spans")
        else:
            _validate_span(self.prior_assistant_span)
            _validate_span(self.current_user_span)


def unique_exact_span(text: str, quote: str) -> tuple[int, int]:
    """Locate exactly one occurrence, including overlapping occurrences."""

    if (
        not isinstance(text, str)
        or not isinstance(quote, str)
        or not quote.strip()
        or len(quote) > PRIOR_DIALOGUE_RELATION_MAX_QUOTE_CHARS
    ):
        raise ValueError("invalid prior dialogue relation quotation")
    start = text.find(quote)
    if start < 0 or text.find(quote, start + 1) >= 0:
        raise ValueError("invalid prior dialogue relation quotation")
    return start, start + len(quote)


def prior_dialogue_relation_matches_context(
    relation: PriorDialogueRelation,
    *,
    recent_context: RecentConversationContext | None,
    current_user_message_id: str,
    current_user_text: str,
) -> bool:
    """Check only the last supplied slot; do not search or reconstruct an older turn."""

    if not isinstance(relation, PriorDialogueRelation) or recent_context is None:
        return False
    if not recent_context.turns or relation.current_user_message_id != current_user_message_id:
        return False
    turn = recent_context.turns[-1]
    if (
        relation.prior_interaction_id != turn.interaction_id
        or relation.prior_user_message_id != turn.user_message_id
        or relation.prior_assistant_message_id != turn.assistant_message_id
    ):
        return False
    if relation.kind is PriorDialogueRelationKind.ABSTAIN:
        return True
    for text, span in (
        (turn.assistant_content, relation.prior_assistant_span),
        (current_user_text, relation.current_user_span),
    ):
        if span is None or span[1] > len(text):
            return False
        try:
            if unique_exact_span(text, text[span[0] : span[1]]) != span:
                return False
        except ValueError:
            return False
    return True


class PriorDialogueRelationStatus(StrEnum):
    APPLIED = "applied"
    ABSTAIN = "abstain"
    UNCERTAIN = "uncertain"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


class PriorDialogueRelationReason(StrEnum):
    PROPOSAL_ACCEPTED = "proposal_accepted"
    ABSTAINED = "abstained"
    MATERIAL_UNCERTAINTY = "material_uncertainty"
    INVALID_PROPOSAL = "invalid_proposal"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CONTEXT_BUDGET = "context_budget"
    INPUT_BOUNDS = "input_bounds"


@dataclass(frozen=True, slots=True)
class PreparedPriorDialogueRelation:
    """Content-free outcome plus numeric evidence; raw responses are not retained."""

    status: PriorDialogueRelationStatus
    reason: PriorDialogueRelationReason
    relation: PriorDialogueRelation | None = None
    classification_kind: PriorDialogueRelationKind | None = None
    uncertainty: PriorDialogueRelationUncertainty | None = None
    provider: str | None = None
    model: str | None = None
    usage: ConversationUsage | None = None
    metrics: ProviderExecutionMetrics | None = None
    latency_ms: float = 0.0
    provider_failure_reason: ConversationProviderFailureReason | None = None

    def __post_init__(self) -> None:
        allowed = {
            PriorDialogueRelationStatus.APPLIED: {PriorDialogueRelationReason.PROPOSAL_ACCEPTED},
            PriorDialogueRelationStatus.ABSTAIN: {PriorDialogueRelationReason.ABSTAINED},
            PriorDialogueRelationStatus.UNCERTAIN: {
                PriorDialogueRelationReason.MATERIAL_UNCERTAINTY
            },
            PriorDialogueRelationStatus.INVALID: {PriorDialogueRelationReason.INVALID_PROPOSAL},
            PriorDialogueRelationStatus.UNAVAILABLE: {
                PriorDialogueRelationReason.PROVIDER_UNAVAILABLE
            },
            PriorDialogueRelationStatus.SKIPPED: {
                PriorDialogueRelationReason.CONTEXT_BUDGET,
                PriorDialogueRelationReason.INPUT_BOUNDS,
            },
        }
        if (
            not isinstance(self.status, PriorDialogueRelationStatus)
            or not isinstance(self.reason, PriorDialogueRelationReason)
            or self.reason not in allowed[self.status]
        ):
            raise ValueError("invalid prior dialogue relation outcome")
        if self.status is PriorDialogueRelationStatus.APPLIED:
            if (
                not isinstance(self.relation, PriorDialogueRelation)
                or self.relation.kind is PriorDialogueRelationKind.ABSTAIN
                or self.relation.uncertainty is not PriorDialogueRelationUncertainty.SUPPORTED
            ):
                raise ValueError("applied dialogue relation requires a supported interpretation")
            if (
                self.classification_kind is not self.relation.kind
                or self.uncertainty is not self.relation.uncertainty
            ):
                raise ValueError("applied dialogue relation metadata must match its interpretation")
        elif self.relation is not None:
            raise ValueError("baseline dialogue relation outcome cannot contain a relation")
        if self.status in {
            PriorDialogueRelationStatus.ABSTAIN,
            PriorDialogueRelationStatus.UNCERTAIN,
        }:
            if not isinstance(
                self.classification_kind, PriorDialogueRelationKind
            ) or not isinstance(self.uncertainty, PriorDialogueRelationUncertainty):
                raise ValueError("valid baseline classification requires typed metadata")
            if self.status is PriorDialogueRelationStatus.ABSTAIN:
                if self.classification_kind is not PriorDialogueRelationKind.ABSTAIN:
                    raise ValueError("abstain outcome requires abstain metadata")
            elif (
                self.classification_kind is PriorDialogueRelationKind.ABSTAIN
                or self.uncertainty is not PriorDialogueRelationUncertainty.MATERIAL
            ):
                raise ValueError("uncertain outcome requires material classification metadata")
        elif self.status is not PriorDialogueRelationStatus.APPLIED and (
            self.classification_kind is not None or self.uncertainty is not None
        ):
            raise ValueError("invalid or skipped output cannot claim classification metadata")
        if (
            isinstance(self.latency_ms, bool)
            or not math.isfinite(self.latency_ms)
            or self.latency_ms < 0
        ):
            raise ValueError("dialogue relation latency must be finite and nonnegative")
        if any(
            value is not None and (not isinstance(value, str) or not value.strip())
            for value in (self.provider, self.model)
        ):
            raise ValueError("dialogue relation provider metadata cannot be blank")
        if self.usage is not None and not isinstance(self.usage, ConversationUsage):
            raise ValueError("dialogue relation usage must be numeric provider metadata")
        if self.metrics is not None and not isinstance(self.metrics, ProviderExecutionMetrics):
            raise ValueError("dialogue relation metrics must be numeric provider metadata")
        if self.provider_failure_reason is not None and (
            self.status is not PriorDialogueRelationStatus.UNAVAILABLE
            or not isinstance(self.provider_failure_reason, ConversationProviderFailureReason)
        ):
            raise ValueError("provider failure reason requires an unavailable relation")
