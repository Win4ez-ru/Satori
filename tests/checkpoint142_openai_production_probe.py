"""One-call OpenAI production probe for the ADR-0032 output-budget boundary.

This manual evaluator runs one approved public turn through the normal Checkpoint 14.2
composition with accepted behavior policy v10 and a fresh disposable database. It permits exactly
one foreground provider call. A validator retry is blocked before network I/O.

Provider prompts, private application context, credentials, response bodies and raw reasoning are
never retained. Paid execution requires the explicit ``--confirm-paid-openai`` flag.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from satori.application.conversation.policy import BEHAVIOR_POLICY_V10
from satori.application.conversation.use_cases import ConversationProvider
from satori.config import ConversationProviderKind, OpenAIReasoningEffort, Settings
from satori.core.conversation import (
    ConversationProviderError,
    ConversationProviderRequest,
    ConversationProviderResponse,
)
from tests.stage81_real_eval import (
    _build_runtime,
    _new_record,
    _run_dialogue,
    _write_report,
)

REPORT_SCHEMA_VERSION = 1
EXPECTED_PROVIDER = ConversationProviderKind.OPENAI
EXPECTED_MODEL = "gpt-5.6-terra"
EXPECTED_REASONING_EFFORT = OpenAIReasoningEffort.LOW
EXPECTED_REASONING_ALLOWANCE = 1024
FIXTURE_TURN: tuple[dict[str, Any], ...] = (
    {
        "turn": 1,
        "id": "completed-difficult-project-part",
        "user_text": "Привет. Я сегодня наконец закончил сложную часть проекта",
        "semantic_tags": ["achievement", "hard_won_result", "fresh_relationship"],
    },
)


class ProviderCallLimitExceeded(RuntimeError):
    """Stop a retry before it can create a second paid foreground call."""


@dataclass(slots=True)
class OneCallLedger:
    """Retain safe metadata for at most one remote foreground attempt."""

    on_change: Callable[[], None] | None = field(default=None, repr=False)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def reserve(self, request: ConversationProviderRequest) -> int:
        if self.calls:
            raise ProviderCallLimitExceeded("authorized OpenAI provider call already consumed")
        self.calls.append(
            {
                "call_number": 1,
                "status": "in_flight",
                "message_count": len(request.messages),
                "request_content_chars": sum(len(message.content) for message in request.messages),
                "temperature": request.parameters.temperature,
                "requested_visible_output_token_limit": request.parameters.max_output_tokens,
            }
        )
        self._notify()
        return 1

    def complete(self, response: ConversationProviderResponse) -> None:
        usage = response.usage
        self.calls[0].update(
            {
                "status": "succeeded",
                "provider": response.provider,
                "model": response.model,
                "finish_status": response.finish_status,
                "input_tokens": usage.input_tokens if usage is not None else None,
                "total_output_tokens": usage.output_tokens if usage is not None else None,
                "provider_metrics": (
                    response.metrics.as_log_fields() if response.metrics is not None else None
                ),
            }
        )
        self._notify()

    def fail(self, error: BaseException) -> None:
        metrics = (
            error.metrics.as_log_fields()
            if isinstance(error, ConversationProviderError) and error.metrics is not None
            else None
        )
        self.calls[0].update(
            {
                "status": "failed",
                "error_type": type(error).__name__,
                "provider_metrics": metrics,
            }
        )
        self._notify()

    def snapshot(self) -> dict[str, Any]:
        return {
            "maximum_provider_calls": 1,
            "provider_call_count": len(self.calls),
            "within_call_limit": len(self.calls) <= 1,
            "calls": self.calls,
        }

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change()


@dataclass(slots=True)
class OneCallConversationProvider:
    """Wrap the real provider with a fail-before-network one-call boundary."""

    delegate: ConversationProvider
    ledger: OneCallLedger

    async def generate(
        self,
        request: ConversationProviderRequest,
        /,
    ) -> ConversationProviderResponse:
        self.ledger.reserve(request)
        try:
            response = await self.delegate.generate(request)
        except BaseException as error:
            self.ledger.fail(error)
            raise
        self.ledger.complete(response)
        return response


def _validate_configuration(settings: Settings) -> None:
    if settings.conversation_provider is not EXPECTED_PROVIDER:
        raise RuntimeError("paid probe requires OpenAI foreground configuration")
    if settings.conversation_model != EXPECTED_MODEL:
        raise RuntimeError(f"paid probe requires exact model {EXPECTED_MODEL}")
    if settings.openai_api_key is None:
        raise RuntimeError("paid probe requires a configured OpenAI API key")
    if settings.openai_reasoning_effort is not EXPECTED_REASONING_EFFORT:
        raise RuntimeError("paid probe requires OpenAI reasoning effort low")
    if settings.openai_reasoning_token_allowance != EXPECTED_REASONING_ALLOWANCE:
        raise RuntimeError(
            f"paid probe requires reasoning allowance {EXPECTED_REASONING_ALLOWANCE}"
        )
    if BEHAVIOR_POLICY_V10.policy_id != "satori.conversation.behavior.v10":
        raise RuntimeError("accepted behavior policy v10 is not active")
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


async def run(
    *,
    output_path: Path,
    alembic_config: Path,
    confirm_paid_openai: bool,
    show_reply: bool,
) -> dict[str, Any]:
    if not confirm_paid_openai:
        raise RuntimeError("paid OpenAI execution requires --confirm-paid-openai")
    settings = Settings()
    _validate_configuration(settings)
    ledger = OneCallLedger()
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "checkpoint": "14.2",
        "purpose": "openai_adr0032_one_call_production_probe",
        "status": "running",
        "policy_id": BEHAVIOR_POLICY_V10.policy_id,
        "approved_public_turn_and_limited_service_context": True,
        "contains_raw_public_eval_dialogue": True,
        "contains_raw_public_sampled_reply": True,
        "contains_raw_provider_prompt_or_private_context": False,
        "contains_raw_memory_or_credential": False,
        "fresh_session_count": 1,
        "expected_foreground_turn_count": 1,
        "database_artifacts_preserved": False,
        "configuration": {
            "conversation_provider": settings.conversation_provider.value,
            "conversation_model": settings.conversation_model,
            "openai_reasoning_effort": settings.openai_reasoning_effort.value,
            "openai_reasoning_token_allowance": settings.openai_reasoning_token_allowance,
            "background_providers": "ollama",
        },
        "budget": ledger.snapshot(),
        "session": None,
    }

    def checkpoint() -> None:
        report["budget"] = ledger.snapshot()
        _write_report(output_path, report)

    ledger.on_change = checkpoint
    checkpoint()
    try:
        with tempfile.TemporaryDirectory(prefix="satori-checkpoint142-openai-") as temporary:
            database_path = Path(temporary) / "session.db"
            raw = _new_record("checkpoint142-openai-production-session", database_path, False)
            report["session"] = raw
            runtime, _ = await _build_runtime(
                settings,
                database_path,
                alembic_config=alembic_config,
            )
            original = runtime.conversation_provider.delegate
            runtime.conversation_provider.delegate = OneCallConversationProvider(original, ledger)
            try:
                await _run_dialogue(
                    runtime,
                    raw,
                    FIXTURE_TURN,
                    derived_mode="none",
                    checkpoint=checkpoint,
                )
            finally:
                runtime.close()
        turns = cast(list[dict[str, Any]], raw["turns"])
        report["completed_foreground_turn_count"] = len(turns)
        report["status"] = "completed"
        report["completed_at"] = datetime.now(UTC).isoformat()
        checkpoint()
        if show_reply and turns:
            print(f"[reply] {turns[0]['reply']}", flush=True)
        return report
    except BaseException as error:
        report["status"] = "failed"
        report["failure"] = {"error_type": type(error).__name__}
        report["failed_at"] = datetime.now(UTC).isoformat()
        checkpoint()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one bounded OpenAI production probe for ADR-0032."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--alembic-config", type=Path, default=Path("alembic.ini"))
    parser.add_argument("--confirm-paid-openai", action="store_true")
    parser.add_argument("--show-reply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    completed = asyncio.run(
        run(
            output_path=arguments.output,
            alembic_config=arguments.alembic_config,
            confirm_paid_openai=arguments.confirm_paid_openai,
            show_reply=arguments.show_reply,
        )
    )
    print(
        "Checkpoint 14.2 OpenAI production probe completed: "
        f"status={completed['status']} "
        f"calls={completed['budget']['provider_call_count']} "
        f"output={arguments.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
