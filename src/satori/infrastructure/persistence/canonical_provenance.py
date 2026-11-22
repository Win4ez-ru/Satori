"""Floor-bound historical attribution after canonical text becomes unavailable.

These proofs are for retained metadata only. They never admit provider inputs,
new evidence, or ordinary text projections.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import Connection, inspect
from sqlalchemy.orm import Session

from satori.domain.erasure_appraisal import AppraisalBodyAction
from satori.domain.erasure_bodies import (
    BodyAction,
    BodyErasureAction,
    BodyErasureTarget,
    action_identity,
    action_lineage,
    body_actions_digest,
    source_root,
)
from satori.domain.erasure_dialogue import (
    MAX_DIALOGUE_WITNESSES,
    DialogueBodyAction,
    DialogueBodyTarget,
    HistoricalEvidenceTable,
    HistoricalEvidenceWitness,
)
from satori.domain.erasure_knowledge import (
    KnowledgeBodyAction,
    KnowledgeBodyKind,
    KnowledgeBodyTarget,
    KnowledgeWitnessOrigin,
    KnowledgeWitnessTransition,
)
from satori.domain.erasure_personality_reasons import CheckpointReasonAction
from satori.domain.erasure_reflection import ReflectionBodyAction
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.erasure_body_codec import decode_body_action

if TYPE_CHECKING:
    from satori.infrastructure.persistence.erasure_bodies import MaterialIndex
    from satori.infrastructure.persistence.knowledge_material import KnowledgeMaterialIndex

type Row = dict[str, object]
type Store = dict[str, tuple[Row, ...]]

_MAX_ACTIONS = 4096
_MAX_RECEIPT_BYTES = 64 * 1024 * 1024
_OWNER_TABLES = {
    HistoricalEvidenceTable.MEMORY: ("episodic_memories", "memory_id"),
    HistoricalEvidenceTable.USER_MODEL: ("user_model_claims", "claim_id"),
    HistoricalEvidenceTable.WORLD_MODEL: ("world_model_claims", "claim_id"),
    HistoricalEvidenceTable.POSITION: ("satori_positions", "position_id"),
    HistoricalEvidenceTable.INCLINATION: ("satori_inclinations", "inclination_id"),
}


def canonical_row_digest(row: Row) -> str:
    """Fingerprint the entire exact retained physical row, not selected familiar fields."""
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def dialogue_message_material(row: Row, *, terminal: bool = False) -> Row:
    """The action reference is verified separately; all other columns remain exact."""
    material = dict(row)
    material["body_action_id"] = None
    if terminal:
        material["content"] = None
        material["body_state"] = "erased"
    return material


def dialogue_message_digest(row: Row, *, terminal: bool = False) -> str:
    return canonical_row_digest(dialogue_message_material(row, terminal=terminal))


def _index(store: Store, table: str, key: str) -> dict[str, Row]:
    return {str(row[key]): row for row in store.get(table, ())}


class _Indexes:
    """One bounded census index; accepted edges do not repeatedly scan unrelated rows."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.rows: dict[tuple[str, str], dict[str, Row]] = {}
        self.episodes: dict[str, bool] = {}
        self.episode_material: MaterialIndex | None = None
        self.episode_targets: (
            dict[str, list[tuple[BodyErasureAction, BodyErasureTarget]]] | None
        ) = None
        self.knowledge_material: KnowledgeMaterialIndex | None = None

    def get(self, table: str, key: str) -> dict[str, Row]:
        pair = table, key
        if pair not in self.rows:
            self.rows[pair] = _index(self.store, table, key)
        return self.rows[pair]


@dataclass(frozen=True, slots=True)
class _EvidenceProof:
    root: CanonicalRootRef
    action_id: str
    origin: KnowledgeWitnessOrigin
    before_digest: str
    after_digest: str
    owner_id: str | None = None


def _approved_evidence(
    actions: tuple[BodyAction, ...],
) -> dict[tuple[HistoricalEvidenceTable, str], _EvidenceProof]:
    """Validate ordered proof succession without treating floor metadata as live evidence."""
    proofs: dict[tuple[HistoricalEvidenceTable, str], _EvidenceProof] = {}
    prior_digests: dict[tuple[HistoricalEvidenceTable, str], set[str]] = {}
    for action in actions:
        if isinstance(action, DialogueBodyAction):
            for target in action.proposal.targets:
                if target.message_id != action.proposal.root.message_id:
                    continue
                for witness in target.witnesses:
                    key = witness.table, witness.evidence_id
                    previous = proofs.get(key)
                    if previous is not None and previous.root != action.proposal.root:
                        raise SourcePolicyError("knowledge_witness_unproven")
                    if previous is not None and previous.origin is KnowledgeWitnessOrigin.KNOWLEDGE:
                        # Canonical anchors and owner transitions are separate:
                        # later dialogue capture must never rewind the owner proof.
                        if witness.digest not in prior_digests[key]:
                            raise SourcePolicyError("knowledge_witness_unproven")
                        continue
                    prior_digests.setdefault(key, set()).add(witness.digest)
                    proofs[key] = _EvidenceProof(
                        action.proposal.root,
                        action.action_id,
                        KnowledgeWitnessOrigin.DIALOGUE,
                        witness.digest,
                        witness.digest,
                    )
        elif isinstance(action, KnowledgeBodyAction):
            for knowledge_target in action.proposal.targets:
                for transition in knowledge_target.witness_transitions:
                    key = transition.table, transition.evidence_id
                    previous = proofs.get(key)
                    if transition.origin is KnowledgeWitnessOrigin.LIVE:
                        if previous is not None:
                            raise SourcePolicyError("knowledge_witness_unproven")
                    elif (
                        previous is None
                        or transition.root != previous.root
                        or transition.predecessor_action_id != previous.action_id
                        or transition.origin is not previous.origin
                        or transition.before_digest
                        not in {previous.before_digest, previous.after_digest}
                    ):
                        raise SourcePolicyError("knowledge_witness_unproven")
                    proofs[key] = _EvidenceProof(
                        transition.root,
                        action.action_id,
                        KnowledgeWitnessOrigin.KNOWLEDGE,
                        transition.before_digest,
                        transition.after_digest,
                        knowledge_target.aggregate_id,
                    )
                    prior_digests.setdefault(key, set()).update(
                        {transition.before_digest, transition.after_digest}
                    )
    return proofs


