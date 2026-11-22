"""SQLAlchemy adapter for Stage 9 user/world model owners."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import exists, literal, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.core.models import ModelEpistemicKind, ModelScalar, ModelSourceMessage, ModelValueKind
from satori.domain.audit_metadata import AuditMetadataFamily
from satori.domain.erasure_knowledge import (
    ErasedKnowledgeHeader,
    KnowledgeBodyKind,
    KnowledgeBodyTarget,
)
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
    UserModelManager,
    WorldModelClaim,
    WorldModelManager,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.audit_metadata_writer import bind_metadata
from satori.infrastructure.persistence.memory_scope import require_live_message_content
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
from satori.infrastructure.persistence.repositories.knowledge_bodies import (
    available_owner_predicate,
    current_owner_row,
    erase_owned_knowledge_bodies,
    erased_knowledge_header,
    repair_owner_rows,
    require_available_knowledge_target,
)
from satori.infrastructure.persistence.source_policy import (
    eligible_payload,
    payload_admission_predicate,
    policy_snapshot,
    record_owner_withdrawal,
    record_payload,
    require_evidence_roots,
    require_owner_reconciled,
    require_source_inputs,
    root_admission_predicate,
)


class SQLAlchemyCurrentModelsRepository:
    """Persist two owner plans while preserving one source transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def erase_knowledge_bodies(
        self, targets: tuple[KnowledgeBodyTarget, ...], *, action_id: str
    ) -> None:
        erase_owned_knowledge_bodies(
            self._session,
            targets,
            action_id=action_id,
            allowed_kinds=frozenset(
                (
                    KnowledgeBodyKind.USER_MODEL_CLAIM,
                    KnowledgeBodyKind.WORLD_MODEL_CLAIM,
                )
            ),
        )

    def get_erased_knowledge_header(
        self, kind: KnowledgeBodyKind, aggregate_id: str
    ) -> ErasedKnowledgeHeader | None:
        return erased_knowledge_header(
            self._session,
            kind,
            aggregate_id,
            allowed_kinds=frozenset(
                (
                    KnowledgeBodyKind.USER_MODEL_CLAIM,
                    KnowledgeBodyKind.WORLD_MODEL_CLAIM,
                )
            ),
        )

    def withdraw_source_support(
        self,
        *,
        excluded_message_ids: frozenset[str],
        request_id: str,
        policy_generation: int,
        now: datetime,
    ) -> None:
        """Route retirement through both existing managers; retain immutable source history."""

        snapshot = policy_snapshot(self._session)
        if snapshot is None:
            return
        identity_id = snapshot.identity_id
        user_rows = repair_owner_rows(
            self._session,
            UserModelClaimRow,
            UserModelClaimRow.identity_id == identity_id,
            UserModelClaimRow.status.in_(("current", "disputed")),
        )
        world_rows = repair_owner_rows(
            self._session,
            WorldModelClaimRow,
            WorldModelClaimRow.identity_id == identity_id,
            WorldModelClaimRow.status.in_(("current", "disputed")),
        )
        users = tuple(self._map_user_claim(row) for row in user_rows)
        worlds = tuple(self._map_world_claim(row) for row in world_rows)
        user_plan = UserModelManager().withdraw_support(
            users,
            affected_claim_ids=frozenset(
                claim.claim_id
                for claim in users
                if not eligible_payload(
                    self._session, "user_model_claim", claim.claim_id, claim.aggregate_version
                )
            ),
            now=now,
        )
        world_plan = WorldModelManager().withdraw_support(
            worlds,
            affected_claim_ids=frozenset(
                claim.claim_id
                for claim in worlds
                if not eligible_payload(
                    self._session, "world_model_claim", claim.claim_id, claim.aggregate_version
                )
            ),
            now=now,
        )
        before_users = {claim.claim_id: claim for claim in users}
        before_worlds = {claim.claim_id: claim for claim in worlds}
        self._persist_user_plan(user_plan)
        self._persist_world_plan(world_plan)
        for kind, changed, prior in (
            ("user_model_claim", user_plan.claims, before_users),
            ("world_model_claim", world_plan.claims, before_worlds),
        ):
            for claim in changed:
                before = prior[claim.claim_id]
                record_owner_withdrawal(
                    self._session,
                    owner=kind,
                    aggregate_id=claim.claim_id,
                    request_id=request_id,
                    policy_generation=policy_generation,
                    before_status=before.status.value,
                    after_status=claim.status.value,
                    before_version=before.aggregate_version,
                    after_version=claim.aggregate_version,
                    root_message_ids=tuple(
                        sorted(
                            edge.source_message_id
                            for edge in claim.evidence
                            if edge.source_message_id in excluded_message_ids
                        )
                    ),
                    now=now,
                )

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
        require_owner_reconciled(self._session, "current_models")
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
                    ConversationMessageRow.session_id == ConversationInteractionRow.session_id,
                    ConversationSessionRow.scope_verified.is_(True)
                    if policy_snapshot(self._session) is not None
                    else literal(True),
                    ConversationInteractionRow.status == "completed",
                    ConversationInteractionRow.model_processing_required.is_(True),
                    ConversationInteractionRow.started_at <= interaction.started_at,
                    ConversationSessionRow.identity_id == session.identity_id,
                    ConversationSessionRow.counterparty_id == session.counterparty_id,
                    root_admission_predicate(self._session, ConversationMessageRow.message_id),
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
                content=require_live_message_content(message),
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
            UserModelClaimRow.body_state == "available",
            UserModelClaimRow.body_action_id.is_(None),
            UserModelClaimRow.identity_id == identity_id,
            UserModelClaimRow.counterparty_id == counterparty_id,
            payload_admission_predicate(
                self._session,
                "user_model_claim",
                UserModelClaimRow.claim_id,
                UserModelClaimRow.aggregate_version,
            ),
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
            WorldModelClaimRow.body_state == "available",
            WorldModelClaimRow.body_action_id.is_(None),
            WorldModelClaimRow.identity_id == identity_id,
            WorldModelClaimRow.counterparty_id == counterparty_id,
            payload_admission_predicate(
                self._session,
                "world_model_claim",
                WorldModelClaimRow.claim_id,
                WorldModelClaimRow.aggregate_version,
            ),
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
        return (
            self._map_user_claim(row)
            if row is not None
            and row.body_state == "available"
            and row.body_action_id is None
            and eligible_payload(
                self._session, "user_model_claim", row.claim_id, row.aggregate_version
            )
            else None
        )

    def get_world_claim(self, claim_id: str) -> WorldModelClaim | None:
        row = self._session.get(WorldModelClaimRow, claim_id)
        return (
            self._map_world_claim(row)
            if row is not None
            and row.body_state == "available"
            and row.body_action_id is None
            and eligible_payload(
                self._session, "world_model_claim", row.claim_id, row.aggregate_version
            )
            else None
        )

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
                    exists(
                        select(ConversationMessageRow.message_id).where(
                            ConversationMessageRow.interaction_id
                            == ConversationInteractionRow.interaction_id,
                            ConversationMessageRow.role == "user",
                            ConversationMessageRow.session_id
                            == ConversationInteractionRow.session_id,
                            exists(
                                select(ConversationSessionRow.session_id).where(
                                    ConversationSessionRow.session_id
                                    == ConversationInteractionRow.session_id,
                                    ConversationSessionRow.scope_verified.is_(True)
                                    if policy_snapshot(self._session) is not None
                                    else literal(True),
                                )
                            ),
                            root_admission_predicate(
                                self._session, ConversationMessageRow.message_id
                            ),
                        )
                    ),
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
        source_input_id: str | None = None,
    ) -> bool:
        require_owner_reconciled(self._session, "current_models")
        require_source_inputs(self._session, source_input_id)
        for claim in user_plan.claims:
            require_available_knowledge_target(
                self._session, KnowledgeBodyKind.USER_MODEL_CLAIM, claim.claim_id
            )
        for world_claim in world_plan.claims:
            require_available_knowledge_target(
                self._session, KnowledgeBodyKind.WORLD_MODEL_CLAIM, world_claim.claim_id
            )
        self._validate_user_sources(user_plan, source_input_id)
        self._validate_world_sources(world_plan, source_input_id)
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
        for claim in user_plan.claims:
            record_payload(
                self._session,
                "user_model_claim",
                claim.claim_id,
                claim.aggregate_version,
                source_input_id,
            )
        for world_claim in world_plan.claims:
            record_payload(
                self._session,
                "world_model_claim",
                world_claim.claim_id,
                world_claim.aggregate_version,
                source_input_id,
            )
        self._session.add_all(
            (
                self._audit_row(
                    user_audit_event_id,
                    decision,
                    owner=ModelOwner.USER,
                    claim_ids=decision.user_claim_ids,
                    source_input_id=source_input_id,
                ),
                self._audit_row(
                    world_audit_event_id,
                    decision,
                    owner=ModelOwner.WORLD,
                    claim_ids=decision.world_claim_ids,
                    source_input_id=source_input_id,
                ),
            )
        )
        return True

    def _validate_user_sources(
        self,
        plan: OwnerFormationPlan[UserModelClaim],
        input_id: str | None,
    ) -> None:
        if policy_snapshot(self._session) is None:
            return
        for claim in plan.claims:
            row = self._session.get(UserModelClaimRow, claim.claim_id)
            existing: tuple[ModelClaimEvidence, ...] = ()
            if row is not None:
                if not eligible_payload(
                    self._session, "user_model_claim", row.claim_id, row.aggregate_version
                ):
                    raise SourcePolicyError("source_payload_unavailable")
                if (
                    row.identity_id != claim.identity_id
                    or row.claim_key != claim.claim_key
                    or row.counterparty_id != claim.counterparty_id
                    or claim.aggregate_version != row.aggregate_version + 1
                ):
                    raise ValueError("model_aggregate_target_invalid")
                existing = self._map_user_claim(row).evidence
            elif claim.aggregate_version != 1:
                raise ValueError("model_aggregate_target_invalid")
            require_evidence_roots(
                self._session,
                input_id,
                tuple(edge.source_message_id for edge in claim.evidence if edge not in existing),
            )

    def _validate_world_sources(
        self,
        plan: OwnerFormationPlan[WorldModelClaim],
        input_id: str | None,
    ) -> None:
        if policy_snapshot(self._session) is None:
            return
        for claim in plan.claims:
            row = self._session.get(WorldModelClaimRow, claim.claim_id)
            existing: tuple[ModelClaimEvidence, ...] = ()
            if row is not None:
                if not eligible_payload(
                    self._session, "world_model_claim", row.claim_id, row.aggregate_version
                ):
                    raise SourcePolicyError("source_payload_unavailable")
                if (
                    row.identity_id != claim.identity_id
                    or row.claim_key != claim.claim_key
                    or row.counterparty_id != claim.counterparty_id
                    or claim.aggregate_version != row.aggregate_version + 1
                ):
                    raise ValueError("model_aggregate_target_invalid")
                existing = self._map_world_claim(row).evidence
            elif claim.aggregate_version != 1:
                raise ValueError("model_aggregate_target_invalid")
            require_evidence_roots(
                self._session,
                input_id,
                tuple(edge.source_message_id for edge in claim.evidence if edge not in existing),
            )

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
            require_available_knowledge_target(
                self._session, KnowledgeBodyKind.USER_MODEL_CLAIM, claim.claim_id
            )
            current = current_owner_row(self._session, UserModelClaimRow, claim.claim_id)
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
            require_available_knowledge_target(
                self._session, KnowledgeBodyKind.WORLD_MODEL_CLAIM, claim.claim_id
            )
            current = current_owner_row(self._session, WorldModelClaimRow, claim.claim_id)
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
                available_owner_predicate(self._session, UserModelClaimRow),
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
                available_owner_predicate(self._session, WorldModelClaimRow),
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
        if (
            row.body_state != "available"
            or row.body_action_id is not None
            or row.normalized_value is None
        ):
            raise SourcePolicyError("knowledge_body_unavailable")
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
        if (
            row.body_state != "available"
            or row.body_action_id is not None
            or row.normalized_value is None
            or row.subject_label is None
            or row.normalized_subject_label is None
        ):
            raise SourcePolicyError("knowledge_body_unavailable")
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

    def _audit_row(
        self,
        event_id: str,
        decision: ModelFormationDecision,
        *,
        owner: ModelOwner,
        claim_ids: tuple[str, ...],
        source_input_id: str | None = None,
    ) -> AuditEventRow:
        prefix = owner.value
        return bind_metadata(
            self._session,
            AuditEventRow(
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
            ),
            family=AuditMetadataFamily.USER_MODEL_FORMATION
            if owner is ModelOwner.USER
            else AuditMetadataFamily.WORLD_MODEL_FORMATION,
            identity_id=decision.identity_id,
            owner_record_id=decision.decision_id,
            source_input_id=source_input_id,
        )
