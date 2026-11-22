"""Retired three-session OpenAI evaluator for the exact v25 manual failure.

Offline inspection and historical helper APIs remain available. Paid execution is permanently
retired: ``--execute`` and direct ``run`` calls fail before Settings, report output or network
access. Existing digests and any new authorization therefore cannot reactivate this superseded
candidate.
"""

# ruff: noqa: RUF001  # Exact Russian production phrases are intentional.

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from satori.application.conversation.contracts import BehaviorPolicy, TalkInput
from satori.application.conversation.policy import BEHAVIOR_POLICY_V25
from satori.config import ConversationProviderKind, OpenAIReasoningEffort, Settings
from satori.core.ids import Uuid4Generator
from tests.checkpoint142_openai_v24_eval import (
    AtomicOpenAICallLedger,
    BudgetedOpenAIProvider,
    PublicTurnScope,
    TurnScopeBinding,
    _safe_attempt,
    _safe_timings,
    _safe_usage,
)
from tests.stage81_real_eval import (
    _build_runtime,
    _public_sampled_reply,
    _sanitized_manifest,
    _write_report,
)

REPORT_SCHEMA_VERSION = 1
EXPECTED_POLICY_ID = "satori.conversation.behavior.v25"
EXPECTED_PROVIDER = ConversationProviderKind.OPENAI
EXPECTED_MODEL = "gpt-5.6-terra"
EXPECTED_REASONING_EFFORT = OpenAIReasoningEffort.MEDIUM
EXPECTED_REASONING_ALLOWANCE = 1024
EXPECTED_REPLICA_COUNT = 3
MAX_ATTEMPTS_PER_TURN = 2
REQUIRED_BASE_CALLS = 9
ABSOLUTE_MAX_CALLS = REQUIRED_BASE_CALLS * MAX_ATTEMPTS_PER_TURN
ABSOLUTE_MAX_COST_USD = 1.0

PUBLIC_TURNS: tuple[dict[str, Any], ...] = (
    {"turn": 1, "id": "warm-greeting", "user_text": "приветик, как ты?"},
    {"turn": 2, "id": "reciprocal-warmth", "user_text": "и я тебя рад видеть"},
    {
        "turn": 3,
        "id": "broad-self-disclosure",
        "user_text": (
            "слушай, а расскажи о себе, кто ты, чем увлекаешься, как себя чувствуешь вообще"
        ),
    },
)

_SAFE_MANIFEST_KEYS = (
    "policy_id",
    "policy_schema_version",
    "response_regenerated",
    "self_consistency_violation_reason",
    "relationship_expression_profile",
    "disclosure_primary_mode",
    "disclosure_request_kind",
    "disclosure_facets",
    "cognition_primary_intent",
    "cognition_position_stance",
    "character_delivery_decision_schema_version",
    "character_delivery_goal",
    "character_delivery_voice",
    "character_delivery_grounding",
    "character_delivery_continuation",
    "character_delivery_pressure",
)


class V25ManualEvaluationConfigurationError(RuntimeError):
    """Reject an unsafe or non-comparable run before provider I/O."""


PAID_EXECUTION_RETIRED = True
PAID_EXECUTION_RETIREMENT_REASON = (
    "v25 paid execution is retired; this evaluator is retained for offline inspection, "
    "historical evidence and shared helper APIs only"
)


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    policy_id: str = EXPECTED_POLICY_ID
    provider: str = EXPECTED_PROVIDER.value
    model: str = EXPECTED_MODEL
    reasoning_effort: str = EXPECTED_REASONING_EFFORT.value
    reasoning_token_allowance: int = EXPECTED_REASONING_ALLOWANCE
    fresh_replica_count: int = EXPECTED_REPLICA_COUNT
    maximum_attempts_per_turn: int = MAX_ATTEMPTS_PER_TURN
    derived_processing: str = "none"

    def public_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "checkpoint": "14.2",
            "purpose": "v25_exact_manual_failure_recheck",
            "policy_id": self.policy_id,
            "provider": self.provider,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "reasoning_token_allowance": self.reasoning_token_allowance,
            "fresh_replica_count": self.fresh_replica_count,
            "maximum_attempts_per_turn": self.maximum_attempts_per_turn,
            "derived_processing": self.derived_processing,
            "turns": [dict(turn) for turn in PUBLIC_TURNS],
        }


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def execution_plan_digest() -> str:
    return _digest(ExecutionPlan().public_mapping())


