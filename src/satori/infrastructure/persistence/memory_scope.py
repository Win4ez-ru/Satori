"""Fail-closed canonical provenance predicates for memory visibility.

These pure queries do not grant access, infer a person, or mutate an owner. A
``None`` scope is reserved for internal processing of any provable partition.
Public reads must supply a concrete scope before selection or provider input.
"""

from sqlalchemy import and_, exists, func, literal, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import FromClause

from satori.domain.memory_scope import MemoryScope
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow, MemoryEvidenceRow
from satori.infrastructure.persistence.models.semantic import (
    SemanticClaimEvidenceRow,
    SemanticClaimRow,
)
from satori.infrastructure.persistence.source_policy import (
    payload_admission_predicate,
    root_admission_predicate,
)


def live_message_predicate(message: FromClause) -> ColumnElement[bool]:
    """A retained header is never a source for new content or provider input."""

    return and_(
        message.c.body_state == "available",
        message.c.body_action_id.is_(None),
        message.c.content.is_not(None),
        func.length(message.c.content) > 0,
    )


def require_live_message_content(row: ConversationMessageRow) -> str:
    if (
        row.body_state != "available"
        or row.body_action_id is not None
        or (row.content is None or not row.content.strip())
    ):
        raise SourcePolicyError("dialogue_body_unavailable")
    return row.content


def admitted_interaction_predicate(
    session: Session, scope: MemoryScope | None
) -> ColumnElement[bool]:
    """Prove the canonical source and both actual episode extraction inputs."""

    interaction = ConversationInteractionRow.__table__
    message = ConversationMessageRow.__table__.alias()
    assistant = ConversationMessageRow.__table__.alias()
    canonical_root = exists(
        select(literal(1))
        .select_from(message)
        .where(
            message.c.interaction_id == interaction.c.interaction_id,
            message.c.role == "user",
            message.c.sequence == 1,
            message.c.session_id == interaction.c.session_id,
            live_message_predicate(message),
            root_admission_predicate(session, message.c.message_id),
        )
        .correlate(interaction)
    )
    return and_(
        eligible_interaction_predicate(scope),
        canonical_root,
        exists(
            select(literal(1))
            .select_from(assistant)
            .where(
                assistant.c.interaction_id == interaction.c.interaction_id,
                assistant.c.session_id == interaction.c.session_id,
                assistant.c.role == "assistant",
                assistant.c.sequence == 2,
                live_message_predicate(assistant),
            )
            .correlate(interaction)
        ),
        payload_admission_predicate(
            session, "assistant_reply", interaction.c.interaction_id, literal(1)
        ),
    )


def admitted_episode_predicate(
    session: Session, scope: MemoryScope | None, *, include_archived: bool = False
) -> ColumnElement[bool]:
    """Apply source policy before selecting any episode text or vector candidate."""

    episode = EpisodicMemoryRow.__table__
    return and_(
        proven_episode_predicate(scope) if include_archived else eligible_episode_predicate(scope),
        payload_admission_predicate(
            session, "episode", episode.c.memory_id, episode.c.aggregate_version
        ),
    )


def admitted_reflection_episode_predicate(session: Session) -> ColumnElement[bool]:
    """Apply the same source floor to the narrower reflection provenance rule."""

    episode = EpisodicMemoryRow.__table__
    return and_(
        eligible_reflection_episode_predicate(),
        payload_admission_predicate(
            session, "episode", episode.c.memory_id, episode.c.aggregate_version
        ),
    )


def admitted_semantic_claim_predicate(session: Session, scope: MemoryScope) -> ColumnElement[bool]:
    """Admit complete retained proof and the actual-input payload dependency closure."""

    claim = SemanticClaimRow.__table__
    return and_(
        eligible_semantic_claim_predicate(scope),
        payload_admission_predicate(
            session, "semantic_claim", claim.c.claim_id, claim.c.aggregate_version
        ),
    )


