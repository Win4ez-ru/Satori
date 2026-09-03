"""Offline regressions for version-neutral OpenAI manual-evaluation support."""

from __future__ import annotations

from typing import Any

import pytest

from satori.config import ConversationProviderKind
from tests.checkpoint142_openai_manual_support import (
    validate_manual_evaluation_sessions,
)

_TIMING_KEYS = (
    "intake_ms",
    "recent_context_ms",
    "relationship_projection_ms",
    "retrieval_embedding_ms",
    "retrieval_search_ranking_ms",
    "affect_materialization_ms",
    "appraisal_request_build_ms",
    "emotion_appraisal_ms",
    "cognition_planning_ms",
    "context_assembly_ms",
    "conversation_generation_ms",
    "response_regeneration_ms",
    "grounding_validation_ms",
    "canonical_commit_ms",
    "committed_reply_ms",
)


def _sessions(*, temperature: float) -> list[dict[str, Any]]:
    return [
        {
            "session_id": "temperature-replica-1",
            "fresh_database": True,
            "completed": True,
            "turns": [
                {
                    "turn": 1,
                    "turn_id": "temperature-turn",
                    "user": "Открытая проверочная реплика.",
                    "status": "completed",
                    "provider_call_observed": True,
                    "reply": "Открытый проверочный ответ.",
                    "generation": {
                        "provider": "openai",
                        "model": "gpt-5.6-terra",
                        "finish_status": "completed",
                        "replayed": False,
                    },
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cached_input_tokens": 0,
                        "cache_write_input_tokens": 0,
                    },
                    "timings_ms": {key: 0.0 for key in _TIMING_KEYS},
                    "provider_attempt_count": 1,
                    "provider_attempts": [
                        {
                            "attempt_number": 1,
                            "wall_ms": 1.0,
                            "request_schema_version": 1,
                            "context_schema_version": 16,
                            "message_count": 1,
                            "message_role_counts": {"user": 1},
                            "request_content_chars": 28,
                            "temperature": temperature,
                            "max_output_tokens": 80,
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "provider_metrics": None,
                            "finish_status": "completed",
                            "succeeded": True,
                            "error_type": None,
                        }
                    ],
                    "usage_source": "atomic_paid_call_ledger",
                    "selected_provider_attempt": 1,
                    "manifest": {"safe": True},
                }
            ],
        }
    ]


def _validate(
    sessions: object,
    *,
    expected_turn_temperatures: tuple[float, ...],
    expected_turn_visible_output_token_limits: tuple[int, ...] = (80,),
) -> list[dict[str, Any]]:
    return validate_manual_evaluation_sessions(
        sessions,
        public_turns=(
            {
                "turn": 1,
                "id": "temperature-turn",
                "user_text": "Открытая проверочная реплика.",
            },
        ),
        expected_turn_temperatures=expected_turn_temperatures,
        expected_turn_visible_output_token_limits=(expected_turn_visible_output_token_limits),
        expected_replica_count=1,
        public_session_prefix="temperature-replica",
        expected_provider=ConversationProviderKind.OPENAI,
        expected_model="gpt-5.6-terra",
        expected_context_schema_version=16,
        visible_output_token_ceiling=768,
        maximum_response_chars=12_000,
        safe_manifest=dict,
    )


def test_session_validator_accepts_digest_bound_zero_temperature() -> None:
    sessions = _sessions(temperature=0.0)

    assert _validate(sessions, expected_turn_temperatures=(0.0,)) == sessions


def test_session_validator_rejects_temperature_drift() -> None:
    with pytest.raises(ValueError, match="provider-attempt evidence drift"):
        _validate(_sessions(temperature=0.0), expected_turn_temperatures=(0.3,))


def test_session_validator_rejects_visible_output_token_drift() -> None:
    with pytest.raises(ValueError, match="provider-attempt evidence drift"):
        _validate(
            _sessions(temperature=0.3),
            expected_turn_temperatures=(0.3,),
            expected_turn_visible_output_token_limits=(96,),
        )


@pytest.mark.parametrize("temperatures", [(), (0.3, 0.0), (-0.1,), (2.1,)])
def test_session_validator_rejects_invalid_expected_temperature_vector(
    temperatures: tuple[float, ...],
) -> None:
    with pytest.raises(ValueError, match="temperature vector"):
        _validate(_sessions(temperature=0.3), expected_turn_temperatures=temperatures)


@pytest.mark.parametrize("limits", [(), (80, 96), (0,), (769,), (True,)])
def test_session_validator_rejects_invalid_expected_visible_output_vector(
    limits: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="visible-output-token vector"):
        _validate(
            _sessions(temperature=0.3),
            expected_turn_temperatures=(0.3,),
            expected_turn_visible_output_token_limits=limits,
        )
