"""Actual independently committed floor actions; no main bodies or providers."""

from dataclasses import replace
from pathlib import Path

import pytest

from satori.domain.erasure_bodies import body_actions_digest
from satori.domain.erasure_dialogue import DialogueBodyAction
from satori.domain.erasure_knowledge import (
    KnowledgeBodyAction,
    KnowledgeBodyKind,
    KnowledgeBodyProposal,
    KnowledgeBodyTarget,
    KnowledgeWitnessOrigin,
    KnowledgeWitnessTransition,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.source_policy import ledger as ledger_module
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.test_erasure_body_floor import _body_commit, _body_proposal, _rows
from tests.test_erasure_dialogue_floor import _dialogue_commit, _dialogue_proposal
from tests.test_erasure_intent_floor import _commit, _exclude, _proposal, _root


@pytest.fixture
def runtime(tmp_path: Path) -> SourcePolicyRuntime:
    runtime = SourcePolicyRuntime(tmp_path / "knowledge-floor", "satori")
    runtime.enroll(reviewed=True, new_installation=True, lineage_id="lineage")
    _exclude(runtime, _root(), "source-request")
    _commit(runtime, _proposal(runtime, _root(), "intent-request"))
    runtime.upgrade_body_policy()
    _body_commit(runtime, _body_proposal(runtime))
    runtime.upgrade_dialogue_policy()
    _dialogue_commit(runtime, _dialogue_proposal(runtime))
    return runtime


def _knowledge_proposal(
    runtime: SourcePolicyRuntime, request_id: str = "knowledge-request"
) -> KnowledgeBodyProposal:
    with runtime.exclusive() as snapshot:
        dialogue = runtime.body_action("dialogue-request")
        assert isinstance(dialogue, DialogueBodyAction)
        witness = dialogue.proposal.targets[0].witnesses[0]
        transition = KnowledgeWitnessTransition(
            witness.table,
            witness.evidence_id,
            _root(),
            witness.digest,
            "1" * 64,
            KnowledgeWitnessOrigin.DIALOGUE,
            dialogue.action_id,
        )
        return KnowledgeBodyProposal(
            _root(),
            "intent-request",
            request_id,
            snapshot.generation,
            snapshot.erasure_revision,
            snapshot.body_revision,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            (
                KnowledgeBodyTarget(
                    KnowledgeBodyKind.POSITION, "position", 1, "d" * 64, "e" * 64, (transition,)
                ),
            ),
        )


def _knowledge_commit(
    runtime: SourcePolicyRuntime, proposal: KnowledgeBodyProposal
) -> KnowledgeBodyAction:
    with runtime.exclusive():
        return runtime.commit_knowledge_action(proposal)


def test_knowledge_operation_does_not_implicitly_upgrade_old_floor(
    runtime: SourcePolicyRuntime,
) -> None:
    proposal = _knowledge_proposal(runtime)
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="policy_knowledge_schema_upgrade_required"):
        _knowledge_commit(runtime, proposal)
    assert runtime.ledger.database_path.read_bytes() == before


def test_upgrade_preserves_old_values_hashes_bindings_inode_and_capabilities(
    runtime: SourcePolicyRuntime,
) -> None:
    before = _rows(runtime)
    binding = runtime.ledger.binding_path.read_bytes()
    inode = runtime.ledger.database_path.stat().st_ino
    with runtime.shared() as original:
        old_actions = runtime.body_actions()
    snapshot = runtime.upgrade_knowledge_policy()
    assert snapshot == original
    after = _rows(runtime)
    assert after["alembic_version"] == (("policy_0005",),)
    for name in before:
        if name != "alembic_version":
            assert after[name] == before[name]
    assert runtime.ledger.binding_path.read_bytes() == binding
    assert runtime.ledger.database_path.stat().st_ino == inode
    with runtime.shared() as current:
        assert runtime.body_actions() == old_actions
        assert current.body_head_digest == original.body_head_digest
        assert runtime.body_policy_ready()
        assert runtime.dialogue_policy_ready()
        assert runtime.knowledge_policy_ready()
    committed = runtime.ledger.database_path.read_bytes()
    runtime.upgrade_policy()
    runtime.upgrade_body_policy()
    runtime.upgrade_dialogue_policy()
    runtime.upgrade_knowledge_policy()
    assert runtime.ledger.database_path.read_bytes() == committed


