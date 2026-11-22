"""Offline owner/lifecycle proofs; fixtures never establish sampled Character quality."""

from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from satori.config import Environment, Settings
from satori.core.affect import (
    AffectiveAppraisalProposal,
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
)
from satori.core.conversation import (
    ConversationMessageRole,
    ConversationProviderRequest,
    ConversationProviderResponse,
)
from satori.core.embedding import EmbeddingRequest, EmbeddingResponse, EmbeddingSpace
from satori.core.episode import (
    EpisodeEvidenceProposal,
    EpisodeFormationProposal,
    EpisodeFormationProviderResponse,
    EpisodeFormationRequest,
)
from satori.core.relationship import (
    RelationshipAppraisalProposal,
    RelationshipAppraisalRequest,
    RelationshipAppraisalResponse,
)
from satori.core.semantic import (
    SemanticFormationProposal,
    SemanticFormationProviderResponse,
    SemanticFormationRequest,
)
from satori.domain.affect import materialize_affective_state
from tests.character_closeout_runtime import (
    BranchChoiceRequired,
    CloseoutRuntime,
    FixedEvaluationClock,
    IsolatedEvaluationSettings,
    RuntimePorts,
    build_runtime,
    condition_relationship_profile,
    digest,
    json_value,
    resolve_turn_text,
    run_turn,
    verify_restart_snapshot,
)