def _canonical_root(
    store: Store, message_id: str, *, lineage_id: str, indexes: _Indexes | None = None
) -> CanonicalRootRef | None:
    indexed = indexes or _Indexes(store)
    message = indexed.get("conversation_messages", "message_id").get(message_id)
    if message is None or message.get("role") != "user" or message.get("sequence") != 1:
        return None
    interaction = indexed.get("conversation_interactions", "interaction_id").get(
        str(message.get("interaction_id"))
    )
    scoped = indexed.get("conversation_sessions", "session_id").get(str(message.get("session_id")))
    if (
        interaction is None
        or scoped is None
        or interaction.get("session_id") != scoped.get("session_id")
        or interaction.get("status") != "completed"
        or scoped.get("scope_verified") != 1
    ):
        return None
    try:
        return CanonicalRootRef(
            lineage_id,
            str(scoped["identity_id"]),
            str(scoped["counterparty_id"]),
            str(scoped["session_id"]),
            str(interaction["interaction_id"]),
            str(message["message_id"]),
        )
    except (ValueError, KeyError):
        return None


def _episode_terminal_proven(
    store: Store, memory_id: str, actions: tuple[BodyErasureAction, ...], indexes: _Indexes
) -> bool:
    # The existing episode owner already binds every retained edge and its NULL state.
    # No old quote is reconstructed or inferred from a stored hash.
    from satori.infrastructure.persistence.erasure_bodies import MaterialIndex, _hash, _material

    if indexes.episode_targets is None:
        indexes.episode_targets = {}
        for action in actions:
            for target in action.proposal.targets:
                indexes.episode_targets.setdefault(target.memory_id, []).append((action, target))
    selected = indexes.episode_targets.get(memory_id, [])
    if not selected:
        return False
    selected.sort(key=lambda pair: pair[0].result_body_revision)
    _latest_action, target = selected[-1]
    if indexes.episode_material is None:
        indexes.episode_material = MaterialIndex.build(store)
    index = indexes.episode_material
    memory = index.memories.get(memory_id)
    if memory is None or memory.get("body_state") != "erased" or memory.get("summary") is not None:
        return False
    edges = index.evidence.get(memory_id, ())
    if not edges or any(
        edge.get("body_state") != "erased"
        or edge.get("quote") is not None
        or edge.get("body_action_id") != memory.get("body_action_id")
        for edge in edges
    ):
        return False
    if not any(
        action.action_id == memory.get("body_action_id")
        and approved.terminal_digest == target.terminal_digest
        for action, approved in selected
    ):
        return False
    return _hash(_material(index, memory_id, terminal=False)) == target.terminal_digest


def _accepted_evidence(
    store: Store,
    table: HistoricalEvidenceTable,
    row: Row,
    root: CanonicalRootRef,
    content: str,
    episode_actions: tuple[BodyErasureAction, ...],
    indexes: _Indexes,
    historical: HistoricalProvenance | None = None,
) -> bool:
    owner_table, owner_key = _OWNER_TABLES[table]
    owner = indexes.get(owner_table, owner_key).get(str(row.get(owner_key)))
    if owner is None or row.get("source_message_id") != root.message_id:
        return False
    if "identity_id" in owner and owner["identity_id"] != root.identity_id:
        return False
    if "source_interaction_id" in row and row["source_interaction_id"] != root.interaction_id:
        return False
    if "source_counterparty_id" in row and row["source_counterparty_id"] != root.counterparty_id:
        return False
    if "source_session_id" in row and row["source_session_id"] != root.session_id:
        return False
    if table in {HistoricalEvidenceTable.USER_MODEL, HistoricalEvidenceTable.WORLD_MODEL}:
        return owner.get("counterparty_id") == root.counterparty_id
    if table is HistoricalEvidenceTable.POSITION and row.get("quote") is None:
        return bool(
            historical is not None
            and historical.admits_evidence(table, row)
            and historical.admits_terminal_owner(KnowledgeBodyKind.POSITION.value, owner)
        )
    if table is HistoricalEvidenceTable.MEMORY:
        source = indexes.get("conversation_interactions", "interaction_id").get(
            str(owner.get("source_interaction_id"))
        )
        if (
            source is None
            or source.get("session_id") != root.session_id
            or source.get("status") != "completed"
            or row.get("provenance_kind") != "explicit_user_statement"
        ):
            return False
        if row.get("body_state") == "erased":
            memory_id = str(owner["memory_id"])
            if memory_id not in indexes.episodes:
                indexes.episodes[memory_id] = _episode_terminal_proven(
                    store, memory_id, episode_actions, indexes
                )
            return indexes.episodes[memory_id]
    if table is HistoricalEvidenceTable.INCLINATION:
        source = indexes.get("reflection_sources", "source_id").get(
            str(row.get("reflection_source_id"))
        )
        if source is None or (
            source.get("root_message_id"),
            source.get("root_interaction_id"),
            source.get("root_counterparty_id"),
            source.get("content_hash"),
        ) != (root.message_id, root.interaction_id, root.counterparty_id, row.get("content_hash")):
            return False
        upstream_table = {
            "position_evidence": HistoricalEvidenceTable.POSITION,
            "episodic_memory_evidence": HistoricalEvidenceTable.MEMORY,
        }.get(str(source.get("kind")))
        if upstream_table is None:
            return False
        upstream = indexes.get(upstream_table.value, "evidence_id").get(
            str(source.get("evidence_edge_id"))
        )
        if upstream is None or not _accepted_evidence(
            store, upstream_table, upstream, root, content, episode_actions, indexes, historical
        ):
            return False
        quote = upstream.get("quote")
        return quote is None or (
            isinstance(quote, str)
            and hashlib.sha256(quote.encode()).hexdigest() == row.get("content_hash")
        )
    quote = row.get("quote")
    return isinstance(quote, str) and bool(quote) and quote in content


