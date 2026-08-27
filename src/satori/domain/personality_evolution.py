"""Deterministic Stage 14 personality evidence, drift, and restore policy."""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, final

from satori.core.personality import (
    CANONICAL_TRAIT_KEYS,
    PersonalityChangeProposal,
    PersonalityCitationRole,
    PersonalityDirection,
    PersonalityRestoreProposal,
    PersonalityTraitKey,
)
from satori.domain.personality import Personality, PersonalityTrait
from satori.domain.validation import aware_utc, non_blank, positive_version, sha256_hex

PERSONALITY_EVOLUTION_SCHEMA_VERSION: Final = 1
PERSONALITY_EVOLUTION_POLICY_VERSION: Final = 1
PERSONALITY_EVIDENCE_NORMALIZATION_VERSION: Final = 1
PERSONALITY_CHECKPOINT_HASH_SCHEMA_VERSION: Final = 1

PERSONALITY_STEP: Final = 0.005
MIN_PROVIDER_CONFIDENCE: Final = 0.80
MIN_SOURCE_COVERAGE: Final = 0.80
MIN_SUPPORT_SHARE: Final = 0.80
MIN_SUPPORTING_SOURCES: Final = 8
MIN_ROOTS: Final = 8
MIN_SESSIONS: Final = 6
MIN_WEEK_BUCKETS: Final = 6
MIN_MONTH_BUCKETS: Final = 4
MIN_LINEAGES: Final = 4
MAX_COUNTED_ROOTS_PER_LINEAGE: Final = 2
MIN_CLUSTERS: Final = 8
MIN_OBSERVATION_SPAN: Final = timedelta(days=90)
PER_TRAIT_COOLDOWN: Final = timedelta(days=90)
GLOBAL_COOLDOWN: Final = timedelta(days=30)
ROLLING_WINDOW: Final = timedelta(days=365)
ROLLING_TRAIT_PATH_CAP: Final = 0.015
ROLLING_GLOBAL_PATH_CAP: Final = 0.060
LIFETIME_TRAIT_PATH_CAP: Final = 0.080
LIFETIME_GLOBAL_PATH_CAP: Final = 0.300
ACTIVATION_LINF_CAP: Final = 0.080
ACTIVATION_L1_CAP: Final = 0.300
CHECKPOINT_LINF_CAP: Final = 0.020
CHECKPOINT_L1_CAP: Final = 0.050
MAX_FIXED_SOURCES: Final = 12
MAX_FIXED_SOURCE_CHARACTERS: Final = 4_800
PERSONALITY_EVIDENCE_RESERVOIR_LIMIT: Final = 256
NEAR_DUPLICATE_TOKEN_JACCARD: Final = 0.80
NEAR_DUPLICATE_TRIGRAM_JACCARD: Final = 0.85
_EPSILON: Final = 1e-9

_TRAIT_TERMS = (
    r"любопыт\w*|аналитич\w*|открыт\w*|эмпат\w*|чувствительн\w*|тепл\w*|"
    r"холодн\w*|независим\w*|самостоятельн\w*|настойчив\w*|уверен\w*|игрив\w*|"
    r"юмор\w*|иронич\w*|терпелив\w*|оптимист\w*|импульсив\w*|"
    r"curious|curiosity|analytical|open(?:ness|-minded)?|empathetic|empathic|sensitive|"
    r"warm|cold|independent|assertive|confident|playful|humorous|funny|ironic|patient|"
    r"optimistic|impulsive"
)
_ASSIGNMENT_OR_EVALUATION = re.compile(
    rf"(?:\b(?:ты|вы|сатор[иия])\b.{{0,48}}\b(?:долж\w*|обязан\w*|следует|надо|"
    rf"нужно|стань|будь|стала|станови\w*|всегда|слишком|более|менее)\b.{{0,48}}"
    rf"(?:{_TRAIT_TERMS})|"
    rf"\b(?:стань|будь)\b.{{0,32}}(?:{_TRAIT_TERMS})|"
    rf"\b(?:я\s+(?:хочу|желаю)|мне\s+(?:хочется|нужно))\b.{{0,64}}"
    rf"\b(?:ты|сатор[иия])\b.{{0,48}}(?:{_TRAIT_TERMS})|"
    rf"\b(?:ты|вы|сатор[иия])\b\s+(?:(?:такая|такой|очень|довольно)\s+)?"
    rf"(?:{_TRAIT_TERMS})|"
    rf"\b(?:you|satori)\b.{{0,48}}\b(?:should|must|have\s+to|become|be|are|became|"
    rf"is|was|seems|always|too|more|less)\b.{{0,48}}(?:{_TRAIT_TERMS})|"
    rf"\b(?:i\s+want|i(?:'d|\s+would)\s+like)\b.{{0,64}}\b(?:you|satori)\b"
    rf".{{0,48}}(?:{_TRAIT_TERMS}))",
    re.IGNORECASE | re.DOTALL,
)
_USER_SELF_ASCRIPTION = re.compile(
    rf"(?:\bя\b.{{0,24}}\b(?:очень|всегда|довольно|слишком|сам\w*)?\b.{{0,16}}"
    rf"(?:{_TRAIT_TERMS})|"
    rf"\bi(?:\s+am|'m)\b.{{0,24}}(?:{_TRAIT_TERMS}))",
    re.IGNORECASE | re.DOTALL,
)
_RELATIONSHIP_MATERIAL = re.compile(
    r"отношен|между\s+нами|наша\s+связ|любов|люблю\s+тебя|довер|близост|"
    r"эксклюзив|исключительн|послуш|повину|зависимост|relationship|between\s+us|"
    r"our\s+bond|love\s+you|trust|closeness|exclusive|obedien|dependency",
    re.IGNORECASE,
)