@dataclass(slots=True)
class Foreground:
    count: int = 0

    async def generate(
        self, _request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        self.count += 1
        return ConversationProviderResponse(
            f"WIRE-ONLY-{self.count:03d}", "offline-fixture", "no-network", "stop", None
        )


@dataclass(slots=True)
class Affect:
    frustration: float = 0.8

    async def generate_structured(
        self, request: AffectiveAppraisalRequest, /
    ) -> AffectiveAppraisalProviderResponse:
        return AffectiveAppraisalProviderResponse(
            AffectiveAppraisalProposal(
                schema_version=1,
                pleasantness=-0.4,
                activation=0.5,
                novelty=0.2,
                salience=0.9,
                uncertainty=0.1,
                curiosity_signal=0.2,
                interest_signal=0.3,
                humor_signal=0.0,
                concern_signal=0.0,
                frustration_signal=self.frustration,
                confidence_signal=0.1,
                appraisal_confidence=0.95,
                source_refs=(request.interaction_id,),
                reason_codes=("frustration_event",),
            ),
            "offline-fixture",
            "no-network",
            "offline.appraisal.v1",
        )


class Relationship:
    async def generate_structured(
        self, request: RelationshipAppraisalRequest, /
    ) -> RelationshipAppraisalResponse:
        return RelationshipAppraisalResponse(
            RelationshipAppraisalProposal(
                1,
                ("respectful_engagement",),
                0.9,
                (request.interaction_id, request.user_message_id),
            ),
            "offline-fixture",
            "no-network",
            "offline.relationship.v1",
        )


class Episode:
    async def generate_structured(
        self, request: EpisodeFormationRequest, /
    ) -> EpisodeFormationProviderResponse:
        user = next(row for row in request.messages if row.role is ConversationMessageRole.USER)
        return EpisodeFormationProviderResponse(
            EpisodeFormationProposal(
                1,
                True,
                user.content,
                0.8,
                0.95,
                (EpisodeEvidenceProposal(user.message_id, user.content),),
            ),
            "offline-fixture",
            "no-network",
            "offline.episode.v1",
        )


@dataclass(slots=True)
class Embedding:
    calls: list[EmbeddingRequest] = field(default_factory=list)

    @property
    def space(self) -> EmbeddingSpace:
        return EmbeddingSpace("offline-fixture", "no-network", 3, 1)

    async def embed(self, request: EmbeddingRequest, /) -> EmbeddingResponse:
        self.calls.append(request)
        return EmbeddingResponse(self.space, tuple((1.0, 0.0, 0.0) for _ in request.texts))


class Semantic:
    async def generate_structured(
        self, _request: SemanticFormationRequest, /
    ) -> SemanticFormationProviderResponse:
        return SemanticFormationProviderResponse(
            SemanticFormationProposal(1, ()),
            "offline-fixture",
            "no-network",
            "offline.semantic.v1",
        )


def ports() -> RuntimePorts:
    return RuntimePorts(Foreground(), Affect(), Relationship(), Episode(), Embedding(), Semantic())


def configured_settings() -> Settings:
    return IsolatedEvaluationSettings(environment=Environment.TEST)


def runtime(tmp_path: Path, project_root: Path, *, name: str = "case") -> CloseoutRuntime:
    return build_runtime(
        tmp_path / f"{name}.db",
        source_policy_directory=tmp_path / f"{name}-floor",
        settings=configured_settings(),
        ports=ports(),
        clock=FixedEvaluationClock(),
        activate=True,
        alembic_config=project_root / "alembic.ini",
    )


def test_real_managed_turn_preserves_temporal_owner_boundaries_without_network(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("offline lifecycle attempted network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    active = runtime(tmp_path, project_root)
    try:
        session = active.services.start_session.execute()
        result = asyncio.run(
            run_turn(
                active,
                turn_id="01-1",
                user_text="Выбрал тёплый свет: от холодного дома устаю.",
                session_id=session.session_id,
            )
        )
        assert result["complete"] is True
        assert result["behavioral_acceptance"] == "UNASSESSED"
        assert result["manifest"]["policy_schema_version"] == 35
        assert (
            result["before"]["relationship"]["state"]["state_version"]
            == (result["after_foreground_before_post"]["relationship"]["state"]["state_version"])
        )
        assert result["after_post"]["relationship"]["state"]["state_version"] == 2
        assert (
            result["after_post"]["affect"]["state"]["fast"]["frustration"]
            > (result["before"]["affect"]["state"]["fast"]["frustration"])
        )
        assert result["prepared_affect"][0]["actual_input_root_message_ids"] == []
        assert result["prepared_affect"][0]["source_receipt"]["proof_status"] == "complete"
        assert result["prepared_affect"][0]["source_receipt"]["root_message_ids"] == [
            result["canonical_user_message_id"]
        ]
        memory = result["after_post"]["memories"][0]
        assert memory["source_user_message_ids"] == [result["canonical_user_message_id"]]
        assert memory["source_interaction_id"] == result["interaction_id"]
        serialized = json.dumps(result)
        assert '"messages"' not in serialized
        assert "Trusted current-turn agency" not in serialized
    finally:
        active.close()


def test_reopen_same_managed_database_preserves_identity_roots_and_exact_decay(
    tmp_path: Path,
    project_root: Path,
) -> None:
    active = runtime(tmp_path, project_root)
    session = active.services.start_session.execute()
    asyncio.run(
        run_turn(
            active,
            turn_id="01-1",
            user_text="Проект Маяк: хочу тёплый свет.",
            session_id=session.session_id,
        )
    )
    before = active.snapshot()
    typed_before = active.services.emotion_status.execute(active.identity_id).state
    instant = active.clock.now()
    active.close()
    clock = FixedEvaluationClock(instant)
    clock.advance(3600)
    reopened = build_runtime(
        tmp_path / "case.db",
        source_policy_directory=tmp_path / "case-floor",
        settings=configured_settings(),
        ports=ports(),
        clock=clock,
        activate=False,
        alembic_config=project_root / "alembic.ini",
    )
    try:
        after = reopened.snapshot()
        assert after["identity_id"] == before["identity_id"]
        assert after["self_sha256"] == before["self_sha256"]
        assert after["relationship"] == before["relationship"]
        assert after["memories"] == before["memories"]
        proof = verify_restart_snapshot(before, after)
        assert proof["status"] == "PASS_OWNER_RESTART_INVARIANTS"
        assert proof["elapsed_seconds"] == 3600
        expected = materialize_affective_state(typed_before, at=clock.now())
        assert after["affect"]["state"] == json_value(expected)
        second = asyncio.run(
            run_turn(
                reopened,
                turn_id="17-1",
                user_text="Вернулся к Маяку.",
                session_id=session.session_id,
            )
        )
        assert before["memories"][0]["memory_id"] in second["manifest"]["retrieved_memory_ids"]
        assert second["manifest"]["recent_conversation_user_message_ids"]
    finally:
        reopened.close()


def test_semantic_branch_requires_operator_choice_bound_to_actual_reply(project_root: Path) -> None:
    corpus = json.loads(
        (project_root / "docs/evaluation/character-closeout-scenarios.v1.json").read_text()
    )
    turn = corpus["cases"][1]["user_turns"][2]
    public_reply = "WIRE-ONLY: no authored semantic judgment"
    with pytest.raises(BranchChoiceRequired):
        resolve_turn_text(turn, choice=None, prior_public_reply=public_reply)
    branch = turn["branches"][1]
    choice = {
        "record": branch["record"],
        "prior_public_reply_sha256": digest(public_reply),
        "operator_reviewed": True,
    }
    assert resolve_turn_text(turn, choice=choice, prior_public_reply=public_reply) == (
        branch["text"],
        branch["record"],
    )
    with pytest.raises(BranchChoiceRequired):
        resolve_turn_text(turn, choice=choice, prior_public_reply=public_reply + " changed")


@pytest.mark.parametrize("present", [False, True])
def test_case20_branch_depends_on_actual_repair_provenance(
    project_root: Path, present: bool
) -> None:
    corpus = json.loads(
        (project_root / "docs/evaluation/character-closeout-scenarios.v1.json").read_text()
    )
    turn = corpus["cases"][19]["user_turns"][2]
    text, record = resolve_turn_text(
        turn, choice=None, prior_public_reply=None, actual_prior_repair_present=present
    )
    assert text == turn["branches"][0 if present else 1]["text"]
    assert record == turn["branches"][0 if present else 1]["record"]


def test_profiles_are_owner_committed_controls_and_counterparty_state_does_not_leak(
    tmp_path: Path,
    project_root: Path,
) -> None:
    active = runtime(tmp_path, project_root)
    try:
        report = asyncio.run(condition_relationship_profile(active, profile="earned-familiar"))
        assert report["kind"] == "CONTROLLED_PROFILE_FIXTURE"
        assert report["model_calls"] == 0
        assert len(report["canonical_roots"]) == 64
        assert report["after"]["relationship_projection"]["maturity"] == "established"
        assert len(active.foreground.calls) == 0
        assert all(not port.calls for port in active.structured)
        strained = asyncio.run(condition_relationship_profile(active, profile="relevant-strained"))
        assert len(strained["canonical_roots"]) == 4
        assert strained["after"]["relationship_projection"]["recent_strain"] is True
        owner = active.services.relationship_status.execute(
            active.identity_id, "character-closeout-b"
        )
        assert owner.state.processed_interaction_count == 0
        assert (
            owner.state.vector
            != active.services.relationship_status.execute(
                active.identity_id, active.settings.default_counterparty_id
            ).state.vector
        )
        assert active.snapshot()["personality_sha256"] == report["before"]["personality_sha256"]
        assert active.services.memories.execute() == ()
    finally:
        active.close()


def test_runtime_refuses_activation_reseed_and_opt_in_candidates(
    tmp_path: Path,
    project_root: Path,
) -> None:
    active = runtime(tmp_path, project_root)
    active.close()
    with pytest.raises(ValueError, match="activation requires"):
        build_runtime(
            tmp_path / "case.db",
            source_policy_directory=tmp_path / "case-floor",
            settings=configured_settings(),
            ports=ports(),
            clock=FixedEvaluationClock(),
            activate=True,
            alembic_config=project_root / "alembic.ini",
        )
    with pytest.raises(ValueError, match="unmodified production V35"):
        build_runtime(
            tmp_path / "candidate.db",
            source_policy_directory=tmp_path / "candidate-floor",
            settings=configured_settings().model_copy(
                update={"conversation_repair_interpretation_enabled": True}
            ),
            ports=ports(),
            clock=FixedEvaluationClock(),
            activate=True,
            alembic_config=project_root / "alembic.ini",
        )
