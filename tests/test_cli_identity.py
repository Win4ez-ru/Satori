"""Non-interactive Stage 2 CLI contract tests."""

from pathlib import Path

import pytest

from satori.__main__ import main
from satori.config import Environment, LogLevel, Settings


def cli_settings(sqlite_url: str) -> Settings:
    """Build isolated CLI settings without process environment state."""

    return Settings(
        environment=Environment.TEST,
        database_url=sqlite_url,
        log_level=LogLevel.WARNING,
    )


def test_status_before_activation_is_explicit_and_does_not_activate(
    sqlite_url: str,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Status may migrate a clean DB but never creates Satori."""

    result = main(
        ["status"],
        settings=cli_settings(sqlite_url),
        alembic_config=project_root / "alembic.ini",
    )

    assert result == 0
    assert capsys.readouterr().out == "Satori: not activated\n"


def test_activate_status_and_repeat_activation_are_safe(
    sqlite_url: str,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Canonical activation is one command and repeat is a safe no-op outcome."""

    arguments = {
        "settings": cli_settings(sqlite_url),
        "alembic_config": project_root / "alembic.ini",
    }
    assert main(["activate"], **arguments) == 0  # type: ignore[arg-type]
    first_output = capsys.readouterr().out
    assert first_output.startswith("Satori activated.\nSatori: active\nIdentity: ")
    identity_line = next(
        line for line in first_output.splitlines() if line.startswith("Identity: ")
    )

    assert main(["status"], **arguments) == 0  # type: ignore[arg-type]
    status_output = capsys.readouterr().out
    assert "Satori: active" in status_output
    assert identity_line in status_output
    assert "Seed: satori.initial.v1 (schema 1, sha256 " in status_output

    assert main(["activate"], **arguments) == 0  # type: ignore[arg-type]
    repeat_output = capsys.readouterr().out
    assert repeat_output.startswith(
        "Satori is already activated; existing state was not changed.\n"
    )
    assert identity_line in repeat_output
