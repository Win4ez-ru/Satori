"""Explicit production runtime ownership over private public fixtures and fake HTTP."""

import asyncio
import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from threading import Event
from typing import Any, Never
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from satori.core.conversation import ProviderUnavailable
from satori.desktop import openai_runtime as api
from satori.desktop.backend import DesktopBackend, DesktopBackendError
from satori.server.openai_runtime import OPENAI_ROLES
from satori.server.usage import UsageLedger
from tests.test_openai_provider import provider_request, response_body
from tests.test_server_runtime import REPOSITORY, prepared

NOW = datetime(2026, 11, 18, 20, tzinfo=UTC)
PUBLIC_KEY = "sk-public-fixture-not-a-real-secret-1234567890"


class PublicHTTP:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        assert path == "/responses"
        assert payload["model"] == "gpt-6-luna"
        assert payload["store"] is False
        assert timeout_seconds > 0
        assert max_response_bytes > 0
        self.calls.append(dict(payload))
        envelope = json.loads(response_body(text="PUBLIC API reply"))
        envelope["model"] = "gpt-6-luna"
        return json.dumps(envelope).encode()

    def close(self) -> None:
        self.closed = True


def configuration(root: Path, *, cap: int = 1_000_000_000) -> api.DesktopOpenAIConfig:
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    credential = root.parent / "public-credential.env"
    credential.write_text("SATORI_OPENAI_API_KEY=" + PUBLIC_KEY + "\n")
    credential.chmod(0o600)
    review = api.load_reviewed_desktop_pricing()
    return api.DesktopOpenAIConfig(
        profile_id=uuid5(NAMESPACE_URL, str(root)),
        credential=api.CredentialReference(path=credential),
        budget_period="2026-11",
        cap_micro_rub=cap,
        pricing_review_id=review.review_id,
        rub_per_usd_ceiling=review.rub_per_usd_ceiling,
        reviewed_at=review.reviewed_at,
        expires_at=review.expires_at,
        authorized=True,
    )


def legacy_configuration(config: api.DesktopOpenAIConfig) -> api.DesktopOpenAIConfig:
    previous = api.load_reviewed_desktop_pricing().compatible_reviews[0]
    return api.DesktopOpenAIConfig.model_validate(
        {
            **config.model_dump(),
            "pricing_review_id": previous.review_id,
            "reviewed_at": previous.reviewed_at,
            "expires_at": previous.expires_at,
            "rub_per_usd_ceiling": previous.rub_per_usd_ceiling,
        }
    )


def test_pinned_compatible_review_preserves_authority_without_persisting_or_opening_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "profile"
    old = legacy_configuration(configuration(root, cap=300_000_000))
    old = old.model_copy(update={"budget_period": "2026-10"})
    path = root / api.CONFIG_NAME
    path.write_text(old.model_dump_json())
    path.chmod(0o600)
    before = path.read_bytes()

    def forbidden(*_args: object, **_kwargs: object) -> Never:
        pytest.fail("review compatibility must not read a key, open HTTP or a ledger")

    monkeypatch.setattr(api, "load_referenced_openai_key", forbidden)
    monkeypatch.setattr(api, "OpenAIHttpClient", forbidden)
    monkeypatch.setattr(api, "UsageLedger", forbidden)
    effective = api.load_openai_runtime_config(path, root)
    review = api.load_reviewed_desktop_pricing()
    assert effective.pricing_review_id == review.review_id
    assert effective.reviewed_at == review.reviewed_at
    assert effective.expires_at == review.expires_at
    assert effective.cap_micro_rub == old.cap_micro_rub
    assert effective.budget_period == old.budget_period
    assert effective.credential == old.credential
    assert effective.profile_id == old.profile_id
    assert path.read_bytes() == before
    assert list(root.iterdir()) == [path]
    with pytest.raises(api.DesktopOpenAIRuntimeError):
        api.DesktopOpenAIRuntime(root, path, clock=lambda: NOW, client_factory=forbidden).open()
    assert path.read_bytes() == before
    assert list(root.iterdir()) == [path]


@pytest.mark.parametrize(
    "field", ["pricing_review_id", "reviewed_at", "expires_at", "rub_per_usd_ceiling"]
)
def test_compatible_review_requires_exact_allowlisted_identity(tmp_path: Path, field: str) -> None:
    root = tmp_path / "profile"
    old = legacy_configuration(configuration(root))
    values = old.model_dump()
    if field == "pricing_review_id":
        values[field] = "unreviewed-but-similar"
    elif field == "rub_per_usd_ceiling":
        values[field] += 1
    else:
        values[field] += timedelta(seconds=1)
    path = root / api.CONFIG_NAME
    path.write_text(api.DesktopOpenAIConfig.model_validate(values).model_dump_json())
    path.chmod(0o600)
    with pytest.raises(api.DesktopOpenAIRuntimeError, match="price_review_mismatch"):
        api.load_openai_runtime_config(path, root)