class PersonalityDecisionKind(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"


class PersonalityCheckpointKind(StrEnum):
    ACTIVATION = "activation"
    EVOLUTION = "evolution"
    RESTORE = "restore"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class PersonalityEvidenceSource:
    """One already-resolved fixed V3 canonical leaf supplied to the owner."""

    source_id: str
    identity_id: str
    evidence_edge_id: str
    root_message_id: str
    root_interaction_id: str
    root_session_id: str
    root_counterparty_id: str
    lineage_id: str
    observed_at: datetime
    quote: str
    content_hash: str
    canonical_user_message: bool = True
    interaction_completed: bool = True
    accepted_as_inclination_evidence: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "source_id",
            "identity_id",
            "evidence_edge_id",
            "root_message_id",
            "root_interaction_id",
            "root_session_id",
            "root_counterparty_id",
            "lineage_id",
        ):
            object.__setattr__(
                self,
                field_name,
                non_blank(getattr(self, field_name), field_name, maximum=128),
            )
        object.__setattr__(self, "observed_at", aware_utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "quote", non_blank(self.quote, "quote", maximum=512))
        object.__setattr__(self, "content_hash", sha256_hex(self.content_hash))


@dataclass(frozen=True, slots=True)
class PersonalityEvolutionRecord:
    """One prior accepted evolution delta; restore events never enter this ledger."""

    identity_id: str
    trait_key: PersonalityTraitKey
    applied_delta: float
    occurred_at: datetime
    policy_version: int = PERSONALITY_EVOLUTION_POLICY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "identity_id",
            non_blank(self.identity_id, "identity_id", maximum=128),
        )
        if self.trait_key not in CANONICAL_TRAIT_KEYS:
            raise ValueError("evolution record trait_key must be canonical")
        if (
            isinstance(self.applied_delta, bool)
            or not math.isfinite(self.applied_delta)
            or self.applied_delta not in {-PERSONALITY_STEP, PERSONALITY_STEP}
        ):
            raise ValueError("accepted evolution applied_delta must be exactly -0.005 or +0.005")
        object.__setattr__(self, "occurred_at", aware_utc(self.occurred_at, "occurred_at"))
        positive_version(self.policy_version, "policy_version")


@dataclass(frozen=True, slots=True)
class PersonalityCheckpointSnapshot:
    """Complete immutable checkpoint state used by evolution budgets and restore."""

    checkpoint_id: str
    checkpoint_kind: PersonalityCheckpointKind
    identity_id: str
    source_aggregate_version: int
    personality_schema_version: int
    hash_schema_version: int
    checkpoint_hash: str
    traits: tuple[PersonalityTrait, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "checkpoint_id", non_blank(self.checkpoint_id, "checkpoint_id", maximum=128)
        )
        object.__setattr__(
            self, "identity_id", non_blank(self.identity_id, "identity_id", maximum=128)
        )
        if not isinstance(self.checkpoint_kind, PersonalityCheckpointKind):
            raise ValueError("checkpoint_kind must be a PersonalityCheckpointKind")
        positive_version(self.source_aggregate_version, "source_aggregate_version")
        positive_version(self.personality_schema_version, "personality_schema_version")
        if self.hash_schema_version != PERSONALITY_CHECKPOINT_HASH_SCHEMA_VERSION:
            raise ValueError("unsupported personality checkpoint hash schema")
        object.__setattr__(self, "checkpoint_hash", sha256_hex(self.checkpoint_hash))
        ordered = tuple(sorted(self.traits, key=lambda item: item.key))
        if tuple(item.key for item in ordered) != CANONICAL_TRAIT_KEYS:
            raise ValueError("checkpoint must contain the exact canonical trait set")
        object.__setattr__(self, "traits", ordered)


