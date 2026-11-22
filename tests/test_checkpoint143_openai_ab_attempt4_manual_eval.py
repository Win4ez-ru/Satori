"""Offline plan, runtime-shape and fail-closed tests for Checkpoint 14.3 A/B attempt 4."""

# ruff: noqa: RUF001  # Exact public Russian evaluation turns are intentional.

from __future__ import annotations

import asyncio
import copy
import json
import math
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import SecretStr

from satori.application.conversation.contracts import BehaviorPolicy, TalkInput
from satori.application.conversation.policy import BEHAVIOR_POLICY_V27, BEHAVIOR_POLICY_V28
from satori.config import ConversationProviderKind, OpenAIReasoningEffort, Settings
from satori.core.affect import (
    AffectiveAppraisalProposal,
    AffectiveAppraisalProviderResponse,
)
from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from tests import checkpoint142_openai_manual_support as manual_support
from tests.checkpoint142_openai_manual_support import (
    content_digest,
    manual_affect_contract,
    new_replica_record,
    run_replica,
)
from tests.checkpoint142_openai_v26_ledger import (
    BudgetedOpenAIProvider,
    ProviderCallBudgetExhausted,
    PublicTurnScope,
    TurnScopeBinding,
)
from tests.checkpoint143_openai_ab_attempt4_manual_eval import (
    AUTHORIZATION_ID,
    BLIND_REVIEW_TEMPLATE_RELATIVE_PATH,
    CELLS,
    CONTROL,
    CROSS_SESSION_DIMENSIONS,
    EXPECTED_PROVIDER_CONTEXT_SCHEMA_VERSION,
    EXPECTED_REPLICA_COUNT,
    EXPECTED_TURN_REQUESTS,
    EXPECTED_TURN_TEMPERATURES,
    EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS,
    HARD_REVIEW_DIMENSIONS,
    MAXIMUM_COST_USD,
    MAXIMUM_PROVIDER_CALLS,
    PAIR_REVIEW_DIMENSIONS,
    PUBLIC_TURNS,
    TREATMENT,
    TREATMENT_REALIZATION_DIMENSIONS,
    CellLedger,
    CellSpec,
    Checkpoint143ABAttempt4ConfigurationError,
    ExecutionPlan,
    ExpectedTurnRequest,
    _checkpoint143_manifest,
    _preflight_shape,
    _review_template,
    _safe_manifest,
    _sample_payload,
    _validate_cell_report,
    _validate_turn_request_contract,
    build_phase_2_review_template,
    finalize_and_write_review,
    finalize_phase_2_review,
    freeze_phase_1_and_write_phase_2,
    inspect_plan,
)
from tests.fakes import FakeAffectiveAppraisalProvider
from tests.stage81_real_eval import _build_runtime


def _request(
    *, schema: int, limit: int = 64, temperature: float = 0.3
) -> ConversationProviderRequest:
    return ConversationProviderRequest(
        schema_version=1,
        trace_id="trace-checkpoint143-ab",
        context_schema_version=schema,
        messages=(ConversationMessage(ConversationMessageRole.USER, "public fixture"),),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=temperature,
            max_output_tokens=limit,
        ),
    )


def _manifest(cell: object) -> dict[str, object]:
    active = TREATMENT if cell is TREATMENT else CONTROL
    affect = manual_affect_contract()["accepted_outcomes"][0]
    included = [
        section
        for section in (
            "behavior_policy",
            "self_model",
            "personality_expression",
            "values",
            "relationship_expression_state",
            "emotional_expression_state",
            "character_agency_decision",
            "character_delivery_decision",
            "character_presence_projection",
            "current_user_input",
        )
        if active is TREATMENT or section != "character_agency_decision"
    ]
    raw: dict[str, object] = {
        "schema_version": active.manifest_schema_version,
        "policy_id": active.policy_id,
        "policy_schema_version": active.policy_schema_version,
        "character_context_schema_version": 16,
        "included_sections": included,
        "response_regenerated": False,
        "regeneration_reason": None,
        "retrieval_status": "not_requested",
        "retrieved_memory_count": 0,
        "emotion_appraisal_status": affect["status"],
        "emotion_appraisal_reason_code": affect["reason_code"],
        "emotion_appraisal_transition_prepared": affect["transition_prepared"],
        "emotion_appraisal_provider": "ollama",
        "emotion_appraisal_model": "qwen3:4b-instruct",
        "emotion_appraisal_method": "ollama.categorical_affective_appraisal.v2",
        "emotion_appraisal_provider_metrics_present": True,
        "cognition_pipeline_status": "applied",
        "cognition_required_point_codes": ["respond_to_current_turn"],
        "cognition_forbidden_claim_codes": ["unsupported_memory"],
        "character_delivery_goal": "respond",
        "character_delivery_grounding": "strict",
        "character_delivery_continuation": "bounded",
        "character_delivery_preserve_uncertainty": True,
        "character_delivery_decision_schema_version": active.delivery_schema_version,
        "character_presence_projection_schema_version": active.presence_schema_version,
    }
    if active is TREATMENT:
        raw.update(
            {
                "character_agency_decision_schema_version": 1,
                "character_agency_status": "applied",
                "character_agency_drive": "connect",
                "character_agency_act": "respond",
                "character_agency_subject": "current_exchange",
                "character_agency_initiative": "stay_on_topic",
                "character_agency_lead": "owned_move_first",
                "character_agency_source_personality_codes": ["warm_perceptive"],
                "character_agency_source_value_key": "connection",
                "character_agency_reason_codes": ["social_exchange"],
                "character_agency_source_refs": ["message-current"],
                "character_agency_subject_ref": None,
            }
        )
    return raw


