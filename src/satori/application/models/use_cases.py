"""Incremental Stage 9 formation, backfill and immutable reads."""

# ruff: noqa: RUF001  # Russian relevance cues intentionally use Cyrillic.

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from satori.application.models.contracts import (
    CURRENT_MODELS_CONTEXT_SCHEMA_VERSION,
    CurrentModelContextClaim,
    CurrentModelsContext,
)
from satori.application.models.ports import CurrentModelsUnitOfWork
from satori.core.clock import Clock
from satori.core.ids import IdGenerator
from satori.core.models import (
    ModelFormationProviderResponse,
    ModelFormationRequest,
)
from satori.core.ports.providers import StructuredGenerationPort
from satori.domain.models import (
    MODEL_FORMATION_VERSION,
    MODEL_POLICY_VERSION,
    ModelClaimRevision,
    ModelDecisionKind,
    ModelFormationDecision,
    OwnerFormationPlan,
    UserModelClaim,
    UserModelManager,
    WorldModelClaim,
    WorldModelManager,
    model_claim_is_current,
    model_idempotency_key,
    normalize_model_text,
)

MODEL_REQUEST_SCHEMA_VERSION = 1
ModelsUnitOfWorkFactory = Callable[[], CurrentModelsUnitOfWork]
ModelFormationProvider = StructuredGenerationPort[
    ModelFormationRequest, ModelFormationProviderResponse
]


def _log_fields(**fields: object) -> dict[str, object]:
    return {"satori_fields": fields}


def _merge_plans[TClaim: (UserModelClaim, WorldModelClaim)](
    first: OwnerFormationPlan[TClaim], second: OwnerFormationPlan[TClaim]
) -> OwnerFormationPlan[TClaim]:
    changed = {claim.claim_id: claim for claim in first.claims}
    changed.update({claim.claim_id: claim for claim in second.claims})
    return OwnerFormationPlan(
        claims=tuple(changed.values()),
        revisions=(*first.revisions, *second.revisions),
        created_count=first.created_count + second.created_count,
        merged_count=first.merged_count + second.merged_count,
        superseded_count=first.superseded_count + second.superseded_count,
        disputed_count=first.disputed_count + second.disputed_count,
        rejected_count=first.rejected_count + second.rejected_count,
    )


def _apply_changed[TClaim: (UserModelClaim, WorldModelClaim)](
    existing: tuple[TClaim, ...], plan: OwnerFormationPlan[TClaim]
) -> tuple[TClaim, ...]:
    changed = {claim.claim_id: claim for claim in plan.claims}
    return tuple(changed.get(claim.claim_id, claim) for claim in existing)


