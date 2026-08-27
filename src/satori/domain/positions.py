"""PositionManager: deterministic Stage 11 evidence and revision policy."""

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from satori.core.inclinations import (
    InclinationEvidenceSource,
    InclinationProposal,
)
from satori.core.positions import (
    PositionEvidenceCitation,
    PositionEvidenceRole,
    PositionKind,
    PositionProposal,
    PositionSourceMessage,
    PositionStance,
)
from satori.domain.inclinations import (
    InclinationEvaluation,
    SatoriInclination,
    evaluate_inclination,
)
from satori.domain.validation import aware_utc, non_blank, positive_version, unit_interval

POSITION_SCHEMA_VERSION = 1
POSITION_FORMATION_VERSION = 1
POSITION_POLICY_VERSION = 1
POSITION_NORMALIZATION_VERSION = 1

_MATERIALITY_CUES = re.compile(
    r"(?:потому\s+что|так\s+как|поскольку|из-за|поэтому|следовательно|"
    r"данн(?:ые|ых)|исследован|наблюд|провер|пример|свидетельств|"
    r"because|since|therefore|evidence|data|research|observ|experiment|example)",
    re.IGNORECASE,
)


class PositionStatus(StrEnum):
    ACTIVE = "active"
    COMPETING = "competing"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class PositionDecisionKind(StrEnum):
    APPLIED = "applied"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class PositionRevisionKind(StrEnum):
    CREATED = "created"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    COMPETING = "competing"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class PositionEvaluationOrigin(StrEnum):
    INTERACTION = "interaction"
    REFLECTION = "reflection"


def normalize_position_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", non_blank(value, "position text", maximum=512))
    return re.sub(r"\s+", " ", normalized.casefold().strip())


def position_key(proposal: PositionProposal) -> str:
    material = {
        "kind": proposal.kind.value,
        "normalization_version": POSITION_NORMALIZATION_VERSION,
        "proposition": normalize_position_text(proposal.proposition),
        "stance": proposal.stance.value,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def position_idempotency_key(source_interaction_id: str, formation_version: int) -> str:
    return f"positions:{source_interaction_id}:formation:{formation_version}"


@dataclass(frozen=True, slots=True)
class PositionEvidence:
    evidence_id: str
    position_id: str
    source_message_id: str
    source_interaction_id: str
    source_counterparty_id: str
    quote: str
    normalized_signature: str
    role: PositionEvidenceRole
    observed_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "evidence_id",
            "position_id",
            "source_message_id",
            "source_interaction_id",
            "source_counterparty_id",
        ):
            object.__setattr__(
                self, field_name, non_blank(getattr(self, field_name), field_name, maximum=128)
            )
        object.__setattr__(self, "quote", non_blank(self.quote, "evidence quote", maximum=512))
        object.__setattr__(
            self,
            "normalized_signature",
            non_blank(self.normalized_signature, "evidence signature", maximum=512),
        )
        object.__setattr__(self, "observed_at", aware_utc(self.observed_at, "observed_at"))


