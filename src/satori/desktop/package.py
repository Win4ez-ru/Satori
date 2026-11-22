"""Build a local self-contained macOS desktop bundle with a build-only PyInstaller."""

import argparse
import hashlib
import json
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from satori.desktop.install import _absolute_path
from satori.desktop.settings import default_repository_directory
from satori.desktop.telegram_reply_worker import PROTOCOL_PATH as _REPLY_PROTOCOL_PATH
from satori.desktop.telegram_reply_worker import PROTOCOL_SHA256 as _REPLY_PROTOCOL_SHA
from satori.desktop.telegram_task_protocol import TASK_PROTOCOL_PATH as _TASK_PROTOCOL_PATH
from satori.desktop.telegram_task_protocol import TASK_PROTOCOL_SHA256 as _TASK_PROTOCOL_SHA
from satori.desktop.telegram_worker import _SOFTWARE_PINS as _TELEGRAM_SOFTWARE_PINS

_BUNDLE_IDENTIFIER = "org.satori.desktop.development"
_CALENDAR_USAGE = (
    "Для просмотра событий macOS требует полный доступ к календарям (чтение и запись). "
    "Сатори читает только явно выбранные календари и добавляет проверенные смены "
    "в выбранный календарь после вашего подтверждения. Приглашения не отправляются."
)
_TELEGRAM_POLICY = "docs/decisions/0092-unified-personal-sources-desktop.md"
_TELEGRAM_LICENSES = {
    "TDLib-Boost-1.0.txt": "c9bff75738922193e67fa726fa225535870d2aa1059f91452c411736284ad566",
    "OpenSSL-Apache-2.0.txt": "7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a",
    "SQLCipher-Zetetic-BSD.txt": "92a681e777538e3cc012d5886505fec7704fb9c1957ef6b67807d771985d457e",
}
_PLACES_SOURCES = (
    "Models.swift",
    "Protocol.swift",
    "Lease.swift",
    "NativeProvider.swift",
    "MapWindow.swift",
    "Coordinator.swift",
    "SelfTest.swift",
    "Main.swift",
)
_REMINDER_IDENTIFIER = "ai.satori.desktop.reminder-notifications"
_REMINDER_EXECUTABLE = "SatoriReminderNotifications"


@dataclass(frozen=True, slots=True)
class _TelegramResources:
    directory: Path
    files: tuple[tuple[str, str, int], ...]


def _read_telegram_file(path: Path, expected: str) -> bytes:
    """Read only known regular software, with no SDK execution or manifest trust."""
    _absolute_path(path)
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise OSError("desktop_package_telegram_resource_required")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("desktop_package_telegram_pin_rejected")
    return data


def _prepare_telegram_resources(
    repository: Path,
    sdk_directory: Path,
    output: Path,
    *,
    reviewed_replies: bool = False,
) -> _TelegramResources:
    """Stage an exact public-software allowlist, never a session or SDK tree."""
    sdk = _absolute_path(sdk_directory)
    if not sdk.is_dir():
        raise FileNotFoundError("desktop_package_telegram_sdk_required")
    contents: dict[str, bytes] = {}
    for relative, expected in _TELEGRAM_SOFTWARE_PINS.items():
        if relative == "telegram/sdk/libtdjson.1.8.67.dylib":
            source = sdk / "lib/libtdjson.1.8.67.dylib"
        elif relative == "telegram/policy.md":
            source = repository / _TELEGRAM_POLICY
        else:
            source = repository / "src/satori/resources" / relative
        contents[relative.removeprefix("telegram/")] = _read_telegram_file(source, expected)
    for name, expected in _TELEGRAM_LICENSES.items():
        contents[f"licenses/{name}"] = _read_telegram_file(sdk / "share/licenses" / name, expected)
    if reviewed_replies:
        reply_source = repository / "src/satori/resources" / _REPLY_PROTOCOL_PATH
        contents[_REPLY_PROTOCOL_PATH.removeprefix("telegram/")] = _read_telegram_file(
            reply_source, _REPLY_PROTOCOL_SHA
        )
        task_source = repository / "src/satori/resources" / _TASK_PROTOCOL_PATH
        contents[_TASK_PROTOCOL_PATH.removeprefix("telegram/")] = _read_telegram_file(
            task_source, _TASK_PROTOCOL_SHA
        )

    worker = "telegram/backend/worker.py.source"
    library = "telegram/sdk/libtdjson.1.8.67.dylib"
    executable_payload = (worker, "telegram/backend/schema.json", library)
    config = {
        "v": 2,
        "worker": {"path": worker, "sha256": _TELEGRAM_SOFTWARE_PINS[worker]},
        "library": {"path": library, "sha256": _TELEGRAM_SOFTWARE_PINS[library]},
        "payload": [
            {
                "path": relative,
                "sha256": _TELEGRAM_SOFTWARE_PINS[relative],
                "bytes": len(contents[relative.removeprefix("telegram/")]),
            }
            for relative in sorted(executable_payload)
        ],
    }
    provenance = {
        "v": 1,
        "name": "TDLib",
        "version": "1.8.67",
        "commit": "bc9c263e2bfee06aaab41e82db51a103376030bc",
        "library_sha256": _TELEGRAM_SOFTWARE_PINS[library],
        "licenses": _TELEGRAM_LICENSES,
        "policy_sha256": _TELEGRAM_SOFTWARE_PINS["telegram/policy.md"],
        "account_acceptance": "NOT_RUN",
    }
    for name, value in (("runtime-config.json", config), ("provenance.json", provenance)):
        contents[name] = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    output.mkdir(mode=0o700)
    records: list[tuple[str, str, int]] = []
    for relative, data in sorted(contents.items()):
        target = output / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if relative == "sdk/libtdjson.1.8.67.dylib" else 0o644)
        records.append((relative, hashlib.sha256(data).hexdigest(), len(data)))
    return _TelegramResources(output, tuple(records))