@dataclass(slots=True)
class FormCurrentModels:
    """Call one provider, then let both owners decide inside one transaction."""

    unit_of_work_factory: ModelsUnitOfWorkFactory
    provider: ModelFormationProvider
    user_manager: UserModelManager
    world_manager: WorldModelManager
    clock: Clock
    id_generator: IdGenerator
    max_source_messages: int = 8
    max_user_claims: int = 4
    max_world_claims: int = 4
    monotonic: Callable[[], float] = time.perf_counter
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.models"))

    async def execute(self, source_interaction_id: str, *, trace_id: str) -> ModelFormationDecision:
        key = model_idempotency_key(source_interaction_id, MODEL_FORMATION_VERSION)
        with self.unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.current_models.get_decision(key)
            messages = unit_of_work.current_models.get_source_messages(
                source_interaction_id, limit=self.max_source_messages
            )
        if existing is not None:
            return existing
        if not messages:
            raise ValueError("model source completed interaction does not exist")
        current = next(item for item in messages if item.interaction_id == source_interaction_id)
        request = ModelFormationRequest(
            schema_version=MODEL_REQUEST_SCHEMA_VERSION,
            trace_id=trace_id,
            source_interaction_id=source_interaction_id,
            source_message_id=current.message_id,
            identity_id=current.identity_id,
            counterparty_id=current.counterparty_id,
            formation_version=MODEL_FORMATION_VERSION,
            max_user_claims=self.max_user_claims,
            max_world_claims=self.max_world_claims,
            messages=messages,
        )
        self.logger.info(
            "model_formation_started",
            extra=_log_fields(
                source_interaction_id=source_interaction_id,
                formation_version=MODEL_FORMATION_VERSION,
                source_message_count=len(messages),
            ),
        )
        started = self.monotonic()
        try:
            response = await self.provider.generate_structured(request)
            decision_id = self.id_generator.new()
            now = self.clock.now()
            with self.unit_of_work_factory() as unit_of_work:
                user_existing = unit_of_work.current_models.list_user_claims(
                    identity_id=current.identity_id, counterparty_id=current.counterparty_id
                )
                world_existing = unit_of_work.current_models.list_world_claims(
                    identity_id=current.identity_id, counterparty_id=current.counterparty_id
                )
                user_expiry = self.user_manager.expire_due(
                    user_existing, now=now, decision_id=decision_id, new_id=self.id_generator.new
                )
                world_expiry = self.world_manager.expire_due(
                    world_existing, now=now, decision_id=decision_id, new_id=self.id_generator.new
                )
                user_plan = _merge_plans(
                    user_expiry,
                    self.user_manager.evaluate(
                        response.proposal.user_claims,
                        identity_id=current.identity_id,
                        counterparty_id=current.counterparty_id,
                        current_message_id=current.message_id,
                        sources=messages,
                        existing_claims=_apply_changed(user_existing, user_expiry),
                        max_claims=self.max_user_claims,
                        now=now,
                        decision_id=decision_id,
                        new_id=self.id_generator.new,
                    ),
                )
                world_plan = _merge_plans(
                    world_expiry,
                    self.world_manager.evaluate(
                        response.proposal.world_claims,
                        identity_id=current.identity_id,
                        counterparty_id=current.counterparty_id,
                        current_message_id=current.message_id,
                        sources=messages,
                        existing_claims=_apply_changed(world_existing, world_expiry),
                        max_claims=self.max_world_claims,
                        now=now,
                        decision_id=decision_id,
                        new_id=self.id_generator.new,
                    ),
                )
                changed_count = len(user_plan.revisions) + len(world_plan.revisions)
                rejected_count = user_plan.rejected_count + world_plan.rejected_count
                kind = (
                    ModelDecisionKind.APPLIED
                    if changed_count
                    else ModelDecisionKind.REJECTED
                    if rejected_count
                    else ModelDecisionKind.SKIPPED
                )
                reason = (
                    "owner_changes_applied"
                    if kind is ModelDecisionKind.APPLIED
                    else "proposal_rejected"
                    if kind is ModelDecisionKind.REJECTED
                    else "no_supported_current_claims"
                )
                decision = ModelFormationDecision(
                    decision_id=decision_id,
                    idempotency_key=key,
                    source_interaction_id=source_interaction_id,
                    source_message_id=current.message_id,
                    identity_id=current.identity_id,
                    counterparty_id=current.counterparty_id,
                    formation_version=MODEL_FORMATION_VERSION,
                    policy_version=MODEL_POLICY_VERSION,
                    kind=kind,
                    reason_code=reason,
                    user_created_count=user_plan.created_count,
                    user_merged_count=user_plan.merged_count,
                    user_superseded_count=user_plan.superseded_count,
                    user_disputed_count=user_plan.disputed_count,
                    user_rejected_count=user_plan.rejected_count,
                    world_created_count=world_plan.created_count,
                    world_merged_count=world_plan.merged_count,
                    world_superseded_count=world_plan.superseded_count,
                    world_disputed_count=world_plan.disputed_count,
                    world_rejected_count=world_plan.rejected_count,
                    user_claim_ids=tuple(claim.claim_id for claim in user_plan.claims),
                    world_claim_ids=tuple(claim.claim_id for claim in world_plan.claims),
                    decided_at=now,
                    trace_id=trace_id,
                    formation_method=response.formation_method,
                    provider=response.provider,
                    model=response.model,
                )
                recorded = unit_of_work.current_models.record_decision(
                    decision,
                    user_plan,
                    world_plan,
                    user_audit_event_id=self.id_generator.new(),
                    world_audit_event_id=self.id_generator.new(),
                )
                if recorded:
                    unit_of_work.commit()
                else:
                    prior = unit_of_work.current_models.get_decision(key)
                    if prior is None:
                        raise RuntimeError("model replay decision disappeared")
                    decision = prior
        except Exception as error:
            self.logger.warning(
                "model_formation_failed",
                extra=_log_fields(
                    source_interaction_id=source_interaction_id,
                    error_type=type(error).__name__,
                    latency_ms=round((self.monotonic() - started) * 1000, 3),
                ),
            )
            raise
        self.logger.info(
            "model_formation_decided",
            extra=_log_fields(
                source_interaction_id=source_interaction_id,
                decision_id=decision.decision_id,
                decision_kind=decision.kind.value,
                reason_code=decision.reason_code,
                user_claim_ids=list(decision.user_claim_ids),
                world_claim_ids=list(decision.world_claim_ids),
                provider=decision.provider,
                model=decision.model,
                latency_ms=round((self.monotonic() - started) * 1000, 3),
            ),
        )
        return decision


