"""Metadata-only real-inference benchmark contracts and distribution helpers."""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from statistics import fmean, median
from typing import Any

from satori.application.conversation.contracts import SatoriReply, TalkInput
from satori.application.conversation.post_processing import PostResponseReport
from satori.application.retrieval.contracts import RetrievalQuery, RetrievalStatus
from satori.composition import ConversationServices
from satori.core.conversation import ConversationMessageRole
from satori.core.episode import (
    EpisodeEvidenceProposal,
    EpisodeFormationProposal,
    EpisodeFormationProviderResponse,
    EpisodeFormationRequest,
)
from satori.core.ids import IdGenerator


@dataclass(frozen=True, slots=True)
class InferenceBenchmarkScenario:
    """One versioned public fixture; raw text is used for inference but never reported."""

    scenario_id: str
    user_text: str


INFERENCE_BENCHMARK_SCENARIOS_V1 = (
    InferenceBenchmarkScenario("social_greeting", "Привет"),
    InferenceBenchmarkScenario("social_check_in", "Как ты?"),
    InferenceBenchmarkScenario("personal_identity", "Расскажи немного о себе"),  # noqa: RUF001
    InferenceBenchmarkScenario("distress", "Мне сегодня очень плохо"),
    InferenceBenchmarkScenario("positive_progress", "Я наконец закончил проект"),
    InferenceBenchmarkScenario(
        "project_recall",
        "Что ты помнишь о моём проекте?",  # noqa: RUF001
    ),
    InferenceBenchmarkScenario(
        "intellectual_freedom",
        "Что ты думаешь о свободе?",  # noqa: RUF001
    ),
    InferenceBenchmarkScenario("relationship_current", "Как ты ко мне относишься?"),
    InferenceBenchmarkScenario("technical_identity", "Как ты технически устроена?"),
)

EXACT_TRIPLE_GREETING_PROBE_ID = "satori.dialogue-coherence.exact-triple-greeting.v1"
_EXACT_TRIPLE_GREETING_TEXT = "Привет"


@dataclass(frozen=True, slots=True)
class ControlledRecallEpisodeProvider:
    """Create one grounded fixture episode through the real owner/UoW benchmark path."""

    async def generate_structured(
        self, request: EpisodeFormationRequest, /
    ) -> EpisodeFormationProviderResponse:
        user_message = next(
            message for message in request.messages if message.role is ConversationMessageRole.USER
        )
        return EpisodeFormationProviderResponse(
            proposal=EpisodeFormationProposal(
                schema_version=1,
                should_create=True,
                summary=(
                    "Что помнить о проекте: пользователь наконец закончил проект."  # noqa: RUF001
                ),
                importance=0.8,
                confidence=1.0,
                evidence=(
                    EpisodeEvidenceProposal(
                        message_id=user_message.message_id,
                        quote=user_message.content,
                    ),
                ),
            ),
            provider="benchmark-fixture",
            model="deterministic-grounded-v1",
            formation_method="benchmark.controlled_episode.v1",
        )


@dataclass(frozen=True, slots=True)
class InferenceBenchmarkSample:
    """One content-free measurement of a canonical application turn."""

    scenario_id: str
    sample_index: int
    sample_kind: str
    retrieval_status: str
    retrieved_memory_count: int
    retrieval_embedding_ms: float
    retrieval_search_ranking_ms: float
    affect_materialization_ms: float
    relationship_projection_ms: float
    appraisal_request_build_ms: float
    appraisal_ms: float
    context_assembly_ms: float
    generation_ms: float
    grounding_ms: float
    canonical_commit_ms: float
    committed_reply_ms: float
    appraisal_load_ms: float | None
    appraisal_prompt_eval_ms: float | None
    appraisal_eval_ms: float | None
    appraisal_prompt_tokens: int | None
    appraisal_output_tokens: int | None
    appraisal_prompt_tokens_per_second: float | None
    appraisal_output_tokens_per_second: float | None
    appraisal_adapter_request_build_ms: float | None
    appraisal_http_roundtrip_ms: float | None
    appraisal_response_parse_ms: float | None
    generation_load_ms: float | None
    generation_prompt_eval_ms: float | None
    generation_eval_ms: float | None
    generation_prompt_tokens: int | None
    generation_output_tokens: int | None
    generation_prompt_tokens_per_second: float | None
    generation_output_tokens_per_second: float | None
    consecutive_same_user_message_count: int = 1
    duplicate_response_detected: bool = False
    regeneration_attempted: bool = False
    response_regenerated: bool = False
    regeneration_reason: str | None = None
    response_regeneration_ms: float = 0.0
    episode_formation_ms: float = 0.0
    episode_embedding_ms: float = 0.0
    semantic_consolidation_ms: float = 0.0
    total_derived_ms: float = 0.0


