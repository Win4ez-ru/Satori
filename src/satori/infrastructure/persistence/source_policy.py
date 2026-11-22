"""SQL-friendly derived admission and complete, immutable actual-input receipts."""

from collections import deque
from datetime import datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import and_, delete, exists, func, inspect, literal, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement, SQLColumnExpression

from satori.domain.audit_metadata import AuditMetadataFamily
from satori.domain.source_policy import (
    FootprintStatus,
    PayloadRef,
    ProtectedContentFootprint,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.audit_metadata_writer import bind_metadata
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.erasure_intent import ErasurePolicyBindingRow
from satori.infrastructure.persistence.models.initial_self import AuditEventRow
from satori.infrastructure.persistence.models.source_policy import (
    OwnerSourceWithdrawalRow,
    SourceExcludedRootRow,
    SourceInputRow,
    SourceOwnerRepairRow,
    SourcePayloadRow,
    SourcePolicyBindingRow,
)


def policy_snapshot(session: Session) -> SourceEligibilitySnapshot | None:
    return cast(SourceEligibilitySnapshot | None, session.info.get("source_snapshot"))


def root_admission_predicate(
    session: Session, message_id: SQLColumnExpression[str]
) -> ColumnElement[bool]:
    message = ConversationMessageRow.__table__.alias()
    conditions = [
        message.c.message_id == message_id,
        message.c.role == "user",
        message.c.sequence == 1,
        message.c.content.is_not(None),
        func.length(message.c.content) > 0,
    ]
    # Only exact predecessor-schema operator repair opts into this private seam.
    # Ordinary current runtime never treats retained terminal headers as live roots.
    if session.info.get("source_operator_legacy") is not True:
        conditions.extend((message.c.body_state == "available", message.c.body_action_id.is_(None)))
    live = exists(select(message.c.message_id).where(*conditions))
    if policy_snapshot(session) is None:
        return live
    return live & ~exists(
        select(SourceExcludedRootRow.message_id).where(
            SourceExcludedRootRow.message_id == message_id
        )
    )


def payload_admission_predicate(
    session: Session,
    kind: str,
    payload_id: SQLColumnExpression[str],
    version: SQLColumnExpression[int],
) -> ColumnElement[bool]:
    snapshot = policy_snapshot(session)
    if snapshot is None:
        return literal(True)
    return exists(
        select(SourcePayloadRow.payload_id).where(
            SourcePayloadRow.kind == kind,
            SourcePayloadRow.payload_id == payload_id,
            SourcePayloadRow.payload_version == version,
            SourcePayloadRow.identity_id == snapshot.identity_id,
            SourcePayloadRow.policy_generation == snapshot.generation,
            SourcePayloadRow.eligible.is_(True),
        )
    )


def eligible_root(session: Session, message_id: str) -> bool:
    snapshot = policy_snapshot(session)
    if snapshot is None:
        return bool(session.scalar(select(root_admission_predicate(session, literal(message_id)))))
    return (
        session.scalar(
            select(ConversationMessageRow.message_id)
            .join(
                ConversationInteractionRow,
                and_(
                    ConversationInteractionRow.interaction_id
                    == ConversationMessageRow.interaction_id,
                    ConversationInteractionRow.session_id == ConversationMessageRow.session_id,
                ),
            )
            .join(ConversationSessionRow)
            .where(
                ConversationMessageRow.message_id == message_id,
                ConversationMessageRow.role == "user",
                ConversationMessageRow.sequence == 1,
                ConversationSessionRow.scope_verified.is_(True),
                ConversationSessionRow.identity_id == snapshot.identity_id,
                root_admission_predicate(session, ConversationMessageRow.message_id),
            )
        )
        is not None
    )


def eligible_payload(session: Session, kind: str, payload_id: str, version: int = 1) -> bool:
    if policy_snapshot(session) is None:
        return True
    return bool(
        session.scalar(
            select(
                payload_admission_predicate(session, kind, literal(payload_id), literal(version))
            )
        )
    )


def _refs(rows: list[dict[str, object]]) -> tuple[PayloadRef, ...]:
    refs: list[PayloadRef] = []
    try:
        for item in rows:
            if (
                set(item) != {"kind", "payload_id", "version"}
                or type(item["kind"]) is not str
                or type(item["payload_id"]) is not str
                or type(item["version"]) is not int
            ):
                raise ValueError("invalid_dependency")
            refs.append(
                PayloadRef(
                    item["kind"],
                    item["payload_id"],
                    item["version"],
                )
            )
        return tuple(refs)
    except (KeyError, TypeError, ValueError):
        raise SourcePolicyError("source_dependency_corrupt") from None


def _encoded(refs: tuple[PayloadRef, ...]) -> list[dict[str, object]]:
    return [
        {"kind": ref.kind, "payload_id": ref.payload_id, "version": ref.version} for ref in refs
    ]


def _admit(session: Session, footprint: ProtectedContentFootprint) -> FootprintStatus:
    snapshot = policy_snapshot(session)
    if snapshot is None:
        return footprint.status
    if footprint.status is FootprintStatus.OVERFLOW:
        raise SourcePolicyError("source_dependency_overflow")
    if any(not eligible_root(session, root) for root in footprint.root_message_ids):
        raise SourcePolicyError("source_root_unavailable")
    status = footprint.status
    for ref in footprint.payload_refs:
        row = session.get(SourcePayloadRow, (ref.kind, ref.payload_id, ref.version))
        if row is None or not eligible_payload(session, ref.kind, ref.payload_id, ref.version):
            raise SourcePolicyError("source_payload_unavailable")
        if row.proof_status != FootprintStatus.COMPLETE:
            status = FootprintStatus.UNKNOWN
    if status is FootprintStatus.UNKNOWN and (
        snapshot.excluded_message_ids or not (footprint.legacy or footprint.payload_refs)
    ):
        raise SourcePolicyError("unknown_content_dependency")
    return status


class SessionSourceAdmission:
    def __init__(self, session: Session) -> None:
        self.session = session

    def prepare(
        self, *, root_message_ids: tuple[str, ...], payload_refs: tuple[PayloadRef, ...]
    ) -> str:
        snapshot = policy_snapshot(self.session)
        if snapshot is None:
            raise SourcePolicyError("source_policy_unmanaged")
        footprint = ProtectedContentFootprint(
            tuple(sorted(set(root_message_ids))), tuple(dict.fromkeys(payload_refs))
        )
        status = _admit(self.session, footprint)
        input_id = str(uuid4())
        self.session.add(
            SourceInputRow(
                input_id=input_id,
                identity_id=snapshot.identity_id,
                policy_generation=snapshot.generation,
                proof_status=status.value,
                legacy=status is FootprintStatus.UNKNOWN,
                root_message_ids=list(footprint.root_message_ids),
                payload_refs=_encoded(footprint.payload_refs),
            )
        )
        self.session.flush()
        return input_id

    def require(self, input_id: str) -> None:
        require_source_inputs(self.session, input_id)

    def dispatch(self, input_id: str) -> None:
        require_source_inputs(self.session, input_id)

    def record(self, kind: str, payload_id: str, version: int, input_id: str) -> None:
        record_payload(self.session, kind, payload_id, version, input_id)


def _require_historical_source_inputs(
    session: Session, input_id: str | None
) -> SourceInputRow | None:
    """Reprove immutable roots/parent closure, without requiring historical owner heads."""
    snapshot = policy_snapshot(session)
    if snapshot is None:
        return None
    row = session.get(SourceInputRow, input_id) if input_id else None
    if row is None or row.identity_id != snapshot.identity_id:
        raise SourcePolicyError("source_input_required")
    _admit(
        session,
        ProtectedContentFootprint(
            tuple(row.root_message_ids),
            _refs(row.payload_refs),
            FootprintStatus(row.proof_status),
            legacy=row.legacy,
        ),
    )
    return row


def require_source_inputs(session: Session, input_id: str | None) -> None:
    """Reprove the actual selected owner heads before dispatch and the first write."""
    row = _require_historical_source_inputs(session, input_id)
    if row is not None:
        require_current_payloads(session, _refs(row.payload_refs))


def require_evidence_roots(
    session: Session, input_id: str | None, root_ids: tuple[str, ...]
) -> None:
    """Admit new durable evidence only from the exact completed canonical input window."""
    if policy_snapshot(session) is None:
        return
    require_source_inputs(session, input_id)
    prepared = session.get(SourceInputRow, input_id)
    assert prepared is not None
    roots = frozenset(prepared.root_message_ids)
    for root_id in frozenset(root_ids):
        if root_id not in roots or not eligible_root(session, root_id):
            raise SourcePolicyError("source_evidence_unavailable")
        completed = session.scalar(
            select(ConversationInteractionRow.interaction_id)
            .join(
                ConversationMessageRow,
                ConversationMessageRow.interaction_id == ConversationInteractionRow.interaction_id,
            )
            .where(
                ConversationMessageRow.message_id == root_id,
                ConversationInteractionRow.status == "completed",
            )
        )
        if completed is None:
            raise SourcePolicyError("source_evidence_unavailable")


def require_current_payloads(session: Session, refs: tuple[PayloadRef, ...]) -> None:
    """Reprove top-level selected owner versions; immutable parents stay historical."""
    from satori.infrastructure.persistence.models import (
        EpisodicMemoryRow,
        ReflectionProposalRow,
        SatoriInclinationRow,
        SatoriPositionRow,
        SemanticClaimRow,
        UserModelClaimRow,
        WorldModelClaimRow,
    )

    models = {
        "episode": EpisodicMemoryRow,
        "semantic_claim": SemanticClaimRow,
        "user_model_claim": UserModelClaimRow,
        "world_model_claim": WorldModelClaimRow,
        "position": SatoriPositionRow,
        "inclination": SatoriInclinationRow,
        "reflection_proposal": ReflectionProposalRow,
        "assistant_reply": ConversationInteractionRow,
    }
    for ref in refs:
        model = models.get(ref.kind)
        if model is None:
            raise SourcePolicyError("source_dispatch_kind_unsupported")
        if ref.kind == "reflection_proposal":
            # Immutable logical version 1 is not an availability grant. Never map payload first.
            if ref.version != 1:
                raise SourcePolicyError("source_dispatch_version_changed")
            connection = session.connection()
            columns = {
                str(c["name"]) for c in inspect(connection).get_columns("reflection_proposals")
            }
            markers = {"body_state", "body_action_id"}
            if markers <= columns:
                predicate = (
                    "body_state='available' AND body_action_id IS NULL "
                    "AND payload IS NOT NULL AND json_type(payload)='object'"
                )
            elif not markers.intersection(columns):
                from satori.infrastructure.persistence.erasure import _admit_schema

                revision, _digest = _admit_schema(connection)
                if revision not in {
                    "0016_source_policy",
                    "0017_erasure_intent",
                    "0018_erasure_bodies",
                    "0019_dialogue_bodies",
                    "0020_knowledge_bodies",
                }:
                    raise SourcePolicyError("reflection_body_unavailable")
                predicate = "payload IS NOT NULL AND json_type(payload)='object'"
            else:
                raise SourcePolicyError("reflection_body_unavailable")
            if (
                connection.exec_driver_sql(
                    "SELECT proposal_id FROM reflection_proposals WHERE proposal_id=? AND "
                    + predicate,
                    (ref.payload_id,),
                ).first()
                is None
            ):
                raise SourcePolicyError("reflection_body_unavailable")
            continue
        row = session.get(model, ref.payload_id, populate_existing=True)
        if row is None or getattr(row, "aggregate_version", 1) != ref.version:
            raise SourcePolicyError("source_dispatch_version_changed")
        if ref.kind == "episode" and (
            getattr(row, "lifecycle_status", None) != "active"
            or getattr(row, "body_state", None) != "available"
        ):
            raise SourcePolicyError("source_dispatch_episode_archived")
        if ref.kind in {
            "semantic_claim",
            "user_model_claim",
            "world_model_claim",
            "position",
            "inclination",
        } and (
            getattr(row, "body_state", None) != "available"
            or getattr(row, "body_action_id", None) is not None
        ):
            raise SourcePolicyError("knowledge_body_unavailable")
        if ref.kind == "assistant_reply":
            if getattr(row, "status", None) != "completed":
                raise SourcePolicyError("source_dispatch_version_changed")
            message = ConversationMessageRow.__table__.alias()
            for role, sequence in (("user", 1), ("assistant", 2)):
                if (
                    session.scalar(
                        select(message.c.message_id).where(
                            message.c.interaction_id == ref.payload_id,
                            message.c.session_id == getattr(row, "session_id", None),
                            message.c.role == role,
                            message.c.sequence == sequence,
                            message.c.body_state == "available",
                            message.c.body_action_id.is_(None),
                            message.c.content.is_not(None),
                            func.length(message.c.content) > 0,
                        )
                    )
                    is None
                ):
                    raise SourcePolicyError("dialogue_body_unavailable")


def record_payload(
    session: Session,
    kind: str,
    payload_id: str,
    version: int,
    input_id: str | None,
    *,
    payload_refs: tuple[PayloadRef, ...] = (),
) -> None:
    snapshot = policy_snapshot(session)
    if snapshot is None:
        return
    row = _require_historical_source_inputs(session, input_id)
    assert row is not None
    previous = (PayloadRef(kind, payload_id, version - 1),) if version > 1 else ()
    refs = tuple(dict.fromkeys((*_refs(row.payload_refs), *previous, *payload_refs)))
    footprint = ProtectedContentFootprint(
        tuple(row.root_message_ids), refs, FootprintStatus(row.proof_status), legacy=row.legacy
    )
    status = _admit(session, footprint)
    key = (kind, payload_id, version)
    if session.get(SourcePayloadRow, key) is not None:
        raise SourcePolicyError("source_payload_receipt_exists")
    session.add(
        SourcePayloadRow(
            kind=kind,
            payload_id=payload_id,
            payload_version=version,
            identity_id=snapshot.identity_id,
            policy_generation=snapshot.generation,
            proof_status=status.value,
            legacy=status is FootprintStatus.UNKNOWN,
            root_message_ids=list(footprint.root_message_ids),
            payload_refs=_encoded(refs),
            eligible=True,
        )
    )


def copy_payload(
    session: Session,
    kind: str,
    payload_id: str,
    from_version: int,
    to_version: int,
    *,
    allow_withheld: bool = False,
) -> None:
    snapshot = policy_snapshot(session)
    if snapshot is None:
        return
    if from_version < 1 or to_version != from_version + 1:
        raise SourcePolicyError("source_payload_version_invalid")
    previous = session.get(SourcePayloadRow, (kind, payload_id, from_version))
    if previous is not None and previous.identity_id != snapshot.identity_id:
        raise SourcePolicyError("source_payload_attribution_unproven")
    if not allow_withheld and (
        previous is None or not eligible_payload(session, kind, payload_id, from_version)
    ):
        raise SourcePolicyError("source_payload_unavailable")
    proof_status = previous.proof_status if previous is not None else FootprintStatus.UNKNOWN.value
    legacy = previous.legacy if previous is not None else False
    refs = _encoded((PayloadRef(kind, payload_id, from_version),))
    existing = session.get(SourcePayloadRow, (kind, payload_id, to_version))
    if existing is not None:
        # Semantic withdrawal already calls this helper. Its owner receipt may
        # repeat the same deterministic carry, never overwrite a different DAG.
        if not allow_withheld or (
            existing.identity_id,
            existing.policy_generation,
            existing.proof_status,
            existing.legacy,
            existing.root_message_ids,
            existing.payload_refs,
            existing.eligible,
        ) != (snapshot.identity_id, snapshot.generation, proof_status, legacy, [], refs, False):
            raise SourcePolicyError("source_payload_receipt_conflict")
        return
    copied = SourcePayloadRow(
        kind=kind,
        payload_id=payload_id,
        payload_version=to_version,
        identity_id=snapshot.identity_id,
        policy_generation=snapshot.generation,
        proof_status=proof_status,
        legacy=legacy,
        root_message_ids=[],
        payload_refs=refs,
        eligible=not allow_withheld,
    )
    session.add(copied)
    # Publish the exact new node for the withdrawal receipt in autoflush=False
    # owner transactions. This is still part of their atomic UoW, not a commit.
    session.flush((copied,))


def _carry_withdrawal_payload(
    session: Session,
    *,
    owner: str,
    aggregate_id: str,
    policy_generation: int,
    before_status: str,
    after_status: str,
    before_version: int,
    after_version: int,
) -> None:
    """Keep deterministic retirement ancestry; never admit it as new input."""
    from satori.infrastructure.persistence.models import (
        SatoriPositionRow,
        SemanticClaimRow,
        UserModelClaimRow,
        WorldModelClaimRow,
    )

    owners = {
        "user_model_claim": ("user_model_claim", UserModelClaimRow, {"current", "disputed"}),
        "world_model_claim": ("world_model_claim", WorldModelClaimRow, {"current", "disputed"}),
        "position": ("position", SatoriPositionRow, {"active", "competing"}),
        "semantic_memory": ("semantic_claim", SemanticClaimRow, {"active", "disputed"}),
    }
    spec = owners.get(owner)
    if spec is None:
        return  # Inclination holds/backlog receipts do not create owner versions.
    snapshot = policy_snapshot(session)
    if snapshot is None:
        return
    kind, model, prior_statuses = spec
    if (
        policy_generation != snapshot.generation
        or before_status not in prior_statuses
        or after_status != "retracted"
        or after_version != before_version + 1
    ):
        raise SourcePolicyError("source_withdrawal_transition_unproven")
    table = model.__table__
    key = table.c["position_id" if owner == "position" else "claim_id"]
    current = select(table.c.aggregate_version, table.c.status).where(key == aggregate_id)
    if model is not SemanticClaimRow:
        current = current.where(table.c.identity_id == snapshot.identity_id)
    if session.execute(current).one_or_none() != (after_version, after_status):
        raise SourcePolicyError("source_withdrawal_transition_unproven")
    copy_payload(session, kind, aggregate_id, before_version, after_version, allow_withheld=True)


def verify_mirror(
    session: Session,
    snapshot: SourceEligibilitySnapshot,
    *,
    include_erasure: bool = True,
    include_body: bool = True,
) -> None:
    row = session.get(SourcePolicyBindingRow, snapshot.identity_id)
    if row is None or (row.installation_id, row.floor_id, row.lineage_id) != (
        snapshot.installation_id,
        snapshot.floor_id,
        snapshot.lineage_id,
    ):
        raise SourcePolicyError("source_installation_binding_mismatch")
    if row.policy_generation != snapshot.generation:
        raise SourcePolicyError("source_policy_reconciliation_required")
    excluded = frozenset(
        session.scalars(
            select(SourceExcludedRootRow.message_id).where(
                SourceExcludedRootRow.identity_id == snapshot.identity_id
            )
        )
    )
    if excluded != snapshot.excluded_message_ids:
        raise SourcePolicyError("source_policy_mirror_mismatch")
    if include_erasure and snapshot.erasure_revision:
        if not inspect(session.connection()).has_table("erasure_policy_bindings"):
            raise SourcePolicyError("erasure_reconciliation_required")
        from satori.infrastructure.persistence.erasure_preparation import (
            validate_preparation_projection,
        )

        try:
            erasure = session.get(ErasurePolicyBindingRow, snapshot.identity_id)
            if erasure is None or (
                erasure.floor_id,
                erasure.lineage_id,
                erasure.erasure_revision,
            ) != (snapshot.floor_id, snapshot.lineage_id, snapshot.erasure_revision):
                raise SourcePolicyError("erasure_reconciliation_required")
            validate_preparation_projection(session, snapshot)
        except (SQLAlchemyError, ValueError, TypeError, KeyError, IndexError, RecursionError):
            raise SourcePolicyError("erasure_reconciliation_required") from None
    if include_erasure and include_body:
        from satori.infrastructure.persistence.erasure_bodies import validate_body_projection

        try:
            validate_body_projection(session, snapshot)
        except (SQLAlchemyError, ValueError, TypeError, KeyError, IndexError, RecursionError):
            raise SourcePolicyError("body_reconciliation_required") from None


def require_owner_reconciled(session: Session, owner: str) -> None:
    snapshot = policy_snapshot(session)
    if snapshot is None:
        return
    row = session.get(SourceOwnerRepairRow, (owner, snapshot.identity_id))
    if row is None or row.policy_generation != snapshot.generation:
        raise SourcePolicyError("source_owner_reconciliation_required")


def mark_owner_reconciled(session: Session, owner: str) -> None:
    snapshot = policy_snapshot(session)
    if snapshot is None:
        raise SourcePolicyError("source_policy_unmanaged")
    row = session.get(SourceOwnerRepairRow, (owner, snapshot.identity_id))
    if row is None:
        session.add(
            SourceOwnerRepairRow(
                owner=owner, identity_id=snapshot.identity_id, policy_generation=snapshot.generation
            )
        )
    else:
        row.policy_generation = snapshot.generation


def materialize_mirror(session: Session, snapshot: SourceEligibilitySnapshot) -> None:
    """Called only by SourcePolicyManager's exclusive, resumable reconciliation path."""
    binding = session.get(SourcePolicyBindingRow, snapshot.identity_id)
    if binding is None:
        binding = SourcePolicyBindingRow(
            identity_id=snapshot.identity_id,
            installation_id=snapshot.installation_id,
            floor_id=snapshot.floor_id,
            lineage_id=snapshot.lineage_id,
            policy_generation=snapshot.generation,
        )
        session.add(binding)
    elif (binding.installation_id, binding.floor_id, binding.lineage_id) != (
        snapshot.installation_id,
        snapshot.floor_id,
        snapshot.lineage_id,
    ):
        raise SourcePolicyError("source_installation_binding_mismatch")
    session.execute(
        delete(SourceExcludedRootRow).where(
            SourceExcludedRootRow.identity_id == snapshot.identity_id
        )
    )
    session.add_all(
        SourceExcludedRootRow(
            message_id=root, identity_id=snapshot.identity_id, policy_generation=snapshot.generation
        )
        for root in snapshot.excluded_message_ids
    )
    rows = tuple(
        session.scalars(
            select(SourcePayloadRow)
            .where(SourcePayloadRow.identity_id == snapshot.identity_id)
            .limit(100001)
        )
    )
    if len(rows) > 100000:
        raise SourcePolicyError("source_reconciliation_capacity_exceeded")
    roots = set(root for row in rows for root in row.root_message_ids)
    proven_roots: set[str] = set()
    root_list = tuple(roots)
    for offset in range(0, len(root_list), 400):
        proven_roots.update(
            session.scalars(
                select(ConversationMessageRow.message_id)
                .join(
                    ConversationInteractionRow,
                    and_(
                        ConversationInteractionRow.interaction_id
                        == ConversationMessageRow.interaction_id,
                        ConversationInteractionRow.session_id == ConversationMessageRow.session_id,
                    ),
                )
                .join(
                    ConversationSessionRow,
                    ConversationSessionRow.session_id == ConversationMessageRow.session_id,
                )
                .where(
                    ConversationMessageRow.message_id.in_(root_list[offset : offset + 400]),
                    ConversationMessageRow.role == "user",
                    ConversationMessageRow.sequence == 1,
                    ConversationSessionRow.scope_verified.is_(True),
                    ConversationSessionRow.identity_id == snapshot.identity_id,
                )
            )
        )
    by_ref = {PayloadRef(row.kind, row.payload_id, row.payload_version): row for row in rows}
    # Topological closure: each immutable edge is visited once; no growing root union,
    # no recursion depth limit that would reject long legitimate version histories.
    parents = {ref: _refs(row.payload_refs) for ref, row in by_ref.items()}
    children: dict[PayloadRef, list[PayloadRef]] = {}
    remaining = {ref: len(dependencies) for ref, dependencies in parents.items()}
    admitted: dict[PayloadRef, bool] = {}
    queue = deque(ref for ref, count in remaining.items() if count == 0)
    for ref, dependencies in parents.items():
        for parent in dependencies:
            children.setdefault(parent, []).append(ref)
    while queue:
        ref = queue.popleft()
        row = by_ref[ref]
        okay = (
            set(row.root_message_ids).issubset(proven_roots)
            and row.proof_status != FootprintStatus.OVERFLOW
            and not (snapshot.excluded_message_ids.intersection(row.root_message_ids))
        )
        if row.proof_status == FootprintStatus.UNKNOWN:
            okay = okay and row.legacy and not snapshot.excluded_message_ids
        admitted[ref] = okay and all(admitted.get(parent, False) for parent in parents[ref])
        for child in children.get(ref, ()):
            remaining[child] -= 1
            if remaining[child] == 0:
                queue.append(child)
    for ref, row in by_ref.items():
        # Cyclic/missing dependencies are never eligible.
        row.eligible = admitted.get(ref, False)
        row.policy_generation = snapshot.generation
    binding.policy_generation = snapshot.generation
    # Owner admission queries use SQL, including in autoflush=False transactions.
    # Publish the derived flags in this transaction before any owner repair/read.
    session.flush()


def record_owner_withdrawal(
    session: Session,
    *,
    owner: str,
    aggregate_id: str,
    request_id: str,
    policy_generation: int,
    before_status: str,
    after_status: str,
    before_version: int,
    after_version: int,
    root_message_ids: tuple[str, ...],
    now: datetime,
) -> None:
    if (
        session.scalar(
            select(OwnerSourceWithdrawalRow.receipt_id).where(
                OwnerSourceWithdrawalRow.owner == owner,
                OwnerSourceWithdrawalRow.aggregate_id == aggregate_id,
                OwnerSourceWithdrawalRow.request_id == request_id,
            )
        )
        is not None
    ):
        return
    _carry_withdrawal_payload(
        session,
        owner=owner,
        aggregate_id=aggregate_id,
        policy_generation=policy_generation,
        before_status=before_status,
        after_status=after_status,
        before_version=before_version,
        after_version=after_version,
    )
    receipt_id = str(uuid4())
    session.add(
        OwnerSourceWithdrawalRow(
            receipt_id=receipt_id,
            owner=owner,
            aggregate_id=aggregate_id,
            request_id=request_id,
            policy_generation=policy_generation,
            before_status=before_status,
            after_status=after_status,
            before_version=before_version,
            after_version=after_version,
            root_message_ids=list(root_message_ids),
            recorded_at=now,
        )
    )
    session.add(
        bind_metadata(
            session,
            AuditEventRow(
                event_id=str(uuid4()),
                schema_version=1,
                aggregate_type=owner,
                aggregate_id=aggregate_id,
                trace_id=request_id,
                event_type="source_support_withdrawn",
                occurred_at=now,
                details={
                    "owner": owner,
                    "aggregate_id": aggregate_id,
                    "request_id": request_id,
                    "policy_generation": policy_generation,
                    "receipt_id": receipt_id,
                    "before_version": before_version,
                    "after_version": after_version,
                },
            ),
            family=AuditMetadataFamily.SOURCE_WITHDRAWAL,
            identity_id=snapshot.identity_id
            if (snapshot := policy_snapshot(session)) is not None
            else "",
            owner_record_id=receipt_id,
        )
    )
