"""Real temporary SQLite lifecycle, owner exclusion and interrupted-wait checks."""

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from satori.core.conversation import ConversationProviderRequest, ConversationProviderResponse
from satori.core.episode import EpisodeFormationProviderError
from satori.core.models import ModelFormationProviderError
from satori.desktop.backend import DesktopBackend, DesktopProviders
from satori.server.runtime import HeadlessCommand, HeadlessError, HeadlessRuntime
from tests.fakes import (
    FakeConversationProvider,
    FakeEpisodeFormationProvider,
    FakeModelFormationProvider,
)
from tests.test_conversation import skip_episode_provider, success_response

REPOSITORY = Path(__file__).resolve().parents[1]


def prepared(tmp_path: Path, *, activate: bool = True) -> tuple[Path, DesktopProviders, str, str]:
    root = tmp_path.resolve() / "installation"
    root.mkdir(mode=0o700)
    providers = DesktopProviders(
        FakeConversationProvider(response=success_response("Synthetic reply.")),
        skip_episode_provider(),
    )
    backend = DesktopBackend(root, REPOSITORY, providers=providers)
    backend.open()
    first = second = ""
    if activate:
        backend.activate()
        first = str(backend.new_session()["session_id"])
        second = str(backend.new_session()["session_id"])
    asyncio.run(backend.close())
    return root, providers, first, second


def test_real_store_send_restart_reconcile_and_session_isolation(tmp_path: Path) -> None:
    root, providers, first, second = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        assert runtime.metadata()["background_processing"] == "not_observed"
        assert (await runtime.start())["state"] == "ready"
        assert runtime.metadata()["background_processing"] == "not_observed"
        reply = await runtime.execute(
            HeadlessCommand(
                operation="send", session_id=first, request_id="one", text="Synthetic greeting."
            )
        )
        assert reply["text"] == "Synthetic reply."
        assert not (await runtime.execute(HeadlessCommand(operation="history", session_id=second)))[
            "interactions"
        ]
        closed = await runtime.close()
        assert closed["state"] == "closed"
        assert closed["background_processing"] == "complete"
        assert closed["post_response_failures"] == []
        again = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await again.start()
        receipt = await again.execute(
            HeadlessCommand(operation="reconcile", session_id=first, request_id="one")
        )
        assert receipt["state"] == "completed"
        assert receipt["interaction_id"] == reply["interaction_id"]
        await again.close()
        assert isinstance(providers.conversation, FakeConversationProvider)
        assert len(providers.conversation.requests) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("problem", ["missing", "old", "unactivated", "missing_policy"])
def test_start_never_initializes_upgrades_or_repairs(tmp_path: Path, problem: str) -> None:
    root, providers, _, _ = prepared(tmp_path, activate=problem != "unactivated")
    database = root / "satori.db"
    if problem == "missing":
        database.unlink()
    elif problem == "old":
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE alembic_version SET version_num='old-revision'")
    elif problem == "missing_policy":
        (root / "source-policy").rename(root / "preserved-policy")
    before = hashlib.sha256(database.read_bytes()).hexdigest() if database.exists() else None

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        with pytest.raises(HeadlessError):
            await runtime.start()
        assert runtime.metadata()["state"] == "closed"
        if problem == "missing_policy":
            assert not (root / "source-policy").exists()
        assert (await runtime.close())["state"] == "closed"

    asyncio.run(scenario())
    after = hashlib.sha256(database.read_bytes()).hexdigest() if database.exists() else None
    assert after == before


def test_exclusive_lease_rejects_second_owner_and_releases_on_close(tmp_path: Path) -> None:
    root, providers, _, _ = prepared(tmp_path)

    async def scenario() -> None:
        first = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await first.start()
        second = HeadlessRuntime(root, REPOSITORY, providers=providers)
        with pytest.raises(HeadlessError, match="owner_busy"):
            await second.start()
        # A different process sees the same kernel-held lease.
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import fcntl,sys; f=open(sys.argv[1], 'r'); "
                    "fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)"
                ),
                str(root / ".satori-headless.lock"),
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert probe.returncode != 0
        await first.close()
        await second.start()
        await second.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "problem", ["root_link", "database_link", "lock_link", "root_mode", "dangling_sidecar"]
)
def test_private_paths_reject_symlinks_and_permissive_root(tmp_path: Path, problem: str) -> None:
    root, providers, _, _ = prepared(tmp_path)
    if problem == "root_link":
        link = root.parent / "linked"
        link.symlink_to(root, target_is_directory=True)
        root = link
    elif problem == "database_link":
        database = root / "satori.db"
        database.rename(root / "actual.db")
        database.symlink_to(root / "actual.db")
    elif problem == "lock_link":
        target = root / "unrelated"
        target.write_text("unchanged")
        (root / ".satori-headless.lock").symlink_to(target)
    elif problem == "root_mode":
        root.chmod(0o755)
    else:
        (root / "satori.db-wal").symlink_to(root / "missing-sidecar")

    async def scenario() -> None:
        with pytest.raises(HeadlessError):
            await HeadlessRuntime(root, REPOSITORY, providers=providers).start()

    asyncio.run(scenario())
    if problem == "lock_link":
        assert (root / "unrelated").read_text() == "unchanged"