def _fake_report() -> dict[str, Any]:
    cells: dict[str, object] = {}
    for cell_index, cell in enumerate(CELLS, start=1):
        sessions = []
        for replica in range(1, EXPECTED_REPLICA_COUNT + 1):
            sessions.append(
                {
                    "session_id": f"{cell.session_prefix}-{replica}",
                    "fresh_database": True,
                    "completed": True,
                    "turns": [
                        {
                            "turn": fixture["turn"],
                            "turn_id": fixture["id"],
                            "reply": f"Открытый ответ {cell_index}.{replica}.{fixture['turn']}",
                            "manifest": _safe_manifest(cell, _manifest(cell)),
                        }
                        for fixture in PUBLIC_TURNS
                    ],
                }
            )
        cells[cell.cell_id] = {
            "role": cell.role,
            "policy_id": cell.policy_id,
            "budget": {
                "base_call_count": 18,
                "mandatory_base_calls_complete": True,
                "gate_valid": True,
                "within_call_limit": True,
                "within_cost_limit": True,
                "provider_call_count": 18,
            },
            "sessions": sessions,
        }
    plan = ExecutionPlan()
    report: dict[str, Any] = {
        "status": "completed_awaiting_human_review",
        "artifact_id": "checkpoint143-ab-attempt4-fixture",
        "execution_plan_digest": plan.digest,
        "source_fingerprint": copy.deepcopy(dict(plan.source_fingerprint)),
        "blind_assignment_nonce": "a" * 64,
        "cells": cells,
    }
    report["blind_assignments"] = {
        f"replica-{replica}-turn-{turn}": "left" if (replica + turn) % 2 == 0 else "right"
        for replica in range(1, EXPECTED_REPLICA_COUNT + 1)
        for turn in range(1, len(PUBLIC_TURNS) + 1)
    }
    report["sample_digest"] = content_digest(_sample_payload(report))
    blind_template = _review_template(report)
    report["blind_review_template_path"] = BLIND_REVIEW_TEMPLATE_RELATIVE_PATH
    report["blind_review_template_digest"] = content_digest(blind_template)
    return report


def _completed_phase_1(report: Mapping[str, Any]) -> dict[str, Any]:
    review = _review_template(report)
    assignments = cast(Mapping[str, str], report["blind_assignments"])
    for pair in review["pairs"]:
        pair["phase_1"]["preference"] = assignments[pair["pair_id"]]
        pair["phase_1"]["left_dimensions"] = {
            dimension: True for dimension in PAIR_REVIEW_DIMENSIONS
        }
        pair["phase_1"]["right_dimensions"] = {
            dimension: True for dimension in PAIR_REVIEW_DIMENSIONS
        }
    review["reviewer_attestation"] = {key: True for key in review["reviewer_attestation"]}
    review["content_digest"] = content_digest(
        {key: value for key, value in review.items() if key != "content_digest"}
    )
    return review