@dataclass(frozen=True, slots=True)
class PersonalityDiversity:
    root_count: int
    interaction_count: int
    session_count: int
    signature_count: int
    cluster_count: int
    week_bucket_count: int
    month_bucket_count: int
    lineage_count: int
    lineage_capped_root_count: int
    observation_span: timedelta


@dataclass(frozen=True, slots=True)
class TraitDistance:
    linf: float
    l1: float


@dataclass(frozen=True, slots=True)
class PersonalityDriftMetrics:
    activation: TraitDistance
    approved_checkpoint: TraitDistance
    rolling_trait_path: float
    rolling_global_path: float
    lifetime_trait_path: float
    lifetime_global_path: float


@dataclass(frozen=True, slots=True)
class PersonalityEvolutionPlan:
    personality: Personality
    trait_key: PersonalityTraitKey
    direction: PersonalityDirection
    applied_delta: float
    decision_confidence: float
    accepted_sources: tuple[PersonalityEvidenceSource, ...]
    diversity: PersonalityDiversity
    before_metrics: PersonalityDriftMetrics
    after_metrics: PersonalityDriftMetrics


@dataclass(frozen=True, slots=True)
class PersonalityChangeEvaluation:
    kind: PersonalityDecisionKind
    reason_code: str
    plan: PersonalityEvolutionPlan | None = None


@dataclass(frozen=True, slots=True)
class PersonalityRestorePlan:
    personality: Personality
    checkpoint_id: str
    changed_traits: tuple[tuple[PersonalityTraitKey, float, float], ...]


@dataclass(frozen=True, slots=True)
class PersonalityRestoreEvaluation:
    kind: PersonalityDecisionKind
    reason_code: str
    plan: PersonalityRestorePlan | None = None


@final
@dataclass(frozen=True, slots=True)
class PersonalityManager:
    """Sole deterministic owner facade for post-activation personality writes."""

    schema_version: int = field(default=PERSONALITY_EVOLUTION_SCHEMA_VERSION, init=False)
    policy_version: int = field(default=PERSONALITY_EVOLUTION_POLICY_VERSION, init=False)

    def select_evidence(
        self,
        reservoir: tuple[PersonalityEvidenceSource, ...],
        *,
        identity_id: str,
        used_root_message_ids: frozenset[str],
        now: datetime,
    ) -> tuple[PersonalityEvidenceSource, ...]:
        """Build the stable bounded fixed set before any provider call."""

        return select_personality_evidence(
            reservoir,
            identity_id=identity_id,
            used_root_message_ids=used_root_message_ids,
            now=now,
        )

    def evaluate_change(
        self,
        proposal: PersonalityChangeProposal,
        *,
        identity_id: str,
        personality: Personality,
        approved_checkpoint: PersonalityCheckpointSnapshot,
        fixed_sources: tuple[PersonalityEvidenceSource, ...],
        prior_evolution: tuple[PersonalityEvolutionRecord, ...],
        used_root_message_ids: frozenset[str],
        now: datetime,
    ) -> PersonalityChangeEvaluation:
        """Evaluate one untrusted semantic proposal through the complete owner policy."""

        return evaluate_personality_change(
            proposal,
            identity_id=identity_id,
            personality=personality,
            approved_checkpoint=approved_checkpoint,
            fixed_sources=fixed_sources,
            prior_evolution=prior_evolution,
            used_root_message_ids=used_root_message_ids,
            now=now,
        )

    def evaluate_restore(
        self,
        proposal: PersonalityRestoreProposal,
        *,
        identity_id: str,
        personality: Personality,
        checkpoint: PersonalityCheckpointSnapshot,
    ) -> PersonalityRestoreEvaluation:
        """Evaluate one explicit local recovery request outside evolution spend."""

        return evaluate_personality_restore(
            proposal,
            identity_id=identity_id,
            personality=personality,
            checkpoint=checkpoint,
        )