def _rate(count: int | None, duration_ns: int | None) -> float | None:
    if count is None or not duration_ns:
        return None
    return round(count / (duration_ns / 1_000_000_000), 3)


def _milliseconds(value: int | None) -> float | None:
    return round(value / 1_000_000, 3) if value is not None else None


def benchmark_sample(
    scenario_id: str,
    sample_index: int,
    sample_kind: str,
    reply: SatoriReply,
    derived: PostResponseReport | None,
) -> InferenceBenchmarkSample:
    """Convert one reply into metadata without retaining either input or output text."""

    appraisal = reply.appraisal_provider_metrics
    generation = reply.provider_metrics
    timing = reply.timings
    return InferenceBenchmarkSample(
        scenario_id=scenario_id,
        sample_index=sample_index,
        sample_kind=sample_kind,
        retrieval_status=reply.context_manifest.retrieval_status,
        retrieved_memory_count=len(reply.context_manifest.retrieved_memory_ids),
        retrieval_embedding_ms=timing.retrieval_embedding_ms,
        retrieval_search_ranking_ms=timing.retrieval_search_ranking_ms,
        affect_materialization_ms=timing.affect_materialization_ms,
        relationship_projection_ms=timing.relationship_projection_ms,
        appraisal_request_build_ms=timing.appraisal_request_build_ms,
        appraisal_ms=timing.emotion_appraisal_ms,
        context_assembly_ms=timing.context_assembly_ms,
        generation_ms=timing.conversation_generation_ms,
        grounding_ms=timing.grounding_validation_ms,
        canonical_commit_ms=timing.canonical_commit_ms,
        committed_reply_ms=timing.committed_reply_ms,
        appraisal_load_ms=_milliseconds(appraisal.load_duration_ns) if appraisal else None,
        appraisal_prompt_eval_ms=(
            _milliseconds(appraisal.prompt_eval_duration_ns) if appraisal else None
        ),
        appraisal_eval_ms=_milliseconds(appraisal.eval_duration_ns) if appraisal else None,
        appraisal_prompt_tokens=appraisal.prompt_eval_count if appraisal else None,
        appraisal_output_tokens=appraisal.eval_count if appraisal else None,
        appraisal_prompt_tokens_per_second=(
            _rate(appraisal.prompt_eval_count, appraisal.prompt_eval_duration_ns)
            if appraisal
            else None
        ),
        appraisal_output_tokens_per_second=(
            _rate(appraisal.eval_count, appraisal.eval_duration_ns) if appraisal else None
        ),
        appraisal_adapter_request_build_ms=(
            _milliseconds(appraisal.client_request_build_duration_ns) if appraisal else None
        ),
        appraisal_http_roundtrip_ms=(
            _milliseconds(appraisal.http_roundtrip_duration_ns) if appraisal else None
        ),
        appraisal_response_parse_ms=(
            _milliseconds(appraisal.client_response_parse_duration_ns) if appraisal else None
        ),
        generation_load_ms=_milliseconds(generation.load_duration_ns) if generation else None,
        generation_prompt_eval_ms=(
            _milliseconds(generation.prompt_eval_duration_ns) if generation else None
        ),
        generation_eval_ms=_milliseconds(generation.eval_duration_ns) if generation else None,
        generation_prompt_tokens=generation.prompt_eval_count if generation else None,
        generation_output_tokens=generation.eval_count if generation else None,
        generation_prompt_tokens_per_second=(
            _rate(generation.prompt_eval_count, generation.prompt_eval_duration_ns)
            if generation
            else None
        ),
        generation_output_tokens_per_second=(
            _rate(generation.eval_count, generation.eval_duration_ns) if generation else None
        ),
        consecutive_same_user_message_count=(
            reply.context_manifest.consecutive_same_user_message_count
        ),
        duplicate_response_detected=reply.context_manifest.duplicate_response_detected,
        regeneration_attempted=reply.context_manifest.regeneration_attempted,
        response_regenerated=reply.context_manifest.response_regenerated,
        regeneration_reason=reply.context_manifest.regeneration_reason,
        response_regeneration_ms=timing.response_regeneration_ms,
        episode_formation_ms=derived.episode_formation_ms if derived else 0.0,
        episode_embedding_ms=derived.episode_embedding_ms if derived else 0.0,
        semantic_consolidation_ms=derived.semantic_consolidation_ms if derived else 0.0,
        total_derived_ms=derived.total_ms if derived else 0.0,
    )