def _verified_session_predicate(
    session: FromClause, scope: MemoryScope | None
) -> ColumnElement[bool]:
    conditions = [
        session.c.scope_verified.is_(True),
        func.length(func.trim(session.c.identity_id)) > 0,
        func.length(func.trim(session.c.counterparty_id)) > 0,
    ]
    if scope is not None:
        conditions.extend(
            (
                session.c.identity_id == scope.identity_id,
                session.c.counterparty_id == scope.counterparty_id,
            )
        )
    return and_(*conditions)


def _eligible_interaction_predicate(
    interaction: FromClause, scope: MemoryScope | None
) -> ColumnElement[bool]:
    session = ConversationSessionRow.__table__.alias()
    message = ConversationMessageRow.__table__.alias()
    canonical_session = exists(
        select(literal(1))
        .select_from(session)
        .where(
            session.c.session_id == interaction.c.session_id,
            _verified_session_predicate(session, scope),
        )
        .correlate(interaction)
    )
    canonical_user = exists(
        select(literal(1))
        .select_from(message)
        .where(
            message.c.interaction_id == interaction.c.interaction_id,
            message.c.session_id == interaction.c.session_id,
            message.c.role == "user",
            message.c.sequence == 1,
            live_message_predicate(message),
        )
        .correlate(interaction)
    )
    return and_(interaction.c.status == "completed", canonical_session, canonical_user)


def eligible_interaction_predicate(scope: MemoryScope | None) -> ColumnElement[bool]:
    """Admit a completed canonical user interaction in a verified partition."""

    return _eligible_interaction_predicate(ConversationInteractionRow.__table__, scope)


def _episode_provenance_predicate(
    episode: FromClause, scope: MemoryScope | None, *, reflection_roots: bool = False
) -> ColumnElement[bool]:
    interaction = ConversationInteractionRow.__table__.alias()
    evidence = MemoryEvidenceRow.__table__.alias()
    message = ConversationMessageRow.__table__.alias()
    source_is_eligible = exists(
        select(literal(1))
        .select_from(interaction)
        .where(
            interaction.c.interaction_id == episode.c.source_interaction_id,
            _eligible_interaction_predicate(interaction, scope),
        )
        .correlate(episode)
    )
    root_matches_source: ColumnElement[bool]
    if reflection_roots:
        formation = ConversationInteractionRow.__table__.alias()
        root_matches_source = exists(
            select(literal(1))
            .select_from(formation)
            .where(
                formation.c.interaction_id == episode.c.source_interaction_id,
                formation.c.session_id == interaction.c.session_id,
            )
            .correlate(episode, interaction)
        )
    else:
        root_matches_source = message.c.interaction_id == episode.c.source_interaction_id
    edge_is_canonical = exists(
        select(literal(1))
        .select_from(
            message.join(interaction, message.c.interaction_id == interaction.c.interaction_id)
        )
        .where(
            message.c.message_id == evidence.c.source_message_id,
            root_matches_source,
            message.c.session_id == interaction.c.session_id,
            interaction.c.status == "completed",
            message.c.role == "user",
            message.c.sequence == 1,
            live_message_predicate(message),
            evidence.c.provenance_kind == "explicit_user_statement",
            evidence.c.body_state == "available",
            func.length(evidence.c.quote) > 0,
            func.instr(message.c.content, evidence.c.quote) > 0,
        )
        .correlate(episode, evidence)
    )
    has_evidence = exists(
        select(literal(1))
        .select_from(evidence)
        .where(evidence.c.memory_id == episode.c.memory_id)
        .correlate(episode)
    )
    has_invalid_evidence = exists(
        select(literal(1))
        .select_from(evidence)
        .where(evidence.c.memory_id == episode.c.memory_id, ~edge_is_canonical)
        .correlate(episode)
    )
    return and_(
        episode.c.body_state == "available", source_is_eligible, has_evidence, ~has_invalid_evidence
    )


