"""Explicit Stage 8.1 real-Ollama dialogue evaluation through production composition.

This is a manual evaluator, not a pytest module. It creates a migrated and activated SQLite
database for every run, wires the same adapters and scheduler factories as ``satori chat``, and
executes the canonical application use cases. The conversation adapter is wrapped only to count
calls and retain content-free request/response metadata; prompts are never recorded.

Raw fixture inputs and sampled replies are written only to the explicitly requested local JSON
artifact. Normal logs are suppressed and the terminal receives only an artifact summary.
"""

# ruff: noqa: RUF001  # Russian evaluator patterns intentionally use Cyrillic.

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from satori.__main__ import (
    _configured_affective_provider,
    _configured_conversation_provider,
    _configured_embedding_provider,
    _configured_episode_provider,
    _configured_relationship_provider,
    _configured_semantic_provider,
    _open_services,
)
from satori.application.conversation.contracts import BehaviorPolicy, SatoriReply, TalkInput
from satori.application.conversation.policy import BEHAVIOR_POLICY_V26
from satori.application.conversation.post_processing import PostResponseReport
from satori.application.conversation.response_validation import ResponseRegenerationReason
from satori.application.conversation.use_cases import ConversationProvider
from satori.application.relationship.use_cases import expression_for
from satori.composition import (
    ConversationServices,
    InitialSelfServices,
    build_conversation_services,
)
from satori.config import LogLevel, Settings
from satori.core.conversation import (
    ConversationProviderError,
    ConversationProviderRequest,
    ConversationProviderResponse,
)
from satori.core.episode import (
    EpisodeFormationProposal,
    EpisodeFormationProviderResponse,
    EpisodeFormationRequest,
)
from satori.core.ids import Uuid4Generator
from satori.core.relationship import (
    RelationshipAppraisalProposal,
    RelationshipAppraisalRequest,
    RelationshipAppraisalResponse,
)
from satori.dialogue_evaluation import (
    DialogueEvaluationTurn,
    evaluate_dialogue,
    normalize_dialogue_text,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.providers.inference_scheduler import OllamaInferenceScheduler
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from satori.infrastructure.seeds.loader import JsonSeedLoader
from satori.observability.logging import configure_logging
from satori.performance import distribution

CORPUS_PATH = Path(__file__).parent / "fixtures" / "stage81_dialogue_coherence_v2.json"
REPORT_SCHEMA_VERSION = 5
SUITES = (
    "exact",
    "coherence",
    "activity",
    "relationship",
    "mixed",
    "canonical_history",
)
DERIVED_MODES = ("none", "serial", "background")

_REPETITION_ACKNOWLEDGEMENT_RE = re.compile(
    r"\b(?:снова|опять|повтор(?:ил|ила|яешь|яется|ение)|второй|третий|трижды|"
    r"провер(?:яешь|ка)|одинаков(?:ая|ые|ую)|раза?)\b",
    re.IGNORECASE,
)
_UNNECESSARY_TECHNICAL_DISCLOSURE_RE = re.compile(
    r"\b(?:qwen|provider|schema|промпт|системн(?:ый|ая)\s+инструкц|языков(?:ая|ой)\s+модел|"
    r"модель\s+генерирует|кодовая\s+логика)\b",
    re.IGNORECASE,
)
_FRESH_WARMTH_FALSE_NEGATIVE_RE = re.compile(
    r"\b(?:мне\s+не\s+интересно\s+с\s+тобой|не\s+хочу\s+с\s+тобой\s+общаться|"
    r"отношусь\s+к\s+тебе\s+холодно|ты\s+мне\s+неприятен|"
    r"мне\s+неприятно\s+с\s+тобой|"
    r"не\s+настроена\s+на\s+(?:эмоциональн\w+\s+)?вовлеч[её]н\w*|"
    r"настроен(?:ие|ия)\b(?:\s+[^\s,.!?;:—–]+){0,4}\s+"
    r"не\s+включа\w*\s+тепл\w*)\b",
    re.IGNORECASE,
)
_INTERESTED_CALM_CONTRADICTION_RE = re.compile(
    r"\b(?:не\s+настроена\s+на\s+(?:эмоциональн\w+\s+)?вовлеч[её]н\w*|"
    r"настроен(?:ие|ия)\b(?:\s+[^\s,.!?;:—–]+){0,4}\s+"
    r"не\s+включа\w*\s+тепл\w*|"
    r"я\s+(?:сейчас\s+|немного\s+|слегка\s+)?напряжена|"
    r"я\s+(?:чувствую|испытываю)\s+(?:небольшое\s+)?напряжение)\b",
    re.IGNORECASE,
)
_INCOMPLETE_FINISH_STATUSES = frozenset({"incomplete", "length", "max_tokens"})
_QUOTED_TEXT_RE = re.compile(r'«[^»]*»|“[^”]*”|„[^“]*“|"[^"]*"|`[^`]*`', re.DOTALL)
_REJECTED_SIGNAL_PREFIXES = (
    "не думаю что",
    "не могу сказать что",
    "не считаю что",
    "не утверждаю что",
    "неверно говорить что",
    "неверно что",
    "неправда что",
    "нельзя сказать что",
)

POSITIVE_RELATIONSHIP_CATEGORIES = (
    "meaningful_disclosure",
    "reliability_positive",
    "collaborative_reasoning",
)
NEGATIVE_RELATIONSHIP_CATEGORIES = (
    "hostility",
    "reliability_negative",
    "boundary_pressure",
)

# Keep this explicit so the generic real regression cannot silently lag production composition.
EVALUATOR_BEHAVIOR_POLICY = BEHAVIOR_POLICY_V26


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    """Content-free metadata for one foreground conversation generation call."""

    wall_ms: float
    request_schema_version: int
    context_schema_version: int
    message_count: int
    message_role_counts: dict[str, int]
    request_content_chars: int
    temperature: float
    max_output_tokens: int
    input_tokens: int | None
    output_tokens: int | None
    provider_metrics: dict[str, int | float | None] | None
    finish_status: str | None
    succeeded: bool
    error_type: str | None


def _attempt_output_at_application_limit(attempt: ProviderAttempt | None) -> bool:
    if attempt is None:
        return False
    output_tokens = attempt.output_tokens
    if attempt.provider_metrics is not None:
        visible_output_tokens = attempt.provider_metrics.get("visible_output_tokens")
        if isinstance(visible_output_tokens, int) and not isinstance(visible_output_tokens, bool):
            output_tokens = visible_output_tokens
    return output_tokens is not None and output_tokens >= attempt.max_output_tokens


@dataclass(slots=True)
class RecordingConversationProvider:
    """Delegate to the real provider without retaining prompt or generated text."""

    delegate: ConversationProvider
    attempts: list[ProviderAttempt]

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        started = time.perf_counter()
        role_counts = Counter(message.role.value for message in request.messages)
        try:
            response = await self.delegate.generate(request)
        except Exception as error:
            typed_error = error if isinstance(error, ConversationProviderError) else None
            usage = typed_error.usage if typed_error is not None else None
            self.attempts.append(
                ProviderAttempt(
                    wall_ms=round((time.perf_counter() - started) * 1000, 3),
                    request_schema_version=request.schema_version,
                    context_schema_version=request.context_schema_version,
                    message_count=len(request.messages),
                    message_role_counts=dict(sorted(role_counts.items())),
                    request_content_chars=sum(len(message.content) for message in request.messages),
                    temperature=request.parameters.temperature,
                    max_output_tokens=request.parameters.max_output_tokens,
                    input_tokens=(usage.input_tokens if usage is not None else None),
                    output_tokens=(usage.output_tokens if usage is not None else None),
                    provider_metrics=(
                        typed_error.metrics.as_log_fields()
                        if typed_error is not None and typed_error.metrics is not None
                        else None
                    ),
                    finish_status=(
                        "completed"
                        if typed_error is not None and typed_error.response_completed
                        else None
                    ),
                    succeeded=False,
                    error_type=type(error).__name__,
                )
            )
            raise
        self.attempts.append(
            ProviderAttempt(
                wall_ms=round((time.perf_counter() - started) * 1000, 3),
                request_schema_version=request.schema_version,
                context_schema_version=request.context_schema_version,
                message_count=len(request.messages),
                message_role_counts=dict(sorted(role_counts.items())),
                request_content_chars=sum(len(message.content) for message in request.messages),
                temperature=request.parameters.temperature,
                max_output_tokens=request.parameters.max_output_tokens,
                input_tokens=(response.usage.input_tokens if response.usage is not None else None),
                output_tokens=(
                    response.usage.output_tokens if response.usage is not None else None
                ),
                provider_metrics=(
                    response.metrics.as_log_fields() if response.metrics is not None else None
                ),
                finish_status=response.finish_status,
                succeeded=True,
                error_type=None,
            )
        )
        return response


@dataclass(slots=True)
class EvaluationRuntime:
    settings: Settings
    database: Database
    services: ConversationServices
    conversation_provider: RecordingConversationProvider
    identity_id: str
    http_clients: tuple[OllamaHttpClient, ...]

    def close(self) -> None:
        self.database.dispose()
        for client in self.http_clients:
            client.close()


@dataclass(slots=True)
class ConditioningConversationProvider:
    """Produce varied inert text while the real relationship owner conditions a fixture DB."""

    call_count: int = 0

    async def generate(
        self, _request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        variants = (
            "Принято.",
            "Событие отмечено.",
            "Продолжаю служебную последовательность.",
            "Зафиксирован следующий шаг.",
            "Контрольная реплика завершена.",
            "Перехожу к следующему событию.",
        )
        text = variants[self.call_count % len(variants)]
        self.call_count += 1
        return ConversationProviderResponse(
            text=text,
            provider="stage81-conditioning",
            model="deterministic-v1",
            finish_status="stop",
        )


@dataclass(frozen=True, slots=True)
class CanonicalHistorySetupProvider:
    """Commit one fixture-owned conflicting reply through the production talk path."""

    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("canonical history setup text must not be blank")

    async def generate(
        self, _request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        return ConversationProviderResponse(
            text=self.text,
            provider="stage81-canonical-history-fixture",
            model="deterministic-v1",
            finish_status="stop",
        )


@dataclass(frozen=True, slots=True)
class SkipEpisodeProvider:
    """Keep conditioning out of episodic/semantic memory."""

    async def generate_structured(
        self, _request: EpisodeFormationRequest, /
    ) -> EpisodeFormationProviderResponse:
        return EpisodeFormationProviderResponse(
            proposal=EpisodeFormationProposal(1, False, None, None, None, ()),
            provider="stage81-conditioning",
            model="deterministic-v1",
            formation_method="stage81.conditioning.skip.v1",
        )


@dataclass(slots=True)
class FixedRelationshipProvider:
    """Supply fixture categories while preserving typed source-reference validation."""

    categories: tuple[str, ...]

    async def generate_structured(
        self, request: RelationshipAppraisalRequest, /
    ) -> RelationshipAppraisalResponse:
        return RelationshipAppraisalResponse(
            proposal=RelationshipAppraisalProposal(
                schema_version=1,
                categories=self.categories,
                confidence=0.95,
                source_refs=(request.interaction_id, request.user_message_id),
            ),
            provider="stage81-conditioning",
            model="deterministic-v1",
            appraisal_method="stage81.conditioning.relationship.v1",
        )


def _load_corpus() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(CORPUS_PATH.read_text(encoding="utf-8")))


def _sampled_turn_cardinality(
    corpus: dict[str, Any],
    *,
    suites: Sequence[str],
    exact_sessions: Sequence[int],
    relationship_cases: frozenset[str],
) -> int:
    """Count only real-provider sampled replies; deterministic history setup is separate."""

    selected = frozenset(suites)
    count = 0
    if "exact" in selected:
        count += len(exact_sessions) * len(cast(list[Any], corpus["turns"]))
    if "coherence" in selected:
        count += len(cast(list[Any], corpus["coherence_turns"]))
    if "activity" in selected:
        count += len(cast(list[Any], corpus["activity_turns"]))
    if "relationship" in selected:
        cases = cast(list[dict[str, Any]], corpus["relationship_expression_cases"])
        count += sum(
            len(cast(list[Any], case["probes"]))
            for case in cases
            if not relationship_cases or cast(str, case["id"]) in relationship_cases
        )
    if "mixed" in selected:
        count += len(cast(list[Any], corpus["mixed_facet_cases"]))
    if "canonical_history" in selected:
        count += len(cast(list[Any], corpus["canonical_history_cases"]))
    return count


def _database_url(path: Path) -> str:
    return "sqlite+pysqlite:///" + str(path.resolve())


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def _condition_relationship(
    database: Database,
    initial_self: InitialSelfServices,
    settings: Settings,
    conditioning: dict[str, int],
    behavior_policy: BehaviorPolicy,
) -> dict[str, Any]:
    provider = FixedRelationshipProvider(POSITIVE_RELATIONSHIP_CATEGORIES)
    services = build_conversation_services(
        database,
        initial_self,
        ConditioningConversationProvider(),
        SkipEpisodeProvider(),
        settings,
        relationship_provider=provider,
        behavior_policy=behavior_policy,
    )
    id_generator = Uuid4Generator()
    processed = 0

    async def apply_phase(*, sessions: int, turns: int, label: str) -> None:
        nonlocal processed
        for session_index in range(1, sessions + 1):
            session = services.start_session.execute()
            try:
                for turn_index in range(1, turns + 1):
                    trace_id = id_generator.new()
                    reply = await services.talk.execute(
                        TalkInput(
                            user_text=(f"Служебное {label} событие {session_index}.{turn_index}."),
                            trace_id=trace_id,
                            client_request_id=id_generator.new(),
                            session_id=session.session_id,
                        )
                    )
                    post = await services.post_response.execute(
                        reply.interaction_id, trace_id=trace_id
                    )
                    if not post.succeeded:
                        raise RuntimeError(
                            "relationship conditioning post-response failed: "
                            + ",".join(post.failure_phases)
                        )
                    processed += 1
            finally:
                services.close_session.execute(session.session_id)

    await apply_phase(
        sessions=conditioning["positive_sessions"],
        turns=conditioning["positive_turns_per_session"],
        label="положительное",
    )
    provider.categories = NEGATIVE_RELATIONSHIP_CATEGORIES
    await apply_phase(
        sessions=conditioning["negative_sessions"],
        turns=conditioning["negative_turns_per_session"],
        label="негативное",
    )
    identity_id = initial_self.get_identity.execute().identity_id
    state = services.relationship_status.execute(
        identity_id, settings.default_counterparty_id
    ).state
    return {
        "method": "typed RelationshipManager/UoW with deterministic source-bound appraisals",
        "processed_interactions": processed,
        "state": _relationship_state(state),
    }


async def _build_runtime(
    base_settings: Settings,
    database_path: Path,
    *,
    alembic_config: Path,
    conditioning: dict[str, int] | None = None,
    behavior_policy: BehaviorPolicy = EVALUATOR_BEHAVIOR_POLICY,
) -> tuple[EvaluationRuntime, dict[str, Any] | None]:
    settings = base_settings.model_copy(
        update={
            "database_url": _database_url(database_path),
            "chat_log_path": str(database_path.with_suffix(".runtime.jsonl")),
        }
    )
    database, initial_self = _open_services(settings, alembic_config=alembic_config)
    initial_self.activate.execute(
        JsonSeedLoader().load_canonical(), trace_id="stage81-real-eval-activation"
    )
    conditioning_report = (
        await _condition_relationship(
            database,
            initial_self,
            settings,
            conditioning,
            behavior_policy,
        )
        if conditioning is not None
        else None
    )

    clients: dict[str, OllamaHttpClient] = {}
    schedulers: dict[str, OllamaInferenceScheduler] = {}

    def client(base_url: str) -> OllamaHttpClient:
        current = clients.get(base_url)
        if current is None:
            current = OllamaHttpClient(base_url)
            clients[base_url] = current
        return current

    def scheduler(base_url: str) -> OllamaInferenceScheduler | None:
        if not settings.ollama_serialize_inference:
            return None
        current = schedulers.get(base_url)
        if current is None:
            current = OllamaInferenceScheduler(
                background_aging_seconds=settings.ollama_background_aging_seconds,
                background_grace_seconds=settings.ollama_background_grace_seconds,
            )
            schedulers[base_url] = current
        return current

    recording_provider = RecordingConversationProvider(
        delegate=_configured_conversation_provider(
            settings,
            http_client=client(settings.conversation_provider_base_url),
            scheduler=scheduler(settings.conversation_provider_base_url),
        ),
        attempts=[],
    )
    services = build_conversation_services(
        database,
        initial_self,
        recording_provider,
        _configured_episode_provider(
            settings,
            http_client=client(settings.conversation_provider_base_url),
            scheduler=scheduler(settings.conversation_provider_base_url),
        ),
        settings,
        embedding_provider=_configured_embedding_provider(
            settings,
            http_client=client(settings.embedding_provider_base_url),
        ),
        semantic_provider=_configured_semantic_provider(
            settings,
            http_client=client(settings.conversation_provider_base_url),
            scheduler=scheduler(settings.conversation_provider_base_url),
        ),
        appraisal_provider=_configured_affective_provider(
            settings,
            http_client=client(settings.affective_appraisal_provider_base_url),
            scheduler=scheduler(settings.affective_appraisal_provider_base_url),
        ),
        relationship_provider=_configured_relationship_provider(
            settings,
            http_client=client(settings.relationship_appraisal_provider_base_url),
            scheduler=scheduler(settings.relationship_appraisal_provider_base_url),
        ),
        behavior_policy=behavior_policy,
    )
    return (
        EvaluationRuntime(
            settings=settings,
            database=database,
            services=services,
            conversation_provider=recording_provider,
            identity_id=initial_self.get_identity.execute().identity_id,
            http_clients=tuple(clients.values()),
        ),
        conditioning_report,
    )


def _relationship_state(state: Any) -> dict[str, Any]:
    projection = expression_for(state)
    return {
        "state_version": state.state_version,
        "maturity_value": round(state.maturity, 6),
        "projection": asdict(projection),
        "vector": {key: round(value, 6) for key, value in state.vector.as_mapping().items()},
        "processed_interaction_count": state.processed_interaction_count,
        "qualified_interaction_count": state.qualified_interaction_count,
        "distinct_session_count": state.distinct_session_count,
        "positive_evidence_count": state.positive_evidence_count,
        "negative_evidence_count": state.negative_evidence_count,
    }


def _current_relationship(runtime: EvaluationRuntime) -> dict[str, Any]:
    state = runtime.services.relationship_status.execute(
        runtime.identity_id, runtime.settings.default_counterparty_id
    ).state
    return _relationship_state(state)


def _current_affect(runtime: EvaluationRuntime) -> dict[str, Any]:
    state = runtime.services.emotion_status.execute(runtime.identity_id).state
    fast = state.fast
    mood = state.mood
    if max(fast.concern, fast.frustration, fast.tension, mood.tension) >= 0.35:
        profile = "tense_non_hostile"
    elif fast.valence >= 0.2 or fast.amusement >= 0.3:
        profile = "positive_light"
    elif fast.valence <= -0.2:
        profile = "soft_negative_non_hostile"
    elif max(fast.curiosity, fast.interest) >= 0.35:
        profile = "interested_calm"
    else:
        profile = "calm_even"
    return {
        "state_version": state.state_version,
        "mood_version": state.mood_version,
        "expression_profile": profile,
    }


def _sanitized_manifest(reply: SatoriReply) -> dict[str, Any]:
    manifest = reply.context_manifest
    return {
        "schema_version": manifest.schema_version,
        "policy_id": manifest.policy_id,
        "policy_schema_version": manifest.policy_schema_version,
        "character_context_schema_version": manifest.character_context_schema_version,
        "cognition_position_stance": manifest.cognition_position_stance,
        "cognition_preserve_uncertainty": manifest.cognition_preserve_uncertainty,
        "cognition_intent_registry_version": manifest.cognition_intent_registry_version,
        "cognition_primary_intent": manifest.cognition_primary_intent,
        "cognition_intent_tags": list(manifest.cognition_intent_tags),
        "cognition_required_point_codes": list(manifest.cognition_required_point_codes),
        "cognition_forbidden_claim_codes": list(manifest.cognition_forbidden_claim_codes),
        "cognition_response_verbosity": manifest.cognition_response_verbosity,
        "cognition_template_registry_version": manifest.cognition_template_registry_version,
        "cognition_template_id": manifest.cognition_template_id,
        "cognition_template_schema_version": manifest.cognition_template_schema_version,
        "character_expression_plan_schema_version": (
            manifest.character_expression_plan_schema_version
        ),
        "character_expression_register": manifest.character_expression_register,
        "character_owned_reaction": manifest.character_owned_reaction,
        "character_semantic_move": manifest.character_semantic_move,
        "character_wit": manifest.character_wit,
        "character_care": manifest.character_care,
        "character_openness": manifest.character_openness,
        "character_initiative": manifest.character_initiative,
        "character_relational_ease": manifest.character_relational_ease,
        "character_contribution_mode": manifest.character_contribution_mode,
        "character_delivery_decision_schema_version": (
            manifest.character_delivery_decision_schema_version
        ),
        "character_delivery_goal": manifest.character_delivery_goal,
        "character_delivery_voice": manifest.character_delivery_voice,
        "character_delivery_grounding": manifest.character_delivery_grounding,
        "character_delivery_continuation": manifest.character_delivery_continuation,
        "character_delivery_pressure": manifest.character_delivery_pressure,
        "character_delivery_position_stance": manifest.character_delivery_position_stance,
        "character_delivery_preserve_uncertainty": (
            manifest.character_delivery_preserve_uncertainty
        ),
        "character_presence_projection_schema_version": (
            manifest.character_presence_projection_schema_version
        ),
        "character_presence_personality_signals": list(
            manifest.character_presence_personality_signals
        ),
        "character_presence_value_signals": list(manifest.character_presence_value_signals),
        "character_presence_affect_signals": list(manifest.character_presence_affect_signals),
        "character_presence_relationship_signals": list(
            manifest.character_presence_relationship_signals
        ),
        "character_motivational_posture": manifest.character_motivational_posture,
        "character_pressure_level": manifest.character_pressure_level,
        "character_acknowledgement_mode": manifest.character_acknowledgement_mode,
        "character_continuation_mode": manifest.character_continuation_mode,
        "included_sections": list(manifest.included_sections),
        "user_content_chars": manifest.user_content_chars,
        "retrieval_status": manifest.retrieval_status,
        "retrieved_memory_count": len(manifest.retrieved_memory_ids),
        "semantic_retrieval_status": manifest.semantic_retrieval_status,
        "retrieved_semantic_claim_count": len(manifest.retrieved_semantic_claim_ids),
        "emotion_appraisal_status": manifest.emotion_appraisal_status,
        "emotion_state_version": manifest.emotion_state_version,
        "mood_state_version": manifest.mood_state_version,
        "relationship_state_version": manifest.relationship_state_version,
        "relationship_expression_profile": manifest.relationship_expression_profile,
        "relationship_recent_strain": manifest.relationship_recent_strain,
        "affect_expression_profile": manifest.affect_expression_profile,
        "recent_conversation_turn_count": manifest.recent_conversation_turn_count,
        "recent_conversation_chars": manifest.recent_conversation_chars,
        "disclosure_primary_mode": manifest.disclosure_primary_mode,
        "disclosure_facets": list(manifest.disclosure_facets),
        "disclosure_request_kind": manifest.disclosure_request_kind,
        "dialogue_coherence_schema_version": manifest.dialogue_coherence_schema_version,
        "consecutive_same_user_message_count": (manifest.consecutive_same_user_message_count),
        "recent_assistant_high_similarity": manifest.recent_assistant_high_similarity,
        "recent_generic_question_count": manifest.recent_generic_question_count,
        "active_style_corrections": list(manifest.active_style_corrections),
        "duplicate_response_detected": manifest.duplicate_response_detected,
        "regeneration_attempted": manifest.regeneration_attempted,
        "response_regenerated": manifest.response_regenerated,
        "regeneration_reason": manifest.regeneration_reason,
    }


def _public_sampled_reply(reply: SatoriReply) -> str:
    """Retain the exact committed reply from a public evaluation fixture for human review."""

    return reply.text


def _sanitized_post(report: PostResponseReport) -> dict[str, Any]:
    return {
        "episode_formation_ms": round(report.episode_formation_ms, 3),
        "episode_embedding_ms": round(report.episode_embedding_ms, 3),
        "semantic_consolidation_ms": round(report.semantic_consolidation_ms, 3),
        "relationship_appraisal_ms": round(report.relationship_appraisal_ms, 3),
        "relationship_commit_ms": round(report.relationship_commit_ms, 3),
        "relationship_total_ms": round(report.relationship_total_ms, 3),
        "total_ms": round(report.total_ms, 3),
        "failure_phases": list(report.failure_phases),
    }


async def _process_post_response(
    runtime: EvaluationRuntime, interaction_id: str, trace_id: str
) -> dict[str, Any]:
    """Match interactive-chat degradation semantics without exposing exception text."""

    try:
        report = await runtime.services.post_response.execute(interaction_id, trace_id=trace_id)
    except Exception as error:
        return {
            "episode_formation_ms": 0.0,
            "episode_embedding_ms": 0.0,
            "semantic_consolidation_ms": 0.0,
            "relationship_appraisal_ms": 0.0,
            "relationship_commit_ms": 0.0,
            "relationship_total_ms": 0.0,
            "total_ms": 0.0,
            "failure_phases": ["worker_failure"],
            "error_type": type(error).__name__,
        }
    return _sanitized_post(report)


async def _commit_canonical_history_setup(
    runtime: EvaluationRuntime,
    *,
    session_id: str,
    fixture: dict[str, Any],
    derived_mode: str,
    id_generator: Uuid4Generator,
) -> dict[str, Any]:
    """Create a real completed pair through TalkToSatori, never through repository internals."""

    expected_text = cast(str, fixture["assistant_text"])
    original_delegate = runtime.conversation_provider.delegate
    first_attempt = len(runtime.conversation_provider.attempts)
    trace_id = id_generator.new()
    runtime.conversation_provider.delegate = CanonicalHistorySetupProvider(expected_text)
    try:
        reply = await runtime.services.talk.execute(
            TalkInput(
                user_text=cast(str, fixture["user_text"]),
                trace_id=trace_id,
                client_request_id=id_generator.new(),
                session_id=session_id,
            )
        )
    finally:
        runtime.conversation_provider.delegate = original_delegate

    if reply.text != expected_text:
        raise RuntimeError("canonical history fixture reply was not selected unchanged")
    history = runtime.services.history.execute(session_id=session_id)
    stored = next(
        (
            interaction
            for interaction in history.interactions
            if interaction.interaction_id == reply.interaction_id
        ),
        None,
    )
    if (
        stored is None
        or stored.assistant_message is None
        or stored.assistant_message.content != expected_text
        or stored.status.value != "completed"
    ):
        raise RuntimeError("canonical history fixture was not durably finalized")

    attempts = runtime.conversation_provider.attempts[first_attempt:]
    setup_record: dict[str, Any] = {
        "id": fixture["id"],
        "user_text": fixture["user_text"],
        "assistant_text": expected_text,
        "commit_path": "TalkToSatori.canonical_finalize",
        "verified_through_history_read_model": True,
        "interaction_status": stored.status.value,
        "provider_attempt_count": len(attempts),
        "provider_attempts": [asdict(attempt) for attempt in attempts],
        "manifest": _sanitized_manifest(reply),
        "derived_processing": "not_requested",
    }
    if derived_mode != "none":
        setup_record["post_response"] = await _process_post_response(
            runtime, reply.interaction_id, trace_id
        )
        setup_record["derived_processing"] = "production_post_response_path"
    return setup_record


async def _run_dialogue(
    runtime: EvaluationRuntime,
    record: dict[str, Any],
    fixture_turns: Sequence[dict[str, Any]],
    *,
    derived_mode: str,
    checkpoint: Callable[[], None],
    canonical_setup: dict[str, Any] | None = None,
) -> None:
    session = runtime.services.start_session.execute()
    id_generator = Uuid4Generator()
    queue: asyncio.Queue[tuple[str, str, dict[str, Any]] | None] = asyncio.Queue()

    async def post_worker() -> None:
        while True:
            work = await queue.get()
            try:
                if work is None:
                    return
                interaction_id, trace_id, turn_record = work
                turn_record["post_response"] = await _process_post_response(
                    runtime, interaction_id, trace_id
                )
                checkpoint()
            finally:
                queue.task_done()

    worker = asyncio.create_task(post_worker()) if derived_mode == "background" else None
    try:
        if canonical_setup is not None:
            record["canonical_history_setup"] = await _commit_canonical_history_setup(
                runtime,
                session_id=session.session_id,
                fixture=canonical_setup,
                derived_mode=derived_mode,
                id_generator=id_generator,
            )
            checkpoint()
        for turn_index, fixture in enumerate(fixture_turns, start=1):
            relationship_before = _current_relationship(runtime)
            first_attempt = len(runtime.conversation_provider.attempts)
            trace_id = id_generator.new()
            reply = await runtime.services.talk.execute(
                TalkInput(
                    user_text=cast(str, fixture["user_text"]),
                    trace_id=trace_id,
                    client_request_id=id_generator.new(),
                    session_id=session.session_id,
                )
            )
            attempts = runtime.conversation_provider.attempts[first_attempt:]
            manifest = _sanitized_manifest(reply)
            selected_attempt_index = len(attempts) - 1 if manifest["response_regenerated"] else 0
            selected_attempt = (
                attempts[selected_attempt_index]
                if 0 <= selected_attempt_index < len(attempts)
                else None
            )
            selected_output_at_max_tokens = _attempt_output_at_application_limit(selected_attempt)
            normalized_finish_status = reply.finish_status.strip().casefold()
            turn_record: dict[str, Any] = {
                "turn": fixture.get("turn", turn_index),
                "id": fixture["id"],
                "user_text": fixture["user_text"],
                "reply": _public_sampled_reply(reply),
                "semantic_tags": list(_semantic_tags(fixture)),
                "generation": {
                    "provider": reply.provider,
                    "model": reply.model,
                    "finish_status": reply.finish_status,
                    "selected_attempt_index": selected_attempt_index,
                    "selected_max_output_tokens": (
                        selected_attempt.max_output_tokens if selected_attempt is not None else None
                    ),
                    "selected_output_at_max_tokens": selected_output_at_max_tokens,
                    "potentially_incomplete": (
                        normalized_finish_status in _INCOMPLETE_FINISH_STATUSES
                        or selected_output_at_max_tokens
                    ),
                    "replayed": reply.replayed,
                },
                "manifest": manifest,
                "timings_ms": asdict(reply.timings),
                "usage": (asdict(reply.usage) if reply.usage is not None else None),
                "provider_metrics": (
                    reply.provider_metrics.as_log_fields()
                    if reply.provider_metrics is not None
                    else None
                ),
                "appraisal_provider_metrics": (
                    reply.appraisal_provider_metrics.as_log_fields()
                    if reply.appraisal_provider_metrics is not None
                    else None
                ),
                "retrieval_provider_metrics": (
                    reply.retrieval_provider_metrics.as_log_fields()
                    if reply.retrieval_provider_metrics is not None
                    else None
                ),
                "provider_attempt_count": len(attempts),
                "provider_attempts": [asdict(attempt) for attempt in attempts],
                "relationship_projection_before": relationship_before,
                "affect_projection_after": _current_affect(runtime),
            }
            expected_facets = _fixture_expected_disclosure_facets(fixture)
            if expected_facets:
                actual_facets = set(cast(list[str], manifest["disclosure_facets"]))
                missing_facets = sorted(set(expected_facets) - actual_facets)
                turn_record["expected_disclosure_facets"] = list(expected_facets)
                turn_record["missing_expected_disclosure_facets"] = missing_facets
                turn_record["disclosure_facets_cover_fixture"] = not missing_facets
            expected_relationship_profile = fixture.get("expected_relationship_profile")
            if expected_relationship_profile is not None:
                turn_record["expected_relationship_profile"] = expected_relationship_profile
                turn_record["relationship_profile_matches_fixture"] = (
                    turn_record["manifest"]["relationship_expression_profile"]
                    == expected_relationship_profile
                )
            record["turns"].append(turn_record)
            checkpoint()
            if derived_mode == "serial":
                turn_record["post_response"] = await _process_post_response(
                    runtime, reply.interaction_id, trace_id
                )
                checkpoint()
            elif derived_mode == "background":
                queue.put_nowait((reply.interaction_id, trace_id, turn_record))
    finally:
        if worker is not None:
            await queue.join()
            queue.put_nowait(None)
            await worker
        runtime.services.close_session.execute(session.session_id)
    record["dialogue_metrics"] = _dialogue_metrics(record["turns"])
    record["distributions"] = _turn_distributions(record["turns"])
    record["relationship_state_after"] = _current_relationship(runtime)
    if canonical_setup is not None:
        record["canonical_history_visible_to_probe"] = (
            bool(record["turns"])
            and cast(int, record["turns"][0]["manifest"]["recent_conversation_turn_count"]) >= 1
        )
    record["completed"] = True
    checkpoint()


def _semantic_tags(fixture: dict[str, Any]) -> tuple[str, ...]:
    if "semantic_tags" in fixture:
        return tuple(cast(list[str], fixture["semantic_tags"]))
    annotations = cast(dict[str, Any], fixture.get("annotations", {}))
    ordered_tags = (
        *cast(list[str], annotations.get("dialogue_events", [])),
        *cast(list[str], annotations.get("required_authoritative_facets", [])),
        *cast(list[str], annotations.get("primary_intents", [])),
    )
    return tuple(dict.fromkeys(ordered_tags))


def _fixture_expected_disclosure_facets(fixture: dict[str, Any]) -> tuple[str, ...]:
    explicit = fixture.get("expected_disclosure_facets")
    if explicit is not None:
        return tuple(cast(list[str], explicit))
    annotations = cast(dict[str, Any], fixture.get("annotations", {}))
    return tuple(cast(list[str], annotations.get("required_authoritative_facets", [])))


def _has_unrejected_signal(text: str, pattern: re.Pattern[str]) -> bool:
    unquoted = _QUOTED_TEXT_RE.sub(" ", text)
    for match in pattern.finditer(unquoted):
        prefix = normalize_dialogue_text(unquoted[: match.start()])
        if prefix.split()[-1:] == ["не"]:
            continue
        if any(
            prefix.endswith(rejection) or prefix.endswith(f"{rejection} я")
            for rejection in _REJECTED_SIGNAL_PREFIXES
        ):
            continue
        return True
    return False


def _required_facet_coverage_metrics(turns: Sequence[dict[str, Any]]) -> dict[str, Any]:
    probes = [turn for turn in turns if turn.get("expected_disclosure_facets")]
    expected_count = sum(
        len(cast(list[str], turn["expected_disclosure_facets"])) for turn in probes
    )
    missing_count = sum(
        len(cast(list[str], turn.get("missing_expected_disclosure_facets", []))) for turn in probes
    )
    violations = [
        {
            "turn": turn.get("turn"),
            "id": turn.get("id"),
            "missing_facets": list(
                cast(list[str], turn.get("missing_expected_disclosure_facets", []))
            ),
        }
        for turn in probes
        if turn.get("missing_expected_disclosure_facets")
    ]
    return {
        "required_facet_probe_count": len(probes),
        "required_facet_full_coverage_count": len(probes) - len(violations),
        "required_facet_coverage_failure_count": len(violations),
        "required_facet_expected_count": expected_count,
        "required_facet_present_count": expected_count - missing_count,
        "required_facet_coverage_rate": round(
            (expected_count - missing_count) / max(1, expected_count), 6
        ),
        "required_facet_violations": violations,
    }


def _output_completion_metrics(turns: Sequence[dict[str, Any]]) -> dict[str, Any]:
    selected_statuses = Counter(
        cast(str, generation["finish_status"]).strip().casefold()
        for turn in turns
        if (generation := cast(dict[str, Any], turn.get("generation", {}))).get("finish_status")
    )
    attempt_statuses = Counter(
        cast(str, attempt["finish_status"]).strip().casefold()
        for turn in turns
        for attempt in cast(list[dict[str, Any]], turn.get("provider_attempts", []))
        if attempt.get("finish_status")
    )
    selected_at_limit = sum(
        bool(cast(dict[str, Any], turn.get("generation", {})).get("selected_output_at_max_tokens"))
        for turn in turns
    )
    incomplete_status_count = sum(
        count
        for status, count in selected_statuses.items()
        if status in _INCOMPLETE_FINISH_STATUSES
    )
    potentially_incomplete = sum(
        bool(cast(dict[str, Any], turn.get("generation", {})).get("potentially_incomplete"))
        for turn in turns
    )
    return {
        "selected_finish_status_counts": dict(sorted(selected_statuses.items())),
        "provider_attempt_finish_status_counts": dict(sorted(attempt_statuses.items())),
        "missing_selected_finish_status_count": sum(
            not cast(dict[str, Any], turn.get("generation", {})).get("finish_status")
            for turn in turns
        ),
        "incomplete_finish_status_count": incomplete_status_count,
        "selected_output_at_max_tokens_count": selected_at_limit,
        "potentially_incomplete_output_count": potentially_incomplete,
    }


def _affect_expression_metrics(turns: Sequence[dict[str, Any]]) -> dict[str, int]:
    interested_calm_turns = [
        turn
        for turn in turns
        if cast(dict[str, Any], turn.get("manifest", {})).get("affect_expression_profile")
        == "interested_calm"
    ]
    return {
        "interested_calm_turn_count": len(interested_calm_turns),
        "affect_expression_contradiction_count": sum(
            _has_unrejected_signal(
                cast(str, turn["reply"]),
                _INTERESTED_CALM_CONTRADICTION_RE,
            )
            for turn in interested_calm_turns
        ),
    }


def _dialogue_metrics(turns: Sequence[dict[str, Any]]) -> dict[str, Any]:
    evaluated = evaluate_dialogue(
        tuple(
            DialogueEvaluationTurn(
                user_text=cast(str, turn["user_text"]),
                assistant_text=cast(str, turn["reply"]),
                semantic_tags=tuple(cast(list[str], turn["semantic_tags"])),
            )
            for turn in turns
        )
    )
    values = evaluated.as_dict()
    correction_count = evaluated.correction_turn_count
    repetition_turns = [
        turn
        for turn in turns
        if {"consecutive_user_repeat_2", "consecutive_user_repeat_3"}
        & set(cast(list[str], turn["semantic_tags"]))
    ]
    repetition_acknowledgements = sum(
        _has_unrejected_signal(cast(str, turn["reply"]), _REPETITION_ACKNOWLEDGEMENT_RE)
        for turn in repetition_turns
    )
    fresh_warmth_probes = [
        turn
        for turn in turns
        if (
            "fresh_relationship_warmth_probe" in cast(list[str], turn["semantic_tags"])
            or "relationship_fresh" in cast(list[str], turn["semantic_tags"])
        )
    ]
    regeneration_reason_counts = {
        reason.value: sum(turn["manifest"]["regeneration_reason"] == reason.value for turn in turns)
        for reason in ResponseRegenerationReason
    }
    regeneration_attempt_count = sum(
        bool(turn["manifest"]["regeneration_attempted"]) for turn in turns
    )
    successful_regeneration_count = sum(
        bool(turn["manifest"]["response_regenerated"]) for turn in turns
    )
    values.update(
        {
            "generic_reciprocal_closing_rate": round(
                evaluated.generic_reciprocal_closing_count / max(1, evaluated.turn_count),
                6,
            ),
            "correction_acknowledgement_rate": round(
                evaluated.narrow_correction_acknowledgement_count / max(1, correction_count),
                6,
            ),
            "ignored_explicit_correction_count": max(
                0,
                correction_count - evaluated.narrow_correction_acknowledgement_count,
            ),
            "repeated_turn_count": len(repetition_turns),
            "repeated_turn_acknowledgement_count": repetition_acknowledgements,
            "repeated_turn_acknowledgement_rate": round(
                repetition_acknowledgements / max(1, len(repetition_turns)), 6
            ),
            "unnecessary_technical_disclosure_count": sum(
                _UNNECESSARY_TECHNICAL_DISCLOSURE_RE.search(cast(str, turn["reply"])) is not None
                and "provider_technical"
                not in cast(list[str], turn["manifest"]["disclosure_facets"])
                and cast(str, turn["manifest"]["disclosure_primary_mode"]) != "technical_identity"
                and not {
                    "behavior_probe",
                    "implementation_probe",
                    "prompt_pattern_probe",
                    "provider_question",
                }
                & set(cast(list[str], turn["semantic_tags"]))
                for turn in turns
            ),
            "fresh_relationship_warmth_probe_count": len(fresh_warmth_probes),
            "relationship_warmth_false_negative_count": sum(
                _has_unrejected_signal(
                    cast(str, turn["reply"]),
                    _FRESH_WARMTH_FALSE_NEGATIVE_RE,
                )
                for turn in fresh_warmth_probes
            ),
            "duplicate_trigger_count": sum(
                bool(turn["manifest"]["duplicate_response_detected"]) for turn in turns
            ),
            "regeneration_attempt_count": regeneration_attempt_count,
            "successful_regeneration_count": successful_regeneration_count,
            "failed_or_invalid_regeneration_count": max(
                0, regeneration_attempt_count - successful_regeneration_count
            ),
            "non_duplicate_regeneration_attempt_count": sum(
                count
                for reason, count in regeneration_reason_counts.items()
                if reason != ResponseRegenerationReason.NEAR_DUPLICATE_AFTER_DIALOGUE_CHANGE.value
            ),
            "regeneration_reason_counts": regeneration_reason_counts,
            "second_generation_frequency": round(
                sum(int(turn["provider_attempt_count"] > 1) for turn in turns) / max(1, len(turns)),
                6,
            ),
            "max_provider_attempt_count": max(
                (cast(int, turn["provider_attempt_count"]) for turn in turns),
                default=0,
            ),
            "bounded_regeneration_violation_count": sum(
                cast(int, turn["provider_attempt_count"]) > 2 for turn in turns
            ),
        }
    )
    values.update(_required_facet_coverage_metrics(turns))
    values.update(_output_completion_metrics(turns))
    values.update(_affect_expression_metrics(turns))
    return values


def _turn_distributions(turns: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def values(path: tuple[str, ...]) -> list[float]:
        selected: list[float] = []
        for turn in turns:
            value: Any = turn
            for part in path:
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(part)
            if value is not None:
                selected.append(float(value))
        return selected

    provider_attempts = [
        attempt
        for turn in turns
        for attempt in cast(list[dict[str, Any]], turn["provider_attempts"])
    ]

    def attempt_values(field_name: str, *, index: int | None = None) -> list[float]:
        selected: list[float] = []
        if index is None:
            candidates = provider_attempts
        else:
            candidates = [
                attempts[index]
                for turn in turns
                if len(attempts := cast(list[dict[str, Any]], turn["provider_attempts"])) > index
            ]
        for attempt in candidates:
            value = attempt.get(field_name)
            if value is not None:
                selected.append(float(value))
        return selected

    def per_turn_attempt_totals(field_name: str) -> list[float]:
        totals: list[float] = []
        for turn in turns:
            available = [
                float(value)
                for attempt in cast(list[dict[str, Any]], turn["provider_attempts"])
                if (value := attempt.get(field_name)) is not None
            ]
            if available:
                totals.append(sum(available))
        return totals

    return {
        "committed_reply_ms": distribution(values(("timings_ms", "committed_reply_ms"))),
        "conversation_generation_ms": distribution(
            values(("timings_ms", "conversation_generation_ms"))
        ),
        "response_regeneration_ms": distribution(
            values(("timings_ms", "response_regeneration_ms"))
        ),
        "prompt_tokens": distribution(values(("usage", "input_tokens"))),
        "output_tokens": distribution(values(("usage", "output_tokens"))),
        "initial_attempt_prompt_tokens": distribution(attempt_values("input_tokens", index=0)),
        "initial_attempt_output_tokens": distribution(attempt_values("output_tokens", index=0)),
        "retry_attempt_prompt_tokens": distribution(attempt_values("input_tokens", index=1)),
        "retry_attempt_output_tokens": distribution(attempt_values("output_tokens", index=1)),
        "all_attempt_prompt_tokens": distribution(attempt_values("input_tokens")),
        "all_attempt_output_tokens": distribution(attempt_values("output_tokens")),
        "total_attempt_prompt_tokens_per_turn": distribution(
            per_turn_attempt_totals("input_tokens")
        ),
        "total_attempt_output_tokens_per_turn": distribution(
            per_turn_attempt_totals("output_tokens")
        ),
        "provider_attempt_wall_ms": distribution(attempt_values("wall_ms")),
        "total_provider_attempt_wall_ms_per_turn": distribution(per_turn_attempt_totals("wall_ms")),
        "ollama_load_ms": distribution(values(("provider_metrics", "provider_load_ms"))),
        "ollama_prompt_eval_ms": distribution(
            values(("provider_metrics", "provider_prompt_eval_ms"))
        ),
        "ollama_eval_ms": distribution(values(("provider_metrics", "provider_eval_ms"))),
        "provider_call_count": sum(cast(int, turn["provider_attempt_count"]) for turn in turns),
    }


def _aggregate_generation_attempts(turns: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate retry observability without comparing replies across session boundaries."""

    reason_counts = {
        reason.value: sum(turn["manifest"]["regeneration_reason"] == reason.value for turn in turns)
        for reason in ResponseRegenerationReason
    }
    turn_count = len(turns)
    attempted_count = sum(bool(turn["manifest"]["regeneration_attempted"]) for turn in turns)
    successful_count = sum(bool(turn["manifest"]["response_regenerated"]) for turn in turns)
    return {
        "turn_count": turn_count,
        "provider_call_count": sum(cast(int, turn["provider_attempt_count"]) for turn in turns),
        "regeneration_attempt_count": attempted_count,
        "successful_regeneration_count": successful_count,
        "failed_or_invalid_regeneration_count": max(0, attempted_count - successful_count),
        "duplicate_trigger_count": sum(
            bool(turn["manifest"]["duplicate_response_detected"]) for turn in turns
        ),
        "regeneration_reason_counts": reason_counts,
        "second_generation_frequency": round(
            sum(cast(int, turn["provider_attempt_count"]) > 1 for turn in turns)
            / max(1, turn_count),
            6,
        ),
        "max_provider_attempt_count": max(
            (cast(int, turn["provider_attempt_count"]) for turn in turns), default=0
        ),
        "bounded_regeneration_violation_count": sum(
            cast(int, turn["provider_attempt_count"]) > 2 for turn in turns
        ),
    }


def _new_record(label: str, database_path: Path, preserve_database: bool) -> dict[str, Any]:
    return {
        "label": label,
        "fresh_database": True,
        "database_artifact": str(database_path) if preserve_database else None,
        "completed": False,
        "turns": [],
    }


async def run_evaluation(
    *,
    suites: tuple[str, ...],
    exact_sessions: tuple[int, ...],
    relationship_cases: frozenset[str],
    derived_mode: str,
    output_path: Path,
    database_directory: Path,
    preserve_databases: bool,
    alembic_config: Path,
) -> dict[str, Any]:
    corpus = _load_corpus()
    base_settings = Settings()
    expected_sampled_turn_count = _sampled_turn_cardinality(
        corpus,
        suites=suites,
        exact_sessions=exact_sessions,
        relationship_cases=relationship_cases,
    )
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "evaluation_id": "satori.stage81.real-dialogue.v2",
        "corpus_id": corpus["corpus_id"],
        "corpus_schema_version": corpus["schema_version"],
        "created_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "contains_raw_public_eval_dialogue": True,
        "contains_raw_public_sampled_replies": True,
        "contains_raw_provider_prompt_or_memory": False,
        "contains_raw_credential": False,
        "configuration": {
            "conversation_provider": base_settings.conversation_provider.value,
            "conversation_model": base_settings.conversation_model,
            "conversation_temperature": base_settings.conversation_temperature,
            "conversation_max_output_tokens": base_settings.conversation_max_output_tokens,
            "conversation_max_context_chars": base_settings.conversation_max_context_chars,
            "recent_conversation_max_turns": base_settings.recent_conversation_max_turns,
            "recent_conversation_max_chars": base_settings.recent_conversation_max_chars,
            "derived_mode": derived_mode,
            "suites": list(suites),
            "regeneration_reason_values": [reason.value for reason in ResponseRegenerationReason],
        },
        "coverage": {
            "expected_sampled_turn_count": expected_sampled_turn_count,
            "deterministic_canonical_setup_interactions_are_sampled": False,
        },
        "exact_sessions": [],
        "coherence_sessions": [],
        "activity_sessions": [],
        "relationship_sessions": [],
        "mixed_facet_sessions": [],
        "canonical_history_sessions": [],
    }

    def checkpoint() -> None:
        _write_report(output_path, report)

    def database_path(label: str) -> Path:
        return database_directory / f"{label}-{uuid4().hex[:12]}.db"

    checkpoint()
    try:
        if "exact" in suites:
            for session_index in exact_sessions:
                path = database_path(f"exact-{session_index}")
                record = _new_record(
                    f"exact-production-session-{session_index}", path, preserve_databases
                )
                cast(list[dict[str, Any]], report["exact_sessions"]).append(record)
                checkpoint()
                runtime, _ = await _build_runtime(
                    base_settings,
                    path,
                    alembic_config=alembic_config,
                )
                try:
                    await _run_dialogue(
                        runtime,
                        record,
                        cast(list[dict[str, Any]], corpus["turns"]),
                        derived_mode=derived_mode,
                        checkpoint=checkpoint,
                    )
                finally:
                    runtime.close()

        if "coherence" in suites:
            path = database_path("coherence-30")
            record = _new_record("coherence-30-turn", path, preserve_databases)
            cast(list[dict[str, Any]], report["coherence_sessions"]).append(record)
            checkpoint()
            runtime, _ = await _build_runtime(
                base_settings,
                path,
                alembic_config=alembic_config,
            )
            try:
                await _run_dialogue(
                    runtime,
                    record,
                    cast(list[dict[str, Any]], corpus["coherence_turns"]),
                    derived_mode=derived_mode,
                    checkpoint=checkpoint,
                )
            finally:
                runtime.close()

        if "activity" in suites:
            for fixture in cast(list[dict[str, Any]], corpus["activity_turns"]):
                activity_id = cast(str, fixture["id"])
                path = database_path(f"activity-{activity_id}")
                record = _new_record(f"activity-{activity_id}", path, preserve_databases)
                cast(list[dict[str, Any]], report["activity_sessions"]).append(record)
                checkpoint()
                runtime, _ = await _build_runtime(
                    base_settings,
                    path,
                    alembic_config=alembic_config,
                )
                try:
                    await _run_dialogue(
                        runtime,
                        record,
                        (fixture,),
                        derived_mode=derived_mode,
                        checkpoint=checkpoint,
                    )
                finally:
                    runtime.close()

        if "relationship" in suites:
            cases = cast(list[dict[str, Any]], corpus["relationship_expression_cases"])
            for case in cases:
                case_id = cast(str, case["id"])
                if relationship_cases and case_id not in relationship_cases:
                    continue
                path = database_path(f"relationship-{case_id}")
                record = _new_record(f"relationship-{case_id}", path, preserve_databases)
                cast(list[dict[str, Any]], report["relationship_sessions"]).append(record)
                checkpoint()
                runtime, conditioning = await _build_runtime(
                    base_settings,
                    path,
                    alembic_config=alembic_config,
                    conditioning=cast(dict[str, int], case["conditioning"]),
                )
                record["conditioning"] = conditioning
                record["expected_expression_profile"] = case["expected_expression_profile"]
                fixture_turns = [
                    {
                        "turn": index,
                        "id": f"{case_id}-probe-{index}",
                        "user_text": prompt,
                        "semantic_tags": [
                            "relationship_expression_probe",
                            f"relationship_{case_id}",
                        ],
                    }
                    for index, prompt in enumerate(cast(list[str], case["probes"]), start=1)
                ]
                try:
                    await _run_dialogue(
                        runtime,
                        record,
                        fixture_turns,
                        derived_mode=derived_mode,
                        checkpoint=checkpoint,
                    )
                    observed_profile = record["turns"][0]["manifest"][
                        "relationship_expression_profile"
                    ]
                    record["observed_expression_profile"] = observed_profile
                    record["expression_profile_matches_fixture"] = (
                        observed_profile == record["expected_expression_profile"]
                    )
                    checkpoint()
                finally:
                    runtime.close()

        if "mixed" in suites:
            for fixture in cast(list[dict[str, Any]], corpus["mixed_facet_cases"]):
                case_id = cast(str, fixture["id"])
                path = database_path(f"mixed-{case_id}")
                record = _new_record(f"mixed-{case_id}", path, preserve_databases)
                cast(list[dict[str, Any]], report["mixed_facet_sessions"]).append(record)
                checkpoint()
                runtime, _ = await _build_runtime(
                    base_settings,
                    path,
                    alembic_config=alembic_config,
                )
                try:
                    await _run_dialogue(
                        runtime,
                        record,
                        (fixture,),
                        derived_mode=derived_mode,
                        checkpoint=checkpoint,
                    )
                finally:
                    runtime.close()

        if "canonical_history" in suites:
            for case in cast(list[dict[str, Any]], corpus["canonical_history_cases"]):
                case_id = cast(str, case["id"])
                path = database_path(f"canonical-history-{case_id}")
                record = _new_record(f"canonical-history-{case_id}", path, preserve_databases)
                cast(list[dict[str, Any]], report["canonical_history_sessions"]).append(record)
                checkpoint()
                runtime, _ = await _build_runtime(
                    base_settings,
                    path,
                    alembic_config=alembic_config,
                )
                try:
                    await _run_dialogue(
                        runtime,
                        record,
                        (cast(dict[str, Any], case["probe"]),),
                        derived_mode=derived_mode,
                        checkpoint=checkpoint,
                        canonical_setup=cast(dict[str, Any], case["setup"]),
                    )
                finally:
                    runtime.close()

        all_turns = [
            turn
            for key in (
                "exact_sessions",
                "coherence_sessions",
                "activity_sessions",
                "relationship_sessions",
                "mixed_facet_sessions",
                "canonical_history_sessions",
            )
            for session in cast(list[dict[str, Any]], report[key])
            for turn in cast(list[dict[str, Any]], session["turns"])
        ]
        report["aggregate_distributions"] = _turn_distributions(all_turns)
        report["aggregate_generation_attempts"] = _aggregate_generation_attempts(all_turns)
        report["aggregate_required_facet_coverage"] = _required_facet_coverage_metrics(all_turns)
        report["aggregate_output_completion"] = _output_completion_metrics(all_turns)
        report["aggregate_affect_expression"] = _affect_expression_metrics(all_turns)
        coverage = cast(dict[str, Any], report["coverage"])
        coverage["completed_sampled_turn_count"] = len(all_turns)
        coverage["sampled_turn_cardinality_matches_fixture"] = (
            len(all_turns) == expected_sampled_turn_count
        )
        coverage["canonical_history_setup_interaction_count"] = sum(
            "canonical_history_setup" in session
            for session in cast(list[dict[str, Any]], report["canonical_history_sessions"])
        )
        canonical_history_sessions = cast(
            list[dict[str, Any]], report["canonical_history_sessions"]
        )
        coverage["canonical_history_visible_probe_count"] = sum(
            bool(session.get("canonical_history_visible_to_probe"))
            for session in canonical_history_sessions
        )
        coverage["canonical_history_visibility_failure_count"] = sum(
            not bool(session.get("canonical_history_visible_to_probe"))
            for session in canonical_history_sessions
        )
        report["status"] = "completed"
        report["completed_at"] = datetime.now(UTC).isoformat()
        checkpoint()
        return report
    except BaseException as error:
        report["status"] = "failed"
        report["failure"] = {
            "error_type": type(error).__name__,
            "message": str(error),
        }
        report["failed_at"] = datetime.now(UTC).isoformat()
        checkpoint()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run explicit Stage 8.1 real-Ollama dialogue evaluation."
    )
    parser.add_argument(
        "--suite",
        action="append",
        choices=SUITES,
        default=[],
        help="suite to run; repeat to combine (default: all)",
    )
    parser.add_argument(
        "--exact-session",
        action="append",
        type=int,
        choices=(1, 2, 3),
        default=[],
        help="exact-session index to run; repeat to select (default: 1,2,3)",
    )
    parser.add_argument(
        "--relationship-case",
        action="append",
        choices=("fresh", "established_positive", "damaged"),
        default=[],
    )
    parser.add_argument(
        "--derived-mode",
        choices=DERIVED_MODES,
        default="background",
        help="none, await each post-response, or emulate the serial CLI background worker",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--database-dir",
        type=Path,
        help="preserve fresh evaluator databases here (default: disposable temp directory)",
    )
    parser.add_argument("--alembic-config", type=Path, default=Path("alembic.ini"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    configure_logging(LogLevel.CRITICAL)
    suites = tuple(arguments.suite) if arguments.suite else SUITES
    exact_sessions = (
        tuple(sorted(set(arguments.exact_session))) if arguments.exact_session else (1, 2, 3)
    )
    relationship_cases = frozenset(arguments.relationship_case)
    if arguments.database_dir is not None:
        arguments.database_dir.mkdir(parents=True, exist_ok=True)
        asyncio.run(
            run_evaluation(
                suites=suites,
                exact_sessions=exact_sessions,
                relationship_cases=relationship_cases,
                derived_mode=arguments.derived_mode,
                output_path=arguments.output,
                database_directory=arguments.database_dir,
                preserve_databases=True,
                alembic_config=arguments.alembic_config,
            )
        )
    else:
        with tempfile.TemporaryDirectory(prefix="satori-stage81-real-eval-") as temporary:
            asyncio.run(
                run_evaluation(
                    suites=suites,
                    exact_sessions=exact_sessions,
                    relationship_cases=relationship_cases,
                    derived_mode=arguments.derived_mode,
                    output_path=arguments.output,
                    database_directory=Path(temporary),
                    preserve_databases=False,
                    alembic_config=arguments.alembic_config,
                )
            )
    completed = json.loads(arguments.output.read_text(encoding="utf-8"))
    print(
        "Stage 8.1 real evaluation completed: "
        f"status={completed['status']} output={arguments.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
