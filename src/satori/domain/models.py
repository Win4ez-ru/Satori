"""Stage 9 current user/world claim owners and deterministic temporal policy."""

# ruff: noqa: RUF001  # Russian evidence cues intentionally use Cyrillic.

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from satori.core.models import (
    ModelEpistemicKind,
    ModelEvidenceCitation,
    ModelScalar,
    ModelSourceMessage,
    ModelValueKind,
    UserModelClaimProposal,
    WorldModelClaimProposal,
)
from satori.domain.validation import aware_utc, non_blank, positive_version, unit_interval

MODEL_SCHEMA_VERSION = 1
MODEL_POLICY_VERSION = 1
MODEL_FORMATION_VERSION = 1
MODEL_NORMALIZATION_VERSION = 1


class ModelOwner(StrEnum):
    USER = "user"
    WORLD = "world"


class ModelClaimStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    RETRACTED = "retracted"
    EXPIRED = "expired"


class ModelRevisionKind(StrEnum):
    CREATED = "created"
    STRENGTHENED = "strengthened"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    RETRACTED = "retracted"
    EXPIRED = "expired"


class ModelDecisionKind(StrEnum):
    APPLIED = "applied"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class PredicateCardinality(StrEnum):
    SINGLE = "single"
    MULTI = "multi"


@dataclass(frozen=True, slots=True)
class UserPredicateDefinition:
    cardinality: PredicateCardinality
    allowed_value_kinds: frozenset[ModelValueKind]
    freshness_days: int | None


USER_MODEL_PREDICATES_V1: Mapping[str, UserPredicateDefinition] = {
    "display_name": UserPredicateDefinition(
        PredicateCardinality.SINGLE, frozenset({ModelValueKind.TEXT}), None
    ),
    "occupation": UserPredicateDefinition(
        PredicateCardinality.SINGLE, frozenset({ModelValueKind.TEXT}), 180
    ),
    "residence_city": UserPredicateDefinition(
        PredicateCardinality.SINGLE, frozenset({ModelValueKind.TEXT}), 180
    ),
    "goal": UserPredicateDefinition(
        PredicateCardinality.MULTI, frozenset({ModelValueKind.TEXT}), 180
    ),
    "project": UserPredicateDefinition(
        PredicateCardinality.MULTI, frozenset({ModelValueKind.TEXT}), 180
    ),
    "important_person": UserPredicateDefinition(
        PredicateCardinality.MULTI, frozenset({ModelValueKind.TEXT}), 180
    ),
}

WORLD_MODEL_STATUSES_V1: Mapping[str, frozenset[str]] = {
    "project": frozenset({"planned", "active", "paused", "completed", "cancelled"}),
    "situation": frozenset({"active", "resolved", "cancelled"}),
    "commitment": frozenset({"planned", "in_progress", "fulfilled", "broken", "cancelled"}),
    "outcome": frozenset({"pending", "occurred", "not_occurred", "cancelled"}),
}

WORLD_TERMINAL_STATUSES = frozenset(
    {"completed", "cancelled", "resolved", "fulfilled", "broken", "occurred", "not_occurred"}
)

WORLD_STATUS_CUES: Mapping[str, tuple[str, ...]] = {
    "planned": ("planned", "планир", "запланир"),
    "active": ("active", "актив", "начал", "начала", "занимаюсь", "в работе"),
    "paused": ("paused", "пауза", "приостанов"),
    "completed": ("completed", "заверш", "закончил", "закончила", "готов"),
    "cancelled": ("cancelled", "отмен", "отказал", "закрыл"),
    "resolved": ("resolved", "решен", "решён", "разреш"),
    "in_progress": ("in progress", "in_progress", "выполня", "в процессе"),
    "fulfilled": ("fulfilled", "выполн", "сдержал", "сдержала"),
    "broken": ("broken", "наруш", "не выпол"),
    "pending": ("pending", "ожида", "жду", "предстоит"),
    "occurred": ("occurred", "произош", "случил"),
    "not_occurred": ("not occurred", "not_occurred", "не произош", "не случил"),
}


def normalize_model_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е").strip()
    return re.sub(r"\s+", " ", normalized)


