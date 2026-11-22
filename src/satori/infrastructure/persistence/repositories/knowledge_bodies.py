"""Closed infrastructure helpers used only by existing knowledge owner writers."""

from typing import cast

from sqlalchemy import literal, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from satori.domain.erasure_knowledge import (
    MAX_KNOWLEDGE_TARGETS,
    ErasedKnowledgeHeader,
    KnowledgeBodyAction,
    KnowledgeBodyKind,
    KnowledgeBodyTarget,
    KnowledgeWitnessOrigin,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.canonical_provenance import canonical_row_digest
from satori.infrastructure.persistence.erasure_body_codec import decode_body_action
from satori.infrastructure.persistence.knowledge_material import (
    KNOWLEDGE_OWNER_SPECS,
    knowledge_material_digest,
)
from satori.infrastructure.persistence.models.erasure_bodies import ErasureBodyApplicationRow
from satori.infrastructure.persistence.models.models import UserModelClaimRow, WorldModelClaimRow
from satori.infrastructure.persistence.models.positions import (
    SatoriInclinationRow,
    SatoriPositionRow,
)
from satori.infrastructure.persistence.models.semantic import SemanticClaimRow

type KnowledgeRow = (
    SemanticClaimRow
    | UserModelClaimRow
    | WorldModelClaimRow
    | SatoriPositionRow
    | SatoriInclinationRow
)
_OWNER_MODELS = (
    SemanticClaimRow,
    UserModelClaimRow,
    WorldModelClaimRow,
    SatoriPositionRow,
    SatoriInclinationRow,
)


def _rows(
    session: Session, table: str, key: str, aggregate_id: str
) -> tuple[dict[str, object], ...]:
    # Table/key names come exclusively from the closed owner registry.
    return tuple(
        dict(row)
        for row in session.connection()
        .exec_driver_sql(f"SELECT * FROM {table} WHERE {key}=?", (aggregate_id,))
        .mappings()
    )


def erase_owned_knowledge_bodies(
    session: Session,
    targets: tuple[KnowledgeBodyTarget, ...],
    *,
    action_id: str,
    allowed_kinds: frozenset[KnowledgeBodyKind],
) -> None:
    "Validate every exact preimage before the caller-owned atomic transaction writes."
    if (
        not 1 <= len(targets) <= MAX_KNOWLEDGE_TARGETS
        or any(
            not isinstance(target, KnowledgeBodyTarget) or target.kind not in allowed_kinds
            for target in targets
        )
        or len({(target.kind, target.aggregate_id) for target in targets}) != len(targets)
    ):
        raise SourcePolicyError("knowledge_body_target_invalid")
    receipt = session.get(ErasureBodyApplicationRow, action_id)
    if receipt is None:
        raise SourcePolicyError("body_action_missing")
    try:
        approved = decode_body_action(receipt.action)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise SourcePolicyError("body_action_target_mismatch") from None
    if not isinstance(approved, KnowledgeBodyAction) or (
        approved.action_id != receipt.action_id
        or approved.floor_id != receipt.floor_id
        or approved.proposal.root.identity_id != receipt.identity_id
        or approved.proposal.root.message_id != receipt.root_message_id
        or approved.proposal.root.lineage_id != receipt.lineage_id
        or approved.proposal.request_id != receipt.request_id
        or approved.result_body_revision != receipt.body_revision
        or any(target not in approved.proposal.targets for target in targets)
    ):
        raise SourcePolicyError("body_action_target_mismatch")
    for target in targets:
        spec = KNOWLEDGE_OWNER_SPECS[target.kind]
        aggregates = _rows(session, spec.table, spec.key, target.aggregate_id)
        evidence = _rows(session, spec.evidence_table, spec.key, target.aggregate_id)
        revisions = _rows(session, spec.revision_table, spec.key, target.aggregate_id)
        if len(aggregates) != 1 or not evidence:
            raise SourcePolicyError("body_owner_state_changed")
        aggregate = aggregates[0]
        if (
            aggregate.get("aggregate_version") != target.aggregate_version
            or aggregate.get("body_state") != "available"
            or aggregate.get("body_action_id") is not None
            or any(
                aggregate.get(column) is None
                for column in spec.content_columns
                if column not in ("alternative_topic", "normalized_alternative_topic")
            )
            or knowledge_material_digest(
                target.kind, aggregate, evidence, revisions, terminal=False
            )
            != target.body_digest
            or knowledge_material_digest(target.kind, aggregate, evidence, revisions, terminal=True)
            != target.terminal_digest
        ):
            raise SourcePolicyError("body_owner_state_changed")
        root = approved.proposal.root
        if "identity_id" in aggregate and aggregate["identity_id"] != root.identity_id:
            raise SourcePolicyError("body_action_target_mismatch")
        if "counterparty_id" in aggregate and aggregate["counterparty_id"] != root.counterparty_id:
            raise SourcePolicyError("body_action_target_mismatch")
        for edge in evidence:
            message_key = (
                "root_message_id"
                if target.kind is KnowledgeBodyKind.SEMANTIC_CLAIM
                else "source_message_id"
            )
            interaction_key = (
                "root_interaction_id"
                if target.kind is KnowledgeBodyKind.SEMANTIC_CLAIM
                else "source_interaction_id"
            )
            source = (
                session.connection()
                .exec_driver_sql(
                    "SELECT s.identity_id,s.counterparty_id,s.session_id,m.content,m.body_state "
                    "FROM conversation_messages m "
                    "JOIN conversation_interactions i ON i.interaction_id=m.interaction_id "
                    "JOIN conversation_sessions s ON s.session_id=i.session_id "
                    "WHERE m.message_id=? AND m.interaction_id=? AND m.session_id=i.session_id "
                    "AND m.role='user' AND m.sequence=1 AND i.status='completed' "
                    "AND s.scope_verified=1",
                    (edge[message_key], edge[interaction_key]),
                )
                .one_or_none()
            )
            if source is None or tuple(source[:2]) != (root.identity_id, root.counterparty_id):
                raise SourcePolicyError("body_action_target_mismatch")
            if target.kind is KnowledgeBodyKind.POSITION:
                witness = next(
                    (
                        item
                        for item in target.witness_transitions
                        if item.evidence_id == edge[spec.evidence_key]
                    ),
                    None,
                )
                if witness is None or witness.root.session_id != source[2]:
                    raise SourcePolicyError("knowledge_body_target_unproven")
                if witness.origin is KnowledgeWitnessOrigin.LIVE and (
                    source[4] != "available"
                    or not isinstance(source[3], str)
                    or not isinstance(edge["quote"], str)
                    or not edge["quote"].strip()
                    or edge["quote"] not in source[3]
                ):
                    raise SourcePolicyError("knowledge_body_target_unproven")
        if target.kind is KnowledgeBodyKind.POSITION:
            witnesses = {item.evidence_id: item for item in target.witness_transitions}
            if set(witnesses) != {edge[spec.evidence_key] for edge in evidence}:
                raise SourcePolicyError("knowledge_body_target_unproven")
            for edge in evidence:
                witness = witnesses[str(edge[spec.evidence_key])]
                terminal_edge = dict(edge, quote=None, normalized_signature=None)
                if (
                    witness.before_digest != canonical_row_digest(edge)
                    or witness.after_digest != canonical_row_digest(terminal_edge)
                    or witness.root.message_id != edge["source_message_id"]
                    or witness.root.interaction_id != edge["source_interaction_id"]
                    or witness.root.counterparty_id != edge["source_counterparty_id"]
                ):
                    raise SourcePolicyError("knowledge_body_target_unproven")
    for target in targets:
        spec = KNOWLEDGE_OWNER_SPECS[target.kind]
        setters = ",".join(f"{column}=NULL" for column in spec.content_columns)
        changed = (
            session.connection()
            .exec_driver_sql(
                f"UPDATE {spec.table} SET {setters},body_state='erased',body_action_id=? "
                f"WHERE {spec.key}=? AND aggregate_version=? AND body_state='available' "
                "AND body_action_id IS NULL",
                (action_id, target.aggregate_id, target.aggregate_version),
            )
            .rowcount
        )
        if changed != 1:
            raise SourcePolicyError("body_owner_state_changed")
        if target.kind is KnowledgeBodyKind.POSITION:
            session.connection().exec_driver_sql(
                "UPDATE satori_position_evidence SET quote=NULL,normalized_signature=NULL "
                "WHERE position_id=?",
                (target.aggregate_id,),
            )
    session.expire_all()


def erased_knowledge_header(
    session: Session,
    kind: KnowledgeBodyKind,
    aggregate_id: str,
    *,
    allowed_kinds: frozenset[KnowledgeBodyKind],
) -> ErasedKnowledgeHeader | None:
    "Expose retained metadata without loading body columns or granting live admission."
    if kind not in allowed_kinds:
        raise SourcePolicyError("knowledge_body_target_invalid")
    spec = KNOWLEDGE_OWNER_SPECS[kind]
    row = (
        session.connection()
        .exec_driver_sql(
            f"SELECT aggregate_version,body_action_id FROM {spec.table} "
            f"WHERE {spec.key}=? AND body_state='erased' AND body_action_id IS NOT NULL",
            (aggregate_id,),
        )
        .one_or_none()
    )
    if row is None:
        return None
    evidence_ids = tuple(
        str(value)
        for value in session.connection()
        .exec_driver_sql(
            f"SELECT {spec.evidence_key} FROM {spec.evidence_table} WHERE {spec.key}=? "
            f"ORDER BY {spec.evidence_key}",
            (aggregate_id,),
        )
        .scalars()
    )
    return ErasedKnowledgeHeader(kind, aggregate_id, int(row[0]), str(row[1]), evidence_ids)


def require_available_knowledge_target(
    session: Session, kind: KnowledgeBodyKind, aggregate_id: str
) -> None:
    "Normal owner writes may create a missing target but never touch terminal identity."
    if _legacy_operator(session):
        return
    spec = KNOWLEDGE_OWNER_SPECS[kind]
    row = (
        session.connection()
        .exec_driver_sql(
            f"SELECT body_state,body_action_id FROM {spec.table} WHERE {spec.key}=?",
            (aggregate_id,),
        )
        .one_or_none()
    )
    if row is not None and (row[0] != "available" or row[1] is not None):
        raise SourcePolicyError("knowledge_body_unavailable")


def _legacy_operator(session: Session) -> bool:
    return session.info.get("source_operator_knowledge_legacy") is True


def available_owner_predicate(session: Session, model: type[KnowledgeRow]) -> ColumnElement[bool]:
    """Only exact predecessor operator sessions lack the new terminal representation."""
    return (
        literal(True)
        if _legacy_operator(session)
        else ((model.body_state == "available") & model.body_action_id.is_(None))
    )


def repair_owner_rows[T: KnowledgeRow](
    session: Session, model: type[T], *conditions: ColumnElement[bool]
) -> tuple[T, ...]:
    """Read a fixed predecessor projection solely for the existing owner repair policy."""
    if model not in _OWNER_MODELS:
        raise SourcePolicyError("knowledge_body_target_invalid")
    if not _legacy_operator(session):
        return tuple(
            session.scalars(
                select(model).where(
                    available_owner_predicate(session, cast(type[KnowledgeRow], model)), *conditions
                )
            )
        )
    columns = tuple(
        column
        for column in model.__table__.columns
        if column.name not in ("body_state", "body_action_id")
    )
    return tuple(
        cast(T, model(**dict(row), body_state="available", body_action_id=None))
        for row in session.execute(select(*columns).where(*conditions)).mappings()
    )


def current_owner_row[T: KnowledgeRow](
    session: Session, model: type[T], aggregate_id: str
) -> T | None:
    if not _legacy_operator(session):
        return session.get(model, aggregate_id)
    key = next(iter(model.__table__.primary_key))
    rows = repair_owner_rows(session, model, key == aggregate_id)
    return rows[0] if rows else None
