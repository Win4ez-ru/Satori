"""Execute only the qualified bundled Telegram source in its own process."""

import argparse
import hashlib
import os
import runpy
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Protocol, cast

_SOFTWARE_PINS = {
    "telegram/backend/worker.py.source": (
        "69235f84d83bc49a01fba8a4e342775027d2a1a433fbef5bb25e88c127bef910"
    ),
    "telegram/backend/schema.json": (
        "cc50233f8e00c4ebef02498ff39b630322fdd029dde2698c84b114efb670f597"
    ),
    "telegram/backend/PROTOCOL.md": (
        "8f1bc1b44531e9d689df0a8de37d32d6318e9bfabb79e9891b98c39ab9e44841"
    ),
    "telegram/sdk/libtdjson.1.8.67.dylib": (
        "8df0ee7cfc9c0fc2479cec0a6cde0b57243ad9a53a6d7dff1b9a6606b5703ad8"
    ),
    "telegram/policy.md": ("bc76f956560195e01e88e085505297f3b87e1b4cbe2d24fcd1f5074c5f101eae"),
}


def resource_root() -> Path:
    """Resolve the actual data directory, including macOS bundle layout."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "Resources"
    return Path(__file__).resolve().parents[1] / "resources"


def verified_software(root: Path | None = None) -> tuple[Path, Path]:
    """Known source/library pins are code, never self-asserted manifest values."""
    base = (root if root is not None else resource_root()).resolve(strict=True)
    for relative, expected in _SOFTWARE_PINS.items():
        candidate = base / relative
        current = candidate
        while current != base:
            if current.is_symlink():
                raise ValueError("telegram_runtime_link_rejected")
            current = current.parent
        if (
            not candidate.is_file()
            or not candidate.resolve(strict=True).is_relative_to(base)
            or candidate.stat().st_size > 64 * 1024 * 1024
            or hashlib.sha256(candidate.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("telegram_runtime_pin_rejected")
    return base / "telegram/backend/worker.py.source", base / "telegram/sdk/libtdjson.1.8.67.dylib"


@contextmanager
def _owned_stdio() -> Iterator[None]:
    """Windowed frozen executables may need explicit inherited protocol streams."""
    original = (sys.stdin, sys.stdout, sys.stderr)
    with ExitStack() as streams:
        try:
            if sys.stdin is None:
                sys.stdin = streams.enter_context(os.fdopen(os.dup(0), "r", encoding="utf-8"))
            if sys.stdout is None:
                sys.stdout = streams.enter_context(os.fdopen(os.dup(1), "w", encoding="utf-8"))
            if sys.stderr is None:
                sys.stderr = streams.enter_context(open(os.devnull, "w", encoding="utf-8"))
            yield
        finally:
            sys.stdin, sys.stdout, sys.stderr = original


class _NativeProbe(Protocol):
    def send(self, query: dict[str, Any]) -> None: ...

    def receive(self, timeout: float) -> dict[str, Any] | None: ...


def native_no_auth_check() -> int:
    """Operator-only packaged-runtime check; no parameters, account or session folder."""
    native: _NativeProbe | None = None
    waiting = False
    closed = False
    try:
        source, library = verified_software()
        namespace = runpy.run_path(str(source), run_name="satori_telegram_qualification")
        factory = cast(Callable[[Path, str], _NativeProbe], namespace["NativeClient"])
        native = factory(library, _SOFTWARE_PINS["telegram/sdk/libtdjson.1.8.67.dylib"])
        native.send({"@type": "getAuthorizationState"})
        until = time.monotonic() + 5
        while time.monotonic() < until:
            value = native.receive(0.05)
            if value and value.get("@type") == "updateAuthorizationState":
                state = value.get("authorization_state")
                if (
                    isinstance(state, dict)
                    and state.get("@type") == "authorizationStateWaitTdlibParameters"
                ):
                    waiting = True
                    break
    except Exception:
        waiting = False
    finally:
        if native is not None:
            try:
                native.send({"@type": "close"})
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    value = native.receive(0.05)
                    if value and value.get("@type") == "updateAuthorizationState":
                        state = value.get("authorization_state")
                        if (
                            isinstance(state, dict)
                            and state.get("@type") == "authorizationStateClosed"
                        ):
                            closed = True
                            break
            except Exception:
                closed = False
    if not waiting or not closed:
        return 3
    print("TELEGRAM_NATIVE_NO_AUTH_PASS wait_parameters=true closed=true")
    return 0


def preflight() -> int:
    """Verify software bytes only; never load the SDK or start an account client."""
    try:
        verified_software()
    except (OSError, ValueError):
        return 3
    print("TELEGRAM_RUNTIME_PREFLIGHT_PASS worker_started=false sdk_loaded=false")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owned Satori Telegram worker")
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--library-sha256", required=True)
    parser.add_argument("--session-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        source, library = verified_software()
        if (
            arguments.library != library
            or arguments.library_sha256 != _SOFTWARE_PINS["telegram/sdk/libtdjson.1.8.67.dylib"]
            or not arguments.session_root.is_absolute()
        ):
            return 3
        prior = sys.argv
        try:
            sys.argv = [
                str(source),
                "--library",
                str(library),
                "--library-sha256",
                arguments.library_sha256,
                "--session-root",
                str(arguments.session_root),
            ]
            with _owned_stdio():
                runpy.run_path(str(source), run_name="__main__")
        except SystemExit as result:
            return result.code if type(result.code) is int else 3
        finally:
            sys.argv = prior
    except (OSError, ValueError):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
