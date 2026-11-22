"""Explicit append-only preparation projection; no aggregate body mutation path."""

from collections.abc import Sequence
from typing import Literal, cast

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from satori.domain.erasure_intent import ErasureIntent
from satori.domain.erasure_preparation import (
    ErasureBodyReadiness,
    ErasureOwnerAssessment,
    ErasureOwnerPreparation,
    ErasurePreparationOwner,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
    ErasurePolicyBindingRow,
    ErasurePreparationRow,
    SourceOwnerRepairRow,
)
from satori.infrastructure.persistence.source_policy import verify_mirror

_SOURCE_OWNERS = (
    "semantic_memory",
    "current_models",
    "positions",
    "reflection_backlog",
    "relationship_backlog",
)
_COUNTS = ("content_rows", "numeric_rows", "metadata_rows", "uncertain_rows")
_COMMON = (
    "source_generation",
    "schema_revision",
    "schema_digest",
    "store_digest",
    "routing_digest",
    "retention_digest",
    "preparation_digest",
    "assessment_status",
    "prepared_at",
)
_MAX_PREPARATION_ROWS = 150_000


def _prove_root(
    session: Session, root: CanonicalRootRef, snapshot: SourceEligibilitySnapshot
) -> None:
    if root.identity_id != snapshot.identity_id or root.lineage_id != snapshot.lineage_id:
        raise SourcePolicyError("canonical_source_unavailable_in_scope")
    proven = session.scalar(
        select(ConversationMessageRow.message_id)
        .join(
            ConversationInteractionRow,
            ConversationInteractionRow.interaction_id == ConversationMessageRow.interaction_id,
        )
        .join(
            ConversationSessionRow,
            ConversationSessionRow.session_id == ConversationInteractionRow.session_id,
        )
        .where(
            ConversationMessageRow.message_id == root.message_id,
            ConversationMessageRow.interaction_id == root.interaction_id,
            ConversationMessageRow.session_id == root.session_id,
            ConversationMessageRow.role == "user",
            ConversationMessageRow.sequence == 1,
            ConversationInteractionRow.session_id == root.session_id,
            ConversationInteractionRow.status == "completed",
            ConversationSessionRow.scope_verified.is_(True),
            ConversationSessionRow.identity_id == root.identity_id,
            ConversationSessionRow.counterparty_id == root.counterparty_id,
        )
    )
    if proven is None:
        raise SourcePolicyError("canonical_source_unavailable_in_scope")
    if root.message_id not in snapshot.excluded_message_ids:
        raise SourcePolicyError("erasure_requires_source_exclusion")


def _source_ready(session: Session, snapshot: SourceEligibilitySnapshot) -> None:
    verify_mirror(session, snapshot, include_erasure=False)
    for owner in _SOURCE_OWNERS:
        row = session.get(SourceOwnerRepairRow, (owner, snapshot.identity_id))
        if row is None or row.policy_generation != snapshot.generation:
            raise SourcePolicyError("source_owner_reconciliation_required")


def _intent(row: ErasurePreparationRow) -> ErasureIntent:
    return ErasureIntent(
        root=CanonicalRootRef(
            row.lineage_id,
            row.identity_id,
            row.counterparty_id,
            row.root_session_id,
            row.root_interaction_id,
            row.root_message_id,
        ),
        request_id=row.request_id,
        decision_id=row.decision_id,
        floor_id=row.floor_id,
        installation_id=row.installation_id,
        expected_source_generation=row.expected_source_generation,
        expected_erasure_revision=row.expected_erasure_revision,
        result_erasure_revision=row.result_erasure_revision,
        reviewed_preview_digest=row.reviewed_preview_digest,
        approval_digest=row.approval_digest,
        schema_revision=row.intent_schema_revision,
        schema_digest=row.intent_schema_digest,
        store_digest=row.intent_store_digest,
        routing_digest=row.intent_routing_digest,
        assessment_status=cast(Literal["complete", "blocked"], row.intent_assessment_status),
        retention_policy=row.retention_policy,
        decided_at=row.decided_at,
        trace_id=row.trace_id,
        policy_version=row.policy_version,
    )


