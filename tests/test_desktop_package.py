"""Bundle resource boundaries and safe publication without a real packaging subprocess."""

import ast
import hashlib
import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from satori.desktop import package


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "checkout with spaces and 'quotes'"
    files = {
        "alembic.ini": "[alembic]\nscript_location = %(here)s/migrations\n",
        "PROJECT_SATORI.md": "product marker",
        "pyproject.toml": "[project]\nname='satori-core'\n",
        "migrations/env.py": "pass\n",
        "migrations/script.py.mako": "template",
        "migrations/versions/0001_initial.py": "revision = '0001'\n",
        "src/satori/__init__.py": "",
        "src/satori/desktop/__main__.py": "def main(): return 0\n",
        "src/satori/desktop/calendar_native/main.swift": "import Foundation\n",
        "src/satori/desktop/calendar_actions_native/main.swift": "import Foundation\n",
        "src/satori/desktop/reminder_native/main.swift": "import Foundation\n",
        "src/satori/infrastructure/source_policy/migrations/env.py": "pass\n",
        "src/satori/infrastructure/source_policy/migrations/versions/policy_0001.py": "pass\n",
        "src/satori/resources/seeds/satori-v1.json": "{}",
        "src/satori/resources/brand/satori-portrait-v1.png": "synthetic portrait fixture",
        "src/satori/resources/openai/desktop-pricing-v1.json": "{}",
        "var/satori.db": "private database must not be collected",
        ".env": "SYNTHETIC_SECRET=not-for-bundle",
        "tests/test_data.py": "private test material must not be collected",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    places = root / "src/satori/desktop/places_native"
    places.mkdir()
    for name in (
        "Models.swift",
        "Protocol.swift",
        "Lease.swift",
        "NativeProvider.swift",
        "MapWindow.swift",
        "Coordinator.swift",
        "SelfTest.swift",
        "Main.swift",
    ):
        (places / name).write_text("import Foundation\n")
    (root / "src/satori/desktop/reminder_native/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "ai.satori.desktop.reminder-notifications",
                "CFBundleExecutable": "SatoriReminderNotifications",
                "CFBundlePackageType": "APPL",
            }
        )
    )
    return root


def _fake_success(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
    if arguments[0] in {"/usr/bin/xattr", "/usr/bin/codesign"}:
        return subprocess.CompletedProcess(arguments, 0)
    if arguments[:2] == ["/usr/bin/xcrun", "swiftc"]:
        output = Path(arguments[arguments.index("-o") + 1])
        output.write_bytes(b"synthetic calendar helper")
        output.chmod(0o700)
        return subprocess.CompletedProcess(arguments, 0)
    dist = Path(arguments[arguments.index("--distpath") + 1])
    contents = dist / "Satori.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    executable = contents / "MacOS" / "Satori"
    executable.write_bytes(b"synthetic bundle executable")
    executable.chmod(0o700)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"SatoriPackaged": True}))
    (contents / "Resources").mkdir()
    (contents / "Resources" / "executable-link").symlink_to("../MacOS/Satori")
    return subprocess.CompletedProcess(arguments, 0)


def test_resource_allowlist_excludes_profile_secrets_and_tests(repository: Path) -> None:
    collected = package._runtime_files(repository)
    paths = [Path(source).relative_to(repository).as_posix() for source, _target in collected]
    assert paths == [
        "alembic.ini",
        "PROJECT_SATORI.md",
        "pyproject.toml",
        "migrations/env.py",
        "migrations/script.py.mako",
        "src/satori/resources/seeds/satori-v1.json",
        "src/satori/resources/brand/satori-portrait-v1.png",
        "src/satori/resources/openai/desktop-pricing-v1.json",
        "migrations/versions/0001_initial.py",
        "src/satori/infrastructure/source_policy/migrations/env.py",
        "src/satori/infrastructure/source_policy/migrations/versions/policy_0001.py",
    ]
    assert (
        str(repository / "src/satori/resources/seeds/satori-v1.json"),
        "satori/resources/seeds",
    ) in collected
    assert (
        str(repository / "src/satori/resources/openai/desktop-pricing-v1.json"),
        "satori/resources/openai",
    ) in collected