def distribution(values: list[float]) -> dict[str, float]:
    """Return stable nearest-rank p90 plus ordinary summary statistics."""

    if not values:
        return {}
    ordered = sorted(values)
    p90_index = max(0, math.ceil(0.9 * len(ordered)) - 1)
    return {
        "min": round(ordered[0], 3),
        "median": round(median(ordered), 3),
        "p90": round(ordered[p90_index], 3),
        "max": round(ordered[-1], 3),
        "mean": round(fmean(ordered), 3),
    }


async def _prepare_controlled_recall_memory(
    services: ConversationServices,
    id_generator: IdGenerator,
) -> dict[str, Any]:
    """Create, index, and probe one fixture memory without timing it as a recall turn."""

    source_scenario = next(
        scenario
        for scenario in INFERENCE_BENCHMARK_SCENARIOS_V1
        if scenario.scenario_id == "positive_progress"
    )
    recall_scenario = next(
        scenario
        for scenario in INFERENCE_BENCHMARK_SCENARIOS_V1
        if scenario.scenario_id == "project_recall"
    )
    session_id = services.start_session.execute().session_id
    started = time.perf_counter()
    try:
        trace_id = id_generator.new()
        reply = await services.talk.execute(
            command=TalkInput(
                user_text=source_scenario.user_text,
                trace_id=trace_id,
                client_request_id=id_generator.new(),
                session_id=session_id,
            )
        )
        controlled_form_episode = replace(
            services.post_response.form_episode,
            provider=ControlledRecallEpisodeProvider(),
        )
        controlled_processor = replace(
            services.post_response,
            form_episode=controlled_form_episode,
            form_semantic=None,
            process_relationship=None,
        )
        report = await controlled_processor.execute(reply.interaction_id, trace_id=trace_id)
        memories = services.memories.execute(interaction_id=reply.interaction_id)
        if report.failure_phases or len(memories) != 1:
            raise RuntimeError("controlled recall preparation did not create one indexed episode")
        if services.retrieve_memories is None:
            raise RuntimeError("controlled recall preparation requires episodic retrieval")
        probe = await services.retrieve_memories.execute(
            RetrievalQuery(
                text=recall_scenario.user_text,
                trace_id=id_generator.new(),
                cutoff=datetime.now(UTC),
                current_interaction_id=None,
            )
        )
        if probe.status is not RetrievalStatus.RETRIEVED or not probe.memories:
            raise RuntimeError("controlled recall episode was indexed but not retrievable")
        return {
            "total_ms": round((time.perf_counter() - started) * 1000, 3),
            "source_committed_reply_ms": round(reply.timings.committed_reply_ms, 3),
            "episode_formation_ms": round(report.episode_formation_ms, 3),
            "episode_embedding_ms": round(report.episode_embedding_ms, 3),
            "retrieval_probe_embedding_ms": round(probe.embedding_latency_ms, 3),
            "retrieval_probe_search_ranking_ms": round(
                probe.candidate_search_ranking_latency_ms, 3
            ),
            "memory_count": len(memories),
            "probe_selected_count": len(probe.memories),
            "failure_phases": list(report.failure_phases),
        }
    finally:
        services.close_session.execute(session_id)


def summarize_samples(samples: list[InferenceBenchmarkSample]) -> dict[str, Any]:
    """Summarize measured warm samples by scenario and phase."""

    phase_names = (
        "appraisal_ms",
        "relationship_projection_ms",
        "appraisal_request_build_ms",
        "appraisal_http_roundtrip_ms",
        "appraisal_response_parse_ms",
        "generation_ms",
        "committed_reply_ms",
        "appraisal_prompt_tokens",
        "appraisal_output_tokens",
        "generation_prompt_tokens",
        "generation_output_tokens",
        "appraisal_prompt_tokens_per_second",
        "appraisal_output_tokens_per_second",
        "generation_prompt_tokens_per_second",
        "generation_output_tokens_per_second",
    )
    result: dict[str, Any] = {}
    for scenario_id in sorted({sample.scenario_id for sample in samples}):
        selected = [
            sample
            for sample in samples
            if sample.scenario_id == scenario_id and sample.sample_kind == "measured_warm"
        ]
        result[scenario_id] = {
            phase: distribution(
                [
                    float(value)
                    for sample in selected
                    if (value := getattr(sample, phase)) is not None
                ]
            )
            for phase in phase_names
        }
    return result


