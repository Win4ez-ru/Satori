"""Structural Stage 7.7 benchmark and appraisal-corpus regressions."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from satori.__main__ import build_parser
from satori.application.conversation.contracts import (
    ConversationContextManifest,
    SatoriReply,
    TalkInput,
    TurnPhaseTimings,
)
from satori.appraisal_evaluation import (
    APPRAISAL_EVALUATION_CORPUS_V1,
    AppraisalExpectation,
    evaluate_appraisal,
    run_appraisal_model_evaluation,
)
from satori.composition import ConversationServices
from satori.core.affect import (
    AffectiveAppraisalProposal,
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
)
from satori.core.conversation import ConversationMessageRole
from satori.core.episode import EpisodeFormationRequest, EpisodeSourceMessage
from satori.performance import (
    EXACT_TRIPLE_GREETING_PROBE_ID,
    INFERENCE_BENCHMARK_SCENARIOS_V1,
    ControlledRecallEpisodeProvider,
    InferenceBenchmarkSample,
    distribution,
    run_inference_benchmark,
    summarize_samples,
)
from tests.fakes import SequenceIdGenerator


def proposal(**updates: object) -> AffectiveAppraisalProposal:
    values: dict[str, object] = {
        "schema_version": 1,
        "pleasantness": 0.0,
        "activation": 0.1,
        "novelty": 0.1,
        "salience": 0.2,
        "uncertainty": 0.1,
        "curiosity_signal": 0.1,
        "interest_signal": 0.1,
        "humor_signal": 0.0,
        "concern_signal": 0.0,
        "frustration_signal": 0.0,
        "confidence_signal": 0.0,
        "appraisal_confidence": 0.9,
        "source_refs": ("event",),
        "reason_codes": ("neutral_social",),
    }
    values.update(updates)
    return AffectiveAppraisalProposal(**values)  # type: ignore[arg-type]


def test_semantic_appraisal_evaluation_uses_ranges_not_exact_floats() -> None:
    expectation = AppraisalExpectation(
        minimums=(("concern_signal", 0.55), ("salience", 0.55)),
        reason_any=frozenset({"distress", "concern"}),
    )

    passed, failures = evaluate_appraisal(
        proposal(
            concern_signal=0.8,
            salience=0.7,
            reason_codes=("distress",),
        ),
        expectation,
    )

    assert passed is True
    assert failures == ()


@dataclass
class FixtureAppraisalAdapter:
    model: str = "fixture-appraisal"

    async def generate_structured(
        self, request: AffectiveAppraisalRequest, /
    ) -> AffectiveAppraisalProviderResponse:
        return AffectiveAppraisalProviderResponse(
            proposal=proposal(source_refs=(request.interaction_id,)),
            provider="fixture",
            model=self.model,
            appraisal_method="fixture.categorical.v2",
        )


def test_appraisal_report_is_versioned_and_does_not_copy_fixture_text() -> None:
    report = asyncio.run(
        run_appraisal_model_evaluation((FixtureAppraisalAdapter(),), repetitions=1)
    )
    serialized = json.dumps(report, ensure_ascii=False)

    assert report["evaluation_id"] == "satori.affective-appraisal.stage77.v1"
    assert report["corpus_size"] == 10
    assert report["models"]["fixture-appraisal"]["p90_wall_ms"] >= 0.0
    assert "Привет, Сатори" not in serialized
    assert "Сегодня погиб" not in serialized


def sample(scenario_id: str, value: float) -> InferenceBenchmarkSample:
    return InferenceBenchmarkSample(
        scenario_id=scenario_id,
        sample_index=1,
        sample_kind="measured_warm",
        retrieval_status="no_relevant_memory",
        retrieved_memory_count=0,
        retrieval_embedding_ms=0.0,
        retrieval_search_ranking_ms=0.0,
        affect_materialization_ms=0.0,
        relationship_projection_ms=0.0,
        appraisal_request_build_ms=0.0,
        appraisal_ms=value,
        context_assembly_ms=0.0,
        generation_ms=value * 2,
        grounding_ms=0.0,
        canonical_commit_ms=0.0,
        committed_reply_ms=value * 3,
        appraisal_load_ms=0.0,
        appraisal_prompt_eval_ms=0.0,
        appraisal_eval_ms=0.0,
        appraisal_prompt_tokens=int(value * 10),
        appraisal_output_tokens=int(value),
        appraisal_prompt_tokens_per_second=10.0,
        appraisal_output_tokens_per_second=10.0,
        appraisal_adapter_request_build_ms=0.0,
        appraisal_http_roundtrip_ms=0.0,
        appraisal_response_parse_ms=0.0,
        generation_load_ms=0.0,
        generation_prompt_eval_ms=0.0,
        generation_eval_ms=0.0,
        generation_prompt_tokens=int(value * 20),
        generation_output_tokens=int(value * 2),
        generation_prompt_tokens_per_second=10.0,
        generation_output_tokens_per_second=10.0,
    )


def test_controlled_recall_episode_is_grounded_in_the_supplied_user_message() -> None:
    request = EpisodeFormationRequest(
        schema_version=1,
        trace_id="trace",
        interaction_id="interaction",
        occurred_at=datetime(2026, 8, 9, tzinfo=UTC),
        formation_version=1,
        messages=(
            EpisodeSourceMessage("user", ConversationMessageRole.USER, "fixture user event"),
            EpisodeSourceMessage("assistant", ConversationMessageRole.ASSISTANT, "fixture reply"),
        ),
    )

    response = asyncio.run(ControlledRecallEpisodeProvider().generate_structured(request))

    assert response.formation_method == "benchmark.controlled_episode.v1"
    assert response.proposal.should_create is True
    assert response.proposal.evidence[0].message_id == "user"
    assert response.proposal.evidence[0].quote == "fixture user event"


def test_distributions_include_nearest_rank_p90_and_scenario_breakdown() -> None:
    values = [float(value) for value in range(1, 11)]

    assert distribution(values)["p90"] == 9.0
    summary = summarize_samples([sample("social", value) for value in values])
    assert summary["social"]["appraisal_ms"]["median"] == 5.5
    assert summary["social"]["committed_reply_ms"]["max"] == 30.0
    assert summary["social"]["appraisal_prompt_tokens"]["median"] == 55.0
    assert summary["social"]["appraisal_output_tokens"]["p90"] == 9.0
    assert summary["social"]["generation_prompt_tokens"]["max"] == 200.0
    assert summary["social"]["generation_output_tokens"]["mean"] == 11.0


def test_stage81_inference_corpus_includes_current_relationship_prompt() -> None:
    scenarios = {
        scenario.scenario_id: scenario.user_text for scenario in INFERENCE_BENCHMARK_SCENARIOS_V1
    }

    assert scenarios["relationship_current"] == "Как ты ко мне относишься?"


@dataclass
class RecordingSessionStart:
    session_ids: list[str] = field(default_factory=list)

    def execute(self) -> SimpleNamespace:
        session_id = f"session-{len(self.session_ids) + 1}"
        self.session_ids.append(session_id)
        return SimpleNamespace(session_id=session_id)


@dataclass
class RecordingSessionClose:
    session_ids: list[str] = field(default_factory=list)

    def execute(self, session_id: str) -> None:
        self.session_ids.append(session_id)


@dataclass
class RecordingBenchmarkTalk:
    commands: list[TalkInput] = field(default_factory=list)
    session_texts: dict[str, list[str]] = field(default_factory=dict)

    async def execute(self, *, command: TalkInput) -> SatoriReply:
        if command.session_id is None:
            raise AssertionError("benchmark turns must use explicit sessions")
        prior = self.session_texts.setdefault(command.session_id, [])
        prior.append(command.user_text)
        repeated = 1
        for prior_text in reversed(prior[:-1]):
            if prior_text != command.user_text:
                break
            repeated += 1
        self.commands.append(command)
        return SatoriReply(
            text="metadata-only fixture reply",
            provider="fixture",
            model="fixture",
            finish_status="stop",
            usage=None,
            context_manifest=ConversationContextManifest(
                schema_version=12,
                policy_id="satori.conversation.behavior.v9",
                policy_schema_version=9,
                character_context_schema_version=12,
                included_sections=(
                    "behavior_policy",
                    "self_model",
                    "personality_expression",
                    "values",
                    "retrieved_episodic_memory",
                    "current_user_input",
                ),
                user_content_chars=len(command.user_text),
                retrieval_status="no_relevant_memory",
                consecutive_same_user_message_count=repeated,
            ),
            session_id=command.session_id,
            interaction_id=f"interaction-{len(self.commands)}",
            client_request_id=command.client_request_id,
            timings=TurnPhaseTimings(),
        )


def test_stage81_benchmark_isolates_warm_samples_and_reports_triple_greeting() -> None:
    start = RecordingSessionStart()
    close = RecordingSessionClose()
    talk = RecordingBenchmarkTalk()
    services = cast(
        ConversationServices,
        SimpleNamespace(
            start_session=start,
            close_session=close,
            talk=talk,
            post_response=SimpleNamespace(),
        ),
    )
    ids = SequenceIdGenerator(*(f"benchmark-id-{index}" for index in range(1, 20)))

    report = asyncio.run(
        run_inference_benchmark(
            services,
            ids,
            provider_models={"conversation": "fixture"},
            warm_repetitions=1,
            selected_scenario_ids=frozenset({"social_greeting"}),
        )
    )

    assert report["schema_version"] == 3
    assert report["benchmark_id"] == "satori.inference.stage81.v3"
    assert report["scenario_ids"] == ["social_greeting"]
    assert "exact_triple_greeting" not in report["distributions"]
    assert all("response_regeneration_ms" in sample for sample in report["samples"])
    assert all("duplicate_regeneration_ms" not in sample for sample in report["samples"])
    assert [command.session_id for command in talk.commands[:2]] == ["session-1", "session-2"]
    assert [sample["consecutive_same_user_message_count"] for sample in report["samples"]] == [
        1,
        1,
    ]

    coherence = report["coherence_probe"]
    assert coherence["probe_id"] == EXACT_TRIPLE_GREETING_PROBE_ID
    assert coherence["shared_session"] is True
    assert coherence["include_derived"] is False
    assert [command.session_id for command in talk.commands[2:]] == [
        "session-3",
        "session-3",
        "session-3",
    ]
    assert [command.user_text for command in talk.commands[2:]] == ["Привет"] * 3
    assert [sample["consecutive_same_user_message_count"] for sample in coherence["samples"]] == [
        1,
        2,
        3,
    ]
    assert close.session_ids == start.session_ids
    assert "Привет" not in json.dumps(report, ensure_ascii=False)


def test_benchmark_cli_exposes_inference_appraisal_and_scheduled_contention() -> None:
    parser = build_parser()

    assert parser.parse_args(["benchmark", "inference"]).benchmark_action == "inference"
    assert parser.parse_args(["benchmark", "appraisal"]).benchmark_action == "appraisal"
    contention = parser.parse_args(["benchmark", "contention", "--scheduled"])
    assert contention.benchmark_action == "contention"
    assert contention.scheduled is True


def test_appraisal_corpus_contains_all_required_semantic_situations() -> None:
    assert {scenario.scenario_id for scenario in APPRAISAL_EVALUATION_CORPUS_V1} == {
        "neutral_greeting",
        "positive_news",
        "strong_negative_news",
        "distressed_user",
        "insult",
        "joke",
        "uncertainty",
        "intellectual_question",
        "praise",
        "farewell",
    }
