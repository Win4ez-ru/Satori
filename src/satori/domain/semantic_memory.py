"""SemanticMemoryManager: deterministic evidence, confidence, and conflict policy."""

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from satori.core.semantic import (
    SemanticClaimKind,
    SemanticClaimProposal,
    SemanticFormationProposal,
    SemanticScalar,
    SemanticSourceMemory,
    SemanticValueKind,
)

SEMANTIC_MEMORY_SCHEMA_VERSION = 1
SEMANTIC_FORMATION_VERSION = 1
SEMANTIC_FORMATION_POLICY_VERSION = 1
SEMANTIC_NORMALIZATION_VERSION = 1
SEMANTIC_SUBJECT_USER = "user"


class PredicateCardinality(StrEnum):
    """Whether active values may coexist for one subject/predicate pair."""

    SINGLE = "single"
    MULTI = "multi"


@dataclass(frozen=True, slots=True)
class PredicateDefinition:
    """Closed v1 predicate registry entry."""

    cardinality: PredicateCardinality
    allowed_value_kinds: frozenset[SemanticValueKind]


SEMANTIC_PREDICATES_V1: Mapping[str, PredicateDefinition] = {
    "age": PredicateDefinition(PredicateCardinality.SINGLE, frozenset({SemanticValueKind.NUMBER})),
    "name": PredicateDefinition(PredicateCardinality.SINGLE, frozenset({SemanticValueKind.TEXT})),
    "occupation": PredicateDefinition(
        PredicateCardinality.SINGLE, frozenset({SemanticValueKind.TEXT})
    ),
    "residence_city": PredicateDefinition(
        PredicateCardinality.SINGLE, frozenset({SemanticValueKind.TEXT})
    ),
    "works_on_project": PredicateDefinition(
        PredicateCardinality.MULTI, frozenset({SemanticValueKind.TEXT})
    ),
    "studies_topic": PredicateDefinition(
        PredicateCardinality.MULTI, frozenset({SemanticValueKind.TEXT})
    ),
    "likes": PredicateDefinition(PredicateCardinality.MULTI, frozenset({SemanticValueKind.TEXT})),
}


