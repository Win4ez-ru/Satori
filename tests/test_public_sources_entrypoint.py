"""Finite source-worker CLI admission; no UI, Core, accounts or source I/O."""

import builtins
import errno
import json
import os
import subprocess
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from satori.desktop import __main__ as desktop_main
from satori.infrastructure.public_sources.wire import MAX_INPUT_BYTES

WORKER_MODULE = "satori.infrastructure.public_sources.worker"
FLAG = "--public-source-worker"


def _fake_worker(monkeypatch: pytest.MonkeyPatch, callback: Callable[[], int]) -> None:
    module = types.ModuleType(WORKER_MODULE)
    module.main = callback  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, WORKER_MODULE, module)


def test_exact_worker_flag_routes_once_and_returns_worker_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    def main() -> int:
        calls.append("source-worker")
        return 17

    _fake_worker(monkeypatch, main)
    monkeypatch.setattr(sys, "argv", ["satori", FLAG])
    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        assert name not in {"satori.desktop.ui", "satori.desktop.worker", "PySide6"}
        assert not name.startswith(("satori.desktop.voice", "satori.desktop.telegram_worker"))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert desktop_main.main() == 17
    assert calls == ["source-worker"]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "arguments",
    [["unexpected"], ["--help"], ["--worker"], ["--repository-directory", "/SYNTHETIC/PATH"]],
)
def test_worker_flag_rejects_trailing_arguments_before_any_worker_or_ui_import(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["satori", FLAG, *arguments])
    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        assert name != WORKER_MODULE
        assert not name.startswith(("satori.desktop.ui", "satori.desktop.worker", "PySide6"))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert desktop_main.main() == 2
    assert capsys.readouterr() == ("", "")


def _environment() -> dict[str, str]:
    # Deliberately omit ambient account keys, proxies, HOME and browser/session state.
    return {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
    }


@pytest.mark.parametrize(
    "data",
    [
        b'{"schema_version":1,"operation":"invalid","query":"SYNTHETIC MARKER"}',
        b'{"schema_version":1,"schema_version":1}',
        b"x" * (MAX_INPUT_BYTES + 1),
    ],
    ids=["invalid-operation", "duplicate-keys", "oversize-input"],
)
def test_actual_module_worker_rejects_invalid_bounded_pipe_without_source_io(data: bytes) -> None:
    command = [sys.executable, "-m", "satori.desktop", FLAG]
    result = subprocess.run(
        command, input=data, capture_output=True, timeout=10, check=False, env=_environment()
    )
    assert command == [sys.executable, "-m", "satori.desktop", FLAG]
    assert result.returncode == 2
    assert result.stderr == b""
    assert len(result.stdout) < 256
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "status": "rejected",
        "reason": "invalid_request",
    }
    assert b"SYNTHETIC MARKER" not in result.stdout