def _verify_telegram_resources(resources: _TelegramResources) -> None:
    for relative, expected, size in resources.files:
        path = resources.directory / relative
        data = _read_telegram_file(path, expected)
        if len(data) != size:
            raise ValueError("desktop_package_telegram_size_rejected")


def _restore_telegram_resources(staged_app: Path, resources: _TelegramResources) -> None:
    """Replace owned data entries after PyInstaller's Mach-O relocation/signing.

    PyInstaller may turn a data library into a symlink or rewrite its signature.
    Remove only this new bundle's entry, never follow it into another directory.
    """
    _verify_telegram_resources(resources)
    bundle_resources = staged_app / "Contents/Resources"
    for directory in (staged_app, staged_app / "Contents", bundle_resources):
        if directory.is_symlink() or not directory.is_dir():
            raise OSError("desktop_package_telegram_bundle_directory_rejected")
    target = bundle_resources / "telegram"
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)
    target.mkdir(mode=0o755)
    for relative, _expected, _size in resources.files:
        output = target / relative
        output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.copyfile(resources.directory / relative, output)
        output.chmod(0o755 if relative == "sdk/libtdjson.1.8.67.dylib" else 0o644)
    _verify_telegram_resources(_TelegramResources(target, resources.files))


def _build_calendar_helper(
    repository: Path, output: Path, log_path: Path, *, create_only: bool = False
) -> None:
    """Compile the allowlisted calendar/OCR adapter; Xcode is build-only."""
    source = _absolute_path(
        repository
        / (
            "src/satori/desktop/calendar_actions_native/main.swift"
            if create_only
            else "src/satori/desktop/calendar_native/main.swift"
        )
    )
    if not source.is_file():
        raise FileNotFoundError("desktop_package_calendar_source_required")
    architecture = platform.machine()
    if architecture not in {"arm64", "x86_64"}:
        raise ValueError("desktop_package_calendar_architecture_unsupported")
    output.parent.mkdir(mode=0o700)
    info_path = output.parent / "Info.plist"
    info_path.write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": f"{_BUNDLE_IDENTIFIER}.calendar-actions"
                if create_only
                else f"{_BUNDLE_IDENTIFIER}.calendar-read",
                "CFBundleExecutable": "SatoriCalendarActions" if create_only else "SatoriCalendar",
                "CFBundleName": "SatoriCalendarActions" if create_only else "SatoriCalendar",
                "CFBundleDisplayName": "Сатори",
                "CFBundleVersion": "1",
                "NSCalendarsFullAccessUsageDescription": _CALENDAR_USAGE,
            }
        )
    )
    arguments = [
        "/usr/bin/xcrun",
        "swiftc",
        "-O",
        "-swift-version",
        "5",
        "-framework",
        "EventKit",
        "-framework",
        "Foundation",
        "-framework",
        "Vision",
        "-framework",
        "ImageIO",
        "-framework",
        "CoreGraphics",
        "-framework",
        "CryptoKit",
        "-target",
        f"{architecture}-apple-macosx14.0",
        "-module-cache-path",
        str(output.parent / "module-cache"),
        "-Xlinker",
        "-sectcreate",
        "-Xlinker",
        "__TEXT",
        "-Xlinker",
        "__info_plist",
        "-Xlinker",
        str(info_path),
        str(source),
        "-o",
        str(output),
    ]
    with log_path.open("ab") as build_log:
        result = subprocess.run(
            arguments, stdout=build_log, stderr=subprocess.STDOUT, check=False, timeout=180
        )
    if result.returncode or not output.is_file():
        raise OSError(f"desktop_calendar_compile_failed; diagnostic log: {log_path}")


