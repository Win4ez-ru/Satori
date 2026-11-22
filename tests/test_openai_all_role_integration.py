"""Real all-role factory, durable budget owner and synthetic canonical Core chain."""

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any

import pytest
from pydantic import SecretStr

from satori.config import Settings
from satori.core.conversation import ConversationProviderError
from satori.core.embedding import EmbeddingProviderError, EmbeddingRequest, EmbeddingResponse
from satori.desktop.backend import DesktopBackend, DesktopProviders
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from satori.infrastructure.providers.openai_http import OpenAIHttpClient
from satori.server.budget import BudgetedOpenAITransport, PriceAssumptions
from satori.server.openai_runtime import (
    OPENAI_GENERATION_MODEL,
    OPENAI_ROLES,
    build_openai_providers,
    build_openai_settings,
)
from satori.server.runtime import HeadlessCommand, HeadlessRuntime
from satori.server.usage import (
    AttemptRecord,
    AttemptSpec,
    AttemptState,
    Provider,
    Role,
    UsageLedger,
)
from tests.test_openai_provider import provider_request
from tests.test_openai_roles import case

REPOSITORY = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
PERIOD = "2026-10"
ROLE_FIELDS = {
    Role.CONVERSATION: "conversation",
    Role.EPISODE: "episode",
    Role.EMBEDDING: "embedding",
    Role.SEMANTIC: "semantic",
    Role.APPRAISAL: "appraisal",
    Role.RELATIONSHIP: "relationship",
    Role.MODELS: "models",
    Role.POSITIONS: "positions",
    Role.REFLECTION: "reflection",
}


