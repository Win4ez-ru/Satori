"""Qualified fixed worker entrypoint; synthetic software only, no native SDK/account."""

import hashlib
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from satori.desktop import telegram_worker


@pytest.fixture
def resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    contents = {
        "telegram/backend/worker.py.source": b"raise SystemExit(0)\n",
        "telegram/backend/schema.json": b"{}\n",
        "telegram/backend/PROTOCOL.md": b"Synthetic protocol\n",
        "telegram/sdk/libtdjson.1.8.67.dylib": b"synthetic-not-a-library",
        "telegram/policy.md": b"Synthetic bounded local-read policy\n",
    }
    pins = {}
    for relative, data in contents.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        pins[relative] = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(telegram_worker, "_SOFTWARE_PINS", pins)
    monkeypatch.setattr(telegram_worker, "resource_root", lambda: tmp_path)
    return tmp_path


def arguments(resources: Path) -> list[str]:
    return [
        "--library",
        str(resources / "telegram/sdk/libtdjson.1.8.67.dylib"),
        "--library-sha256",
        telegram_worker._SOFTWARE_PINS["telegram/sdk/libtdjson.1.8.67.dylib"],
        "--session-root",
        str(resources / "new-owned-parent"),
    ]


def test_only_fixed_qualified_software_runs_and_argv_is_restored(resources: Path) -> None:
    prior = list(sys.argv)
    assert telegram_worker.main(arguments(resources)) == 0
    assert sys.argv == prior
    assert not (resources / "new-owned-parent").exists()


def test_mutated_source_cannot_self_assert_a_new_manifest_hash(resources: Path) -> None:
    (resources / "telegram/backend/worker.py.source").write_text("raise SystemExit(19)\n")
    (resources / "telegram/runtime-config.json").write_text('{"trusted": true}')
    assert telegram_worker.main(arguments(resources)) == 3


def test_symlinked_payload_is_rejected(resources: Path) -> None:
    target = resources / "telegram/backend/schema.json"
    target.rename(resources / "schema-copy.json")
    target.symlink_to(resources / "schema-copy.json")
    with pytest.raises(ValueError, match="link_rejected"):
        telegram_worker.verified_software()


def test_foreign_library_path_cannot_be_admitted_by_matching_hash(resources: Path) -> None:
    args = arguments(resources)
    args[1] = str(resources / "foreign.dylib")
    assert telegram_worker.main(args) == 3


def test_byte_preflight_does_not_execute_worker_or_sdk(
    resources: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    del resources

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("worker execution is not a byte preflight")

    monkeypatch.setattr(runpy, "run_path", forbidden)
    assert telegram_worker.preflight() == 0
    assert "worker_started=false sdk_loaded=false" in capsys.readouterr().out


def test_frozen_resource_root_uses_bundle_data_without_external_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "Satori.app/Contents/MacOS/Satori"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    assert telegram_worker.resource_root() == tmp_path / "Satori.app/Contents/Resources"


def test_no_auth_probe_uses_only_wait_parameters_and_close(
    resources: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del resources
    calls: list[str] = []

    class Native:
        def __init__(self, _library: Path, _pin: str) -> None:
            self.state = "authorizationStateWaitTdlibParameters"

        def send(self, query: dict[str, Any]) -> None:
            calls.append(query["@type"])
            if query["@type"] == "close":
                self.state = "authorizationStateClosed"

        def receive(self, _timeout: float) -> dict[str, Any]:
            return {
                "@type": "updateAuthorizationState",
                "authorization_state": {"@type": self.state},
            }

    monkeypatch.setattr(runpy, "run_path", lambda *_a, **_k: {"NativeClient": Native})
    assert telegram_worker.native_no_auth_check() == 0
    assert calls == ["getAuthorizationState", "close"]


def test_no_auth_probe_still_closes_after_receive_failure(
    resources: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del resources
    calls: list[str] = []

    class Native:
        def __init__(self, _library: Path, _pin: str) -> None:
            self.closed = False

        def send(self, query: dict[str, Any]) -> None:
            calls.append(query["@type"])
            self.closed = query["@type"] == "close"

        def receive(self, _timeout: float) -> dict[str, Any]:
            if not self.closed:
                raise ValueError("synthetic closed failure")
            return {
                "@type": "updateAuthorizationState",
                "authorization_state": {"@type": "authorizationStateClosed"},
            }

    monkeypatch.setattr(runpy, "run_path", lambda *_a, **_k: {"NativeClient": Native})
    assert telegram_worker.native_no_auth_check() == 3
    assert calls == ["getAuthorizationState", "close"]
