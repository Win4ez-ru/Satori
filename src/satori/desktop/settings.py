"""Private desktop preferences, separate from canonical state and credentials."""

import os
import re
import stat
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.infrastructure.artifacts import PrivateArtifactStore


def default_repository_directory() -> Path:
    """Find the source checkout required by the initial development launcher."""

    bundle_resources = getattr(sys, "_MEIPASS", None)
    candidates: list[Path] = [Path(__file__).resolve().parents[3], Path.cwd()]
    if isinstance(bundle_resources, str):
        candidates.insert(0, Path(bundle_resources))
    for candidate in candidates:
        if (candidate / "PROJECT_SATORI.md").is_file() and (candidate / "pyproject.toml").is_file():
            return candidate.absolute()
    raise FileNotFoundError("desktop_source_repository_required")


def _application_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Satori"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Satori"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "satori"


def preferences_path() -> Path:
    override = os.environ.get("SATORI_DESKTOP_PREFERENCES_PATH")
    if override is not None:
        candidate = Path(override)
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("desktop_preferences_absolute_path_required")
        return candidate
    return _application_directory() / "desktop" / "preferences.json"


def _default_data_directory(repository_directory: Path) -> Path:
    # This only proposes a path. The desktop confirms it before opening the runtime.
    existing = repository_directory / "var"
    if (existing / "satori.db").is_file():
        return existing
    return _application_directory() / "profile"


def default_voice_model_directory() -> Path:
    """Public software weights are separate from canonical profiles and recordings."""

    return _application_directory() / "models" / "gigaam-v3-e2e-rnnt-322c3b294926"


def default_speech_model_directory() -> Path:
    """Keep explicitly installed neural speech resources outside canonical profiles."""

    return _application_directory() / "models" / "qwen3-tts-1.7b-customvoice-4bit-f35faf19b0cc"


class DesktopPreferences(BaseModel):
    """Local UI choices; keys, conversation bodies and Character state are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_directory: Path = Field(
        default_factory=lambda: _default_data_directory(default_repository_directory())
    )
    repository_directory: Path = Field(default_factory=default_repository_directory)
    model: str = Field(default="qwen3:4b-instruct", min_length=1, max_length=256)
    runtime_provider: Literal["local", "openai"] = "local"
    openai_runtime_config: Path | None = None
    start_hidden: bool = False
    gmail_client_configuration_path: Path | None = None
    voice_model_directory: Path = Field(default_factory=default_voice_model_directory)
    speech_model_directory: Path = Field(default_factory=default_speech_model_directory)
    speech_backend: Literal["neural", "system", "elevenlabs"] = "elevenlabs"
    elevenlabs_voice_id: str = Field(default="ymDCYd8puC7gYjxIamPt", max_length=128)
    speak_replies: bool = False
    speech_voice: str = Field(default="Milena", min_length=1, max_length=80)
    speech_rate: int = Field(default=165, ge=100, le=260)

    @model_validator(mode="before")
    @classmethod
    def migrate_previous_voice_default(cls, value: object) -> object:
        if isinstance(value, dict):
            migrated = dict(value)
            if "speech_backend" not in migrated:
                # Old system-voice playback consent does not authorize paid cloud speech.
                migrated.update(speech_backend="elevenlabs", speak_replies=False)
            previous = _application_directory() / "models" / "faster-whisper-small-536b0662742c"
            candidate = migrated.get("voice_model_directory")
            if isinstance(candidate, (str, Path)) and Path(candidate) == previous:
                migrated["voice_model_directory"] = str(default_voice_model_directory())
            return migrated
        return value

    @field_validator(
        "data_directory", "repository_directory", "voice_model_directory", "speech_model_directory"
    )
    @classmethod
    def absolute_directory(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("desktop_directory_must_be_absolute")
        return value

    @field_validator("gmail_client_configuration_path")
    @classmethod
    def gmail_configuration_file_path(cls, value: Path | None) -> Path | None:
        # Remember the explicit file location only; restoring preferences must
        # never inspect/read it or turn a path into account authorization.
        if value is not None and (
            not value.is_absolute()
            or ".." in value.parts
            or len(str(value)) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in str(value))
        ):
            raise ValueError("desktop_gmail_configuration_path_invalid")
        return value

    @field_validator("openai_runtime_config")
    @classmethod
    def openai_configuration_file_path(cls, value: Path | None) -> Path | None:
        # A path is metadata, not permission to read a credential or call an API.
        if value is not None and (
            not value.is_absolute()
            or ".." in value.parts
            or len(str(value)) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in str(value))
        ):
            raise ValueError("desktop_openai_configuration_path_invalid")
        return value

    @field_validator("speech_voice")
    @classmethod
    def valid_speech_voice(cls, value: str) -> str:
        if (
            value != value.strip()
            or value.startswith("-")
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("desktop_voice_must_be_nonblank_single_line")
        return value

    @field_validator("elevenlabs_voice_id")
    @classmethod
    def valid_elevenlabs_voice_id(cls, value: str) -> str:
        if value and re.fullmatch(r"[A-Za-z0-9]{20,128}", value, flags=re.ASCII) is None:
            raise ValueError("desktop_cloud_voice_id_invalid")
        return value

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("desktop_model_must_be_nonblank_single_line")
        return value


def load_preferences(repository_directory: Path | None = None) -> DesktopPreferences:
    """Read preferences without creating a profile or opening its database."""

    path = preferences_path()
    payload = (
        PrivateArtifactStore().read(path, max_bytes=16_384) if _configuration_exists(path) else None
    )
    if payload is not None:
        preferences = DesktopPreferences.model_validate_json(payload)
        if repository_directory is not None:
            preferences = DesktopPreferences.model_validate(
                {**preferences.model_dump(), "repository_directory": repository_directory}
            )
        return preferences
    repository = repository_directory or default_repository_directory()
    return DesktopPreferences(
        repository_directory=repository,
        data_directory=_default_data_directory(repository),
    )


def _configuration_exists(path: Path) -> bool:
    for parent in reversed(path.parents):
        try:
            metadata = parent.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError("desktop_preferences_invalid_parent")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError("desktop_preferences_invalid_file")
    return True


def save_preferences(preferences: DesktopPreferences) -> Path:
    """Atomically publish a bounded private configuration without following links."""

    validated = DesktopPreferences.model_validate(preferences.model_dump())
    path = preferences_path()
    PrivateArtifactStore().write(path, validated.model_dump_json(indent=2).encode(), overwrite=True)
    return path