@dataclass(frozen=True, slots=True)
class SatoriPosition:
    position_id: str
    position_key: str
    identity_id: str
    schema_version: int
    aggregate_version: int
    policy_version: int
    formation_version: int
    normalization_version: int
    proposition: str
    normalized_proposition: str
    kind: PositionKind
    stance: PositionStance
    confidence: float
    status: PositionStatus
    value_key: str | None
    competing_with_position_id: str | None
    superseded_by_position_id: str | None
    created_at: datetime
    updated_at: datetime
    evidence: tuple[PositionEvidence, ...]

    def __post_init__(self) -> None:
        for field_name in ("position_id", "position_key", "identity_id"):
            object.__setattr__(
                self, field_name, non_blank(getattr(self, field_name), field_name, maximum=128)
            )
        for field_name in (
            "schema_version",
            "aggregate_version",
            "policy_version",
            "formation_version",
            "normalization_version",
        ):
            positive_version(getattr(self, field_name), field_name)
        object.__setattr__(
            self, "proposition", non_blank(self.proposition, "proposition", maximum=240)
        )
        object.__setattr__(
            self,
            "normalized_proposition",
            non_blank(self.normalized_proposition, "normalized proposition", maximum=240),
        )
        unit_interval(self.confidence, "position confidence")
        for field_name in (
            "value_key",
            "competing_with_position_id",
            "superseded_by_position_id",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, non_blank(value, field_name, maximum=128))
        if self.kind is PositionKind.OPINION and self.value_key is None:
            raise ValueError("opinion requires value_key")
        if self.kind is not PositionKind.OPINION and self.value_key is not None:
            raise ValueError("only opinion may reference a value")
        if self.status is PositionStatus.COMPETING and self.competing_with_position_id is None:
            raise ValueError("competing position requires a peer")
        if self.status is not PositionStatus.COMPETING and self.competing_with_position_id:
            raise ValueError("non-competing position cannot have a peer")
        if self.status is PositionStatus.SUPERSEDED and self.superseded_by_position_id is None:
            raise ValueError("superseded position requires replacement")
        if self.status is not PositionStatus.SUPERSEDED and self.superseded_by_position_id:
            raise ValueError("non-superseded position cannot have replacement")
        object.__setattr__(self, "created_at", aware_utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", aware_utc(self.updated_at, "updated_at"))
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True, slots=True)
class PositionRevision:
    revision_id: str
    position_id: str
    position_version: int
    decision_id: str | None
    kind: PositionRevisionKind
    prior_status: PositionStatus | None
    new_status: PositionStatus
    prior_confidence: float | None
    new_confidence: float
    reason_code: str
    occurred_at: datetime
    reflection_outcome_id: str | None = None


@dataclass(frozen=True, slots=True)
class PositionFormationDecision:
    decision_id: str
    idempotency_key: str
    source_interaction_id: str
    source_message_id: str
    identity_id: str
    formation_version: int
    policy_version: int
    kind: PositionDecisionKind
    reason_code: str
    created_count: int
    merged_count: int
    superseded_count: int
    competing_count: int
    rejected_count: int
    position_ids: tuple[str, ...]
    decided_at: datetime
    trace_id: str
    formation_method: str
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class PositionFormationPlan:
    positions: tuple[SatoriPosition, ...]
    revisions: tuple[PositionRevision, ...]
    created_count: int = 0
    merged_count: int = 0
    superseded_count: int = 0
    competing_count: int = 0
    rejected_count: int = 0


def _eligible_evidence(
    citations: tuple[PositionEvidenceCitation, ...],
    *,
    sources: tuple[PositionSourceMessage, ...],
    position_id: str,
    new_id: Callable[[], str],
) -> tuple[PositionEvidence, ...]:
    source_by_id = {item.message_id: item for item in sources}
    accepted: list[PositionEvidence] = []
    seen_messages: set[str] = set()
    seen_signatures: set[str] = set()
    for citation in citations:
        source = source_by_id.get(citation.message_id)
        signature = normalize_position_text(citation.quote)
        if (
            source is None
            or citation.quote not in source.content
            or citation.role is PositionEvidenceRole.VERIFIED_RECORD
            or not (
                _MATERIALITY_CUES.search(citation.quote) or _MATERIALITY_CUES.search(source.content)
            )
            or source.message_id in seen_messages
            or signature in seen_signatures
        ):
            continue
        seen_messages.add(source.message_id)
        seen_signatures.add(signature)
        accepted.append(
            PositionEvidence(
                evidence_id=new_id(),
                position_id=position_id,
                source_message_id=source.message_id,
                source_interaction_id=source.interaction_id,
                source_counterparty_id=source.counterparty_id,
                quote=citation.quote,
                normalized_signature=signature,
                role=citation.role,
                observed_at=source.observed_at,
            )
        )
    return tuple(accepted)