@dataclass(frozen=True, slots=True)
class ModelBackfillReport:
    considered: int
    applied: int
    skipped: int
    rejected: int
    failed: int


@dataclass(frozen=True, slots=True)
class BackfillCurrentModels:
    unit_of_work_factory: ModelsUnitOfWorkFactory
    form_models: FormCurrentModels

    async def execute(self, *, trace_id: str, limit: int) -> ModelBackfillReport:
        if limit < 1:
            raise ValueError("model backfill limit must be positive")
        with self.unit_of_work_factory() as unit_of_work:
            interaction_ids = unit_of_work.current_models.list_unprocessed_interaction_ids(
                limit=limit
            )
        applied = skipped = rejected = failed = 0
        for interaction_id in interaction_ids:
            try:
                decision = await self.form_models.execute(interaction_id, trace_id=trace_id)
            except Exception:
                failed += 1
                continue
            if decision.kind is ModelDecisionKind.APPLIED:
                applied += 1
            elif decision.kind is ModelDecisionKind.SKIPPED:
                skipped += 1
            else:
                rejected += 1
        return ModelBackfillReport(len(interaction_ids), applied, skipped, rejected, failed)


@dataclass(frozen=True, slots=True)
class GetCurrentModels:
    unit_of_work_factory: ModelsUnitOfWorkFactory

    def list_user(
        self, *, identity_id: str, counterparty_id: str, current_only: bool = True
    ) -> tuple[UserModelClaim, ...]:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.current_models.list_user_claims(
                identity_id=identity_id,
                counterparty_id=counterparty_id,
                current_only=current_only,
            )

    def list_world(
        self, *, identity_id: str, counterparty_id: str, current_only: bool = True
    ) -> tuple[WorldModelClaim, ...]:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.current_models.list_world_claims(
                identity_id=identity_id,
                counterparty_id=counterparty_id,
                current_only=current_only,
            )

    def inspect_user(
        self, claim_id: str, *, identity_id: str, counterparty_id: str
    ) -> tuple[UserModelClaim, tuple[ModelClaimRevision, ...]] | None:
        with self.unit_of_work_factory() as unit_of_work:
            claim = unit_of_work.current_models.get_user_claim(claim_id)
            if (
                claim is None
                or claim.identity_id != identity_id
                or claim.counterparty_id != counterparty_id
            ):
                return None
            return claim, unit_of_work.current_models.list_user_revisions(claim_id)

    def inspect_world(
        self, claim_id: str, *, identity_id: str, counterparty_id: str
    ) -> tuple[WorldModelClaim, tuple[ModelClaimRevision, ...]] | None:
        with self.unit_of_work_factory() as unit_of_work:
            claim = unit_of_work.current_models.get_world_claim(claim_id)
            if (
                claim is None
                or claim.identity_id != identity_id
                or claim.counterparty_id != counterparty_id
            ):
                return None
            return claim, unit_of_work.current_models.list_world_revisions(claim_id)

    def export_json(self, *, identity_id: str, counterparty_id: str, as_of: datetime) -> str:
        """Export one opaque counterparty partition with lineage and no raw messages."""

        user_claims = self.list_user(
            identity_id=identity_id,
            counterparty_id=counterparty_id,
            current_only=False,
        )
        world_claims = self.list_world(
            identity_id=identity_id,
            counterparty_id=counterparty_id,
            current_only=False,
        )

        def revision_payload(revision: ModelClaimRevision) -> dict[str, object]:
            return {
                "revision_id": revision.revision_id,
                "claim_version": revision.claim_version,
                "decision_id": revision.decision_id,
                "kind": revision.kind.value,
                "prior_status": (
                    revision.prior_status.value if revision.prior_status is not None else None
                ),
                "new_status": revision.new_status.value,
                "prior_confidence": revision.prior_confidence,
                "new_confidence": revision.new_confidence,
                "prior_expires_at": (
                    revision.prior_expires_at.isoformat()
                    if revision.prior_expires_at is not None
                    else None
                ),
                "new_expires_at": (
                    revision.new_expires_at.isoformat()
                    if revision.new_expires_at is not None
                    else None
                ),
                "reason_code": revision.reason_code,
                "occurred_at": revision.occurred_at.isoformat(),
            }

        def base_payload(
            claim: UserModelClaim | WorldModelClaim,
            revisions: tuple[ModelClaimRevision, ...],
        ) -> dict[str, object]:
            return {
                "claim_id": claim.claim_id,
                "claim_key": claim.claim_key,
                "schema_version": claim.schema_version,
                "aggregate_version": claim.aggregate_version,
                "policy_version": claim.policy_version,
                "formation_version": claim.formation_version,
                "normalization_version": claim.normalization_version,
                "predicate": claim.predicate,
                "value_kind": claim.value_kind.value,
                "value": claim.value,
                "normalized_value": claim.normalized_value,
                "epistemic_kind": claim.epistemic_kind.value,
                "confidence": claim.confidence,
                "status": claim.status.value,
                "current_as_of_export": model_claim_is_current(claim, as_of=as_of),
                "valid_from": claim.valid_from.isoformat(),
                "valid_until": (
                    claim.valid_until.isoformat() if claim.valid_until is not None else None
                ),
                "last_observed_at": claim.last_observed_at.isoformat(),
                "expires_at": claim.expires_at.isoformat()
                if claim.expires_at is not None
                else None,
                "superseded_by_claim_id": claim.superseded_by_claim_id,
                "created_at": claim.created_at.isoformat(),
                "updated_at": claim.updated_at.isoformat(),
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "source_message_id": item.source_message_id,
                        "source_interaction_id": item.source_interaction_id,
                        "observed_at": item.observed_at.isoformat(),
                    }
                    for item in claim.evidence
                ],
                "revisions": [revision_payload(item) for item in revisions],
            }

        user_payload = []
        for user_claim in user_claims:
            inspected = self.inspect_user(
                user_claim.claim_id,
                identity_id=identity_id,
                counterparty_id=counterparty_id,
            )
            if inspected is None:
                raise RuntimeError("user model claim disappeared during export")
            user_payload.append(base_payload(user_claim, inspected[1]))
        world_payload = []
        for world_claim in world_claims:
            world_inspection = self.inspect_world(
                world_claim.claim_id,
                identity_id=identity_id,
                counterparty_id=counterparty_id,
            )
            if world_inspection is None:
                raise RuntimeError("world model claim disappeared during export")
            payload = base_payload(world_claim, world_inspection[1])
            payload.update(
                {
                    "subject_kind": world_claim.subject_kind,
                    "subject_label": world_claim.subject_label,
                    "normalized_subject_label": world_claim.normalized_subject_label,
                }
            )
            world_payload.append(payload)
        return json.dumps(
            {
                "schema_version": 1,
                "identity_id": identity_id,
                "counterparty_id": counterparty_id,
                "as_of": as_of.isoformat(),
                "user_claims": user_payload,
                "world_claims": world_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def project_context(
        self,
        *,
        identity_id: str,
        counterparty_id: str,
        user_text: str,
        as_of: datetime,
        max_user_claims: int = 4,
        max_world_claims: int = 4,
    ) -> CurrentModelsContext:
        """Select current relevant claims without mutating expiry state."""

        if max_user_claims < 1 or max_world_claims < 1:
            raise ValueError("current models context limits must be positive")
        with self.unit_of_work_factory() as unit_of_work:
            user_claims = unit_of_work.current_models.list_user_claims(
                identity_id=identity_id,
                counterparty_id=counterparty_id,
                current_only=True,
            )
            world_claims = unit_of_work.current_models.list_world_claims(
                identity_id=identity_id,
                counterparty_id=counterparty_id,
                current_only=True,
            )
        current_user = tuple(
            item for item in user_claims if model_claim_is_current(item, as_of=as_of)
        )
        current_world = tuple(
            item for item in world_claims if model_claim_is_current(item, as_of=as_of)
        )
        selected_user = self._select_user(current_user, user_text, limit=max_user_claims)
        selected_world = self._select_world(current_world, user_text, limit=max_world_claims)
        projected_user = tuple(
            CurrentModelContextClaim(
                claim_id=item.claim_id,
                owner="user",
                epistemic_kind=item.epistemic_kind,
                predicate=item.predicate,
                value_kind=item.value_kind,
                value=item.value,
                confidence=item.confidence,
                valid_from=item.valid_from,
            )
            for item in selected_user
        )
        projected_world = tuple(
            CurrentModelContextClaim(
                claim_id=item.claim_id,
                owner="world",
                epistemic_kind=item.epistemic_kind,
                predicate=item.predicate,
                value_kind=item.value_kind,
                value=item.value,
                confidence=item.confidence,
                valid_from=item.valid_from,
                subject_kind=item.subject_kind,
                subject_label=item.subject_label,
            )
            for item in selected_world
        )
        selected_count = len(projected_user) + len(projected_world)
        total_count = len(user_claims) + len(world_claims)
        return CurrentModelsContext(
            schema_version=CURRENT_MODELS_CONTEXT_SCHEMA_VERSION,
            status="available" if selected_count else "empty",
            as_of=as_of,
            user_claims=projected_user,
            world_claims=projected_world,
            excluded_claim_count=total_count - selected_count,
        )

    @classmethod
    def _select_user(
        cls, claims: tuple[UserModelClaim, ...], user_text: str, *, limit: int
    ) -> tuple[UserModelClaim, ...]:
        normalized = normalize_model_text(user_text)
        broad = any(cue in normalized for cue in ("обо мне", "про меня", "что ты знаешь"))
        cues = {
            "display_name": ("имя", "зовут"),
            "occupation": ("работ", "професс", "занимаюсь"),
            "residence_city": ("живу", "город", "место"),
            "goal": ("цель", "хочу", "добиться"),
            "project": ("проект", "делаю", "работаю над"),
            "important_person": ("важный человек", "близкий", "семья"),
        }
        ranked = (
            (cls._relevance(item, normalized, cues.get(item.predicate, ()), broad), item)
            for item in claims
        )
        relevant = tuple(
            item
            for score, item in sorted(
                ranked,
                key=lambda pair: (
                    pair[0],
                    pair[1].confidence,
                    pair[1].last_observed_at,
                    pair[1].claim_id,
                ),
                reverse=True,
            )
            if score > 0
        )
        return relevant[:limit]

    @classmethod
    def _select_world(
        cls, claims: tuple[WorldModelClaim, ...], user_text: str, *, limit: int
    ) -> tuple[WorldModelClaim, ...]:
        normalized = normalize_model_text(user_text)
        broad = any(
            cue in normalized
            for cue in ("что происходит", "текущие дела", "текущая ситуация", "что сейчас")
        )
        cues = {
            "project": ("проект", "работ", "дело"),
            "situation": ("ситуац", "происход"),
            "commitment": ("обещ", "обяз", "договор"),
            "outcome": ("результат", "исход", "случил"),
        }
        ranked = (
            (
                cls._relevance(
                    item,
                    normalized,
                    cues.get(item.subject_kind, ()),
                    broad,
                    subject=item.subject_label,
                ),
                item,
            )
            for item in claims
        )
        relevant = tuple(
            item
            for score, item in sorted(
                ranked,
                key=lambda pair: (
                    pair[0],
                    pair[1].confidence,
                    pair[1].last_observed_at,
                    pair[1].claim_id,
                ),
                reverse=True,
            )
            if score > 0
        )
        return relevant[:limit]

    @staticmethod
    def _relevance(
        claim: UserModelClaim | WorldModelClaim,
        normalized_query: str,
        cues: tuple[str, ...],
        broad: bool,
        *,
        subject: str | None = None,
    ) -> int:
        query_tokens = set(re.findall(r"[^\W_]{3,}", normalized_query, flags=re.UNICODE))
        material = " ".join(
            part
            for part in (
                claim.predicate.replace("_", " "),
                str(claim.value),
                subject,
            )
            if part is not None
        )
        claim_tokens = set(
            re.findall(r"[^\W_]{3,}", normalize_model_text(material), flags=re.UNICODE)
        )
        return (20 if query_tokens & claim_tokens else 0) + (
            5 if broad or any(cue in normalized_query for cue in cues) else 0
        )
