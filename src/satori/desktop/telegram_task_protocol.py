"""Fixed public task-query protocol; reading it never loads TDLib or an account."""

import hashlib
from pathlib import Path

TASK_PROTOCOL_PATH = "telegram/task_v1/protocol.json"
TASK_PROTOCOL_SHA256 = "4f4fcfb8110d8a23c34c9cfea41d564f68f13d64268e67f9cce4178495683063"


def verified_task_protocol(root: Path) -> None:
    path = root / TASK_PROTOCOL_PATH
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root):
        raise ValueError("telegram_task_protocol_invalid")
    if (
        not path.is_file()
        or path.stat().st_size > 16384
        or hashlib.sha256(path.read_bytes()).hexdigest() != TASK_PROTOCOL_SHA256
    ):
        raise ValueError("telegram_task_protocol_invalid")
