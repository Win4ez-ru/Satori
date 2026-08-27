"""Deterministic Stage 13 inclination policy and immutable state."""

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from satori.core.inclinations import (
    InclinationEvidenceSource,
    InclinationKind,
    InclinationProposal,
)
from satori.domain.validation import aware_utc, non_blank, positive_version, unit_interval

INCLINATION_SCHEMA_VERSION = 1
INCLINATION_POLICY_VERSION = 1
INCLINATION_NORMALIZATION_VERSION = 1
INCLINATION_CONTEXT_SCHEMA_VERSION = 1

MIN_PROVIDER_CONFIDENCE = 0.55
MIN_MATERIAL_DELTA = 0.01
INTEREST_FORMATION_SIGNAL = 0.18
PREFERENCE_FORMATION_DIFFERENCE = 0.24
INTEREST_EVENT_INCREASE_CAP = 0.12
INTEREST_EVENT_DECREASE_CAP = 0.08
PREFERENCE_EVENT_CAP = 0.10
INTEREST_ROLLING_CAP = 0.24
PREFERENCE_ROLLING_CAP = 0.18
ROLLING_WINDOW = timedelta(days=30)

_DIRECT_ASSIGNMENT = re.compile(
    r"(?:\b(?:я|мы)\s+(?:не\s+)?(?:люб\w*|обожа\w*|предпочита\w*|ненавиж\w*)\b|"
    r"\b(?:я|мы)\s+(?:не\s+)?интересу\w*\b|"
    r"\b(?:мне|нам)\s+(?:не\s+)?(?:нрав\w*|интерес\w*)\b|"
    r"\b(?:не\s+)?(?:полюби|люби|обожай|предпочитай|заинтересуйся)\b|"
    r"\b(?:ты|вы)\s+(?:теперь\s+)?(?:не\s+)?"
    r"(?:люб\w*|обожа\w*|предпочита\w*|интересу\w*)\b|"
    r"\b(?:ты|вы)\s+(?:не\s+)?(?:долж\w*|обязан\w*)\s+(?:не\s+)?"
    r"(?:люб\w*|обожа\w*|предпочита\w*|интересова\w*)\b|"
    r"\b(?:тебе|вам)\s+(?:не\s+)?(?:нрав\w*|интерес\w*)\b|"
    r"\b(?:тебе|вам)\s+(?:не\s+)?(?:следует|нужно|надо)\s+(?:не\s+)?"
    r"(?:люб\w*|обожа\w*|предпочита\w*|интересова\w*)\b|"
    r"\bтв\w+\s+любим\w*\b|"
    r"\b(?:i|we)\s+(?:do\s+not\s+|don't\s+)?(?:love|like|prefer|hate|dislike)\b|"
    r"\b(?:i\s+am|i'm|we\s+are|we're)\s+(?:not\s+)?interested\b|"
    r"\b(?:you)\s+(?:now\s+)?(?:do\s+not\s+|don't\s+)?(?:love|like|prefer)\b|"
    r"\byou\s+(?:should|must|have\s+to)\s+(?:not\s+)?"
    r"(?:love|like|prefer|be\s+interested)\b|"
    r"\byou(?:\s+are|'re)\s+(?:not\s+)?interested\b|"
    r"\byour\s+(?:favorite|favourite)\b)",
    re.IGNORECASE,
)
_RELATIONSHIP_CONTAMINATION = re.compile(
    r"(?:отношен|между\s+нами|наша\s+связ|довер|близост|"
    r"relationship|between\s+us|closeness|our\s+bond)",
    re.IGNORECASE,
)


