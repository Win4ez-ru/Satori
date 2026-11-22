"""Explicit Terra foreground, unchanged Luna background, and exact-cost admission."""

import asyncio
import json
import logging
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from satori.config import OpenAIModelProfile, Settings
from satori.core.conversation import ConversationProviderFailureReason, GenerationFailed
from satori.desktop.backend import DesktopBackend
from satori.server.__main__ import main
from satori.server.budget import BudgetedOpenAITransport, OpenAIBudgetError, PriceAssumptions
from satori.server.openai_runtime import (
    OPENAI_ROLES,
    OpenAIRuntimeConfigurationError,
    budget_model_for_role,
    build_openai_providers,
    build_openai_settings,
)
from satori.server.usage import AttemptState, Model, Role, UsageLedger
from tests.test_openai_all_role_integration import (
    NOW,
    PERIOD,
    REPOSITORY,
    ROLE_FIELDS,
    CanonicalFixtureHTTP,
    RecordingLedger,
)
from tests.test_openai_budget import Transport, request, response
from tests.test_openai_output_bound import correction
from tests.test_server_runtime import prepared


def selected(root: Path) -> Settings:
    return build_openai_settings(
        root.resolve(),
        SecretStr("synthetic-key"),
        model_profile=OpenAIModelProfile.FOREGROUND_TERRA,
    )


def prices() -> PriceAssumptions:
    return PriceAssumptions(Decimal(100), NOW - timedelta(hours=1), NOW + timedelta(days=1))


def terra_request() -> dict[str, Any]:
    return request() | {"model": "gpt-6.1-terra"}


def terra_response() -> dict[str, Any]:
    return response() | {"model": "gpt-6.1-terra"}