def _daily_helper_sources(repository: Path) -> tuple[tuple[Path, ...], Path, bytes]:
    """Read only accepted native source names; no checkout or runtime Swift collection."""
    root = repository / "src/satori/desktop"
    places = tuple(_absolute_path(root / "places_native" / name) for name in _PLACES_SOURCES)
    reminder = _absolute_path(root / "reminder_native/main.swift")
    info_path = _absolute_path(root / "reminder_native/Info.plist")
    for source in (*places, reminder, info_path):
        if not source.is_file() or source.stat().st_size > 1024 * 1024:
            raise FileNotFoundError("desktop_package_daily_source_required")
    if info_path.stat().st_size > 16384:
        raise ValueError("desktop_package_reminder_info_rejected")
    data = info_path.read_bytes()
    try:
        info = plistlib.loads(data)
    except (ValueError, plistlib.InvalidFileException):
        raise ValueError("desktop_package_reminder_info_rejected") from None
    if (
        not isinstance(info, dict)
        or info.get("CFBundleIdentifier") != _REMINDER_IDENTIFIER
        or info.get("CFBundleExecutable") != _REMINDER_EXECUTABLE
        or info.get("CFBundlePackageType") != "APPL"
    ):
        raise ValueError("desktop_package_reminder_info_rejected")
    return places, reminder, data


def _compile_daily_helper(
    sources: tuple[Path, ...], output: Path, cache: Path, log_path: Path, *, places: bool
) -> None:
    architecture = platform.machine()
    if architecture not in {"arm64", "x86_64"}:
        raise ValueError("desktop_package_daily_architecture_unsupported")
    output.parent.mkdir(mode=0o700, parents=True)
    arguments = ["/usr/bin/xcrun", "swiftc", "-O", "-swift-version", "6" if places else "5"]
    if places:
        arguments.extend(["-strict-concurrency=complete", "-warnings-as-errors"])
    arguments.extend(
        [
            "-target",
            f"{architecture}-apple-macosx14.0",
            "-module-cache-path",
            str(cache),
        ]
    )
    for framework in (
        ("AppKit", "Foundation", "MapKit")
        if places
        else ("AppKit", "Foundation", "UserNotifications")
    ):
        arguments.extend(["-framework", framework])
    arguments.extend([*(str(source) for source in sources), "-o", str(output)])
    with log_path.open("ab") as build_log:
        result = subprocess.run(
            arguments, stdout=build_log, stderr=subprocess.STDOUT, check=False, timeout=180
        )
    if result.returncode or not output.is_file() or output.is_symlink():
        name = "places" if places else "reminder"
        raise OSError(f"desktop_{name}_compile_failed; diagnostic log: {log_path}")


def _build_daily_helpers(repository: Path, staged_app: Path, stage: Path, log_path: Path) -> None:
    """Add compiled helpers after PyInstaller; caches and sources stay outside the app."""
    places_sources, reminder_source, reminder_info = _daily_helper_sources(repository)
    resources = staged_app / "Contents/Resources"
    for directory in (staged_app, staged_app / "Contents", resources):
        _absolute_path(directory)
        if not directory.is_dir():
            raise OSError("desktop_package_daily_bundle_directory_rejected")
    places_target = resources / "places-native"
    reminder_target = resources / "reminder-native"
    if any(path.exists() or path.is_symlink() for path in (places_target, reminder_target)):
        raise FileExistsError("desktop_package_daily_resource_collision")
    native = stage / "daily-native-build"
    places = native / "places" / "SatoriPlaces"
    reminder = native / "reminder" / _REMINDER_EXECUTABLE
    _compile_daily_helper(
        places_sources, places, native / "places-module-cache", log_path, places=True
    )
    _compile_daily_helper(
        (reminder_source,), reminder, native / "reminder-module-cache", log_path, places=False
    )
    places_target.mkdir(mode=0o755)
    shutil.copyfile(places, places_target / "SatoriPlaces")
    (places_target / "SatoriPlaces").chmod(0o755)
    contents = reminder_target / f"{_REMINDER_EXECUTABLE}.app" / "Contents"
    (contents / "MacOS").mkdir(mode=0o755, parents=True)
    (contents / "Info.plist").write_bytes(reminder_info)
    shutil.copyfile(reminder, contents / "MacOS" / _REMINDER_EXECUTABLE)
    (contents / "MacOS" / _REMINDER_EXECUTABLE).chmod(0o755)


