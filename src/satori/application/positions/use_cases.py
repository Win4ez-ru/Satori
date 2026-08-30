"""Incremental position formation plus immutable position/inclination reads and export."""

# ruff: noqa: RUF001  # Russian tokenization and stopwords intentionally use Cyrillic.

import json
import logging
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from satori.application.positions.contracts import (
    InclinationContextItem,
    PositionContextItem,
    SatoriInclinationsContext,
    SatoriPositionsContext,
    inclinations_context_json,
    positions_context_json,
)
from satori.application.positions.ports import PositionsUnitOfWork
from satori.core.clock import Clock
from satori.core.ids import IdGenerator
from satori.core.inclinations import InclinationKind, InclinationStateReference
from satori.core.ports.providers import StructuredGenerationPort
from satori.core.positions import (
    PositionFormationProviderResponse,
    PositionFormationRequest,
    PositionStateReference,
)
from satori.domain.inclinations import (
    INCLINATION_CONTEXT_SCHEMA_VERSION,
    INCLINATION_POLICY_VERSION,
    SatoriInclination,
    project_inclination_score,
)
from satori.domain.positions import (
    POSITION_FORMATION_VERSION,
    POSITION_POLICY_VERSION,
    PositionDecisionKind,
    PositionFormationDecision,
    PositionManager,
    PositionRevision,
    PositionStatus,
    SatoriPosition,
    position_idempotency_key,
)
from satori.domain.validation import aware_utc

POSITION_REQUEST_SCHEMA_VERSION = 1
POSITIONS_CONTEXT_SCHEMA_VERSION = 1
PositionsUnitOfWorkFactory = Callable[[], PositionsUnitOfWork]
PositionFormationProvider = StructuredGenerationPort[
    PositionFormationRequest, PositionFormationProviderResponse
]


def _log_fields(**fields: object) -> dict[str, object]:
    return {"satori_fields": fields}


