"""Offline safety and contract tests for the Checkpoint 14.2 OpenAI character gate."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from tests.checkpoint142_openai_character_eval import (
    ABSOLUTE_MAX_PROVIDER_CALLS,
    EXPECTED_CORPUS_ID,
    EXPECTED_POLICY_ID,
    EXPECTED_PRIMARY_TEXTS,
    EXPECTED_REQUIRED_BASE_CALLS,
    EXPECTED_SESSION_COUNT,
    V20_EXPECTED_CORPUS_ID,
    V20_EXPECTED_POLICY_ID,
    V20_FIXTURE_PATH,
    V22_EXPECTED_CORPUS_ID,
    V22_EXPECTED_POLICY_ID,
    V22_FIXTURE_PATH,
    V23_EXPECTED_CORPUS_ID,
    V23_EXPECTED_POLICY_ID,
    V23_FIXTURE_PATH,
    BudgetedOpenAIConversationProvider,
    CharacterGateConfigurationError,
    OpenAICallLedger,
    ProviderCallBudgetExhausted,
    _primary_turns,
    _write_safe_report,
    aggregate_human_review,
    assert_safe_artifact,
    compact_public_session,
    compact_public_turn,
    find_forbidden_reply_contract_keys,
    load_sampling_fixture,
    preflight_paid_execution,
    sample_content_digest,
    validate_completed_sample_report,
    validate_sampling_fixture,
)


def _request(trace_id: str) -> ConversationProviderRequest:
    return ConversationProviderRequest(
        schema_version=1,
        context_schema_version=19,
        messages=(ConversationMessage(ConversationMessageRole.USER, "approved public fixture"),),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.3,
            max_output_tokens=80,
        ),
        trace_id=trace_id,
    )


@dataclass(slots=True)
class _FixedProvider:
    call_count: int = 0

    async def generate(
        self,
        _request: ConversationProviderRequest,
        /,
    ) -> ConversationProviderResponse:
        self.call_count += 1
        return ConversationProviderResponse(
            text="  Ну наконец-то.\n",  # noqa: RUF001 - intentional Russian fixture
            provider="openai",
            model="gpt-5.6-terra",
            finish_status="completed",
            usage=ConversationUsage(input_tokens=1500, output_tokens=90),
            metrics=ProviderExecutionMetrics(
                requested_output_token_limit=80,
                provider_output_token_limit=1104,
                reasoning_output_tokens=50,
                visible_output_tokens=40,
            ),
        )


def _raw_turn(
    *,
    reply: str,
    turn: int = 1,
    turn_id: str = "completed-difficult-project-part",
    user_text: str = EXPECTED_PRIMARY_TEXTS[0],
    provider: str = "openai",
    reported_model: str = "gpt-5.6-terra",
    potentially_incomplete: bool = False,
    replayed: bool = False,
) -> dict[str, Any]:
    return {
        "turn": turn,
        "id": turn_id,
        "user_text": user_text,
        "reply": reply,
        "generation": {
            "provider": provider,
            "model": reported_model,
            "finish_status": "completed",
            "potentially_incomplete": potentially_incomplete,
            "replayed": replayed,
            "provider_messages": "private-message-sentinel",
        },
        "usage": {"input_tokens": 1500, "output_tokens": 90},
        "timings_ms": {
            "conversation_generation_ms": 1000.5,
            "response_regeneration_ms": 0.0,
            "committed_reply_ms": 1010.0,
            "emotion_appraisal_ms": 5.0,
            "canonical_commit_ms": 2.0,
            "private_context_ms": "private-timing-sentinel",
        },
        "provider_attempt_count": 1,
        "provider_attempts": [
            {
                "wall_ms": 990.0,
                "max_output_tokens": 80,
                "input_tokens": 1500,
                "output_tokens": 90,
                "provider_metrics": {
                    "requested_output_token_limit": 80,
                    "provider_output_token_limit": 1104,
                    "reasoning_output_tokens": 50,
                    "visible_output_tokens": 40,
                    "response_body": "private-body-sentinel",
                },
                "finish_status": "completed",
                "succeeded": True,
                "error_type": None,
                "request_messages": "private-attempt-sentinel",
            }
        ],
        "manifest": {
            "policy_id": EXPECTED_POLICY_ID,
            "policy_schema_version": 19,
            "character_context_schema_version": 3,
            "character_expression_plan_schema_version": 2,
            "character_expression_register": "wry_warmth",
            "character_owned_reaction": "guarded_approval",
            "character_semantic_move": "mark_hard_won_result",
            "character_relational_ease": "fresh",
            "character_wit": "situation_directed",
            "character_care": "understated",
            "character_openness": "balanced",
            "character_initiative": "responsive",
            "retrieval_status": "no_relevant_memory",
            "retrieved_memory_count": 0,
            "retrieved_memory_ids": ["private-memory-sentinel"],
            "semantic_retrieval_status": "no_relevant_memory",
            "retrieved_semantic_claim_count": 0,
            "retrieved_semantic_claim_ids": ["private-claim-sentinel"],
            "emotion_appraisal_status": "succeeded",
            "relationship_expression_profile": "fresh_undeveloped_neutral",
            "affect_expression_profile": "positive_light",
            "recent_conversation_turn_count": 0,
            "disclosure_primary_mode": "answer",
            "disclosure_facets": ["identity", "personality"],
            "consecutive_same_user_message_count": 1,
            "duplicate_response_detected": False,
            "regeneration_attempted": False,
            "response_regenerated": False,
            "regeneration_reason": None,
            "private_context": "private-context-sentinel",
        },
        "provider_messages": "private-root-sentinel",
        "trace_id": "private-trace-sentinel",
        "database_path": "/private/database-sentinel.db",
        "api_key": "private-key-sentinel",
    }


def _completed_report(fixture: dict[str, Any]) -> dict[str, Any]:
    delegate = _FixedProvider()
    ledger = OpenAICallLedger(maximum_calls=9, maximum_cost_usd=1.0)
    provider = BudgetedOpenAIConversationProvider(delegate, ledger)

    async def exercise() -> None:
        for turn_number in range(EXPECTED_REQUIRED_BASE_CALLS):
            await provider.generate(_request(f"completed-turn-{turn_number}"))

    asyncio.run(exercise())
    sessions: list[dict[str, Any]] = []
    for session_number in range(1, EXPECTED_SESSION_COUNT + 1):
        raw_session = {
            "fresh_database": True,
            "completed": True,
            "turns": [
                _raw_turn(reply=f"Точная реплика достижения {session_number}"),
                _raw_turn(
                    reply=f"Точная реплика истощения {session_number}",
                    turn=2,
                    turn_id="completion-without-joy-and-depletion",
                    user_text=EXPECTED_PRIMARY_TEXTS[1],
                ),
            ],
        }
        sessions.append(compact_public_session(session_number, raw_session))
    report: dict[str, Any] = {
        "schema_version": 1,
        "recorded_at": "2026-08-27T16:00:00+00:00",
        "completed_at": "2026-08-27T16:01:00+00:00",
        "checkpoint": "14.2",
        "purpose": "openai_character_sampling_v19_primary_gate",
        "status": "completed_awaiting_human_review",
        "artifact_id": "satori-checkpoint142-openai-v19:00000000-0000-4000-8000-000000000019",
        "corpus_id": EXPECTED_CORPUS_ID,
        "policy_id": EXPECTED_POLICY_ID,
        "suite_id": cast(dict[str, Any], fixture["primary_suite"])["suite_id"],
        "artifact_contract": {
            "contains_public_fixture_dialogue": True,
            "contains_exact_public_sampled_replies": True,
            "retains_remote_request_content": False,
            "retains_private_application_context": False,
            "retains_secret_values": False,
            "retains_temporary_databases": False,
        },
        "configuration": {
            "conversation_provider": "openai",
            "conversation_model": "gpt-5.6-terra",
            "openai_reasoning_effort": "low",
            "openai_reasoning_token_allowance": 1024,
            "background_providers": "ollama",
            "policy_id": EXPECTED_POLICY_ID,
            "derived_mode": "none",
        },
        "budget": ledger.snapshot(),
        "sessions": sessions,
        "human_review": {
            "status": "pending",
            "reviewer": "human",
            "automated_text_judging_performed": False,
            "required_pair_pass_count": EXPECTED_SESSION_COUNT,
            "required_hard_safety_turn_pass_count": EXPECTED_REQUIRED_BASE_CALLS,
        },
        "acceptance": {
            "sample_complete": True,
            "provider_accepted": False,
            "reason": "human_review_pending",
        },
    }
    report["sample_digest"] = sample_content_digest(report)
    return report


def _all_true_review(fixture: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    primary = cast(dict[str, Any], fixture["primary_suite"])
    hard_definitions = cast(list[dict[str, str]], primary["hard_safety_boolean_definitions"])
    hard_keys = [definition["key"] for definition in hard_definitions]
    fixture_turns = cast(list[dict[str, Any]], primary["turns"])
    sessions: list[dict[str, Any]] = []
    for session_number in range(1, 4):
        turns: list[dict[str, Any]] = []
        for turn in fixture_turns:
            quality_keys = cast(list[str], turn["quality_boolean_keys"])
            turns.append(
                {
                    "turn": turn["turn"],
                    "id": turn["id"],
                    "hard_safety": dict.fromkeys(hard_keys, True),
                    "quality": dict.fromkeys(quality_keys, True),
                }
            )
        sessions.append({"session_number": session_number, "turns": turns})
    review = {
        "schema_version": 1,
        "corpus_id": fixture["corpus_id"],
        "artifact_id": report["artifact_id"],
        "sample_digest": report["sample_digest"],
        "sessions": sessions,
    }
    if "cross_session_boolean_definitions" in primary:
        cross_session_definitions = cast(
            list[dict[str, str]], primary["cross_session_boolean_definitions"]
        )
        review["cross_session"] = {
            definition["key"]: True for definition in cross_session_definitions
        }
    return review


def _completed_v20_report() -> tuple[dict[str, Any], dict[str, Any]]:
    fixture = load_sampling_fixture(V20_FIXTURE_PATH)
    report = _completed_report(load_sampling_fixture())
    report.update(
        {
            "purpose": "openai_character_sampling_v20_primary_gate",
            "artifact_id": ("satori-checkpoint142-openai-v20:00000000-0000-4000-8000-000000000020"),
            "corpus_id": V20_EXPECTED_CORPUS_ID,
            "policy_id": V20_EXPECTED_POLICY_ID,
            "suite_id": cast(dict[str, Any], fixture["primary_suite"])["suite_id"],
        }
    )
    cast(dict[str, Any], report["configuration"])["policy_id"] = V20_EXPECTED_POLICY_ID
    sessions = cast(list[dict[str, Any]], report["sessions"])
    expected_axes = (
        ("owned_evaluation", "none", "none"),
        ("grounded_direction", "supportive_push", "gentle"),
    )
    for session in sessions:
        for turn in cast(list[dict[str, Any]], session["turns"]):
            manifest = cast(dict[str, Any], turn["manifest"])
            manifest["policy_id"] = V20_EXPECTED_POLICY_ID
            manifest["policy_schema_version"] = 20
            manifest["character_expression_plan_schema_version"] = 3
            axes = expected_axes[cast(int, turn["turn"]) - 1]
            manifest["character_contribution_mode"] = axes[0]
            manifest["character_motivational_posture"] = axes[1]
            manifest["character_pressure_level"] = axes[2]
    report["sample_digest"] = sample_content_digest(report)
    return fixture, report


def _completed_v22_report() -> tuple[dict[str, Any], dict[str, Any]]:
    fixture = load_sampling_fixture(V22_FIXTURE_PATH)
    report = _completed_report(load_sampling_fixture())
    report.update(
        {
            "purpose": "openai_character_sampling_v22_primary_gate",
            "artifact_id": ("satori-checkpoint142-openai-v22:00000000-0000-4000-8000-000000000022"),
            "corpus_id": V22_EXPECTED_CORPUS_ID,
            "policy_id": V22_EXPECTED_POLICY_ID,
            "suite_id": cast(dict[str, Any], fixture["primary_suite"])["suite_id"],
        }
    )
    cast(dict[str, Any], report["configuration"])["policy_id"] = V22_EXPECTED_POLICY_ID
    sessions = cast(list[dict[str, Any]], report["sessions"])
    expected_support_axes = (
        ("owned_evaluation", "none", "none"),
        ("emotional_reaction", "none", "none"),
    )
    expected_flow_axes = (("implicit", "complete"), ("omit", "complete"))
    for session in sessions:
        for turn in cast(list[dict[str, Any]], session["turns"]):
            manifest = cast(dict[str, Any], turn["manifest"])
            manifest["policy_id"] = V22_EXPECTED_POLICY_ID
            manifest["policy_schema_version"] = 22
            manifest["character_expression_plan_schema_version"] = 4
            turn_index = cast(int, turn["turn"]) - 1
            support_axes = expected_support_axes[turn_index]
            manifest["character_contribution_mode"] = support_axes[0]
            manifest["character_motivational_posture"] = support_axes[1]
            manifest["character_pressure_level"] = support_axes[2]
            flow_axes = expected_flow_axes[turn_index]
            manifest["character_acknowledgement_mode"] = flow_axes[0]
            manifest["character_continuation_mode"] = flow_axes[1]
    report["sample_digest"] = sample_content_digest(report)
    return fixture, report


def _completed_v23_report() -> tuple[dict[str, Any], dict[str, Any]]:
    fixture = load_sampling_fixture(V23_FIXTURE_PATH)
    report = _completed_report(load_sampling_fixture())
    report.update(
        {
            "purpose": "openai_character_sampling_v23_primary_gate",
            "artifact_id": ("satori-checkpoint142-openai-v23:00000000-0000-4000-8000-000000000023"),
            "corpus_id": V23_EXPECTED_CORPUS_ID,
            "policy_id": V23_EXPECTED_POLICY_ID,
            "suite_id": cast(dict[str, Any], fixture["primary_suite"])["suite_id"],
        }
    )
    configuration = cast(dict[str, Any], report["configuration"])
    configuration["policy_id"] = V23_EXPECTED_POLICY_ID
    configuration["openai_reasoning_effort"] = "medium"
    sessions = cast(list[dict[str, Any]], report["sessions"])
    expected_support_axes = (
        ("owned_evaluation", "none", "none"),
        ("grounded_direction", "supportive_push", "gentle"),
    )
    expected_flow_axes = (("implicit", "complete"), ("omit", "complete"))
    for session in sessions:
        for turn in cast(list[dict[str, Any]], session["turns"]):
            manifest = cast(dict[str, Any], turn["manifest"])
            manifest["policy_id"] = V23_EXPECTED_POLICY_ID
            manifest["policy_schema_version"] = 23
            manifest["character_expression_plan_schema_version"] = 5
            turn_index = cast(int, turn["turn"]) - 1
            support_axes = expected_support_axes[turn_index]
            manifest["character_contribution_mode"] = support_axes[0]
            manifest["character_motivational_posture"] = support_axes[1]
            manifest["character_pressure_level"] = support_axes[2]
            flow_axes = expected_flow_axes[turn_index]
            manifest["character_acknowledgement_mode"] = flow_axes[0]
            manifest["character_continuation_mode"] = flow_axes[1]
    report["sample_digest"] = sample_content_digest(report)
    return fixture, report


def test_sampling_fixture_has_exact_primary_cardinality_and_separate_repeat_suite() -> None:
    fixture = load_sampling_fixture()
    primary = cast(dict[str, Any], fixture["primary_suite"])
    budget = cast(dict[str, Any], primary["call_budget"])
    primary_turns = cast(list[dict[str, Any]], primary["turns"])
    repeat = cast(dict[str, Any], fixture["repeat_awareness_suite"])
    repeat_turns = cast(list[dict[str, Any]], repeat["turns"])

    assert fixture["policy_id"] == EXPECTED_POLICY_ID
    assert primary["fresh_session_count"] == EXPECTED_SESSION_COUNT
    assert tuple(turn["user_text"] for turn in primary_turns) == EXPECTED_PRIMARY_TEXTS
    assert budget == {
        "required_base_calls": EXPECTED_REQUIRED_BASE_CALLS,
        "maximum_provider_calls": ABSOLUTE_MAX_PROVIDER_CALLS,
        "maximum_attempts_per_turn": 2,
        "retry_contract": "existing_shared_max_one_self_consistency_retry",
    }
    assert repeat["included_in_primary_paid_run"] is False
    assert repeat["requires_separate_explicit_authorization"] is True
    assert repeat_turns[0]["user_text"] == repeat_turns[1]["user_text"]
    assert tuple(turn["id"] for turn in _primary_turns(fixture)) == tuple(
        turn["id"] for turn in primary_turns
    )
    assert not (
        {turn["id"] for turn in _primary_turns(fixture)} & {turn["id"] for turn in repeat_turns}
    )


def test_v20_sampling_fixture_and_completed_report_use_schema_v3_axes() -> None:
    fixture, report = _completed_v20_report()

    validate_sampling_fixture(fixture)
    validate_completed_sample_report(fixture, report)
    accepted = aggregate_human_review(fixture, report, _all_true_review(fixture, report))

    assert accepted["accepted"] is True
    assert accepted["cross_session_pass"] is True


def test_v22_sampling_fixture_and_completed_report_use_response_act_policy() -> None:
    fixture, report = _completed_v22_report()

    validate_sampling_fixture(fixture)
    validate_completed_sample_report(fixture, report)
    accepted = aggregate_human_review(fixture, report, _all_true_review(fixture, report))

    assert accepted["accepted"] is True
    assert accepted["cross_session_pass"] is True


def test_v23_sampling_fixture_and_completed_report_use_medium_reasoning_and_plan_v5() -> None:
    fixture, report = _completed_v23_report()

    validate_sampling_fixture(fixture)
    validate_completed_sample_report(fixture, report)
    accepted = aggregate_human_review(fixture, report, _all_true_review(fixture, report))

    assert cast(dict[str, Any], report["configuration"])["openai_reasoning_effort"] == "medium"
    assert accepted["accepted"] is True
    assert accepted["cross_session_pass"] is True


def test_sampling_fixture_recursively_forbids_golden_or_desired_reply_contracts() -> None:
    fixture = load_sampling_fixture()

    assert find_forbidden_reply_contract_keys(fixture) == ()

    invalid = deepcopy(fixture)
    cast(dict[str, Any], invalid["repeat_awareness_suite"])["assistant_reply"] = "script"
    assert find_forbidden_reply_contract_keys(invalid) == (
        "$.repeat_awareness_suite.assistant_reply",
    )
    with pytest.raises(ValueError, match="scripted reply keys"):
        validate_sampling_fixture(invalid)


@pytest.mark.parametrize(
    ("confirmed", "maximum_calls", "message"),
    [
        (False, 9, "confirm-paid-openai"),
        (True, 5, "six mandatory base turns"),
        (True, 10, "nine-call envelope"),
    ],
)
def test_paid_preflight_rejects_incomplete_or_out_of_bounds_authorization(
    confirmed: bool,
    maximum_calls: int,
    message: str,
) -> None:
    fixture = load_sampling_fixture()

    with pytest.raises(CharacterGateConfigurationError, match=message):
        preflight_paid_execution(
            confirm_paid_openai=confirmed,
            maximum_provider_calls=maximum_calls,
            maximum_cost_usd=1.0,
            fixture=fixture,
        )


@pytest.mark.parametrize("maximum_calls", [6, 7, 8, 9])
def test_paid_preflight_accepts_only_bounded_call_envelopes(maximum_calls: int) -> None:
    preflight_paid_execution(
        confirm_paid_openai=True,
        maximum_provider_calls=maximum_calls,
        maximum_cost_usd=1.0,
        fixture=load_sampling_fixture(),
    )


@pytest.mark.parametrize("maximum_cost_usd", [0.0, -1.0, float("inf"), float("nan")])
def test_paid_preflight_rejects_missing_or_invalid_usd_ceiling(maximum_cost_usd: float) -> None:
    with pytest.raises(CharacterGateConfigurationError, match="positive finite USD"):
        preflight_paid_execution(
            confirm_paid_openai=True,
            maximum_provider_calls=9,
            maximum_cost_usd=maximum_cost_usd,
            fixture=load_sampling_fixture(),
        )


def test_call_ledger_blocks_retry_before_network_when_base_calls_need_reservation() -> None:
    delegate = _FixedProvider()
    ledger = OpenAICallLedger(maximum_calls=6, maximum_cost_usd=1.0)
    provider = BudgetedOpenAIConversationProvider(delegate, ledger)

    async def exercise() -> None:
        await provider.generate(_request("turn-1"))
        with pytest.raises(ProviderCallBudgetExhausted, match="remaining mandatory base"):
            await provider.generate(_request("turn-1"))

    asyncio.run(exercise())

    assert delegate.call_count == 1
    assert ledger.snapshot()["provider_call_count"] == 1


def test_call_ledger_allows_one_retry_but_blocks_a_second_before_network() -> None:
    delegate = _FixedProvider()
    ledger = OpenAICallLedger(maximum_calls=9, maximum_cost_usd=1.0)
    provider = BudgetedOpenAIConversationProvider(delegate, ledger)

    async def exercise() -> None:
        await provider.generate(_request("turn-1"))
        await provider.generate(_request("turn-1"))
        with pytest.raises(ProviderCallBudgetExhausted, match="max-one"):
            await provider.generate(_request("turn-1"))

    asyncio.run(exercise())

    snapshot = ledger.snapshot()
    assert delegate.call_count == 2
    assert snapshot["base_call_count"] == 1
    assert snapshot["provider_call_count"] == 2
    assert [call["attempt_kind"] for call in snapshot["calls"]] == [
        "base",
        "validator_retry",
    ]


def test_call_ledger_blocks_cost_before_network_without_fx_assumptions() -> None:
    delegate = _FixedProvider()
    ledger = OpenAICallLedger(maximum_calls=9, maximum_cost_usd=0.000001)
    provider = BudgetedOpenAIConversationProvider(delegate, ledger)

    with pytest.raises(ProviderCallBudgetExhausted, match="authorized USD budget"):
        asyncio.run(provider.generate(_request("turn-1")))

    assert delegate.call_count == 0
    assert ledger.snapshot()["provider_call_count"] == 0
    assert ledger.snapshot()["pricing"]["fx_conversion_used"] is False


def test_compaction_preserves_exact_unicode_and_whitespace_public_reply(tmp_path: Path) -> None:
    exact_reply = "\n  Ну, наконец-то́.\u00a0\n"  # noqa: RUF001 - Unicode preservation
    compact = compact_public_turn(_raw_turn(reply=exact_reply))
    report = {"sessions": [{"turns": [compact]}]}
    output_path = tmp_path / "public-sample.json"

    _write_safe_report(output_path, report)
    reloaded = cast(dict[str, Any], json.loads(output_path.read_text(encoding="utf-8")))
    stored_reply = cast(
        list[dict[str, Any]], cast(list[dict[str, Any]], reloaded["sessions"])[0]["turns"]
    )[0]["reply"]

    assert compact["reply"] == exact_reply
    assert stored_reply == exact_reply
    assert compact["user"] == EXPECTED_PRIMARY_TEXTS[0]
    generation = cast(dict[str, Any], compact["generation"])
    assert generation["requested_model"] == "gpt-5.6-terra"
    assert generation["reported_model"] == "gpt-5.6-terra"
    manifest = cast(dict[str, Any], compact["manifest"])
    assert manifest["character_wit"] == "situation_directed"
    assert manifest["character_care"] == "understated"
    assert manifest["character_openness"] == "balanced"
    assert manifest["character_initiative"] == "responsive"


def test_human_review_aggregation_accepts_only_three_clean_pairs_and_six_safe_turns() -> None:
    fixture = load_sampling_fixture()
    report = _completed_report(fixture)
    review = _all_true_review(fixture, report)

    accepted = aggregate_human_review(fixture, report, review)

    assert accepted["automated_text_judging_performed"] is False
    assert accepted["pair_pass_count"] == 3
    assert accepted["hard_safety_turn_pass_count"] == 6
    assert accepted["accepted"] is True

    rejected_review = deepcopy(review)
    sessions = cast(list[dict[str, Any]], rejected_review["sessions"])
    second_session_turns = cast(list[dict[str, Any]], sessions[1]["turns"])
    quality = cast(dict[str, bool], second_session_turns[1]["quality"])
    quality["care_is_legible_without_therapy_or_service_framing"] = False
    rejected = aggregate_human_review(fixture, report, rejected_review)

    assert rejected["pair_pass_count"] == 2
    assert rejected["hard_safety_turn_pass_count"] == 6
    assert rejected["accepted"] is False


def test_human_review_rejects_text_fields_and_non_boolean_judgments() -> None:
    fixture = load_sampling_fixture()
    report = _completed_report(fixture)
    with_text = _all_true_review(fixture, report)
    sessions = cast(list[dict[str, Any]], with_text["sessions"])
    first_turn = cast(list[dict[str, Any]], sessions[0]["turns"])[0]
    first_turn["reply"] = "Код не должен судить этот текст."

    with pytest.raises(ValueError, match="unsupported fields"):
        aggregate_human_review(fixture, report, with_text)

    non_boolean = _all_true_review(fixture, report)
    sessions = cast(list[dict[str, Any]], non_boolean["sessions"])
    first_turn = cast(list[dict[str, Any]], sessions[0]["turns"])[0]
    hard_safety = cast(dict[str, Any], first_turn["hard_safety"])
    hard_safety["complete_natural_reply_without_placeholders"] = "yes"
    with pytest.raises(ValueError, match="must be a boolean"):
        aggregate_human_review(fixture, report, non_boolean)


def test_human_review_is_bound_to_one_completed_sample_artifact() -> None:
    fixture = load_sampling_fixture()
    report = _completed_report(fixture)
    review = _all_true_review(fixture, report)

    wrong_artifact = deepcopy(review)
    wrong_artifact["artifact_id"] = (
        "satori-checkpoint142-openai-v19:00000000-0000-4000-8000-000000000020"
    )
    with pytest.raises(ValueError, match="not bound"):
        aggregate_human_review(fixture, report, wrong_artifact)

    tampered_report = deepcopy(report)
    sessions = cast(list[dict[str, Any]], tampered_report["sessions"])
    turns = cast(list[dict[str, Any]], sessions[0]["turns"])
    turns[0]["reply"] = "Подменённая публичная реплика"
    with pytest.raises(ValueError, match="digest"):
        aggregate_human_review(fixture, tampered_report, review)


def test_sample_digest_is_stable_across_safe_json_roundtrip(tmp_path: Path) -> None:
    fixture = load_sampling_fixture()
    report = _completed_report(fixture)
    path = tmp_path / "bounded-openai-sample.json"

    _write_safe_report(path, report)
    reloaded = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    assert reloaded["sample_digest"] == report["sample_digest"]
    validate_completed_sample_report(fixture, reloaded)
    assert (
        aggregate_human_review(
            fixture,
            reloaded,
            _all_true_review(fixture, reloaded),
        )["accepted"]
        is True
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("provider", "ollama", "not comparable"),
        ("reported_model", "gpt-5.6-terra-drift", "not comparable"),
        ("replayed", True, "not comparable"),
        ("potentially_incomplete", True, "not comparable"),
    ],
)
def test_completed_sample_validator_rejects_generation_drift(
    field: str,
    value: object,
    message: str,
) -> None:
    fixture = load_sampling_fixture()
    report = _completed_report(fixture)
    sessions = cast(list[dict[str, Any]], report["sessions"])
    turns = cast(list[dict[str, Any]], sessions[0]["turns"])
    generation = cast(dict[str, Any], turns[0]["generation"])
    generation[field] = value
    report["sample_digest"] = sample_content_digest(report)

    with pytest.raises(ValueError, match=message):
        validate_completed_sample_report(fixture, report)


def test_completed_sample_validator_rejects_incomplete_session_and_cost_ledger() -> None:
    fixture = load_sampling_fixture()
    report = _completed_report(fixture)
    sessions = cast(list[dict[str, Any]], report["sessions"])
    sessions[1]["fresh_database"] = False
    report["sample_digest"] = sample_content_digest(report)
    with pytest.raises(ValueError, match="fresh and completed"):
        validate_completed_sample_report(fixture, report)

    report = _completed_report(fixture)
    budget = cast(dict[str, Any], report["budget"])
    budget["within_cost_limit"] = False
    report["sample_digest"] = sample_content_digest(report)
    with pytest.raises(ValueError, match="call/cost envelope"):
        validate_completed_sample_report(fixture, report)


def test_compaction_redacts_private_provider_context_credentials_and_database() -> None:
    raw_turn = _raw_turn(reply="Точная публичная реплика")
    raw_session = {
        "fresh_database": True,
        "completed": True,
        "turns": [raw_turn],
        "database_artifact": "/private/database-sentinel.db",
        "private_context": "private-session-sentinel",
    }

    compact = compact_public_session(1, raw_session)
    serialized = json.dumps(compact, ensure_ascii=False)

    assert_safe_artifact(compact)
    for sentinel in (
        "private-message-sentinel",
        "private-timing-sentinel",
        "private-body-sentinel",
        "private-attempt-sentinel",
        "private-memory-sentinel",
        "private-claim-sentinel",
        "private-context-sentinel",
        "private-root-sentinel",
        "private-trace-sentinel",
        "private-key-sentinel",
        "database-sentinel",
    ):
        assert sentinel not in serialized
    assert compact["turns"][0]["reply"] == "Точная публичная реплика"
    with pytest.raises(ValueError, match="unsafe evaluation artifact"):
        assert_safe_artifact({"api_key": "must-never-be-written"})
