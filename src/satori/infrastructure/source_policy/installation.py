"""Explicit enrollment and resumable owner repair of an installation's main projection."""

from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import delete, func, inspect, literal, select, text
from sqlalchemy.orm import Session

from satori.domain.errors import NotActivated
from satori.domain.source_policy import (
    MAX_FOOTPRINT_ROOTS,
    CanonicalRootRef,
    FootprintStatus,
    SourceEligibilitySnapshot,
    SourceExclusionReceipt,
    SourcePolicyError,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
    EpisodicMemoryRow,
    InclinationEvidenceRow,
    MemoryEvidenceRow,
    PositionEvidenceRow,
    ReflectionProposalRow,
    ReflectionRunRow,
    ReflectionSourceRow,
    SatoriIdentityRow,
    SatoriInclinationRow,
    SatoriPositionRow,
    SemanticClaimEvidenceRow,
    SemanticClaimRow,
    SourceOwnerRepairRow,
    SourcePayloadRow,
    SourcePolicyBindingRow,
    UserModelClaimEvidenceRow,
    UserModelClaimRow,
    WorldModelClaimEvidenceRow,
    WorldModelClaimRow,
)
from satori.infrastructure.persistence.source_policy import (
    mark_owner_reconciled,
    materialize_mirror,
    verify_mirror,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime


class _LegacyMemoryEdge(NamedTuple):
    evidence_id: str
    memory_id: str
    source_message_id: str
    quote: str | None
    source_interaction_id: str


def _available_memory_evidence_ids(session: Session) -> set[str] | None:
    """Admit current live bodies separately from the historical proof projection."""
    inspector = inspect(session.connection())
    body_columns = {"body_state", "body_action_id"}
    present = tuple(
        body_columns.intersection(column["name"] for column in inspector.get_columns(table))
        for table in ("episodic_memories", "memory_evidence")
    )
    if not any(present):
        # Only the known pre-body schemas may use their original nonnullable quote.
        revisions = (
            tuple(session.scalars(text("SELECT version_num FROM alembic_version")))
            if inspector.has_table("alembic_version")
            else ()
        )
        if revisions not in (("0016_source_policy",), ("0017_erasure_intent",)):
            raise SourcePolicyError("source_enrollment_body_schema_unproven")
        return None
    if any(columns != body_columns for columns in present):
        raise SourcePolicyError("source_enrollment_body_schema_unproven")
    rows = session.scalars(
        select(MemoryEvidenceRow.evidence_id)
        .join(EpisodicMemoryRow)
        .where(
            EpisodicMemoryRow.body_state == "available",
            EpisodicMemoryRow.body_action_id.is_(None),
            EpisodicMemoryRow.summary.is_not(None),
            func.length(func.trim(EpisodicMemoryRow.summary)) > 0,
            MemoryEvidenceRow.body_state == "available",
            MemoryEvidenceRow.body_action_id.is_(None),
            MemoryEvidenceRow.quote.is_not(None),
            func.length(func.trim(MemoryEvidenceRow.quote)) > 0,
        )
        .limit(100001)
    ).all()
    if len(rows) > 100000:
        raise SourcePolicyError("source_enrollment_capacity_exceeded")
    return set(rows)


def _available_canonical_message_ids(session: Session) -> set[str] | None:
    """Enrollment compatibility never turns a terminal or malformed body into live text."""
    inspector = inspect(session.connection())
    body_columns = {"body_state", "body_action_id"}
    present = body_columns.intersection(
        column["name"] for column in inspector.get_columns("conversation_messages")
    )
    if not present:
        from satori.infrastructure.persistence.erasure import _admit_schema

        revision, _digest = _admit_schema(session.connection())
        if revision not in {"0016_source_policy", "0017_erasure_intent", "0018_erasure_bodies"}:
            raise SourcePolicyError("source_enrollment_dialogue_schema_unproven")
        return None
    if present != body_columns:
        raise SourcePolicyError("source_enrollment_dialogue_schema_unproven")
    rows = session.scalars(
        select(ConversationMessageRow.message_id)
        .where(
            ConversationMessageRow.body_state == "available",
            ConversationMessageRow.body_action_id.is_(None),
            ConversationMessageRow.content.is_not(None),
            func.length(func.trim(ConversationMessageRow.content)) > 0,
        )
        .limit(100001)
    ).all()
    if len(rows) > 100000:
        raise SourcePolicyError("source_enrollment_capacity_exceeded")
    return set(rows)


def _available_knowledge_ids(session: Session) -> dict[str, set[str]] | None:
    """Known old schemas have no markers; terminal/partial new bodies never seed legacy grants."""
    kinds = (
        ("semantic_claim", SemanticClaimRow, "claim_id", ("value", "normalized_value")),
        ("user_model_claim", UserModelClaimRow, "claim_id", ("value", "normalized_value")),
        (
            "world_model_claim",
            WorldModelClaimRow,
            "claim_id",
            ("value", "normalized_value", "subject_label", "normalized_subject_label"),
        ),
        ("position", SatoriPositionRow, "position_id", ("proposition", "normalized_proposition")),
        ("inclination", SatoriInclinationRow, "inclination_id", ("topic", "normalized_topic")),
    )
    inspector = inspect(session.connection())
    required = {"body_state", "body_action_id"}
    present = tuple(
        required.intersection(
            column["name"] for column in inspector.get_columns(model.__tablename__)
        )
        for _kind, model, _key, _body in kinds
    )
    if all(not fields for fields in present):
        from satori.infrastructure.persistence.erasure import _admit_schema

        revision, _digest = _admit_schema(session.connection())
        if revision not in {
            "0016_source_policy",
            "0017_erasure_intent",
            "0018_erasure_bodies",
            "0019_dialogue_bodies",
        }:
            raise SourcePolicyError("source_enrollment_knowledge_schema_unproven")
        return None
    if any(fields != required for fields in present):
        raise SourcePolicyError("source_enrollment_knowledge_schema_unproven")
    result: dict[str, set[str]] = {}
    for kind, model, key, body in kinds:
        ids = session.scalars(
            select(getattr(model, key))
            .where(
                model.body_state == "available",
                model.body_action_id.is_(None),
                *(getattr(model, column).is_not(None) for column in body),
            )
            .limit(100001)
        ).all()
        if len(ids) > 100000:
            raise SourcePolicyError("source_enrollment_capacity_exceeded")
        result[kind] = set(ids)
    return result


def _available_reflection_ids(session: Session) -> set[str] | None:
    """Enrollment cannot turn a terminal Reflection header into available content."""
    connection = session.connection()
    columns = {str(c["name"]) for c in inspect(connection).get_columns("reflection_proposals")}
    markers = {"body_state", "body_action_id"}
    if not markers.intersection(columns):
        from satori.infrastructure.persistence.erasure import _admit_schema

        revision, _digest = _admit_schema(connection)
        if revision not in {
            "0016_source_policy",
            "0017_erasure_intent",
            "0018_erasure_bodies",
            "0019_dialogue_bodies",
            "0020_knowledge_bodies",
        }:
            raise SourcePolicyError("source_enrollment_reflection_schema_unproven")
        return None
    if not markers <= columns:
        raise SourcePolicyError("source_enrollment_reflection_schema_unproven")
    rows = connection.exec_driver_sql(
        "SELECT proposal_id FROM reflection_proposals WHERE body_state='available' "
        "AND body_action_id IS NULL AND payload IS NOT NULL "
        "AND json_type(payload)='object' LIMIT 100001"
    ).all()
    if len(rows) > 100000:
        raise SourcePolicyError("source_enrollment_capacity_exceeded")
    return {str(row[0]) for row in rows}


def installation_identity(database: Database) -> str:
    with database.session_factory() as session:
        value = session.scalar(select(SatoriIdentityRow.identity_id))
        if value is None:
            raise NotActivated()
        return value


def bind_installation(database: Database, directory: Path) -> SourcePolicyRuntime:
    """Bind before operational composition; never creates/replaces a policy floor."""
    runtime = SourcePolicyRuntime(directory, installation_identity(database))

    def verify(snapshot: SourceEligibilitySnapshot) -> None:
        with database.session_factory() as session:
            verify_mirror(session, snapshot)

    runtime.verify_mirror = verify
    with runtime.shared():
        pass
    database.session_factory.configure(info={"source_runtime": runtime})
    return runtime


def _seed_legacy(session: Session, snapshot: SourceEligibilitySnapshot) -> None:
    """Explicitly reviewed pre-policy text remains UNKNOWN, never upgraded to COMPLETE."""
    roots, invalid = _legacy_root_proofs(session, snapshot)
    existing = set(
        session.execute(
            select(
                SourcePayloadRow.kind, SourcePayloadRow.payload_id, SourcePayloadRow.payload_version
            ).where(SourcePayloadRow.identity_id == snapshot.identity_id)
        ).tuples()
    )
    kinds = (
        ("episode", EpisodicMemoryRow, "memory_id", "aggregate_version"),
        ("semantic_claim", SemanticClaimRow, "claim_id", "aggregate_version"),
        ("user_model_claim", UserModelClaimRow, "claim_id", "aggregate_version"),
        ("world_model_claim", WorldModelClaimRow, "claim_id", "aggregate_version"),
        ("position", SatoriPositionRow, "position_id", "aggregate_version"),
        ("inclination", SatoriInclinationRow, "inclination_id", "aggregate_version"),
        ("reflection_proposal", ReflectionProposalRow, "proposal_id", None),
        ("assistant_reply", ConversationInteractionRow, "interaction_id", None),
    )
    for kind, model, id_name, version_name in kinds:
        # Enrollment does not materialize current ORM bodies on predecessor schemas.
        query = select(
            getattr(model, id_name), getattr(model, version_name) if version_name else literal(1)
        )
        if hasattr(model, "identity_id"):
            query = query.where(model.identity_id == snapshot.identity_id)
        elif model is ConversationInteractionRow:
            query = query.join(ConversationSessionRow).where(
                ConversationSessionRow.identity_id == snapshot.identity_id,
                ConversationInteractionRow.status == "completed",
            )
        payloads = session.execute(query.limit(100001)).tuples().all()
        if len(payloads) > 100000:
            raise SourcePolicyError("source_enrollment_capacity_exceeded")
        for raw_id, raw_version in payloads:
            payload_id = str(raw_id)
            version = int(raw_version)
            if (kind, payload_id, version) not in existing:
                key = (kind, payload_id)
                root_ids = tuple(sorted(roots.get(key, ())))
                overflow = len(root_ids) > MAX_FOOTPRINT_ROOTS
                legacy = bool(root_ids) and key not in invalid and not overflow
                session.add(
                    SourcePayloadRow(
                        kind=kind,
                        payload_id=payload_id,
                        payload_version=version,
                        identity_id=snapshot.identity_id,
                        policy_generation=snapshot.generation,
                        proof_status=(
                            FootprintStatus.OVERFLOW.value
                            if overflow
                            else FootprintStatus.UNKNOWN.value
                        ),
                        legacy=legacy,
                        root_message_ids=list(root_ids[:MAX_FOOTPRINT_ROOTS]),
                        payload_refs=[],
                        eligible=legacy and not snapshot.excluded_message_ids,
                    )
                )
    session.flush()


def _legacy_root_proofs(
    session: Session, snapshot: SourceEligibilitySnapshot
) -> tuple[dict[tuple[str, str], set[str]], set[tuple[str, str]]]:
    """Bulk prove accepted root chains, without certifying old provider-input closure."""
    available_ids = _available_memory_evidence_ids(session)
    available_messages = _available_canonical_message_ids(session)
    available_knowledge = _available_knowledge_ids(session)
    available_reflection = _available_reflection_ids(session)
    canonical_rows = session.execute(
        select(
            ConversationMessageRow.message_id,
            ConversationMessageRow.interaction_id,
            ConversationMessageRow.session_id,
            ConversationSessionRow.counterparty_id,
            ConversationMessageRow.content,
        )
        .join(
            ConversationInteractionRow,
            ConversationInteractionRow.interaction_id == ConversationMessageRow.interaction_id,
        )
        .join(
            ConversationSessionRow,
            ConversationSessionRow.session_id == ConversationMessageRow.session_id,
        )
        .where(
            ConversationMessageRow.role == "user",
            ConversationMessageRow.sequence == 1,
            ConversationInteractionRow.session_id == ConversationMessageRow.session_id,
            ConversationInteractionRow.status == "completed",
            ConversationSessionRow.scope_verified.is_(True),
            ConversationSessionRow.identity_id == snapshot.identity_id,
        )
        .limit(100001)
    ).all()
    if len(canonical_rows) > 100000:
        raise SourcePolicyError("source_enrollment_capacity_exceeded")
    canonical = {
        root: (interaction, scope, person, content)
        for root, interaction, scope, person, content in canonical_rows
        if isinstance(content, str)
        and content.strip()
        and (available_messages is None or root in available_messages)
    }
    roots: dict[tuple[str, str], set[str]] = {}
    invalid: set[tuple[str, str]] = set()

    def add(
        kind: str,
        payload_id: str,
        message_id: str,
        interaction_id: str,
        *,
        counterparty_id: str | None = None,
        session_id: str | None = None,
        quote: str | None = None,
    ) -> bool:
        key = (kind, payload_id)
        roots.setdefault(key, set()).add(message_id)
        proof = canonical.get(message_id)
        valid = (
            proof is not None
            and proof[0] == interaction_id
            and (session_id is None or proof[1] == session_id)
            and (counterparty_id is None or proof[2] == counterparty_id)
            and (quote is None or (bool(quote) and quote in proof[3]))
        )
        if not valid:
            invalid.add(key)
        return valid

    memory_rows = (
        session.execute(
            select(
                MemoryEvidenceRow.evidence_id,
                MemoryEvidenceRow.memory_id,
                MemoryEvidenceRow.source_message_id,
                MemoryEvidenceRow.quote,
                EpisodicMemoryRow.source_interaction_id,
            )
            .join(EpisodicMemoryRow)
            .limit(100001)
        )
        .tuples()
        .all()
    )
    if len(memory_rows) > 100000:
        raise SourcePolicyError("source_enrollment_capacity_exceeded")
    memory_edges = {
        evidence_id: _LegacyMemoryEdge(
            evidence_id, memory_id, source_message_id, quote, source_interaction_id
        )
        for evidence_id, memory_id, source_message_id, quote, source_interaction_id in memory_rows
    }
    for memory_edge in memory_edges.values():
        if (
            not memory_edge.quote
            or not memory_edge.quote.strip()
            or (available_ids is not None and memory_edge.evidence_id not in available_ids)
        ):
            invalid.add(("episode", memory_edge.memory_id))
        add(
            "episode",
            memory_edge.memory_id,
            memory_edge.source_message_id,
            memory_edge.source_interaction_id,
            quote=memory_edge.quote,
        )
    for semantic_edge in session.scalars(select(SemanticClaimEvidenceRow)):
        valid = add(
            "semantic_claim",
            semantic_edge.claim_id,
            semantic_edge.root_message_id,
            semantic_edge.root_interaction_id,
        )
        linked = memory_edges.get(semantic_edge.memory_evidence_id)
        if (
            not valid
            or linked is None
            or linked.memory_id != semantic_edge.memory_id
            or linked.source_message_id != semantic_edge.root_message_id
            or linked.source_interaction_id != semantic_edge.root_interaction_id
            or not linked.quote
            or linked.quote not in canonical[semantic_edge.root_message_id][3]
            or ("episode", semantic_edge.memory_id) in invalid
        ):
            invalid.add(("semantic_claim", semantic_edge.claim_id))
    for key, ids in roots.items():
        if (
            key[0] == "semantic_claim"
            and len({canonical[root][2] for root in ids if root in canonical}) != 1
        ):
            invalid.add(key)
    for model, evidence, kind in (
        (UserModelClaimRow, UserModelClaimEvidenceRow, "user_model_claim"),
        (WorldModelClaimRow, WorldModelClaimEvidenceRow, "world_model_claim"),
    ):
        for model_edge, claim_id, counterparty_id in session.execute(
            select(evidence, model.claim_id, model.counterparty_id)
            .join(model)
            .where(model.identity_id == snapshot.identity_id)
        ):
            add(
                kind,
                claim_id,
                model_edge.source_message_id,
                model_edge.source_interaction_id,
                counterparty_id=counterparty_id,
            )
    position_edges = {
        edge.evidence_id: edge
        for edge in session.scalars(
            select(PositionEvidenceRow)
            .join(SatoriPositionRow)
            .where(SatoriPositionRow.identity_id == snapshot.identity_id)
        )
    }
    for position_edge in position_edges.values():
        if not isinstance(position_edge.quote, str) or not position_edge.quote.strip():
            invalid.add(("position", position_edge.position_id))
        add(
            "position",
            position_edge.position_id,
            position_edge.source_message_id,
            position_edge.source_interaction_id,
            counterparty_id=position_edge.source_counterparty_id,
            quote=position_edge.quote,
        )
    for inclination_edge in session.scalars(
        select(InclinationEvidenceRow)
        .join(SatoriInclinationRow)
        .where(SatoriInclinationRow.identity_id == snapshot.identity_id)
    ):
        add(
            "inclination",
            inclination_edge.inclination_id,
            inclination_edge.source_message_id,
            inclination_edge.source_interaction_id,
            counterparty_id=inclination_edge.source_counterparty_id,
            session_id=inclination_edge.source_session_id,
        )
    sources: dict[str, list[ReflectionSourceRow]] = {}
    for source in session.scalars(
        select(ReflectionSourceRow)
        .join(ReflectionRunRow)
        .where(ReflectionRunRow.identity_id == snapshot.identity_id)
    ):
        sources.setdefault(source.run_id, []).append(source)
    for proposal_id, run_id, cited_sources in session.execute(
        select(
            ReflectionProposalRow.proposal_id,
            ReflectionProposalRow.run_id,
            ReflectionProposalRow.evidence_source_ids,
        )
        .join(ReflectionRunRow)
        .where(ReflectionRunRow.identity_id == snapshot.identity_id)
    ).tuples():
        run_sources = sources.get(run_id, ())
        if available_reflection is not None and proposal_id not in available_reflection:
            invalid.add(("reflection_proposal", proposal_id))
        if (
            not cited_sources
            or not all(type(source_id) is str for source_id in cited_sources)
            or not set(cited_sources).issubset({source.source_id for source in run_sources})
        ):
            invalid.add(("reflection_proposal", proposal_id))
        for source in run_sources:
            add(
                "reflection_proposal",
                proposal_id,
                source.root_message_id,
                source.root_interaction_id,
                counterparty_id=source.root_counterparty_id,
            )
    for root, (interaction, _, _, _) in canonical.items():
        add("assistant_reply", interaction, root, interaction)
    if available_knowledge is not None:
        invalid.update(
            (kind, payload_id)
            for kind, payload_id in roots
            if kind in available_knowledge and payload_id not in available_knowledge[kind]
        )
    return roots, invalid


def enroll_installation(
    database: Database, directory: Path, *, reviewed: bool, new_installation: bool = False
) -> SourcePolicyRuntime:
    """Only explicit enrollment may establish compatibility for pre-policy stored text."""
    identity_id = installation_identity(database)
    with database.session_factory() as session:
        bound = session.get(SourcePolicyBindingRow, identity_id)
        lineage_id = bound.lineage_id if bound else None
        if bound is not None and new_installation:
            raise SourcePolicyError("source_existing_lineage_cannot_create_floor")
    runtime = SourcePolicyRuntime(directory, identity_id)
    runtime.enroll(reviewed=reviewed, lineage_id=lineage_id, new_installation=new_installation)
    with runtime.exclusive() as snapshot, database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        # A populated bound DB cannot acquire invented receipts for a missing new version.
        if session.get(SourcePolicyBindingRow, identity_id) is None:
            _seed_legacy(session, snapshot)
        materialize_mirror(session, snapshot)
        session.commit()
    with runtime.exclusive() as snapshot:
        _repair_owners(database, snapshot, request_id=f"enroll-{snapshot.generation}")
    return bind_installation(database, directory)


def _repair_owners(
    database: Database, snapshot: SourceEligibilitySnapshot, *, request_id: str
) -> None:
    from satori.infrastructure.persistence.repositories.models import (
        SQLAlchemyCurrentModelsRepository,
    )
    from satori.infrastructure.persistence.repositories.positions import (
        SQLAlchemyPositionsRepository,
    )
    from satori.infrastructure.persistence.repositories.reflection import (
        SQLAlchemyReflectionRepository,
    )
    from satori.infrastructure.persistence.repositories.relationship import (
        SQLAlchemyRelationshipRepository,
    )
    from satori.infrastructure.persistence.repositories.semantic import (
        SQLAlchemySemanticMemoryRepository,
    )

    # This private seam is only historical/backlog reconciliation on an exact
    # admitted predecessor. It grants no content permission and acknowledges no
    # erasure/body head. Ordinary UoWs never set source_operator_legacy.
    with database.session_factory() as session:
        inspector = inspect(session.connection())
        if inspector.has_table("alembic_version"):
            from satori.infrastructure.persistence.erasure import _admit_schema

            revision, _digest = _admit_schema(session.connection())
            legacy = revision in {
                "0016_source_policy",
                "0017_erasure_intent",
                "0018_erasure_bodies",
            }
            knowledge_legacy = revision in {
                "0016_source_policy",
                "0017_erasure_intent",
                "0018_erasure_bodies",
                "0019_dialogue_bodies",
            }
            reflection_legacy = revision in {
                "0016_source_policy",
                "0017_erasure_intent",
                "0018_erasure_bodies",
                "0019_dialogue_bodies",
                "0020_knowledge_bodies",
            }
        else:
            columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
            if not {"body_state", "body_action_id"} <= columns:
                raise SourcePolicyError("source_operator_dialogue_schema_unproven")
            legacy = False
            knowledge_legacy = False
            reflection_legacy = False
            if not {"body_state", "body_action_id"} <= {
                column["name"] for column in inspector.get_columns("reflection_proposals")
            }:
                raise SourcePolicyError("source_operator_reflection_schema_unproven")
            if any(
                not {"body_state", "body_action_id"}
                <= {column["name"] for column in inspector.get_columns(table)}
                for table in (
                    "semantic_claims",
                    "user_model_claims",
                    "world_model_claims",
                    "satori_positions",
                    "satori_inclinations",
                )
            ):
                raise SourcePolicyError("source_operator_knowledge_schema_unproven")
    failed = False
    for owner, repository in (
        ("semantic_memory", SQLAlchemySemanticMemoryRepository),
        ("current_models", SQLAlchemyCurrentModelsRepository),
        ("positions", SQLAlchemyPositionsRepository),
        ("reflection_backlog", SQLAlchemyReflectionRepository),
        ("relationship_backlog", SQLAlchemyRelationshipRepository),
    ):
        # Clear a previous success before repair; only this owner's committed plan can
        # restore its watermark, including retries at the same policy generation.
        with database.session_factory() as session:
            session.execute(
                delete(SourceOwnerRepairRow).where(
                    SourceOwnerRepairRow.owner == owner,
                    SourceOwnerRepairRow.identity_id == snapshot.identity_id,
                )
            )
            session.commit()
        try:
            with database.session_factory() as session:
                session.info["source_snapshot"] = snapshot
                if legacy:
                    session.info["source_operator_legacy"] = True
                if knowledge_legacy:
                    # Exact-schema historical owner repair only. It neither grants
                    # content permission nor acknowledges any erasure/body head.
                    session.info["source_operator_knowledge_legacy"] = True
                if reflection_legacy:
                    session.info["source_operator_reflection_legacy"] = True
                repository(session).withdraw_source_support(
                    excluded_message_ids=snapshot.excluded_message_ids,
                    request_id=request_id,
                    policy_generation=snapshot.generation,
                    now=datetime.now(UTC),
                )
                mark_owner_reconciled(session, owner)
                session.commit()
        except Exception:
            # Mirror withholding remains active; one failed owner does not block unrelated reads.
            failed = True
    if failed:
        raise SourcePolicyError("source_owner_reconciliation_required")


def repair_installation(
    database: Database,
    runtime: SourcePolicyRuntime,
    snapshot: SourceEligibilitySnapshot,
    receipt: SourceExclusionReceipt,
) -> None:
    """Exclusive floor-first projection, followed by independent resumable owner transactions."""
    runtime._require_lease(exclusive=True)
    with database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        materialize_mirror(session, snapshot)
        session.commit()
    _repair_owners(database, snapshot, request_id=receipt.request_id)


def canonical_root(
    database: Database, runtime: SourcePolicyRuntime, message_id: str, *, counterparty_id: str
) -> CanonicalRootRef:
    """Prove exact canonical metadata without exposing source contents or requiring eligibility."""
    with runtime.exclusive() as snapshot, database.session_factory() as session:
        record = session.execute(
            select(
                ConversationMessageRow.message_id,
                ConversationMessageRow.interaction_id,
                ConversationMessageRow.session_id,
                ConversationSessionRow.counterparty_id,
            )
            .join(
                ConversationInteractionRow,
                ConversationInteractionRow.interaction_id == ConversationMessageRow.interaction_id,
            )
            .join(
                ConversationSessionRow,
                ConversationSessionRow.session_id == ConversationMessageRow.session_id,
            )
            .where(
                ConversationMessageRow.message_id == message_id,
                ConversationMessageRow.role == "user",
                ConversationMessageRow.sequence == 1,
                ConversationInteractionRow.session_id == ConversationMessageRow.session_id,
                ConversationSessionRow.identity_id == snapshot.identity_id,
                ConversationSessionRow.counterparty_id == counterparty_id,
                ConversationSessionRow.scope_verified.is_(True),
            )
        ).one_or_none()
        if record is None:
            raise SourcePolicyError("canonical_source_unavailable_in_scope")
        message_id, interaction_id, session_id, scoped_counterparty_id = record
        return CanonicalRootRef(
            snapshot.lineage_id,
            snapshot.identity_id,
            scoped_counterparty_id,
            session_id,
            interaction_id,
            message_id,
        )
