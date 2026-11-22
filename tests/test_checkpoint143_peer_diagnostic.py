"""Offline proof of the complete diagnostic and its spending/data boundaries."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from satori.config import Settings
from satori.core.affect import AffectiveAppraisalProposal, AffectiveAppraisalProviderResponse
from satori.core.conversation import (
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from tests import checkpoint142_openai_manual_support as support
from tests import checkpoint143_peer_diagnostic as diagnostic
from tests.fakes import FakeAffectiveAppraisalProvider
from tests.stage81_real_eval import _build_runtime


def test_inspection_never_loads_settings_or_creates_paid_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("inspection touched execution")

    monkeypatch.setattr(diagnostic, "Settings", forbidden)
    monkeypatch.setattr(diagnostic, "acquire_one_shot_authorization_claim", forbidden)
    inspected = diagnostic.inspect_plan()
    assert inspected["plan"]["required_base_calls"] == 12
    assert inspected["plan"]["maximum_provider_calls"] == 16
    assert inspected["plan"]["review"]["character_acceptance"] is False
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


@pytest.mark.parametrize("retry_once", [False, True])
def test_full_conditioned_two_cell_execution_is_offline_and_one_shot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retry_once: bool
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
            return ConversationProviderResponse(
                text=text,
                provider="openai",
                model=diagnostic.MODEL,
                finish_status="completed",
                usage=ConversationUsage(
                    input_tokens=1000,
                    output_tokens=20,
                    cached_input_tokens=0,
                    cache_write_input_tokens=0,
                ),
            )

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
    result = asyncio.run(diagnostic.execute(**grant))
    expected_calls = 14 if retry_once else 12
    assert len(calls) == expected_calls
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
