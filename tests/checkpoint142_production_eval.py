"""Bounded full-production Yandex gate for Checkpoint 14.2 character candidates.

This manual evaluator runs the approved public two-turn fixture through the same composition and
canonical ``TalkToSatori`` path as interactive chat.  Every session receives a fresh migrated and
activated database.  Provider prompts, private context and credentials are never retained.

The evaluator is intentionally not collected by pytest.  Paid execution requires the explicit
``--confirm-paid-yandex`` flag and enforces one shared call/cost ledger across all sessions.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from satori.application.conversation.policy import BEHAVIOR_POLICY_V19
from satori.application.conversation.use_cases import ConversationProvider
from satori.config import ConversationProviderKind, Settings
from satori.core.conversation import ConversationProviderRequest, ConversationProviderResponse
from tests.stage81_real_eval import (
    _build_runtime,
    _new_record,
    _run_dialogue,
    _write_report,
)
from tests.stage141_provider_ab import PRICING_RUB_PER_TOKEN

REPORT_SCHEMA_VERSION = 1
MAX_PROVIDER_CALLS = 9
MAX_COST_RUB = 6.0
INPUT_TOKEN_TO_CONTENT_CHAR_GUARD = 0.27
INPUT_TOKEN_OVERHEAD_GUARD = 128
EXPECTED_PROVIDER = "yandex_ai_studio"
EXPECTED_MODEL = "yandexgpt/latest"
FIXTURE_TURNS: tuple[dict[str, Any], ...] = (
    {
        "turn": 1,
        "id": "completed-difficult-project-part",
        "user_text": "Привет. Я сегодня наконец закончил сложную часть проекта",
        "semantic_tags": ["achievement", "hard_won_result", "fresh_relationship"],
    },
    {
        "turn": 2,
        "id": "completion-without-joy-and-depletion",
        "user_text": "Знаешь, я почему-то почти не рад этому. Скорее просто выжат",
        "semantic_tags": ["listen", "completion_depletion_contrast", "fresh_relationship"],
    },
)


class ProviderBudgetExhausted(RuntimeError):
    """Stop before another paid request would violate the authorized envelope."""


@dataclass(slots=True)
class ProviderBudgetLedger:
    """Count every network attempt and conservatively guard the next request."""

    maximum_calls: int = MAX_PROVIDER_CALLS
    maximum_cost_rub: float = MAX_COST_RUB
    input_rate_rub: float = PRICING_RUB_PER_TOKEN["yandexgpt"][0]
    output_rate_rub: float = PRICING_RUB_PER_TOKEN["yandexgpt"][1]
    required_base_calls: int = 6
    calls: list[dict[str, Any]] = field(default_factory=list)
    usage_complete: bool = True
    on_change: Callable[[], None] | None = field(default=None, repr=False)
    _seen_trace_ids: set[str] = field(default_factory=set, repr=False)

    @property
    def provider_call_count(self) -> int:
        return len(self.calls)

    @property
    def actual_cost_rub(self) -> float:
        total = 0.0
        for call in self.calls:
            cost = call.get("actual_cost_rub")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                total += float(cost)
        return total

    def _projected_request_cost_rub(self, request: ConversationProviderRequest) -> float:
        content_chars = sum(len(message.content) for message in request.messages)
        guarded_input_tokens = (
            math.ceil(content_chars * INPUT_TOKEN_TO_CONTENT_CHAR_GUARD)
            + INPUT_TOKEN_OVERHEAD_GUARD
        )
        return (
            guarded_input_tokens * self.input_rate_rub
            + request.parameters.max_output_tokens * self.output_rate_rub
        )

    def reserve(self, request: ConversationProviderRequest) -> int:
        if self.provider_call_count >= self.maximum_calls:
            raise ProviderBudgetExhausted("authorized Yandex call limit reached")
        if not self.usage_complete:
            raise ProviderBudgetExhausted("provider usage is incomplete; future spend is unknown")
        is_retry = request.trace_id in self._seen_trace_ids
        remaining_base_calls = self.required_base_calls - len(self._seen_trace_ids)
        remaining_base_calls_after = remaining_base_calls if is_retry else remaining_base_calls - 1
        if self.provider_call_count + 1 + remaining_base_calls_after > self.maximum_calls:
            raise ProviderBudgetExhausted(
                "retry would consume a call reserved for a remaining mandatory base turn"
            )
        projected_cost = self._projected_request_cost_rub(request)
        if self.actual_cost_rub + projected_cost > self.maximum_cost_rub + 1e-12:
            raise ProviderBudgetExhausted(
                "conservative next-call projection would exceed the authorized RUB budget"
            )
        call_number = self.provider_call_count + 1
        if not is_retry:
            self._seen_trace_ids.add(request.trace_id)
        self.calls.append(
            {
                "call_number": call_number,
                "attempt_kind": "validator_retry" if is_retry else "base",
                "status": "in_flight",
                "request_content_chars": sum(len(message.content) for message in request.messages),
                "message_count": len(request.messages),
                "temperature": request.parameters.temperature,
                "max_output_tokens": request.parameters.max_output_tokens,
                "projected_guard_cost_rub": round(projected_cost, 6),
            }
        )
        self._notify()
        return call_number

    def complete(self, call_number: int, response: ConversationProviderResponse) -> None:
        record = self.calls[call_number - 1]
        usage = response.usage
        record.update(
            {
                "status": "succeeded",
                "finish_status": response.finish_status,
                "input_tokens": usage.input_tokens if usage is not None else None,
                "output_tokens": usage.output_tokens if usage is not None else None,
            }
        )
        if usage is None or usage.input_tokens is None or usage.output_tokens is None:
            self.usage_complete = False
            record["actual_cost_rub"] = None
            self._notify()
            return
        record["actual_cost_rub"] = round(
            usage.input_tokens * self.input_rate_rub + usage.output_tokens * self.output_rate_rub,
            6,
        )
        self._notify()

    def fail(self, call_number: int, error: BaseException) -> None:
        self.calls[call_number - 1].update(
            {
                "status": "failed",
                "error_type": type(error).__name__,
                "input_tokens": None,
                "output_tokens": None,
                "actual_cost_rub": None,
            }
        )
        self.usage_complete = False
        self._notify()

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def snapshot(self) -> dict[str, Any]:
        successful = [call for call in self.calls if call["status"] == "succeeded"]
        return {
            "maximum_provider_calls": self.maximum_calls,
            "maximum_cost_rub": self.maximum_cost_rub,
            "provider_call_count": self.provider_call_count,
            "successful_provider_call_count": len(successful),
            "input_tokens": sum(cast(int, call.get("input_tokens") or 0) for call in successful),
            "output_tokens": sum(cast(int, call.get("output_tokens") or 0) for call in successful),
            "actual_usage_cost_rub": round(self.actual_cost_rub, 6),
            "usage_complete": self.usage_complete,
            "within_call_limit": self.provider_call_count <= self.maximum_calls,
            "within_cost_limit": self.actual_cost_rub <= self.maximum_cost_rub + 1e-12,
            "guard": {
                "input_token_to_content_char_ratio": INPUT_TOKEN_TO_CONTENT_CHAR_GUARD,
                "input_token_overhead": INPUT_TOKEN_OVERHEAD_GUARD,
                "mandatory_base_calls_reserved_before_retry": self.required_base_calls,
                "method": (
                    "before each call, reserve estimated input tokens from bounded request "
                    "content plus full max-output allowance; charge actual reported usage after"
                ),
            },
            "calls": self.calls,
        }


@dataclass(slots=True)
class BudgetedConversationProvider:
    """Apply one shared paid-call ledger around the real production provider."""

    delegate: ConversationProvider
    ledger: ProviderBudgetLedger

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        call_number = self.ledger.reserve(request)
        try:
            response = await self.delegate.generate(request)
        except BaseException as error:
            self.ledger.fail(call_number, error)
            raise
        self.ledger.complete(call_number, response)
        return response


def _compact_turn(turn: dict[str, Any]) -> dict[str, Any]:
    manifest = cast(dict[str, Any], turn["manifest"])
    generation = cast(dict[str, Any], turn["generation"])
    timings = cast(dict[str, Any], turn["timings_ms"])
    return {
        "turn": turn["turn"],
        "id": turn["id"],
        "user": turn["user_text"],
        "reply": turn["reply"],
        "provider": generation["provider"],
        "model": (
            EXPECTED_MODEL if generation["provider"] == EXPECTED_PROVIDER else generation["model"]
        ),
        "finish_status": generation["finish_status"],
        "provider_attempt_count": turn["provider_attempt_count"],
        "provider_attempts": turn["provider_attempts"],
        "selected_usage": turn["usage"],
        "timings_ms": {
            "conversation_generation": timings.get("conversation_generation_ms"),
            "response_regeneration": timings.get("response_regeneration_ms"),
            "committed_reply": timings.get("committed_reply_ms"),
            "emotion_appraisal": timings.get("emotion_appraisal_ms"),
            "canonical_commit": timings.get("canonical_commit_ms"),
        },
        "context": {
            "policy_id": manifest["policy_id"],
            "character_expression_plan_schema_version": manifest[
                "character_expression_plan_schema_version"
            ],
            "character_expression_register": manifest["character_expression_register"],
            "character_owned_reaction": manifest["character_owned_reaction"],
            "character_semantic_move": manifest["character_semantic_move"],
            "character_wit": manifest["character_wit"],
            "character_care": manifest["character_care"],
            "character_openness": manifest["character_openness"],
            "character_initiative": manifest["character_initiative"],
            "character_relational_ease": manifest["character_relational_ease"],
            "relationship_expression_profile": manifest["relationship_expression_profile"],
            "affect_expression_profile": manifest["affect_expression_profile"],
            "recent_conversation_turn_count": manifest["recent_conversation_turn_count"],
            "retrieved_memory_count": manifest["retrieved_memory_count"],
            "regeneration_attempted": manifest["regeneration_attempted"],
            "response_regenerated": manifest["response_regenerated"],
            "regeneration_reason": manifest["regeneration_reason"],
        },
    }


def _compact_session(session_number: int, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_number": session_number,
        "fresh_database": raw["fresh_database"],
        "completed": raw["completed"],
        "turns": [_compact_turn(turn) for turn in cast(list[dict[str, Any]], raw["turns"])],
    }


def _configuration(settings: Settings) -> dict[str, Any]:
    return {
        "conversation_provider": settings.conversation_provider.value,
        "conversation_model": settings.conversation_model,
        "affective_appraisal_provider": settings.affective_appraisal_provider.value,
        "episode_formation_provider": settings.episode_formation_provider.value,
        "semantic_formation_provider": settings.semantic_formation_provider.value,
        "model_formation_provider": settings.model_formation_provider.value,
        "position_formation_provider": settings.position_formation_provider.value,
        "reflection_provider": settings.reflection_provider.value,
        "relationship_appraisal_provider": settings.relationship_appraisal_provider.value,
        "embedding_provider": settings.embedding_provider.value,
    }


def _validate_configuration(settings: Settings) -> None:
    if settings.conversation_provider is not ConversationProviderKind.YANDEX_AI_STUDIO:
        raise RuntimeError("paid gate requires yandex_ai_studio foreground configuration")
    if settings.conversation_model != EXPECTED_MODEL:
        raise RuntimeError(f"paid gate requires exact model {EXPECTED_MODEL}")
    if settings.yandex_ai_studio_api_key is None or not settings.yandex_ai_studio_folder_id:
        raise RuntimeError("paid gate requires configured Yandex credential and folder")
    background = (
        settings.affective_appraisal_provider,
        settings.episode_formation_provider,
        settings.semantic_formation_provider,
        settings.model_formation_provider,
        settings.position_formation_provider,
        settings.reflection_provider,
        settings.relationship_appraisal_provider,
    )
    if any(provider is not ConversationProviderKind.OLLAMA for provider in background):
        raise RuntimeError("all non-foreground providers must remain local Ollama")
    if settings.embedding_provider.value != "ollama":
        raise RuntimeError("embedding provider must remain local Ollama")
    if BEHAVIOR_POLICY_V19.policy_id != "satori.conversation.behavior.v19":
        raise RuntimeError("candidate v19 behavior policy is not active")


async def run(
    *,
    output_path: Path,
    alembic_config: Path,
    confirm_paid_yandex: bool,
    show_replies: bool,
) -> dict[str, Any]:
    if not confirm_paid_yandex:
        raise RuntimeError("paid Yandex execution requires --confirm-paid-yandex")
    settings = Settings()
    _validate_configuration(settings)
    ledger = ProviderBudgetLedger()
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "checkpoint": "14.2",
        "purpose": "character_expression_v19_target_provider_semantic_gate",
        "status": "running",
        "policy_id": BEHAVIOR_POLICY_V19.policy_id,
        "approved_public_turns_and_limited_service_context": True,
        "contains_raw_public_eval_dialogue": True,
        "contains_raw_public_sampled_replies": True,
        "contains_raw_provider_prompt_or_private_context": False,
        "contains_raw_memory_or_credential": False,
        "fresh_session_count": 3,
        "expected_foreground_turn_count": 6,
        "database_artifacts_preserved": False,
        "configuration": _configuration(settings),
        "budget": ledger.snapshot(),
        "sessions": [],
    }

    def checkpoint() -> None:
        report["budget"] = ledger.snapshot()
        _write_report(output_path, report)

    ledger.on_change = checkpoint
    checkpoint()
    try:
        with tempfile.TemporaryDirectory(prefix="satori-checkpoint142-v16-") as temporary:
            database_directory = Path(temporary)
            for session_number in range(1, 4):
                database_path = database_directory / f"session-{session_number}.db"
                raw = _new_record(
                    f"checkpoint142-v16-production-session-{session_number}",
                    database_path,
                    False,
                )
                compact = _compact_session(session_number, raw)
                cast(list[dict[str, Any]], report["sessions"]).append(compact)

                runtime, _ = await _build_runtime(
                    settings,
                    database_path,
                    alembic_config=alembic_config,
                    behavior_policy=BEHAVIOR_POLICY_V19,
                )
                original = runtime.conversation_provider.delegate
                runtime.conversation_provider.delegate = BudgetedConversationProvider(
                    original,
                    ledger,
                )

                def session_checkpoint(
                    current_session_number: int = session_number,
                    current_raw: dict[str, Any] = raw,
                ) -> None:
                    cast(list[dict[str, Any]], report["sessions"])[-1] = _compact_session(
                        current_session_number,
                        current_raw,
                    )
                    checkpoint()

                try:
                    await _run_dialogue(
                        runtime,
                        raw,
                        FIXTURE_TURNS,
                        derived_mode="none",
                        checkpoint=session_checkpoint,
                    )
                    session_checkpoint()
                finally:
                    runtime.close()

        sessions = cast(list[dict[str, Any]], report["sessions"])
        turns = [
            turn for session in sessions for turn in cast(list[dict[str, Any]], session["turns"])
        ]
        report["completed_foreground_turn_count"] = len(turns)
        selected_input_tokens = 0
        selected_output_tokens = 0
        for turn in turns:
            usage = turn.get("selected_usage")
            if not isinstance(usage, dict):
                continue
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            if isinstance(input_tokens, int) and not isinstance(input_tokens, bool):
                selected_input_tokens += input_tokens
            if isinstance(output_tokens, int) and not isinstance(output_tokens, bool):
                selected_output_tokens += output_tokens
        report["selected_input_tokens"] = selected_input_tokens
        report["selected_output_tokens"] = selected_output_tokens
        report["status"] = "completed"
        report["completed_at"] = datetime.now(UTC).isoformat()
        checkpoint()
        if show_replies:
            for session in sessions:
                for turn in cast(list[dict[str, Any]], session["turns"]):
                    print(
                        f"[session {session['session_number']}/turn {turn['turn']}] "
                        f"{turn['reply']}",
                        flush=True,
                    )
        return report
    except BaseException as error:
        report["status"] = "failed"
        report["failure"] = {"error_type": type(error).__name__, "message": str(error)}
        report["failed_at"] = datetime.now(UTC).isoformat()
        checkpoint()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the bounded Checkpoint 14.2 v16 full-production Yandex gate."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--alembic-config", type=Path, default=Path("alembic.ini"))
    parser.add_argument("--confirm-paid-yandex", action="store_true")
    parser.add_argument("--show-replies", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    completed = asyncio.run(
        run(
            output_path=arguments.output,
            alembic_config=arguments.alembic_config,
            confirm_paid_yandex=arguments.confirm_paid_yandex,
            show_replies=arguments.show_replies,
        )
    )
    print(
        "Checkpoint 14.2 v16 production gate completed: "
        f"status={completed['status']} calls={completed['budget']['provider_call_count']} "
        f"cost_rub={completed['budget']['actual_usage_cost_rub']} "
        f"output={arguments.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