def _map(rows: Sequence[ErasurePreparationRow]) -> ErasureOwnerPreparation:
    first = rows[0]
    intent = _intent(first)
    assessments = []
    for row in rows:
        if _intent(row) != intent or any(
            getattr(row, name) != getattr(first, name) for name in _COMMON
        ):
            raise ValueError("erasure_preparation_projection_inconsistent")
        if not isinstance(row.counts, dict) or set(row.counts) != set(_COUNTS):
            raise ValueError("erasure_preparation_projection_invalid")
        if not isinstance(row.reason_codes, list) or any(
            type(item) is not str for item in row.reason_codes
        ):
            raise ValueError("erasure_preparation_projection_invalid")
        assessments.append(
            ErasureOwnerAssessment(
                ErasurePreparationOwner(row.owner),
                ErasureBodyReadiness(row.body_readiness),
                tuple(row.reason_codes),
                row.counts["content_rows"],
                row.counts["numeric_rows"],
                row.counts["metadata_rows"],
                row.counts["uncertain_rows"],
                row.contract_version,
            )
        )
    result = ErasureOwnerPreparation(
        intent,
        first.source_generation,
        first.schema_revision,
        first.schema_digest,
        first.store_digest,
        first.routing_digest,
        first.retention_digest,
        first.assessment_status,
        tuple(assessments),
        first.prepared_at,
    )
    if result.preparation_digest != first.preparation_digest:
        raise ValueError("erasure_preparation_projection_digest_invalid")
    return result


def _bound(preparation: ErasureOwnerPreparation, snapshot: SourceEligibilitySnapshot) -> None:
    intent = preparation.intent
    if (
        (intent.root.identity_id, intent.root.lineage_id, intent.floor_id, intent.installation_id)
        != (snapshot.identity_id, snapshot.lineage_id, snapshot.floor_id, snapshot.installation_id)
        or intent.result_erasure_revision > snapshot.erasure_revision
        or preparation.source_generation > snapshot.generation
        or intent.root.message_id not in snapshot.excluded_message_ids
    ):
        raise ValueError("erasure_preparation_binding_mismatch")


def _latest(
    session: Session, request_id: str, snapshot: SourceEligibilitySnapshot
) -> tuple[int, ErasureOwnerPreparation | None]:
    rows = tuple(
        session.scalars(
            select(ErasurePreparationRow)
            .where(
                ErasurePreparationRow.request_id == request_id,
            )
            .order_by(ErasurePreparationRow.preparation_revision, ErasurePreparationRow.owner)
            .limit(_MAX_PREPARATION_ROWS + 1)
        )
    )
    if not rows:
        return 0, None
    if len(rows) > _MAX_PREPARATION_ROWS:
        raise ValueError("erasure_preparation_capacity_exceeded")
    groups: dict[int, list[ErasurePreparationRow]] = {}
    for row in rows:
        groups.setdefault(row.preparation_revision, []).append(row)
    if max(groups) > _MAX_PREPARATION_ROWS // len(ErasurePreparationOwner) or set(groups) != set(
        range(1, max(groups) + 1)
    ):
        raise ValueError("erasure_preparation_history_incomplete")
    previous: ErasureOwnerPreparation | None = None
    for group in groups.values():
        current = _map(group)
        _bound(current, snapshot)
        if previous is not None and (
            current.intent != previous.intent
            or current.source_generation < previous.source_generation
        ):
            raise ValueError("erasure_preparation_history_inconsistent")
        previous = current
    return max(groups), previous


