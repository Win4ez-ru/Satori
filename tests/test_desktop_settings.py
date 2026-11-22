"""Desktop configuration and source-linked packaging without model or OS service calls."""

import json
import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from satori.desktop import install, settings
from satori.desktop.settings import DesktopPreferences


def test_explicit_preferences_location_requires_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "isolated-preferences.json"
    monkeypatch.setenv("SATORI_DESKTOP_PREFERENCES_PATH", str(selected))
    assert settings.preferences_path() == selected
    monkeypatch.setenv("SATORI_DESKTOP_PREFERENCES_PATH", "relative.json")
    with pytest.raises(ValueError, match="absolute_path_required"):
        settings.preferences_path()


def test_bundled_resources_are_selected_without_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "PROJECT_SATORI.md").touch()
    (tmp_path / "pyproject.toml").touch()
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert settings.default_repository_directory() == tmp_path


@pytest.fixture
def preferences_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "private" / "desktop" / "preferences.json"
    monkeypatch.setattr(settings, "preferences_path", lambda: path)
    return path


def _preferences(tmp_path: Path) -> DesktopPreferences:
    return DesktopPreferences(
        data_directory=tmp_path / "profile", repository_directory=tmp_path / "checkout"
    )


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "source checkout with spaces"
    (repository / "src" / "satori").mkdir(parents=True)
    return repository


def test_missing_preferences_propose_existing_profile_without_opening_it(
    tmp_path: Path, preferences_file: Path
) -> None:
    repository = tmp_path / "checkout"
    (repository / "var").mkdir(parents=True)
    database = repository / "var" / "satori.db"
    database.write_bytes(b"sentinel database, never opened")

    preferences = settings.load_preferences(repository)

    assert preferences.data_directory == repository / "var"
    assert preferences.repository_directory == repository
    assert database.read_bytes() == b"sentinel database, never opened"
    assert not preferences_file.exists()
    assert list((repository / "var").iterdir()) == [database]