def _confidence(kind: PositionKind, roots: int, provider_confidence: float) -> float:
    if kind is PositionKind.HYPOTHESIS:
        cap = min(0.50, 0.35 + 0.05 * max(0, roots - 1))
    elif kind is PositionKind.BELIEF:
        cap = min(0.80, 0.55 + 0.05 * max(0, roots - 2))
    elif kind is PositionKind.OPINION:
        cap = min(0.75, 0.50 + 0.05 * max(0, roots - 2))
    else:
        cap = 0.98
    return round(min(provider_confidence, cap), 6)


class PositionManager:
    """The sole deterministic writer for identity-global Satori positions."""

    def evaluate_inclination(
        self,
        proposal: InclinationProposal,
        *,
        identity_id: str,
        sources: tuple[InclinationEvidenceSource, ...],
        existing_inclinations: tuple[SatoriInclination, ...],
        reflection_outcome_id: str,
        now: datetime,
        new_id: Callable[[], str],
    ) -> InclinationEvaluation:
        """Evaluate one Stage 13 candidate through the shared positions owner boundary."""

        return evaluate_inclination(
            proposal,
            identity_id=identity_id,
            sources=sources,
            existing_inclinations=existing_inclinations,
            reflection_outcome_id=reflection_outcome_id,
            now=now,
            new_id=new_id,
        )

    def evaluate(
        self,
        proposals: tuple[PositionProposal, ...],
        *,
        identity_id: str,
        current_message_id: str,
        sources: tuple[PositionSourceMessage, ...],
        value_keys: frozenset[str],
        existing_positions: tuple[SatoriPosition, ...],
        max_positions: int,
        now: datetime,
        decision_id: str | None,
        new_id: Callable[[], str],
        origin: PositionEvaluationOrigin = PositionEvaluationOrigin.INTERACTION,
        reflection_outcome_id: str | None = None,
    ) -> PositionFormationPlan:
        identity_id = non_blank(identity_id, "identity_id", maximum=128)
        current_message_id = non_blank(current_message_id, "current_message_id", maximum=128)
        aware_utc(now, "now")
        if max_positions < 1:
            raise ValueError("max_positions must be positive")
        if origin is PositionEvaluationOrigin.INTERACTION and (
            decision_id is None or reflection_outcome_id is not None
        ):
            raise ValueError("interaction position evaluation requires only decision_id")
        if origin is PositionEvaluationOrigin.REFLECTION and (
            decision_id is not None or reflection_outcome_id is None
        ):
            raise ValueError("reflection position evaluation requires only reflection_outcome_id")
        current = {
            item.position_id: item
            for item in existing_positions
            if item.identity_id == identity_id
            and item.status in {PositionStatus.ACTIVE, PositionStatus.COMPETING}
        }
        changed: dict[str, SatoriPosition] = {}
        revisions: list[PositionRevision] = []
        created = merged = superseded = competing = rejected = 0

        for proposal in proposals[:max_positions]:
            if proposal.kind is PositionKind.FACT:
                rejected += 1
                continue
            if (
                proposal.kind is PositionKind.HYPOTHESIS
                and proposal.stance is not PositionStance.UNCERTAIN
            ):
                rejected += 1
                continue
            if proposal.kind is PositionKind.OPINION:
                if proposal.value_key is None or proposal.value_key not in value_keys:
                    rejected += 1
                    continue
            elif proposal.value_key is not None:
                rejected += 1
                continue
            if origin is PositionEvaluationOrigin.INTERACTION and current_message_id not in {
                item.message_id for item in proposal.evidence
            }:
                rejected += 1
                continue

            target_id = (
                proposal.revises_position_id
                or proposal.opposes_position_id
                or proposal.challenges_position_id
            )
            target = current.get(target_id) if target_id is not None else None
            if target_id is not None and (
                target is None
                or proposal.expected_target_version != target.aggregate_version
                or target.kind is not proposal.kind
            ):
                rejected += 1
                continue
            new_position_id = new_id()
            evidence = _eligible_evidence(
                proposal.evidence, sources=sources, position_id=new_position_id, new_id=new_id
            )
            interaction_ids = {item.source_interaction_id for item in evidence}
            signatures = {item.normalized_signature for item in evidence}
            key = position_key(proposal)
            exact = next(
                (
                    item
                    for item in current.values()
                    if item.position_key == key and item.position_id != target_id
                ),
                None,
            )
            if origin is PositionEvaluationOrigin.REFLECTION:
                required = (
                    2
                    if proposal.kind is PositionKind.HYPOTHESIS
                    or exact is not None
                    or proposal.challenges_position_id is not None
                    else 3
                )
            else:
                required = (
                    1
                    if proposal.kind is PositionKind.HYPOTHESIS
                    or exact is not None
                    or proposal.challenges_position_id is not None
                    else 2
                )
            if (
                len(evidence) < required
                or len(interaction_ids) < required
                or len(signatures) < required
            ):
                rejected += 1
                continue

            if proposal.challenges_position_id is not None:
                assert target is not None
                if proposal.kind not in {PositionKind.BELIEF, PositionKind.OPINION} or any(
                    item.role is not PositionEvidenceRole.COUNTEREXAMPLE
                    for item in proposal.evidence
                ):
                    rejected += 1
                    continue
                existing_messages = {item.source_message_id for item in target.evidence}
                existing_signatures = {item.normalized_signature for item in target.evidence}
                additions = tuple(
                    replace(item, position_id=target.position_id)
                    for item in evidence
                    if item.source_message_id not in existing_messages
                    and item.normalized_signature not in existing_signatures
                )
                if not additions:
                    continue
                updated = replace(
                    target,
                    aggregate_version=target.aggregate_version + 1,
                    confidence=round(max(0.20, target.confidence - 0.10 * len(additions)), 6),
                    updated_at=now,
                    evidence=(*target.evidence, *additions),
                )
                changed[updated.position_id] = updated
                current[updated.position_id] = updated
                revisions.append(
                    self._revision(
                        updated,
                        prior=target,
                        decision_id=decision_id,
                        reflection_outcome_id=reflection_outcome_id,
                        kind=PositionRevisionKind.WEAKENED,
                        reason_code="new_independent_counterevidence",
                        now=now,
                        new_id=new_id,
                    )
                )
                merged += 1
                continue

            if target is None and exact is not None:
                existing_messages = {item.source_message_id for item in exact.evidence}
                existing_signatures = {item.normalized_signature for item in exact.evidence}
                additions = tuple(
                    replace(item, position_id=exact.position_id)
                    for item in evidence
                    if item.source_message_id not in existing_messages
                    and item.normalized_signature not in existing_signatures
                )
                if not additions:
                    continue
                all_evidence = (*exact.evidence, *additions)
                confidence = _confidence(proposal.kind, len(all_evidence), proposal.confidence)
                updated = replace(
                    exact,
                    aggregate_version=exact.aggregate_version + 1,
                    confidence=max(exact.confidence, confidence),
                    updated_at=now,
                    evidence=all_evidence,
                )
                changed[updated.position_id] = updated
                current[updated.position_id] = updated
                revisions.append(
                    self._revision(
                        updated,
                        prior=exact,
                        decision_id=decision_id,
                        reflection_outcome_id=reflection_outcome_id,
                        kind=PositionRevisionKind.STRENGTHENED,
                        reason_code="new_independent_material_evidence",
                        now=now,
                        new_id=new_id,
                    )
                )
                merged += 1
                continue

            if proposal.revises_position_id is not None:
                assert target is not None
                target_signatures = {item.normalized_signature for item in target.evidence}
                if not signatures - target_signatures or len(evidence) < len(target.evidence):
                    rejected += 1
                    continue

            status = PositionStatus.ACTIVE
            competing_with: str | None = None
            if proposal.opposes_position_id is not None:
                assert target is not None
                if proposal.kind is not PositionKind.HYPOTHESIS:
                    rejected += 1
                    continue
                status = PositionStatus.COMPETING
                competing_with = target.position_id

            position = SatoriPosition(
                position_id=new_position_id,
                position_key=key,
                identity_id=identity_id,
                schema_version=POSITION_SCHEMA_VERSION,
                aggregate_version=1,
                policy_version=POSITION_POLICY_VERSION,
                formation_version=POSITION_FORMATION_VERSION,
                normalization_version=POSITION_NORMALIZATION_VERSION,
                proposition=proposal.proposition,
                normalized_proposition=normalize_position_text(proposal.proposition),
                kind=proposal.kind,
                stance=proposal.stance,
                confidence=_confidence(proposal.kind, len(evidence), proposal.confidence),
                status=status,
                value_key=proposal.value_key,
                competing_with_position_id=competing_with,
                superseded_by_position_id=None,
                created_at=now,
                updated_at=now,
                evidence=evidence,
            )
            changed[position.position_id] = position
            current[position.position_id] = position
            revisions.append(
                self._revision(
                    position,
                    prior=None,
                    decision_id=decision_id,
                    reflection_outcome_id=reflection_outcome_id,
                    kind=PositionRevisionKind.CREATED,
                    reason_code="eligible_position_created",
                    now=now,
                    new_id=new_id,
                )
            )
            created += 1

            if proposal.revises_position_id is not None:
                assert target is not None
                prior_target = changed.get(target.position_id, target)
                closed = replace(
                    prior_target,
                    aggregate_version=prior_target.aggregate_version + 1,
                    status=PositionStatus.SUPERSEDED,
                    competing_with_position_id=None,
                    superseded_by_position_id=position.position_id,
                    updated_at=now,
                )
                changed[closed.position_id] = closed
                current.pop(closed.position_id, None)
                revisions.append(
                    self._revision(
                        closed,
                        prior=prior_target,
                        decision_id=decision_id,
                        reflection_outcome_id=reflection_outcome_id,
                        kind=PositionRevisionKind.SUPERSEDED,
                        reason_code="explicit_stronger_revision",
                        now=now,
                        new_id=new_id,
                    )
                )
                superseded += 1
            elif proposal.opposes_position_id is not None:
                assert target is not None
                prior_target = changed.get(target.position_id, target)
                peer = replace(
                    prior_target,
                    aggregate_version=prior_target.aggregate_version + 1,
                    status=PositionStatus.COMPETING,
                    competing_with_position_id=position.position_id,
                    updated_at=now,
                )
                changed[peer.position_id] = peer
                current[peer.position_id] = peer
                revisions.append(
                    self._revision(
                        peer,
                        prior=prior_target,
                        decision_id=decision_id,
                        reflection_outcome_id=reflection_outcome_id,
                        kind=PositionRevisionKind.COMPETING,
                        reason_code="supported_competing_hypothesis",
                        now=now,
                        new_id=new_id,
                    )
                )
                competing += 1

        return PositionFormationPlan(
            positions=tuple(changed.values()),
            revisions=tuple(revisions),
            created_count=created,
            merged_count=merged,
            superseded_count=superseded,
            competing_count=competing,
            rejected_count=rejected,
        )

    @staticmethod
    def _revision(
        updated: SatoriPosition,
        *,
        prior: SatoriPosition | None,
        decision_id: str | None,
        reflection_outcome_id: str | None,
        kind: PositionRevisionKind,
        reason_code: str,
        now: datetime,
        new_id: Callable[[], str],
    ) -> PositionRevision:
        return PositionRevision(
            revision_id=new_id(),
            position_id=updated.position_id,
            position_version=updated.aggregate_version,
            decision_id=decision_id,
            reflection_outcome_id=reflection_outcome_id,
            kind=kind,
            prior_status=prior.status if prior is not None else None,
            new_status=updated.status,
            prior_confidence=prior.confidence if prior is not None else None,
            new_confidence=updated.confidence,
            reason_code=reason_code,
            occurred_at=now,
        )
