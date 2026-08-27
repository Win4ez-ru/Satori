"""Provider-neutral contracts for bounded versioned Stage 12-14 reflection."""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.core.inclinations import (
    InclinationAffectiveSignal,
    InclinationKind,
    InclinationProposal,
    InclinationStateReference,
)
from satori.core.personality import (
    PersonalityChangeProposal,
    PersonalityCitation,
    PersonalityCitationRole,
    PersonalityDirection,
    PersonalityStateReference,
    PersonalityTraitKey,
)
from satori.core.positions import (
    PositionEvidenceRole,
    PositionKind,
    PositionStance,
    PositionStateReference,
    PositionValueReference,
)
from satori.core.provider_metrics import ProviderExecutionMetrics


def _text(value: str, field_name: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    result = value.strip()
    if len(result) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    return result


class ReflectionSourceKind(StrEnum):
    POSITION_EVIDENCE = "position_evidence"
    EPISODIC_MEMORY_EVIDENCE = "episodic_memory_evidence"


class ReflectionPurpose(StrEnum):
    GENERAL = "general"
    PERSONALITY_EVOLUTION = "personality_evolution"


class ReflectionLineageKind(StrEnum):
    POSITION = "position"
    EPISODIC_MEMORY = "episodic_memory"


class ReflectionTargetOwner(StrEnum):
    SATORI_POSITIONS = "satori_positions"
    SATORI_INCLINATIONS = "satori_inclinations"
    PERSONALITY = "personality"
    VALUES = "values"


@dataclass(frozen=True, slots=True)
class ReflectionSource:
    source_id: str
    kind: ReflectionSourceKind
    evidence_edge_id: str
    evidence_edge_version: int
    root_interaction_id: str
    root_message_id: str
    root_counterparty_id: str
    observed_at: datetime
    content_hash: str
    quote: str
    affective: InclinationAffectiveSignal | None = None
    root_session_id: str | None = None
    upstream_lineage_kind: ReflectionLineageKind | None = None
    upstream_lineage_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "source_id",
            "evidence_edge_id",
            "root_interaction_id",
            "root_message_id",
            "root_counterparty_id",
        ):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name, maximum=128)
            )
        if type(self.evidence_edge_version) is not int or self.evidence_edge_version < 1:
            raise ValueError("evidence_edge_version must be positive")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(
            self, "content_hash", _text(self.content_hash, "content_hash", maximum=64)
        )
        object.__setattr__(self, "quote", _text(self.quote, "quote", maximum=512))
        if self.affective is not None and not isinstance(
            self.affective, InclinationAffectiveSignal
        ):
            raise ValueError("affective must be an InclinationAffectiveSignal")
        if self.root_session_id is not None:
            object.__setattr__(
                self,
                "root_session_id",
                _text(self.root_session_id, "root_session_id", maximum=128),
            )
        lineage = (self.upstream_lineage_kind, self.upstream_lineage_id)
        if any(item is not None for item in lineage) and not all(
            item is not None for item in lineage
        ):
            raise ValueError("reflection upstream lineage must be all-or-none")
        if self.upstream_lineage_id is not None:
            object.__setattr__(
                self,
                "upstream_lineage_id",
                _text(self.upstream_lineage_id, "upstream_lineage_id", maximum=128),
            )


@dataclass(frozen=True, slots=True)
class ReflectionCitation:
    source_id: str
    role: PositionEvidenceRole

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id", maximum=128))