def capture_historical_witnesses(
    store: Store,
    root: CanonicalRootRef,
    *,
    approved_episode_actions: tuple[BodyErasureAction, ...] = (),
    approved_actions: tuple[BodyAction, ...] = (),
    snapshot: SourceEligibilitySnapshot | None = None,
) -> tuple[HistoricalEvidenceWitness, ...]:
    """Capture exact accepted rows while the canonical body can still prove membership."""
    indexes = _Indexes(store)
    if approved_actions and snapshot is None:
        raise SourcePolicyError("dialogue_provenance_unproven")
    historical = (
        historical_from_approved_actions(store, snapshot, approved_actions)
        if snapshot is not None
        else None
    )
    if _canonical_root(store, root.message_id, lineage_id=root.lineage_id, indexes=indexes) != root:
        raise SourcePolicyError("dialogue_provenance_unproven")
    message = indexes.get("conversation_messages", "message_id")[root.message_id]
    content = message.get("content")
    if (
        not isinstance(content, str)
        or not content.strip()
        or message.get("body_state", "available") != "available"
        or message.get("body_action_id") is not None
    ):
        raise SourcePolicyError("dialogue_provenance_unproven")
    candidates = tuple(
        (table, row)
        for table in HistoricalEvidenceTable
        for row in store.get(table.value, ())
        if row.get("source_message_id") == root.message_id
    )
    if len(candidates) > MAX_DIALOGUE_WITNESSES:
        raise SourcePolicyError("dialogue_witness_capacity_exceeded")
    witnesses = []
    try:
        for table, row in candidates:
            if not _accepted_evidence(
                store, table, row, root, content, approved_episode_actions, indexes, historical
            ):
                raise SourcePolicyError("dialogue_provenance_unproven")
            witnesses.append(
                HistoricalEvidenceWitness(table, str(row["evidence_id"]), canonical_row_digest(row))
            )
    except (ValueError, TypeError, KeyError, RecursionError):
        raise SourcePolicyError("dialogue_provenance_unproven") from None
    witnesses.sort(key=lambda item: (item.table.value, item.evidence_id))
    if len({(item.table, item.evidence_id) for item in witnesses}) != len(witnesses):
        raise SourcePolicyError("dialogue_provenance_unproven")
    return tuple(witnesses)


def capture_knowledge_transitions(
    store: Store,
    root: CanonicalRootRef,
    targets: tuple[tuple[KnowledgeBodyKind, str], ...],
    *,
    snapshot: SourceEligibilitySnapshot,
    approved_actions: tuple[BodyAction, ...] = (),
) -> tuple[KnowledgeWitnessTransition, ...]:
    """Approve exact position-edge changes; no quote is reconstructed from a NULL source."""
    historical = historical_from_approved_actions(store, snapshot, approved_actions)
    indexes = _Indexes(store)
    if (
        _canonical_root(store, root.message_id, lineage_id=snapshot.lineage_id, indexes=indexes)
        != root
    ):
        raise SourcePolicyError("knowledge_witness_unproven")
    selected = {
        aggregate_id for kind, aggregate_id in targets if kind is KnowledgeBodyKind.POSITION
    }
    candidates = tuple(
        row
        for row in store.get(HistoricalEvidenceTable.POSITION.value, ())
        if row.get("position_id") in selected
    )
    if selected != {str(row["position_id"]) for row in candidates}:
        raise SourcePolicyError("knowledge_witness_unproven")
    if len(candidates) > MAX_DIALOGUE_WITNESSES:
        raise SourcePolicyError("knowledge_witness_capacity_exceeded")
    transitions: list[KnowledgeWitnessTransition] = []
    for row in candidates:
        source = _canonical_root(
            store,
            str(row.get("source_message_id")),
            lineage_id=snapshot.lineage_id,
            indexes=indexes,
        )
        if source is None or (source.identity_id, source.counterparty_id, source.lineage_id) != (
            root.identity_id,
            root.counterparty_id,
            root.lineage_id,
        ):
            raise SourcePolicyError("knowledge_witness_unproven")
        message = indexes.get("conversation_messages", "message_id")[source.message_id]
        content = message.get("content")
        live = (
            isinstance(content, str)
            and bool(content.strip())
            and message.get("body_state", "available") == "available"
            and message.get("body_action_id") is None
        )
        if live:
            if not _accepted_evidence(
                store,
                HistoricalEvidenceTable.POSITION,
                row,
                source,
                str(content),
                (),
                indexes,
                historical,
            ):
                raise SourcePolicyError("knowledge_witness_unproven")
        elif not historical.admits_root(source.message_id) or not historical.admits_evidence(
            HistoricalEvidenceTable.POSITION, row
        ):
            raise SourcePolicyError("knowledge_witness_unproven")
        before = canonical_row_digest(row)
        previous = historical.evidence_proofs.get(
            (HistoricalEvidenceTable.POSITION, str(row["evidence_id"]))
        )
        if previous is None:
            if not live:
                raise SourcePolicyError("knowledge_witness_unproven")
            origin, predecessor = KnowledgeWitnessOrigin.LIVE, None
        else:
            if source != previous.root or before not in {
                previous.before_digest,
                previous.after_digest,
            }:
                raise SourcePolicyError("knowledge_witness_unproven")
            origin, predecessor = previous.origin, previous.action_id
        terminal = dict(row, quote=None, normalized_signature=None)
        transitions.append(
            KnowledgeWitnessTransition(
                HistoricalEvidenceTable.POSITION,
                str(row["evidence_id"]),
                source,
                before,
                canonical_row_digest(terminal),
                origin,
                predecessor,
            )
        )
    transitions.sort(key=lambda item: (item.table.value, item.evidence_id))
    return tuple(transitions)


