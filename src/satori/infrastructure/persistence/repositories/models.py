"""SQLAlchemy adapter for Stage 9 user/world model owners."""

from collections.abc import Sequence

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.core.models import ModelEpistemicKind, ModelScalar, ModelSourceMessage, ModelValueKind
from satori.domain.models import (
    MODEL_FORMATION_VERSION,
    ModelClaimEvidence,
    ModelClaimRevision,
    ModelClaimStatus,
    ModelDecisionKind,
    ModelFormationDecision,
    ModelOwner,
    ModelRevisionKind,
    OwnerFormationPlan,
    UserModelClaim,
    WorldModelClaim,
)
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.initial_self import AuditEventRow
from satori.infrastructure.persistence.models.models import (
    ModelFormationDecisionRow,
    UserModelClaimEvidenceRow,
    UserModelClaimRevisionRow,
    UserModelClaimRow,
    WorldModelClaimEvidenceRow,
    WorldModelClaimRevisionRow,
    WorldModelClaimRow,
)


class SQLAlchemyCurrentModelsRepository:
    """Persist two owner plans while preserving one source transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_decision(self, idempotency_key: str) -> ModelFormationDecision | None:
        row = self._session.execute(
            select(ModelFormationDecisionRow).where(
                ModelFormationDecisionRow.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        return self._map_decision(row) if row is not None else None

    def get_source_messages(
        self, source_interaction_id: str, *, limit: int
    ) -> tuple[ModelSourceMessage, ...]:
        if limit < 1:
            raise ValueError("model source-message limit must be positive")
        interaction = self._session.get(ConversationInteractionRow, source_interaction_id)
        if (
            interaction is None
            or interaction.status != "completed"
            or not interaction.model_processing_required
        ):
            return ()
        session = self._session.get(ConversationSessionRow, interaction.session_id)
        if session is None:
            raise RuntimeError("model source session is missing")
        rows = tuple(
            self._session.execute(
                select(ConversationMessageRow, ConversationInteractionRow)
                .join(
                    ConversationInteractionRow,
                    ConversationInteractionRow.interaction_id
                    == ConversationMessageRow.interaction_id,
                )
                .join(
                    ConversationSessionRow,
                    ConversationSessionRow.session_id == ConversationInteractionRow.session_id,
                )
                .where(
                    ConversationMessageRow.role == "user",
                    ConversationInteractionRow.status == "completed",
                    ConversationInteractionRow.model_processing_required.is_(True),
                    ConversationInteractionRow.started_at <= interaction.started_at,
                    ConversationSessionRow.identity_id == session.identity_id,
                    ConversationSessionRow.counterparty_id == session.counterparty_id,
                )
                .order_by(
                    ConversationInteractionRow.started_at.desc(),
                    ConversationInteractionRow.interaction_id.desc(),
                )
                .limit(limit)
            ).all()
        )
        mapped = tuple(
            ModelSourceMessage(
                message_id=message.message_id,
                interaction_id=row_interaction.interaction_id,
                identity_id=session.identity_id,
                counterparty_id=session.counterparty_id,
                observed_at=message.created_at,
                content=message.content,
            )
            for message, row_interaction in rows
        )
        source = next(
            (item for item in mapped if item.interaction_id == source_interaction_id), None
        )
        return (
            ()
            if source is None
            else (source, *(item for item in mapped if item.message_id != source.message_id))
        )

    def list_user_claims(
        self,
        *,
        identity_id: str,
        counterparty_id: str,
        current_only: bool = False,
    ) -> tuple[UserModelClaim, ...]:
        query = select(UserModelClaimRow).where(
            UserModelClaimRow.identity_id == identity_id,
            UserModelClaimRow.counterparty_id == counterparty_id,
        )
        if current_only:
            query = query.where(UserModelClaimRow.status == ModelClaimStatus.CURRENT.value)
        rows = tuple(
            self._session.execute(
                query.order_by(
                    UserModelClaimRow.predicate,
                    UserModelClaimRow.valid_from,
                    UserModelClaimRow.claim_id,
                )
            ).scalars()
        )
        return tuple(self._map_user_claim(row) for row in rows)

    def list_world_claims(
        self,
        *,
        identity_id: str,
        counterparty_id: str,
        current_only: bool = False,
    ) -> tuple[WorldModelClaim, ...]:
        query = select(WorldModelClaimRow).where(
            WorldModelClaimRow.identity_id == identity_id,
            WorldModelClaimRow.counterparty_id == counterparty_id,
        )
        if current_only:
            query = query.where(WorldModelClaimRow.status == ModelClaimStatus.CURRENT.value)
        rows = tuple(
            self._session.execute(
                query.order_by(
                    WorldModelClaimRow.subject_kind,
                    WorldModelClaimRow.normalized_subject_label,
                    WorldModelClaimRow.valid_from,
                    WorldModelClaimRow.claim_id,
                )
            ).scalars()
        )
        return tuple(self._map_world_claim(row) for row in rows)

    def get_user_claim(self, claim_id: str) -> UserModelClaim | None:
        row = self._session.get(UserModelClaimRow, claim_id)
        return self._map_user_claim(row) if row is not None else None

    def get_world_claim(self, claim_id: str) -> WorldModelClaim | None:
        row = self._session.get(WorldModelClaimRow, claim_id)
        return self._map_world_claim(row) if row is not None else None

    def list_user_revisions(self, claim_id: str) -> tuple[ModelClaimRevision, ...]:
        rows = tuple(
            self._session.execute(
                select(UserModelClaimRevisionRow)
                .where(UserModelClaimRevisionRow.claim_id == claim_id)
                .order_by(UserModelClaimRevisionRow.claim_version)
            ).scalars()
        )
        return tuple(self._map_revision(row, ModelOwner.USER) for row in rows)

    def list_world_revisions(self, claim_id: str) -> tuple[ModelClaimRevision, ...]:
        rows = tuple(
            self._session.execute(
                select(WorldModelClaimRevisionRow)
                .where(WorldModelClaimRevisionRow.claim_id == claim_id)
                .order_by(WorldModelClaimRevisionRow.claim_version)
            ).scalars()
        )
        return tuple(self._map_revision(row, ModelOwner.WORLD) for row in rows)

    def list_unprocessed_interaction_ids(self, *, limit: int) -> tuple[str, ...]:
        processed = exists().where(
            ModelFormationDecisionRow.source_interaction_id
            == ConversationInteractionRow.interaction_id,
            ModelFormationDecisionRow.formation_version == MODEL_FORMATION_VERSION,
        )
        return tuple(
            self._session.execute(
                select(ConversationInteractionRow.interaction_id)
                .where(
                    ConversationInteractionRow.status == "completed",
                    ConversationInteractionRow.model_processing_required.is_(True),
                    ~processed,
                )
                .order_by(
                    ConversationInteractionRow.started_at,
                    ConversationInteractionRow.interaction_id,
                )
                .limit(limit)
            ).scalars()
        )

    def record_decision(
        self,
        decision: ModelFormationDecision,
        user_plan: OwnerFormationPlan[UserModelClaim],
        world_plan: OwnerFormationPlan[WorldModelClaim],
        *,
        user_audit_event_id: str,
        world_audit_event_id: str,
    ) -> bool:
        statement = (
            sqlite_insert(ModelFormationDecisionRow)
            .values(**self._decision_values(decision))
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(ModelFormationDecisionRow.decision_id)
        )
        if self._session.execute(statement).scalar_one_or_none() is None:
            return False
        self._persist_user_plan(user_plan)
        self._persist_world_plan(world_plan)
        self._session.add_all(
            (
                self._audit_row(
                    user_audit_event_id,
                    decision,
                    owner=ModelOwner.USER,
                    claim_ids=decision.user_claim_ids,
                ),
                self._audit_row(
                    world_audit_event_id,
                    decision,
                    owner=ModelOwner.WORLD,
                    claim_ids=decision.world_claim_ids,
                ),
            )
        )
        return True

    def _persist_user_plan(self, plan: OwnerFormationPlan[UserModelClaim]) -> None:
        new, changed = self._partition_user_claims(plan.claims)
        self._session.add_all(self._user_row(claim) for claim in new)
        self._session.flush()
        for claim, expected_version in changed:
            self._update_user_claim(claim, expected_version)
        self._session.flush()
        for claim in plan.claims:
            known = set(
                self._session.execute(
                    select(UserModelClaimEvidenceRow.source_message_id).where(
                        UserModelClaimEvidenceRow.claim_id == claim.claim_id
                    )
                ).scalars()
            )
            self._session.add_all(
                self._user_evidence_row(item)
                for item in claim.evidence
                if item.source_message_id not in known
            )
        self._session.add_all(self._user_revision_row(item) for item in plan.revisions)

    def _persist_world_plan(self, plan: OwnerFormationPlan[WorldModelClaim]) -> None:
        new, changed = self._partition_world_claims(plan.claims)
        self._session.add_all(self._world_row(claim) for claim in new)
        self._session.flush()
        for claim, expected_version in changed:
            self._update_world_claim(claim, expected_version)
        self._session.flush()
        for claim in plan.claims:
            known = set(
                self._session.execute(
                    select(WorldModelClaimEvidenceRow.source_message_id).where(
                        WorldModelClaimEvidenceRow.claim_id == claim.claim_id
                    )
                ).scalars()
            )
            self._session.add_all(
                self._world_evidence_row(item)
                for item in claim.evidence
                if item.source_message_id not in known
            )
        self._session.add_all(self._world_revision_row(item) for item in plan.revisions)

    def _partition_user_claims(
        self, claims: Sequence[UserModelClaim]
    ) -> tuple[list[UserModelClaim], list[tuple[UserModelClaim, int]]]:
        new: list[UserModelClaim] = []
        changed: list[tuple[UserModelClaim, int]] = []
        for claim in claims:
            current = self._session.get(UserModelClaimRow, claim.claim_id)
            if current is None:
                new.append(claim)
            else:
                changed.append((claim, current.aggregate_version))
        return new, changed

    def _partition_world_claims(
        self, claims: Sequence[WorldModelClaim]
    ) -> tuple[list[WorldModelClaim], list[tuple[WorldModelClaim, int]]]:
        new: list[WorldModelClaim] = []
        changed: list[tuple[WorldModelClaim, int]] = []
        for claim in claims:
            current = self._session.get(WorldModelClaimRow, claim.claim_id)
            if current is None:
                new.append(claim)
            else:
                changed.append((claim, current.aggregate_version))
        return new, changed

    def _update_user_claim(self, claim: UserModelClaim, expected_version: int) -> None:
        self._check_version(claim.aggregate_version, expected_version)
        result = self._session.execute(
            update(UserModelClaimRow)
            .where(
                UserModelClaimRow.claim_id == claim.claim_id,
                UserModelClaimRow.aggregate_version == expected_version,
            )
            .values(**self._mutable_claim_values(claim))
        )
        if getattr(result, "rowcount", None) != 1:
            raise RuntimeError("user model claim was concurrently modified")

    def _update_world_claim(self, claim: WorldModelClaim, expected_version: int) -> None:
        self._check_version(claim.aggregate_version, expected_version)
        result = self._session.execute(
            update(WorldModelClaimRow)
            .where(
                WorldModelClaimRow.claim_id == claim.claim_id,
                WorldModelClaimRow.aggregate_version == expected_version,
            )
            .values(**self._mutable_claim_values(claim))
        )
        if getattr(result, "rowcount", None) != 1:
            raise RuntimeError("world model claim was concurrently modified")

    @staticmethod
    def _check_version(resulting: int, expected: int) -> None:
        if resulting != expected + 1:
            raise RuntimeError("model claim aggregate version is not monotonic")

    @staticmethod
    def _mutable_claim_values(claim: UserModelClaim | WorldModelClaim) -> dict[str, object]:
        return {
            "aggregate_version": claim.aggregate_version,
            "confidence": claim.confidence,
            "status": claim.status.value,
            "valid_until": claim.valid_until,
            "last_observed_at": claim.last_observed_at,
            "expires_at": claim.expires_at,
            "superseded_by_claim_id": claim.superseded_by_claim_id,
            "updated_at": claim.updated_at,
        }

    @staticmethod
    def _base_claim_values(claim: UserModelClaim | WorldModelClaim) -> dict[str, object]:
        return {
            "claim_id": claim.claim_id,
            "claim_key": claim.claim_key,
            "identity_id": claim.identity_id,
            "counterparty_id": claim.counterparty_id,
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
            "valid_from": claim.valid_from,
            "valid_until": claim.valid_until,
            "last_observed_at": claim.last_observed_at,
            "expires_at": claim.expires_at,
            "superseded_by_claim_id": claim.superseded_by_claim_id,
            "created_at": claim.created_at,
            "updated_at": claim.updated_at,
        }

    @classmethod
    def _user_row(cls, claim: UserModelClaim) -> UserModelClaimRow:
        return UserModelClaimRow(**cls._base_claim_values(claim))

    @classmethod
    def _world_row(cls, claim: WorldModelClaim) -> WorldModelClaimRow:
        return WorldModelClaimRow(
            **cls._base_claim_values(claim),
            subject_kind=claim.subject_kind,
            subject_label=claim.subject_label,
            normalized_subject_label=claim.normalized_subject_label,
        )

    @staticmethod
    def _user_evidence_row(item: ModelClaimEvidence) -> UserModelClaimEvidenceRow:
        return UserModelClaimEvidenceRow(
            evidence_id=item.evidence_id,
            claim_id=item.claim_id,
            source_message_id=item.source_message_id,
            source_interaction_id=item.source_interaction_id,
            observed_at=item.observed_at,
        )

    @staticmethod
    def _world_evidence_row(item: ModelClaimEvidence) -> WorldModelClaimEvidenceRow:
        return WorldModelClaimEvidenceRow(
            evidence_id=item.evidence_id,
            claim_id=item.claim_id,
            source_message_id=item.source_message_id,
            source_interaction_id=item.source_interaction_id,
            observed_at=item.observed_at,
        )

    @staticmethod
    def _revision_values(item: ModelClaimRevision) -> dict[str, object]:
        return {
            "revision_id": item.revision_id,
            "claim_id": item.claim_id,
            "claim_version": item.claim_version,
            "decision_id": item.decision_id,
            "kind": item.kind.value,
            "prior_status": item.prior_status.value if item.prior_status else None,
            "new_status": item.new_status.value,
            "prior_confidence": item.prior_confidence,
            "new_confidence": item.new_confidence,
            "prior_expires_at": item.prior_expires_at,
            "new_expires_at": item.new_expires_at,
            "reason_code": item.reason_code,
            "occurred_at": item.occurred_at,
        }

    @classmethod
    def _user_revision_row(cls, item: ModelClaimRevision) -> UserModelClaimRevisionRow:
        return UserModelClaimRevisionRow(**cls._revision_values(item))

    @classmethod
    def _world_revision_row(cls, item: ModelClaimRevision) -> WorldModelClaimRevisionRow:
        return WorldModelClaimRevisionRow(**cls._revision_values(item))

    def _map_user_claim(self, row: UserModelClaimRow) -> UserModelClaim:
        return UserModelClaim(
            claim_id=row.claim_id,
            claim_key=row.claim_key,
            identity_id=row.identity_id,
            counterparty_id=row.counterparty_id,
            schema_version=row.schema_version,
            aggregate_version=row.aggregate_version,
            policy_version=row.policy_version,
            formation_version=row.formation_version,
            normalization_version=row.normalization_version,
            predicate=row.predicate,
            value_kind=ModelValueKind(row.value_kind),
            value=self._scalar(row.value),
            normalized_value=row.normalized_value,
            epistemic_kind=ModelEpistemicKind(row.epistemic_kind),
            confidence=row.confidence,
            status=ModelClaimStatus(row.status),
            valid_from=row.valid_from,
            valid_until=row.valid_until,
            last_observed_at=row.last_observed_at,
            expires_at=row.expires_at,
            superseded_by_claim_id=row.superseded_by_claim_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            evidence=self._user_evidence(row.claim_id),
        )

    def _map_world_claim(self, row: WorldModelClaimRow) -> WorldModelClaim:
        return WorldModelClaim(
            claim_id=row.claim_id,
            claim_key=row.claim_key,
            identity_id=row.identity_id,
            counterparty_id=row.counterparty_id,
            schema_version=row.schema_version,
            aggregate_version=row.aggregate_version,
            policy_version=row.policy_version,
            formation_version=row.formation_version,
            normalization_version=row.normalization_version,
            subject_kind=row.subject_kind,
            subject_label=row.subject_label,
            normalized_subject_label=row.normalized_subject_label,
            predicate=row.predicate,
            value_kind=ModelValueKind(row.value_kind),
            value=self._scalar(row.value),
            normalized_value=row.normalized_value,
            epistemic_kind=ModelEpistemicKind(row.epistemic_kind),
            confidence=row.confidence,
            status=ModelClaimStatus(row.status),
            valid_from=row.valid_from,
            valid_until=row.valid_until,
            last_observed_at=row.last_observed_at,
            expires_at=row.expires_at,
            superseded_by_claim_id=row.superseded_by_claim_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            evidence=self._world_evidence(row.claim_id),
        )

    @staticmethod
    def _scalar(value: object) -> ModelScalar:
        if not isinstance(value, (str, int, float, bool)):
            raise RuntimeError("persisted model value has an unsupported type")
        return value

    def _user_evidence(self, claim_id: str) -> tuple[ModelClaimEvidence, ...]:
        rows = tuple(
            self._session.execute(
                select(UserModelClaimEvidenceRow)
                .where(UserModelClaimEvidenceRow.claim_id == claim_id)
                .order_by(
                    UserModelClaimEvidenceRow.observed_at,
                    UserModelClaimEvidenceRow.evidence_id,
                )
            ).scalars()
        )
        return tuple(self._map_evidence(row, ModelOwner.USER) for row in rows)

    def _world_evidence(self, claim_id: str) -> tuple[ModelClaimEvidence, ...]:
        rows = tuple(
            self._session.execute(
                select(WorldModelClaimEvidenceRow)
                .where(WorldModelClaimEvidenceRow.claim_id == claim_id)
                .order_by(
                    WorldModelClaimEvidenceRow.observed_at,
                    WorldModelClaimEvidenceRow.evidence_id,
                )
            ).scalars()
        )
        return tuple(self._map_evidence(row, ModelOwner.WORLD) for row in rows)

    @staticmethod
    def _map_evidence(
        row: UserModelClaimEvidenceRow | WorldModelClaimEvidenceRow, owner: ModelOwner
    ) -> ModelClaimEvidence:
        return ModelClaimEvidence(
            evidence_id=row.evidence_id,
            owner=owner,
            claim_id=row.claim_id,
            source_message_id=row.source_message_id,
            source_interaction_id=row.source_interaction_id,
            observed_at=row.observed_at,
        )

    @staticmethod
    def _map_revision(
        row: UserModelClaimRevisionRow | WorldModelClaimRevisionRow, owner: ModelOwner
    ) -> ModelClaimRevision:
        return ModelClaimRevision(
            revision_id=row.revision_id,
            owner=owner,
            claim_id=row.claim_id,
            claim_version=row.claim_version,
            decision_id=row.decision_id,
            kind=ModelRevisionKind(row.kind),
            prior_status=ModelClaimStatus(row.prior_status) if row.prior_status else None,
            new_status=ModelClaimStatus(row.new_status),
            prior_confidence=row.prior_confidence,
            new_confidence=row.new_confidence,
            prior_expires_at=row.prior_expires_at,
            new_expires_at=row.new_expires_at,
            reason_code=row.reason_code,
            occurred_at=row.occurred_at,
        )

    @staticmethod
    def _decision_values(decision: ModelFormationDecision) -> dict[str, object]:
        values = {
            name: getattr(decision, name)
            for name in (
                "decision_id",
                "idempotency_key",
                "source_interaction_id",
                "source_message_id",
                "identity_id",
                "counterparty_id",
                "formation_version",
                "policy_version",
                "reason_code",
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
                "decided_at",
                "trace_id",
                "formation_method",
                "provider",
                "model",
            )
        }
        values.update(
            kind=decision.kind.value,
            user_claim_ids=list(decision.user_claim_ids),
            world_claim_ids=list(decision.world_claim_ids),
        )
        return values

    @staticmethod
    def _map_decision(row: ModelFormationDecisionRow) -> ModelFormationDecision:
        return ModelFormationDecision(
            decision_id=row.decision_id,
            idempotency_key=row.idempotency_key,
            source_interaction_id=row.source_interaction_id,
            source_message_id=row.source_message_id,
            identity_id=row.identity_id,
            counterparty_id=row.counterparty_id,
            formation_version=row.formation_version,
            policy_version=row.policy_version,
            kind=ModelDecisionKind(row.kind),
            reason_code=row.reason_code,
            user_created_count=row.user_created_count,
            user_merged_count=row.user_merged_count,
            user_superseded_count=row.user_superseded_count,
            user_disputed_count=row.user_disputed_count,
            user_rejected_count=row.user_rejected_count,
            world_created_count=row.world_created_count,
            world_merged_count=row.world_merged_count,
            world_superseded_count=row.world_superseded_count,
            world_disputed_count=row.world_disputed_count,
            world_rejected_count=row.world_rejected_count,
            user_claim_ids=tuple(row.user_claim_ids),
            world_claim_ids=tuple(row.world_claim_ids),
            decided_at=row.decided_at,
            trace_id=row.trace_id,
            formation_method=row.formation_method,
            provider=row.provider,
            model=row.model,
        )

    @staticmethod
    def _audit_row(
        event_id: str,
        decision: ModelFormationDecision,
        *,
        owner: ModelOwner,
        claim_ids: tuple[str, ...],
    ) -> AuditEventRow:
        prefix = owner.value
        return AuditEventRow(
            event_id=event_id,
            schema_version=1,
            event_type=f"models.{prefix}_{decision.kind.value}",
            aggregate_type=f"{prefix}_model",
            aggregate_id=f"{decision.identity_id}:{decision.counterparty_id}",
            occurred_at=decision.decided_at,
            trace_id=decision.trace_id,
            details={
                "decision_id": decision.decision_id,
                "source_interaction_id": decision.source_interaction_id,
                "formation_version": decision.formation_version,
                "policy_version": decision.policy_version,
                "reason_code": decision.reason_code,
                "claim_ids": list(claim_ids),
                "created_count": getattr(decision, f"{prefix}_created_count"),
                "merged_count": getattr(decision, f"{prefix}_merged_count"),
                "superseded_count": getattr(decision, f"{prefix}_superseded_count"),
                "disputed_count": getattr(decision, f"{prefix}_disputed_count"),
                "rejected_count": getattr(decision, f"{prefix}_rejected_count"),
            },
        )