def test_default_new_profile_is_not_created(
    tmp_path: Path, preferences_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_directory = tmp_path / "Application Support" / "Satori"
    monkeypatch.setattr(settings, "_application_directory", lambda: app_directory)

    preferences = settings.load_preferences(tmp_path / "checkout")

    assert preferences.data_directory == app_directory / "profile"
    assert not app_directory.exists()
    assert not preferences_file.exists()


def test_preferences_roundtrip_is_private_atomic_and_contains_no_secrets(
    tmp_path: Path, preferences_file: Path
) -> None:
    preferences = _preferences(tmp_path)
    assert settings.save_preferences(preferences) == preferences_file
    first_inode = preferences_file.stat().st_ino
    changed = preferences.model_copy(update={"model": "other:model", "start_hidden": True})
    settings.save_preferences(changed)

    assert settings.load_preferences() == changed
    assert preferences_file.stat().st_ino != first_inode
    assert stat.S_IMODE(preferences_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(preferences_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(preferences_file.parent.parent.stat().st_mode) == 0o700
    assert set(json.loads(preferences_file.read_text())) == {
        "gmail_client_configuration_path",
        "data_directory",
        "repository_directory",
        "model",
        "runtime_provider",
        "openai_runtime_config",
        "start_hidden",
        "voice_model_directory",
        "speech_model_directory",
        "speech_backend",
        "elevenlabs_voice_id",
        "speak_replies",
        "speech_voice",
        "speech_rate",
    }
    assert list(preferences_file.parent.iterdir()) == [preferences_file]


def test_explicit_repository_override_is_validated(tmp_path: Path, preferences_file: Path) -> None:
    settings.save_preferences(_preferences(tmp_path))
    selected = tmp_path / "selected checkout"
    assert settings.load_preferences(selected).repository_directory == selected
    with pytest.raises(ValidationError, match="absolute"):
        settings.load_preferences(Path("relative"))


@pytest.mark.parametrize(
    "field",
    ["data_directory", "repository_directory", "voice_model_directory", "speech_model_directory"],
)
def test_preferences_reject_relative_paths(tmp_path: Path, field: str) -> None:
    data = _preferences(tmp_path).model_dump()
    data[field] = Path("relative")
    with pytest.raises(ValidationError, match="absolute"):
        DesktopPreferences.model_validate(data)


def test_previous_default_asr_path_migrates_without_rewriting_preferences_or_weights(
    tmp_path: Path, preferences_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "application"
    monkeypatch.setattr(settings, "_application_directory", lambda: app)
    previous = app / "models" / "faster-whisper-small-536b0662742c"
    previous.mkdir(parents=True)
    (previous / "sentinel").write_bytes(b"preserved public weights")
    preferences_file.parent.mkdir(parents=True)
    original = json.dumps(
        {
            "data_directory": str(tmp_path / "profile"),
            "repository_directory": str(tmp_path),
            "voice_model_directory": str(previous),
            "speech_voice": "Milena",
            "speech_rate": 165,
        }
    )
    preferences_file.write_text(original)
    loaded = settings.load_preferences()
    assert loaded.voice_model_directory == settings.default_voice_model_directory()
    assert loaded.speech_backend == "elevenlabs"
    assert loaded.speech_model_directory == settings.default_speech_model_directory()
    assert not loaded.speak_replies
    assert preferences_file.read_text() == original
    assert (previous / "sentinel").read_bytes() == b"preserved public weights"
    assert not loaded.voice_model_directory.exists()
    assert not loaded.speech_model_directory.exists()


def test_legacy_system_autoplay_does_not_authorize_default_cloud_speech(tmp_path: Path) -> None:
    preferences = DesktopPreferences.model_validate_json(
        json.dumps({"data_directory": str(tmp_path), "speak_replies": True})
    )
    assert preferences.speech_backend == "elevenlabs"
    assert preferences.elevenlabs_voice_id == "ymDCYd8puC7gYjxIamPt"
    assert not preferences.speak_replies


@pytest.mark.parametrize("backend", ["system", "neural", "elevenlabs"])
def test_explicit_existing_backend_and_its_autoplay_are_preserved(
    tmp_path: Path, backend: str
) -> None:
    preferences = DesktopPreferences.model_validate_json(
        json.dumps(
            {"data_directory": str(tmp_path), "speech_backend": backend, "speak_replies": True}
        )
    )
    assert preferences.speech_backend == backend
    assert preferences.speak_replies


def test_custom_asr_path_and_explicit_system_choice_are_preserved(tmp_path: Path) -> None:
    custom = tmp_path / "faster-whisper-small-536b0662742c"
    preferences = DesktopPreferences(
        data_directory=tmp_path,
        voice_model_directory=custom,
        speech_backend="system",
        speech_voice="Milena",
        speech_rate=165,
    )
    assert preferences.voice_model_directory == custom
    assert preferences.speech_backend == "system"
    assert preferences.speech_voice == "Milena"


@pytest.mark.parametrize("backend", ["cloud", "neural\nPRIVATE", True])
def test_unknown_speech_backends_cannot_be_persisted(tmp_path: Path, backend: object) -> None:
    with pytest.raises(ValidationError):
        DesktopPreferences.model_validate({"data_directory": tmp_path, "speech_backend": backend})


@pytest.mark.parametrize("voice_id", ["", "ymDCYd8puC7gYjxIamPt", "A" * 128])
def test_cloud_voice_choice_is_persisted_without_a_key(
    tmp_path: Path, preferences_file: Path, voice_id: str
) -> None:
    preferences = DesktopPreferences(
        data_directory=tmp_path, speech_backend="elevenlabs", elevenlabs_voice_id=voice_id
    )
    settings.save_preferences(preferences)
    assert settings.load_preferences().elevenlabs_voice_id == voice_id
    assert "api_key" not in preferences_file.read_text()


@pytest.mark.parametrize(
    "voice_id", ["short", "A" * 129, "A" * 19 + "_", "A" * 19 + "-", "A" * 19 + "я"]
)
def test_cloud_voice_id_rejects_malformed_url_components(tmp_path: Path, voice_id: str) -> None:
    with pytest.raises(ValidationError, match=r"cloud_voice_id_invalid|too_long"):
        DesktopPreferences(data_directory=tmp_path, elevenlabs_voice_id=voice_id)


@pytest.mark.parametrize("model", ["", " ", " model", "model\nother"])
def test_preferences_reject_unsafe_model_values(tmp_path: Path, model: str) -> None:
    with pytest.raises(ValidationError):
        DesktopPreferences.model_validate({**_preferences(tmp_path).model_dump(), "model": model})


def test_unknown_or_secret_configuration_fields_fail_closed(
    tmp_path: Path, preferences_file: Path
) -> None:
    preferences_file.parent.mkdir(parents=True)
    data = _preferences(tmp_path).model_dump(mode="json")
    data["api_key"] = "synthetic-secret"
    preferences_file.write_text(json.dumps(data))
    with pytest.raises(ValidationError, match="Extra inputs"):
        settings.load_preferences()


def test_gpt_selection_roundtrips_reference_without_enabling_old_preferences(
    tmp_path: Path, preferences_file: Path
) -> None:
    original = _preferences(tmp_path)
    assert original.runtime_provider == "local"
    assert original.openai_runtime_config is None
    metadata = original.data_directory / "desktop-openai-runtime.json"
    selected = DesktopPreferences.model_validate(
        {**original.model_dump(), "runtime_provider": "openai", "openai_runtime_config": metadata}
    )
    settings.save_preferences(selected)
    assert settings.load_preferences() == selected
    assert not metadata.exists()
    assert not selected.data_directory.exists()
    assert "SATORI_OPENAI_API_KEY" not in preferences_file.read_text()


@pytest.mark.parametrize("value", ["relative.json", "/PUBLIC/../other.json", "/PUBLIC/line\nbreak"])
def test_gpt_metadata_reference_rejects_unbounded_paths(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValidationError, match="configuration_path_invalid"):
        DesktopPreferences.model_validate(
            {**_preferences(tmp_path).model_dump(), "openai_runtime_config": value}
        )


@pytest.mark.parametrize("operation", ["load", "save"])
def test_preferences_symlink_preserves_unrelated_target(
    tmp_path: Path, preferences_file: Path, operation: str
) -> None:
    target = tmp_path / "unrelated.json"
    target.write_text("unchanged")
    preferences_file.parent.mkdir(parents=True)
    preferences_file.symlink_to(target)
    if operation == "load":
        with pytest.raises(OSError, match="invalid_file"):
            settings.load_preferences()
    else:
        with pytest.raises(OSError, match="artifact_invalid_target"):
            settings.save_preferences(_preferences(tmp_path))
    assert preferences_file.is_symlink()
    assert target.read_text() == "unchanged"


def test_preferences_parent_symlink_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(settings, "preferences_path", lambda: alias / "preferences.json")
    with pytest.raises(OSError, match="invalid_parent"):
        settings.load_preferences(tmp_path)
    with pytest.raises(OSError, match="artifact_write_failed"):
        settings.save_preferences(_preferences(tmp_path))
    assert list(real.iterdir()) == []


def test_invalid_saved_configuration_is_not_replaced_by_defaults(
    tmp_path: Path, preferences_file: Path
) -> None:
    preferences_file.parent.mkdir(parents=True)
    preferences_file.write_text("invalid JSON")
    with pytest.raises(ValidationError):
        settings.load_preferences(tmp_path)
    assert preferences_file.read_text() == "invalid JSON"


def test_source_linked_app_has_correct_plist_and_quotes_paths(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    destination = tmp_path / "Satori Development.app"
    interpreter = tmp_path / "python with spaces"
    interpreter.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$PYTHONPATH" "$@"\n',
        encoding="utf-8",
    )
    interpreter.chmod(0o700)

    assert install.create_app_bundle(destination, repository, interpreter) == destination
    info = plistlib.loads((destination / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "Satori"
    assert info["SatoriSourceLinked"] is True
    assert info["SatoriRepositoryDirectory"] == str(repository)
    launcher = destination / "Contents" / "MacOS" / "Satori"
    assert os.access(launcher, os.X_OK)
    result = subprocess.run([str(launcher), "--hidden"], capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == [
        str(repository / "src"),
        "-m",
        "satori.desktop",
        "--repository-directory",
        str(repository),
        "--hidden",
    ]
    assert (
        "not a standalone distribution"
        in (destination / "Contents" / "Resources" / "SOURCE-LINKED.txt").read_text()
    )
    assert not (repository / "var").exists()


def test_app_destination_is_never_overwritten(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    destination = tmp_path / "Satori.app"
    destination.mkdir()
    sentinel = destination / "user-file"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError):
        install.create_app_bundle(destination, repository, Path(sys.executable))
    assert sentinel.read_text() == "keep"


def test_app_parent_symlink_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(repository, target_is_directory=True)
    with pytest.raises(OSError, match="symlink"):
        install.create_app_bundle(alias / "Satori.app", repository, Path(sys.executable))
    assert not (repository / "Satori.app").exists()


def test_autostart_is_opt_in_private_and_exactly_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    target = tmp_path / "LaunchAgents" / "satori.plist"
    monkeypatch.setattr(install, "autostart_path", lambda: target)
    repository = _repository(tmp_path)
    bundle = install.create_app_bundle(tmp_path / "Satori.app", repository, Path(sys.executable))
    assert not target.exists()
    assert not install.autostart_enabled(bundle)

    assert install.set_autostart(True, bundle) == target
    assert install.autostart_enabled(bundle)
    info = plistlib.loads(target.read_bytes())
    assert info["ProgramArguments"] == [
        "/usr/bin/open",
        "-g",
        "-a",
        str(bundle),
        "--args",
        "--hidden",
    ]
    assert info["RunAtLoad"] is True
    assert "KeepAlive" not in info
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    before = target.read_bytes()
    install.set_autostart(True, bundle)
    assert target.read_bytes() == before
    install.set_autostart(False, bundle)
    assert not target.exists()


@pytest.mark.parametrize("enabled", [False, True])
def test_unrelated_login_item_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    target = tmp_path / "unrelated.plist"
    target.write_bytes(plistlib.dumps({"Label": "someone.else", "RunAtLoad": True}))
    original = target.read_bytes()
    monkeypatch.setattr(install, "autostart_path", lambda: target)
    with pytest.raises(FileExistsError, match="unrelated"):
        install.set_autostart(enabled, tmp_path / "Satori.app")
    assert target.read_bytes() == original


def test_autostart_symlink_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    victim = tmp_path / "victim"
    victim.write_text("unchanged")
    target = tmp_path / "login.plist"
    target.symlink_to(victim)
    monkeypatch.setattr(install, "autostart_path", lambda: target)
    with pytest.raises(OSError, match="symlink"):
        install.set_autostart(False, tmp_path / "Satori.app")
    assert victim.read_text() == "unchanged"


def test_windows_autostart_is_explicitly_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(NotImplementedError, match="macos_only"):
        install.set_autostart(True, tmp_path / "Satori.app")
