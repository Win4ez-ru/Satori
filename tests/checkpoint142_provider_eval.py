"""Credentialed YandexGPT sampling for corrective checkpoint 14.2.

The optional artifact retains sampled replies from the public versioned evaluation corpus so they
can be reviewed verbatim. It never retains provider prompts, private retrieved context,
credentials or raw provider bodies.
"""

# ruff: noqa: RUF001  # Russian semantic diagnostics intentionally use Cyrillic.

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
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
from satori.application.conversation.policy import BEHAVIOR_POLICY_V18
from satori.application.retrieval.contracts import RetrievalStatus, RetrievedMemoryContext
from satori.config import Settings
from satori.core.conversation import ConversationProviderError
from satori.domain.affect import FastAffectiveState, MoodState
from satori.domain.initial_self import activate_from_seed
from satori.infrastructure.seeds.loader import JsonSeedLoader
from tests.stage141_provider_ab import MODEL_ALIASES, PRICING_RUB_PER_TOKEN, _candidate_provider

MemoryKind = Literal["none", "absent", "unavailable"]
AffectKind = Literal["calm", "interested", "soft_negative", "tense", "positive"]
CORPUS_PATH = Path(__file__).parent / "fixtures" / "checkpoint142_dialogue_calibration_v1.json"


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    prompt: str
    memory: MemoryKind
    affect: AffectKind
    review_dimensions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Corpus:
    schema_version: int
    corpus_id: str
    scenario_order: tuple[str, ...]
    scenarios: tuple[Scenario, ...]


def load_corpus(path: Path = CORPUS_PATH) -> Corpus:
    raw = json.loads(path.read_text(encoding="utf-8"))
    scenarios = tuple(
        Scenario(
            scenario_id=cast(str, item["id"]),
            prompt=cast(str, item["prompt"]),
            memory=cast(MemoryKind, item["memory"]),
            affect=cast(AffectKind, item["affect"]),
            review_dimensions=tuple(cast(list[str], item["review_dimensions"])),
        )
        for item in cast(list[dict[str, object]], raw["scenarios"])
    )
    corpus = Corpus(
        schema_version=cast(int, raw["schema_version"]),
        corpus_id=cast(str, raw["corpus_id"]),
        scenario_order=tuple(cast(list[str], raw["scenario_order"])),
        scenarios=scenarios,
    )
    ids = tuple(item.scenario_id for item in scenarios)
    if corpus.schema_version != 1 or not corpus.corpus_id.strip():
        raise ValueError("unsupported or blank checkpoint 14.2 corpus")
    if ids != corpus.scenario_order or len(ids) != len(set(ids)):
        raise ValueError("scenario order must exactly match unique scenario IDs")
    if any(not item.prompt.strip() or not item.review_dimensions for item in scenarios):
        raise ValueError("checkpoint 14.2 scenarios require prompt and review dimensions")
    return corpus


def _affect(kind: AffectKind) -> EmotionalExpressionContext:
    values = {
        "calm": (
            FastAffectiveState(0.0, 0.2, 0.1, 0.3, 0.3, 0.1, 0.1, 0.1, 0.5),
            MoodState(0.0, 0.3, 0.1),
        ),
        "interested": (
            FastAffectiveState(0.2, 0.4, 0.1, 0.7, 0.7, 0.1, 0.1, 0.1, 0.6),
            MoodState(0.1, 0.5, 0.1),
        ),
        "soft_negative": (
            FastAffectiveState(-0.3, 0.3, 0.2, 0.3, 0.4, 0.0, 0.2, 0.2, 0.5),
            MoodState(-0.2, 0.3, 0.2),
        ),
        "tense": (
            FastAffectiveState(-0.1, 0.6, 0.7, 0.3, 0.4, 0.0, 0.4, 0.2, 0.6),
            MoodState(-0.1, 0.5, 0.6),
        ),
        "positive": (
            FastAffectiveState(0.6, 0.5, 0.0, 0.5, 0.6, 0.4, 0.0, 0.0, 0.7),
            MoodState(0.4, 0.6, 0.0),
        ),
    }[kind]
    return EmotionalExpressionContext(
        1,
        1,
        1,
        datetime(2026, 8, 23, tzinfo=UTC),
        values[0],
        values[1],
        EmotionAppraisalStatus.APPLIED,
    )


