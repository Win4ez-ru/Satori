"""Offline structure and review contract for the four-module employer demo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_employer_demo_v1.json"

EXPECTED_TOP_LEVEL_KEYS = {
    "schema_version",
    "corpus_id",
    "checkpoint",
    "policy_id",
    "execution_contract",
    "invariants",
    "hard_safety_dimensions",
    "quality_dimension_registry",
    "modules",
    "acceptance",
}
EXPECTED_MODULE_IDS = (
    "core_emotional",
    "intellectual_partner",
    "hurt_and_repair",
    "identity_and_memory",
)
EXPECTED_SEMANTIC_FAMILIES = {
    "achievement",
    "active_collaboration",
    "direct_personal_devaluation",
    "disagreement",
    "explicit_depletion",
    "important_help",
    "absent_memory",
    "indirect_recall",
    "repair_offer",
    "technical_identity",
    "topic_closure",
}
FORBIDDEN_REPLY_KEY_PARTS = (
    "assistant_text",
    "desired_reply",
    "desired_response",
    "exact_text",
    "example_reply",
    "expected_reply",
    "golden_reply",
    "reference_reply",
    "required_phrase",
    "required_reply",
    "required_response",
    "target_reply",
    "template_reply",
)


def _load_corpus() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _normalized_key(key: str) -> str:
    return key.casefold().replace("-", "_").replace(" ", "_")


def _forbidden_reply_keys(value: object, *, path: str = "$.") -> tuple[str, ...]:
    matches: list[str] = []
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = _normalized_key(key)
            if any(part in normalized for part in FORBIDDEN_REPLY_KEY_PARTS):
                matches.append(f"{path}{key}")
            matches.extend(_forbidden_reply_keys(nested, path=f"{path}{key}."))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            matches.extend(_forbidden_reply_keys(nested, path=f"{path}[{index}]."))
    return tuple(matches)


def test_employer_demo_contract_is_versioned_modular_and_offline_by_default() -> None:
    corpus = _load_corpus()
    execution = cast(dict[str, Any], corpus["execution_contract"])
    modules = cast(list[dict[str, Any]], corpus["modules"])

    assert set(corpus) == EXPECTED_TOP_LEVEL_KEYS
    assert corpus["schema_version"] == 1
    assert corpus["corpus_id"] == "satori.checkpoint142.employer-demo.ru.v1"
    assert corpus["checkpoint"] == "14.2"
    assert corpus["policy_id"] == "satori.conversation.behavior.v24"
    assert tuple(str(module["id"]) for module in modules) == EXPECTED_MODULE_IDS
    assert execution == {
        "offline_only_by_default": True,
        "provider_execution_requires_separate_authorization": True,
        "provider_calls_authorized_by_this_fixture": False,
        "target_provider": "openai",
        "target_model": "gpt-5.6-terra",
        "fresh_replica_count": 3,
        "automated_text_judging_performed": False,
        "provider_sample_is_authority": False,
    }
    assert _forbidden_reply_keys(corpus) == ()


def test_employer_demo_modules_have_closed_public_review_structure() -> None:
    corpus = _load_corpus()
    execution = cast(dict[str, Any], corpus["execution_contract"])
    modules = cast(list[dict[str, Any]], corpus["modules"])
    hard_safety = {str(item) for item in cast(list[object], corpus["hard_safety_dimensions"])}
    quality = {str(item) for item in cast(list[object], corpus["quality_dimension_registry"])}
    review_registry = hard_safety | quality
    seen_turn_ids: set[str] = set()
    seen_semantic_tags: set[str] = set()

    assert hard_safety
    assert quality
    assert hard_safety.isdisjoint(quality)
    for module in modules:
        assert isinstance(module["purpose"], str)
        assert str(module["purpose"]).strip()
        assert module["fresh_database_per_replica"] is True
        assert str(module["relationship_setup"]) in {
            "fresh_undeveloped_neutral",
            "developing_neutral",
            "established_positive",
        }
        turns = cast(list[dict[str, Any]], module["turns"])
        turn_numbers = [int(turn["turn"]) for turn in turns]
        assert turn_numbers == list(range(1, len(turns) + 1))
        assert len(turns) in {3, 4}

        for turn in turns:
            turn_id = f"{module['id']}:{turn['id']}"
            assert turn_id not in seen_turn_ids
            seen_turn_ids.add(turn_id)
            assert isinstance(turn["user_text"], str)
            assert str(turn["user_text"]).strip()
            semantic_tags = {str(item) for item in cast(list[object], turn["semantic_tags"])}
            dimensions = {str(item) for item in cast(list[object], turn["review_dimensions"])}
            assert semantic_tags
            assert dimensions
            assert dimensions <= quality
            seen_semantic_tags.update(semantic_tags)

        restart_after = set(cast(list[int], module.get("restart_after_turns", [])))
        derived_after = set(cast(list[int], module.get("derived_processing_after_turns", [])))
        assert restart_after <= set(turn_numbers[:-1])
        assert derived_after <= set(turn_numbers[:-1])
        assert {
            str(item) for item in cast(list[object], module["dialogue_review_dimensions"])
        } <= review_registry
        assert {
            str(item) for item in cast(list[object], module["cross_replica_review_dimensions"])
        } <= review_registry

    assert seen_semantic_tags >= EXPECTED_SEMANTIC_FAMILIES
    modules_by_id = {str(module["id"]): module for module in modules}
    assert modules_by_id["hurt_and_repair"]["derived_processing_after_turns"] == [1, 2]
    assert modules_by_id["identity_and_memory"]["derived_processing_after_turns"] == [1]
    total_turns = sum(len(cast(list[object], module["turns"])) for module in modules)
    assert total_turns == 13
    assert total_turns * int(execution["fresh_replica_count"]) == 39
    assert execution["provider_calls_authorized_by_this_fixture"] is False


def test_employer_demo_acceptance_requires_every_module_and_human_review() -> None:
    corpus = _load_corpus()
    acceptance = cast(dict[str, Any], corpus["acceptance"])
    execution = cast(dict[str, Any], corpus["execution_contract"])

    assert acceptance == {
        "required_module_count": 4,
        "required_fresh_replica_count_per_module": 3,
        "all_hard_safety_dimensions_must_pass_on_every_turn": True,
        "all_declared_turn_quality_dimensions_must_pass": True,
        "all_dialogue_review_dimensions_must_pass": True,
        "all_cross_replica_review_dimensions_must_pass": True,
        "human_review_required": True,
        "automated_text_judging_performed": False,
        "provider_sample_is_authority": False,
        "one_module_cannot_accept_employer_demo_readiness": True,
    }
    assert acceptance["required_fresh_replica_count_per_module"] == execution["fresh_replica_count"]
    assert acceptance["automated_text_judging_performed"] is False
    assert acceptance["provider_sample_is_authority"] is False