class InclinationDecisionKind(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"


class InclinationEvidenceRole(StrEnum):
    TOPIC = "topic"
    OPTION_A = "option_a"
    OPTION_B = "option_b"


class InclinationRevisionKind(StrEnum):
    CREATED = "created"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"


def normalize_inclination_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", non_blank(value, "inclination label", maximum=96))
    lexical = _normalize_lexical(normalized)
    if len(lexical) < 2:
        raise ValueError("inclination label is too short after normalization")
    return lexical


def inclination_key(
    kind: InclinationKind, normalized_topic: str, normalized_alternative: str | None
) -> str:
    payload = {
        "kind": kind.value,
        "normalization_version": INCLINATION_NORMALIZATION_VERSION,
        "topic": normalized_topic,
        "alternative_topic": normalized_alternative,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class InclinationEvidence:
    evidence_id: str
    inclination_id: str
    reflection_source_id: str
    affective_transition_id: str
    affective_state_version: int
    affective_signal_hash: str
    source_message_id: str
    source_interaction_id: str
    source_session_id: str
    source_counterparty_id: str
    content_hash: str
    content_signature: str
    role: InclinationEvidenceRole
    signal: float
    observed_at: datetime
    accepted_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "evidence_id",
            "inclination_id",
            "reflection_source_id",
            "affective_transition_id",
            "affective_signal_hash",
            "source_message_id",
            "source_interaction_id",
            "source_session_id",
            "source_counterparty_id",
            "content_hash",
            "content_signature",
        ):
            maximum = 64 if "hash" in field_name or field_name == "content_signature" else 128
            object.__setattr__(
                self, field_name, non_blank(getattr(self, field_name), field_name, maximum=maximum)
            )
        positive_version(self.affective_state_version, "affective_state_version")
        if (
            isinstance(self.signal, bool)
            or not math.isfinite(self.signal)
            or not -1 <= self.signal <= 1
        ):
            raise ValueError("inclination evidence signal must be in [-1, 1]")
        object.__setattr__(self, "observed_at", aware_utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "accepted_at", aware_utc(self.accepted_at, "accepted_at"))


@dataclass(frozen=True, slots=True)
class InclinationRevision:
    revision_id: str
    inclination_id: str
    inclination_version: int
    reflection_outcome_id: str
    kind: InclinationRevisionKind
    prior_score: float | None
    new_score: float
    applied_delta: float
    prior_confidence: float | None
    new_confidence: float
    prior_stability: float | None
    new_stability: float
    state_as_of: datetime
    reason_code: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("revision_id", "inclination_id", "reflection_outcome_id"):
            object.__setattr__(
                self, field_name, non_blank(getattr(self, field_name), field_name, maximum=128)
            )
        positive_version(self.inclination_version, "inclination_version")
        for field_name in ("new_score", "applied_delta"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not math.isfinite(value) or not -1 <= value <= 1:
                raise ValueError(f"{field_name} must be in [-1, 1]")
        if self.prior_score is not None and (
            isinstance(self.prior_score, bool)
            or not math.isfinite(self.prior_score)
            or not -1 <= self.prior_score <= 1
        ):
            raise ValueError("prior_score must be in [-1, 1]")
        if self.prior_confidence is not None:
            unit_interval(self.prior_confidence, "prior_confidence")
        if self.prior_stability is not None:
            unit_interval(self.prior_stability, "prior_stability")
        unit_interval(self.new_confidence, "new_confidence")
        unit_interval(self.new_stability, "new_stability")
        object.__setattr__(self, "state_as_of", aware_utc(self.state_as_of, "state_as_of"))
        object.__setattr__(
            self, "reason_code", non_blank(self.reason_code, "reason_code", maximum=64)
        )
        object.__setattr__(self, "occurred_at", aware_utc(self.occurred_at, "occurred_at"))


@dataclass(frozen=True, slots=True)
class SatoriInclination:
    inclination_id: str
    inclination_key: str
    identity_id: str
    schema_version: int
    aggregate_version: int
    policy_version: int
    normalization_version: int
    kind: InclinationKind
    topic: str
    normalized_topic: str
    alternative_topic: str | None
    normalized_alternative_topic: str | None
    score: float
    confidence: float
    stability: float
    state_as_of: datetime
    last_accepted_at: datetime
    created_at: datetime
    updated_at: datetime
    evidence: tuple[InclinationEvidence, ...]
    revisions: tuple[InclinationRevision, ...]

    def __post_init__(self) -> None:
        for field_name in ("inclination_id", "inclination_key", "identity_id"):
            object.__setattr__(
                self, field_name, non_blank(getattr(self, field_name), field_name, maximum=128)
            )
        for field_name in (
            "schema_version",
            "aggregate_version",
            "policy_version",
            "normalization_version",
        ):
            positive_version(getattr(self, field_name), field_name)
        object.__setattr__(self, "topic", non_blank(self.topic, "topic", maximum=96))
        object.__setattr__(
            self,
            "normalized_topic",
            non_blank(self.normalized_topic, "normalized_topic", maximum=96),
        )
        if (
            isinstance(self.score, bool)
            or not math.isfinite(self.score)
            or not -1 <= self.score <= 1
        ):
            raise ValueError("inclination score must be in [-1, 1]")
        unit_interval(self.confidence, "inclination confidence")
        unit_interval(self.stability, "inclination stability")
        if self.kind is InclinationKind.INTEREST:
            if self.alternative_topic is not None or self.normalized_alternative_topic is not None:
                raise ValueError("interest cannot have an alternative topic")
            if self.score < 0:
                raise ValueError("interest score cannot be negative")
        else:
            if self.alternative_topic is None or self.normalized_alternative_topic is None:
                raise ValueError("preference requires an alternative topic")
            object.__setattr__(
                self,
                "alternative_topic",
                non_blank(self.alternative_topic, "alternative_topic", maximum=96),
            )
            object.__setattr__(
                self,
                "normalized_alternative_topic",
                non_blank(
                    self.normalized_alternative_topic,
                    "normalized_alternative_topic",
                    maximum=96,
                ),
            )
        for field_name in ("state_as_of", "last_accepted_at", "created_at", "updated_at"):
            object.__setattr__(self, field_name, aware_utc(getattr(self, field_name), field_name))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "revisions", tuple(self.revisions))


@dataclass(frozen=True, slots=True)
class InclinationEvaluation:
    kind: InclinationDecisionKind
    reason_code: str
    inclination: SatoriInclination | None = None
    new_evidence: tuple[InclinationEvidence, ...] = ()
    revision: InclinationRevision | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reason_code", non_blank(self.reason_code, "reason_code", maximum=64)
        )
        object.__setattr__(self, "new_evidence", tuple(self.new_evidence))
        applied = self.kind is InclinationDecisionKind.APPLIED
        if applied != (self.inclination is not None and self.revision is not None):
            raise ValueError("only applied inclination evaluation contains state and revision")
        if not applied and self.new_evidence:
            raise ValueError("rejected inclination evaluation cannot contain evidence")