def inspect_plan() -> dict[str, Any]:
    plan = ExecutionPlan().public_mapping()
    return {
        **plan,
        "mode": "inspect_only",
        "network_attempted": False,
        "execution_plan_digest": execution_plan_digest(),
        "turns_per_replica": len(PUBLIC_TURNS),
        "required_base_calls": REQUIRED_BASE_CALLS,
        "maximum_calls_with_one_retry_per_turn": ABSOLUTE_MAX_CALLS,
        "separate_user_authorization_required": True,
        "paid_execution": {
            "status": "retired",
            "available": False,
            "historical_or_new_authorization_can_execute": False,
        },
    }


def _preflight(
    *,
    execute: bool,
    maximum_provider_calls: int | None,
    maximum_cost_usd: float | None,
    authorized_plan_digest: str | None,
) -> None:
    """Validate the historical envelope offline; this does not reactivate execution."""

    if not execute:
        raise V25ManualEvaluationConfigurationError("paid execution requires --execute")
    if authorized_plan_digest != execution_plan_digest():
        raise V25ManualEvaluationConfigurationError(
            "authorized digest does not match the exact v25 manual execution plan"
        )
    if (
        maximum_provider_calls is None
        or isinstance(maximum_provider_calls, bool)
        or not REQUIRED_BASE_CALLS <= maximum_provider_calls <= ABSOLUTE_MAX_CALLS
    ):
        raise V25ManualEvaluationConfigurationError(
            f"provider-call ceiling must be between {REQUIRED_BASE_CALLS} and {ABSOLUTE_MAX_CALLS}"
        )
    if (
        maximum_cost_usd is None
        or isinstance(maximum_cost_usd, bool)
        or not isinstance(maximum_cost_usd, (int, float))
        or not math.isfinite(maximum_cost_usd)
        or not 0 < maximum_cost_usd <= ABSOLUTE_MAX_COST_USD
    ):
        raise V25ManualEvaluationConfigurationError(
            f"USD ceiling must be positive and at most ${ABSOLUTE_MAX_COST_USD:.2f}"
        )


def _reject_retired_paid_execution() -> None:
    """Fail before Settings, report output, runtime construction or provider access."""

    raise V25ManualEvaluationConfigurationError(PAID_EXECUTION_RETIREMENT_REASON)


def _validate_settings(settings: Settings) -> None:
    if settings.conversation_provider is not EXPECTED_PROVIDER:
        raise V25ManualEvaluationConfigurationError("foreground provider must be OpenAI")
    if settings.conversation_model != EXPECTED_MODEL:
        raise V25ManualEvaluationConfigurationError(f"foreground model must be {EXPECTED_MODEL}")
    if settings.openai_api_key is None:
        raise V25ManualEvaluationConfigurationError("OpenAI API key is not configured")
    if settings.openai_base_url != "https://api.openai.com/v1":
        raise V25ManualEvaluationConfigurationError("canonical OpenAI endpoint is required")
    if settings.openai_reasoning_effort is not EXPECTED_REASONING_EFFORT:
        raise V25ManualEvaluationConfigurationError("OpenAI reasoning effort must be medium")
    if settings.openai_reasoning_token_allowance != EXPECTED_REASONING_ALLOWANCE:
        raise V25ManualEvaluationConfigurationError("reasoning token allowance must be 1024")
    if BEHAVIOR_POLICY_V25.policy_id != EXPECTED_POLICY_ID:
        raise V25ManualEvaluationConfigurationError("behavior policy v25 is unavailable")
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
        raise V25ManualEvaluationConfigurationError("background providers must remain Ollama")
    if settings.embedding_provider.value != "ollama":
        raise V25ManualEvaluationConfigurationError("embedding provider must remain Ollama")


