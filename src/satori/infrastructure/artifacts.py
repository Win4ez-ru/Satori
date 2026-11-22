"""Bounded private artifact I/O with directory-bound atomic publication."""

import os
import secrets
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path


class _ArtifactError(OSError):
    """A content-free storage failure code."""


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _target_status(parent: int, name: str) -> os.stat_result | None:
    try:
        observed = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(observed.st_mode):
        raise _ArtifactError("artifact_invalid_target")
    return observed


def _parent_path(path: Path) -> Path:
    """Allow only the fixed, root-owned macOS aliases before no-follow traversal."""

    if ".." in path.parent.parts:
        raise _ArtifactError("artifact_invalid_parent")
    parent = Path(os.path.abspath(path.parent))
    if sys.platform == "darwin" and len(parent.parts) > 1 and parent.parts[1] in {"tmp", "var"}:
        name = parent.parts[1]
        alias = Path("/") / name
        metadata = alias.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            root = Path("/").stat()
            if (
                metadata.st_uid != 0
                or root.st_uid != 0
                or root.st_mode & 0o022
                or os.readlink(alias) not in {f"private/{name}", f"/private/{name}"}
            ):
                raise _ArtifactError("artifact_invalid_parent")
            return Path("/private") / name / Path(*parent.parts[2:])
    return parent


@contextmanager
def _bound_parent(path: Path, *, create: bool) -> Iterator[tuple[int, str]]:
    """Walk every parent relative to bound directory FDs without following links."""

    expanded = path.expanduser()
    if not expanded.name or expanded.name in {".", ".."}:
        raise _ArtifactError("artifact_invalid_target")
    parent_path = _parent_path(expanded)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(parent_path.anchor, flags)
    try:
        for component in parent_path.parts[1:]:
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    child = os.open(component, flags, dir_fd=descriptor)
                else:
                    child = os.open(component, flags, dir_fd=descriptor)
                    try:
                        os.fchmod(child, 0o700)
                        os.fsync(descriptor)
                    except BaseException:
                        os.close(child)
                        raise
            os.close(descriptor)
            descriptor = child
        yield descriptor, expanded.name
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise _ArtifactError("artifact_write_failed")
        remaining = remaining[written:]


class PrivateArtifactStore:
    """No raw paths or artifact contents are included in storage exceptions."""

    def read(self, path: Path, *, max_bytes: int) -> bytes:
        if max_bytes <= 0:
            raise ValueError("artifact_invalid_bound")
        try:
            with _bound_parent(path, create=False) as (parent, name):
                previous = _target_status(parent, name)
                if previous is None:
                    raise _ArtifactError("artifact_read_failed")
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
                )
                try:
                    before = os.fstat(descriptor)
                    if not stat.S_ISREG(before.st_mode) or not _same_inode(previous, before):
                        raise _ArtifactError("artifact_invalid_target")
                    if before.st_size > max_bytes:
                        raise _ArtifactError("artifact_too_large")
                    chunks: list[bytes] = []
                    count = 0
                    while chunk := os.read(descriptor, min(1024 * 1024, max_bytes + 1 - count)):
                        chunks.append(chunk)
                        count += len(chunk)
                        if count > max_bytes:
                            raise _ArtifactError("artifact_too_large")
                    after = os.fstat(descriptor)
                    observed = _target_status(parent, name)
                    if (
                        observed is None
                        or not _same_inode(after, observed)
                        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        or count != after.st_size
                    ):
                        raise _ArtifactError("artifact_target_changed")
                    return b"".join(chunks)
                finally:
                    os.close(descriptor)
        except _ArtifactError:
            raise
        except (OSError, RuntimeError):
            raise _ArtifactError("artifact_read_failed") from None

    def write(self, path: Path, payload: bytes, *, overwrite: bool = False) -> None:
        try:
            with _bound_parent(path, create=True) as (parent, name):
                previous = _target_status(parent, name)
                if previous is not None:
                    if not overwrite:
                        raise _ArtifactError("artifact_target_exists")
                    if previous.st_nlink != 1:
                        raise _ArtifactError("artifact_hardlinked_target")
                stage = f".satori-artifact-{secrets.token_hex(16)}.tmp"
                descriptor = os.open(
                    stage,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
                try:
                    try:
                        os.fchmod(descriptor, 0o600)
                        _write_all(descriptor, payload)
                        os.fsync(descriptor)
                        staged = os.fstat(descriptor)
                        if staged.st_nlink != 1 or not stat.S_ISREG(staged.st_mode):
                            raise _ArtifactError("artifact_stage_changed")
                    finally:
                        os.close(descriptor)
                    stage_status = _target_status(parent, stage)
                    if stage_status is None or not _same_inode(staged, stage_status):
                        raise _ArtifactError("artifact_stage_changed")
                    current = _target_status(parent, name)
                    if previous is None:
                        if current is not None:
                            raise _ArtifactError("artifact_target_exists")
                    elif (
                        current is None
                        or not _same_inode(previous, current)
                        or current.st_nlink != 1
                        or (previous.st_size, previous.st_mtime_ns, previous.st_ctime_ns)
                        != (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
                    ):
                        raise _ArtifactError("artifact_target_changed")
                    if overwrite and previous is not None:
                        os.replace(stage, name, src_dir_fd=parent, dst_dir_fd=parent)
                    else:
                        os.link(
                            stage,
                            name,
                            src_dir_fd=parent,
                            dst_dir_fd=parent,
                            follow_symlinks=False,
                        )
                        os.unlink(stage, dir_fd=parent)
                    try:
                        os.fsync(parent)
                    except OSError:
                        raise _ArtifactError("artifact_publication_sync_failed") from None
                finally:
                    with suppress(FileNotFoundError):
                        os.unlink(stage, dir_fd=parent)
        except _ArtifactError:
            raise
        except (OSError, RuntimeError):
            raise _ArtifactError("artifact_write_failed") from None
