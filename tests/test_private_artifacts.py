"""Privacy, failure and atomic publication contracts for local artifact storage."""

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from satori.application.recovery.ports import PrivateArtifactPort
from satori.infrastructure.artifacts import PrivateArtifactStore


def test_private_writer_creates_private_parents_and_file(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    target = tmp_path / "new" / "nested" / "archive.bin"
    store: PrivateArtifactPort = PrivateArtifactStore()
    store.write(target, b"private snapshot")

    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755
    assert stat.S_IMODE(target.parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert store.read(target, max_bytes=100) == b"private snapshot"
    assert list(target.parent.iterdir()) == [target]


def test_existing_target_is_preserved_without_explicit_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "archive.bin"
    target.write_bytes(b"original")
    target.chmod(0o644)

    with pytest.raises(OSError, match="artifact_target_exists"):
        PrivateArtifactStore().write(target, b"replacement")

    assert target.read_bytes() == b"original"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert list(tmp_path.iterdir()) == [target]


def test_explicit_overwrite_atomically_replaces_with_private_file(tmp_path: Path) -> None:
    target = tmp_path / "archive.bin"
    target.write_bytes(b"original")
    target.chmod(0o644)
    previous_inode = target.stat().st_ino

    PrivateArtifactStore().write(target, b"replacement", overwrite=True)

    assert target.read_bytes() == b"replacement"
    assert target.stat().st_ino != previous_inode
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("overwrite", [False, True])
def test_symlink_and_directory_targets_are_rejected(tmp_path: Path, overwrite: bool) -> None:
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"unchanged")
    link = tmp_path / "link.bin"
    link.symlink_to(victim)
    directory = tmp_path / "directory"
    directory.mkdir()
    store = PrivateArtifactStore()

    for target in (link, directory):
        with pytest.raises(OSError, match="artifact_invalid_target"):
            store.write(target, b"replacement", overwrite=overwrite)

    assert victim.read_bytes() == b"unchanged"
    assert link.is_symlink()
    assert directory.is_dir()


def test_hardlinked_target_is_preserved_on_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "archive.bin"
    alias = tmp_path / "alias.bin"
    target.write_bytes(b"unchanged")
    alias.hardlink_to(target)

    with pytest.raises(OSError, match="artifact_hardlinked_target"):
        PrivateArtifactStore().write(target, b"replacement", overwrite=True)

    assert target.read_bytes() == alias.read_bytes() == b"unchanged"


def test_user_controlled_parent_symlink_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    target = real / "archive.bin"
    target.write_bytes(b"original")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    store = PrivateArtifactStore()

    with pytest.raises(OSError, match=r"^artifact_write_failed$"):
        store.write(alias / "archive.bin", b"replacement", overwrite=True)
    with pytest.raises(OSError, match=r"^artifact_read_failed$"):
        store.read(alias / "archive.bin", max_bytes=100)

    assert target.read_bytes() == b"original"
    assert list(real.iterdir()) == [target]


@pytest.mark.parametrize("existing", [False, True])
def test_stage_sync_failure_preserves_target_and_removes_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    target = tmp_path / "archive.bin"
    if existing:
        target.write_bytes(b"original")

    def fail_sync(descriptor: int) -> None:
        raise OSError("sensitive internal diagnostic")

    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(OSError, match=r"^artifact_write_failed$"):
        PrivateArtifactStore().write(target, b"private contents", overwrite=existing)

    assert (target.read_bytes() if existing else target.exists()) == (
        b"original" if existing else False
    )
    assert list(tmp_path.iterdir()) == ([target] if existing else [])


def test_read_bounds_and_empty_artifact(tmp_path: Path) -> None:
    target = tmp_path / "archive.bin"
    target.write_bytes(b"1234")
    store = PrivateArtifactStore()

    assert store.read(target, max_bytes=4) == b"1234"
    with pytest.raises(OSError, match="artifact_too_large"):
        store.read(target, max_bytes=3)
    with pytest.raises(ValueError, match="artifact_invalid_bound"):
        store.read(target, max_bytes=0)
    target.write_bytes(b"")
    assert store.read(target, max_bytes=1) == b""


def test_read_rejects_symlink_and_nonregular_target(tmp_path: Path) -> None:
    target = tmp_path / "private.bin"
    target.write_bytes(b"private contents")
    link = tmp_path / "link.bin"
    link.symlink_to(target)
    directory = tmp_path / "directory"
    directory.mkdir()
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    with pytest.raises(OSError, match="artifact_invalid_target"):
        PrivateArtifactStore().read(link, max_bytes=100)
    with pytest.raises(OSError, match="artifact_invalid_target"):
        PrivateArtifactStore().read(directory, max_bytes=100)
    with pytest.raises(OSError, match="artifact_invalid_target"):
        PrivateArtifactStore().read(fifo, max_bytes=100)


def test_parent_sync_failure_reports_published_but_not_durable_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "archive.bin"
    real_sync = os.fsync

    def fail_directory_sync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("internal filesystem information")
        real_sync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_directory_sync)
    with pytest.raises(OSError, match=r"^artifact_publication_sync_failed$"):
        PrivateArtifactStore().write(target, b"private contents")

    assert target.read_bytes() == b"private contents"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]


def test_parent_replacement_cannot_redirect_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "parent"
    original.mkdir()
    moved = tmp_path / "moved"
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    real_link = os.link

    def replace_parent_then_link(source: str, destination: str, **kwargs: Any) -> None:
        original.rename(moved)
        original.symlink_to(unrelated, target_is_directory=True)
        real_link(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", replace_parent_then_link)
    PrivateArtifactStore().write(original / "archive.bin", b"private contents")

    assert (moved / "archive.bin").read_bytes() == b"private contents"
    assert list(unrelated.iterdir()) == []
    assert list(moved.iterdir()) == [moved / "archive.bin"]


def test_no_overwrite_publication_preserves_concurrent_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "archive.bin"
    real_link = os.link

    def create_target_then_link(source: str, destination: str, **kwargs: Any) -> None:
        target.write_bytes(b"concurrent creation")
        real_link(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", create_target_then_link)
    with pytest.raises(OSError, match="artifact_write_failed"):
        PrivateArtifactStore().write(target, b"replacement")

    assert target.read_bytes() == b"concurrent creation"
    assert list(tmp_path.iterdir()) == [target]