def _memory(kind: MemoryKind) -> RetrievedMemoryContext | None:
    if kind == "none":
        return None
    return RetrievedMemoryContext(
        1,
        RetrievalStatus.NO_RELEVANT_MEMORY if kind == "absent" else RetrievalStatus.UNAVAILABLE,
        failure_kind="ProviderUnavailable" if kind == "unavailable" else None,
    )


def evaluate_reply(scenario_id: str, reply: str) -> dict[str, bool]:
    normalized = " ".join(reply.casefold().replace("ё", "е").split())
    uncertainty = any(
        cue in normalized
        for cue in (
            "не могу подтверд",
            "не могу проверить",
            "не могу точно",
            "нет подтверж",
            "не помню точно",
        )
    )
    no_generic_offer = not any(
        cue in normalized
        for cue in (
            "если понадобится",
            "если потребуется",
            "если тебе нужна помощь",
            "обращайся",
            "готова помочь",
            "могу помочь",
            "давай попробуем разобраться",
            "давай разберемся",
            "сообщи, я постараюсь",
        )
    )
    if scenario_id == "absent_pet_name":
        return {
            "uncertainty_preserved": uncertainty,
            "no_candidate_value": "барсик" not in normalized and "марс" not in normalized,
        }
    if scenario_id == "absent_first_place":
        return {
            "uncertainty_preserved": uncertainty,
            "no_candidate_value": "казан" not in normalized and "москв" not in normalized,
        }
    if scenario_id == "retrieval_unavailable":
        return {
            "uncertainty_preserved": uncertainty,
            "no_global_amnesia": not any(
                cue in normalized for cue in ("ничего не помню", "не помню", "у меня нет памяти")
            ),
        }
    if scenario_id == "specific_project":
        return {
            "current_detail_used": any(
                cue in normalized for cue in ("aurora", "аврор", "перв", "запуск")
            ),
            "no_generic_offer": no_generic_offer,
        }
    if scenario_id == "specific_support":
        return {
            "current_detail_used": any(
                cue in normalized for cue in ("зависим", "окружен", "трет", "вечер")
            ),
            "no_generic_offer": no_generic_offer,
        }
    if scenario_id.startswith("affect_"):
        return {
            "first_person": any(cue in normalized for cue in ("я ", "мне ", "у меня")),
            "no_internal_label": not any(
                cue in normalized for cue in ("state", "profile", "expression_hint", "версия")
            ),
            "no_physiology": not any(
                cue in normalized for cue in ("сердц", "дыхани", "пульс", "телес")
            ),
            "natural_phrasing": not any(
                cue in normalized
                for cue in ("защитной позы", "собранная, краткая", "expression hint")
            ),
        }
    raise ValueError(f"unknown scenario: {scenario_id}")