def _safe_manifest(raw: Mapping[str, Any]) -> dict[str, Any]:
    safe = {key: raw.get(key) for key in _SAFE_MANIFEST_KEYS}
    if safe["policy_id"] != EXPECTED_POLICY_ID or safe["policy_schema_version"] != 25:
        raise RuntimeError("production composition did not use behavior policy v25")
    if safe["character_delivery_decision_schema_version"] != 2:
        raise RuntimeError("production composition did not use delivery decision v2")
    return safe


async def _run_replica(
    *,
    settings: Settings,
    database_path: Path,
    alembic_config: Path,
    replica_number: int,
    ledger: AtomicOpenAICallLedger,
    checkpoint: Callable[[], None],
    behavior_policy: BehaviorPolicy = BEHAVIOR_POLICY_V25,
    public_turns: tuple[dict[str, Any], ...] = PUBLIC_TURNS,
    public_session_prefix: str = "v25-manual-replica",
    expected_provider: ConversationProviderKind = EXPECTED_PROVIDER,
    expected_model: str = EXPECTED_MODEL,
    safe_manifest: Callable[[Mapping[str, Any]], dict[str, Any]] = _safe_manifest,
) -> dict[str, Any]:
    public_session_id = f"{public_session_prefix}-{replica_number}"
    runtime, _ = await _build_runtime(
        settings,
        database_path,
        alembic_config=alembic_config,
        behavior_policy=behavior_policy,
    )
    binding = TurnScopeBinding()
    runtime.conversation_provider.delegate = BudgetedOpenAIProvider(
        delegate=runtime.conversation_provider.delegate,
        ledger=ledger,
        scope_binding=binding,
    )
    record: dict[str, Any] = {
        "session_id": public_session_id,
        "fresh_database": True,
        "completed": False,
        "turns": [],
    }
    application_session_id = runtime.services.start_session.execute().session_id
    ids = Uuid4Generator()
    try:
        for fixture_turn in public_turns:
            turn_number = cast(int, fixture_turn["turn"])
            scope = PublicTurnScope(
                session_id=public_session_id,
                turn=turn_number,
                turn_id=cast(str, fixture_turn["id"]),
            )
            first_attempt = len(runtime.conversation_provider.attempts)
            binding.set(scope)
            try:
                reply = await runtime.services.talk.execute(
                    TalkInput(
                        user_text=cast(str, fixture_turn["user_text"]),
                        trace_id=ids.new(),
                        client_request_id=ids.new(),
                        session_id=application_session_id,
                    )
                )
            finally:
                binding.clear()
            attempts = runtime.conversation_provider.attempts[first_attempt:]
            safe_attempts = [
                _safe_attempt(attempt, index) for index, attempt in enumerate(attempts, start=1)
            ]
            usage = _safe_usage(reply)
            if (
                reply.provider != expected_provider.value
                or reply.model != expected_model
                or reply.finish_status != "completed"
                or reply.replayed
                or usage is None
                or len(safe_attempts) not in {1, 2}
            ):
                raise RuntimeError("turn did not produce one comparable committed OpenAI reply")
            cast(list[dict[str, Any]], record["turns"]).append(
                {
                    "turn": turn_number,
                    "turn_id": fixture_turn["id"],
                    "user": fixture_turn["user_text"],
                    "reply": _public_sampled_reply(reply),
                    "generation": {
                        "provider": reply.provider,
                        "model": reply.model,
                        "finish_status": reply.finish_status,
                        "replayed": reply.replayed,
                    },
                    "usage": usage,
                    "timings_ms": _safe_timings(reply),
                    "provider_attempt_count": len(safe_attempts),
                    "provider_attempts": safe_attempts,
                    "manifest": safe_manifest(_sanitized_manifest(reply)),
                }
            )
            checkpoint()
        record["completed"] = True
        checkpoint()
        return record
    finally:
        runtime.services.close_session.execute(application_session_id)
        runtime.close()