def _sign_daily_helpers(staged_app: Path, log_path: Path) -> None:
    resources = staged_app / "Contents/Resources"
    targets = (
        resources / "places-native/SatoriPlaces",
        resources / f"reminder-native/{_REMINDER_EXECUTABLE}.app",
    )
    with log_path.open("ab") as build_log:
        for target in targets:
            _absolute_path(target)
            for arguments in (
                ["/usr/bin/codesign", "--force", "--sign", "-", str(target)],
                ["/usr/bin/codesign", "--verify", "--strict", str(target)],
            ):
                result = subprocess.run(
                    arguments,
                    stdout=build_log,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=30,
                )
                if result.returncode:
                    raise OSError(
                        f"desktop_package_daily_signature_failed; diagnostic log: {log_path}"
                    )


def _runtime_files(repository: Path) -> list[tuple[str, str]]:
    """Use an explicit resource allowlist; never collect a checkout wholesale."""

    required = [
        (repository / "alembic.ini", "."),
        (repository / "PROJECT_SATORI.md", "."),
        (repository / "pyproject.toml", "."),
        (repository / "migrations" / "env.py", "migrations"),
        (repository / "migrations" / "script.py.mako", "migrations"),
        (
            repository / "src" / "satori" / "resources" / "seeds" / "satori-v1.json",
            "satori/resources/seeds",
        ),
        (
            repository / "src" / "satori" / "resources" / "brand" / "satori-portrait-v1.png",
            "satori/resources/brand",
        ),
        (
            repository / "src" / "satori" / "resources" / "openai" / "desktop-pricing-v1.json",
            "satori/resources/openai",
        ),
    ]
    versions = sorted((repository / "migrations" / "versions").glob("*.py"))
    if not versions:
        raise FileNotFoundError("desktop_package_migrations_required")
    required.extend((version, "migrations/versions") for version in versions)
    policy_directory = (
        repository / "src" / "satori" / "infrastructure" / "source_policy" / "migrations"
    )
    policy_versions = sorted((policy_directory / "versions").glob("*.py"))
    if not policy_versions:
        raise FileNotFoundError("desktop_package_policy_migrations_required")
    required.append((policy_directory / "env.py", "satori/infrastructure/source_policy/migrations"))
    required.extend(
        (version, "satori/infrastructure/source_policy/migrations/versions")
        for version in policy_versions
    )
    if any(not source.is_file() or source.is_symlink() for source, _target in required):
        raise FileNotFoundError("desktop_package_runtime_resource_required")
    return [(str(source), target) for source, target in required]


def _hidden_imports(repository: Path) -> list[str]:
    # Static enumeration avoids importing application modules during collection.
    source = repository / "src"
    names: set[str] = {
        "logging.config",
        "alembic.context",
        "alembic.op",
        "sqlalchemy.dialects.sqlite",
        "sqlalchemy.dialects.sqlite.pysqlite",
        "pydantic_settings.sources",
        "satori.desktop.worker",
        "onnx_asr",
        "onnxruntime",
        "av",
        "certifi",
        "regex",
        "mlx.core",
        "mlx.nn",
        "mlx_audio.tts.utils",
        "mlx_audio.tts.models.qwen3_tts",
        "transformers.models.auto.tokenization_auto",
        "transformers.models.qwen2.tokenization_qwen2",
    }
    for path in (source / "satori").rglob("*.py"):
        parts = path.relative_to(source).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.add(".".join(parts))
    return sorted(names)