def validate_preparation_projection(session: Session, snapshot: SourceEligibilitySnapshot) -> None:
    """Operational ACK admission reads only the latest owner group of each intent."""
    latest = (
        select(
            ErasurePreparationRow.request_id,
            func.max(ErasurePreparationRow.preparation_revision).label("revision"),
        )
        .group_by(ErasurePreparationRow.request_id)
        .subquery()
    )
    rows = tuple(
        session.scalars(
            select(ErasurePreparationRow)
            .join(
                latest,
                (latest.c.request_id == ErasurePreparationRow.request_id)
                & (latest.c.revision == ErasurePreparationRow.preparation_revision),
            )
            .order_by(ErasurePreparationRow.request_id, ErasurePreparationRow.owner)
            .limit(_MAX_PREPARATION_ROWS + 1)
        )
    )
    if len(rows) > _MAX_PREPARATION_ROWS:
        raise ValueError("erasure_preparation_capacity_exceeded")
    groups: dict[str, list[ErasurePreparationRow]] = {}
    for row in rows:
        groups.setdefault(row.request_id, []).append(row)
    if len(groups) != snapshot.erasure_revision:
        raise ValueError("erasure_preparation_intent_coverage_incomplete")
    revisions: set[int] = set()
    for group in groups.values():
        preparation = _map(group)
        _bound(preparation, snapshot)
        _prove_root(session, preparation.intent.root, snapshot)
        revisions.add(preparation.intent.result_erasure_revision)
    if revisions != set(range(1, snapshot.erasure_revision + 1)):
        raise ValueError("erasure_preparation_intent_coverage_incomplete")


def _schema_ready(session: Session) -> None:
    # Reuse the exact admitted schema registry. This private path requires 0017;
    # a source-only 0016 database must be migrated before a floor intent is durable.
    from satori.infrastructure.persistence.erasure import (
        ERASURE_APPRAISAL_SCHEMA_REVISION,
        ERASURE_AUDIT_SCHEMA_REVISION,
        ERASURE_BODY_SCHEMA_REVISION,
        ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
        ERASURE_DIALOGUE_SCHEMA_REVISION,
        ERASURE_INTENT_SCHEMA_REVISION,
        ERASURE_KNOWLEDGE_SCHEMA_REVISION,
        ERASURE_REFLECTION_SCHEMA_REVISION,
        _admit_schema,
    )

    revision, _ = _admit_schema(session.connection())
    if revision not in {
        ERASURE_INTENT_SCHEMA_REVISION,
        ERASURE_BODY_SCHEMA_REVISION,
        ERASURE_DIALOGUE_SCHEMA_REVISION,
        ERASURE_KNOWLEDGE_SCHEMA_REVISION,
        ERASURE_REFLECTION_SCHEMA_REVISION,
        ERASURE_APPRAISAL_SCHEMA_REVISION,
        ERASURE_AUDIT_SCHEMA_REVISION,
        ERASURE_CHECKPOINT_REASON_SCHEMA_REVISION,
    }:
        raise SourcePolicyError("erasure_preparation_schema_required")


def _row(
    preparation: ErasureOwnerPreparation, revision: int, assessment: ErasureOwnerAssessment
) -> ErasurePreparationRow:
    intent = preparation.intent
    return ErasurePreparationRow(
        request_id=intent.request_id,
        preparation_revision=revision,
        owner=assessment.owner.value,
        decision_id=intent.decision_id,
        identity_id=intent.root.identity_id,
        counterparty_id=intent.root.counterparty_id,
        root_message_id=intent.root.message_id,
        root_interaction_id=intent.root.interaction_id,
        root_session_id=intent.root.session_id,
        floor_id=intent.floor_id,
        installation_id=intent.installation_id,
        lineage_id=intent.root.lineage_id,
        policy_version=intent.policy_version,
        retention_policy=intent.retention_policy,
        expected_source_generation=intent.expected_source_generation,
        expected_erasure_revision=intent.expected_erasure_revision,
        result_erasure_revision=intent.result_erasure_revision,
        source_generation=preparation.source_generation,
        reviewed_preview_digest=intent.reviewed_preview_digest,
        approval_digest=intent.approval_digest,
        intent_schema_revision=intent.schema_revision,
        intent_schema_digest=intent.schema_digest,
        intent_store_digest=intent.store_digest,
        intent_routing_digest=intent.routing_digest,
        intent_assessment_status=intent.assessment_status,
        decided_at=intent.decided_at,
        trace_id=intent.trace_id,
        contract_version=assessment.contract_version,
        schema_revision=preparation.schema_revision,
        schema_digest=preparation.schema_digest,
        store_digest=preparation.store_digest,
        routing_digest=preparation.routing_digest,
        retention_digest=preparation.retention_digest,
        preparation_digest=preparation.preparation_digest,
        assessment_status=preparation.assessment_status,
        body_readiness=assessment.body_readiness.value,
        reason_codes=list(assessment.reason_codes),
        counts={name: getattr(assessment, name) for name in _COUNTS},
        prepared_at=preparation.prepared_at,
    )


class SQLiteErasurePreparationStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def admit(self, snapshot: SourceEligibilitySnapshot) -> None:
        try:
            with self.database.session_factory() as session:
                _schema_ready(session)
                _source_ready(session, snapshot)
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, ValueError, TypeError):
            raise SourcePolicyError("erasure_preparation_projection_invalid") from None

    def verify_current(self, snapshot: SourceEligibilitySnapshot) -> None:
        try:
            with self.database.session_factory() as session:
                _schema_ready(session)
                _source_ready(session, snapshot)
                verify_mirror(session, snapshot)
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, ValueError, TypeError):
            raise SourcePolicyError("erasure_preparation_projection_invalid") from None

    def inspect(
        self, request_id: str, *, root: CanonicalRootRef, snapshot: SourceEligibilitySnapshot
    ) -> ErasureOwnerPreparation | None:
        try:
            with self.database.session_factory() as session:
                _prove_root(session, root, snapshot)
                _schema_ready(session)
                _source_ready(session, snapshot)
                _, result = _latest(session, request_id, snapshot)
                if result is not None and result.intent.root != root:
                    raise SourcePolicyError("canonical_source_unavailable_in_scope")
                return result
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, ValueError, TypeError, KeyError, IndexError):
            raise SourcePolicyError("erasure_preparation_projection_invalid") from None

    def persist(
        self,
        preparations: tuple[ErasureOwnerPreparation, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
    ) -> None:
        if (
            not preparations
            or len(preparations) != snapshot.erasure_revision
            or {item.intent.result_erasure_revision for item in preparations}
            != set(range(1, snapshot.erasure_revision + 1))
            or len({item.intent.request_id for item in preparations}) != len(preparations)
        ):
            raise SourcePolicyError("erasure_preparation_intent_coverage_incomplete")
        try:
            with self.database.session_factory() as session:
                # The explicit caller holds the floor's exclusive lease first. Acquire
                # SQLite's writer slot before proofs; no provider or body owner runs here.
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                _schema_ready(session)
                _source_ready(session, snapshot)
                for preparation in preparations:
                    intent = preparation.intent
                    _prove_root(session, intent.root, snapshot)
                    if (
                        intent.floor_id != snapshot.floor_id
                        or intent.installation_id != snapshot.installation_id
                        or preparation.source_generation != snapshot.generation
                    ):
                        raise SourcePolicyError("erasure_preparation_binding_mismatch")
                    revision, previous = _latest(session, intent.request_id, snapshot)
                    if previous is not None and previous.intent != intent:
                        raise SourcePolicyError("erasure_request_conflict")
                    if (
                        previous is not None
                        and previous.preparation_digest == preparation.preparation_digest
                    ):
                        continue
                    session.add_all(
                        _row(preparation, revision + 1, item) for item in preparation.assessments
                    )
                session.flush()
                validate_preparation_projection(session, snapshot)
                binding = session.get(ErasurePolicyBindingRow, snapshot.identity_id)
                if binding is None:
                    session.add(
                        ErasurePolicyBindingRow(
                            identity_id=snapshot.identity_id,
                            floor_id=snapshot.floor_id,
                            lineage_id=snapshot.lineage_id,
                            erasure_revision=snapshot.erasure_revision,
                        )
                    )
                elif (binding.floor_id, binding.lineage_id) != (
                    snapshot.floor_id,
                    snapshot.lineage_id,
                ) or binding.erasure_revision > snapshot.erasure_revision:
                    raise SourcePolicyError("erasure_preparation_binding_mismatch")
                else:
                    binding.erasure_revision = snapshot.erasure_revision
                session.commit()
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, ValueError, TypeError, KeyError, IndexError):
            raise SourcePolicyError("erasure_preparation_commit_failed") from None