@dataclass(frozen=True, slots=True)
class HistoricalProvenance:
    """An explicit purpose-limited index backed by the trusted complete action head."""

    roots: frozenset[str]
    witnesses: dict[tuple[HistoricalEvidenceTable, str], tuple[str, str]]
    terminal_owners: dict[tuple[str, str], str] = field(default_factory=dict)
    evidence_proofs: dict[tuple[HistoricalEvidenceTable, str], _EvidenceProof] = field(
        default_factory=dict
    )

    def admits_root(self, message_id: str) -> bool:
        return message_id in self.roots

    def admits_evidence(self, table: HistoricalEvidenceTable, row: Row) -> bool:
        expected = self.witnesses.get((table, str(row.get("evidence_id"))))
        if expected is None or row.get("source_message_id") != expected[0]:
            return False
        try:
            return canonical_row_digest(row) == expected[1]
        except (ValueError, TypeError):
            return False

    def admits_terminal_owner(self, kind: str, row: Row) -> bool:
        owner_key = {
            "semantic_claim": "claim_id",
            "user_model_claim": "claim_id",
            "world_model_claim": "claim_id",
            "position": "position_id",
            "inclination": "inclination_id",
            "reflection_proposal": "proposal_id",
        }.get(kind)
        if owner_key is None or row.get("body_state") != "erased":
            return False
        expected = self.terminal_owners.get((kind, str(row.get(owner_key))))
        try:
            return expected is not None and canonical_row_digest(row) == expected
        except (ValueError, TypeError):
            return False


def _terminal_knowledge_owners(
    store: Store, actions: tuple[KnowledgeBodyAction, ...]
) -> dict[tuple[str, str], str]:
    if not actions:
        return {}
    owners = {
        KnowledgeBodyKind.SEMANTIC_CLAIM: ("semantic_claims", "claim_id"),
        KnowledgeBodyKind.USER_MODEL_CLAIM: ("user_model_claims", "claim_id"),
        KnowledgeBodyKind.WORLD_MODEL_CLAIM: ("world_model_claims", "claim_id"),
        KnowledgeBodyKind.POSITION: ("satori_positions", "position_id"),
        KnowledgeBodyKind.INCLINATION: ("satori_inclinations", "inclination_id"),
    }
    if not any(store.get(table) for table, _key in owners.values()):
        return {}
    from satori.infrastructure.persistence.knowledge_material import (
        KnowledgeMaterialIndex,
        knowledge_target_terminal_proven,
    )

    indexed = _Indexes(store)
    material = KnowledgeMaterialIndex.build(store)
    latest: dict[tuple[KnowledgeBodyKind, str], KnowledgeBodyTarget] = {}
    approved: dict[
        tuple[str, KnowledgeBodyKind, str], tuple[KnowledgeBodyAction, KnowledgeBodyTarget]
    ] = {}
    for action in actions:
        for target in action.proposal.targets:
            latest[(target.kind, target.aggregate_id)] = target
            approved[(action.action_id, target.kind, target.aggregate_id)] = action, target
    proven: dict[tuple[str, str], str] = {}
    for (kind, aggregate_id), target in latest.items():
        table, key = owners[kind]
        row = indexed.get(table, key).get(aggregate_id)
        if row is None:
            continue
        bound = approved.get((str(row.get("body_action_id")), kind, aggregate_id))
        if (
            bound is not None
            and bound[1].terminal_digest == target.terminal_digest
            and knowledge_target_terminal_proven(store, bound[0], bound[1], index=material)
        ):
            proven[(kind.value, aggregate_id)] = canonical_row_digest(row)
    return proven


def _terminal_reflection_owners(
    store: Store,
    actions: tuple[ReflectionBodyAction, ...],
) -> dict[tuple[str, str], str]:
    """Exact frozen Reflection skeletons grant historical attribution only."""
    if not actions:
        return {}
    from satori.infrastructure.persistence.reflection_material import (
        ReflectionMaterialIndex,
        reflection_target_terminal_proven,
    )

    material = ReflectionMaterialIndex.build(store)
    latest = {t.proposal_id: t for a in actions for t in a.proposal.targets}
    approved = {(a.action_id, t.proposal_id): (a, t) for a in actions for t in a.proposal.targets}
    result: dict[tuple[str, str], str] = {}
    for key, target in latest.items():
        row = material.proposals.get(key)
        bound = approved.get((str(row.get("body_action_id")) if row else "", key))
        if (
            row is not None
            and bound is not None
            and bound[1].terminal_digest == target.terminal_digest
            and reflection_target_terminal_proven(material, *bound)
        ):
            result[("reflection_proposal", key)] = canonical_row_digest(row)
    return result