def _specification(
    repository: Path,
    entrypoint: Path,
    calendar_helper: Path | None = None,
    telegram_resources: _TelegramResources | None = None,
    calendar_action_helper: Path | None = None,
) -> str:
    """Generate plain Python spec values using repr, never shell interpolation."""

    calendar_binaries = (
        [] if calendar_helper is None else [(str(calendar_helper), "calendar-native")]
    )
    if calendar_action_helper is not None:
        calendar_binaries.append((str(calendar_action_helper), "calendar-actions-native"))
    telegram_data = (
        []
        if telegram_resources is None
        else [
            (
                str(telegram_resources.directory / relative),
                (Path("telegram") / Path(relative).parent).as_posix(),
            )
            for relative, _hash, _size in telegram_resources.files
        ]
    )
    return (
        "# Generated local Satori bundle specification.\n"
        "from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs, "
        "copy_metadata)\n"
        "voice_data = collect_data_files('onnx_asr', includes=['preprocessors/data/*'])\n"
        "voice_data += collect_data_files('mlx', includes=['lib/*.metallib'])\n"
        "voice_binaries = collect_dynamic_libs('mlx')\n"
        f"voice_binaries += {calendar_binaries!r}\n"
        "for dependency in ('onnx-asr', 'onnxruntime', 'av', 'regex', 'mlx', 'mlx-metal',\n"
        "                   'mlx-audio', 'transformers', 'huggingface-hub', 'safetensors',\n"
        "                   'numpy', 'tokenizers', 'certifi', 'PySide6-Addons',\n"
        "                   'PySide6-Essentials', 'shiboken6'):\n"
        "    voice_data += copy_metadata(dependency)\n"
        f"a = Analysis([{str(entrypoint)!r}], pathex={[str(repository / 'src')]!r},\n"
        f"    binaries=voice_binaries, datas={_runtime_files(repository)!r} + "
        f"{telegram_data!r} + voice_data,\n"
        f"    hiddenimports={_hidden_imports(repository)!r},\n"
        "    hookspath=[], hooksconfig={}, runtime_hooks=[],\n"
        "    excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'torch', 'tensorflow', "
        "'pytest', '_pytest', 'mypy', 'ruff'], "
        "noarchive=False)\n"
        "pyz = PYZ(a.pure)\n"
        "exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Satori',\n"
        "    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,\n"
        "    console=False, disable_windowed_traceback=True, argv_emulation=False,\n"
        "    target_arch=None, codesign_identity=None, entitlements_file=None)\n"
        "coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Satori')\n"
        "app = BUNDLE(coll, name='Satori.app', icon=None,\n"
        f"    bundle_identifier={_BUNDLE_IDENTIFIER!r},\n"
        "    info_plist={'CFBundleDisplayName': 'Satori', 'CFBundleName': 'Satori',\n"
        "        'CFBundleShortVersionString': '0.1.0', 'CFBundleVersion': '26',\n"
        "        'NSHighResolutionCapable': True, 'SatoriPackaged': True,\n"
        f"        'NSCalendarsFullAccessUsageDescription': {_CALENDAR_USAGE!r},\n"
        "        'NSMicrophoneUsageDescription': "
        "'Микрофон используется только для явной диктовки. "
        "Распознавание локальное; аудио не сохраняется.',\n"
        "        'SatoriSourceLinked': False})\n"
    )


