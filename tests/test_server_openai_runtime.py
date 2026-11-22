"""All-role wiring and real temporary Core execution with offline Responses transports."""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import SecretStr, ValidationError

from satori.config import ConversationProviderKind, EmbeddingProviderKind, Settings
from satori.desktop.backend import DesktopBackend, DesktopBackendError, DesktopProviders
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from satori.infrastructure.providers.openai import OpenAIConversationAdapter
from satori.infrastructure.providers.openai_embedding import OpenAIEmbeddingAdapter
from satori.infrastructure.providers.openai_roles import (
    OpenAIAffectiveAppraisalAdapter,
    OpenAIEpisodeFormationAdapter,
    OpenAIModelFormationAdapter,
    OpenAIPositionFormationAdapter,
    OpenAIReflectionAdapter,
    OpenAIRelationshipAppraisalAdapter,
    OpenAISemanticFormationAdapter,
)
from satori.server.openai_runtime import (
    OPENAI_ROLES,
    OpenAIRuntimeConfigurationError,
    build_openai_providers,
    build_openai_settings,
)
from satori.server.runtime import HeadlessCommand, HeadlessRuntime
from satori.server.usage import Role
from tests.fakes import FakeConversationProvider
from tests.test_conversation import skip_episode_provider, success_response

REPOSITORY = Path(__file__).resolve().parents[1]
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


class SyntheticTransport:
    """A role-bound finite fixture; it never contacts a provider or stores keys."""

    def __init__(self, role: Role) -> None:
        self.role = role
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

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
        self.calls.append((path, dict(payload)))
        if path == "/embeddings":
            dimensions = payload["dimensions"]
            return json.dumps(
                {
                    "object": "list",
                    "model": "text-embedding-3-small",
                    "data": [
                        {"object": "embedding", "index": index, "embedding": [1.0] * dimensions}
                        for index, _ in enumerate(payload["input"])
                    ],
                    "usage": {"prompt_tokens": 10, "total_tokens": 10},
                }
            ).encode()
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
        }
        text = (
            "Synthetic reply."
            if self.role == Role.CONVERSATION
            else json.dumps(documents[self.role])
        )
        return json.dumps(
            {
                "model": "gpt-6-luna",
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
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            }
        ).encode()


def transports() -> dict[Role, SyntheticTransport]:
    return {role: SyntheticTransport(role) for role in OPENAI_ROLES}


def settings(root: Path) -> Settings:
    return build_openai_settings(root.resolve(), SecretStr("synthetic-private-key"))


def build(root: Path) -> tuple[Settings, DesktopProviders, dict[Role, SyntheticTransport]]:
    selected = settings(root)
    adapters = transports()
    return selected, build_openai_providers(selected, adapters), adapters


def test_settings_are_complete_explicit_and_ignore_environment_and_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SATORI_OPENAI_API_KEY=PRIVATE_DOTENV\n")
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "ollama")
    monkeypatch.setenv("SATORI_OPENAI_API_KEY", "PRIVATE_ENVIRONMENT")
    monkeypatch.setenv("SATORI_CONVERSATION_CURRENT_POSITION_ENABLED", "true")
    result = settings(tmp_path)
    assert result.all_openai_enabled
    assert result.conversation_provider == ConversationProviderKind.OPENAI
    assert result.embedding_provider == EmbeddingProviderKind.OPENAI
    assert result.openai_api_key == SecretStr("synthetic-private-key")
    assert not result.conversation_current_position_enabled
    assert result.embedding_model == "text-embedding-3-small"
    assert result.embedding_dimensions == 1536
    assert "synthetic-private-key" not in repr(result)
    assert "PRIVATE_ENVIRONMENT" not in repr(result)


@pytest.mark.parametrize(
    "field",
    [
        "conversation_provider",
        "episode_formation_provider",
        "embedding_provider",
        "semantic_formation_provider",
        "affective_appraisal_provider",
        "relationship_appraisal_provider",
        "model_formation_provider",
        "position_formation_provider",
        "reflection_provider",
    ],
)
def test_all_openai_rejects_any_mixed_role(tmp_path: Path, field: str) -> None:
    values = settings(tmp_path).model_dump()
    values[field] = "ollama"
    with pytest.raises(ValidationError, match="all_openai_requires_all_nine_roles"):
        Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "change",
    [
        {"conversation_model": "different_model"},
        {"embedding_model": "embeddinggemma:300m"},
        {"embedding_dimensions": 1537},
        {"openai_reasoning_effort": "low"},
        {"openai_api_key": None},
        {"openai_api_key": SecretStr("synthetic\nkey")},
        {"openai_base_url": "https://example.invalid/v1"},
        {"embedding_provider_base_url": "http://127.0.0.1:11434"},
    ],
)
def test_candidate_models_endpoint_and_credentials_remain_pinned(
    tmp_path: Path, change: dict[str, object]
) -> None:
    values = settings(tmp_path).model_dump()
    values.update(change)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.mark.parametrize("omitted", list(OPENAI_ROLES))
def test_factory_cannot_drop_any_role_transport(tmp_path: Path, omitted: Role) -> None:
    supplied = transports()
    del supplied[omitted]
    with pytest.raises(OpenAIRuntimeConfigurationError):
        build_openai_providers(settings(tmp_path), supplied)


