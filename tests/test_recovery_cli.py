"""Recovery has an independent CLI composition and no generation side effects."""

import json
import stat
from pathlib import Path

import pytest

import satori.__main__ as cli
from satori.config import Environment, LogLevel, Settings


def settings(path: Path) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+pysqlite:///{path}",
        source_policy_directory=path.parent / "source-policy",
        log_level=LogLevel.WARNING,
    )


def test_recovery_cli_uses_no_live_services_and_preview_does_not_create_configured_database(
    tmp_path: Path,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = settings(tmp_path / "source.db")
    config = project_root / "alembic.ini"
    assert cli.main(["activate"], settings=active, alembic_config=config) == 0
    capsys.readouterr()

    def forbidden_services(*args: object, **kwargs: object) -> None:
        pytest.fail("recovery opened application services")

    monkeypatch.setattr(cli, "_open_services", forbidden_services)
    archive = tmp_path / "archive.state"
    assert (
        cli.main(
            ["state", "backup", "--output", str(archive)], settings=active, alembic_config=config
        )
        == 0
    )
    exported = json.loads(capsys.readouterr().out)
    missing = tmp_path / "not-created.db"
    fresh = settings(missing)
    assert (
        cli.main(
            ["state", "import", str(archive), "--preview"], settings=fresh, alembic_config=config
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview == exported
    assert not missing.exists()
    target = tmp_path / "restored.db"
    assert (
        cli.main(
            [
                "state",
                "import",
                str(archive),
                "--activate",
                "--target",
                str(target),
                "--sha256",
                preview["archive_sha256"],
            ],
            settings=fresh,
            alembic_config=config,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "automatic_processing=false" in output
    assert target.is_file()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not missing.exists()


def test_export_missing_source_does_not_bootstrap_and_activation_requires_exact_review(
    tmp_path: Path, project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "source.db"
    active = settings(missing)
    config = project_root / "alembic.ini"
    assert (
        cli.main(
            ["state", "export", "--output", str(tmp_path / "archive.state")],
            settings=active,
            alembic_config=config,
        )
        == 2
    )
    assert not missing.exists()
    assert "State recovery rejected:" in capsys.readouterr().err
    assert (
        cli.main(
            ["state", "import", "unused", "--activate"], settings=active, alembic_config=config
        )
        == 2
    )
    assert "activation_requires_target_and_reviewed_hash" in capsys.readouterr().err
    assert (
        cli.main(
            ["state", "import", "unused", "--preview", "--target", "unused.db"],
            settings=active,
            alembic_config=config,
        )
        == 2
    )
    assert "preview_does_not_activate" in capsys.readouterr().err


def test_partial_export_is_private_atomic_and_requires_explicit_overwrite(
    tmp_path: Path, project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    active = settings(tmp_path / "source.db")
    config = project_root / "alembic.ini"
    assert cli.main(["activate"], settings=active, alembic_config=config) == 0
    output = tmp_path / "private" / "traits.json"
    capsys.readouterr()
    assert (
        cli.main(
            ["personality", "export", "--output", str(output)],
            settings=active,
            alembic_config=config,
        )
        == 0
    )
    assert capsys.readouterr().out == f"Personality export written: {output}\n"
    before = output.read_bytes()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    assert (
        cli.main(
            ["personality", "export", "--output", str(output)],
            settings=active,
            alembic_config=config,
        )
        == 2
    )
    assert "Partial export rejected:" in capsys.readouterr().err
    assert output.read_bytes() == before
    assert (
        cli.main(
            ["personality", "export", "--output", str(output), "--overwrite"],
            settings=active,
            alembic_config=config,
        )
        == 0
    )