def test_knowledge_extends_same_head_and_all_original_replays_survive_restart(
    runtime: SourcePolicyRuntime,
) -> None:
    runtime.upgrade_knowledge_policy()
    proposal = _knowledge_proposal(runtime)
    action = _knowledge_commit(runtime, proposal)
    before = runtime.ledger.database_path.read_bytes()
    assert _knowledge_commit(runtime, proposal) == action
    restarted = SourcePolicyRuntime(runtime.ledger.directory, "satori")
    with restarted.shared() as snapshot:
        actions = restarted.body_actions()
        assert (snapshot.generation, snapshot.erasure_revision, snapshot.body_revision) == (1, 1, 3)
        assert snapshot.body_head_digest == body_actions_digest(actions)
        assert restarted.body_action(proposal.request_id) == action
    assert runtime.ledger.database_path.read_bytes() == before
    episode = _body_commit(runtime, _body_proposal(runtime, "later-episode", memory_id="later"))
    assert episode.result_body_revision == 4
    # No witness reset: this captures the exact historical original/terminal descriptor.
    dialogue = _dialogue_commit(runtime, _dialogue_proposal(runtime, "later-dialogue"))
    assert dialogue.result_body_revision == 5
    assert _knowledge_commit(runtime, proposal) == action


@pytest.mark.parametrize("fault", ["wrong_hash", "wrong_id", "wrong_root", "future", "live_bypass"])
def test_wrong_witness_link_rejects_before_any_floor_write(
    runtime: SourcePolicyRuntime, fault: str
) -> None:
    runtime.upgrade_knowledge_policy()
    proposal = _knowledge_proposal(runtime)
    target = proposal.targets[0]
    witness = target.witness_transitions[0]
    if fault == "wrong_hash":
        witness = replace(witness, before_digest="9" * 64)
    elif fault == "wrong_id":
        witness = replace(witness, evidence_id="other-edge")
    elif fault == "wrong_root":
        witness = replace(witness, root=replace(witness.root, session_id="different"))
    elif fault == "future":
        witness = replace(witness, predecessor_action_id="future-action")
    else:
        witness = replace(witness, origin=KnowledgeWitnessOrigin.LIVE, predecessor_action_id=None)
    changed = replace(proposal, targets=(replace(target, witness_transitions=(witness,)),))
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="knowledge_witness_predecessor_conflict"):
        _knowledge_commit(runtime, changed)
    assert runtime.ledger.database_path.read_bytes() == before


@pytest.mark.parametrize("state", ["original", "terminal"])
def test_current_knowledge_predecessor_supports_pending_and_terminal_branches(
    runtime: SourcePolicyRuntime, state: str
) -> None:
    runtime.upgrade_knowledge_policy()
    first = _knowledge_commit(runtime, _knowledge_proposal(runtime))
    original = first.proposal.targets[0].witness_transitions[0]
    successor = replace(
        original,
        origin=KnowledgeWitnessOrigin.KNOWLEDGE,
        predecessor_action_id=first.action_id,
        before_digest=original.before_digest if state == "original" else original.after_digest,
    )
    proposal = replace(
        first.proposal,
        request_id="knowledge-successor",
        expected_body_revision=3,
        targets=(replace(first.proposal.targets[0], witness_transitions=(successor,)),),
    )
    result = _knowledge_commit(runtime, proposal)
    assert result.result_body_revision == 4
    stale = replace(first.proposal, request_id="stale-predecessor", expected_body_revision=4)
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="knowledge_witness_predecessor_conflict"):
        _knowledge_commit(runtime, stale)
    assert runtime.ledger.database_path.read_bytes() == before