class SemanticClaimStatus(StrEnum):
    """Lifecycle is historical and non-destructive."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    RETRACTED = "retracted"


class SemanticEvidenceSourceKind(StrEnum):
    """How root user evidence supports the semantic layer."""

    EXPLICIT_USER_STATEMENT = "explicit_user_statement"
    EPISODE_INFERENCE = "episode_inference"


class SemanticDecisionKind(StrEnum):
    """Terminal outcome for one source-memory/version key."""

    APPLIED = "applied"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class SemanticRevisionKind(StrEnum):
    """Auditable material changes to a semantic aggregate."""

    CREATED = "created"
    STRENGTHENED = "strengthened"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    RETRACTED = "retracted"


def semantic_idempotency_key(source_memory_id: str, formation_version: int) -> str:
    """Stable processing identity; provider retries cannot create new evidence."""

    return f"semantic:{source_memory_id}:formation:{formation_version}"


def normalize_semantic_value(kind: SemanticValueKind, value: SemanticScalar) -> str:
    """Apply conservative, versioned normalization without synonym inference."""

    if kind is SemanticValueKind.TEXT:
        assert isinstance(value, str)
        normalized = unicodedata.normalize("NFKC", value).casefold().strip()
        return re.sub(r"\s+", " ", normalized)
    if kind is SemanticValueKind.NUMBER:
        assert not isinstance(value, bool)
        assert isinstance(value, (int, float))
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("semantic numeric value must be finite")
        return str(int(number)) if number.is_integer() else format(number, ".15g")
    assert type(value) is bool
    return "true" if value else "false"


def semantic_claim_key(proposal: SemanticClaimProposal) -> str:
    """Stable structured identity; display wording never participates."""

    material = {
        "claim_kind": proposal.claim_kind.value,
        "normalization_version": SEMANTIC_NORMALIZATION_VERSION,
        "polarity": proposal.polarity,
        "predicate": proposal.predicate,
        "subject": proposal.subject,
        "value": normalize_semantic_value(proposal.value_kind, proposal.value),
        "value_kind": proposal.value_kind.value,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SemanticEvidence:
    """Complete edge from a claim to an episode and its root user message."""

    semantic_evidence_id: str
    claim_id: str
    memory_id: str
    memory_evidence_id: str
    root_message_id: str
    root_interaction_id: str
    source_kind: SemanticEvidenceSourceKind
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SemanticClaim:
    """Immutable read representation of one versioned semantic aggregate."""

    claim_id: str
    claim_key: str
    schema_version: int
    aggregate_version: int
    subject: str
    predicate: str
    value_kind: SemanticValueKind
    value: SemanticScalar
    normalized_value: str
    polarity: bool
    claim_kind: SemanticClaimKind
    confidence: float
    status: SemanticClaimStatus
    valid_from: datetime
    valid_until: datetime | None
    superseded_by_claim_id: str | None
    created_at: datetime
    updated_at: datetime
    formation_method: str
    formation_version: int
    normalization_version: int
    evidence: tuple[SemanticEvidence, ...]


@dataclass(frozen=True, slots=True)
class SemanticClaimRevision:
    """Append-only explanation of a material semantic aggregate transition."""

    revision_id: str
    claim_id: str
    claim_version: int
    decision_id: str
    kind: SemanticRevisionKind
    prior_status: SemanticClaimStatus | None
    new_status: SemanticClaimStatus
    prior_confidence: float | None
    new_confidence: float
    reason_code: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SemanticFormationDecision:
    """Terminal, replayable owner decision for one source episodic memory."""

    decision_id: str
    idempotency_key: str
    source_memory_id: str
    formation_version: int
    policy_version: int
    kind: SemanticDecisionKind
    reason_code: str
    created_count: int
    merged_count: int
    superseded_count: int
    disputed_count: int
    rejected_count: int
    claim_ids: tuple[str, ...]
    decided_at: datetime
    trace_id: str
    formation_method: str
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class SemanticFormationPlan:
    """Atomic state replacement and append-only history prepared by the owner."""

    kind: SemanticDecisionKind
    reason_code: str
    claims: tuple[SemanticClaim, ...]
    revisions: tuple[SemanticClaimRevision, ...]
    created_count: int
    merged_count: int
    superseded_count: int
    disputed_count: int
    rejected_count: int

    @property
    def claim_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(claim.claim_id for claim in self.claims))


def _confidence_cap(kind: SemanticClaimKind, independent_roots: int) -> float:
    """Exact deterministic v1 evidence-quality cap."""

    if kind is SemanticClaimKind.EXPLICIT_FACT:
        return min(0.90 + 0.02 * (independent_roots - 1), 0.96)
    if kind is SemanticClaimKind.ATTRIBUTED_STATEMENT:
        return min(0.85 + 0.02 * (independent_roots - 1), 0.91)
    if kind is SemanticClaimKind.INFERRED_FACT:
        return min(0.65 + 0.07 * (independent_roots - 2), 0.79)
    return min(0.50 + 0.05 * (independent_roots - 2), 0.65)


def _is_inference_like(kind: SemanticClaimKind) -> bool:
    return kind in {SemanticClaimKind.INFERRED_FACT, SemanticClaimKind.HYPOTHESIS}


class SemanticMemoryManager:
    """Sole v1 owner of semantic formation, merge, confidence, and conflict policy."""

    def evaluate(
        self,
        proposal: SemanticFormationProposal,
        *,
        source_memory_id: str,
        memories: tuple[SemanticSourceMemory, ...],
        existing_claims: tuple[SemanticClaim, ...],
        max_claims: int,
        now: datetime,
        decision_id: str,
        formation_method: str,
        new_id: Callable[[], str],
    ) -> SemanticFormationPlan:
        """Validate untrusted proposals and return one deterministic atomic plan."""

        if len(proposal.claims) > max_claims:
            return self._empty(SemanticDecisionKind.REJECTED, "proposal_limit_exceeded", 1)
        by_memory = {memory.memory_id: memory for memory in memories}
        if source_memory_id not in by_memory:
            return self._empty(SemanticDecisionKind.REJECTED, "source_memory_missing", 1)

        working = {claim.claim_id: claim for claim in existing_claims}
        changed: dict[str, SemanticClaim] = {}
        revisions: list[SemanticClaimRevision] = []
        created = merged = superseded = disputed = rejected = 0

        for item in proposal.claims:
            result = self._apply_one(
                item,
                source_memory_id=source_memory_id,
                by_memory=by_memory,
                working=working,
                now=now,
                decision_id=decision_id,
                formation_method=formation_method,
                new_id=new_id,
            )
            if result is None:
                rejected += 1
                continue
            item_claims, item_revisions, operation = result
            for claim in item_claims:
                working[claim.claim_id] = claim
                changed[claim.claim_id] = claim
            revisions.extend(item_revisions)
            if operation == "created":
                created += 1
            elif operation == "merged":
                merged += 1
            elif operation == "superseded":
                created += 1
                superseded += len(item_claims) - 1
            elif operation == "disputed":
                created += 1
                disputed += len(item_claims)
            elif operation == "noop":
                continue

        if changed:
            kind = SemanticDecisionKind.APPLIED
            reason = "claims_applied"
        elif proposal.claims and rejected:
            kind = SemanticDecisionKind.REJECTED
            reason = "all_claims_rejected"
        else:
            kind = SemanticDecisionKind.SKIPPED
            reason = "no_claims" if not proposal.claims else "no_material_change"
        return SemanticFormationPlan(
            kind=kind,
            reason_code=reason,
            claims=tuple(changed.values()),
            revisions=tuple(revisions),
            created_count=created,
            merged_count=merged,
            superseded_count=superseded,
            disputed_count=disputed,
            rejected_count=rejected,
        )

    def _apply_one(
        self,
        proposal: SemanticClaimProposal,
        *,
        source_memory_id: str,
        by_memory: Mapping[str, SemanticSourceMemory],
        working: Mapping[str, SemanticClaim],
        now: datetime,
        decision_id: str,
        formation_method: str,
        new_id: Callable[[], str],
    ) -> tuple[tuple[SemanticClaim, ...], tuple[SemanticClaimRevision, ...], str] | None:
        definition = SEMANTIC_PREDICATES_V1.get(proposal.predicate)
        if (
            proposal.subject != SEMANTIC_SUBJECT_USER
            or definition is None
            or proposal.value_kind not in definition.allowed_value_kinds
            or (
                proposal.claim_kind is SemanticClaimKind.ATTRIBUTED_STATEMENT
                and definition.cardinality is PredicateCardinality.SINGLE
            )
            or source_memory_id not in proposal.evidence_memory_ids
            or any(memory_id not in by_memory for memory_id in proposal.evidence_memory_ids)
        ):
            return None
        selected = tuple(by_memory[item] for item in proposal.evidence_memory_ids)
        evidence = self._evidence_for(proposal, selected, claim_id="", new_id=new_id)
        if not evidence:
            return None
        independent_messages = {item.root_message_id for item in evidence}
        independent_interactions = {item.root_interaction_id for item in evidence}
        if _is_inference_like(proposal.claim_kind) and (
            len(independent_messages) < 2 or len(independent_interactions) < 2
        ):
            return None

        key = semantic_claim_key(proposal)
        active = tuple(
            claim
            for claim in working.values()
            if claim.status is SemanticClaimStatus.ACTIVE
            and claim.subject == proposal.subject
            and claim.predicate == proposal.predicate
        )
        exact = next((claim for claim in active if claim.claim_key == key), None)
        if exact is not None:
            return self._merge(
                exact,
                proposal,
                evidence,
                now=now,
                decision_id=decision_id,
                new_id=new_id,
            )

        compatible_explicit = tuple(
            claim
            for claim in active
            if claim.normalized_value
            == normalize_semantic_value(proposal.value_kind, proposal.value)
            and claim.value_kind is proposal.value_kind
            and claim.polarity == proposal.polarity
            and not _is_inference_like(claim.claim_kind)
        )
        if _is_inference_like(proposal.claim_kind) and compatible_explicit:
            return None
        if proposal.claim_kind is SemanticClaimKind.HYPOTHESIS and any(
            claim.claim_kind is SemanticClaimKind.INFERRED_FACT
            and claim.value_kind is proposal.value_kind
            and claim.normalized_value
            == normalize_semantic_value(proposal.value_kind, proposal.value)
            and claim.polarity == proposal.polarity
            for claim in active
        ):
            return None

        correction_target = None
        if proposal.corrects_claim_id is not None:
            correction_target = next(
                (claim for claim in active if claim.claim_id == proposal.corrects_claim_id), None
            )
            if (
                correction_target is None
                or proposal.claim_kind is not SemanticClaimKind.EXPLICIT_FACT
            ):
                return None

        weaker_same_value = tuple(
            claim
            for claim in active
            if _is_inference_like(claim.claim_kind)
            and (
                not _is_inference_like(proposal.claim_kind)
                or (
                    proposal.claim_kind is SemanticClaimKind.INFERRED_FACT
                    and claim.claim_kind is SemanticClaimKind.HYPOTHESIS
                )
            )
            and claim.value_kind is proposal.value_kind
            and claim.normalized_value
            == normalize_semantic_value(proposal.value_kind, proposal.value)
            and claim.polarity == proposal.polarity
        )
        conflicts = tuple(
            claim
            for claim in active
            if definition.cardinality is PredicateCardinality.SINGLE
            and (
                claim.value_kind is not proposal.value_kind
                or claim.normalized_value
                != normalize_semantic_value(proposal.value_kind, proposal.value)
                or claim.polarity != proposal.polarity
            )
        )
        supersede_targets = tuple(dict.fromkeys((*weaker_same_value, *conflicts)))
        if correction_target is not None and correction_target not in supersede_targets:
            supersede_targets = (*supersede_targets, correction_target)

        if conflicts and _is_inference_like(proposal.claim_kind):
            if any(not _is_inference_like(claim.claim_kind) for claim in conflicts):
                return None
            return self._create_dispute(
                proposal,
                evidence,
                conflicts,
                now=now,
                decision_id=decision_id,
                formation_method=formation_method,
                new_id=new_id,
            )
        return self._create(
            proposal,
            evidence,
            supersede_targets,
            now=now,
            decision_id=decision_id,
            formation_method=formation_method,
            new_id=new_id,
        )

    @staticmethod
    def _evidence_for(
        proposal: SemanticClaimProposal,
        memories: tuple[SemanticSourceMemory, ...],
        *,
        claim_id: str,
        new_id: Callable[[], str],
    ) -> tuple[SemanticEvidence, ...]:
        source_kind = (
            SemanticEvidenceSourceKind.EPISODE_INFERENCE
            if _is_inference_like(proposal.claim_kind)
            else SemanticEvidenceSourceKind.EXPLICIT_USER_STATEMENT
        )
        result: list[SemanticEvidence] = []
        seen_messages: set[str] = set()
        for memory in memories:
            for root in memory.evidence:
                if root.source_message_id in seen_messages:
                    continue
                normalized_quote = re.sub(
                    r"\s+",
                    " ",
                    unicodedata.normalize("NFKC", root.quote).casefold(),
                )
                normalized_value = normalize_semantic_value(proposal.value_kind, proposal.value)
                if normalized_value not in normalized_quote:
                    continue
                seen_messages.add(root.source_message_id)
                result.append(
                    SemanticEvidence(
                        semantic_evidence_id=new_id(),
                        claim_id=claim_id,
                        memory_id=memory.memory_id,
                        memory_evidence_id=root.memory_evidence_id,
                        root_message_id=root.source_message_id,
                        root_interaction_id=memory.source_interaction_id,
                        source_kind=source_kind,
                        observed_at=memory.occurred_at,
                    )
                )
        return tuple(result)

    def _create(
        self,
        proposal: SemanticClaimProposal,
        provisional_evidence: tuple[SemanticEvidence, ...],
        supersede_targets: tuple[SemanticClaim, ...],
        *,
        now: datetime,
        decision_id: str,
        formation_method: str,
        new_id: Callable[[], str],
    ) -> tuple[tuple[SemanticClaim, ...], tuple[SemanticClaimRevision, ...], str]:
        claim_id = new_id()
        evidence = tuple(replace(item, claim_id=claim_id) for item in provisional_evidence)
        observed = max(item.observed_at for item in evidence)
        confidence = min(proposal.confidence, _confidence_cap(proposal.claim_kind, len(evidence)))
        claim = SemanticClaim(
            claim_id=claim_id,
            claim_key=semantic_claim_key(proposal),
            schema_version=SEMANTIC_MEMORY_SCHEMA_VERSION,
            aggregate_version=1,
            subject=proposal.subject,
            predicate=proposal.predicate,
            value_kind=proposal.value_kind,
            value=proposal.value,
            normalized_value=normalize_semantic_value(proposal.value_kind, proposal.value),
            polarity=proposal.polarity,
            claim_kind=proposal.claim_kind,
            confidence=confidence,
            status=SemanticClaimStatus.ACTIVE,
            valid_from=proposal.valid_from or observed,
            valid_until=proposal.valid_until,
            superseded_by_claim_id=None,
            created_at=now,
            updated_at=now,
            formation_method=formation_method,
            formation_version=SEMANTIC_FORMATION_VERSION,
            normalization_version=SEMANTIC_NORMALIZATION_VERSION,
            evidence=evidence,
        )
        claims: list[SemanticClaim] = [claim]
        revisions = [
            self._revision(
                claim, None, SemanticRevisionKind.CREATED, "claim_created", decision_id, now, new_id
            )
        ]
        for target in supersede_targets:
            updated = replace(
                target,
                aggregate_version=target.aggregate_version + 1,
                status=SemanticClaimStatus.SUPERSEDED,
                valid_until=claim.valid_from,
                superseded_by_claim_id=claim.claim_id,
                updated_at=now,
            )
            claims.append(updated)
            revisions.append(
                self._revision(
                    updated,
                    target,
                    SemanticRevisionKind.SUPERSEDED,
                    "explicit_correction"
                    if proposal.corrects_claim_id
                    else "newer_explicit_evidence",
                    decision_id,
                    now,
                    new_id,
                )
            )
        return tuple(claims), tuple(revisions), "superseded" if supersede_targets else "created"

    def _merge(
        self,
        existing: SemanticClaim,
        proposal: SemanticClaimProposal,
        provisional_evidence: tuple[SemanticEvidence, ...],
        *,
        now: datetime,
        decision_id: str,
        new_id: Callable[[], str],
    ) -> tuple[tuple[SemanticClaim, ...], tuple[SemanticClaimRevision, ...], str] | None:
        known_roots = {item.root_message_id for item in existing.evidence}
        additions = tuple(
            replace(item, claim_id=existing.claim_id)
            for item in provisional_evidence
            if item.root_message_id not in known_roots
        )
        if not additions:
            return (), (), "noop"
        all_evidence = (*existing.evidence, *additions)
        cap = _confidence_cap(
            existing.claim_kind, len({item.root_message_id for item in all_evidence})
        )
        confidence = max(existing.confidence, min(proposal.confidence, cap))
        updated = replace(
            existing,
            aggregate_version=existing.aggregate_version + 1,
            confidence=confidence,
            updated_at=now,
            evidence=all_evidence,
        )
        return (
            (updated,),
            (
                self._revision(
                    updated,
                    existing,
                    SemanticRevisionKind.STRENGTHENED,
                    "independent_evidence_added",
                    decision_id,
                    now,
                    new_id,
                ),
            ),
            "merged",
        )

    def _create_dispute(
        self,
        proposal: SemanticClaimProposal,
        evidence: tuple[SemanticEvidence, ...],
        conflicts: tuple[SemanticClaim, ...],
        *,
        now: datetime,
        decision_id: str,
        formation_method: str,
        new_id: Callable[[], str],
    ) -> tuple[tuple[SemanticClaim, ...], tuple[SemanticClaimRevision, ...], str]:
        created, revisions, _ = self._create(
            proposal,
            evidence,
            (),
            now=now,
            decision_id=decision_id,
            formation_method=formation_method,
            new_id=new_id,
        )
        incoming = replace(created[0], status=SemanticClaimStatus.DISPUTED)
        claims: list[SemanticClaim] = [incoming]
        all_revisions = [
            replace(
                revisions[0],
                new_status=SemanticClaimStatus.DISPUTED,
                kind=SemanticRevisionKind.DISPUTED,
                reason_code="competing_inferences",
            )
        ]
        for conflict in conflicts:
            updated = replace(
                conflict,
                aggregate_version=conflict.aggregate_version + 1,
                status=SemanticClaimStatus.DISPUTED,
                updated_at=now,
            )
            claims.append(updated)
            all_revisions.append(
                self._revision(
                    updated,
                    conflict,
                    SemanticRevisionKind.DISPUTED,
                    "competing_inferences",
                    decision_id,
                    now,
                    new_id,
                )
            )
        return tuple(claims), tuple(all_revisions), "disputed"

    @staticmethod
    def _revision(
        updated: SemanticClaim,
        prior: SemanticClaim | None,
        kind: SemanticRevisionKind,
        reason: str,
        decision_id: str,
        now: datetime,
        new_id: Callable[[], str],
    ) -> SemanticClaimRevision:
        return SemanticClaimRevision(
            revision_id=new_id(),
            claim_id=updated.claim_id,
            claim_version=updated.aggregate_version,
            decision_id=decision_id,
            kind=kind,
            prior_status=prior.status if prior else None,
            new_status=updated.status,
            prior_confidence=prior.confidence if prior else None,
            new_confidence=updated.confidence,
            reason_code=reason,
            occurred_at=now,
        )

    @staticmethod
    def _empty(kind: SemanticDecisionKind, reason: str, rejected: int) -> SemanticFormationPlan:
        return SemanticFormationPlan(kind, reason, (), (), 0, 0, 0, 0, rejected)