def verified_historical_provenance(
    store: Store,
    snapshot: SourceEligibilitySnapshot,
    dialogue_actions: tuple[DialogueBodyAction, ...],
    *,
    approved_knowledge_actions: tuple[KnowledgeBodyAction, ...] = (),
    approved_reflection_actions: tuple[ReflectionBodyAction, ...] = (),
) -> HistoricalProvenance:
    """Index caller-supplied floor-approved dialogue actions, requiring actual terminal rows."""
    indexes = _Indexes(store)
    messages = indexes.get("conversation_messages", "message_id")
    latest: dict[str, tuple[DialogueBodyAction, DialogueBodyTarget]] = {}
    approved: dict[tuple[str, str], DialogueBodyTarget] = {}
    for action in sorted(dialogue_actions, key=lambda item: item.result_body_revision):
        if (
            action.floor_id,
            action.installation_id,
            action.proposal.root.identity_id,
            action.proposal.root.lineage_id,
        ) != (
            snapshot.floor_id,
            snapshot.installation_id,
            snapshot.identity_id,
            snapshot.lineage_id,
        ) or action.result_body_revision > snapshot.body_revision:
            raise SourcePolicyError("dialogue_provenance_unproven")
        for target in action.proposal.targets:
            latest[target.message_id] = action, target
            approved[(action.action_id, target.message_id)] = target
    roots: set[str] = set()
    witnesses: dict[tuple[HistoricalEvidenceTable, str], tuple[str, str]] = {}
    for message_id, (action, target) in latest.items():
        if message_id != action.proposal.root.message_id:
            continue
        row = messages.get(message_id)
        if (
            row is None
            or _canonical_root(store, message_id, lineage_id=snapshot.lineage_id, indexes=indexes)
            != action.proposal.root
        ):
            continue
        bound = approved.get((str(row.get("body_action_id")), message_id))
        if (
            row.get("body_state") != "erased"
            or row.get("content") is not None
            or row.get("schema_version") != target.schema_version
            or bound is None
            or bound.terminal_digest != target.terminal_digest
            or bound.witnesses != target.witnesses
            or dialogue_message_digest(row) != target.terminal_digest
        ):
            continue
        roots.add(message_id)
        for witness in target.witnesses:
            witnesses[(witness.table, witness.evidence_id)] = message_id, witness.digest
    knowledge = _terminal_knowledge_owners(store, approved_knowledge_actions)
    knowledge.update(_terminal_reflection_owners(store, approved_reflection_actions))
    actions: tuple[BodyAction, ...] = tuple(
        sorted(
            (*dialogue_actions, *approved_knowledge_actions),
            key=lambda item: item.result_body_revision,
        )
    )
    proofs = _approved_evidence(actions)
    for key, proof in proofs.items():
        if proof.origin is not KnowledgeWitnessOrigin.KNOWLEDGE:
            continue
        row = indexes.get(key[0].value, "evidence_id").get(key[1])
        if (
            row is None
            or _canonical_root(
                store, proof.root.message_id, lineage_id=snapshot.lineage_id, indexes=indexes
            )
            != proof.root
        ):
            continue
        if (
            proof.owner_id is not None
            and (KnowledgeBodyKind.POSITION.value, proof.owner_id) in knowledge
            and canonical_row_digest(row) == proof.after_digest
        ):
            witnesses[key] = proof.root.message_id, proof.after_digest
        # A pending action does not revoke an exact original dialogue witness.
        # It also cannot turn an unapproved changed row into historical support.
    return HistoricalProvenance(frozenset(roots), witnesses, knowledge, proofs)


def _validate_action_head(
    actions: tuple[BodyAction, ...], snapshot: SourceEligibilitySnapshot
) -> None:
    try:
        if len(actions) != snapshot.body_revision or len(actions) > _MAX_ACTIONS:
            raise ValueError("invalid_action_count")
        action_ids: set[str] = set()
        request_ids: set[str] = set()
        for revision, action in enumerate(actions, 1):
            if not isinstance(
                action,
                BodyErasureAction
                | DialogueBodyAction
                | KnowledgeBodyAction
                | ReflectionBodyAction
                | AppraisalBodyAction
                | CheckpointReasonAction,
            ):
                raise ValueError("invalid_action_type")
            if (
                action.result_body_revision,
                action.floor_id,
                action.installation_id,
                action_identity(action),
                action_lineage(action),
            ) != (
                revision,
                snapshot.floor_id,
                snapshot.installation_id,
                snapshot.identity_id,
                snapshot.lineage_id,
            ):
                raise ValueError("invalid_action_binding")
            if action.action_id in action_ids or action.proposal.request_id in request_ids:
                raise ValueError("duplicate_action")
            root = source_root(action)
            if (
                action.proposal.expected_source_generation > snapshot.generation
                or action.proposal.expected_erasure_revision > snapshot.erasure_revision
                or (root is not None and root.message_id not in snapshot.excluded_message_ids)
            ):
                raise ValueError("invalid_action_source_binding")
            action_ids.add(action.action_id)
            request_ids.add(action.proposal.request_id)
        if actions:
            if body_actions_digest(actions) != snapshot.body_head_digest:
                raise ValueError("invalid_action_head")
        elif snapshot.body_head_digest:
            raise ValueError("invalid_action_head")
    except (ValueError, TypeError, KeyError, RecursionError):
        raise SourcePolicyError("dialogue_provenance_unproven") from None


def historical_from_approved_actions(
    store: Store, snapshot: SourceEligibilitySnapshot, actions: tuple[BodyAction, ...]
) -> HistoricalProvenance:
    """Use the complete trusted floor head for historical-only operator assessment.

    Pending materialization of a different target does not revoke an exact terminal
    root proof. This is not ordinary body admission or a materialization ACK.
    """
    _validate_action_head(actions, snapshot)
    return verified_historical_provenance(
        store,
        snapshot,
        tuple(a for a in actions if isinstance(a, DialogueBodyAction)),
        approved_knowledge_actions=tuple(a for a in actions if isinstance(a, KnowledgeBodyAction)),
        approved_reflection_actions=tuple(
            a for a in actions if isinstance(a, ReflectionBodyAction)
        ),
    )


def _historical_needed(store: Store, identity_id: str) -> bool:
    for table in (
        "semantic_claims",
        "user_model_claims",
        "world_model_claims",
        "satori_positions",
        "satori_inclinations",
        "reflection_proposals",
    ):
        for row in store.get(table, ()):
            if row.get("identity_id", identity_id) != identity_id:
                continue
            if (
                row.get("body_state", "available") != "available"
                or row.get("body_action_id") is not None
            ):
                return True
    sessions = _index(store, "conversation_sessions", "session_id")
    for message in store.get("conversation_messages", ()):
        if message.get("role") != "user":
            continue
        scoped = sessions.get(str(message.get("session_id")))
        if (
            scoped is not None
            and scoped.get("identity_id") is not None
            and scoped.get("identity_id") != identity_id
        ):
            continue
        content = message.get("content")
        if (
            scoped is None
            or not isinstance(content, str)
            or not content.strip()
            or message.get("body_state", "available") != "available"
            or message.get("body_action_id") is not None
        ):
            return True
    return False