@dataclass(frozen=True, slots=True)
class ReflectionPositionCandidate:
    target_owner: ReflectionTargetOwner
    proposition: str
    kind: PositionKind
    stance: PositionStance
    confidence: float
    evidence: tuple[ReflectionCitation, ...]
    value_key: str | None = None
    revises_position_id: str | None = None
    opposes_position_id: str | None = None
    challenges_position_id: str | None = None
    expected_target_version: int | None = None

    def __post_init__(self) -> None:
        if self.target_owner is not ReflectionTargetOwner.SATORI_POSITIONS:
            raise ValueError("position candidate requires satori_positions target")
        object.__setattr__(self, "proposition", _text(self.proposition, "proposition", maximum=240))
        if (
            isinstance(self.confidence, bool)
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must be in [0, 1]")
        evidence = tuple(self.evidence)
        if not evidence or len(evidence) > 8:
            raise ValueError("position candidate requires one to eight citations")
        if len({item.source_id for item in evidence}) != len(evidence):
            raise ValueError("position candidate citations must be unique")
        object.__setattr__(self, "evidence", evidence)
        for field_name in (
            "value_key",
            "revises_position_id",
            "opposes_position_id",
            "challenges_position_id",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _text(value, field_name, maximum=128))
        targets = (self.revises_position_id, self.opposes_position_id, self.challenges_position_id)
        if sum(item is not None for item in targets) > 1:
            raise ValueError("position candidate accepts at most one target operation")
        if self.expected_target_version is not None and (
            type(self.expected_target_version) is not int or self.expected_target_version < 1
        ):
            raise ValueError("expected_target_version must be positive")
        if (self.expected_target_version is not None) != any(item is not None for item in targets):
            raise ValueError("target operation and expected_target_version must appear together")


@dataclass(frozen=True, slots=True)
class ReflectionOwnerObservation:
    target_owner: ReflectionTargetOwner
    observation: str
    evidence_source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.target_owner not in {
            ReflectionTargetOwner.PERSONALITY,
            ReflectionTargetOwner.VALUES,
        }:
            raise ValueError("owner observation target must be personality or values")
        object.__setattr__(self, "observation", _text(self.observation, "observation", maximum=240))
        ids = tuple(
            _text(item, "evidence_source_id", maximum=128) for item in self.evidence_source_ids
        )
        if not ids or len(ids) > 8 or len(set(ids)) != len(ids):
            raise ValueError("owner observation requires one to eight unique evidence sources")
        object.__setattr__(self, "evidence_source_ids", ids)


@dataclass(frozen=True, slots=True)
class ReflectionInclinationCandidate:
    """Strict Reflection V2 candidate with no provider-owned state delta."""

    target_owner: ReflectionTargetOwner
    kind: InclinationKind
    topic: str
    alternative_topic: str | None
    confidence: float
    source_ids: tuple[str, ...]
    target_inclination_id: str | None = None
    expected_target_version: int | None = None

    def __post_init__(self) -> None:
        if self.target_owner is not ReflectionTargetOwner.SATORI_INCLINATIONS:
            raise ValueError("inclination candidate requires satori_inclinations target")
        validated = InclinationProposal(
            kind=self.kind,
            topic=self.topic,
            alternative_topic=self.alternative_topic,
            confidence=self.confidence,
            source_ids=self.source_ids,
            target_inclination_id=self.target_inclination_id,
            expected_target_version=self.expected_target_version,
        )
        for field_name in (
            "kind",
            "topic",
            "alternative_topic",
            "confidence",
            "source_ids",
            "target_inclination_id",
            "expected_target_version",
        ):
            object.__setattr__(self, field_name, getattr(validated, field_name))


@dataclass(frozen=True, slots=True)
class ReflectionPersonalityCitation:
    """Strict opaque V3 citation without a free-text rationale."""

    source_id: str
    role: PersonalityCitationRole

    def __post_init__(self) -> None:
        validated = PersonalityCitation(source_id=self.source_id, role=self.role)
        object.__setattr__(self, "source_id", validated.source_id)
        object.__setattr__(self, "role", validated.role)


@dataclass(frozen=True, slots=True)
class ReflectionPersonalityCandidate:
    """Reflection V3 direction-only personality candidate."""

    target_owner: ReflectionTargetOwner
    trait_key: PersonalityTraitKey
    direction: PersonalityDirection
    confidence: float
    citations: tuple[ReflectionPersonalityCitation, ...]
    expected_personality_version: int

    def __post_init__(self) -> None:
        if self.target_owner is not ReflectionTargetOwner.PERSONALITY:
            raise ValueError("personality candidate requires personality target")
        citations = tuple(self.citations)
        validated = PersonalityChangeProposal(
            trait_key=self.trait_key,
            direction=self.direction,
            confidence=self.confidence,
            citations=tuple(
                PersonalityCitation(source_id=item.source_id, role=item.role) for item in citations
            ),
            expected_personality_version=self.expected_personality_version,
        )
        object.__setattr__(self, "trait_key", validated.trait_key)
        object.__setattr__(self, "direction", validated.direction)
        object.__setattr__(self, "confidence", validated.confidence)
        object.__setattr__(self, "citations", citations)
        object.__setattr__(
            self,
            "expected_personality_version",
            validated.expected_personality_version,
        )


ReflectionCandidate = (
    ReflectionPositionCandidate
    | ReflectionOwnerObservation
    | ReflectionInclinationCandidate
    | ReflectionPersonalityCandidate
)


@dataclass(frozen=True, slots=True)
class ReflectionProposalDocument:
    schema_version: int
    proposals: tuple[ReflectionCandidate, ...]

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2, 3}:
            raise ValueError("unsupported reflection proposal schema_version")
        proposals = tuple(self.proposals)
        maximum = 1 if self.schema_version == 3 else 3
        if len(proposals) > maximum:
            raise ValueError("reflection proposal count exceeds policy")
        if self.schema_version == 1 and any(
            isinstance(item, ReflectionInclinationCandidate) for item in proposals
        ):
            raise ValueError("reflection schema v1 cannot contain inclination candidates")
        personality_candidates = tuple(
            item for item in proposals if isinstance(item, ReflectionPersonalityCandidate)
        )
        if self.schema_version < 3 and personality_candidates:
            raise ValueError("Reflection V1/V2 cannot contain personality change candidates")
        if self.schema_version == 3 and len(personality_candidates) != len(proposals):
            raise ValueError("Reflection V3 accepts only personality change candidates")
        object.__setattr__(self, "proposals", proposals)


