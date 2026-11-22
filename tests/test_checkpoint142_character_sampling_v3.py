"""Offline contract for the v21 non-echoing human-reviewed sampling gate."""

import json
from pathlib import Path
from typing import Any

from tests.checkpoint142_openai_character_eval import (
    V21_GATE_SPEC,
    find_forbidden_reply_contract_keys,
    validate_sampling_fixture,
)

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_character_sampling_v3.json"


def test_v21_sampling_corpus_is_exact_bounded_and_human_reviewed() -> None:
    corpus: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    validate_sampling_fixture(corpus)
    suite = corpus["primary_suite"]

    assert corpus["schema_version"] == 3
    assert corpus["corpus_id"] == V21_GATE_SPEC.corpus_id
    assert corpus["policy_id"] == "satori.conversation.behavior.v21"
    assert [turn["user_text"] for turn in suite["turns"]] == [
        "Привет. Я сегодня наконец закончил сложную часть проекта",
        "Знаешь, я почему-то почти не рад этому. Скорее просто выжат",
    ]
    assert suite["fresh_session_count"] == 3
    assert suite["turns_per_session"] == 2
    assert suite["requires_explicit_paid_confirmation"] is True
    assert suite["call_budget"]["maximum_provider_calls"] == 9
    assert suite["acceptance"]["provider_sample_is_authority"] is False
    assert find_forbidden_reply_contract_keys(corpus) == ()