def historical_from_store(
    store: Store, snapshot: SourceEligibilitySnapshot
) -> HistoricalProvenance:
    """Require exact whole-main head when historical attribution is actually needed."""
    if not _historical_needed(store, snapshot.identity_id):
        # An empty historical context grants no root, evidence or operational admission.
        return HistoricalProvenance(frozenset(), {})
    try:
        rows = sorted(
            (
                row
                for row in store.get("erasure_body_applications", ())
                if row.get("identity_id") == snapshot.identity_id
            ),
            key=lambda row: int(str(row["body_revision"])),
        )
    except (ValueError, TypeError, KeyError):
        raise SourcePolicyError("dialogue_provenance_unproven") from None
    if len(rows) != snapshot.body_revision or len(rows) > _MAX_ACTIONS:
        raise SourcePolicyError("dialogue_provenance_unproven")
    if not snapshot.body_revision:
        return HistoricalProvenance(frozenset(), {})
    actions: list[BodyAction] = []
    try:
        for revision, row in enumerate(rows, 1):
            raw = row["action"]
            value = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(value, dict):
                raise ValueError("invalid_action")
            action = decode_body_action(value)
            root = source_root(action)
            if (
                row["action_id"],
                row["request_id"],
                row["root_message_id"],
                row["floor_id"],
                row["lineage_id"],
                row["body_revision"],
                action.result_body_revision,
                action.installation_id,
                action_identity(action),
            ) != (
                action.action_id,
                action.proposal.request_id,
                root.message_id if root is not None else None,
                snapshot.floor_id,
                snapshot.lineage_id,
                revision,
                revision,
                snapshot.installation_id,
                snapshot.identity_id,
            ):
                raise ValueError("invalid_action_binding")
            actions.append(action)
        approved_actions = tuple(actions)
        return historical_from_approved_actions(store, snapshot, approved_actions)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise SourcePolicyError("dialogue_provenance_unproven") from None


class _ReadBudget(Protocol):
    def add(self, *, rows: int, logical_bytes: int = 0) -> None: ...


@dataclass(slots=True)
class _WitnessBudget:
    rows: int = 0
    logical_bytes: int = 0

    def add(self, *, rows: int, logical_bytes: int = 0) -> None:
        self.rows += rows
        self.logical_bytes += logical_bytes
        if self.rows > 100_000 or self.logical_bytes > _MAX_RECEIPT_BYTES:
            raise SourcePolicyError("dialogue_witness_capacity_exceeded")