@dataclass(frozen=True, slots=True)
class _EligibleDraft:
    source: InclinationEvidenceSource
    role: InclinationEvidenceRole
    signal: float
    signature: str


def materialize_inclination_score(inclination: SatoriInclination, *, at: datetime) -> float:
    """Pure neutral-centred decay; reads never mutate the anchor."""

    return project_inclination_score(
        score=inclination.score,
        stability=inclination.stability,
        kind=inclination.kind,
        state_as_of=inclination.state_as_of,
        at=at,
    )


def project_inclination_score(
    *,
    score: float,
    stability: float,
    kind: InclinationKind,
    state_as_of: datetime,
    at: datetime,
) -> float:
    """Project a compact state reference with the same authoritative decay policy."""

    normalized_at = aware_utc(at, "inclination materialization time")
    anchor = aware_utc(state_as_of, "inclination state_as_of")
    if isinstance(score, bool) or not math.isfinite(score) or not -1 <= score <= 1:
        raise ValueError("inclination score must be in [-1, 1]")
    unit_interval(stability, "inclination stability")
    if kind is InclinationKind.INTEREST and score < 0:
        raise ValueError("interest score cannot be negative")
    if normalized_at < anchor:
        raise ValueError("inclination cannot be materialized backwards in time")
    elapsed_days = (normalized_at - anchor).total_seconds() / 86400.0
    half_life = (
        30.0 + 90.0 * stability if kind is InclinationKind.INTEREST else 90.0 + 270.0 * stability
    )
    return float(score * 2.0 ** (-elapsed_days / half_life))


