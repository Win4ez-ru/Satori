"""One-shot, inspect-first V29/V30 familiar-dialogue diagnostic; never character acceptance."""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import secrets
import tempfile
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from satori.application.conversation.policy import BEHAVIOR_POLICY_V29, BEHAVIOR_POLICY_V30
from satori.config import ConversationProviderKind, OpenAIReasoningEffort, Settings
from satori.core.affect import (
    AffectiveAppraisalRequest,
    AppraisalFastState,
    AppraisalMoodState,
    AppraisalTrait,
    AppraisalValue,
)
from satori.core.conversation import ConversationProviderRequest
from satori.infrastructure.providers.ollama_affect import (
    APPRAISAL_METHOD,
    OllamaAffectiveAppraisalAdapter,
)
from tests.checkpoint142_openai_manual_support import (
    DurableReportWriter,
    acquire_one_shot_authorization_claim,
    content_digest,
    execution_source_fingerprint,
    new_replica_record,
    openai_manual_evaluation_settings,
    public_settings_contract,
    repository_root,
    run_replica,
    strict_json_equal,
)
from tests.checkpoint142_openai_v26_ledger import (
    AtomicOpenAICallLedger,
    ProviderCallBudgetExhausted,
    PublicTurnScope,
    validate_exact_openai_ledger,
)
from tests.checkpoint143_openai_ab_attempt4_manual_eval import (
    _AGENCY_SAFE_MANIFEST_KEYS,
    _COMMON_SAFE_MANIFEST_KEYS,
    PUBLIC_TURNS,
    _checkpoint143_manifest,
)

MODEL = "gpt-5.6-terra"
AUTHORIZATION_ID = "satori.checkpoint143.peer-v29-v30.diagnostic5.2026-09-28.one-shot"
PREFIX = "checkpoint143-peer-v29-v30-diagnostic5-2026-09-28"
MAXIMUM_CALLS = 16
MAXIMUM_COST_USD = 0.20
POLICIES = (BEHAVIOR_POLICY_V29, BEHAVIOR_POLICY_V30)
TEMPERATURES = (0.3, 0.0, 0.3, 0.3, 0.3, 0.3)
NATIVE_CAPS = {29: (48, 80, 96, 96, 112, 96), 30: (48, 80, 96, 96, 112, 96)}
CAPS = {29: (384,) * 6, 30: (384,) * 6}
CONDITIONING = {
    "positive_sessions": 10,
    "positive_turns_per_session": 8,
    "negative_sessions": 0,
    "negative_turns_per_session": 0,
}
SOURCE_BUNDLE = (
    "checkpoint143_peer_diagnostic5.py",
    "checkpoint143_openai_ab_attempt4_manual_eval.py",
    "checkpoint142_openai_manual_support.py",
    "checkpoint142_openai_v26_ledger.py",
    "stage81_real_eval.py",
)
EXPECTED_SETTINGS = openai_manual_evaluation_settings(
    model=MODEL,
    reasoning_effort=OpenAIReasoningEffort.MEDIUM,
    reasoning_token_allowance=1024,
    visible_output_token_ceiling=768,
)


async def local_preflight() -> dict[str, Any]:
    """Probe the actual local structured adapter before consuming a spending grant."""

    request = AffectiveAppraisalRequest(
        schema_version=1,
        trace_id="peer-diagnostic-local-preflight",
        interaction_id="peer-diagnostic-local-preflight",
        appraised_at=datetime.now(UTC),
        user_content=PUBLIC_TURNS[0]["user_text"],
        traits=(AppraisalTrait("curiosity", 0.8),),
        values=(AppraisalValue("growth", 0.9, "Understanding and learning."),),
        fast_state=AppraisalFastState(
            valence=0.0,
            arousal=0.12,
            tension=0.08,
            curiosity=0.18,
            interest=0.16,
            amusement=0.05,
            concern=0.08,
            frustration=0.04,
            situational_confidence=0.55,
        ),
        mood_state=AppraisalMoodState(valence=0.0, energy=0.3, tension=0.1),
    )
    adapter = OllamaAffectiveAppraisalAdapter(
        base_url=cast(str, EXPECTED_SETTINGS["affective_appraisal_provider_base_url"]),
        model=cast(str, EXPECTED_SETTINGS["affective_appraisal_model"]),
        timeout_seconds=cast(float, EXPECTED_SETTINGS["affective_appraisal_timeout_seconds"]),
        max_output_tokens=cast(int, EXPECTED_SETTINGS["affective_appraisal_max_output_tokens"]),
        context_window=cast(int, EXPECTED_SETTINGS["affective_appraisal_context_window"]),
        keep_alive=cast(str, EXPECTED_SETTINGS["ollama_keep_alive"]),
    )
    started = time.perf_counter()
    result = await adapter.generate_structured(request)
    if (
        result.provider != "ollama"
        or result.model != adapter.model
        or result.appraisal_method != APPRAISAL_METHOD
        or result.metrics is None
    ):
        raise RuntimeError("local preflight has incomplete provider evidence")
    return {
        "status": "passed",
        "provider": result.provider,
        "model": result.model,
        "appraisal_method": result.appraisal_method,
        "wall_ms": round((time.perf_counter() - started) * 1000, 3),
        "provider_metrics": result.metrics.as_log_fields(),
        "application_state_changed": False,
        "openai_calls": 0,
    }


