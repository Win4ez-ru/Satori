"""Deterministic lifecycle and versioned identity policy for Stage 12-14 reflection."""

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from satori.core.reflection import (
    ReflectionCandidate,
    ReflectionInclinationCandidate,
    ReflectionLineageKind,
    ReflectionOwnerObservation,
    ReflectionPersonalityCandidate,
    ReflectionPurpose,
    ReflectionSourceKind,
    ReflectionTargetOwner,
)

REFLECTION_SCHEMA_VERSION_V1 = 1
REFLECTION_POLICY_VERSION_V1 = 1
REFLECTION_SCHEMA_VERSION_V2 = 2
REFLECTION_POLICY_VERSION_V2 = 2
REFLECTION_SCHEMA_VERSION_V3 = 3
REFLECTION_POLICY_VERSION_V3 = 3
REFLECTION_SCHEMA_VERSION = REFLECTION_SCHEMA_VERSION_V2
REFLECTION_POLICY_VERSION = REFLECTION_POLICY_VERSION_V2
REFLECTION_MAX_SOURCES = 12
REFLECTION_MAX_SOURCE_CHARACTERS = 4800
REFLECTION_MAX_TARGET_POSITIONS = 12
REFLECTION_MAX_TARGET_INCLINATIONS = 12
REFLECTION_MAX_PROPOSALS = 3
REFLECTION_MAX_ATTEMPTS = 2
REFLECTION_MAX_OUTPUT_TOKENS = 768

_AFFECTIVE_APPRAISAL_FIELDS = (
    "pleasantness",
    "activation",
    "novelty",
    "salience",
    "uncertainty",
    "curiosity_signal",
    "interest_signal",
    "humor_signal",
    "concern_signal",
    "frustration_signal",
    "confidence_signal",
)
_AFFECTIVE_DELTA_FIELDS = (
    "valence",
    "arousal",
    "tension",
    "curiosity",
    "interest",
    "amusement",
    "concern",
    "frustration",
    "situational_confidence",
)


class ReflectionTriggerKind(StrEnum):
    AUTOMATIC = "automatic"
    EXPLICIT_LOCAL = "explicit_local"


class ReflectionRunStatus(StrEnum):
    PENDING_GENERATION = "pending_generation"
    PROPOSALS_READY = "proposals_ready"
    APPLYING = "applying"
    COMPLETED = "completed"
    RETRYABLE_FAILURE = "retryable_failure"
    EXHAUSTED = "exhausted"

    @property
    def terminal(self) -> bool:
        return self in {ReflectionRunStatus.COMPLETED, ReflectionRunStatus.EXHAUSTED}

    @property
    def requires_routing(self) -> bool:
        return self in {
            ReflectionRunStatus.PROPOSALS_READY,
            ReflectionRunStatus.APPLYING,
        }


class ReflectionAttemptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ReflectionOutcomeDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


def reflection_trigger_reason(
    trigger: ReflectionTriggerKind,
    *,
    root_count: int,
    interaction_count: int,
    observation_span: timedelta,
    completed_within_day: bool,
    completed_within_cooldown: bool,
) -> str | None:
    """Evaluate deterministic rarity/cost inputs without provider involvement."""

    minimum_roots = 8 if trigger is ReflectionTriggerKind.AUTOMATIC else 4
    minimum_interactions = 6 if trigger is ReflectionTriggerKind.AUTOMATIC else 3
    if root_count < minimum_roots:
        return "insufficient_eligible_roots"
    if interaction_count < minimum_interactions:
        return "insufficient_distinct_interactions"
    if completed_within_day:
        return "rolling_daily_cap"
    if trigger is ReflectionTriggerKind.AUTOMATIC:
        if observation_span < timedelta(days=7):
            return "observation_span_too_short"
        if completed_within_cooldown:
            return "reflection_cooldown"
    return None