class WaitingProvider(FakeConversationProvider):
    def __init__(self) -> None:
        super().__init__(response=success_response("Synthetic reply."))
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        self.entered.set()
        await self.release.wait()
        return await super().generate(request)


def test_cancelled_wait_keeps_lane_and_lease_until_actual_drain(tmp_path: Path) -> None:
    root, _, first, _ = prepared(tmp_path)

    async def scenario() -> None:
        provider = WaitingProvider()
        providers = DesktopProviders(provider, skip_episode_provider())
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        caller = asyncio.create_task(
            runtime.execute(
                HeadlessCommand(
                    operation="send",
                    session_id=first,
                    request_id="interrupted",
                    text="Synthetic text.",
                )
            )
        )
        await asyncio.wait_for(provider.entered.wait(), 5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        with pytest.raises(HeadlessError, match="busy"):
            await runtime.execute(HeadlessCommand(operation="status"))
        closer = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        closer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closer
        other = HeadlessRuntime(root, REPOSITORY, providers=providers)
        with pytest.raises(HeadlessError, match="owner_busy"):
            await other.start()
        provider.release.set()
        assert (await runtime.close())["state"] == "closed"
        await other.start()
        result = await other.execute(
            HeadlessCommand(operation="reconcile", session_id=first, request_id="interrupted")
        )
        assert result["state"] == "completed"
        assert len(provider.requests) == 1
        await other.close()

    asyncio.run(scenario())


def test_replaced_lock_rejects_commands_and_never_claims_clean_drain(tmp_path: Path) -> None:
    root, providers, _, _ = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        lock = root / ".satori-headless.lock"
        lock.rename(root / "original-lock")
        lock.touch(mode=0o600)
        with pytest.raises(HeadlessError, match="storage_changed"):
            await runtime.execute(HeadlessCommand(operation="status"))
        with pytest.raises(HeadlessError, match="drain_unknown"):
            await runtime.close()
        assert runtime.metadata()["state"] == "unknown"
        # Test-only descriptor cleanup, after backend drain; production keeps it
        # until process exit rather than asserting a newly created lock is owned.
        runtime._lease.release()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["activate", "restore", "new_session", "resume_session"])
def test_unsupported_effects_cannot_be_expressed(operation: str) -> None:
    with pytest.raises(ValidationError):
        HeadlessCommand.model_validate({"operation": operation})


def test_cli_check_is_metadata_only_and_does_not_import_qt(tmp_path: Path) -> None:
    root, _, _, _ = prepared(tmp_path)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from satori.server.__main__ import main; "
                "assert not any(n.startswith('PySide6') for n in sys.modules); "
                "raise SystemExit(main(sys.argv[1:]))"
            ),
            "check",
            "--data-directory",
            str(root),
            "--repository-directory",
            str(REPOSITORY),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
        env={**os.environ, "SATORI_OPENAI_API_KEY": "must-not-leak"},
    )
    assert probe.returncode == 0
    assert probe.stderr == ""
    result = json.loads(probe.stdout)
    assert result == {
        "state": "closed",
        "network_listener": False,
        "migration_enabled": False,
        "activation_enabled": False,
        "command_pending": False,
        "background_processing": "complete",
        "post_response_failures": [],
    }
    assert "must-not-leak" not in probe.stdout
    assert str(root) not in probe.stdout


def test_failed_background_providers_are_partial_with_closed_owner_and_released_lease(
    tmp_path: Path,
) -> None:
    root, providers, session, _ = prepared(tmp_path)
    episode = FakeEpisodeFormationProvider(
        error=EpisodeFormationProviderError("synthetic", "test", "PRIVATE_EPISODE_FAILURE")
    )
    models = FakeModelFormationProvider(
        error=ModelFormationProviderError("synthetic", "test", "PRIVATE_MODEL_FAILURE")
    )
    providers = replace(providers, episode=episode, models=models)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        reply = await runtime.execute(
            HeadlessCommand(
                operation="send",
                session_id=session,
                request_id="background-failure",
                text="I started a new project today.",
            )
        )
        assert reply["text"] == "Synthetic reply."
        assert runtime.metadata()["background_processing"] == "not_observed"
        closed = await runtime.close()
        assert closed["state"] == "closed"
        assert closed["background_processing"] == "partial"
        assert closed["post_response_failures"] == ["current_models", "episode_formation"]
        assert episode.requests
        assert models.requests
        assert "PRIVATE_" not in json.dumps(closed)
        assert await runtime.close() == closed
        other = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await other.start()
        receipt = await other.execute(
            HeadlessCommand(
                operation="reconcile",
                session_id=session,
                request_id="background-failure",
            )
        )
        assert receipt["state"] == "completed"
        await other.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phases", [["PRIVATE_UNKNOWN_PHASE"], "PRIVATE_BAD_REPORT", None])
def test_unknown_background_metadata_is_concealed_without_claiming_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phases: object,
) -> None:
    root, providers, _, _ = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        assert runtime._backend is not None
        original = runtime._backend.close

        async def changed_report() -> dict[str, object]:
            await original()
            return {"closed": True, "post_response_failures": phases}

        monkeypatch.setattr(runtime._backend, "close", changed_report)
        closed = await runtime.close()
        assert closed["state"] == "closed"
        assert closed["background_processing"] == "not_observed"
        assert closed["post_response_failures"] == []
        assert "PRIVATE_" not in json.dumps(closed)

    asyncio.run(scenario())
