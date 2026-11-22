"""Offline desktop composition, identity/replay and private-wire regression checks."""

import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest

from satori.core.conversation import (
    ConversationProviderFailureReason,
    ProviderUnavailable,
)
from satori.core.episode import EpisodeFormationProviderResponse, EpisodeFormationRequest
from satori.desktop.backend import DesktopBackend, DesktopProviders
from satori.desktop.voice.expression import VoiceDelivery
from satori.desktop.worker import WorkerRequest, run_worker
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from tests.fakes import FakeConversationProvider, FakeEpisodeFormationProvider
from tests.test_conversation import skip_episode_provider, success_response

REPOSITORY = Path(__file__).resolve().parents[1]


def make_backend(
    directory: Path,
) -> tuple[DesktopBackend, FakeConversationProvider, FakeEpisodeFormationProvider]:
    conversation = FakeConversationProvider(response=success_response("Привет."))
    episode = skip_episode_provider()
    backend = DesktopBackend(
        directory,
        REPOSITORY,
        providers=DesktopProviders(conversation, episode),
    )
    return backend, conversation, episode


def test_open_is_nonactivating_and_ignores_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "openai")
    monkeypatch.setenv("SATORI_OPENAI_API_KEY", "must-not-be-read")
    monkeypatch.setenv("SATORI_CONVERSATION_REPAIR_INTERPRETATION_ENABLED", "true")
    backend, conversation, episode = make_backend(tmp_path)
    try:
        assert backend.open()["activated"] is False
        assert backend.settings.conversation_provider.value == "ollama"
        assert backend.settings.openai_api_key is None
        assert backend.settings.conversation_repair_interpretation_enabled is False
        assert not conversation.requests
        assert not episode.requests
    finally:
        asyncio.run(backend.close())


def test_committed_reply_replay_background_and_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend, conversation, episode = make_backend(tmp_path)
        assert backend.open()["activated"] is False
        identity = backend.activate()["identity_id"]
        session = str(backend.new_session()["session_id"])
        first = await backend.send("Привет", "desktop-request-1")
        replay = await backend.send("Привет", "desktop-request-1")
        assert first["interaction_id"] == replay["interaction_id"]
        assert replay["replayed"] is True
        assert first["client_request_id"] == "desktop-request-1"
        delivery = VoiceDelivery.model_validate_json(json.dumps(first["voice_delivery"]))
        assert delivery.availability == "selected"
        assert replay["voice_delivery"] is None
        assert len(conversation.requests) == 1
        assert backend.history(session)["interactions"]
        assert backend.memories() == {"memories": [], "truncated": False}
        result = await backend.close()
        assert result["closed"] is True
        assert len(episode.requests) == 1
        restarted, new_conversation, new_episode = make_backend(tmp_path)
        try:
            assert restarted.open()["identity_id"] == identity
            assert restarted.resume_session(session) == {"session_id": session}
            assert len(restarted.history(session)["interactions"]) == 1  # type: ignore[arg-type]
            assert not new_conversation.requests
            assert not new_episode.requests
        finally:
            await restarted.close()

    asyncio.run(scenario())


def test_missing_existing_ledger_is_not_automatically_enrolled(tmp_path: Path) -> None:
    backend, _, _ = make_backend(tmp_path)
    backend.open()
    backend.activate()
    asyncio.run(backend.close())
    ledger = tmp_path / "source-policy"
    moved = tmp_path / "preserved-policy"
    ledger.rename(moved)
    reopened, _, _ = make_backend(tmp_path)
    try:
        with pytest.raises(SourcePolicyError):
            reopened.open()
        with pytest.raises(ValueError, match="runtime_not_open"):
            reopened.status()
        assert not ledger.exists() or not list(ledger.rglob("*.json"))
    finally:
        asyncio.run(reopened.close())


def test_worker_protocol_reports_validation_without_echoing_text(tmp_path: Path) -> None:
    backend, conversation, _ = make_backend(tmp_path)
    commands = [
        {"id": "open", "command": "initialize"},
        {"id": "bad", "command": "send", "data": {"text": "private-secret"}},
        {"id": "activate", "command": "activate"},
        {"id": "history", "command": "history"},
        {"id": "stop", "command": "shutdown"},
    ]
    source = StringIO("".join(json.dumps(command) + "\n" for command in commands))
    output = StringIO()
    assert asyncio.run(run_worker(backend, source, output)) == 0
    assert "private-secret" not in output.getvalue()
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(responses) == 5
    assert responses[1]["event"] == "error"
    assert responses[1]["id"] == "bad"
    assert responses[-1]["data"]["closed"] is True
    assert not conversation.requests