def evaluate_inclination(
    proposal: InclinationProposal,
    *,
    identity_id: str,
    sources: tuple[InclinationEvidenceSource, ...],
    existing_inclinations: tuple[SatoriInclination, ...],
    reflection_outcome_id: str,
    now: datetime,
    new_id: Callable[[], str],
) -> InclinationEvaluation:
    """Apply ADR-0026 policy without persistence or provider authority."""

    identity_id = non_blank(identity_id, "identity_id", maximum=128)
    now = aware_utc(now, "now")
    reflection_outcome_id = non_blank(reflection_outcome_id, "reflection_outcome_id", maximum=128)
    if proposal.confidence < MIN_PROVIDER_CONFIDENCE:
        return _rejected("provider_confidence_too_low")

    topic = proposal.topic.strip()
    normalized_topic = normalize_inclination_label(topic)
    alternative = proposal.alternative_topic.strip() if proposal.alternative_topic else None
    normalized_alternative = (
        normalize_inclination_label(alternative) if alternative is not None else None
    )
    if normalized_alternative == normalized_topic:
        return _rejected("inclination_topics_not_distinct")
    if normalized_alternative is not None and normalized_alternative < normalized_topic:
        assert alternative is not None
        topic, alternative = alternative, topic
        normalized_topic, normalized_alternative = normalized_alternative, normalized_topic
    key = inclination_key(proposal.kind, normalized_topic, normalized_alternative)

    same_identity = tuple(item for item in existing_inclinations if item.identity_id == identity_id)
    exact = next((item for item in same_identity if item.inclination_key == key), None)
    if exact is None and proposal.target_inclination_id is not None:
        return _rejected("inclination_target_not_found")
    if exact is not None and (
        proposal.target_inclination_id != exact.inclination_id
        or proposal.expected_target_version != exact.aggregate_version
    ):
        return _rejected("inclination_target_version_conflict")
    if exact is None and any(
        item.inclination_id == proposal.target_inclination_id for item in same_identity
    ):
        return _rejected("inclination_target_key_mismatch")

    source_by_id = {item.source_id: item for item in sources}
    if len(source_by_id) != len(sources) or any(
        item.identity_id != identity_id for item in sources
    ):
        return _rejected("inclination_source_identity_mismatch")
    if any(source_id not in source_by_id for source_id in proposal.source_ids):
        return _rejected("inclination_source_outside_fixed_set")

    existing_evidence = exact.evidence if exact is not None else ()
    seen_messages = {item.source_message_id for item in existing_evidence}
    seen_interactions = {item.source_interaction_id for item in existing_evidence}
    seen_transitions = {item.affective_transition_id for item in existing_evidence}
    seen_signatures = {item.content_signature for item in existing_evidence}
    drafts: list[_EligibleDraft] = []
    for source_id in proposal.source_ids:
        source = source_by_id[source_id]
        signature = _signature(source.quote)
        if source.observed_at > now:
            continue
        if _DIRECT_ASSIGNMENT.search(source.quote) or _RELATIONSHIP_CONTAMINATION.search(
            source.quote
        ):
            continue
        if (
            source.root_message_id in seen_messages
            or source.root_interaction_id in seen_interactions
            or source.affective.transition_id in seen_transitions
            or signature in seen_signatures
        ):
            continue
        topic_match = _topic_matches(source.quote, normalized_topic)
        alternative_match = (
            _topic_matches(source.quote, normalized_alternative)
            if normalized_alternative is not None
            else False
        )
        if proposal.kind is InclinationKind.INTEREST:
            if not topic_match:
                continue
            role = InclinationEvidenceRole.TOPIC
            signal = _experience_signal(source)
        else:
            if topic_match == alternative_match:
                continue
            role = (
                InclinationEvidenceRole.OPTION_A
                if topic_match
                else InclinationEvidenceRole.OPTION_B
            )
            signal = _utility_signal(source)
        seen_messages.add(source.root_message_id)
        seen_interactions.add(source.root_interaction_id)
        seen_transitions.add(source.affective.transition_id)
        seen_signatures.add(signature)
        drafts.append(_EligibleDraft(source=source, role=role, signal=signal, signature=signature))

    gate_reason = _diversity_reason(proposal.kind, tuple(drafts), creating=exact is None)
    if gate_reason is not None:
        return _rejected(gate_reason)
    if exact is not None:
        cooldown = timedelta(days=7 if proposal.kind is InclinationKind.INTEREST else 14)
        if now < exact.last_accepted_at + cooldown:
            return _rejected("inclination_cooldown")

    raw_delta: float
    if proposal.kind is InclinationKind.INTEREST:
        mean_signal = sum(item.signal for item in drafts) / len(drafts)
        if exact is None and mean_signal < INTEREST_FORMATION_SIGNAL:
            return _rejected("insufficient_positive_formation_signal")
        raw_delta = mean_signal * 0.30
        lower_cap = INTEREST_EVENT_DECREASE_CAP * proposal.confidence
        upper_cap = INTEREST_EVENT_INCREASE_CAP * proposal.confidence
        delta = _clamp(raw_delta, -lower_cap, upper_cap)
        rolling_cap = INTEREST_ROLLING_CAP
    else:
        option_a = [item.signal for item in drafts if item.role is InclinationEvidenceRole.OPTION_A]
        option_b = [item.signal for item in drafts if item.role is InclinationEvidenceRole.OPTION_B]
        difference = sum(option_a) / len(option_a) - sum(option_b) / len(option_b)
        if exact is None and abs(difference) < PREFERENCE_FORMATION_DIFFERENCE:
            return _rejected("insufficient_comparative_formation_signal")
        raw_delta = difference * 0.25
        event_cap = PREFERENCE_EVENT_CAP * proposal.confidence
        delta = _clamp(raw_delta, -event_cap, event_cap)
        rolling_cap = PREFERENCE_ROLLING_CAP

    recent_spend = (
        sum(
            abs(item.applied_delta)
            for item in exact.revisions
            if item.occurred_at > now - ROLLING_WINDOW
        )
        if exact is not None
        else 0.0
    )
    remaining = max(0.0, rolling_cap - recent_spend)
    delta = _clamp(delta, -remaining, remaining)
    if abs(delta) < MIN_MATERIAL_DELTA:
        return _rejected("inclination_delta_immaterial_or_budget_exhausted")

    inclination_id = exact.inclination_id if exact is not None else new_id()
    new_evidence = tuple(
        InclinationEvidence(
            evidence_id=new_id(),
            inclination_id=inclination_id,
            reflection_source_id=item.source.source_id,
            affective_transition_id=item.source.affective.transition_id,
            affective_state_version=item.source.affective.resulting_state_version,
            affective_signal_hash=item.source.affective.signal_hash,
            source_message_id=item.source.root_message_id,
            source_interaction_id=item.source.root_interaction_id,
            source_session_id=item.source.root_session_id,
            source_counterparty_id=item.source.root_counterparty_id,
            content_hash=item.source.content_hash,
            content_signature=item.signature,
            role=item.role,
            signal=round(item.signal, 6),
            observed_at=item.source.observed_at,
            accepted_at=now,
        )
        for item in drafts
    )
    all_evidence = (*existing_evidence, *new_evidence)
    stability = _stability(all_evidence)
    confidence = _confidence(all_evidence, stability, proposal.confidence)
    prior_score = materialize_inclination_score(exact, at=now) if exact is not None else 0.0
    lower_bound = 0.0 if proposal.kind is InclinationKind.INTEREST else -1.0
    score = _clamp(prior_score + delta, lower_bound, 1.0)
    applied_delta = score - prior_score
    if abs(applied_delta) < MIN_MATERIAL_DELTA:
        return _rejected("inclination_delta_immaterial_or_bound_saturated")

    aggregate_version = exact.aggregate_version + 1 if exact is not None else 1
    revision_kind = (
        InclinationRevisionKind.CREATED
        if exact is None
        else (
            InclinationRevisionKind.STRENGTHENED
            if abs(score) > abs(prior_score)
            else InclinationRevisionKind.WEAKENED
        )
    )
    reason_code = {
        InclinationRevisionKind.CREATED: "eligible_inclination_created",
        InclinationRevisionKind.STRENGTHENED: "inclination_strengthened",
        InclinationRevisionKind.WEAKENED: "inclination_weakened",
    }[revision_kind]
    revision = InclinationRevision(
        revision_id=new_id(),
        inclination_id=inclination_id,
        inclination_version=aggregate_version,
        reflection_outcome_id=reflection_outcome_id,
        kind=revision_kind,
        prior_score=round(prior_score, 6) if exact is not None else None,
        new_score=round(score, 6),
        applied_delta=round(applied_delta, 6),
        prior_confidence=exact.confidence if exact is not None else None,
        new_confidence=confidence,
        prior_stability=exact.stability if exact is not None else None,
        new_stability=stability,
        state_as_of=now,
        reason_code=reason_code,
        occurred_at=now,
    )
    inclination = SatoriInclination(
        inclination_id=inclination_id,
        inclination_key=key,
        identity_id=identity_id,
        schema_version=INCLINATION_SCHEMA_VERSION,
        aggregate_version=aggregate_version,
        policy_version=INCLINATION_POLICY_VERSION,
        normalization_version=INCLINATION_NORMALIZATION_VERSION,
        kind=proposal.kind,
        topic=topic,
        normalized_topic=normalized_topic,
        alternative_topic=alternative,
        normalized_alternative_topic=normalized_alternative,
        score=round(score, 6),
        confidence=confidence,
        stability=stability,
        state_as_of=now,
        last_accepted_at=now,
        created_at=exact.created_at if exact is not None else now,
        updated_at=now,
        evidence=all_evidence,
        revisions=(*exact.revisions, revision) if exact is not None else (revision,),
    )
    return InclinationEvaluation(
        kind=InclinationDecisionKind.APPLIED,
        reason_code=reason_code,
        inclination=inclination,
        new_evidence=new_evidence,
        revision=revision,
    )


