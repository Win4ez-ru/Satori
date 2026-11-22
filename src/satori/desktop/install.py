"""Create an explicit source-linked development app and opt-in login configuration."""

import argparse
import plistlib
import shlex
import shutil
import stat
import sys
from pathlib import Path

from satori.desktop.settings import default_repository_directory
from satori.infrastructure.artifacts import PrivateArtifactStore

_BUNDLE_IDENTIFIER = "org.satori.desktop.development"
_LAUNCH_LABEL = "org.satori.desktop.development.login"


def _absolute_path(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("desktop_install_absolute_path_required")
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise OSError("desktop_install_symlink_rejected")
    return path


def create_app_bundle(
    destination: Path,
    repository_directory: Path | None = None,
    python_executable: Path | None = None,
) -> Path:
    """Create a Finder-launchable app; an existing destination is never replaced."""

    destination = _absolute_path(destination)
    if destination.suffix != ".app":
        raise ValueError("desktop_install_app_suffix_required")
    repository = _absolute_path(repository_directory or default_repository_directory())
    # A virtualenv Python is commonly a symlink; retain its invocation path so
    # Python selects the intended environment rather than the base interpreter.
    interpreter = python_executable or Path(sys.executable)
    if not interpreter.is_absolute() or not interpreter.is_file():
        raise ValueError("desktop_install_python_executable_required")
    if not (repository / "src" / "satori").is_dir():
        raise ValueError("desktop_install_source_repository_required")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("desktop_install_destination_exists")
    if not destination.parent.is_dir():
        raise FileNotFoundError("desktop_install_parent_required")
    destination.mkdir(mode=0o700)
    created = destination.stat()
    try:
        contents = destination / "Contents"
        executable_directory = contents / "MacOS"
        resources = contents / "Resources"
        executable_directory.mkdir(parents=True, mode=0o700)
        resources.mkdir(mode=0o700)
        launcher = executable_directory / "Satori"
        command = (
            "#!/bin/sh\n"
            f"export PYTHONPATH={shlex.quote(str(repository / 'src'))}\n"
            f"export SATORI_DESKTOP_BUNDLE={shlex.quote(str(destination))}\n"
            f"exec {shlex.quote(str(interpreter))} -m satori.desktop "
            f'--repository-directory {shlex.quote(str(repository))} "$@"\n'
        )
        launcher.write_text(command, encoding="utf-8")
        launcher.chmod(0o700)
        info = {
            "CFBundleDevelopmentRegion": "en",
            "CFBundleDisplayName": "Satori",
            "CFBundleExecutable": "Satori",
            "CFBundleIdentifier": _BUNDLE_IDENTIFIER,
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleName": "Satori",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "2",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": (
                "Микрофон используется только пока ты держишь кнопку диктовки. "
                "Распознавание локальное; аудио не сохраняется."
            ),
            "SatoriSourceLinked": True,
            "SatoriRepositoryDirectory": str(repository),
            "SatoriPythonExecutable": str(interpreter),
        }
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        (resources / "SOURCE-LINKED.txt").write_text(
            "Development launcher, not a standalone distribution.\n"
            "Requires this source checkout, its Python environment and desktop dependencies.\n"
            "Moving/deleting the checkout or environment breaks this app.\n"
            "No Python, Qt, model or database is bundled. No signing/notarization is claimed.\n",
            encoding="utf-8",
        )
    except BaseException:
        observed = destination.lstat()
        if (observed.st_dev, observed.st_ino) == (created.st_dev, created.st_ino):
            shutil.rmtree(destination)
        raise
    return destination


def autostart_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCH_LABEL}.plist"


def _autostart_configuration(bundle_path: Path) -> dict[str, object]:
    return {
        "Label": _LAUNCH_LABEL,
        "ProgramArguments": ["/usr/bin/open", "-g", "-a", str(bundle_path), "--args", "--hidden"],
        "RunAtLoad": True,
    }


def autostart_enabled(bundle_path: Path) -> bool:
    path = _absolute_path(autostart_path())
    if not path.exists() and not path.is_symlink():
        return False
    payload = PrivateArtifactStore().read(path, max_bytes=16_384)
    try:
        return bool(plistlib.loads(payload) == _autostart_configuration(bundle_path))
    except (ValueError, plistlib.InvalidFileException):
        return False


def set_autostart(enabled: bool, bundle_path: Path) -> Path:
    """Configure the next macOS login only, after an explicit UI toggle.

    No launchctl invocation, current-session launch or privileged service is used.
    Existing configuration must match this exact bundle before it can be removed.
    """

    if sys.platform != "darwin":
        raise NotImplementedError("desktop_autostart_macos_only")
    bundle_path = _absolute_path(bundle_path)
    path = _absolute_path(autostart_path())
    expected = _autostart_configuration(bundle_path)
    store = PrivateArtifactStore()
    existing = path.exists() or path.is_symlink()
    if existing:
        original_metadata = path.lstat()
        observed = store.read(path, max_bytes=16_384)
        try:
            owned = plistlib.loads(observed) == expected
        except (ValueError, plistlib.InvalidFileException):
            owned = False
        if not owned:
            raise FileExistsError("desktop_autostart_unrelated_configuration")
    if enabled:
        if not bundle_path.is_dir():
            raise FileNotFoundError("desktop_autostart_bundle_required")
        info_path = bundle_path / "Contents" / "Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        if not isinstance(info, dict) or info.get("CFBundleIdentifier") != _BUNDLE_IDENTIFIER:
            raise ValueError("desktop_autostart_owned_bundle_required")
        if not existing:
            store.write(path, plistlib.dumps(expected))
    elif existing:
        # Recheck the inode/content immediately before unlinking our exact file.
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino)
            != (original_metadata.st_dev, original_metadata.st_ino)
            or store.read(path, max_bytes=16_384) != observed
        ):
            raise OSError("desktop_autostart_configuration_changed")
        path.unlink()
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a source-linked Satori development .app")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--repository-directory", type=Path)
    arguments = parser.parse_args(argv)
    try:
        destination = create_app_bundle(arguments.destination, arguments.repository_directory)
    except (OSError, ValueError) as error:
        print(f"Satori development app could not be created: {error}", file=sys.stderr)
        return 2
    print(f"Created source-linked development app: {destination}")
    print("This launcher requires the source checkout and its Python environment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