@dataclass(frozen=True, slots=True)
class ReflectionSourceRecord:
    source_id: str
    run_id: str
    ordinal: int
    kind: ReflectionSourceKind
    evidence_edge_id: str
    evidence_edge_version: int
    root_interaction_id: str
    root_message_id: str
    root_counterparty_id: str
    observed_at: datetime
    content_hash: str
    affective_transition_id: str | None = None
    affective_state_version: int | None = None
    affective_signal_hash: str | None = None
    upstream_lineage_kind: ReflectionLineageKind | None = None
    upstream_lineage_id: str | None = None

    def __post_init__(self) -> None:
        attachment = (
            self.affective_transition_id,
            self.affective_state_version,
            self.affective_signal_hash,
        )
        if any(item is not None for item in attachment) and not all(
            item is not None for item in attachment
        ):
            raise ValueError("reflection affect attachment must be all-or-none")
        if self.affective_transition_id is not None and not self.affective_transition_id.strip():
            raise ValueError("affective_transition_id must not be blank")
        if self.affective_state_version is not None and (
            type(self.affective_state_version) is not int or self.affective_state_version < 1
        ):
            raise ValueError("affective_state_version must be positive")
        if self.affective_signal_hash is not None and not self.affective_signal_hash.strip():
            raise ValueError("affective_signal_hash must not be blank")
        lineage = (self.upstream_lineage_kind, self.upstream_lineage_id)
        if any(item is not None for item in lineage) and not all(
            item is not None for item in lineage
        ):
            raise ValueError("reflection upstream lineage must be all-or-none")
        if self.upstream_lineage_id is not None and not self.upstream_lineage_id.strip():
            raise ValueError("upstream_lineage_id must not be blank")


@dataclass(frozen=True, slots=True)
class ReflectionRun:
    run_id: str
    run_key: str
    identity_id: str
    schema_version: int
    policy_version: int
    trigger_kind: ReflectionTriggerKind
    source_set_hash: str
    status: ReflectionRunStatus
    aggregate_version: int
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    purpose: ReflectionPurpose = ReflectionPurpose.GENERAL

    def __post_init__(self) -> None:
        versions = (self.schema_version, self.policy_version)
        if versions not in {
            (REFLECTION_SCHEMA_VERSION_V1, REFLECTION_POLICY_VERSION_V1),
            (REFLECTION_SCHEMA_VERSION_V2, REFLECTION_POLICY_VERSION_V2),
            (REFLECTION_SCHEMA_VERSION_V3, REFLECTION_POLICY_VERSION_V3),
        }:
            raise ValueError("unsupported reflection run version")
        if self.purpose is ReflectionPurpose.GENERAL and self.schema_version >= 3:
            raise ValueError("general reflection cannot use the V3 personality wire")
        if (
            self.purpose is ReflectionPurpose.PERSONALITY_EVOLUTION
            and self.schema_version != REFLECTION_SCHEMA_VERSION_V3
        ):
            raise ValueError("personality_evolution reflection requires V3")


@dataclass(frozen=True, slots=True)
class ReflectionAttempt:
    attempt_id: str
    run_id: str
    ordinal: int
    status: ReflectionAttemptStatus
    reason_code: str
    provider: str
    model: str
    formation_method: str
    started_at: datetime
    finished_at: datetime
    metrics: dict[str, int | float | None]


@dataclass(frozen=True, slots=True)
class ReflectionProposal:
    proposal_id: str
    run_id: str
    ordinal: int
    target_owner: ReflectionTargetOwner
    payload: dict[str, object]
    evidence_source_ids: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReflectionOutcome:
    outcome_id: str
    proposal_id: str
    target_policy_version: int
    decision: ReflectionOutcomeDecision
    reason_code: str
    target_aggregate_type: str | None
    target_aggregate_id: str | None
    decided_at: datetime