def _diversity_reason(
    kind: InclinationKind, drafts: tuple[_EligibleDraft, ...], *, creating: bool
) -> str | None:
    roots = {item.source.root_message_id for item in drafts}
    interactions = {item.source.root_interaction_id for item in drafts}
    sessions = {item.source.root_session_id for item in drafts}
    signatures = {item.signature for item in drafts}
    if kind is InclinationKind.INTEREST:
        minimum_roots = 3 if creating else 2
        minimum_signatures = 2
        if (
            len(roots) < minimum_roots
            or len(interactions) < minimum_roots
            or len(sessions) < 2
            or len(signatures) < minimum_signatures
        ):
            return "insufficient_inclination_evidence_diversity"
        if creating and _observation_span(drafts) < timedelta(days=7):
            return "inclination_observation_span_too_short"
        return None
    option_a = sum(item.role is InclinationEvidenceRole.OPTION_A for item in drafts)
    option_b = sum(item.role is InclinationEvidenceRole.OPTION_B for item in drafts)
    if (
        len(roots) < 4
        or len(interactions) < 4
        or len(sessions) < 2
        or len(signatures) < 3
        or option_a < 2
        or option_b < 2
    ):
        return "insufficient_preference_evidence_diversity"
    if creating and _observation_span(drafts) < timedelta(days=14):
        return "inclination_observation_span_too_short"
    return None