def test_specification_is_valid_python_with_quoted_paths(repository: Path, tmp_path: Path) -> None:
    specification = package._specification(repository, tmp_path / "entry 'quoted'.py")
    ast.parse(specification)
    assert "console=False" in specification
    assert "SatoriPackaged" in specification
    assert "org.satori.desktop.development" in specification
    assert "codesign_identity=None" in specification
    assert "sqlalchemy.dialects.sqlite.pysqlite" in specification
    assert "logging.config" in specification
    assert "alembic.context" in specification
    assert "satori.desktop.__main__" in specification
    assert "NSMicrophoneUsageDescription" in specification
    assert "NSCalendarsFullAccessUsageDescription" in specification
    assert "onnx_asr" in specification
    assert "preprocessors/data/*" in specification
    assert "lib/*.metallib" in specification
    assert "mlx_audio.tts.models.qwen3_tts" in specification
    assert "collect_dynamic_libs('mlx')" in specification
    assert "copy_metadata" in specification
    assert str(repository / ".env") not in specification
    assert "satori.db" not in specification


def test_success_publishes_complete_bundle_and_preserves_links(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", _fake_success)
    destination = tmp_path / "output" / "Satori.app"
    assert package.build_app_bundle(destination, repository, Path(sys.executable)) == destination
    link = destination / "Contents" / "Resources" / "executable-link"
    assert link.is_symlink()
    assert link.read_bytes() == b"synthetic bundle executable"
    assert list(destination.parent.iterdir()) == [destination]
    assert (repository / "var" / "satori.db").read_text().startswith("private database")


def test_existing_app_is_preserved_without_running_builder(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    destination = tmp_path / "Satori.app"
    destination.mkdir()
    (destination / "user-file").write_text("preserve")
    with pytest.raises(FileExistsError, match="destination_exists"):
        package.build_app_bundle(destination, repository, Path(sys.executable))
    assert (destination / "user-file").read_text() == "preserve"


def test_failed_build_keeps_diagnostics_and_never_publishes_app(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    def fail(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(arguments, 1)

    monkeypatch.setattr(subprocess, "run", fail)
    destination = tmp_path / "output" / "Satori.app"
    with pytest.raises(OSError, match="diagnostic log") as error:
        package.build_app_bundle(destination, repository, Path(sys.executable))
    assert not destination.exists()
    log_path = Path(str(error.value).split("diagnostic log: ")[1])
    assert log_path.is_file()
    assert (log_path.parent / "Satori.spec").is_file()


def test_destination_created_during_build_is_preserved(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    destination = tmp_path / "Satori.app"

    def collision(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        result = _fake_success(arguments, **kwargs)
        if "PyInstaller" in arguments:
            destination.mkdir()
            (destination / "unrelated").write_text("keep")
        return result

    monkeypatch.setattr(subprocess, "run", collision)
    with pytest.raises(FileExistsError):
        package.build_app_bundle(destination, repository, Path(sys.executable))
    assert (destination / "unrelated").read_text() == "keep"


def test_symlink_resource_is_rejected(repository: Path, tmp_path: Path) -> None:
    resource = repository / "src/satori/resources/seeds/satori-v1.json"
    resource.unlink()
    target = tmp_path / "outside.json"
    target.write_text("outside")
    resource.symlink_to(target)
    with pytest.raises(FileNotFoundError, match="runtime_resource"):
        package._runtime_files(repository)


@pytest.mark.parametrize("failed_step", ["--sign", "--verify"])
def test_signature_failure_never_publishes_an_accepted_bundle(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_step: str
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    def fail_sign(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if failed_step in arguments:
            return subprocess.CompletedProcess(arguments, 1)
        return _fake_success(arguments, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_sign)
    destination = tmp_path / "Satori.app"
    with pytest.raises(OSError, match="diagnostic log"):
        package.build_app_bundle(destination, repository, Path(sys.executable))
    assert not destination.exists()


def test_other_platform_is_explicitly_unsupported(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(NotImplementedError, match="macos_only"):
        package.build_app_bundle(tmp_path / "Satori.app", repository, Path(sys.executable))


def test_calendar_helper_is_bundled_without_source_or_build_tools(
    repository: Path, tmp_path: Path
) -> None:
    helper = tmp_path / "private stage" / "SatoriCalendar"
    specification = package._specification(repository, tmp_path / "entry.py", helper)
    ast.parse(specification)
    assert repr((str(helper), "calendar-native")) in specification
    assert "main.swift" not in specification
    assert "module-cache" not in specification


def test_calendar_compile_failure_does_not_run_packager_or_publish(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    commands: list[list[str]] = []

    def fail(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 1)

    monkeypatch.setattr(subprocess, "run", fail)
    target = tmp_path / "Satori.app"
    with pytest.raises(OSError, match="calendar_compile_failed"):
        package.build_app_bundle(target, repository, Path(sys.executable))
    assert len(commands) == 1
    assert commands[0][:2] == ["/usr/bin/xcrun", "swiftc"]
    assert not target.exists()


def test_calendar_compiler_permission_description_and_private_cache(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def compile_only(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(arguments)
        return _fake_success(arguments, **kwargs)

    monkeypatch.setattr(subprocess, "run", compile_only)
    output = tmp_path / "native output" / "SatoriCalendar"
    package._build_calendar_helper(repository, output, tmp_path / "build.log")
    info = plistlib.loads(output.with_name("Info.plist").read_bytes())
    assert "чтение и запись" in info["NSCalendarsFullAccessUsageDescription"]
    assert "явно выбран" in info["NSCalendarsFullAccessUsageDescription"]
    assert commands[0][commands[0].index("-module-cache-path") + 1] == str(
        output.parent / "module-cache"
    )
    assert output.is_file()


@pytest.fixture
def telegram_sdk(repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Authored non-loadable software bytes; no SDK or account operation."""
    sdk = tmp_path / "qualified sdk"
    payload = {
        "telegram/backend/worker.py.source": b"# synthetic worker source\n",
        "telegram/backend/schema.json": b'{"synthetic":true}\n',
        "telegram/backend/PROTOCOL.md": b"Synthetic local protocol\n",
        "telegram/sdk/libtdjson.1.8.67.dylib": b"synthetic non-loadable library\n",
        "telegram/policy.md": b"Synthetic accepted policy\n",
    }
    for relative, data in payload.items():
        if relative == "telegram/sdk/libtdjson.1.8.67.dylib":
            path = sdk / "lib/libtdjson.1.8.67.dylib"
        elif relative == "telegram/policy.md":
            path = repository / package._TELEGRAM_POLICY
        else:
            path = repository / "src/satori/resources" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    licenses = {}
    for name in package._TELEGRAM_LICENSES:
        data = f"Synthetic license: {name}\n".encode()
        path = sdk / "share/licenses" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        licenses[name] = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(
        package,
        "_TELEGRAM_SOFTWARE_PINS",
        {relative: hashlib.sha256(data).hexdigest() for relative, data in payload.items()},
    )
    monkeypatch.setattr(package, "_TELEGRAM_LICENSES", licenses)
    return sdk


def test_telegram_allowlist_config_has_only_relative_public_software(
    repository: Path, telegram_sdk: Path, tmp_path: Path
) -> None:
    (telegram_sdk / "private-session.json").write_text("must not collect")
    (telegram_sdk / "lib/unqualified.dylib").write_text("must not collect")
    resources = package._prepare_telegram_resources(repository, telegram_sdk, tmp_path / "stage")
    paths = {relative for relative, _digest, _size in resources.files}
    assert paths == {
        "backend/worker.py.source",
        "backend/schema.json",
        "backend/PROTOCOL.md",
        "sdk/libtdjson.1.8.67.dylib",
        "policy.md",
        "runtime-config.json",
        "provenance.json",
        *(f"licenses/{name}" for name in package._TELEGRAM_LICENSES),
    }
    config_data = (resources.directory / "runtime-config.json").read_text()
    config = json.loads(config_data)
    assert set(config) == {"v", "worker", "library", "payload"}
    assert config["v"] == 2
    assert config["worker"]["path"] == "telegram/backend/worker.py.source"
    assert config["library"]["path"] == "telegram/sdk/libtdjson.1.8.67.dylib"
    assert {item["path"] for item in config["payload"]} == {
        "telegram/backend/worker.py.source",
        "telegram/backend/schema.json",
        "telegram/sdk/libtdjson.1.8.67.dylib",
    }
    assert len(config["payload"]) == 3
    for item in config["payload"]:
        data = (resources.directory / item["path"].removeprefix("telegram/")).read_bytes()
        assert item["bytes"] == len(data)
        assert item["sha256"] == hashlib.sha256(data).hexdigest()
    assert str(repository) not in config_data
    assert str(telegram_sdk) not in config_data
    assert "Python.framework" not in config_data
    assert "session_root" not in config_data
    provenance = json.loads((resources.directory / "provenance.json").read_text())
    assert provenance["version"] == "1.8.67"
    assert provenance["account_acceptance"] == "NOT_RUN"
    specification = package._specification(
        repository, tmp_path / "entry.py", telegram_resources=resources
    )
    ast.parse(specification)
    assert "private-session.json" not in specification
    assert "unqualified.dylib" not in specification
    assert "'CFBundleVersion': '26'" in specification


def test_telegram_package_config_is_accepted_by_the_actual_runtime_loader(
    repository: Path,
    telegram_sdk: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Join producer and consumer with authored pins, never a loadable SDK."""
    from satori.desktop import telegram_runtime
    from satori.desktop.telegram_port import SCHEMA_KEYS

    schema = {key: ["FIXTURE"] for key in SCHEMA_KEYS - {"v"}}
    schema["page_stale_reason"] = ["NEW_MESSAGE"]
    schema_data = (json.dumps({"v": 1, **schema}, sort_keys=True) + "\n").encode()
    (repository / "src/satori/resources" / telegram_runtime.SCHEMA_PATH).write_bytes(schema_data)
    policy = b"S" * 6951
    (repository / package._TELEGRAM_POLICY).write_bytes(policy)
    pins = {}
    for relative in (
        telegram_runtime.WORKER_PATH,
        telegram_runtime.SCHEMA_PATH,
        telegram_runtime.LIBRARY_PATH,
        telegram_runtime.POLICY_PATH,
        "telegram/backend/PROTOCOL.md",
    ):
        if relative == telegram_runtime.LIBRARY_PATH:
            source = telegram_sdk / "lib/libtdjson.1.8.67.dylib"
        elif relative == telegram_runtime.POLICY_PATH:
            source = repository / package._TELEGRAM_POLICY
        else:
            source = repository / "src/satori/resources" / relative
        pins[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(package, "_TELEGRAM_SOFTWARE_PINS", pins)
    for name, path in (
        ("WORKER_SHA256", telegram_runtime.WORKER_PATH),
        ("SCHEMA_SHA256", telegram_runtime.SCHEMA_PATH),
        ("LIBRARY_SHA256", telegram_runtime.LIBRARY_PATH),
        ("POLICY_SHA256", telegram_runtime.POLICY_PATH),
    ):
        monkeypatch.setattr(telegram_runtime, name, pins[path])
    expected_payload = {}
    for relative in (
        telegram_runtime.WORKER_PATH,
        telegram_runtime.SCHEMA_PATH,
        telegram_runtime.LIBRARY_PATH,
    ):
        source = (
            telegram_sdk / "lib/libtdjson.1.8.67.dylib"
            if relative == telegram_runtime.LIBRARY_PATH
            else repository / "src/satori/resources" / relative
        )
        expected_payload[relative] = (pins[relative], source.stat().st_size)
    monkeypatch.setattr(telegram_runtime, "EXPECTED_PAYLOAD", expected_payload)
    resources = package._prepare_telegram_resources(repository, telegram_sdk, tmp_path / "telegram")
    config = (resources.directory / "runtime-config.json").read_bytes()
    assert config == telegram_runtime.runtime_config_bytes()
    accepted = telegram_runtime.load_runtime_resources(tmp_path)
    assert accepted.root == tmp_path
    assert accepted.config_sha256 == hashlib.sha256(config).hexdigest()
    assert accepted.library == resources.directory / "sdk/libtdjson.1.8.67.dylib"


@pytest.mark.parametrize(
    "relative",
    ["lib/libtdjson.1.8.67.dylib", "share/licenses/TDLib-Boost-1.0.txt"],
)
def test_telegram_tampered_sdk_or_license_fails_before_builder(
    repository: Path,
    telegram_sdk: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    calls: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    (telegram_sdk / relative).write_bytes(b"tampered")
    destination = tmp_path / "Satori.app"
    with pytest.raises(ValueError, match="telegram_pin_rejected"):
        package.build_app_bundle(
            destination, repository, Path(sys.executable), telegram_sdk_directory=telegram_sdk
        )
    assert not destination.exists()
    assert calls == []


def test_telegram_source_pin_is_not_supplied_by_sdk_manifest(
    repository: Path, telegram_sdk: Path, tmp_path: Path
) -> None:
    source = repository / "src/satori/resources/telegram/backend/worker.py.source"
    source.write_text("arbitrary replacement")
    (telegram_sdk / "sdk-manifest.json").write_text(
        json.dumps({"worker_sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    )
    with pytest.raises(ValueError, match="telegram_pin_rejected"):
        package._prepare_telegram_resources(repository, telegram_sdk, tmp_path / "stage")


@pytest.mark.parametrize("linked", ["library", "directory", "license"])
def test_telegram_input_symlinks_are_rejected(
    repository: Path, telegram_sdk: Path, tmp_path: Path, linked: str
) -> None:
    if linked == "directory":
        link = tmp_path / "sdk-link"
        link.symlink_to(telegram_sdk, target_is_directory=True)
        telegram_sdk = link
    else:
        path = telegram_sdk / (
            "lib/libtdjson.1.8.67.dylib"
            if linked == "library"
            else "share/licenses/OpenSSL-Apache-2.0.txt"
        )
        outside = tmp_path / f"outside-{linked}"
        path.rename(outside)
        path.symlink_to(outside)
    with pytest.raises(OSError, match="symlink_rejected"):
        package._prepare_telegram_resources(repository, telegram_sdk, tmp_path / "stage")


def test_optional_telegram_legacy_build_has_no_telegram_resources(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(arguments)
        return _fake_success(arguments, **kwargs)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", run)
    destination = tmp_path / "Satori.app"
    package.build_app_bundle(destination, repository, Path(sys.executable))
    assert not (destination / "Contents/Resources/telegram").exists()
    signing = next(
        args for args in commands if "--sign" in args and Path(args[-1]).name == "Satori.app"
    )
    assert "--deep" in signing


def test_telegram_restores_exact_library_and_signs_outer_bundle_only(
    repository: Path,
    telegram_sdk: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    outside = tmp_path / "outside-original"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_text("unchanged")

    def relocate(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(arguments)
        result = _fake_success(arguments, **kwargs)
        if "PyInstaller" in arguments:
            contents = Path(arguments[arguments.index("--distpath") + 1]) / "Satori.app/Contents"
            # Model PyInstaller's rewritten/symlinked data directory, never a real dylib load.
            (contents / "Resources/telegram").symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", relocate)
    destination = tmp_path / "Satori.app"
    package.build_app_bundle(
        destination, repository, Path(sys.executable), telegram_sdk_directory=telegram_sdk
    )
    target = destination / "Contents/Resources/telegram"
    library = target / "sdk/libtdjson.1.8.67.dylib"
    assert not target.is_symlink()
    assert not library.is_symlink()
    assert library.read_bytes() == (telegram_sdk / "lib/libtdjson.1.8.67.dylib").read_bytes()
    assert sentinel.read_text() == "unchanged"
    assert not (target / "keep").exists()
    signing = next(
        args for args in commands if "--sign" in args and Path(args[-1]).name == "Satori.app"
    )
    assert "--deep" not in signing
    verifying = next(
        args for args in commands if "--verify" in args and Path(args[-1]).name == "Satori.app"
    )
    assert "--deep" in verifying
    assert "--strict" in verifying


@pytest.mark.parametrize("phase", ["sign", "destination_copy"])
def test_telegram_post_package_byte_tampering_is_not_published(
    repository: Path,
    telegram_sdk: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    destination = tmp_path / "Satori.app"

    def tamper(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        result = _fake_success(arguments, **kwargs)
        if Path(arguments[-1]).name == "Satori.app" and (
            (phase == "sign" and "--sign" in arguments)
            or (phase == "destination_copy" and "--verify" in arguments)
        ):
            app = Path(arguments[-1])
            (app / "Contents/Resources/telegram/sdk/libtdjson.1.8.67.dylib").write_bytes(
                b"rewritten during packaging"
            )
        return result

    monkeypatch.setattr(subprocess, "run", tamper)
    with pytest.raises(ValueError, match="telegram_pin_rejected"):
        package.build_app_bundle(
            destination, repository, Path(sys.executable), telegram_sdk_directory=telegram_sdk
        )
    assert not destination.exists()


def test_telegram_refuses_a_symlinked_bundle_resources_parent(
    repository: Path, telegram_sdk: Path, tmp_path: Path
) -> None:
    resources = package._prepare_telegram_resources(repository, telegram_sdk, tmp_path / "stage")
    app = tmp_path / "Satori.app"
    (app / "Contents").mkdir(parents=True)
    outside = tmp_path / "outside-resources"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_text("unchanged")
    (app / "Contents/Resources").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="bundle_directory_rejected"):
        package._restore_telegram_resources(app, resources)
    assert sentinel.read_text() == "unchanged"


def test_daily_helpers_are_allowlisted_post_packager_resources_and_signed_children(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = repository / "src/satori/desktop"
    (source_root / "places_native/unqualified.swift").write_text("must not compile or bundle")
    (source_root / "reminder_native/private.json").write_text("must not bundle")
    commands: list[list[str]] = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(arguments)
        return _fake_success(arguments, **kwargs)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", run)
    destination = tmp_path / "Satori.app"
    package.build_app_bundle(destination, repository, Path(sys.executable))
    resources = destination / "Contents/Resources"
    places = resources / "places-native/SatoriPlaces"
    reminder = resources / "reminder-native/SatoriReminderNotifications.app"
    assert places.is_file()
    assert (reminder / "Contents/MacOS/SatoriReminderNotifications").is_file()
    info = plistlib.loads((reminder / "Contents/Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "ai.satori.desktop.reminder-notifications"
    assert not list(resources.rglob("*.swift"))
    assert not list(resources.rglob("module-cache"))
    assert not list(resources.rglob("private.json"))
    builder = next(i for i, cmd in enumerate(commands) if "PyInstaller" in cmd)
    places_compile = next(
        i for i, cmd in enumerate(commands) if "-strict-concurrency=complete" in cmd
    )
    reminder_compile = next(
        i
        for i, cmd in enumerate(commands)
        if any("reminder_native/main.swift" in arg for arg in cmd)
    )
    root_sign = next(
        i
        for i, cmd in enumerate(commands)
        if "--sign" in cmd and Path(cmd[-1]).name == "Satori.app"
    )
    assert builder < places_compile < reminder_compile < root_sign
    compile_command = commands[places_compile]
    assert compile_command[compile_command.index("-swift-version") + 1] == "6"
    assert "-warnings-as-errors" in compile_command
    assert compile_command[compile_command.index("-target") + 1].endswith("-apple-macosx14.0")
    assert [Path(arg).name for arg in compile_command if arg.endswith(".swift")] == [
        "Models.swift",
        "Protocol.swift",
        "Lease.swift",
        "NativeProvider.swift",
        "MapWindow.swift",
        "Coordinator.swift",
        "SelfTest.swift",
        "Main.swift",
    ]
    assert not any("unqualified.swift" in arg for cmd in commands for arg in cmd)
    children = [cmd for cmd in commands[:root_sign] if "--sign" in cmd]
    assert [Path(cmd[-1]).name for cmd in children] == [
        "SatoriPlaces",
        "SatoriReminderNotifications.app",
    ]
    assert all("--deep" not in cmd for cmd in children)


@pytest.mark.parametrize(
    "phase", ["places_compile", "reminder_compile", "child_sign", "child_verify"]
)
def test_daily_helper_failure_keeps_diagnostics_and_never_publishes(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    commands: list[list[str]] = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(arguments)
        fail = (
            (phase == "places_compile" and "-strict-concurrency=complete" in arguments)
            or (
                phase == "reminder_compile"
                and any("reminder_native/main.swift" in arg for arg in arguments)
            )
            or (
                phase == "child_sign"
                and "--sign" in arguments
                and Path(arguments[-1]).name == "SatoriReminderNotifications.app"
            )
            or (
                phase == "child_verify"
                and "--verify" in arguments
                and Path(arguments[-1]).name == "SatoriReminderNotifications.app"
            )
        )
        return (
            subprocess.CompletedProcess(arguments, 1)
            if fail
            else _fake_success(arguments, **kwargs)
        )

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", run)
    destination = tmp_path / "Satori.app"
    with pytest.raises(OSError, match="diagnostic log") as error:
        package.build_app_bundle(destination, repository, Path(sys.executable))
    assert not destination.exists()
    assert not any("--sign" in cmd and Path(cmd[-1]).name == "Satori.app" for cmd in commands)
    assert Path(str(error.value).split("diagnostic log: ")[1]).is_file()


@pytest.mark.parametrize("relative", ["places_native/Lease.swift", "reminder_native/Info.plist"])
def test_daily_source_symlink_is_rejected_before_any_builder(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    source = repository / "src/satori/desktop" / relative
    outside = tmp_path / "outside-source"
    source.rename(outside)
    source.symlink_to(outside)
    commands: list[object] = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: commands.append(args))
    with pytest.raises(OSError, match="symlink_rejected"):
        package.build_app_bundle(tmp_path / "Satori.app", repository, Path(sys.executable))
    assert commands == []


def test_daily_resource_collision_never_follows_builder_symlink(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "unrelated"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_text("unchanged")

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        result = _fake_success(arguments, **kwargs)
        if "PyInstaller" in arguments:
            app = Path(arguments[arguments.index("--distpath") + 1]) / "Satori.app"
            (app / "Contents/Resources/reminder-native").symlink_to(
                outside, target_is_directory=True
            )
        return result

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(FileExistsError, match="daily_resource_collision"):
        package.build_app_bundle(tmp_path / "Satori.app", repository, Path(sys.executable))
    assert sentinel.read_text() == "unchanged"
