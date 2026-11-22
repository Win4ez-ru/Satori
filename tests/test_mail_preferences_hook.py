"""Root-owned path preference seam: no credential file read or dialogue restart."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from satori.desktop import settings, ui
from satori.desktop.settings import DesktopPreferences


def preferences(tmp_path: Path) -> DesktopPreferences:
    return DesktopPreferences(data_directory=tmp_path / "profile", repository_directory=tmp_path)


@pytest.mark.parametrize("value", ["relative.json", "/tmp/../other.json", "/tmp/new\nline.json"])
def test_file_path_preference_rejects_unsafe_lexical_paths(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValidationError, match="desktop_gmail_configuration_path_invalid"):
        DesktopPreferences.model_validate(
            {**preferences(tmp_path).model_dump(), "gmail_client_configuration_path": value}
        )


def test_saved_path_roundtrip_does_not_open_or_require_client_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "not-present" / "oauth-client.json"
    saved = tmp_path / "private" / "preferences.json"
    monkeypatch.setattr(settings, "preferences_path", lambda: saved)
    updated = DesktopPreferences.model_validate(
        {**preferences(tmp_path).model_dump(), "gmail_client_configuration_path": target}
    )
    settings.save_preferences(updated)
    assert settings.load_preferences().gmail_client_configuration_path == target
    assert json.loads(saved.read_text())["gmail_client_configuration_path"] == str(target)
    assert not target.parent.exists()
    assert "access_token" not in saved.read_text()
    assert "client_secret" not in saved.read_text()
    cleared = DesktopPreferences.model_validate(
        {**updated.model_dump(), "gmail_client_configuration_path": None}
    )
    settings.save_preferences(cleared)
    assert settings.load_preferences().gmail_client_configuration_path is None


def test_path_hook_preserves_live_dialogue_and_every_other_preference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = preferences(tmp_path)
    saved: list[DesktopPreferences] = []
    monkeypatch.setattr(ui, "save_preferences", saved.append)
    host = SimpleNamespace(
        preferences=original, session_id="live-session", editor=object(), pending={"reply": "live"}
    )
    editor = host.editor
    target = tmp_path / "oauth.json"
    ui.DesktopWindow._set_mail_client_configuration_path(cast(ui.DesktopWindow, host), target)
    assert saved == [host.preferences]
    assert host.preferences.gmail_client_configuration_path == target
    assert host.preferences.model_dump(exclude={"gmail_client_configuration_path"}) == (
        original.model_dump(exclude={"gmail_client_configuration_path"})
    )
    assert host.session_id == "live-session"
    assert host.editor is editor
    assert host.pending == {"reply": "live"}


def test_failed_preference_write_leaves_existing_runtime_preferences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = preferences(tmp_path)
    host = SimpleNamespace(preferences=original, session_id="live-session")

    def unavailable(value: DesktopPreferences) -> Path:
        raise OSError("SYNTHETIC_PRIVATE_DETAIL_MUST_NOT_BE_DISPLAYED")

    monkeypatch.setattr(ui, "save_preferences", unavailable)
    with pytest.raises(OSError, match="SYNTHETIC_PRIVATE_DETAIL_MUST_NOT_BE_DISPLAYED"):
        ui.DesktopWindow._set_mail_client_configuration_path(
            cast(ui.DesktopWindow, host), tmp_path / "oauth.json"
        )
    assert host.preferences is original
    assert host.session_id == "live-session"