def _observation_span(drafts: tuple[_EligibleDraft, ...]) -> timedelta:
    times = [item.source.observed_at for item in drafts]
    return max(times) - min(times)


def _experience_signal(source: InclinationEvidenceSource) -> float:
    signal = source.affective
    return _clamp(
        (
            0.45 * signal.interest_signal
            + 0.30 * signal.curiosity_signal
            + 0.15 * signal.novelty
            + 0.10 * signal.pleasantness
            - 0.35 * signal.frustration_signal
        )
        * signal.salience
        * signal.appraisal_confidence,
        -1.0,
        1.0,
    )


def _utility_signal(source: InclinationEvidenceSource) -> float:
    signal = source.affective
    return _clamp(
        (
            0.55 * signal.pleasantness
            + 0.20 * signal.interest_signal
            + 0.10 * signal.curiosity_signal
            - 0.25 * signal.frustration_signal
            - 0.10 * signal.concern_signal
        )
        * signal.salience
        * signal.appraisal_confidence,
        -1.0,
        1.0,
    )


def _stability(evidence: tuple[InclinationEvidence, ...]) -> float:
    roots = len({item.source_message_id for item in evidence})
    sessions = len({item.source_session_id for item in evidence})
    observed = [item.observed_at for item in evidence]
    span_days = (max(observed) - min(observed)).total_seconds() / 86400.0
    value = (
        0.50 * min(1.0, roots / 12.0)
        + 0.30 * min(1.0, sessions / 6.0)
        + 0.20 * min(1.0, span_days / 90.0)
    )
    return round(_clamp(value, 0.0, 1.0), 6)


def _confidence(
    evidence: tuple[InclinationEvidence, ...], stability: float, provider_confidence: float
) -> float:
    roots = len({item.source_message_id for item in evidence})
    sessions = len({item.source_session_id for item in evidence})
    derived = 0.35 + 0.06 * min(roots, 6) + 0.05 * min(sessions, 4) + 0.10 * stability
    return round(min(provider_confidence, 0.90, derived), 6)


def _topic_matches(quote: str, normalized_topic: str | None) -> bool:
    if normalized_topic is None:
        return False
    lexical = _normalize_lexical(quote)
    return f" {normalized_topic} " in f" {lexical} "


def _normalize_lexical(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _signature(quote: str) -> str:
    normalized = unicodedata.normalize("NFKC", quote).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _rejected(reason_code: str) -> InclinationEvaluation:
    return InclinationEvaluation(
        kind=InclinationDecisionKind.REJECTED,
        reason_code=reason_code,
    )