class RecordingLedger(UsageLedger):
    """Observe returned metadata; every mutation still runs the real owner path."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory, clock=lambda: NOW)
        self.observed: list[AttemptRecord] = []
        self.observed_lock = Lock()

    def reserve(self, spec: AttemptSpec) -> AttemptRecord:
        result = super().reserve(spec)
        with self.observed_lock:
            self.observed.append(result)
        return result

    def records(self) -> list[AttemptRecord]:
        with self.observed_lock:
            ids = [row.spec.attempt_id for row in self.observed]
        return [self.reconcile(attempt) for attempt in ids]


class CanonicalFixtureHTTP:
    """Finite Responses/embeddings fixtures, with no fabricated durable evidence."""

    def __init__(self, role: Role, ledger: RecordingLedger, *, fault: str | None = None) -> None:
        self.role = role
        self.ledger = ledger
        self.fault = fault
        self.calls: list[str] = []

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        assert timeout_seconds > 0
        assert max_response_bytes > 0
        # This runs at the innermost fake network boundary, after durable dispatch.
        assert any(
            row.spec.role == self.role and row.state == AttemptState.DISPATCHED
            for row in self.ledger.records()
        )
        self.calls.append(path)
        if self.fault == "timeout":
            raise TimeoutError("private detail must not escape")
        if self.role == Role.EMBEDDING:
            assert path == "/embeddings"
            response: dict[str, object] = {
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [
                    {
                        "object": "embedding",
                        "index": index,
                        "embedding": [1.0] + [0.0] * (payload["dimensions"] - 1),
                    }
                    for index, _ in enumerate(payload["input"])
                ],
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            }
        else:
            assert path == "/responses"
            assert payload["model"] == OPENAI_GENERATION_MODEL
            assert payload["store"] is False
            assert payload["service_tier"] == "default"
            documents: dict[Role, dict[str, object]] = {
                Role.EPISODE: {
                    "schema_version": 1,
                    "should_create": False,
                    "summary": None,
                    "importance": None,
                    "confidence": None,
                    "evidence": [],
                },
                Role.SEMANTIC: {"schema_version": 1, "claims": []},
                Role.APPRAISAL: {"v": 2, "k": ["neutral_social"], "q": 90, "r": ["e"]},
                Role.RELATIONSHIP: {"v": 1, "k": ["neutral_contact"], "q": 90, "r": ["i", "u"]},
                Role.MODELS: {"schema_version": 1, "user_claims": [], "world_claims": []},
                Role.POSITIONS: {"schema_version": 1, "positions": []},
                Role.REFLECTION: {"schema_version": 1, "proposals": []},
            }
            text = (
                f"Integration reply {len(self.calls)}."
                if self.role == Role.CONVERSATION
                else json.dumps(documents[self.role])
            )
            response = {
                "model": OPENAI_GENERATION_MODEL,
                "status": "completed",
                "service_tier": "default",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            }
        if self.fault == "missing_usage":
            del response["usage"]
        return json.dumps(response).encode()


def composition(
    root: Path,
    *,
    cap: int = 800_000_000,
    fault: str | None = None,
) -> tuple[Settings, DesktopProviders, RecordingLedger, dict[Role, CanonicalFixtureHTTP]]:
    settings = build_openai_settings(root, SecretStr("synthetic-key-never-real"))
    ledger = RecordingLedger(root.parent / "usage")
    ledger.configure_period(PERIOD, cap)
    prices = PriceAssumptions(Decimal(100), NOW - timedelta(hours=1), NOW + timedelta(days=1))
    http = {role: CanonicalFixtureHTTP(role, ledger, fault=fault) for role in OPENAI_ROLES}
    bounded = {
        role: BudgetedOpenAITransport(raw, ledger, role, prices, clock=lambda: NOW)
        for role, raw in http.items()
    }
    return settings, build_openai_providers(settings, bounded), ledger, http


async def call_role(providers: DesktopProviders, role: Role) -> object:
    provider = getattr(providers, ROLE_FIELDS[role])
    if role == Role.CONVERSATION:
        return await provider.generate(provider_request())
    if role == Role.EMBEDDING:
        return await provider.embed(
            EmbeddingRequest(1, "integration-embedding", ("Synthetic input.",))
        )
    kind = (
        "affect"
        if role == Role.APPRAISAL
        else "reflection1"
        if role == Role.REFLECTION
        else role.value
    )
    request = case(kind)[0]
    return await provider.generate_structured(request)


def reject_local(*args: object, **kwargs: object) -> None:
    raise AssertionError("unadmitted_local_or_raw_transport_constructed")


def deny_raw_transports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DesktopBackend, "_local_providers", reject_local)
    monkeypatch.setattr(OllamaHttpClient, "__init__", reject_local)
    monkeypatch.setattr(OllamaHttpClient, "post_json", reject_local)
    monkeypatch.setattr(OpenAIHttpClient, "__init__", reject_local)


def test_all_nine_real_ports_dispatch_only_after_budget_reservation_and_survive_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deny_raw_transports(monkeypatch)
    _, providers, ledger, http = composition(tmp_path.resolve() / "installation")
    ids = []
    try:

        async def exercise() -> None:
            for role in ROLE_FIELDS:
                response = await call_role(providers, role)
                if role == Role.EMBEDDING:
                    assert isinstance(response, EmbeddingResponse)
                    assert response.space.provider == "openai"
                else:
                    assert getattr(response, "provider", None) == "openai"

        asyncio.run(exercise())
        rows = ledger.records()
        assert len(rows) == 9
        assert {row.spec.role for row in rows} == OPENAI_ROLES
        assert all(row.state == AttemptState.SETTLED for row in rows)
        assert all(row.spec.provider == Provider.OPENAI for row in rows)
        assert all(
            row.input_tokens == (10 if row.spec.role == Role.EMBEDDING else 100) for row in rows
        )
        assert all(
            row.output_tokens == (0 if row.spec.role == Role.EMBEDDING else 20) for row in rows
        )
        assert all(len(raw.calls) == 1 for raw in http.values())
        before = ledger.snapshot(PERIOD)
        assert before.unsettled_attempts == 0
        assert before.settled_reported_micro_rub > 0
        assert before.provider_invoice_verified is False
        ids = [row.spec.attempt_id for row in rows]
    finally:
        ledger.close()
    restarted = UsageLedger(tmp_path.resolve() / "usage", clock=lambda: NOW)
    try:
        assert restarted.snapshot(PERIOD) == before
        assert all(restarted.reconcile(attempt).state == AttemptState.SETTLED for attempt in ids)
    finally:
        restarted.close()


def test_canonical_headless_multiturn_restart_is_budgeted_without_local_model_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deny_raw_transports(monkeypatch)
    root = tmp_path.resolve() / "installation"
    root.mkdir(mode=0o700)
    settings, providers, ledger, http = composition(root)
    preparation = DesktopBackend(root, REPOSITORY, providers=providers, runtime_settings=settings)
    preparation.open()
    identity = preparation.activate()["identity_id"]
    session = str(preparation.new_session()["session_id"])
    asyncio.run(preparation.close())
    assert ledger.records() == []

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers, runtime_settings=settings)
        await runtime.start()
        assert ledger.records() == []
        first = await runtime.execute(
            HeadlessCommand(operation="send", session_id=session, request_id="first", text="Hello.")
        )
        second = await runtime.execute(
            HeadlessCommand(
                operation="send",
                session_id=session,
                request_id="second",
                text="How is this test going?",
            )
        )
        assert first["text"] != second["text"]
        assert first["provider"] == second["provider"] == "openai"
        await runtime.close()
        previous_count = len(http[Role.CONVERSATION].calls)
        restored = HeadlessRuntime(root, REPOSITORY, providers=providers, runtime_settings=settings)
        await restored.start()
        receipt = await restored.execute(
            HeadlessCommand(operation="reconcile", session_id=session, request_id="second")
        )
        assert receipt["state"] == "completed"
        assert receipt["interaction_id"] == second["interaction_id"]
        history = await restored.execute(HeadlessCommand(operation="history", session_id=session))
        interactions = history["interactions"]
        assert isinstance(interactions, list)
        assert len(interactions) == 2
        await restored.close()
        assert len(http[Role.CONVERSATION].calls) == previous_count

    try:
        asyncio.run(scenario())
        assert http[Role.APPRAISAL].calls
        assert http[Role.EPISODE].calls
        assert all(row.state == AttemptState.SETTLED for row in ledger.records())
        assert ledger.snapshot(PERIOD).unsettled_attempts == 0
        accounting_bytes = (tmp_path.resolve() / "usage" / "usage.db").read_bytes()
        assert b"synthetic-key-never-real" not in accounting_bytes
        assert b"How is this test going?" not in accounting_bytes
        assert b"Integration reply" not in accounting_bytes
        readback = DesktopBackend(root, REPOSITORY, providers=providers, runtime_settings=settings)
        try:
            assert readback.open()["identity_id"] == identity
        finally:
            asyncio.run(readback.close())
    finally:
        ledger.close()


@pytest.mark.parametrize("role", list(ROLE_FIELDS))
def test_zero_model_budget_prevents_every_role_transport(role: Role, tmp_path: Path) -> None:
    _, providers, ledger, http = composition(tmp_path.resolve() / "installation", cap=0)
    error = (
        ConversationProviderError
        if role == Role.CONVERSATION
        else EmbeddingProviderError
        if role == Role.EMBEDDING
        else case(
            "affect"
            if role == Role.APPRAISAL
            else "reflection1"
            if role == Role.REFLECTION
            else role.value
        )[4]
    )
    try:
        with pytest.raises(error):
            asyncio.run(call_role(providers, role))
        assert not any(raw.calls for raw in http.values())
        assert ledger.records() == []
        assert ledger.snapshot(PERIOD).liability_micro_rub == 0
    finally:
        ledger.close()


@pytest.mark.parametrize("fault", ["timeout", "missing_usage"])
@pytest.mark.parametrize("role", [Role.CONVERSATION, Role.EMBEDDING])
def test_uncertain_http_or_usage_retains_liability_without_retry(
    fault: str,
    role: Role,
    tmp_path: Path,
) -> None:
    _, providers, ledger, http = composition(tmp_path.resolve() / "installation", fault=fault)
    error = ConversationProviderError if role == Role.CONVERSATION else EmbeddingProviderError
    try:
        with pytest.raises(error) as failure:
            asyncio.run(call_role(providers, role))
        assert "private detail" not in str(failure.value)
        assert len(http[role].calls) == 1
        rows = ledger.records()
        assert len(rows) == 1
        assert rows[0].state == AttemptState.UNKNOWN
        held = rows[0].spec.reservation_micro_rub
        assert ledger.snapshot(PERIOD).liability_micro_rub == held > 0
        ledger.close()
        restarted = UsageLedger(tmp_path.resolve() / "usage", clock=lambda: NOW)
        try:
            assert restarted.snapshot(PERIOD).liability_micro_rub == held
            assert restarted.reconcile(rows[0].spec.attempt_id).state == AttemptState.UNKNOWN
            assert len(http[role].calls) == 1
        finally:
            restarted.close()
    finally:
        ledger.close()