def build_app_bundle(
    destination: Path,
    repository_directory: Path | None = None,
    python_executable: Path | None = None,
    *,
    telegram_sdk_directory: Path | None = None,
    reviewed_telegram_replies: bool = False,
) -> Path:
    """Build and publish a new app; preserve build diagnostics after any failure.

    The selected Python environment must already contain PyInstaller, Qt and all
    project runtime dependencies. This function never installs packages or models.
    """

    if sys.platform != "darwin":
        raise NotImplementedError("desktop_package_macos_only")
    destination = _absolute_path(destination)
    repository = _absolute_path(repository_directory or default_repository_directory())
    interpreter = python_executable or Path(sys.executable)
    if destination.suffix != ".app":
        raise ValueError("desktop_package_app_suffix_required")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("desktop_package_destination_exists")
    if not interpreter.is_absolute() or not interpreter.is_file():
        raise ValueError("desktop_package_python_required")
    _runtime_files(repository)
    _daily_helper_sources(repository)
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    # Build outside synchronized source folders: Finder metadata on .framework
    # directories can otherwise invalidate macOS bundle signing.
    stage = Path(tempfile.mkdtemp(prefix="satori-package-")).resolve(strict=True)
    telegram_resources = (
        None
        if telegram_sdk_directory is None
        else _prepare_telegram_resources(
            repository,
            telegram_sdk_directory,
            stage / "telegram",
            reviewed_replies=reviewed_telegram_replies,
        )
    )
    entrypoint = stage / "desktop_entrypoint.py"
    entrypoint.write_text(
        "import sys\n"
        "sys.dont_write_bytecode = True\n"
        "from satori.desktop.__main__ import main\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    specification = stage / "Satori.spec"
    calendar_helper = stage / "calendar-native" / "SatoriCalendar"
    calendar_action_helper = stage / "calendar-actions-native" / "SatoriCalendarActions"
    specification.write_text(
        _specification(
            repository, entrypoint, calendar_helper, telegram_resources, calendar_action_helper
        ),
        encoding="utf-8",
    )
    log_path = stage / "build.log"
    _build_calendar_helper(repository, calendar_helper, log_path)
    _build_calendar_helper(repository, calendar_action_helper, log_path, create_only=True)
    arguments = [
        str(interpreter),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(stage / "dist"),
        "--workpath",
        str(stage / "work"),
        str(specification),
    ]
    with log_path.open("wb") as build_log:
        result = subprocess.run(
            arguments, cwd=stage, stdout=build_log, stderr=subprocess.STDOUT, check=False
        )
    staged_app = stage / "dist" / "Satori.app"
    if result.returncode != 0 or not staged_app.is_dir():
        raise OSError(f"desktop_package_build_failed; diagnostic log: {log_path}")
    if telegram_resources is not None:
        _restore_telegram_resources(staged_app, telegram_resources)
    _build_daily_helpers(repository, staged_app, stage, log_path)
    with log_path.open("ab") as build_log:
        for attribute in ("com.apple.FinderInfo", "com.apple.ResourceFork"):
            subprocess.run(
                ["/usr/bin/xattr", "-dr", attribute, str(staged_app)],
                stdout=build_log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        _sign_daily_helpers(staged_app, log_path)
        sign_arguments = ["/usr/bin/codesign", "--force"]
        if telegram_resources is None:
            sign_arguments.append("--deep")
        sign_arguments.extend(["--sign", "-", str(staged_app)])
        signed = subprocess.run(
            sign_arguments,
            stdout=build_log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if signed.returncode:
            raise OSError(f"desktop_package_sign_failed; diagnostic log: {log_path}")
    if telegram_resources is not None:
        _verify_telegram_resources(
            _TelegramResources(staged_app / "Contents/Resources/telegram", telegram_resources.files)
        )
    # Reserve the destination exclusively before copying. Even a destination
    # created during the build is preserved rather than replaced by rename().
    destination.mkdir(mode=0o700)
    created = destination.stat()
    try:
        shutil.copytree(
            staged_app, destination, dirs_exist_ok=True, symlinks=True, copy_function=shutil.copy
        )
        with log_path.open("ab") as build_log:
            verified = subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(destination)],
                stdout=build_log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if verified.returncode:
            raise OSError(f"desktop_package_signature_invalid; diagnostic log: {log_path}")
        if telegram_resources is not None:
            _verify_telegram_resources(
                _TelegramResources(
                    destination / "Contents/Resources/telegram", telegram_resources.files
                )
            )
    except BaseException:
        observed = destination.lstat()
        if (observed.st_dev, observed.st_ino) == (created.st_dev, created.st_ino):
            shutil.rmtree(destination)
        raise
    shutil.rmtree(stage)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a self-contained local Satori macOS app")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--repository-directory", type=Path)
    parser.add_argument("--python-executable", type=Path)
    parser.add_argument("--telegram-sdk-directory", type=Path)
    parser.add_argument("--reviewed-telegram-replies", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        destination = build_app_bundle(
            arguments.destination,
            arguments.repository_directory,
            arguments.python_executable,
            telegram_sdk_directory=arguments.telegram_sdk_directory,
            reviewed_telegram_replies=arguments.reviewed_telegram_replies,
        )
    except (OSError, ValueError, NotImplementedError) as error:
        print(f"Satori bundle could not be built: {error}", file=sys.stderr)
        return 2
    print(f"Created self-contained local app: {destination}")
    print("Local ad-hoc signature only; Developer ID distribution and notarization are separate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