def eligible_episode_predicate(scope: MemoryScope | None) -> ColumnElement[bool]:
    """Admit only active episodes whose every exact edge proves one partition."""

    episode = EpisodicMemoryRow.__table__
    return and_(episode.c.lifecycle_status == "active", proven_episode_predicate(scope))


def proven_episode_predicate(scope: MemoryScope | None) -> ColumnElement[bool]:
    """Prove attribution and every source edge independently of recall lifecycle."""

    return _episode_provenance_predicate(EpisodicMemoryRow.__table__, scope)


def eligible_reflection_episode_predicate() -> ColumnElement[bool]:
    """Prove every reflection root in the formation source's verified session.

    Reflection may cite a prior canonical user interaction in that same session
    under ADR-0025/0026. This internal global-self source admission does not widen
    ordinary episode retrieval or semantic lineage.
    """

    episode = EpisodicMemoryRow.__table__
    return and_(
        episode.c.lifecycle_status == "active",
        _episode_provenance_predicate(episode, None, reflection_roots=True),
    )


def resolve_memory_scope(session: Session, memory_id: str) -> MemoryScope | None:
    """Resolve from verified canonical roots; never use a configured-person fallback."""

    return _resolve_memory_scope(session, memory_id, active_only=True)


def resolve_proven_memory_scope(session: Session, memory_id: str) -> MemoryScope | None:
    """Prove retained support, including archives; never admit a new source with this."""

    return _resolve_memory_scope(session, memory_id, active_only=False)


def _resolve_memory_scope(
    session: Session, memory_id: str, *, active_only: bool
) -> MemoryScope | None:

    row = session.execute(
        select(ConversationSessionRow.identity_id, ConversationSessionRow.counterparty_id)
        .select_from(EpisodicMemoryRow)
        .join(
            ConversationInteractionRow,
            ConversationInteractionRow.interaction_id == EpisodicMemoryRow.source_interaction_id,
        )
        .join(
            ConversationSessionRow,
            ConversationSessionRow.session_id == ConversationInteractionRow.session_id,
        )
        .where(
            EpisodicMemoryRow.memory_id == memory_id,
            admitted_episode_predicate(session, None, include_archived=not active_only),
        )
    ).one_or_none()
    if row is None:
        return None
    try:
        return MemoryScope(identity_id=row.identity_id, counterparty_id=row.counterparty_id)
    except ValueError:
        return None


def eligible_semantic_claim_predicate(scope: MemoryScope) -> ColumnElement[bool]:
    """Require every claim edge to prove the same complete retained root chain.

    Aggregate key namespace validation is a separate pure check on the mapped
    record, before ranking or projection. SQL never registers a hash function.
    """

    claim = SemanticClaimRow.__table__
    edge = SemanticClaimEvidenceRow.__table__.alias()
    episode = EpisodicMemoryRow.__table__.alias()
    evidence = MemoryEvidenceRow.__table__.alias()
    edge_is_canonical = exists(
        select(literal(1))
        .select_from(episode.join(evidence, evidence.c.memory_id == episode.c.memory_id))
        .where(
            episode.c.memory_id == edge.c.memory_id,
            episode.c.source_interaction_id == edge.c.root_interaction_id,
            evidence.c.evidence_id == edge.c.memory_evidence_id,
            evidence.c.source_message_id == edge.c.root_message_id,
            _episode_provenance_predicate(episode, scope),
        )
        .correlate(edge)
    )
    has_evidence = exists(
        select(literal(1))
        .select_from(edge)
        .where(edge.c.claim_id == claim.c.claim_id)
        .correlate(claim)
    )
    has_invalid_evidence = exists(
        select(literal(1))
        .select_from(edge)
        .where(edge.c.claim_id == claim.c.claim_id, ~edge_is_canonical)
        .correlate(claim)
    )
    return and_(has_evidence, ~has_invalid_evidence)