def complete_reflection_run(run: ReflectionRun, *, completed_at: datetime) -> ReflectionRun:
    """Finalize an applying run once while preserving completed replay identity."""

    if run.status is ReflectionRunStatus.COMPLETED:
        return run
    if run.status is not ReflectionRunStatus.APPLYING:
        raise ValueError("only an applying reflection run can be completed")
    return replace(
        run,
        status=ReflectionRunStatus.COMPLETED,
        aggregate_version=run.aggregate_version + 1,
        updated_at=completed_at,
        completed_at=completed_at,
    )


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def affective_signal_hash(
    *,
    transition_id: str,
    identity_id: str,
    interaction_id: str,
    source_message_id: str,
    resulting_state_version: int,
    source: ReflectionSourceRecord,
    appraisal_schema_version: int,
    appraisal_payload: Mapping[str, object],
    appraisal_confidence: float,
    applied_delta: Mapping[str, object],
) -> str:
    """Bind a V2 source to one immutable owner-approved affective transition."""

    transition_identity = {
        "transition_id": _hash_text(transition_id, "transition_id"),
        "identity_id": _hash_text(identity_id, "identity_id"),
        "interaction_id": _hash_text(interaction_id, "interaction_id"),
        "source_message_id": _hash_text(source_message_id, "source_message_id"),
        "resulting_state_version": _positive_hash_version(
            resulting_state_version, "resulting_state_version"
        ),
    }
    source_identity = {
        "kind": source.kind.value,
        "edge_id": source.evidence_edge_id,
        "edge_version": source.evidence_edge_version,
        "root_interaction_id": source.root_interaction_id,
        "root_message_id": source.root_message_id,
        "root_counterparty_id": source.root_counterparty_id,
        "content_hash": source.content_hash,
    }
    confidence = _finite_hash_number(appraisal_confidence, "appraisal_confidence")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("appraisal_confidence must be in [0, 1]")
    return _digest(
        {
            "hash_schema": "satori.reflection.affective-signal.v1",
            "transition": transition_identity,
            "source": source_identity,
            "appraisal_schema_version": _positive_hash_version(
                appraisal_schema_version, "appraisal_schema_version"
            ),
            "appraisal": _canonical_numeric_mapping(
                appraisal_payload,
                expected=_AFFECTIVE_APPRAISAL_FIELDS,
                field_name="appraisal_payload",
            ),
            "appraisal_confidence": confidence,
            "applied_delta": _canonical_numeric_mapping(
                applied_delta,
                expected=_AFFECTIVE_DELTA_FIELDS,
                field_name="applied_delta",
            ),
        }
    )


def source_set_hash(
    sources: tuple[ReflectionSourceRecord, ...],
    *,
    schema_version: int = REFLECTION_SCHEMA_VERSION,
    purpose: ReflectionPurpose = ReflectionPurpose.GENERAL,
) -> str:
    if schema_version not in {
        REFLECTION_SCHEMA_VERSION_V1,
        REFLECTION_SCHEMA_VERSION_V2,
        REFLECTION_SCHEMA_VERSION_V3,
    }:
        raise ValueError("unsupported reflection source hash schema_version")
    if schema_version < REFLECTION_SCHEMA_VERSION_V3 and purpose is not ReflectionPurpose.GENERAL:
        raise ValueError("Reflection V1/V2 sources require the general purpose")
    if (
        schema_version == REFLECTION_SCHEMA_VERSION_V3
        and purpose is not ReflectionPurpose.PERSONALITY_EVOLUTION
    ):
        raise ValueError("Reflection V3 sources require personality_evolution purpose")
    ordered = sorted(sources, key=lambda item: item.ordinal)
    if not ordered or tuple(item.ordinal for item in ordered) != tuple(range(len(ordered))):
        raise ValueError("reflection sources require contiguous zero-based ordinals")
    payload: list[dict[str, object]] = []
    for item in ordered:
        entry: dict[str, object] = {
            "kind": item.kind.value,
            "edge_id": item.evidence_edge_id,
            "edge_version": item.evidence_edge_version,
            "root_interaction_id": item.root_interaction_id,
            "root_message_id": item.root_message_id,
            "root_counterparty_id": item.root_counterparty_id,
            "content_hash": item.content_hash,
        }
        if schema_version == REFLECTION_SCHEMA_VERSION_V2:
            entry["affective_attachment"] = (
                None
                if item.affective_transition_id is None
                else {
                    "transition_id": item.affective_transition_id,
                    "state_version": item.affective_state_version,
                    "signal_hash": item.affective_signal_hash,
                }
            )
        elif schema_version == REFLECTION_SCHEMA_VERSION_V3:
            if item.upstream_lineage_kind is None or item.upstream_lineage_id is None:
                raise ValueError("Reflection V3 source requires upstream lineage")
            if item.affective_transition_id is not None:
                raise ValueError("Reflection V3 source cannot contain affect attachment")
            entry["purpose"] = purpose.value
            entry["upstream_lineage"] = {
                "kind": item.upstream_lineage_kind.value,
                "id": item.upstream_lineage_id,
            }
        payload.append(entry)
    return _digest(payload)