def test_factory_wires_each_port_to_exact_role_transport_without_io(tmp_path: Path) -> None:
    selected, providers, calls = build(tmp_path)
    expected = (
        OpenAIConversationAdapter,
        OpenAIEpisodeFormationAdapter,
        OpenAIEmbeddingAdapter,
        OpenAISemanticFormationAdapter,
        OpenAIAffectiveAppraisalAdapter,
        OpenAIRelationshipAppraisalAdapter,
        OpenAIModelFormationAdapter,
        OpenAIPositionFormationAdapter,
        OpenAIReflectionAdapter,
    )
    for (role, name), adapter_type in zip(ROLE_FIELDS.items(), expected, strict=True):
        candidate = getattr(providers, name)
        assert isinstance(candidate, adapter_type)
        provider = cast(Any, candidate)
        if role in {Role.CONVERSATION, Role.EMBEDDING}:
            assert provider.http_client is calls[role]
        else:
            assert provider.client.transport is calls[role]
        assert provider.model == (
            selected.embedding_model if role == Role.EMBEDDING else selected.conversation_model
        )
    assert len(fields(providers)) == 9
    assert all(not transport.calls for transport in calls.values())
    assert "synthetic-private-key" not in repr(providers)


def test_backend_requires_complete_injected_bundle_and_matching_paths(tmp_path: Path) -> None:
    selected, providers, _ = build(tmp_path)
    with pytest.raises(DesktopBackendError, match="all_openai_provider_bundle_required"):
        DesktopBackend(tmp_path, REPOSITORY, runtime_settings=selected)
    with pytest.raises(DesktopBackendError, match="all_openai_provider_bundle_required"):
        DesktopBackend(
            tmp_path,
            REPOSITORY,
            providers=replace(providers, semantic=None),
            runtime_settings=selected,
        )
    with pytest.raises(DesktopBackendError, match="runtime_settings_path_mismatch"):
        DesktopBackend(
            tmp_path / "other", REPOSITORY, providers=providers, runtime_settings=selected
        )


def test_backend_never_selects_ollama_and_reports_actual_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected, providers, calls = build(tmp_path)

    def reject_local(*args: object, **kwargs: object) -> None:
        raise AssertionError("local_provider_construction_forbidden")

    monkeypatch.setattr(DesktopBackend, "_local_providers", reject_local)
    monkeypatch.setattr(OllamaHttpClient, "__init__", reject_local)
    backend = DesktopBackend(
        tmp_path, REPOSITORY, "ignored-local-model", providers=providers, runtime_settings=selected
    )

    async def scenario() -> None:
        try:
            status = backend.open()
            assert status["provider"] == "openai"
            assert status["model"] == "gpt-6-luna"
            assert backend.settings.database_url == selected.database_url
            backend.activate()
            backend.new_session()
            reply = await backend.send("Hello.", "synthetic-request")
            assert reply["text"] == "Synthetic reply."
            assert calls[Role.CONVERSATION].calls
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_headless_core_uses_all_openai_configuration_and_reconciles_after_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve() / "installation"
    root.mkdir(mode=0o700)
    preparation = DesktopBackend(
        root,
        REPOSITORY,
        providers=DesktopProviders(
            FakeConversationProvider(response=success_response("Synthetic preparation.")),
            skip_episode_provider(),
        ),
    )
    preparation.open()
    identity = preparation.activate()["identity_id"]
    session = str(preparation.new_session()["session_id"])
    asyncio.run(preparation.close())
    selected, providers, calls = build(root)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers, runtime_settings=selected)
        assert (await runtime.start())["state"] == "ready"
        result = await runtime.execute(
            HeadlessCommand(
                operation="send", session_id=session, request_id="cloud-one", text="Hello."
            )
        )
        assert result["text"] == "Synthetic reply."
        await runtime.close()
        restarted = HeadlessRuntime(
            root, REPOSITORY, providers=providers, runtime_settings=selected
        )
        await restarted.start()
        receipt = await restarted.execute(
            HeadlessCommand(operation="reconcile", session_id=session, request_id="cloud-one")
        )
        assert receipt["state"] == "completed"
        assert receipt["interaction_id"] == result["interaction_id"]
        await restarted.close()

    asyncio.run(scenario())
    assert len(calls[Role.CONVERSATION].calls) == 1
    assert calls[Role.APPRAISAL].calls
    assert calls[Role.EPISODE].calls
    readback = DesktopBackend(root, REPOSITORY, providers=providers, runtime_settings=selected)
    try:
        assert readback.open()["identity_id"] == identity
    finally:
        asyncio.run(readback.close())


def test_factory_revalidates_copied_settings_and_refuses_extra_transports(tmp_path: Path) -> None:
    selected = settings(tmp_path)
    forged = selected.model_copy(
        update={"episode_formation_provider": ConversationProviderKind.OLLAMA}
    )
    with pytest.raises(OpenAIRuntimeConfigurationError):
        build_openai_providers(forged, transports())
    supplied = transports()
    supplied[Role.TTS] = SyntheticTransport(Role.TTS)
    with pytest.raises(OpenAIRuntimeConfigurationError):
        build_openai_providers(selected, supplied)


def test_factory_embedding_has_explicit_openai_space_and_bound_transport(tmp_path: Path) -> None:
    from satori.core.embedding import EmbeddingRequest

    selected, providers, calls = build(tmp_path)
    assert isinstance(providers.embedding, OpenAIEmbeddingAdapter)
    result = asyncio.run(
        providers.embedding.embed(
            EmbeddingRequest(
                schema_version=1,
                trace_id="synthetic-embedding",
                texts=("Synthetic lookup.",),
            )
        )
    )
    assert result.space.provider == "openai"
    assert result.space.model == selected.embedding_model
    assert result.space.dimensions == 1536
    assert len(result.vectors[0]) == 1536
    assert calls[Role.EMBEDDING].calls[0][0] == "/embeddings"
