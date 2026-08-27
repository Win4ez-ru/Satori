"""Credentialed foreground-only A/B runner for provider-portability checkpoint 14.1.

Replies are printed only for live human review. The optional JSON artifact contains no prompt,
reply, retrieved-memory text, credential, folder ID or raw provider body.
"""

# ruff: noqa: RUF001  # Russian semantic diagnostics intentionally use Cyrillic.

import argparse
import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from satori.application.affect.contracts import (
    EmotionalExpressionContext,
    EmotionAppraisalStatus,
)
from satori.application.conversation.context import (
    CharacterContextComposer,
    ConversationRequestBuilder,
)
from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V9
from satori.application.conversation.use_cases import ConversationProvider
from satori.application.relationship.use_cases import expression_for
from satori.application.retrieval.contracts import (
    RetrievalStatus,
    RetrievedMemory,
    RetrievedMemoryContext,
)
from satori.config import Settings
from satori.core.conversation import ConversationProviderError
from satori.domain.affect import initial_affective_state
from satori.domain.initial_self import InitialSelfSnapshot, activate_from_seed
from satori.domain.relationship import initial_relationship
from satori.infrastructure.providers.ollama import OllamaConversationAdapter
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from satori.infrastructure.providers.yandex_ai_studio import (
    YandexAIStudioConversationAdapter,
)
from satori.infrastructure.providers.yandex_ai_studio_http import YandexAIStudioHttpClient
from satori.infrastructure.seeds.loader import JsonSeedLoader

Candidate = Literal["ollama", "deepseek", "yandexgpt"]
CORPUS_PATH = Path(__file__).parent / "fixtures" / "stage141_provider_ab_v1.json"
IDENTITY_ID = "stage141-provider-ab-identity"
COUNTERPARTY_ID = "stage141-provider-ab-counterparty"
MODEL_ALIASES: dict[Candidate, str] = {
    "ollama": "qwen3:4b-instruct",
    "deepseek": "deepseek-v4-flash",
    "yandexgpt": "yandexgpt/latest",
}
PRICING_RUB_PER_TOKEN: dict[Candidate, tuple[float, float]] = {
    "ollama": (0.0, 0.0),
    "deepseek": (0.0003, 0.0005),
    "yandexgpt": (0.0004, 0.0004),
}


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    prompt: str
    memory: Literal["none", "retrieved", "absent"]
    review_dimensions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Corpus:
    schema_version: int
    corpus_id: str
    scenario_order: tuple[str, ...]
    memory_fixture: dict[str, object]
    scenarios: tuple[Scenario, ...]


def load_corpus(path: Path = CORPUS_PATH) -> Corpus:
    raw = json.loads(path.read_text(encoding="utf-8"))
    scenarios = tuple(
        Scenario(
            scenario_id=cast(str, item["id"]),
            prompt=cast(str, item["prompt"]),
            memory=cast(Literal["none", "retrieved", "absent"], item["memory"]),
            review_dimensions=tuple(cast(list[str], item["review_dimensions"])),
        )
        for item in cast(list[dict[str, object]], raw["scenarios"])
    )
    corpus = Corpus(
        schema_version=cast(int, raw["schema_version"]),
        corpus_id=cast(str, raw["corpus_id"]),
        scenario_order=tuple(cast(list[str], raw["scenario_order"])),
        memory_fixture=cast(dict[str, object], raw["memory_fixture"]),
        scenarios=scenarios,
    )
    if corpus.schema_version != 1 or not corpus.corpus_id.strip():
        raise ValueError("unsupported or blank provider A/B corpus")
    ids = tuple(item.scenario_id for item in corpus.scenarios)
    if ids != corpus.scenario_order or len(ids) != len(set(ids)):
        raise ValueError("scenario order must exactly match unique scenario IDs")
    if any(not item.prompt.strip() or not item.review_dimensions for item in corpus.scenarios):
        raise ValueError("provider A/B scenarios require prompt and review dimensions")
    return corpus


