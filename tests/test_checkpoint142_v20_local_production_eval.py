"""Offline wiring contract for the manual v20 local production gate."""

import json
from pathlib import Path

from tests.checkpoint142_local_production_eval import SESSION_COUNT
from tests.checkpoint142_v20_local_production_eval import (
    EXPECTED_PLAN_SCHEMA_VERSION,
    EXPECTED_POLICY_ID,
    EXPECTED_SUPPORT_AXES,
    FIXTURE_TURNS,
    REPORT_SCHEMA_VERSION,
    _generation_is_complete,
)


def test_v20_local_gate_is_pinned_to_the_versioned_public_fixture() -> None:
    fixture_path = Path(__file__).with_name("fixtures") / "checkpoint142_character_sampling_v2.json"
    corpus = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert REPORT_SCHEMA_VERSION == 1
    assert SESSION_COUNT == 3
    assert EXPECTED_POLICY_ID == "satori.conversation.behavior.v20"
    assert EXPECTED_PLAN_SCHEMA_VERSION == 3
    assert EXPECTED_SUPPORT_AXES == (
        ("owned_evaluation", "none", "none"),
        ("grounded_direction", "supportive_push", "gentle"),
    )
    assert [
        {
            "turn": turn["turn"],
            "id": turn["id"],
            "user_text": turn["user_text"],
            "semantic_tags": turn["semantic_tags"],
        }
        for turn in FIXTURE_TURNS
    ] == [
        {
            "turn": turn["turn"],
            "id": turn["id"],
            "user_text": turn["user_text"],
            "semantic_tags": turn["semantic_tags"],
        }
        for turn in corpus["primary_suite"]["turns"]
    ]


def test_v20_local_gate_rejects_incomplete_or_limit_saturated_generation() -> None:
    assert _generation_is_complete(
        {"generation": {"finish_status": "stop", "potentially_incomplete": False}}
    )
    assert not _generation_is_complete(
        {"generation": {"finish_status": "length", "potentially_incomplete": True}}
    )
    assert not _generation_is_complete(
        {"generation": {"finish_status": "stop", "potentially_incomplete": True}}
    )
    assert not _generation_is_complete({"generation": {}})