def test_compatible_review_does_not_reset_existing_production_liability(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    config = configuration(root, cap=300_000_000)
    path = api.write_reviewed_openai_config(root, config)
    client = PublicHTTP()
    owner = api.DesktopOpenAIRuntime(
        root, path, clock=lambda: NOW, client_factory=lambda *_: client
    ).open()
    assert owner.providers is not None
    asyncio.run(owner.providers.conversation.generate(provider_request()))
    assert owner._ledger is not None
    before = owner._ledger.snapshot("2026-11")
    owner.close()
    path.write_text(legacy_configuration(config).model_dump_json())
    restarted = api.DesktopOpenAIRuntime(
        root, path, clock=lambda: NOW, client_factory=lambda *_: PublicHTTP()
    ).open()
    assert restarted._ledger is not None
    assert restarted._ledger.snapshot("2026-11") == before
    assert len(client.calls) == 1
    restarted.close()


def test_expired_pinned_review_stops_before_credentials_or_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "profile"
    config = configuration(root)
    expired = config.expires_at
    config = config.model_copy(update={"budget_period": expired.strftime("%Y-%m")})
    path = api.write_reviewed_openai_config(root, config)

    def forbidden(*_args: object, **_kwargs: object) -> Never:
        pytest.fail("expired review must stop before credential, HTTP or ledger access")

    monkeypatch.setattr(api, "load_referenced_openai_key", forbidden)
    monkeypatch.setattr(api, "UsageLedger", forbidden)
    with pytest.raises(api.DesktopOpenAIRuntimeError):
        api.DesktopOpenAIRuntime(root, path, clock=lambda: expired, client_factory=forbidden).open()
    assert not (root / api.USAGE_NAME).exists()


def test_review_resource_requires_pinned_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "PRICING_SHA256", "0" * 64)
    with pytest.raises(api.DesktopOpenAIRuntimeError, match="price_review_unavailable"):
        api.load_reviewed_desktop_pricing()


