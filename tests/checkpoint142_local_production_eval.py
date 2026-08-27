"""Three-session local production gate for the current Checkpoint 14.2 character policy.

The evaluator uses fresh disposable databases and the real production composition with local
Ollama foreground inference. It preserves only the approved public fixture and sampled replies;
provider prompts, private context, credentials and disposable databases are never retained.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from satori.config import ConversationProviderKind, Environment, Settings
from tests.stage81_real_eval import _build_runtime, _new_record, _run_dialogue, _write_report

REPORT_SCHEMA_VERSION = 1
SESSION_COUNT = 3
EXPECTED_POLICY_ID = "satori.conversation.behavior.v19"
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


def _local_settings() -> Settings:
    return Settings(
        environment=Environment.TEST,
        conversation_provider=ConversationProviderKind.OLLAMA,
        conversation_model="qwen3:4b-instruct",
        conversation_provider_base_url="http://127.0.0.1:11434",
    )


def _usage_tokens(turn: dict[str, Any], key: str) -> int:
    usage = turn.get("usage")
    if not isinstance(usage, dict):
        return 0
    value = usage.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


async def run(*, output_path: Path, alembic_config: Path) -> dict[str, Any]:
    settings = _local_settings()
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "checkpoint": "14.2",
        "purpose": "character_expression_v19_local_production_gate",
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
            "derived_mode": "none",
        },
        "human_review_dimensions": [
            "recognizable_owned_satori_reaction",
            "guarded_warmth_without_generic_praise",
            "situation_directed_wit_without_user_attack",
            "explicit_contrast_without_paraphrase_or_inference",
            "no_unsolicited_advice_or_service_offer",
            "fresh_relationship_without_invented_shared_history",
        ],
        "sessions": [],
    }

    def checkpoint() -> None:
        _write_report(output_path, report)

    checkpoint()
    with tempfile.TemporaryDirectory(prefix="satori-v19-local-") as directory:
        database_directory = Path(directory)
        for session_number in range(1, SESSION_COUNT + 1):
            database_path = database_directory / f"session-{session_number}.db"
            record = _new_record(
                f"character-v19-local-session-{session_number}",
                database_path,
                False,
            )
            cast(list[dict[str, Any]], report["sessions"]).append(record)
            checkpoint()
            runtime, _ = await _build_runtime(
                settings,
                database_path,
                alembic_config=alembic_config,
            )
            try:
                await _run_dialogue(
                    runtime,
                    record,
                    FIXTURE_TURNS,
                    derived_mode="none",
                    checkpoint=checkpoint,
                )
            finally:
                runtime.close()

    sessions = cast(list[dict[str, Any]], report["sessions"])
    turns = [turn for session in sessions for turn in cast(list[dict[str, Any]], session["turns"])]
    if any(
        cast(dict[str, Any], turn["manifest"])["policy_id"] != EXPECTED_POLICY_ID for turn in turns
    ):
        raise RuntimeError("local character evaluator did not use the expected behavior policy")
    report["aggregate"] = {
        "fresh_session_count": len(sessions),
        "completed_session_count": sum(bool(session["completed"]) for session in sessions),
        "committed_turn_count": len(turns),
        "provider_call_count": sum(cast(int, turn["provider_attempt_count"]) for turn in turns),
        "input_tokens": sum(_usage_tokens(turn, "input_tokens") for turn in turns),
        "output_tokens": sum(_usage_tokens(turn, "output_tokens") for turn in turns),
    }
    report["status"] = "completed"
    checkpoint()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--alembic-config", type=Path, default=Path("alembic.ini"))
    arguments = parser.parse_args()
    asyncio.run(run(output_path=arguments.output, alembic_config=arguments.alembic_config))
