"""Versioned deterministic long-period trigger corpus for Stage 12."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from satori.domain.reflection import (
    REFLECTION_POLICY_VERSION_V1,
    REFLECTION_SCHEMA_VERSION_V1,
    ReflectionTriggerKind,
    reflection_trigger_reason,
)


def corpus() -> dict[str, object]:
    path = Path(__file__).parent / "fixtures" / "stage12_reflection_v1.json"
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def scenarios() -> list[dict[str, object]]:
    loaded = corpus().get("scenarios")
    assert isinstance(loaded, list)
    assert all(isinstance(item, dict) for item in loaded)
    return loaded


def test_corpus_versions_match_implemented_policy() -> None:
    loaded = corpus()
    assert loaded["schema_version"] == REFLECTION_SCHEMA_VERSION_V1
    assert loaded["policy_version"] == REFLECTION_POLICY_VERSION_V1
    assert len(scenarios()) == 10


@pytest.mark.parametrize("scenario", scenarios(), ids=lambda item: str(item["id"]))
def test_long_period_trigger_scenarios(scenario: dict[str, object]) -> None:
    result = reflection_trigger_reason(
        ReflectionTriggerKind(str(scenario["trigger"])),
        root_count=int(str(scenario["root_count"])),
        interaction_count=int(str(scenario["interaction_count"])),
        observation_span=timedelta(days=float(str(scenario["observation_span_days"]))),
        completed_within_day=bool(scenario["completed_within_day"]),
        completed_within_cooldown=bool(scenario["completed_within_cooldown"]),
    )
    assert result == scenario["expected_reason"]
