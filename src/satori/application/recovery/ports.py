"""Narrow storage ports; no domain write, model or Character dependency."""

from pathlib import Path
from typing import Protocol

from satori.application.recovery.contracts import SnapshotInventory


class StoredSnapshotPort(Protocol):
    """Pure committed-state capture and trusted-schema staged inspection."""

    @property
    def source_path(self) -> Path: ...

    def capture(self, destination: Path) -> SnapshotInventory: ...

    def inspect(self, snapshot: Path) -> SnapshotInventory: ...


class PrivateArtifactPort(Protocol):
    """Private regular-file input and atomic explicit-overwrite publication."""

    def read(self, path: Path, *, max_bytes: int) -> bytes: ...

    def write(self, path: Path, payload: bytes, *, overwrite: bool = False) -> None: ...
