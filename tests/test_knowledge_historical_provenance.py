"""Approved body removal transfers exact attribution, never live evidence authority."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, create_engine, event

from satori.domain.erasure_bodies import BodyAction, body_actions_digest, source_root
from satori.domain.erasure_dialogue import (
    DialogueBodyAction,
    DialogueBodyProposal,
    DialogueBodyTarget,
    HistoricalEvidenceTable,
)
from satori.domain.erasure_knowledge import (
    KnowledgeBodyAction,
    KnowledgeBodyKind,
    KnowledgeBodyProposal,
    KnowledgeBodyTarget,
    KnowledgeWitnessOrigin,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.canonical_provenance import (
    Store,
    canonical_row_digest,
    capture_historical_witnesses,
    capture_knowledge_transitions,
    dialogue_message_digest,
    historical_from_approved_actions,
    validate_historical_witnesses,
)
from satori.infrastructure.persistence.erasure_inventory import ProvenOwners
from satori.infrastructure.persistence.knowledge_material import KnowledgeMaterialIndex
from tests.test_canonical_provenance import _ROOT, _action, _snapshot, _store, _terminal


def _position_store() -> Store:
    store = _store()
    store["satori_positions"][0].update(
        proposition="Private project",
        normalized_proposition="private project",
        body_state="available",
        body_action_id=None,
    )
    return store


def _head(actions: tuple[BodyAction, ...]) -> SourceEligibilitySnapshot:
    return replace(
        _snapshot(),
        body_revision=len(actions),
        body_head_digest=body_actions_digest(actions),
        excluded_message_ids=frozenset(
            root.message_id for action in actions if (root := source_root(action)) is not None
        ),
    )


def _knowledge(
    store: Store, actions: tuple[BodyAction, ...], *, request_id: str = "knowledge-request"
) -> KnowledgeBodyAction:
    index = KnowledgeMaterialIndex.build(store)
    kind = KnowledgeBodyKind.POSITION
    target = KnowledgeBodyTarget(
        kind,
        "position",
        1,
        index.digest(kind, "position", terminal=False),
        index.digest(kind, "position", terminal=True),
        capture_knowledge_transitions(
            store,
            _ROOT,
            ((kind, "position"),),
            snapshot=_head(actions),
            approved_actions=actions,
        ),
    )
    return KnowledgeBodyAction(
        KnowledgeBodyProposal(
            _ROOT,
            "intent",
            request_id,
            1,
            1,
            len(actions),
            "a" * 64,
            "b" * 64,
            "c" * 64,
            (target,),
        ),
        request_id + "-action",
        "floor",
        "installation",
        len(actions) + 1,
        datetime(2026, 10, 6, tzinfo=UTC),
        "trace",
    )


def _erase_position(store: Store, action: KnowledgeBodyAction) -> None:
    store["satori_positions"][0].update(
        proposition=None,
        normalized_proposition=None,
        body_state="erased",
        body_action_id=action.action_id,
    )
    for edge in store["satori_position_evidence"]:
        edge["quote"] = edge["normalized_signature"] = None


def _second_source(store: Store) -> CanonicalRootRef:
    root = replace(_ROOT, message_id="second-message", interaction_id="second-interaction")
    store["conversation_interactions"] += (
        dict(store["conversation_interactions"][0], interaction_id=root.interaction_id),
    )
    store["conversation_messages"] += (
        dict(
            store["conversation_messages"][0],
            message_id=root.message_id,
            interaction_id=root.interaction_id,
            content="Another private project.",
        ),
    )
    store["satori_position_evidence"] += (
        dict(
            store["satori_position_evidence"][0],
            evidence_id="second-edge",
            source_message_id=root.message_id,
            source_interaction_id=root.interaction_id,
            quote="Another private project",
            normalized_signature="another private project",
        ),
    )
    return root


def _dialogue_second(
    store: Store, root: CanonicalRootRef, actions: tuple[BodyAction, ...]
) -> DialogueBodyAction:
    message = store["conversation_messages"][1]
    target = DialogueBodyTarget(
        root.message_id,
        1,
        dialogue_message_digest(message),
        dialogue_message_digest(message, terminal=True),
        capture_historical_witnesses(
            store, root, approved_actions=actions, snapshot=_head(actions)
        ),
    )
    return DialogueBodyAction(
        DialogueBodyProposal(
            root,
            "second-intent",
            "second-dialogue",
            1,
            1,
            len(actions),
            "a" * 64,
            "b" * 64,
            "c" * 64,
            (target,),
        ),
        "second-dialogue-action",
        "floor",
        "installation",
        len(actions) + 1,
        datetime(2026, 10, 6, tzinfo=UTC),
        "trace",
    )


def test_terminal_position_transfers_original_witness_without_changing_it() -> None:
    store = _position_store()
    dialogue = _action(store)
    original = dialogue.proposal.targets[0].witnesses
    _terminal(store, dialogue)
    knowledge = _knowledge(store, (dialogue,))
    transition = knowledge.proposal.targets[0].witness_transitions[0]
    assert transition.origin is KnowledgeWitnessOrigin.DIALOGUE
    assert transition.predecessor_action_id == dialogue.action_id
    assert transition.before_digest == canonical_row_digest(store["satori_position_evidence"][0])
    _erase_position(store, knowledge)
    actions: tuple[BodyAction, ...] = dialogue, knowledge
    historical = historical_from_approved_actions(store, _head(actions), actions)
    assert historical.admits_root(_ROOT.message_id)
    assert historical.admits_evidence(
        HistoricalEvidenceTable.POSITION, store["satori_position_evidence"][0]
    )
    assert historical.admits_terminal_owner("position", store["satori_positions"][0])
    assert ProvenOwners(store, "satori", historical=historical).admits(("position", "position", 1))
    assert not ProvenOwners(store, "satori").admits(("position", "position", 1))
    assert dialogue.proposal.targets[0].witnesses == original


@pytest.mark.parametrize(
    "field", ["quote", "normalized_signature", "source_message_id", "observed_at"]
)
def test_changed_or_invented_terminal_evidence_loses_proof(field: str) -> None:
    store = _position_store()
    dialogue = _action(store)
    _terminal(store, dialogue)
    knowledge = _knowledge(store, (dialogue,))
    _erase_position(store, knowledge)
    store["satori_position_evidence"][0][field] = "forged"
    actions: tuple[BodyAction, ...] = dialogue, knowledge
    historical = historical_from_approved_actions(store, _head(actions), actions)
    assert not historical.admits_evidence(
        HistoricalEvidenceTable.POSITION, store["satori_position_evidence"][0]
    )
    assert not historical.admits_terminal_owner("position", store["satori_positions"][0])
    assert not ProvenOwners(store, "satori", historical=historical).admits(
        ("position", "position", 1)
    )


def test_pending_reapproval_uses_current_knowledge_predecessor_and_exact_original_row() -> None:
    store = _position_store()
    dialogue = _action(store)
    _terminal(store, dialogue)
    first = _knowledge(store, (dialogue,))
    actions: tuple[BodyAction, ...] = dialogue, first
    store["satori_positions"][0]["proposition"] = "Changed body, same accepted evidence"
    second = _knowledge(store, actions, request_id="reapproval")
    transition = second.proposal.targets[0].witness_transitions[0]
    assert transition.origin is KnowledgeWitnessOrigin.KNOWLEDGE
    assert transition.predecessor_action_id == first.action_id
    assert (
        transition.before_digest == first.proposal.targets[0].witness_transitions[0].before_digest
    )
    historical = historical_from_approved_actions(store, _head(actions), actions)
    assert historical.admits_evidence(
        HistoricalEvidenceTable.POSITION, store["satori_position_evidence"][0]
    )
    assert not historical.admits_terminal_owner("position", store["satori_positions"][0])


def test_null_source_cannot_reapprove_changed_quote_as_new_literal_evidence() -> None:
    store = _position_store()
    dialogue = _action(store)
    _terminal(store, dialogue)
    store["satori_position_evidence"][0]["quote"] = "fabricated quotation"
    before = deepcopy(store)
    with pytest.raises(SourcePolicyError, match="knowledge_witness_unproven"):
        _knowledge(store, (dialogue,))
    assert store == before


def test_missing_or_stale_predecessor_does_not_transfer_a_witness() -> None:
    store = _position_store()
    dialogue = _action(store)
    _terminal(store, dialogue)
    first = _knowledge(store, (dialogue,))
    second = _knowledge(store, (dialogue, first), request_id="reapproval")
    target = second.proposal.targets[0]
    forged = replace(
        target.witness_transitions[0],
        origin=KnowledgeWitnessOrigin.DIALOGUE,
        predecessor_action_id=dialogue.action_id,
    )
    second = replace(
        second,
        proposal=replace(
            second.proposal, targets=(replace(target, witness_transitions=(forged,)),)
        ),
    )
    actions: tuple[BodyAction, ...] = dialogue, first, second
    with pytest.raises(SourcePolicyError, match="knowledge_witness_unproven"):
        historical_from_approved_actions(store, _head(actions), actions)


@pytest.mark.parametrize("erase_before_dialogue", [True, False])
def test_live_second_root_can_later_become_null_without_reinventing_removed_quote(
    erase_before_dialogue: bool,
) -> None:
    store = _position_store()
    second_root = _second_source(store)
    dialogue = _action(store)
    _terminal(store, dialogue)
    # The first helper replaces messages with its single fixture row.
    store["conversation_messages"] += (
        dict(
            store["conversation_messages"][0],
            message_id=second_root.message_id,
            interaction_id=second_root.interaction_id,
            content="Another private project.",
            body_state="available",
            body_action_id=None,
        ),
    )
    knowledge = _knowledge(store, (dialogue,))
    assert {item.origin for item in knowledge.proposal.targets[0].witness_transitions} == {
        KnowledgeWitnessOrigin.DIALOGUE,
        KnowledgeWitnessOrigin.LIVE,
    }
    if erase_before_dialogue:
        _erase_position(store, knowledge)
    actions: tuple[BodyAction, ...] = dialogue, knowledge
    second_dialogue = _dialogue_second(store, second_root, actions)
    if not erase_before_dialogue:
        _erase_position(store, knowledge)
    store["conversation_messages"][1].update(
        content=None,
        body_state="erased",
        body_action_id=second_dialogue.action_id,
    )
    actions += (second_dialogue,)
    historical = historical_from_approved_actions(store, _head(actions), actions)
    assert historical.admits_root(second_root.message_id)
    assert historical.admits_evidence(
        HistoricalEvidenceTable.POSITION, store["satori_position_evidence"][1]
    )
    assert (
        historical.evidence_proofs[HistoricalEvidenceTable.POSITION, "second-edge"].action_id
        == knowledge.action_id
    )
    assert ProvenOwners(store, "satori", historical=historical).admits(("position", "position", 1))


def test_extra_unapproved_edge_invalidates_terminal_parent_and_transferred_proof() -> None:
    store = _position_store()
    dialogue = _action(store)
    _terminal(store, dialogue)
    knowledge = _knowledge(store, (dialogue,))
    _erase_position(store, knowledge)
    store["satori_position_evidence"] += (
        dict(store["satori_position_evidence"][0], evidence_id="unapproved-edge"),
    )
    actions: tuple[BodyAction, ...] = dialogue, knowledge
    historical = historical_from_approved_actions(store, _head(actions), actions)
    assert not historical.admits_terminal_owner("position", store["satori_positions"][0])
    assert not historical.admits_evidence(
        HistoricalEvidenceTable.POSITION, store["satori_position_evidence"][1]
    )


def _witness_database(store: Store) -> Engine:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        for table in (
            "memory_evidence",
            "user_model_claim_evidence",
            "world_model_claim_evidence",
            "satori_position_evidence",
            "satori_inclination_evidence",
            "satori_positions",
        ):
            rows = store[table]
            assert rows
            columns = ",".join(
                f'"{name}" '
                + ("INTEGER" if type(value) is int else "REAL" if type(value) is float else "TEXT")
                for name, value in rows[0].items()
            )
            connection.exec_driver_sql(f'CREATE TABLE "{table}" ({columns})')
            for row in rows:
                marks = ",".join("?" for _ in row)
                connection.exec_driver_sql(
                    f'INSERT INTO "{table}" VALUES ({marks})', tuple(row.values())
                )
        connection.exec_driver_sql(
            "CREATE TABLE satori_position_revisions (revision_id TEXT,position_id TEXT)"
        )
    return engine


@pytest.mark.parametrize("terminal_parent", [False, True])
def test_ordinary_witness_rejects_restored_quote_before_materializing_it(
    terminal_parent: bool,
) -> None:
    store = _position_store()
    dialogue = _action(store)
    _terminal(store, dialogue)
    knowledge = _knowledge(store, (dialogue,))
    original = dict(store["satori_position_evidence"][0])
    if terminal_parent:
        _erase_position(store, knowledge)
        store["satori_position_evidence"][0].update(original)
    actions: tuple[BodyAction, ...] = dialogue, knowledge
    engine = _witness_database(store)
    reads: list[str] = []

    def record(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        if statement.startswith('SELECT * FROM "satori_position_evidence"'):
            reads.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with (
            engine.connect() as connection,
            pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"),
        ):
            validate_historical_witnesses(connection, actions, snapshot=_head(actions))
        assert reads == []
    finally:
        engine.dispose()


def test_privileged_pending_witness_requires_exact_whole_original_position() -> None:
    store = _position_store()
    dialogue = _action(store)
    _terminal(store, dialogue)
    knowledge = _knowledge(store, (dialogue,))
    actions: tuple[BodyAction, ...] = dialogue, knowledge
    engine = _witness_database(store)
    try:
        with engine.connect() as connection:
            validate_historical_witnesses(
                connection,
                actions,
                snapshot=_head(actions),
                message_ids=frozenset({_ROOT.message_id}),
            )
            connection.exec_driver_sql(
                "UPDATE satori_positions SET proposition='changed unavailable-source body'"
            )
            with pytest.raises(SourcePolicyError, match="dialogue_provenance_unproven"):
                validate_historical_witnesses(
                    connection,
                    actions,
                    snapshot=_head(actions),
                    message_ids=frozenset({_ROOT.message_id}),
                )
    finally:
        engine.dispose()


def test_terminal_witness_reads_are_sql_null_constrained() -> None:
    store = _position_store()
    dialogue = _action(store)
    _terminal(store, dialogue)
    knowledge = _knowledge(store, (dialogue,))
    _erase_position(store, knowledge)
    actions: tuple[BodyAction, ...] = dialogue, knowledge
    engine = _witness_database(store)
    reads: list[str] = []

    def record(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        if statement.startswith('SELECT * FROM "satori_position_evidence"'):
            reads.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with engine.connect() as connection:
            validate_historical_witnesses(connection, actions, snapshot=_head(actions))
        assert reads
        assert all("quote IS NULL AND normalized_signature IS NULL" in sql for sql in reads)
    finally:
        engine.dispose()
