"""Three-session local production gate for Checkpoint 14.2 behavior policy v20.

The evaluator uses fresh disposable databases and the production composition with local Ollama
foreground inference. It retains only the public fixture, sampled replies and safe metadata;
provider prompts, private context, credentials and disposable databases are never retained.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from satori.application.conversation.policy import BEHAVIOR_POLICY_V20
from satori.core.conversation import ConversationProviderError
from tests.checkpoint142_local_production_eval import (
    SESSION_COUNT,
    _local_settings,
    _usage_tokens,
)
from tests.stage81_real_eval import _build_runtime, _new_record, _run_dialogue, _write_report

REPORT_SCHEMA_VERSION = 1
EXPECTED_POLICY_ID = "satori.conversation.behavior.v20"
EXPECTED_PLAN_SCHEMA_VERSION = 3
EXPECTED_SUPPORT_AXES = (
    ("owned_evaluation", "none", "none"),
    ("grounded_direction", "supportive_push", "gentle"),
)
FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_character_sampling_v2.json"


def _load_fixture_turns() -> tuple[dict[str, Any], ...]:
    corpus = cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
    suite = cast(dict[str, Any], corpus["primary_suite"])
    source_turns = cast(list[dict[str, Any]], suite["turns"])
    return tuple(
        {
            "turn": turn["turn"],
            "id": turn["id"],
            "user_text": turn["user_text"],
            "semantic_tags": list(cast(list[str], turn["semantic_tags"])),
        }
        for turn in source_turns
    )


FIXTURE_TURNS = _load_fixture_turns()


def _generation_is_complete(turn: dict[str, Any]) -> bool:
    generation = cast(dict[str, Any], turn.get("generation", {}))
    finish_status = str(generation.get("finish_status", "")).strip().casefold()
    return (
        bool(finish_status)
        and finish_status not in {"length", "incomplete"}
        and not bool(generation.get("potentially_incomplete", True))
    )


async def run(*, output_path: Path, alembic_config: Path) -> dict[str, Any]:
    settings = _local_settings()
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "checkpoint": "14.2",
        "purpose": "character_expression_v20_local_production_gate",
        "status": "running",
        "contains_raw_public_eval_dialogue": True,
        "contains_raw_public_sampled_replies": True,
        "contains_raw_provider_prompt_or_private_context": False,
        "contains_raw_memory_or_credential": False,
        "database_artifacts_preserved": False,
        "configuration": {
            "conversation_provider": settings.conversation_provider.value,
            "conversation_model": settings.conversation_model,
            "policy_id": EXPECTED_POLICY_ID,
            "character_expression_plan_schema_version": EXPECTED_PLAN_SCHEMA_VERSION,
            "derived_mode": "none",
        },
        "human_review_dimensions": [
            "acknowledgement_does_not_consume_the_reply",
            "owned_satori_contribution_not_paraphrase",
            "care_and_character_are_both_legible",
            "supportive_push_is_grounded_bounded_and_gentle",
            "no_invented_cause_intent_remaining_work_or_closeness",
            "no_shame_or_productivity_worth_coupling",
            "no_repeated_catchphrase_or_reply_scaffold",
        ],
        "sessions": [],
    }

    def checkpoint() -> None:
        _write_report(output_path, report)

    checkpoint()
    with tempfile.TemporaryDirectory(prefix="satori-v20-local-") as directory:
        database_directory = Path(directory)
        for session_number in range(1, SESSION_COUNT + 1):
            database_path = database_directory / f"session-{session_number}.db"
            record = _new_record(
                f"character-v20-local-session-{session_number}",
                database_path,
                False,
            )
            cast(list[dict[str, Any]], report["sessions"]).append(record)
            checkpoint()
            runtime, _ = await _build_runtime(
                settings,
                database_path,
                alembic_config=alembic_config,
                behavior_policy=BEHAVIOR_POLICY_V20,
            )
            try:
                try:
                    await _run_dialogue(
                        runtime,
                        record,
                        FIXTURE_TURNS,
                        derived_mode="none",
                        checkpoint=checkpoint,
                    )
                except ConversationProviderError as error:
                    record["failure"] = {"error_type": type(error).__name__}
            finally:
                record["provider_attempt_count"] = len(runtime.conversation_provider.attempts)
                record["provider_attempts"] = [
                    asdict(attempt) for attempt in runtime.conversation_provider.attempts
                ]
                checkpoint()
                runtime.close()

    sessions = cast(list[dict[str, Any]], report["sessions"])
    turns = [turn for session in sessions for turn in cast(list[dict[str, Any]], session["turns"])]
    for turn in turns:
        manifest = cast(dict[str, Any], turn["manifest"])
        turn_index = cast(int, turn["turn"]) - 1
        actual_support_axes = (
            manifest["character_contribution_mode"],
            manifest["character_motivational_posture"],
            manifest["character_pressure_level"],
        )
        if manifest["policy_id"] != EXPECTED_POLICY_ID:
            raise RuntimeError("v20 local evaluator did not use the expected behavior policy")
        if manifest["character_expression_plan_schema_version"] != EXPECTED_PLAN_SCHEMA_VERSION:
            raise RuntimeError("v20 local evaluator did not use character expression plan v3")
        if actual_support_axes != EXPECTED_SUPPORT_AXES[turn_index]:
            raise RuntimeError("v20 local evaluator selected unexpected support axes")

    report["aggregate"] = {
        "fresh_session_count": len(sessions),
        "completed_session_count": sum(bool(session["completed"]) for session in sessions),
        "committed_turn_count": len(turns),
        "provider_call_count": sum(
            cast(int, session["provider_attempt_count"]) for session in sessions
        ),
        "failed_provider_call_count": sum(
            not cast(bool, attempt["succeeded"])
            for session in sessions
            for attempt in cast(list[dict[str, Any]], session["provider_attempts"])
        ),
        "input_tokens": sum(_usage_tokens(turn, "input_tokens") for turn in turns),
        "output_tokens": sum(_usage_tokens(turn, "output_tokens") for turn in turns),
        "incomplete_output_count": sum(not _generation_is_complete(turn) for turn in turns),
    }
    report["status"] = (
        "completed"
        if len(turns) == SESSION_COUNT * len(FIXTURE_TURNS)
        and all(bool(session["completed"]) for session in sessions)
        and all(_generation_is_complete(turn) for turn in turns)
        and cast(dict[str, Any], report["aggregate"])["failed_provider_call_count"] == 0
        else "rejected"
    )
    checkpoint()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--alembic-config", type=Path, default=Path("alembic.ini"))
    arguments = parser.parse_args()
    asyncio.run(run(output_path=arguments.output, alembic_config=arguments.alembic_config))
