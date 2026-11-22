"""Trusted pipe and explicit cloud-configuration checks with no model requests."""

import asyncio
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest
from pydantic import SecretStr

from satori.__main__ import main as legacy_main
from satori.server.__main__ import main, serve_private_stream
from satori.server.openai_runtime import build_openai_settings
from satori.server.runtime import HeadlessRuntime
from tests.test_server_runtime import REPOSITORY, prepared


@pytest.fixture(autouse=True)
def restore_process_logging() -> Iterator[None]:
    previous = logging.root.manager.disable
    try:
        yield
    finally:
        logging.disable(previous)


def test_private_pipe_status_rejection_and_no_activate(tmp_path: Path) -> None:
    root, providers, _, _ = prepared(tmp_path)

    async def scenario() -> None:
        runtime = HeadlessRuntime(root, REPOSITORY, providers=providers)
        await runtime.start()
        output = StringIO()
        await serve_private_stream(
            runtime,
            StringIO(
                '{"operation":"status"}\n'
                '{"operation":"activate","text":"private-sentinel"}\n' + "x" * 65_000 + "\n"
                '{"operation":"status"}\n'
            ),
            output,
        )
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        assert [row["event"] for row in rows] == ["result", "error", "error", "result"]
        assert "private-sentinel" not in output.getvalue()
        await runtime.close()

    asyncio.run(scenario())


def test_openai_check_is_nonactivating_and_makes_no_network_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _, _, _ = prepared(tmp_path)
    monkeypatch.setenv("SATORI_OPENAI_API_KEY", "test-cli-key-never-log")
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "untrusted-inherited")
    monkeypatch.setattr(
        "satori.infrastructure.providers.openai_http.OpenAIHttpClient.post_json",
        lambda *_a, **_k: pytest.fail("check must not dispatch"),
    )
    now = datetime.now(UTC)
    args = [
        "check",
        "--openai",
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
    assert main(args) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["state"] == "closed"
    assert "test-cli-key-never-log" not in output.out + output.err


def test_cloud_worker_cannot_silently_use_local_defaults(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "serve-stdio",
                "--data-directory",
                str(tmp_path),
                "--repository-directory",
                str(REPOSITORY),
            ]
        )
        == 1
    )
    assert "configuration_or_runtime_rejected" in capsys.readouterr().out


def test_legacy_cli_refuses_all_role_mode_without_budgeted_injection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = build_openai_settings(tmp_path.resolve(), SecretStr("test-key"))
    assert legacy_main(["chat"], settings=settings) == 2
    assert capsys.readouterr().err.strip() == "all_openai_requires_budgeted_server_runtime"
    assert not (tmp_path / "satori.db").exists()