def _knowledge_witness_branches(
    connection: Connection,
    actions: tuple[BodyAction, ...],
    proofs: dict[tuple[HistoricalEvidenceTable, str], _EvidenceProof],
    snapshot: SourceEligibilitySnapshot,
    budget: _ReadBudget,
    *,
    require_terminal: bool,
) -> dict[tuple[HistoricalEvidenceTable, str], tuple[str, bool]]:
    """Choose one exact whole-owner state before loading any effective witness body."""
    if not proofs:
        return {}
    from satori.infrastructure.persistence.knowledge_material import (
        KnowledgeMaterialIndex,
        knowledge_target_terminal_proven,
    )

    owners = {str(proof.owner_id) for proof in proofs.values()}
    latest = {
        target.aggregate_id: (action, target)
        for action in actions
        if isinstance(action, KnowledgeBodyAction)
        for target in action.proposal.targets
        if target.kind is KnowledgeBodyKind.POSITION and target.aggregate_id in owners
    }
    if set(latest) != owners:
        raise SourcePolicyError("dialogue_provenance_unproven")
    columns = {
        str(row[1]) for row in connection.exec_driver_sql('PRAGMA table_info("satori_positions")')
    }
    if not {"body_state", "body_action_id", "proposition", "normalized_proposition"} <= columns:
        raise SourcePolicyError("dialogue_provenance_unproven")
    headers: dict[str, Row] = {}
    ids = tuple(sorted(owners))
    for offset in range(0, len(ids), 500):
        chunk = ids[offset : offset + 500]
        marks = ",".join("?" for _ in chunk)
        rows = tuple(
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT position_id,identity_id,aggregate_version,body_state,body_action_id,"
                "proposition IS NULL AS proposition_null,"
                "normalized_proposition IS NULL AS normalized_null "
                f"FROM satori_positions WHERE position_id IN ({marks})",
                chunk,
            ).mappings()
        )
        if len(rows) != len(chunk):
            raise SourcePolicyError("dialogue_provenance_unproven")
        budget.add(rows=len(rows), logical_bytes=sum(len(str(row)) for row in rows))
        headers.update((str(row["position_id"]), row) for row in rows)
    terminal: set[str] = set()
    original: set[str] = set()
    for key, header in headers.items():
        _action, target = latest[key]
        if (
            header["identity_id"] != snapshot.identity_id
            or header["aggregate_version"] != target.aggregate_version
        ):
            raise SourcePolicyError("dialogue_provenance_unproven")
        if (
            header["body_state"] == "erased"
            and header["body_action_id"] is not None
            and header["proposition_null"] == 1
            and header["normalized_null"] == 1
        ):
            terminal.add(key)
        elif (
            not require_terminal
            and header["body_state"] == "available"
            and header["body_action_id"] is None
            and header["proposition_null"] == 0
            and header["normalized_null"] == 0
        ):
            original.add(key)
        else:
            raise SourcePolicyError("dialogue_provenance_unproven")
    # A copied floor/ACK cannot expose retained quote bytes. Census the entire
    # selected parent rowset first, then constrain every materialized body read.
    store: Store = {}
    for table in ("satori_positions", "satori_position_evidence", "satori_position_revisions"):
        table_columns = tuple(
            str(row[1]) for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")')
        )
        if "position_id" not in table_columns:
            raise SourcePolicyError("dialogue_provenance_unproven")
        sizes = "+".join(
            'coalesce(length(CAST("' + name.replace('"', '""') + '" AS BLOB)),0)'
            for name in table_columns
        )
        records: list[Row] = []
        for erased, selected in ((True, terminal), (False, original)):
            ordered = tuple(sorted(selected))
            for offset in range(0, len(ordered), 500):
                chunk = ordered[offset : offset + 500]
                marks = ",".join("?" for _ in chunk)
                base = f"position_id IN ({marks})"
                predicate = base
                if table == "satori_positions":
                    predicate += (
                        " AND body_state='erased' AND body_action_id IS NOT NULL"
                        " AND proposition IS NULL AND normalized_proposition IS NULL"
                        if erased
                        else " AND body_state='available' AND body_action_id IS NULL"
                        " AND proposition IS NOT NULL AND normalized_proposition IS NOT NULL"
                    )
                elif table == "satori_position_evidence":
                    nullity = "NULL" if erased else "NOT NULL"
                    predicate += f" AND quote IS {nullity} AND normalized_signature IS {nullity}"
                total = int(
                    connection.exec_driver_sql(
                        f'SELECT count(*) FROM "{table}" WHERE {base}', chunk
                    ).scalar_one()
                )
                count, size = connection.exec_driver_sql(
                    f'SELECT count(*),coalesce(sum({sizes}),0) FROM "{table}" WHERE {predicate}',
                    chunk,
                ).one()
                if int(count) != total or (table == "satori_positions" and total != len(chunk)):
                    raise SourcePolicyError("dialogue_provenance_unproven")
                budget.add(rows=total, logical_bytes=int(size))
                records.extend(
                    dict(row)
                    for row in connection.exec_driver_sql(
                        f'SELECT * FROM "{table}" WHERE {predicate}', chunk
                    ).mappings()
                )
        store[table] = tuple(records)
    index = KnowledgeMaterialIndex.build(store)
    approved = {
        (action.action_id, target.aggregate_id, target.terminal_digest): (action, target)
        for action in actions
        if isinstance(action, KnowledgeBodyAction)
        for target in action.proposal.targets
        if target.kind is KnowledgeBodyKind.POSITION
    }
    for key, (_action, target) in latest.items():
        if key in terminal:
            candidate = approved.get(
                (str(headers[key]["body_action_id"]), key, target.terminal_digest)
            )
            if candidate is None or not knowledge_target_terminal_proven(
                {}, *candidate, index=index
            ):
                raise SourcePolicyError("dialogue_provenance_unproven")
        elif (
            index.digest(KnowledgeBodyKind.POSITION, key, terminal=False) != target.body_digest
            or index.digest(KnowledgeBodyKind.POSITION, key, terminal=True)
            != target.terminal_digest
        ):
            raise SourcePolicyError("dialogue_provenance_unproven")
    return {
        pair: (
            proof.after_digest if proof.owner_id in terminal else proof.before_digest,
            proof.owner_id in terminal,
        )
        for pair, proof in proofs.items()
    }


def validate_historical_witnesses(
    connection: Connection,
    actions: tuple[BodyAction, ...],
    *,
    snapshot: SourceEligibilitySnapshot,
    budget: _ReadBudget | None = None,
    message_ids: frozenset[str] | None = None,
) -> None:
    """Reprove exact retained rows using fixed tables and bounded reads, never new membership."""
    if not snapshot.body_revision and not actions:
        return
    _validate_action_head(actions, snapshot)
    proofs = _approved_evidence(actions)
    latest: dict[str, DialogueBodyTarget] = {}
    for action in actions:
        if isinstance(action, DialogueBodyAction):
            for target in action.proposal.targets:
                latest[target.message_id] = target
    selected: dict[HistoricalEvidenceTable, dict[str, str]] = {}
    for message_id, target in latest.items():
        if message_ids is not None and message_id not in message_ids:
            continue
        for witness in target.witnesses:
            by_id = selected.setdefault(witness.table, {})
            if witness.evidence_id in by_id and by_id[witness.evidence_id] != witness.digest:
                raise SourcePolicyError("dialogue_provenance_unproven")
            by_id[witness.evidence_id] = witness.digest
    knowledge_proofs: dict[tuple[HistoricalEvidenceTable, str], _EvidenceProof] = {}
    for (table, evidence_id), proof in proofs.items():
        if proof.origin is not KnowledgeWitnessOrigin.KNOWLEDGE:
            continue
        if message_ids is None or proof.root.message_id in message_ids:
            knowledge_proofs[table, evidence_id] = proof
    read_budget = budget if budget is not None else _WitnessBudget()
    branches = _knowledge_witness_branches(
        connection,
        actions,
        knowledge_proofs,
        snapshot,
        read_budget,
        require_terminal=message_ids is None,
    )
    for (table, evidence_id), (digest, _erased) in branches.items():
        selected.setdefault(table, {})[evidence_id] = digest
    for table, expected in selected.items():
        # Table identifiers come only from the closed domain enum. Column identifiers
        # are escaped even though the caller already admitted the exact physical schema.
        columns = tuple(
            str(row[1]) for row in connection.exec_driver_sql(f'PRAGMA table_info("{table.value}")')
        )
        if not columns or "evidence_id" not in columns:
            raise SourcePolicyError("dialogue_provenance_unproven")
        sizes = "+".join(
            'coalesce(length(CAST("' + name.replace('"', '""') + '" AS BLOB)),0)'
            for name in columns
        )
        groups: dict[bool | None, list[str]] = {}
        for evidence_id in expected:
            branch = branches.get((table, evidence_id))
            groups.setdefault(None if branch is None else branch[1], []).append(evidence_id)
        for erased, group in groups.items():
            ids = tuple(sorted(group))
            for offset in range(0, len(ids), 500):
                chunk = ids[offset : offset + 500]
                marks = ",".join("?" for _ in chunk)
                predicate = f"evidence_id IN ({marks})"
                if erased is not None:
                    nullity = "NULL" if erased else "NOT NULL"
                    predicate += f" AND quote IS {nullity} AND normalized_signature IS {nullity}"
                count, size = connection.exec_driver_sql(
                    f'SELECT count(*),coalesce(sum({sizes}),0) FROM "{table.value}" '
                    f"WHERE {predicate}",
                    chunk,
                ).one()
                if int(count) != len(chunk):
                    raise SourcePolicyError("dialogue_provenance_unproven")
                read_budget.add(rows=int(count), logical_bytes=int(size))
                rows = tuple(
                    dict(row)
                    for row in connection.exec_driver_sql(
                        f'SELECT * FROM "{table.value}" WHERE {predicate}', chunk
                    ).mappings()
                )
                if any(
                    canonical_row_digest(row) != expected[str(row["evidence_id"])] for row in rows
                ):
                    raise SourcePolicyError("dialogue_provenance_unproven")