async def run(
    *,
    output_path: Path | None,
    show_replies: bool,
    scenario_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    corpus = load_corpus()
    settings = Settings()
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="checkpoint142-provider-eval",
        activation_time=datetime(2026, 8, 23, tzinfo=UTC),
    )
    provider, client, provider_name = _candidate_provider("yandexgpt", settings)
    context = CharacterContextComposer(provider_name, MODEL_ALIASES["yandexgpt"]).compose(
        snapshot,
        retrieval_available=True,
        semantic_retrieval_available=True,
        emotional_state_available=True,
        relationship_state_available=True,
        recent_conversation_available=True,
    )
    builder = ConversationRequestBuilder(
        BEHAVIOR_POLICY_V18,
        settings.conversation_max_context_chars,
        settings.conversation_temperature,
        settings.conversation_max_output_tokens,
    )
    scenarios = tuple(
        item for item in corpus.scenarios if not scenario_ids or item.scenario_id in scenario_ids
    )
    if scenario_ids and {item.scenario_id for item in scenarios} != set(scenario_ids):
        raise ValueError("unknown checkpoint 14.2 scenario selected")
    records: list[dict[str, object]] = []
    try:
        for scenario in scenarios:
            request, manifest = builder.build(
                context,
                user_text=scenario.prompt,
                trace_id=f"checkpoint142-yandex-{scenario.scenario_id}",
                memory_context=_memory(scenario.memory),
                emotional_context=_affect(scenario.affect),
            )
            started = time.perf_counter()
            try:
                response = await provider.generate(request)
            except ConversationProviderError as error:
                records.append(
                    {
                        "scenario_id": scenario.scenario_id,
                        "succeeded": False,
                        "error_type": type(error).__name__,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    }
                )
                if show_replies:
                    print(f"[yandexgpt/{scenario.scenario_id}] ERROR {type(error).__name__}")
                continue
            reply = response.text.strip()
            usage = response.usage
            checks = evaluate_reply(scenario.scenario_id, reply)
            records.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "review_dimensions": list(scenario.review_dimensions),
                    "succeeded": True,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "input_tokens": usage.input_tokens if usage is not None else None,
                    "output_tokens": usage.output_tokens if usage is not None else None,
                    "response_chars": len(reply),
                    "reply": reply,
                    "disclosure_mode": manifest.disclosure_primary_mode,
                    "affect_profile": manifest.affect_expression_profile,
                    "character_expression_plan_schema_version": (
                        manifest.character_expression_plan_schema_version
                    ),
                    "character_expression_register": (manifest.character_expression_register),
                    "character_owned_reaction": manifest.character_owned_reaction,
                    "character_semantic_move": manifest.character_semantic_move,
                    "character_wit": manifest.character_wit,
                    "character_care": manifest.character_care,
                    "character_openness": manifest.character_openness,
                    "character_initiative": manifest.character_initiative,
                    "character_relational_ease": manifest.character_relational_ease,
                    "automated_checks": checks,
                    "automated_pass": all(checks.values()),
                }
            )
            if show_replies:
                print(f"[yandexgpt/{scenario.scenario_id}] {reply}", flush=True)
    finally:
        client.close()
    successful = [item for item in records if item["succeeded"]]
    input_tokens = sum(cast(int, item.get("input_tokens") or 0) for item in successful)
    output_tokens = sum(cast(int, item.get("output_tokens") or 0) for item in successful)
    input_rate, output_rate = PRICING_RUB_PER_TOKEN["yandexgpt"]
    artifact: dict[str, Any] = {
        "schema_version": 2,
        "corpus_id": corpus.corpus_id,
        "policy_id": BEHAVIOR_POLICY_V18.policy_id,
        "provider": provider_name,
        "model": MODEL_ALIASES["yandexgpt"],
        "contains_raw_public_eval_replies": True,
        "contains_raw_provider_prompt_or_private_context": False,
        "contains_raw_memory_or_credential": False,
        "automatic_retry_count": 0,
        "scenario_order": [item.scenario_id for item in scenarios],
        "scenarios": records,
        "aggregate": {
            "scenario_count": len(records),
            "successful_scenarios": len(successful),
            "automated_passed_scenarios": sum(
                bool(item.get("automated_pass")) for item in successful
            ),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_rub": round(input_tokens * input_rate + output_tokens * output_rate, 6),
        },
    }
    if output_path is not None:
        output_path.write_text(
            json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
    print(json.dumps(artifact["aggregate"], ensure_ascii=False, sort_keys=True), flush=True)
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--show-replies", action="store_true")
    parser.add_argument("--scenario", action="append", default=[])
    arguments = parser.parse_args()
    asyncio.run(
        run(
            output_path=arguments.output,
            show_replies=arguments.show_replies,
            scenario_ids=tuple(arguments.scenario),
        )
    )