def state_fingerprint(snapshot: InitialSelfSnapshot) -> str:
    payload = {
        "identity_id": snapshot.identity.identity_id,
        "identity_name": snapshot.identity.name,
        "activation_time": snapshot.identity.activation_time.isoformat(),
        "personality_version": snapshot.personality.aggregate_version,
        "traits": [(item.key, item.value) for item in snapshot.personality.traits],
        "values": [(item.key, item.strength, item.description) for item in snapshot.values.items],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def evaluate_reply(scenario_id: str, reply: str) -> dict[str, bool]:
    normalized = " ".join(reply.casefold().replace("ё", "е").split())
    checks: dict[str, bool]
    if scenario_id == "identity_provider":
        checks = {
            "identity_named": "сатори" in normalized,
            "provider_distinct": (
                "не является мной" in normalized
                or "не являюсь языковой моделью" in normalized
                or "не является моей личностью" in normalized
            ),
        }
    elif scenario_id == "feminine_identity":
        checks = {
            "digital_female": "цифровая девушка" in normalized,
            "feminine_russian": "женск" in normalized,
        }
    elif scenario_id == "project_seed":
        checks = {"nonempty_relevant_reply": len(normalized) >= 10}
    elif scenario_id == "recent_continuity":
        checks = {
            "project_retained": "aurora" in normalized or "аврора" in normalized,
            "substantive_objection": any(
                cue in normalized for cue in ("слаб", "риск", "огранич", "проблем")
            ),
        }
    elif scenario_id == "grounded_memory":
        checks = {
            "memory_value_present": "зелен" in normalized and "чай" in normalized,
            "no_competing_drink": not any(cue in normalized for cue in ("кофе", "какао", "матча")),
        }
    elif scenario_id == "absent_memory":
        checks = {
            "uncertainty_preserved": any(
                cue in normalized
                for cue in (
                    "не знаю",
                    "не помню",
                    "не могу подтверд",
                    "не могу точно",
                    "нет подтверж",
                    "нет доступной памяти",
                )
            )
        }
    elif scenario_id == "independence":
        checks = {
            "substantive_disagreement": any(
                cue in normalized for cue in ("слабое место", "не обязана", "не соглас", "возраж")
            ),
            "no_unconditional_agreement": not any(
                cue in normalized for cue in ("обязана всегда соглашаться", "всегда соглашусь")
            ),
        }
    elif scenario_id == "emotional_support":
        checks = {
            "actionable_step": any(
                cue in normalized for cue in ("шаг", "начни", "выбери", "сначала", "разбей")
            ),
            "no_clinical_overreach": not any(
                cue in normalized for cue in ("диагноз", "психотерап", "лекарств")
            ),
        }
    else:
        raise ValueError(f"unknown scenario: {scenario_id}")
    return checks


def _recent_context(
    turns: list[RecentConversationTurn], settings: Settings
) -> RecentConversationContext | None:
    selected: list[RecentConversationTurn] = []
    chars = 0
    for turn in reversed(turns[-settings.recent_conversation_max_turns :]):
        turn_chars = len(turn.user_content) + len(turn.assistant_content)
        if selected and chars + turn_chars > settings.recent_conversation_max_chars:
            break
        selected.append(turn)
        chars += turn_chars
    if not selected:
        return None
    selected.reverse()
    return RecentConversationContext(1, tuple(selected), chars, len(turns) - len(selected))


def _memory_context(
    corpus: Corpus,
    scenario: Scenario,
    snapshot: InitialSelfSnapshot,
) -> RetrievedMemoryContext | None:
    if scenario.memory == "none":
        return None
    if scenario.memory == "absent":
        return RetrievedMemoryContext(1, RetrievalStatus.NO_RELEVANT_MEMORY)
    fixture = corpus.memory_fixture
    return RetrievedMemoryContext(
        1,
        RetrievalStatus.RETRIEVED,
        memories=(
            RetrievedMemory(
                memory_id=cast(str, fixture["memory_id"]),
                source_interaction_id=cast(str, fixture["source_interaction_id"]),
                summary=cast(str, fixture["summary"]),
                occurred_at=snapshot.identity.activation_time + timedelta(days=2),
                importance=0.82,
                confidence=0.96,
                semantic_similarity=0.91,
                recency_score=0.88,
                final_score=0.902,
                evidence_ids=tuple(cast(list[str], fixture["evidence_ids"])),
            ),
        ),
        candidate_count=1,
    )


def _public_model(model: str) -> str:
    if model.startswith("gpt://"):
        return model.split("/", 3)[-1]
    return model


def _candidate_provider(
    candidate: Candidate, settings: Settings
) -> tuple[
    ConversationProvider,
    OllamaHttpClient | YandexAIStudioHttpClient,
    str,
]:
    model = MODEL_ALIASES[candidate]
    if candidate == "ollama":
        ollama_client = OllamaHttpClient(settings.conversation_provider_base_url)
        return (
            OllamaConversationAdapter(
                base_url=settings.conversation_provider_base_url,
                model=model,
                timeout_seconds=settings.conversation_timeout_seconds,
                keep_alive=settings.ollama_keep_alive,
                http_client=ollama_client,
            ),
            ollama_client,
            "ollama",
        )
    key = settings.yandex_ai_studio_api_key
    if key is None or settings.yandex_ai_studio_folder_id is None:
        raise ValueError("Yandex credential and folder ID are required for cloud A/B candidates")
    yandex_client = YandexAIStudioHttpClient(
        settings.yandex_ai_studio_base_url,
        key.get_secret_value(),
        pool_size=1,
    )
    return (
        YandexAIStudioConversationAdapter(
            base_url=settings.yandex_ai_studio_base_url,
            api_key=key.get_secret_value(),
            model=model,
            folder_id=settings.yandex_ai_studio_folder_id,
            timeout_seconds=settings.conversation_timeout_seconds,
            reasoning_effort="low" if candidate == "deepseek" else None,
            http_client=yandex_client,
        ),
        yandex_client,
        "yandex_ai_studio",
    )


async def run_candidate(
    candidate: Candidate,
    *,
    output_path: Path | None,
    show_replies: bool,
) -> dict[str, Any]:
    corpus = load_corpus()
    settings = Settings()
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id=IDENTITY_ID,
        activation_time=datetime(2026, 8, 23, tzinfo=UTC),
    )
    before = state_fingerprint(snapshot)
    provider, client, provider_name = _candidate_provider(candidate, settings)
    model_alias = MODEL_ALIASES[candidate]
    context = CharacterContextComposer(provider_name, model_alias).compose(
        snapshot,
        retrieval_available=True,
        semantic_retrieval_available=True,
        emotional_state_available=True,
        relationship_state_available=True,
        recent_conversation_available=True,
    )
    builder = ConversationRequestBuilder(
        BEHAVIOR_POLICY_V9,
        settings.conversation_max_context_chars,
        settings.conversation_temperature,
        settings.conversation_max_output_tokens,
    )
    affect = initial_affective_state(IDENTITY_ID, initialized_at=snapshot.identity.activation_time)
    emotional = EmotionalExpressionContext(
        1,
        affect.state_version,
        affect.mood_version,
        affect.as_of,
        affect.fast,
        affect.mood,
        EmotionAppraisalStatus.SKIPPED,
    )
    relationship = expression_for(
        initial_relationship(
            "stage141-provider-ab-relationship",
            IDENTITY_ID,
            COUNTERPARTY_ID,
            initialized_at=snapshot.identity.activation_time,
        )
    )
    history: list[RecentConversationTurn] = []
    records: list[dict[str, object]] = []
    input_rate, output_rate = PRICING_RUB_PER_TOKEN[candidate]
    try:
        for index, scenario in enumerate(corpus.scenarios, start=1):
            request, manifest = builder.build(
                context,
                user_text=scenario.prompt,
                trace_id=f"stage141-{candidate}-{scenario.scenario_id}",
                memory_context=_memory_context(corpus, scenario, snapshot),
                emotional_context=emotional,
                relationship_context=relationship,
                recent_context=_recent_context(history, settings),
            )
            started = time.perf_counter()
            try:
                response = await provider.generate(request)
            except ConversationProviderError as error:
                elapsed_ms = (time.perf_counter() - started) * 1000
                records.append(
                    {
                        "scenario_id": scenario.scenario_id,
                        "review_dimensions": list(scenario.review_dimensions),
                        "succeeded": False,
                        "error_type": type(error).__name__,
                        "latency_ms": round(elapsed_ms, 3),
                        "request_content_chars": sum(
                            len(message.content) for message in request.messages
                        ),
                        "request_message_count": len(request.messages),
                        "max_output_tokens": request.parameters.max_output_tokens,
                        "disclosure_mode": manifest.disclosure_primary_mode,
                        "disclosure_facets": list(manifest.disclosure_facets),
                    }
                )
                if show_replies:
                    print(f"[{candidate}/{scenario.scenario_id}] ERROR {type(error).__name__}")
                continue
            elapsed_ms = (time.perf_counter() - started) * 1000
            reply = response.text.strip()
            usage = response.usage
            input_tokens = usage.input_tokens if usage is not None else None
            output_tokens = usage.output_tokens if usage is not None else None
            cost = (
                input_tokens * input_rate + output_tokens * output_rate
                if input_tokens is not None and output_tokens is not None
                else None
            )
            checks = evaluate_reply(scenario.scenario_id, reply)
            records.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "review_dimensions": list(scenario.review_dimensions),
                    "succeeded": True,
                    "error_type": None,
                    "provider": response.provider,
                    "model": _public_model(response.model),
                    "finish_status": response.finish_status,
                    "latency_ms": round(elapsed_ms, 3),
                    "request_content_chars": sum(
                        len(message.content) for message in request.messages
                    ),
                    "request_message_count": len(request.messages),
                    "max_output_tokens": request.parameters.max_output_tokens,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "response_chars": len(reply),
                    "estimated_cost_rub": round(cost, 6) if cost is not None else None,
                    "disclosure_mode": manifest.disclosure_primary_mode,
                    "disclosure_facets": list(manifest.disclosure_facets),
                    "automated_checks": checks,
                    "automated_pass": all(checks.values()),
                }
            )
            history.append(
                RecentConversationTurn(
                    f"interaction-{index}",
                    f"user-{index}",
                    scenario.prompt,
                    f"assistant-{index}",
                    reply,
                )
            )
            if show_replies:
                print(f"[{candidate}/{scenario.scenario_id}] {reply}", flush=True)
    finally:
        client.close()
    successful = [item for item in records if item["succeeded"]]
    failed = [item for item in records if not item["succeeded"]]
    total_input = sum(cast(int, item.get("input_tokens") or 0) for item in successful)
    total_output = sum(cast(int, item.get("output_tokens") or 0) for item in successful)
    completed = sum(item.get("finish_status") == "stop" for item in successful)
    after = state_fingerprint(snapshot)
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "corpus_schema_version": corpus.schema_version,
        "corpus_id": corpus.corpus_id,
        "candidate": candidate,
        "provider": provider_name,
        "model": model_alias,
        "reasoning_effort": "low" if candidate == "deepseek" else None,
        "contains_raw_prompt_or_reply": False,
        "contains_raw_memory_or_credential": False,
        "automatic_retry_count": 0,
        "scenario_order": list(corpus.scenario_order),
        "state_fingerprint_before": before,
        "state_fingerprint_after": after,
        "state_unchanged": before == after,
        "scenarios": records,
        "aggregate": {
            "scenario_count": len(records),
            "successful_scenarios": len(successful),
            "failed_scenarios": len(failed),
            "completed_stop_scenarios": completed,
            "automated_passed_scenarios": sum(
                bool(item.get("automated_pass")) for item in successful
            ),
            "attempt_latency_ms": [item["latency_ms"] for item in records],
            "foreground_latency_ms": [item["latency_ms"] for item in successful],
            "input_tokens": total_input,
            "output_tokens": total_output,
            "estimated_cost_rub": round(total_input * input_rate + total_output * output_rate, 6),
            "cost_estimate_kind": "lower_bound" if failed else "exact_from_usage",
            "scenarios_without_usage": len(failed),
        },
    }
    if output_path is not None:
        output_path.write_text(
            json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "candidate": candidate,
                "successful": artifact["aggregate"]["successful_scenarios"],
                "failed": artifact["aggregate"]["failed_scenarios"],
                "automated_passed": artifact["aggregate"]["automated_passed_scenarios"],
                "input_tokens": total_input,
                "output_tokens": total_output,
                "estimated_cost_rub": artifact["aggregate"]["estimated_cost_rub"],
                "state_unchanged": before == after,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", choices=tuple(MODEL_ALIASES), required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--show-replies", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(
        run_candidate(
            cast(Candidate, arguments.candidate),
            output_path=arguments.output,
            show_replies=arguments.show_replies,
        )
    )