def test_live_origin_can_capture_an_independent_same_scope_root(
    runtime: SourcePolicyRuntime,
) -> None:
    runtime.upgrade_knowledge_policy()
    proposal = _knowledge_proposal(runtime)
    old = proposal.targets[0].witness_transitions[0]
    live = replace(
        old,
        evidence_id="independent-edge",
        root=replace(_root(), message_id="independent"),
        origin=KnowledgeWitnessOrigin.LIVE,
        predecessor_action_id=None,
    )
    assert (
        _knowledge_commit(
            runtime,
            replace(
                proposal,
                targets=(replace(proposal.targets[0], witness_transitions=(live,)),),
            ),
        ).result_body_revision
        == 3
    )


def test_old_dialogue_only_recapture_is_preserved_by_upgrade(
    runtime: SourcePolicyRuntime,
) -> None:
    original = _dialogue_proposal(runtime, "legacy-recapture")
    target = original.targets[0]
    recaptured = replace(
        original,
        targets=(replace(target, witnesses=(replace(target.witnesses[0], digest="7" * 64),)),),
    )
    action = _dialogue_commit(runtime, recaptured)
    old_rows = _rows(runtime)["policy_body_actions"]
    runtime.upgrade_knowledge_policy()
    assert _rows(runtime)["policy_body_actions"] == old_rows
    with runtime.shared():
        assert runtime.body_action("legacy-recapture") == action


def test_later_dialogue_keeps_historical_anchor_without_rewinding_current_predecessor(
    runtime: SourcePolicyRuntime,
) -> None:
    runtime.upgrade_knowledge_policy()
    first = _knowledge_commit(runtime, _knowledge_proposal(runtime))
    prior = first.proposal.targets[0].witness_transitions[0]
    second_transition = replace(
        prior,
        origin=KnowledgeWitnessOrigin.KNOWLEDGE,
        predecessor_action_id=first.action_id,
        before_digest=prior.after_digest,
        after_digest="2" * 64,
    )
    second = _knowledge_commit(
        runtime,
        replace(
            first.proposal,
            request_id="knowledge-second",
            expected_body_revision=3,
            targets=(replace(first.proposal.targets[0], witness_transitions=(second_transition,)),),
        ),
    )
    _dialogue_commit(runtime, _dialogue_proposal(runtime, "original-dialogue-recapture"))
    rewind = replace(
        second.proposal,
        request_id="ancient-fingerprint-rewind",
        expected_body_revision=5,
        targets=(
            replace(
                second.proposal.targets[0],
                witness_transitions=(
                    replace(
                        second_transition,
                        predecessor_action_id=second.action_id,
                        before_digest=prior.before_digest,
                    ),
                ),
            ),
        ),
    )
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="knowledge_witness_predecessor_conflict"):
        _knowledge_commit(runtime, rewind)
    assert runtime.ledger.database_path.read_bytes() == before
    proper = replace(
        rewind,
        request_id="current-fingerprint-successor",
        targets=(
            replace(
                second.proposal.targets[0],
                witness_transitions=(
                    replace(second_transition, predecessor_action_id=second.action_id),
                ),
            ),
        ),
    )
    assert _knowledge_commit(runtime, proper).result_body_revision == 6


def test_knowledge_witnesses_count_toward_whole_floor_quota_before_write(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime.upgrade_knowledge_policy()
    # The existing episode + dialogue consume three references; the knowledge
    # target plus its transition consume two, even though the row overlaps.
    monkeypatch.setattr(ledger_module, "MAX_BODY_REFERENCES", 4)
    proposal = _knowledge_proposal(runtime)
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="policy_body_capacity_exceeded"):
        _knowledge_commit(runtime, proposal)
    assert runtime.ledger.database_path.read_bytes() == before