def test_worker_rejects_extra_command_data() -> None:
    with pytest.raises(ValueError, match="unexpected_command_data"):
        WorkerRequest.model_validate(
            {"id": "r", "command": "status", "data": {"text": "not-a-user-turn"}}
        )


def test_worker_rejects_unknown_wire_version() -> None:
    with pytest.raises(ValueError, match="protocol_version"):
        WorkerRequest.model_validate({"id": "r", "command": "status", "protocol_version": 2})


def test_failed_enrollment_preserves_identity_and_invalidates_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_enrollment(*args: object, **kwargs: object) -> None:
        raise OSError("injected_enrollment_failure")

    monkeypatch.setattr("satori.desktop.backend.enroll_installation", fail_enrollment)
    backend, _, _ = make_backend(tmp_path)
    backend.open()
    try:
        with pytest.raises(OSError, match="injected_enrollment_failure"):
            backend.activate()
        with pytest.raises(ValueError, match="runtime_not_open"):
            backend.status()
        # The same runtime now treats this as an existing identity, requiring its
        # real ledger. It never retries new-installation enrollment or erases self.
        with pytest.raises(SourcePolicyError):
            backend.open()
        assert (tmp_path / "satori.db").is_file()
        assert not (tmp_path / "source-policy").exists()
    finally:
        asyncio.run(backend.close())


def test_default_local_composition_performs_no_network_before_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("unexpected_provider_call")

    monkeypatch.setattr(OllamaHttpClient, "post_json", forbidden)
    backend = DesktopBackend(tmp_path, REPOSITORY)
    try:
        backend.open()
        backend.activate()
        assert backend.history()["sessions"] == []
        assert backend.memories()["memories"] == []
        assert backend.new_session()["session_id"]
    finally:
        asyncio.run(backend.close())


def test_provider_failure_is_durable_and_same_request_can_recover(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend, provider, _ = make_backend(tmp_path)
        provider.error = ProviderUnavailable(
            "fake",
            "fixture",
            "private-provider-message",
            reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
        )
        backend.open()
        backend.activate()
        try:
            with pytest.raises(ProviderUnavailable, match="private-provider-message"):
                await backend.send("Привет", "retry-request")
            failed = backend.history()["interactions"]
            assert isinstance(failed, list)
            assert failed[0]["status"] == "failed"
            assert failed[0]["assistant_text"] is None
            provider.error = None
            reply = await backend.send("Привет", "retry-request")
            assert reply["replayed"] is False
            completed = backend.history()["interactions"]
            assert isinstance(completed, list)
            assert len(completed) == 1
            assert completed[0]["status"] == "completed"
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_shutdown_drains_background_owner_without_cancelling_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        class BlockingEpisode(FakeEpisodeFormationProvider):
            async def generate_structured(
                self, request: EpisodeFormationRequest, /
            ) -> EpisodeFormationProviderResponse:
                started.set()
                await release.wait()
                return await super().generate_structured(request)

        provider = FakeConversationProvider(response=success_response("Привет."))
        episode = BlockingEpisode(response=skip_episode_provider().response)
        backend = DesktopBackend(
            tmp_path, REPOSITORY, providers=DesktopProviders(provider, episode)
        )
        backend.open()
        backend.activate()
        reply = await backend.send("Привет", "drain-request")
        assert reply["text"] == "Привет."
        await asyncio.wait_for(started.wait(), timeout=10.0)
        closing = asyncio.create_task(backend.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        result = await closing
        assert result["closed"] is True
        assert len(episode.requests) == 1

    asyncio.run(scenario())


def test_scoped_history_keeps_sidebar_and_filters_before_delivery_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("satori.desktop.backend.MAX_HISTORY_INTERACTIONS", 1)

    async def scenario() -> None:
        backend, _, _ = make_backend(tmp_path)
        backend.open()
        backend.activate()
        old_session = str(backend.new_session()["session_id"])
        try:
            await backend.send("Привет", "old-session-request")
            new_session = str(backend.new_session()["session_id"])
            await backend.send("Привет", "new-session-request")
            global_history = backend.history()
            assert global_history["truncated"] is True
            all_items = global_history["interactions"]
            assert isinstance(all_items, list)
            assert all_items[0]["session_id"] == new_session
            selected = backend.history(old_session)
            sessions = selected["sessions"]
            items = selected["interactions"]
            assert isinstance(sessions, list)
            assert isinstance(items, list)
            assert len(sessions) == 2
            assert len(items) == 1
            assert items[0]["session_id"] == old_session
            assert selected["truncated"] is False
        finally:
            await backend.close()

    asyncio.run(scenario())