def test_fresh_process_worker_has_no_ui_core_or_account_sdk_import_path() -> None:
    bootstrap = """
import importlib.abc
import runpy
import socket
import ssl
import sys

def no_network(*args, **kwargs):
    raise AssertionError('forbidden_network_operation')

socket.socket = no_network
socket.create_connection = no_network
socket.getaddrinfo = no_network

class DenyHeavy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = (
            'PySide6', 'satori.desktop.ui', 'satori.desktop.worker',
            'satori.desktop.telegram_worker', 'satori.desktop.voice',
            'satori.application', 'satori.composition', 'sqlalchemy',
            'onnxruntime', 'mlx', 'satori.infrastructure.gmail',
            'satori.infrastructure.telegram',
        )
        if any(fullname == name or fullname.startswith(name + '.') for name in forbidden):
            raise AssertionError('forbidden_heavy_import')
        return None

sys.meta_path.insert(0, DenyHeavy())
sys.argv = ['satori', '--public-source-worker']
runpy.run_module('satori.desktop', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", bootstrap],
        input=b"{}",
        capture_output=True,
        timeout=10,
        check=False,
        env=_environment(),
    )
    assert result.returncode == 2
    assert result.stderr == b""
    assert json.loads(result.stdout)["reason"] == "invalid_request"


@pytest.mark.parametrize(
    "missing", [("stdin",), ("stdout",), ("stderr",), ("stdin", "stdout", "stderr")]
)
def test_missing_streams_use_owned_descriptors_then_restore_original_references(
    monkeypatch: pytest.MonkeyPatch, missing: tuple[str, ...]
) -> None:
    original = (sys.stdin, sys.stdout, sys.stderr)
    descriptors: list[int] = []

    def worker() -> int:
        for name, previous in zip(("stdin", "stdout", "stderr"), original, strict=True):
            stream = getattr(sys, name)
            if name in missing:
                assert stream is not None
                assert stream.buffer is not None
                descriptors.append(stream.fileno())
            else:
                assert stream is previous
        return 23

    with monkeypatch.context() as patch:
        for name in missing:
            patch.setattr(sys, name, None)
        expected = (sys.stdin, sys.stdout, sys.stderr)
        patch.setattr(sys, "argv", ["satori", FLAG])
        _fake_worker(patch, worker)
        assert desktop_main.main() == 23
        assert (sys.stdin, sys.stdout, sys.stderr) == expected
    assert (sys.stdin, sys.stdout, sys.stderr) == original
    for descriptor in descriptors:
        with pytest.raises(OSError, match=r"\[Errno") as error:
            os.fstat(descriptor)
        assert error.value.errno == errno.EBADF
    for descriptor in (0, 1, 2):
        os.fstat(descriptor)


@pytest.mark.parametrize("unavailable", [0, 1, 2])
def test_unavailable_standard_descriptor_is_closed_without_raw_exception_or_worker_import(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], unavailable: int
) -> None:
    original_dup = os.dup
    duplicates: list[int] = []
    worker_called: list[bool] = []

    def duplicate(descriptor: int) -> int:
        if descriptor == unavailable:
            raise OSError("SYNTHETIC PRIVATE FD EXCEPTION")
        result = original_dup(descriptor)
        duplicates.append(result)
        return result

    def worker() -> int:
        worker_called.append(True)
        return 0

    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdin", None)
        patch.setattr(sys, "stdout", None)
        patch.setattr(sys, "stderr", None)
        patch.setattr(sys, "argv", ["satori", FLAG])
        patch.setattr(os, "dup", duplicate)
        _fake_worker(patch, worker)
        assert desktop_main.main() == 1
        assert (sys.stdin, sys.stdout, sys.stderr) == (None, None, None)
    assert worker_called == []
    assert capsys.readouterr() == ("", "")
    for descriptor in duplicates:
        with pytest.raises(OSError, match=r"\[Errno") as error:
            os.fstat(descriptor)
        assert error.value.errno == errno.EBADF


def test_fdopen_failure_closes_already_owned_duplicate_and_restores_streams(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original_dup = os.dup
    duplicates: list[int] = []

    def duplicate(descriptor: int) -> int:
        result = original_dup(descriptor)
        duplicates.append(result)
        return result

    def fail_open(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise OSError("SYNTHETIC PRIVATE OPEN EXCEPTION")

    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdin", None)
        patch.setattr(sys, "argv", ["satori", FLAG])
        patch.setattr(os, "dup", duplicate)
        patch.setattr(os, "fdopen", fail_open)
        assert desktop_main.main() == 1
        assert sys.stdin is None
    assert capsys.readouterr() == ("", "")
    assert len(duplicates) == 1
    with pytest.raises(OSError, match=r"\[Errno") as error:
        os.fstat(duplicates[0])
    assert error.value.errno == errno.EBADF


def test_actual_windowed_missing_streams_keep_owned_pipe_protocol_and_no_heavy_imports() -> None:
    bootstrap = """
import importlib.abc
import socket
import ssl
import sys

def no_network(*args, **kwargs):
    raise AssertionError('forbidden_network_operation')

socket.socket = no_network
socket.create_connection = no_network
socket.getaddrinfo = no_network

class DenyHeavy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = (
            'PySide6', 'satori.desktop.ui', 'satori.desktop.worker',
            'satori.desktop.telegram_worker', 'satori.desktop.voice',
            'satori.application', 'satori.composition', 'sqlalchemy',
            'onnxruntime', 'mlx', 'satori.infrastructure.gmail',
            'satori.infrastructure.telegram',
        )
        if any(fullname == name or fullname.startswith(name + '.') for name in forbidden):
            raise AssertionError('forbidden_heavy_import')
        return None

sys.meta_path.insert(0, DenyHeavy())
from satori.desktop.__main__ import main
original = (sys.stdin, sys.stdout, sys.stderr)
sys.stdin = sys.stdout = sys.stderr = None
sys.argv = ['satori', '--public-source-worker']
code = main()
assert (sys.stdin, sys.stdout, sys.stderr) == (None, None, None)
sys.stdin, sys.stdout, sys.stderr = original
raise SystemExit(code)
"""
    result = subprocess.run(
        [sys.executable, "-c", bootstrap],
        input=b"{}",
        capture_output=True,
        timeout=10,
        check=False,
        env=_environment(),
    )
    assert result.returncode == 2
    assert result.stderr == b""
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "status": "rejected",
        "reason": "invalid_request",
    }