class ModelAccessPreflightError(RuntimeError):
    """Sanitized failure of a non-generating, canonical model metadata check."""


def openai_model_preflight(settings: Settings) -> dict[str, Any]:
    """Check direct DNS/TLS, credentials and model access without generating tokens."""

    if settings.openai_base_url != "https://api.openai.com/v1" or not settings.openai_api_key:
        raise ModelAccessPreflightError("canonical API configuration and key required")
    connection = http.client.HTTPSConnection("api.openai.com", timeout=20)
    started = time.perf_counter()
    try:
        connection.request(
            "GET",
            f"/v1/models/{MODEL}",
            headers={
                "Authorization": "Bearer " + settings.openai_api_key.get_secret_value(),
                "Accept": "application/json",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise ModelAccessPreflightError(f"model metadata HTTP status {response.status}")
        body = response.read(65537)
        if len(body) > 65536:
            raise ModelAccessPreflightError("model metadata response too large")
        metadata = json.loads(body)
        if not isinstance(metadata, dict) or metadata.get("id") != MODEL:
            raise ModelAccessPreflightError("model metadata identity mismatch")
    except ModelAccessPreflightError:
        raise
    except Exception:
        raise ModelAccessPreflightError("model metadata transport or format failure") from None
    finally:
        connection.close()
    return {
        "status": "passed",
        "http_status": 200,
        "model": MODEL,
        "wall_ms": round((time.perf_counter() - started) * 1000, 3),
        "generation_calls": 0,
        "scope": "DNS/TLS/key/model metadata only; generation quota is not established",
    }


def inspect_plan() -> dict[str, Any]:
    """No Settings, credentials, claims, network or sampled-output files are touched."""

    plan: dict[str, Any] = {
        "schema_version": 1,
        "authorization_id": AUTHORIZATION_ID,
        "purpose": "familiar_dialogue_diagnostic_not_acceptance",
        "source_fingerprint": execution_source_fingerprint(evaluator_names=SOURCE_BUNDLE),
        "configuration": public_settings_contract(EXPECTED_SETTINGS),
        "provider": "openai",
        "model": MODEL,
        "reasoning_effort": "medium",
        "reasoning_token_allowance": 1024,
        "endpoint": "https://api.openai.com/v1/responses",
        "service_tier": "default",
        "store": False,
        "tools": [],
        "prompt_cache_reads_and_writes": "disabled; exact 0/0 required",
        "local_preflight": {
            "when": "after grant validation, before Settings/key loading and one-shot claim",
            "provider": "ollama",
            "model": "qwen3:4b-instruct",
            "input": PUBLIC_TURNS[0]["user_text"],
            "state": "synthetic health probe only; no application state or conversation",
            "failure": "stop with no claim and no paid artifacts or OpenAI calls",
            "paid_calls": 0,
            "probe_count": 1,
            "warmup_reported": True,
        },
        "remote_preflight": {
            "when": "after local preflight and Settings validation, before paid artifacts/claim",
            "method": "GET",
            "endpoint": "https://api.openai.com/v1/models/gpt-5.6-terra",
            "credential_scope": "configured key only to canonical OpenAI HTTPS origin",
            "redirects": False,
            "timeout_seconds": 20,
            "maximum_response_bytes": 65536,
            "expected_http_status": 200,
            "expected_model": MODEL,
            "generation_calls": 0,
            "failure": "stop with no claim and no paid artifacts",
            "does_not_prove": "generation quota, future availability or response quality",
        },
        "required_base_calls": 12,
        "maximum_provider_calls": MAXIMUM_CALLS,
        "maximum_cost_usd": MAXIMUM_COST_USD,
        "maximum_calls_per_cell": 8,
        "maximum_cost_usd_per_cell": 0.10,
        "maximum_attempts_per_turn": 2,
        "retry_rule": "existing validator only; stop on any provider or metadata failure",
        "replicas_per_cell": 1,
        "relationship_conditioning": dict(CONDITIONING),
        "initial_state": "same canonical seed and typed positive relationship; no strain",
        "history": "each cell retains its own actual replies; no reference assistant prose",
        "affect": "local Ollama before replies; post-response formation disabled",
        "state_comparison": "identical initial conditions, not identical sampled affect or history",
        "comparison": "native prompt policies under a shared diagnostic visible cap of 384",
        "output_limit_override": {
            "scope": "diagnostic only; before recording and budget reservation",
            "native_caps": {str(v): list(c) for v, c in NATIVE_CAPS.items()},
            "effective_visible_cap": 384,
            "effective_provider_total_cap": 1408,
            "native_request_checked_before_override": True,
            "prompt_text_changed": False,
            "response_text_changed": False,
            "production_limits_changed": False,
            "reason": "diagnostic1 stopped at 49 visible tokens against native cap 48",
        },
        "cells": [
            {
                "policy_id": policy.policy_id,
                "policy_schema_version": policy.schema_version,
                "turns": [
                    {
                        **turn,
                        "temperature": TEMPERATURES[index],
                        "native_visible_output_token_limit": NATIVE_CAPS[policy.schema_version][
                            index
                        ],
                        "visible_output_token_limit": CAPS[policy.schema_version][index],
                        "provider_output_token_limit": CAPS[policy.schema_version][index] + 1024,
                    }
                    for index, turn in enumerate(PUBLIC_TURNS)
                ],
            }
            for policy in POLICIES
        ],
        "pricing": {
            "checked_on": "2026-09-28",
            "source": "https://developers.openai.com/api/docs/pricing",
            "usd_per_million_input": 2.0,
            "usd_per_million_output_including_reasoning": 12.0,
            "usd_per_million_cached_input": 0.20,
            "usd_per_million_cache_write": 2.50,
            "ledger": "integer nano-USD; reserve before network; failures retain reservation",
        },
        "review": {
            "presentation": "six anonymous pairs plus both complete dialogues; unedited replies",
            "feedback": "free-form user comments, no numerical rubric required",
            "automated_text_judge": False,
            "response_rewriting": False,
            "character_acceptance": False,
            "future_stages_opened": False,
        },
        "data_scope": "six public inputs, synthetic Satori state and this diagnostic's replies",
        "artifacts": {
            "claim": f"var/evaluation-authorizations/{PREFIX}.claim.json",
            "report": f"var/evaluations/{PREFIX}.json",
            "blind": f"var/evaluations/{PREFIX}.blind.json",
        },
    }
    return {"plan": plan, "execution_plan_digest": content_digest(plan)}


def diagnostic_request(
    version: int, request: ConversationProviderRequest, scope: PublicTurnScope
) -> ConversationProviderRequest:
    """Raise only the declared experimental cap after verifying the native request."""

    if version not in NATIVE_CAPS or not 1 <= scope.turn <= 6:
        raise ProviderCallBudgetExhausted("unplanned native request")
    index = scope.turn - 1
    current_users = [m.content for m in request.messages if m.role.value == "user"]
    if (
        scope.session_id != f"peer-v{version}-1"
        or scope.turn_id != PUBLIC_TURNS[index]["id"]
        or request.schema_version != 1
        or request.context_schema_version != 16
        or not current_users
        or current_users[-1] != PUBLIC_TURNS[index]["user_text"]
        or request.parameters.temperature != TEMPERATURES[index]
        or request.parameters.max_output_tokens != NATIVE_CAPS[version][index]
    ):
        raise ProviderCallBudgetExhausted("native public request drift")
    return replace(request, parameters=replace(request.parameters, max_output_tokens=384))


class PeerLedger(AtomicOpenAICallLedger):
    """Reject unplanned turns, text, parameters and extra calls before network I/O."""

    def __init__(self, version: int) -> None:
        if version not in CAPS:
            raise ValueError("unplanned policy")
        super().__init__(
            maximum_calls=8,
            maximum_cost_usd=0.10,
            required_base_calls=6,
            reasoning_token_allowance=1024,
            expected_context_schema_version=16,
        )
        self.version = version

    def _temperature_is_valid(self, temperature: float) -> bool:
        return temperature in TEMPERATURES

    def reserve(self, request: ConversationProviderRequest, scope: PublicTurnScope) -> int:
        if not 1 <= scope.turn <= 6:
            raise ProviderCallBudgetExhausted("unplanned turn")
        index = scope.turn - 1
        current_users = [
            message.content for message in request.messages if message.role.value == "user"
        ]
        if (
            scope.session_id != f"peer-v{self.version}-1"
            or scope.turn_id != PUBLIC_TURNS[index]["id"]
            or not current_users
            or current_users[-1] != PUBLIC_TURNS[index]["user_text"]
            or request.parameters.temperature != TEMPERATURES[index]
            or request.parameters.max_output_tokens != CAPS[self.version][index]
        ):
            raise ProviderCallBudgetExhausted("public request drift")
        return super().reserve(request, scope)


def safe_manifest(version: int, raw: Mapping[str, Any]) -> dict[str, Any]:
    expected = (17, 5, 3)
    if (
        raw.get("policy_schema_version") != version
        or raw.get("schema_version") != expected[0]
        or raw.get("character_delivery_decision_schema_version") != expected[1]
        or raw.get("character_presence_projection_schema_version") != expected[2]
        or raw.get("retrieved_memory_count") != 0
        or raw.get("relationship_recent_strain") is not False
    ):
        raise RuntimeError("diagnostic runtime or isolated state drift")
    keys = _COMMON_SAFE_MANIFEST_KEYS + _AGENCY_SAFE_MANIFEST_KEYS
    result = {key: raw.get(key) for key in keys}
    if version in (29, 30):
        refs = raw.get("character_agency_source_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or raw.get("character_agency_status") != "applied"
        ):
            raise RuntimeError("missing applied agency evidence")
        result["character_agency_source_ref_count"] = len(refs)
        result["character_agency_subject_ref_present"] = (
            raw.get("character_agency_subject_ref") is not None
        )
        if raw.get("relationship_expression_profile") != "established_positive":
            raise RuntimeError("treatment lost familiar state")
    return result


def validate_completed_cell(version: int, cell: Mapping[str, Any]) -> None:
    record = cell["session"]
    if record.get("completed") is not True or len(record["turns"]) != 6:
        raise RuntimeError("incomplete diagnostic cell")
    conditioning = record.get("conditioning")
    if not isinstance(conditioning, dict) or conditioning.get("processed_interactions") != 80:
        raise RuntimeError("missing deterministic relationship conditioning")
    state = conditioning["state"]
    if (
        state["negative_evidence_count"] != 0
        or state["positive_evidence_count"] != 80
        or state["projection"]["maturity"] != "established"
        or state["projection"]["recent_strain"] is not False
    ):
        raise RuntimeError("unexpected initial relationship")
    if cell["budget"]["gate_valid"] is not True:
        raise RuntimeError("invalid terminal budget")
    validate_exact_openai_ledger(
        cell["budget"],
        [record],
        required_base_calls=6,
        maximum_provider_calls=8,
        maximum_cost_usd=0.10,
        reasoning_token_allowance=1024,
        visible_output_token_ceiling=768,
    )
    for index, turn in enumerate(record["turns"]):
        if turn["status"] != "completed" or turn["user"] != PUBLIC_TURNS[index]["user_text"]:
            raise RuntimeError("incomplete public turn")
        for attempt in turn["provider_attempts"]:
            if (
                attempt["temperature"] != TEMPERATURES[index]
                or attempt["max_output_tokens"] != CAPS[version][index]
            ):
                raise RuntimeError("terminal request drift")


async def execute(
    *,
    authorization_id: str | None,
    authorized_plan_digest: str | None,
    maximum_provider_calls: int | None,
    maximum_cost_usd: float | None,
) -> dict[str, Any]:
    inspected = inspect_plan()
    plan, digest = inspected["plan"], inspected["execution_plan_digest"]
    if (
        authorization_id != AUTHORIZATION_ID
        or authorized_plan_digest != digest
        or type(maximum_provider_calls) is not int
        or maximum_provider_calls != MAXIMUM_CALLS
        or type(maximum_cost_usd) is not float
        or maximum_cost_usd != MAXIMUM_COST_USD
        or not plan["source_fingerprint"]["installed_wheel_parity"]
    ):
        raise ValueError("exact current plan, call ceiling and cost authorization required")
    preflight = await local_preflight()
    settings = Settings(**cast(dict[str, Any], EXPECTED_SETTINGS))
    actual = {key: getattr(settings, key) for key in EXPECTED_SETTINGS}
    if not strict_json_equal(public_settings_contract(actual), plan["configuration"]):
        raise RuntimeError("settings drift")
    remote_preflight = await asyncio.to_thread(openai_model_preflight, settings)
    root = repository_root()
    writer = DurableReportWriter(root / "var", f"{PREFIX}.json", "peer diagnostic")
    blind_writer = DurableReportWriter(
        root / "var", f"{PREFIX}.blind.json", "peer diagnostic blind"
    )
    writer.prepare()
    blind_writer.prepare()
    ledgers = {policy.schema_version: PeerLedger(policy.schema_version) for policy in POLICIES}
    report: dict[str, Any] = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "authorized_preflight",
        "execution_plan": plan,
        "execution_plan_digest": digest,
        "character_accepted": False,
        "human_feedback": None,
        "local_preflight": preflight,
        "remote_preflight": remote_preflight,
        "cells": {},
    }
    for version in ledgers:
        report["cells"][str(version)] = {
            "session": new_replica_record(session_id=f"peer-v{version}-1"),
            "budget": ledgers[version].snapshot(),
        }

    def checkpoint() -> None:
        for version, ledger in ledgers.items():
            report["cells"][str(version)]["budget"] = ledger.snapshot()
        writer.write(report)

    for ledger in ledgers.values():
        ledger.on_change = checkpoint
    acquire_one_shot_authorization_claim(
        root=root / "var",
        authorization_id=authorization_id,
        expected_authorization_id=AUTHORIZATION_ID,
        plan_digest=digest,
        expected_claim_name=f"{PREFIX}.claim.json",
        evaluation_label="peer diagnostic",
    )
    try:
        checkpoint()
        if inspect_plan()["execution_plan_digest"] != digest:
            raise RuntimeError("source drift after claim")
        report["status"] = "running"
        checkpoint()
        with tempfile.TemporaryDirectory(prefix="satori-peer-diagnostic-") as directory:
            for policy in POLICIES:
                version = policy.schema_version
                cell = report["cells"][str(version)]

                def project(raw: Mapping[str, Any], version: int = version) -> dict[str, Any]:
                    return safe_manifest(version, raw)

                def transform(
                    request: ConversationProviderRequest,
                    scope: PublicTurnScope,
                    version: int = version,
                ) -> ConversationProviderRequest:
                    return diagnostic_request(version, request, scope)

                await run_replica(
                    settings=settings,
                    database_path=Path(directory) / f"v{version}.db",
                    alembic_config=root / "alembic.ini",
                    replica_number=1,
                    ledger=ledgers[version],
                    checkpoint=checkpoint,
                    behavior_policy=policy,
                    public_turns=PUBLIC_TURNS,
                    public_session_prefix=f"peer-v{version}",
                    expected_provider=ConversationProviderKind.OPENAI,
                    expected_model=MODEL,
                    safe_manifest=project,
                    record=cell["session"],
                    manifest_projector=_checkpoint143_manifest,
                    relationship_conditioning=dict(CONDITIONING),
                    request_transform=transform,
                )
                validate_completed_cell(version, cell)
        before = report["cells"]["29"]["session"]["conditioning"]["state"]
        after = report["cells"]["30"]["session"]["conditioning"]["state"]
        if before != after:
            raise RuntimeError("initial state differs between policies")
        report["status"] = "completed_diagnostic_awaiting_feedback"
        first, second = ("29", "30") if secrets.randbelow(2) == 0 else ("30", "29")
        report["blind_assignment"] = {"A": first, "B": second}
        report["sample_digest"] = content_digest(report)
        checkpoint()
        blind_writer.write(
            {
                "schema_version": 1,
                "sample_digest": report["sample_digest"],
                "character_accepted": False,
                "pairs": [
                    {
                        "turn": index + 1,
                        "user": PUBLIC_TURNS[index]["user_text"],
                        "A": report["cells"][first]["session"]["turns"][index]["reply"],
                        "B": report["cells"][second]["session"]["turns"][index]["reply"],
                    }
                    for index in range(6)
                ],
                "dialogues": {
                    label: [
                        {"turn": turn["turn"], "user": turn["user"], "reply": turn["reply"]}
                        for turn in report["cells"][version]["session"]["turns"]
                    ]
                    for label, version in (("A", first), ("B", second))
                },
            }
        )
        return report
    except BaseException as error:
        report["status"] = "failed_inconclusive"
        report["error_type"] = type(error).__name__
        checkpoint()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorization-id")
    parser.add_argument("--authorized-plan-digest")
    parser.add_argument("--maximum-provider-calls", type=int)
    parser.add_argument("--maximum-cost-usd", type=float)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(inspect_plan(), ensure_ascii=False, indent=2))
        return 0
    result = asyncio.run(
        execute(
            authorization_id=args.authorization_id,
            authorized_plan_digest=args.authorized_plan_digest,
            maximum_provider_calls=args.maximum_provider_calls,
            maximum_cost_usd=args.maximum_cost_usd,
        )
    )
    print(json.dumps({"status": result["status"], "report": f"var/evaluations/{PREFIX}.json"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