@pytest.mark.parametrize("profile", list(OpenAIModelProfile))
def test_explicit_factory_selects_one_foreground_seven_background_and_embedding(
    tmp_path: Path, profile: OpenAIModelProfile, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SATORI_OPENAI_MODEL_PROFILE", "untrusted-inherited-value")
    configuration = build_openai_settings(
        tmp_path, SecretStr("synthetic-key"), model_profile=profile
    )
    foreground = "gpt-6.1-terra" if profile is OpenAIModelProfile.FOREGROUND_TERRA else "gpt-6-luna"
    assert configuration.openai_model_profile is profile
    assert configuration.conversation_model == foreground
    assert configuration.conversation_output_token_limit_floor == 128
    assert configuration.conversation_max_output_tokens == 768
    transports = {role: Transport() for role in OPENAI_ROLES}
    ports = build_openai_providers(configuration, transports)
    for role, field in ROLE_FIELDS.items():
        expected = (
            "text-embedding-3-small"
            if role is Role.EMBEDDING
            else foreground
            if role is Role.CONVERSATION
            else "gpt-6-luna"
        )
        assert getattr(ports, field).model == expected
        assert budget_model_for_role(configuration, role) is (
            Model.TEXT_EMBEDDING_3_SMALL
            if role is Role.EMBEDDING
            else Model.GPT_6_1_TERRA
            if role is Role.CONVERSATION and profile is OpenAIModelProfile.FOREGROUND_TERRA
            else Model.GPT_6_LUNA
        )


def test_luna_remains_default_and_profile_changes_only_two_settings(tmp_path: Path) -> None:
    original = build_openai_settings(tmp_path, SecretStr("synthetic-key"))
    terra = selected(tmp_path)
    assert original.openai_model_profile is OpenAIModelProfile.ALL_LUNA
    assert original.conversation_model == "gpt-6-luna"
    assert {
        field
        for field in Settings.model_fields
        if getattr(original, field) != getattr(terra, field)
    } == {"openai_model_profile", "conversation_model"}


@pytest.mark.parametrize(
    "mutation",
    [
        {"conversation_model": "gpt-6-luna"},
        {"conversation_model": "arbitrary-model"},
        {"episode_formation_model": "gpt-6.1-terra"},
        {"semantic_formation_model": "gpt-6.1-terra"},
        {"affective_appraisal_model": "gpt-6.1-terra"},
        {"relationship_appraisal_model": "gpt-6.1-terra"},
        {"model_formation_model": "gpt-6.1-terra"},
        {"position_formation_model": "gpt-6.1-terra"},
        {"reflection_model": "gpt-6.1-terra"},
        {"embedding_model": "gpt-6.1-terra"},
        {"conversation_max_output_tokens": 769},
        {"conversation_max_output_tokens": 127},
    ],
)
def test_profile_rejects_mixed_or_unbounded_configuration(
    tmp_path: Path, mutation: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **(selected(tmp_path).model_dump() | mutation))  # type: ignore[call-arg]


def test_profile_is_not_implicit_or_an_arbitrary_model_string(tmp_path: Path) -> None:
    defaults = {
        name: field.get_default(call_default_factory=True)
        for name, field in Settings.model_fields.items()
    }
    with pytest.raises(ValidationError, match="profile_requires_all_openai_mode"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            **(defaults | {"openai_model_profile": OpenAIModelProfile.FOREGROUND_TERRA}),
        )
    with pytest.raises(OpenAIRuntimeConfigurationError):
        build_openai_settings(
            tmp_path,
            SecretStr("synthetic-key"),
            model_profile="gpt-6.1-terra",  # type: ignore[arg-type]
        )
    original = build_openai_settings(tmp_path, SecretStr("synthetic-key"))
    with pytest.raises(ValidationError, match="pinned_generation_models"):
        Settings(  # type: ignore[call-arg]
            _env_file=None, **(original.model_dump() | {"conversation_model": "gpt-6.1-terra"})
        )


@pytest.mark.parametrize("role", [role for role in OPENAI_ROLES if role is not Role.CONVERSATION])
def test_terra_budget_cannot_be_assigned_to_any_background_or_embedding_role(
    tmp_path: Path, role: Role
) -> None:
    owner = UsageLedger(tmp_path / "usage", clock=lambda: NOW)
    try:
        with pytest.raises(ValueError, match="unsupported_budget_model_for_role"):
            BudgetedOpenAITransport(Transport(), owner, role, prices(), model=Model.GPT_6_1_TERRA)
    finally:
        owner.close()


def test_cost_is_exactly_ten_times_luna_and_covers_highest_cache_write_price() -> None:
    assumptions = prices()
    luna = assumptions.estimate_micro_rub(
        embedding=False, model=Model.GPT_6_LUNA, input_tokens=20, output_tokens=10
    )
    terra = assumptions.estimate_micro_rub(
        embedding=False, model=Model.GPT_6_1_TERRA, input_tokens=20, output_tokens=10
    )
    assert terra == 10 * luna == 10_000
    assert (
        assumptions.estimate_micro_rub(
            embedding=False, model=Model.GPT_6_1_TERRA, input_tokens=1_000_000, output_tokens=0
        )
        == 250_000_000
    )


@pytest.mark.parametrize(
    ("expected_model", "wire_model"),
    [(Model.GPT_6_LUNA, "gpt-6.1-terra"), (Model.GPT_6_1_TERRA, "gpt-6-luna")],
)
def test_explicit_budget_model_mismatch_rejects_before_reservation_or_dispatch(
    tmp_path: Path, expected_model: Model, wire_model: str
) -> None:
    owner = UsageLedger(tmp_path / "usage", clock=lambda: NOW)
    owner.configure_period(PERIOD, 20_000_000)
    raw = Transport()
    try:
        client = BudgetedOpenAITransport(
            raw, owner, Role.CONVERSATION, prices(), clock=lambda: NOW, model=expected_model
        )
        with pytest.raises(OpenAIBudgetError, match="request_rejected"):
            client.post_json(
                "/responses",
                request() | {"model": wire_model},
                timeout_seconds=3,
                max_response_bytes=1_000_000,
            )
        assert raw.calls == 0
        assert owner.snapshot(PERIOD).liability_micro_rub == 0
    finally:
        owner.close()


@pytest.mark.parametrize("model", [False, "", "gpt_6_1_terra", Model.SPEECHKIT_V3])
def test_budget_never_defaults_an_invalid_explicit_model(tmp_path: Path, model: object) -> None:
    owner = UsageLedger(tmp_path / "usage", clock=lambda: NOW)
    try:
        with pytest.raises(ValueError, match="unsupported_budget_model_for_role"):
            BudgetedOpenAITransport(
                Transport(),
                owner,
                Role.CONVERSATION,
                prices(),
                model=model,  # type: ignore[arg-type]
            )
        with pytest.raises(OpenAIBudgetError, match="usage_unknown"):
            prices().estimate_micro_rub(
                embedding=False,
                model=model,  # type: ignore[arg-type]
                input_tokens=20,
                output_tokens=10,
            )
    finally:
        owner.close()


def test_private_cli_explicit_profile_wires_exact_budget_models_without_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _, _, _ = prepared(tmp_path)
    observed: dict[Role, Model] = {}

    def record(configuration: Settings, role: Role) -> Model:
        assert configuration.openai_model_profile is OpenAIModelProfile.FOREGROUND_TERRA
        result = budget_model_for_role(configuration, role)
        observed[role] = result
        return result

    monkeypatch.setenv("SATORI_OPENAI_API_KEY", "synthetic-cli-key-never-log")
    monkeypatch.setenv("SATORI_OPENAI_MODEL_PROFILE", "all_luna")
    monkeypatch.setattr("satori.server.__main__.budget_model_for_role", record)
    monkeypatch.setattr(
        "satori.infrastructure.providers.openai_http.OpenAIHttpClient.post_json",
        lambda *_a, **_k: pytest.fail("check must not dispatch"),
    )
    now = datetime.now(UTC)
    previous = logging.root.manager.disable
    try:
        assert (
            main(
                [
                    "check",
                    "--openai",
                    "--model-profile",
                    "foreground_terra",
                    "--data-directory",
                    str(root),
                    "--repository-directory",
                    str(REPOSITORY),
                    "--usage-directory",
                    str(tmp_path / "usage"),
                    "--rub-per-usd-ceiling",
                    "130",
                    "--tariff-reviewed-utc",
                    (now - timedelta(seconds=1)).isoformat(),
                    "--tariff-expires-utc",
                    (now + timedelta(hours=1)).isoformat(),
                ]
            )
            == 0
        )
        assert set(observed) == OPENAI_ROLES
        assert observed[Role.CONVERSATION] is Model.GPT_6_1_TERRA
        assert all(
            model is (Model.TEXT_EMBEDDING_3_SMALL if role is Role.EMBEDDING else Model.GPT_6_LUNA)
            for role, model in observed.items()
            if role is not Role.CONVERSATION
        )
        output = capsys.readouterr()
        assert json.loads(output.out)["state"] == "closed"
        assert "synthetic-cli-key-never-log" not in output.out + output.err
    finally:
        logging.disable(previous)


@pytest.mark.parametrize(
    "mutation",
    [
        {"model": "gpt-6-luna"},
        {"service_tier": "priority"},
        {"usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 99}},
    ],
)
def test_terra_bad_response_preserves_unknown_liability(
    tmp_path: Path, mutation: dict[str, object]
) -> None:
    owner = UsageLedger(tmp_path / "usage", clock=lambda: NOW)
    owner.configure_period(PERIOD, 20_000_000)
    raw = Transport(terra_response() | mutation)
    try:
        client = BudgetedOpenAITransport(
            raw,
            owner,
            Role.CONVERSATION,
            prices(),
            clock=lambda: NOW,
            model=Model.GPT_6_1_TERRA,
        )
        with pytest.raises(OpenAIBudgetError, match="usage_unknown"):
            client.post_json(
                "/responses", terra_request(), timeout_seconds=3, max_response_bytes=1_000_000
            )
        assert raw.calls == 1
        assert owner.snapshot(PERIOD).unsettled_attempts == 1
        assert owner.snapshot(PERIOD).liability_micro_rub > 10_000
    finally:
        owner.close()


def test_legacy_v1_record_survives_new_model_without_schema_or_cap_change(tmp_path: Path) -> None:
    directory = tmp_path / "usage"
    owner = UsageLedger(directory, clock=lambda: NOW)
    owner.configure_period(PERIOD, 20_000_000)
    owner.close()
    old_id = uuid4()
    old_document = json.dumps(
        {
            "spec": {
                "attempt_id": str(old_id),
                "provider": "openai",
                "model": "gpt_6_luna",
                "role": "conversation",
                "reservation_micro_rub": 321,
            },
            "period": PERIOD,
            "created_at": NOW.isoformat(),
            "state": "unknown",
            "reported_micro_rub": None,
            "input_tokens": None,
            "output_tokens": None,
        }
    )
    with sqlite3.connect(directory / "usage.db") as connection:
        connection.execute("INSERT INTO attempts VALUES (?,?)", (str(old_id), old_document))
    reopened = UsageLedger(directory, clock=lambda: NOW)
    try:
        assert reopened.reconcile(old_id).spec.model is Model.GPT_6_LUNA
        assert reopened.reconcile(old_id).state is AttemptState.UNKNOWN
        raw = Transport(terra_response())
        client = BudgetedOpenAITransport(
            raw,
            reopened,
            Role.CONVERSATION,
            prices(),
            clock=lambda: NOW,
            model=Model.GPT_6_1_TERRA,
        )
        assert client.post_json(
            "/responses", terra_request(), timeout_seconds=3, max_response_bytes=1_000_000
        )
        snapshot = reopened.snapshot(PERIOD)
        assert snapshot.cap_micro_rub == 20_000_000
        assert snapshot.unsettled_attempts == 1
        assert snapshot.settled_reported_micro_rub == 10_000
        assert snapshot.liability_micro_rub == 10_321
    finally:
        reopened.close()
    with sqlite3.connect(directory / "usage.db") as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute(
            "SELECT document FROM attempts WHERE attempt_id=?", (str(old_id),)
        ).fetchone() == (old_document,)
        assert connection.execute("SELECT count(*) FROM attempts").fetchone() == (2,)
    readback = UsageLedger(directory, clock=lambda: NOW)
    assert readback.snapshot(PERIOD).liability_micro_rub == 10_321
    readback.close()


class TerraHTTP(CanonicalFixtureHTTP):
    def __init__(self, role: Role, owner: RecordingLedger, scenario: str) -> None:
        super().__init__(role, owner)
        self.scenario = scenario
        self.caps: list[int] = []

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        if self.role is not Role.CONVERSATION:
            return super().post_json(
                path,
                payload,
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
            )
        assert path == "/responses"
        assert payload["model"] == "gpt-6.1-terra"
        assert payload["max_output_tokens"] == 128
        assert payload["reasoning"] == {"effort": "none"}
        self.caps.append(payload["max_output_tokens"])
        self.calls.append(path)
        rows = [
            row
            for row in self.ledger.records()
            if row.state is AttemptState.DISPATCHED and row.spec.role is self.role
        ]
        assert len(rows) == 1
        assert rows[0].spec.model is Model.GPT_6_1_TERRA
        wire_bytes = len(json.dumps(dict(payload), ensure_ascii=False, allow_nan=False).encode())
        assert rows[0].spec.reservation_micro_rub == prices().estimate_micro_rub(
            embedding=False,
            model=Model.GPT_6_1_TERRA,
            input_tokens=wire_bytes + 4096,
            output_tokens=128,
        )
        document = terra_response()
        text = "Я готов." if self.scenario == "retry" else "Integration reply 1."
        document["output"] = [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
        if self.scenario == "incomplete":
            document["status"] = "incomplete"
            document["incomplete_details"] = {"reason": "max_output_tokens"}
        return json.dumps(document).encode()


@pytest.mark.parametrize("scenario", ["normal", "retry", "incomplete"])
def test_terra_core_keeps_exact_floor_budget_incomplete_and_existing_single_retry(
    tmp_path: Path, scenario: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve() / "installation"
    root.mkdir(mode=0o700)
    configuration = selected(root)
    owner = RecordingLedger(tmp_path / "usage")
    owner.configure_period(PERIOD, 20_000_000)
    raw = {role: TerraHTTP(role, owner, scenario) for role in OPENAI_ROLES}
    transports = {
        role: BudgetedOpenAITransport(
            transport,
            owner,
            role,
            prices(),
            clock=lambda: NOW,
            model=budget_model_for_role(configuration, role),
        )
        for role, transport in raw.items()
    }
    ports = build_openai_providers(configuration, transports)
    monkeypatch.setattr(
        DesktopBackend, "_local_providers", lambda *_a: pytest.fail("local fallback")
    )
    backend = DesktopBackend(root, REPOSITORY, providers=ports, runtime_settings=configuration)
    backend.open()
    backend.activate()
    session = str(backend.new_session()["session_id"])
    assert backend.status()["model"] == "gpt-6.1-terra"

    async def run() -> None:
        try:
            if scenario == "incomplete":
                with pytest.raises(GenerationFailed) as failed:
                    await backend.send_for_session(correction(), "terra-contract", session)
                assert failed.value.reason is ConversationProviderFailureReason.OUTPUT_TOKEN_LIMIT
                history = backend.history(session)["interactions"]
                assert isinstance(history, list)
                assert len(history) == 1
                assert history[0]["status"] == "failed"
                assert history[0]["assistant_text"] is None
            else:
                reply = await backend.send_for_session(correction(), "terra-contract", session)
                assert reply["text"] == (
                    "Я готов." if scenario == "retry" else "Integration reply 1."
                )
            # The second invalid sample keeps the historical max-one path;
            # this is a lifecycle assertion, never semantic quality acceptance.
            assert raw[Role.CONVERSATION].caps == [128] * (2 if scenario == "retry" else 1)
        finally:
            await backend.close()

    try:
        asyncio.run(run())
        rows = owner.records()
        assert all(row.state is AttemptState.SETTLED for row in rows)
        assert all(
            row.spec.model is budget_model_for_role(configuration, row.spec.role) for row in rows
        )
    finally:
        owner.close()