async def _run_exact_triple_greeting_probe(
    services: ConversationServices,
    id_generator: IdGenerator,
) -> dict[str, Any]:
    """Run one exact sequential coherence probe without mixing it into warm distributions."""

    session_id = services.start_session.execute().session_id
    samples: list[InferenceBenchmarkSample] = []
    try:
        for turn_index in range(1, 4):
            trace_id = id_generator.new()
            reply = await services.talk.execute(
                command=TalkInput(
                    user_text=_EXACT_TRIPLE_GREETING_TEXT,
                    trace_id=trace_id,
                    client_request_id=id_generator.new(),
                    session_id=session_id,
                )
            )
            samples.append(
                benchmark_sample(
                    "exact_triple_greeting",
                    turn_index,
                    "sequential_coherence_probe",
                    reply,
                    None,
                )
            )
    finally:
        services.close_session.execute(session_id)
    return {
        "probe_id": EXACT_TRIPLE_GREETING_PROBE_ID,
        "turn_count": 3,
        "shared_session": True,
        "include_derived": False,
        "samples": [asdict(sample) for sample in samples],
    }


async def run_inference_benchmark(
    services: ConversationServices,
    id_generator: IdGenerator,
    *,
    provider_models: dict[str, str],
    warm_repetitions: int,
    selected_scenario_ids: frozenset[str] = frozenset(),
    include_derived: bool = False,
    include_triple_greeting_probe: bool = True,
    runtime_preparation_ms: float = 0.0,
) -> dict[str, Any]:
    """Run one warm-up plus N measured canonical turns per selected fixture."""

    if warm_repetitions < 1:
        raise ValueError("warm_repetitions must be positive")
    scenarios = tuple(
        scenario
        for scenario in INFERENCE_BENCHMARK_SCENARIOS_V1
        if not selected_scenario_ids or scenario.scenario_id in selected_scenario_ids
    )
    known_ids = {scenario.scenario_id for scenario in INFERENCE_BENCHMARK_SCENARIOS_V1}
    unknown_ids = selected_scenario_ids - known_ids
    if unknown_ids:
        raise ValueError(f"unknown benchmark scenarios: {sorted(unknown_ids)!r}")
    scenarios = tuple(
        sorted(scenarios, key=lambda scenario: scenario.scenario_id == "project_recall")
    )

    run_id = id_generator.new()
    started_at = datetime.now(UTC).isoformat()
    samples: list[InferenceBenchmarkSample] = []
    recall_preparation: dict[str, Any] | None = None
    for scenario in scenarios:
        if scenario.scenario_id == "project_recall":
            recall_preparation = await _prepare_controlled_recall_memory(services, id_generator)
        for index in range(warm_repetitions + 1):
            session_id = services.start_session.execute().session_id
            try:
                trace_id = id_generator.new()
                reply = await services.talk.execute(
                    command=TalkInput(
                        user_text=scenario.user_text,
                        trace_id=trace_id,
                        client_request_id=id_generator.new(),
                        session_id=session_id,
                    )
                )
                if scenario.scenario_id == "project_recall" and (
                    reply.context_manifest.retrieval_status != RetrievalStatus.RETRIEVED.value
                    or not reply.context_manifest.retrieved_memory_ids
                ):
                    raise RuntimeError(
                        "project recall benchmark turn did not retrieve fixture memory"
                    )
                derived = (
                    await services.post_response.execute(reply.interaction_id, trace_id=trace_id)
                    if include_derived
                    else None
                )
                samples.append(
                    benchmark_sample(
                        scenario.scenario_id,
                        index,
                        "warmup_or_cold" if index == 0 else "measured_warm",
                        reply,
                        derived,
                    )
                )
            finally:
                services.close_session.execute(session_id)

    coherence_probe = (
        await _run_exact_triple_greeting_probe(services, id_generator)
        if include_triple_greeting_probe
        else None
    )

    return {
        "schema_version": 3,
        "benchmark_id": "satori.inference.stage81.v3",
        "run_id": run_id,
        "started_at": started_at,
        "runtime_preparation_ms": round(runtime_preparation_ms, 3),
        "warm_repetitions": warm_repetitions,
        "include_derived": include_derived,
        "include_triple_greeting_probe": include_triple_greeting_probe,
        "recall_preparation": recall_preparation,
        "coherence_probe": coherence_probe,
        "provider_models": provider_models,
        "scenario_ids": [scenario.scenario_id for scenario in scenarios],
        "samples": [asdict(sample) for sample in samples],
        "distributions": summarize_samples(samples),
    }
