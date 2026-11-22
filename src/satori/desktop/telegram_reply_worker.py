"""Separately opted-in reply-v1 worker, reusing the exact read receive lifecycle."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from satori.application.assistant.contracts import LocalPrincipal
from satori.desktop.telegram_reply_native import build_reply_engine, legacy_definitions
from satori.desktop.telegram_task_protocol import verified_task_protocol
from satori.desktop.telegram_worker import _owned_stdio, resource_root, verified_software

PROTOCOL_PATH = "telegram/reply_v1/protocol.json"
PROTOCOL_SHA256 = "f62c802f76414955ad79006044a287bf7617c4a3a8857cf5e5733a73b11c59a7"


def verified_reply_protocol(root: Path) -> None:
    path = root / PROTOCOL_PATH
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("reply_protocol_rejected")
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != PROTOCOL_SHA256:
        raise ValueError("reply_protocol_rejected")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit Satori reviewed Telegram reply v1")
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--library-sha256", required=True)
    parser.add_argument("--session-root", type=Path, required=True)
    parser.add_argument("--reply-principal", required=True)
    parser.add_argument("--reply-generation", type=int, required=True)
    arguments = parser.parse_args(argv)
    try:
        source, library = verified_software()
        verified_reply_protocol(resource_root())
        verified_task_protocol(resource_root())
        principal = LocalPrincipal.model_validate_json(arguments.reply_principal)
        profile = arguments.session_root.parent
        if (
            arguments.library != library
            or not arguments.session_root.is_absolute()
            or arguments.session_root.name != "telegram-temporary"
            or principal.profile_id != uuid5(NAMESPACE_URL, str(profile.resolve()))
            or arguments.reply_generation < 1
        ):
            return 3
        legacy = legacy_definitions(source)
        fixed_definitions = dict(legacy)

        def factory(*args: Any, **kwargs: Any) -> Any:
            return build_reply_engine(
                fixed_definitions,
                *args,
                **kwargs,
                principal=principal,
                generation=arguments.reply_generation,
                journal_path=profile / "telegram-replies" / "attempts.sqlite3",
            )

        # This is a private namespace loaded from the fixed old source. The old
        # resource and the ordinary read-worker entry point remain unchanged.
        entry = legacy["main"]
        entry.__globals__["Engine"] = factory
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
                result = entry()
            return result if type(result) is int else 3
        finally:
            sys.argv = prior
    except Exception:
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