def checkpoint_hash(
    *,
    identity_id: str,
    checkpoint_kind: PersonalityCheckpointKind,
    personality: Personality,
) -> str:
    """Hash the exact complete checkpoint state under the accepted V1 canonical schema."""

    if not isinstance(checkpoint_kind, PersonalityCheckpointKind):
        raise ValueError("checkpoint_kind must be a PersonalityCheckpointKind")
    _require_canonical_personality(personality)
    payload = {
        "checkpoint_kind": checkpoint_kind.value,
        "hash_schema": "satori.personality-checkpoint.v1",
        "identity_id": non_blank(identity_id, "identity_id", maximum=128),
        "personality_schema_version": personality.schema_version,
        "source_aggregate_version": personality.aggregate_version,
        "traits": [
            {
                "baseline_value": float(round(item.baseline_value, 6)),
                "key": item.key,
                "value": float(round(item.value, 6)),
            }
            for item in personality.traits
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def select_personality_evidence(
    reservoir: tuple[PersonalityEvidenceSource, ...],
    *,
    identity_id: str,
    used_root_message_ids: frozenset[str],
    now: datetime,
) -> tuple[PersonalityEvidenceSource, ...]:
    """Select a stable time-diverse V1 fixed set without semantic inference.

    Eligible sources are ordered independently of repository return order. Selection then takes
    one source per UTC calendar month on each oldest-to-newest pass, while globally admitting at
    most two roots per upstream lineage and one member of each near-duplicate cluster.
    """

    identity_id = non_blank(identity_id, "identity_id", maximum=128)
    now = aware_utc(now, "now")
    eligible = tuple(
        sorted(
            (
                item
                for item in reservoir
                if item.identity_id == identity_id
                and item.observed_at <= now
                and item.root_message_id not in used_root_message_ids
                and _source_is_eligible(item)
            ),
            key=lambda item: (
                item.observed_at,
                item.root_message_id,
                item.evidence_edge_id,
                item.source_id,
            ),
        )
    )
    if not eligible:
        return ()

    cluster_ids = _near_duplicate_cluster_ids(eligible)
    month_queues: dict[tuple[int, int], list[tuple[PersonalityEvidenceSource, int]]] = {}
    for source, cluster_id in zip(eligible, cluster_ids, strict=True):
        month = (source.observed_at.year, source.observed_at.month)
        month_queues.setdefault(month, []).append((source, cluster_id))

    selected: list[PersonalityEvidenceSource] = []
    selected_characters = 0
    selected_clusters: set[int] = set()
    selected_edges: set[str] = set()
    selected_roots: set[str] = set()
    selected_interactions: set[str] = set()
    lineage_roots: Counter[str] = Counter()
    ordered_months = tuple(sorted(month_queues))

    while len(selected) < MAX_FIXED_SOURCES:
        made_progress = False
        for month in ordered_months:
            queue = month_queues[month]
            while queue:
                source, cluster_id = queue.pop(0)
                if (
                    cluster_id in selected_clusters
                    or source.evidence_edge_id in selected_edges
                    or source.root_message_id in selected_roots
                    or source.root_interaction_id in selected_interactions
                    or lineage_roots[source.lineage_id] >= MAX_COUNTED_ROOTS_PER_LINEAGE
                    or selected_characters + len(source.quote) > MAX_FIXED_SOURCE_CHARACTERS
                ):
                    continue
                selected.append(source)
                selected_characters += len(source.quote)
                selected_clusters.add(cluster_id)
                selected_edges.add(source.evidence_edge_id)
                selected_roots.add(source.root_message_id)
                selected_interactions.add(source.root_interaction_id)
                lineage_roots[source.lineage_id] += 1
                made_progress = True
                break
            if len(selected) >= MAX_FIXED_SOURCES:
                break
        if not made_progress:
            break
    return tuple(selected)


def evaluate_personality_change(
    proposal: PersonalityChangeProposal,
    *,
    identity_id: str,
    personality: Personality,
    approved_checkpoint: PersonalityCheckpointSnapshot,
    fixed_sources: tuple[PersonalityEvidenceSource, ...],
    prior_evolution: tuple[PersonalityEvolutionRecord, ...],
    used_root_message_ids: frozenset[str],
    now: datetime,
) -> PersonalityChangeEvaluation:
    """Apply all Stage 14 V1 owner gates without persistence or provider authority."""

    identity_id = non_blank(identity_id, "identity_id", maximum=128)
    now = aware_utc(now, "now")
    _require_canonical_personality(personality)
    checkpoint_reason = _checkpoint_compatibility_reason(
        approved_checkpoint,
        identity_id=identity_id,
        personality=personality,
    )
    if checkpoint_reason is not None:
        return _rejected(checkpoint_reason)
    if proposal.expected_personality_version != personality.aggregate_version:
        return _rejected("personality_target_version_conflict")
    if proposal.confidence < MIN_PROVIDER_CONFIDENCE:
        return _rejected("provider_confidence_too_low")
    if (
        len(fixed_sources) < MIN_ROOTS
        or len(fixed_sources) > MAX_FIXED_SOURCES
        or sum(len(item.quote) for item in fixed_sources) > MAX_FIXED_SOURCE_CHARACTERS
    ):
        return _rejected("personality_fixed_source_set_invalid")
    source_by_id = {item.source_id: item for item in fixed_sources}
    if (
        len(source_by_id) != len(fixed_sources)
        or len({item.evidence_edge_id for item in fixed_sources}) != len(fixed_sources)
        or len({item.root_message_id for item in fixed_sources}) != len(fixed_sources)
        or len({item.root_interaction_id for item in fixed_sources}) != len(fixed_sources)
    ):
        return _rejected("personality_fixed_source_set_invalid")
    if any(item.identity_id != identity_id for item in fixed_sources):
        return _rejected("personality_source_identity_mismatch")
    if any(item.observed_at > now for item in fixed_sources):
        return _rejected("personality_source_from_future")
    if any(not _source_is_eligible(item) for item in fixed_sources):
        return _rejected("personality_source_ineligible")

    citation_ids = tuple(item.source_id for item in proposal.citations)
    if any(item not in source_by_id for item in citation_ids):
        return _rejected("personality_source_outside_fixed_set")
    coverage = len(citation_ids) / len(fixed_sources)
    if coverage + _EPSILON < MIN_SOURCE_COVERAGE:
        return _rejected("personality_source_coverage_too_low")
    cited_sources = tuple(source_by_id[item] for item in citation_ids)
    if any(item.root_message_id in used_root_message_ids for item in cited_sources):
        return _rejected("personality_evidence_root_already_used")

    fixed_diversity = personality_diversity(fixed_sources)
    reason = _diversity_reason(fixed_diversity)
    if reason is not None:
        return _rejected(reason)
    support_ids = {
        item.source_id
        for item in proposal.citations
        if item.role is PersonalityCitationRole.SUPPORT
    }
    support_sources = tuple(item for item in cited_sources if item.source_id in support_ids)
    if len(support_sources) < MIN_SUPPORTING_SOURCES:
        return _rejected("insufficient_personality_support")
    support_share = len(support_sources) / len(cited_sources)
    if support_share + _EPSILON < MIN_SUPPORT_SHARE:
        return _rejected("personality_support_share_too_low")
    support_diversity = personality_diversity(support_sources)
    reason = _diversity_reason(support_diversity)
    if reason is not None:
        return _rejected(reason)

    relevant_history = tuple(item for item in prior_evolution if item.identity_id == identity_id)
    if len(relevant_history) != len(prior_evolution):
        return _rejected("personality_history_identity_mismatch")
    if any(item.occurred_at > now for item in relevant_history):
        return _rejected("personality_history_from_future")
    trait_history = tuple(item for item in relevant_history if item.trait_key == proposal.trait_key)
    if trait_history and now < max(item.occurred_at for item in trait_history) + PER_TRAIT_COOLDOWN:
        return _rejected("personality_trait_cooldown")
    if (
        relevant_history
        and now < max(item.occurred_at for item in relevant_history) + GLOBAL_COOLDOWN
    ):
        return _rejected("personality_global_cooldown")

    applied_delta = (
        PERSONALITY_STEP
        if proposal.direction is PersonalityDirection.INCREASE
        else -PERSONALITY_STEP
    )
    before_metrics = personality_drift_metrics(
        personality,
        approved_checkpoint=approved_checkpoint,
        history=relevant_history,
        target_trait=proposal.trait_key,
        now=now,
    )
    next_personality = _with_exact_delta(personality, proposal.trait_key, applied_delta)
    if next_personality is None:
        return _rejected("personality_trait_value_bound")
    after_metrics = personality_drift_metrics(
        next_personality,
        approved_checkpoint=approved_checkpoint,
        history=(
            *relevant_history,
            PersonalityEvolutionRecord(
                identity_id=identity_id,
                trait_key=proposal.trait_key,
                applied_delta=applied_delta,
                occurred_at=now,
            ),
        ),
        target_trait=proposal.trait_key,
        now=now,
    )
    budget_reason = _budget_reason(after_metrics)
    if budget_reason is not None:
        return _rejected(budget_reason)

    confidence_cap = (
        0.80
        + 0.015 * min(4, support_diversity.root_count - 8)
        + 0.010 * min(2, support_diversity.session_count - 6)
        + (0.020 if support_diversity.observation_span >= timedelta(days=180) else 0.0)
    )
    decision_confidence = round(min(proposal.confidence, confidence_cap, 0.90), 6)
    return PersonalityChangeEvaluation(
        kind=PersonalityDecisionKind.APPLIED,
        reason_code="personality_evolution_applied",
        plan=PersonalityEvolutionPlan(
            personality=next_personality,
            trait_key=proposal.trait_key,
            direction=proposal.direction,
            applied_delta=applied_delta,
            decision_confidence=decision_confidence,
            accepted_sources=cited_sources,
            diversity=support_diversity,
            before_metrics=before_metrics,
            after_metrics=after_metrics,
        ),
    )


def evaluate_personality_restore(
    proposal: PersonalityRestoreProposal,
    *,
    identity_id: str,
    personality: Personality,
    checkpoint: PersonalityCheckpointSnapshot,
) -> PersonalityRestoreEvaluation:
    """Validate an explicit checkpoint restore without evolution evidence or budget gates."""

    identity_id = non_blank(identity_id, "identity_id", maximum=128)
    _require_canonical_personality(personality)
    if proposal.expected_personality_version != personality.aggregate_version:
        return _restore_rejected("personality_target_version_conflict")
    if proposal.checkpoint_id != checkpoint.checkpoint_id:
        return _restore_rejected("personality_checkpoint_not_found")
    if checkpoint.identity_id != identity_id:
        return _restore_rejected("personality_checkpoint_identity_mismatch")
    if checkpoint.personality_schema_version != personality.schema_version:
        return _restore_rejected("personality_checkpoint_schema_mismatch")
    if checkpoint.source_aggregate_version > personality.aggregate_version:
        return _restore_rejected("personality_checkpoint_version_from_future")
    if proposal.checkpoint_hash != checkpoint.checkpoint_hash:
        return _restore_rejected("personality_checkpoint_hash_mismatch")
    checkpoint_personality = _checkpoint_personality(checkpoint)
    if (
        checkpoint_hash(
            identity_id=identity_id,
            checkpoint_kind=checkpoint.checkpoint_kind,
            personality=checkpoint_personality,
        )
        != checkpoint.checkpoint_hash
    ):
        return _restore_rejected("personality_checkpoint_hash_mismatch")
    current_by_key = {item.key: item for item in personality.traits}
    checkpoint_by_key = {item.key: item for item in checkpoint.traits}
    if any(
        checkpoint_by_key[key].baseline_value != current_by_key[key].baseline_value
        for key in CANONICAL_TRAIT_KEYS
    ):
        return _restore_rejected("personality_checkpoint_baseline_mismatch")
    changed = tuple(
        (key, current_by_key[key].value, checkpoint_by_key[key].value)
        for key in CANONICAL_TRAIT_KEYS
        if current_by_key[key].value != checkpoint_by_key[key].value
    )
    if not changed:
        return _restore_rejected("personality_restore_no_change")
    restored = Personality(
        schema_version=personality.schema_version,
        aggregate_version=personality.aggregate_version + 1,
        traits=tuple(
            PersonalityTrait(
                key=key,
                value=checkpoint_by_key[key].value,
                baseline_value=current_by_key[key].baseline_value,
            )
            for key in CANONICAL_TRAIT_KEYS
        ),
    )
    return PersonalityRestoreEvaluation(
        kind=PersonalityDecisionKind.APPLIED,
        reason_code="personality_checkpoint_restored",
        plan=PersonalityRestorePlan(
            personality=restored,
            checkpoint_id=checkpoint.checkpoint_id,
            changed_traits=changed,
        ),
    )


def personality_diversity(
    sources: tuple[PersonalityEvidenceSource, ...],
) -> PersonalityDiversity:
    """Measure the exact Stage 14 structural independence dimensions."""

    if not sources:
        return PersonalityDiversity(0, 0, 0, 0, 0, 0, 0, 0, 0, timedelta(0))
    signatures = {personality_content_signature(item.quote) for item in sources}
    lineages = Counter(item.lineage_id for item in sources)
    observed = [item.observed_at for item in sources]
    return PersonalityDiversity(
        root_count=len({item.root_message_id for item in sources}),
        interaction_count=len({item.root_interaction_id for item in sources}),
        session_count=len({item.root_session_id for item in sources}),
        signature_count=len(signatures),
        cluster_count=_near_duplicate_cluster_count(sources),
        week_bucket_count=len(
            {
                (item.observed_at.isocalendar().year, item.observed_at.isocalendar().week)
                for item in sources
            }
        ),
        month_bucket_count=len(
            {(item.observed_at.year, item.observed_at.month) for item in sources}
        ),
        lineage_count=len(lineages),
        lineage_capped_root_count=sum(
            min(MAX_COUNTED_ROOTS_PER_LINEAGE, count) for count in lineages.values()
        ),
        observation_span=max(observed) - min(observed),
    )


def trait_distance(left: Personality, right: Personality) -> TraitDistance:
    """Compare two complete canonical vectors without hiding concentrated drift."""

    _require_canonical_personality(left)
    _require_canonical_personality(right)
    left_values = {item.key: item.value for item in left.traits}
    right_values = {item.key: item.value for item in right.traits}
    deltas = tuple(abs(left_values[key] - right_values[key]) for key in CANONICAL_TRAIT_KEYS)
    return TraitDistance(linf=round(max(deltas), 6), l1=round(sum(deltas), 6))


def personality_drift_metrics(
    personality: Personality,
    *,
    approved_checkpoint: PersonalityCheckpointSnapshot,
    history: tuple[PersonalityEvolutionRecord, ...],
    target_trait: PersonalityTraitKey,
    now: datetime,
) -> PersonalityDriftMetrics:
    """Compute endpoint and non-refundable evolution-path measures."""

    now = aware_utc(now, "now")
    _require_canonical_personality(personality)
    activation = Personality(
        schema_version=personality.schema_version,
        aggregate_version=1,
        traits=tuple(
            PersonalityTrait(
                key=item.key,
                value=item.baseline_value,
                baseline_value=item.baseline_value,
            )
            for item in personality.traits
        ),
    )
    checkpoint_personality = _checkpoint_personality(approved_checkpoint)
    recent = tuple(item for item in history if item.occurred_at >= now - ROLLING_WINDOW)
    trait_history = tuple(item for item in history if item.trait_key == target_trait)
    recent_trait = tuple(item for item in recent if item.trait_key == target_trait)
    return PersonalityDriftMetrics(
        activation=trait_distance(personality, activation),
        approved_checkpoint=trait_distance(personality, checkpoint_personality),
        rolling_trait_path=round(sum(abs(item.applied_delta) for item in recent_trait), 6),
        rolling_global_path=round(sum(abs(item.applied_delta) for item in recent), 6),
        lifetime_trait_path=round(sum(abs(item.applied_delta) for item in trait_history), 6),
        lifetime_global_path=round(sum(abs(item.applied_delta) for item in history), 6),
    )


def _source_is_eligible(source: PersonalityEvidenceSource) -> bool:
    if (
        not source.canonical_user_message
        or not source.interaction_completed
        or source.accepted_as_inclination_evidence
    ):
        return False
    if hashlib.sha256(source.quote.encode("utf-8")).hexdigest() != source.content_hash:
        return False
    quote = _normalize_cyrillic_yo(unicodedata.normalize("NFKC", source.quote))
    return not (
        _ASSIGNMENT_OR_EVALUATION.search(quote)
        or _USER_SELF_ASCRIPTION.search(quote)
        or _RELATIONSHIP_MATERIAL.search(quote)
    )


def _diversity_reason(diversity: PersonalityDiversity) -> str | None:
    if diversity.observation_span < MIN_OBSERVATION_SPAN:
        return "personality_observation_span_too_short"
    if (
        diversity.root_count < MIN_ROOTS
        or diversity.interaction_count < MIN_ROOTS
        or diversity.session_count < MIN_SESSIONS
        or diversity.signature_count < MIN_ROOTS
        or diversity.cluster_count < MIN_CLUSTERS
        or diversity.week_bucket_count < MIN_WEEK_BUCKETS
        or diversity.month_bucket_count < MIN_MONTH_BUCKETS
        or diversity.lineage_count < MIN_LINEAGES
        or diversity.lineage_capped_root_count < MIN_ROOTS
    ):
        return "insufficient_personality_evidence_diversity"
    return None


def _budget_reason(metrics: PersonalityDriftMetrics) -> str | None:
    if metrics.rolling_trait_path > ROLLING_TRAIT_PATH_CAP + _EPSILON:
        return "personality_rolling_trait_budget_exhausted"
    if metrics.rolling_global_path > ROLLING_GLOBAL_PATH_CAP + _EPSILON:
        return "personality_rolling_global_budget_exhausted"
    if metrics.lifetime_trait_path > LIFETIME_TRAIT_PATH_CAP + _EPSILON:
        return "personality_lifetime_trait_budget_exhausted"
    if metrics.lifetime_global_path > LIFETIME_GLOBAL_PATH_CAP + _EPSILON:
        return "personality_lifetime_global_budget_exhausted"
    if (
        metrics.activation.linf > ACTIVATION_LINF_CAP + _EPSILON
        or metrics.activation.l1 > ACTIVATION_L1_CAP + _EPSILON
    ):
        return "personality_activation_distance_budget_exhausted"
    if (
        metrics.approved_checkpoint.linf > CHECKPOINT_LINF_CAP + _EPSILON
        or metrics.approved_checkpoint.l1 > CHECKPOINT_L1_CAP + _EPSILON
    ):
        return "personality_checkpoint_distance_budget_exhausted"
    return None


def _checkpoint_compatibility_reason(
    checkpoint: PersonalityCheckpointSnapshot,
    *,
    identity_id: str,
    personality: Personality,
) -> str | None:
    if checkpoint.identity_id != identity_id:
        return "personality_checkpoint_identity_mismatch"
    if checkpoint.personality_schema_version != personality.schema_version:
        return "personality_checkpoint_schema_mismatch"
    if checkpoint.source_aggregate_version > personality.aggregate_version:
        return "personality_checkpoint_version_from_future"
    checkpoint_personality = _checkpoint_personality(checkpoint)
    if (
        checkpoint_hash(
            identity_id=identity_id,
            checkpoint_kind=checkpoint.checkpoint_kind,
            personality=checkpoint_personality,
        )
        != checkpoint.checkpoint_hash
    ):
        return "personality_checkpoint_hash_mismatch"
    current = {item.key: item for item in personality.traits}
    if any(item.baseline_value != current[item.key].baseline_value for item in checkpoint.traits):
        return "personality_checkpoint_baseline_mismatch"
    return None


def _checkpoint_personality(checkpoint: PersonalityCheckpointSnapshot) -> Personality:
    return Personality(
        schema_version=checkpoint.personality_schema_version,
        aggregate_version=checkpoint.source_aggregate_version,
        traits=checkpoint.traits,
    )


def _with_exact_delta(
    personality: Personality,
    trait_key: PersonalityTraitKey,
    delta: float,
) -> Personality | None:
    traits: list[PersonalityTrait] = []
    for item in personality.traits:
        value = item.value + delta if item.key == trait_key else item.value
        if value < 0.0 or value > 1.0:
            return None
        traits.append(
            PersonalityTrait(
                key=item.key,
                value=value,
                baseline_value=item.baseline_value,
            )
        )
    return Personality(
        schema_version=personality.schema_version,
        aggregate_version=personality.aggregate_version + 1,
        traits=tuple(traits),
    )


def _require_canonical_personality(personality: Personality) -> None:
    if tuple(item.key for item in personality.traits) != CANONICAL_TRAIT_KEYS:
        raise ValueError("personality must contain the exact canonical Stage 14 trait set")


def _normalize_content(value: str) -> str:
    normalized = _normalize_cyrillic_yo(unicodedata.normalize("NFKC", value)).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_cyrillic_yo(value: str) -> str:
    """Keep Russian safety matching invariant to Cyrillic io/ie spelling."""

    return value.replace(
        "\N{CYRILLIC CAPITAL LETTER IO}", "\N{CYRILLIC CAPITAL LETTER IE}"
    ).replace("\N{CYRILLIC SMALL LETTER IO}", "\N{CYRILLIC SMALL LETTER IE}")


def personality_content_signature(value: str) -> str:
    """Return the versioned normalized evidence signature stored by the owner."""

    return hashlib.sha256(_normalize_content(value).encode("utf-8")).hexdigest()


def _near_duplicate_cluster_count(sources: tuple[PersonalityEvidenceSource, ...]) -> int:
    return len(set(_near_duplicate_cluster_ids(sources)))


def _near_duplicate_cluster_ids(
    sources: tuple[PersonalityEvidenceSource, ...],
) -> tuple[int, ...]:
    normalized = tuple(_normalize_content(item.quote) for item in sources)
    parent = list(range(len(normalized)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(normalized)):
        for right in range(left + 1, len(normalized)):
            if _near_duplicate(normalized[left], normalized[right]):
                union(left, right)
    return tuple(find(index) for index in range(len(normalized)))


def _near_duplicate(left: str, right: str) -> bool:
    if left == right:
        return True
    left_tokens = frozenset(left.split())
    right_tokens = frozenset(right.split())
    token_score = _jaccard(left_tokens, right_tokens)
    left_trigrams = _trigrams(left)
    right_trigrams = _trigrams(right)
    trigram_score = _jaccard(left_trigrams, right_trigrams)
    return (
        token_score >= NEAR_DUPLICATE_TOKEN_JACCARD
        or trigram_score >= NEAR_DUPLICATE_TRIGRAM_JACCARD
    )


def _trigrams(value: str) -> frozenset[str]:
    compact = f"  {value}  "
    return frozenset(compact[index : index + 3] for index in range(len(compact) - 2))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def _rejected(reason_code: str) -> PersonalityChangeEvaluation:
    return PersonalityChangeEvaluation(
        kind=PersonalityDecisionKind.REJECTED,
        reason_code=reason_code,
    )


def _restore_rejected(reason_code: str) -> PersonalityRestoreEvaluation:
    return PersonalityRestoreEvaluation(
        kind=PersonalityDecisionKind.REJECTED,
        reason_code=reason_code,
    )
