"""Stage 3 one-turn CLI behavior with deterministic providers."""

import json
from pathlib import Path

import pytest

from satori.__main__ import main
from satori.config import Environment, LogLevel, Settings
from satori.core.conversation import ConversationProviderResponse, ProviderUnavailable
from satori.core.episode import EpisodeFormationProposal, EpisodeFormationProviderResponse
from tests.fakes import (
    FakeConversationProvider,
    FakeEmbeddingProvider,
    FakeEpisodeFormationProvider,
)


def cli_settings(sqlite_url: str) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=sqlite_url,
        log_level=LogLevel.WARNING,
    )


def skip_episode_provider() -> FakeEpisodeFormationProvider:
    return FakeEpisodeFormationProvider(
        response=EpisodeFormationProviderResponse(
            proposal=EpisodeFormationProposal(1, False, None, None, None, ()),
            provider="fake-episode",
            model="fixture",
            formation_method="fixture.v1",
        )
    )


def test_cli_talk_requires_explicit_activation(
    sqlite_url: str,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Talk fails concisely and leaves a fresh installation unactivated."""

    provider = FakeConversationProvider(
        response=ConversationProviderResponse("unused", "fake", "model", "stop")
    )
    settings = cli_settings(sqlite_url)

    result = main(
        ["talk", "Привет"],
        settings=settings,
        alembic_config=project_root / "alembic.ini",
        conversation_provider=provider,
        episode_formation_provider=skip_episode_provider(),
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == "Satori is not activated. Run `satori activate` first.\n"
    assert provider.requests == []


def test_cli_talk_prints_only_the_validated_reply(
    sqlite_url: str,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The simplest Stage 3 UX is one quoted message and one reply."""

    settings = cli_settings(sqlite_url)
    assert main(["activate"], settings=settings, alembic_config=project_root / "alembic.ini") == 0
    capsys.readouterr()
    provider = FakeConversationProvider(
        response=ConversationProviderResponse(
            "Привет. Рада наконец заговорить.",
            "fake",
            "fixture",
            "stop",
        )
    )

    result = main(
        ["talk", "Привет, Сатори"],
        settings=settings,
        alembic_config=project_root / "alembic.ini",
        conversation_provider=provider,
        episode_formation_provider=skip_episode_provider(),
    )

    assert result == 0
    assert capsys.readouterr().out == "Привет. Рада наконец заговорить.\n"
    assert provider.requests[0].messages[-1].content == "Привет, Сатори"


def test_cli_provider_failure_has_no_traceback(
    sqlite_url: str,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unavailable local inference is normal typed UX, not an internal traceback."""

    settings = cli_settings(sqlite_url)
    assert main(["activate"], settings=settings, alembic_config=project_root / "alembic.ini") == 0
    capsys.readouterr()
    provider = FakeConversationProvider(error=ProviderUnavailable("ollama", "fixture", "offline"))

    result = main(
        ["talk", "Привет"],
        settings=settings,
        alembic_config=project_root / "alembic.ini",
        conversation_provider=provider,
        episode_formation_provider=skip_episode_provider(),
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    structured = next(
        json.loads(line)
        for line in lines
        if json.loads(line).get("message") == "conversation_failed"
    )
    assert structured["message"] == "conversation_failed"
    assert structured["fields"]["provider"] == "ollama"
    assert structured["fields"]["model"] == "fixture"
    assert lines[-1] == "Conversation unavailable (ollama/fixture): offline"
    assert "Traceback" not in captured.err
    assert "Привет" not in captured.err


def test_cli_history_survives_separate_process_style_invocations(
    sqlite_url: str,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Talk and later history commands prove durable exact dialogue without internals."""

    active_settings = cli_settings(sqlite_url)
    assert (
        main(
            ["activate"],
            settings=active_settings,
            alembic_config=project_root / "alembic.ini",
        )
        == 0
    )
    capsys.readouterr()
    provider = FakeConversationProvider(
        response=ConversationProviderResponse("Сохранила разговор.", "fake", "fixture", "stop")
    )
    assert (
        main(
            ["talk", "Это останется после restart", "--request-id", "cli-request-1"],
            settings=active_settings,
            alembic_config=project_root / "alembic.ini",
            conversation_provider=provider,
            episode_formation_provider=skip_episode_provider(),
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            ["history"],
            settings=active_settings,
            alembic_config=project_root / "alembic.ini",
            conversation_provider=provider,
            episode_formation_provider=skip_episode_provider(),
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "completed" in output
    assert "user: Это останется после restart" in output
    assert "assistant: Сохранила разговор." in output
    assert "Trusted Satori behavior policy" not in output


def test_cli_memory_index_and_search_expose_safe_debug_metadata(
    sqlite_url: str,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can backfill and inspect no-result retrieval without dumping vectors."""

    active_settings = cli_settings(sqlite_url)
    conversation = FakeConversationProvider(
        response=ConversationProviderResponse("unused", "fake", "fixture", "stop")
    )
    embedding = FakeEmbeddingProvider({"unknown topic": (1.0, 0.0, 0.0)})
    assert (
        main(
            ["memories", "index"],
            settings=active_settings,
            alembic_config=project_root / "alembic.ini",
            conversation_provider=conversation,
            episode_formation_provider=skip_episode_provider(),
            embedding_provider=embedding,
        )
        == 0
    )
    index_output = capsys.readouterr().out
    assert "considered=0 indexed=0 failed=0" in index_output

    assert (
        main(
            ["memories", "search", "unknown topic"],
            settings=active_settings,
            alembic_config=project_root / "alembic.ini",
            conversation_provider=conversation,
            episode_formation_provider=skip_episode_provider(),
            embedding_provider=embedding,
        )
        == 0
    )
    search_output = capsys.readouterr().out
    assert "status=no_relevant_memory candidates=0 selected=0" in search_output
    assert "[1.0" not in search_output