@dataclass(slots=True)
class FormSatoriPositions:
    unit_of_work_factory: PositionsUnitOfWorkFactory
    provider: PositionFormationProvider
    manager: PositionManager
    clock: Clock
    id_generator: IdGenerator
    max_source_messages: int = 8
    max_positions: int = 3
    monotonic: Callable[[], float] = time.perf_counter
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.positions"))

    async def execute(
        self, source_interaction_id: str, *, trace_id: str
    ) -> PositionFormationDecision:
        key = position_idempotency_key(source_interaction_id, POSITION_FORMATION_VERSION)
        with self.unit_of_work_factory() as unit_of_work:
            prior = unit_of_work.positions.get_decision(key)
            messages = unit_of_work.positions.get_source_messages(
                source_interaction_id, limit=self.max_source_messages
            )
        if prior is not None:
            return prior
        if not messages:
            raise ValueError("position source completed interaction does not exist")
        current = next(item for item in messages if item.interaction_id == source_interaction_id)
        with self.unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.positions.list_positions(
                identity_id=current.identity_id, current_only=True
            )
            values = unit_of_work.positions.get_value_references(current.identity_id)
        request = PositionFormationRequest(
            schema_version=POSITION_REQUEST_SCHEMA_VERSION,
            trace_id=trace_id,
            source_interaction_id=source_interaction_id,
            source_message_id=current.message_id,
            identity_id=current.identity_id,
            formation_version=POSITION_FORMATION_VERSION,
            max_positions=self.max_positions,
            messages=messages,
            current_positions=tuple(
                PositionStateReference(
                    position_id=item.position_id,
                    aggregate_version=item.aggregate_version,
                    kind=item.kind,
                    stance=item.stance,
                    status=item.status.value,
                    proposition=item.proposition,
                    confidence=item.confidence,
                )
                for item in existing
            ),
            values=values,
        )
        started = self.monotonic()
        self.logger.info(
            "position_formation_started",
            extra=_log_fields(
                source_interaction_id=source_interaction_id,
                formation_version=POSITION_FORMATION_VERSION,
                source_message_count=len(messages),
                current_position_count=len(existing),
            ),
        )
        response = await self.provider.generate_structured(request)
        decision_id = self.id_generator.new()
        now = self.clock.now()
        with self.unit_of_work_factory() as unit_of_work:
            latest = unit_of_work.positions.list_positions(
                identity_id=current.identity_id, current_only=False
            )
            plan = self.manager.evaluate(
                response.proposal.positions,
                identity_id=current.identity_id,
                current_message_id=current.message_id,
                sources=messages,
                value_keys=frozenset(item.key for item in values),
                existing_positions=latest,
                max_positions=self.max_positions,
                now=now,
                decision_id=decision_id,
                new_id=self.id_generator.new,
            )
            changed_count = len(plan.revisions)
            kind = (
                PositionDecisionKind.APPLIED
                if changed_count
                else PositionDecisionKind.REJECTED
                if plan.rejected_count
                else PositionDecisionKind.SKIPPED
            )
            reason = (
                "owner_changes_applied"
                if kind is PositionDecisionKind.APPLIED
                else "proposal_rejected"
                if kind is PositionDecisionKind.REJECTED
                else "no_new_eligible_evidence"
            )
            decision = PositionFormationDecision(
                decision_id=decision_id,
                idempotency_key=key,
                source_interaction_id=source_interaction_id,
                source_message_id=current.message_id,
                identity_id=current.identity_id,
                formation_version=POSITION_FORMATION_VERSION,
                policy_version=POSITION_POLICY_VERSION,
                kind=kind,
                reason_code=reason,
                created_count=plan.created_count,
                merged_count=plan.merged_count,
                superseded_count=plan.superseded_count,
                competing_count=plan.competing_count,
                rejected_count=plan.rejected_count,
                position_ids=tuple(dict.fromkeys(item.position_id for item in plan.positions)),
                decided_at=now,
                trace_id=trace_id,
                formation_method=response.formation_method,
                provider=response.provider,
                model=response.model,
            )
            recorded = unit_of_work.positions.record_decision(
                decision, plan, audit_event_id=self.id_generator.new()
            )
            if recorded:
                unit_of_work.commit()
            else:
                replay = unit_of_work.positions.get_decision(key)
                if replay is None:
                    raise RuntimeError("position replay decision disappeared")
                decision = replay
        self.logger.info(
            "position_formation_decided",
            extra=_log_fields(
                source_interaction_id=source_interaction_id,
                decision_id=decision.decision_id,
                decision_kind=decision.kind.value,
                reason_code=decision.reason_code,
                position_ids=list(decision.position_ids),
                provider=decision.provider,
                model=decision.model,
                latency_ms=round((self.monotonic() - started) * 1000, 3),
            ),
        )
        return decision


@dataclass(frozen=True, slots=True)
class PositionBackfillReport:
    considered: int
    applied: int
    skipped: int
    rejected: int
    failed: int


@dataclass(frozen=True, slots=True)
class BackfillSatoriPositions:
    unit_of_work_factory: PositionsUnitOfWorkFactory
    form_positions: FormSatoriPositions

    async def execute(self, *, trace_id: str, limit: int) -> PositionBackfillReport:
        if limit < 1:
            raise ValueError("position backfill limit must be positive")
        with self.unit_of_work_factory() as unit_of_work:
            interaction_ids = unit_of_work.positions.list_unprocessed_interaction_ids(limit=limit)
        applied = skipped = rejected = failed = 0
        for interaction_id in interaction_ids:
            try:
                decision = await self.form_positions.execute(interaction_id, trace_id=trace_id)
            except Exception:
                failed += 1
                continue
            if decision.kind is PositionDecisionKind.APPLIED:
                applied += 1
            elif decision.kind is PositionDecisionKind.SKIPPED:
                skipped += 1
            else:
                rejected += 1
        return PositionBackfillReport(len(interaction_ids), applied, skipped, rejected, failed)