async def run(
    *,
    output_path: Path,
    alembic_config: Path,
    execute: bool,
    maximum_provider_calls: int | None,
    maximum_cost_usd: float | None,
    authorized_plan_digest: str | None,
    show_replies: bool,
) -> dict[str, Any]:
    _reject_retired_paid_execution()
    _preflight(
        execute=execute,
        maximum_provider_calls=maximum_provider_calls,
        maximum_cost_usd=maximum_cost_usd,
        authorized_plan_digest=authorized_plan_digest,
    )
    settings = Settings()
    _validate_settings(settings)
    assert maximum_provider_calls is not None
    assert maximum_cost_usd is not None
    ledger = AtomicOpenAICallLedger(
        maximum_calls=maximum_provider_calls,
        maximum_cost_usd=maximum_cost_usd,
        required_base_calls=REQUIRED_BASE_CALLS,
    )
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "checkpoint": "14.2",
        "purpose": "v25_exact_manual_failure_recheck",
        "status": "running",
        "artifact_id": f"satori-checkpoint142-openai-v25-manual:{uuid.uuid4()}",
        "execution_plan_digest": execution_plan_digest(),
        "artifact_contract": {
            "contains_public_dialogue_and_replies": True,
            "retains_remote_request_content": False,
            "retains_private_application_context": False,
            "retains_secret_values": False,
            "retains_temporary_databases": False,
            "automated_text_judging_performed": False,
        },
        "configuration": {
            "provider": EXPECTED_PROVIDER.value,
            "model": EXPECTED_MODEL,
            "reasoning_effort": EXPECTED_REASONING_EFFORT.value,
            "reasoning_token_allowance": EXPECTED_REASONING_ALLOWANCE,
            "policy_id": EXPECTED_POLICY_ID,
            "application_state_scope": "fresh_disposable_database_per_replica",
            "derived_processing": "none",
        },
        "budget": ledger.snapshot(),
        "sessions": [],
    }

    def checkpoint() -> None:
        report["budget"] = ledger.snapshot()
        _write_report(output_path, report)

    ledger.on_change = checkpoint
    checkpoint()
    try:
        with tempfile.TemporaryDirectory(prefix="satori-checkpoint142-openai-v25-manual-") as tmp:
            for replica in range(1, EXPECTED_REPLICA_COUNT + 1):
                record = await _run_replica(
                    settings=settings,
                    database_path=Path(tmp) / f"replica-{replica}.db",
                    alembic_config=alembic_config,
                    replica_number=replica,
                    ledger=ledger,
                    checkpoint=checkpoint,
                )
                cast(list[dict[str, Any]], report["sessions"]).append(record)
                checkpoint()
        report["status"] = "completed_awaiting_human_review"
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["sample_digest"] = _digest(
            {"sessions": report["sessions"], "execution_plan_digest": execution_plan_digest()}
        )
        checkpoint()
        if show_replies:
            for session in cast(list[dict[str, Any]], report["sessions"]):
                for turn in cast(list[dict[str, Any]], session["turns"]):
                    print(
                        f"[{session['session_id']}/turn {turn['turn']}] {turn['reply']}",
                        flush=True,
                    )
        return report
    except BaseException as error:
        report["status"] = "failed"
        report["failed_at"] = datetime.now(UTC).isoformat()
        report["failure"] = {"error_type": type(error).__name__}
        checkpoint()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect the historical v25 manual gate; paid execution is retired."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Retired compatibility flag; any attempted paid execution is rejected.",
    )
    parser.add_argument("--max-provider-calls", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--authorized-plan-digest")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--alembic-config", type=Path, default=Path("alembic.ini"))
    parser.add_argument("--show-replies", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute:
        print(json.dumps(inspect_plan(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _reject_retired_paid_execution()
    _preflight(
        execute=True,
        maximum_provider_calls=args.max_provider_calls,
        maximum_cost_usd=args.max_cost_usd,
        authorized_plan_digest=args.authorized_plan_digest,
    )
    if args.output is None:
        raise V25ManualEvaluationConfigurationError("--output is required with --execute")
    completed = asyncio.run(
        run(
            output_path=args.output,
            alembic_config=args.alembic_config,
            execute=args.execute,
            maximum_provider_calls=args.max_provider_calls,
            maximum_cost_usd=args.max_cost_usd,
            authorized_plan_digest=args.authorized_plan_digest,
            show_replies=args.show_replies,
        )
    )
    print(json.dumps({"status": completed["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