@dataclass(frozen=True, slots=True)
class ReflectionGenerationRequest:
    schema_version: int
    trace_id: str
    run_id: str
    identity_id: str
    policy_version: int
    max_proposals: int
    sources: tuple[ReflectionSource, ...]
    current_positions: tuple[PositionStateReference, ...]
    values: tuple[PositionValueReference, ...]
    current_inclinations: tuple[InclinationStateReference, ...] = ()
    purpose: ReflectionPurpose = ReflectionPurpose.GENERAL
    personality_state: PersonalityStateReference | None = None

    def __post_init__(self) -> None:
        if (self.schema_version, self.policy_version) not in {(1, 1), (2, 2), (3, 3)}:
            raise ValueError("unsupported reflection request version")
        for field_name in ("trace_id", "run_id", "identity_id"):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name, maximum=128)
            )
        maximum = 1 if self.schema_version == 3 else 3
        if type(self.max_proposals) is not int or not 0 < self.max_proposals <= maximum:
            raise ValueError("max_proposals must be in [1, 3]")
        sources = tuple(self.sources)
        if (
            not sources
            or len(sources) > 12
            or len({item.source_id for item in sources}) != len(sources)
        ):
            raise ValueError("reflection request requires one to twelve unique sources")
        if sum(len(item.quote) for item in sources) > 4800:
            raise ValueError("reflection source character budget exceeded")
        positions = tuple(self.current_positions)
        values = tuple(self.values)
        if len(positions) > 12:
            raise ValueError("reflection target position limit exceeded")
        if len({item.position_id for item in positions}) != len(positions):
            raise ValueError("reflection position references must be unique")
        if len({item.key for item in values}) != len(values):
            raise ValueError("reflection value references must be unique")
        inclinations = tuple(self.current_inclinations)
        if len(inclinations) > 12:
            raise ValueError("reflection target inclination limit exceeded")
        if len({item.inclination_id for item in inclinations}) != len(inclinations):
            raise ValueError("reflection inclination references must be unique")
        if self.schema_version == 1 and (
            inclinations or any(item.affective is not None for item in sources)
        ):
            raise ValueError("reflection schema v1 cannot contain Stage 13 state")
        if self.schema_version < 3:
            if self.purpose is not ReflectionPurpose.GENERAL:
                raise ValueError("Reflection V1/V2 requests require general purpose")
            if self.personality_state is not None:
                raise ValueError("Reflection V1/V2 requests cannot contain personality state")
        else:
            if self.purpose is not ReflectionPurpose.PERSONALITY_EVOLUTION:
                raise ValueError("Reflection V3 requests require personality_evolution purpose")
            if self.personality_state is None:
                raise ValueError("Reflection V3 request requires personality state reference")
            if self.personality_state.identity_id != self.identity_id:
                raise ValueError("personality state reference identity mismatch")
            if positions or values or inclinations:
                raise ValueError("Reflection V3 request cannot contain general target state")
            if len(sources) < 8:
                raise ValueError("Reflection V3 request requires at least eight sources")
            if any(
                item.affective is not None
                or item.root_session_id is None
                or item.upstream_lineage_kind is None
                or item.upstream_lineage_id is None
                for item in sources
            ):
                raise ValueError("Reflection V3 sources require session/lineage and no affect")
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "current_positions", positions)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "current_inclinations", inclinations)


@dataclass(frozen=True, slots=True)
class ReflectionProviderResponse:
    document: ReflectionProposalDocument
    provider: str
    model: str
    formation_method: str
    metrics: ProviderExecutionMetrics | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _text(self.provider, "provider", maximum=128))
        object.__setattr__(self, "model", _text(self.model, "model", maximum=256))
        object.__setattr__(
            self, "formation_method", _text(self.formation_method, "formation_method", maximum=128)
        )


class ReflectionProviderError(Exception):
    def __init__(self, provider: str, model: str, message: str) -> None:
        self.provider = _text(provider, "provider", maximum=128)
        self.model = _text(model, "model", maximum=256)
        super().__init__(_text(message, "message", maximum=512))