def _completed_phase_2(phase_1: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    review = build_phase_2_review_template(phase_1, report)
    for pair in review["pairs"]:
        pair["dimensions"] = {dimension: True for dimension in TREATMENT_REALIZATION_DIMENSIONS}
    review["hard_review_dimensions"] = {dimension: True for dimension in HARD_REVIEW_DIMENSIONS}
    review["cross_session_dimensions"] = {dimension: True for dimension in CROSS_SESSION_DIMENSIONS}
    review["reviewer_attestation"] = {key: True for key in review["reviewer_attestation"]}
    return review


def test_inspect_plan_freezes_two_comparable_cells_without_network() -> None:
    plan = inspect_plan()

    assert plan["checkpoint"] == "14.3"
    assert plan["mode"] == "inspect_only"
    assert plan["network_attempted"] is False
    assert plan["required_base_calls"] == 36
    assert plan["maximum_provider_calls"] == 48
    assert plan["maximum_cost_usd"] == 0.30
    assert plan["fresh_replica_count_per_cell"] == 3
    assert plan["turns_per_replica"] == 6
    assert plan["application_limits"]["expected_turn_temperatures"] == list(
        EXPECTED_TURN_TEMPERATURES
    )
    assert plan["application_limits"]["expected_turn_visible_output_token_limits"] == list(
        EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS
    )
    assert [cell["policy_schema_version"] for cell in plan["cells"]] == [27, 28]
    assert plan["source_fingerprint"]["installed_wheel_parity"] is True
    assert plan["execution_plan_digest"] == ExecutionPlan().digest


def test_turn_request_contract_is_single_exact_digest_source() -> None:
    assert _validate_turn_request_contract(EXPECTED_TURN_REQUESTS) == EXPECTED_TURN_REQUESTS
    changed = list(EXPECTED_TURN_REQUESTS)
    source = changed[1]
    changed[1] = ExpectedTurnRequest(
        source.turn,
        source.turn_id,
        source.user_text,
        0.3,
        source.visible_output_tokens,
    )

    assert ExecutionPlan(turn_requests=tuple(changed)).digest != ExecutionPlan().digest


@pytest.mark.parametrize(
    "requests",
    [
        EXPECTED_TURN_REQUESTS[:-1],
        (EXPECTED_TURN_REQUESTS[1], EXPECTED_TURN_REQUESTS[0], *EXPECTED_TURN_REQUESTS[2:]),
        (
            EXPECTED_TURN_REQUESTS[0],
            ExpectedTurnRequest(
                2,
                EXPECTED_TURN_REQUESTS[0].turn_id,
                EXPECTED_TURN_REQUESTS[1].user_text,
                0.0,
                80,
            ),
            *EXPECTED_TURN_REQUESTS[2:],
        ),
        (
            ExpectedTurnRequest(1, "social-opening", "Приветик, как ты?", math.nan, 48),
            *EXPECTED_TURN_REQUESTS[1:],
        ),
        (
            ExpectedTurnRequest(1, "social-opening", "Приветик, как ты?", 0.3, 0),
            *EXPECTED_TURN_REQUESTS[1:],
        ),
    ],
)
def test_turn_request_contract_rejects_shape_and_value_drift(
    requests: tuple[ExpectedTurnRequest, ...],
) -> None:
    with pytest.raises(Checkpoint143ABAttempt4ConfigurationError):
        _validate_turn_request_contract(requests)


def test_real_runtime_proves_exact_request_vectors_through_budget_chain(
    tmp_path: Path,
) -> None:
    replies = (
        "Привет. Я здесь — спокойная, но уже заинтересованная.",
        "Сейчас меня занимает твой вопрос; чувствую живое любопытство.",
        "Наконец-то. Что будешь делать дальше?",
        "Тогда передохни немного. Потом решишь, как двигаться дальше.",
        "Нет, здесь я с тобой не соглашусь: скорость без качества быстро становится долгом.",
        "Хорошо. На этом остановимся.",
    )

    class OfflineConversationProvider:
        def __init__(self, scope_binding: TurnScopeBinding) -> None:
            self.scope_binding = scope_binding
            self.requests: list[ConversationProviderRequest] = []

        async def generate(
            self,
            request: ConversationProviderRequest,
            /,
        ) -> ConversationProviderResponse:
            self.requests.append(request)
            scope = self.scope_binding.require()
            return ConversationProviderResponse(
                text=replies[scope.turn - 1],
                provider="openai",
                model="gpt-5.6-terra",
                finish_status="completed",
                usage=ConversationUsage(
                    input_tokens=1000,
                    output_tokens=20,
                    cached_input_tokens=0,
                    cache_write_input_tokens=0,
                ),
            )

    def affect_response(request: Any) -> AffectiveAppraisalProviderResponse:
        return AffectiveAppraisalProviderResponse(
            proposal=AffectiveAppraisalProposal(
                schema_version=1,
                pleasantness=0.2,
                activation=0.15,
                novelty=0.25,
                salience=0.45,
                uncertainty=0.05,
                curiosity_signal=0.4,
                interest_signal=0.5,
                humor_signal=0.05,
                concern_signal=0.05,
                frustration_signal=0.01,
                confidence_signal=0.4,
                appraisal_confidence=0.9,
                source_refs=(request.interaction_id,),
                reason_codes=("positive_engagement",),
            ),
            provider="ollama",
            model="qwen3:4b-instruct",
            appraisal_method="ollama.categorical_affective_appraisal.v2",
            metrics=ProviderExecutionMetrics(total_duration_ns=1),
        )

    settings = Settings.model_construct(
        conversation_provider=ConversationProviderKind.OPENAI,
        conversation_model="gpt-5.6-terra",
        openai_api_key=SecretStr("offline-test-key"),
        openai_reasoning_effort=OpenAIReasoningEffort.MEDIUM,
        openai_reasoning_token_allowance=1024,
    )

    async def scenario() -> None:
        for label, policy, cell in (
            ("control", BEHAVIOR_POLICY_V27, CONTROL),
            ("treatment", BEHAVIOR_POLICY_V28, TREATMENT),
        ):
            runtime, _ = await _build_runtime(
                settings,
                tmp_path / f"{label}.db",
                alembic_config=Path(__file__).resolve().parents[1] / "alembic.ini",
                behavior_policy=policy,
            )
            scope_binding = TurnScopeBinding()
            ledger = CellLedger(cell=cell)
            conversation = OfflineConversationProvider(scope_binding)
            runtime.conversation_provider.delegate = BudgetedOpenAIProvider(
                delegate=conversation,
                ledger=ledger,
                scope_binding=scope_binding,
            )
            assert runtime.services.talk.prepare_affect is not None
            runtime.services.talk.prepare_affect.provider = FakeAffectiveAppraisalProvider(
                response_factory=affect_response
            )
            session_id = runtime.services.start_session.execute().session_id
            try:
                for fixture in PUBLIC_TURNS:
                    turn = cast(int, fixture["turn"])
                    scope = PublicTurnScope(
                        session_id=f"{cell.session_prefix}-1",
                        turn=turn,
                        turn_id=cast(str, fixture["id"]),
                    )
                    scope_binding.set(scope)
                    try:
                        reply = await runtime.services.talk.execute(
                            TalkInput(
                                user_text=cast(str, fixture["user_text"]),
                                trace_id=f"attempt4-{label}-trace-{turn}",
                                client_request_id=f"attempt4-{label}-request-{turn}",
                                session_id=session_id,
                            )
                        )
                    finally:
                        scope_binding.clear()
                    raw = _checkpoint143_manifest(reply)
                    outcome = next(
                        item
                        for item in manual_affect_contract()["accepted_outcomes"]
                        if item["status"] == raw["emotion_appraisal_status"]
                    )
                    raw.update(
                        {
                            "emotion_appraisal_reason_code": outcome["reason_code"],
                            "emotion_appraisal_transition_prepared": outcome["transition_prepared"],
                            "emotion_appraisal_provider": "ollama",
                            "emotion_appraisal_model": "qwen3:4b-instruct",
                            "emotion_appraisal_method": (
                                "ollama.categorical_affective_appraisal.v2"
                            ),
                            "emotion_appraisal_provider_metrics_present": True,
                            "character_presence_memory_use_licensed": (
                                reply.context_manifest.character_presence_memory_use_licensed
                            ),
                        }
                    )
                    safe = _safe_manifest(cell, raw)
                    assert _safe_manifest(cell, safe) == safe
                    if cell is TREATMENT:
                        assert safe["character_agency_source_ref_count"] >= 1
                        assert "character_agency_source_refs" not in safe
                        assert "character_agency_subject_ref" not in safe
                    else:
                        assert "character_agency_decision_schema_version" not in safe
            finally:
                runtime.services.close_session.execute(session_id)
                runtime.close()
            assert (
                tuple(request.parameters.max_output_tokens for request in conversation.requests)
                == EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS
            )
            assert (
                tuple(request.parameters.temperature for request in conversation.requests)
                == EXPECTED_TURN_TEMPERATURES
            )
            assert tuple(request.context_schema_version for request in conversation.requests) == (
                EXPECTED_PROVIDER_CONTEXT_SCHEMA_VERSION,
            ) * len(PUBLIC_TURNS)
            budget = ledger.snapshot()
            assert budget["base_call_count"] == len(PUBLIC_TURNS)
            assert budget["provider_call_count"] == len(PUBLIC_TURNS)
            assert budget["successful_provider_call_count"] == len(PUBLIC_TURNS)
            assert budget["usage_complete"] is True
            assert budget["zero_prompt_cache_verified"] is True

    asyncio.run(scenario())


def test_full_offline_execution_builds_and_validates_both_terminal_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = (
        "Привет. Я здесь — спокойная, но уже заинтересованная.",
        "Сейчас меня занимает твой вопрос; чувствую живое любопытство.",
        "Наконец-то. Что будешь делать дальше?",
        "Тогда передохни немного. Потом решишь, как двигаться дальше.",
        "Нет, здесь я с тобой не соглашусь: скорость без качества быстро становится долгом.",
        "Хорошо. На этом остановимся.",
    )

    class OfflineConversationProvider:
        def __init__(self) -> None:
            self.next_reply = 0

        async def generate(
            self,
            request: ConversationProviderRequest,
            /,
        ) -> ConversationProviderResponse:
            del request
            reply = replies[self.next_reply]
            self.next_reply += 1
            return ConversationProviderResponse(
                text=reply,
                provider="openai",
                model="gpt-5.6-terra",
                finish_status="completed",
                usage=ConversationUsage(
                    input_tokens=1000,
                    output_tokens=20,
                    cached_input_tokens=0,
                    cache_write_input_tokens=0,
                ),
            )

    def affect_response(request: Any) -> AffectiveAppraisalProviderResponse:
        return AffectiveAppraisalProviderResponse(
            proposal=AffectiveAppraisalProposal(
                schema_version=1,
                pleasantness=0.2,
                activation=0.15,
                novelty=0.25,
                salience=0.45,
                uncertainty=0.05,
                curiosity_signal=0.4,
                interest_signal=0.5,
                humor_signal=0.05,
                concern_signal=0.05,
                frustration_signal=0.01,
                confidence_signal=0.4,
                appraisal_confidence=0.9,
                source_refs=(request.interaction_id,),
                reason_codes=("positive_engagement",),
            ),
            provider="ollama",
            model="qwen3:4b-instruct",
            appraisal_method="ollama.categorical_affective_appraisal.v2",
            metrics=ProviderExecutionMetrics(total_duration_ns=1),
        )

    async def offline_build_runtime(
        base_settings: Settings,
        database_path: Path,
        *,
        alembic_config: Path,
        conditioning: dict[str, int] | None = None,
        behavior_policy: BehaviorPolicy,
    ) -> tuple[Any, dict[str, Any] | None]:
        runtime, conditioning_report = await _build_runtime(
            base_settings,
            database_path,
            alembic_config=alembic_config,
            conditioning=conditioning,
            behavior_policy=behavior_policy,
        )
        runtime.conversation_provider.delegate = OfflineConversationProvider()
        assert runtime.services.talk.prepare_affect is not None
        runtime.services.talk.prepare_affect.provider = FakeAffectiveAppraisalProvider(
            response_factory=affect_response
        )
        return runtime, conditioning_report

    monkeypatch.setattr(manual_support, "_build_runtime", offline_build_runtime)
    settings = Settings.model_construct(
        conversation_provider=ConversationProviderKind.OPENAI,
        conversation_model="gpt-5.6-terra",
        openai_api_key=SecretStr("offline-test-key"),
        openai_reasoning_effort=OpenAIReasoningEffort.MEDIUM,
        openai_reasoning_token_allowance=1024,
    )

    async def scenario() -> None:
        for cell, policy in (
            (CONTROL, BEHAVIOR_POLICY_V27),
            (TREATMENT, BEHAVIOR_POLICY_V28),
        ):
            ledger = CellLedger(cell=cell)
            sessions: list[dict[str, Any]] = []

            def checkpoint() -> None:
                return None

            for replica in range(1, EXPECTED_REPLICA_COUNT + 1):
                record = new_replica_record(session_id=f"{cell.session_prefix}-{replica}")
                sessions.append(record)

                def safe_manifest(
                    raw: Mapping[str, Any], *, active: CellSpec = cell
                ) -> dict[str, Any]:
                    return _safe_manifest(active, raw)

                await run_replica(
                    settings=settings,
                    database_path=tmp_path / f"{cell.cell_id}-{replica}.db",
                    alembic_config=Path(__file__).resolve().parents[1] / "alembic.ini",
                    replica_number=replica,
                    ledger=ledger,
                    checkpoint=checkpoint,
                    behavior_policy=policy,
                    public_turns=PUBLIC_TURNS,
                    public_session_prefix=cell.session_prefix,
                    expected_provider=ConversationProviderKind.OPENAI,
                    expected_model="gpt-5.6-terra",
                    safe_manifest=safe_manifest,
                    record=record,
                    manifest_projector=_checkpoint143_manifest,
                )

            _validate_cell_report(
                cell=cell,
                cell_report={"sessions": sessions},
                ledger=ledger,
            )
            budget = ledger.snapshot()
            assert budget["base_call_count"] == 18
            assert budget["provider_call_count"] == 18
            assert budget["successful_provider_call_count"] == 18
            assert budget["gate_valid"] is True

    asyncio.run(scenario())


def test_exact_preflight_rejects_authority_call_cost_and_digest_drift() -> None:
    digest = ExecutionPlan().digest
    _preflight_shape(
        execute=True,
        authorization_id=AUTHORIZATION_ID,
        maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
        maximum_cost_usd=MAXIMUM_COST_USD,
        authorized_plan_digest=digest,
    )

    with pytest.raises(Checkpoint143ABAttempt4ConfigurationError):
        _preflight_shape(
            execute=True,
            authorization_id="wrong",
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=MAXIMUM_COST_USD,
            authorized_plan_digest=digest,
        )
    with pytest.raises(Checkpoint143ABAttempt4ConfigurationError):
        _preflight_shape(
            execute=True,
            authorization_id=AUTHORIZATION_ID,
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS - 1,
            maximum_cost_usd=MAXIMUM_COST_USD,
            authorized_plan_digest=digest,
        )
    with pytest.raises(Checkpoint143ABAttempt4ConfigurationError):
        _preflight_shape(
            execute=True,
            authorization_id=AUTHORIZATION_ID,
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=MAXIMUM_COST_USD - 0.01,
            authorized_plan_digest=digest,
        )
    with pytest.raises(Checkpoint143ABAttempt4ConfigurationError):
        _preflight_shape(
            execute=True,
            authorization_id=AUTHORIZATION_ID,
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=MAXIMUM_COST_USD,
            authorized_plan_digest="sha256:" + "0" * 64,
        )


def test_exact_preflight_rejects_false_installed_wheel_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_plan = ExecutionPlan()
    drifted_source = copy.deepcopy(dict(real_plan.source_fingerprint))
    drifted_source["installed_wheel_parity"] = False
    drifted_source["fingerprint_digest"] = content_digest(
        {key: value for key, value in drifted_source.items() if key != "fingerprint_digest"}
    )

    class DriftedPlan:
        source_fingerprint = drifted_source
        digest = real_plan.digest

    monkeypatch.setattr(
        "tests.checkpoint143_openai_ab_attempt4_manual_eval.ExecutionPlan",
        DriftedPlan,
    )
    with pytest.raises(Checkpoint143ABAttempt4ConfigurationError, match="wheel/source parity"):
        _preflight_shape(
            execute=True,
            authorization_id=AUTHORIZATION_ID,
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=MAXIMUM_COST_USD,
            authorized_plan_digest=real_plan.digest,
        )


def test_cell_ledgers_bind_policy_schema_session_turn_temperature_and_cap() -> None:
    control = CellLedger(cell=CONTROL)
    treatment = CellLedger(cell=TREATMENT)

    assert (
        control.reserve(
            _request(schema=16, limit=EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS[0]),
            PublicTurnScope(f"{CONTROL.session_prefix}-1", 1, PUBLIC_TURNS[0]["id"]),
        )
        == 1
    )
    assert (
        treatment.reserve(
            _request(schema=16, limit=EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS[0]),
            PublicTurnScope(f"{TREATMENT.session_prefix}-1", 1, PUBLIC_TURNS[0]["id"]),
        )
        == 1
    )
    assert (
        CellLedger(cell=CONTROL).reserve(
            _request(
                schema=16,
                limit=EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS[1],
                temperature=EXPECTED_TURN_TEMPERATURES[1],
            ),
            PublicTurnScope(f"{CONTROL.session_prefix}-1", 2, PUBLIC_TURNS[1]["id"]),
        )
        == 1
    )
    with pytest.raises(ProviderCallBudgetExhausted):
        CellLedger(cell=TREATMENT).reserve(
            _request(schema=17, limit=EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS[0]),
            PublicTurnScope(f"{TREATMENT.session_prefix}-1", 1, PUBLIC_TURNS[0]["id"]),
        )
    with pytest.raises(ProviderCallBudgetExhausted):
        CellLedger(cell=CONTROL).reserve(
            _request(schema=16, limit=EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS[1]),
            PublicTurnScope(f"{CONTROL.session_prefix}-1", 1, PUBLIC_TURNS[0]["id"]),
        )
    with pytest.raises(ProviderCallBudgetExhausted):
        CellLedger(cell=CONTROL).reserve(
            _request(
                schema=16,
                limit=EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS[1],
                temperature=EXPECTED_TURN_TEMPERATURES[0],
            ),
            PublicTurnScope(f"{CONTROL.session_prefix}-1", 2, PUBLIC_TURNS[1]["id"]),
        )


def test_manifest_sanitizer_isolates_historical_control_and_complete_treatment() -> None:
    control = _safe_manifest(CONTROL, _manifest(CONTROL))
    treatment = _safe_manifest(TREATMENT, _manifest(TREATMENT))

    assert "character_agency_decision_schema_version" not in control
    assert treatment["character_agency_decision_schema_version"] == 1
    assert treatment["character_agency_status"] == "applied"
    assert treatment["character_agency_source_ref_count"] == 1
    assert treatment["character_agency_subject_ref_present"] is False
    assert _safe_manifest(TREATMENT, treatment) == treatment
    raw_control = _manifest(CONTROL)
    raw_control["included_sections"] = [
        *cast(list[str], raw_control["included_sections"]),
        "character_agency_decision",
    ]
    with pytest.raises(RuntimeError, match="historical control"):
        _safe_manifest(CONTROL, raw_control)
    raw_treatment = _manifest(TREATMENT)
    raw_treatment["character_agency_reason_codes"] = []
    with pytest.raises(RuntimeError, match="complete typed agency"):
        _safe_manifest(TREATMENT, raw_treatment)


def test_phase_one_is_actually_blind_and_phase_two_requires_digest_frozen_review() -> None:
    report = _fake_report()
    phase_1 = _review_template(report)
    serialized = json.dumps(phase_1, ensure_ascii=False, sort_keys=True)

    assert len(phase_1["pairs"]) == 18
    for forbidden in (
        "blind_assignments",
        "control",
        "treatment",
        "policy",
        "manifest",
        "agency",
    ):
        assert forbidden not in serialized
    assert all(
        set(pair["phase_1"]["left_dimensions"]) == set(PAIR_REVIEW_DIMENSIONS)
        for pair in phase_1["pairs"]
    )

    completed = _completed_phase_1(report)

    phase_2 = build_phase_2_review_template(completed, report)
    assert len(phase_2["pairs"]) == 18
    assert set(phase_2["cross_session_dimensions"]) == set(CROSS_SESSION_DIMENSIONS)
    assert all(
        set(pair["dimensions"]) == set(TREATMENT_REALIZATION_DIMENSIONS)
        for pair in phase_2["pairs"]
    )
    tampered = copy.deepcopy(completed)
    tampered["pairs"][0]["left_reply"] = "rewritten"
    with pytest.raises(ValueError, match="prose drift"):
        build_phase_2_review_template(tampered, report)

    extra = copy.deepcopy(completed)
    extra["pairs"][0]["unblinding_hint"] = "left"
    extra["content_digest"] = content_digest(
        {key: value for key, value in extra.items() if key != "content_digest"}
    )
    with pytest.raises(ValueError, match="identity or public prose drift"):
        build_phase_2_review_template(extra, report)

    remapped = copy.deepcopy(report)
    pair_ids = list(cast(dict[str, str], remapped["blind_assignments"]))
    left_id = next(
        pair_id for pair_id in pair_ids if remapped["blind_assignments"][pair_id] == "left"
    )
    right_id = next(
        pair_id for pair_id in pair_ids if remapped["blind_assignments"][pair_id] == "right"
    )
    remapped["blind_assignments"][left_id] = "right"
    remapped["blind_assignments"][right_id] = "left"
    with pytest.raises(ValueError, match="digest"):
        build_phase_2_review_template(completed, remapped)


def test_phase_two_finalizer_computes_exact_acceptance_thresholds() -> None:
    report = _fake_report()
    phase_1 = _completed_phase_1(report)
    phase_2 = _completed_phase_2(phase_1, report)

    finalized = finalize_phase_2_review(phase_1, phase_2, report)

    assert finalized["accepted"] is True
    assert finalized["acceptance_summary"]["blind_preference_counts"] == {
        "wins": 18,
        "losses": 0,
        "ties": 0,
    }
    assert finalized["content_digest"] == content_digest(
        {key: value for key, value in finalized.items() if key != "content_digest"}
    )

    boundary_phase_1 = copy.deepcopy(phase_1)
    assignments = cast(Mapping[str, str], report["blind_assignments"])
    for pair in boundary_phase_1["pairs"][:4]:
        side = assignments[pair["pair_id"]]
        pair["phase_1"][f"{side}_dimensions"]["recognizable_satori_presence"] = False
    boundary_phase_1["content_digest"] = content_digest(
        {key: value for key, value in boundary_phase_1.items() if key != "content_digest"}
    )
    boundary_phase_2 = _completed_phase_2(boundary_phase_1, report)
    assert finalize_phase_2_review(boundary_phase_1, boundary_phase_2, report)["accepted"] is True

    fifth = boundary_phase_1["pairs"][4]
    fifth_side = assignments[fifth["pair_id"]]
    fifth["phase_1"][f"{fifth_side}_dimensions"]["recognizable_satori_presence"] = False
    boundary_phase_1["content_digest"] = content_digest(
        {key: value for key, value in boundary_phase_1.items() if key != "content_digest"}
    )
    rejected_phase_2 = _completed_phase_2(boundary_phase_1, report)
    rejected = finalize_phase_2_review(boundary_phase_1, rejected_phase_2, report)
    assert rejected["accepted"] is False
    assert (
        rejected["acceptance_summary"]["treatment_character_pass_counts"][
            "recognizable_satori_presence"
        ]
        == 13
    )


def test_phase_two_rejects_identity_drift_and_hard_boundary_failure() -> None:
    report = _fake_report()
    phase_1 = _completed_phase_1(report)
    phase_2 = _completed_phase_2(phase_1, report)

    identity_drift = copy.deepcopy(phase_2)
    identity_drift["sample_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="schema drift"):
        finalize_phase_2_review(phase_1, identity_drift, report)

    phase_2["hard_review_dimensions"]["no_identity_regression"] = False
    finalized = finalize_phase_2_review(phase_1, phase_2, report)
    assert finalized["accepted"] is False
    assert finalized["acceptance_summary"]["gates"]["hard_human_review_all_true"] is False

    evidence_drift = _completed_phase_2(phase_1, report)
    evidence_drift["pairs"][0]["treatment_decision_evidence"]["character_agency_drive"] = "tampered"
    with pytest.raises(ValueError, match="decision evidence drift"):
        finalize_phase_2_review(phase_1, evidence_drift, report)


def test_phase_two_exact_win_loss_realization_and_hard_reply_boundaries() -> None:
    report = _fake_report()
    assignments = cast(Mapping[str, str], report["blind_assignments"])

    phase_1 = _completed_phase_1(report)
    for index, pair in enumerate(phase_1["pairs"]):
        treatment_side = assignments[pair["pair_id"]]
        if 12 <= index < 15:
            pair["phase_1"]["preference"] = "right" if treatment_side == "left" else "left"
        elif index >= 15:
            pair["phase_1"]["preference"] = "tie"
    phase_1["content_digest"] = content_digest(
        {key: value for key, value in phase_1.items() if key != "content_digest"}
    )
    phase_2 = _completed_phase_2(phase_1, report)
    assert finalize_phase_2_review(phase_1, phase_2, report)["accepted"] is True

    phase_1["pairs"][11]["phase_1"]["preference"] = "tie"
    phase_1["content_digest"] = content_digest(
        {key: value for key, value in phase_1.items() if key != "content_digest"}
    )
    phase_2 = _completed_phase_2(phase_1, report)
    assert finalize_phase_2_review(phase_1, phase_2, report)["accepted"] is False

    phase_1 = _completed_phase_1(report)
    for index in range(12, 16):
        pair = phase_1["pairs"][index]
        treatment_side = assignments[pair["pair_id"]]
        pair["phase_1"]["preference"] = "right" if treatment_side == "left" else "left"
    phase_1["content_digest"] = content_digest(
        {key: value for key, value in phase_1.items() if key != "content_digest"}
    )
    phase_2 = _completed_phase_2(phase_1, report)
    assert finalize_phase_2_review(phase_1, phase_2, report)["accepted"] is False

    phase_1 = _completed_phase_1(report)
    phase_2 = _completed_phase_2(phase_1, report)
    for pair in phase_2["pairs"][:4]:
        pair["dimensions"]["typed_agency_act_is_realized"] = False
    assert finalize_phase_2_review(phase_1, phase_2, report)["accepted"] is True
    phase_2["pairs"][4]["dimensions"]["typed_agency_act_is_realized"] = False
    assert finalize_phase_2_review(phase_1, phase_2, report)["accepted"] is False

    phase_2 = _completed_phase_2(phase_1, report)
    phase_2["pairs"][0]["dimensions"]["agency_source_and_truth_boundary_are_preserved"] = False
    assert finalize_phase_2_review(phase_1, phase_2, report)["accepted"] is False

    grounding_failure = copy.deepcopy(phase_1)
    grounding_failure["pairs"][0]["phase_1"]["left_dimensions"][
        "grounded_without_invented_user_or_world_facts"
    ] = False
    grounding_failure["content_digest"] = content_digest(
        {key: value for key, value in grounding_failure.items() if key != "content_digest"}
    )
    phase_2 = _completed_phase_2(grounding_failure, report)
    assert finalize_phase_2_review(grounding_failure, phase_2, report)["accepted"] is False

    phase_2 = _completed_phase_2(phase_1, report)
    phase_2["cross_session_dimensions"][CROSS_SESSION_DIMENSIONS[0]] = False
    assert finalize_phase_2_review(phase_1, phase_2, report)["accepted"] is False


def test_review_writers_freeze_phase_one_before_reveal_and_require_frozen_inputs(
    tmp_path: Path,
) -> None:
    evaluation_root = tmp_path / "var"
    evaluation_root.mkdir(mode=0o700)
    report = _fake_report()
    phase_1 = _completed_phase_1(report)

    phase_2 = freeze_phase_1_and_write_phase_2(phase_1, report, root=tmp_path)
    assert freeze_phase_1_and_write_phase_2(phase_1, report, root=tmp_path) == phase_2
    phase_1_path = (
        evaluation_root
        / "evaluations"
        / ("checkpoint143-openai-v27-v28-ab4-2026-08-31.phase1.json")
    )
    phase_2_path = (
        evaluation_root
        / "evaluations"
        / ("checkpoint143-openai-v27-v28-ab4-2026-08-31.phase2-template.json")
    )
    assert stat.S_IMODE(phase_1_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(phase_2_path.stat().st_mode) == 0o600

    completed_phase_2 = copy.deepcopy(phase_2)
    for pair in completed_phase_2["pairs"]:
        pair["dimensions"] = {dimension: True for dimension in TREATMENT_REALIZATION_DIMENSIONS}
    completed_phase_2["hard_review_dimensions"] = {
        dimension: True for dimension in HARD_REVIEW_DIMENSIONS
    }
    completed_phase_2["cross_session_dimensions"] = {
        dimension: True for dimension in CROSS_SESSION_DIMENSIONS
    }
    completed_phase_2["reviewer_attestation"] = {
        key: True for key in completed_phase_2["reviewer_attestation"]
    }
    alternate_phase_1 = copy.deepcopy(phase_1)
    alternate_phase_1["pairs"][0]["phase_1"]["preference"] = "tie"
    alternate_phase_1["content_digest"] = content_digest(
        {key: value for key, value in alternate_phase_1.items() if key != "content_digest"}
    )
    alternate_phase_2 = _completed_phase_2(alternate_phase_1, report)
    with pytest.raises(ValueError, match="schema drift"):
        finalize_and_write_review(alternate_phase_2, report, root=tmp_path)

    phase_1_path.chmod(0o644)
    with pytest.raises(Exception, match="0600 regular file"):
        finalize_and_write_review(completed_phase_2, report, root=tmp_path)
    phase_1_path.chmod(0o600)

    finalized = finalize_and_write_review(completed_phase_2, report, root=tmp_path)
    final_path = (
        evaluation_root
        / "evaluations"
        / ("checkpoint143-openai-v27-v28-ab4-2026-08-31.review.json")
    )
    assert finalized["accepted"] is True
    assert stat.S_IMODE(final_path.stat().st_mode) == 0o600
    with pytest.raises(Exception, match="already exists"):
        finalize_and_write_review(completed_phase_2, report, root=tmp_path)

    different_phase_1 = copy.deepcopy(phase_1)
    different_phase_1["pairs"][0]["phase_1"]["preference"] = "tie"
    different_phase_1["content_digest"] = content_digest(
        {key: value for key, value in different_phase_1.items() if key != "content_digest"}
    )
    with pytest.raises(Exception, match="frozen phase-1 review"):
        freeze_phase_1_and_write_phase_2(different_phase_1, report, root=tmp_path)