def normalize_model_value(kind: ModelValueKind, value: ModelScalar) -> str:
    if kind is ModelValueKind.TEXT:
        assert isinstance(value, str)
        return normalize_model_text(value)
    if kind is ModelValueKind.NUMBER:
        assert not isinstance(value, bool)
        assert isinstance(value, (int, float))
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("model numeric value must be finite")
        return str(int(number)) if number.is_integer() else format(number, ".15g")
    assert type(value) is bool
    return "true" if value else "false"


@dataclass(frozen=True, slots=True)
class ModelClaimEvidence:
    evidence_id: str
    owner: ModelOwner
    claim_id: str
    source_message_id: str
    source_interaction_id: str
    observed_at: datetime

    def __post_init__(self) -> None:
        for name in ("evidence_id", "claim_id", "source_message_id", "source_interaction_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        object.__setattr__(self, "observed_at", aware_utc(self.observed_at, "observed_at"))


@dataclass(frozen=True, slots=True)
class UserModelClaim:
    claim_id: str
    claim_key: str
    identity_id: str
    counterparty_id: str
    schema_version: int
    aggregate_version: int
    policy_version: int
    formation_version: int
    normalization_version: int
    predicate: str
    value_kind: ModelValueKind
    value: ModelScalar
    normalized_value: str
    epistemic_kind: ModelEpistemicKind
    confidence: float
    status: ModelClaimStatus
    valid_from: datetime
    valid_until: datetime | None
    last_observed_at: datetime
    expires_at: datetime | None
    superseded_by_claim_id: str | None
    created_at: datetime
    updated_at: datetime
    evidence: tuple[ModelClaimEvidence, ...]

    def __post_init__(self) -> None:
        _validate_claim(self)


@dataclass(frozen=True, slots=True)
class WorldModelClaim:
    claim_id: str
    claim_key: str
    identity_id: str
    counterparty_id: str
    schema_version: int
    aggregate_version: int
    policy_version: int
    formation_version: int
    normalization_version: int
    subject_kind: str
    subject_label: str
    normalized_subject_label: str
    predicate: str
    value_kind: ModelValueKind
    value: ModelScalar
    normalized_value: str
    epistemic_kind: ModelEpistemicKind
    confidence: float
    status: ModelClaimStatus
    valid_from: datetime
    valid_until: datetime | None
    last_observed_at: datetime
    expires_at: datetime | None
    superseded_by_claim_id: str | None
    created_at: datetime
    updated_at: datetime
    evidence: tuple[ModelClaimEvidence, ...]

    def __post_init__(self) -> None:
        _validate_claim(self)
        non_blank(self.subject_kind, "subject_kind", maximum=32)
        non_blank(self.subject_label, "subject_label", maximum=120)


ModelClaim = UserModelClaim | WorldModelClaim


def _validate_claim(claim: ModelClaim) -> None:
    for name in (
        "claim_id",
        "claim_key",
        "identity_id",
        "counterparty_id",
        "predicate",
        "normalized_value",
    ):
        non_blank(getattr(claim, name), name, maximum=128)
    positive_version(claim.schema_version, "model schema_version")
    positive_version(claim.aggregate_version, "model aggregate_version")
    positive_version(claim.policy_version, "model policy_version")
    positive_version(claim.formation_version, "model formation_version")
    positive_version(claim.normalization_version, "model normalization_version")
    unit_interval(claim.confidence, "model confidence")
    for name in ("valid_from", "last_observed_at", "created_at", "updated_at"):
        aware_utc(getattr(claim, name), name)
    if claim.valid_until is not None:
        aware_utc(claim.valid_until, "valid_until")
    if claim.expires_at is not None:
        aware_utc(claim.expires_at, "expires_at")
    if not claim.evidence:
        raise ValueError("model claim requires evidence")


@dataclass(frozen=True, slots=True)
class ModelClaimRevision:
    revision_id: str
    owner: ModelOwner
    claim_id: str
    claim_version: int
    decision_id: str
    kind: ModelRevisionKind
    prior_status: ModelClaimStatus | None
    new_status: ModelClaimStatus
    prior_confidence: float | None
    new_confidence: float
    prior_expires_at: datetime | None
    new_expires_at: datetime | None
    reason_code: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        for name in ("revision_id", "claim_id", "decision_id", "reason_code"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        positive_version(self.claim_version, "model claim_version")
        if self.prior_confidence is not None:
            unit_interval(self.prior_confidence, "prior_confidence")
        unit_interval(self.new_confidence, "new_confidence")
        for name in ("prior_expires_at", "new_expires_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, aware_utc(value, name))
        object.__setattr__(self, "occurred_at", aware_utc(self.occurred_at, "occurred_at"))
        if self.kind is ModelRevisionKind.CREATED:
            if self.prior_status is not None or self.prior_confidence is not None:
                raise ValueError("created model revision cannot have prior state")
        elif self.prior_status is None or self.prior_confidence is None:
            raise ValueError("non-created model revision requires prior state")


@dataclass(frozen=True, slots=True)
class ModelFormationDecision:
    """Terminal application record containing both independent owner outcomes."""

    decision_id: str
    idempotency_key: str
    source_interaction_id: str
    source_message_id: str
    identity_id: str
    counterparty_id: str
    formation_version: int
    policy_version: int
    kind: ModelDecisionKind
    reason_code: str
    user_created_count: int
    user_merged_count: int
    user_superseded_count: int
    user_disputed_count: int
    user_rejected_count: int
    world_created_count: int
    world_merged_count: int
    world_superseded_count: int
    world_disputed_count: int
    world_rejected_count: int
    user_claim_ids: tuple[str, ...]
    world_claim_ids: tuple[str, ...]
    decided_at: datetime
    trace_id: str
    formation_method: str
    provider: str
    model: str

    def __post_init__(self) -> None:
        for name in (
            "decision_id",
            "idempotency_key",
            "source_interaction_id",
            "source_message_id",
            "identity_id",
            "counterparty_id",
            "reason_code",
            "trace_id",
            "formation_method",
            "provider",
            "model",
        ):
            maximum = 256 if name in {"idempotency_key", "model"} else 128
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=maximum))
        positive_version(self.formation_version, "model formation_version")
        positive_version(self.policy_version, "model policy_version")
        for name in (
            "user_created_count",
            "user_merged_count",
            "user_superseded_count",
            "user_disputed_count",
            "user_rejected_count",
            "world_created_count",
            "world_merged_count",
            "world_superseded_count",
            "world_disputed_count",
            "world_rejected_count",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be non-negative")
        object.__setattr__(self, "decided_at", aware_utc(self.decided_at, "decided_at"))
        for name in ("user_claim_ids", "world_claim_ids"):
            values = tuple(
                non_blank(item, f"{name} item", maximum=128) for item in getattr(self, name)
            )
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
            object.__setattr__(self, name, values)


def model_idempotency_key(source_interaction_id: str, formation_version: int) -> str:
    return f"models:{source_interaction_id}:formation:{formation_version}"


@dataclass(frozen=True, slots=True)
class OwnerFormationPlan[TClaim: (UserModelClaim, WorldModelClaim)]:
    claims: tuple[TClaim, ...]
    revisions: tuple[ModelClaimRevision, ...]
    created_count: int = 0
    merged_count: int = 0
    superseded_count: int = 0
    disputed_count: int = 0
    rejected_count: int = 0


def model_claim_is_current(claim: ModelClaim, *, as_of: datetime) -> bool:
    aware_utc(as_of, "as_of")
    return claim.status is ModelClaimStatus.CURRENT and (
        claim.expires_at is None or as_of < claim.expires_at
    )


def _confidence(kind: ModelEpistemicKind, proposal: float, root_count: int) -> float:
    if kind is ModelEpistemicKind.EXPLICIT_FACT:
        cap = min(0.90 + 0.02 * (root_count - 1), 0.96)
    elif kind is ModelEpistemicKind.INFERENCE:
        cap = min(0.65 + 0.05 * (root_count - 2), 0.70)
    else:
        cap = 0.50
    return min(proposal, cap)


def _claim_key(material: Mapping[str, object]) -> str:
    payload = {**material, "normalization_version": MODEL_NORMALIZATION_VERSION}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _validate_sources(
    citations: tuple[ModelEvidenceCitation, ...],
    sources: Mapping[str, ModelSourceMessage],
    *,
    identity_id: str,
    counterparty_id: str,
    current_message_id: str,
    epistemic_kind: ModelEpistemicKind,
) -> tuple[ModelSourceMessage, ...] | None:
    if current_message_id not in {item.message_id for item in citations}:
        return None
    resolved: list[ModelSourceMessage] = []
    for citation in citations:
        source = sources.get(citation.message_id)
        if (
            source is None
            or source.identity_id != identity_id
            or source.counterparty_id != counterparty_id
        ):
            return None
        if citation.quote not in source.content:
            return None
        resolved.append(source)
    if epistemic_kind is ModelEpistemicKind.INFERENCE and (
        len({item.message_id for item in resolved}) < 2
        or len({item.interaction_id for item in resolved}) < 2
    ):
        return None
    return tuple(resolved)


def _evidence(
    owner: ModelOwner,
    claim_id: str,
    sources: Sequence[ModelSourceMessage],
    new_id: Callable[[], str],
) -> tuple[ModelClaimEvidence, ...]:
    return tuple(
        ModelClaimEvidence(
            new_id(), owner, claim_id, item.message_id, item.interaction_id, item.observed_at
        )
        for item in sources
    )


def _expiry(observed_at: datetime, freshness_days: int | None) -> datetime | None:
    return None if freshness_days is None else observed_at + timedelta(days=freshness_days)


def _revision(
    updated: ModelClaim,
    prior: ModelClaim | None,
    *,
    owner: ModelOwner,
    kind: ModelRevisionKind,
    reason_code: str,
    decision_id: str,
    occurred_at: datetime,
    new_id: Callable[[], str],
) -> ModelClaimRevision:
    return ModelClaimRevision(
        revision_id=new_id(),
        owner=owner,
        claim_id=updated.claim_id,
        claim_version=updated.aggregate_version,
        decision_id=decision_id,
        kind=kind,
        prior_status=prior.status if prior else None,
        new_status=updated.status,
        prior_confidence=prior.confidence if prior else None,
        new_confidence=updated.confidence,
        prior_expires_at=prior.expires_at if prior else None,
        new_expires_at=updated.expires_at,
        reason_code=reason_code,
        occurred_at=occurred_at,
    )


def _merge_claim[TClaim: (UserModelClaim, WorldModelClaim)](
    existing: TClaim,
    proposal_confidence: float,
    sources: Sequence[ModelSourceMessage],
    *,
    freshness_days: int | None,
    owner: ModelOwner,
    now: datetime,
    decision_id: str,
    new_id: Callable[[], str],
) -> tuple[TClaim, ModelClaimRevision] | None:
    known = {item.source_message_id for item in existing.evidence}
    additions: tuple[ModelClaimEvidence, ...] = _evidence(
        owner, existing.claim_id, [item for item in sources if item.message_id not in known], new_id
    )
    if not additions:
        return None
    evidence: tuple[ModelClaimEvidence, ...] = (*existing.evidence, *additions)
    last_observed = max(item.observed_at for item in evidence)
    updated = replace(
        existing,
        aggregate_version=existing.aggregate_version + 1,
        confidence=max(
            existing.confidence,
            _confidence(existing.epistemic_kind, proposal_confidence, len(evidence)),
        ),
        last_observed_at=last_observed,
        expires_at=_expiry(last_observed, freshness_days),
        updated_at=now,
        evidence=evidence,
    )
    return updated, _revision(
        updated,
        existing,
        owner=owner,
        kind=ModelRevisionKind.STRENGTHENED,
        reason_code="independent_evidence_added",
        decision_id=decision_id,
        occurred_at=now,
        new_id=new_id,
    )


def _expire_due[TClaim: (UserModelClaim, WorldModelClaim)](
    claims: Sequence[TClaim],
    *,
    owner: ModelOwner,
    now: datetime,
    decision_id: str,
    new_id: Callable[[], str],
) -> OwnerFormationPlan[TClaim]:
    changed: list[TClaim] = []
    revisions: list[ModelClaimRevision] = []
    for claim in claims:
        if (
            claim.status is not ModelClaimStatus.CURRENT
            or claim.expires_at is None
            or now < claim.expires_at
        ):
            continue
        updated = replace(
            claim,
            aggregate_version=claim.aggregate_version + 1,
            status=ModelClaimStatus.EXPIRED,
            valid_until=claim.expires_at,
            updated_at=now,
        )
        changed.append(updated)
        revisions.append(
            _revision(
                updated,
                claim,
                owner=owner,
                kind=ModelRevisionKind.EXPIRED,
                reason_code="freshness_window_elapsed",
                decision_id=decision_id,
                occurred_at=now,
                new_id=new_id,
            )
        )
    return OwnerFormationPlan(tuple(changed), tuple(revisions))


class UserModelManager:
    """Sole owner of minimal counterparty user-model claims."""

    def evaluate(
        self,
        proposals: tuple[UserModelClaimProposal, ...],
        *,
        identity_id: str,
        counterparty_id: str,
        current_message_id: str,
        sources: tuple[ModelSourceMessage, ...],
        existing_claims: tuple[UserModelClaim, ...],
        max_claims: int,
        now: datetime,
        decision_id: str,
        new_id: Callable[[], str],
    ) -> OwnerFormationPlan[UserModelClaim]:
        if len(proposals) > max_claims:
            return OwnerFormationPlan((), (), rejected_count=len(proposals))
        source_map = {item.message_id: item for item in sources}
        working = {item.claim_id: item for item in existing_claims}
        changed: dict[str, UserModelClaim] = {}
        revisions: list[ModelClaimRevision] = []
        created = merged = superseded = disputed = rejected = 0
        for proposal in proposals:
            definition = USER_MODEL_PREDICATES_V1.get(proposal.predicate)
            resolved = _validate_sources(
                proposal.evidence,
                source_map,
                identity_id=identity_id,
                counterparty_id=counterparty_id,
                current_message_id=current_message_id,
                epistemic_kind=proposal.epistemic_kind,
            )
            normalized_value = normalize_model_value(proposal.value_kind, proposal.value)
            if (
                definition is None
                or proposal.value_kind not in definition.allowed_value_kinds
                or resolved is None
                or not all(
                    normalized_value in normalize_model_text(c.quote) for c in proposal.evidence
                )
                or (
                    proposal.corrects_claim_id
                    and proposal.epistemic_kind is not ModelEpistemicKind.EXPLICIT_FACT
                )
            ):
                rejected += 1
                continue
            key = _claim_key(
                {
                    "owner": ModelOwner.USER.value,
                    "identity_id": identity_id,
                    "counterparty_id": counterparty_id,
                    "predicate": proposal.predicate,
                    "value_kind": proposal.value_kind.value,
                    "value": normalized_value,
                    "epistemic_kind": proposal.epistemic_kind.value,
                }
            )
            exact = next(
                (
                    item
                    for item in working.values()
                    if item.claim_key == key and item.status is ModelClaimStatus.CURRENT
                ),
                None,
            )
            if exact is not None:
                result = _merge_claim(
                    exact,
                    proposal.confidence,
                    resolved,
                    freshness_days=definition.freshness_days,
                    owner=ModelOwner.USER,
                    now=now,
                    decision_id=decision_id,
                    new_id=new_id,
                )
                if result is not None:
                    updated, revision = result
                    working[updated.claim_id] = updated
                    changed[updated.claim_id] = updated
                    revisions.append(revision)
                    merged += 1
                continue
            conflicts = tuple(
                item
                for item in working.values()
                if item.status is ModelClaimStatus.CURRENT
                and item.predicate == proposal.predicate
                and item.normalized_value != normalized_value
            )
            target = working.get(proposal.corrects_claim_id or "")
            if proposal.corrects_claim_id and (target is None or target not in conflicts):
                rejected += 1
                continue
            if definition.cardinality is PredicateCardinality.MULTI and target is None:
                conflicts = ()
            if proposal.epistemic_kind is not ModelEpistemicKind.EXPLICIT_FACT and any(
                item.epistemic_kind is ModelEpistemicKind.EXPLICIT_FACT for item in conflicts
            ):
                rejected += 1
                continue
            claim_id = new_id()
            observed = max(item.observed_at for item in resolved)
            status = (
                ModelClaimStatus.DISPUTED
                if conflicts and proposal.epistemic_kind is not ModelEpistemicKind.EXPLICIT_FACT
                else ModelClaimStatus.CURRENT
            )
            claim = UserModelClaim(
                claim_id=claim_id,
                claim_key=key,
                identity_id=identity_id,
                counterparty_id=counterparty_id,
                schema_version=MODEL_SCHEMA_VERSION,
                aggregate_version=1,
                policy_version=MODEL_POLICY_VERSION,
                formation_version=MODEL_FORMATION_VERSION,
                normalization_version=MODEL_NORMALIZATION_VERSION,
                predicate=proposal.predicate,
                value_kind=proposal.value_kind,
                value=proposal.value,
                normalized_value=normalized_value,
                epistemic_kind=proposal.epistemic_kind,
                confidence=_confidence(proposal.epistemic_kind, proposal.confidence, len(resolved)),
                status=status,
                valid_from=observed,
                valid_until=None,
                last_observed_at=observed,
                expires_at=_expiry(observed, definition.freshness_days),
                superseded_by_claim_id=None,
                created_at=now,
                updated_at=now,
                evidence=_evidence(ModelOwner.USER, claim_id, resolved, new_id),
            )
            working[claim_id] = claim
            changed[claim_id] = claim
            created += 1
            kind = (
                ModelRevisionKind.DISPUTED
                if status is ModelClaimStatus.DISPUTED
                else ModelRevisionKind.CREATED
            )
            revisions.append(
                _revision(
                    claim,
                    None,
                    owner=ModelOwner.USER,
                    kind=kind,
                    reason_code="competing_inferences"
                    if status is ModelClaimStatus.DISPUTED
                    else "claim_created",
                    decision_id=decision_id,
                    occurred_at=now,
                    new_id=new_id,
                )
            )
            for conflict in conflicts:
                new_status = (
                    ModelClaimStatus.SUPERSEDED
                    if proposal.epistemic_kind is ModelEpistemicKind.EXPLICIT_FACT
                    else ModelClaimStatus.DISPUTED
                )
                updated = replace(
                    conflict,
                    aggregate_version=conflict.aggregate_version + 1,
                    status=new_status,
                    valid_until=observed
                    if new_status is ModelClaimStatus.SUPERSEDED
                    else conflict.valid_until,
                    superseded_by_claim_id=claim_id
                    if new_status is ModelClaimStatus.SUPERSEDED
                    else None,
                    updated_at=now,
                )
                working[updated.claim_id] = updated
                changed[updated.claim_id] = updated
                revision_kind = (
                    ModelRevisionKind.SUPERSEDED
                    if new_status is ModelClaimStatus.SUPERSEDED
                    else ModelRevisionKind.DISPUTED
                )
                revisions.append(
                    _revision(
                        updated,
                        conflict,
                        owner=ModelOwner.USER,
                        kind=revision_kind,
                        reason_code="explicit_correction"
                        if proposal.corrects_claim_id
                        else (
                            "newer_explicit_evidence"
                            if new_status is ModelClaimStatus.SUPERSEDED
                            else "competing_inferences"
                        ),
                        decision_id=decision_id,
                        occurred_at=now,
                        new_id=new_id,
                    )
                )
                superseded += new_status is ModelClaimStatus.SUPERSEDED
                disputed += new_status is ModelClaimStatus.DISPUTED
        return OwnerFormationPlan(
            tuple(changed.values()),
            tuple(revisions),
            created,
            merged,
            superseded,
            disputed,
            rejected,
        )

    def expire_due(
        self,
        claims: tuple[UserModelClaim, ...],
        *,
        now: datetime,
        decision_id: str,
        new_id: Callable[[], str],
    ) -> OwnerFormationPlan[UserModelClaim]:
        return _expire_due(
            claims, owner=ModelOwner.USER, now=now, decision_id=decision_id, new_id=new_id
        )


class WorldModelManager:
    """Sole owner of counterparty-relative current situations and statuses."""

    def evaluate(
        self,
        proposals: tuple[WorldModelClaimProposal, ...],
        *,
        identity_id: str,
        counterparty_id: str,
        current_message_id: str,
        sources: tuple[ModelSourceMessage, ...],
        existing_claims: tuple[WorldModelClaim, ...],
        max_claims: int,
        now: datetime,
        decision_id: str,
        new_id: Callable[[], str],
    ) -> OwnerFormationPlan[WorldModelClaim]:
        if len(proposals) > max_claims:
            return OwnerFormationPlan((), (), rejected_count=len(proposals))
        source_map = {item.message_id: item for item in sources}
        working = {item.claim_id: item for item in existing_claims}
        changed: dict[str, WorldModelClaim] = {}
        revisions: list[ModelClaimRevision] = []
        created = merged = superseded = disputed = rejected = 0
        for proposal in proposals:
            normalized_subject = normalize_model_text(proposal.subject_label)
            normalized_value = normalize_model_value(proposal.value_kind, proposal.value)
            resolved = _validate_sources(
                proposal.evidence,
                source_map,
                identity_id=identity_id,
                counterparty_id=counterparty_id,
                current_message_id=current_message_id,
                epistemic_kind=proposal.epistemic_kind,
            )
            allowed = WORLD_MODEL_STATUSES_V1.get(proposal.subject_kind)
            cues = WORLD_STATUS_CUES.get(normalized_value, ())
            quote_text = " ".join(normalize_model_text(item.quote) for item in proposal.evidence)
            if (
                proposal.predicate != "status"
                or proposal.value_kind is not ModelValueKind.TEXT
                or allowed is None
                or normalized_value not in allowed
                or resolved is None
                or normalized_subject not in quote_text
                or not any(normalize_model_text(cue) in quote_text for cue in cues)
                or (
                    proposal.corrects_claim_id
                    and proposal.epistemic_kind is not ModelEpistemicKind.EXPLICIT_FACT
                )
            ):
                rejected += 1
                continue
            freshness = (
                365
                if normalized_value in WORLD_TERMINAL_STATUSES
                else (30 if proposal.subject_kind == "situation" else 90)
            )
            key = _claim_key(
                {
                    "owner": ModelOwner.WORLD.value,
                    "identity_id": identity_id,
                    "counterparty_id": counterparty_id,
                    "subject_kind": proposal.subject_kind,
                    "subject_label": normalized_subject,
                    "predicate": proposal.predicate,
                    "value": normalized_value,
                    "epistemic_kind": proposal.epistemic_kind.value,
                }
            )
            exact = next(
                (
                    item
                    for item in working.values()
                    if item.claim_key == key and item.status is ModelClaimStatus.CURRENT
                ),
                None,
            )
            if exact is not None:
                result = _merge_claim(
                    exact,
                    proposal.confidence,
                    resolved,
                    freshness_days=freshness,
                    owner=ModelOwner.WORLD,
                    now=now,
                    decision_id=decision_id,
                    new_id=new_id,
                )
                if result is not None:
                    updated, revision = result
                    working[updated.claim_id] = updated
                    changed[updated.claim_id] = updated
                    revisions.append(revision)
                    merged += 1
                continue
            conflicts = tuple(
                item
                for item in working.values()
                if item.status is ModelClaimStatus.CURRENT
                and item.subject_kind == proposal.subject_kind
                and item.normalized_subject_label == normalized_subject
                and item.predicate == proposal.predicate
                and item.normalized_value != normalized_value
            )
            target = working.get(proposal.corrects_claim_id or "")
            if proposal.corrects_claim_id and (target is None or target not in conflicts):
                rejected += 1
                continue
            if proposal.epistemic_kind is not ModelEpistemicKind.EXPLICIT_FACT and any(
                item.epistemic_kind is ModelEpistemicKind.EXPLICIT_FACT for item in conflicts
            ):
                rejected += 1
                continue
            claim_id = new_id()
            observed = max(item.observed_at for item in resolved)
            status = (
                ModelClaimStatus.DISPUTED
                if conflicts and proposal.epistemic_kind is not ModelEpistemicKind.EXPLICIT_FACT
                else ModelClaimStatus.CURRENT
            )
            claim = WorldModelClaim(
                claim_id=claim_id,
                claim_key=key,
                identity_id=identity_id,
                counterparty_id=counterparty_id,
                schema_version=MODEL_SCHEMA_VERSION,
                aggregate_version=1,
                policy_version=MODEL_POLICY_VERSION,
                formation_version=MODEL_FORMATION_VERSION,
                normalization_version=MODEL_NORMALIZATION_VERSION,
                subject_kind=proposal.subject_kind,
                subject_label=proposal.subject_label,
                normalized_subject_label=normalized_subject,
                predicate=proposal.predicate,
                value_kind=proposal.value_kind,
                value=proposal.value,
                normalized_value=normalized_value,
                epistemic_kind=proposal.epistemic_kind,
                confidence=_confidence(proposal.epistemic_kind, proposal.confidence, len(resolved)),
                status=status,
                valid_from=observed,
                valid_until=None,
                last_observed_at=observed,
                expires_at=_expiry(observed, freshness),
                superseded_by_claim_id=None,
                created_at=now,
                updated_at=now,
                evidence=_evidence(ModelOwner.WORLD, claim_id, resolved, new_id),
            )
            working[claim_id] = claim
            changed[claim_id] = claim
            created += 1
            kind = (
                ModelRevisionKind.DISPUTED
                if status is ModelClaimStatus.DISPUTED
                else ModelRevisionKind.CREATED
            )
            revisions.append(
                _revision(
                    claim,
                    None,
                    owner=ModelOwner.WORLD,
                    kind=kind,
                    reason_code="competing_inferences"
                    if status is ModelClaimStatus.DISPUTED
                    else "claim_created",
                    decision_id=decision_id,
                    occurred_at=now,
                    new_id=new_id,
                )
            )
            for conflict in conflicts:
                new_status = (
                    ModelClaimStatus.SUPERSEDED
                    if proposal.epistemic_kind is ModelEpistemicKind.EXPLICIT_FACT
                    else ModelClaimStatus.DISPUTED
                )
                updated = replace(
                    conflict,
                    aggregate_version=conflict.aggregate_version + 1,
                    status=new_status,
                    valid_until=observed
                    if new_status is ModelClaimStatus.SUPERSEDED
                    else conflict.valid_until,
                    superseded_by_claim_id=claim_id
                    if new_status is ModelClaimStatus.SUPERSEDED
                    else None,
                    updated_at=now,
                )
                working[updated.claim_id] = updated
                changed[updated.claim_id] = updated
                revision_kind = (
                    ModelRevisionKind.SUPERSEDED
                    if new_status is ModelClaimStatus.SUPERSEDED
                    else ModelRevisionKind.DISPUTED
                )
                revisions.append(
                    _revision(
                        updated,
                        conflict,
                        owner=ModelOwner.WORLD,
                        kind=revision_kind,
                        reason_code="explicit_correction"
                        if proposal.corrects_claim_id
                        else (
                            "newer_explicit_evidence"
                            if new_status is ModelClaimStatus.SUPERSEDED
                            else "competing_inferences"
                        ),
                        decision_id=decision_id,
                        occurred_at=now,
                        new_id=new_id,
                    )
                )
                superseded += new_status is ModelClaimStatus.SUPERSEDED
                disputed += new_status is ModelClaimStatus.DISPUTED
        return OwnerFormationPlan(
            tuple(changed.values()),
            tuple(revisions),
            created,
            merged,
            superseded,
            disputed,
            rejected,
        )

    def expire_due(
        self,
        claims: tuple[WorldModelClaim, ...],
        *,
        now: datetime,
        decision_id: str,
        new_id: Callable[[], str],
    ) -> OwnerFormationPlan[WorldModelClaim]:
        return _expire_due(
            claims, owner=ModelOwner.WORLD, now=now, decision_id=decision_id, new_id=new_id
        )