@pytest.mark.parametrize(
    "field",
    ["currency", "generation_input", "generation_model", "service_tier", "rub_per_usd_ceiling"],
)
def test_future_trusted_review_cannot_claim_compatibility_after_rate_contract_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    root = tmp_path / "profile"
    old = legacy_configuration(configuration(root))
    path = root / api.CONFIG_NAME
    path.write_text(old.model_dump_json())
    path.chmod(0o600)
    data = json.loads(
        files("satori.resources").joinpath("openai/desktop-pricing-v1.json").read_bytes()
    )
    data[field] = "151" if field == "rub_per_usd_ceiling" else "changed-rate-contract"
    resources = tmp_path / "public-resource"
    (resources / "openai").mkdir(parents=True)
    raw = json.dumps(data).encode()
    (resources / "openai" / "desktop-pricing-v1.json").write_bytes(raw)
    monkeypatch.setattr(api, "files", lambda _package: resources)
    monkeypatch.setattr(api, "PRICING_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(api.DesktopOpenAIRuntimeError, match="price_review_mismatch"):
        api.load_openai_runtime_config(path, root)


def test_constructor_and_inspection_never_read_key_or_open_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "profile"
    config = configuration(root)
    path = api.write_reviewed_openai_config(root, config)
    client = PublicHTTP()
    local_credential = root / ".env"
    local_credential.write_text("SATORI_OPENAI_API_KEY=" + PUBLIC_KEY + "\n")
    local_credential.chmod(0o600)

    def forbidden(_reference: object) -> object:
        pytest.fail("credential read before explicitly configured start")

    monkeypatch.setattr(api, "load_referenced_openai_key", forbidden)
    runtime = api.DesktopOpenAIRuntime(root, path, client_factory=lambda *_: client)
    assert api.inspect_existing_credential_reference(root) == api.CredentialReference(
        path=local_credential
    )
    assert api.inspect_openai_runtime_config(path, root)["configured"] is True
    assert client.calls == []
    assert not (root / api.USAGE_NAME).exists()
    runtime.close()


def test_explicit_open_nine_roles_zero_calls_then_real_budgeted_adapter(
    tmp_path: Path,
) -> None:
    root = tmp_path / "profile"
    config = configuration(root)
    path = api.write_reviewed_openai_config(root, config)
    client = PublicHTTP()
    owner = api.DesktopOpenAIRuntime(
        root,
        path,
        clock=lambda: NOW,
        client_factory=lambda *_: client,
    ).open()
    assert owner.providers is not None
    assert owner.settings is not None
    assert owner.settings.all_openai_enabled
    assert all(
        getattr(owner.providers, name) is not None for name in owner.providers.__dataclass_fields__
    )
    assert len(OPENAI_ROLES) == 9
    assert owner.status()["supports_source_answers"] is True
    assert client.calls == []
    assert owner._ledger is not None
    before = owner._ledger.snapshot("2026-11")
    assert before.liability_micro_rub == 0
    result = asyncio.run(owner.providers.conversation.generate(provider_request()))
    assert result.provider == "openai"
    assert result.model == "gpt-6-luna"
    after = owner._ledger.snapshot("2026-11")
    assert after.liability_micro_rub > 0
    assert len(client.calls) == 1
    assert PUBLIC_KEY not in repr(owner.settings)
    owner.close()
    assert client.closed
    assert (root / api.USAGE_NAME / "usage.db").exists()
    # Restart keeps the same production liability, never mints another trial.
    restarted = api.DesktopOpenAIRuntime(
        root,
        path,
        clock=lambda: NOW,
        client_factory=lambda *_: PublicHTTP(),
    ).open()
    assert restarted._ledger is not None
    assert restarted._ledger.snapshot("2026-11").liability_micro_rub == after.liability_micro_rub
    restarted.close()


def test_production_cap_is_not_reset_by_metadata_change(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    config = configuration(root)
    path = api.write_reviewed_openai_config(root, config)
    first = api.DesktopOpenAIRuntime(
        root,
        path,
        clock=lambda: NOW,
        client_factory=lambda *_: PublicHTTP(),
    ).open()
    first.close()
    api.write_reviewed_openai_config(root, config.model_copy(update={"cap_micro_rub": 500_000_000}))
    with pytest.raises(api.DesktopOpenAIRuntimeError):
        api.DesktopOpenAIRuntime(
            root,
            path,
            clock=lambda: NOW,
            client_factory=lambda *_: PublicHTTP(),
        ).open()
    ledger = UsageLedger(root / api.USAGE_NAME)
    assert ledger.snapshot("2026-11").cap_micro_rub == 1_000_000_000
    ledger.close()


@pytest.mark.parametrize(
    "problem", ["mode", "symlink", "hardlink", "absent_variable", "interpolation"]
)
def test_bad_explicit_credential_never_uses_ambient_key_or_contacts_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    root = tmp_path / "profile"
    config = configuration(root)
    path = api.write_reviewed_openai_config(root, config)
    key = config.credential.path
    monkeypatch.setenv("SATORI_OPENAI_API_KEY", PUBLIC_KEY)
    if problem == "mode":
        key.chmod(0o644)
    elif problem == "symlink":
        replacement = root.parent / "public-other.env"
        key.rename(replacement)
        key.symlink_to(replacement)
    elif problem == "hardlink":
        os.link(key, root.parent / "public-hardlink.env")
    elif problem == "absent_variable":
        key.write_text("OTHER=value\n")
    else:
        key.write_text("SATORI_OPENAI_API_KEY=${SATORI_OPENAI_API_KEY}\n")
    client = PublicHTTP()
    with pytest.raises(api.DesktopOpenAIRuntimeError):
        api.DesktopOpenAIRuntime(
            root, path, clock=lambda: NOW, client_factory=lambda *_: client
        ).open()
    assert client.calls == []
    assert not (root / api.USAGE_NAME).exists()


@pytest.mark.parametrize("change", ["profile", "review", "fx", "renewal"])
def test_metadata_cannot_forge_profile_or_refresh_tariff(tmp_path: Path, change: str) -> None:
    root = tmp_path / "profile"
    config = configuration(root)
    updates: dict[str, dict[str, Any]] = {
        "profile": {"profile_id": uuid4()},
        "review": {"pricing_review_id": "invented-review"},
        "fx": {"rub_per_usd_ceiling": config.rub_per_usd_ceiling + 1},
        "renewal": {
            "reviewed_at": config.reviewed_at + timedelta(days=1),
            "expires_at": config.expires_at + timedelta(days=1),
        },
    }
    with pytest.raises(api.DesktopOpenAIRuntimeError):
        api.write_reviewed_openai_config(root, config.model_copy(update=updates[change]))
    assert not (root / api.CONFIG_NAME).exists()


@pytest.mark.parametrize(
    "later", [datetime(2026, 11, 14, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)]
)
def test_capability_expires_without_tariff_or_period_renewal(
    tmp_path: Path, later: datetime
) -> None:
    root = tmp_path / "profile"
    config = configuration(root)
    path = api.write_reviewed_openai_config(root, config)
    clock = [NOW]
    client = PublicHTTP()
    owner = api.DesktopOpenAIRuntime(
        root,
        path,
        clock=lambda: clock[0],
        client_factory=lambda *_: client,
    ).open()
    assert owner.status()["supports_source_answers"] is True
    clock[0] = later
    assert owner.status()["supports_source_answers"] is False
    assert owner.providers is not None
    with pytest.raises(ProviderUnavailable):
        asyncio.run(owner.providers.conversation.generate(provider_request()))
    assert client.calls == []
    owner.close()


def test_actual_backend_facts_cannot_be_spoofed_by_model_name_and_close_drains(
    tmp_path: Path,
) -> None:
    root, _, _session, _ = prepared(tmp_path)
    local = DesktopBackend(root, REPOSITORY, model="gpt-6-luna")
    local.open()
    assert local.status()["provider"] == "ollama"
    assert local.status()["supports_source_answers"] is False
    asyncio.run(local.close())
    config = configuration(root)
    path = api.write_reviewed_openai_config(root, config)
    client = PublicHTTP()
    owner = api.DesktopOpenAIRuntime(
        root,
        path,
        clock=lambda: NOW,
        client_factory=lambda *_: client,
    ).open()
    backend = DesktopBackend(
        root,
        REPOSITORY,
        providers=owner.providers,
        runtime_settings=owner.settings,
        openai_runtime=owner,
    )
    backend.open()
    assert backend.status()["provider"] == "openai"
    assert backend.status()["model"] == "gpt-6-luna"
    assert backend.status()["supports_source_answers"] is True
    assert client.calls == []

    async def scenario() -> None:
        entered, finish = asyncio.Event(), asyncio.Event()

        async def owned_turn() -> dict[str, object]:
            entered.set()
            await finish.wait()
            return {}

        turn = asyncio.create_task(owned_turn())
        backend._turn_tasks.add(turn)
        turn.add_done_callback(backend._turn_tasks.discard)
        await entered.wait()
        closing = asyncio.create_task(backend.close())
        await asyncio.sleep(0)
        assert not client.closed
        finish.set()
        await turn
        backend._turn_tasks.discard(turn)
        await closing
        assert client.closed

    asyncio.run(scenario())
    assert PUBLIC_KEY not in repr(owner)
    with pytest.raises(DesktopBackendError):
        backend.open()


@pytest.mark.parametrize("stage", ["http", "accounting"])
@pytest.mark.parametrize("cancel_close", [False, True])
def test_cancelled_to_thread_request_keeps_resources_until_accounting_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    cancel_close: bool,
) -> None:
    root = tmp_path / "profile"
    config = configuration(root)
    path = api.write_reviewed_openai_config(root, config)
    entered, release = Event(), Event()
    client = PublicHTTP()
    owner = api.DesktopOpenAIRuntime(
        root,
        path,
        clock=lambda: NOW,
        client_factory=lambda *_: client,
    ).open()
    assert owner._ledger is not None
    ledger = owner._ledger
    if stage == "http":
        original = client.post_json

        def blocked_http(*args: Any, **kwargs: Any) -> bytes:
            entered.set()
            assert release.wait(10), "public fixture release missing"
            return original(*args, **kwargs)

        monkeypatch.setattr(client, "post_json", blocked_http)
    else:
        original_settle = ledger.settle_report

        def blocked_settle(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            assert release.wait(10), "public fixture accounting release missing"
            return original_settle(*args, **kwargs)

        monkeypatch.setattr(ledger, "settle_report", blocked_settle)

    async def scenario() -> None:
        assert owner.providers is not None
        providers = owner.providers
        generating = asyncio.create_task(providers.conversation.generate(provider_request()))
        assert await asyncio.to_thread(entered.wait, 5)
        assert owner._active_calls == 1
        assert ledger.snapshot("2026-11").unsettled_attempts == 1
        generating.cancel()
        with pytest.raises(asyncio.CancelledError):
            await generating
        closing = asyncio.create_task(owner.aclose())
        await asyncio.sleep(0)
        assert not client.closed
        assert owner.status()["supports_source_answers"] is False
        assert ledger.snapshot("2026-11").unsettled_attempts == 1
        if cancel_close:
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert not client.closed
            with pytest.raises(api.DesktopOpenAIRuntimeError, match="still_draining"):
                owner.close()
            assert not client.closed
            assert ledger.snapshot("2026-11").unsettled_attempts == 1
            closing = asyncio.create_task(owner.aclose())
        # Closing rejects admission before a second reservation/HTTP attempt.
        with pytest.raises(ProviderUnavailable):
            await providers.conversation.generate(provider_request())
        release.set()
        await closing
        assert client.closed
        assert owner._active_calls == 0

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    reopened = UsageLedger(root / api.USAGE_NAME)
    final = reopened.snapshot("2026-11")
    assert final.unsettled_attempts == 0
    assert final.settled_reported_micro_rub > 0
    assert final.liability_micro_rub == final.settled_reported_micro_rub
    reopened.close()
    assert len(client.calls) == 1