@dataclass(frozen=True, slots=True)
class GetSatoriPositions:
    unit_of_work_factory: PositionsUnitOfWorkFactory
    top_k: int = 4
    max_context_chars: int = 1600
    inclination_top_k: int = 3
    max_inclination_context_chars: int = 720

    def list(self, *, identity_id: str, current_only: bool = True) -> tuple[SatoriPosition, ...]:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.positions.list_positions(
                identity_id=identity_id, current_only=current_only
            )

    def inspect(
        self, position_id: str, *, identity_id: str
    ) -> tuple[SatoriPosition, tuple[PositionRevision, ...]] | None:
        with self.unit_of_work_factory() as unit_of_work:
            position = unit_of_work.positions.get_position(position_id)
            if position is None or position.identity_id != identity_id:
                return None
            return position, unit_of_work.positions.list_revisions(position_id)

    def list_inclinations(self, *, identity_id: str) -> tuple[SatoriInclination, ...]:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.positions.list_inclinations(identity_id=identity_id)

    def inspect_inclination(
        self, inclination_id: str, *, identity_id: str
    ) -> SatoriInclination | None:
        with self.unit_of_work_factory() as unit_of_work:
            inclination = unit_of_work.positions.get_inclination(inclination_id)
        if inclination is None or inclination.identity_id != identity_id:
            return None
        return inclination

    def export_json(self, *, identity_id: str) -> str:
        positions = self.list(identity_id=identity_id, current_only=False)
        payload = {
            "schema_version": 1,
            "identity_id": identity_id,
            "position_policy_version": POSITION_POLICY_VERSION,
            "position_formation_version": POSITION_FORMATION_VERSION,
            "positions": [
                {
                    "position_id": item.position_id,
                    "position_key": item.position_key,
                    "aggregate_version": item.aggregate_version,
                    "kind": item.kind.value,
                    "stance": item.stance.value,
                    "proposition": item.proposition,
                    "normalized_proposition": item.normalized_proposition,
                    "confidence": item.confidence,
                    "status": item.status.value,
                    "value_key": item.value_key,
                    "competing_with_position_id": item.competing_with_position_id,
                    "superseded_by_position_id": item.superseded_by_position_id,
                    "created_at": item.created_at.isoformat(),
                    "updated_at": item.updated_at.isoformat(),
                    "evidence": [
                        {
                            "evidence_id": edge.evidence_id,
                            "source_message_id": edge.source_message_id,
                            "source_interaction_id": edge.source_interaction_id,
                            "source_counterparty_id": edge.source_counterparty_id,
                            "quote": edge.quote,
                            "normalized_signature": edge.normalized_signature,
                            "role": edge.role.value,
                            "observed_at": edge.observed_at.isoformat(),
                        }
                        for edge in item.evidence
                    ],
                }
                for item in positions
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def export_inclinations_json(self, *, identity_id: str, as_of: datetime) -> str:
        as_of = aware_utc(as_of, "inclination export as_of")
        inclinations = self.list_inclinations(identity_id=identity_id)
        payload = {
            "schema_version": 1,
            "identity_id": identity_id,
            "inclination_policy_version": INCLINATION_POLICY_VERSION,
            "materialized_at": as_of.isoformat(),
            "inclinations": [
                {
                    "inclination_id": item.inclination_id,
                    "inclination_key": item.inclination_key,
                    "aggregate_version": item.aggregate_version,
                    "policy_version": item.policy_version,
                    "normalization_version": item.normalization_version,
                    "kind": item.kind.value,
                    "topic": item.topic,
                    "normalized_topic": item.normalized_topic,
                    "alternative_topic": item.alternative_topic,
                    "normalized_alternative_topic": item.normalized_alternative_topic,
                    "score_at_state_as_of": item.score,
                    "state_as_of": item.state_as_of.isoformat(),
                    "effective_score": round(
                        project_inclination_score(
                            score=item.score,
                            stability=item.stability,
                            kind=item.kind,
                            state_as_of=item.state_as_of,
                            at=as_of,
                        ),
                        6,
                    ),
                    "confidence": item.confidence,
                    "stability": item.stability,
                    "last_accepted_at": item.last_accepted_at.isoformat(),
                    "created_at": item.created_at.isoformat(),
                    "updated_at": item.updated_at.isoformat(),
                    "evidence": [
                        {
                            "evidence_id": edge.evidence_id,
                            "reflection_source_id": edge.reflection_source_id,
                            "affective_transition_id": edge.affective_transition_id,
                            "affective_state_version": edge.affective_state_version,
                            "affective_signal_hash": edge.affective_signal_hash,
                            "source_message_id": edge.source_message_id,
                            "source_interaction_id": edge.source_interaction_id,
                            "source_session_id": edge.source_session_id,
                            "source_counterparty_id": edge.source_counterparty_id,
                            "content_hash": edge.content_hash,
                            "content_signature": edge.content_signature,
                            "role": edge.role.value,
                            "signal": edge.signal,
                            "observed_at": edge.observed_at.isoformat(),
                            "accepted_at": edge.accepted_at.isoformat(),
                        }
                        for edge in item.evidence
                    ],
                    "revisions": [
                        {
                            "revision_id": revision.revision_id,
                            "inclination_version": revision.inclination_version,
                            "reflection_outcome_id": revision.reflection_outcome_id,
                            "kind": revision.kind.value,
                            "prior_score": revision.prior_score,
                            "new_score": revision.new_score,
                            "applied_delta": revision.applied_delta,
                            "prior_confidence": revision.prior_confidence,
                            "new_confidence": revision.new_confidence,
                            "prior_stability": revision.prior_stability,
                            "new_stability": revision.new_stability,
                            "state_as_of": revision.state_as_of.isoformat(),
                            "reason_code": revision.reason_code,
                            "occurred_at": revision.occurred_at.isoformat(),
                        }
                        for revision in item.revisions
                    ],
                }
                for item in inclinations
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def project_context(self, *, identity_id: str, user_text: str) -> SatoriPositionsContext:
        if self.top_k < 1 or self.max_context_chars < 256:
            raise ValueError("position context limits are invalid")
        positions = self.list(identity_id=identity_id, current_only=True)
        query_tokens = _position_tokens(user_text)
        scored = tuple(
            sorted(
                (
                    (len(query_tokens & _position_tokens(item.proposition)), item)
                    for item in positions
                ),
                key=lambda pair: (
                    -pair[0],
                    -pair[1].confidence,
                    pair[1].position_id,
                ),
            )
        )
        by_id = {item.position_id: item for item in positions}
        selected: list[SatoriPosition] = []
        selected_ids: set[str] = set()
        for overlap, candidate in scored:
            if overlap < 1 or candidate.position_id in selected_ids:
                continue
            group = [candidate]
            if (
                candidate.status is PositionStatus.COMPETING
                and candidate.competing_with_position_id is not None
            ):
                peer = by_id.get(candidate.competing_with_position_id)
                if peer is not None and peer.position_id not in selected_ids:
                    group.append(peer)
            if len(selected) + len(group) > self.top_k:
                continue
            tentative = (*selected, *group)
            context = _position_context(tuple(tentative))
            if len(positions_context_json(context)) > self.max_context_chars:
                continue
            selected.extend(group)
            selected_ids.update(item.position_id for item in group)
        return _position_context(tuple(selected))

    def project_inclination_context(
        self, *, identity_id: str, user_text: str, as_of: datetime
    ) -> SatoriInclinationsContext:
        if self.inclination_top_k < 1 or self.max_inclination_context_chars < 256:
            raise ValueError("inclination context limits are invalid")
        as_of = aware_utc(as_of, "inclination context as_of")
        with self.unit_of_work_factory() as unit_of_work:
            references = unit_of_work.positions.list_inclination_references(identity_id=identity_id)
        explicit_query = bool(_EXPLICIT_INCLINATION_QUERY.search(user_text))
        normalized_user = _inclination_lexical(user_text)
        eligible: list[tuple[float, InclinationStateReference]] = []
        for item in references:
            effective = project_inclination_score(
                score=item.score,
                stability=item.stability,
                kind=item.kind,
                state_as_of=item.state_as_of,
                at=as_of,
            )
            if item.confidence < 0.55 or abs(effective) < 0.05:
                continue
            if not explicit_query and not (
                _inclination_phrase_present(normalized_user, item.topic)
                or (
                    item.alternative_topic is not None
                    and _inclination_phrase_present(normalized_user, item.alternative_topic)
                )
            ):
                continue
            eligible.append((effective, item))
        eligible.sort(
            key=lambda pair: (
                -abs(pair[0]),
                -pair[1].confidence,
                pair[1].inclination_id,
            )
        )
        selected: list[InclinationContextItem] = []
        for effective, item in eligible:
            preferred = None
            if item.kind is InclinationKind.PREFERENCE:
                preferred = item.topic if effective > 0 else item.alternative_topic
            candidate = InclinationContextItem(
                inclination_id=item.inclination_id,
                kind=item.kind.value,
                topic=item.topic,
                alternative_topic=item.alternative_topic,
                effective_score=round(effective, 6),
                confidence=item.confidence,
                stability=item.stability,
                preferred_topic=preferred,
            )
            tentative = _inclination_context((*selected, candidate))
            if len(inclinations_context_json(tentative)) > self.max_inclination_context_chars:
                continue
            selected.append(candidate)
            if len(selected) >= self.inclination_top_k:
                break
        return _inclination_context(tuple(selected))


def position_is_current(position: SatoriPosition) -> bool:
    return position.status in {PositionStatus.ACTIVE, PositionStatus.COMPETING}


_POSITION_TOKEN = re.compile(r"[a-zа-яё0-9]{3,}", re.IGNORECASE)
_POSITION_STOPWORDS = frozenset(
    {
        "что",
        "это",
        "как",
        "для",
        "про",
        "или",
        "the",
        "and",
        "about",
        "with",
    }
)


def _position_tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _POSITION_TOKEN.findall(value.casefold())
        if token not in _POSITION_STOPWORDS
    )


def _position_context(positions: tuple[SatoriPosition, ...]) -> SatoriPositionsContext:
    return SatoriPositionsContext(
        schema_version=POSITIONS_CONTEXT_SCHEMA_VERSION,
        status="available" if positions else "empty",
        positions=tuple(
            PositionContextItem(
                position_id=item.position_id,
                kind=item.kind.value,
                stance=item.stance.value,
                proposition=item.proposition,
                confidence=item.confidence,
                status=item.status.value,
                uncertain=(
                    item.kind.value == "hypothesis"
                    or item.stance.value == "uncertain"
                    or item.status is PositionStatus.COMPETING
                ),
                competing_with_position_id=item.competing_with_position_id,
            )
            for item in positions
        ),
    )


_EXPLICIT_INCLINATION_QUERY = re.compile(
    r"(?:какие\s+у\s+тебя\s+(?:интересы|предпочтения)|"
    r"что\s+тебе\s+(?:нравится|интересно)|чем\s+ты\s+интересуешься|"
    r"чем\s+(?:ты\s+)?увлекаешься|"
    r"что\s+ты\s+предпочитаешь|"
    r"your\s+(?:interests|preferences)|what\s+do\s+you\s+(?:like|prefer))",
    re.IGNORECASE,
)


def _inclination_lexical(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _inclination_phrase_present(normalized_text: str, topic: str) -> bool:
    normalized_topic = _inclination_lexical(topic)
    return bool(normalized_topic) and f" {normalized_topic} " in f" {normalized_text} "


def _inclination_context(
    inclinations: tuple[InclinationContextItem, ...],
) -> SatoriInclinationsContext:
    curiosity = min(
        0.20,
        max(
            (
                item.effective_score
                for item in inclinations
                if item.kind == InclinationKind.INTEREST.value
            ),
            default=0.0,
        ),
    )
    return SatoriInclinationsContext(
        schema_version=INCLINATION_CONTEXT_SCHEMA_VERSION,
        status="available" if inclinations else "empty",
        inclinations=inclinations,
        curiosity_influence=round(curiosity, 6),
    )