def canonical_root_available(
    connection: Connection,
    message_id: str,
    snapshot: SourceEligibilitySnapshot,
    *,
    approved_actions: tuple[BodyAction, ...] | None = None,
) -> bool:
    """Prove a live or approved terminal canonical root without reopening its text to cognition."""
    columns = {
        str(column["name"]) for column in inspect(connection).get_columns("conversation_messages")
    }
    body_columns = {"body_state", "body_action_id"}
    if body_columns.intersection(columns) != body_columns:
        from satori.infrastructure.persistence.erasure import _admit_physical_schema

        revision, _digest = _admit_physical_schema(connection)
        if revision not in {"0016_source_policy", "0017_erasure_intent", "0018_erasure_bodies"}:
            return False
        body_fields = "'available' AS body_state,NULL AS body_action_id"
    else:
        body_fields = "m.body_state,m.body_action_id"
    row = (
        connection.exec_driver_sql(
            "SELECT m.message_id,m.interaction_id,m.session_id,"
            f"length(trim(m.content))>0 AS content_available,{body_fields} "
            "FROM conversation_messages m "
            "JOIN conversation_interactions i ON i.interaction_id=m.interaction_id "
            "AND i.session_id=m.session_id "
            "JOIN conversation_sessions s ON s.session_id=m.session_id "
            "WHERE m.message_id=? AND m.role='user' AND m.sequence=1 "
            "AND i.status='completed' AND s.scope_verified=1 AND s.identity_id=?",
            (message_id, snapshot.identity_id),
        )
        .mappings()
        .first()
    )
    if row is None:
        return False
    header = dict(row)
    if header["content_available"]:
        return (
            header.get("body_state", "available") == "available"
            and header.get("body_action_id") is None
        )
    if header.get("body_state") != "erased":
        return False
    receipts: tuple[Row, ...] = ()
    if approved_actions is None:
        if not inspect(connection).has_table("erasure_body_applications"):
            return False
        receipt_columns = tuple(
            str(row[1])
            for row in connection.exec_driver_sql('PRAGMA table_info("erasure_body_applications")')
        )
        receipt_sizes = "+".join(
            'coalesce(length(CAST("' + name.replace('"', '""') + '" AS BLOB)),0)'
            for name in receipt_columns
        )
        count, size = connection.exec_driver_sql(
            f"SELECT count(*),coalesce(sum({receipt_sizes}),0) "
            "FROM erasure_body_applications WHERE identity_id=?",
            (snapshot.identity_id,),
        ).one()
        if int(count) > _MAX_ACTIONS or int(size) > _MAX_RECEIPT_BYTES:
            raise SourcePolicyError("dialogue_provenance_capacity_exceeded")
        receipts = tuple(
            dict(item)
            for item in connection.exec_driver_sql(
                "SELECT * FROM erasure_body_applications WHERE identity_id=? "
                "ORDER BY body_revision",
                (snapshot.identity_id,),
            ).mappings()
        )
    message = dict(
        connection.exec_driver_sql(
            "SELECT * FROM conversation_messages WHERE message_id=? "
            "AND body_state='erased' AND content IS NULL",
            (message_id,),
        )
        .mappings()
        .one()
    )
    interaction = (
        connection.exec_driver_sql(
            "SELECT * FROM conversation_interactions WHERE interaction_id=?",
            (message["interaction_id"],),
        )
        .mappings()
        .one()
    )
    scoped = (
        connection.exec_driver_sql(
            "SELECT * FROM conversation_sessions WHERE session_id=?", (message["session_id"],)
        )
        .mappings()
        .one()
    )
    store: Store = {
        "conversation_messages": (message,),
        "conversation_interactions": (dict(interaction),),
        "conversation_sessions": (dict(scoped),),
        "erasure_body_applications": receipts,
    }
    historical = (
        historical_from_store(store, snapshot)
        if approved_actions is None
        else historical_from_approved_actions(store, snapshot, approved_actions)
    )
    return historical.admits_root(message_id)


def historical_root_available(session: Session, message_id: str) -> bool:
    """Use the already held operational lease and its trusted snapshot; never recursively admit."""
    snapshot: Any = session.info.get("source_snapshot")
    runtime: Any = session.info.get("source_runtime")
    if snapshot is None and runtime is not None:
        snapshot = runtime.snapshot(verify_mirror=False)
    if not isinstance(snapshot, SourceEligibilitySnapshot):
        return False
    return canonical_root_available(session.connection(), message_id, snapshot)
