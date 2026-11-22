"""Offline proof of the complete diagnostic and its spending/data boundaries."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from satori.config import Settings
from satori.core.affect import AffectiveAppraisalProposal, AffectiveAppraisalProviderResponse
from satori.core.conversation import (
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ConversationProviderResponse,
    InvalidProviderResponse,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from tests import checkpoint142_openai_manual_support as support
from tests import checkpoint143_peer_diagnostic3 as diagnostic
from tests.checkpoint142_openai_v26_ledger import ProviderCallBudgetExhausted, PublicTurnScope
from tests.checkpoint143_openai_ab_attempt4_manual_eval import PUBLIC_TURNS
from tests.fakes import FakeAffectiveAppraisalProvider
from tests.stage81_real_eval import _build_runtime
from tests.test_openai_provider import FakeOpenAITransport, adapter, provider_request, response_body


def test_inspection_never_loads_settings_or_creates_paid_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("inspection touched execution")

    monkeypatch.setattr(diagnostic, "local_preflight", forbidden)
    monkeypatch.setattr(diagnostic, "Settings", forbidden)
    monkeypatch.setattr(diagnostic, "acquire_one_shot_authorization_claim", forbidden)
    inspected = diagnostic.inspect_plan()
    assert inspected["plan"]["required_base_calls"] == 12
    assert inspected["plan"]["maximum_provider_calls"] == 16
    assert inspected["plan"]["review"]["character_acceptance"] is False
    assert inspected["plan"]["output_limit_override"]["production_limits_changed"] is False
    assert all(
        turn["visible_output_token_limit"] == 384 and turn["provider_output_token_limit"] == 1408
        for cell in inspected["plan"]["cells"]
        for turn in cell["turns"]
    )
    assert inspected["execution_plan_digest"] == support.content_digest(inspected["plan"])


@pytest.mark.parametrize(
    "field",
    ["authorization_id", "authorized_plan_digest", "maximum_provider_calls", "maximum_cost_usd"],
)
def test_wrong_grant_stops_before_settings_claim_or_network(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("invalid authorization reached execution")

    monkeypatch.setattr(diagnostic, "local_preflight", forbidden)
    monkeypatch.setattr(diagnostic, "Settings", forbidden)
    monkeypatch.setattr(diagnostic, "acquire_one_shot_authorization_claim", forbidden)
    grant = {
        "authorization_id": diagnostic.AUTHORIZATION_ID,
        "authorized_plan_digest": diagnostic.inspect_plan()["execution_plan_digest"],
        "maximum_provider_calls": 16,
        "maximum_cost_usd": 0.20,
    }
    grant[field] = None
    with pytest.raises(ValueError, match="authorization required"):
        asyncio.run(diagnostic.execute(**grant))


def test_unavailable_local_preflight_never_consumes_claim_or_loads_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probes = 0

    async def unavailable() -> dict[str, Any]:
        nonlocal probes
        probes += 1
        raise RuntimeError("local service unavailable")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("failed preflight reached paid authorization or credentials")

    monkeypatch.setattr(diagnostic, "local_preflight", unavailable)
    monkeypatch.setattr(diagnostic, "Settings", forbidden)
    monkeypatch.setattr(diagnostic, "acquire_one_shot_authorization_claim", forbidden)
    monkeypatch.setattr(diagnostic, "repository_root", lambda: tmp_path)
    grant = {
        "authorization_id": diagnostic.AUTHORIZATION_ID,
        "authorized_plan_digest": diagnostic.inspect_plan()["execution_plan_digest"],
        "maximum_provider_calls": 16,
        "maximum_cost_usd": 0.20,
    }
    for _ in range(2):
        with pytest.raises(RuntimeError, match="local service unavailable"):
            asyncio.run(diagnostic.execute(**grant))
    assert probes == 2
    assert not (tmp_path / "var").exists()


@pytest.mark.parametrize(
    ("retry_once", "over_limit"), [(False, False), (True, False), (False, True)]
)
def test_full_conditioned_two_cell_execution_is_offline_and_one_shot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retry_once: bool, over_limit: bool
) -> None:
    real_build = _build_runtime
    real_root = support.repository_root()
    calls: list[ConversationProviderRequest] = []
    (tmp_path / "var").mkdir()

    class WireProvider:
        def __init__(self) -> None:
            self.count = 0
            self.inject_retry = retry_once

        async def generate(
            self, request: ConversationProviderRequest, /
        ) -> ConversationProviderResponse:
            calls.append(request)
            labels = ("Альфа.", "Бета.", "Гамма.", "Дельта.", "Эпсилон.", "Омега.")
            if self.inject_retry:
                text = "Я готов."
                self.inject_retry = False
            else:
                text = labels[self.count]
                self.count += 1
            # Exercise the real adapter with the exact failing numeric boundary (49 > 48).
            raw = json.loads(
                response_body(
                    text=text, output_tokens=435 if over_limit else 99, reasoning_tokens=50
                )
            )
            raw["model"] = diagnostic.MODEL
            transport = FakeOpenAITransport(json.dumps(raw).encode())
            wire = replace(adapter(transport), reasoning_effort="medium")
            result = await wire.generate(request)
            assert transport.calls[0][1]["max_output_tokens"] == 1408
            return result

    def affect(request: Any) -> AffectiveAppraisalProviderResponse:
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

    async def build(settings: Settings, database_path: Path, **kwargs: Any) -> Any:
        kwargs["alembic_config"] = real_root / "alembic.ini"
        runtime, conditioned = await real_build(settings, database_path, **kwargs)
        runtime.conversation_provider.delegate = WireProvider()
        assert runtime.services.talk.prepare_affect is not None
        runtime.services.talk.prepare_affect.provider = FakeAffectiveAppraisalProvider(
            response_factory=affect
        )
        return runtime, conditioned

    async def preflight() -> dict[str, Any]:
        return {"status": "passed", "openai_calls": 0}

    monkeypatch.setattr(diagnostic, "local_preflight", preflight)
    monkeypatch.setattr(support, "_build_runtime", build)
    monkeypatch.setattr(diagnostic, "repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        diagnostic,
        "Settings",
        lambda **kwargs: Settings(
            **{"_env_file": None, "openai_api_key": SecretStr("offline-test-key"), **kwargs}
        ),
    )
    grant = {
        "authorization_id": diagnostic.AUTHORIZATION_ID,
        "authorized_plan_digest": diagnostic.inspect_plan()["execution_plan_digest"],
        "maximum_provider_calls": 16,
        "maximum_cost_usd": 0.20,
    }
    if over_limit:
        with pytest.raises(InvalidProviderResponse, match="visible output exceeded"):
            asyncio.run(diagnostic.execute(**grant))
        report = json.loads(
            (tmp_path / "var/evaluations" / f"{diagnostic.PREFIX}.json").read_text()
        )
        assert report["status"] == "failed_inconclusive"
        assert report["character_accepted"] is False
        assert report["cells"]["27"]["budget"]["provider_call_count"] == 1
        assert report["cells"]["29"]["budget"]["provider_call_count"] == 0
        receipt = report["cells"]["27"]["budget"]["calls"][0]
        assert receipt["failure_reason"] == "visible_output_limit_exceeded"
        assert receipt["usage_complete"] is True
        assert receipt["output_tokens"] == 435
        assert not (tmp_path / "var/evaluations" / f"{diagnostic.PREFIX}.blind.json").exists()
        with pytest.raises(support.EvaluationArtifactSafetyError):
            asyncio.run(diagnostic.execute(**grant))
        assert len(calls) == 1
        return
    result = asyncio.run(diagnostic.execute(**grant))
    expected_calls = 14 if retry_once else 12
    assert len(calls) == expected_calls
    assert all(call.parameters.max_output_tokens == 384 for call in calls)
    assert all("Служебное" not in message.content for call in calls for message in call.messages)
    assert result["status"] == "completed_diagnostic_awaiting_feedback"
    assert result["character_accepted"] is False
    for version in (27, 29):
        cell = result["cells"][str(version)]
        diagnostic.validate_completed_cell(version, cell)
        assert cell["budget"]["provider_call_count"] == (7 if retry_once else 6)
        assert cell["session"]["conditioning"]["processed_interactions"] == 80
        broken = copy.deepcopy(cell)
        broken["session"]["turns"][0]["provider_attempts"][0]["max_output_tokens"] += 1
        with pytest.raises((ValueError, RuntimeError)):
            diagnostic.validate_completed_cell(version, broken)
    blind_path = tmp_path / "var/evaluations" / f"{diagnostic.PREFIX}.blind.json"
    blind = json.loads(blind_path.read_text())
    assert result["sample_digest"] == support.content_digest(
        {key: value for key, value in result.items() if key != "sample_digest"}
    )
    assert len(blind["pairs"]) == 6
    for index, pair in enumerate(blind["pairs"]):
        assert pair["A"] == blind["dialogues"]["A"][index]["reply"]
        assert pair["B"] == blind["dialogues"]["B"][index]["reply"]
    for dialogue in blind["dialogues"].values():
        assert len(dialogue) == 6
        assert all(set(turn) == {"turn", "user", "reply"} for turn in dialogue)
    with pytest.raises(support.EvaluationArtifactSafetyError):
        asyncio.run(diagnostic.execute(**grant))
    assert len(calls) == expected_calls


def native_request() -> ConversationProviderRequest:
    request = provider_request()
    return replace(
        request,
        messages=(
            *request.messages[:-1],
            replace(request.messages[-1], content=PUBLIC_TURNS[0]["user_text"]),
        ),
        parameters=replace(request.parameters, temperature=0.3, max_output_tokens=48),
    )


@pytest.mark.parametrize("visible_tokens", [49, 384, 385])
def test_exact_visible_boundary_without_prompt_or_response_changes(visible_tokens: int) -> None:
    native = native_request()
    scope = PublicTurnScope(session_id="peer-v27-1", turn=1, turn_id="social-opening")
    changed = diagnostic.diagnostic_request(27, native, scope)
    assert replace(changed, parameters=native.parameters) == native
    assert changed.messages is native.messages
    assert changed.parameters == replace(native.parameters, max_output_tokens=384)
    body = response_body(text="Привет.", output_tokens=visible_tokens + 50, reasoning_tokens=50)
    transport = FakeOpenAITransport(body)
    if visible_tokens <= 384:
        response = asyncio.run(adapter(transport).generate(changed))
        assert response.text == "Привет."
        assert response.metrics is not None
        assert response.metrics.visible_output_tokens == visible_tokens
    else:
        with pytest.raises(InvalidProviderResponse) as failure:
            asyncio.run(adapter(transport).generate(changed))
        assert (
            failure.value.reason is ConversationProviderFailureReason.VISIBLE_OUTPUT_LIMIT_EXCEEDED
        )
    assert transport.calls[0][1]["max_output_tokens"] == 1408
    with pytest.raises(InvalidProviderResponse, match="visible output exceeded"):
        asyncio.run(adapter(FakeOpenAITransport(body)).generate(native))


@pytest.mark.parametrize("drift", ["cap", "temperature", "scope", "text", "context"])
def test_native_drift_is_rejected_before_the_override(drift: str) -> None:
    request = native_request()
    scope = PublicTurnScope(session_id="peer-v27-1", turn=1, turn_id="social-opening")
    if drift == "cap":
        request = replace(request, parameters=replace(request.parameters, max_output_tokens=49))
    elif drift == "temperature":
        request = replace(request, parameters=replace(request.parameters, temperature=0.0))
    elif drift == "scope":
        scope = replace(scope, session_id="peer-v29-1")
    elif drift == "text":
        request = replace(
            request,
            messages=(
                *request.messages[:-1],
                replace(request.messages[-1], content="Different input"),
            ),
        )
    else:
        request = replace(request, context_schema_version=17)
    with pytest.raises(ProviderCallBudgetExhausted, match="native public request drift"):
        diagnostic.diagnostic_request(27, request, scope)