def reflection_run_key(
    *,
    identity_id: str,
    source_hash: str,
    schema_version: int = REFLECTION_SCHEMA_VERSION,
    policy_version: int = REFLECTION_POLICY_VERSION,
    purpose: ReflectionPurpose = ReflectionPurpose.GENERAL,
) -> str:
    if (schema_version, policy_version) not in {
        (REFLECTION_SCHEMA_VERSION_V1, REFLECTION_POLICY_VERSION_V1),
        (REFLECTION_SCHEMA_VERSION_V2, REFLECTION_POLICY_VERSION_V2),
        (REFLECTION_SCHEMA_VERSION_V3, REFLECTION_POLICY_VERSION_V3),
    }:
        raise ValueError("unsupported reflection run key version")
    if schema_version < REFLECTION_SCHEMA_VERSION_V3 and purpose is not ReflectionPurpose.GENERAL:
        raise ValueError("Reflection V1/V2 runs require the general purpose")
    if (
        schema_version == REFLECTION_SCHEMA_VERSION_V3
        and purpose is not ReflectionPurpose.PERSONALITY_EVOLUTION
    ):
        raise ValueError("Reflection V3 runs require personality_evolution purpose")
    payload: dict[str, object] = {
        "identity_id": identity_id,
        "policy_version": policy_version,
        "schema_version": schema_version,
        "source_set_hash": source_hash,
    }
    if schema_version == REFLECTION_SCHEMA_VERSION_V3:
        payload["purpose"] = purpose.value
    return _digest(payload)


def reflection_run_id(run_key: str) -> str:
    return f"reflection-run-{run_key[:40]}"


def reflection_source_id(*, run_id: str, ordinal: int) -> str:
    return f"reflection-source-{_digest({'run_id': run_id, 'ordinal': ordinal})[:40]}"


def proposal_payload(candidate: ReflectionCandidate) -> dict[str, object]:
    payload = asdict(candidate)
    return _json_value(payload)


def reflection_proposal_id(*, run_id: str, ordinal: int, candidate: ReflectionCandidate) -> str:
    digest = _digest({"run_id": run_id, "ordinal": ordinal, "payload": proposal_payload(candidate)})
    return f"reflection-proposal-{digest[:40]}"


def reflection_outcome_id(*, proposal_id: str, target_policy_version: int) -> str:
    digest = _digest({"proposal_id": proposal_id, "target_policy_version": target_policy_version})
    return f"reflection-outcome-{digest[:40]}"


def candidate_evidence_source_ids(candidate: ReflectionCandidate) -> tuple[str, ...]:
    if isinstance(candidate, ReflectionOwnerObservation):
        return candidate.evidence_source_ids
    if isinstance(candidate, ReflectionInclinationCandidate):
        return candidate.source_ids
    if isinstance(candidate, ReflectionPersonalityCandidate):
        return tuple(item.source_id for item in candidate.citations)
    return tuple(item.source_id for item in candidate.evidence)


def validate_candidate_sources(
    candidate: ReflectionCandidate, *, allowed_source_ids: frozenset[str]
) -> None:
    cited = candidate_evidence_source_ids(candidate)
    if not cited or not set(cited) <= allowed_source_ids:
        raise ValueError("reflection candidate cites a source outside the fixed run set")


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _canonical_numeric_mapping(
    values: Mapping[str, object],
    *,
    expected: tuple[str, ...],
    field_name: str,
) -> dict[str, float]:
    if set(values) != set(expected):
        raise ValueError(f"{field_name} has unknown or missing fields")
    return {key: _finite_hash_number(values[key], f"{field_name}.{key}") for key in expected}


def _finite_hash_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number")
    numeric = float(value)
    return 0.0 if numeric == 0.0 else numeric


def _hash_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _positive_hash_version(value: int, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be positive")
    return value


def _json_value(value: object) -> dict[str, object]:
    encoded = json.loads(json.dumps(value, default=lambda item: item.value, sort_keys=True))
    if not isinstance(encoded, dict):
        raise TypeError("reflection proposal payload must be an object")
    return encoded
