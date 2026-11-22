"""Request-local repair targeting, without affect or relationship state proposals.

Half-open Unicode code-point spans bind exact quotations to supplied canonical text.
They establish source reachability, never sincerity, forgiveness or semantic truth.
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

REPAIR_INTERPRETATION_SCHEMA_VERSION = 2
REPAIR_INTERPRETATION_MAX_ID_CHARS = 128
REPAIR_INTERPRETATION_MAX_QUOTE_CHARS = 512


class RepairInterpretationKind(StrEnum):
    REPAIR_TOWARD_SATORI = "repair_toward_satori"
    OTHER = "other"
    ABSTAIN = "abstain"


class RepairInterpretationUncertainty(StrEnum):
    SUPPORTED = "supported"
    MATERIAL = "material"


class RepairInterpretationSpeechAct(StrEnum):
    REPAIR_OFFER = "repair_offer"
    OTHER = "other"
    ABSTAIN = "abstain"


class RepairInterpretationFrame(StrEnum):
    DIRECT_EXCHANGE = "direct_exchange"
    SUPPLIED_OR_REPORTED = "supplied_or_reported"
    UNRESOLVED = "unresolved"


class RepairInterpretationAddressee(StrEnum):
    SATORI = "satori"
    OTHER = "other"
    UNRESOLVED = "unresolved"
    NONE = "none"


class RepairInterpretationBasis(StrEnum):
    CURRENT_TURN = "current_turn"
    PRIOR_PAIR = "prior_pair"
    UNRESOLVED = "unresolved"


def validate_repair_classification(
    *,
    speech_act: RepairInterpretationSpeechAct,
    frame: RepairInterpretationFrame,
    addressee: RepairInterpretationAddressee,
    basis: RepairInterpretationBasis,
    uncertainty: RepairInterpretationUncertainty,
) -> None:
    """Validate closed semantic dimensions without judging what the dialogue means."""

    if (
        not isinstance(speech_act, RepairInterpretationSpeechAct)
        or not isinstance(frame, RepairInterpretationFrame)
        or not isinstance(addressee, RepairInterpretationAddressee)
        or not isinstance(basis, RepairInterpretationBasis)
        or not isinstance(uncertainty, RepairInterpretationUncertainty)
    ):
        raise ValueError("repair interpretation classification must be typed")
    if speech_act is RepairInterpretationSpeechAct.ABSTAIN:
        if (
            frame is not RepairInterpretationFrame.UNRESOLVED
            or addressee is not RepairInterpretationAddressee.UNRESOLVED
            or basis is not RepairInterpretationBasis.UNRESOLVED
            or uncertainty is not RepairInterpretationUncertainty.MATERIAL
        ):
            raise ValueError("abstain repair classification requires unresolved material metadata")
        return
    if speech_act is RepairInterpretationSpeechAct.OTHER:
        if addressee is not RepairInterpretationAddressee.NONE:
            raise ValueError("other speech act cannot claim a repair addressee")
    elif addressee is RepairInterpretationAddressee.NONE:
        raise ValueError("repair offer cannot have no addressee")
    if uncertainty is RepairInterpretationUncertainty.SUPPORTED and (
        frame is RepairInterpretationFrame.UNRESOLVED
        or addressee is RepairInterpretationAddressee.UNRESOLVED
        or basis is RepairInterpretationBasis.UNRESOLVED
    ):
        raise ValueError("supported repair classification requires resolved dimensions")


def derive_repair_kind(
    *,
    speech_act: RepairInterpretationSpeechAct,
    frame: RepairInterpretationFrame,
    addressee: RepairInterpretationAddressee,
    basis: RepairInterpretationBasis,
    uncertainty: RepairInterpretationUncertainty,
) -> RepairInterpretationKind:
    """Derive the reaction input in application code; the provider cannot select it."""

    validate_repair_classification(
        speech_act=speech_act,
        frame=frame,
        addressee=addressee,
        basis=basis,
        uncertainty=uncertainty,
    )
    if (
        speech_act is RepairInterpretationSpeechAct.ABSTAIN
        or uncertainty is RepairInterpretationUncertainty.MATERIAL
    ):
        return RepairInterpretationKind.ABSTAIN
    if (
        speech_act is RepairInterpretationSpeechAct.REPAIR_OFFER
        and frame is RepairInterpretationFrame.DIRECT_EXCHANGE
        and addressee is RepairInterpretationAddressee.SATORI
    ):
        return RepairInterpretationKind.REPAIR_TOWARD_SATORI
    return RepairInterpretationKind.OTHER


def validate_repair_ids(
    *,
    current_user_message_id: str,
    prior_interaction_id: str | None,
    prior_user_message_id: str | None,
    prior_assistant_message_id: str | None,
) -> None:
    """Require caller-owned identifiers and either one complete prior pair or none."""

    prior_ids = (prior_interaction_id, prior_user_message_id, prior_assistant_message_id)
    if any(value is None for value in prior_ids) and any(value is not None for value in prior_ids):
        raise ValueError("repair interpretation requires a complete prior ID set")
    for value in (current_user_message_id, *prior_ids):
        if value is None:
            continue
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > REPAIR_INTERPRETATION_MAX_ID_CHARS
        ):
            raise ValueError("repair interpretation IDs must be exact and bounded")
    if not isinstance(current_user_message_id, str):
        raise ValueError("repair interpretation requires a current user ID")
    message_ids = (current_user_message_id, prior_user_message_id, prior_assistant_message_id)
    present_message_ids = tuple(value for value in message_ids if value is not None)
    if len(set(present_message_ids)) != len(present_message_ids):
        raise ValueError("repair interpretation message IDs must be distinct")


def _validate_span(span: tuple[int, int] | None) -> None:
    if (
        not isinstance(span, tuple)
        or len(span) != 2
        or any(type(value) is not int for value in span)
        or span[0] < 0
        or span[1] <= span[0]
        or span[1] - span[0] > REPAIR_INTERPRETATION_MAX_QUOTE_CHARS
    ):
        raise ValueError("repair interpretation span must be exact and bounded")


def unique_repair_span(text: str, quote: str) -> tuple[int, int]:
    """Locate exactly one occurrence, including overlapping occurrences."""

    if (
        not isinstance(text, str)
        or not isinstance(quote, str)
        or not quote.strip()
        or len(quote) > REPAIR_INTERPRETATION_MAX_QUOTE_CHARS
    ):
        raise ValueError("invalid repair interpretation quotation")
    start = text.find(quote)
    if start < 0 or text.find(quote, start + 1) >= 0:
        raise ValueError("invalid repair interpretation quotation")
    return start, start + len(quote)


@dataclass(frozen=True, slots=True)
class RepairInterpretation:
    """Application-bound targeting only; no raw quotation, motivation or durable lifetime."""

    schema_version: int
    kind: RepairInterpretationKind
    uncertainty: RepairInterpretationUncertainty
    current_user_message_id: str
    current_user_span: tuple[int, int] | None
    speech_act: RepairInterpretationSpeechAct
    frame: RepairInterpretationFrame
    addressee: RepairInterpretationAddressee
    basis: RepairInterpretationBasis
    prior_interaction_id: str | None = None
    prior_user_message_id: str | None = None
    prior_assistant_message_id: str | None = None
    prior_user_span: tuple[int, int] | None = None
    prior_assistant_span: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ValueError("repair interpretation schema_version must be 2")
        if not isinstance(self.kind, RepairInterpretationKind) or not isinstance(
            self.uncertainty, RepairInterpretationUncertainty
        ):
            raise ValueError("repair interpretation kind and uncertainty must be typed")
        derived_kind = derive_repair_kind(
            speech_act=self.speech_act,
            frame=self.frame,
            addressee=self.addressee,
            basis=self.basis,
            uncertainty=self.uncertainty,
        )
        if self.kind is not derived_kind:
            raise ValueError("repair interpretation kind must match its semantic dimensions")
        validate_repair_ids(
            current_user_message_id=self.current_user_message_id,
            prior_interaction_id=self.prior_interaction_id,
            prior_user_message_id=self.prior_user_message_id,
            prior_assistant_message_id=self.prior_assistant_message_id,
        )
        spans = (self.current_user_span, self.prior_user_span, self.prior_assistant_span)
        if self.speech_act is RepairInterpretationSpeechAct.ABSTAIN:
            if any(span is not None for span in spans):
                raise ValueError("abstain cannot contain repair interpretation spans")
            return
        _validate_span(self.current_user_span)
        if (self.prior_user_span is None) != (self.prior_assistant_span is None):
            raise ValueError("repair interpretation requires paired prior spans")
        if self.basis is RepairInterpretationBasis.PRIOR_PAIR:
            if self.prior_interaction_id is None or self.prior_user_span is None:
                raise ValueError("prior-pair repair basis requires a supplied quoted prior pair")
            _validate_span(self.prior_user_span)
            _validate_span(self.prior_assistant_span)
        elif self.prior_user_span is not None:
            raise ValueError("current or unresolved repair basis cannot quote the prior pair")


def repair_interpretation_matches_context(
    interpretation: RepairInterpretation,
    *,
    recent_context: RecentConversationContext | None,
    current_user_message_id: str,
    current_user_text: str,
) -> bool:
    """Match only the supplied latest slot; never find an older available exchange."""

    if (
        not isinstance(interpretation, RepairInterpretation)
        or interpretation.current_user_message_id != current_user_message_id
        or not isinstance(current_user_text, str)
    ):
        return False
    latest = (
        recent_context.turns[-1] if recent_context is not None and recent_context.turns else None
    )
    if interpretation.prior_interaction_id is None:
        if latest is not None:
            return False
    elif latest is None or (
        interpretation.prior_interaction_id != latest.interaction_id
        or interpretation.prior_user_message_id != latest.user_message_id
        or interpretation.prior_assistant_message_id != latest.assistant_message_id
    ):
        return False
    pairs = [(current_user_text, interpretation.current_user_span)]
    if latest is not None:
        pairs.extend(
            (
                (latest.user_content, interpretation.prior_user_span),
                (latest.assistant_content, interpretation.prior_assistant_span),
            )
        )
    for text, span in pairs:
        if span is None:
            continue
        if span[1] > len(text):
            return False
        try:
            if unique_repair_span(text, text[span[0] : span[1]]) != span:
                return False
        except ValueError:
            return False
    return True


class RepairInterpretationStatus(StrEnum):
    APPLIED = "applied"
    ABSTAIN = "abstain"
    UNCERTAIN = "uncertain"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


class RepairInterpretationReason(StrEnum):
    PROPOSAL_ACCEPTED = "proposal_accepted"
    ABSTAINED = "abstained"
    MATERIAL_UNCERTAINTY = "material_uncertainty"
    INVALID_PROPOSAL = "invalid_proposal"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CONTEXT_BUDGET = "context_budget"
    INPUT_BOUNDS = "input_bounds"
    NOT_ELIGIBLE = "not_eligible"
    PRIOR_UNAVAILABLE = "prior_unavailable"


def _validate_repair_outcome(
    *,
    status: RepairInterpretationStatus,
    reason: RepairInterpretationReason,
    kind: RepairInterpretationKind | None,
    uncertainty: RepairInterpretationUncertainty | None,
    speech_act: RepairInterpretationSpeechAct | None,
    frame: RepairInterpretationFrame | None,
    addressee: RepairInterpretationAddressee | None,
    basis: RepairInterpretationBasis | None,
) -> None:
    allowed = {
        RepairInterpretationStatus.APPLIED: {RepairInterpretationReason.PROPOSAL_ACCEPTED},
        RepairInterpretationStatus.ABSTAIN: {RepairInterpretationReason.ABSTAINED},
        RepairInterpretationStatus.UNCERTAIN: {RepairInterpretationReason.MATERIAL_UNCERTAINTY},
        RepairInterpretationStatus.INVALID: {RepairInterpretationReason.INVALID_PROPOSAL},
        RepairInterpretationStatus.UNAVAILABLE: {RepairInterpretationReason.PROVIDER_UNAVAILABLE},
        RepairInterpretationStatus.SKIPPED: {
            RepairInterpretationReason.CONTEXT_BUDGET,
            RepairInterpretationReason.INPUT_BOUNDS,
            RepairInterpretationReason.NOT_ELIGIBLE,
            RepairInterpretationReason.PRIOR_UNAVAILABLE,
        },
    }
    if (
        not isinstance(status, RepairInterpretationStatus)
        or not isinstance(reason, RepairInterpretationReason)
        or reason not in allowed[status]
    ):
        raise ValueError("invalid repair interpretation outcome")
    if status not in {
        RepairInterpretationStatus.APPLIED,
        RepairInterpretationStatus.ABSTAIN,
        RepairInterpretationStatus.UNCERTAIN,
    }:
        if any(
            value is not None for value in (kind, uncertainty, speech_act, frame, addressee, basis)
        ):
            raise ValueError("invalid or skipped repair cannot claim classification metadata")
        return
    if (
        not isinstance(kind, RepairInterpretationKind)
        or not isinstance(uncertainty, RepairInterpretationUncertainty)
        or not isinstance(speech_act, RepairInterpretationSpeechAct)
        or not isinstance(frame, RepairInterpretationFrame)
        or not isinstance(addressee, RepairInterpretationAddressee)
        or not isinstance(basis, RepairInterpretationBasis)
    ):
        raise ValueError("valid repair outcome requires complete typed classification metadata")
    derived_kind = derive_repair_kind(
        speech_act=speech_act,
        frame=frame,
        addressee=addressee,
        basis=basis,
        uncertainty=uncertainty,
    )
    if kind is not derived_kind:
        raise ValueError("repair outcome kind must match its semantic dimensions")
    if status is RepairInterpretationStatus.APPLIED:
        if (
            speech_act is RepairInterpretationSpeechAct.ABSTAIN
            or uncertainty is not RepairInterpretationUncertainty.SUPPORTED
        ):
            raise ValueError("applied repair outcome requires supported non-abstain metadata")
    elif status is RepairInterpretationStatus.ABSTAIN:
        if speech_act is not RepairInterpretationSpeechAct.ABSTAIN:
            raise ValueError("abstain outcome requires abstain speech-act metadata")
    elif (
        speech_act is RepairInterpretationSpeechAct.ABSTAIN
        or uncertainty is not RepairInterpretationUncertainty.MATERIAL
    ):
        raise ValueError("uncertain repair requires material non-abstain metadata")


@dataclass(frozen=True, slots=True)
class PreparedRepairInterpretation:
    """Content-free classification outcome plus existing numerical provider evidence."""

    status: RepairInterpretationStatus
    reason: RepairInterpretationReason
    interpretation: RepairInterpretation | None = None
    classification_kind: RepairInterpretationKind | None = None
    uncertainty: RepairInterpretationUncertainty | None = None
    provider: str | None = None
    model: str | None = None
    usage: ConversationUsage | None = None
    metrics: ProviderExecutionMetrics | None = None
    latency_ms: float = 0.0
    provider_failure_reason: ConversationProviderFailureReason | None = None
    speech_act: RepairInterpretationSpeechAct | None = None
    frame: RepairInterpretationFrame | None = None
    addressee: RepairInterpretationAddressee | None = None
    basis: RepairInterpretationBasis | None = None

    def __post_init__(self) -> None:
        _validate_repair_outcome(
            status=self.status,
            reason=self.reason,
            kind=self.classification_kind,
            uncertainty=self.uncertainty,
            speech_act=self.speech_act,
            frame=self.frame,
            addressee=self.addressee,
            basis=self.basis,
        )
        if self.status in {
            RepairInterpretationStatus.APPLIED,
            RepairInterpretationStatus.UNCERTAIN,
        }:
            if (
                not isinstance(self.interpretation, RepairInterpretation)
                or self.interpretation.speech_act is RepairInterpretationSpeechAct.ABSTAIN
            ):
                raise ValueError(
                    "effective repair targeting requires a source-bound interpretation"
                )
            if (
                self.classification_kind is not self.interpretation.kind
                or self.uncertainty is not self.interpretation.uncertainty
                or self.speech_act is not self.interpretation.speech_act
                or self.frame is not self.interpretation.frame
                or self.addressee is not self.interpretation.addressee
                or self.basis is not self.interpretation.basis
            ):
                raise ValueError("effective repair metadata must match its interpretation")
        elif self.interpretation is not None:
            raise ValueError("baseline repair outcome cannot contain an interpretation")
        if (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, int | float)
            or not math.isfinite(self.latency_ms)
            or self.latency_ms < 0
        ):
            raise ValueError("repair latency must be finite and nonnegative")
        if any(
            value is not None and (not isinstance(value, str) or not value.strip())
            for value in (self.provider, self.model)
        ):
            raise ValueError("repair provider metadata cannot be blank")
        if self.usage is not None and not isinstance(self.usage, ConversationUsage):
            raise ValueError("repair usage must be numeric provider metadata")
        if self.metrics is not None and not isinstance(self.metrics, ProviderExecutionMetrics):
            raise ValueError("repair metrics must be numeric provider metadata")
        if self.provider_failure_reason is not None and (
            self.status is not RepairInterpretationStatus.UNAVAILABLE
            or not isinstance(self.provider_failure_reason, ConversationProviderFailureReason)
        ):
            raise ValueError("provider failure reason requires unavailable repair interpretation")

    @property
    def repair_offer_override(self) -> bool | None:
        """Material non-abstain targeting withholds repair; ordinary fallback preserves it."""

        if self.status is RepairInterpretationStatus.UNCERTAIN:
            return False
        if self.interpretation is None:
            return None
        return self.interpretation.kind is RepairInterpretationKind.REPAIR_TOWARD_SATORI


@dataclass(frozen=True, slots=True)
class RepairInterpretationTrace:
    """Content-free transient transport record; targeting is separate from state evolution."""

    status: RepairInterpretationStatus
    reason: RepairInterpretationReason
    kind: RepairInterpretationKind | None
    uncertainty: RepairInterpretationUncertainty | None
    baseline_repair_offer: bool
    effective_repair_offer: bool
    speech_act: RepairInterpretationSpeechAct | None = None
    frame: RepairInterpretationFrame | None = None
    addressee: RepairInterpretationAddressee | None = None
    basis: RepairInterpretationBasis | None = None

    def __post_init__(self) -> None:
        if (
            type(self.baseline_repair_offer) is not bool
            or type(self.effective_repair_offer) is not bool
        ):
            raise ValueError("repair interpretation trace cue values must be exact bool")
        _validate_repair_outcome(
            status=self.status,
            reason=self.reason,
            kind=self.kind,
            uncertainty=self.uncertainty,
            speech_act=self.speech_act,
            frame=self.frame,
            addressee=self.addressee,
            basis=self.basis,
        )
        if self.status is RepairInterpretationStatus.APPLIED:
            if self.effective_repair_offer is not (
                self.kind is RepairInterpretationKind.REPAIR_TOWARD_SATORI
            ):
                raise ValueError("applied repair trace cue must match supported targeting")
        elif self.status is RepairInterpretationStatus.UNCERTAIN:
            if self.effective_repair_offer:
                raise ValueError("material repair trace must withhold the current repair cue")
        else:
            if self.baseline_repair_offer is not self.effective_repair_offer:
                raise ValueError("baseline repair trace cannot change the current cue")
